"""Qt-independent authoritative Current Annealing controller backend."""

from __future__ import annotations

import json
import math
from pathlib import Path
import time
from typing import Any, Callable

from data_logging.mini_dma_logger.control_process import ControlStartRequest
from data_logging.shared_power_supply.broker import ROLE_CURRENT_ANNEALING
from data_logging.shared_power_supply.protocol import BrokerJsonClient

from .session import CurrentAnnealingSessionWriter


class _SimulatedBrokerClient:
    """Deterministic in-process supply used only by software verification."""

    def __init__(self, *, open_circuit: bool = False) -> None:
        self.current_mA = 0.0
        self.output_on = False
        self.open_circuit = bool(open_circuit)
        self._started = time.monotonic()

    def lease(self, **_kwargs: Any) -> dict[str, str]:
        return {"lease_id": "simulated-current-annealing"}

    def start_scheduler(self, **_kwargs: Any) -> None: pass

    def configure_polling(self, *, requested_hz: float, **_kwargs: Any) -> dict[str, Any]:
        return {"generation": 1, "polling": {"requested_hz": requested_hz, "effective_hz": requested_hz}}

    def configure_channel(self, *, current_a: float, output_on: bool, **_kwargs: Any) -> None:
        self.current_mA = max(0.0, float(current_a) * 1000.0)
        self.output_on = bool(output_on)

    def schedule_current(self, *, current_mA: float, **_kwargs: Any) -> None:
        self.current_mA = max(0.0, float(current_mA))

    def latest_readback(self, **_kwargs: Any) -> dict[str, Any]:
        current = self.current_mA if self.output_on and not self.open_circuit else 0.0
        resistance = 125.0 + 0.03 * current
        return {
            "voltage_V": resistance * current / 1000.0,
            "current_mA": current,
            "timestamp_s": time.monotonic(),
            "age_s": 0.0,
            "cadence": {"generation": 1, "polling": {"effective_hz": 20.0}},
        }

    def set_current(self, *, current_mA: float, **_kwargs: Any) -> None:
        self.current_mA = max(0.0, float(current_mA))

    def set_output(self, *, output_on: bool, **_kwargs: Any) -> None:
        self.output_on = bool(output_on)

    def release(self, **_kwargs: Any) -> None: pass


class CurrentAnnealingProcessBackend:
    """Own the PSU lease, recipe clock, readbacks, and logging in one process."""

    def __init__(self, client_factory: Callable[..., Any] | None = None) -> None:
        self._client_factory = client_factory
        self._client: Any = None
        self._lease_id = ""
        self._writer: CurrentAnnealingSessionWriter | None = None
        self._config: dict[str, Any] = {}
        self._running = False
        self._paused = False
        self._finalized = False
        self._completion: str | None = None
        self._last_tick_s = 0.0
        self._last_sample_timestamp_s: float | None = None
        self._last_sample_elapsed_s = -1.0
        self._set_current_mA = 0.0
        self._measured_current_mA = 0.0
        self._voltage_V = 0.0
        self._resistance_ohm = math.nan
        self._power_mW = 0.0
        self._direction = "heating"
        self._cycle_index = 1
        self._sample_sequence = 0
        self._nonzero_seen = False
        self._zero_count = 0
        self._effective_hz = 0.0
        self._last_readback_age_s = 0.0
        self._started_s = 0.0

    def start(self, request: ControlStartRequest) -> None:
        config = json.loads(request.config_json)
        self._validate_config(config)
        self._config = config
        if bool(config.get("simulate")):
            self._client = _SimulatedBrokerClient(open_circuit=bool(config.get("simulate_open_circuit")))
        elif self._client_factory is not None:
            self._client = self._client_factory(config)
        else:
            self._client = BrokerJsonClient(
                host=str(config["broker_host"]),
                port=int(config["broker_port"]),
                timeout_s=float(config.get("broker_timeout_s", 2.0)),
            )
        channel = int(config["channel"])
        lease = self._client.lease(
            channel=channel,
            owner=f"current_annealing_control:{request.identity.session_id}",
            role=ROLE_CURRENT_ANNEALING,
        )
        self._lease_id = str(lease.get("lease_id") or "")
        if not self._lease_id:
            raise RuntimeError("shared HMP broker did not return a lease id")
        self._client.start_scheduler(tick_s=0.05)
        status = self._client.configure_polling(
            channel=channel,
            lease_id=self._lease_id,
            requested_hz=float(config["requested_hz"]),
        )
        self._effective_hz = self._polling_hz(status, float(config["requested_hz"]))
        self._set_current_mA = float(config["start_current_mA"])
        self._client.configure_channel(
            channel=channel,
            lease_id=self._lease_id,
            voltage_v=float(config["voltage_limit_V"]),
            current_a=self._set_current_mA / 1000.0,
            output_on=True,
        )
        self._writer = CurrentAnnealingSessionWriter(Path(config["run_dir"]), dict(config["metadata"]))
        self._writer.log(
            f"Dedicated controller started on CH{channel}; requested PSU rate {float(config['requested_hz']):g} Hz; "
            f"effective {self._effective_hz:g} Hz."
        )
        self._last_tick_s = time.monotonic()
        self._started_s = self._last_tick_s
        self._running = True

    def tick(self, now_s: float) -> None:
        if not self._running or self._paused:
            self._last_tick_s = now_s
            return
        dt = max(0.0, min(1.0, now_s - self._last_tick_s))
        self._last_tick_s = now_s
        self._advance_setpoint(dt)
        if not self._running:
            return
        self._client.schedule_current(
            channel=int(self._config["channel"]),
            lease_id=self._lease_id,
            current_mA=self._set_current_mA,
        )
        readback = self._client.latest_readback(
            channel=int(self._config["channel"]),
            max_age_s=float(self._config.get("max_readback_age_s", 2.5)),
            fallback_to_measure=True,
        )
        timestamp = readback.get("timestamp_s")
        if timestamp is not None and self._last_sample_timestamp_s is not None:
            if float(timestamp) <= self._last_sample_timestamp_s:
                return
        if timestamp is not None:
            self._last_sample_timestamp_s = float(timestamp)
        self._record_readback(readback)

    def pause(self) -> None:
        self._paused = True
        if self._writer:
            self._writer.log("Recipe paused by operator.")

    def resume(self) -> None:
        self._paused = False
        self._last_tick_s = time.monotonic()
        if self._writer:
            self._writer.log("Recipe resumed by operator.")

    def stop(self) -> None:
        self._finish("stopped", "operator_stop", "Stopped by operator.")

    def emergency_stop(self, reason: str) -> None:
        self._finish("emergency", "emergency_stop", reason)

    def update_config(self, config_json: str) -> tuple[bool, str]:
        update = json.loads(config_json)
        allowed = {"max_current_mA", "ramp_rate_mA_s", "force_reverse"}
        unexpected = set(update) - allowed
        if unexpected:
            return False, "unsupported runtime fields: " + ", ".join(sorted(unexpected))
        if "max_current_mA" in update:
            value = float(update["max_current_mA"])
            if value < float(self._config["start_current_mA"]):
                return False, "maximum current is below the start current"
            self._config["max_current_mA"] = value
        if "ramp_rate_mA_s" in update:
            value = float(update["ramp_rate_mA_s"])
            if value <= 0.0:
                return False, "ramp rate must be positive"
            self._config["ramp_rate_mA_s"] = value
        if bool(update.get("force_reverse")):
            self._direction = "cooling"
        return True, "current annealing recipe updated"

    def set_current_hold_bypass(self, enabled: bool) -> tuple[bool, str]:
        return False, "current hold bypass is not part of Current Annealing"

    def readback(self) -> tuple[tuple[str, float | int | str | bool | None], ...]:
        return (
            ("phase", "paused" if self._paused else self._direction),
            ("cycle_index", self._cycle_index),
            ("direction", self._direction),
            ("set_current_mA", self._set_current_mA),
            ("measured_current_mA", self._measured_current_mA),
            ("voltage_V", self._voltage_V),
            ("resistance_ohm", self._resistance_ohm),
            ("power_mW", self._power_mW),
            ("sample_sequence", self._sample_sequence),
            ("effective_hz", self._effective_hz),
            ("readback_age_s", self._last_readback_age_s),
            ("run_dir", str(self._config.get("run_dir") or "")),
        )

    def completion_detail(self) -> str | None:
        return self._completion

    def close(self) -> None:
        if self._running and not self._finalized:
            self._finish("failed", "controller_closed", "Controller closed before completion.")

    def _advance_setpoint(self, dt: float) -> None:
        rate = float(self._config["ramp_rate_mA_s"])
        start = float(self._config["start_current_mA"])
        maximum = float(self._config["max_current_mA"])
        if self._direction == "heating":
            self._set_current_mA = min(maximum, self._set_current_mA + rate * dt)
            if self._set_current_mA >= maximum - 1e-9:
                if bool(self._config.get("reverse_enabled", True)):
                    self._direction = "cooling"
                else:
                    self._finish("completed", "recipe_complete", "Maximum current reached.")
        else:
            self._set_current_mA = max(start, self._set_current_mA - rate * dt)
            if self._set_current_mA <= start + 1e-9:
                if bool(self._config.get("infinite_loops", False)):
                    self._cycle_index += 1
                    self._direction = "heating"
                elif self._cycle_index >= int(self._config.get("loops", 1)):
                    self._finish("completed", "recipe_complete", "Configured current cycles completed.")
                else:
                    self._cycle_index += 1
                    self._direction = "heating"

    def _record_readback(self, readback: dict[str, Any]) -> None:
        current = float(readback.get("current_mA") or 0.0)
        voltage = float(readback.get("voltage_V") or 0.0)
        self._measured_current_mA = current
        self._voltage_V = voltage
        self._resistance_ohm = voltage / (current / 1000.0) if current > 0.0 else math.nan
        self._power_mW = voltage * current
        self._last_readback_age_s = float(readback.get("age_s") or 0.0)
        minimum = float(self._config.get("minimum_contact_current_mA", 0.2))
        if current >= minimum:
            self._nonzero_seen = True
            self._zero_count = 0
        elif time.monotonic() - self._started_s >= float(self._config.get("contact_grace_s", 2.0)):
            self._zero_count += 1
            if self._zero_count >= int(self._config.get("contact_loss_samples", 3)):
                self._finish("failed", "contact_lost", "Measured current disappeared during annealing.")
                raise RuntimeError("Measured current disappeared during annealing.")
        # Voltage compliance is meaningful recipe feedback only after the
        # mounted wire has demonstrated real current flow.  An open circuit
        # reaches compliance immediately; reversing there could finish a
        # nominal sweep before the bounded contact-loss counter has time to
        # reject the missing wire.
        if self._nonzero_seen and voltage >= float(self._config["voltage_limit_V"]) - float(
            self._config.get("voltage_margin_V", 0.05)
        ):
            action = str(self._config.get("voltage_limit_action", "reverse"))
            if action == "reverse" and self._direction == "heating":
                self._direction = "cooling"
                if self._writer:
                    self._writer.log(f"Voltage limit reached at {voltage:.6g} V; reversing current ramp.")
            elif action == "stop":
                self._finish("completed", "voltage_limit", f"Voltage limit reached at {voltage:.6g} V.")
                return
        assert self._writer is not None
        sample = self._writer.append(
            phase="current_ramp",
            cycle_index=self._cycle_index,
            direction=self._direction,
            set_current_mA=self._set_current_mA,
            measured_current_mA=current,
            voltage_V=voltage,
            diameter_um=self._config.get("diameter_um"),
            readback_age_s=self._last_readback_age_s,
        )
        self._last_sample_elapsed_s = sample.elapsed_s
        self._sample_sequence += 1

    def _finish(self, state: str, reason: str, detail: str) -> None:
        if self._finalized:
            return
        self._running = False
        self._safe_output_off()
        if self._writer is not None:
            self._writer.finalize(state=state, reason=reason, detail=detail)
        self._finalized = True
        if state == "completed":
            self._completion = detail

    def _safe_output_off(self) -> None:
        if self._client is None or not self._lease_id:
            return
        channel = int(self._config["channel"])
        try:
            self._client.configure_channel(
                channel=channel,
                lease_id=self._lease_id,
                voltage_v=0.0,
                current_a=0.0,
                output_on=False,
            )
        except Exception:
            try:
                self._client.set_current(channel=channel, lease_id=self._lease_id, current_mA=0.0)
                self._client.set_output(channel=channel, lease_id=self._lease_id, output_on=False)
            except Exception:
                pass
        finally:
            try:
                self._client.release(channel=channel, lease_id=self._lease_id)
            except Exception:
                pass
            self._lease_id = ""

    @staticmethod
    def _polling_hz(status: Any, fallback: float) -> float:
        try:
            return max(0.1, float(status["polling"]["effective_hz"]))
        except (KeyError, TypeError, ValueError):
            return max(0.1, fallback)

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> None:
        required = (
            "run_dir", "metadata", "channel", "requested_hz", "voltage_limit_V",
            "start_current_mA", "max_current_mA", "ramp_rate_mA_s",
        )
        missing = [name for name in required if name not in config]
        if missing:
            raise ValueError("missing Current Annealing config: " + ", ".join(missing))
        if int(config["channel"]) <= 0:
            raise ValueError("channel must be positive")
        start = float(config["start_current_mA"])
        maximum = float(config["max_current_mA"])
        if start < 0.0 or maximum < start:
            raise ValueError("invalid current range")
        if float(config["ramp_rate_mA_s"]) <= 0.0:
            raise ValueError("ramp rate must be positive")


def create_current_annealing_backend() -> CurrentAnnealingProcessBackend:
    return CurrentAnnealingProcessBackend()


__all__ = ["CurrentAnnealingProcessBackend", "create_current_annealing_backend"]
