"""Live viewer for the MLX90640 thermal camera bridge."""

from __future__ import annotations

import math
import queue
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Iterable

from PyQt6 import QtCore, QtGui, QtWidgets

from experiments.mlx90640_calibration import MLX90640Calibration
from plotting.shared.theme import ensure_app_theme
from plotting.shared.utils import install_standard_menu

try:
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover - optional runtime dependency guard
    serial = None  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]


FRAME_WIDTH = 32
FRAME_HEIGHT = 24
FRAME_PIXELS = FRAME_WIDTH * FRAME_HEIGHT
ROI_WIDTH = 16
ROI_START_COL = (FRAME_WIDTH - ROI_WIDTH) // 2
BINARY_MAGIC = b"MLX4"
BINARY_VERSION = 1
BINARY_HEADER = struct.Struct("<4sBBBBIIh")
BINARY_PACKET_SIZE = BINARY_HEADER.size + (FRAME_PIXELS * 2) + 2
RAW_MAGIC = b"MLXR"
EEPROM_MAGIC = b"MLXE"
RAW_VERSION = 1
RAW_HEADER = struct.Struct("<4sBBHIIIHHI")
RAW_FRAME_WORDS = 832
RAW_PIXEL_WORDS = FRAME_PIXELS
RAW_AUX_WORDS = RAW_FRAME_WORDS - RAW_PIXEL_WORDS
RAW_REQUIRED_AUX_START = 768
RAW_REQUIRED_AUX_END = 810
RAW_REQUIRED_AUX_WORDS = RAW_REQUIRED_AUX_END - RAW_REQUIRED_AUX_START + 1
RAW_ROI_COMPACT_WORDS = ((FRAME_HEIGHT // 2) * ROI_WIDTH) + RAW_REQUIRED_AUX_WORDS
RAW_COMPACT_WORDS = RAW_ROI_COMPACT_WORDS
RAW_FULL_WIDTH_COMPACT_WORDS = (RAW_PIXEL_WORDS // 2) + RAW_REQUIRED_AUX_WORDS
RAW_LEGACY_COMPACT_WORDS = (RAW_PIXEL_WORDS // 2) + RAW_AUX_WORDS
RAW_PAYLOAD_BYTES = RAW_FRAME_WORDS * 2
RAW_COMPACT_PAYLOAD_BYTES = RAW_COMPACT_WORDS * 2
RAW_FULL_WIDTH_COMPACT_PAYLOAD_BYTES = RAW_FULL_WIDTH_COMPACT_WORDS * 2
RAW_LEGACY_COMPACT_PAYLOAD_BYTES = RAW_LEGACY_COMPACT_WORDS * 2
RAW_PACKET_SIZE = RAW_HEADER.size + RAW_PAYLOAD_BYTES + 2
EEPROM_PACKET_SIZE = RAW_PACKET_SIZE
RAW_FLAG_SUBPAGE_1 = 0x01
RAW_FLAG_COMPACT = 0x40
RAW_FLAG_OVERRUN = 0x80


def _raw_compact_geometry(words: int) -> tuple[int, int] | None:
    if words == RAW_FRAME_WORDS:
        return FRAME_WIDTH, 0
    if words == RAW_LEGACY_COMPACT_WORDS:
        return FRAME_WIDTH, 0
    if words == RAW_FULL_WIDTH_COMPACT_WORDS:
        return FRAME_WIDTH, 0
    data_words = words - RAW_REQUIRED_AUX_WORDS
    subpage_rows = FRAME_HEIGHT // 2
    if data_words <= 0 or data_words % subpage_rows != 0:
        return None
    row_width = data_words // subpage_rows
    if row_width <= 0 or row_width > FRAME_WIDTH:
        return None
    return row_width, (FRAME_WIDTH - row_width) // 2


def _raw_payload_len_is_valid(words: int, payload_len: int) -> bool:
    if words == RAW_FRAME_WORDS:
        return payload_len == RAW_PAYLOAD_BYTES
    if words == RAW_LEGACY_COMPACT_WORDS:
        return payload_len == RAW_LEGACY_COMPACT_PAYLOAD_BYTES
    geometry = _raw_compact_geometry(words)
    return geometry is not None and payload_len == words * 2
BINARY_TEXT = "text"
BINARY_FAST = "binary"
CUBE_RAW = "cube_raw"
MLX90614_TEXT = "mlx90614_text"
BAUD_RATES = (115200, 230400, 460800, 921600, 1000000, 2000000)
REFRESH_RATES = ((16, 5), (32, 6), (64, 7))
MLX90614_INTERVALS = (
    ("10 Hz", 3),
    ("50 Hz", 5),
    ("100 Hz", 6),
    ("Max stream", 7),
)


@dataclass(frozen=True)
class ThermalFrame:
    """One complete MLX90640 frame parsed from the Nucleo text stream."""

    elapsed_ms: int | None
    ambient_c: float | None
    values: tuple[float, ...]
    unit: str = "C"
    raw_read_us: int | None = None
    sequence: int | None = None
    flags: int = 0
    raw_words: tuple[int, ...] = ()
    raw_pixel_mask: tuple[bool, ...] = ()
    status: int = 0
    control: int = 0
    width: int = FRAME_WIDTH
    height: int = FRAME_HEIGHT
    roi_start_col: int = 0

    @property
    def minimum_c(self) -> float:
        return min(value for value in self.values if math.isfinite(value))

    @property
    def maximum_c(self) -> float:
        return max(value for value in self.values if math.isfinite(value))

    @property
    def mean_c(self) -> float:
        finite = [value for value in self.values if math.isfinite(value)]
        return sum(finite) / len(finite) if finite else math.nan


class FrameRateTracker:
    """Track frame cadence using device timestamps when the stream provides them."""

    def __init__(self, sample_limit: int = 8) -> None:
        self.sample_limit = sample_limit
        self._last_elapsed_ms: int | None = None
        self._last_received_at: float | None = None
        self._samples: list[float] = []

    def reset(self) -> None:
        self._last_elapsed_ms = None
        self._last_received_at = None
        self._samples = []

    def record(self, frame: ThermalFrame, *, received_at: float | None = None) -> float:
        now = time.monotonic() if received_at is None else received_at
        sample: float | None = None
        if frame.elapsed_ms is not None and self._last_elapsed_ms is not None:
            delta_ms = frame.elapsed_ms - self._last_elapsed_ms
            if delta_ms > 0:
                sample = 1000.0 / delta_ms
        elif self._last_received_at is not None:
            delta_s = now - self._last_received_at
            if delta_s > 0:
                sample = 1.0 / delta_s

        self._last_elapsed_ms = frame.elapsed_ms
        self._last_received_at = now
        if sample is not None:
            self._samples.append(sample)
            self._samples = self._samples[-self.sample_limit :]
        return self.fps

    @property
    def fps(self) -> float:
        return sum(self._samples) / len(self._samples) if self._samples else 0.0


def parse_frame_lines(lines: Iterable[str]) -> ThermalFrame | None:
    """Parse one ``FRAME_BEGIN``/``ROW``/``FRAME_END`` block."""

    elapsed_ms: int | None = None
    ambient_c: float | None = None
    rows: list[list[float]] = []
    in_frame = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("FRAME_BEGIN"):
            parts = line.split(",")
            elapsed_ms = _parse_int(parts[1]) if len(parts) > 1 else None
            ambient_c = _parse_float(parts[2]) if len(parts) > 2 else None
            rows = []
            in_frame = True
            continue
        if line == "FRAME_END":
            if in_frame and len(rows) == FRAME_HEIGHT and all(
                len(row) == FRAME_WIDTH for row in rows
            ):
                values = tuple(value for row in rows for value in row)
                if len(values) == FRAME_PIXELS:
                    return ThermalFrame(elapsed_ms, ambient_c, values)
            return None
        if in_frame and line.startswith("ROW,"):
            parts = line.split(",")
            if len(parts) != FRAME_WIDTH + 2:
                return None
            row_index = _parse_int(parts[1])
            if row_index is None or row_index != len(rows):
                return None
            try:
                rows.append([float(part) for part in parts[2:]])
            except ValueError:
                return None

    return None


def parse_mlx90614_line(line: str) -> ThermalFrame | None:
    """Parse one text line from the MLX90614 serial probe firmware.

    Expected format:
    ``MLX90614,<seq>,<elapsed_ms>,<read_us>,<ambient_c>,<object_c>,<raw_ta>,<raw_to>,<flags>``.
    """

    parts = [part.strip() for part in line.strip().split(",")]
    if len(parts) < 6 or parts[0] != "MLX90614":
        return None
    sequence = _parse_int(parts[1])
    elapsed_ms = _parse_int(parts[2])
    read_us = _parse_int(parts[3])
    ambient_c = _parse_float(parts[4])
    object_c = _parse_float(parts[5])
    if (ambient_c is None or object_c is None) and len(parts) > 7:
        raw_ambient = _parse_int(parts[6])
        raw_object = _parse_int(parts[7])
        if raw_ambient is not None and raw_object is not None:
            ambient_c = (raw_ambient * 0.02) - 273.15
            object_c = (raw_object * 0.02) - 273.15
    if ambient_c is None or object_c is None:
        return None
    flags = _parse_int(parts[8]) if len(parts) > 8 else 0
    return ThermalFrame(
        elapsed_ms,
        ambient_c,
        (object_c,),
        "C",
        read_us,
        sequence,
        flags or 0,
        (),
        (),
        0,
        0,
        1,
        1,
        0,
    )


def parse_binary_frame(packet: bytes) -> ThermalFrame | None:
    """Parse one fixed-size binary MLX90640 frame packet."""

    if len(packet) != BINARY_PACKET_SIZE:
        return None
    expected_checksum = int.from_bytes(packet[-2:], "little", signed=False)
    actual_checksum = sum(packet[:-2]) & 0xFFFF
    if expected_checksum != actual_checksum:
        return None
    try:
        (
            magic,
            version,
            width,
            height,
            _flags,
            _sequence,
            elapsed_ms,
            ambient_centi,
        ) = BINARY_HEADER.unpack_from(packet, 0)
    except struct.error:
        return None
    if (
        magic != BINARY_MAGIC
        or version != BINARY_VERSION
        or width != FRAME_WIDTH
        or height != FRAME_HEIGHT
    ):
        return None
    values_offset = BINARY_HEADER.size
    values_end = values_offset + (FRAME_PIXELS * 2)
    try:
        raw_values = struct.unpack_from(f"<{FRAME_PIXELS}h", packet, values_offset)
    except struct.error:
        return None
    values = tuple(value / 100.0 for value in raw_values)
    ambient_c = ambient_centi / 100.0
    return ThermalFrame(int(elapsed_ms), ambient_c, values)


def _s16_word(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def parse_raw_cube_frame(packet: bytes) -> ThermalFrame | None:
    """Parse one STM32Cube ``MLXR`` raw frame-RAM packet."""

    if len(packet) < RAW_HEADER.size + 2 or len(packet) > RAW_PACKET_SIZE:
        return None
    expected_checksum = int.from_bytes(packet[-2:], "little", signed=False)
    actual_checksum = sum(packet[:-2]) & 0xFFFF
    if expected_checksum != actual_checksum:
        return None
    try:
        (
            magic,
            version,
            flags,
            words,
            sequence,
            elapsed_ms,
            read_us,
            status,
            control,
            payload_len,
        ) = RAW_HEADER.unpack_from(packet, 0)
    except struct.error:
        return None
    if (
        magic != RAW_MAGIC
        or version != RAW_VERSION
        or _raw_compact_geometry(words) is None
        or not _raw_payload_len_is_valid(words, payload_len)
        or len(packet) != RAW_HEADER.size + payload_len + 2
    ):
        return None

    values_offset = RAW_HEADER.size
    try:
        payload_words = struct.unpack_from(f">{words}H", packet, values_offset)
    except struct.error:
        return None
    if words == RAW_FRAME_WORDS:
        raw_words = tuple(int(value) for value in payload_words)
        raw_pixel_mask = tuple(True for _ in range(FRAME_PIXELS))
    else:
        raw_words_list = [0] * RAW_FRAME_WORDS
        raw_pixel_mask_list = [False] * FRAME_PIXELS
        cursor = 0
        subpage = int(flags & RAW_FLAG_SUBPAGE_1)
        geometry = _raw_compact_geometry(words)
        if geometry is None:
            return None
        row_width, row_start_col = geometry
        for row in range(subpage, FRAME_HEIGHT, 2):
            row_offset = row * FRAME_WIDTH + row_start_col
            raw_words_list[row_offset : row_offset + row_width] = payload_words[cursor : cursor + row_width]
            raw_pixel_mask_list[row_offset : row_offset + row_width] = [True] * row_width
            cursor += row_width
        if words == RAW_LEGACY_COMPACT_WORDS:
            raw_words_list[RAW_PIXEL_WORDS:RAW_FRAME_WORDS] = payload_words[cursor : cursor + RAW_AUX_WORDS]
        else:
            raw_words_list[RAW_REQUIRED_AUX_START : RAW_REQUIRED_AUX_END + 1] = payload_words[
                cursor : cursor + RAW_REQUIRED_AUX_WORDS
            ]
        raw_words = tuple(raw_words_list)
        raw_pixel_mask = tuple(raw_pixel_mask_list)
    raw_values = tuple(_s16_word(value) for value in raw_words[:FRAME_PIXELS])
    return ThermalFrame(
        int(elapsed_ms),
        None,
        tuple(float(value) for value in raw_values),
        "raw",
        int(read_us),
        int(sequence),
        int(flags),
        tuple(int(value) for value in raw_words),
        raw_pixel_mask,
        int(status),
        int(control),
    )


def parse_cube_eeprom_packet(packet: bytes) -> tuple[int, ...] | None:
    """Parse one STM32Cube ``MLXE`` EEPROM calibration packet."""

    if len(packet) != RAW_PACKET_SIZE:
        return None
    expected_checksum = int.from_bytes(packet[-2:], "little", signed=False)
    actual_checksum = sum(packet[:-2]) & 0xFFFF
    if expected_checksum != actual_checksum:
        return None
    try:
        (
            magic,
            version,
            _flags,
            words,
            _sequence,
            _elapsed_ms,
            _read_us,
            _status,
            _control,
            payload_len,
        ) = RAW_HEADER.unpack_from(packet, 0)
    except struct.error:
        return None
    if (
        magic != EEPROM_MAGIC
        or version != RAW_VERSION
        or words != RAW_FRAME_WORDS
        or payload_len != RAW_PAYLOAD_BYTES
    ):
        return None
    try:
        return tuple(int(value) for value in struct.unpack_from(f">{RAW_FRAME_WORDS}H", packet, RAW_HEADER.size))
    except struct.error:
        return None


def pop_binary_frames(buffer: bytearray) -> list[ThermalFrame]:
    """Extract all complete binary frames from ``buffer`` in-place."""

    frames: list[ThermalFrame] = []
    while True:
        start = buffer.find(BINARY_MAGIC)
        if start < 0:
            if len(buffer) > len(BINARY_MAGIC):
                del buffer[:-len(BINARY_MAGIC)]
            return frames
        if start:
            del buffer[:start]
        if len(buffer) < BINARY_PACKET_SIZE:
            return frames
        packet = bytes(buffer[:BINARY_PACKET_SIZE])
        frame = parse_binary_frame(packet)
        if frame is None:
            del buffer[0]
            continue
        frames.append(frame)
        del buffer[:BINARY_PACKET_SIZE]


def pop_cube_packets(buffer: bytearray) -> tuple[list[tuple[int, ...]], list[ThermalFrame]]:
    """Extract STM32Cube EEPROM and raw frame packets from ``buffer`` in-place."""

    eeprom_packets: list[tuple[int, ...]] = []
    frames: list[ThermalFrame] = []
    while True:
        starts = [index for index in (buffer.find(RAW_MAGIC), buffer.find(EEPROM_MAGIC)) if index >= 0]
        start = min(starts) if starts else -1
        if start < 0:
            if len(buffer) > len(RAW_MAGIC):
                del buffer[:-len(RAW_MAGIC)]
            return eeprom_packets, frames
        if start:
            del buffer[:start]
        if len(buffer) < RAW_HEADER.size:
            return eeprom_packets, frames
        try:
            magic, _version, _flags, words, *_rest, payload_len = RAW_HEADER.unpack_from(buffer, 0)
        except struct.error:
            return eeprom_packets, frames
        if magic not in {RAW_MAGIC, EEPROM_MAGIC} or _raw_compact_geometry(words) is None:
            del buffer[0]
            continue
        packet_size = RAW_HEADER.size + int(payload_len) + 2
        if not _raw_payload_len_is_valid(words, payload_len) or packet_size > RAW_PACKET_SIZE:
            del buffer[0]
            continue
        if len(buffer) < packet_size:
            return eeprom_packets, frames
        packet = bytes(buffer[:packet_size])
        if packet.startswith(EEPROM_MAGIC):
            eeprom_words = parse_cube_eeprom_packet(packet)
            if eeprom_words is None:
                del buffer[0]
                continue
            eeprom_packets.append(eeprom_words)
        else:
            frame = parse_raw_cube_frame(packet)
            if frame is None:
                del buffer[0]
                continue
            frames.append(frame)
        del buffer[:packet_size]


def pop_raw_cube_frames(buffer: bytearray) -> list[ThermalFrame]:
    """Extract all complete STM32Cube raw frames from ``buffer`` in-place."""

    _eeprom_packets, frames = pop_cube_packets(buffer)
    return frames


def _active_columns_for_mask(pixel_mask: Sequence[bool]) -> list[int]:
    return [
        column
        for column in range(FRAME_WIDTH)
        if any(pixel_mask[row * FRAME_WIDTH + column] for row in range(FRAME_HEIGHT))
    ]


def _display_values_for_mask(values: Sequence[float], pixel_mask: Sequence[bool]) -> tuple[tuple[float, ...], int, int]:
    active_columns = _active_columns_for_mask(pixel_mask)
    if not active_columns:
        return tuple(values), FRAME_WIDTH, 0
    start_col = min(active_columns)
    end_col = max(active_columns) + 1
    width = end_col - start_col
    cropped = []
    for row in range(FRAME_HEIGHT):
        offset = row * FRAME_WIDTH
        cropped.extend(values[offset + start_col : offset + end_col])
    return tuple(cropped), width, start_col


def _parse_int(text: str) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _parse_float(text: str) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _heat_color(value: float, minimum: float, maximum: float) -> QtGui.QColor:
    if maximum > minimum:
        t = (value - minimum) / (maximum - minimum)
    else:
        t = 0.0
    t = min(1.0, max(0.0, t))
    red = int(255 * min(1.0, max(0.0, 1.5 * t - 0.25)))
    green = int(255 * min(1.0, max(0.0, 1.5 - abs(3.0 * t - 1.5))))
    blue = int(255 * min(1.0, max(0.0, 1.25 - 1.5 * t)))
    return QtGui.QColor(red, green, blue)


def render_frame_pixmap(
    frame: ThermalFrame,
    *,
    minimum_c: float | None = None,
    maximum_c: float | None = None,
    scale: int = 20,
) -> QtGui.QPixmap:
    """Render the thermal frame as a nearest-neighbour heatmap pixmap."""

    minimum = frame.minimum_c if minimum_c is None else float(minimum_c)
    maximum = frame.maximum_c if maximum_c is None else float(maximum_c)
    image = QtGui.QImage(
        frame.width,
        frame.height,
        QtGui.QImage.Format.Format_RGB32,
    )
    for y in range(frame.height):
        offset = y * frame.width
        for x in range(frame.width):
            image.setPixelColor(x, y, _heat_color(frame.values[offset + x], minimum, maximum))
    pixmap = QtGui.QPixmap.fromImage(image)
    return pixmap.scaled(
        frame.width * scale,
        frame.height * scale,
        QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
        QtCore.Qt.TransformationMode.FastTransformation,
    )


class ThermalSerialWorker(QtCore.QObject):
    """Read thermal frames from a serial port on a background thread."""

    frame_ready = QtCore.pyqtSignal(object)
    status_changed = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, port_name: str, baudrate: int, protocol: str, refresh_code: int = 5) -> None:
        super().__init__()
        self.port_name = port_name
        self.baudrate = int(baudrate)
        self.protocol = protocol
        self.refresh_code = int(refresh_code)
        self._refresh_requests: queue.SimpleQueue[int] = queue.SimpleQueue()
        self._stop = Event()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        if serial is None:
            self.status_changed.emit("pyserial is not available.")
            self.finished.emit()
            return
        try:
            with serial.Serial(
                self.port_name,
                self.baudrate,
                timeout=1.0,
                write_timeout=1.0,
            ) as port:
                self.status_changed.emit(
                    f"Connected to {self.port_name} at {self.baudrate} baud."
                )
                if self.protocol in {BINARY_FAST, CUBE_RAW}:
                    self._run_binary(port)
                elif self.protocol == MLX90614_TEXT:
                    self._run_mlx90614_text(port)
                else:
                    self._run_text(port)
        except Exception as exc:
            self.status_changed.emit(f"Serial error: {exc}")
        finally:
            self.finished.emit()

    def _run_text(self, port: object) -> None:
        buffer: list[str] = []
        in_frame = False
        while not self._stop.is_set():
            raw = port.readline()
            if not raw:
                continue
            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if line.startswith("FRAME_BEGIN"):
                buffer = [line]
                in_frame = True
                continue
            if not in_frame:
                continue
            buffer.append(line)
            if line == "FRAME_END":
                frame = parse_frame_lines(buffer)
                if frame is not None:
                    self.frame_ready.emit(frame)
                else:
                    self.status_changed.emit("Skipped malformed thermal frame.")
                buffer = []
                in_frame = False

    def _run_mlx90614_text(self, port: object) -> None:
        self._write_refresh_code(port, self.refresh_code)
        while not self._stop.is_set():
            self._send_pending_refresh_requests(port)
            raw = port.readline()
            if not raw:
                continue
            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if not line:
                continue
            if line.startswith("MLX90614_BOOT") or line.startswith("MLX90614_STREAM"):
                self.status_changed.emit(line)
                continue
            if line.startswith("MLX90614_ERROR"):
                self.status_changed.emit(line)
                continue
            frame = parse_mlx90614_line(line)
            if frame is not None:
                self.frame_ready.emit(frame)
            elif line.startswith("MLX90614"):
                self.status_changed.emit("Skipped malformed MLX90614 sample.")

    def _run_binary(self, port: object) -> None:
        buffer = bytearray()
        calibrator: MLX90640Calibration | None = None
        celsius_values: list[float] | None = None
        if self.protocol == CUBE_RAW:
            self._write_refresh_code(port, self.refresh_code)
        while not self._stop.is_set():
            if self.protocol == CUBE_RAW:
                self._send_pending_refresh_requests(port)
            chunk = port.read(4096)
            if not chunk:
                continue
            buffer.extend(chunk)
            if self.protocol == CUBE_RAW:
                eeprom_packets, frames = pop_cube_packets(buffer)
                for eeprom_words in eeprom_packets:
                    try:
                        calibrator = MLX90640Calibration(eeprom_words)
                        celsius_values = None
                        self.status_changed.emit("Loaded MLX90640 EEPROM calibration.")
                    except Exception as exc:
                        calibrator = None
                        celsius_values = None
                        self.status_changed.emit(f"Skipped invalid MLX90640 calibration: {exc}")
                packet_size = RAW_PACKET_SIZE
            else:
                frames = pop_binary_frames(buffer)
                packet_size = BINARY_PACKET_SIZE
            for frame in frames:
                if self.protocol == CUBE_RAW and calibrator is not None:
                    converted, celsius_values = self._convert_raw_frame(
                        frame,
                        calibrator,
                        celsius_values,
                    )
                    if converted is not None:
                        self.frame_ready.emit(converted)
                    continue
                self.frame_ready.emit(frame)
            if len(buffer) > packet_size * 4:
                del buffer[:-packet_size]

    def _convert_raw_frame(
        self,
        frame: ThermalFrame,
        calibrator: MLX90640Calibration,
        previous_values: list[float] | None,
    ) -> tuple[ThermalFrame | None, list[float] | None]:
        try:
            subpage_values = calibrator.calculate_to(frame.raw_words, frame.control, frame.status)
            ambient_c = calibrator.ambient_c(frame.raw_words, frame.control, frame.status)
        except Exception as exc:
            self.status_changed.emit(f"Skipped Celsius conversion: {exc}")
            return frame, previous_values
        if not (-60.0 <= ambient_c <= 125.0):
            self.status_changed.emit(
                f"Skipped Celsius conversion: invalid ambient {ambient_c:.2f} C."
            )
            return frame, previous_values

        values = [math.nan] * FRAME_PIXELS if previous_values is None else list(previous_values)
        pixel_mask = frame.raw_pixel_mask or tuple(True for _ in range(FRAME_PIXELS))
        for index, value in enumerate(subpage_values):
            if pixel_mask[index] and math.isfinite(value):
                values[index] = value
        active_columns = _active_columns_for_mask(pixel_mask)
        complete_mask = [
            (row * FRAME_WIDTH) + column
            for row in range(FRAME_HEIGHT)
            for column in active_columns
        ] or list(range(FRAME_PIXELS))
        if any(not math.isfinite(values[index]) for index in complete_mask):
            return None, values
        active_values = [values[index] for index in complete_mask]
        if min(active_values) < -100.0 or max(active_values) > 500.0:
            self.status_changed.emit("Skipped Celsius conversion: calibrated pixels are out of range.")
            return frame, previous_values
        display_values, display_width, roi_start_col = _display_values_for_mask(values, pixel_mask)
        return ThermalFrame(
            frame.elapsed_ms,
            ambient_c,
            display_values,
            "C",
            frame.raw_read_us,
            frame.sequence,
            frame.flags,
            frame.raw_words,
            frame.raw_pixel_mask,
            frame.status,
            frame.control,
            display_width,
            FRAME_HEIGHT,
            roi_start_col,
        ), values

    def stop(self) -> None:
        self._stop.set()

    def request_refresh_code(self, refresh_code: int) -> None:
        self._refresh_requests.put(int(refresh_code))

    def _send_pending_refresh_requests(self, port: object) -> None:
        latest: int | None = None
        while True:
            try:
                latest = self._refresh_requests.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self.refresh_code = latest
            self._write_refresh_code(port, latest)

    def _write_refresh_code(self, port: object, refresh_code: int) -> None:
        valid_codes = {1, 2, 3, 4, 5, 6, 7} if self.protocol == MLX90614_TEXT else {5, 6, 7}
        if refresh_code not in valid_codes:
            return
        try:
            port.write(f"{refresh_code}\n".encode("ascii"))
            sensor = "MLX90614 interval" if self.protocol == MLX90614_TEXT else "MLX90640 refresh"
            self.status_changed.emit(f"Requested {sensor} code {refresh_code}.")
        except Exception as exc:
            self.status_changed.emit(f"Could not send rate command: {exc}")


class ThermalCameraViewer(QtWidgets.QWidget):
    """Experiment window for live MLX90640 thermal camera viewing."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Thermal Camera Viewer")
        self.resize(920, 650)

        self.settings = QtCore.QSettings("MicrowireLab", "ThermalCameraViewer")
        self._thread: QtCore.QThread | None = None
        self._worker: ThermalSerialWorker | None = None
        self._latest_frame: ThermalFrame | None = None
        self._fps_tracker = FrameRateTracker()

        self._build_ui()
        self._load_settings()
        self.refresh_ports()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._save_settings()
        self._disconnect()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        controls = QtWidgets.QHBoxLayout()
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setMinimumWidth(180)
        self.refresh_button = QtWidgets.QPushButton("Refresh ports")
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.export_button = QtWidgets.QPushButton("Export frame")
        self.export_button.setEnabled(False)
        self.baud_combo = QtWidgets.QComboBox()
        for baudrate in BAUD_RATES:
            self.baud_combo.addItem(str(baudrate), baudrate)
        self.baud_combo.setCurrentIndex(self.baud_combo.findData(921600))
        self.protocol_combo = QtWidgets.QComboBox()
        self.protocol_combo.addItem("Cube raw", CUBE_RAW)
        self.protocol_combo.addItem("MLX90614 spot thermometer", MLX90614_TEXT)
        self.protocol_combo.addItem("Arduino binary", BINARY_FAST)
        self.protocol_combo.addItem("Text frame dump", BINARY_TEXT)
        self.refresh_rate_combo = QtWidgets.QComboBox()
        self._populate_rate_options(CUBE_RAW)
        controls.addWidget(QtWidgets.QLabel("Port"))
        controls.addWidget(self.port_combo, 1)
        controls.addWidget(self.refresh_button)
        controls.addWidget(QtWidgets.QLabel("Protocol"))
        controls.addWidget(self.protocol_combo)
        controls.addWidget(QtWidgets.QLabel("Rate"))
        controls.addWidget(self.refresh_rate_combo)
        controls.addWidget(QtWidgets.QLabel("Baud"))
        controls.addWidget(self.baud_combo)
        controls.addWidget(self.connect_button)
        controls.addWidget(self.export_button)
        root.addLayout(controls)

        scale_row = QtWidgets.QHBoxLayout()
        self.auto_scale_cb = QtWidgets.QCheckBox("Auto scale")
        self.auto_scale_cb.setChecked(True)
        self.min_spin = QtWidgets.QDoubleSpinBox()
        self.min_spin.setRange(-100.0, 500.0)
        self.min_spin.setDecimals(1)
        self.min_spin.setValue(20.0)
        self.max_spin = QtWidgets.QDoubleSpinBox()
        self.max_spin.setRange(-100.0, 500.0)
        self.max_spin.setDecimals(1)
        self.max_spin.setValue(80.0)
        self.stats_label = QtWidgets.QLabel("No frame yet")
        self.stats_label.setMinimumWidth(420)
        scale_row.addWidget(self.auto_scale_cb)
        scale_row.addWidget(QtWidgets.QLabel("Min C"))
        scale_row.addWidget(self.min_spin)
        scale_row.addWidget(QtWidgets.QLabel("Max C"))
        scale_row.addWidget(self.max_spin)
        scale_row.addStretch(1)
        scale_row.addWidget(self.stats_label)
        root.addLayout(scale_row)

        self.image_label = QtWidgets.QLabel()
        self.image_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(FRAME_WIDTH * 20, FRAME_HEIGHT * 20)
        self.image_label.setStyleSheet("QLabel { background: #111827; }")
        root.addWidget(self.image_label, 1)

        self.status_label = QtWidgets.QLabel(
            "Connect to the Cube raw firmware on COM10 at 2000000 baud."
        )
        root.addWidget(self.status_label)

        self.refresh_button.clicked.connect(self.refresh_ports)
        self.protocol_combo.currentIndexChanged.connect(self._protocol_changed)
        self.refresh_rate_combo.currentIndexChanged.connect(self._refresh_rate_changed)
        self.connect_button.clicked.connect(self._toggle_connection)
        self.export_button.clicked.connect(self._export_frame)
        self.auto_scale_cb.toggled.connect(self._update_scale_controls)
        self.min_spin.valueChanged.connect(lambda _value: self._render_latest())
        self.max_spin.valueChanged.connect(lambda _value: self._render_latest())
        self._update_scale_controls()

        install_standard_menu(
            self,
            help_topic="experiment_thermal_camera_viewer",
            open_folder=self._open_downloads,
        )

    def _load_settings(self) -> None:
        protocol = self.settings.value("protocol", CUBE_RAW, type=str)
        index = self.protocol_combo.findData(protocol)
        self.protocol_combo.setCurrentIndex(max(0, index))
        baudrate = self.settings.value("baudrate", 2000000, type=int)
        if protocol == BINARY_FAST and baudrate == 921600:
            protocol = CUBE_RAW
            index = self.protocol_combo.findData(protocol)
            self.protocol_combo.setCurrentIndex(max(0, index))
            baudrate = 2000000
        self._set_baudrate(baudrate)
        refresh_code = self.settings.value("refresh_code", 5, type=int)
        self._set_refresh_code(refresh_code)
        self.auto_scale_cb.setChecked(self.settings.value("auto_scale", True, type=bool))
        self.min_spin.setValue(self.settings.value("fixed_min_c", 20.0, type=float))
        self.max_spin.setValue(self.settings.value("fixed_max_c", 80.0, type=float))
        self._update_scale_controls()

    def _save_settings(self) -> None:
        self.settings.setValue("baudrate", self._baudrate())
        self.settings.setValue("protocol", self.protocol_combo.currentData() or BINARY_FAST)
        self.settings.setValue("refresh_code", self._refresh_code())
        self.settings.setValue("auto_scale", self.auto_scale_cb.isChecked())
        self.settings.setValue("fixed_min_c", self.min_spin.value())
        self.settings.setValue("fixed_max_c", self.max_spin.value())
        port = self.port_combo.currentData()
        if isinstance(port, str):
            self.settings.setValue("port", port)

    def _protocol_changed(self) -> None:
        protocol = self.protocol_combo.currentData()
        previous_code = self._refresh_code()
        self._populate_rate_options(protocol if isinstance(protocol, str) else CUBE_RAW)
        if protocol == CUBE_RAW:
            self._set_baudrate(2000000)
        elif protocol == MLX90614_TEXT:
            self._set_baudrate(2000000)
        elif protocol == BINARY_FAST and self._baudrate() == 115200:
            self._set_baudrate(921600)
        elif protocol == BINARY_TEXT and self._baudrate() != 115200:
            self._set_baudrate(115200)
        self._set_refresh_code(previous_code)
        self.refresh_rate_combo.setEnabled(protocol in {CUBE_RAW, MLX90614_TEXT})

    def _populate_rate_options(self, protocol: str) -> None:
        current_code = self._refresh_code()
        self.refresh_rate_combo.blockSignals(True)
        self.refresh_rate_combo.clear()
        if protocol == MLX90614_TEXT:
            for label, code in MLX90614_INTERVALS:
                self.refresh_rate_combo.addItem(label, code)
        else:
            for label_hz, code in REFRESH_RATES:
                suffix = " experimental" if label_hz == 64 else ""
                self.refresh_rate_combo.addItem(f"{label_hz} Hz{suffix}", code)
        self.refresh_rate_combo.blockSignals(False)
        self._set_refresh_code(current_code)

    def _refresh_rate_changed(self) -> None:
        self.settings.setValue("refresh_code", self._refresh_code())
        if self._worker is not None:
            self._worker.request_refresh_code(self._refresh_code())

    def _refresh_code(self) -> int:
        refresh_code = self.refresh_rate_combo.currentData()
        return int(refresh_code) if isinstance(refresh_code, int) else 5

    def _set_refresh_code(self, refresh_code: int) -> None:
        index = self.refresh_rate_combo.findData(int(refresh_code))
        if index < 0:
            index = self.refresh_rate_combo.findData(5)
        self.refresh_rate_combo.setCurrentIndex(max(0, index))

    def _baudrate(self) -> int:
        baudrate = self.baud_combo.currentData()
        return int(baudrate) if isinstance(baudrate, int) else 2000000

    def _set_baudrate(self, baudrate: int) -> None:
        index = self.baud_combo.findData(int(baudrate))
        if index < 0:
            index = self.baud_combo.findData(2000000)
        self.baud_combo.setCurrentIndex(max(0, index))

    def refresh_ports(self) -> None:
        current = self.settings.value("port", "COM10", type=str)
        if self.port_combo.currentData():
            current = str(self.port_combo.currentData())
        self.port_combo.clear()
        if list_ports is None:
            self.port_combo.addItem("pyserial unavailable", "")
            return
        ports = list(list_ports.comports())
        for port in ports:
            label = f"{port.device} - {port.description}"
            self.port_combo.addItem(label, port.device)
        if not ports:
            self.port_combo.addItem("No serial ports found", "")
            return
        index = self.port_combo.findData(current)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)
        else:
            fallback = self.port_combo.findData("COM10")
            if fallback >= 0:
                self.port_combo.setCurrentIndex(fallback)

    def _toggle_connection(self) -> None:
        if self._worker is not None:
            self._disconnect()
            return
        port = self.port_combo.currentData()
        if not isinstance(port, str) or not port:
            QtWidgets.QMessageBox.warning(self, self.windowTitle(), "Select a serial port first.")
            return
        self._save_settings()
        self._thread = QtCore.QThread(self)
        protocol = self.protocol_combo.currentData()
        self._worker = ThermalSerialWorker(
            port,
            self._baudrate(),
            protocol if isinstance(protocol, str) else BINARY_FAST,
            self._refresh_code(),
        )
        self._fps_tracker.reset()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.frame_ready.connect(self._handle_frame)
        self._worker.status_changed.connect(self.status_label.setText)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        self.connect_button.setText("Disconnect")
        self.port_combo.setEnabled(False)
        self.protocol_combo.setEnabled(False)
        self.refresh_rate_combo.setEnabled(protocol == CUBE_RAW)
        self.baud_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)

    def _disconnect(self) -> None:
        thread = self._thread
        worker = self._worker
        if worker is not None:
            worker.stop()
        if thread is not None:
            thread.quit()
            thread.wait(1500)
        self._worker = None
        self._thread = None
        self.connect_button.setText("Connect")
        self.port_combo.setEnabled(True)
        self.protocol_combo.setEnabled(True)
        self.refresh_rate_combo.setEnabled(self.protocol_combo.currentData() in {CUBE_RAW, MLX90614_TEXT})
        self.baud_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)

    @QtCore.pyqtSlot()
    def _on_worker_finished(self) -> None:
        self._worker = None
        self._thread = None
        self.connect_button.setText("Connect")
        self.port_combo.setEnabled(True)
        self.protocol_combo.setEnabled(True)
        self.refresh_rate_combo.setEnabled(self.protocol_combo.currentData() in {CUBE_RAW, MLX90614_TEXT})
        self.baud_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)

    @QtCore.pyqtSlot(object)
    def _handle_frame(self, frame: ThermalFrame) -> None:
        self._fps_tracker.record(frame)
        self._latest_frame = frame
        self.export_button.setEnabled(True)
        self._render_latest()

    def _render_latest(self) -> None:
        frame = self._latest_frame
        if frame is None:
            return
        minimum = None if self.auto_scale_cb.isChecked() else self.min_spin.value()
        maximum = None if self.auto_scale_cb.isChecked() else self.max_spin.value()
        pixmap = render_frame_pixmap(frame, minimum_c=minimum, maximum_c=maximum, scale=20)
        self.image_label.setPixmap(pixmap)
        fps = self._fps_tracker.fps
        ambient = "--" if frame.ambient_c is None else f"{frame.ambient_c:.2f} C"
        read_time = "" if frame.raw_read_us is None else f" | Read {frame.raw_read_us / 1000.0:.2f} ms"
        overrun = " | Overrun" if frame.flags & RAW_FLAG_OVERRUN else ""
        self.stats_label.setText(
            f"Min {frame.minimum_c:.2f} {frame.unit} | Mean {frame.mean_c:.2f} {frame.unit} | "
            f"Max {frame.maximum_c:.2f} {frame.unit} | Ambient {ambient} | {fps:.2f} fps"
            f"{read_time}{overrun}"
        )

    def _update_scale_controls(self) -> None:
        fixed = not self.auto_scale_cb.isChecked()
        self.min_spin.setEnabled(fixed)
        self.max_spin.setEnabled(fixed)
        self._render_latest()

    def _export_frame(self) -> None:
        frame = self._latest_frame
        if frame is None:
            return
        downloads = _downloads_path()
        downloads.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        prefix = "mlx90614_live" if frame.width == 1 and frame.height == 1 else "mlx90640_live"
        png_path = downloads / f"{prefix}_{timestamp}.png"
        txt_path = downloads / f"{prefix}_{timestamp}_frame.txt"
        minimum = None if self.auto_scale_cb.isChecked() else self.min_spin.value()
        maximum = None if self.auto_scale_cb.isChecked() else self.max_spin.value()
        pixmap = render_frame_pixmap(frame, minimum_c=minimum, maximum_c=maximum, scale=20)
        pixmap.save(str(png_path), "PNG")
        _write_frame_text(txt_path, frame)
        self.status_label.setText(f"Exported {png_path}")

    def _open_downloads(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(_downloads_path())))


def _downloads_path() -> Path:
    return Path.home() / "Downloads"


def _write_frame_text(path: Path, frame: ThermalFrame) -> None:
    if frame.width == 1 and frame.height == 1:
        ambient = "--" if frame.ambient_c is None else f"{frame.ambient_c:.2f}"
        path.write_text(
            "\n".join(
                [
                    f"MLX90614_SAMPLE,{frame.sequence or 0},{frame.elapsed_ms or 0}",
                    f"AMBIENT_C,{ambient}",
                    f"OBJECT_C,{frame.values[0]:.2f}",
                    f"READ_US,{frame.raw_read_us or 0}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return
    lines = [
        f"FRAME_BEGIN,{frame.elapsed_ms or 0},{frame.ambient_c or 0:.2f}",
        f"WIDTH,{frame.width}",
        f"HEIGHT,{frame.height}",
        f"ROI_START_COL,{frame.roi_start_col}",
    ]
    for y in range(frame.height):
        offset = y * frame.width
        row = frame.values[offset : offset + frame.width]
        lines.append("ROW," + str(y) + "," + ",".join(f"{value:.2f}" for value in row))
    lines.append("FRAME_END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> QtWidgets.QWidget | None:
    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication([])
    ensure_app_theme(app)
    window = ThermalCameraViewer()
    window.show()
    if owns_app:
        app.exec()
    return window


__all__ = [
    "ThermalCameraViewer",
    "ThermalFrame",
    "FrameRateTracker",
    "EEPROM_MAGIC",
    "EEPROM_PACKET_SIZE",
    "main",
    "parse_binary_frame",
    "parse_cube_eeprom_packet",
    "parse_mlx90614_line",
    "parse_raw_cube_frame",
    "parse_frame_lines",
    "pop_binary_frames",
    "pop_cube_packets",
    "pop_raw_cube_frames",
    "render_frame_pixmap",
]


if __name__ == "__main__":  # pragma: no cover - manual launch helper
    main()
