from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from threading import Event, RLock, Thread
from uuid import uuid4

from .driver import HmpSerialDriver
from .profiles import HMP_PROFILES, SupplyProfile
from .ramp import RateLimitedCurrentRamp


ROLE_UNUSED = "unused"
ROLE_MINI_DMA_MOTOR = "mini_dma_motor_supply"
ROLE_MINI_DMA_CURRENT = "mini_dma_current_sweep"
ROLE_CURRENT_ANNEALING = "current_annealing"
ROLE_OTHER_MANUAL = "other_manual"
VALID_ROLES = {
    ROLE_UNUSED,
    ROLE_MINI_DMA_MOTOR,
    ROLE_MINI_DMA_CURRENT,
    ROLE_CURRENT_ANNEALING,
    ROLE_OTHER_MANUAL,
}
GLOBAL_GUARDED_COMMANDS = {"*RST", "OUTP:GEN 0", "SYST:LOC", "ALL_OUTPUTS_OFF"}
LIMIT_EPSILON = 1e-12


def _exceeds_limit(value: float, limit: float | None) -> bool:
    if limit is None:
        return False
    return float(value) > float(limit) + LIMIT_EPSILON


@dataclass
class BenchChannel:
    role: str = ROLE_UNUSED
    confirmed: bool = False
    voltage_limit_v: float | None = None
    current_limit_a: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "confirmed": self.confirmed,
            "voltage_limit_v": self.voltage_limit_v,
            "current_limit_a": self.current_limit_a,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "BenchChannel":
        return cls(
            role=str(payload.get("role") or ROLE_UNUSED),
            confirmed=bool(payload.get("confirmed", False)),
            voltage_limit_v=(
                None if payload.get("voltage_limit_v") is None else float(payload["voltage_limit_v"])
            ),
            current_limit_a=(
                None if payload.get("current_limit_a") is None else float(payload["current_limit_a"])
            ),
        )


@dataclass
class BenchProfile:
    name: str
    model: str
    port_identity: str
    channels: dict[int, BenchChannel] = field(default_factory=dict)
    confirmed_at_utc: str | None = None
    requires_confirmation: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "model": self.model,
            "port_identity": self.port_identity,
            "confirmed_at_utc": self.confirmed_at_utc,
            "requires_confirmation": self.requires_confirmation,
            "channels": {str(channel): config.to_dict() for channel, config in self.channels.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "BenchProfile":
        raw_channels = payload.get("channels", {})
        channels: dict[int, BenchChannel] = {}
        if isinstance(raw_channels, dict):
            for channel, config in raw_channels.items():
                if isinstance(config, dict):
                    channels[int(channel)] = BenchChannel.from_dict(config)
        return cls(
            name=str(payload.get("name") or "Shared HMP bench"),
            model=str(payload.get("model") or ""),
            port_identity=str(payload.get("port_identity") or ""),
            channels=channels,
            confirmed_at_utc=(
                None if payload.get("confirmed_at_utc") is None else str(payload["confirmed_at_utc"])
            ),
            requires_confirmation=bool(payload.get("requires_confirmation", True)),
        )

    def needs_reconfirmation(self, *, model: str, port_identity: str) -> bool:
        if self.model != model or self.port_identity != port_identity:
            return True
        if self.requires_confirmation:
            return True
        return any(config.role != ROLE_UNUSED and not config.confirmed for config in self.channels.values())


@dataclass(frozen=True)
class ChannelLease:
    channel: int
    owner: str
    role: str
    lease_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "owner": self.owner,
            "role": self.role,
            "lease_id": self.lease_id,
        }


@dataclass
class PollState:
    interval_s: float
    next_due_s: float | None = None
    last_polled_s: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "interval_s": self.interval_s,
            "next_due_s": self.next_due_s,
            "last_polled_s": self.last_polled_s,
        }


@dataclass
class PendingCurrent:
    lease_id: str
    current_mA: float
    requested_at_s: float

    def to_dict(self) -> dict[str, object]:
        return {
            "lease_id": self.lease_id,
            "current_mA": self.current_mA,
            "requested_at_s": self.requested_at_s,
        }


class SharedPowerSupplyBroker:
    """Channel lease and safety layer above one serialized HMP driver."""

    def __init__(self, driver: HmpSerialDriver, profile: SupplyProfile) -> None:
        self.driver = driver
        self.profile = profile
        self.bench_profile = BenchProfile(
            name="Shared HMP bench",
            model=profile.profile_id,
            port_identity=driver.port_name,
            channels={channel: BenchChannel() for channel in range(1, profile.channel_count + 1)},
        )
        self._leases: dict[int, ChannelLease] = {}
        self._readbacks: dict[int, dict[str, float | None]] = {}
        self._polling: dict[int, PollState] = {}
        self._pending_currents: dict[int, PendingCurrent] = {}
        self._current_ramps: dict[int, tuple[str, RateLimitedCurrentRamp]] = {}
        self._setpoint_currents_mA: dict[int, float] = {}
        self._metrics: dict[str, int] = {
            "coalesced_current_requests": 0,
            "current_commands_sent": 0,
            "ramp_steps_sent": 0,
            "polls_completed": 0,
            "poll_errors": 0,
        }
        self._scheduler_lock = RLock()
        self._scheduler_stop = Event()
        self._scheduler_thread: Thread | None = None

    def validate_channel(self, channel: int) -> int:
        return self.profile.validate_channel(channel)

    def assign_role(
        self,
        *,
        channel: int,
        role: str,
        confirmed: bool = False,
        voltage_limit_v: float | None = None,
        current_limit_a: float | None = None,
    ) -> BenchChannel:
        channel = self.validate_channel(channel)
        if role not in VALID_ROLES:
            raise ValueError(f"Unsupported channel role: {role}")
        config = BenchChannel(
            role=role,
            confirmed=bool(confirmed and role != ROLE_UNUSED),
            voltage_limit_v=voltage_limit_v,
            current_limit_a=current_limit_a,
        )
        if role == ROLE_UNUSED:
            config.confirmed = False
        self.bench_profile.channels[channel] = config
        self.bench_profile.requires_confirmation = True
        return config

    def confirm_profile(self, *, name: str | None = None) -> BenchProfile:
        if name:
            self.bench_profile.name = name
        for config in self.bench_profile.channels.values():
            if config.role != ROLE_UNUSED:
                config.confirmed = True
        self.bench_profile.confirmed_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.bench_profile.requires_confirmation = False
        return self.bench_profile

    def load_profile(self, profile: BenchProfile) -> BenchProfile:
        if profile.model not in HMP_PROFILES:
            raise ValueError(f"Unsupported bench profile model: {profile.model}")
        if profile.model != self.profile.profile_id or profile.port_identity != self.driver.port_name:
            profile.requires_confirmation = True
            for config in profile.channels.values():
                config.confirmed = False
        self.bench_profile = profile
        for channel in range(1, self.profile.channel_count + 1):
            self.bench_profile.channels.setdefault(channel, BenchChannel())
        for channel in list(self.bench_profile.channels):
            self.validate_channel(channel)
        return self.bench_profile

    def lease(self, *, channel: int, owner: str, role: str) -> ChannelLease:
        channel = self.validate_channel(channel)
        config = self.bench_profile.channels.get(channel, BenchChannel())
        if config.role == ROLE_UNUSED or not config.confirmed:
            raise PermissionError(f"CH{channel} must have a confirmed role before it can be leased.")
        if config.role != role:
            raise PermissionError(f"CH{channel} is assigned to {config.role}, not {role}.")
        existing = self._leases.get(channel)
        if existing is not None and existing.owner != owner:
            raise PermissionError(f"CH{channel} is already leased by {existing.owner}.")
        if existing is not None:
            return existing
        lease = ChannelLease(channel=channel, owner=owner, role=role, lease_id=uuid4().hex)
        self._leases[channel] = lease
        return lease

    def release(self, *, channel: int, lease_id: str) -> None:
        channel = self.validate_channel(channel)
        existing = self._leases.get(channel)
        if existing is None:
            return
        if existing.lease_id != lease_id:
            raise PermissionError(f"Lease mismatch for CH{channel}.")
        self._leases.pop(channel, None)
        with self._scheduler_lock:
            self._pending_currents.pop(channel, None)
            self._current_ramps.pop(channel, None)
            self._polling.pop(channel, None)
            self._setpoint_currents_mA.pop(channel, None)
            self._readbacks.pop(channel, None)

    def _require_lease(self, *, channel: int, lease_id: str) -> BenchChannel:
        channel = self.validate_channel(channel)
        lease = self._leases.get(channel)
        if lease is None or lease.lease_id != lease_id:
            raise PermissionError(f"A valid lease is required before controlling CH{channel}.")
        config = self.bench_profile.channels[channel]
        if not config.confirmed or config.role == ROLE_UNUSED:
            raise PermissionError(f"CH{channel} wiring is not confirmed.")
        return config

    def configure_channel(
        self,
        *,
        channel: int,
        lease_id: str,
        voltage_v: float,
        current_a: float,
        output_on: bool,
    ) -> None:
        config = self._require_lease(channel=channel, lease_id=lease_id)
        if _exceeds_limit(voltage_v, config.voltage_limit_v):
            raise ValueError(f"Requested voltage exceeds CH{channel} limit.")
        if _exceeds_limit(current_a, config.current_limit_a):
            raise ValueError(f"Requested current exceeds CH{channel} limit.")
        self.driver.configure_channel(
            channel=channel,
            voltage_v=voltage_v,
            current_a=current_a,
            output_on=output_on,
        )
        with self._scheduler_lock:
            self._setpoint_currents_mA[channel] = max(0.0, float(current_a) * 1000.0)

    def set_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
        config = self._require_lease(channel=channel, lease_id=lease_id)
        if _exceeds_limit(current_mA / 1000.0, config.current_limit_a):
            raise ValueError(f"Requested current exceeds CH{channel} limit.")
        self.driver.set_current_mA(channel=channel, current_mA=current_mA)
        with self._scheduler_lock:
            self._setpoint_currents_mA[channel] = max(0.0, float(current_mA))
            self._metrics["current_commands_sent"] += 1

    def set_output(self, *, channel: int, lease_id: str, output_on: bool) -> None:
        self._require_lease(channel=channel, lease_id=lease_id)
        self.driver.set_output(channel=channel, output_on=output_on)
        if not output_on:
            with self._scheduler_lock:
                self._pending_currents.pop(channel, None)
                self._current_ramps.pop(channel, None)

    def output_state(self, *, channel: int) -> bool | None:
        channel = self.validate_channel(channel)
        config = self.bench_profile.channels.get(channel, BenchChannel())
        if config.role == ROLE_UNUSED or not config.confirmed:
            raise PermissionError(f"CH{channel} must be confirmed before output-state readback.")
        return self.driver.output_state(channel=channel)

    def measure_channel(self, *, channel: int) -> dict[str, float | None]:
        return self._measure_channel_at(channel=channel, now_s=time.monotonic())

    def _measure_channel_at(self, *, channel: int, now_s: float) -> dict[str, float | None]:
        channel = self.validate_channel(channel)
        config = self.bench_profile.channels.get(channel, BenchChannel())
        if config.role == ROLE_UNUSED or not config.confirmed:
            raise PermissionError(f"CH{channel} must be confirmed before measurement.")
        readback = self.driver.measure(channel=channel)
        readback["timestamp_s"] = float(now_s)
        readback["cached"] = False
        self._readbacks[channel] = readback
        return dict(readback)

    def configure_polling(self, *, channel: int, interval_s: float) -> None:
        channel = self.validate_channel(channel)
        config = self.bench_profile.channels.get(channel, BenchChannel())
        if config.role == ROLE_UNUSED or not config.confirmed:
            raise PermissionError(f"CH{channel} must be confirmed before polling.")
        interval = max(0.01, float(interval_s))
        with self._scheduler_lock:
            existing = self._polling.get(channel)
            last_polled = None if existing is None else existing.last_polled_s
            self._polling[channel] = PollState(interval_s=interval, last_polled_s=last_polled)

    def disable_polling(self, *, channel: int) -> None:
        channel = self.validate_channel(channel)
        with self._scheduler_lock:
            self._polling.pop(channel, None)

    def schedule_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
        config = self._require_lease(channel=channel, lease_id=lease_id)
        current = max(0.0, float(current_mA))
        if _exceeds_limit(current / 1000.0, config.current_limit_a):
            raise ValueError(f"Requested current exceeds CH{channel} limit.")
        with self._scheduler_lock:
            if channel in self._pending_currents:
                self._metrics["coalesced_current_requests"] += 1
            self._current_ramps.pop(channel, None)
            self._pending_currents[channel] = PendingCurrent(
                lease_id=str(lease_id),
                current_mA=current,
                requested_at_s=time.monotonic(),
            )

    def schedule_current_ramp(
        self,
        *,
        channel: int,
        lease_id: str,
        target_mA: float,
        rate_mA_s: float,
        max_step_mA: float | None = None,
        resolution_mA: float | None = None,
        now_s: float | None = None,
    ) -> None:
        config = self._require_lease(channel=channel, lease_id=lease_id)
        target = max(0.0, float(target_mA))
        if _exceeds_limit(target / 1000.0, config.current_limit_a):
            raise ValueError(f"Requested current exceeds CH{channel} limit.")
        resolution = max(0.001, float(resolution_mA or self.profile.current_resolution_mA))
        max_step = max(resolution, float(max_step_mA or resolution))
        now = time.monotonic() if now_s is None else float(now_s)
        with self._scheduler_lock:
            self._pending_currents.pop(channel, None)
            current = self._setpoint_currents_mA.get(channel)
            if current is None:
                readback = self._readbacks.get(channel)
                measured = None if readback is None else readback.get("current_mA")
                current = 0.0 if measured is None else float(measured)
            existing = self._current_ramps.get(channel)
            if existing is not None and existing[0] == lease_id:
                existing[1].update_target(target_mA=target, rate_mA_s=rate_mA_s, now_s=now)
                return
            self._current_ramps[channel] = (
                str(lease_id),
                RateLimitedCurrentRamp(
                    initial_mA=float(current),
                    target_mA=target,
                    rate_mA_s=float(rate_mA_s),
                    resolution_mA=resolution,
                    max_step_mA=max_step,
                    now_s=now,
                ),
            )

    def latest_readback(
        self,
        *,
        channel: int,
        max_age_s: float | None = None,
        fallback_to_measure: bool = True,
        now_s: float | None = None,
    ) -> dict[str, float | None | bool]:
        channel = self.validate_channel(channel)
        now = time.monotonic() if now_s is None else float(now_s)
        with self._scheduler_lock:
            readback = self._readbacks.get(channel)
            pending = self._pending_currents.get(channel)
            setpoint = self._setpoint_currents_mA.get(channel)
            if readback is not None:
                timestamp = readback.get("timestamp_s")
                age_s = None if timestamp is None else max(0.0, now - float(timestamp))
                if max_age_s is None or age_s is None or age_s <= float(max_age_s):
                    payload: dict[str, float | None | bool] = dict(readback)
                    payload["cached"] = True
                    payload["age_s"] = age_s
                    payload["setpoint_current_mA"] = setpoint
                    payload["pending_current_mA"] = None if pending is None else pending.current_mA
                    return payload
        if not fallback_to_measure:
            return {
                "voltage_V": None,
                "current_mA": None,
                "timestamp_s": None,
                "cached": False,
                "age_s": None,
                "setpoint_current_mA": setpoint,
                "pending_current_mA": None if pending is None else pending.current_mA,
            }
        measured = self._measure_channel_at(channel=channel, now_s=now)
        measured["age_s"] = 0.0
        measured["setpoint_current_mA"] = self._setpoint_currents_mA.get(channel)
        measured["pending_current_mA"] = None
        return measured

    def process_scheduler_once(self, *, now_s: float | None = None) -> None:
        now = time.monotonic() if now_s is None else float(now_s)
        with self._scheduler_lock:
            pending = list(self._pending_currents.items())
            ramps = list(self._current_ramps.items())
        for channel, current in pending:
            with self._scheduler_lock:
                if self._pending_currents.get(channel) is not current:
                    continue
            self.set_current(channel=channel, lease_id=current.lease_id, current_mA=current.current_mA)
            with self._scheduler_lock:
                latest = self._pending_currents.get(channel)
                if latest is current:
                    self._pending_currents.pop(channel, None)
        for channel, (lease_id, ramp) in ramps:
            next_mA = ramp.next_setpoint(now_s=now)
            if next_mA is None:
                if ramp.is_complete:
                    with self._scheduler_lock:
                        latest = self._current_ramps.get(channel)
                        if latest is not None and latest[1] is ramp:
                            self._current_ramps.pop(channel, None)
                continue
            with self._scheduler_lock:
                latest = self._current_ramps.get(channel)
                if latest is None or latest[0] != lease_id or latest[1] is not ramp:
                    continue
            self.set_current(channel=channel, lease_id=lease_id, current_mA=next_mA)
            with self._scheduler_lock:
                self._metrics["ramp_steps_sent"] += 1

        with self._scheduler_lock:
            due_channels = [
                channel
                for channel, state in self._polling.items()
                if state.next_due_s is None or state.next_due_s <= now
            ]
        for channel in due_channels:
            try:
                self._measure_channel_at(channel=channel, now_s=now)
                with self._scheduler_lock:
                    self._metrics["polls_completed"] += 1
            except Exception:
                with self._scheduler_lock:
                    self._metrics["poll_errors"] += 1
                raise
            with self._scheduler_lock:
                state = self._polling.get(channel)
                if state is not None:
                    state.last_polled_s = now
                    state.next_due_s = now + state.interval_s

    def start_scheduler(self, *, tick_s: float = 0.05) -> None:
        with self._scheduler_lock:
            if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
                return
            self._scheduler_stop.clear()
            interval = max(0.001, float(tick_s))

            def _run() -> None:
                while not self._scheduler_stop.wait(interval):
                    try:
                        self.process_scheduler_once()
                    except Exception:
                        # Individual client calls still surface errors; the background loop
                        # must not die silently because one poll failed.
                        continue

            self._scheduler_thread = Thread(target=_run, name="shared-hmp-scheduler", daemon=True)
            self._scheduler_thread.start()

    def stop_scheduler(self) -> None:
        thread: Thread | None
        with self._scheduler_lock:
            thread = self._scheduler_thread
            self._scheduler_thread = None
            self._scheduler_stop.set()
        if thread is not None:
            thread.join(timeout=2.0)

    def raw_command(self, command: str) -> None:
        normalized = command.strip().upper()
        if normalized in GLOBAL_GUARDED_COMMANDS:
            raise PermissionError(f"{normalized} is guarded by the broker emergency path.")
        raise PermissionError("Raw SCPI commands are not exposed in shared mode.")

    def emergency_all_outputs_off(self, *, intent: str) -> None:
        if intent != "emergency_stop_all":
            raise PermissionError("Emergency all-output stop requires explicit emergency_stop_all intent.")
        for channel in range(1, self.profile.channel_count + 1):
            self.driver.set_output(channel=channel, output_on=False)

    def snapshot(self) -> dict[str, object]:
        return {
            "model": self.profile.profile_id,
            "channel_count": self.profile.channel_count,
            "bench_profile": self.bench_profile.to_dict(),
            "leases": {str(channel): lease.to_dict() for channel, lease in self._leases.items()},
            "readbacks": {str(channel): dict(readback) for channel, readback in self._readbacks.items()},
            "scheduler": {
                "running": self._scheduler_thread is not None and self._scheduler_thread.is_alive(),
                "polling": {str(channel): state.to_dict() for channel, state in self._polling.items()},
                "pending_currents": {
                    str(channel): current.to_dict()
                    for channel, current in self._pending_currents.items()
                },
                "current_ramps": {
                    str(channel): {
                        "lease_id": lease_id,
                        "current_mA": ramp.current_mA,
                        "target_mA": ramp.target_mA,
                        "rate_mA_s": ramp.rate_mA_s,
                        "resolution_mA": ramp.resolution_mA,
                        "max_step_mA": ramp.max_step_mA,
                        "complete": ramp.is_complete,
                    }
                    for channel, (lease_id, ramp) in self._current_ramps.items()
                },
                "setpoint_currents_mA": {
                    str(channel): current
                    for channel, current in self._setpoint_currents_mA.items()
                },
                "metrics": dict(self._metrics),
            },
        }
