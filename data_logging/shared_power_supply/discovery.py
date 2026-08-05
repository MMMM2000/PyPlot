from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


HAMEG_HO720_VID = 0x0403
HAMEG_HO720_PID = 0xED72


@dataclass(frozen=True)
class SerialPortIdentity:
    device: str
    description: str = ""
    manufacturer: str = ""
    hwid: str = ""
    vid: int | None = None
    pid: int | None = None


def is_native_hmp_usb(identity: SerialPortIdentity) -> bool:
    """Return whether a port is the HMP's native USB-D virtual COM interface."""

    if identity.vid == HAMEG_HO720_VID and identity.pid == HAMEG_HO720_PID:
        return True
    text = " ".join(
        (identity.description, identity.manufacturer, identity.hwid)
    ).casefold()
    vendor = "hameg" in text or "rohde" in text
    interface = "ho720" in text or "vcp" in text or "0403:ed72" in text
    return vendor and interface


def hmp_port_preference_key(identity: SerialPortIdentity) -> tuple[int, str]:
    """Prefer native USB-D, while keeping all other port ordering deterministic."""

    return (0 if is_native_hmp_usb(identity) else 1, identity.device.casefold())


def sort_hmp_port_identities(
    identities: Iterable[SerialPortIdentity],
) -> tuple[SerialPortIdentity, ...]:
    return tuple(sorted(identities, key=hmp_port_preference_key))
