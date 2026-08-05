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
from .discovery import (
    SerialPortIdentity,
    hmp_port_preference_key,
    is_native_hmp_usb,
    sort_hmp_port_identities,
)

__all__ = [
    "BenchChannel",
    "BenchProfile",
    "ChannelLease",
    "HMP4030_PROFILE",
    "HMP4040_PROFILE",
    "HMP_PROFILES",
    "SerialPortIdentity",
    "SharedPowerSupplyBroker",
    "SupplyProfile",
    "detect_hmp_profile",
    "hmp_port_preference_key",
    "is_native_hmp_usb",
    "sort_hmp_port_identities",
]
