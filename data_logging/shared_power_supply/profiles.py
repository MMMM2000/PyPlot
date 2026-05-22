from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupplyProfile:
    """Static HMP model capabilities used by the broker."""

    profile_id: str
    label: str
    channel_count: int
    max_voltage_v: float
    current_resolution_mA: float
    baudrate: int = 115200

    def validate_channel(self, channel: int) -> int:
        channel = int(channel)
        if channel < 1 or channel > self.channel_count:
            raise ValueError(
                f"Channel CH{channel} is not valid for {self.label}; "
                f"valid channels are CH1-CH{self.channel_count}."
            )
        return channel


HMP4030_PROFILE = SupplyProfile(
    profile_id="hmp4030",
    label="HMP4030",
    channel_count=3,
    max_voltage_v=32.05,
    current_resolution_mA=0.2,
)
HMP4040_PROFILE = SupplyProfile(
    profile_id="hmp4040",
    label="HMP4040",
    channel_count=4,
    max_voltage_v=32.05,
    current_resolution_mA=0.2,
)
HMP_PROFILES: dict[str, SupplyProfile] = {
    HMP4030_PROFILE.profile_id: HMP4030_PROFILE,
    HMP4040_PROFILE.profile_id: HMP4040_PROFILE,
}


def detect_hmp_profile(idn_text: str) -> SupplyProfile | None:
    upper_text = str(idn_text or "").upper()
    if "HMP4040" in upper_text:
        return HMP4040_PROFILE
    if "HMP4030" in upper_text or "HAMEG" in upper_text:
        return HMP4030_PROFILE
    return None
