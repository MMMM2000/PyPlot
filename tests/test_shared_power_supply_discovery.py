from __future__ import annotations

from data_logging.shared_power_supply.discovery import (
    SerialPortIdentity,
    is_native_hmp_usb,
    sort_hmp_port_identities,
)


def test_native_hmp_usb_is_detected_by_ho720_vid_pid() -> None:
    identity = SerialPortIdentity(
        device="COM5",
        description="USB Serial Port",
        vid=0x0403,
        pid=0xED72,
    )

    assert is_native_hmp_usb(identity) is True


def test_native_hmp_usb_is_detected_from_descriptive_metadata() -> None:
    identity = SerialPortIdentity(
        device="COM5",
        description="HAMEG HO720 VCP",
        hwid="USB VID:PID=0403:ED72",
    )

    assert is_native_hmp_usb(identity) is True


def test_usb_to_rs232_adapter_is_not_misclassified_as_native_usb() -> None:
    identity = SerialPortIdentity(
        device="COM3",
        description="USB-SERIAL CH340",
        manufacturer="wch.cn",
        vid=0x1A86,
        pid=0x7523,
    )

    assert is_native_hmp_usb(identity) is False


def test_hmp_discovery_prefers_native_usb_and_keeps_fallbacks() -> None:
    ordered = sort_hmp_port_identities(
        (
            SerialPortIdentity("COM9", "Generic serial"),
            SerialPortIdentity("COM5", "HAMEG HO720 VCP"),
            SerialPortIdentity("COM3", "USB-SERIAL CH340"),
        )
    )

    assert [port.device for port in ordered] == ["COM5", "COM3", "COM9"]
