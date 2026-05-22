"""Shared multi-channel HMP power-supply broker utilities."""

from .broker import (
    BenchChannel,
    BenchProfile,
    ChannelLease,
    SharedPowerSupplyBroker,
)
from .profiles import (
    HMP4030_PROFILE,
    HMP4040_PROFILE,
    HMP_PROFILES,
    SupplyProfile,
    detect_hmp_profile,
)

__all__ = [
    "BenchChannel",
    "BenchProfile",
    "ChannelLease",
    "HMP4030_PROFILE",
    "HMP4040_PROFILE",
    "HMP_PROFILES",
    "SharedPowerSupplyBroker",
    "SupplyProfile",
    "detect_hmp_profile",
]
