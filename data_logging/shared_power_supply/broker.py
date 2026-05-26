from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from .driver import HmpSerialDriver
from .profiles import HMP_PROFILES, SupplyProfile


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
        if config.voltage_limit_v is not None and voltage_v > config.voltage_limit_v:
            raise ValueError(f"Requested voltage exceeds CH{channel} limit.")
        if config.current_limit_a is not None and current_a > config.current_limit_a:
            raise ValueError(f"Requested current exceeds CH{channel} limit.")
        self.driver.configure_channel(
            channel=channel,
            voltage_v=voltage_v,
            current_a=current_a,
            output_on=output_on,
        )

    def set_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
        config = self._require_lease(channel=channel, lease_id=lease_id)
        if config.current_limit_a is not None and current_mA / 1000.0 > config.current_limit_a:
            raise ValueError(f"Requested current exceeds CH{channel} limit.")
        self.driver.set_current_mA(channel=channel, current_mA=current_mA)

    def set_output(self, *, channel: int, lease_id: str, output_on: bool) -> None:
        self._require_lease(channel=channel, lease_id=lease_id)
        self.driver.set_output(channel=channel, output_on=output_on)

    def measure_channel(self, *, channel: int) -> dict[str, float | None]:
        channel = self.validate_channel(channel)
        config = self.bench_profile.channels.get(channel, BenchChannel())
        if config.role == ROLE_UNUSED or not config.confirmed:
            raise PermissionError(f"CH{channel} must be confirmed before measurement.")
        readback = self.driver.measure(channel=channel)
        self._readbacks[channel] = readback
        return dict(readback)

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
        }
