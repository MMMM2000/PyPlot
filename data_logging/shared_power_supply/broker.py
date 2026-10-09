from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from threading import Event, RLock, Thread
from uuid import uuid4

from .driver import HmpSerialDriver
from .profiles import HMP_PROFILES, SupplyProfile


ROLE_UNUSED = "unused"
ROLE_MINI_DMA_MOTOR = "mini_dma_motor_supply"
ROLE_MINI_DMA_CURRENT = "mini_dma_current_sweep"
ROLE_CURRENT_ANNEALING = "current_annealing"
ROLE_AC_SUSCEPTIBILITY = "ac_susceptibility"
ROLE_OTHER_MANUAL = "other_manual"
VALID_ROLES = {
    ROLE_UNUSED,
    ROLE_MINI_DMA_MOTOR,
    ROLE_MINI_DMA_CURRENT,
    ROLE_CURRENT_ANNEALING,
    ROLE_AC_SUSCEPTIBILITY,
    ROLE_OTHER_MANUAL,
}
GLOBAL_GUARDED_COMMANDS = {"*RST", "OUTP:GEN 0", "SYST:LOC", "ALL_OUTPUTS_OFF"}
MAX_TOTAL_READBACK_HZ = 2.0
MAX_CADENCE_EVENTS = 64


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
    channel: int
    lease_id: str
    owner: str
    role: str
    requested_hz: float
    effective_hz: float = 0.0
    next_due_s: float | None = None
    last_polled_s: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "lease_id": self.lease_id,
            "owner": self.owner,
            "role": self.role,
            "requested_hz": self.requested_hz,
            "effective_hz": self.effective_hz,
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
        self._setpoint_currents_mA: dict[int, float] = {}
        self._cadence_generation = 0
        self._cadence_events: deque[dict[str, object]] = deque(maxlen=MAX_CADENCE_EVENTS)
        self._scheduler_stop = Event()
        self._scheduler_thread: Thread | None = None
        self._scheduler_error: str | None = None
        self._lock = RLock()

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
        with self._lock:
            channel = self.validate_channel(channel)
            if channel in self._leases:
                raise PermissionError(f"Cannot change CH{channel} role while it is leased.")
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
        with self._lock:
            if self._leases:
                raise PermissionError("Cannot confirm a bench profile while channels are leased.")
            if name:
                self.bench_profile.name = name
            for config in self.bench_profile.channels.values():
                if config.role != ROLE_UNUSED:
                    config.confirmed = True
            self.bench_profile.confirmed_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.bench_profile.requires_confirmation = False
            return self.bench_profile

    def load_profile(self, profile: BenchProfile) -> BenchProfile:
        with self._lock:
            if self._leases:
                raise PermissionError("Cannot load a bench profile while channels are leased.")
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
        with self._lock:
            channel = self.validate_channel(channel)
            config = self.bench_profile.channels.get(channel, BenchChannel())
            if config.role == ROLE_UNUSED or not config.confirmed:
                raise PermissionError(f"CH{channel} must have a confirmed role before it can be leased.")
            if config.role != role:
                raise PermissionError(f"CH{channel} is assigned to {config.role}, not {role}.")
            existing = self._leases.get(channel)
            if existing is not None:
                if existing.owner != owner:
                    raise PermissionError(f"CH{channel} is already leased by {existing.owner}.")
                if existing.role != role:
                    raise PermissionError(f"CH{channel} is already leased as {existing.role}.")
                return existing
            lease = ChannelLease(channel=channel, owner=owner, role=role, lease_id=uuid4().hex)
            self._leases[channel] = lease
            return lease

    def release(self, *, channel: int, lease_id: str) -> None:
        with self._lock:
            channel = self.validate_channel(channel)
            existing = self._leases.get(channel)
            if existing is None:
                return
            if existing.lease_id != lease_id:
                raise PermissionError(f"Lease mismatch for CH{channel}.")
            self._leases.pop(channel, None)
            self._pending_currents.pop(channel, None)
            self._polling.pop(channel, None)
            self._recompute_polling_locked(reason="lease_released")

    def _require_lease(self, *, channel: int, lease_id: str) -> BenchChannel:
        channel = self.validate_channel(channel)
        lease = self._leases.get(channel)
        if lease is None or lease.lease_id != lease_id:
            raise PermissionError(f"A valid lease is required before controlling CH{channel}.")
        config = self.bench_profile.channels[channel]
        if not config.confirmed or config.role == ROLE_UNUSED:
            raise PermissionError(f"CH{channel} wiring is not confirmed.")
        if config.role != lease.role:
            raise PermissionError(f"CH{channel} lease role {lease.role} no longer matches {config.role}.")
        return config

    def _check_limits(
        self,
        *,
        channel: int,
        config: BenchChannel,
        voltage_v: float | None = None,
        current_a: float | None = None,
    ) -> None:
        if voltage_v is not None and config.voltage_limit_v is not None:
            if float(voltage_v) > float(config.voltage_limit_v) + 1e-9:
                raise PermissionError(
                    f"requested voltage exceeds CH{channel} limit "
                    f"({float(voltage_v):.6g} V > {float(config.voltage_limit_v):.6g} V)"
                )
        if current_a is not None and config.current_limit_a is not None:
            if float(current_a) > float(config.current_limit_a) + 1e-12:
                raise PermissionError(
                    f"requested current exceeds CH{channel} limit "
                    f"({float(current_a):.6g} A > {float(config.current_limit_a):.6g} A)"
                )

    def configure_channel(
        self,
        *,
        channel: int,
        lease_id: str,
        voltage_v: float,
        current_a: float,
        output_on: bool,
    ) -> None:
        with self._lock:
            config = self._require_lease(channel=channel, lease_id=lease_id)
            self._check_limits(
                channel=channel,
                config=config,
                voltage_v=voltage_v,
                current_a=current_a,
            )
            self.driver.configure_channel(
                channel=channel,
                voltage_v=voltage_v,
                current_a=current_a,
                output_on=output_on,
            )

    def set_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
        with self._lock:
            config = self._require_lease(channel=channel, lease_id=lease_id)
            self._check_limits(
                channel=channel,
                config=config,
                current_a=float(current_mA) / 1000.0,
            )
            self.driver.set_current_mA(channel=channel, current_mA=current_mA)
            self._setpoint_currents_mA[channel] = max(0.0, float(current_mA))

    def set_output(self, *, channel: int, lease_id: str, output_on: bool) -> None:
        with self._lock:
            self._require_lease(channel=channel, lease_id=lease_id)
            self.driver.set_output(channel=channel, output_on=output_on)

    def output_state(self, *, channel: int) -> bool | None:
        with self._lock:
            channel = self.validate_channel(channel)
            config = self.bench_profile.channels.get(channel, BenchChannel())
            if config.role == ROLE_UNUSED or not config.confirmed:
                raise PermissionError(f"CH{channel} must be confirmed before output-state readback.")
            return self.driver.output_state(channel=channel)

    def measure_channel(self, *, channel: int) -> dict[str, float | None]:
        return self._measure_channel_at(channel=channel, now_s=time.monotonic())

    def _measure_channel_at(self, *, channel: int, now_s: float) -> dict[str, float | None]:
        with self._lock:
            channel = self.validate_channel(channel)
            config = self.bench_profile.channels.get(channel, BenchChannel())
            if config.role == ROLE_UNUSED or not config.confirmed:
                raise PermissionError(f"CH{channel} must be confirmed before measurement.")
            readback = self.driver.measure(channel=channel)
            readback["timestamp_s"] = float(now_s)
            readback["cached"] = False
            self._readbacks[channel] = readback
            return dict(readback)

    @staticmethod
    def _validate_requested_hz(requested_hz: float) -> float:
        requested = float(requested_hz)
        if requested not in {1.0, 2.0}:
            raise ValueError("Shared HMP readback rate must be 1 Hz or 2 Hz.")
        return requested

    @staticmethod
    def _fair_polling_rates(requested: dict[int, float]) -> dict[int, float]:
        if not requested:
            return {}
        remaining = float(MAX_TOTAL_READBACK_HZ)
        unresolved = set(requested)
        allocated: dict[int, float] = {}
        while unresolved:
            share = remaining / len(unresolved)
            satisfied = {
                channel for channel in unresolved if requested[channel] <= share + 1e-12
            }
            if not satisfied:
                for channel in unresolved:
                    allocated[channel] = max(0.0, share)
                break
            for channel in satisfied:
                allocated[channel] = requested[channel]
                remaining -= requested[channel]
                unresolved.remove(channel)
        return allocated

    def _polling_preview_locked(
        self,
        *,
        channel: int,
        requested_hz: float,
        owner: str,
        role: str,
    ) -> dict[str, object]:
        requested = {
            existing_channel: state.requested_hz
            for existing_channel, state in self._polling.items()
            if existing_channel in self._leases
        }
        requested[channel] = requested_hz
        effective = self._fair_polling_rates(requested)
        changes: list[dict[str, object]] = []
        for existing_channel, state in self._polling.items():
            after_hz = effective.get(existing_channel, 0.0)
            if abs(after_hz - state.effective_hz) <= 1e-12:
                continue
            changes.append(
                {
                    "channel": existing_channel,
                    "owner": state.owner,
                    "role": state.role,
                    "before_hz": state.effective_hz,
                    "after_hz": after_hz,
                }
            )
        existing = self._polling.get(channel)
        current_hz = 0.0 if existing is None else existing.effective_hz
        candidate = {
            "channel": channel,
            "owner": owner,
            "role": role,
            "requested_hz": requested_hz,
            "effective_hz": effective[channel],
            "before_hz": current_hz,
        }
        downgrades = [
            change
            for change in changes
            if int(change["channel"]) != channel
            and float(change["after_hz"]) + 1e-12 < float(change["before_hz"])
        ]
        candidate_is_limited = effective[channel] + 1e-12 < requested_hz
        return {
            "capacity_hz": MAX_TOTAL_READBACK_HZ,
            "candidate": candidate,
            "changes": changes,
            "downgrades": downgrades,
            "requires_confirmation": bool(downgrades) or candidate_is_limited,
            "active_pollers": len(requested),
        }

    def preview_polling(
        self,
        *,
        channel: int,
        requested_hz: float,
        owner: str,
        role: str,
    ) -> dict[str, object]:
        with self._lock:
            channel = self.validate_channel(channel)
            requested = self._validate_requested_hz(requested_hz)
            return self._polling_preview_locked(
                channel=channel,
                requested_hz=requested,
                owner=str(owner),
                role=str(role),
            )

    def _record_cadence_event_locked(
        self,
        *,
        channel: int,
        owner: str,
        role: str,
        requested_hz: float,
        before_hz: float,
        after_hz: float,
        reason: str,
    ) -> None:
        self._cadence_generation += 1
        self._cadence_events.append(
            {
                "generation": self._cadence_generation,
                "timestamp_s": time.monotonic(),
                "channel": channel,
                "owner": owner,
                "role": role,
                "requested_hz": requested_hz,
                "before_hz": before_hz,
                "after_hz": after_hz,
                "reason": reason,
            }
        )

    def _recompute_polling_locked(self, *, reason: str) -> None:
        requested = {
            channel: state.requested_hz
            for channel, state in self._polling.items()
            if channel in self._leases
        }
        rates = self._fair_polling_rates(requested)
        for channel in list(self._polling):
            state = self._polling[channel]
            if channel not in rates:
                self._polling.pop(channel, None)
                continue
            before_hz = state.effective_hz
            after_hz = rates[channel]
            state.effective_hz = after_hz
            if abs(before_hz - after_hz) > 1e-12:
                state.next_due_s = None
                self._record_cadence_event_locked(
                    channel=channel,
                    owner=state.owner,
                    role=state.role,
                    requested_hz=state.requested_hz,
                    before_hz=before_hz,
                    after_hz=after_hz,
                    reason=reason,
                )

    def configure_polling(
        self,
        *,
        channel: int,
        lease_id: str,
        requested_hz: float,
    ) -> dict[str, object]:
        with self._lock:
            self._require_lease(channel=channel, lease_id=lease_id)
            lease = self._leases[int(channel)]
            requested = self._validate_requested_hz(requested_hz)
            existing = self._polling.get(int(channel))
            self._polling[int(channel)] = PollState(
                channel=int(channel),
                lease_id=str(lease_id),
                owner=lease.owner,
                role=lease.role,
                requested_hz=requested,
                effective_hz=0.0 if existing is None else existing.effective_hz,
                next_due_s=None,
                last_polled_s=None if existing is None else existing.last_polled_s,
            )
            self._recompute_polling_locked(reason="polling_configured")
            return self.polling_status(channel=int(channel))

    def disable_polling(self, *, channel: int, lease_id: str) -> None:
        with self._lock:
            self._require_lease(channel=channel, lease_id=lease_id)
            self._polling.pop(int(channel), None)
            self._recompute_polling_locked(reason="polling_disabled")

    def polling_status(self, *, channel: int) -> dict[str, object]:
        with self._lock:
            channel = self.validate_channel(channel)
            state = self._polling.get(channel)
            return {
                "generation": self._cadence_generation,
                "capacity_hz": MAX_TOTAL_READBACK_HZ,
                "active_pollers": len(self._polling),
                "polling": None if state is None else state.to_dict(),
            }

    def schedule_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
        with self._lock:
            config = self._require_lease(channel=channel, lease_id=lease_id)
            current = max(0.0, float(current_mA))
            self._check_limits(
                channel=channel,
                config=config,
                current_a=current / 1000.0,
            )
            self._pending_currents[int(channel)] = PendingCurrent(
                lease_id=str(lease_id),
                current_mA=current,
                requested_at_s=time.monotonic(),
            )

    def latest_readback(
        self,
        *,
        channel: int,
        max_age_s: float | None = None,
        fallback_to_measure: bool = True,
        now_s: float | None = None,
    ) -> dict[str, float | None | bool | object]:
        with self._lock:
            channel = self.validate_channel(channel)
            now = time.monotonic() if now_s is None else float(now_s)
            readback = self._readbacks.get(channel)
            pending = self._pending_currents.get(channel)
            setpoint = self._setpoint_currents_mA.get(channel)
            if readback is not None:
                timestamp = readback.get("timestamp_s")
                age_s = None if timestamp is None else max(0.0, now - float(timestamp))
                if max_age_s is None or age_s is None or age_s <= float(max_age_s):
                    payload: dict[str, float | None | bool | object] = dict(readback)
                    payload["cached"] = True
                    payload["age_s"] = age_s
                    payload["setpoint_current_mA"] = setpoint
                    payload["pending_current_mA"] = None if pending is None else pending.current_mA
                    payload["cadence"] = self.polling_status(channel=channel)
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
                    "cadence": self.polling_status(channel=channel),
                }
            measured: dict[str, float | None | bool | object] = self._measure_channel_at(
                channel=channel,
                now_s=now,
            )
            measured["age_s"] = 0.0
            measured["setpoint_current_mA"] = self._setpoint_currents_mA.get(channel)
            measured["pending_current_mA"] = None
            measured["cadence"] = self.polling_status(channel=channel)
            return measured

    def process_scheduler_once(self, *, now_s: float | None = None) -> None:
        with self._lock:
            now = time.monotonic() if now_s is None else float(now_s)
            for channel, pending in list(self._pending_currents.items()):
                self.set_current(
                    channel=channel,
                    lease_id=pending.lease_id,
                    current_mA=pending.current_mA,
                )
                if self._pending_currents.get(channel) is pending:
                    self._pending_currents.pop(channel, None)
            due_channels = sorted(
                (
                    channel
                    for channel, state in self._polling.items()
                    if state.effective_hz > 0.0
                    and (state.next_due_s is None or state.next_due_s <= now)
                ),
                key=lambda channel: (
                    float("-inf")
                    if self._polling[channel].next_due_s is None
                    else self._polling[channel].next_due_s,
                    channel,
                ),
            )
            for channel in due_channels:
                state = self._polling.get(channel)
                if state is None or state.effective_hz <= 0.0:
                    continue
                self._measure_channel_at(channel=channel, now_s=now)
                state.last_polled_s = now
                state.next_due_s = now + (1.0 / state.effective_hz)

    def start_scheduler(self, *, tick_s: float = 0.05) -> None:
        with self._lock:
            if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
                return
            self._scheduler_stop.clear()
            interval = max(0.001, float(tick_s))

            def _run() -> None:
                while not self._scheduler_stop.wait(interval):
                    try:
                        self.process_scheduler_once()
                        with self._lock:
                            self._scheduler_error = None
                    except Exception as exc:
                        with self._lock:
                            self._scheduler_error = f"{type(exc).__name__}: {exc}"

            self._scheduler_thread = Thread(target=_run, name="shared-hmp-scheduler", daemon=True)
            self._scheduler_thread.start()

    def stop_scheduler(self) -> None:
        with self._lock:
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
        with self._lock:
            if intent != "emergency_stop_all":
                raise PermissionError("Emergency all-output stop requires explicit emergency_stop_all intent.")
            for channel in range(1, self.profile.channel_count + 1):
                self.driver.set_output(channel=channel, output_on=False)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "model": self.profile.profile_id,
                "channel_count": self.profile.channel_count,
                "bench_profile": self.bench_profile.to_dict(),
                "leases": {str(channel): lease.to_dict() for channel, lease in self._leases.items()},
                "readbacks": {str(channel): dict(readback) for channel, readback in self._readbacks.items()},
                "scheduler": {
                    "running": self._scheduler_thread is not None and self._scheduler_thread.is_alive(),
                    "capacity_hz": MAX_TOTAL_READBACK_HZ,
                    "generation": self._cadence_generation,
                    "polling": {
                        str(channel): state.to_dict()
                        for channel, state in self._polling.items()
                    },
                    "pending_currents": {
                        str(channel): current.to_dict()
                        for channel, current in self._pending_currents.items()
                    },
                    "setpoint_currents_mA": {
                        str(channel): current
                        for channel, current in self._setpoint_currents_mA.items()
                    },
                    "events": list(self._cadence_events),
                    "last_error": self._scheduler_error,
                },
            }
