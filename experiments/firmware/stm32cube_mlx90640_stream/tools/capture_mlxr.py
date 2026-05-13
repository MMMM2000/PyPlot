from __future__ import annotations

import argparse
import statistics
import struct
import time

import serial


MAGIC = b"MLXR"
HEADER = struct.Struct("<4sBBHIIIHHI")
HEADER_SIZE = HEADER.size
PAYLOAD_BYTES = 832 * 2
PACKET_SIZE = HEADER_SIZE + PAYLOAD_BYTES + 2


def pop_packets(buffer: bytearray) -> list[dict[str, int]]:
    packets: list[dict[str, int]] = []
    while True:
        start = buffer.find(MAGIC)
        if start < 0:
            if len(buffer) > len(MAGIC):
                del buffer[:-len(MAGIC)]
            return packets
        if start:
            del buffer[:start]
        if len(buffer) < PACKET_SIZE:
            return packets

        packet = bytes(buffer[:PACKET_SIZE])
        checksum = int.from_bytes(packet[-2:], "little")
        if (sum(packet[:-2]) & 0xFFFF) != checksum:
            del buffer[0]
            continue

        (
            _magic,
            version,
            flags,
            words,
            sequence,
            elapsed_ms,
            read_us,
            status,
            control,
            payload_len,
        ) = HEADER.unpack_from(packet)
        if version != 1 or words != 832 or payload_len != PAYLOAD_BYTES:
            del buffer[0]
            continue

        packets.append(
            {
                "flags": flags,
                "sequence": sequence,
                "elapsed_ms": elapsed_ms,
                "read_us": read_us,
                "status": status,
                "control": control,
            }
        )
        del buffer[:PACKET_SIZE]


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture STM32Cube MLX90640 raw packets.")
    parser.add_argument("port")
    parser.add_argument("--baud", type=int, default=2_000_000)
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()

    deadline = time.monotonic() + args.seconds
    buffer = bytearray()
    packets: list[dict[str, int]] = []

    with serial.Serial(args.port, args.baud, timeout=0.05) as port:
        port.reset_input_buffer()
        while time.monotonic() < deadline:
            chunk = port.read(8192)
            if chunk:
                buffer.extend(chunk)
                packets.extend(pop_packets(buffer))

    if not packets:
        print("packets=0")
        return 1

    elapsed = [packet["elapsed_ms"] for packet in packets]
    read_us = [packet["read_us"] for packet in packets]
    sequence = [packet["sequence"] for packet in packets]
    duration_ms = max(elapsed) - min(elapsed)
    fps = (len(packets) - 1) * 1000.0 / duration_ms if duration_ms > 0 else 0.0
    dropped = max(sequence) - min(sequence) + 1 - len(set(sequence))
    overrun = sum(1 for packet in packets if packet["flags"] & 0x80)
    subpage_counts = {
        0: sum(1 for packet in packets if (packet["flags"] & 0x01) == 0),
        1: sum(1 for packet in packets if (packet["flags"] & 0x01) == 1),
    }

    print(f"packets={len(packets)}")
    print(f"fps={fps:.2f}")
    print(f"dropped_sequences={dropped}")
    print(f"overrun_packets={overrun}")
    print(f"subpages={subpage_counts}")
    print(f"read_us_mean={statistics.mean(read_us):.0f}")
    print(f"read_us_min={min(read_us)}")
    print(f"read_us_max={max(read_us)}")
    print(f"first_status=0x{packets[0]['status']:04X}")
    print(f"first_control=0x{packets[0]['control']:04X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
