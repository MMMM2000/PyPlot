from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterable, Mapping

from PyQt6 import QtCore, QtGui, QtWidgets

from plotting.shared.utils import ensure_app_theme, install_standard_menu

try:
    import serial
    from serial import SerialException
    from serial.tools import list_ports
except Exception:  # pragma: no cover - import guard
    serial = None  # type: ignore[assignment]
    SerialException = Exception  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
except Exception:
    try:
        FigureCanvas = getattr(
            import_module("matplotlib.backends.backend_qt5agg"),
            "FigureCanvasQTAgg",
        )
    except Exception:  # pragma: no cover - optional backend fallback
        FigureCanvas = None  # type: ignore[assignment]

try:
    from matplotlib.backends.backend_qt import NavigationToolbar2QT as NavigationToolbar
except Exception:
    try:
        NavigationToolbar = getattr(
            import_module("matplotlib.backends.backend_qt5agg"),
            "NavigationToolbar2QT",
        )
    except Exception:  # pragma: no cover - optional backend fallback
        NavigationToolbar = None  # type: ignore[assignment]

from matplotlib.figure import Figure


APP_NAME = "Mini DMA Logger"
DEFAULT_LOG_BASENAME = "mini_dma"
GRAVITY_MS2 = 9.80665
LONG_NAMES = ("Displacement", "Load", "Strain", "Stress")
UNITS = ("mm", "g", "%", "MPa")
FLOAT_PATTERN = re.compile(r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)")
WINDOWS: list[QtWidgets.QWidget] = []
GNG_SUPPORTED_BAUDS = (600, 1200, 2400, 4800, 9600)
SCALE_NO_DATA_HINT_DELAY_MS = 3500
STALE_SCALE_AFTER_S = 2.0
PROJECT_EXTENSION = ".pydpj"
PRELOAD_PENDING = "pending"
PRELOAD_ACTIVE = "active"
PRELOAD_DISABLED = "disabled"
SUPPLY_PROFILES: dict[str, dict[str, Any]] = {
    "hmp4030": {
        "label": "HMP4030 (original)",
        "start_current_mA": 1.0,
        "min_start_current_mA": 1.0,
        "max_voltage": 30.0,
        "channel_select": 3,
        "reset_on_start": True,
        "voltage_first": False,
    },
    "owon_spe6102": {
        "label": "Owon SPE6102",
        "start_current_mA": 10.0,
        "min_start_current_mA": 10.0,
        "max_voltage": 62.0,
        "channel_select": 0,
        "reset_on_start": False,
        "voltage_first": True,
    },
}
HEATING_MODE_OFF = "off"
HEATING_MODE_CONSTANT = "constant"
HEATING_MODE_RAMP = "ramp"
HEATING_MODE_TRIANGLE = "triangle"
HEATING_MODE_LABELS = {
    HEATING_MODE_OFF: "Off",
    HEATING_MODE_CONSTANT: "Constant current",
    HEATING_MODE_RAMP: "Current ramp",
    HEATING_MODE_TRIANGLE: "Current triangle",
}
HEATING_LIMIT_STOP = "stop"
HEATING_LIMIT_HOLD = "hold"
HEATING_LIMIT_DISABLE = "disable"
HEATING_LIMIT_LABELS = {
    HEATING_LIMIT_STOP: "Stop recipe",
    HEATING_LIMIT_HOLD: "Hold current",
    HEATING_LIMIT_DISABLE: "Turn output off",
}
HSW_BASIS_LOAD_G = "load_g"
HSW_BASIS_STRESS_MPA = "stress_mpa"
HSW_BASIS_STRAIN_PCT = "strain_pct"
HSW_BASIS_LABELS = {
    HSW_BASIS_LOAD_G: "Load (g)",
    HSW_BASIS_STRESS_MPA: "Stress (MPa)",
    HSW_BASIS_STRAIN_PCT: "Strain (%)",
}
PROJECT_ROW_DIAMETER_KEYS = ("d (µm)", "d (um)", "d", "Diameter", "diameter_um")
PROJECT_ROW_CURRENT_KEYS = (
    "Stress/strain current (mA)",
    "Current (mA)",
    "Fracture stress/strain current (mA)",
)
PROJECT_ROW_MICROWIRE_KEYS = ("Microwire", "Wire")
PROJECT_ROW_SPECIMEN_KEYS = ("Specimen", "Sample", "Piece", "Sample name")


def _default_download_dir() -> str:
    home = Path.home()
    candidates = [
        Path(os.environ.get("USERPROFILE", "")) / "Downloads",
        home / "Downloads",
        home / "downloads",
    ]
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_dir():
                return str(candidate)
        except Exception:
            continue
    fallback = home / "Downloads"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(fallback)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _find_ticcmd() -> str:
    candidates = [
        shutil.which("ticcmd"),
        r"C:\Program Files (x86)\Pololu\Tic\bin\ticcmd.exe",
        r"C:\Program Files\Pololu\Tic\bin\ticcmd.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if Path(candidate).exists():
            return str(candidate)
    return "ticcmd"


def _decode_escape_text(text: str) -> bytes:
    if not text:
        return b""
    normalized = text.encode("utf-8").decode("unicode_escape")
    return normalized.encode("utf-8")


def _parse_first_float(text: str) -> float | None:
    match = FLOAT_PATTERN.search(text)
    if not match:
        return None
    token = match.group(0).replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None


def _safe_float(value: Any) -> float | None:
    try:
        numeric = float(str(value).replace(",", "."))
    except Exception:
        return None
    return numeric if math.isfinite(numeric) else None


def _normalized_token(text: Any) -> str:
    token = str(text or "").strip().lower()
    token = token.replace("_", "").replace("-", "").replace(" ", "")
    return token


def _normalized_microwire_token(text: Any) -> str:
    token = str(text or "").strip().lower()
    token = token.replace("_", "/").replace("-", "/")
    token = re.sub(r"\s+", "", token)
    return token


def _normalized_column_key(text: Any) -> str:
    token = str(text or "").strip().lower()
    token = token.replace("µ", "u").replace("μ", "u").replace("?", "u")
    return re.sub(r"[^a-z0-9]+", "", token)


def _project_row_value(row: Mapping[str, Any], aliases: Iterable[str]) -> Any:
    alias_map = {_normalized_column_key(alias): alias for alias in aliases}
    for key, value in row.items():
        if _normalized_column_key(key) in alias_map:
            return value
    for alias in aliases:
        if alias in row:
            return row.get(alias)
    return None


def _extract_status_value(text: str, label: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


def _extract_first_int(text: str) -> int | None:
    match = re.search(r"[-+]?\d+", text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _read_serial_bytes(
    port_name: str,
    *,
    baudrate: int,
    payload: bytes,
    timeout_s: float = 0.35,
    total_wait_s: float = 1.0,
) -> bytes:
    if serial is None:
        raise RuntimeError("pyserial is not available.")

    with serial.Serial(port_name, baudrate=baudrate, timeout=timeout_s, write_timeout=timeout_s) as port:
        port.reset_input_buffer()
        port.reset_output_buffer()
        port.rts = False
        port.dtr = False
        time.sleep(0.08)
        if payload:
            port.write(payload)
            port.flush()

        chunks: list[bytes] = []
        deadline = time.time() + max(total_wait_s, timeout_s)
        while time.time() < deadline:
            waiting = port.in_waiting
            chunk = port.read(waiting or 1)
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)


def strain_percent(
    displacement_mm: float,
    initial_length_mm: float,
    reference_mm: float,
) -> float | None:
    if initial_length_mm <= 0.0:
        return None
    return ((displacement_mm - reference_mm) / initial_length_mm) * 100.0


def stress_mpa_from_load_g(load_g: float, diameter_mm: float) -> float | None:
    if diameter_mm <= 0.0:
        return None
    area_mm2 = (math.pi * diameter_mm * diameter_mm) / 4.0
    if area_mm2 <= 0.0:
        return None
    force_n = load_g * GRAVITY_MS2 / 1000.0
    return force_n / area_mm2


@dataclass
class MeasurementPoint:
    elapsed_s: float
    timestamp_utc: str
    position_mm: float
    raw_load_g: float
    load_g: float
    preload_state: str
    strain_pct: float | None
    stress_mpa: float | None
    current_set_mA: float | None
    current_measured_mA: float | None
    voltage_V: float | None
    resistance_ohm: float | None
    power_W: float | None
    automation_phase: str
    automation_basis: str | None
    automation_target_value: float | None
    plateau_index: int | None
    plateau_label: str | None


@dataclass
class AutomationStep:
    action: str
    target_mm: float | None = None
    target_value: float | None = None
    basis: str | None = None
    note: str = ""


@dataclass
class PlotChannel:
    key: str
    label: str
    color: str
    getter: Callable[[MeasurementPoint], float | None]


@dataclass
class PlotTileWidgets:
    visible: QtWidgets.QCheckBox
    x_combo: QtWidgets.QComboBox
    y_left_combo: QtWidgets.QComboBox
    y_right_combo: QtWidgets.QComboBox


@dataclass
class ProjectImportResult:
    path: Path
    section: str
    diameter_mm: float | None
    current_mA: float | None
    matched_row: dict[str, Any]


class PlotConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure plot dashboard")
        self.setModal(False)
        self.resize(860, 320)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.body_layout = QtWidgets.QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.body_layout, stretch=1)
        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        close_button = QtWidgets.QPushButton("Close", self)
        close_button.clicked.connect(self.close)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)


class CollapsibleSection(QtWidgets.QFrame):
    def __init__(
        self,
        title: str,
        *,
        expanded: bool = True,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame { border: 1px solid palette(mid); border-radius: 8px; }")
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        self.toggle_button = QtWidgets.QToolButton(self)
        self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setText(title)
        self.toggle_button.clicked.connect(self._handle_toggled)
        root.addWidget(self.toggle_button)
        self.content = QtWidgets.QWidget(self)
        self.content_layout = QtWidgets.QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(4, 0, 4, 2)
        self.content_layout.setSpacing(8)
        root.addWidget(self.content)
        self.set_expanded(expanded)

    def _handle_toggled(self, checked: bool) -> None:
        self.set_expanded(checked)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if expanded else QtCore.Qt.ArrowType.RightArrow
        )
        self.content.setVisible(expanded)

    def is_expanded(self) -> bool:
        return self.toggle_button.isChecked()


class ScaleWorker(QtCore.QObject):
    measurement_received = QtCore.pyqtSignal(float, str, float)
    status_changed = QtCore.pyqtSignal(str)
    error_occurred = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        port_name: str,
        baudrate: int,
        poll_interval_ms: int,
        request_command: str,
        request_terminator: str,
    ) -> None:
        super().__init__()
        self.port_name = port_name
        self.baudrate = baudrate
        self.poll_interval_ms = max(50, int(poll_interval_ms))
        self.request_command = request_command
        self.request_terminator = request_terminator
        self._stop_event = Event()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        if serial is None:
            self.error_occurred.emit("pyserial is not available.")
            self.finished.emit()
            return

        port: Any = None
        try:
            timeout_s = max(0.05, self.poll_interval_ms / 1000.0)
            port = serial.Serial(
                self.port_name,
                self.baudrate,
                timeout=timeout_s,
                write_timeout=0.2,
            )
            self.status_changed.emit(
                f"Scale connected on {self.port_name} at {self.baudrate} baud."
            )
            request_payload = _decode_escape_text(self.request_command)
            terminator_payload = _decode_escape_text(self.request_terminator)
            while not self._stop_event.is_set():
                if request_payload:
                    try:
                        port.write(request_payload + terminator_payload)
                    except Exception as exc:
                        self.error_occurred.emit(f"Scale write failed: {exc}")
                        break

                raw_bytes = port.readline()
                if not raw_bytes:
                    continue
                raw_text = raw_bytes.decode("utf-8", errors="ignore").strip()
                if not raw_text:
                    continue
                value = _parse_first_float(raw_text)
                if value is None:
                    self.status_changed.emit(f"Scale raw: {raw_text}")
                    continue
                self.measurement_received.emit(value, raw_text, time.time())
        except SerialException as exc:
            self.error_occurred.emit(f"Scale connection failed: {exc}")
        except Exception as exc:
            self.error_occurred.emit(f"Scale worker failed: {exc}")
        finally:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass
            self.finished.emit()

    @QtCore.pyqtSlot()
    def stop(self) -> None:
        self._stop_event.set()


class TicController:
    def __init__(self, command_path: str = "ticcmd", device_serial: str = "") -> None:
        self.command_path = command_path.strip() or "ticcmd"
        self.device_serial = device_serial.strip()

    def executable(self) -> str | None:
        if os.path.sep in self.command_path or "/" in self.command_path:
            return self.command_path if Path(self.command_path).exists() else None
        return shutil.which(self.command_path)

    def _base_args(self) -> list[str]:
        exe = self.executable()
        if not exe:
            raise FileNotFoundError(
                "ticcmd was not found. Install Pololu Tic software and make ticcmd available on PATH."
            )
        args = [exe]
        if self.device_serial:
            args.extend(["-d", self.device_serial])
        return args

    def run(self, *extra_args: str, timeout_s: float = 5.0) -> str:
        args = self._base_args()
        args.extend(extra_args)
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        if completed.returncode != 0:
            detail = stderr or stdout or f"ticcmd exited with code {completed.returncode}"
            raise RuntimeError(detail)
        return stdout

    def get_status(self) -> str:
        for args in (("--status", "--full"), ("--status",), ("--full",)):
            try:
                return self.run(*args)
            except RuntimeError:
                continue
        return self.run("--list")

    def halt_and_hold(self) -> None:
        self.run("--halt-and-hold")

    def set_current_position(self, position_steps: int) -> None:
        self.run("--halt-and-set-position", str(int(position_steps)))

    def set_target_position(self, position_steps: int) -> None:
        self.run("--exit-safe-start", "--position", str(int(position_steps)))


class PowerSupplyController:
    def __init__(
        self,
        *,
        port_name: str,
        baudrate: int,
        profile_id: str,
        max_voltage_v: float,
        device_serial: str = "",
    ) -> None:
        self.port_name = port_name.strip()
        self.baudrate = int(baudrate)
        self.profile_id = profile_id if profile_id in SUPPLY_PROFILES else "hmp4030"
        self.profile = dict(SUPPLY_PROFILES[self.profile_id])
        self.max_voltage_v = float(max_voltage_v)
        self.device_serial = device_serial.strip()
        self._serial: Any = None

    def connect(self) -> None:
        if serial is None:
            raise RuntimeError("pyserial is not available.")
        if not self.port_name:
            raise RuntimeError("Select a power-supply serial port first.")
        if self._serial is not None and getattr(self._serial, "is_open", False):
            return
        self._serial = serial.Serial(
            self.port_name,
            baudrate=self.baudrate,
            timeout=0.5,
            write_timeout=0.5,
        )
        self._serial.rts = False
        self._serial.dtr = False
        time.sleep(0.08)

    def disconnect(self) -> None:
        port = self._serial
        self._serial = None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    def is_connected(self) -> bool:
        return self._serial is not None and bool(getattr(self._serial, "is_open", False))

    def _require_port(self) -> Any:
        if not self.is_connected():
            raise RuntimeError("Power supply is not connected.")
        return self._serial

    def _write_command(self, command: str, *, settle_s: float = 0.08) -> None:
        port = self._require_port()
        payload = command.rstrip() + "\n"
        port.reset_input_buffer()
        port.write(payload.encode("ascii", errors="ignore"))
        port.flush()
        if settle_s > 0:
            time.sleep(settle_s)

    def _read_line(self, *, timeout_s: float = 0.7) -> str:
        port = self._require_port()
        deadline = time.time() + max(0.1, timeout_s)
        chunks: list[bytes] = []
        while time.time() < deadline:
            line = port.readline()
            if line:
                chunks.append(line)
                if line.endswith(b"\n") or line.endswith(b"\r"):
                    break
        return b"".join(chunks).decode("ascii", errors="ignore").strip()

    def command(self, command: str, *, settle_s: float = 0.08) -> None:
        self._write_command(command, settle_s=settle_s)

    def query_float(self, command: str, *, settle_s: float = 0.08, timeout_s: float = 0.7) -> float | None:
        self._write_command(command, settle_s=settle_s)
        return _parse_first_float(self._read_line(timeout_s=timeout_s))

    def initialize_output(
        self,
        *,
        current_mA: float,
        reset_on_start: bool,
        force_voltage_first: bool | None = None,
    ) -> None:
        channel = int(self.profile.get("channel_select", 0) or 0)
        if reset_on_start:
            self.command("*RST", settle_s=1.2)
        if channel > 0:
            self.command(f"INST:NSEL {channel}")
        limit_v = max(0.0, float(self.max_voltage_v))
        current_a = max(0.0, float(current_mA)) / 1000.0
        voltage_first = bool(self.profile.get("voltage_first", False)) if force_voltage_first is None else bool(force_voltage_first)
        if voltage_first:
            self.command(f"VOLT {limit_v:.1f}")
            self.command(f"CURR {current_a:.3f}")
        else:
            self.command(f"CURR {current_a:.3f}")
            self.command(f"VOLT {limit_v:.1f}")
        self.command("OUTP ON")

    def set_current_mA(self, current_mA: float) -> None:
        self.command(f"CURR {max(0.0, float(current_mA)) / 1000.0:.3f}")

    def output_on(self) -> None:
        self.command("OUTP ON")

    def output_off(self) -> None:
        self.command("OUTP OFF")

    def measure(self) -> dict[str, float | None]:
        voltage_v = self.query_float("MEAS:VOLT?")
        current_a = self.query_float("MEAS:CURR?")
        current_mA = None if current_a is None else current_a * 1000.0
        resistance_ohm = None
        power_w = None
        if voltage_v is not None and current_a is not None:
            if abs(current_a) > 1e-12:
                resistance_ohm = voltage_v / current_a
            power_w = voltage_v * current_a
        return {
            "voltage_V": voltage_v,
            "current_mA": current_mA,
            "resistance_ohm": resistance_ohm,
            "power_W": power_w,
        }


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, log_dir: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.settings = QtCore.QSettings("microwire", "mini_dma_logger")
        self._provided_log_dir = log_dir
        self._scale_thread: QtCore.QThread | None = None
        self._scale_worker: ScaleWorker | None = None
        self._tic_status_text = ""
        self._latest_scale_value_g = 0.0
        self._latest_scale_text = ""
        self._latest_scale_timestamp: float | None = None
        self._scale_connected_at_s: float | None = None
        self._scale_no_data_hint_emitted = False
        self._current_position_steps = 0
        self._current_position_mm = 0.0
        self._last_move_target_mm = 0.0
        self._session_points: list[MeasurementPoint] = []
        self._session_active = False
        self._session_start_monotonic = 0.0
        self._session_txt_handle: Any = None
        self._session_csv_handle: Any = None
        self._session_csv_writer: csv.DictWriter[str] | None = None
        self._session_base_path: Path | None = None
        self._session_json_path: Path | None = None
        self._load_offset_g = 0.0
        self._position_reference_mm = 0.0
        self._preload_reference_armed = False
        self._preload_trigger_elapsed_s: float | None = None
        self._builder_project_path: Path | None = None
        self._builder_project_match: ProjectImportResult | None = None
        self._supply_controller: PowerSupplyController | None = None
        self._supply_snapshot: dict[str, float | None] = {
            "voltage_V": None,
            "current_mA": None,
            "resistance_ohm": None,
            "power_W": None,
        }
        self._supply_output_enabled = False
        self._supply_last_setpoint_mA: float | None = None
        self._heating_program_current_mA: float | None = None
        self._heating_program_direction = 1.0
        self._automation_active = False
        self._automation_steps: list[AutomationStep] = []
        self._automation_index = 0
        self._automation_interval_ms = 1000
        self._automation_name = ""
        self._automation_phase = "idle"
        self._automation_basis: str | None = None
        self._automation_target_value: float | None = None
        self._automation_plateau_index: int | None = None
        self._automation_plateau_label: str | None = None
        self._recipe_origin_mm = 0.0
        self._recipe_estimated_points = 0
        self._plot_tiles: list[PlotTileWidgets] = []
        self._build_ui(log_dir or _default_download_dir())
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._handle_status_timer)
        self._auto_ramp_timer = QtCore.QTimer(self)
        self._auto_ramp_timer.timeout.connect(self._handle_auto_ramp_tick)
        self._scale_hint_timer = QtCore.QTimer(self)
        self._scale_hint_timer.setSingleShot(True)
        self._scale_hint_timer.timeout.connect(self._warn_if_scale_is_silent)
        self._restore_settings()
        self._refresh_scale_ports()
        self._refresh_live_labels()

    def _build_ui(self, log_dir: str) -> None:
        install_standard_menu(self, open_folder=self._choose_log_dir)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, central)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        control_scroll = QtWidgets.QScrollArea(splitter)
        control_scroll.setWidgetResizable(True)
        control_scroll.setMinimumWidth(500)
        control_scroll.setMaximumWidth(620)
        control_panel = QtWidgets.QWidget(control_scroll)
        control_scroll.setWidget(control_panel)
        controls = QtWidgets.QVBoxLayout(control_panel)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(10)

        self.overview_section = CollapsibleSection("Overview", expanded=False, parent=control_panel)
        overview_layout = self.overview_section.content_layout
        overview_label = QtWidgets.QLabel(
            "Mini DMA is a hardware-driven stress/strain logger for your small stepper stage and "
            "serial balance. The current build is focused on safe mechanical probing, repeatable "
            "displacement-controlled recipes, and output compatible with the Shape Memory Stress/Strain workflow."
        )
        overview_label.setWordWrap(True)
        overview_layout.addWidget(overview_label)
        self.label_session_status = QtWidgets.QLabel("Session idle")
        overview_layout.addWidget(self.label_session_status)
        self.label_live_summary = QtWidgets.QLabel("Live strain: - | Live stress: -")
        overview_layout.addWidget(self.label_live_summary)
        cards_grid = QtWidgets.QGridLayout()
        cards_grid.setContentsMargins(0, 4, 0, 0)
        cards_grid.setHorizontalSpacing(8)
        cards_grid.setVerticalSpacing(8)
        session_card, self.label_card_session = self._build_status_card(
            "Session",
            "Idle",
            "No active run.",
            "#2ca02c",
        )
        scale_card, self.label_card_scale = self._build_status_card(
            "Scale",
            "Disconnected",
            "COM link not active.",
            "#1f77b4",
        )
        motion_card, self.label_card_motion = self._build_status_card(
            "Motion",
            "Unknown",
            "Tic status not queried yet.",
            "#ff7f0e",
        )
        recipe_card, self.label_card_recipe = self._build_status_card(
            "Recipe",
            "Manual",
            "Ready for ramp, cycle, or hold control.",
            "#9467bd",
        )
        cards_grid.addWidget(session_card, 0, 0)
        cards_grid.addWidget(scale_card, 0, 1)
        cards_grid.addWidget(motion_card, 1, 0)
        cards_grid.addWidget(recipe_card, 1, 1)
        overview_layout.addLayout(cards_grid)
        controls.addWidget(self.overview_section)

        tabs = QtWidgets.QTabWidget(control_panel)
        controls.addWidget(tabs)

        hardware_tab = QtWidgets.QWidget(tabs)
        hardware_layout = QtWidgets.QVBoxLayout(hardware_tab)
        hardware_layout.setContentsMargins(0, 0, 0, 0)
        hardware_layout.setSpacing(10)

        scale_box = self._group_box("Scale")
        scale_form = QtWidgets.QFormLayout(scale_box)
        self.combo_scale_port = QtWidgets.QComboBox(scale_box)
        refresh_ports_button = QtWidgets.QPushButton("Refresh ports", scale_box)
        refresh_ports_button.clicked.connect(self._refresh_scale_ports)
        port_row = QtWidgets.QHBoxLayout()
        port_row.addWidget(self.combo_scale_port, stretch=1)
        port_row.addWidget(refresh_ports_button)
        scale_form.addRow("Port", port_row)

        self.combo_scale_baud = QtWidgets.QComboBox(scale_box)
        for baud in ("600", "1200", "2400", "4800", "9600", "19200", "38400", "115200"):
            self.combo_scale_baud.addItem(baud)
        self.combo_scale_baud.setCurrentText("600")
        scale_form.addRow("Baud", self.combo_scale_baud)

        self.spin_scale_interval = QtWidgets.QSpinBox(scale_box)
        self.spin_scale_interval.setRange(50, 5000)
        self.spin_scale_interval.setSuffix(" ms")
        self.spin_scale_interval.setValue(250)
        scale_form.addRow("Poll interval", self.spin_scale_interval)

        self.edit_scale_request = QtWidgets.QLineEdit(scale_box)
        self.edit_scale_request.setPlaceholderText("leave blank if the scale streams continuously")
        scale_form.addRow("Request command", self.edit_scale_request)

        self.edit_scale_terminator = QtWidgets.QLineEdit(scale_box)
        self.edit_scale_terminator.setText("")
        scale_form.addRow("Line ending", self.edit_scale_terminator)

        scale_buttons = QtWidgets.QHBoxLayout()
        self.button_scale_connect = QtWidgets.QPushButton("Connect scale", scale_box)
        self.button_scale_connect.clicked.connect(self._toggle_scale_connection)
        scale_buttons.addWidget(self.button_scale_connect)
        tare_button = QtWidgets.QPushButton("Software tare", scale_box)
        tare_button.clicked.connect(self._tare_scale)
        scale_buttons.addWidget(tare_button)
        scale_form.addRow("", scale_buttons)

        self.label_scale_value = QtWidgets.QLabel("Latest load: 0.000 g")
        self.label_scale_raw = QtWidgets.QLabel("Raw line: -")
        self.label_scale_raw.setWordWrap(True)
        self.label_scale_hint = QtWidgets.QLabel(
            "G&G RS232 note: these balances often need a DB9 null modem crossover between the "
            "USB-serial adapter and the scale."
        )
        self.label_scale_hint.setWordWrap(True)
        gng_button = QtWidgets.QPushButton("Apply G&G E-series preset", scale_box)
        gng_button.clicked.connect(self._apply_gng_scale_preset)
        probe_button = QtWidgets.QPushButton("Probe scale", scale_box)
        probe_button.clicked.connect(self._probe_scale_port)
        scale_form.addRow("", self.label_scale_value)
        scale_form.addRow("", self.label_scale_raw)
        scale_form.addRow("", self.label_scale_hint)
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(gng_button)
        preset_row.addWidget(probe_button)
        scale_form.addRow("", preset_row)
        hardware_layout.addWidget(scale_box)

        motion_box = self._group_box("Motion")
        motion_form = QtWidgets.QFormLayout(motion_box)

        self.edit_ticcmd_path = QtWidgets.QLineEdit(motion_box)
        self.edit_ticcmd_path.setText(_find_ticcmd())
        motion_form.addRow("ticcmd path", self.edit_ticcmd_path)

        self.edit_tic_serial = QtWidgets.QLineEdit(motion_box)
        self.edit_tic_serial.setPlaceholderText("optional when only one Tic is connected")
        motion_form.addRow("Device serial", self.edit_tic_serial)

        self.spin_steps_per_mm = QtWidgets.QDoubleSpinBox(motion_box)
        self.spin_steps_per_mm.setDecimals(3)
        self.spin_steps_per_mm.setRange(1.0, 100000.0)
        self.spin_steps_per_mm.setValue(100.0)
        self.spin_steps_per_mm.setToolTip(
            "Controller position units per mm. Full-step nominal value for your actuator is 100 steps/mm."
        )
        motion_form.addRow("Steps per mm", self.spin_steps_per_mm)

        motion_buttons = QtWidgets.QHBoxLayout()
        refresh_tic_button = QtWidgets.QPushButton("Check Tic", motion_box)
        refresh_tic_button.clicked.connect(self._refresh_tic_status)
        motion_buttons.addWidget(refresh_tic_button)
        zero_tic_button = QtWidgets.QPushButton("Set position = 0", motion_box)
        zero_tic_button.clicked.connect(self._zero_tic_position)
        motion_buttons.addWidget(zero_tic_button)
        halt_tic_button = QtWidgets.QPushButton("Halt", motion_box)
        halt_tic_button.clicked.connect(self._halt_tic)
        motion_buttons.addWidget(halt_tic_button)
        motion_form.addRow("", motion_buttons)

        self.spin_jog_mm = QtWidgets.QDoubleSpinBox(motion_box)
        self.spin_jog_mm.setDecimals(4)
        self.spin_jog_mm.setRange(0.0001, 10.0)
        self.spin_jog_mm.setValue(0.1)
        motion_form.addRow("Jog step", self.spin_jog_mm)

        jog_buttons = QtWidgets.QHBoxLayout()
        jog_negative = QtWidgets.QPushButton("Jog -", motion_box)
        jog_negative.clicked.connect(lambda: self._jog_relative(-1.0))
        jog_buttons.addWidget(jog_negative)
        jog_positive = QtWidgets.QPushButton("Jog +", motion_box)
        jog_positive.clicked.connect(lambda: self._jog_relative(1.0))
        jog_buttons.addWidget(jog_positive)
        motion_form.addRow("", jog_buttons)

        self.label_tic_position = QtWidgets.QLabel("Position: 0.0000 mm")
        self.label_tic_summary = QtWidgets.QLabel("Tic status not queried yet.")
        self.label_tic_summary.setWordWrap(True)
        motion_form.addRow("", self.label_tic_position)
        motion_form.addRow("", self.label_tic_summary)
        hardware_layout.addWidget(motion_box)

        safety_box = self._group_box("Reference & Safety")
        safety_form = QtWidgets.QFormLayout(safety_box)
        self.button_set_reference_now = QtWidgets.QPushButton("Use current position as zero", safety_box)
        self.button_set_reference_now.clicked.connect(self._set_position_reference_now)
        safety_form.addRow("", self.button_set_reference_now)

        self.check_soft_limits = QtWidgets.QCheckBox("Enable position soft limits", safety_box)
        safety_form.addRow("", self.check_soft_limits)
        soft_limit_row = QtWidgets.QHBoxLayout()
        self.spin_soft_min_mm = QtWidgets.QDoubleSpinBox(safety_box)
        self.spin_soft_min_mm.setDecimals(4)
        self.spin_soft_min_mm.setRange(-100.0, 100.0)
        self.spin_soft_min_mm.setValue(-5.0)
        self.spin_soft_min_mm.setSuffix(" mm")
        self.spin_soft_max_mm = QtWidgets.QDoubleSpinBox(safety_box)
        self.spin_soft_max_mm.setDecimals(4)
        self.spin_soft_max_mm.setRange(-100.0, 100.0)
        self.spin_soft_max_mm.setValue(5.0)
        self.spin_soft_max_mm.setSuffix(" mm")
        soft_limit_row.addWidget(QtWidgets.QLabel("Min", safety_box))
        soft_limit_row.addWidget(self.spin_soft_min_mm)
        soft_limit_row.addWidget(QtWidgets.QLabel("Max", safety_box))
        soft_limit_row.addWidget(self.spin_soft_max_mm)
        safety_form.addRow("Soft limits", soft_limit_row)

        self.check_max_load = QtWidgets.QCheckBox("Stop automation if effective load exceeds", safety_box)
        safety_form.addRow("", self.check_max_load)
        self.spin_max_load_g = QtWidgets.QDoubleSpinBox(safety_box)
        self.spin_max_load_g.setDecimals(3)
        self.spin_max_load_g.setRange(0.001, 1000.0)
        self.spin_max_load_g.setValue(25.0)
        self.spin_max_load_g.setSuffix(" g")
        safety_form.addRow("Max load", self.spin_max_load_g)

        self.label_reference_status = QtWidgets.QLabel("Reference position: 0.0000 mm")
        self.label_reference_status.setWordWrap(True)
        safety_form.addRow("", self.label_reference_status)
        hardware_layout.addWidget(safety_box)
        hardware_layout.addStretch(1)
        tabs.addTab(hardware_tab, "Hardware")

        heating_tab = QtWidgets.QWidget(tabs)
        heating_layout = QtWidgets.QVBoxLayout(heating_tab)
        heating_layout.setContentsMargins(0, 0, 0, 0)
        heating_layout.setSpacing(10)

        supply_box = self._group_box("Current Annealing")
        supply_form = QtWidgets.QFormLayout(supply_box)
        self.combo_supply_port = QtWidgets.QComboBox(supply_box)
        refresh_supply_button = QtWidgets.QPushButton("Refresh ports", supply_box)
        refresh_supply_button.clicked.connect(self._refresh_supply_ports)
        supply_port_row = QtWidgets.QHBoxLayout()
        supply_port_row.addWidget(self.combo_supply_port, stretch=1)
        supply_port_row.addWidget(refresh_supply_button)
        supply_form.addRow("Port", supply_port_row)

        self.combo_supply_baud = QtWidgets.QComboBox(supply_box)
        for baud in ("9600", "19200", "38400", "57600", "115200"):
            self.combo_supply_baud.addItem(baud)
        self.combo_supply_baud.setCurrentText("9600")
        supply_form.addRow("Baud", self.combo_supply_baud)

        self.combo_supply_profile = QtWidgets.QComboBox(supply_box)
        for profile_id, profile in SUPPLY_PROFILES.items():
            self.combo_supply_profile.addItem(str(profile.get("label", profile_id)), profile_id)
        self.combo_supply_profile.currentIndexChanged.connect(self._apply_supply_profile_defaults)
        supply_form.addRow("Profile", self.combo_supply_profile)

        self.spin_supply_voltage_limit = QtWidgets.QDoubleSpinBox(supply_box)
        self.spin_supply_voltage_limit.setDecimals(2)
        self.spin_supply_voltage_limit.setRange(0.0, 1000.0)
        self.spin_supply_voltage_limit.setValue(30.0)
        self.spin_supply_voltage_limit.setSuffix(" V")
        supply_form.addRow("Voltage limit", self.spin_supply_voltage_limit)

        self.spin_supply_manual_current = QtWidgets.QDoubleSpinBox(supply_box)
        self.spin_supply_manual_current.setDecimals(2)
        self.spin_supply_manual_current.setRange(0.0, 5000.0)
        self.spin_supply_manual_current.setValue(1.0)
        self.spin_supply_manual_current.setSuffix(" mA")
        supply_form.addRow("Manual set current", self.spin_supply_manual_current)

        connect_supply_row = QtWidgets.QHBoxLayout()
        connect_supply_button = QtWidgets.QPushButton("Connect supply", supply_box)
        connect_supply_button.clicked.connect(self._connect_supply)
        connect_supply_row.addWidget(connect_supply_button)
        disconnect_supply_button = QtWidgets.QPushButton("Disconnect supply", supply_box)
        disconnect_supply_button.clicked.connect(self._disconnect_supply)
        connect_supply_row.addWidget(disconnect_supply_button)
        supply_form.addRow("", connect_supply_row)

        manual_supply_row = QtWidgets.QHBoxLayout()
        apply_current_button = QtWidgets.QPushButton("Apply current", supply_box)
        apply_current_button.clicked.connect(self._apply_manual_supply_current)
        manual_supply_row.addWidget(apply_current_button)
        output_on_button = QtWidgets.QPushButton("Output on", supply_box)
        output_on_button.clicked.connect(self._enable_supply_output)
        manual_supply_row.addWidget(output_on_button)
        output_off_button = QtWidgets.QPushButton("Output off", supply_box)
        output_off_button.clicked.connect(self._disable_supply_output)
        manual_supply_row.addWidget(output_off_button)
        supply_form.addRow("", manual_supply_row)

        read_supply_button = QtWidgets.QPushButton("Read supply now", supply_box)
        read_supply_button.clicked.connect(self._refresh_supply_snapshot)
        supply_form.addRow("", read_supply_button)

        self.label_supply_status = QtWidgets.QLabel("Supply disconnected.")
        self.label_supply_status.setWordWrap(True)
        supply_form.addRow("", self.label_supply_status)
        self.label_supply_live = QtWidgets.QLabel("Set - | Current - | Voltage - | Resistance - | Power -")
        self.label_supply_live.setWordWrap(True)
        supply_form.addRow("", self.label_supply_live)
        heating_layout.addWidget(supply_box)

        heating_recipe_box = self._group_box("Heating Program")
        heating_recipe_form = QtWidgets.QFormLayout(heating_recipe_box)
        self.combo_heating_mode = QtWidgets.QComboBox(heating_recipe_box)
        for mode_key, label in HEATING_MODE_LABELS.items():
            self.combo_heating_mode.addItem(label, mode_key)
        self.combo_heating_mode.currentIndexChanged.connect(self._update_recipe_mode_ui)
        heating_recipe_form.addRow("Program", self.combo_heating_mode)

        self.spin_heat_constant_current = QtWidgets.QDoubleSpinBox(heating_recipe_box)
        self.spin_heat_constant_current.setDecimals(2)
        self.spin_heat_constant_current.setRange(0.0, 5000.0)
        self.spin_heat_constant_current.setValue(50.0)
        self.spin_heat_constant_current.setSuffix(" mA")
        heating_recipe_form.addRow("Constant current", self.spin_heat_constant_current)

        self.spin_heat_start_current = QtWidgets.QDoubleSpinBox(heating_recipe_box)
        self.spin_heat_start_current.setDecimals(2)
        self.spin_heat_start_current.setRange(0.0, 5000.0)
        self.spin_heat_start_current.setValue(10.0)
        self.spin_heat_start_current.setSuffix(" mA")
        heating_recipe_form.addRow("Ramp start", self.spin_heat_start_current)

        self.spin_heat_max_current = QtWidgets.QDoubleSpinBox(heating_recipe_box)
        self.spin_heat_max_current.setDecimals(2)
        self.spin_heat_max_current.setRange(0.0, 5000.0)
        self.spin_heat_max_current.setValue(100.0)
        self.spin_heat_max_current.setSuffix(" mA")
        heating_recipe_form.addRow("Ramp max", self.spin_heat_max_current)

        self.spin_heat_step_current = QtWidgets.QDoubleSpinBox(heating_recipe_box)
        self.spin_heat_step_current.setDecimals(2)
        self.spin_heat_step_current.setRange(0.01, 5000.0)
        self.spin_heat_step_current.setValue(5.0)
        self.spin_heat_step_current.setSuffix(" mA")
        heating_recipe_form.addRow("Ramp step", self.spin_heat_step_current)

        self.combo_heat_limit_action = QtWidgets.QComboBox(heating_recipe_box)
        for action_key, label in HEATING_LIMIT_LABELS.items():
            self.combo_heat_limit_action.addItem(label, action_key)
        heating_recipe_form.addRow("Voltage-limit action", self.combo_heat_limit_action)

        self.check_output_off_on_stop = QtWidgets.QCheckBox("Turn output off when the session stops", heating_recipe_box)
        self.check_output_off_on_stop.setChecked(True)
        heating_recipe_form.addRow("", self.check_output_off_on_stop)
        heating_layout.addWidget(heating_recipe_box)
        heating_layout.addStretch(1)
        tabs.addTab(heating_tab, "Heating")

        specimen_tab = QtWidgets.QWidget(tabs)
        specimen_layout = QtWidgets.QVBoxLayout(specimen_tab)
        specimen_layout.setContentsMargins(0, 0, 0, 0)
        specimen_layout.setSpacing(10)

        naming_box = self._group_box("Naming")
        naming_form = QtWidgets.QFormLayout(naming_box)
        self.edit_name_composition = QtWidgets.QLineEdit(naming_box)
        self.edit_name_composition.setPlaceholderText("e.g. Ni51Fe26Ga21")
        naming_form.addRow("Composition", self.edit_name_composition)
        self.edit_name_wire = QtWidgets.QLineEdit(naming_box)
        self.edit_name_wire.setPlaceholderText("e.g. 156_2")
        naming_form.addRow("Microwire", self.edit_name_wire)
        self.edit_name_specimen = QtWidgets.QLineEdit(naming_box)
        self.edit_name_specimen.setPlaceholderText("e.g. s1")
        naming_form.addRow("Specimen", self.edit_name_specimen)
        self.edit_name_condition = QtWidgets.QLineEdit(naming_box)
        self.edit_name_condition.setPlaceholderText("e.g. preload test")
        naming_form.addRow("Condition / notes", self.edit_name_condition)
        self.check_auto_name = QtWidgets.QCheckBox("Auto-fill sample name and base filename from the fields above", naming_box)
        self.check_auto_name.setChecked(True)
        naming_form.addRow("", self.check_auto_name)
        apply_name_button = QtWidgets.QPushButton("Apply naming fields now", naming_box)
        apply_name_button.clicked.connect(self._apply_name_fields)
        naming_form.addRow("", apply_name_button)
        specimen_layout.addWidget(naming_box)

        sample_box = self._group_box("Sample")
        sample_form = QtWidgets.QFormLayout(sample_box)
        self.spin_initial_length = QtWidgets.QDoubleSpinBox(sample_box)
        self.spin_initial_length.setDecimals(3)
        self.spin_initial_length.setRange(0.0, 1000.0)
        self.spin_initial_length.setValue(30.0)
        self.spin_initial_length.setSuffix(" mm")
        sample_form.addRow("Gauge length l0", self.spin_initial_length)

        self.spin_diameter = QtWidgets.QDoubleSpinBox(sample_box)
        self.spin_diameter.setDecimals(5)
        self.spin_diameter.setRange(0.0, 10.0)
        self.spin_diameter.setValue(0.03)
        self.spin_diameter.setSuffix(" mm")
        sample_form.addRow("Wire diameter", self.spin_diameter)

        self.check_zero_on_preload = QtWidgets.QCheckBox(
            "Zero strain/stress only after preload is reached",
            sample_box,
        )
        self.check_zero_on_preload.setChecked(True)
        sample_form.addRow("", self.check_zero_on_preload)
        self.spin_preload_threshold_g = QtWidgets.QDoubleSpinBox(sample_box)
        self.spin_preload_threshold_g.setDecimals(4)
        self.spin_preload_threshold_g.setRange(0.0, 1000.0)
        self.spin_preload_threshold_g.setValue(0.02)
        self.spin_preload_threshold_g.setSuffix(" g")
        sample_form.addRow("Preload threshold", self.spin_preload_threshold_g)
        preload_button = QtWidgets.QPushButton("Set current position as gauge zero", sample_box)
        preload_button.clicked.connect(self._set_reference_from_current_position)
        sample_form.addRow("", preload_button)

        self.edit_sample_name = QtWidgets.QLineEdit(sample_box)
        sample_form.addRow("Sample name", self.edit_sample_name)
        self.edit_run_notes = QtWidgets.QPlainTextEdit(sample_box)
        self.edit_run_notes.setPlaceholderText(
            "Optional notes saved into the session metadata, for example gauge length, fixture state, or operator notes."
        )
        self.edit_run_notes.setMaximumBlockCount(200)
        self.edit_run_notes.setFixedHeight(80)
        sample_form.addRow("Run notes", self.edit_run_notes)
        specimen_layout.addWidget(sample_box)

        project_box = self._group_box("Builder Project")
        project_form = QtWidgets.QFormLayout(project_box)
        self.edit_project_path = QtWidgets.QLineEdit(project_box)
        project_path_row = QtWidgets.QHBoxLayout()
        project_path_row.addWidget(self.edit_project_path, stretch=1)
        browse_project_button = QtWidgets.QPushButton("Browse", project_box)
        browse_project_button.clicked.connect(self._choose_builder_project)
        project_path_row.addWidget(browse_project_button)
        import_project_button = QtWidgets.QPushButton("Import sample info", project_box)
        import_project_button.clicked.connect(self._import_builder_project)
        project_path_row.addWidget(import_project_button)
        project_form.addRow("Project (.pydpj)", project_path_row)
        self.label_project_status = QtWidgets.QLabel(
            "Load a Microwire Data Builder project to auto-fill diameter and sample metadata."
        )
        self.label_project_status.setWordWrap(True)
        project_form.addRow("", self.label_project_status)
        specimen_layout.addWidget(project_box)

        logging_box = self._group_box("Session")
        logging_form = QtWidgets.QFormLayout(logging_box)
        self.edit_log_dir = QtWidgets.QLineEdit(logging_box)
        self.edit_log_dir.setText(log_dir)
        log_dir_buttons = QtWidgets.QHBoxLayout()
        log_dir_buttons.addWidget(self.edit_log_dir, stretch=1)
        browse_button = QtWidgets.QPushButton("Browse", logging_box)
        browse_button.clicked.connect(self._choose_log_dir)
        log_dir_buttons.addWidget(browse_button)
        logging_form.addRow("Output folder", log_dir_buttons)

        self.edit_log_name = QtWidgets.QLineEdit(logging_box)
        self.edit_log_name.setText(DEFAULT_LOG_BASENAME)
        logging_form.addRow("Base filename", self.edit_log_name)

        self.check_zero_position_on_start = QtWidgets.QCheckBox(
            "Set current Tic position to 0 when the session starts",
            logging_box,
        )
        self.check_zero_position_on_start.setChecked(True)
        logging_form.addRow("", self.check_zero_position_on_start)

        self.check_tare_on_start = QtWidgets.QCheckBox(
            "Software tare the latest scale value when the session starts",
            logging_box,
        )
        self.check_tare_on_start.setChecked(True)
        logging_form.addRow("", self.check_tare_on_start)

        session_buttons = QtWidgets.QHBoxLayout()
        self.button_start_session = QtWidgets.QPushButton("Start session", logging_box)
        self.button_start_session.clicked.connect(self._start_session)
        session_buttons.addWidget(self.button_start_session)
        self.button_stop_session = QtWidgets.QPushButton("Stop session", logging_box)
        self.button_stop_session.clicked.connect(self._stop_session)
        self.button_stop_session.setEnabled(False)
        session_buttons.addWidget(self.button_stop_session)
        logging_form.addRow("", session_buttons)

        record_button = QtWidgets.QPushButton("Record point now", logging_box)
        record_button.clicked.connect(self._record_current_point)
        logging_form.addRow("", record_button)
        specimen_layout.addWidget(logging_box)
        specimen_layout.addStretch(1)
        tabs.addTab(specimen_tab, "Specimen")

        experiment_tab = QtWidgets.QWidget(tabs)
        experiment_layout = QtWidgets.QVBoxLayout(experiment_tab)
        experiment_layout.setContentsMargins(0, 0, 0, 0)
        experiment_layout.setSpacing(10)

        automation_box = self._group_box("Experiment Recipe")
        automation_form = QtWidgets.QFormLayout(automation_box)
        self.combo_recipe_mode = QtWidgets.QComboBox(automation_box)
        self.combo_recipe_mode.addItem("One-way ramp", "ramp")
        self.combo_recipe_mode.addItem("Cyclic triangle", "cycle")
        self.combo_recipe_mode.addItem("Position hold", "hold")
        self.combo_recipe_mode.addItem("Hsw distribution", "distribution")
        self.combo_recipe_mode.currentIndexChanged.connect(self._update_recipe_mode_ui)
        automation_form.addRow("Recipe type", self.combo_recipe_mode)

        self.recipe_stack = QtWidgets.QStackedWidget(automation_box)

        ramp_page = QtWidgets.QWidget(self.recipe_stack)
        ramp_form = QtWidgets.QFormLayout(ramp_page)
        self.spin_ramp_distance = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_ramp_distance.setDecimals(4)
        self.spin_ramp_distance.setRange(-50.0, 50.0)
        self.spin_ramp_distance.setValue(1.0)
        self.spin_ramp_distance.setSuffix(" mm")
        ramp_form.addRow("Total distance", self.spin_ramp_distance)

        self.spin_ramp_step = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_ramp_step.setDecimals(4)
        self.spin_ramp_step.setRange(0.0001, 10.0)
        self.spin_ramp_step.setValue(0.1)
        self.spin_ramp_step.setSuffix(" mm")
        ramp_form.addRow("Step size", self.spin_ramp_step)

        self.spin_ramp_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_ramp_interval.setRange(100, 60000)
        self.spin_ramp_interval.setValue(1000)
        self.spin_ramp_interval.setSuffix(" ms")
        ramp_form.addRow("Settle interval", self.spin_ramp_interval)
        self.recipe_stack.addWidget(ramp_page)

        cycle_page = QtWidgets.QWidget(self.recipe_stack)
        cycle_form = QtWidgets.QFormLayout(cycle_page)
        self.spin_cycle_amplitude = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_cycle_amplitude.setDecimals(4)
        self.spin_cycle_amplitude.setRange(-50.0, 50.0)
        self.spin_cycle_amplitude.setValue(1.0)
        self.spin_cycle_amplitude.setSuffix(" mm")
        cycle_form.addRow("Amplitude", self.spin_cycle_amplitude)
        self.spin_cycle_step = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_cycle_step.setDecimals(4)
        self.spin_cycle_step.setRange(0.0001, 10.0)
        self.spin_cycle_step.setValue(0.1)
        self.spin_cycle_step.setSuffix(" mm")
        cycle_form.addRow("Step size", self.spin_cycle_step)
        self.spin_cycle_count = QtWidgets.QSpinBox(automation_box)
        self.spin_cycle_count.setRange(1, 1000)
        self.spin_cycle_count.setValue(3)
        cycle_form.addRow("Cycles", self.spin_cycle_count)
        self.spin_cycle_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_cycle_interval.setRange(100, 60000)
        self.spin_cycle_interval.setValue(1000)
        self.spin_cycle_interval.setSuffix(" ms")
        cycle_form.addRow("Settle interval", self.spin_cycle_interval)
        self.recipe_stack.addWidget(cycle_page)

        hold_page = QtWidgets.QWidget(self.recipe_stack)
        hold_form = QtWidgets.QFormLayout(hold_page)
        self.spin_hold_target = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_hold_target.setDecimals(4)
        self.spin_hold_target.setRange(-50.0, 50.0)
        self.spin_hold_target.setValue(0.5)
        self.spin_hold_target.setSuffix(" mm")
        hold_form.addRow("Target offset", self.spin_hold_target)
        self.spin_hold_duration_s = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_hold_duration_s.setDecimals(1)
        self.spin_hold_duration_s.setRange(0.1, 86400.0)
        self.spin_hold_duration_s.setValue(10.0)
        self.spin_hold_duration_s.setSuffix(" s")
        hold_form.addRow("Hold duration", self.spin_hold_duration_s)
        self.spin_hold_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_hold_interval.setRange(100, 60000)
        self.spin_hold_interval.setValue(1000)
        self.spin_hold_interval.setSuffix(" ms")
        hold_form.addRow("Record interval", self.spin_hold_interval)
        self.recipe_stack.addWidget(hold_page)

        distribution_page = QtWidgets.QWidget(self.recipe_stack)
        distribution_form = QtWidgets.QFormLayout(distribution_page)
        self.combo_distribution_basis = QtWidgets.QComboBox(automation_box)
        for basis_key, label in HSW_BASIS_LABELS.items():
            self.combo_distribution_basis.addItem(label, basis_key)
        distribution_form.addRow("Control basis", self.combo_distribution_basis)
        self.spin_distribution_start = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_distribution_start.setDecimals(3)
        self.spin_distribution_start.setRange(-100000.0, 100000.0)
        self.spin_distribution_start.setValue(10.0)
        distribution_form.addRow("Start", self.spin_distribution_start)
        self.spin_distribution_end = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_distribution_end.setDecimals(3)
        self.spin_distribution_end.setRange(-100000.0, 100000.0)
        self.spin_distribution_end.setValue(100.0)
        distribution_form.addRow("End", self.spin_distribution_end)
        self.spin_distribution_step = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_distribution_step.setDecimals(3)
        self.spin_distribution_step.setRange(0.001, 100000.0)
        self.spin_distribution_step.setValue(10.0)
        distribution_form.addRow("Step", self.spin_distribution_step)
        self.spin_distribution_tolerance = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_distribution_tolerance.setDecimals(4)
        self.spin_distribution_tolerance.setRange(0.0001, 100000.0)
        self.spin_distribution_tolerance.setValue(0.5)
        distribution_form.addRow("Target tolerance", self.spin_distribution_tolerance)
        self.spin_distribution_nudge_mm = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_distribution_nudge_mm.setDecimals(4)
        self.spin_distribution_nudge_mm.setRange(0.0001, 10.0)
        self.spin_distribution_nudge_mm.setValue(0.01)
        self.spin_distribution_nudge_mm.setSuffix(" mm")
        distribution_form.addRow("Seek nudge", self.spin_distribution_nudge_mm)
        self.spin_distribution_points = QtWidgets.QSpinBox(automation_box)
        self.spin_distribution_points.setRange(1, 1000000)
        self.spin_distribution_points.setValue(10000)
        distribution_form.addRow("Points per plateau", self.spin_distribution_points)
        self.spin_distribution_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_distribution_interval.setRange(10, 60000)
        self.spin_distribution_interval.setValue(100)
        self.spin_distribution_interval.setSuffix(" ms")
        distribution_form.addRow("Record interval", self.spin_distribution_interval)
        self.spin_distribution_settle_s = QtWidgets.QDoubleSpinBox(automation_box)
        self.spin_distribution_settle_s.setDecimals(2)
        self.spin_distribution_settle_s.setRange(0.0, 3600.0)
        self.spin_distribution_settle_s.setValue(1.0)
        self.spin_distribution_settle_s.setSuffix(" s")
        distribution_form.addRow("Plateau settle", self.spin_distribution_settle_s)
        self.check_distribution_return_sweep = QtWidgets.QCheckBox(
            "Sweep back to the start target after the forward pass",
            automation_box,
        )
        self.check_distribution_return_sweep.setChecked(True)
        distribution_form.addRow("", self.check_distribution_return_sweep)
        distribution_hint = QtWidgets.QLabel(
            "Closed-loop in Mini DMA terms: the stage nudges until load, stress, or strain is within tolerance, "
            "then records the requested point count before moving to the next plateau.",
            distribution_page,
        )
        distribution_hint.setWordWrap(True)
        distribution_hint.setStyleSheet("color: palette(mid);")
        distribution_form.addRow("", distribution_hint)
        self.recipe_stack.addWidget(distribution_page)

        automation_form.addRow("", self.recipe_stack)
        self.check_return_to_origin = QtWidgets.QCheckBox(
            "Return to the recipe start position when finished",
            automation_box,
        )
        self.check_return_to_origin.setChecked(True)
        automation_form.addRow("", self.check_return_to_origin)
        self.label_recipe_summary = QtWidgets.QLabel("Recipe ready: one-way ramp from the current position.")
        self.label_recipe_summary.setWordWrap(True)
        automation_form.addRow("", self.label_recipe_summary)
        self.label_recipe_estimate = QtWidgets.QLabel("Estimated points: - | Estimated duration: -")
        self.label_recipe_estimate.setWordWrap(True)
        automation_form.addRow("", self.label_recipe_estimate)

        ramp_buttons = QtWidgets.QHBoxLayout()
        start_ramp_button = QtWidgets.QPushButton("Start recipe", automation_box)
        start_ramp_button.clicked.connect(self._start_auto_ramp)
        ramp_buttons.addWidget(start_ramp_button)
        stop_ramp_button = QtWidgets.QPushButton("Stop recipe", automation_box)
        stop_ramp_button.clicked.connect(self._stop_auto_ramp)
        ramp_buttons.addWidget(stop_ramp_button)
        automation_form.addRow("", ramp_buttons)
        experiment_layout.addWidget(automation_box)

        manual_box = self._group_box("Manual Actions")
        manual_layout = QtWidgets.QVBoxLayout(manual_box)
        manual_hint = QtWidgets.QLabel(
            "Use manual controls for setup, preloading, or quick checks before launching a recipe."
        )
        manual_hint.setWordWrap(True)
        manual_layout.addWidget(manual_hint)
        manual_record = QtWidgets.QPushButton("Record point now", manual_box)
        manual_record.clicked.connect(self._record_current_point)
        manual_layout.addWidget(manual_record)
        manual_refresh = QtWidgets.QPushButton("Refresh Tic status", manual_box)
        manual_refresh.clicked.connect(self._refresh_tic_status)
        manual_layout.addWidget(manual_refresh)
        experiment_layout.addWidget(manual_box)
        experiment_layout.addStretch(1)
        tabs.addTab(experiment_tab, "Recipes")

        controls.addStretch(1)
        splitter.addWidget(control_scroll)

        plot_panel = QtWidgets.QWidget(splitter)
        plot_layout = QtWidgets.QVBoxLayout(plot_panel)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(6)

        hero_box = QtWidgets.QFrame(plot_panel)
        hero_box.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        hero_layout = QtWidgets.QHBoxLayout(hero_box)
        hero_layout.setContentsMargins(12, 10, 12, 10)
        hero_layout.setSpacing(18)
        hero_title = QtWidgets.QLabel("Mini DMA Dashboard", hero_box)
        hero_font = hero_title.font()
        hero_font.setPointSize(max(hero_font.pointSize(), 13))
        hero_font.setBold(True)
        hero_title.setFont(hero_font)
        hero_layout.addWidget(hero_title)
        self.button_plot_setup = QtWidgets.QPushButton("Configure plots", hero_box)
        self.button_plot_setup.clicked.connect(self._show_plot_config_dialog)
        hero_layout.addWidget(self.button_plot_setup)
        hero_layout.addStretch(1)
        self.label_recipe_banner = QtWidgets.QLabel("Manual mode", hero_box)
        self.label_recipe_banner.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        hero_layout.addWidget(self.label_recipe_banner)
        plot_layout.addWidget(hero_box, stretch=0)

        self.plot_config_dialog = PlotConfigDialog(self)
        plot_config_box = self._group_box("Plot Dashboard")
        plot_config_layout = QtWidgets.QGridLayout(plot_config_box)
        plot_config_layout.setContentsMargins(8, 8, 8, 8)
        plot_config_layout.setHorizontalSpacing(10)
        plot_config_layout.setVerticalSpacing(6)
        preset_row = QtWidgets.QHBoxLayout()
        dma_preset_button = QtWidgets.QPushButton("DMA preset", plot_config_box)
        dma_preset_button.clicked.connect(lambda: self._apply_plot_preset("dma"))
        preset_row.addWidget(dma_preset_button)
        heating_preset_button = QtWidgets.QPushButton("Heating preset", plot_config_box)
        heating_preset_button.clicked.connect(lambda: self._apply_plot_preset("heating"))
        preset_row.addWidget(heating_preset_button)
        mechanical_preset_button = QtWidgets.QPushButton("Mechanical preset", plot_config_box)
        mechanical_preset_button.clicked.connect(lambda: self._apply_plot_preset("mechanical"))
        preset_row.addWidget(mechanical_preset_button)
        preset_row.addStretch(1)
        plot_config_layout.addWidget(QtWidgets.QLabel("Presets", plot_config_box), 0, 0)
        plot_config_layout.addLayout(preset_row, 0, 1, 1, 5)

        header_labels = ("Tile", "Show", "Bottom X", "Left Y", "Right Y")
        for column, label in enumerate(header_labels):
            plot_config_layout.addWidget(QtWidgets.QLabel(label, plot_config_box), 1, column)

        self._plot_tiles = []
        for tile_index in range(4):
            visible = QtWidgets.QCheckBox(plot_config_box)
            visible.setChecked(True)
            x_combo = QtWidgets.QComboBox(plot_config_box)
            y_left_combo = QtWidgets.QComboBox(plot_config_box)
            y_right_combo = QtWidgets.QComboBox(plot_config_box)
            for combo in (x_combo, y_left_combo):
                for channel in self._plot_channels():
                    combo.addItem(channel.label, channel.key)
            y_right_combo.addItem("(none)", "")
            for channel in self._plot_channels():
                y_right_combo.addItem(channel.label, channel.key)
            for widget in (visible, x_combo, y_left_combo, y_right_combo):
                signal = (
                    widget.toggled
                    if isinstance(widget, QtWidgets.QCheckBox)
                    else widget.currentIndexChanged
                )
                signal.connect(self._refresh_plots)
            plot_config_layout.addWidget(QtWidgets.QLabel(f"Plot {tile_index + 1}", plot_config_box), tile_index + 2, 0)
            plot_config_layout.addWidget(visible, tile_index + 2, 1)
            plot_config_layout.addWidget(x_combo, tile_index + 2, 2)
            plot_config_layout.addWidget(y_left_combo, tile_index + 2, 3)
            plot_config_layout.addWidget(y_right_combo, tile_index + 2, 4)
            self._plot_tiles.append(
                PlotTileWidgets(
                    visible=visible,
                    x_combo=x_combo,
                    y_left_combo=y_left_combo,
                    y_right_combo=y_right_combo,
                )
            )
        self.plot_config_dialog.body_layout.addWidget(plot_config_box)

        plot_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical, plot_panel)
        plot_splitter.setChildrenCollapsible(False)
        plot_layout.addWidget(plot_splitter, stretch=1)

        plot_canvas_container = QtWidgets.QWidget(plot_splitter)
        plot_canvas_layout = QtWidgets.QVBoxLayout(plot_canvas_container)
        plot_canvas_layout.setContentsMargins(0, 0, 0, 0)
        plot_canvas_layout.setSpacing(6)
        self.figure = Figure(figsize=(10.5, 7.0))
        self.canvas = FigureCanvas(self.figure) if FigureCanvas is not None else None
        if NavigationToolbar is not None and self.canvas is not None:
            plot_canvas_layout.addWidget(NavigationToolbar(self.canvas, plot_canvas_container))
        if self.canvas is not None:
            plot_canvas_layout.addWidget(self.canvas, stretch=1)

        log_container = QtWidgets.QWidget(plot_splitter)
        log_layout = QtWidgets.QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(4)
        log_label = QtWidgets.QLabel("Run log", log_container)
        log_font = log_label.font()
        log_font.setBold(True)
        log_label.setFont(log_font)
        log_layout.addWidget(log_label)
        self.log_output = QtWidgets.QPlainTextEdit(log_container)
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(1000)
        self.log_output.setPlaceholderText("Mini DMA log output")
        log_layout.addWidget(self.log_output, stretch=1)
        plot_splitter.setStretchFactor(0, 5)
        plot_splitter.setStretchFactor(1, 2)
        splitter.addWidget(plot_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([520, 1280])

        for widget in (
            self.edit_name_composition,
            self.edit_name_wire,
            self.edit_name_specimen,
            self.edit_name_condition,
        ):
            widget.textChanged.connect(self._sync_auto_name_fields)
        for widget in (
            self.spin_ramp_distance,
            self.spin_cycle_amplitude,
            self.spin_cycle_count,
            self.spin_hold_target,
            self.spin_hold_duration_s,
            self.spin_distribution_start,
            self.spin_distribution_end,
            self.spin_distribution_step,
            self.spin_distribution_tolerance,
            self.spin_distribution_nudge_mm,
            self.spin_distribution_points,
            self.spin_distribution_settle_s,
            self.spin_heat_constant_current,
            self.spin_heat_start_current,
            self.spin_heat_max_current,
        ):
            widget.valueChanged.connect(self._update_recipe_mode_ui)
        for widget in (
            self.spin_ramp_step,
            self.spin_ramp_interval,
            self.spin_cycle_step,
            self.spin_cycle_interval,
            self.spin_hold_interval,
            self.spin_distribution_interval,
            self.spin_heat_step_current,
        ):
            widget.valueChanged.connect(self._update_recipe_mode_ui)
        self.check_return_to_origin.toggled.connect(self._update_recipe_mode_ui)
        self.check_distribution_return_sweep.toggled.connect(self._update_recipe_mode_ui)
        self.check_zero_on_preload.toggled.connect(self._refresh_live_labels)
        self.spin_preload_threshold_g.valueChanged.connect(self._refresh_live_labels)
        self.combo_heat_limit_action.currentIndexChanged.connect(self._update_recipe_mode_ui)
        self.combo_heating_mode.currentIndexChanged.connect(self._update_recipe_mode_ui)
        self.combo_distribution_basis.currentIndexChanged.connect(self._update_distribution_basis_ui)
        self.combo_distribution_basis.currentIndexChanged.connect(self._update_recipe_mode_ui)

        self.statusBar().showMessage("Ready")
        self._refresh_supply_ports()
        self._apply_supply_profile_defaults()
        self._update_distribution_basis_ui()
        self._apply_plot_preset("dma")
        self._update_recipe_mode_ui()
        self._refresh_plots()

    def _group_box(self, title: str) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox(title, self)
        box.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        return box

    def _build_status_card(
        self,
        title: str,
        value: str,
        detail: str,
        accent_color: str,
    ) -> tuple[QtWidgets.QFrame, QtWidgets.QLabel]:
        card = QtWidgets.QFrame(self)
        card.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        card.setStyleSheet(
            "QFrame { border: 1px solid palette(mid); border-radius: 8px; }"
            f"QLabel#statusValue {{ color: {accent_color}; font-weight: 700; }}"
        )
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        title_label = QtWidgets.QLabel(title, card)
        value_label = QtWidgets.QLabel(value, card)
        value_label.setObjectName("statusValue")
        detail_label = QtWidgets.QLabel(detail, card)
        detail_label.setWordWrap(True)
        detail_label.setStyleSheet("color: palette(mid);")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(detail_label)
        return card, value_label

    def _plot_channels(self) -> list[PlotChannel]:
        return [
            PlotChannel("elapsed_s", "Time (s)", "#ef4444", lambda point: point.elapsed_s),
            PlotChannel("position_mm", "Displacement (mm)", "#60a5fa", lambda point: point.position_mm),
            PlotChannel("raw_load_g", "Raw load (g)", "#f59e0b", lambda point: point.raw_load_g),
            PlotChannel("load_g", "Effective load (g)", "#38bdf8", lambda point: point.load_g),
            PlotChannel(
                "strain_pct",
                "Strain (%)",
                "#22c55e",
                lambda point: point.strain_pct,
            ),
            PlotChannel(
                "stress_mpa",
                "Stress (MPa)",
                "#a78bfa",
                lambda point: point.stress_mpa,
            ),
            PlotChannel(
                "current_set_mA",
                "Set current (mA)",
                "#f97316",
                lambda point: point.current_set_mA,
            ),
            PlotChannel(
                "current_measured_mA",
                "Measured current (mA)",
                "#fb7185",
                lambda point: point.current_measured_mA,
            ),
            PlotChannel("voltage_V", "Voltage (V)", "#facc15", lambda point: point.voltage_V),
            PlotChannel(
                "resistance_ohm",
                "Resistance (Ohm)",
                "#14b8a6",
                lambda point: point.resistance_ohm,
            ),
            PlotChannel("power_W", "Power (W)", "#c084fc", lambda point: point.power_W),
        ]

    def _plot_channel(self, key: str) -> PlotChannel | None:
        for channel in self._plot_channels():
            if channel.key == key:
                return channel
        return None

    def _compact_plot_label(self, label: str) -> str:
        compact = re.sub(r"\s*\([^)]*\)", "", label).strip()
        compact = compact.replace("Effective load", "Load")
        compact = compact.replace("Measured current", "Current")
        compact = compact.replace("Displacement", "Disp.")
        compact = compact.replace("Resistance", "Res.")
        return compact

    def _plot_title(
        self,
        x_channel: PlotChannel,
        y_left_channel: PlotChannel,
        y_right_channel: PlotChannel | None,
    ) -> str:
        x_label = self._compact_plot_label(x_channel.label)
        left_label = self._compact_plot_label(y_left_channel.label)
        if y_right_channel is None:
            return f"{left_label} vs {x_label}"
        right_label = self._compact_plot_label(y_right_channel.label)
        return f"{left_label} + {right_label} vs {x_label}"

    def _apply_plot_preset(self, preset: str) -> None:
        presets = {
            "dma": [
                ("elapsed_s", "load_g", "current_measured_mA"),
                ("elapsed_s", "position_mm", "strain_pct"),
                ("position_mm", "load_g", ""),
                ("strain_pct", "stress_mpa", ""),
            ],
            "heating": [
                ("elapsed_s", "current_measured_mA", "voltage_V"),
                ("elapsed_s", "resistance_ohm", "power_W"),
                ("elapsed_s", "load_g", "position_mm"),
                ("strain_pct", "stress_mpa", "current_measured_mA"),
            ],
            "mechanical": [
                ("position_mm", "load_g", ""),
                ("strain_pct", "stress_mpa", ""),
                ("elapsed_s", "load_g", ""),
                ("elapsed_s", "position_mm", "strain_pct"),
            ],
        }
        config = presets.get(preset, presets["dma"])
        for index, tile in enumerate(self._plot_tiles):
            x_key, y_left, y_right = config[index]
            tile.visible.setChecked(True)
            x_index = tile.x_combo.findData(x_key)
            if x_index >= 0:
                tile.x_combo.setCurrentIndex(x_index)
            y_left_index = tile.y_left_combo.findData(y_left)
            if y_left_index >= 0:
                tile.y_left_combo.setCurrentIndex(y_left_index)
            y_right_index = tile.y_right_combo.findData(y_right)
            if y_right_index >= 0:
                tile.y_right_combo.setCurrentIndex(y_right_index)
        self._refresh_plots()

    def _plot_theme(self) -> dict[str, Any]:
        palette = self.palette()
        app = QtWidgets.QApplication.instance()
        style_hints = app.styleHints() if isinstance(app, QtWidgets.QApplication) else None
        color_scheme = style_hints.colorScheme() if style_hints is not None else QtCore.Qt.ColorScheme.Light
        window = palette.color(QtGui.QPalette.ColorRole.Window)
        base = palette.color(QtGui.QPalette.ColorRole.Base)
        text = palette.color(QtGui.QPalette.ColorRole.Text)
        mid = palette.color(QtGui.QPalette.ColorRole.Mid)
        grid = QtGui.QColor(mid)
        grid.setAlpha(160 if color_scheme == QtCore.Qt.ColorScheme.Dark else 120)
        return {
            "dark": color_scheme == QtCore.Qt.ColorScheme.Dark,
            "figure_rgb": window.getRgbF()[:3],
            "axes_rgb": base.getRgbF()[:3],
            "text_rgb": text.getRgbF()[:3],
            "grid_rgba": grid.getRgbF(),
        }

    def _show_plot_config_dialog(self) -> None:
        if self.plot_config_dialog.isHidden():
            self.plot_config_dialog.show()
        self.plot_config_dialog.raise_()
        self.plot_config_dialog.activateWindow()

    def _refresh_supply_ports(self) -> None:
        current = self.combo_supply_port.currentData() or self.settings.value("supply_port", "", type=str)
        self.combo_supply_port.clear()
        if list_ports is None:
            self.combo_supply_port.addItem("pyserial unavailable", "")
            return
        for port in list_ports.comports():
            label = f"{port.device} - {port.description}"
            self.combo_supply_port.addItem(label, port.device)
        if current:
            index = self.combo_supply_port.findData(current)
            if index >= 0:
                self.combo_supply_port.setCurrentIndex(index)

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.log_output.appendPlainText(line)
        self.statusBar().showMessage(message, 5000)

    def _refresh_scale_ports(self) -> None:
        current = self.combo_scale_port.currentData() or self.settings.value("scale_port", "", type=str)
        self.combo_scale_port.clear()
        if list_ports is None:
            self.combo_scale_port.addItem("pyserial unavailable", "")
            return
        seen = False
        preferred_index = -1
        for port in list_ports.comports():
            label = f"{port.device} - {port.description}"
            self.combo_scale_port.addItem(label, port.device)
            if current and port.device == current:
                seen = True
            description = port.description.lower()
            if "prolific" in description or "pl2303" in description:
                preferred_index = self.combo_scale_port.count() - 1
            elif preferred_index < 0 and port.device.upper() == "COM4":
                preferred_index = self.combo_scale_port.count() - 1
        if current and seen:
            index = self.combo_scale_port.findData(current)
            if index >= 0:
                self.combo_scale_port.setCurrentIndex(index)
        elif preferred_index >= 0:
            self.combo_scale_port.setCurrentIndex(preferred_index)
        elif self.combo_scale_port.count():
            self.combo_scale_port.setCurrentIndex(0)

    def _build_tic_controller(self) -> TicController:
        return TicController(
            command_path=self.edit_ticcmd_path.text(),
            device_serial=self.edit_tic_serial.text(),
        )

    def _build_supply_controller(self) -> PowerSupplyController:
        return PowerSupplyController(
            port_name=str(self.combo_supply_port.currentData() or "").strip(),
            baudrate=int(self.combo_supply_baud.currentText()),
            profile_id=str(self.combo_supply_profile.currentData() or "hmp4030"),
            max_voltage_v=float(self.spin_supply_voltage_limit.value()),
        )

    def _apply_supply_profile_defaults(self) -> None:
        profile_id = str(self.combo_supply_profile.currentData() or "hmp4030")
        profile = SUPPLY_PROFILES.get(profile_id, SUPPLY_PROFILES["hmp4030"])
        self.spin_supply_voltage_limit.setValue(float(profile.get("max_voltage", 30.0)))
        self.spin_supply_manual_current.setValue(float(profile.get("start_current_mA", 1.0)))
        self.spin_heat_start_current.setValue(float(profile.get("start_current_mA", 1.0)))
        self.spin_heat_constant_current.setValue(max(float(profile.get("start_current_mA", 1.0)), 10.0))

    def _connect_supply(self) -> None:
        self._disconnect_supply()
        controller = self._build_supply_controller()
        try:
            controller.connect()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to connect power supply: {exc}")
            return
        self._supply_controller = controller
        self.label_supply_status.setText(
            f"Supply connected on {controller.port_name} at {controller.baudrate} baud ({controller.profile['label']})."
        )
        self._log(self.label_supply_status.text())
        self._refresh_supply_snapshot()

    def _disconnect_supply(self) -> None:
        if self._supply_controller is not None:
            self._supply_controller.disconnect()
        self._supply_controller = None
        self._supply_output_enabled = False
        self.label_supply_status.setText("Supply disconnected.")
        self._refresh_supply_live_label()

    def _refresh_supply_live_label(self) -> None:
        setpoint_text = "-" if self._supply_last_setpoint_mA is None else f"{self._supply_last_setpoint_mA:.2f} mA"
        current_text = "-" if self._supply_snapshot["current_mA"] is None else f"{self._supply_snapshot['current_mA']:.2f} mA"
        voltage_text = "-" if self._supply_snapshot["voltage_V"] is None else f"{self._supply_snapshot['voltage_V']:.3f} V"
        resistance_text = "-" if self._supply_snapshot["resistance_ohm"] is None else f"{self._supply_snapshot['resistance_ohm']:.3f} Ohm"
        power_text = "-" if self._supply_snapshot["power_W"] is None else f"{self._supply_snapshot['power_W']:.4f} W"
        self.label_supply_live.setText(
            f"Set {setpoint_text} | Current {current_text} | Voltage {voltage_text} | "
            f"Resistance {resistance_text} | Power {power_text}"
        )

    def _refresh_supply_snapshot(self) -> dict[str, float | None]:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            self._refresh_supply_live_label()
            return dict(self._supply_snapshot)
        try:
            self._supply_snapshot = dict(self._supply_controller.measure())
        except Exception as exc:
            self._log(f"Supply read failed: {exc}")
        self._refresh_supply_live_label()
        self._handle_supply_limit_condition()
        return dict(self._supply_snapshot)

    def _apply_manual_supply_current(self) -> None:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            QtWidgets.QMessageBox.information(self, APP_NAME, "Connect the power supply first.")
            return
        try:
            controller = self._supply_controller
            assert controller is not None
            controller.initialize_output(
                current_mA=float(self.spin_supply_manual_current.value()),
                reset_on_start=bool(controller.profile.get("reset_on_start", False)),
            )
            self._supply_output_enabled = True
            self._supply_last_setpoint_mA = float(self.spin_supply_manual_current.value())
            self._heating_program_current_mA = self._supply_last_setpoint_mA
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to apply current: {exc}")
            return
        self.label_supply_status.setText("Supply output enabled from the manual current control.")
        self._refresh_supply_snapshot()

    def _enable_supply_output(self) -> None:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            QtWidgets.QMessageBox.information(self, APP_NAME, "Connect the power supply first.")
            return
        try:
            self._supply_controller.output_on()
            self._supply_output_enabled = True
            self.label_supply_status.setText("Supply output enabled.")
            self._refresh_supply_snapshot()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to enable output: {exc}")

    def _disable_supply_output(self) -> None:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            return
        try:
            self._supply_controller.output_off()
        except Exception as exc:
            self._log(f"Failed to disable supply output: {exc}")
        self._supply_output_enabled = False
        self.label_supply_status.setText("Supply output disabled.")
        self._refresh_supply_snapshot()

    def _set_reference_from_current_position(self) -> None:
        self._position_reference_mm = self._current_position_mm
        self._preload_reference_armed = False
        self._preload_trigger_elapsed_s = 0.0 if self._session_active else None
        self._refresh_live_labels()
        self._log(f"Gauge zero moved to the current position ({self._current_position_mm:.4f} mm).")

    def _heating_mode(self) -> str:
        return str(self.combo_heating_mode.currentData() or HEATING_MODE_OFF)

    def _prepare_heating_for_session(self) -> None:
        mode = self._heating_mode()
        if mode == HEATING_MODE_OFF:
            return
        if self._supply_controller is None or not self._supply_controller.is_connected():
            self._log("Heating program is enabled, but the power supply is not connected.")
            return
        if mode == HEATING_MODE_CONSTANT:
            target = float(self.spin_heat_constant_current.value())
        else:
            target = float(self.spin_heat_start_current.value())
        self._heating_program_current_mA = target
        self._heating_program_direction = 1.0
        self._supply_controller.initialize_output(
            current_mA=target,
            reset_on_start=bool(self._supply_controller.profile.get("reset_on_start", False)),
        )
        self._supply_output_enabled = True
        self._supply_last_setpoint_mA = target
        self.label_supply_status.setText(
            f"Heating program armed in {HEATING_MODE_LABELS.get(mode, mode)} mode."
        )
        self._refresh_supply_snapshot()

    def _advance_heating_after_record(self) -> None:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            return
        mode = self._heating_mode()
        if mode in {HEATING_MODE_OFF, HEATING_MODE_CONSTANT}:
            return
        current = self._heating_program_current_mA
        if current is None:
            current = float(self.spin_heat_start_current.value())
        step = abs(float(self.spin_heat_step_current.value()))
        upper = max(float(self.spin_heat_start_current.value()), float(self.spin_heat_max_current.value()))
        lower = min(float(self.spin_heat_start_current.value()), float(self.spin_heat_max_current.value()))
        next_current = current + (self._heating_program_direction * step)
        if mode == HEATING_MODE_RAMP:
            next_current = min(upper, next_current)
        else:
            if next_current >= upper:
                next_current = upper
                self._heating_program_direction = -1.0
            elif next_current <= lower:
                next_current = lower
                self._heating_program_direction = 1.0
        if abs(next_current - current) < 1e-12:
            return
        try:
            self._supply_controller.set_current_mA(next_current)
            self._heating_program_current_mA = next_current
            self._supply_last_setpoint_mA = next_current
        except Exception as exc:
            self._log(f"Heating step failed: {exc}")

    def _handle_supply_limit_condition(self) -> None:
        limit_v = float(self.spin_supply_voltage_limit.value())
        measured_v = self._supply_snapshot.get("voltage_V")
        if measured_v is None or limit_v <= 0:
            return
        if measured_v < limit_v * 0.995:
            return
        action = str(self.combo_heat_limit_action.currentData() or HEATING_LIMIT_STOP)
        self._log(f"Supply voltage reached the configured limit ({measured_v:.3f} V / {limit_v:.3f} V).")
        if action == HEATING_LIMIT_DISABLE:
            self._disable_supply_output()
        elif action == HEATING_LIMIT_HOLD:
            return
        else:
            self._stop_auto_ramp(log_completion=False)

    def _choose_builder_project(self) -> None:
        start_dir = str(self._builder_project_path.parent) if self._builder_project_path is not None else self.edit_log_dir.text().strip()
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Microwire Builder project",
            start_dir,
            f"Microwire Project (*{PROJECT_EXTENSION});;All files (*)",
        )
        if path_str:
            self.edit_project_path.setText(path_str)

    def _project_match_score(self, row: Mapping[str, Any]) -> int:
        score = 0
        composition = _normalized_token(self.edit_name_composition.text())
        microwire = _normalized_microwire_token(self.edit_name_wire.text())
        specimen = _normalized_token(self.edit_name_specimen.text())
        row_composition = _normalized_token(row.get("Composition"))
        row_microwire = _normalized_microwire_token(_project_row_value(row, PROJECT_ROW_MICROWIRE_KEYS))
        row_specimen = _normalized_token(_project_row_value(row, PROJECT_ROW_SPECIMEN_KEYS))
        if composition and row_composition == composition:
            score += 5
        if microwire and row_microwire == microwire:
            score += 5
        if specimen and row_specimen == specimen:
            score += 3
        diameter = _safe_float(_project_row_value(row, PROJECT_ROW_DIAMETER_KEYS))
        if diameter and diameter > 0:
            score += 2
        return score

    def _find_project_sample(self, payload: Any, path: Path) -> ProjectImportResult | None:
        rows_by_section: list[tuple[str, list[Any]]] = []
        if isinstance(payload, Mapping):
            sections = payload.get("sections", {})
            if isinstance(sections, Mapping):
                preferred_sections = ("microscope", "assemble", "shape_memory_stress_strain")
                for section_name in preferred_sections:
                    section_payload = sections.get(section_name)
                    if not isinstance(section_payload, Mapping):
                        continue
                    rows = section_payload.get("rows")
                    if isinstance(rows, list):
                        rows_by_section.append((section_name, rows))
            top_level_rows = payload.get("rows")
            if isinstance(top_level_rows, list):
                rows_by_section.append(("rows", top_level_rows))
        elif isinstance(payload, list):
            rows_by_section.append(("rows", payload))
        if not rows_by_section:
            return None
        best_score = -1
        best_match: ProjectImportResult | None = None
        for section_name, rows in rows_by_section:
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                score = self._project_match_score(row)
                if score < 0:
                    continue
                diameter_um = _safe_float(_project_row_value(row, PROJECT_ROW_DIAMETER_KEYS))
                current_mA = _safe_float(_project_row_value(row, PROJECT_ROW_CURRENT_KEYS))
                if score > best_score:
                    best_score = score
                    best_match = ProjectImportResult(
                        path=path,
                        section=section_name,
                        diameter_mm=None if diameter_um is None else diameter_um / 1000.0,
                        current_mA=current_mA,
                        matched_row=dict(row),
                    )
        return best_match if best_score >= 0 else None

    def _import_builder_project(self) -> None:
        path = Path(self.edit_project_path.text().strip())
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Choose a valid .pydpj file first.")
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to read project file: {exc}")
            return
        match = self._find_project_sample(payload, path)
        if match is None:
            self.label_project_status.setText(
                "Project loaded, but no matching sample row was found from the current naming fields."
            )
            return
        self._builder_project_path = path
        self._builder_project_match = match
        row = match.matched_row
        if row.get("Composition"):
            self.edit_name_composition.setText(str(row.get("Composition")))
        microwire_value = _project_row_value(row, PROJECT_ROW_MICROWIRE_KEYS)
        if microwire_value:
            self.edit_name_wire.setText(str(microwire_value))
        specimen_value = _project_row_value(row, PROJECT_ROW_SPECIMEN_KEYS)
        if specimen_value:
            self.edit_name_specimen.setText(str(specimen_value))
        if match.diameter_mm is not None:
            self.spin_diameter.setValue(match.diameter_mm)
        if match.current_mA is not None:
            self.spin_heat_constant_current.setValue(match.current_mA)
        self.label_project_status.setText(
            f"Imported {path.name} -> section {match.section}, diameter "
            f"{'-' if match.diameter_mm is None else f'{match.diameter_mm:.5f} mm'}"
            f"{'' if match.current_mA is None else f', current {match.current_mA:.2f} mA'}."
        )
        self._sync_auto_name_fields()

    def _toggle_scale_connection(self) -> None:
        if self._scale_thread is not None:
            self._disconnect_scale()
        else:
            self._connect_scale()

    def _connect_scale(self) -> None:
        port_name = str(self.combo_scale_port.currentData() or "").strip()
        if not port_name:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Select a scale serial port first.")
            return
        baudrate = int(self.combo_scale_baud.currentText())
        worker = ScaleWorker(
            port_name=port_name,
            baudrate=baudrate,
            poll_interval_ms=int(self.spin_scale_interval.value()),
            request_command=self.edit_scale_request.text(),
            request_terminator=self.edit_scale_terminator.text(),
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.measurement_received.connect(self._handle_scale_measurement)
        worker.status_changed.connect(self._handle_scale_status)
        worker.error_occurred.connect(self._handle_scale_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._handle_scale_thread_finished)
        self._scale_worker = worker
        self._scale_thread = thread
        self._scale_connected_at_s = time.time()
        self._scale_no_data_hint_emitted = False
        thread.start()
        self.button_scale_connect.setText("Disconnect scale")
        self._scale_hint_timer.start(SCALE_NO_DATA_HINT_DELAY_MS)

    def _disconnect_scale(self) -> None:
        worker = self._scale_worker
        thread = self._scale_thread
        self._scale_worker = None
        self._scale_thread = None
        self._scale_connected_at_s = None
        self._scale_no_data_hint_emitted = False
        self._scale_hint_timer.stop()
        if worker is not None:
            worker.stop()
        if thread is not None:
            thread.quit()
            thread.wait(1500)
        self.button_scale_connect.setText("Connect scale")

    def _handle_scale_thread_finished(self) -> None:
        self.button_scale_connect.setText("Connect scale")
        self._scale_hint_timer.stop()
        self._refresh_live_labels()

    def _handle_scale_measurement(self, value_g: float, raw_text: str, timestamp_s: float) -> None:
        self._latest_scale_value_g = value_g
        self._latest_scale_text = raw_text
        self._latest_scale_timestamp = timestamp_s
        self._scale_no_data_hint_emitted = True
        self._scale_hint_timer.stop()
        self._refresh_live_labels()

    def _handle_scale_status(self, message: str) -> None:
        self._log(message)
        self._refresh_live_labels()

    def _handle_scale_error(self, message: str) -> None:
        self._log(message)
        self.label_scale_raw.setText(f"Raw line: {message}")
        self._refresh_live_labels()

    def _tare_scale(self) -> None:
        self._load_offset_g = -self._latest_scale_value_g
        self._refresh_live_labels()
        self._log(f"Software tare set to {self._load_offset_g:+.5f} g.")

    def _apply_gng_scale_preset(self) -> None:
        if self.combo_scale_baud.findText("600") >= 0:
            self.combo_scale_baud.setCurrentText("600")
        self.edit_scale_request.setText("\\x1bp")
        self.edit_scale_terminator.setText("")
        self._log("Applied G&G E-series scale preset: 600 baud, ESC+p request, no extra terminator.")

    def _build_sample_name(self) -> str:
        parts = [
            self.edit_name_composition.text().strip(),
            self.edit_name_wire.text().strip(),
            self.edit_name_specimen.text().strip(),
            self.edit_name_condition.text().strip(),
        ]
        return " ".join(part for part in parts if part)

    def _distribution_log_suffix(self) -> str:
        basis = self._distribution_basis()
        basis_label = {
            HSW_BASIS_LOAD_G: "load",
            HSW_BASIS_STRESS_MPA: "stress",
            HSW_BASIS_STRAIN_PCT: "strain",
        }.get(basis, "distribution")
        start_token = f"{self.spin_distribution_start.value():.3f}".rstrip("0").rstrip(".")
        end_token = f"{self.spin_distribution_end.value():.3f}".rstrip("0").rstrip(".")
        step_token = f"{self.spin_distribution_step.value():.3f}".rstrip("0").rstrip(".")
        return f"hsw-{basis_label}-{start_token}-{end_token}-step{step_token}"

    def _apply_name_fields(self) -> None:
        built = self._build_sample_name()
        if built:
            self.edit_sample_name.setText(built)
            log_label = built
            if str(self.combo_recipe_mode.currentData() or "") == "distribution":
                log_label = f"{built} {self._distribution_log_suffix()}".strip()
            safe_name = re.sub(r'[<>:"/\\\\|?*]+', "_", log_label).strip(" .")
            self.edit_log_name.setText(safe_name or DEFAULT_LOG_BASENAME)
            self._log(f"Applied naming fields: {built}")

    def _sync_auto_name_fields(self) -> None:
        if self.check_auto_name.isChecked():
            built = self._build_sample_name()
            if built:
                self.edit_sample_name.setText(built)
                log_label = built
                if str(self.combo_recipe_mode.currentData() or "") == "distribution":
                    log_label = f"{built} {self._distribution_log_suffix()}".strip()
                safe_name = re.sub(r'[<>:"/\\\\|?*]+', "_", log_label).strip(" .")
                self.edit_log_name.setText(safe_name or DEFAULT_LOG_BASENAME)

    def _set_position_reference_now(self) -> None:
        self._position_reference_mm = self._current_position_mm
        self._refresh_live_labels()
        self._log(f"Reference position set to the current stage position ({self._position_reference_mm:.4f} mm).")

    def _distribution_basis(self) -> str:
        return str(self.combo_distribution_basis.currentData() or HSW_BASIS_STRESS_MPA)

    def _distribution_units(self, basis: str | None = None) -> tuple[str, int]:
        basis = basis or self._distribution_basis()
        if basis == HSW_BASIS_LOAD_G:
            return " g", 4
        if basis == HSW_BASIS_STRAIN_PCT:
            return " %", 4
        return " MPa", 3

    def _update_distribution_basis_ui(self) -> None:
        suffix, decimals = self._distribution_units()
        for widget in (
            self.spin_distribution_start,
            self.spin_distribution_end,
            self.spin_distribution_step,
            self.spin_distribution_tolerance,
        ):
            widget.blockSignals(True)
            widget.setDecimals(decimals)
            widget.setSuffix(suffix)
            widget.blockSignals(False)

    def _build_distribution_targets(
        self,
        start_value: float,
        end_value: float,
        step_value: float,
        *,
        include_return: bool,
    ) -> list[float]:
        if step_value <= 0.0:
            raise ValueError("Distribution step must be greater than zero.")
        delta_value = end_value - start_value
        if delta_value == 0.0:
            targets = [start_value]
        else:
            sign = 1.0 if delta_value >= 0.0 else -1.0
            count = max(1, int(math.ceil(abs(delta_value) / step_value)))
            targets = [
                start_value + sign * min(index * step_value, abs(delta_value))
                for index in range(0, count + 1)
            ]
        if include_return and len(targets) > 1:
            targets.extend(reversed(targets[:-1]))
        return targets

    def _current_distribution_value(self, basis: str) -> float | None:
        effective_load = self._current_effective_load_g()
        if basis == HSW_BASIS_LOAD_G:
            return effective_load
        if basis == HSW_BASIS_STRESS_MPA:
            return stress_mpa_from_load_g(effective_load, float(self.spin_diameter.value()))
        preload_state = self._current_preload_state(effective_load)
        if preload_state == PRELOAD_PENDING:
            return None
        return strain_percent(
            self._current_position_mm,
            float(self.spin_initial_length.value()),
            self._position_reference_mm,
        )

    def _distribution_target_reached(self, basis: str, target_value: float, tolerance: float) -> bool:
        current_value = self._current_distribution_value(basis)
        if current_value is None:
            return False
        return abs(target_value - current_value) <= tolerance

    def _seek_distribution_target(self, basis: str, target_value: float, tolerance: float) -> bool:
        if self._distribution_target_reached(basis, target_value, tolerance):
            return True
        current_value = self._current_distribution_value(basis)
        if current_value is None:
            current_value = 0.0
        delta_value = target_value - current_value
        if abs(delta_value) <= tolerance:
            return True
        nudge_mm = abs(float(self.spin_distribution_nudge_mm.value()))
        if nudge_mm <= 0.0:
            raise ValueError("Set a non-zero seek nudge for Hsw distribution.")
        target_mm = self._current_position_mm + math.copysign(nudge_mm, delta_value)
        if not self._move_to_position_mm(target_mm):
            return False
        return self._distribution_target_reached(basis, target_value, tolerance)

    def _update_recipe_mode_ui(self) -> None:
        mode = str(self.combo_recipe_mode.currentData() or "ramp")
        page_index = {"ramp": 0, "cycle": 1, "hold": 2, "distribution": 3}.get(mode, 0)
        self.recipe_stack.setCurrentIndex(page_index)
        if mode == "cycle":
            summary = (
                f"Recipe ready: {self.spin_cycle_count.value()} triangular cycle(s) "
                f"with ±{abs(self.spin_cycle_amplitude.value()):.4f} mm amplitude."
            )
            banner = "Cycle recipe"
        elif mode == "hold":
            summary = (
                f"Recipe ready: move by {self.spin_hold_target.value():.4f} mm and hold for "
                f"{self.spin_hold_duration_s.value():.1f} s."
            )
            banner = "Hold recipe"
        elif mode == "distribution":
            basis = self._distribution_basis()
            suffix, _ = self._distribution_units(basis)
            summary = (
                f"Recipe ready: Hsw distribution from {self.spin_distribution_start.value():.4f}{suffix} "
                f"to {self.spin_distribution_end.value():.4f}{suffix} in "
                f"{self.spin_distribution_step.value():.4f}{suffix} steps, "
                f"{self.spin_distribution_points.value()} point(s) per plateau."
            )
            if self.check_distribution_return_sweep.isChecked():
                summary += " Includes a reverse sweep."
            summary += (
                f" Target tolerance {self.spin_distribution_tolerance.value():.4f}{suffix} "
                f"with {self.spin_distribution_nudge_mm.value():.4f} mm seek nudges and "
                f"{self.spin_distribution_settle_s.value():.2f} s settling."
            )
            banner = "Hsw distribution"
        else:
            summary = (
                f"Recipe ready: one-way ramp of {self.spin_ramp_distance.value():.4f} mm "
                f"from the current position."
            )
            banner = "Ramp recipe"
        heating_mode = self._heating_mode()
        if heating_mode == HEATING_MODE_CONSTANT:
            summary += f" Heating: hold {self.spin_heat_constant_current.value():.2f} mA."
        elif heating_mode == HEATING_MODE_RAMP:
            summary += (
                f" Heating: ramp {self.spin_heat_start_current.value():.2f} → "
                f"{self.spin_heat_max_current.value():.2f} mA in {self.spin_heat_step_current.value():.2f} mA steps."
            )
        elif heating_mode == HEATING_MODE_TRIANGLE:
            summary += (
                f" Heating: triangle between {self.spin_heat_start_current.value():.2f} and "
                f"{self.spin_heat_max_current.value():.2f} mA."
            )
        preload_text = (
            f" Strain zero waits for {self.spin_preload_threshold_g.value():.4f} g preload."
            if self.check_zero_on_preload.isChecked() and self.spin_preload_threshold_g.value() > 0
            else " Strain zero follows the current reference immediately."
        )
        summary += preload_text
        self.label_recipe_summary.setText(summary)
        self.label_recipe_banner.setText(banner)
        try:
            steps, _, interval_ms = self._build_automation_recipe()
            record_points = sum(1 for step in steps if step.action == "record")
            duration_s = (len(steps) * interval_ms) / 1000.0
            self._recipe_estimated_points = record_points
            self.label_recipe_estimate.setText(
                f"Estimated points: {record_points} | Estimated duration: {duration_s:.1f} s"
            )
        except Exception:
            self._recipe_estimated_points = 0
            self.label_recipe_estimate.setText("Estimated points: - | Estimated duration: -")

    def _warn_if_scale_is_silent(self) -> None:
        if self._scale_thread is None or self._scale_no_data_hint_emitted:
            return
        connected_at_s = self._scale_connected_at_s
        if connected_at_s is None:
            return
        if self._latest_scale_timestamp is not None and self._latest_scale_timestamp >= connected_at_s:
            return
        self._scale_no_data_hint_emitted = True
        self._log(
            "Scale connected but no serial data arrived. G&G documentation says these balances need a "
            "DB9 null modem crossover, so a straight-through adapter/cable chain will stay silent."
        )

    def _probe_scale_port(self) -> None:
        if self._scale_thread is not None:
            QtWidgets.QMessageBox.information(
                self,
                APP_NAME,
                "Disconnect the live scale connection first, then run Probe scale.",
            )
            return

        port_name = str(self.combo_scale_port.currentData() or "").strip()
        if not port_name:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Select a scale serial port first.")
            return

        trials = [
            ("Passive listen", 600, b""),
            ("G&G request", 600, b"\x1bp"),
            ("G&G request", 9600, b"\x1bp"),
            ("G&G request+CRLF", 9600, b"\x1bp\r\n"),
        ]
        findings: list[str] = []
        errors: list[str] = []

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            for label, baudrate, payload in trials:
                try:
                    raw = _read_serial_bytes(
                        port_name,
                        baudrate=baudrate,
                        payload=payload,
                        total_wait_s=1.1,
                    )
                except Exception as exc:
                    errors.append(f"{label} @ {baudrate} baud failed: {exc}")
                    continue
                if raw:
                    findings.append(f"{label} @ {baudrate} baud returned {raw!r}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        if findings:
            for line in findings:
                self._log(f"Scale probe: {line}")
            return

        for line in errors:
            self._log(f"Scale probe: {line}")
        supported = ", ".join(str(value) for value in GNG_SUPPORTED_BAUDS)
        self._log(
            "Scale probe found no serial response on the selected port. Tested passive listen plus ESC+p "
            f"requests at 600 and 9600 baud. G&G docs list supported rates {supported} and warn that the "
            "balance needs a null modem crossover instead of a straight-through DB9 link."
        )

    def _append_return_to_origin(self, steps: list[AutomationStep]) -> list[AutomationStep]:
        if not self.check_return_to_origin.isChecked():
            return steps
        final_target = self._recipe_origin_mm
        if steps and steps[-1].action == "move" and steps[-1].target_mm == final_target:
            return steps
        steps = list(steps)
        steps.extend(
            (
                AutomationStep("move", final_target, "Return to origin"),
                AutomationStep("record", note="Origin checkpoint"),
            )
        )
        return steps

    def _refresh_tic_status(self) -> None:
        controller = self._build_tic_controller()
        try:
            status_text = controller.get_status()
        except Exception as exc:
            self._log(f"Tic status failed: {exc}")
            self.label_tic_summary.setText(str(exc))
            self.label_card_motion.setText("Tic unavailable")
            self._status_timer.stop()
            return
        self._tic_status_text = status_text
        current_position_text = _extract_status_value(status_text, "Current position")
        if current_position_text is not None:
            current_position = _extract_first_int(current_position_text)
            if current_position is not None:
                self._current_position_steps = current_position
                self._current_position_mm = current_position / float(self.spin_steps_per_mm.value())
        operation_state = _extract_status_value(status_text, "Operation state") or "unknown"
        errors = _extract_status_value(status_text, "Errors currently stopping the motor") or "none"
        self.label_tic_summary.setText(
            f"Operation state: {operation_state}\nErrors: {errors}"
        )
        self.label_card_motion.setText(f"{operation_state} | {self._current_position_mm:.4f} mm")
        self._refresh_live_labels()
        self._status_timer.start()

    def _zero_tic_position(self) -> None:
        controller = self._build_tic_controller()
        try:
            controller.set_current_position(0)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to set Tic position: {exc}")
            return
        self._current_position_steps = 0
        self._current_position_mm = 0.0
        self._position_reference_mm = 0.0
        self._refresh_live_labels()
        self._log("Tic current position was set to 0.")
        self._refresh_tic_status()

    def _halt_tic(self) -> None:
        controller = self._build_tic_controller()
        try:
            controller.halt_and_hold()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to halt Tic: {exc}")
            return
        self._log("Sent halt-and-hold to Tic.")
        self._refresh_tic_status()

    def _jog_relative(self, direction: float) -> None:
        distance_mm = abs(float(self.spin_jog_mm.value())) * float(direction)
        self._move_to_position_mm(self._current_position_mm + distance_mm)

    def _is_max_load_exceeded(self) -> bool:
        if not self.check_max_load.isChecked():
            return False
        return abs(self._current_effective_load_g()) > float(self.spin_max_load_g.value())

    def _move_to_position_mm(self, position_mm: float) -> bool:
        if self._is_max_load_exceeded():
            self._log(
                f"Move cancelled because effective load {self._current_effective_load_g():.5f} g exceeds "
                f"the safety limit of {self.spin_max_load_g.value():.5f} g."
            )
            if self._automation_active:
                self._stop_auto_ramp(log_completion=False)
            return False
        if self.check_soft_limits.isChecked():
            min_mm = min(float(self.spin_soft_min_mm.value()), float(self.spin_soft_max_mm.value()))
            max_mm = max(float(self.spin_soft_min_mm.value()), float(self.spin_soft_max_mm.value()))
            if position_mm < min_mm or position_mm > max_mm:
                self._log(
                    f"Move cancelled because {position_mm:.4f} mm is outside soft limits "
                    f"[{min_mm:.4f}, {max_mm:.4f}] mm."
                )
                if self._automation_active:
                    self._stop_auto_ramp(log_completion=False)
                return False
        steps_per_mm = float(self.spin_steps_per_mm.value())
        target_steps = int(round(position_mm * steps_per_mm))
        controller = self._build_tic_controller()
        try:
            controller.set_target_position(target_steps)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to move Tic: {exc}")
            return False
        self._log(f"Move command sent to {position_mm:.4f} mm ({target_steps} steps).")
        self._current_position_steps = target_steps
        self._current_position_mm = position_mm
        self._last_move_target_mm = position_mm
        self._refresh_live_labels()
        return True

    def _session_base_paths(self) -> tuple[Path, Path, Path]:
        directory = Path(self.edit_log_dir.text().strip() or _default_download_dir())
        directory.mkdir(parents=True, exist_ok=True)
        basename = self.edit_log_name.text().strip() or DEFAULT_LOG_BASENAME
        return (
            directory / f"{basename}.txt",
            directory / f"{basename}.csv",
            directory / f"{basename}.json",
        )

    def _prepare_session_files(self) -> tuple[Any, Any, csv.DictWriter[str], Path, Path]:
        txt_path, csv_path, json_path = self._session_base_paths()
        if txt_path.exists() or csv_path.exists() or json_path.exists():
            answer = QtWidgets.QMessageBox.question(
                self,
                APP_NAME,
                f"{txt_path.name}, {csv_path.name}, or {json_path.name} already exists. Replace them?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                raise RuntimeError("Session start cancelled because output files already exist.")

        txt_handle = txt_path.open("w", encoding="utf-8", newline="")
        csv_handle = csv_path.open("w", encoding="utf-8", newline="")
        txt_handle.write("\t".join(LONG_NAMES) + "\n")
        txt_handle.write("\t".join(UNITS) + "\n")
        txt_handle.write(f"# Created UTC\t{_utc_timestamp()}\n")
        txt_handle.write(f"# Sample\t{self.edit_sample_name.text().strip()}\n")
        txt_handle.write(f"# Notes\t{self.edit_run_notes.toPlainText().strip()}\n")
        txt_handle.write(f"# Initial length mm\t{self.spin_initial_length.value():.6f}\n")
        txt_handle.write(f"# Wire diameter mm\t{self.spin_diameter.value():.6f}\n")
        txt_handle.write(f"# Preload zeroing\t{self.check_zero_on_preload.isChecked()}\n")
        txt_handle.write(f"# Preload threshold g\t{self.spin_preload_threshold_g.value():.6f}\n")
        txt_handle.write(f"# Recipe mode\t{self.combo_recipe_mode.currentText()}\n")
        txt_handle.write(f"# Recipe summary\t{self.label_recipe_summary.text()}\n")
        txt_handle.flush()

        writer = csv.DictWriter(
            csv_handle,
            fieldnames=[
                "elapsed_s",
                "timestamp_utc",
                "recipe_mode",
                "automation_phase",
                "automation_basis",
                "automation_target_value",
                "plateau_index",
                "plateau_label",
                "position_mm",
                "raw_load_g",
                "load_g",
                "preload_state",
                "strain_pct",
                "stress_mpa",
                "current_set_mA",
                "current_measured_mA",
                "voltage_V",
                "resistance_ohm",
                "power_W",
            ],
        )
        writer.writeheader()
        csv_handle.flush()
        return txt_handle, csv_handle, writer, txt_path, json_path

    def _session_metadata(self) -> dict[str, Any]:
        return {
            "created_utc": _utc_timestamp(),
            "sample_name": self.edit_sample_name.text().strip(),
            "name_fields": {
                "composition": self.edit_name_composition.text().strip(),
                "microwire": self.edit_name_wire.text().strip(),
                "specimen": self.edit_name_specimen.text().strip(),
                "condition": self.edit_name_condition.text().strip(),
            },
            "notes": self.edit_run_notes.toPlainText().strip(),
            "initial_length_mm": float(self.spin_initial_length.value()),
            "wire_diameter_mm": float(self.spin_diameter.value()),
            "preload_zeroing_enabled": self.check_zero_on_preload.isChecked(),
            "preload_threshold_g": float(self.spin_preload_threshold_g.value()),
            "steps_per_mm": float(self.spin_steps_per_mm.value()),
            "position_reference_mm": float(self._position_reference_mm),
            "preload_reference_armed": self._preload_reference_armed,
            "preload_trigger_elapsed_s": self._preload_trigger_elapsed_s,
            "soft_limits_enabled": self.check_soft_limits.isChecked(),
            "soft_limit_min_mm": float(self.spin_soft_min_mm.value()),
            "soft_limit_max_mm": float(self.spin_soft_max_mm.value()),
            "max_load_limit_enabled": self.check_max_load.isChecked(),
            "max_load_limit_g": float(self.spin_max_load_g.value()),
            "return_to_origin": self.check_return_to_origin.isChecked(),
            "scale": {
                "port": str(self.combo_scale_port.currentData() or ""),
                "baud": int(self.combo_scale_baud.currentText()),
                "request_command": self.edit_scale_request.text(),
                "line_ending": self.edit_scale_terminator.text(),
            },
            "heating": {
                "port": str(self.combo_supply_port.currentData() or ""),
                "baud": int(self.combo_supply_baud.currentText()),
                "profile": str(self.combo_supply_profile.currentData() or "hmp4030"),
                "voltage_limit_v": float(self.spin_supply_voltage_limit.value()),
                "mode": self._heating_mode(),
                "constant_current_mA": float(self.spin_heat_constant_current.value()),
                "start_current_mA": float(self.spin_heat_start_current.value()),
                "max_current_mA": float(self.spin_heat_max_current.value()),
                "step_current_mA": float(self.spin_heat_step_current.value()),
                "limit_action": str(self.combo_heat_limit_action.currentData() or HEATING_LIMIT_STOP),
                "output_off_on_stop": self.check_output_off_on_stop.isChecked(),
            },
            "recipe_mode": str(self.combo_recipe_mode.currentData() or "ramp"),
            "recipe_summary": self.label_recipe_summary.text(),
            "recipe_estimated_points": int(self._recipe_estimated_points),
            "hsw_distribution": {
                "basis": self._distribution_basis(),
                "start": float(self.spin_distribution_start.value()),
                "end": float(self.spin_distribution_end.value()),
                "step": float(self.spin_distribution_step.value()),
                "tolerance": float(self.spin_distribution_tolerance.value()),
                "seek_nudge_mm": float(self.spin_distribution_nudge_mm.value()),
                "settle_s": float(self.spin_distribution_settle_s.value()),
                "points_per_plateau": int(self.spin_distribution_points.value()),
                "interval_ms": int(self.spin_distribution_interval.value()),
                "return_sweep": self.check_distribution_return_sweep.isChecked(),
            },
            "builder_project": None if self._builder_project_path is None else str(self._builder_project_path),
        }

    def _write_session_metadata(self, *, finished_utc: str | None = None) -> None:
        if self._session_json_path is None:
            return
        payload = self._session_metadata()
        payload["point_count"] = len(self._session_points)
        if self._session_active:
            payload["session_state"] = "running"
            payload["elapsed_s"] = time.monotonic() - self._session_start_monotonic
        else:
            payload["session_state"] = "finished"
        if finished_utc:
            payload["finished_utc"] = finished_utc
        self._session_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _set_automation_context(
        self,
        *,
        phase: str,
        basis: str | None = None,
        target_value: float | None = None,
        plateau_index: int | None = None,
    ) -> None:
        self._automation_phase = phase
        self._automation_basis = basis
        self._automation_target_value = target_value
        self._automation_plateau_index = plateau_index
        if basis and target_value is not None:
            label = HSW_BASIS_LABELS.get(basis, basis)
            suffix, _ = self._distribution_units(basis)
            self._automation_plateau_label = f"{label} {target_value:.4f}{suffix}"
        else:
            self._automation_plateau_label = None

    def _start_session(self) -> None:
        if self._session_active:
            return
        try:
            txt_handle, csv_handle, csv_writer, txt_path, json_path = self._prepare_session_files()
        except Exception as exc:
            if str(exc):
                self._log(str(exc))
            return

        if self.check_zero_position_on_start.isChecked():
            self._zero_tic_position()
        if self.check_tare_on_start.isChecked():
            self._load_offset_g = -self._latest_scale_value_g
        self._position_reference_mm = self._current_position_mm
        self._preload_reference_armed = (
            self.check_zero_on_preload.isChecked() and self.spin_preload_threshold_g.value() > 0
        )
        self._preload_trigger_elapsed_s = None
        self._session_points = []
        self._session_active = True
        self._session_start_monotonic = time.monotonic()
        self._session_txt_handle = txt_handle
        self._session_csv_handle = csv_handle
        self._session_csv_writer = csv_writer
        self._session_base_path = txt_path
        self._session_json_path = json_path
        self.button_start_session.setEnabled(False)
        self.button_stop_session.setEnabled(True)
        self.label_session_status.setText(f"Session running -> {txt_path.name}")
        self._log(f"Session started: {txt_path}")
        self._prepare_heating_for_session()
        self._write_session_metadata()
        self._refresh_live_labels()
        self._record_current_point()

    def _stop_session(self) -> None:
        if not self._session_active:
            return
        self._stop_auto_ramp(log_completion=False)
        self._session_active = False
        if self._session_txt_handle is not None:
            self._session_txt_handle.close()
            self._session_txt_handle = None
        if self._session_csv_handle is not None:
            self._session_csv_handle.close()
            self._session_csv_handle = None
        self._session_csv_writer = None
        self.button_start_session.setEnabled(True)
        self.button_stop_session.setEnabled(False)
        point_count = len(self._session_points)
        self.label_session_status.setText(f"Session saved ({point_count} point(s))")
        if self._session_base_path is not None:
            self._log(f"Session stopped. Saved {point_count} point(s) to {self._session_base_path}.")
        if self.check_output_off_on_stop.isChecked():
            self._disable_supply_output()
        if self._session_json_path is not None:
            self._write_session_metadata(finished_utc=_utc_timestamp())
        self._refresh_live_labels()

    def _current_effective_load_g(self) -> float:
        return self._latest_scale_value_g + self._load_offset_g

    def _current_preload_state(self, load_g: float) -> str:
        if not self.check_zero_on_preload.isChecked() or self.spin_preload_threshold_g.value() <= 0:
            return PRELOAD_DISABLED
        if self._preload_reference_armed:
            return PRELOAD_PENDING
        if self._preload_trigger_elapsed_s is not None:
            return PRELOAD_ACTIVE
        if abs(load_g) >= float(self.spin_preload_threshold_g.value()):
            return PRELOAD_ACTIVE
        return PRELOAD_PENDING

    def _capture_measurement_point(self, *, elapsed_s: float, position_mm: float, raw_load_g: float, load_g: float) -> MeasurementPoint:
        preload_state = self._current_preload_state(load_g)
        if preload_state == PRELOAD_PENDING and abs(load_g) >= float(self.spin_preload_threshold_g.value()):
            self._position_reference_mm = position_mm
            self._preload_reference_armed = False
            self._preload_trigger_elapsed_s = elapsed_s
            preload_state = PRELOAD_ACTIVE
            self._log(
                f"Preload reached at {load_g:.5f} g. Gauge zero moved to {position_mm:.4f} mm."
            )
        strain = None
        stress = None
        if preload_state != PRELOAD_PENDING:
            strain = strain_percent(
                displacement_mm=position_mm,
                initial_length_mm=float(self.spin_initial_length.value()),
                reference_mm=self._position_reference_mm,
            )
            stress = stress_mpa_from_load_g(load_g, float(self.spin_diameter.value()))
        snapshot = self._refresh_supply_snapshot()
        return MeasurementPoint(
            elapsed_s=elapsed_s,
            timestamp_utc=_utc_timestamp(),
            position_mm=position_mm,
            raw_load_g=raw_load_g,
            load_g=load_g,
            preload_state=preload_state,
            strain_pct=strain,
            stress_mpa=stress,
            current_set_mA=self._supply_last_setpoint_mA,
            current_measured_mA=snapshot.get("current_mA"),
            voltage_V=snapshot.get("voltage_V"),
            resistance_ohm=snapshot.get("resistance_ohm"),
            power_W=snapshot.get("power_W"),
            automation_phase=self._automation_phase,
            automation_basis=self._automation_basis,
            automation_target_value=self._automation_target_value,
            plateau_index=self._automation_plateau_index,
            plateau_label=self._automation_plateau_label,
        )

    def _record_current_point(self) -> None:
        if not self._session_active:
            QtWidgets.QMessageBox.information(self, APP_NAME, "Start a session before recording points.")
            return
        if self._is_max_load_exceeded():
            self._log(
                f"Safety stop: effective load {self._current_effective_load_g():.5f} g exceeded "
                f"the configured limit of {self.spin_max_load_g.value():.5f} g."
            )
            self._stop_auto_ramp(log_completion=False)
        elapsed_s = time.monotonic() - self._session_start_monotonic
        position_mm = self._current_position_mm
        raw_load_g = self._latest_scale_value_g
        load_g = self._current_effective_load_g()
        point = self._capture_measurement_point(
            elapsed_s=elapsed_s,
            position_mm=position_mm,
            raw_load_g=raw_load_g,
            load_g=load_g,
        )
        self._session_points.append(point)
        self._write_point(point)
        self._write_session_metadata()
        self._refresh_plots()
        self._refresh_live_labels()
        self._log(
            f"Recorded point #{len(self._session_points)} at {position_mm:.4f} mm, "
            f"{load_g:.5f} g."
        )
        self._advance_heating_after_record()

    def _write_point(self, point: MeasurementPoint) -> None:
        if self._session_txt_handle is None or self._session_csv_writer is None:
            return
        txt_values = (
            f"{point.position_mm:.6f}",
            f"{point.load_g:.6f}",
            "" if point.strain_pct is None else f"{point.strain_pct:.6f}",
            "" if point.stress_mpa is None else f"{point.stress_mpa:.6f}",
        )
        self._session_txt_handle.write("\t".join(txt_values) + "\n")
        self._session_txt_handle.flush()

        self._session_csv_writer.writerow(
            {
                "elapsed_s": f"{point.elapsed_s:.6f}",
                "timestamp_utc": point.timestamp_utc,
                "recipe_mode": str(self.combo_recipe_mode.currentData() or "ramp"),
                "automation_phase": point.automation_phase,
                "automation_basis": "" if point.automation_basis is None else point.automation_basis,
                "automation_target_value": ""
                if point.automation_target_value is None
                else f"{point.automation_target_value:.6f}",
                "plateau_index": "" if point.plateau_index is None else point.plateau_index,
                "plateau_label": "" if point.plateau_label is None else point.plateau_label,
                "position_mm": f"{point.position_mm:.6f}",
                "raw_load_g": f"{point.raw_load_g:.6f}",
                "load_g": f"{point.load_g:.6f}",
                "preload_state": point.preload_state,
                "strain_pct": "" if point.strain_pct is None else f"{point.strain_pct:.6f}",
                "stress_mpa": "" if point.stress_mpa is None else f"{point.stress_mpa:.6f}",
                "current_set_mA": "" if point.current_set_mA is None else f"{point.current_set_mA:.6f}",
                "current_measured_mA": "" if point.current_measured_mA is None else f"{point.current_measured_mA:.6f}",
                "voltage_V": "" if point.voltage_V is None else f"{point.voltage_V:.6f}",
                "resistance_ohm": "" if point.resistance_ohm is None else f"{point.resistance_ohm:.6f}",
                "power_W": "" if point.power_W is None else f"{point.power_W:.6f}",
            }
        )
        if self._session_csv_handle is not None:
            self._session_csv_handle.flush()

    def _start_auto_ramp(self) -> None:
        if self._automation_active:
            return
        if not self._session_active:
            self._start_session()
            if not self._session_active:
                return
        try:
            steps, summary, interval_ms = self._build_automation_recipe()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self._automation_steps = steps
        self._automation_index = 0
        self._automation_active = True
        self._automation_interval_ms = interval_ms
        self._recipe_origin_mm = self._current_position_mm
        self._automation_name = str(self.combo_recipe_mode.currentData() or "ramp")
        self._set_automation_context(phase="start")
        self._auto_ramp_timer.start(interval_ms)
        self._log(summary)
        self._refresh_live_labels()

    def _stop_auto_ramp(self, *, log_completion: bool = True) -> None:
        if not self._automation_active:
            return
        self._automation_active = False
        self._automation_steps = []
        self._automation_index = 0
        self._auto_ramp_timer.stop()
        self._set_automation_context(phase="idle")
        if log_completion:
            self._log("Recipe stopped.")
        self._refresh_live_labels()

    def _build_segment_targets(
        self,
        start_offset_mm: float,
        end_offset_mm: float,
        step_mm: float,
    ) -> list[float]:
        if step_mm <= 0.0:
            raise ValueError("Step size must be greater than zero.")
        delta_mm = end_offset_mm - start_offset_mm
        if delta_mm == 0.0:
            return []
        sign = 1.0 if delta_mm >= 0.0 else -1.0
        count = max(1, int(math.ceil(abs(delta_mm) / step_mm)))
        return [
            self._recipe_origin_mm
            + start_offset_mm
            + sign * min(index * step_mm, abs(delta_mm))
            for index in range(1, count + 1)
        ]

    def _build_automation_recipe(self) -> tuple[list[AutomationStep], str, int]:
        mode = str(self.combo_recipe_mode.currentData() or "ramp")
        self._recipe_origin_mm = self._current_position_mm

        if mode == "cycle":
            amplitude = float(self.spin_cycle_amplitude.value())
            step_mm = abs(float(self.spin_cycle_step.value()))
            cycles = int(self.spin_cycle_count.value())
            interval_ms = int(self.spin_cycle_interval.value())
            if amplitude == 0.0:
                raise ValueError("Set a non-zero cycle amplitude.")
            up_targets = self._build_segment_targets(0.0, amplitude, step_mm)
            down_targets = self._build_segment_targets(amplitude, 0.0, step_mm)
            steps: list[AutomationStep] = []
            for _ in range(cycles):
                for target in up_targets:
                    steps.extend((AutomationStep("move", target), AutomationStep("record")))
                for target in down_targets:
                    steps.extend((AutomationStep("move", target), AutomationStep("record")))
            steps = self._append_return_to_origin(steps)
            summary = (
                f"Started cyclic triangle recipe: {cycles} cycle(s), amplitude {amplitude:.4f} mm, "
                f"step {step_mm:.4f} mm, settle {interval_ms} ms."
            )
            return steps, summary, interval_ms

        if mode == "hold":
            target_offset = float(self.spin_hold_target.value())
            duration_s = float(self.spin_hold_duration_s.value())
            interval_ms = int(self.spin_hold_interval.value())
            if duration_s <= 0.0:
                raise ValueError("Hold duration must be greater than zero.")
            sample_count = max(1, int(math.ceil((duration_s * 1000.0) / interval_ms)))
            target_mm = self._recipe_origin_mm + target_offset
            steps = [AutomationStep("move", target_mm), AutomationStep("record")]
            steps.extend(AutomationStep("record") for _ in range(max(0, sample_count - 1)))
            steps = self._append_return_to_origin(steps)
            summary = (
                f"Started position-hold recipe: target offset {target_offset:.4f} mm for "
                f"{duration_s:.1f} s, record every {interval_ms} ms."
            )
            return steps, summary, interval_ms

        if mode == "distribution":
            basis = self._distribution_basis()
            start_value = float(self.spin_distribution_start.value())
            end_value = float(self.spin_distribution_end.value())
            step_value = abs(float(self.spin_distribution_step.value()))
            points_per_plateau = int(self.spin_distribution_points.value())
            interval_ms = int(self.spin_distribution_interval.value())
            settle_s = float(self.spin_distribution_settle_s.value())
            if points_per_plateau <= 0:
                raise ValueError("Set at least one point per Hsw plateau.")
            targets = self._build_distribution_targets(
                start_value,
                end_value,
                step_value,
                include_return=self.check_distribution_return_sweep.isChecked(),
            )
            steps = []
            for plateau_index, target in enumerate(targets, start=1):
                steps.append(
                    AutomationStep(
                        "seek_target",
                        target_value=target,
                        basis=basis,
                        note=str(plateau_index),
                    )
                )
                settle_steps = max(0, int(math.ceil((settle_s * 1000.0) / interval_ms)))
                steps.extend(
                    AutomationStep(
                        "settle",
                        target_value=target,
                        basis=basis,
                        note=str(plateau_index),
                    )
                    for _ in range(settle_steps)
                )
                steps.extend(
                    AutomationStep(
                        "record",
                        target_value=target,
                        basis=basis,
                        note=str(plateau_index),
                    )
                    for _ in range(points_per_plateau)
                )
            steps = self._append_return_to_origin(steps)
            suffix, _ = self._distribution_units(basis)
            summary = (
                f"Started Hsw distribution recipe: {start_value:.4f}{suffix} to {end_value:.4f}{suffix}, "
                f"step {step_value:.4f}{suffix}, {points_per_plateau} point(s) per plateau, "
                f"interval {interval_ms} ms, settle {settle_s:.2f} s."
            )
            return steps, summary, interval_ms

        total_distance_mm = float(self.spin_ramp_distance.value())
        step_mm = abs(float(self.spin_ramp_step.value()))
        interval_ms = int(self.spin_ramp_interval.value())
        if total_distance_mm == 0.0:
            raise ValueError("Set a non-zero ramp distance.")
        targets = self._build_segment_targets(0.0, total_distance_mm, step_mm)
        steps = []
        for target in targets:
            steps.extend((AutomationStep("move", target), AutomationStep("record")))
        steps = self._append_return_to_origin(steps)
        summary = (
            f"Started one-way ramp recipe: distance {total_distance_mm:.4f} mm, "
            f"step {step_mm:.4f} mm, settle {interval_ms} ms."
        )
        return steps, summary, interval_ms

    def _handle_auto_ramp_tick(self) -> None:
        if not self._automation_active:
            return
        if self._automation_index >= len(self._automation_steps):
            self._stop_auto_ramp(log_completion=False)
            self._log("Recipe completed.")
            return
        step = self._automation_steps[self._automation_index]
        self._automation_index += 1
        if step.action == "move":
            self._set_automation_context(phase="move")
            if step.target_mm is None or not self._move_to_position_mm(step.target_mm):
                self._stop_auto_ramp(log_completion=False)
        elif step.action == "seek_target":
            if step.target_value is None or not step.basis:
                self._stop_auto_ramp(log_completion=False)
                return
            plateau_index = int(step.note) if step.note.isdigit() else None
            self._set_automation_context(
                phase="seek",
                basis=step.basis,
                target_value=step.target_value,
                plateau_index=plateau_index,
            )
            tolerance = abs(float(self.spin_distribution_tolerance.value()))
            if tolerance <= 0.0:
                self._stop_auto_ramp(log_completion=False)
                self._log("Hsw distribution stopped because the target tolerance is zero.")
                return
            try:
                reached = self._seek_distribution_target(step.basis, step.target_value, tolerance)
            except Exception as exc:
                self._stop_auto_ramp(log_completion=False)
                self._log(f"Hsw distribution stopped: {exc}")
                return
            if reached:
                current_value = self._current_distribution_value(step.basis)
                if current_value is None:
                    current_value = 0.0
                label = HSW_BASIS_LABELS.get(step.basis, step.basis)
                self._log(
                    f"Reached {label} plateau {step.target_value:.4f} "
                    f"(live {current_value:.4f})."
                )
            else:
                self._automation_index -= 1
        elif step.action == "settle":
            plateau_index = int(step.note) if step.note.isdigit() else None
            self._set_automation_context(
                phase="settle",
                basis=step.basis,
                target_value=step.target_value,
                plateau_index=plateau_index,
            )
        elif step.action == "record":
            plateau_index = int(step.note) if step.note.isdigit() else None
            self._set_automation_context(
                phase="record",
                basis=step.basis,
                target_value=step.target_value,
                plateau_index=plateau_index,
            )
            self._record_current_point()
        self._refresh_live_labels()

    def _handle_status_timer(self) -> None:
        if self._automation_active or self._session_active:
            self._refresh_tic_status()
        if self._supply_controller is not None and self._supply_controller.is_connected():
            self._refresh_supply_snapshot()
        if self._automation_active and self._is_max_load_exceeded():
            self._log(
                f"Automation stopped because effective load {self._current_effective_load_g():.5f} g exceeded "
                f"the limit of {self.spin_max_load_g.value():.5f} g."
            )
            self._stop_auto_ramp(log_completion=False)

    def _refresh_live_labels(self) -> None:
        effective_load = self._current_effective_load_g()
        self.label_scale_value.setText(
            f"Latest load: {self._latest_scale_value_g:.5f} g (effective {effective_load:.5f} g)"
        )
        self.label_scale_raw.setText(f"Raw line: {self._latest_scale_text or '-'}")
        self.label_tic_position.setText(
            f"Position: {self._current_position_mm:.4f} mm ({self._current_position_steps} steps)"
        )
        self.label_reference_status.setText(
            f"Reference position: {self._position_reference_mm:.4f} mm | "
            f"Last target: {self._last_move_target_mm:.4f} mm"
            f"{' | waiting for preload' if self._preload_reference_armed else ''}"
        )

        preload_state = self._current_preload_state(effective_load)
        if preload_state == PRELOAD_PENDING:
            strain = None
            stress = None
        else:
            strain = strain_percent(
                self._current_position_mm,
                float(self.spin_initial_length.value()),
                self._position_reference_mm,
            )
            stress = stress_mpa_from_load_g(effective_load, float(self.spin_diameter.value()))
        self.label_live_summary.setText(
            f"Live strain: {'-' if strain is None else f'{strain:.4f} %'} | "
            f"Live stress: {'-' if stress is None else f'{stress:.4f} MPa'}"
            f" | Heating: {'off' if not self._supply_output_enabled else f'{self._supply_last_setpoint_mA or 0.0:.2f} mA'}"
        )
        session_value = "Running" if self._session_active else "Idle"
        self.label_card_session.setText(
            f"{session_value} | {len(self._session_points)} point(s)"
        )
        if self._latest_scale_timestamp is None:
            scale_value = "No readings yet"
        else:
            age_s = max(0.0, time.time() - self._latest_scale_timestamp)
            freshness = "stale" if age_s > STALE_SCALE_AFTER_S else "live"
            scale_value = f"{effective_load:.4f} g | {freshness} {age_s:.1f} s"
        self.label_card_scale.setText(scale_value)
        motion_state = f"{self._current_position_mm:.4f} mm"
        if self.check_soft_limits.isChecked():
            motion_state += (
                f" | limits {min(self.spin_soft_min_mm.value(), self.spin_soft_max_mm.value()):.2f}"
                f" to {max(self.spin_soft_min_mm.value(), self.spin_soft_max_mm.value()):.2f}"
            )
        if preload_state == PRELOAD_PENDING:
            motion_state += f" | preload < {self.spin_preload_threshold_g.value():.4f} g"
        self.label_card_motion.setText(motion_state)
        if self._automation_active:
            recipe_state = (
                f"{self._automation_name} | done {self._automation_index}"
                f"/{max(1, len(self._automation_steps))}"
            )
            if self._automation_plateau_label:
                recipe_state += f" | {self._automation_phase} {self._automation_plateau_label}"
            elif self._automation_phase not in {"idle", "start"}:
                recipe_state += f" | {self._automation_phase}"
        else:
            recipe_state = str(self.combo_recipe_mode.currentText())
        self.label_card_recipe.setText(recipe_state)
        self._refresh_supply_live_label()

    def _refresh_plots(self) -> None:
        theme = self._plot_theme()
        self.figure.clear()
        self.figure.set_facecolor(theme["figure_rgb"])
        grid = self.figure.add_gridspec(2, 2, hspace=0.46, wspace=0.34)
        active_tiles = [tile for tile in self._plot_tiles if tile.visible.isChecked()]
        if not active_tiles:
            active_tiles = list(self._plot_tiles[:1])
        for tile_index, tile in enumerate(active_tiles[:4]):
            row, column = divmod(tile_index, 2)
            axis = self.figure.add_subplot(grid[row, column])
            axis.set_facecolor(theme["axes_rgb"])
            for spine in axis.spines.values():
                spine.set_color(theme["text_rgb"])
            axis.tick_params(colors=theme["text_rgb"])
            axis.xaxis.label.set_color(theme["text_rgb"])
            axis.yaxis.label.set_color(theme["text_rgb"])
            axis.title.set_color(theme["text_rgb"])
            axis.grid(True, color=theme["grid_rgba"], alpha=0.6)

            x_channel = self._plot_channel(str(tile.x_combo.currentData() or "elapsed_s"))
            y_left_channel = self._plot_channel(str(tile.y_left_combo.currentData() or "load_g"))
            y_right_channel = self._plot_channel(str(tile.y_right_combo.currentData() or ""))
            if x_channel is None or y_left_channel is None:
                continue

            axis.set_xlabel(x_channel.label, fontsize=9, labelpad=4)
            axis.set_ylabel(y_left_channel.label, fontsize=8, labelpad=3)
            axis.set_title(self._plot_title(x_channel, y_left_channel, y_right_channel), fontsize=9, pad=8)

            left_pairs = [
                (x_channel.getter(point), y_left_channel.getter(point))
                for point in self._session_points
            ]
            left_pairs = [(x_value, y_value) for x_value, y_value in left_pairs if x_value is not None and y_value is not None]
            if left_pairs:
                axis.plot(
                    [x_value for x_value, _ in left_pairs],
                    [y_value for _, y_value in left_pairs],
                    color=y_left_channel.color,
                    linewidth=1.7,
                    marker="o",
                    markersize=3.2,
                )
            else:
                axis.text(
                    0.5,
                    0.5,
                    "No data for selected channels",
                    ha="center",
                    va="center",
                    color=theme["text_rgb"],
                    transform=axis.transAxes,
                )

            if y_right_channel is not None:
                twin = axis.twinx()
                twin.set_facecolor((0, 0, 0, 0))
                for spine in twin.spines.values():
                    spine.set_color(theme["text_rgb"])
                twin.tick_params(colors=theme["text_rgb"])
                twin.yaxis.label.set_color(theme["text_rgb"])
                twin.set_ylabel(y_right_channel.label, fontsize=8, labelpad=3)
                right_pairs = [
                    (x_channel.getter(point), y_right_channel.getter(point))
                    for point in self._session_points
                ]
                right_pairs = [
                    (x_value, y_value)
                    for x_value, y_value in right_pairs
                    if x_value is not None and y_value is not None
                ]
                if right_pairs:
                    twin.plot(
                        [x_value for x_value, _ in right_pairs],
                        [y_value for _, y_value in right_pairs],
                        color=y_right_channel.color,
                        linewidth=1.5,
                        marker="s",
                        markersize=3.0,
                    )
        self.figure.subplots_adjust(left=0.07, right=0.94, top=0.90, bottom=0.12, hspace=0.50, wspace=0.34)

        if self.canvas is not None:
            self.canvas.draw_idle()

    def _choose_log_dir(self) -> None:
        start_dir = self.edit_log_dir.text().strip() or _default_download_dir()
        new_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select output folder",
            start_dir,
        )
        if new_dir:
            self.edit_log_dir.setText(new_dir)

    def _save_settings(self) -> None:
        self.settings.setValue("scale_port", self.combo_scale_port.currentData() or "")
        self.settings.setValue("scale_baud", self.combo_scale_baud.currentText())
        self.settings.setValue("scale_interval_ms", self.spin_scale_interval.value())
        self.settings.setValue("scale_request", self.edit_scale_request.text())
        self.settings.setValue("scale_terminator", self.edit_scale_terminator.text())
        self.settings.setValue("overview_expanded", self.overview_section.is_expanded())
        self.settings.setValue("supply_port", self.combo_supply_port.currentData() or "")
        self.settings.setValue("supply_baud", self.combo_supply_baud.currentText())
        self.settings.setValue("supply_profile", self.combo_supply_profile.currentData() or "hmp4030")
        self.settings.setValue("supply_voltage_limit_v", self.spin_supply_voltage_limit.value())
        self.settings.setValue("supply_manual_current_mA", self.spin_supply_manual_current.value())
        self.settings.setValue("ticcmd_path", self.edit_ticcmd_path.text())
        self.settings.setValue("tic_serial", self.edit_tic_serial.text())
        self.settings.setValue("steps_per_mm", self.spin_steps_per_mm.value())
        self.settings.setValue("jog_mm", self.spin_jog_mm.value())
        self.settings.setValue("soft_limits_enabled", self.check_soft_limits.isChecked())
        self.settings.setValue("soft_limit_min_mm", self.spin_soft_min_mm.value())
        self.settings.setValue("soft_limit_max_mm", self.spin_soft_max_mm.value())
        self.settings.setValue("max_load_enabled", self.check_max_load.isChecked())
        self.settings.setValue("max_load_g", self.spin_max_load_g.value())
        self.settings.setValue("initial_length_mm", self.spin_initial_length.value())
        self.settings.setValue("diameter_mm", self.spin_diameter.value())
        self.settings.setValue("preload_zeroing_enabled", self.check_zero_on_preload.isChecked())
        self.settings.setValue("preload_threshold_g", self.spin_preload_threshold_g.value())
        self.settings.setValue("name_composition", self.edit_name_composition.text())
        self.settings.setValue("name_wire", self.edit_name_wire.text())
        self.settings.setValue("name_specimen", self.edit_name_specimen.text())
        self.settings.setValue("name_condition", self.edit_name_condition.text())
        self.settings.setValue("auto_name", self.check_auto_name.isChecked())
        self.settings.setValue("sample_name", self.edit_sample_name.text())
        self.settings.setValue("run_notes", self.edit_run_notes.toPlainText())
        self.settings.setValue("builder_project_path", self.edit_project_path.text())
        self.settings.setValue("log_dir", self.edit_log_dir.text())
        self.settings.setValue("log_name", self.edit_log_name.text())
        self.settings.setValue(
            "zero_position_on_start",
            self.check_zero_position_on_start.isChecked(),
        )
        self.settings.setValue("tare_on_start", self.check_tare_on_start.isChecked())
        self.settings.setValue("recipe_mode", self.combo_recipe_mode.currentData())
        self.settings.setValue("return_to_origin", self.check_return_to_origin.isChecked())
        self.settings.setValue("ramp_distance_mm", self.spin_ramp_distance.value())
        self.settings.setValue("ramp_step_mm", self.spin_ramp_step.value())
        self.settings.setValue("ramp_interval_ms", self.spin_ramp_interval.value())
        self.settings.setValue("cycle_amplitude_mm", self.spin_cycle_amplitude.value())
        self.settings.setValue("cycle_step_mm", self.spin_cycle_step.value())
        self.settings.setValue("cycle_count", self.spin_cycle_count.value())
        self.settings.setValue("cycle_interval_ms", self.spin_cycle_interval.value())
        self.settings.setValue("hold_target_mm", self.spin_hold_target.value())
        self.settings.setValue("hold_duration_s", self.spin_hold_duration_s.value())
        self.settings.setValue("hold_interval_ms", self.spin_hold_interval.value())
        self.settings.setValue("distribution_basis", self.combo_distribution_basis.currentData() or HSW_BASIS_STRESS_MPA)
        self.settings.setValue("distribution_start", self.spin_distribution_start.value())
        self.settings.setValue("distribution_end", self.spin_distribution_end.value())
        self.settings.setValue("distribution_step", self.spin_distribution_step.value())
        self.settings.setValue("distribution_tolerance", self.spin_distribution_tolerance.value())
        self.settings.setValue("distribution_nudge_mm", self.spin_distribution_nudge_mm.value())
        self.settings.setValue("distribution_settle_s", self.spin_distribution_settle_s.value())
        self.settings.setValue("distribution_points", self.spin_distribution_points.value())
        self.settings.setValue("distribution_interval_ms", self.spin_distribution_interval.value())
        self.settings.setValue("distribution_return_sweep", self.check_distribution_return_sweep.isChecked())
        self.settings.setValue("heating_mode", self.combo_heating_mode.currentData() or HEATING_MODE_OFF)
        self.settings.setValue("heat_constant_current_mA", self.spin_heat_constant_current.value())
        self.settings.setValue("heat_start_current_mA", self.spin_heat_start_current.value())
        self.settings.setValue("heat_max_current_mA", self.spin_heat_max_current.value())
        self.settings.setValue("heat_step_current_mA", self.spin_heat_step_current.value())
        self.settings.setValue("heat_limit_action", self.combo_heat_limit_action.currentData() or HEATING_LIMIT_STOP)
        self.settings.setValue("output_off_on_stop", self.check_output_off_on_stop.isChecked())
        for index, tile in enumerate(self._plot_tiles):
            prefix = f"plot_tile_{index}"
            self.settings.setValue(f"{prefix}_visible", tile.visible.isChecked())
            self.settings.setValue(f"{prefix}_x", tile.x_combo.currentData() or "elapsed_s")
            self.settings.setValue(f"{prefix}_y_left", tile.y_left_combo.currentData() or "load_g")
            self.settings.setValue(f"{prefix}_y_right", tile.y_right_combo.currentData() or "")
        self.settings.sync()

    def _restore_settings(self) -> None:
        baud = self.settings.value("scale_baud", "600", type=str)
        if self.combo_scale_baud.findText(baud) >= 0:
            self.combo_scale_baud.setCurrentText(baud)
        self.spin_scale_interval.setValue(int(self.settings.value("scale_interval_ms", 250)))
        scale_request = self.settings.value("scale_request", "\\x1bp", type=str)
        scale_terminator = self.settings.value("scale_terminator", "", type=str)
        self.overview_section.set_expanded(
            bool(self.settings.value("overview_expanded", False, type=bool))
        )
        if baud == "9600" and (not scale_request) and scale_terminator == "\\r\\n":
            baud = "600"
            self.combo_scale_baud.setCurrentText(baud)
            scale_request = "\\x1bp"
            scale_terminator = ""
        self.edit_scale_request.setText(scale_request)
        self.edit_scale_terminator.setText(scale_terminator)
        supply_baud = self.settings.value("supply_baud", "9600", type=str)
        if self.combo_supply_baud.findText(supply_baud) >= 0:
            self.combo_supply_baud.setCurrentText(supply_baud)
        supply_profile = self.settings.value("supply_profile", "hmp4030", type=str)
        supply_profile_index = self.combo_supply_profile.findData(supply_profile)
        if supply_profile_index >= 0:
            self.combo_supply_profile.setCurrentIndex(supply_profile_index)
        self.spin_supply_voltage_limit.setValue(float(self.settings.value("supply_voltage_limit_v", 30.0)))
        self.spin_supply_manual_current.setValue(float(self.settings.value("supply_manual_current_mA", 1.0)))
        saved_ticcmd = self.settings.value("ticcmd_path", "ticcmd", type=str)
        discovered_ticcmd = _find_ticcmd()
        if saved_ticcmd.strip().lower() == "ticcmd" and discovered_ticcmd != "ticcmd":
            saved_ticcmd = discovered_ticcmd
        self.edit_ticcmd_path.setText(saved_ticcmd)
        self.edit_tic_serial.setText(self.settings.value("tic_serial", "", type=str))
        self.spin_steps_per_mm.setValue(float(self.settings.value("steps_per_mm", 100.0)))
        self.spin_jog_mm.setValue(float(self.settings.value("jog_mm", 0.1)))
        self.check_soft_limits.setChecked(bool(self.settings.value("soft_limits_enabled", False, type=bool)))
        self.spin_soft_min_mm.setValue(float(self.settings.value("soft_limit_min_mm", -5.0)))
        self.spin_soft_max_mm.setValue(float(self.settings.value("soft_limit_max_mm", 5.0)))
        self.check_max_load.setChecked(bool(self.settings.value("max_load_enabled", False, type=bool)))
        self.spin_max_load_g.setValue(float(self.settings.value("max_load_g", 25.0)))
        self.spin_initial_length.setValue(float(self.settings.value("initial_length_mm", 30.0)))
        self.spin_diameter.setValue(float(self.settings.value("diameter_mm", 0.03)))
        self.check_zero_on_preload.setChecked(bool(self.settings.value("preload_zeroing_enabled", True, type=bool)))
        self.spin_preload_threshold_g.setValue(float(self.settings.value("preload_threshold_g", 0.02)))
        self.edit_name_composition.setText(self.settings.value("name_composition", "", type=str))
        self.edit_name_wire.setText(self.settings.value("name_wire", "", type=str))
        self.edit_name_specimen.setText(self.settings.value("name_specimen", "", type=str))
        self.edit_name_condition.setText(self.settings.value("name_condition", "", type=str))
        self.check_auto_name.setChecked(bool(self.settings.value("auto_name", True, type=bool)))
        self.edit_sample_name.setText(self.settings.value("sample_name", "", type=str))
        self.edit_run_notes.setPlainText(self.settings.value("run_notes", "", type=str))
        self.edit_project_path.setText(self.settings.value("builder_project_path", "", type=str))
        restored_log_dir = self.settings.value("log_dir", self.edit_log_dir.text(), type=str)
        if self._provided_log_dir:
            self.edit_log_dir.setText(self._provided_log_dir)
        else:
            self.edit_log_dir.setText(restored_log_dir)
        self.edit_log_name.setText(self.settings.value("log_name", DEFAULT_LOG_BASENAME, type=str))
        self.check_zero_position_on_start.setChecked(
            bool(self.settings.value("zero_position_on_start", True, type=bool))
        )
        self.check_tare_on_start.setChecked(
            bool(self.settings.value("tare_on_start", True, type=bool))
        )
        recipe_mode = self.settings.value("recipe_mode", "ramp", type=str)
        recipe_index = self.combo_recipe_mode.findData(recipe_mode)
        if recipe_index >= 0:
            self.combo_recipe_mode.setCurrentIndex(recipe_index)
        self.check_return_to_origin.setChecked(
            bool(self.settings.value("return_to_origin", True, type=bool))
        )
        self.spin_ramp_distance.setValue(float(self.settings.value("ramp_distance_mm", 1.0)))
        self.spin_ramp_step.setValue(float(self.settings.value("ramp_step_mm", 0.1)))
        self.spin_ramp_interval.setValue(int(self.settings.value("ramp_interval_ms", 1000)))
        self.spin_cycle_amplitude.setValue(float(self.settings.value("cycle_amplitude_mm", 1.0)))
        self.spin_cycle_step.setValue(float(self.settings.value("cycle_step_mm", 0.1)))
        self.spin_cycle_count.setValue(int(self.settings.value("cycle_count", 3)))
        self.spin_cycle_interval.setValue(int(self.settings.value("cycle_interval_ms", 1000)))
        self.spin_hold_target.setValue(float(self.settings.value("hold_target_mm", 0.5)))
        self.spin_hold_duration_s.setValue(float(self.settings.value("hold_duration_s", 10.0)))
        self.spin_hold_interval.setValue(int(self.settings.value("hold_interval_ms", 1000)))
        distribution_basis = self.settings.value("distribution_basis", HSW_BASIS_STRESS_MPA, type=str)
        distribution_basis_index = self.combo_distribution_basis.findData(distribution_basis)
        if distribution_basis_index >= 0:
            self.combo_distribution_basis.setCurrentIndex(distribution_basis_index)
        self.spin_distribution_start.setValue(float(self.settings.value("distribution_start", 10.0)))
        self.spin_distribution_end.setValue(float(self.settings.value("distribution_end", 100.0)))
        self.spin_distribution_step.setValue(float(self.settings.value("distribution_step", 10.0)))
        self.spin_distribution_tolerance.setValue(float(self.settings.value("distribution_tolerance", 0.5)))
        self.spin_distribution_nudge_mm.setValue(float(self.settings.value("distribution_nudge_mm", 0.01)))
        self.spin_distribution_settle_s.setValue(float(self.settings.value("distribution_settle_s", 1.0)))
        self.spin_distribution_points.setValue(int(self.settings.value("distribution_points", 10000)))
        self.spin_distribution_interval.setValue(int(self.settings.value("distribution_interval_ms", 100)))
        self.check_distribution_return_sweep.setChecked(
            bool(self.settings.value("distribution_return_sweep", True, type=bool))
        )
        heating_mode = self.settings.value("heating_mode", HEATING_MODE_OFF, type=str)
        heating_index = self.combo_heating_mode.findData(heating_mode)
        if heating_index >= 0:
            self.combo_heating_mode.setCurrentIndex(heating_index)
        self.spin_heat_constant_current.setValue(float(self.settings.value("heat_constant_current_mA", 50.0)))
        self.spin_heat_start_current.setValue(float(self.settings.value("heat_start_current_mA", 10.0)))
        self.spin_heat_max_current.setValue(float(self.settings.value("heat_max_current_mA", 100.0)))
        self.spin_heat_step_current.setValue(float(self.settings.value("heat_step_current_mA", 5.0)))
        heat_limit_action = self.settings.value("heat_limit_action", HEATING_LIMIT_STOP, type=str)
        heat_limit_index = self.combo_heat_limit_action.findData(heat_limit_action)
        if heat_limit_index >= 0:
            self.combo_heat_limit_action.setCurrentIndex(heat_limit_index)
        self.check_output_off_on_stop.setChecked(bool(self.settings.value("output_off_on_stop", True, type=bool)))
        for index, tile in enumerate(self._plot_tiles):
            prefix = f"plot_tile_{index}"
            tile.visible.setChecked(bool(self.settings.value(f"{prefix}_visible", True, type=bool)))
            x_index = tile.x_combo.findData(self.settings.value(f"{prefix}_x", "elapsed_s", type=str))
            if x_index >= 0:
                tile.x_combo.setCurrentIndex(x_index)
            y_left_index = tile.y_left_combo.findData(self.settings.value(f"{prefix}_y_left", "load_g", type=str))
            if y_left_index >= 0:
                tile.y_left_combo.setCurrentIndex(y_left_index)
            y_right_index = tile.y_right_combo.findData(self.settings.value(f"{prefix}_y_right", "", type=str))
            if y_right_index >= 0:
                tile.y_right_combo.setCurrentIndex(y_right_index)
        self._sync_auto_name_fields()
        self._update_recipe_mode_ui()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self._save_settings()
        self._stop_auto_ramp(log_completion=False)
        self._disconnect_scale()
        self._stop_session()
        self._disconnect_supply()
        super().closeEvent(event)


def main(log_dir: str | None = None) -> QtWidgets.QWidget:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if not isinstance(app, QtWidgets.QApplication):
        app = QtWidgets.QApplication(sys.argv)
        owns_app = True

    ensure_app_theme(app)
    window = MainWindow(log_dir)
    window.showMaximized()
    WINDOWS.append(window)

    if owns_app:
        sys.exit(app.exec())
    return window


if __name__ == "__main__":
    main()
