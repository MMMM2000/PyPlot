"""Probe a GW Instek LCR-6000 series meter over its virtual COM port."""

from __future__ import annotations

import argparse
import sys

from data_logging.ac_susceptibility_logger.lcr6000 import (
    DEFAULT_BAUDRATE,
    Lcr6000Serial,
    Lcr6000Settings,
    available_serial_ports,
    first_lcr_port,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", nargs="?", help="Serial port, e.g. COM7. Defaults to first LCR candidate.")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUDRATE, help="Serial baud rate.")
    parser.add_argument("--fetch", action="store_true", help="Also read FETC:IMP? after *IDN?.")
    parser.add_argument("--configure", action="store_true", help="Apply a simple Ls-Q, 1 kHz, 0.1 V setup first.")
    parser.add_argument("--list", action="store_true", help="List detected serial ports and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ports = available_serial_ports()
    if args.list:
        for port in ports:
            print(port.label)
        return 0
    port_name = args.port
    if not port_name:
        candidate = first_lcr_port(ports)
        if candidate is None:
            print("No serial ports found. Install the LCR-6000 USB VCP driver and reconnect the meter.", file=sys.stderr)
            return 2
        port_name = candidate.device
    meter = Lcr6000Serial(port_name, baudrate=args.baud)
    try:
        print(f"Port: {port_name}")
        print(f"*IDN?: {meter.identify()}")
        if args.configure:
            meter.configure(Lcr6000Settings(frequency_hz=1000.0, level_value=0.1))
            print("Configured: Ls-Q, 1000 Hz, 0.1 V")
        if args.fetch:
            reading = meter.fetch_impedance()
            print(f"FETC:IMP?: {reading.raw}")
            print(
                "Parsed: "
                f"primary={reading.primary}, secondary={reading.secondary}, "
                f"monitor1={reading.monitor1}, monitor2={reading.monitor2}, "
                f"comparator={reading.comparator}"
            )
    finally:
        meter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
