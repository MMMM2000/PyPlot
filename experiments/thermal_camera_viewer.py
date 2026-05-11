"""Live viewer for the MLX90640 thermal camera bridge."""

from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Iterable

from PyQt6 import QtCore, QtGui, QtWidgets

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
BINARY_MAGIC = b"MLX4"
BINARY_VERSION = 1
BINARY_HEADER = struct.Struct("<4sBBBBIIh")
BINARY_PACKET_SIZE = BINARY_HEADER.size + (FRAME_PIXELS * 2) + 2
BINARY_TEXT = "text"
BINARY_FAST = "binary"
BAUD_RATES = (115200, 230400, 460800, 921600, 1000000, 2000000)


@dataclass(frozen=True)
class ThermalFrame:
    """One complete MLX90640 frame parsed from the Nucleo text stream."""

    elapsed_ms: int | None
    ambient_c: float | None
    values: tuple[float, ...]

    @property
    def minimum_c(self) -> float:
        return min(self.values)

    @property
    def maximum_c(self) -> float:
        return max(self.values)

    @property
    def mean_c(self) -> float:
        return sum(self.values) / len(self.values)


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
        FRAME_WIDTH,
        FRAME_HEIGHT,
        QtGui.QImage.Format.Format_RGB32,
    )
    for y in range(FRAME_HEIGHT):
        offset = y * FRAME_WIDTH
        for x in range(FRAME_WIDTH):
            image.setPixelColor(x, y, _heat_color(frame.values[offset + x], minimum, maximum))
    pixmap = QtGui.QPixmap.fromImage(image)
    return pixmap.scaled(
        FRAME_WIDTH * scale,
        FRAME_HEIGHT * scale,
        QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
        QtCore.Qt.TransformationMode.FastTransformation,
    )


class ThermalSerialWorker(QtCore.QObject):
    """Read thermal frames from a serial port on a background thread."""

    frame_ready = QtCore.pyqtSignal(object)
    status_changed = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, port_name: str, baudrate: int, protocol: str) -> None:
        super().__init__()
        self.port_name = port_name
        self.baudrate = int(baudrate)
        self.protocol = protocol
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
                if self.protocol == BINARY_FAST:
                    self._run_binary(port)
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

    def _run_binary(self, port: object) -> None:
        buffer = bytearray()
        while not self._stop.is_set():
            chunk = port.read(4096)
            if not chunk:
                continue
            buffer.extend(chunk)
            for frame in pop_binary_frames(buffer):
                self.frame_ready.emit(frame)
            if len(buffer) > BINARY_PACKET_SIZE * 4:
                del buffer[:-BINARY_PACKET_SIZE]

    def stop(self) -> None:
        self._stop.set()


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
        self.protocol_combo.addItem("Binary fast", BINARY_FAST)
        self.protocol_combo.addItem("Text frame dump", BINARY_TEXT)
        controls.addWidget(QtWidgets.QLabel("Port"))
        controls.addWidget(self.port_combo, 1)
        controls.addWidget(self.refresh_button)
        controls.addWidget(QtWidgets.QLabel("Protocol"))
        controls.addWidget(self.protocol_combo)
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
            "Connect to the Nucleo fast binary firmware on COM10 at 921600 baud."
        )
        root.addWidget(self.status_label)

        self.refresh_button.clicked.connect(self.refresh_ports)
        self.protocol_combo.currentIndexChanged.connect(self._protocol_changed)
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
        protocol = self.settings.value("protocol", BINARY_FAST, type=str)
        index = self.protocol_combo.findData(protocol)
        self.protocol_combo.setCurrentIndex(max(0, index))
        self._set_baudrate(self.settings.value("baudrate", 921600, type=int))
        self.auto_scale_cb.setChecked(self.settings.value("auto_scale", True, type=bool))
        self.min_spin.setValue(self.settings.value("fixed_min_c", 20.0, type=float))
        self.max_spin.setValue(self.settings.value("fixed_max_c", 80.0, type=float))
        self._update_scale_controls()

    def _save_settings(self) -> None:
        self.settings.setValue("baudrate", self._baudrate())
        self.settings.setValue("protocol", self.protocol_combo.currentData() or BINARY_FAST)
        self.settings.setValue("auto_scale", self.auto_scale_cb.isChecked())
        self.settings.setValue("fixed_min_c", self.min_spin.value())
        self.settings.setValue("fixed_max_c", self.max_spin.value())
        port = self.port_combo.currentData()
        if isinstance(port, str):
            self.settings.setValue("port", port)

    def _protocol_changed(self) -> None:
        protocol = self.protocol_combo.currentData()
        if protocol == BINARY_FAST and self._baudrate() == 115200:
            self._set_baudrate(921600)
        elif protocol == BINARY_TEXT and self._baudrate() == 921600:
            self._set_baudrate(115200)

    def _baudrate(self) -> int:
        baudrate = self.baud_combo.currentData()
        return int(baudrate) if isinstance(baudrate, int) else 921600

    def _set_baudrate(self, baudrate: int) -> None:
        index = self.baud_combo.findData(int(baudrate))
        if index < 0:
            index = self.baud_combo.findData(921600)
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
        self.baud_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)

    @QtCore.pyqtSlot()
    def _on_worker_finished(self) -> None:
        self._worker = None
        self._thread = None
        self.connect_button.setText("Connect")
        self.port_combo.setEnabled(True)
        self.protocol_combo.setEnabled(True)
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
        self.stats_label.setText(
            f"Min {frame.minimum_c:.2f} C | Mean {frame.mean_c:.2f} C | "
            f"Max {frame.maximum_c:.2f} C | Ambient {ambient} | {fps:.2f} fps"
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
        png_path = downloads / f"mlx90640_live_{timestamp}.png"
        txt_path = downloads / f"mlx90640_live_{timestamp}_frame.txt"
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
    lines = [f"FRAME_BEGIN,{frame.elapsed_ms or 0},{frame.ambient_c or 0:.2f}"]
    for y in range(FRAME_HEIGHT):
        offset = y * FRAME_WIDTH
        row = frame.values[offset : offset + FRAME_WIDTH]
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
    "main",
    "parse_binary_frame",
    "parse_frame_lines",
    "pop_binary_frames",
    "render_frame_pixmap",
]


if __name__ == "__main__":  # pragma: no cover - manual launch helper
    main()
