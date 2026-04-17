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
from typing import Any

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
    strain_pct: float | None
    stress_mpa: float | None


@dataclass
class AutomationStep:
    action: str
    target_mm: float | None = None
    note: str = ""


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
        self._automation_active = False
        self._automation_steps: list[AutomationStep] = []
        self._automation_index = 0
        self._automation_interval_ms = 1000
        self._automation_name = ""
        self._recipe_origin_mm = 0.0
        self._recipe_estimated_points = 0
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

        overview_box = self._group_box("Overview")
        overview_layout = QtWidgets.QVBoxLayout(overview_box)
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
        controls.addWidget(overview_box)

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
        sample_form.addRow("Initial length", self.spin_initial_length)

        self.spin_diameter = QtWidgets.QDoubleSpinBox(sample_box)
        self.spin_diameter.setDecimals(5)
        self.spin_diameter.setRange(0.0, 10.0)
        self.spin_diameter.setValue(0.03)
        self.spin_diameter.setSuffix(" mm")
        sample_form.addRow("Wire diameter", self.spin_diameter)

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
        hero_layout.addStretch(1)
        self.label_recipe_banner = QtWidgets.QLabel("Manual mode", hero_box)
        self.label_recipe_banner.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        hero_layout.addWidget(self.label_recipe_banner)
        plot_layout.addWidget(hero_box, stretch=0)

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
        grid = self.figure.add_gridspec(2, 2, height_ratios=[1.0, 1.15], hspace=0.30, wspace=0.16)
        self.ax_load = self.figure.add_subplot(grid[0, 0])
        self.ax_stress = self.figure.add_subplot(grid[0, 1])
        self.ax_time = self.figure.add_subplot(grid[1, :])

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
        ):
            widget.valueChanged.connect(self._update_recipe_mode_ui)
        for widget in (
            self.spin_ramp_step,
            self.spin_ramp_interval,
            self.spin_cycle_step,
            self.spin_cycle_interval,
            self.spin_hold_interval,
        ):
            widget.valueChanged.connect(self._update_recipe_mode_ui)
        self.check_return_to_origin.toggled.connect(self._update_recipe_mode_ui)

        self.statusBar().showMessage("Ready")
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

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.log_output.appendPlainText(line)
        self.statusBar().showMessage(message, 5000)

    def _refresh_scale_ports(self) -> None:
        current = self.combo_scale_port.currentData()
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
            if port.device.upper() == "COM4":
                preferred_index = self.combo_scale_port.count() - 1
        if current and seen:
            index = self.combo_scale_port.findData(current)
            if index >= 0:
                self.combo_scale_port.setCurrentIndex(index)
        elif preferred_index >= 0:
            self.combo_scale_port.setCurrentIndex(preferred_index)

    def _build_tic_controller(self) -> TicController:
        return TicController(
            command_path=self.edit_ticcmd_path.text(),
            device_serial=self.edit_tic_serial.text(),
        )

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

    def _apply_name_fields(self) -> None:
        built = self._build_sample_name()
        if built:
            self.edit_sample_name.setText(built)
            safe_name = re.sub(r'[<>:"/\\\\|?*]+', "_", built).strip(" .")
            self.edit_log_name.setText(safe_name or DEFAULT_LOG_BASENAME)
            self._log(f"Applied naming fields: {built}")

    def _sync_auto_name_fields(self) -> None:
        if self.check_auto_name.isChecked():
            built = self._build_sample_name()
            if built:
                self.edit_sample_name.setText(built)
                safe_name = re.sub(r'[<>:"/\\\\|?*]+', "_", built).strip(" .")
                self.edit_log_name.setText(safe_name or DEFAULT_LOG_BASENAME)

    def _set_position_reference_now(self) -> None:
        self._position_reference_mm = self._current_position_mm
        self._refresh_live_labels()
        self._log(f"Reference position set to the current stage position ({self._position_reference_mm:.4f} mm).")

    def _update_recipe_mode_ui(self) -> None:
        mode = str(self.combo_recipe_mode.currentData() or "ramp")
        page_index = {"ramp": 0, "cycle": 1, "hold": 2}.get(mode, 0)
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
        else:
            summary = (
                f"Recipe ready: one-way ramp of {self.spin_ramp_distance.value():.4f} mm "
                f"from the current position."
            )
            banner = "Ramp recipe"
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
        txt_handle.write(f"# Recipe mode\t{self.combo_recipe_mode.currentText()}\n")
        txt_handle.write(f"# Recipe summary\t{self.label_recipe_summary.text()}\n")
        txt_handle.flush()

        writer = csv.DictWriter(
            csv_handle,
            fieldnames=[
                "elapsed_s",
                "timestamp_utc",
                "position_mm",
                "raw_load_g",
                "load_g",
                "strain_pct",
                "stress_mpa",
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
            "steps_per_mm": float(self.spin_steps_per_mm.value()),
            "position_reference_mm": float(self._position_reference_mm),
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
            "recipe_mode": str(self.combo_recipe_mode.currentData() or "ramp"),
            "recipe_summary": self.label_recipe_summary.text(),
            "recipe_estimated_points": int(self._recipe_estimated_points),
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
        if self._session_json_path is not None:
            self._write_session_metadata(finished_utc=_utc_timestamp())
        self._refresh_live_labels()

    def _current_effective_load_g(self) -> float:
        return self._latest_scale_value_g + self._load_offset_g

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
        strain = strain_percent(
            displacement_mm=position_mm,
            initial_length_mm=float(self.spin_initial_length.value()),
            reference_mm=self._position_reference_mm,
        )
        stress = stress_mpa_from_load_g(load_g, float(self.spin_diameter.value()))
        point = MeasurementPoint(
            elapsed_s=elapsed_s,
            timestamp_utc=_utc_timestamp(),
            position_mm=position_mm,
            raw_load_g=raw_load_g,
            load_g=load_g,
            strain_pct=strain,
            stress_mpa=stress,
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
                "position_mm": f"{point.position_mm:.6f}",
                "raw_load_g": f"{point.raw_load_g:.6f}",
                "load_g": f"{point.load_g:.6f}",
                "strain_pct": "" if point.strain_pct is None else f"{point.strain_pct:.6f}",
                "stress_mpa": "" if point.stress_mpa is None else f"{point.stress_mpa:.6f}",
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
            if step.target_mm is None or not self._move_to_position_mm(step.target_mm):
                self._stop_auto_ramp(log_completion=False)
        elif step.action == "record":
            self._record_current_point()
        self._refresh_live_labels()

    def _handle_status_timer(self) -> None:
        if self._automation_active or self._session_active:
            self._refresh_tic_status()
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
        )

        strain = strain_percent(
            self._current_position_mm,
            float(self.spin_initial_length.value()),
            self._position_reference_mm,
        )
        stress = stress_mpa_from_load_g(effective_load, float(self.spin_diameter.value()))
        self.label_live_summary.setText(
            f"Live strain: {'-' if strain is None else f'{strain:.4f} %'} | "
            f"Live stress: {'-' if stress is None else f'{stress:.4f} MPa'}"
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
        self.label_card_motion.setText(motion_state)
        if self._automation_active:
            recipe_state = (
                f"{self._automation_name} | done {self._automation_index}"
                f"/{max(1, len(self._automation_steps))}"
            )
        else:
            recipe_state = str(self.combo_recipe_mode.currentText())
        self.label_card_recipe.setText(recipe_state)

    def _refresh_plots(self) -> None:
        for axis in (self.ax_load, self.ax_stress, self.ax_time):
            axis.clear()

        self.ax_load.set_title("Load vs Displacement")
        self.ax_load.set_xlabel("Displacement (mm)")
        self.ax_load.set_ylabel("Load (g)")
        self.ax_load.grid(True, alpha=0.3)

        self.ax_stress.set_title("Stress vs Strain")
        self.ax_stress.set_xlabel("Strain (%)")
        self.ax_stress.set_ylabel("Stress (MPa)")
        self.ax_stress.grid(True, alpha=0.3)

        self.ax_time.set_title("Load vs Time")
        self.ax_time.set_xlabel("Elapsed time (s)")
        self.ax_time.set_ylabel("Load (g)")
        self.ax_time.grid(True, alpha=0.3)

        if self._session_points:
            positions = [point.position_mm for point in self._session_points]
            loads = [point.load_g for point in self._session_points]
            elapsed = [point.elapsed_s for point in self._session_points]
            strains = [point.strain_pct for point in self._session_points]
            stresses = [point.stress_mpa for point in self._session_points]

            self.ax_load.plot(positions, loads, marker="o", linewidth=1.5, color="#1f77b4")
            self.ax_time.plot(elapsed, loads, marker="o", linewidth=1.5, color="#d62728")

            stress_pairs = [
                (strain, stress)
                for strain, stress in zip(strains, stresses)
                if strain is not None and stress is not None
            ]
            if stress_pairs:
                self.ax_stress.plot(
                    [pair[0] for pair in stress_pairs],
                    [pair[1] for pair in stress_pairs],
                    marker="o",
                    linewidth=1.5,
                    color="#2ca02c",
                )

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
        self.settings.setValue("name_composition", self.edit_name_composition.text())
        self.settings.setValue("name_wire", self.edit_name_wire.text())
        self.settings.setValue("name_specimen", self.edit_name_specimen.text())
        self.settings.setValue("name_condition", self.edit_name_condition.text())
        self.settings.setValue("auto_name", self.check_auto_name.isChecked())
        self.settings.setValue("sample_name", self.edit_sample_name.text())
        self.settings.setValue("run_notes", self.edit_run_notes.toPlainText())
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
        self.settings.sync()

    def _restore_settings(self) -> None:
        baud = self.settings.value("scale_baud", "600", type=str)
        if self.combo_scale_baud.findText(baud) >= 0:
            self.combo_scale_baud.setCurrentText(baud)
        self.spin_scale_interval.setValue(int(self.settings.value("scale_interval_ms", 250)))
        scale_request = self.settings.value("scale_request", "\\x1bp", type=str)
        scale_terminator = self.settings.value("scale_terminator", "", type=str)
        if baud == "9600" and (not scale_request) and scale_terminator == "\\r\\n":
            baud = "600"
            self.combo_scale_baud.setCurrentText(baud)
            scale_request = "\\x1bp"
            scale_terminator = ""
        self.edit_scale_request.setText(scale_request)
        self.edit_scale_terminator.setText(scale_terminator)
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
        self.edit_name_composition.setText(self.settings.value("name_composition", "", type=str))
        self.edit_name_wire.setText(self.settings.value("name_wire", "", type=str))
        self.edit_name_specimen.setText(self.settings.value("name_specimen", "", type=str))
        self.edit_name_condition.setText(self.settings.value("name_condition", "", type=str))
        self.check_auto_name.setChecked(bool(self.settings.value("auto_name", True, type=bool)))
        self.edit_sample_name.setText(self.settings.value("sample_name", "", type=str))
        self.edit_run_notes.setPlainText(self.settings.value("run_notes", "", type=str))
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
        self._sync_auto_name_fields()
        self._update_recipe_mode_ui()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self._save_settings()
        self._stop_auto_ramp(log_completion=False)
        self._disconnect_scale()
        self._stop_session()
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
