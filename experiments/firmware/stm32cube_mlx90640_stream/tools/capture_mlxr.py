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
LEGACY_COMPACT_PAYLOAD_BYTES = 448 * 2
MAX_PACKET_SIZE = HEADER_SIZE + PAYLOAD_BYTES + 2
REQUIRED_AUX_WORDS = 43
SUBPAGE_ROWS = 12
TEXT_STATUS_PREFIX = "MLX90640_CUBE_"


def extract_status_lines(chunk: bytes) -> list[str]:
    text = chunk.decode("ascii", errors="ignore")
    lines = []
    for line in text.replace("\r", "\n").split("\n"):
        stripped = line.strip()
        prefix_index = stripped.find(TEXT_STATUS_PREFIX)
        if prefix_index >= 0:
            lines.append(stripped[prefix_index:])
    return lines


def payload_len_is_valid(words: int, payload_len: int) -> bool:
    if words == 832:
        return payload_len == PAYLOAD_BYTES
    if words == 448:
        return payload_len == LEGACY_COMPACT_PAYLOAD_BYTES
    data_words = words - REQUIRED_AUX_WORDS
    if data_words <= 0 or data_words % SUBPAGE_ROWS != 0:
        return False
    row_width = data_words // SUBPAGE_ROWS
    return 0 < row_width <= 32 and payload_len == words * 2


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
        if len(buffer) < HEADER_SIZE:
            return packets

        try:
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
            ) = HEADER.unpack_from(buffer)
        except struct.error:
            return packets
        packet_size = HEADER_SIZE + payload_len + 2
        if (
            version != 1
            or not payload_len_is_valid(words, payload_len)
            or packet_size > MAX_PACKET_SIZE
        ):
            del buffer[0]
            continue
        if len(buffer) < packet_size:
            return packets

        packet = bytes(buffer[:packet_size])
        checksum = int.from_bytes(packet[-2:], "little")
        if (sum(packet[:-2]) & 0xFFFF) != checksum:
            del buffer[0]
            continue

        packets.append(
            {
                "flags": flags,
                "words": words,
                "sequence": sequence,
                "elapsed_ms": elapsed_ms,
                "read_us": read_us,
                "status": status,
                "control": control,
            }
        )
        del buffer[:packet_size]


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture STM32Cube MLX90640 raw packets.")
    parser.add_argument("port")
    parser.add_argument("--baud", type=int, default=2_000_000)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--refresh-code", type=int, choices=(5, 6, 7), help="Request MLX90640 refresh code 5, 6, or 7 before capturing.")
    args = parser.parse_args()

    deadline = time.monotonic() + args.seconds
    buffer = bytearray()
    packets: list[dict[str, int]] = []
    status_lines: list[str] = []
    seen_status_lines: set[str] = set()

    with serial.Serial(args.port, args.baud, timeout=0.05) as port:
        port.reset_input_buffer()
        if args.refresh_code is not None:
            port.write(f"{args.refresh_code}\n".encode("ascii"))
            port.flush()
            time.sleep(0.25)
            port.reset_input_buffer()
        while time.monotonic() < deadline:
            chunk = port.read(8192)
            if chunk:
                for line in extract_status_lines(chunk):
                    if line not in seen_status_lines:
                        status_lines.append(line)
                        seen_status_lines.add(line)
                buffer.extend(chunk)
                packets.extend(pop_packets(buffer))

    if not packets:
        print("packets=0")
        for line in status_lines:
            print(f"status={line}")
        return 1

    elapsed = [packet["elapsed_ms"] for packet in packets]
    read_us = [packet["read_us"] for packet in packets]
    sequence = [packet["sequence"] for packet in packets]
    duration_ms = max(elapsed) - min(elapsed)
    fps = (len(packets) - 1) * 1000.0 / duration_ms if duration_ms > 0 else 0.0
    dropped = max(sequence) - min(sequence) + 1 - len(set(sequence))
    overrun = sum(1 for packet in packets if packet["flags"] & 0x80)
    compact = sum(1 for packet in packets if packet["flags"] & 0x40)
    subpage_counts = {
        0: sum(1 for packet in packets if (packet["flags"] & 0x01) == 0),
        1: sum(1 for packet in packets if (packet["flags"] & 0x01) == 1),
    }

    print(f"packets={len(packets)}")
    print(f"fps={fps:.2f}")
    print(f"dropped_sequences={dropped}")
    print(f"overrun_packets={overrun}")
    print(f"compact_packets={compact}")
    print(f"subpages={subpage_counts}")
    print(f"read_us_mean={statistics.mean(read_us):.0f}")
    print(f"read_us_min={min(read_us)}")
    print(f"read_us_max={max(read_us)}")
    print(f"first_status=0x{packets[0]['status']:04X}")
    print(f"first_control=0x{packets[0]['control']:04X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
