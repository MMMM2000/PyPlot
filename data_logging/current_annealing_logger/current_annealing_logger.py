# -*- coding: utf-8 -*-
"""Current Annealing Logger for HMP4030.

Modern PyQt6 application that logs voltage/current from an HMP4030 power
source during current annealing. Includes automatic and manual modes,
live plotting, file naming presets, port discovery, and robust handling
of contact loss and device timeouts.
"""

import sys
import os
import time
import math
import re
import json
import logging
import shutil
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections import deque
from importlib import import_module
from threading import Event, Lock, Thread
from typing import Any, Deque, Dict, List, Mapping, Optional, SupportsBytes, TextIO, Tuple, cast

from PyQt6 import QtCore, QtWidgets, QtSerialPort, QtGui
from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtSerialPort import QSerialPortInfo

from .ui_en import Ui_MainWindow
from plotting.shared.utils import ensure_app_theme, format_annealing_title, show_plots, install_standard_menu
from data_logging.naming_history import LineEditHistory
from data_logging.data_logger.file_name_builder import composition_warning_state
from data_logging.shared_power_supply.broker import ROLE_CURRENT_ANNEALING, SharedPowerSupplyBroker
from data_logging.shared_power_supply.driver import HmpSerialDriver
from data_logging.shared_power_supply.discovery import (
    SerialPortIdentity,
    hmp_port_preference_key,
)
from data_logging.shared_power_supply.profiles import HMP4030_PROFILE, HMP4040_PROFILE, SupplyProfile
from data_logging.shared_power_supply.protocol import (
    BrokerJsonClient,
    broker_failure_diagnostic,
    start_broker_server,
)
from plotting.shared.power_guard import create_experiment_sleep_guard
from data_logging.source_provenance import (
    CAPTURE_PENDING,
    SourceProvenanceCache,
    patch_source_control_metadata,
    unavailable_source_provenance,
)

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.figure import Figure

LOGGER = logging.getLogger(__name__)

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
except Exception:
    try:
        FigureCanvas = getattr(import_module("matplotlib.backends.backend_qt5agg"), "FigureCanvasQTAgg")
    except Exception:  # pragma: no cover - backend optional
        FigureCanvas = None  # type: ignore[assignment]

try:
    from matplotlib.backends.backend_qt import NavigationToolbar2QT as NavigationToolbar
except Exception:
    try:
        NavigationToolbar = getattr(
            import_module("matplotlib.backends.backend_qt5agg"), "NavigationToolbar2QT"
        )
    except Exception:  # pragma: no cover - backend optional
        NavigationToolbar = None  # type: ignore[assignment]

try:
    import pyqtgraph as pg
except Exception:  # pragma: no cover - optional realtime backend
    pg = None  # type: ignore[assignment]


fig_size = plt.rcParams["figure.figsize"]
fig_size[0] = 19 #19
fig_size[1] = 10 #10
plt.rcParams["figure.figsize"] = fig_size
plt.rcParams["font.family"] = ["sans-serif"]
plt.rcParams["font.size"] = 12


def _apply_app_font_to_matplotlib(app: QtWidgets.QApplication | None = None) -> None:
    """Keep Matplotlib fonts aligned with the active Qt application font."""

    app = app or QtWidgets.QApplication.instance()
    if app is None:
        return
    font = app.font()
    family = font.family()
    if family:
        plt.rcParams["font.family"] = [family]
    size = font.pointSize()
    if size > 0:
        plt.rcParams["font.size"] = max(10, size)

def _default_download_dir() -> str:
    home = Path.home()
    candidates = [
        Path(os.environ.get("USERPROFILE", "")) / "Downloads",
        home / "Downloads",
        home / "downloads",
    ]
    for p in candidates:
        try:
            if p and p.exists():
                return str(p)
        except Exception:
            continue
    p = home / "Downloads"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(p)

DEFAULT_LOG_DIR = _default_download_dir()


def _unique_replaced_output_path(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = path.with_name(f"{path.stem}.replaced-{timestamp}{path.suffix}")
    candidate = base
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}.replaced-{timestamp}-{counter}{path.suffix}")
        counter += 1
    return candidate


def _move_path_to_trash(path: Path) -> Path | None:
    """Move an existing output path to the OS trash, with a local fallback."""

    if not path.exists():
        return None
    if os.name == "nt":
        try:
            fo_delete = 0x0003
            fof_allowundo = 0x0040
            fof_noconfirmation = 0x0010
            fof_silent = 0x0004

            class _SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR),
                    ("fFlags", wintypes.USHORT),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", wintypes.LPVOID),
                    ("lpszProgressTitle", wintypes.LPCWSTR),
                ]

            payload = str(path) + "\0\0"
            operation = _SHFILEOPSTRUCTW(
                None,
                fo_delete,
                payload,
                None,
                fof_allowundo | fof_noconfirmation | fof_silent,
                False,
                None,
                None,
            )
            result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
            if result == 0 and not operation.fAnyOperationsAborted and not path.exists():
                return path
        except Exception:
            pass
    try:
        moved, trash_path = QtCore.QFile.moveToTrash(str(path))
        if moved:
            return Path(trash_path) if trash_path else path
    except Exception:
        pass
    fallback = _unique_replaced_output_path(path)
    if path.is_dir():
        shutil.move(str(path), str(fallback))
    else:
        path.replace(fallback)
    return fallback

DEFAULT_PRESET = {
    "preset": 0,
    "composition": "Ni51Fe26Ga21",
    "microwire": "1_2",
    "sample": "s1",
    "load": "",
    "notes": "",
    "custom_name": "",
}

MAX_VOLTAGE_DEFAULT_ACTION = "ask"
MAX_VOLTAGE_ACTION_LABELS = {
    "ask": "Ask every time",
    "reverse": "Reverse to zero",
    "stop": "Stop measurement",
}
CURRENT_DENSITY_UNIT = "A/mm²"
PLOT_TOP_AXIS_CURRENT_DENSITY = "current_density"
PLOT_TOP_AXIS_POWER_MW = "power_mw"
PLOT_TOP_AXIS_NONE = "none"
PLOT_RIGHT_AXIS_NONE = "none"
PLOT_RIGHT_AXIS_VOLTAGE = "voltage"
PLOT_AXIS_CURRENT_MA = "current_mA"
PLOT_AXIS_SAMPLE_N = "sample_n"
PLOT_AXIS_RESISTANCE = "resistance"
PLOT_AXIS_VOLTAGE = "voltage"
PLOT_AXIS_CURRENT_DENSITY = "current_density"
PLOT_AXIS_POWER_MW = "power_mw"
PLOT_AXIS_NONE = "none"
MIN_PLOTTABLE_RESISTANCE_OHM = 1.0
PLOT_X_AXIS_CHOICES = (
    (PLOT_AXIS_CURRENT_MA, "Current", "mA"),
    (PLOT_AXIS_SAMPLE_N, "N", ""),
    (PLOT_AXIS_CURRENT_DENSITY, "Current density", CURRENT_DENSITY_UNIT),
    (PLOT_AXIS_POWER_MW, "Power", "mW"),
)
PLOT_Y_AXIS_CHOICES = (
    (PLOT_AXIS_RESISTANCE, "Resistance", "Ohm"),
    (PLOT_AXIS_VOLTAGE, "Voltage", "V"),
)

SUPPLY_PROFILES: Dict[str, Dict[str, Any]] = {
    "hmp4030": {
        "label": "HMP4030",
        "start_current_mA": 1,
        "min_start_current_mA": 1,
        "max_voltage": HMP4030_PROFILE.max_voltage_v,
        "channel_select": 0,
        "channel_count": HMP4030_PROFILE.channel_count,
        "current_resolution_mA": HMP4030_PROFILE.current_resolution_mA,
        "min_current_mA": HMP4030_PROFILE.min_current_mA,
        "hmp_profile_id": HMP4030_PROFILE.profile_id,
        "requires_channel": True,
        "reset_on_start": True,
        "voltage_first": False,
    },
    "hmp4040": {
        "label": "HMP4040",
        "start_current_mA": 1,
        "min_start_current_mA": 1,
        "max_voltage": HMP4040_PROFILE.max_voltage_v,
        "channel_select": 0,
        "channel_count": HMP4040_PROFILE.channel_count,
        "current_resolution_mA": HMP4040_PROFILE.current_resolution_mA,
        "min_current_mA": HMP4040_PROFILE.min_current_mA,
        "hmp_profile_id": HMP4040_PROFILE.profile_id,
        "requires_channel": True,
        "reset_on_start": True,
        "voltage_first": False,
    },
    "owon_spe6102": {
        "label": "Owon SPE6102",
        "start_current_mA": 10,
        "min_start_current_mA": 10,
        "max_voltage": 62.0,
        "channel_select": 0,
        "requires_channel": False,
        "reset_on_start": False,
        "voltage_first": True,
    },
    "shared_hmp_broker": {
        "label": "Shared HMP broker",
        "start_current_mA": 1,
        "min_start_current_mA": 1,
        "max_voltage": HMP4040_PROFILE.max_voltage_v,
        "channel_select": 0,
        "channel_count": HMP4040_PROFILE.channel_count,
        "current_resolution_mA": HMP4040_PROFILE.current_resolution_mA,
        "min_current_mA": HMP4040_PROFILE.min_current_mA,
        "requires_channel": True,
        "reset_on_start": False,
        "voltage_first": False,
        "shared_broker": True,
    },
}

INCREASING_CYCLE_COLORS = ["#dc2626", "#f97316", "#ea580c", "#ef4444"]
DECREASING_CYCLE_COLORS = ["#2563eb", "#0ea5e9", "#1d4ed8", "#06b6d4"]
MEASUREMENT_HISTORY_LIMIT = 12
PROJECT_EXTENSION = ".pydpj"
PROJECT_ROW_MICROWIRE_KEYS = ("Microwire", "microwire", "wire", "sample", "Sample")
PROJECT_ROW_DIAMETER_KEYS = ("d (µm)", "d (μm)", "d (um)", "d_um", "d", "Diameter", "diameter_um", "diameter")


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
            if not value:
                return None
        result = float(value)
    except Exception:
        return None
    return result if math.isfinite(result) else None


def _project_row_value(row: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for key in keys:
        value = lowered.get(key.strip().lower())
        if value is not None:
            return value
    return None


def _normalized_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _normalized_microwire_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower().replace("_", "/"))


def _display_microwire(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/").replace("_", "/")
    text = re.sub(r"\s+", "", text)
    if "/" in text:
        left, right = text.split("/", 1)
        return f"{left}/{right}".strip("/")
    digits = re.findall(r"\d+", text)
    if len(digits) >= 2:
        return f"{digits[0]}/{digits[1]}"
    return text


def _cycle_color(direction: float, cycle_index: int) -> str:
    palette = INCREASING_CYCLE_COLORS if direction >= 0 else DECREASING_CYCLE_COLORS
    return palette[(max(1, cycle_index) - 1) % len(palette)]


def _segment_colors_for_currents(currents: List[float], *, step_mA: float = 1.0) -> List[str]:
    if len(currents) < 2:
        return []
    step_value = abs(float(step_mA or 1.0))
    tolerance = max(0.5, step_value * 0.6)
    reversal_threshold = max(tolerance * 2.0, step_value * 1.5)
    inc_count = 0
    dec_count = 0
    current_direction: float | None = None
    pending_direction: float | None = None
    pending_start: int | None = None
    pending_delta = 0.0
    directions: List[float] = []
    for idx in range(1, len(currents)):
        diff = float(currents[idx]) - float(currents[idx - 1])
        if abs(diff) <= tolerance * 0.2 and current_direction is not None:
            direction = current_direction
        else:
            direction = 1.0 if diff >= 0 else -1.0
        directions.append(direction)
        if current_direction is None:
            current_direction = direction
            pending_direction = None
            pending_start = None
            pending_delta = 0.0
            continue
        if direction == current_direction:
            pending_direction = None
            pending_start = None
            pending_delta = 0.0
            continue
        directions[-1] = current_direction
        if pending_direction != direction:
            pending_direction = direction
            pending_start = idx - 1
            pending_delta = abs(diff)
        else:
            pending_delta += abs(diff)
        if pending_start is not None and pending_delta >= reversal_threshold:
            for replace_idx in range(pending_start, len(directions)):
                directions[replace_idx] = direction
            current_direction = direction
            pending_direction = None
            pending_start = None
            pending_delta = 0.0

    colors: List[str] = []
    last_direction: float | None = None
    cycle_index = 0
    for direction in directions:
        if last_direction is None or direction != last_direction:
            if direction >= 0:
                inc_count += 1
                cycle_index = inc_count
            else:
                dec_count += 1
                cycle_index = dec_count
            last_direction = direction
        colors.append(_cycle_color(direction, cycle_index))
    return colors


def _segment_runs_for_currents(currents: List[float], *, step_mA: float = 1.0) -> List[tuple[str, int, int]]:
    if not currents:
        return []
    if len(currents) == 1:
        return [(_cycle_color(1.0, 1), 0, 0)]
    colors = _segment_colors_for_currents(currents, step_mA=step_mA)
    if not colors:
        return [(_cycle_color(1.0, 1), 0, len(currents) - 1)]
    runs: List[tuple[str, int, int]] = []
    start_idx = 0
    current_color = colors[0]
    for segment_idx, color in enumerate(colors[1:], start=1):
        if color == current_color:
            continue
        runs.append((current_color, start_idx, segment_idx))
        start_idx = segment_idx
        current_color = color
    runs.append((current_color, start_idx, len(currents) - 1))
    return runs


@dataclass
class AnnealingSampleRecord:
    composition: str
    microwire: str
    diameter_um: float | None
    source: str


def _records_from_fabrication_index_payload(index: Any, *, source: str) -> list[AnnealingSampleRecord]:
    records: list[AnnealingSampleRecord] = []
    piece_level = getattr(index, "piece_level", {})
    if not isinstance(piece_level, Mapping):
        return records
    for key, data in piece_level.items():
        if not isinstance(key, tuple) or len(key) != 3 or not isinstance(data, Mapping):
            continue
        composition, draw, piece = key
        try:
            draw_int = int(draw)
            piece_int = int(piece)
        except Exception:
            continue
        diameter_um = _safe_float(_project_row_value(data, PROJECT_ROW_DIAMETER_KEYS))
        records.append(
            AnnealingSampleRecord(
                composition=str(composition).strip(),
                microwire=f"{draw_int}/{piece_int}",
                diameter_um=None if diameter_um is None or diameter_um <= 0.0 else diameter_um,
                source=source,
            )
        )
    return records


class FabricationFolderLoadWorker(QtCore.QObject):
    progress_changed = QtCore.pyqtSignal(str)
    succeeded = QtCore.pyqtSignal(object, object, int)
    failed = QtCore.pyqtSignal(object, str)
    cancelled = QtCore.pyqtSignal(object)

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = Path(root)
        self._cancel_event = Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            if self._cancel_event.is_set():
                self.cancelled.emit(self.root)
                return
            if not self.root.exists() or not self.root.is_dir():
                self.failed.emit(self.root, "Fabrication folder was not found.")
                return

            self.progress_changed.emit(f"Scanning fabrication folder: {self.root}")
            files: list[Path] = []
            for path in self.root.rglob("*.xlsx"):
                if self._cancel_event.is_set():
                    self.cancelled.emit(self.root)
                    return
                if path.is_file() and not path.name.startswith("~$"):
                    files.append(path)
                    if len(files) % 100 == 0:
                        self.progress_changed.emit(f"Found {len(files)} fabrication workbook(s)...")

            if not files:
                self.failed.emit(self.root, "No fabrication Excel workbooks were found.")
                return

            self.progress_changed.emit(f"Reading {len(files)} fabrication workbook(s)...")
            from microwire_data_builder import core as builder_core

            index = builder_core.build_fabrication_index(
                files,
                cancel_callback=self._cancel_event.is_set,
            )
            if self._cancel_event.is_set():
                self.cancelled.emit(self.root)
                return
            records = _records_from_fabrication_index_payload(index, source=self.root.name)
            self.succeeded.emit(self.root, records, len(files))
        except Exception as exc:
            if self._cancel_event.is_set():
                self.cancelled.emit(self.root)
            else:
                self.failed.emit(self.root, f"Failed to load fabrication spreadsheets: {exc}")


_RETAINED_FABRICATION_TASKS: set["DaemonFabricationTask"] = set()


class DaemonFabricationTask:
    """Run a fabrication scan without any reference back to its window."""

    def __init__(self, worker: FabricationFolderLoadWorker) -> None:
        self.worker = worker
        self.done_event = Event()
        self._event_lock = Lock()
        self._events: deque[tuple[str, tuple[object, ...]]] = deque()
        worker.progress_changed.connect(
            self._record_progress_changed,
            QtCore.Qt.ConnectionType.DirectConnection,
        )
        worker.succeeded.connect(
            self._record_succeeded,
            QtCore.Qt.ConnectionType.DirectConnection,
        )
        worker.failed.connect(
            self._record_failed,
            QtCore.Qt.ConnectionType.DirectConnection,
        )
        worker.cancelled.connect(
            self._record_cancelled,
            QtCore.Qt.ConnectionType.DirectConnection,
        )
        self.thread = Thread(
            target=self._run,
            name="current-annealing-fabrication-filesystem",
            daemon=True,
        )

    def _record_event(self, name: str, args: tuple[object, ...]) -> None:
        with self._event_lock:
            self._events.append((name, args))

    def _record_progress_changed(self, message: str) -> None:
        self._record_event("progress_changed", (message,))

    def _record_succeeded(self, root: object, records: object, file_count: int) -> None:
        self._record_event("succeeded", (root, records, file_count))

    def _record_failed(self, root: object, message: str) -> None:
        self._record_event("failed", (root, message))

    def _record_cancelled(self, root: object) -> None:
        self._record_event("cancelled", (root,))

    def drain_events(self) -> list[tuple[str, tuple[object, ...]]]:
        with self._event_lock:
            events = list(self._events)
            self._events.clear()
        return events

    def start(self) -> None:
        _RETAINED_FABRICATION_TASKS.add(self)
        self.thread.start()

    def cancel(self) -> None:
        self.worker.cancel()

    def isRunning(self) -> bool:  # noqa: N802 - compatibility with previous QThread owner
        return self.thread.is_alive()

    def wait(self, timeout_ms: int) -> bool:
        self.thread.join(timeout=max(0, int(timeout_ms)) / 1000.0)
        return not self.thread.is_alive()

    def _run(self) -> None:
        try:
            self.worker.run()
        finally:
            self.done_event.set()
            _RETAINED_FABRICATION_TASKS.discard(self)


class MeasurementHistoryDialog(QtWidgets.QDialog):
    """Display recently recorded resistance-current traces."""

    def __init__(self, parent: QtWidgets.QWidget | None, entries: List[Dict[str, Any]]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Measurement history")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        if not entries:
            label = QtWidgets.QLabel("No measurements recorded yet.")
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            layout.addWidget(label)
        else:
            scroll = QtWidgets.QScrollArea(self)
            scroll.setWidgetResizable(True)
            container = QtWidgets.QWidget(scroll)
            container_layout = QtWidgets.QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(12)
            for idx, entry in enumerate(entries, start=1):
                card = QtWidgets.QFrame(container)
                card.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
                card_layout = QtWidgets.QVBoxLayout(card)
                card_layout.setContentsMargins(10, 10, 10, 10)
                card_layout.setSpacing(6)
                title = entry.get("title") or f"Measurement {idx}"
                currents = entry.get("currents", [])
                resistances = entry.get("resistances", [])
                timestamp = entry.get("timestamp", "")
                source = entry.get("source", "")
                title_label = QtWidgets.QLabel(str(title))
                title_label.setWordWrap(True)
                font = title_label.font()
                font.setBold(True)
                title_label.setFont(font)
                card_layout.addWidget(title_label)
                if FigureCanvas is not None:
                    figure = Figure(figsize=(5.5, 3.2))
                    canvas = FigureCanvas(figure)
                    canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
                    ax = figure.add_subplot(111)
                    ax.set_xlabel("Current [mA]")
                    ax.set_ylabel("Resistance [Ohm]")
                    ax.grid(True)
                    if isinstance(currents, list) and isinstance(resistances, list) and len(currents) == len(resistances):
                        if len(currents) == 1:
                            ax.plot(currents, resistances, color=_cycle_color(1.0, 1), marker='o', linestyle='None')
                        else:
                            for color, start_idx, end_idx in _segment_runs_for_currents(currents):
                                ax.plot(
                                    currents[start_idx : end_idx + 1],
                                    resistances[start_idx : end_idx + 1],
                                    color=color,
                                    marker='o',
                                    linestyle='-',
                                )
                    card_layout.addWidget(canvas)
                else:
                    placeholder = QtWidgets.QLabel("Matplotlib backend not available.")
                    placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                    placeholder.setMinimumHeight(160)
                    card_layout.addWidget(placeholder)
                details: list[str] = []
                if timestamp:
                    details.append(timestamp)
                if source:
                    details.append(source)
                if details:
                    info = QtWidgets.QLabel("\n".join(details))
                    info.setWordWrap(True)
                    card_layout.addWidget(info)
                container_layout.addWidget(card)
            container_layout.addStretch(1)
            scroll.setWidget(container)
            layout.addWidget(scroll)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CurrentAnnealingPlotConfigDialog(QtWidgets.QDialog):
    """Dashboard axis selector for both Current Annealing live plots."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        *,
        axis_modes: Mapping[str, Mapping[str, str]],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure plots")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        layout.addLayout(grid)
        for column, label in enumerate(("Bottom X", "Left Y", "Top X", "Right Y"), start=1):
            header = QtWidgets.QLabel(label, self)
            header.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(header, 0, column)

        self._combos: dict[tuple[str, str], QtWidgets.QComboBox] = {}
        defaults = {
            "upper": {
                "bottom": PLOT_AXIS_CURRENT_MA,
                "left": PLOT_AXIS_RESISTANCE,
                "top": PLOT_AXIS_CURRENT_DENSITY,
                "right": PLOT_AXIS_NONE,
            },
            "lower": {
                "bottom": PLOT_AXIS_SAMPLE_N,
                "left": PLOT_AXIS_RESISTANCE,
                "top": PLOT_AXIS_NONE,
                "right": PLOT_AXIS_NONE,
            },
        }
        for row, (plot_key, label) in enumerate((("upper", "Upper plot"), ("lower", "Lower plot")), start=1):
            grid.addWidget(QtWidgets.QLabel(label, self), row, 0)
            plot_modes = {**defaults[plot_key], **dict(axis_modes.get(plot_key, {}))}
            for column, axis_key in enumerate(("bottom", "left", "top", "right"), start=1):
                combo = QtWidgets.QComboBox(self)
                choices = PLOT_X_AXIS_CHOICES if axis_key in {"bottom", "top"} else PLOT_Y_AXIS_CHOICES
                if axis_key in {"top", "right"}:
                    combo.addItem("None", PLOT_AXIS_NONE)
                for mode, name, units in choices:
                    label_text = name if not units else f"{name} ({units})"
                    combo.addItem(label_text, mode)
                selected = str(plot_modes.get(axis_key, defaults[plot_key][axis_key]))
                index = combo.findData(selected)
                combo.setCurrentIndex(index if index >= 0 else 0)
                grid.addWidget(combo, row, column)
                self._combos[(plot_key, axis_key)] = combo

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def axis_modes(self) -> dict[str, dict[str, str]]:
        payload: dict[str, dict[str, str]] = {"upper": {}, "lower": {}}
        for (plot_key, axis_key), combo in self._combos.items():
            payload[plot_key][axis_key] = str(combo.currentData() or PLOT_AXIS_NONE)
        return payload


class MainWindow(QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()
        _apply_app_font_to_matplotlib()
        self.ui = cast(Any, Ui_MainWindow())
        self.ui.setupUi(self)
        self.history_settings = QtCore.QSettings("microwire", "naming_history")
        self.name_history = LineEditHistory(self.history_settings, parent=self)
        # Window title and size cap for laptop screens
        self.setWindowTitle("Current Annealing Logger")
        try:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                self.resize(
                    min(self.width() or 880, max(640, avail.width() - 80)),
                    min(self.height() or 720, max(480, avail.height() - 80)),
                )
        except Exception:
            pass
        menu_bar = install_standard_menu(
            self,
            help_topic="logger_current_annealing",
            open_file=self.handle_browse_full_file,
            open_folder=self.handle_browse_log_dir,
        )
        self._mode_actions: Dict[int, QtGui.QAction] = {}
        self._mode_group: Optional[QtGui.QActionGroup] = None
        self._mode_menu: Optional[QtWidgets.QMenu] = None
        self._init_mode_menu(menu_bar)
        # Remember last log directory and file separately
        self.settings = QtCore.QSettings("microwire", "current_annealing")
        cadence_combo = getattr(self.ui, "comboBox_hmp_readback_rate", None)
        if isinstance(cadence_combo, QtWidgets.QComboBox):
            saved_hz = float(self.settings.value("hmp_readback_hz", 1.0))
            cadence_index = cadence_combo.findData(saved_hz)
            cadence_combo.setCurrentIndex(max(0, cadence_index))
        self._metadata_records_by_composition: dict[str, list[AnnealingSampleRecord]] = {}
        self._metadata_composition_lookup: dict[str, str] = {}
        self._metadata_record_lookup: dict[tuple[str, str], AnnealingSampleRecord] = {}
        self._metadata_composition_model: QtCore.QStringListModel | None = None
        self._metadata_microwire_model: QtCore.QStringListModel | None = None
        self._metadata_composition_completer: QtWidgets.QCompleter | None = None
        self._metadata_microwire_completer: QtWidgets.QCompleter | None = None
        self._metadata_completer_compositions: tuple[str, ...] = ()
        self._metadata_microwire_completer_key: tuple[str, tuple[str, ...]] | None = None
        self._metadata_diameter_imported = False
        self._metadata_diameter_import_sample_key: tuple[str, str] | None = None
        self._fabrication_thread: DaemonFabricationTask | None = None
        self._fabrication_worker: FabricationFolderLoadWorker | None = None
        self._fabrication_tasks: dict[int, DaemonFabricationTask] = {}
        self._fabrication_poll_timer = QtCore.QTimer(self)
        self._fabrication_poll_timer.setInterval(50)
        self._fabrication_poll_timer.timeout.connect(
            self._poll_fabrication_tasks,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self._source_provenance_cache = SourceProvenanceCache()
        self._source_provenance_token: object | None = None
        self._source_provenance_output_path: str | None = None
        self._source_provenance_poll_timer = QtCore.QTimer(self)
        self._source_provenance_poll_timer.setInterval(50)
        self._source_provenance_poll_timer.timeout.connect(
            self._poll_source_provenance_capture,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self._window_closing = False
        self._closing_safe_end = False
        self._close_in_progress = False
        self._callbacks_torn_down = False
        self._delay_timer = QtCore.QTimer(self)
        self._delay_timer.setSingleShot(True)
        self._delay_timer.timeout.connect(
            self._finish_simple_delay,
            QtCore.Qt.ConnectionType.DirectConnection,
        )
        self._last_loop_value = max(1, int(self.settings.value("loops", 1) or 1))
        self.supply_profile_id = "hmp4030"
        self.min_start_current_mA = 1
        self.voltage_first = False
        if hasattr(self.ui, 'lineEdit_log_dir'):
            self.ui.lineEdit_log_dir.setText(
                self.settings.value("log_dir", DEFAULT_LOG_DIR, type=str)
            )
        if hasattr(self.ui, 'lineEdit_log_file'):
            self.ui.lineEdit_log_file.setText(
                self.settings.value("log_file", "anneal_log", type=str)
            )
        self.restore_name_preset()
        self._restore_microwire_metadata_settings()
        try:
            last_max = int(self.settings.value("max_current", 10))
            self.ui.spinBox_max_current.setValue(last_max)
        except Exception:
            pass
        try:
            self.max_current_mA = self.ui.spinBox_max_current.value()
        except Exception:
            pass
        self.start_current_mA = 1
        self.max_voltage = HMP4040_PROFILE.max_voltage_v
        self.reset_on_start = True
        self.channel_select = 0
        self._shared_broker_client: Any = None
        self._shared_broker_lease_id: str | None = None
        self._shared_broker_owner = "current_annealing_logger"
        self._shared_broker_role_checked_channel: int | None = None
        self._shared_broker_current_limit_mA: float | None = None
        self._shared_broker_limit_warning_shown = False
        self._shared_broker_effective_hz = self._requested_hmp_readback_hz()
        self._shared_broker_cadence_generation = 0
        self._owned_shared_broker_server: Any = None
        self._owned_shared_broker_thread: Any = None
        self._owned_shared_broker_driver: Any = None
        self._hardware_auto_connect_progress: QtWidgets.QProgressDialog | None = None
        self._sleep_guard: Any = None
        self.is_connected = False
        self._update_hmp_cadence_label()
        self._init_supply_profile()
        self.max_voltage_action: str = MAX_VOLTAGE_DEFAULT_ACTION
        self._init_max_voltage_action()
        self.init_live_values()
        self.serial_response = ''
        self.serial_command = ''
        self.is_connected = False
        self.port_number = self.ui.spinBox_port_number.value()
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())
        self.ser_mcu = QtSerialPort.QSerialPort()
        self.lock = QtCore.QMutex()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(
            self.handle_update_serial_response_label,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self.timer.start(50)
        # timer for time remaining label
        self.time_timer = QtCore.QTimer(self)
        self.time_timer.timeout.connect(
            self.update_time_estimate,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self.time_timer.start(1000)
        
        # Timer that schedules outgoing commands
        self.command_number = 0
        self.timer_command = QtCore.QTimer(self)
        self.timer_command.timeout.connect(
            self.handle_send_new_command,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        
        self.f_name: str | None = None
        self.f_out: TextIO | None = None
        self.sample_window_size = 1000
        self.sample_index = 0
        self.recording_enabled = False
        self.expecting_voltage = True

        self.resistance_drop_percent = 10
        if not hasattr(self, "max_current_mA"):
            self.max_current_mA = 10
        self.operation_mode = 2  # 0 - VCP, 1 - manual, 2 - automatic (default auto)
        self.process_running = False

        self.current_current_set = self._start_current_A()
        self._ramp_ideal_current_A = self.current_current_set
        self.current_current_read = 0.0
        self.current_increment = 0.001
        self.temp_resistance_maximum = 0.0
        self.current_voltage = 0.0
        self.current_resistance = 0.0
        self.max_voltage = float(getattr(self, "max_voltage", 30.0))
        self.open_threshold = self.max_voltage
        self._max_voltage_dialog = False
        self.direction_ascending = True
        self.sample_ready = False
        self.force_stop_at_zero = False
        # Debug + progress/time tracking
        self.DEBUG = False
        self.sample_rate: float | None = None
        self._rate_window: Deque[float] = deque(maxlen=200)
        self.last_sample_time: float | None = None
        self._finish_time: float | None = None
        self.step_idx = 0
        self.total_steps = 0
        self._loop_sample_history: list[int] = []
        self._current_loop_samples = 0
        self._planned_loop_steps = 0
        self._projected_loop_samples: int | None = None
        self._contact_lost = False
        self._zero_current_count = 0
        self._nonzero_current_seen = False
        self._skip_current_sample = False
        self._process_start_time: float | None = None
        self._last_nonzero_current_time: float | None = None
        self._zero_placeholder_count = 0
        self._zero_placeholders_active = False
        self._zero_placeholder_line1: Line2D | None = None
        self._zero_placeholder_line2: Line2D | None = None
        self._contact_grace_period = 5.0
        self._last_serial_rx: float | None = None
        self._serial_quiet_failures = 0
        self._voltage_history: Deque[Tuple[float, float, float]] = deque(maxlen=60)
        self._time_to_voltage_limit: float | None = None
        self._estimated_limit_current_mA: float | None = None
        self._applied_limit_current_mA: float | None = None
        self._samples_current: List[float] = []
        self._samples_resistance: List[float] = []
        self._samples_voltage: List[float] = []
        self._segment_lines_ax1: list[Any] = []
        self._segment_lines_ax2: list[Any] = []
        self._right_axis_views: dict[str, Any] = {}
        self._right_axis_curves: dict[str, Any] = {}
        self._placeholder_text_ax1: Any = None
        self._placeholder_text_ax2: Any = None
        self._plot_backend = "none"
        self.pg_plot_resistance_vs_current: Any = None
        self.pg_plot_resistance_vs_sample: Any = None
        self._plot_axis_modes = self._load_plot_axis_modes()
        self._pg_placeholder_labels: list[QtWidgets.QLabel] = []
        self._hardware_auto_connect_progress: QtWidgets.QProgressDialog | None = None
        self._last_auto_connect_error = ""
        self._last_run_error = ""
        self._last_stop_reason = ""
        self._process_state = "idle"
        self._history_settings = QtCore.QSettings("microwire", "current_annealing_history")
        self._measurement_history: List[Dict[str, Any]] = self._load_measurement_history()

        self.curr_value_x: float = 0.0
        self.curr_value_y: float = 0.0
        

        # Populate modern port list if available
        self.port_name = ""
        if hasattr(self.ui, 'comboBox_port'):
            try:
                self.populate_ports()
            except Exception:
                pass
        self._set_port_controls_enabled(True)
        
        # Connect UI signals to the logic handlers
        self.ui.pushButton_connect_port.clicked.connect(self.handle_connect_port_clicked)
        self.ui.spinBox_port_number.valueChanged.connect(self.handle_port_number_value_changed)
        self.ui.comboBox_baudrate.currentIndexChanged.connect(self.handle_comboBox_baudrate_currentIndexChanged)
        if hasattr(self.ui, "pushButton_auto_detect_hmp"):
            self.ui.pushButton_auto_detect_hmp.clicked.connect(self.handle_auto_detect_hmp_clicked)
        if hasattr(self.ui, "checkBox_show_hmp_port_options"):
            self.ui.checkBox_show_hmp_port_options.toggled.connect(self._sync_hardware_connection_controls)
        self.ui.pushButton_send_serial_command.clicked.connect(self.handle_send_serial_command_clicked)
        
        
        self.ui.spinBox_max_current.valueChanged.connect(self.handle_max_current_value_changed)
        if hasattr(self.ui, 'spinBox_max_voltage'):
            self.ui.spinBox_max_voltage.valueChanged.connect(self.handle_max_voltage_value_changed)
        if hasattr(self.ui, 'comboBox_channel'):
            self.ui.comboBox_channel.currentIndexChanged.connect(self.handle_channel_select_value_changed)
        if hasattr(self.ui, 'spinBox_channel'):
            self.ui.spinBox_channel.valueChanged.connect(self.handle_channel_select_value_changed)
        if hasattr(self.ui, 'checkBox_reset_on_start'):
            self.ui.checkBox_reset_on_start.toggled.connect(self.handle_reset_on_start_toggled)
        if hasattr(self.ui, 'spinBox_start_current'):
            self.ui.spinBox_start_current.valueChanged.connect(self.handle_start_current_value_changed)
            self.ui.spinBox_start_current.valueChanged.connect(self._refresh_config_current_density_labels)
        self.ui.pushButton_start_process.clicked.connect(self.handle_toggle_process_clicked)
        self.ui.lineEdit_log_file_full.textChanged.connect(self.handle_legacy_log_path_changed)
        if hasattr(self.ui, 'pushButton_select_filename'):
            self.ui.pushButton_select_filename.clicked.connect(self.handle_browse_full_file)
        if hasattr(self.ui, 'pushButton_reverse_now'):
            self.ui.pushButton_reverse_now.clicked.connect(self.handle_pushButton_reverse_now_clicked)
            self.ui.pushButton_reverse_now.setEnabled(False)
        if hasattr(self.ui, 'pushButton_update_running_recipe'):
            self.ui.pushButton_update_running_recipe.clicked.connect(self.handle_update_running_recipe_clicked)
            self.ui.pushButton_update_running_recipe.setEnabled(False)
            self.ui.pushButton_update_running_recipe.setVisible(False)
        if hasattr(self.ui, 'pushButton_show_history'):
            self.ui.pushButton_show_history.clicked.connect(self.handle_show_history_clicked)
            self._update_history_button_state()
        # New UI pieces: port dropdown and separate log directory/name
        if hasattr(self.ui, 'comboBox_port'):
            self.ui.comboBox_port.currentIndexChanged.connect(self.handle_comboBox_port_changed)
        if hasattr(self.ui, 'pushButton_refresh_ports'):
            self.ui.pushButton_refresh_ports.clicked.connect(self.populate_ports)
        if hasattr(self.ui, 'comboBox_supply'):
            self.ui.comboBox_supply.currentIndexChanged.connect(self.handle_supply_profile_changed)
        if hasattr(self.ui, 'lineEdit_broker_host'):
            self.ui.lineEdit_broker_host.textChanged.connect(self.handle_broker_settings_changed)
        if hasattr(self.ui, 'spinBox_broker_port'):
            self.ui.spinBox_broker_port.valueChanged.connect(self.handle_broker_settings_changed)
        if hasattr(self.ui, 'comboBox_hmp_readback_rate'):
            self.ui.comboBox_hmp_readback_rate.currentIndexChanged.connect(
                self.handle_hmp_readback_rate_changed
            )
        if hasattr(self.ui, 'pushButton_browse_dir'):
            self.ui.pushButton_browse_dir.clicked.connect(self.handle_browse_log_dir)
        if hasattr(self.ui, 'pushButton_open_dir'):
            self.ui.pushButton_open_dir.clicked.connect(self.open_log_dir)
        if hasattr(self.ui, 'lineEdit_log_dir'):
            self.ui.lineEdit_log_dir.textChanged.connect(self.sync_full_log_path)
        if hasattr(self.ui, 'lineEdit_log_file'):
            self.ui.lineEdit_log_file.textChanged.connect(self.sync_full_log_path)
        # Name builder and planned duration estimation
        if hasattr(self.ui, 'comboBox_name_preset'):
            self.ui.comboBox_name_preset.currentIndexChanged.connect(self.update_file_name_from_preset)
        for name in (
            'lineEdit_composition',
            'lineEdit_microwire',
            'lineEdit_sample',
            'lineEdit_load',
            'lineEdit_notes',
            'lineEdit_custom_name',
        ):
            if hasattr(self.ui, name):
                getattr(self.ui, name).textChanged.connect(self.update_file_name_from_preset)
        if hasattr(self.ui, 'lineEdit_composition'):
            self.ui.lineEdit_composition.textChanged.connect(self._handle_composition_text_changed)
            self.ui.lineEdit_composition.textChanged.connect(self._sync_microwire_metadata_fields)
        if hasattr(self.ui, 'lineEdit_microwire'):
            self.ui.lineEdit_microwire.textChanged.connect(self._sync_microwire_metadata_fields)
            self.ui.lineEdit_microwire.editingFinished.connect(self._normalize_microwire_field_separator)
        if hasattr(self.ui, 'pushButton_browse_builder_project'):
            self.ui.pushButton_browse_builder_project.clicked.connect(self._choose_builder_project)
        if hasattr(self.ui, 'pushButton_import_builder_project'):
            self.ui.pushButton_import_builder_project.clicked.connect(self._import_builder_project_from_ui)
        if hasattr(self.ui, 'pushButton_browse_fabrication_folder'):
            self.ui.pushButton_browse_fabrication_folder.clicked.connect(self._choose_fabrication_folder)
        if hasattr(self.ui, 'pushButton_load_fabrication'):
            self.ui.pushButton_load_fabrication.clicked.connect(self._handle_fabrication_load_button)
        if hasattr(self.ui, 'lineEdit_builder_project'):
            self.ui.lineEdit_builder_project.textChanged.connect(self._store_microwire_metadata_settings)
        if hasattr(self.ui, 'lineEdit_fabrication_folder'):
            self.ui.lineEdit_fabrication_folder.textChanged.connect(self._store_microwire_metadata_settings)
        if hasattr(self.ui, 'doubleSpinBox_wire_diameter_um'):
            self.ui.doubleSpinBox_wire_diameter_um.valueChanged.connect(self._handle_diameter_changed)
        self.name_history.register('composition', getattr(self.ui, 'lineEdit_composition', None))
        self.name_history.register('microwire', getattr(self.ui, 'lineEdit_microwire', None))
        if hasattr(self.ui, 'pushButton_reset_preset'):
            self.ui.pushButton_reset_preset.clicked.connect(self.reset_name_preset)
        if hasattr(self.ui, 'checkBox_reverse'):
            self.ui.checkBox_reverse.toggled.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'spinBox_loops'):
            self.ui.spinBox_loops.valueChanged.connect(self.update_planned_time_label)
            try:
                stored_loops = int(self.settings.value("loops", self._last_loop_value) or self._last_loop_value)
            except Exception:
                stored_loops = self._last_loop_value
            stored_loops = max(1, stored_loops)
            self._last_loop_value = stored_loops
            try:
                self.ui.spinBox_loops.blockSignals(True)
                self.ui.spinBox_loops.setValue(stored_loops)
            finally:
                self.ui.spinBox_loops.blockSignals(False)
            self.ui.spinBox_loops.valueChanged.connect(self._handle_loop_value_changed)
        if hasattr(self.ui, 'checkBox_infinite_loops'):
            self.ui.checkBox_infinite_loops.toggled.connect(self.handle_checkBox_infinite_loops_toggled)
            stored_infinite = bool(int(self.settings.value("loops_infinite", 0) or 0))
            try:
                self.ui.checkBox_infinite_loops.blockSignals(True)
                self.ui.checkBox_infinite_loops.setChecked(stored_infinite)
            finally:
                self.ui.checkBox_infinite_loops.blockSignals(False)
            self.ui.checkBox_infinite_loops.toggled.connect(self._store_loop_preferences)
            if stored_infinite:
                self.handle_checkBox_infinite_loops_toggled(True)
        if hasattr(self.ui, 'spinBox_step_mA'):
            self.ui.spinBox_step_mA.valueChanged.connect(self.handle_step_changed)
            self.ui.spinBox_step_mA.valueChanged.connect(self._refresh_config_current_density_labels)
        self.ui.spinBox_max_current.valueChanged.connect(self.update_file_name_from_preset)
        self.ui.spinBox_max_current.valueChanged.connect(self.update_planned_time_label)
        self.ui.spinBox_max_current.valueChanged.connect(self._refresh_config_current_density_labels)
        if hasattr(self.ui, 'checkBox_infinite_loops'):
            self.ui.checkBox_infinite_loops.toggled.connect(self.update_planned_time_label)
        self._store_loop_preferences()
        # Initialize planned estimate and file name once
        try:
            self.update_file_name_from_preset()
            self.update_planned_time_label()
        except Exception:
            pass
        # Apply initial mode selection
        try:
            self.handle_mode_changed(self.operation_mode)
        except Exception:
            pass
        
        # Keep recipe/process settings editable before hardware connection.
        self.ui.frame_process_settings.setEnabled(True)
        self.ui.frame_command_and_response.setEnabled(False)

        # Legacy overlay is kept as a no-op compatibility hook; settings stay editable.
        self._setup_connect_overlay()
        self._show_connect_overlay(False)
        
        self.max_resistance = 0
        
        self._refresh_command_profiles()
        
        self.f_out = None
        self.f_name = self.build_log_path() if hasattr(self, 'build_log_path') else self.ui.lineEdit_log_file_full.text()
        
        
        # Variables used for plotting data
        self.curr_value_x = 0.0
        self.curr_value_y = 0.0
        self.first_sample = True

        self.fig = None
        self.ax1 = None
        self.ax2 = None
        self.canvas = None
                
        self.line_color="r"
        # Initialize progress UI defaults
        if hasattr(self.ui, 'progressBar_process'):
            self.ui.progressBar_process.setMaximum(1)
            self.ui.progressBar_process.setValue(0)
        self._set_process_state("idle")
        self._set_hardware_status("Disconnected", state="idle")
        if hasattr(self.ui, 'label_time_remaining'):
            self.ui.label_time_remaining.setText("Time remaining: N/A")
        # Current step defaults
        try:
            self.current_step_mA = self.ui.spinBox_step_mA.value()
        except Exception:
            self.current_step_mA = 1
        self.current_step_A = self.current_step_mA / 1000.0

        # Show initial placeholder plot on the right
        try:
            self.init_graph_window()
            if getattr(self, '_plot_backend', '') == 'pyqtgraph':
                self._show_pyqtgraph_placeholders()
            else:
                ax1 = getattr(self, 'ax1', None)
                if ax1 is not None:
                    self._placeholder_text_ax1 = ax1.text(
                        0.5,
                        0.5,
                        'No data yet',
                        transform=ax1.transAxes,
                        ha='center',
                        va='center',
                        fontsize=14,
                        fontweight='bold',
                        color=self.palette().color(QtGui.QPalette.ColorRole.Text),
                        bbox=dict(facecolor='k', alpha=0.35, edgecolor='none', pad=3),
                    )
                ax2 = getattr(self, 'ax2', None)
                if ax2 is not None:
                    self._placeholder_text_ax2 = ax2.text(
                        0.5,
                        0.5,
                        'No data yet',
                        transform=ax2.transAxes,
                        ha='center',
                        va='center',
                        fontsize=14,
                        fontweight='bold',
                        color=self.palette().color(QtGui.QPalette.ColorRole.Text),
                        bbox=dict(facecolor='k', alpha=0.35, edgecolor='none', pad=3),
                    )
            canvas = getattr(self, 'canvas', None)
            if canvas is not None:
                canvas.draw()
        except Exception:
            pass
        try:
            self.adjustSize()
        except Exception:
            pass
        self._install_settings_wheel_guard()

    # utilities
    def dbg(self, *args):
        if getattr(self, 'DEBUG', False):
            try:
                print(*args)
            except Exception:
                pass

    def _install_settings_wheel_guard(self) -> None:
        scroll = getattr(self.ui, "left_scroll", None)
        if not isinstance(scroll, QtWidgets.QScrollArea):
            return
        control_root = scroll.widget()
        if not isinstance(control_root, QtWidgets.QWidget):
            return
        for widget in control_root.findChildren((QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)):
            widget.setProperty("_current_annealing_wheel_guard", True)
            widget.installEventFilter(self)
            if isinstance(widget, QtWidgets.QAbstractSpinBox):
                editor = widget.lineEdit()
                editor.setProperty("_current_annealing_wheel_guard", True)
                editor.installEventFilter(self)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if (
            event.type() == QtCore.QEvent.Type.Wheel
            and isinstance(watched, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox, QtWidgets.QLineEdit))
            and watched.property("_current_annealing_wheel_guard")
        ):
            if isinstance(watched, QtWidgets.QComboBox) and watched.view().isVisible():
                return super().eventFilter(watched, event)
            self._scroll_settings_panel_from_wheel(event)
            return True
        return super().eventFilter(watched, event)

    def _scroll_settings_panel_from_wheel(self, event: QtCore.QEvent) -> None:
        if not isinstance(event, QtGui.QWheelEvent):
            event.ignore()
            return
        scroll = getattr(self.ui, "left_scroll", None)
        if not isinstance(scroll, QtWidgets.QScrollArea):
            event.ignore()
            return
        scrollbar = scroll.verticalScrollBar()
        delta = event.pixelDelta().y()
        if delta == 0:
            delta = int(event.angleDelta().y() / 120 * scrollbar.singleStep() * 3)
        if delta != 0:
            scrollbar.setValue(scrollbar.value() - delta)
        event.accept()

    def _show_pyqtgraph_placeholders(self) -> None:
        if self._pg_placeholder_labels:
            for label in self._pg_placeholder_labels:
                label.show()
            return
        for plot in (self.pg_plot_resistance_vs_current, self.pg_plot_resistance_vs_sample):
            if plot is None:
                continue
            label = QtWidgets.QLabel("No data yet", plot)
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            label.setStyleSheet(
                "QLabel { color: palette(text); background: rgba(0, 0, 0, 90); "
                "font-weight: 700; padding: 4px; }"
            )
            label.setGeometry(plot.rect())
            label.show()
            self._pg_placeholder_labels.append(label)

    def _resize_pyqtgraph_placeholders(self) -> None:
        for label in self._pg_placeholder_labels:
            parent = label.parentWidget()
            if parent is not None:
                label.setGeometry(parent.rect())

    def _pyqtgraph_color(self, color: str) -> str:
        if color == "r":
            return "#d32f2f"
        if color == "g":
            return "#27ae60"
        if color == "b":
            return "#1976d2"
        return color

    def _pyqtgraph_plot_kwargs(self, color: str) -> dict[str, Any]:
        if pg is None:
            return {}
        mapped = self._pyqtgraph_color(color)
        return {
            "pen": pg.mkPen(mapped, width=2),
            "symbol": "o",
            "symbolSize": 6,
            "symbolBrush": pg.mkBrush(mapped),
            "symbolPen": pg.mkPen(mapped),
        }

    def _draw_live_canvas(self) -> None:
        canvas = getattr(self, 'canvas', None)
        if canvas is None:
            return
        try:
            canvas.draw_idle()
            canvas.flush_events()
        except Exception:
            canvas.draw()

    def _refresh_pyqtgraph_ranges(self) -> None:
        if not self._samples_current or not self._samples_resistance:
            for plot in (self.pg_plot_resistance_vs_current, self.pg_plot_resistance_vs_sample):
                if plot is not None:
                    plot.enableAutoRange(axis='xy', enable=True)
            return
        for plot_key, plot in (
            ("upper", self.pg_plot_resistance_vs_current),
            ("lower", self.pg_plot_resistance_vs_sample),
        ):
            x_values = self._plot_series_values(self._axis_mode(plot_key, "bottom"))
            y_values = self._plot_series_values(self._axis_mode(plot_key, "left"))
            self._set_pyqtgraph_range_from_values(plot, x_values, y_values, plot_key=plot_key)

    def _set_pyqtgraph_range_from_values(
        self,
        plot: Any,
        x_values: list[float],
        y_values: list[float],
        *,
        plot_key: str,
    ) -> None:
        if plot is None:
            return
        finite_x = [float(value) for value in x_values if math.isfinite(float(value))]
        finite_y = [float(value) for value in y_values if math.isfinite(float(value))]
        if not finite_x or not finite_y:
            plot.enableAutoRange(axis='xy', enable=True)
            return

        def _padded_bounds(values: list[float], *, minimum_padding: float) -> tuple[float, float]:
            low = min(values)
            high = max(values)
            if math.isclose(low, high):
                padding = max(abs(low) * 0.05, minimum_padding)
            else:
                padding = max((high - low) * 0.06, minimum_padding)
            return low - padding, high + padding

        x_low, x_high = _padded_bounds(finite_x, minimum_padding=1.0)
        y_low, y_high = _padded_bounds(finite_y, minimum_padding=1.0)
        plot.setXRange(x_low, x_high, padding=0.0)
        plot.setYRange(y_low, y_high, padding=0.0)
        self._refresh_top_axis(plot_key, x_low=x_low, x_high=x_high)
        self._refresh_right_axis_overlay(plot_key)

    def _axis_mode(self, plot_key: str, axis_key: str) -> str:
        return str(
            getattr(self, "_plot_axis_modes", self._default_plot_axis_modes()).get(plot_key, {}).get(
                axis_key,
                self._default_plot_axis_modes().get(plot_key, {}).get(axis_key, PLOT_AXIS_NONE),
            )
        )

    def _plot_axis_label_units(self, mode: str) -> tuple[str, str]:
        for candidate, label, units in (*PLOT_X_AXIS_CHOICES, *PLOT_Y_AXIS_CHOICES):
            if mode == candidate:
                return label, units
        return "", ""

    def _plot_series_values(self, mode: str) -> list[float]:
        currents = list(self._samples_current)
        resistances = list(self._samples_resistance)
        voltages = list(self._samples_voltage)
        n = len(currents)
        if mode == PLOT_AXIS_CURRENT_MA:
            return [float(value) for value in currents]
        if mode == PLOT_AXIS_SAMPLE_N:
            return [float(index) for index in range(1, n + 1)]
        if mode == PLOT_AXIS_RESISTANCE:
            return [float(value) for value in resistances]
        if mode == PLOT_AXIS_VOLTAGE:
            if len(voltages) < n:
                voltages = voltages + [math.nan] * (n - len(voltages))
            return [float(value) for value in voltages[:n]]
        if mode == PLOT_AXIS_CURRENT_DENSITY:
            return [
                float(value) if value is not None else math.nan
                for value in (self._current_density_a_mm2(current) for current in currents)
            ]
        if mode == PLOT_AXIS_POWER_MW:
            return [
                (float(current) ** 2) * float(resistance) / 1000.0
                for current, resistance in zip(currents, resistances)
            ]
        return []

    def _format_axis_value(self, mode: str, value: float) -> str:
        if not math.isfinite(float(value)):
            return ""
        abs_value = abs(float(value))
        decimals = 2 if abs_value < 10.0 else (1 if abs_value < 100.0 else 0)
        return f"{float(value):.{decimals}f}"

    def _nearest_axis_label_for_bottom_position(self, plot_key: str, axis_mode: str, position: float) -> str:
        bottom_values = self._plot_series_values(self._axis_mode(plot_key, "bottom"))
        axis_values = self._plot_series_values(axis_mode)
        pairs = [
            (float(x_value), float(axis_value))
            for x_value, axis_value in zip(bottom_values, axis_values)
            if math.isfinite(float(x_value)) and math.isfinite(float(axis_value))
        ]
        if not pairs:
            return ""
        _, value = min(pairs, key=lambda pair: abs(pair[0] - float(position)))
        return self._format_axis_value(axis_mode, value)

    def _refresh_current_density_axis(self, *, x_low: float | None = None, x_high: float | None = None) -> None:
        self._refresh_top_axis("upper", x_low=x_low, x_high=x_high)

    def _refresh_top_axis(self, plot_key: str, *, x_low: float | None = None, x_high: float | None = None) -> None:
        plot = (
            getattr(self, "pg_plot_resistance_vs_current", None)
            if plot_key == "upper"
            else getattr(self, "pg_plot_resistance_vs_sample", None)
        )
        if pg is None or plot is None:
            return
        plot_item = plot.getPlotItem()
        top_axis = plot_item.getAxis("top")
        mode = self._axis_mode(plot_key, "top")
        if mode == PLOT_AXIS_NONE:
            top_axis.setLabel("")
            top_axis.setTicks([])
            top_axis.setStyle(showValues=False, tickLength=0, maxTickLevel=0, maxTextLevel=0)
            return
        if mode == PLOT_AXIS_CURRENT_DENSITY and self._diameter_um() is None:
            top_axis.setLabel("")
            top_axis.setTicks([])
            top_axis.setStyle(showValues=False, tickLength=0, maxTickLevel=0, maxTextLevel=0)
            return
        if x_low is None or x_high is None:
            try:
                x_low, x_high = plot_item.viewRange()[0]
            except Exception:
                return
        if not (math.isfinite(float(x_low)) and math.isfinite(float(x_high))) or math.isclose(float(x_low), float(x_high)):
            return
        positions = self._current_axis_tick_positions(plot_item, float(x_low), float(x_high))
        label, units = self._plot_axis_label_units(mode)
        ticks = [
            (position, self._nearest_axis_label_for_bottom_position(plot_key, mode, position))
            for position in positions
        ]
        top_axis.setLabel(label, units=units)
        top_axis.setTicks([ticks])
        top_axis.setStyle(showValues=True, tickLength=4, maxTickLevel=0, maxTextLevel=0)

    def _current_axis_tick_positions(self, plot_item: Any, x_low: float, x_high: float) -> list[float]:
        bottom_axis = plot_item.getAxis("bottom")
        try:
            view = plot_item.getViewBox()
            width = max(1, int(view.width()))
        except Exception:
            try:
                width = max(1, int(plot_item.width()))
            except Exception:
                width = 400
        try:
            levels = bottom_axis.tickValues(x_low, x_high, width)
        except Exception:
            levels = []
        for _spacing, values in levels:
            positions = [
                float(value)
                for value in values
                if math.isfinite(float(value)) and x_low <= float(value) <= x_high
            ]
            if positions:
                return positions
        return [x_low + (x_high - x_low) * idx / 4.0 for idx in range(5)]

    def _add_live_plot_item(self, plot: Any, x_values: list[float], y_values: list[float], color: str) -> Any:
        if plot is None or pg is None:
            return None
        item = plot.plot(x_values, y_values, **self._pyqtgraph_plot_kwargs(color))
        return item

    def _remove_live_plot_item(self, item: Any) -> None:
        if item is None:
            return
        try:
            parent = item.getViewBox()
            if parent is not None:
                parent.removeItem(item)
                return
        except Exception:
            pass
        for plot in (self.pg_plot_resistance_vs_current, self.pg_plot_resistance_vs_sample):
            try:
                if plot is not None:
                    plot.removeItem(item)
                    return
            except Exception:
                pass

    def _record_zero_placeholder(self) -> None:
        """Ignore leading zero-current samples without plotting or persisting them."""

        if self._nonzero_current_seen:
            return
        self._clear_zero_placeholders()

    def _handle_zero_current_readback(self) -> None:
        """Handle a near-zero measured current while a nonzero setpoint is active."""

        self._skip_current_sample = True
        self.sample_ready = True
        try:
            now = time.monotonic()
        except Exception:
            now = None
        self._zero_current_count += 1
        if not self._nonzero_current_seen:
            self._record_zero_placeholder()
            self._last_nonzero_current_time = None
        zero_limit = 6
        zero_delay = 2.0
        if (
            now is not None
            and self._process_start_time is not None
            and (now - self._process_start_time) < self._contact_grace_period
        ):
            return
        if (
            self._nonzero_current_seen
            and now is not None
            and self._last_nonzero_current_time is not None
            and (now - self._last_nonzero_current_time) < zero_delay
        ):
            return
        if self._zero_current_count < zero_limit:
            return
        if not self._contact_lost:
            self._contact_lost = True
            QtWidgets.QMessageBox.warning(
                self,
                "Contact lost",
                "Measured current is zero while current is being applied. "
                "Check the wire/contact connection; the wire may be disconnected or burned through. "
                "Stopping the process.",
            )
            if self.process_running:
                self.stop_annealing(
                    "Contact lost; stopping measurement.",
                    show_dialog=False,
                    final_state="failed",
                )

    def _clear_zero_placeholders(self) -> None:
        """Remove any temporary zero-current markers from the plots."""

        if not self._zero_placeholders_active and self._zero_placeholder_count == 0:
            return

        for line in (self._zero_placeholder_line1, self._zero_placeholder_line2):
            if line is None:
                continue
            try:
                if getattr(self, '_plot_backend', '') == 'pyqtgraph':
                    self._remove_live_plot_item(line)
                else:
                    line.remove()
            except Exception:
                pass

        self._zero_placeholder_line1 = None
        self._zero_placeholder_line2 = None
        self._zero_placeholder_count = 0
        self._zero_placeholders_active = False

        ax1 = getattr(self, 'ax1', None)
        ax2 = getattr(self, 'ax2', None)
        if getattr(self, '_plot_backend', '') == 'pyqtgraph':
            self._refresh_pyqtgraph_ranges()
        else:
            for axis in (ax1, ax2):
                if axis is not None:
                    axis.relim()
                    axis.autoscale_view()
            self._draw_live_canvas()

    def _display_ui_value(self, attr: str, text: str) -> None:
        if attr == 'label_live_voltage':
            self._set_live_voltage_text(text)
            return
        if attr == 'lcd_current_mA':
            self._set_live_current_text(text)
            return
        if attr == 'label_set_current':
            self._set_live_set_current_text(text)
            return
        widget = getattr(self.ui, attr, None)
        if widget is None:
            return
        target = cast(Any, widget)
        display_fn = getattr(target, 'display', None)
        if callable(display_fn):
            display_fn(text)
            return
        setter = getattr(target, 'setText', None)
        if callable(setter):
            setter(text)

    def _diameter_um(self) -> float | None:
        spin = getattr(self.ui, "doubleSpinBox_wire_diameter_um", None)
        if not isinstance(spin, QtWidgets.QDoubleSpinBox):
            return None
        diameter = float(spin.value())
        return diameter if math.isfinite(diameter) and diameter > 0.0 else None

    def _current_density_a_mm2(self, current_mA: float) -> float | None:
        diameter_um = self._diameter_um()
        if diameter_um is None:
            return None
        diameter_mm = diameter_um / 1000.0
        area_mm2 = math.pi * (diameter_mm / 2.0) ** 2
        if area_mm2 <= 0.0:
            return None
        return (float(current_mA) / 1000.0) / area_mm2

    def _format_current_density(self, current_mA: float) -> str:
        density = self._current_density_a_mm2(current_mA)
        if density is None:
            return ""
        abs_density = abs(density)
        decimals = 2 if abs_density < 10.0 else (1 if abs_density < 100.0 else 0)
        return f"{density:.{decimals}f}"

    def _resistance_at_current_for_power_axis(self, current_mA: float) -> float | None:
        currents = list(getattr(self, "_samples_current", []))
        resistances = list(getattr(self, "_samples_resistance", []))
        pairs = [
            (float(c), float(r))
            for c, r in zip(currents, resistances)
            if math.isfinite(float(c)) and math.isfinite(float(r)) and float(r) > 0.0
        ]
        if not pairs:
            return None
        pairs.sort(key=lambda pair: pair[0])
        if len(pairs) == 1:
            return pairs[0][1]
        for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
            if x0 <= current_mA <= x1 or x1 <= current_mA <= x0:
                if math.isclose(x0, x1):
                    return y1
                frac = (current_mA - x0) / (x1 - x0)
                return y0 + frac * (y1 - y0)
        nearest = min(pairs, key=lambda pair: abs(pair[0] - current_mA))
        return nearest[1]

    def _format_power_mw_at_current(self, current_mA: float) -> str:
        resistance = self._resistance_at_current_for_power_axis(float(current_mA))
        if resistance is None:
            return ""
        power_mw = (float(current_mA) ** 2) * float(resistance) / 1000.0
        abs_power = abs(power_mw)
        decimals = 2 if abs_power < 10.0 else (1 if abs_power < 100.0 else 0)
        return f"{power_mw:.{decimals}f}"

    def _refresh_current_density_visibility(self) -> None:
        visible = self._diameter_um() is not None
        for label in (
            getattr(self, "label_live_set_density", None),
            getattr(self, "label_live_current_density", None),
            getattr(self.ui, "label_max_current_density", None),
            getattr(self.ui, "label_start_current_density", None),
            getattr(self.ui, "label_step_density", None),
        ):
            if isinstance(label, QtWidgets.QLabel):
                label.setVisible(visible)
                layout = label.parentWidget().layout() if label.parentWidget() is not None else None
                label_for_field = getattr(layout, "labelForField", None)
                if callable(label_for_field):
                    field_label = label_for_field(label)
                    if isinstance(field_label, QtWidgets.QWidget):
                        field_label.setVisible(visible)
        hint = getattr(self.ui, "label_current_density_hint", None)
        if isinstance(hint, QtWidgets.QLabel):
            diameter_um = self._diameter_um()
            if diameter_um is None:
                hint.setText("")
            elif self._metadata_diameter_imported:
                hint.setText(f"Imported d = {diameter_um:.3g} um")
            else:
                hint.setText(f"Manual/unchecked d = {diameter_um:.3g} um")
        self._refresh_density_labels()
        self._refresh_config_current_density_labels()
        self._refresh_current_density_axis()

    def _refresh_density_labels(self) -> None:
        try:
            set_mA = float(getattr(self, "current_current_set", 0.0) or 0.0) * 1000.0
        except Exception:
            set_mA = 0.0
        try:
            measured_mA = float(getattr(self, "curr_value_x", 0.0) or 0.0)
        except Exception:
            measured_mA = 0.0
        if isinstance(getattr(self, "label_live_set_density", None), QtWidgets.QLabel):
            self.label_live_set_density.setText(self._format_current_density(set_mA))
        if isinstance(getattr(self, "label_live_current_density", None), QtWidgets.QLabel):
            self.label_live_current_density.setText(self._format_current_density(measured_mA))

    def _refresh_config_current_density_labels(self, *_args: object) -> None:
        visible = self._diameter_um() is not None

        def _set(attr: str, value_mA: float, suffix: str = "") -> None:
            label = getattr(self.ui, attr, None)
            if not isinstance(label, QtWidgets.QLabel):
                return
            label.setVisible(visible)
            label.setText("" if not visible else f"{self._format_current_density(value_mA)} {CURRENT_DENSITY_UNIT}{suffix}")

        try:
            _set("label_max_current_density", float(self.ui.spinBox_max_current.value()))
        except Exception:
            pass
        try:
            _set("label_start_current_density", float(self.ui.spinBox_start_current.value()))
        except Exception:
            pass
        try:
            _set("label_step_density", float(self.ui.spinBox_step_mA.value()), "/s")
        except Exception:
            pass

    def _set_live_current_text(self, text: str) -> None:
        if isinstance(getattr(self, "label_live_current", None), QtWidgets.QLabel):
            self.label_live_current.setText(text)
        try:
            self.curr_value_x = float(str(text).replace(",", "."))
        except Exception:
            pass
        self._refresh_density_labels()

    def _set_live_set_current_text(self, text: str) -> None:
        if isinstance(getattr(self, "label_live_set", None), QtWidgets.QLabel):
            self.label_live_set.setText(text)
        self._refresh_density_labels()

    def _store_microwire_metadata_settings(self) -> None:
        try:
            self.settings.setValue("builder_project_path", self.ui.lineEdit_builder_project.text())
            self.settings.setValue("fabrication_folder_path", self.ui.lineEdit_fabrication_folder.text())
            self.settings.setValue("wire_diameter_um", self.ui.doubleSpinBox_wire_diameter_um.value())
        except Exception:
            pass

    def _restore_microwire_metadata_settings(self) -> None:
        try:
            self.ui.lineEdit_builder_project.setText(self.settings.value("builder_project_path", "", type=str))
            self.ui.lineEdit_fabrication_folder.setText(self.settings.value("fabrication_folder_path", "", type=str))
            diameter = float(self.settings.value("wire_diameter_um", 0.0) or 0.0)
            self.ui.doubleSpinBox_wire_diameter_um.setValue(max(0.0, diameter))
        except Exception:
            pass
        self._mark_metadata_diameter_imported(False)
        self._refresh_current_density_visibility()

    def _handle_diameter_changed(self, _value: float) -> None:
        self._mark_metadata_diameter_imported(False)
        self._store_microwire_metadata_settings()
        self._refresh_current_density_visibility()

    def _choose_builder_project(self) -> None:
        current = self.ui.lineEdit_builder_project.text().strip()
        start_dir = str(Path(current).parent) if current else self.ui.lineEdit_log_dir.text().strip()
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Microwire Builder project",
            start_dir,
            f"Microwire Project (*{PROJECT_EXTENSION} *.pypdj);;All files (*)",
        )
        if path_str:
            self.ui.lineEdit_builder_project.setText(path_str)
            self._import_builder_project_from_ui()

    def _choose_fabrication_folder(self) -> None:
        current = self.ui.lineEdit_fabrication_folder.text().strip()
        start_dir = current or self.ui.lineEdit_log_dir.text().strip()
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select fabrication data folder", start_dir)
        if folder:
            self.ui.lineEdit_fabrication_folder.setText(folder)
            self._load_fabrication_folder_from_ui()

    def _fabrication_load_active(self) -> bool:
        thread = self._fabrication_thread
        if thread is None:
            return False
        if thread.done_event.is_set():
            self._poll_fabrication_tasks()
            thread = self._fabrication_thread
            if thread is None:
                return False
        if thread.isRunning():
            return True
        self._poll_fabrication_tasks()
        return False

    def _set_fabrication_loading_ui(self, loading: bool) -> None:
        button = getattr(self.ui, "pushButton_load_fabrication", None)
        if isinstance(button, QtWidgets.QPushButton):
            button.setText("Cancel" if loading else "Load")
        for attr in ("pushButton_browse_fabrication_folder", "lineEdit_fabrication_folder"):
            widget = getattr(self.ui, attr, None)
            if isinstance(widget, QtWidgets.QWidget):
                widget.setEnabled(not loading)

    def _handle_fabrication_load_button(self) -> None:
        if self._fabrication_load_active():
            self._cancel_fabrication_folder_load()
            self._set_metadata_status("Cancelling fabrication folder load...")
            return
        self._load_fabrication_folder_from_ui()

    def _cancel_fabrication_folder_load(self) -> None:
        for task in list(self._fabrication_tasks.values()):
            task.cancel()

    def _retain_fabrication_task(
        self,
        task: DaemonFabricationTask,
    ) -> None:
        self._fabrication_tasks[id(task)] = task
        self._fabrication_poll_timer.start()

    def _release_fabrication_task(self, task: DaemonFabricationTask) -> None:
        self._fabrication_tasks.pop(id(task), None)

    def _wait_for_fabrication_tasks(self, timeout_ms: int = 250) -> bool:
        tasks = list(self._fabrication_tasks.values())
        if not tasks:
            return True
        deadline = time.monotonic() + max(0, int(timeout_ms)) / 1000.0
        all_finished = True
        for task in tasks:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000.0))
            if task.isRunning() and remaining_ms > 0:
                task.wait(remaining_ms)
            if task.isRunning():
                all_finished = False
            else:
                self._release_fabrication_task(task)
        return all_finished

    @QtCore.pyqtSlot()
    def _poll_fabrication_tasks(self) -> None:
        for task in list(self._fabrication_tasks.values()):
            current = self._fabrication_thread is task and not self._window_closing
            for event_name, args in task.drain_events():
                if not current:
                    continue
                if event_name == "progress_changed" and args:
                    self._set_metadata_status(str(args[0]))
                elif event_name == "succeeded" and len(args) == 3:
                    self._handle_fabrication_load_success(args[0], args[1], int(args[2]))
                elif event_name == "failed" and len(args) == 2:
                    self._handle_fabrication_load_failure(args[0], str(args[1]))
                elif event_name == "cancelled" and args:
                    self._handle_fabrication_load_cancelled(args[0])
            if task.done_event.is_set():
                self._finish_fabrication_thread(task, task.worker)
        if self._fabrication_tasks and not self._window_closing:
            self._fabrication_poll_timer.start()
        else:
            self._fabrication_poll_timer.stop()

    def _finish_fabrication_thread(
        self,
        thread: DaemonFabricationTask,
        worker: FabricationFolderLoadWorker,
    ) -> None:
        if self._fabrication_thread is thread:
            self._fabrication_thread = None
            self._fabrication_worker = None
            self._set_fabrication_loading_ui(False)
        self._release_fabrication_task(thread)

    def _handle_fabrication_load_success(self, root_obj: object, records_obj: object, file_count: int) -> None:
        root = Path(str(root_obj))
        current_text = self.ui.lineEdit_fabrication_folder.text().strip()
        if current_text and Path(current_text) != root:
            return
        records = records_obj if isinstance(records_obj, list) else []
        self._merge_metadata_records(cast(list[AnnealingSampleRecord], records))
        self._store_microwire_metadata_settings()
        self._set_metadata_status(
            f"Loaded {len(records)} microwire suggestion(s) from {file_count} fabrication workbook(s)."
            f"{self._metadata_import_status_suffix()}"
        )

    def _handle_fabrication_load_failure(self, root_obj: object, message: str) -> None:
        root = Path(str(root_obj))
        current_text = self.ui.lineEdit_fabrication_folder.text().strip()
        if current_text and Path(current_text) != root:
            return
        self._set_metadata_status(message)

    def _handle_fabrication_load_cancelled(self, root_obj: object) -> None:
        root = Path(str(root_obj))
        current_text = self.ui.lineEdit_fabrication_folder.text().strip()
        if current_text and Path(current_text) != root:
            return
        self._set_metadata_status("Fabrication folder load cancelled.")

    def _set_metadata_status(self, text: str) -> None:
        label = getattr(self.ui, "label_microwire_metadata_status", None)
        if isinstance(label, QtWidgets.QLabel):
            label.setText(text)

    def _metadata_import_status_suffix(self) -> str:
        record = self._matching_metadata_record()
        if record is None:
            return ""
        if record.diameter_um is None:
            return f" Exact match: {record.composition} {record.microwire}, but no diameter is available."
        return f" Exact match: imported d = {float(record.diameter_um):.3g} um."

    def _read_builder_project_payload(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    @classmethod
    def _records_from_project_payload(cls, payload: Any, *, source: str) -> list[AnnealingSampleRecord]:
        rows_by_section: list[list[Any]] = []
        if isinstance(payload, Mapping):
            sections = payload.get("sections", {})
            if isinstance(sections, Mapping):
                for section_name in ("microscope", "fabrication", "assemble", "current_density"):
                    section_payload = sections.get(section_name)
                    if not isinstance(section_payload, Mapping):
                        continue
                    rows = section_payload.get("rows")
                    if isinstance(rows, list):
                        rows_by_section.append(rows)
            rows = payload.get("rows")
            if isinstance(rows, list):
                rows_by_section.append(rows)
        elif isinstance(payload, list):
            rows_by_section.append(payload)
        records: list[AnnealingSampleRecord] = []
        seen: set[tuple[str, str, float | None]] = set()
        for rows in rows_by_section:
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                composition = str(row.get("Composition") or row.get("composition") or "").strip()
                microwire = str(_project_row_value(row, PROJECT_ROW_MICROWIRE_KEYS) or "").strip()
                if not composition or not microwire:
                    continue
                diameter_um = _safe_float(_project_row_value(row, PROJECT_ROW_DIAMETER_KEYS))
                if diameter_um is not None and diameter_um <= 0.0:
                    diameter_um = None
                display_wire = _display_microwire(microwire)
                key = (composition, display_wire, diameter_um)
                if key in seen:
                    continue
                seen.add(key)
                records.append(
                    AnnealingSampleRecord(
                        composition=composition,
                        microwire=display_wire,
                        diameter_um=diameter_um,
                        source=source,
                    )
                )
        return records

    @staticmethod
    def _records_from_fabrication_index(index: Any, *, source: str) -> list[AnnealingSampleRecord]:
        return _records_from_fabrication_index_payload(index, source=source)

    def _merge_metadata_records(self, records: list[AnnealingSampleRecord]) -> None:
        by_composition = dict(self._metadata_records_by_composition)
        for record in records:
            if not record.composition or not record.microwire:
                continue
            current = by_composition.setdefault(record.composition, [])
            key = _normalized_microwire_token(record.microwire)
            replaced = False
            for idx, existing in enumerate(current):
                if _normalized_microwire_token(existing.microwire) == key:
                    if existing.diameter_um is None and record.diameter_um is not None:
                        current[idx] = record
                    replaced = True
                    break
            if not replaced:
                current.append(record)
        for records_for_comp in by_composition.values():
            records_for_comp.sort(key=lambda item: (_normalized_microwire_token(item.microwire), item.microwire))
        self._metadata_records_by_composition = dict(sorted(by_composition.items(), key=lambda item: item[0].lower()))
        self._refresh_metadata_completers()
        self._apply_metadata_sample_if_possible()

    def _import_builder_project_from_ui(self) -> bool:
        path_text = self.ui.lineEdit_builder_project.text().strip()
        if not path_text:
            self._set_metadata_status("Select a .pydpj project first.")
            return False
        path = Path(path_text)
        if not path.exists():
            self._set_metadata_status("Project file was not found.")
            return False
        try:
            records = self._records_from_project_payload(self._read_builder_project_payload(path), source=path.name)
        except Exception as exc:
            self._set_metadata_status(f"Failed to import project: {exc}")
            return False
        self._merge_metadata_records(records)
        self._store_microwire_metadata_settings()
        self._set_metadata_status(
            f"Imported {len(records)} microwire suggestion(s) from {path.name}."
            f"{self._metadata_import_status_suffix()}"
        )
        return True

    def _load_fabrication_folder_from_ui(self) -> bool:
        if self._fabrication_load_active():
            self._cancel_fabrication_folder_load()
        folder_text = self.ui.lineEdit_fabrication_folder.text().strip()
        if not folder_text:
            self._set_metadata_status("Select a fabrication folder first.")
            return False
        root = Path(folder_text)
        if not root.exists() or not root.is_dir():
            self._set_metadata_status("Fabrication folder was not found.")
            return False
        worker = FabricationFolderLoadWorker(root)
        task = DaemonFabricationTask(worker)
        self._fabrication_thread = task
        self._fabrication_worker = worker
        self._retain_fabrication_task(task)
        self._set_fabrication_loading_ui(True)
        self._set_metadata_status(f"Scanning fabrication folder: {root}")
        task.start()
        return True

    def _refresh_metadata_completers(self) -> None:
        self._metadata_composition_lookup = {
            _normalized_token(composition): composition
            for composition in self._metadata_records_by_composition
            if _normalized_token(composition)
        }
        record_lookup: dict[tuple[str, str], AnnealingSampleRecord] = {}
        for composition, records in self._metadata_records_by_composition.items():
            comp_key = _normalized_token(composition)
            for record in records:
                wire_key = _normalized_microwire_token(record.microwire)
                if comp_key and wire_key:
                    record_lookup[(comp_key, wire_key)] = record
        self._metadata_record_lookup = record_lookup
        compositions = tuple(self._metadata_records_by_composition)
        if self._metadata_composition_model is None:
            self._metadata_composition_model = QtCore.QStringListModel(self.ui.lineEdit_composition)
        if self._metadata_composition_completer is None:
            completer = QtWidgets.QCompleter(self._metadata_composition_model, self.ui.lineEdit_composition)
            completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
            completer.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
            completer.activated.connect(self._handle_metadata_composition_activated)
            self._metadata_composition_completer = completer
            self.ui.lineEdit_composition.setCompleter(completer)
        if compositions != self._metadata_completer_compositions:
            self._metadata_composition_model.setStringList(list(compositions))
            self._metadata_completer_compositions = compositions
            self._metadata_microwire_completer_key = None
        self._update_metadata_microwire_completer()

    def _update_metadata_microwire_completer(self) -> None:
        composition = self._matching_metadata_composition()
        labels = tuple(record.microwire for record in self._metadata_records_by_composition.get(composition or "", []))
        key = (_normalized_token(composition), labels)
        if self._metadata_microwire_model is None:
            self._metadata_microwire_model = QtCore.QStringListModel(self.ui.lineEdit_microwire)
        if self._metadata_microwire_completer is None:
            completer = QtWidgets.QCompleter(self._metadata_microwire_model, self.ui.lineEdit_microwire)
            completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
            completer.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
            completer.activated.connect(self._handle_metadata_microwire_activated)
            self._metadata_microwire_completer = completer
            self.ui.lineEdit_microwire.setCompleter(completer)
        if key != self._metadata_microwire_completer_key:
            self._metadata_microwire_model.setStringList(list(labels))
            self._metadata_microwire_completer_key = key

    def _completion_text(self, value: object) -> str:
        if isinstance(value, QtCore.QModelIndex):
            data = value.data()
            return "" if data is None else str(data)
        return str(value or "")

    def _handle_metadata_composition_activated(self, value: object) -> None:
        text = self._completion_text(value).strip()
        if not text:
            return
        with QtCore.QSignalBlocker(self.ui.lineEdit_composition):
            self.ui.lineEdit_composition.setText(text)
        self._sync_microwire_metadata_fields()
        self.update_file_name_from_preset()

    def _handle_metadata_microwire_activated(self, value: object) -> None:
        text = _display_microwire(self._completion_text(value))
        if not text:
            return
        with QtCore.QSignalBlocker(self.ui.lineEdit_microwire):
            self.ui.lineEdit_microwire.setText(text)
        self._sync_microwire_metadata_fields()
        self.update_file_name_from_preset()

    def _normalize_microwire_field_separator(self) -> None:
        edit = getattr(self.ui, "lineEdit_microwire", None)
        if not isinstance(edit, QtWidgets.QLineEdit):
            return
        current = edit.text()
        normalized = _display_microwire(current)
        if normalized and normalized != current:
            with QtCore.QSignalBlocker(edit):
                edit.setText(normalized)
            self._sync_microwire_metadata_fields()
            self.update_file_name_from_preset()

    def _matching_metadata_composition(self) -> str | None:
        key = _normalized_token(self.ui.lineEdit_composition.text())
        return self._metadata_composition_lookup.get(key) if key else None

    def _matching_metadata_record(self) -> AnnealingSampleRecord | None:
        composition = self._matching_metadata_composition()
        if not composition:
            return None
        wire_key = _normalized_microwire_token(self.ui.lineEdit_microwire.text())
        if not wire_key:
            return None
        return self._metadata_record_lookup.get((_normalized_token(composition), wire_key))

    def _current_metadata_sample_key(self) -> tuple[str, str]:
        return (
            _normalized_token(self.ui.lineEdit_composition.text()),
            _normalized_microwire_token(self.ui.lineEdit_microwire.text()),
        )

    def _refresh_metadata_diameter_import_state(self) -> None:
        self._mark_metadata_diameter_imported(
            self._metadata_diameter_imported
            and self._metadata_diameter_import_sample_key == self._current_metadata_sample_key()
        )

    def _mark_metadata_diameter_imported(self, imported: bool) -> None:
        self._metadata_diameter_imported = bool(imported)
        self._metadata_diameter_import_sample_key = (
            self._current_metadata_sample_key() if self._metadata_diameter_imported else None
        )
        spin = getattr(self.ui, "doubleSpinBox_wire_diameter_um", None)
        if not isinstance(spin, QtWidgets.QDoubleSpinBox):
            return
        if self._metadata_diameter_imported:
            spin.setStyleSheet(
                "QDoubleSpinBox { border: 1px solid #16a34a; background-color: rgba(22, 163, 74, 0.10); }"
            )
            spin.setToolTip(
                "Wire diameter was imported for the current composition and microwire from an exact metadata match; manual edits are allowed."
            )
        else:
            spin.setStyleSheet(
                "QDoubleSpinBox { border: 1px solid #dc2626; background-color: rgba(220, 38, 38, 0.10); }"
            )
            spin.setToolTip(
                "Wire diameter is missing, manual, stale, or not verified against the current composition and microwire."
            )

    def _apply_metadata_sample_if_possible(self) -> bool:
        self._refresh_metadata_diameter_import_state()
        record = self._matching_metadata_record()
        if record is None:
            self._mark_metadata_diameter_imported(False)
            self._refresh_current_density_visibility()
            return False
        if record.diameter_um is None:
            self._mark_metadata_diameter_imported(False)
            self._refresh_current_density_visibility()
            self._set_metadata_status(f"Matched {record.composition} {record.microwire}; no diameter available.")
            return False
        try:
            with QtCore.QSignalBlocker(self.ui.doubleSpinBox_wire_diameter_um):
                self.ui.doubleSpinBox_wire_diameter_um.setValue(float(record.diameter_um))
            self._mark_metadata_diameter_imported(True)
            self._store_microwire_metadata_settings()
            self._refresh_current_density_visibility()
            self._set_metadata_status(
                f"Using d = {float(record.diameter_um):.3g} um for {record.composition} {record.microwire}."
            )
            return True
        except Exception as exc:
            self._set_metadata_status(f"Failed to apply diameter: {exc}")
            return False

    def _sync_microwire_metadata_fields(self) -> None:
        self._update_metadata_microwire_completer()
        self._apply_metadata_sample_if_possible()

    def _set_live_voltage_text(self, text: str) -> None:
        label = getattr(self, 'label_live_voltage', None)
        if not isinstance(label, QtWidgets.QLabel):
            widget = getattr(self.ui, 'label_live_voltage', None)
            if isinstance(widget, QtWidgets.QLabel):
                label = widget
        if not isinstance(label, QtWidgets.QLabel):
            widget = getattr(self.ui, 'label_live_voltage', None)
            if widget is not None:
                target = cast(Any, widget)
                setter = getattr(target, 'setText', None)
                if callable(setter):
                    setter(text)
            return
        label.setText(text)
        self._apply_voltage_label_style(label, text)

    def _apply_voltage_label_style(self, label: QtWidgets.QLabel, text: str) -> None:
        try:
            value = float(text)
        except Exception:
            value = None
        warning_style = getattr(self, '_voltage_warning_style', 'color: #c0392b;')
        default_style = getattr(self, '_voltage_default_style', '')
        if value is not None and value > 25.0:
            label.setStyleSheet(warning_style)
        else:
            label.setStyleSheet(default_style)

    def _load_measurement_history(self) -> List[Dict[str, Any]]:
        stored = self._history_settings.value("entries", "[]")
        payload: List[Any]
        if isinstance(stored, str):
            try:
                payload = json.loads(stored)
            except Exception:
                payload = []
        elif isinstance(stored, (list, tuple)):
            payload = list(stored)
        else:
            payload = []
        entries: List[Dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            currents = item.get("currents")
            resistances = item.get("resistances")
            if not isinstance(currents, list) or not isinstance(resistances, list):
                continue
            if len(currents) != len(resistances) or len(currents) < 2:
                continue
            try:
                current_vals = [float(value) for value in currents]
                resistance_vals = [float(value) for value in resistances]
            except Exception:
                continue
            entries.append(
                {
                    "currents": current_vals,
                    "resistances": resistance_vals,
                    "title": str(item.get("title", "")),
                    "timestamp": str(item.get("timestamp", "")),
                    "source": str(item.get("source", "")),
                }
            )
            if len(entries) >= MEASUREMENT_HISTORY_LIMIT:
                break
        return entries

    def _update_history_button_state(self) -> None:
        button = getattr(self.ui, 'pushButton_show_history', None)
        if isinstance(button, QtWidgets.QPushButton):
            button.setEnabled(bool(self._measurement_history))

    def _save_measurement_history(self) -> None:
        payload: List[Dict[str, Any]] = []
        for entry in self._measurement_history[:MEASUREMENT_HISTORY_LIMIT]:
            payload.append(
                {
                    "currents": [round(float(value), 6) for value in entry.get("currents", [])],
                    "resistances": [round(float(value), 6) for value in entry.get("resistances", [])],
                    "title": entry.get("title", ""),
                    "timestamp": entry.get("timestamp", ""),
                    "source": entry.get("source", ""),
                }
            )
        try:
            self._history_settings.setValue("entries", json.dumps(payload, ensure_ascii=False))
            self._history_settings.sync()
        except Exception:
            pass
        self._update_history_button_state()

    def _reset_sample_buffers(self) -> None:
        self._samples_current = []
        self._samples_resistance = []
        self._samples_voltage = []
        self._clear_segment_lines()
        self._clear_right_axis_overlay()

    def _clear_segment_lines(self) -> None:
        for container in (self._segment_lines_ax1, self._segment_lines_ax2):
            for line in list(container):
                try:
                    if getattr(self, '_plot_backend', '') == 'pyqtgraph':
                        self._remove_live_plot_item(line)
                    else:
                        line.remove()
                except Exception:
                    pass
            container.clear()

    def _clear_right_axis_overlay(self) -> None:
        if pg is None:
            return
        for plot_key, curve in list(getattr(self, "_right_axis_curves", {}).items()):
            view = self._right_axis_views.get(plot_key)
            if curve is None or view is None:
                continue
            try:
                view.removeItem(curve)
            except Exception:
                pass
        self._right_axis_curves.clear()

    def _clear_right_axis_overlay_for_plot(self, plot_key: str) -> None:
        curve = self._right_axis_curves.pop(plot_key, None)
        view = self._right_axis_views.get(plot_key)
        if curve is not None and view is not None:
            try:
                view.removeItem(curve)
            except Exception:
                pass

    def _ensure_right_axis_view(self, plot_key: str) -> Any:
        plot = self.pg_plot_resistance_vs_current if plot_key == "upper" else self.pg_plot_resistance_vs_sample
        if pg is None or plot is None:
            return None
        if plot_key in self._right_axis_views:
            return self._right_axis_views[plot_key]
        plot_item = plot.getPlotItem()
        view = pg.ViewBox()
        plot_item.scene().addItem(view)
        plot_item.getAxis("right").linkToView(view)
        view.setXLink(plot_item)

        def _sync_geometry() -> None:
            try:
                view.setGeometry(plot_item.vb.sceneBoundingRect())
                view.linkedViewChanged(plot_item.vb, view.XAxis)
            except Exception:
                pass

        try:
            plot_item.vb.sigResized.connect(_sync_geometry)
        except Exception:
            pass
        self._right_axis_views[plot_key] = view
        _sync_geometry()
        return view

    def _refresh_right_axis_overlay(self, plot_key: str = "upper") -> None:
        plot = self.pg_plot_resistance_vs_current if plot_key == "upper" else self.pg_plot_resistance_vs_sample
        if pg is None or plot is None:
            return
        plot_item = plot.getPlotItem()
        right_axis = plot_item.getAxis("right")
        mode = self._axis_mode(plot_key, "right")
        if mode == PLOT_AXIS_NONE:
            self._clear_right_axis_overlay_for_plot(plot_key)
            right_axis.setLabel("")
            right_axis.setTicks([])
            right_axis.setStyle(showValues=False, tickLength=0, maxTickLevel=0, maxTextLevel=0)
            return
        bottom_values = self._plot_series_values(self._axis_mode(plot_key, "bottom"))
        right_values = self._plot_series_values(mode)
        points = [
            (float(x_value), float(y_value))
            for x_value, y_value in zip(bottom_values, right_values)
            if math.isfinite(float(x_value)) and math.isfinite(float(y_value))
        ]
        label, units = self._plot_axis_label_units(mode)
        if not points:
            right_axis.setLabel(label, units=units)
            right_axis.setStyle(showValues=True, tickLength=4, maxTickLevel=0, maxTextLevel=0)
            return
        view = self._ensure_right_axis_view(plot_key)
        if view is None:
            return
        self._clear_right_axis_overlay_for_plot(plot_key)
        x_values = [point[0] for point in points]
        y_values = [point[1] for point in points]
        curve = pg.PlotDataItem(
            x_values,
            y_values,
            pen=pg.mkPen("#a855f7", width=2),
            symbol="o",
            symbolSize=5,
            symbolBrush=pg.mkBrush("#a855f7"),
            symbolPen=pg.mkPen("#a855f7"),
        )
        view.addItem(curve)
        self._right_axis_curves[plot_key] = curve
        y_low = min(y_values)
        y_high = max(y_values)
        padding = max((y_high - y_low) * 0.08, 0.05) if not math.isclose(y_low, y_high) else max(abs(y_low) * 0.05, 0.05)
        try:
            view.setYRange(y_low - padding, y_high + padding, padding=0.0)
        except Exception:
            pass
        right_axis.setLabel(label, units=units)
        right_axis.setStyle(showValues=True, tickLength=4, maxTickLevel=0, maxTextLevel=0)

    def _remove_placeholder_text(self) -> None:
        for label in self._pg_placeholder_labels:
            label.hide()
            label.deleteLater()
        self._pg_placeholder_labels = []
        for attr in ('_placeholder_text_ax1', '_placeholder_text_ax2'):
            text_item = getattr(self, attr, None)
            if text_item is None:
                continue
            try:
                text_item.remove()
            except Exception:
                try:
                    text_item.set_visible(False)
                except Exception:
                    pass
            setattr(self, attr, None)

    def _measurement_sample_is_plottable(self, current_mA: float, resistance: float) -> bool:
        if not math.isfinite(current_mA) or not math.isfinite(resistance):
            return False
        if current_mA < self._minimum_plottable_current_mA():
            return False
        return resistance >= MIN_PLOTTABLE_RESISTANCE_OHM

    def _append_measurement_sample(self, current_mA: float, resistance: float, voltage: float | None = None) -> None:
        if not self._measurement_sample_is_plottable(current_mA, resistance):
            return
        self._remove_placeholder_text()
        self._samples_current.append(float(current_mA))
        self._samples_resistance.append(float(resistance))
        if voltage is None:
            try:
                voltage = float(getattr(self, "current_voltage", math.nan))
            except Exception:
                voltage = math.nan
        self._samples_voltage.append(float(voltage))
        self.sample_index = len(self._samples_current)
        self._redraw_segments()

    @staticmethod
    def _cycle_color(direction: float, cycle_index: int) -> str:
        return _cycle_color(direction, cycle_index)

    def _segment_colors(self, currents: List[float]) -> List[str]:
        return _segment_colors_for_currents(
            currents,
            step_mA=float(getattr(self, 'current_step_mA', 1) or 1),
        )

    def _segment_runs(self, currents: List[float]) -> List[tuple[str, int, int]]:
        return _segment_runs_for_currents(
            currents,
            step_mA=float(getattr(self, 'current_step_mA', 1) or 1),
        )

    def _redraw_segments(self) -> None:
        if getattr(self, '_plot_backend', '') == 'pyqtgraph':
            self._redraw_pyqtgraph_segments()
            return
        ax1 = getattr(self, 'ax1', None)
        ax2 = getattr(self, 'ax2', None)
        if ax1 is None or ax2 is None:
            return
        self._clear_segment_lines()
        currents = list(self._samples_current)
        resistances = list(self._samples_resistance)
        if not currents:
            self._draw_live_canvas()
            return
        for color, start_idx, end_idx in self._segment_runs(currents):
            x_current = currents[start_idx : end_idx + 1]
            y_values = resistances[start_idx : end_idx + 1]
            x_sample = [float(index + 1) for index in range(start_idx, end_idx + 1)]
            linestyle = '-' if len(x_current) > 1 else 'None'
            marker1 = Line2D(x_current, y_values, color=color, marker='o', linestyle=linestyle)
            marker2 = Line2D(x_sample, y_values, color=color, marker='o', linestyle=linestyle)
            ax1.add_line(marker1)
            ax2.add_line(marker2)
            self._segment_lines_ax1.append(marker1)
            self._segment_lines_ax2.append(marker2)
        for axis in (ax1, ax2):
            axis.relim()
            axis.autoscale_view()
        self._draw_live_canvas()

    def _redraw_pyqtgraph_segments(self) -> None:
        self._clear_segment_lines()
        currents = list(self._samples_current)
        if not currents:
            self._refresh_pyqtgraph_ranges()
            return
        upper_x = self._plot_series_values(self._axis_mode("upper", "bottom"))
        upper_y = self._plot_series_values(self._axis_mode("upper", "left"))
        lower_x = self._plot_series_values(self._axis_mode("lower", "bottom"))
        lower_y = self._plot_series_values(self._axis_mode("lower", "left"))
        for color, start_idx, end_idx in self._segment_runs(currents):
            item1 = self._add_live_plot_item(
                self.pg_plot_resistance_vs_current,
                upper_x[start_idx : end_idx + 1],
                upper_y[start_idx : end_idx + 1],
                color,
            )
            item2 = self._add_live_plot_item(
                self.pg_plot_resistance_vs_sample,
                lower_x[start_idx : end_idx + 1],
                lower_y[start_idx : end_idx + 1],
                color,
            )
            if item1 is not None:
                self._segment_lines_ax1.append(item1)
            if item2 is not None:
                self._segment_lines_ax2.append(item2)
        self._refresh_pyqtgraph_ranges()

    def _finalize_measurement_history(self) -> None:
        if len(self._samples_current) < 2 or len(self._samples_current) != len(self._samples_resistance):
            return
        title_source = self.f_name or ""
        try:
            base_title = format_annealing_title(Path(title_source).stem if title_source else "")
        except Exception:
            base_title = format_annealing_title(title_source)
        entry = {
            "currents": list(self._samples_current),
            "resistances": list(self._samples_resistance),
            "title": base_title or "Current annealing",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": str(title_source),
        }
        self._measurement_history.insert(0, entry)
        self._measurement_history = self._measurement_history[:MEASUREMENT_HISTORY_LIMIT]
        self._save_measurement_history()

    def _reset_loop_tracking(self) -> None:
        self._loop_sample_history = []
        self._current_loop_samples = 0
        self._planned_loop_steps = 0
        self._projected_loop_samples = None
        self.step_idx = 0
        self.total_steps = 0
        self._finish_time = None

    def _init_loop_tracking(self, per_loop: int, loops: int, infinite: bool) -> None:
        self._reset_loop_tracking()
        self._planned_loop_steps = max(1, int(per_loop))
        projected = self._planned_loop_steps
        self._projected_loop_samples = projected
        if infinite:
            self.total_steps = 0
        else:
            self.total_steps = max(1, projected * max(1, int(loops)))
        self.step_idx = 0
        self._finish_time = None

    def _planned_automatic_loop_steps(self) -> int:
        """Return the nominal samples in one automatic up/down current loop."""

        try:
            start_mA = int(self.ui.spinBox_start_current.value()) if hasattr(self.ui, 'spinBox_start_current') else 1
        except Exception:
            start_mA = 1
        try:
            max_mA = int(self.ui.spinBox_max_current.value())
        except Exception:
            max_mA = int(getattr(self, "max_current_mA", start_mA))
        min_start = max(1, int(getattr(self, "min_start_current_mA", 1)))
        if max_mA < min_start:
            start_mA = max_mA
        else:
            start_mA = max(min_start, min(start_mA, max_mA))
        step_mA = max(
            self._current_resolution_mA(),
            abs(float(getattr(self, "current_step_mA", self._current_resolution_mA()) or self._current_resolution_mA())),
        ) / self._effective_hmp_command_hz()
        up_steps = max(0, math.ceil(max(0.0, float(max_mA - start_mA)) / step_mA))
        down_steps = up_steps if self._reverse_to_zero_after_max_enabled() else 0
        return max(1, int(up_steps + down_steps))

    def _refresh_running_recipe_plan(self) -> None:
        """Apply editable recipe settings to the active run and progress model."""

        if getattr(self, 'operation_mode', 2) != 2:
            return
        self.loop_target = self.ui.spinBox_loops.value() if hasattr(self.ui, 'spinBox_loops') else 1
        self.infinite_loops = (
            bool(self.ui.checkBox_infinite_loops.isChecked())
            if hasattr(self.ui, 'checkBox_infinite_loops')
            else False
        )
        per_loop = self._planned_automatic_loop_steps()
        self._planned_loop_steps = per_loop
        if not self._loop_sample_history:
            self._projected_loop_samples = per_loop
        elif not self._projected_loop_samples:
            self._projected_loop_samples = per_loop
        if self.infinite_loops:
            self.total_steps = 0
            if hasattr(self.ui, 'progressBar_process'):
                self.ui.progressBar_process.setMaximum(0)
            self._finish_time = None
        else:
            self._recalculate_total_steps(projected_loop_steps=self._projected_loop_samples or per_loop)
        if hasattr(self.ui, 'label_time_to_limit'):
            self.ui.label_time_to_limit.setText(self._format_voltage_limit_label())
        self.update_time_estimate()

    def _note_loop_sample(self) -> None:
        if getattr(self, 'operation_mode', 2) != 2:
            return
        self._current_loop_samples += 1
        if self.total_steps > 0:
            self._recalculate_total_steps()

    def _finalize_loop_cycle(self) -> None:
        if getattr(self, 'operation_mode', 2) != 2:
            return
        samples = getattr(self, '_current_loop_samples', 0)
        if samples > 0:
            self._loop_sample_history.append(samples)
        self._current_loop_samples = 0
        if self._loop_sample_history:
            avg = sum(self._loop_sample_history) / len(self._loop_sample_history)
            self._projected_loop_samples = max(1, int(math.ceil(avg)))
        elif self._planned_loop_steps > 0:
            self._projected_loop_samples = self._planned_loop_steps
        else:
            self._projected_loop_samples = None
        self._recalculate_total_steps(0)

    def _recalculate_total_steps(self, remaining_in_current_loop: int | None = None, projected_loop_steps: int | None = None) -> None:
        if getattr(self, 'operation_mode', 2) != 2:
            return
        if bool(getattr(self, 'infinite_loops', False)) and not self.total_steps:
            return
        if self.total_steps <= 0 and remaining_in_current_loop is None and projected_loop_steps is None:
            return
        target_loops = self._loop_target_count()
        completed_loops = len(self._loop_sample_history)
        loops_remaining_after_current = max(0, target_loops - completed_loops - 1)
        current_samples = max(0, self._current_loop_samples)
        projected = projected_loop_steps
        if projected is None:
            projected = self._projected_loop_samples
        if projected is None or projected <= 0:
            projected = self._planned_loop_steps if self._planned_loop_steps > 0 else current_samples
        projected = max(1, int(projected))
        if remaining_in_current_loop is None:
            remaining_current = max(0, projected - current_samples)
        else:
            remaining_current = max(0, int(remaining_in_current_loop))
        estimated_total = int(self.step_idx + remaining_current + (loops_remaining_after_current * projected))
        if estimated_total < self.step_idx:
            estimated_total = self.step_idx
        if estimated_total <= 0:
            estimated_total = self.step_idx or projected
        self.total_steps = estimated_total
        if hasattr(self.ui, 'progressBar_process'):
            self.ui.progressBar_process.setMaximum(self.total_steps if self.total_steps > 0 else 0)
            self.ui.progressBar_process.setValue(min(self.step_idx, self.total_steps))
        self._finish_time = None

    def _init_mode_menu(self, menu_bar: QtWidgets.QMenuBar) -> None:
        settings_menu = menu_bar.addMenu("&Settings")
        if settings_menu is None:
            return
        settings_menu.setObjectName("mw_current_settings")
        mode_menu = settings_menu.addMenu("Mode of operation")
        if mode_menu is None:
            return
        self._mode_menu = mode_menu
        group = QtGui.QActionGroup(settings_menu)
        group.setExclusive(True)
        self._mode_group = group
        self._mode_actions.clear()
        for mode, label in (
            (0, "Raw VCP"),
            (1, "Manual annealing"),
            (2, "Automatic annealing"),
        ):
            action = mode_menu.addAction(label)
            if action is None:
                continue
            action.setCheckable(True)
            action.setData(mode)
            group.addAction(action)
            self._mode_actions[mode] = action
        group.triggered.connect(self._handle_mode_action_triggered)
        self._sync_mode_actions(getattr(self, 'operation_mode', 2))
        self._update_mode_action_state()

    def _handle_mode_action_triggered(self, action: QtGui.QAction) -> None:
        try:
            mode = int(action.data())
        except (TypeError, ValueError):
            mode = 0
        self.handle_mode_changed(mode)

    def _sync_mode_actions(self, mode: int) -> None:
        action = self._mode_actions.get(mode)
        if action is None:
            return
        if not action.isChecked():
            action.blockSignals(True)
            action.setChecked(True)
            action.blockSignals(False)

    def _update_mode_action_state(self) -> None:
        connected = bool(getattr(self, 'is_connected', False))
        running = bool(getattr(self, 'process_running', False))
        enabled = connected and not running
        for action in self._mode_actions.values():
            action.setEnabled(enabled)

    def _record_name_history(self) -> None:
        for key, attr in (('composition', 'lineEdit_composition'), ('microwire', 'lineEdit_microwire')):
            widget = getattr(self.ui, attr, None)
            if isinstance(widget, QtWidgets.QLineEdit):
                self.name_history.remember(key, widget.text())

    def _reverse_to_zero_after_max_enabled(self) -> bool:
        return True

    def _default_plot_axis_modes(self) -> dict[str, dict[str, str]]:
        return {
            "upper": {
                "bottom": PLOT_AXIS_CURRENT_MA,
                "left": PLOT_AXIS_RESISTANCE,
                "top": PLOT_AXIS_CURRENT_DENSITY,
                "right": PLOT_AXIS_NONE,
            },
            "lower": {
                "bottom": PLOT_AXIS_SAMPLE_N,
                "left": PLOT_AXIS_RESISTANCE,
                "top": PLOT_AXIS_NONE,
                "right": PLOT_AXIS_NONE,
            },
        }

    def _load_plot_axis_modes(self) -> dict[str, dict[str, str]]:
        modes = self._default_plot_axis_modes()
        raw = self.settings.value("plot_axis_modes", "", type=str)
        if raw:
            try:
                payload = json.loads(str(raw))
                if isinstance(payload, dict):
                    for plot_key in ("upper", "lower"):
                        plot_payload = payload.get(plot_key)
                        if isinstance(plot_payload, dict):
                            for axis_key in ("bottom", "left", "top", "right"):
                                value = plot_payload.get(axis_key)
                                if isinstance(value, str):
                                    modes[plot_key][axis_key] = value
                    return modes
            except Exception:
                pass
        # Compatibility with the first configurable-axis implementation.
        legacy_top = str(self.settings.value("plot_top_axis", modes["upper"]["top"]) or modes["upper"]["top"])
        legacy_right = str(self.settings.value("plot_right_axis", modes["upper"]["right"]) or modes["upper"]["right"])
        legacy_map = {
            PLOT_TOP_AXIS_CURRENT_DENSITY: PLOT_AXIS_CURRENT_DENSITY,
            PLOT_TOP_AXIS_POWER_MW: PLOT_AXIS_POWER_MW,
            PLOT_TOP_AXIS_NONE: PLOT_AXIS_NONE,
            PLOT_RIGHT_AXIS_NONE: PLOT_AXIS_NONE,
            PLOT_RIGHT_AXIS_VOLTAGE: PLOT_AXIS_VOLTAGE,
        }
        modes["upper"]["top"] = legacy_map.get(legacy_top, legacy_top)
        modes["upper"]["right"] = legacy_map.get(legacy_right, legacy_right)
        return modes

    def _store_plot_axis_modes(self) -> None:
        try:
            self.settings.setValue("plot_axis_modes", json.dumps(self._plot_axis_modes, sort_keys=True))
            self.settings.setValue("plot_top_axis", self._plot_axis_modes["upper"].get("top", PLOT_AXIS_CURRENT_DENSITY))
            self.settings.setValue("plot_right_axis", self._plot_axis_modes["upper"].get("right", PLOT_AXIS_NONE))
        except Exception:
            pass

    def _set_port_controls_enabled(self, enabled: bool) -> None:
        for name in (
            'spinBox_port_number',
            'comboBox_baudrate',
            'comboBox_port',
            'pushButton_refresh_ports',
            'pushButton_auto_detect_hmp',
            'checkBox_show_hmp_port_options',
        ):
            w = getattr(self.ui, name, None)
            if w is not None:
                w.setEnabled(enabled)

    def _set_broker_controls_visible(self, visible: bool) -> None:
        for name in ('label_broker_host', 'lineEdit_broker_host', 'spinBox_broker_port', 'label_broker_hint'):
            widget = getattr(self.ui, name, None)
            if widget is not None:
                widget.setVisible(visible)

    def _sync_hardware_connection_controls(self) -> None:
        shared = self._using_shared_broker()
        disclosure = getattr(self.ui, "checkBox_show_hmp_port_options", None)
        if isinstance(disclosure, QtWidgets.QCheckBox):
            disclosure.setVisible(shared)
        show_hmp_port_options = not shared
        if shared and isinstance(disclosure, QtWidgets.QCheckBox):
            show_hmp_port_options = bool(disclosure.isChecked())
        endpoint_visible = shared and show_hmp_port_options
        for name in ('label_broker_host', 'lineEdit_broker_host', 'spinBox_broker_port'):
            widget = getattr(self.ui, name, None)
            if widget is not None:
                widget.setVisible(endpoint_visible)
        hint = getattr(self.ui, "label_broker_hint", None)
        if hint is not None:
            hint.setVisible(shared)
        frame = getattr(self.ui, "frame_hmp_port_options", None)
        if isinstance(frame, QtWidgets.QWidget):
            frame.setVisible(show_hmp_port_options)
        for name in (
            'label_port',
            'comboBox_port',
            'pushButton_refresh_ports',
            'pushButton_auto_detect_hmp',
            'label_baudrate',
            'comboBox_baudrate',
        ):
            widget = getattr(self.ui, name, None)
            if widget is not None:
                widget.setVisible(True)
        button = getattr(self.ui, "pushButton_connect_port", None)
        if isinstance(button, QtWidgets.QPushButton):
            if self.is_connected:
                button.setText("Disconnect broker" if shared else "Disconnect")
            else:
                button.setText("Connect broker" if shared else "Connect to port")

    def _connect_overlay_message(self) -> str:
        if self._using_shared_broker():
            return "Connect shared HMP broker to enable settings"
        return "Connect COM port to enable settings"

    def _using_shared_broker(self) -> bool:
        return str(getattr(self, "supply_profile_id", "")) == "shared_hmp_broker"

    def _shared_broker_channel(self) -> int:
        channel = int(getattr(self, "channel_select", 0) or 0)
        if channel <= 0:
            raise RuntimeError("Select a confirmed shared HMP broker channel first.")
        return channel

    def _start_preflight_errors(self, *, check_connection: bool = True) -> list[str]:
        errors: list[str] = []
        self._sync_runtime_settings()
        profile = SUPPLY_PROFILES.get(str(getattr(self, "supply_profile_id", "")), {})
        if bool(profile.get("requires_channel", False)) and int(getattr(self, "channel_select", 0) or 0) <= 0:
            errors.append("Select the physically connected PSU channel before starting.")
        if check_connection and self._using_shared_broker() and not bool(getattr(self, "is_connected", False)):
            errors.append("Connect or auto-connect the shared HMP broker before starting.")
        if check_connection and self._using_shared_broker() and bool(getattr(self, "is_connected", False)):
            try:
                snapshot = self._get_shared_broker_client().snapshot()
                self._remember_shared_broker_channel_limit(snapshot)
            except Exception as exc:
                errors.append(
                    "Could not refresh the shared HMP broker channel limits before starting: "
                    + broker_failure_diagnostic(exc, context="Current Annealing shared HMP broker")
                )
        return errors

    def _show_start_preflight_errors(self, errors: list[str]) -> None:
        if not errors:
            return
        message = "Recipe preflight failed:\n\n" + "\n".join(f"- {error}" for error in errors)
        self._show_status_message(message, timeout_ms=15000)
        try:
            QtWidgets.QMessageBox.warning(self, "Recipe preflight failed", message)
        except Exception:
            pass

    def _auto_connect_for_start(self) -> bool:
        if bool(getattr(self, "is_connected", False)):
            return True
        self._last_auto_connect_error = ""
        self._set_process_state("connecting")
        self._set_hardware_status("Connecting…", state="connecting")
        dialog: QtWidgets.QProgressDialog | None = None
        try:
            text = "Connecting shared HMP broker..." if self._using_shared_broker() else "Connecting hardware..."
            dialog = QtWidgets.QProgressDialog(text, None, 0, 0, self)
            dialog.setWindowTitle("Current Annealing hardware")
            dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
            dialog.setCancelButton(None)
            dialog.setMinimumDuration(0)
            self._hardware_auto_connect_progress = dialog
            dialog.show()
            QtWidgets.QApplication.processEvents()
        except Exception:
            self._hardware_auto_connect_progress = None
            dialog = None
        try:
            if self._using_shared_broker():
                self._connect_shared_broker_mode()
            else:
                self.handle_connect_port_clicked()
            connected = bool(getattr(self, "is_connected", False))
            if connected:
                self._set_hardware_status("Connected", state="connected")
            else:
                detail = str(self._last_auto_connect_error or "Hardware connection did not complete.")
                self._set_process_state("failed", detail)
            return connected
        except Exception as exc:
            message = f"Hardware auto-connect failed: {exc}"
            self._last_auto_connect_error = message
            self._set_hardware_status(message, state="failed")
            self._set_process_state("failed", message)
            self._show_status_message(message, timeout_ms=15000)
            try:
                QtWidgets.QMessageBox.warning(self, "Hardware auto-connect failed", message)
            except Exception:
                pass
            return False
        finally:
            if dialog is not None:
                try:
                    dialog.close()
                except Exception:
                    pass
            self._hardware_auto_connect_progress = None

    def _shared_broker_port(self) -> int:
        widget = getattr(self.ui, "spinBox_broker_port", None)
        if isinstance(widget, QtWidgets.QSpinBox):
            return int(widget.value())
        return 8765

    def _shared_broker_host(self) -> str:
        widget = getattr(self.ui, "lineEdit_broker_host", None)
        if isinstance(widget, QtWidgets.QLineEdit):
            return widget.text().strip() or "127.0.0.1"
        return "127.0.0.1"

    def _get_shared_broker_client(self) -> Any:
        if self._shared_broker_client is None:
            self._shared_broker_client = BrokerJsonClient(
                host=self._shared_broker_host(),
                port=self._shared_broker_port(),
            )
        return self._shared_broker_client

    def _shared_broker_channel_payload(
        self,
        snapshot: dict[str, Any] | None,
        channel: int,
    ) -> dict[str, Any] | None:
        if not isinstance(snapshot, dict):
            return None
        bench_profile = snapshot.get("bench_profile")
        if not isinstance(bench_profile, dict):
            return None
        channels = bench_profile.get("channels")
        if not isinstance(channels, dict):
            return None
        payload = channels.get(str(channel))
        return payload if isinstance(payload, dict) else None

    def _shared_broker_current_limit_needs_clearing(self, payload: dict[str, Any] | None) -> bool:
        return isinstance(payload, dict) and payload.get("current_limit_a") is not None

    def _ensure_shared_broker_channel_role(self, snapshot: dict[str, Any] | None = None) -> dict[str, Any] | None:
        channel = int(getattr(self, "channel_select", 0) or 0)
        if channel <= 0:
            return snapshot
        if self._shared_broker_role_checked_channel == channel:
            return snapshot
        client = self._get_shared_broker_client()
        if snapshot is None:
            snapshot = client.snapshot()
        payload = self._shared_broker_channel_payload(snapshot, channel)
        if payload is None:
            return snapshot
        configured_role = str(payload.get("role") or "unused")
        confirmed = bool(payload.get("confirmed", False))
        needs_current_limit_clear = self._shared_broker_current_limit_needs_clearing(payload)
        if configured_role == ROLE_CURRENT_ANNEALING and confirmed and not needs_current_limit_clear:
            self._shared_broker_role_checked_channel = channel
            return snapshot
        if configured_role not in {"unused", ROLE_CURRENT_ANNEALING}:
            raise RuntimeError(f"Shared HMP broker CH{channel} is assigned to {configured_role}, not current annealing.")
        request = getattr(client, "request", None)
        if not callable(request):
            return snapshot
        request(
            "assign_role",
            channel=channel,
            role=ROLE_CURRENT_ANNEALING,
            confirmed=True,
            voltage_limit_v=None,
            current_limit_a=None,
        )
        self._shared_broker_role_checked_channel = channel
        try:
            return client.snapshot()
        except Exception:
            self._shared_broker_current_limit_mA = None
            return snapshot

    def handle_broker_settings_changed(self) -> None:
        self._shared_broker_client = None
        self._shared_broker_role_checked_channel = None
        try:
            self.settings.setValue("shared_broker_host", self._shared_broker_host())
            self.settings.setValue("shared_broker_port", self._shared_broker_port())
        except Exception:
            pass

    def _requested_hmp_readback_hz(self) -> float:
        combo = getattr(self.ui, "comboBox_hmp_readback_rate", None)
        if isinstance(combo, QtWidgets.QComboBox):
            try:
                return 2.0 if float(combo.currentData()) >= 2.0 else 1.0
            except (TypeError, ValueError):
                pass
        return 1.0

    def _effective_hmp_command_hz(self) -> float:
        if self._using_shared_broker():
            return max(1.0, float(getattr(self, "_shared_broker_effective_hz", 1.0)))
        return self._requested_hmp_readback_hz()

    def _current_increment_for_direction(self, direction: float) -> float:
        magnitude = abs(float(getattr(self, "current_step_A", 0.001)))
        magnitude /= self._effective_hmp_command_hz()
        return math.copysign(magnitude, direction)

    def _set_current_ramp_direction(self, direction: float) -> None:
        self.current_increment = self._current_increment_for_direction(direction)
        self._ramp_ideal_current_A = float(getattr(self, "current_current_set", 0.0) or 0.0)

    def _advance_current_setpoint(self) -> None:
        current = float(getattr(self, "current_current_set", 0.0) or 0.0)
        ideal = float(getattr(self, "_ramp_ideal_current_A", current) or current)
        ideal += float(getattr(self, "current_increment", 0.0) or 0.0)
        self._ramp_ideal_current_A = ideal
        resolution_a = self._current_resolution_mA() / 1000.0
        quantized = round(ideal / resolution_a) * resolution_a
        self.current_current_set = max(0.0, quantized)

    def _refresh_current_increment_for_cadence(self) -> None:
        current = float(getattr(self, "current_increment", 0.0) or 0.0)
        if current:
            self.current_increment = self._current_increment_for_direction(current)

    def _update_hmp_cadence_label(self) -> None:
        label = getattr(self.ui, "label_hmp_cadence_status", None)
        if not isinstance(label, QtWidgets.QLabel):
            return
        requested = self._requested_hmp_readback_hz()
        effective = self._effective_hmp_command_hz()
        sharing = self._using_shared_broker() and effective + 1e-12 < requested
        suffix = " (shared broker capacity)" if sharing else ""
        label.setText(f"Effective PSU rate: {effective:g} Hz{suffix}")
        label.setStyleSheet("color: #b45309;" if sharing else "color: #15803d;")

    def _apply_shared_broker_cadence_status(
        self,
        status: Mapping[str, Any] | None,
        *,
        announce: bool,
    ) -> None:
        if not isinstance(status, Mapping):
            return
        polling = status.get("polling")
        if not isinstance(polling, Mapping):
            return
        try:
            effective_hz = float(polling.get("effective_hz", 1.0))
        except (TypeError, ValueError):
            return
        if effective_hz <= 0:
            return
        before_hz = float(getattr(self, "_shared_broker_effective_hz", 1.0))
        self._shared_broker_effective_hz = effective_hz
        try:
            self._shared_broker_cadence_generation = int(status.get("generation", 0))
        except (TypeError, ValueError):
            pass
        self._refresh_current_increment_for_cadence()
        if self.timer_command.isActive():
            self.timer_command.setInterval(max(1, round(1000.0 / effective_hz)))
        self._update_hmp_cadence_label()
        if announce and abs(before_hz - effective_hz) > 1e-12:
            requested = self._requested_hmp_readback_hz()
            reason = " because another broker client is active" if effective_hz < requested else ""
            message = f"Shared HMP readback changed to {effective_hz:g} Hz{reason}."
            LOGGER.info(message)
            self._show_status_message(message, timeout_ms=15000)

    def handle_hmp_readback_rate_changed(self) -> None:
        requested_hz = self._requested_hmp_readback_hz()
        try:
            self.settings.setValue("hmp_readback_hz", requested_hz)
        except Exception:
            pass
        if not self._using_shared_broker():
            self._shared_broker_effective_hz = requested_hz
        elif self.process_running and self._shared_broker_lease_id:
            try:
                status = self._get_shared_broker_client().configure_polling(
                    channel=self._shared_broker_channel(),
                    lease_id=self._shared_broker_lease_id,
                    requested_hz=requested_hz,
                )
                self._apply_shared_broker_cadence_status(status, announce=True)
            except Exception as exc:
                self._show_status_message(f"Could not change shared HMP readback rate: {exc}")
        self._refresh_current_increment_for_cadence()
        if self.timer_command.isActive():
            self.timer_command.setInterval(max(1, round(1000.0 / self._effective_hmp_command_hz())))
        self._update_hmp_cadence_label()

    def _confirm_shared_broker_cadence_start(self) -> bool:
        if not self._using_shared_broker():
            return True
        client = self._get_shared_broker_client()
        preview_polling = getattr(client, "preview_polling", None)
        if not callable(preview_polling):
            return True
        requested_hz = self._requested_hmp_readback_hz()
        preview = preview_polling(
            channel=self._shared_broker_channel(),
            requested_hz=requested_hz,
            owner=self._shared_broker_owner,
            role=ROLE_CURRENT_ANNEALING,
        )
        if not bool(preview.get("requires_confirmation")):
            return True
        candidate = preview.get("candidate") if isinstance(preview, Mapping) else None
        effective_hz = (
            float(candidate.get("effective_hz", 1.0))
            if isinstance(candidate, Mapping)
            else 1.0
        )
        downgrades = preview.get("downgrades") if isinstance(preview, Mapping) else []
        affected = ", ".join(
            f"{item.get('owner', 'another app')} CH{item.get('channel', '?')}"
            for item in downgrades
            if isinstance(item, Mapping)
        )
        detail = f" This also reduces {affected} to 1 Hz." if affected else ""
        answer = QtWidgets.QMessageBox.question(
            self,
            "Shared PSU readback rate",
            f"The requested {requested_hz:g} Hz rate will run at {effective_hz:g} Hz because the "
            f"shared HMP broker has 2 Hz total readback capacity.{detail}\n\nStart anyway?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    def _connect_shared_broker_mode(self) -> None:
        host = self._shared_broker_host()
        configured_port = self._shared_broker_port()
        candidate_ports = [configured_port]
        if configured_port != 8765:
            candidate_ports.append(8765)
        last_error: Exception | None = None
        snapshot: dict[str, Any] | None = None
        client: Any = None
        connected_port = configured_port
        if self._shared_broker_client is not None:
            try:
                client = self._shared_broker_client
                snapshot = client.snapshot()
            except Exception as exc:
                last_error = exc
                client = None
                snapshot = None
        for port in candidate_ports:
            if snapshot is not None and client is not None:
                break
            try:
                client = BrokerJsonClient(host=host, port=port)
                snapshot = client.snapshot()
                connected_port = port
                break
            except Exception as exc:
                last_error = exc
        if snapshot is None or client is None:
            if not self._auto_detect_hmp_port(show_errors=False):
                detail = (
                    ""
                    if last_error is None
                    else " Last broker error: "
                    + broker_failure_diagnostic(last_error, context="Current Annealing shared HMP broker")
                )
                raise RuntimeError(
                    "No existing shared HMP broker answered, and automatic HMP discovery did not "
                    "find a supported HMP4030/HMP4040 power supply."
                    + detail
                )
            self._start_owned_shared_broker()
            connected_port = configured_port
            client = BrokerJsonClient(host=host, port=connected_port)
            snapshot = client.snapshot()
        self._shared_broker_client = client
        if connected_port != configured_port:
            widget = getattr(self.ui, "spinBox_broker_port", None)
            if isinstance(widget, QtWidgets.QSpinBox):
                widget.blockSignals(True)
                widget.setValue(connected_port)
                widget.blockSignals(False)
            try:
                self.settings.setValue("shared_broker_port", connected_port)
            except Exception:
                pass
        profile_payload = snapshot.get("profile") if isinstance(snapshot, dict) else None
        profile_id = ""
        if isinstance(profile_payload, dict):
            profile_id = str(profile_payload.get("profile_id") or "")
        if not profile_id:
            profile_id = str(snapshot.get("model") or "")
        selected_channel = int(getattr(self, "channel_select", 0) or 0)
        if profile_id == HMP4030_PROFILE.profile_id:
            self._set_detected_hmp_profile(HMP4030_PROFILE, selected=selected_channel)
        elif profile_id == HMP4040_PROFILE.profile_id:
            self._set_detected_hmp_profile(HMP4040_PROFILE, selected=selected_channel)
        snapshot = self._ensure_shared_broker_channel_role(snapshot) or snapshot
        self._remember_shared_broker_channel_limit(snapshot)
        self.is_connected = True
        self._set_hardware_status("Connected to shared HMP broker", state="connected")
        self.ui.pushButton_connect_port.setText("Disconnect broker")
        self._set_port_controls_enabled(False)
        self.ui.frame_command_and_response.setEnabled(False)
        self.ui.frame_process_settings.setEnabled(True)
        self._show_connect_overlay(False)
        self.handle_mode_changed(self.operation_mode)
        self._update_mode_action_state()
        self._sync_hardware_connection_controls()

    def _selected_hmp_port_name(self) -> str:
        combo = getattr(self.ui, "comboBox_port", None)
        if isinstance(combo, QtWidgets.QComboBox):
            data = combo.currentData()
            if data:
                return str(data)
            text = combo.currentText().strip()
            if text:
                return text.split(" - ")[0]
        return str(getattr(self, "port_name", "") or "").strip()

    def _candidate_hmp_ports_for_broker(self, *, include_all: bool = False) -> list[str]:
        candidates: list[str] = []
        selected = self._selected_hmp_port_name()
        if selected and not include_all:
            candidates.append(selected)
        if not include_all:
            return candidates
        combo = getattr(self.ui, "comboBox_port", None)
        if isinstance(combo, QtWidgets.QComboBox):
            items: list[tuple[tuple[int, str], str]] = []
            for index in range(combo.count()):
                data = combo.itemData(index)
                text = combo.itemText(index).strip()
                value = str(data or text.split(" - ")[0]).strip()
                if value:
                    items.append(
                        (
                            hmp_port_preference_key(
                                SerialPortIdentity(device=value, description=text)
                            ),
                            value,
                        )
                    )
            for _key, value in sorted(items):
                if value not in candidates:
                    candidates.append(value)
        if selected and selected not in candidates:
            candidates.append(selected)
        return candidates

    def _probe_hmp_candidate(self, port_name: str) -> dict[str, Any] | None:
        baudrates = (115200, 9600, 57600, 38400, 19200)
        for baudrate in baudrates:
            driver = HmpSerialDriver(port_name=port_name, baudrate=baudrate, timeout_s=0.5)
            try:
                driver.connect()
                idn_text = driver.identify()
                profile = driver.profile
            except Exception:
                try:
                    driver.close()
                except Exception:
                    pass
                continue
            try:
                driver.close()
            except Exception:
                pass
            if profile is None:
                continue
            return {
                "port": port_name,
                "baudrate": baudrate,
                "profile": profile,
                "idn_text": idn_text,
            }
        return None

    def _set_current_hmp_port(self, port_name: str) -> None:
        combo = getattr(self.ui, "comboBox_port", None)
        if isinstance(combo, QtWidgets.QComboBox):
            index = combo.findData(port_name)
            if index < 0:
                for i in range(combo.count()):
                    data = str(combo.itemData(i) or "")
                    text = combo.itemText(i)
                    if data == port_name or text.startswith(port_name):
                        index = i
                        break
            if index < 0:
                combo.addItem(port_name, port_name)
                index = combo.count() - 1
            combo.setCurrentIndex(index)
        self.port_name = port_name

    def _apply_detected_hmp_match(self, match: dict[str, Any]) -> None:
        profile = match.get("profile")
        if isinstance(profile, SupplyProfile):
            self._set_detected_hmp_profile(profile, selected=int(getattr(self, "channel_select", 0) or 0))
            if not self._using_shared_broker():
                combo = getattr(self.ui, "comboBox_supply", None)
                if isinstance(combo, QtWidgets.QComboBox):
                    index = combo.findData(profile.profile_id)
                    if index >= 0:
                        combo.setCurrentIndex(index)
        self._set_current_hmp_port(str(match.get("port") or ""))
        baud_combo = getattr(self.ui, "comboBox_baudrate", None)
        if isinstance(baud_combo, QtWidgets.QComboBox):
            baud_text = str(match.get("baudrate") or "")
            if baud_combo.findText(baud_text) >= 0:
                baud_combo.setCurrentText(baud_text)
                self.baudrate = int(baud_text)

    def _nonpreferred_hmp_baud_message(self, match: dict[str, Any]) -> str:
        profile = match.get("profile")
        label = profile.label if isinstance(profile, SupplyProfile) else "HMP supply"
        preferred = int(profile.baudrate) if isinstance(profile, SupplyProfile) else 115200
        return (
            f"Detected {label} on {match.get('port')} at {match.get('baudrate')} baud, "
            f"but the preferred baud rate is {preferred}. Change the baud rate in the power supply settings "
            f"to {preferred}, then retry auto-detect/connect."
        )

    def _auto_detect_hmp_port(self, *, show_errors: bool = True) -> bool:
        errors: list[str] = []
        candidates = self._candidate_hmp_ports_for_broker(include_all=True)

        def _refresh_candidate_ports() -> list[str]:
            before = list(candidates)
            try:
                self.populate_ports()
            except Exception:
                pass
            refreshed = self._candidate_hmp_ports_for_broker(include_all=True)
            for port_name in before:
                if port_name and port_name not in refreshed:
                    refreshed.insert(0, port_name)
            return refreshed

        if not candidates:
            candidates = _refresh_candidate_ports()
        refreshed_once = not bool(candidates)

        while True:
            for port_name in candidates:
                match = self._probe_hmp_candidate(port_name)
                if match is None:
                    if port_name not in errors:
                        errors.append(port_name)
                    continue
                profile = match.get("profile")
                preferred_baud = int(profile.baudrate) if isinstance(profile, SupplyProfile) else 115200
                if int(match.get("baudrate") or 0) != preferred_baud:
                    self._set_current_hmp_port(str(match.get("port") or ""))
                    baud_combo = getattr(self.ui, "comboBox_baudrate", None)
                    if isinstance(baud_combo, QtWidgets.QComboBox):
                        baud_text = str(match.get("baudrate") or "")
                        if baud_combo.findText(baud_text) >= 0:
                            baud_combo.setCurrentText(baud_text)
                            self.baudrate = int(baud_text)
                    message = self._nonpreferred_hmp_baud_message(match)
                    self._show_status_message(message, timeout_ms=20000)
                    if show_errors:
                        try:
                            QtWidgets.QMessageBox.warning(self, "HMP baud rate", message)
                        except Exception:
                            pass
                    return False
                self._apply_detected_hmp_match(match)
                label = profile.label if isinstance(profile, SupplyProfile) else "HMP"
                self._show_status_message(
                    f"Auto-detected {label} on {match['port']} at {match['baudrate']} baud.",
                    timeout_ms=10000,
                )
                return True
            if refreshed_once:
                break
            candidates = _refresh_candidate_ports()
            refreshed_once = True
            if not candidates or all(port_name in errors for port_name in candidates):
                break
        message = "Automatic HMP detection did not find a supported HMP4030/HMP4040 power supply."
        if show_errors and errors:
            message += " Checked: " + ", ".join(errors)
        self._show_status_message(message, timeout_ms=12000)
        if show_errors:
            try:
                QtWidgets.QMessageBox.warning(self, "HMP auto-detect", message)
            except Exception:
                pass
        return False

    def handle_auto_detect_hmp_clicked(self) -> None:
        self._auto_detect_hmp_port(show_errors=True)

    def _shared_broker_current_limit_a(self) -> float:
        values: list[float] = []
        for attr in ("max_current_mA", "start_current_mA"):
            try:
                values.append(float(getattr(self, attr)))
            except Exception:
                pass
        for name in ("spinBox_max_current", "spinBox_start_current"):
            widget = getattr(self.ui, name, None)
            if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                values.append(float(widget.value()))
        return max(values or [1.0], default=1.0) / 1000.0

    def _remember_shared_broker_channel_limit(self, snapshot: dict[str, Any] | None) -> None:
        self._shared_broker_current_limit_mA = None
        if not isinstance(snapshot, dict):
            return
        try:
            channel = str(self._shared_broker_channel())
        except Exception:
            return
        bench_profile = snapshot.get("bench_profile")
        if not isinstance(bench_profile, dict):
            return
        channels = bench_profile.get("channels")
        if not isinstance(channels, dict):
            return
        payload = channels.get(channel)
        if not isinstance(payload, dict):
            return
        try:
            limit_a = payload.get("current_limit_a")
            if limit_a is not None:
                self._shared_broker_current_limit_mA = max(0.0, float(limit_a) * 1000.0)
        except Exception:
            self._shared_broker_current_limit_mA = None

    def _start_owned_shared_broker(self) -> None:
        if self._owned_shared_broker_server is not None:
            return
        channel = self._shared_broker_channel()
        host = self._shared_broker_host()
        port = self._shared_broker_port()
        baudrate = int(getattr(self, "baudrate", 115200) or 115200)
        baud_combo = getattr(self.ui, "comboBox_baudrate", None)
        if isinstance(baud_combo, QtWidgets.QComboBox):
            try:
                baudrate = int(baud_combo.currentText())
            except Exception:
                pass
        candidates = self._candidate_hmp_ports_for_broker()
        if not candidates:
            raise RuntimeError(
                "No shared HMP broker is running. Expand 'Show broker and HMP port options', "
                "select or auto-detect the HMP COM port, then connect broker."
            )

        errors: list[str] = []
        for port_name in candidates:
            driver = HmpSerialDriver(port_name=port_name, baudrate=baudrate, timeout_s=0.7)
            try:
                driver.connect()
                idn_text = driver.identify()
                if driver.profile is None:
                    raise RuntimeError(f"Unsupported shared HMP response: {idn_text}")
                broker = SharedPowerSupplyBroker(driver, driver.profile)
                broker.assign_role(
                    channel=channel,
                    role=ROLE_CURRENT_ANNEALING,
                    confirmed=True,
                    voltage_limit_v=None,
                    current_limit_a=None,
                )
                broker.confirm_profile(name="Current Annealing auto-started shared HMP broker")
                server, thread = start_broker_server(broker, host=host, port=port)
            except Exception as exc:
                errors.append(self._format_shared_broker_start_error(port_name, exc))
                try:
                    driver.close()
                except Exception:
                    pass
                continue

            self._owned_shared_broker_server = server
            self._owned_shared_broker_thread = thread
            self._owned_shared_broker_driver = driver
            self._shared_broker_current_limit_mA = None
            self.port_name = port_name
            self._show_status_message(
                f"Started shared HMP broker on {host}:{port} for {port_name}.",
                timeout_ms=10000,
            )
            return

        detail = "; ".join(errors) if errors else "no HMP ports found"
        raise RuntimeError(
            "No existing broker answered, and the selected HMP port could not start a broker "
            f"({detail})."
        )

    def _format_shared_broker_start_error(self, port_name: str, exc: Exception) -> str:
        text = str(exc).strip()
        if "Access is denied" in text or "PermissionError" in text:
            return (
                f"{port_name}: "
                + broker_failure_diagnostic(exc, context="Current Annealing shared HMP broker")
            )
        if "Unsupported shared HMP response" in text:
            return f"{port_name}: not a supported HMP4030/HMP4040 response"
        return f"{port_name}: {text or exc.__class__.__name__}"

    def _stop_owned_shared_broker(self) -> None:
        server = self._owned_shared_broker_server
        thread = self._owned_shared_broker_thread
        driver = self._owned_shared_broker_driver
        self._owned_shared_broker_server = None
        self._owned_shared_broker_thread = None
        self._owned_shared_broker_driver = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread is not None:
            try:
                thread.join(timeout=1.0)
            except Exception:
                pass
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass

    def _disconnect_shared_broker_mode(self) -> None:
        if self.process_running:
            self.handle_toggle_process_clicked()
        else:
            self.send_safe_end_commands()
        self._shared_broker_client = None
        self._shared_broker_lease_id = None
        self._shared_broker_role_checked_channel = None
        self._shared_broker_current_limit_mA = None
        self._stop_owned_shared_broker()
        self.is_connected = False
        self.ui.pushButton_connect_port.setText("Connect to broker")
        self._set_port_controls_enabled(True)
        self.ui.frame_command_and_response.setEnabled(False)
        self.ui.frame_process_settings.setEnabled(True)
        self._show_connect_overlay(False)
        self._update_mode_action_state()
        self._sync_hardware_connection_controls()

    def _ensure_shared_broker_lease(self) -> str:
        if self._shared_broker_lease_id:
            return self._shared_broker_lease_id
        channel = self._shared_broker_channel()
        self._ensure_shared_broker_channel_role()
        lease = self._get_shared_broker_client().lease(
            channel=channel,
            owner=self._shared_broker_owner,
            role=ROLE_CURRENT_ANNEALING,
        )
        lease_id = str(lease.get("lease_id") or "")
        if not lease_id:
            raise RuntimeError("Shared HMP broker did not return a lease id.")
        self._shared_broker_lease_id = lease_id
        return lease_id

    def _initialize_shared_broker_output(self) -> None:
        channel = self._shared_broker_channel()
        lease_id = self._ensure_shared_broker_lease()
        client = self._get_shared_broker_client()
        start_scheduler = getattr(client, "start_scheduler", None)
        if callable(start_scheduler):
            start_scheduler(tick_s=0.05)
        configure_polling = getattr(client, "configure_polling", None)
        if callable(configure_polling):
            status = configure_polling(
                channel=channel,
                lease_id=lease_id,
                requested_hz=self._requested_hmp_readback_hz(),
            )
            self._apply_shared_broker_cadence_status(status, announce=False)
        client.configure_channel(
            channel=channel,
            lease_id=lease_id,
            voltage_v=self._voltage_limit_value(),
            current_a=max(0.0, float(self.current_current_set)),
            output_on=True,
        )

    def _read_shared_broker_sample(self) -> bool:
        if self._read_shared_broker_sample_once():
            return True
        if self.process_running and self._recover_shared_broker_connection():
            return self._read_shared_broker_sample_once()
        return False

    def _read_shared_broker_sample_once(self) -> bool:
        channel = self._shared_broker_channel()
        client = self._get_shared_broker_client()
        readback = None
        for attempt in range(2):
            try:
                latest_readback = getattr(client, "latest_readback", None)
                if callable(latest_readback):
                    readback = latest_readback(channel=channel, max_age_s=2.5, fallback_to_measure=True)
                    cadence = readback.get("cadence") if isinstance(readback, Mapping) else None
                    self._apply_shared_broker_cadence_status(cadence, announce=True)
                else:
                    readback = client.measure_channel(channel=channel)
            except Exception:
                if attempt == 0:
                    continue
                return False
            voltage = readback.get("voltage_V")
            current_mA = readback.get("current_mA")
            if voltage is not None and current_mA is not None:
                break
        else:
            return False
        voltage = readback.get("voltage_V")
        current_mA = readback.get("current_mA")
        if voltage is None or current_mA is None:
            return False
        self.current_voltage = float(voltage)
        self.current_current_read = float(current_mA) / 1000.0
        if float(current_mA) < self._minimum_plottable_current_mA():
            self._handle_zero_current_readback()
            return True
        self._skip_current_sample = False
        self._zero_current_count = 0
        self._contact_lost = False
        self._nonzero_current_seen = True
        self.current_resistance = self.current_voltage / self.current_current_read
        self.serial_response = (
            f"broker CH{channel}: {self.current_voltage:.6g} V, "
            f"{float(current_mA):.6g} mA"
        )
        self.sample_ready = True
        return True

    def _recover_shared_broker_connection(self) -> bool:
        self._show_status_message(
            "Shared HMP broker stopped responding; trying to reconnect without stopping the annealing run.",
            timeout_ms=12000,
        )
        self._shared_broker_client = None
        self._shared_broker_lease_id = None
        self._shared_broker_role_checked_channel = None
        self._shared_broker_current_limit_mA = None
        try:
            self._connect_shared_broker_mode()
            self._initialize_shared_broker_output()
        except Exception as exc:
            self._show_status_message(
                "Shared HMP broker recovery failed: "
                + broker_failure_diagnostic(exc, context="Current Annealing shared HMP broker"),
                timeout_ms=15000,
            )
            return False
        self._show_status_message(
            f"Recovered shared HMP broker on CH{self._shared_broker_channel()}; annealing run continues.",
            timeout_ms=12000,
        )
        return True

    def _set_shared_broker_current(self) -> None:
        channel = self._shared_broker_channel()
        lease_id = self._ensure_shared_broker_lease()
        client = self._get_shared_broker_client()
        target_mA = max(0.0, float(self.current_current_set) * 1000.0)
        resolution_mA = self._current_resolution_mA()
        ramp_rate_mA_s = max(
            resolution_mA,
            abs(float(getattr(self, "current_step_mA", resolution_mA) or resolution_mA)),
        )
        schedule_current_ramp = getattr(client, "schedule_current_ramp", None)
        if callable(schedule_current_ramp):
            schedule_current_ramp(
                channel=channel,
                lease_id=lease_id,
                target_mA=target_mA,
                rate_mA_s=ramp_rate_mA_s,
                max_step_mA=resolution_mA,
                resolution_mA=resolution_mA,
            )
            return
        schedule_current = getattr(client, "schedule_current", None)
        if callable(schedule_current):
            schedule_current(
                channel=channel,
                lease_id=lease_id,
                current_mA=target_mA,
            )
            return
        client.set_current(
            channel=channel,
            lease_id=lease_id,
            current_mA=target_mA,
        )

    def _shutdown_shared_broker_output(self) -> None:
        lease_id = self._shared_broker_lease_id
        if not lease_id:
            return
        channel = self._shared_broker_channel()
        client = self._get_shared_broker_client()
        try:
            client.configure_channel(
                channel=channel,
                lease_id=lease_id,
                voltage_v=0.0,
                current_a=0.0,
                output_on=False,
            )
        except Exception:
            client.set_current(channel=channel, lease_id=lease_id, current_mA=0.0)
            client.set_output(channel=channel, lease_id=lease_id, output_on=False)
        finally:
            client.release(channel=channel, lease_id=lease_id)
            self._shared_broker_lease_id = None
            self._shared_broker_effective_hz = self._requested_hmp_readback_hz()
            self._update_hmp_cadence_label()

    def _handle_loop_value_changed(self, value: int) -> None:
        try:
            loops = max(1, int(value))
        except Exception:
            loops = 1
        self._last_loop_value = loops
        self._store_loop_preferences()
        try:
            self.update_file_name_from_preset()
        except Exception:
            pass

    def _store_loop_preferences(self) -> None:
        spin = getattr(self.ui, 'spinBox_loops', None)
        chk = getattr(self.ui, 'checkBox_infinite_loops', None)
        loops = self._last_loop_value
        if isinstance(spin, QtWidgets.QSpinBox):
            try:
                loops = max(1, int(spin.value()))
            except Exception:
                loops = self._last_loop_value
        infinite = bool(chk.isChecked()) if isinstance(chk, QtWidgets.QCheckBox) else False
        if infinite:
            loops = max(1, getattr(self, '_last_loop_value', loops))
        else:
            self._last_loop_value = loops
        try:
            self.settings.setValue('loops', max(1, loops))
            self.settings.setValue('loops_infinite', int(infinite))
        except Exception:
            pass

    def _current_loop_settings(self) -> Tuple[int, bool]:
        """Return the configured loop count and whether infinite looping is enabled."""

        loops = max(1, getattr(self, '_last_loop_value', 1))
        spin = getattr(self.ui, 'spinBox_loops', None)
        if isinstance(spin, QtWidgets.QSpinBox):
            try:
                loops = max(1, int(spin.value()))
            except Exception:
                loops = max(1, loops)
        chk = getattr(self.ui, 'checkBox_infinite_loops', None)
        infinite = bool(chk.isChecked()) if isinstance(chk, QtWidgets.QCheckBox) else False
        return loops, infinite

    def _apply_loop_suffix_to_base(self, base: str) -> str:
        """Append or update the ``loops`` suffix in ``base`` based on current settings."""

        base = base.strip()
        loops, infinite = self._current_loop_settings()
        if infinite or loops <= 1:
            return base
        suffix = f"{loops}loops"
        tokens = base.split()
        if tokens and tokens[-1].lower().endswith("loops"):
            tokens[-1] = suffix
        elif suffix not in base:
            tokens.append(suffix)
        else:
            # Already contains a loops suffix elsewhere; leave untouched.
            return base
        return " ".join(tokens)

    def _loop_target_count(self) -> int:
        """Return the configured loop target (at least 1)."""

        try:
            target = int(getattr(self, 'loop_target', 1))
        except Exception:
            target = 1
        return max(1, target)

    def _has_remaining_loops(self, next_index: int | None = None) -> bool:
        """Return ``True`` if additional loops should execute after this cycle."""

        if bool(getattr(self, 'infinite_loops', False)):
            return True
        target = self._loop_target_count()
        if next_index is None:
            try:
                completed = int(getattr(self, 'loop_idx', 0))
            except Exception:
                completed = 0
            return completed < target
        return next_index < target

    @staticmethod
    def _format_sample_value(value: float) -> str:
        text = format(float(value), ".12g")
        # Normalise "-0" artefacts from floating point conversion.
        return "0" if text == "-0" else text

    def _write_sample_to_file(self, *, initial_sample: bool) -> bool:
        """Persist the latest sample to disk if appropriate."""

        if initial_sample or not self.f_name:
            return True
        current_mA = float(self.current_current_read) * 1000.0
        voltage = float(self.current_voltage)
        resistance = float(self.current_resistance)
        if not self._measurement_sample_is_plottable(current_mA, resistance):
            return True
        if not self.f_out:
            try:
                Path(self.f_name).parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return self._handle_measurement_write_failure(exc)
            try:
                self.f_out = open(self.f_name, "a", encoding="utf-8")
            except OSError as exc:
                self.f_out = None
                return self._handle_measurement_write_failure(exc)
        if self.f_out:
            line = "\t".join(
                [
                    self._format_sample_value(current_mA),
                    self._format_sample_value(voltage),
                    self._format_sample_value(resistance),
                ]
            ) + "\n"
            try:
                self.f_out.write(line)
                self.f_out.flush()
                self.f_out.close()
            except OSError as exc:
                try:
                    self.f_out.close()
                except OSError:
                    pass
                self.f_out = None
                return self._handle_measurement_write_failure(exc)
            self.f_out = None
            return True
        return self._handle_measurement_write_failure(OSError("output file is unavailable"))

    def _handle_measurement_write_failure(self, exc: OSError) -> bool:
        path = str(self.f_name or "the measurement file")
        message = f"Measurement file write failed for {path}: {exc}"
        LOGGER.error(message)
        self._last_run_error = message
        if self.process_running:
            self.stop_annealing(message, show_dialog=False, final_state="failed")
        else:
            self._set_process_state("failed", message)
            self._show_status_message(message, timeout_ms=0)
        try:
            QtWidgets.QMessageBox.critical(self, "Measurement not saved", message)
        except Exception:
            pass
        return False

    def _record_sample_progress(self) -> None:
        """Update progress/rate counters for a persisted non-initial sample."""

        now = time.perf_counter()
        if self.last_sample_time is not None:
            dt = now - self.last_sample_time
            if dt > 0:
                rate = 1.0 / dt
                self._rate_window.append(rate)
                self.sample_rate = sum(self._rate_window) / len(self._rate_window)
                if self.total_steps:
                    remaining = max(0, self.total_steps - self.step_idx)
                    self._finish_time = now + (remaining / self.sample_rate) if self.sample_rate else None
        self.last_sample_time = now
        self.step_idx += 1
        self._note_loop_sample()
        if hasattr(self.ui, 'progressBar_process') and self.total_steps:
            self.ui.progressBar_process.setMaximum(self.total_steps)
            self.ui.progressBar_process.setValue(min(self.step_idx, self.total_steps))

    def _first_sample_current_matches_setpoint(self, current_mA: float) -> bool:
        if not bool(getattr(self, "first_sample", False)):
            return True
        try:
            target_mA = max(0.0, float(getattr(self, "current_current_set", 0.0) or 0.0) * 1000.0)
        except Exception:
            return True
        if not math.isfinite(target_mA) or target_mA <= 0.0:
            return True
        try:
            resolution_mA = float(self._current_resolution_mA())
        except Exception:
            resolution_mA = 0.2
        try:
            step_mA = abs(float(getattr(self, "current_step_mA", resolution_mA) or resolution_mA))
        except Exception:
            step_mA = resolution_mA
        tolerance_mA = max(2.0 * resolution_mA, 2.0 * step_mA, 0.25 * target_mA)
        return abs(float(current_mA) - target_mA) <= tolerance_mA

    def _record_acquired_sample(self, *, record_voltage_progress: bool = False) -> bool:
        """Write, plot, and account for the latest accepted measurement once."""

        if self._skip_current_sample:
            return False
        try:
            current_mA = float(self.curr_value_x)
            resistance = float(self.curr_value_y)
        except Exception:
            return False
        if not self._measurement_sample_is_plottable(current_mA, resistance):
            return False
        if not self._first_sample_current_matches_setpoint(current_mA):
            return False
        initial_sample = self.first_sample
        if not self._write_sample_to_file(initial_sample=initial_sample):
            return False
        if self.first_sample:
            self.first_sample = False
        self._append_measurement_sample(current_mA, resistance, float(getattr(self, "current_voltage", math.nan)))
        if not initial_sample:
            self._record_sample_progress()
        if record_voltage_progress:
            self._record_voltage_progress()
        return True

    def _accept_measurement_sample(self, *, record_voltage_progress: bool = False) -> bool:
        return self._record_acquired_sample(record_voltage_progress=record_voltage_progress)

    def handle_checkBox_infinite_loops_toggled(self, checked: bool) -> None:
        spin = getattr(self.ui, 'spinBox_loops', None)
        if isinstance(spin, QtWidgets.QSpinBox):
            if checked:
                try:
                    self._last_loop_value = max(1, spin.value())
                except Exception:
                    self._last_loop_value = max(1, getattr(self, '_last_loop_value', 1))
                spin.setValue(0)
                spin.setEnabled(False)
            else:
                restored = max(1, getattr(self, '_last_loop_value', 1))
                if spin.value() == 0:
                    spin.setValue(restored)
                spin.setEnabled(True)
        self._store_loop_preferences()
        self.update_planned_time_label()
        try:
            self.update_file_name_from_preset()
        except Exception:
            pass

    # Connect signals and slots
    def handle_connect_port_clicked(self):
        if self._using_shared_broker():
            if not self.is_connected:
                try:
                    self._connect_shared_broker_mode()
                except Exception as exc:
                    self.is_connected = False
                    self._shared_broker_client = None
                    message = f"Shared HMP broker connection failed: {exc}"
                    self._show_status_message(message, timeout_ms=15000)
                    try:
                        QtWidgets.QMessageBox.warning(self, "Shared HMP broker", message)
                    except Exception:
                        pass
            else:
                self._disconnect_shared_broker_mode()
            return
        if not self.is_connected:
            # Use selected port name from dropdown if available
            port_name = ''
            if hasattr(self, 'port_name') and self.port_name:
                port_name = self.port_name
            else:
                try:
                    port_name = 'COM' + str(self.port_number)
                except Exception:
                    port_name = ''
            if not port_name:
                QtWidgets.QMessageBox.warning(self, "No port", "Please select a serial port")
                return
            try:
                import os as _os
                name = _os.path.basename(port_name) if '/' in port_name else port_name
            except Exception:
                name = port_name
            self.ser_mcu.setPortName(name)
            self.ser_mcu.setBaudRate(self.baudrate)
            self.ser_mcu.setFlowControl(QtSerialPort.QSerialPort.FlowControl.NoFlowControl)
            self.ser_mcu.setDataBits(QtSerialPort.QSerialPort.DataBits.Data8)
            self.ser_mcu.setParity(QtSerialPort.QSerialPort.Parity.NoParity)
            self.ser_mcu.setStopBits(QtSerialPort.QSerialPort.StopBits.OneStop)
            
            
            if self.ser_mcu.open(QtCore.QIODeviceBase.OpenModeFlag.ReadWrite):
                self.ser_mcu.clear()
                self.ser_mcu.readyRead.connect(
                    self.handle_ser_mcu_readyRead,
                    QtCore.Qt.ConnectionType.QueuedConnection,
                )
                self.is_connected = True
                self.ui.pushButton_connect_port.setText('Disconnect')
                self._set_port_controls_enabled(False)
                self.ui.frame_command_and_response.setEnabled(True)
                # Respect the selected mode rather than forcing raw VCP
                try:
                    self.handle_mode_changed(self.operation_mode)
                except Exception:
                    self.handle_raw_vcp_mode_selected()
                self._update_mode_action_state()
                self._show_connect_overlay(False)
                self._set_hardware_status(f"Connected to {name}", state="connected")
            else:
                detail = str(self.ser_mcu.errorString() or "Unknown serial-port error")
                message = f"Could not open {name}: {detail}"
                LOGGER.error(message)
                self.is_connected = False
                self._last_auto_connect_error = message
                self._set_hardware_status(message, state="failed")
                self._show_status_message(message, timeout_ms=0)
                try:
                    QtWidgets.QMessageBox.warning(self, "Serial port open failed", message)
                except Exception:
                    pass

        else:
            if self.process_running:
                self.handle_toggle_process_clicked()
            else:
                self.send_safe_end_commands()
            # Proactively disconnect signal-slot before closing the port
            self._disconnect_serial_ready_read()
            self.ser_mcu.close()
            self.is_connected = False
            self._set_hardware_status("Disconnected", state="idle")
            self.ui.pushButton_connect_port.setText('Connect to port')
            self._show_connect_overlay(False)
            self.ui.frame_command_and_response.setEnabled(False)
            self.ui.frame_process_settings.setEnabled(True)
            self._set_port_controls_enabled(True)
            self._update_mode_action_state()

    def handle_port_number_value_changed(self):
        self.port_number = self.ui.spinBox_port_number.value()
            
    def handle_comboBox_baudrate_currentIndexChanged(self):
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())

    def handle_comboBox_port_changed(self):
        """Update selected port name from the dropdown."""
        try:
            data = self.ui.comboBox_port.currentData()
            if data:
                self.port_name = str(data)
            else:
                # fallback to the text
                text = self.ui.comboBox_port.currentText()
                self.port_name = text.split(" - ")[0]
        except Exception:
            pass

    def _disconnect_serial_ready_read(self) -> None:
        try:
            self.ser_mcu.readyRead.disconnect(self.handle_ser_mcu_readyRead)
        except (AttributeError, RuntimeError, TypeError):
            pass

    @QtCore.pyqtSlot()
    def handle_ser_mcu_readyRead(self):
        if self._window_closing or not self.is_connected:
            return
        if self.ser_mcu.canReadLine():
            self.lock.lock()
            try:
                raw_line = self.ser_mcu.readLine()
                raw_bytes = bytes(cast(SupportsBytes, raw_line))
                self.serial_response = raw_bytes.decode('ascii', errors='ignore')
            except Exception:
                self.serial_response = str(self.ser_mcu.readLine())
            try:
                self._last_serial_rx = time.monotonic()
            except Exception:
                self._last_serial_rx = None
            # reduce console spam
            if self.operation_mode > 0 and self.process_running:
                if self.expecting_voltage:
                    try:
                        self.current_voltage = float(self.serial_response.strip())
                        label_live_voltage = getattr(self.ui, 'label_live_voltage', None)
                        if label_live_voltage is not None:
                            cast(Any, label_live_voltage).display(f"{self.current_voltage:.2f}")
                    except ValueError:
                        # Ignore non-numeric responses (e.g., from config commands)
                        self.lock.unlock()
                        return
                else:
                    try:
                        self.current_current_read = float(self.serial_response.strip())
                    except ValueError:
                        self.lock.unlock()
                        return
                    if self.current_current_read * 1000.0 < self._minimum_plottable_current_mA():
                        self._handle_zero_current_readback()
                        self.lock.unlock()
                        return
                    self._skip_current_sample = False
                    self._zero_current_count = 0
                    self._contact_lost = False
                    self._nonzero_current_seen = True
                    if self._zero_placeholders_active:
                        self._clear_zero_placeholders()
                    try:
                        self._last_nonzero_current_time = time.monotonic()
                    except Exception:
                        self._last_nonzero_current_time = None
                    try:
                        self.current_resistance = self.current_voltage / self.current_current_read
                    except ZeroDivisionError:
                        self.lock.unlock()
                        return
                if (
                    self.current_increment > 0
                    and self.current_voltage >= self.max_voltage
                    and not self._max_voltage_dialog
                ):
                    self.handle_max_voltage()
                self.sample_ready = True
            self.lock.unlock()
                    
    @QtCore.pyqtSlot()
    def handle_update_serial_response_label(self):
        if self._window_closing:
            return
        self.ui.label_serial_response.setText(self.serial_response)

    def _reset_voltage_projection(self) -> None:
        """Clear the running voltage-limit projection state."""

        self._voltage_history.clear()
        self._time_to_voltage_limit = None
        self._estimated_limit_current_mA = None
        self._applied_limit_current_mA = None
        self._apply_voltage_projection_to_progress(None, force=True)

    def _note_voltage_limit_reached(self) -> None:
        """Record that the voltage ceiling has been hit."""

        self._voltage_history.clear()
        self._time_to_voltage_limit = 0.0
        current_mA = getattr(self, "curr_value_x", None)
        if current_mA is None:
            current_mA = self.current_current_set * 1000.0
        try:
            current_mA = float(current_mA)
        except Exception:
            current_mA = 0.0
        self._estimated_limit_current_mA = max(0.0, current_mA)
        self._apply_voltage_projection_to_progress(self._estimated_limit_current_mA, force=True)

    def _update_voltage_projection(self, timestamp: float) -> None:
        """Update the projection to the voltage limit using recent samples."""

        history = self._voltage_history
        limit = float(getattr(self, "max_voltage", 30.0))
        while history and (timestamp - history[0][0]) > 20.0:
            history.popleft()
        if len(history) < 2:
            self._time_to_voltage_limit = None
            self._estimated_limit_current_mA = None
            return
        start_t, start_v, start_i = history[0]
        end_t, end_v, end_i = history[-1]
        dt = end_t - start_t
        dv = end_v - start_v
        if dt <= 0 or dv <= 1e-6:
            self._time_to_voltage_limit = None
            self._estimated_limit_current_mA = None
            return
        remaining_v = limit - float(self.current_voltage)
        if remaining_v <= 0:
            self._note_voltage_limit_reached()
            return
        rate_v_per_s = dv / dt
        time_est = remaining_v / rate_v_per_s if rate_v_per_s > 1e-6 else None
        di = end_i - start_i
        current_est = None
        if di > 1e-6:
            slope_v_per_mA = dv / di
            if slope_v_per_mA > 1e-6:
                current_est = end_i + remaining_v / slope_v_per_mA
                if current_est < 0:
                    current_est = None
        if time_est is not None and time_est < 0:
            time_est = 0.0
        self._time_to_voltage_limit = time_est
        self._estimated_limit_current_mA = current_est
        self._apply_voltage_projection_to_progress(current_est)

    def _apply_voltage_projection_to_progress(self, limit_mA: float | None, *, force: bool = False) -> None:
        if getattr(self, 'operation_mode', 2) != 2:
            return
        if bool(getattr(self, 'infinite_loops', False)):
            if limit_mA is None:
                self._applied_limit_current_mA = None
            return
        if not self.process_running:
            if limit_mA is None:
                self._applied_limit_current_mA = None
            return
        if not self.direction_ascending or self.current_increment <= 0:
            if limit_mA is None and self._applied_limit_current_mA is not None:
                self._applied_limit_current_mA = None
                projected = self._planned_loop_steps or self._projected_loop_samples
                if projected:
                    self._projected_loop_samples = int(projected)
                    self._recalculate_total_steps(projected_loop_steps=self._projected_loop_samples)
                    self.update_time_estimate()
            return
        try:
            planned_max = float(self.ui.spinBox_max_current.value())
        except Exception:
            planned_max = float(getattr(self, 'max_current_mA', 0))
        step_mA = (
            abs(float(getattr(self, 'current_step_mA', self._current_resolution_mA()) or self._current_resolution_mA()))
            / self._effective_hmp_command_hz()
        )
        tolerance = step_mA * 0.5
        if limit_mA is None:
            if self._applied_limit_current_mA is not None or force:
                self._applied_limit_current_mA = None
                projected = self._planned_loop_steps or self._projected_loop_samples
                if projected:
                    self._projected_loop_samples = int(projected)
                    self._recalculate_total_steps(projected_loop_steps=self._projected_loop_samples)
                    self.update_time_estimate()
            return
        limit_value = max(0.0, float(limit_mA))
        if not force and self._applied_limit_current_mA is not None and abs(self._applied_limit_current_mA - limit_value) <= tolerance:
            return
        if limit_value >= planned_max - tolerance:
            if self._applied_limit_current_mA is not None or force:
                self._applied_limit_current_mA = None
                projected = self._planned_loop_steps or self._projected_loop_samples
                if projected:
                    self._projected_loop_samples = int(projected)
                    self._recalculate_total_steps(projected_loop_steps=self._projected_loop_samples)
                    self.update_time_estimate()
            return
        ascend_steps = max(1, int(math.ceil(max(0.0, limit_value - 1.0) / step_mA)))
        hold_steps = 0
        reverse_steps = ascend_steps if self._reverse_to_zero_after_max_enabled() else 0
        projected_loop = max(1, ascend_steps + hold_steps + reverse_steps)
        self._projected_loop_samples = projected_loop
        self._applied_limit_current_mA = limit_value
        self._recalculate_total_steps(projected_loop_steps=projected_loop)
        self.update_time_estimate()

    def _record_voltage_progress(self) -> None:
        if not self.process_running:
            return
        if not self.direction_ascending or self.current_increment <= 0:
            return
        try:
            now = time.perf_counter()
        except Exception:
            now = None
        if now is None:
            return
        try:
            current_mA = float(self.curr_value_x)
        except Exception:
            current_mA = 0.0
        self._voltage_history.append((now, float(self.current_voltage), current_mA))
        self._update_voltage_projection(now)

    def _format_voltage_limit_label(self) -> str:
        prefix = self._voltage_limit_prefix()
        if not self.process_running:
            return f"{prefix}: N/A"
        if self._time_to_voltage_limit == 0:
            if self._estimated_limit_current_mA is not None:
                return f"{prefix}: reached (≈ {self._estimated_limit_current_mA:.0f} mA)"
            return f"{prefix}: reached"
        if not self.direction_ascending or self.current_increment <= 0:
            return f"{prefix}: N/A"
        if self._time_to_voltage_limit is None:
            return f"{prefix}: N/A"
        secs = max(0, int(self._time_to_voltage_limit + 0.999))
        text = self._format_secs(prefix, secs)
        if self._estimated_limit_current_mA is not None:
            text += f" (≈ {self._estimated_limit_current_mA:.0f} mA)"
        return text

    @QtCore.pyqtSlot()
    def update_time_estimate(self):
        if self._window_closing:
            return
        label = getattr(self.ui, 'label_time_remaining', None)
        limit_label = getattr(self.ui, 'label_time_to_limit', None)
        if label is None:
            if limit_label is not None:
                limit_label.setText(self._format_voltage_limit_label())
            return
        # Show a planned estimate when idle; measured when running
        if not self.process_running:
            secs = self.compute_planned_seconds()
            if secs is None:
                label.setText("Time remaining: ∞")
            else:
                label.setText(self._format_secs("Time remaining", secs))
            if limit_label is not None:
                limit_label.setText(self._format_voltage_limit_label())
            return
        now = time.perf_counter()
        if self._finish_time is not None:
            secs = max(0, int(self._finish_time - now + 0.999))
        else:
            if not self.sample_rate or not self.total_steps:
                label.setText("Time remaining: N/A")
                if limit_label is not None:
                    limit_label.setText(self._format_voltage_limit_label())
                return
            remaining = max(0, self.total_steps - self.step_idx)
            secs = int((remaining / self.sample_rate) + 0.999)
        label.setText(self._format_secs("Time remaining", secs))
        if limit_label is not None:
            limit_label.setText(self._format_voltage_limit_label())

    def _format_secs(self, prefix: str, secs: int) -> str:
        if secs >= 3600:
            h = secs // 3600
            m = (secs % 3600) // 60
            s = secs % 60
            return f"{prefix}: {h}h {m:02d}m {s:02d}s"
        elif secs >= 60:
            m = secs // 60
            s = secs % 60
            return f"{prefix}: {m}m {s:02d}s"
        else:
            return f"{prefix}: {secs}s"

    def _profile_setting_key(self, profile_id: str, name: str) -> str:
        return f"supply_profile/{profile_id}/{name}"

    def _load_profile_setting(self, profile_id: str, name: str, default: Any, value_type: type) -> Any:
        key = self._profile_setting_key(profile_id, name)
        try:
            if self.settings.contains(key):
                return self.settings.value(key, default, type=value_type)
            return self.settings.value(name, default, type=value_type)
        except Exception:
            return default

    def _store_profile_setting(self, name: str, value: Any) -> None:
        profile_id = getattr(self, "supply_profile_id", "hmp4030")
        try:
            self.settings.setValue(self._profile_setting_key(profile_id, name), value)
        except Exception:
            pass

    def _load_profile_int(self, profile_id: str, name: str, default: int, minimum: int = 0) -> int:
        try:
            value = int(self._load_profile_setting(profile_id, name, default, int))
        except Exception:
            value = int(default)
        return max(int(minimum), value)

    def _init_supply_profile(self) -> None:
        combo = getattr(self.ui, 'comboBox_supply', None)
        if not isinstance(combo, QtWidgets.QComboBox):
            return
        combo.blockSignals(True)
        combo.clear()
        for key, profile in SUPPLY_PROFILES.items():
            combo.addItem(profile["label"], key)
        stored = self.settings.value("supply_profile", "shared_hmp_broker")
        idx = combo.findData(stored)
        if idx < 0:
            idx = combo.findData("shared_hmp_broker")
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        selected = combo.currentData()
        if isinstance(selected, str):
            self._apply_supply_profile(selected)

    def _hmp_profile_for_supply_profile(self, profile_id: str) -> SupplyProfile | None:
        profile = SUPPLY_PROFILES.get(profile_id, {})
        profile_key = str(profile.get("hmp_profile_id") or "")
        if profile_key == HMP4030_PROFILE.profile_id:
            return HMP4030_PROFILE
        if profile_key == HMP4040_PROFILE.profile_id:
            return HMP4040_PROFILE
        if bool(profile.get("shared_broker")):
            return HMP4040_PROFILE
        return None

    def _current_resolution_mA(self) -> float:
        profile = SUPPLY_PROFILES.get(str(getattr(self, "supply_profile_id", "")), {})
        try:
            return max(0.2, float(profile.get("current_resolution_mA", 0.2) or 0.2))
        except Exception:
            return 0.2

    def _min_positive_current_mA(self) -> float:
        profile = SUPPLY_PROFILES.get(str(getattr(self, "supply_profile_id", "")), {})
        fallback = profile.get("min_start_current_mA", 1.0)
        try:
            return max(0.0, float(profile.get("min_current_mA", fallback) or 0.0))
        except Exception:
            return 0.0

    def _minimum_plottable_current_mA(self) -> float:
        try:
            start_current = float(getattr(self, "start_current_mA", self._min_positive_current_mA()) or 0.0)
        except Exception:
            start_current = self._min_positive_current_mA()
        return max(self._min_positive_current_mA(), start_current)

    def _quantize_current_ramp_mA_s(self, value: float) -> float:
        resolution = self._current_resolution_mA()
        try:
            raw_value = float(value)
        except Exception:
            raw_value = resolution
        steps = max(1, round(raw_value / resolution))
        return steps * resolution

    def _populate_channel_options(self, channel_count: int, *, selected: int = 0) -> None:
        combo = getattr(self.ui, 'comboBox_channel', None)
        if not isinstance(combo, QtWidgets.QComboBox):
            return
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Select channel...", None)
        for channel in range(1, max(0, int(channel_count)) + 1):
            combo.addItem(f"CH{channel}", channel)
        index = combo.findData(int(selected)) if int(selected or 0) > 0 else 0
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)
        data = combo.currentData()
        self.channel_select = int(data) if data is not None else 0
        legacy_spin = getattr(self.ui, 'spinBox_channel', None)
        if isinstance(legacy_spin, QtWidgets.QSpinBox):
            legacy_spin.blockSignals(True)
            legacy_spin.setValue(self.channel_select)
            legacy_spin.blockSignals(False)

    def _set_detected_hmp_profile(self, profile: SupplyProfile, *, selected: int | None = None) -> None:
        self._detected_hmp_profile = profile
        volt_spin = getattr(self.ui, 'spinBox_max_voltage', None)
        if isinstance(volt_spin, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            volt_spin.blockSignals(True)
            if isinstance(volt_spin, QtWidgets.QDoubleSpinBox):
                volt_spin.setValue(float(profile.max_voltage_v))
            else:
                volt_spin.setValue(int(round(float(profile.max_voltage_v))))
            volt_spin.blockSignals(False)
            self.max_voltage = float(volt_spin.value())
            self.open_threshold = self.max_voltage
        if selected is None:
            selected = int(getattr(self, "channel_select", 0) or 0)
        if int(selected or 0) < 1 or int(selected or 0) > profile.channel_count:
            selected = 0
        self._populate_channel_options(profile.channel_count, selected=selected)

    def _apply_supply_profile(self, profile_id: str) -> None:
        profile = SUPPLY_PROFILES.get(profile_id, SUPPLY_PROFILES["hmp4030"])
        self.supply_profile_id = profile_id
        self.min_start_current_mA = int(profile.get("min_start_current_mA", 1))
        self.voltage_first = bool(profile.get("voltage_first", False))
        # Apply profile-specific defaults to UI and internal state.
        start_spin = getattr(self.ui, 'spinBox_start_current', None)
        if isinstance(start_spin, QtWidgets.QSpinBox):
            try:
                start_spin.setMinimum(self.min_start_current_mA)
            except Exception:
                pass
            default_start = int(profile.get("start_current_mA", self.min_start_current_mA))
            start_value = self._load_profile_setting(profile_id, "start_current", default_start, int)
            if start_value < self.min_start_current_mA:
                start_value = self.min_start_current_mA
            start_spin.blockSignals(True)
            start_spin.setValue(int(start_value))
            start_spin.blockSignals(False)
            self.start_current_mA = int(start_spin.value())
        max_spin = getattr(self.ui, 'spinBox_max_current', None)
        if isinstance(max_spin, QtWidgets.QSpinBox):
            default_max = self._load_profile_int(profile_id, "max_current", 10, self.min_start_current_mA)
            max_value = self._load_profile_int(
                profile_id,
                "max_current",
                default_max,
                self.min_start_current_mA,
            )
            if max_value < int(getattr(self, "start_current_mA", self.min_start_current_mA)):
                max_value = int(getattr(self, "start_current_mA", self.min_start_current_mA))
            max_spin.blockSignals(True)
            max_spin.setValue(max_value)
            max_spin.blockSignals(False)
            self.max_current_mA = int(max_spin.value())
            try:
                self.settings.setValue("max_current", self.max_current_mA)
            except Exception:
                pass
        step_spin = getattr(self.ui, 'spinBox_step_mA', None)
        if isinstance(step_spin, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            default_step = max(0.2, float(step_spin.value()))
            step_value = float(self._load_profile_setting(profile_id, "step_mA", default_step, float))
            step_value = self._quantize_current_ramp_mA_s(step_value)
            step_spin.blockSignals(True)
            step_spin.setValue(step_value)
            step_spin.blockSignals(False)
            self.current_step_mA = float(step_spin.value())
            self.current_step_A = self.current_step_mA / 1000.0
        volt_spin = getattr(self.ui, 'spinBox_max_voltage', None)
        if isinstance(volt_spin, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            default_voltage = float(profile.get("max_voltage", 30.0))
            voltage_value = self._load_profile_setting(profile_id, "max_voltage", default_voltage, float)
            volt_spin.blockSignals(True)
            if isinstance(volt_spin, QtWidgets.QDoubleSpinBox):
                volt_spin.setValue(float(voltage_value))
            else:
                volt_spin.setValue(int(round(float(voltage_value))))
            volt_spin.blockSignals(False)
            self.max_voltage = float(volt_spin.value())
            self.open_threshold = self.max_voltage
        reset_box = getattr(self.ui, 'checkBox_reset_on_start', None)
        if isinstance(reset_box, QtWidgets.QCheckBox):
            default_reset = int(bool(profile.get("reset_on_start", True)))
            reset_value = int(self._load_profile_setting(profile_id, "reset_on_start", default_reset, int))
            reset_box.blockSignals(True)
            reset_box.setChecked(bool(reset_value))
            reset_box.blockSignals(False)
            self.reset_on_start = bool(reset_box.isChecked())
        hmp_profile = self._hmp_profile_for_supply_profile(profile_id)
        if hmp_profile is not None:
            default_channel = int(profile.get("channel_select", 0) or 0)
            selected_channel = self._load_profile_int(profile_id, "channel_select", default_channel, 0)
            self._set_detected_hmp_profile(hmp_profile, selected=selected_channel)
        else:
            self._populate_channel_options(0, selected=0)
        try:
            self._apply_profile_max_voltage_action(profile_id)
        except Exception:
            pass
        try:
            self.settings.setValue("supply_profile", profile_id)
        except Exception:
            pass
        shared = self._using_shared_broker()
        if isinstance(reset_box, QtWidgets.QCheckBox):
            reset_box.setVisible(not shared)
        self._set_broker_controls_visible(shared)
        for name in (
            "lineEdit_serial_command",
            "pushButton_send_serial_command",
        ):
            widget = getattr(self.ui, name, None)
            if widget is not None:
                widget.setEnabled(not shared)
        for name in ("comboBox_port", "comboBox_baudrate", "pushButton_refresh_ports", "pushButton_auto_detect_hmp"):
            widget = getattr(self.ui, name, None)
            if widget is not None:
                widget.setEnabled(True)
        if hasattr(self.ui, "lineEdit_broker_host"):
            self.ui.lineEdit_broker_host.setText(
                self.settings.value("shared_broker_host", "127.0.0.1", type=str)
            )
        if hasattr(self.ui, "spinBox_broker_port"):
            self.ui.spinBox_broker_port.blockSignals(True)
            self.ui.spinBox_broker_port.setValue(int(self.settings.value("shared_broker_port", 8765, type=int)))
            self.ui.spinBox_broker_port.blockSignals(False)
        if not shared:
            self._shared_broker_lease_id = None
        self._sync_hardware_connection_controls()
        self._refresh_command_profiles()

    def handle_supply_profile_changed(self) -> None:
        combo = getattr(self.ui, 'comboBox_supply', None)
        if not isinstance(combo, QtWidgets.QComboBox):
            return
        profile_id = combo.currentData(QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(profile_id, str):
            profile_id = "hmp4030"
        self._apply_supply_profile(profile_id)
        self.update_planned_time_label()
        self.update_file_name_from_preset()

    def _voltage_limit_value(self) -> float:
        try:
            return float(getattr(self, "max_voltage", 30.0))
        except Exception:
            return 30.0

    def _format_voltage_limit(self) -> str:
        limit = self._voltage_limit_value()
        if abs(limit - round(limit)) < 1e-6:
            return str(int(round(limit)))
        text = f"{limit:.1f}".rstrip("0").rstrip(".")
        return text or str(limit)

    def _voltage_limit_prefix(self) -> str:
        return f"To {self._format_voltage_limit()} V"

    def _sync_runtime_settings(self) -> None:
        """Refresh runtime parameters from the UI spinboxes."""

        max_spin = getattr(self.ui, 'spinBox_max_current', None)
        if isinstance(max_spin, QtWidgets.QSpinBox):
            try:
                max_spin.interpretText()
            except Exception:
                pass
            try:
                self.max_current_mA = int(max_spin.value())
                min_start = int(getattr(self, "min_start_current_mA", 1))
                if self.max_current_mA < min_start:
                    max_spin.blockSignals(True)
                    max_spin.setValue(min_start)
                    max_spin.blockSignals(False)
                    self.max_current_mA = min_start
            except Exception:
                pass
        start_spin = getattr(self.ui, 'spinBox_start_current', None)
        if isinstance(start_spin, QtWidgets.QSpinBox):
            try:
                start_spin.interpretText()
            except Exception:
                pass
            try:
                value = int(start_spin.value())
                min_value = int(getattr(self, "min_start_current_mA", 1))
                if value < min_value:
                    start_spin.blockSignals(True)
                    start_spin.setValue(min_value)
                    start_spin.blockSignals(False)
                    value = min_value
                self.start_current_mA = value
            except Exception:
                pass
        step_spin = getattr(self.ui, 'spinBox_step_mA', None)
        if isinstance(step_spin, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            try:
                step_spin.interpretText()
            except Exception:
                pass
            try:
                self.current_step_mA = self._quantize_current_ramp_mA_s(float(step_spin.value()))
                if abs(float(step_spin.value()) - self.current_step_mA) > 1e-9:
                    step_spin.blockSignals(True)
                    step_spin.setValue(self.current_step_mA)
                    step_spin.blockSignals(False)
                self.current_step_A = self.current_step_mA / 1000.0
            except Exception:
                pass
        volt_spin = getattr(self.ui, 'spinBox_max_voltage', None)
        if isinstance(volt_spin, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            try:
                volt_spin.interpretText()
            except Exception:
                pass
            try:
                self.max_voltage = float(volt_spin.value())
                self.open_threshold = self.max_voltage
            except Exception:
                pass

    def _start_current_A(self) -> float:
        """Return the configured start current in amps."""

        try:
            start_mA = int(getattr(self, "start_current_mA", 1))
        except Exception:
            start_mA = 1
        min_mA = int(getattr(self, "min_start_current_mA", 1))
        return max(min_mA, start_mA) / 1000.0

    def _warn_start_at_max(self) -> None:
        try:
            start_spin = getattr(self.ui, 'spinBox_start_current', None)
            if isinstance(start_spin, QtWidgets.QSpinBox):
                try:
                    start_spin.interpretText()
                except Exception:
                    pass
                start_mA = int(start_spin.value())
            else:
                start_mA = int(getattr(self, "start_current_mA", 1))
        except Exception:
            start_mA = 1
        try:
            max_spin = getattr(self.ui, 'spinBox_max_current', None)
            if isinstance(max_spin, QtWidgets.QSpinBox):
                try:
                    max_spin.interpretText()
                except Exception:
                    pass
                max_mA = int(max_spin.value())
            else:
                max_mA = int(getattr(self, "max_current_mA", start_mA))
        except Exception:
            max_mA = start_mA
        if start_mA >= max_mA:
            message = (
                "Start current is at or above Max current; ramp will not increase. "
                "Increase Max current to see a ramp."
            )
            self._show_status_message(message, timeout_ms=15000)
            try:
                QtWidgets.QMessageBox.information(self, "Start current", message)
            except Exception:
                pass

    def _refresh_command_profiles(self) -> None:
        """Sync command templates with the configured start current."""

        start_a = self._start_current_A()
        limit_v = self._voltage_limit_value()
        channel = int(getattr(self, "channel_select", 0) or 0)
        commands_init: list[str] = []
        if bool(getattr(self, "reset_on_start", True)):
            commands_init.append("*RST\n")
        commands_init.append("SYST:REM\n")
        if channel > 0:
            commands_init.append(f"INST:NSEL {channel}\n")
        if bool(getattr(self, "voltage_first", False)):
            commands_init.append(f"VOLT {limit_v:.1f}\n")
            commands_init.append(f"CURR {start_a:.4f}\n")
        else:
            commands_init.append(f"CURR {start_a:.4f}\n")
            commands_init.append(f"VOLT {limit_v:.1f}\n")
        commands_init.append("OUTP ON\n")
        self.commands_init = commands_init
        safe_end = [
            "CURR 0.0000\n",
            "VOLT 0.000\n",
            "OUTP OFF\n",
            "SYST:LOC\n",
        ]
        if channel > 0:
            safe_end.insert(0, f"INST:NSEL {channel}\n")
            safe_end.append("OUTP:GEN 0\n")
        self.commands_safe_end = safe_end

    def compute_planned_seconds(self) -> int | None:
        """Estimate duration based on UI parameters, even when idle.

        Assumes 1 mA per second ramp rate (timer_command = 1000 ms).
        """
        try:
            max_mA = int(self.ui.spinBox_max_current.value())
            start_mA = int(self.ui.spinBox_start_current.value()) if hasattr(self.ui, 'spinBox_start_current') else 1
            hold_s = 0
            loops = int(self.ui.spinBox_loops.value()) if hasattr(self.ui, 'spinBox_loops') else 1
            reverse = self._reverse_to_zero_after_max_enabled()
            infinite = bool(self.ui.checkBox_infinite_loops.isChecked()) if hasattr(self.ui, 'checkBox_infinite_loops') else False
            step_mA = float(self.ui.spinBox_step_mA.value()) if hasattr(self.ui, 'spinBox_step_mA') else 1.0
        except Exception:
            return None
        if infinite:
            return None
        min_start = int(getattr(self, "min_start_current_mA", 1))
        min_start = max(1, min_start)
        if max_mA < min_start:
            start_mA = max_mA
        else:
            start_mA = max(min_start, min(start_mA, max_mA))
        step_mA = max(self._current_resolution_mA(), float(step_mA))
        # steps up from start current to max in increments of step_mA
        up_steps = max(0, math.ceil(max(0, max_mA - start_mA) / step_mA))
        down_steps = up_steps if reverse else 0
        per_loop = up_steps + hold_s + down_steps
        return per_loop * max(1, loops)

    def update_planned_time_label(self):
        label = getattr(self.ui, 'label_time_remaining', None)
        if label is None:
            return
        secs = self.compute_planned_seconds()
        if secs is None:
            label.setText("Time remaining: N/A")
        else:
            label.setText(self._format_secs("Time remaining", secs))
        if hasattr(self.ui, 'label_time_to_limit'):
            self.ui.label_time_to_limit.setText(self._format_voltage_limit_label())

    def update_file_name_from_preset(self):
        # Build file name based on naming preset
        if not hasattr(self.ui, 'comboBox_name_preset'):
            return
        preset = self.ui.comboBox_name_preset.currentText().strip().lower()
        if preset.startswith('current'):
            comp = getattr(self.ui, 'lineEdit_composition', None)
            wire = getattr(self.ui, 'lineEdit_microwire', None)
            sample = getattr(self.ui, 'lineEdit_sample', None)
            load = getattr(self.ui, 'lineEdit_load', None)
            notes = getattr(self.ui, 'lineEdit_notes', None)
            comp_s = comp.text().strip() if comp is not None else ''
            wire_s = wire.text().strip() if wire is not None else ''
            wire_s = wire_s.replace("\\", "_").replace("/", "_")
            sample_s = sample.text().strip() if sample is not None else ''
            load_s = " ".join(load.text().split()) if load is not None else ''
            notes_s = " ".join(notes.text().split()) if notes is not None else ''
            try:
                max_mA = int(self.ui.spinBox_max_current.value())
            except Exception:
                max_mA = 0
            parts = [p for p in [comp_s, wire_s, sample_s, load_s, f"{max_mA}mA", notes_s] if p]
            base = " ".join(parts) if parts else "anneal_log"
            # Show only preset fields
            for name in (
                'lineEdit_composition',
                'lineEdit_microwire',
                'lineEdit_sample',
                'sample_row_widget',
                'lineEdit_load',
                'lineEdit_notes',
            ):
                widget = getattr(self.ui, name, None)
                if widget is not None:
                    widget.setVisible(True)
            for name in ('label_composition', 'label_microwire', 'label_sample', 'label_load', 'label_notes'):
                label = getattr(self.ui, name, None)
                if label is not None:
                    label.setVisible(True)
            if hasattr(self.ui, 'label_custom_name'):
                self.ui.label_custom_name.setVisible(False)
            if hasattr(self.ui, 'lineEdit_custom_name'):
                self.ui.lineEdit_custom_name.setVisible(False)
        else:
            custom = getattr(self.ui, 'lineEdit_custom_name', None)
            base = custom.text().strip() if custom is not None and custom.text().strip() else 'anneal_log'
            # Show only custom name field
            for name in (
                'lineEdit_composition',
                'lineEdit_microwire',
                'lineEdit_sample',
                'sample_row_widget',
                'lineEdit_load',
                'lineEdit_notes',
            ):
                widget = getattr(self.ui, name, None)
                if widget is not None:
                    widget.setVisible(False)
            for name in ('label_composition', 'label_microwire', 'label_sample', 'label_load', 'label_notes'):
                label = getattr(self.ui, name, None)
                if label is not None:
                    label.setVisible(False)
            if hasattr(self.ui, 'label_custom_name'):
                self.ui.label_custom_name.setVisible(True)
            if hasattr(self.ui, 'lineEdit_custom_name'):
                self.ui.lineEdit_custom_name.setVisible(True)
        base = self._apply_loop_suffix_to_base(base)
        if hasattr(self.ui, 'lineEdit_log_file'):
            self.ui.lineEdit_log_file.setText(base)
        self.store_name_preset()
        self._update_composition_warning()

    def store_name_preset(self):
        s = self.settings
        s.setValue("preset", self.ui.comboBox_name_preset.currentIndex())
        s.setValue("composition", self.ui.lineEdit_composition.text())
        s.setValue("microwire", self.ui.lineEdit_microwire.text())
        s.setValue("sample", self.ui.lineEdit_sample.text())
        load = getattr(self.ui, 'lineEdit_load', None)
        s.setValue("load", load.text() if load is not None else "")
        notes = getattr(self.ui, 'lineEdit_notes', None)
        s.setValue("notes", notes.text() if notes is not None else "")
        s.setValue("custom_name", self.ui.lineEdit_custom_name.text())

    def restore_name_preset(self):
        try:
            self.ui.comboBox_name_preset.blockSignals(True)
            self.ui.lineEdit_composition.blockSignals(True)
            self.ui.lineEdit_microwire.blockSignals(True)
            self.ui.lineEdit_sample.blockSignals(True)
            if hasattr(self.ui, 'lineEdit_load'):
                self.ui.lineEdit_load.blockSignals(True)
            if hasattr(self.ui, 'lineEdit_notes'):
                self.ui.lineEdit_notes.blockSignals(True)
            self.ui.lineEdit_custom_name.blockSignals(True)
        except Exception:
            pass
        s = self.settings
        self.ui.comboBox_name_preset.setCurrentIndex(int(s.value("preset", DEFAULT_PRESET["preset"])))
        self.ui.lineEdit_composition.setText(s.value("composition", DEFAULT_PRESET["composition"]))
        self.ui.lineEdit_microwire.setText(_display_microwire(s.value("microwire", DEFAULT_PRESET["microwire"])))
        self.ui.lineEdit_sample.setText(s.value("sample", DEFAULT_PRESET["sample"]))
        if hasattr(self.ui, 'lineEdit_load'):
            self.ui.lineEdit_load.setText(s.value("load", DEFAULT_PRESET["load"]))
        if hasattr(self.ui, 'lineEdit_notes'):
            self.ui.lineEdit_notes.setText(s.value("notes", DEFAULT_PRESET["notes"]))
        self.ui.lineEdit_custom_name.setText(s.value("custom_name", DEFAULT_PRESET["custom_name"]))
        try:
            self.ui.comboBox_name_preset.blockSignals(False)
            self.ui.lineEdit_composition.blockSignals(False)
            self.ui.lineEdit_microwire.blockSignals(False)
            self.ui.lineEdit_sample.blockSignals(False)
            if hasattr(self.ui, 'lineEdit_load'):
                self.ui.lineEdit_load.blockSignals(False)
            if hasattr(self.ui, 'lineEdit_notes'):
                self.ui.lineEdit_notes.blockSignals(False)
            self.ui.lineEdit_custom_name.blockSignals(False)
        except Exception:
            pass
        self._update_composition_warning()

    def reset_name_preset(self):
        self.settings.clear()
        self.restore_name_preset()
        self.update_file_name_from_preset()

    def _handle_composition_text_changed(self, _text: str) -> None:
        self._update_composition_warning()

    def _update_composition_warning(self) -> None:
        edit = getattr(self.ui, 'lineEdit_composition', None)
        if edit is None or not hasattr(edit, 'set_extra_warning'):
            return
        warn, total = composition_warning_state(edit.text())
        if warn and total is not None:
            message = f"Element percentages add up to {total:.2f} %, expected 100."
            edit.set_extra_warning(True, message)
        else:
            edit.set_extra_warning(False)

    def open_log_dir(self) -> None:
        try:
            path = self.ui.lineEdit_log_dir.text().strip()
            if not path:
                return
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        except Exception:
            pass

    def handle_step_changed(self):
        try:
            self.current_step_mA = self._quantize_current_ramp_mA_s(float(self.ui.spinBox_step_mA.value()))
            if abs(float(self.ui.spinBox_step_mA.value()) - self.current_step_mA) > 1e-9:
                self.ui.spinBox_step_mA.blockSignals(True)
                self.ui.spinBox_step_mA.setValue(self.current_step_mA)
                self.ui.spinBox_step_mA.blockSignals(False)
        except Exception:
            self.current_step_mA = self._current_resolution_mA()
        self.current_step_A = self.current_step_mA/1000.0
        try:
            self.settings.setValue("step_mA", self.current_step_mA)
            self._store_profile_setting("step_mA", self.current_step_mA)
        except Exception:
            pass
        self.update_planned_time_label()

    def handle_send_serial_command_clicked(self):
        self.serial_command = self.ui.lineEdit_serial_command.text() + "\n"
        self.send_serial_command()
        
    def send_serial_command(self):
        if self._using_shared_broker():
            raise RuntimeError("Raw serial commands are disabled in shared HMP broker mode.")
        self.ser_mcu.write(bytes(self.serial_command, encoding='ascii'))
        self.ui.label_last_command.setText(self.serial_command)

    def _send_current_setpoint(self) -> None:
        """Apply the next current setpoint, refreshing voltage first when required."""

        current_limits_mA: list[float] = []
        for value in (
            getattr(self, "max_current_mA", 0.0),
        ):
            try:
                limit = float(value)
            except Exception:
                continue
            if limit > 0.0:
                current_limits_mA.append(limit)
        max_current_mA = min(current_limits_mA) if current_limits_mA else 0.0
        if max_current_mA > 0.0:
            requested_mA = float(self.current_current_set) * 1000.0
            tolerance_mA = max(1e-9, self._current_resolution_mA() * 1e-6)
            if requested_mA > max_current_mA + tolerance_mA:
                self.current_current_set = max_current_mA / 1000.0
        if self._using_shared_broker():
            self._set_shared_broker_current()
            self.ui.label_last_command.setText(
                f"broker set CH{self._shared_broker_channel()} current "
                f"{self.current_current_set * 1000.0:.1f} mA"
            )
            return
        if bool(getattr(self, "voltage_first", False)):
            limit_v = self._voltage_limit_value()
            self.serial_command = f"VOLT {limit_v:.1f}\n"
            self.send_serial_command()
            # Owon responds more reliably when voltage is refreshed slightly
            # ahead of each current update.
            self.simple_delay(80)
        self.serial_command = f"CURR {self.current_current_set:.4f}\n"
        self.send_serial_command()
        
    def handle_raw_vcp_mode_selected(self):
        self.operation_mode = 0
        self.ui.frame_process_settings.setEnabled(False)
        self.ui.pushButton_start_process.setEnabled(False)
        self.ui.pushButton_start_process.setText("Recipe disabled (Raw VCP)")
        self.ui.pushButton_start_process.setToolTip(
            "Raw VCP is manual serial-console mode. Select Manual or Automatic annealing to run a recipe."
        )
        self._set_process_state("idle", "Raw VCP manual mode — recipe actions disabled")

    def handle_manual_mode_selected(self):
        self.operation_mode = 1
        self.ui.frame_process_settings.setEnabled(True)
        self.ui.spinBox_max_current.setEnabled(False)
        self.ui.pushButton_start_process.setEnabled(True)
        self.ui.pushButton_start_process.setText("Start annealing process")
        self.ui.pushButton_start_process.setToolTip("")

    def handle_automatic_mode_selected(self):
        self.operation_mode = 2
        self.ui.frame_process_settings.setEnabled(True)
        self.ui.spinBox_max_current.setEnabled(True)
        self.ui.pushButton_start_process.setEnabled(True)
        self.ui.pushButton_start_process.setText("Start annealing process")
        self.ui.pushButton_start_process.setToolTip("")

    def handle_mode_changed(self, index: int) -> None:
        if index == 0:
            self.handle_raw_vcp_mode_selected()
        elif index == 1:
            self.handle_manual_mode_selected()
        else:
            self.handle_automatic_mode_selected()
        self._sync_mode_actions(self.operation_mode)
        
    def handle_max_current_value_changed(self):
        self.max_current_mA = self.ui.spinBox_max_current.value()
        min_start = int(getattr(self, "min_start_current_mA", 1))
        if self.max_current_mA < min_start:
            try:
                self.ui.spinBox_max_current.blockSignals(True)
                self.ui.spinBox_max_current.setValue(min_start)
            finally:
                self.ui.spinBox_max_current.blockSignals(False)
            self.max_current_mA = min_start
        try:
            self.settings.setValue("max_current", self.max_current_mA)
            self._store_profile_setting("max_current", self.max_current_mA)
        except Exception:
            pass
        spin = getattr(self.ui, 'spinBox_start_current', None)
        if isinstance(spin, QtWidgets.QSpinBox):
            try:
                if spin.value() > self.max_current_mA:
                    spin.blockSignals(True)
                    spin.setValue(self.max_current_mA)
                    spin.blockSignals(False)
                self.start_current_mA = int(spin.value())
                self.settings.setValue("start_current", self.start_current_mA)
                self._store_profile_setting("start_current", self.start_current_mA)
                self._refresh_command_profiles()
            except Exception:
                pass

    def handle_max_voltage_value_changed(self):
        spin = getattr(self.ui, 'spinBox_max_voltage', None)
        if not isinstance(spin, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            return
        try:
            value = float(spin.value())
        except Exception:
            return
        if value <= 0:
            return
        self.max_voltage = value
        self.open_threshold = value
        try:
            self.settings.setValue("max_voltage", value)
            self._store_profile_setting("max_voltage", value)
        except Exception:
            pass
        self._refresh_command_profiles()
        self._reset_voltage_projection()
        self.update_planned_time_label()

    def handle_channel_select_value_changed(self):
        value = 0
        combo = getattr(self.ui, 'comboBox_channel', None)
        if isinstance(combo, QtWidgets.QComboBox):
            data = combo.currentData()
            if data is not None:
                try:
                    value = int(data)
                except Exception:
                    value = 0
        else:
            spin = getattr(self.ui, 'spinBox_channel', None)
            if not isinstance(spin, QtWidgets.QSpinBox):
                return
            try:
                spin.interpretText()
            except Exception:
                pass
            try:
                value = int(spin.value())
            except Exception:
                value = 0
        self.channel_select = value
        self._shared_broker_role_checked_channel = None
        spin = getattr(self.ui, 'spinBox_channel', None)
        if isinstance(spin, QtWidgets.QSpinBox):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        try:
            self.settings.setValue("channel_select", value)
            self._store_profile_setting("channel_select", value)
        except Exception:
            pass
        self._refresh_command_profiles()

    def handle_reset_on_start_toggled(self, checked: bool) -> None:
        self.reset_on_start = bool(checked)
        try:
            reset_value = int(self.reset_on_start)
            self.settings.setValue("reset_on_start", reset_value)
            self._store_profile_setting("reset_on_start", reset_value)
        except Exception:
            pass
        self._refresh_command_profiles()

    def handle_start_current_value_changed(self):
        spin = getattr(self.ui, 'spinBox_start_current', None)
        if not isinstance(spin, QtWidgets.QSpinBox):
            return
        try:
            start_mA = int(spin.value())
        except Exception:
            return
        max_spin = getattr(self.ui, 'spinBox_max_current', None)
        try:
            max_mA = int(max_spin.value()) if isinstance(max_spin, QtWidgets.QSpinBox) else start_mA
        except Exception:
            max_mA = start_mA
        if start_mA > max_mA and isinstance(max_spin, QtWidgets.QSpinBox):
            try:
                max_spin.blockSignals(True)
                max_spin.setValue(start_mA)
            finally:
                max_spin.blockSignals(False)
            max_mA = start_mA
            self.max_current_mA = max_mA
            try:
                self.settings.setValue("max_current", max_mA)
                self._store_profile_setting("max_current", max_mA)
            except Exception:
                pass
        min_value = int(getattr(self, "min_start_current_mA", 1))
        if start_mA < min_value:
            try:
                spin.blockSignals(True)
                spin.setValue(min_value)
            finally:
                spin.blockSignals(False)
            start_mA = min_value
        self.start_current_mA = start_mA
        try:
            self.settings.setValue("start_current", start_mA)
            self._store_profile_setting("start_current", start_mA)
        except Exception:
            pass
        self._refresh_command_profiles()
        self.update_planned_time_label()
        
    def handle_show_history_clicked(self) -> None:
        dialog = MeasurementHistoryDialog(self, list(self._measurement_history))
        dialog.exec()

    def handle_configure_plots_clicked(self) -> None:
        dialog = CurrentAnnealingPlotConfigDialog(
            self,
            axis_modes=self._plot_axis_modes,
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self._plot_axis_modes = dialog.axis_modes
        self._store_plot_axis_modes()
        self.init_graph_window()
        if self._samples_current:
            self._redraw_segments()

    def handle_toggle_process_clicked(self):
        if not self.process_running:
            if self.operation_mode == 0:
                self._set_process_state("idle", "Raw VCP manual mode — recipe actions disabled")
                self._show_status_message(
                    "Raw VCP is manual serial-console mode; select an annealing mode to run a recipe.",
                    timeout_ms=10000,
                )
                return
            preflight_errors = self._start_preflight_errors(check_connection=False)
            if preflight_errors:
                self._show_start_preflight_errors(preflight_errors)
                return
            if not bool(getattr(self, "is_connected", False)) and not self._auto_connect_for_start():
                detail = str(getattr(self, "_last_auto_connect_error", "") or "").strip()
                self._show_start_preflight_errors([detail or "Hardware auto-connect did not complete."])
                return
            preflight_errors = self._start_preflight_errors()
            if preflight_errors:
                self._show_start_preflight_errors(preflight_errors)
                return
            if not self._confirm_shared_broker_cadence_start():
                self._show_status_message("Annealing start cancelled; shared PSU rate was not accepted.")
                return
            self.process_running = True
            self._last_run_error = ""
            self._update_mode_action_state()
            self._sync_runtime_settings()
            self._refresh_command_profiles()
            self._max_voltage_dialog = False
            self._shared_broker_limit_warning_shown = False
            self._contact_lost = False
            self._zero_current_count = 0
            try:
                self._process_start_time = time.monotonic()
            except Exception:
                self._process_start_time = None
            self._nonzero_current_seen = False
            self._last_nonzero_current_time = None
            self._skip_current_sample = False
            self._clear_zero_placeholders()
            self._reset_sample_buffers()
            self._set_process_controls_enabled(False)
            if hasattr(self.ui, 'pushButton_reverse_now'):
                self.ui.pushButton_reverse_now.setEnabled(True)
            self.force_stop_at_zero = False
            self.command_number = 0
            self.sample_index = 0
            self.first_sample = True
            self.direction_ascending = True
            self._reset_voltage_projection()
            self._reset_loop_tracking()
            self.ui.pushButton_start_process.setText("Stop annealing process")
            if(self.operation_mode == 0):
                pass

            elif(self.operation_mode == 1):
                # Prepare output file with overwrite prompt
                if not self.prepare_output_file():
                    self.process_running = False
                    self._process_start_time = None
                    self.ui.pushButton_start_process.setText("Start annealing process")
                    self._restore_idle_controls()
                    self._set_process_state("idle")
                    return
                self._record_name_history()
                if hasattr(self.ui, 'progressBar_process'):
                    self.ui.progressBar_process.setMaximum(0)
                    self.ui.progressBar_process.setValue(0)
                self._set_process_state("infinite-running", "Manual annealing — running until stopped")
                if hasattr(self.ui, 'label_time_remaining'):
                    self.ui.label_time_remaining.setText("Time remaining: N/A")
                if hasattr(self.ui, 'label_time_to_limit'):
                    self.ui.label_time_to_limit.setText(self._format_voltage_limit_label())
                self._set_current_ramp_direction(1.0)
                self.direction_ascending = True
                self.current_current_set = self._start_current_A()
                self._ramp_ideal_current_A = self.current_current_set
                self._display_ui_value('label_set_current', f"{self.current_current_set*1000:.1f}")
                self.temp_resistance_maximum = 0
                self.current_voltage = 0
                self.current_resistance = 0
                self._display_ui_value('lcd_current_mA', "0")
                self._display_ui_value('label_live_voltage', "0")
                self.line_color="r"
                self.init_graph_window()
                self.send_init_commands()
                # Immediately request the first sample instead of waiting
                # for the one-second timer interval to elapse.  This avoids
                # an unnecessary pause after the user presses *Start*.
                self.handle_send_new_command()
                self.timer_command.start(max(1, round(1000.0 / self._effective_hmp_command_hz())))
                
            elif(self.operation_mode == 2):
                # Prepare output file with overwrite prompt
                if not self.prepare_output_file():
                    self.process_running = False
                    self._process_start_time = None
                    self.ui.pushButton_start_process.setText("Start annealing process")
                    self._restore_idle_controls()
                    self._set_process_state("idle")
                    return
                self._record_name_history()
                self._set_current_ramp_direction(1.0)
                self.direction_ascending = True
                self.current_current_set = self._start_current_A()
                self._ramp_ideal_current_A = self.current_current_set
                self._display_ui_value('label_set_current', f"{self.current_current_set*1000:.1f}")
                self.temp_resistance_maximum = 0
                self.current_voltage = 0
                self.current_resistance = 0
                self._display_ui_value('lcd_current_mA', "0")
                self._display_ui_value('label_live_voltage', "0")
                self._warn_start_at_max()
                # reverse + loop configuration
                self.reverse_enabled = self._reverse_to_zero_after_max_enabled()
                self.loop_target = self.ui.spinBox_loops.value() if hasattr(self.ui, 'spinBox_loops') else 1
                self.infinite_loops = bool(self.ui.checkBox_infinite_loops.isChecked()) if hasattr(self.ui, 'checkBox_infinite_loops') else False
                self.loop_idx = 0
                # progress plan
                per_loop = self._planned_automatic_loop_steps()
                self._init_loop_tracking(per_loop, int(self.loop_target or 1), self.infinite_loops)
                if hasattr(self.ui, 'progressBar_process'):
                    if self.total_steps:
                        self.ui.progressBar_process.setMaximum(self.total_steps)
                        self.ui.progressBar_process.setValue(0)
                    else:
                        self.ui.progressBar_process.setMaximum(0)
                if self.infinite_loops:
                    self._set_process_state("infinite-running")
                else:
                    self._set_process_state("finite-running")
                if hasattr(self.ui, 'label_time_to_limit'):
                    self.ui.label_time_to_limit.setText(self._format_voltage_limit_label())
                self.line_color="r"
                self.init_graph_window()
                self.send_init_commands()
                # Kick off the first acquisition immediately so the
                # measurement starts without a one-second delay.
                self.handle_send_new_command()
                self.timer_command.start(max(1, round(1000.0 / self._effective_hmp_command_hz())))
                
            else:
                pass
        else:
            self.stop_annealing("Stopped by user.", show_dialog=False)
    def handle_pushButton_reverse_now_clicked(self):
        """Immediately ramp current down toward zero."""
        if not self.process_running:
            return
        self._set_current_ramp_direction(-1.0)
        self.line_color = "b"
        self.force_stop_at_zero = True
        self.direction_ascending = False
        self._reset_voltage_projection()

    def handle_update_running_recipe_clicked(self) -> None:
        """Apply runtime-safe recipe edits to the active automatic run."""

        if not self.process_running:
            return
        self._sync_runtime_settings()
        self._store_loop_preferences()
        if getattr(self, "operation_mode", 2) != 2:
            self.update_time_estimate()
            self._show_status_message("Updated running settings.")
            return
        if self.current_increment != 0:
            self.current_increment = self._current_increment_for_direction(self.current_increment)
        if self.direction_ascending and self.current_increment > 0:
            current_set_mA = float(getattr(self, "current_current_set", 0.0) or 0.0) * 1000.0
            if current_set_mA >= float(getattr(self, "max_current_mA", current_set_mA)):
                self._set_current_ramp_direction(-1.0)
                self.line_color = "b"
                self.direction_ascending = False
                self._reset_voltage_projection()
                self._adjust_progress_for_reverse()
        self._refresh_running_recipe_plan()
        self._show_status_message(
            f"Updated running recipe: max {int(getattr(self, 'max_current_mA', 0))} mA, "
            f"ramp {float(getattr(self, 'current_step_mA', 0.0)):.1f} mA/s."
        )

    def _set_process_controls_enabled(self, enabled: bool) -> None:
        if not hasattr(self.ui, 'groupBox_process_settings'):
            return
        keep = {self.ui.pushButton_start_process}
        if hasattr(self.ui, 'pushButton_reverse_now'):
            keep.add(self.ui.pushButton_reverse_now)
        if hasattr(self.ui, 'progressBar_process'):
            keep.add(self.ui.progressBar_process)
        if hasattr(self.ui, 'label_time_remaining'):
            keep.add(self.ui.label_time_remaining)
        if hasattr(self.ui, 'pushButton_update_running_recipe'):
            keep.add(self.ui.pushButton_update_running_recipe)
        if hasattr(self.ui, 'groupBox_live_values'):
            keep.add(self.ui.groupBox_live_values)
        for child in self.ui.groupBox_process_settings.findChildren(QtWidgets.QWidget):
            if child in keep:
                continue
            child.setEnabled(enabled)
        recipe_help = "" if enabled else "Recipe is locked for this run. Stop before editing it."
        for name in ('spinBox_max_current', 'spinBox_step_mA', 'spinBox_start_current', 'spinBox_loops', 'checkBox_infinite_loops'):
            widget = getattr(self.ui, name, None)
            if widget is not None:
                widget.setToolTip(recipe_help)

    def _restore_idle_controls(self) -> None:
        """Re-enable process controls after a start attempt is canceled."""

        self._set_process_controls_enabled(True)
        self._update_mode_action_state()
        if hasattr(self.ui, 'pushButton_reverse_now'):
            self.ui.pushButton_reverse_now.setEnabled(False)
        if hasattr(self.ui, 'pushButton_update_running_recipe'):
            self.ui.pushButton_update_running_recipe.setEnabled(False)
            self.ui.pushButton_update_running_recipe.setVisible(False)

    def stop_annealing(
        self,
        reason: str | None = None,
        *,
        show_dialog: bool = False,
        final_state: str | None = None,
    ):
        """Abort the annealing run and power down the supply safely."""
        message = reason or "Measurement stopped."
        self._last_stop_reason = message
        self._set_process_state("stopping", message)
        QtWidgets.QApplication.processEvents()
        self.process_running = False
        self.wait = False  # break any pending delays
        self.force_stop_at_zero = False
        self._contact_lost = False
        self._zero_current_count = 0
        self._nonzero_current_seen = False
        self._skip_current_sample = False
        self._process_start_time = None
        self._last_nonzero_current_time = None
        self._clear_zero_placeholders()
        self._finalize_measurement_history()
        try:
            self.timer_command.stop()
        except Exception:
            pass
        if self.f_out:
            self.f_out.close()
            self.f_out = None
        if not self._using_shared_broker():
            # Immediately ramp the supply to zero before running the shutdown sequence
            try:
                channel = int(getattr(self, "channel_select", 0) or 0)
            except Exception:
                channel = 0
            try:
                ramp_cmds = []
                if channel > 0:
                    ramp_cmds.append(f"INST:NSEL {channel}\n")
                ramp_cmds.extend(["CURR 0.000\n", "OUTP OFF\n"])
                for cmd in ramp_cmds:
                    self.serial_command = cmd
                    self.send_serial_command()
                    self.simple_delay(100)
            except Exception:
                pass
        try:
            self.send_safe_end_commands()
        except Exception:
            pass
        self.ui.pushButton_start_process.setText("Start annealing process")
        self._set_process_controls_enabled(True)
        if hasattr(self.ui, 'pushButton_reverse_now'):
            self.ui.pushButton_reverse_now.setEnabled(False)
        if hasattr(self.ui, 'pushButton_update_running_recipe'):
            self.ui.pushButton_update_running_recipe.setEnabled(False)
            self.ui.pushButton_update_running_recipe.setVisible(False)
        self._update_mode_action_state()
        self._reset_voltage_projection()
        if hasattr(self.ui, 'label_time_to_limit'):
            self.ui.label_time_to_limit.setText(self._format_voltage_limit_label())
        self._display_ui_value('label_set_current', "0")
        self._max_voltage_dialog = False
        self.first_sample = True
        self._reset_loop_tracking()
        if final_state is None:
            final_state = "completed" if message.startswith("Run complete") else "idle"
        self._set_process_state(final_state, message)
        self._show_status_message(message, timeout_ms=0 if final_state == "failed" else 15000)
        if show_dialog:
            try:
                QtWidgets.QMessageBox.information(self, "Measurement stopped", message)
            except Exception:
                pass
        
    @QtCore.pyqtSlot()
    def handle_send_new_command(self):
        if self._window_closing or not self.process_running:
            return

        self._sync_runtime_settings()

        # Manual annealing
        if self.operation_mode == 1:
            self.sample_ready = False
            self.expecting_voltage = True
            if self._using_shared_broker():
                if not self._read_shared_broker_sample():
                    self.warn_no_response_and_abort()
                    return
                if not self.process_running:
                    return
            else:
                self.serial_command = "MEAS:VOLT?\n"
                # Use this command for the simulator; use the first for real hardware
                #self.serial_command = "*RRAWO\n"
                self.send_serial_command()
                # wait boundedly, allow stopping
                if not self.wait_for_sample(3000):
                    if not self.process_running:
                        return
                    self.warn_no_response_and_abort()
                    return

                self.sample_ready = False
                self.expecting_voltage = False
                self.serial_command = "MEAS:CURR?\n"
                # Use this command for the simulator; use the first for real hardware
                #self.serial_command = "*RRAWO\n"
                self.send_serial_command()
                if not self.wait_for_sample(3000):
                    if not self.process_running:
                        return
                    self.warn_no_response_and_abort()
                    return
                
            self.curr_value_x = self.current_current_read * 1000.0
            self.curr_value_y = self.current_resistance
            self._display_ui_value('lcd_current_mA', f"{self.curr_value_x:.1f}")
            self._display_ui_value('label_live_voltage', f"{self.current_voltage:.2f}")

            # Signal that a new sample arrived so command sequencing can continue
            if not self._record_acquired_sample():
                if self.process_running:
                    self._send_current_setpoint()
                return


            # Iterate the current set point
            self._advance_current_setpoint()
            self._display_ui_value('label_set_current', f"{self.current_current_set*1000:.1f}")

            # Stop the process once we are below the configured start current.
            if self.current_current_set < self._start_current_A():
                self.stop_annealing("Reached minimum current; stopping measurement.", show_dialog=True)

            if not self.process_running:
                return
            self._send_current_setpoint()
           
            
                
            
            
        elif self.operation_mode == 2:
            self.sample_ready = False
            self.expecting_voltage = True
            if self._using_shared_broker():
                if not self._read_shared_broker_sample():
                    self.warn_no_response_and_abort()
                    return
                if not self.process_running:
                    return
            else:
                self.serial_command = "MEAS:VOLT?\n"
                # Use this command for the simulator; use the first for real hardware
                #self.serial_command = "*RRAWO\n"
                self.send_serial_command()
                if not self.wait_for_sample(3000):
                    if not self.process_running:
                        return
                    self.warn_no_response_and_abort()
                    return

                self.sample_ready = False
                self.expecting_voltage = False
                self.serial_command = "MEAS:CURR?\n"
                # Use this command for the simulator; use the first for real hardware
                #self.serial_command = "*RRAWO\n"
                self.send_serial_command()
                if not self.wait_for_sample(3000):
                    if not self.process_running:
                        return
                    self.warn_no_response_and_abort()
                    return
                
            self.curr_value_x = self.current_current_read * 1000.0
            self.curr_value_y = self.current_resistance
            self._display_ui_value('lcd_current_mA', f"{self.curr_value_x:.1f}")
            self._display_ui_value('label_live_voltage', f"{self.current_voltage:.2f}")

            # Signal that a new sample arrived so command sequencing can continue
            if not self._record_acquired_sample(record_voltage_progress=True):
                if self.process_running:
                    self._send_current_setpoint()
                return

            # Reverse or stop immediately at the configured maximum current.
            if (self.current_current_set >= (self.max_current_mA/1000.0)) and (self.current_increment > 0):
                self._set_current_ramp_direction(-1.0)
                self.line_color = "b"
                self.direction_ascending = False
                self._reset_voltage_projection()

            # Iterate the current set point
            if not self.process_running:
                return
            self._advance_current_setpoint()
            self._display_ui_value('label_set_current', f"{self.current_current_set*1000:.1f}")

            if not self.process_running:
                return
            self._send_current_setpoint()
            # completed descending to zero? manage loops or stop
            if (self.current_increment < 0) and (self.current_current_set < self._start_current_A()):
                next_loop = int(getattr(self, 'loop_idx', 0)) + 1
                loops_pending = self._has_remaining_loops(next_loop)
                force_stop = bool(getattr(self, 'force_stop_at_zero', False))
                self.loop_idx = next_loop
                self._finalize_loop_cycle()
                if force_stop:
                    self.stop_annealing("Reverse completed; stopping measurement.", show_dialog=True)
                elif loops_pending:
                    # prepare next loop
                    self._set_current_ramp_direction(1.0)
                    self.current_current_set = self._start_current_A()
                    self._ramp_ideal_current_A = self.current_current_set
                    self.line_color = "r"
                    self.direction_ascending = True
                    self._reset_voltage_projection()
                else:
                    self.stop_annealing(
                        "Run complete; measurement stopped safely.",
                        show_dialog=True,
                        final_state="completed",
                    )

        else:
            pass
        
        self.command_number +=1
        
        
        

    def send_safe_end_commands(self):
        if self._using_shared_broker():
            self._shutdown_shared_broker_output()
            self.ui.label_last_command.setText("broker output off")
            self._release_experiment_sleep_guard()
            return
        for i in range(0, len(self.commands_safe_end)):
            self.serial_command = self.commands_safe_end[i]
            self.send_serial_command()
            self.simple_delay(200)
        self._release_experiment_sleep_guard()
            

    def send_init_commands(self):
        if self.process_running:
            self._acquire_experiment_sleep_guard()
        if self._using_shared_broker():
            if self.process_running:
                self._initialize_shared_broker_output()
            self.ui.label_last_command.setText(
                f"broker lease CH{self._shared_broker_channel()}"
            )
            return
        for cmd in self.commands_init:
            if not self.process_running:
                break
            self.serial_command = cmd
            self.send_serial_command()
            if cmd.strip() == "*RST":
                # Allow extra time after reset before sending follow-up commands.
                self.simple_delay(1200)
            else:
                # The original implementation paused for a full second between
                # initialisation commands, which caused a noticeable start-up
                # delay.  A brief 200 ms gap gives the supply time to process
                # each command while keeping the UI responsive.
                self.simple_delay(200)

    def _acquire_experiment_sleep_guard(self) -> None:
        try:
            if self._sleep_guard is not None:
                return
            self._sleep_guard = create_experiment_sleep_guard("Current annealing experiment")
            self._sleep_guard.acquire()
            self._show_status_message("Sleep prevention active while annealing is running.", timeout_ms=5000)
        except Exception as exc:
            self._sleep_guard = None
            self._show_status_message(f"Could not enable sleep prevention: {exc}", timeout_ms=10000)

    def _release_experiment_sleep_guard(self) -> None:
        guard = self._sleep_guard
        self._sleep_guard = None
        if guard is None:
            return
        try:
            guard.release()
            self._show_status_message("Sleep prevention released.", timeout_ms=5000)
        except Exception as exc:
            self._show_status_message(f"Could not release sleep prevention: {exc}", timeout_ms=10000)
            
    @QtCore.pyqtSlot()
    def _finish_simple_delay(self) -> None:
        if self._window_closing and not self._closing_safe_end:
            return
        self.wait = False

    def simple_delay(self, delay_ms):
        if self._window_closing and not self._closing_safe_end:
            self.wait = False
            return
        self.wait = True
        self._delay_timer.start(max(0, int(delay_ms)))

        while self.wait and (not self._window_closing or self._closing_safe_end):
            QtWidgets.QApplication.processEvents()
        self.wait = False
        self._delay_timer.stop()
        
    def wait_for_sample(self, timeout_ms: int) -> bool:
        """Spin the event loop until a sample arrives, stop requested, or timeout."""
        self.wait = False
        elapsed = 0
        step = 20
        retries = 0
        limit = max(step, int(timeout_ms))
        while self.process_running and not self.sample_ready:
            self.simple_delay(step)
            if self.sample_ready or not self.process_running:
                break
            elapsed += step
            if elapsed >= limit:
                recent = False
                try:
                    now = time.monotonic()
                    if self._last_serial_rx is not None and (now - self._last_serial_rx) < 0.75:
                        recent = True
                except Exception:
                    recent = False
                if recent:
                    elapsed = 0
                    continue
                if retries == 0:
                    retries = 1
                    elapsed = 0
                    continue
                break
        ok = bool(self.sample_ready)
        if not ok:
            self._serial_quiet_failures += 1
        else:
            self._serial_quiet_failures = 0
        self.sample_ready = False
        return ok

    def warn_no_response_and_abort(self) -> None:
        QtWidgets.QMessageBox.warning(
            self,
            "No response",
            "No response from power supply. Is it turned on? Aborting the process.",
        )
        if self.process_running:
            self.stop_annealing(
                "No response from power supply. Measurement stopped.",
                show_dialog=False,
                final_state="failed",
            )
        self._serial_quiet_failures = 0

    def handle_legacy_log_path_changed(self):
        # Sync f_name from separate directory + file name controls
        try:
            self.f_name = self.build_log_path()
        except Exception:
            self.f_name = self.ui.lineEdit_log_file_full.text()

    def init_live_values(self) -> None:
        box = getattr(self.ui, "groupBox_live_values", None)
        if box is None:
            return
        for child in box.findChildren(QtWidgets.QWidget):
            child.deleteLater()
        old_layout = box.layout()
        if old_layout is not None:
            QtWidgets.QWidget().setLayout(old_layout)
        layout = QtWidgets.QFormLayout(box)
        layout.setContentsMargins(6, 6, 6, 6)
        self.label_live_current = QtWidgets.QLabel("0")
        self.label_live_set = QtWidgets.QLabel("0")
        self.label_live_current_density = QtWidgets.QLabel("")
        self.label_live_set_density = QtWidgets.QLabel("")
        self.label_live_voltage = QtWidgets.QLabel("0")
        for lbl in (
            self.label_live_current,
            self.label_live_set,
            self.label_live_current_density,
            self.label_live_set_density,
            self.label_live_voltage,
        ):
            lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addRow("Set current (mA)", self.label_live_set)
        layout.addRow(f"Set current density ({CURRENT_DENSITY_UNIT})", self.label_live_set_density)
        layout.addRow("Current (mA)", self.label_live_current)
        layout.addRow(f"Current density ({CURRENT_DENSITY_UNIT})", self.label_live_current_density)
        layout.addRow("Voltage (V)", self.label_live_voltage)
        # Alias old names for compatibility
        self.ui.lcd_current_mA = self.label_live_current
        self.ui.label_set_current = self.label_live_set
        self.ui.label_live_voltage = self.label_live_voltage
        self._voltage_default_style = self.label_live_voltage.styleSheet() or ""
        self._voltage_warning_style = "color: #c0392b;"
        setattr(self.ui.lcd_current_mA, "display", self._set_live_current_text)
        setattr(self.ui.label_set_current, "display", self._set_live_set_current_text)
        setattr(self.ui.label_live_voltage, "display", self._set_live_voltage_text)
        self._refresh_current_density_visibility()

    def handle_max_voltage(self) -> None:
        self._max_voltage_dialog = True
        self.current_increment = 0
        action = getattr(self, "max_voltage_action", MAX_VOLTAGE_DEFAULT_ACTION)
        if action == "ask":
            self._show_max_voltage_prompt()
        else:
            self._apply_max_voltage_action(action)
        self._max_voltage_dialog = False

    def _init_max_voltage_action(self) -> None:
        stored = MAX_VOLTAGE_DEFAULT_ACTION
        try:
            value = self.settings.value("max_voltage_action", MAX_VOLTAGE_DEFAULT_ACTION)
            if isinstance(value, str):
                stored = value
        except Exception:
            stored = MAX_VOLTAGE_DEFAULT_ACTION
        if stored not in MAX_VOLTAGE_ACTION_LABELS:
            stored = MAX_VOLTAGE_DEFAULT_ACTION
        self.max_voltage_action = stored
        combo = getattr(self.ui, 'comboBox_max_voltage_action', None)
        if isinstance(combo, QtWidgets.QComboBox):
            idx = combo.findData(stored)
            if idx < 0:
                idx = combo.findData(MAX_VOLTAGE_DEFAULT_ACTION)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(self._store_max_voltage_action)

    def _apply_profile_max_voltage_action(self, profile_id: str) -> None:
        combo = getattr(self.ui, 'comboBox_max_voltage_action', None)
        if not isinstance(combo, QtWidgets.QComboBox):
            return
        fallback = getattr(self, "max_voltage_action", MAX_VOLTAGE_DEFAULT_ACTION)
        stored = self._load_profile_setting(profile_id, "max_voltage_action", fallback, str)
        if not isinstance(stored, str) or stored not in MAX_VOLTAGE_ACTION_LABELS:
            stored = MAX_VOLTAGE_DEFAULT_ACTION
        idx = combo.findData(stored)
        if idx < 0:
            idx = combo.findData(MAX_VOLTAGE_DEFAULT_ACTION)
        if idx < 0:
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        self.max_voltage_action = stored

    def _store_max_voltage_action(self) -> None:
        combo = getattr(self.ui, 'comboBox_max_voltage_action', None)
        if not isinstance(combo, QtWidgets.QComboBox):
            return
        data = combo.currentData(QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(data, str) or data not in MAX_VOLTAGE_ACTION_LABELS:
            data = MAX_VOLTAGE_DEFAULT_ACTION
        self.max_voltage_action = data
        try:
            self.settings.setValue("max_voltage_action", data)
            self._store_profile_setting("max_voltage_action", data)
        except Exception:
            pass

    def _show_status_message(self, message: str, timeout_ms: int = 10000) -> None:
        try:
            status_bar = self.statusBar()
            if status_bar is not None:
                status_bar.showMessage(message, timeout_ms)
        except Exception:
            pass

    def _set_hardware_status(self, message: str, *, state: str) -> None:
        label = getattr(self.ui, "label_hardware_status", None)
        if not isinstance(label, QtWidgets.QLabel):
            return
        colors = {"idle": "#6b7280", "connecting": "#b45309", "connected": "#15803d", "failed": "#b91c1c"}
        label.setText(f"Hardware status: {message}")
        label.setStyleSheet(f"font-weight: 600; color: {colors.get(state, '#6b7280')};")

    def _set_process_state(self, state: str, detail: str | None = None) -> None:
        self._process_state = state
        labels = {
            "idle": "Idle — no run active",
            "connecting": "Connecting — waiting for hardware",
            "finite-running": "Running — finite recipe",
            "infinite-running": "Running continuously — stop manually",
            "stopping": "Stopping safely — disabling output",
            "completed": "Completed — output stopped safely",
            "failed": "Failed — output stopped; operator action required",
        }
        colors = {
            "idle": "#6b7280", "connecting": "#b45309", "finite-running": "#1d4ed8",
            "infinite-running": "#7e22ce", "stopping": "#b45309", "completed": "#15803d", "failed": "#b91c1c",
        }
        text = labels.get(state, state.replace("-", " ").title())
        if detail and state in {"failed", "completed", "idle"}:
            text = f"{text}: {detail}"
        label = getattr(self.ui, "label_process_state", None)
        if isinstance(label, QtWidgets.QLabel):
            label.setText(text)
            label.setStyleSheet(f"font-weight: 600; color: {colors.get(state, '#6b7280')};")
        bar = getattr(self.ui, "progressBar_process", None)
        if not isinstance(bar, QtWidgets.QProgressBar):
            return
        if state == "idle":
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setFormat("Idle")
        elif state == "connecting":
            bar.setRange(0, 0)
            bar.setFormat("Connecting…")
        elif state == "finite-running":
            if self.total_steps > 0:
                bar.setRange(0, self.total_steps)
                bar.setValue(min(self.step_idx, self.total_steps))
            bar.setFormat("Running — %p%")
        elif state == "infinite-running":
            bar.setRange(0, 0)
            bar.setFormat("Running continuously")
        elif state == "stopping":
            bar.setRange(0, 0)
            bar.setFormat("Stopping safely…")
        elif state == "completed":
            bar.setRange(0, 1)
            bar.setValue(1)
            bar.setFormat("Completed")
        elif state == "failed":
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setFormat("Failed")

    def _adjust_progress_for_reverse(self) -> None:
        if not self.total_steps:
            return
        step_mA = (
            abs(float(getattr(self, 'current_step_mA', self._current_resolution_mA()) or self._current_resolution_mA()))
            / self._effective_hmp_command_hz()
        )
        current_mA = getattr(self, 'curr_value_x', None)
        if current_mA is None:
            current_mA = self.current_current_set * 1000.0
        try:
            current_mA = float(current_mA)
        except Exception:
            current_mA = 0.0
        remaining_steps = math.ceil(max(0.0, current_mA) / step_mA)
        projected_loop = getattr(self, '_current_loop_samples', 0) + remaining_steps
        if projected_loop > 0:
            self._projected_loop_samples = max(1, int(projected_loop))
        self._recalculate_total_steps(remaining_steps, self._projected_loop_samples)

    def _apply_max_voltage_action(self, action: str) -> None:
        if action not in {"reverse", "stop"}:
            action = "reverse"
        limit_label = f"{self._format_voltage_limit()} V"
        if action == "reverse":
            self._set_current_ramp_direction(-1.0)
            self.line_color = "b"
            next_loop = int(getattr(self, 'loop_idx', 0)) + 1
            if self._has_remaining_loops(next_loop):
                self.force_stop_at_zero = False
            else:
                self.force_stop_at_zero = False
            self.direction_ascending = False
            self._note_voltage_limit_reached()
            self._adjust_progress_for_reverse()
            self.update_time_estimate()
            self._show_status_message(f"{limit_label} reached — reversing to zero.")
        elif action == "stop":
            self._show_status_message(f"{limit_label} reached — stopping measurement.")
            self.direction_ascending = False
            self._note_voltage_limit_reached()
            self.update_time_estimate()
            if self.process_running:
                self.stop_annealing(f"{limit_label} reached — stopping measurement.", show_dialog=False)
    def _show_max_voltage_prompt(self) -> None:
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Voltage limit reached")
        msg.setText(f"Power supply reached {self._format_voltage_limit()} V. What do you want to do?")
        reverse_btn = msg.addButton("Reverse to zero", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        stop_btn = msg.addButton("Stop measurement", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is reverse_btn:
            self._apply_max_voltage_action("reverse")
        elif clicked is stop_btn:
            self._apply_max_voltage_action("stop")
        else:
            self._apply_max_voltage_action("reverse")
    
    def init_graph_window(self):
        """
        self.x_data_ax1 = []
        self.y_data_ax1 = []
        self.x_data_ax2 = []
        self.y_data_ax2 = []
        self.n_counter = 0
        self._segment_lines_ax1 = []
        self._segment_lines_ax2 = []
        """
    
        # Create an embedded realtime plot dashboard on the right panel.
        if hasattr(self.ui, 'plot_container'):
            container = self.ui.plot_container
            layout = container.layout()
            if layout is None:
                layout = QtWidgets.QVBoxLayout(container)
                # Zero margins to eliminate bright edge lines and maximize canvas area
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(0)
            self._remove_placeholder_text()
            while layout.count():
                item = layout.takeAt(0)
                if item is None:
                    continue
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self._pg_placeholder_labels = []
            self.pg_plot_resistance_vs_current = None
            self.pg_plot_resistance_vs_sample = None
            self._right_axis_views.clear()
            self._right_axis_curves.clear()

            if pg is not None:
                self._plot_backend = "pyqtgraph"
                self.fig = None
                self.canvas = None
                self.ax1 = None
                self.ax2 = None
                title_source = self.f_name or ""
                try:
                    title = format_annealing_title(Path(title_source).stem if title_source else "")
                except Exception:
                    title = format_annealing_title(title_source)
                header_row = QtWidgets.QHBoxLayout()
                header_row.setContentsMargins(0, 0, 0, 0)
                header_row.setSpacing(8)
                title_label = QtWidgets.QLabel(title, container)
                title_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                title_label.setStyleSheet("font-weight: 700; padding: 4px;")
                self.ui.pushButton_configure_plots = QtWidgets.QPushButton("Configure plots", container)
                self.ui.pushButton_configure_plots.setObjectName("pushButton_configure_plots")
                self.ui.pushButton_configure_plots.clicked.connect(self.handle_configure_plots_clicked)
                header_row.addStretch(1)
                header_row.addWidget(title_label, 3)
                header_row.addWidget(self.ui.pushButton_configure_plots, 0)
                layout.addLayout(header_row)
                self.pg_plot_resistance_vs_current = pg.PlotWidget(container)
                self.pg_plot_resistance_vs_sample = pg.PlotWidget(container)
                upper_bottom_label, upper_bottom_units = self._plot_axis_label_units(self._axis_mode("upper", "bottom"))
                upper_left_label, upper_left_units = self._plot_axis_label_units(self._axis_mode("upper", "left"))
                lower_bottom_label, lower_bottom_units = self._plot_axis_label_units(self._axis_mode("lower", "bottom"))
                lower_left_label, lower_left_units = self._plot_axis_label_units(self._axis_mode("lower", "left"))
                self._configure_pyqtgraph_plot(
                    self.pg_plot_resistance_vs_current,
                    bottom_label=upper_bottom_label,
                    bottom_units=upper_bottom_units,
                    left_label=upper_left_label,
                    left_units=upper_left_units,
                )
                self._configure_pyqtgraph_plot(
                    self.pg_plot_resistance_vs_sample,
                    bottom_label=lower_bottom_label,
                    bottom_units=lower_bottom_units,
                    left_label=lower_left_label,
                    left_units=lower_left_units,
                )
                layout.addWidget(self.pg_plot_resistance_vs_current, 1)
                layout.addWidget(self.pg_plot_resistance_vs_sample, 1)
                self._refresh_right_axis_overlay("upper")
                self._refresh_right_axis_overlay("lower")
                self._zero_placeholder_line1 = None
                self._zero_placeholder_line2 = None
                self._zero_placeholder_count = 0
                self._zero_placeholders_active = False
                return

            self._plot_backend = "matplotlib"
            # Align matplotlib colors with Qt palette for a native look
            scheme = QtCore.Qt.ColorScheme.Light
            win_rgb = (1.0, 1.0, 1.0)
            base_rgb = (1.0, 1.0, 1.0)
            text_rgb = (0.0, 0.0, 0.0)
            app = QtWidgets.QApplication.instance()
            if isinstance(app, QtWidgets.QApplication):
                try:
                    style_hints = app.styleHints()
                    if style_hints is not None:
                        scheme = style_hints.colorScheme()
                    palette = app.palette()
                    win = palette.color(QtGui.QPalette.ColorRole.Window)
                    base = palette.color(QtGui.QPalette.ColorRole.Base)
                    text = palette.color(QtGui.QPalette.ColorRole.Text)
                    win_rgb = (win.redF(), win.greenF(), win.blueF())
                    base_rgb = (base.redF(), base.greenF(), base.blueF())
                    text_rgb = (text.redF(), text.greenF(), text.blueF())
                except Exception:
                    pass

            self.fig = Figure(facecolor=win_rgb, constrained_layout=True)
            self.canvas = FigureCanvas(self.fig) if FigureCanvas is not None else None
            title_source = self.f_name or ""
            try:
                _title = format_annealing_title(Path(title_source).stem if title_source else "")
            except Exception:
                _title = format_annealing_title(title_source)
            self.fig.suptitle(_title, color=text_rgb)
            if NavigationToolbar is not None and self.canvas is not None:
                self.toolbar = NavigationToolbar(self.canvas, container)
                layout.addWidget(self.toolbar)
            if self.canvas is not None:
                layout.addWidget(self.canvas, 1)

            self.ax1 = self.fig.add_subplot(211)
            self.ax1.set_facecolor(base_rgb)
            self.ax1.set_xlabel("Current [mA]")
            self.ax1.set_ylabel("Resistance [Ohm]")
            self.ax1.grid(False)
            for spine in self.ax1.spines.values():
                spine.set_color(text_rgb)
            self.ax1.tick_params(colors=text_rgb)
            self.ax1.xaxis.label.set_color(text_rgb)
            self.ax1.yaxis.label.set_color(text_rgb)

            self.ax2 = self.fig.add_subplot(212)
            self.ax2.set_facecolor(base_rgb)
            self.ax2.set_xlabel("N")
            self.ax2.set_ylabel("Resistance [Ohm]")
            self.ax2.grid(False)
            for spine in self.ax2.spines.values():
                spine.set_color(text_rgb)
            self.ax2.tick_params(colors=text_rgb)
            self.ax2.xaxis.label.set_color(text_rgb)
            self.ax2.yaxis.label.set_color(text_rgb)
            self._zero_placeholder_line1 = None
            self._zero_placeholder_line2 = None
            self._zero_placeholder_count = 0
            self._zero_placeholders_active = False
            # Let Matplotlib compute proper spacing; avoid text overlap
            if self.canvas is not None:
                self.canvas.draw()
        else:
            self._plot_backend = "matplotlib"
            # Fallback to separate window
            self.fig = plt.figure(constrained_layout=True)
            self.ax1 = self.fig.add_subplot(211)
            self.ax1.set_xlabel("Current [mA]")
            self.ax1.set_ylabel("Resistance [Ohm]")
            self.ax1.grid(False)
            self.ax2 = self.fig.add_subplot(212)
            self.ax2.set_xlabel("N")
            self.ax2.set_ylabel("Resistance [Ohm]")
            self.ax2.grid(False)
            self._zero_placeholder_line1 = None
            self._zero_placeholder_line2 = None
            self._zero_placeholder_count = 0
            self._zero_placeholders_active = False
            plt.ion()
            show_plots()
        
        
    def _configure_pyqtgraph_plot(
        self,
        plot: Any,
        *,
        bottom_label: str,
        bottom_units: str,
        left_label: str,
        left_units: str,
    ) -> None:
        if pg is None or plot is None:
            return
        app = QtWidgets.QApplication.instance()
        palette = app.palette() if isinstance(app, QtWidgets.QApplication) else self.palette()
        base = palette.color(QtGui.QPalette.ColorRole.Base)
        text = palette.color(QtGui.QPalette.ColorRole.Text)
        grid = palette.color(QtGui.QPalette.ColorRole.Mid)
        plot_item = plot.getPlotItem()
        plot.setBackground(base)
        try:
            plot_item.setClipToView(True)
            plot_item.vb.setDefaultPadding(0.06)
            plot_item.layout.setContentsMargins(6, 6, 16, 6)
        except Exception:
            pass
        plot.showGrid(x=False, y=False)
        plot_item.showGrid(x=False, y=False)
        plot_item.setLabel("bottom", bottom_label, units=bottom_units)
        plot_item.setLabel("left", left_label, units=left_units)
        plot_item.setLabel("top", "")
        plot_item.setLabel("right", "")
        plot_item.showAxis("top", True)
        plot_item.showAxis("right", True)
        for axis_name in ("bottom", "left"):
            axis = plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(text))
            axis.setTextPen(pg.mkPen(text))
            axis.setGrid(False)
            axis.setStyle(maxTickLevel=0, maxTextLevel=0)
        for axis_name in ("top", "right"):
            axis = plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(text))
            axis.setTextPen(pg.mkPen(text))
            axis.setTicks([])
            axis.setGrid(False)
            axis.setStyle(showValues=False, tickLength=0, maxTickLevel=0, maxTextLevel=0)
        plot_item.getViewBox().setBackgroundColor(base)
        try:
            plot_item.ctrl.xGridCheck.setChecked(False)
            plot_item.ctrl.yGridCheck.setChecked(False)
            plot_item.getAxis("bottom").setGrid(False)
            plot_item.getAxis("left").setGrid(False)
        except Exception:
            _ = grid


    def handle_pushButton_select_filename_clicked(self):
        self.f_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save file",
            "data",
            "Text files (*.txt);;All files (*)"
        )

        if self.f_name:
            if not self.f_name.endswith(".txt"):
                self.f_name += ".txt"
            
            self.ui.lineEdit_log_file_full.setText(self.f_name)


    def handle_select_filename_en(self):
        # Choose full path via Save dialog and update new fields when available
        start_dir = self.ui.lineEdit_log_dir.text() if hasattr(self.ui, 'lineEdit_log_dir') else DEFAULT_LOG_DIR
        fpath, _ = QFileDialog.getSaveFileName(
            self,
            "Save file",
            start_dir,
            "Text files (*.txt);;All files (*)"
        )

        if fpath:
            if not fpath.endswith(".txt"):
                fpath += ".txt"
            d = os.path.dirname(fpath)
            b = self._apply_loop_suffix_to_base(os.path.splitext(os.path.basename(fpath))[0])
            if hasattr(self.ui, 'lineEdit_log_dir'):
                self.ui.lineEdit_log_dir.setText(d)
            if hasattr(self.ui, 'lineEdit_log_file'):
                self.ui.lineEdit_log_file.setText(b)
            self.ui.lineEdit_log_file_full.setText(fpath)
            self.settings.setValue("log_dir", d)
            self.settings.setValue("log_file", b)

    def handle_browse_log_dir(self):
        start_dir = self.ui.lineEdit_log_dir.text() if hasattr(self.ui, 'lineEdit_log_dir') else DEFAULT_LOG_DIR
        new_dir = QFileDialog.getExistingDirectory(self, "Select log directory", start_dir)
        if new_dir and hasattr(self.ui, 'lineEdit_log_dir'):
            self.ui.lineEdit_log_dir.setText(new_dir)
            self.settings.setValue("log_dir", new_dir)

    def handle_browse_full_file(self):
        # Unified handler to select full path then split into directory + base name
        start_dir = self.ui.lineEdit_log_dir.text() if hasattr(self.ui, 'lineEdit_log_dir') else DEFAULT_LOG_DIR
        fpath, _ = QFileDialog.getSaveFileName(
            self,
            "Save file",
            start_dir,
            "Text files (*.txt);;All files (*)"
        )
        if fpath:
            if not fpath.endswith(".txt"):
                fpath += ".txt"
            d = os.path.dirname(fpath)
            b = self._apply_loop_suffix_to_base(os.path.splitext(os.path.basename(fpath))[0])
            if hasattr(self.ui, 'lineEdit_log_dir'):
                self.ui.lineEdit_log_dir.setText(d)
            if hasattr(self.ui, 'lineEdit_log_file'):
                self.ui.lineEdit_log_file.setText(b)
            self.ui.lineEdit_log_file_full.setText(fpath)
            self.settings.setValue("log_dir", d)
            self.settings.setValue("log_file", b)

    def sync_full_log_path(self):
        # Update hidden full-path edit and internal f_name
        full = self.build_log_path()
        if hasattr(self.ui, 'lineEdit_log_file_full'):
            self.ui.lineEdit_log_file_full.setText(full)
        self.f_name = full
        try:
            d = self.ui.lineEdit_log_dir.text().strip()
            b = self.ui.lineEdit_log_file.text().strip()
            if d:
                self.settings.setValue("log_dir", d)
            if b:
                self.settings.setValue("log_file", b)
        except Exception:
            pass

    def build_log_path(self) -> str:
        try:
            d = self.ui.lineEdit_log_dir.text().strip()
            base = self.ui.lineEdit_log_file.text().strip()
            if not base:
                base = "anneal_log"
            base = self._apply_loop_suffix_to_base(base)
            if not base:
                base = "anneal_log"
            if d:
                os.makedirs(d, exist_ok=True)
                return os.path.join(d, f"{base}.txt")
        except Exception:
            pass
        # Fallback to legacy full-path field if present
        try:
            t = self.ui.lineEdit_log_file_full.text().strip()
            if t:
                return t
        except Exception:
            pass
        return os.path.join(DEFAULT_LOG_DIR, "anneal_log.txt")

    def _ensure_log_header(self, path: str) -> None:
        header_line = "# Current (mA)\tVoltage (V)\tResistance (Ohm)"
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            return
        lines = text.splitlines()
        changed = False
        if not lines:
            lines = [header_line]
            changed = True
        else:
            header_index: int | None = None
            for idx, line in enumerate(lines):
                stripped = line.strip()
                if not stripped:
                    continue
                if "Voltage" in stripped and "Resistance" in stripped:
                    header_index = idx
                    break
            if header_index is None:
                lines.insert(0, header_line)
                changed = True
            else:
                if lines[header_index].strip() != header_line:
                    lines[header_index] = header_line
                    changed = True
        if changed:
            try:
                Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
            except OSError:
                pass

    def _ui_text(self, attr: str) -> str:
        widget = getattr(self.ui, attr, None)
        if widget is None:
            return ""
        text_fn = getattr(widget, "text", None)
        if callable(text_fn):
            try:
                return str(text_fn()).strip()
            except Exception:
                return ""
        return ""

    def _source_provenance_repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _request_source_provenance(self, output_path: str) -> object:
        self._release_source_provenance()
        token = self._source_provenance_cache.request(self._source_provenance_repo_root())
        self._source_provenance_token = token
        self._source_provenance_output_path = str(output_path)
        self._source_provenance_poll_timer.start()
        return token

    @QtCore.pyqtSlot()
    def _poll_source_provenance_capture(self) -> None:
        token = self._source_provenance_token
        output_path = self._source_provenance_output_path
        snapshot = self._source_provenance_cache.snapshot(token)
        if snapshot is None or snapshot["capture_state"] == CAPTURE_PENDING:
            return
        self._source_provenance_poll_timer.stop()
        if (
            self._window_closing
            or token != self._source_provenance_token
            or output_path != self._source_provenance_output_path
            or output_path is None
        ):
            return
        self._write_source_provenance_completion(output_path, token)

    def _release_source_provenance(self) -> None:
        self._source_provenance_poll_timer.stop()
        self._source_provenance_cache.release(self._source_provenance_token)
        self._source_provenance_token = None
        self._source_provenance_output_path = None

    def _source_control_metadata(self, token: object | None = None) -> Dict[str, Any]:
        repo_root = Path(__file__).resolve().parents[2]
        snapshot = self._source_provenance_cache.snapshot(
            self._source_provenance_token if token is None else token
        )
        if snapshot is not None:
            return snapshot
        return unavailable_source_provenance(repo_root, error="capture_not_requested")

    def _metadata_payload(
        self,
        output_path: str,
        *,
        source_provenance_token: object | None = None,
    ) -> Dict[str, Any]:
        output = Path(output_path)
        loops, infinite = self._current_loop_settings()
        reverse = self._reverse_to_zero_after_max_enabled()
        supply_widget = getattr(self.ui, "comboBox_supply", None)
        supply = ""
        if supply_widget is not None:
            try:
                supply = str(supply_widget.currentText())
            except Exception:
                supply = str(getattr(self, "supply_profile_id", ""))
        supply_profile_id = str(getattr(self, "supply_profile_id", ""))
        detected_profile = getattr(self, "_detected_hmp_profile", None)
        hardware_payload = {
            "profile_id": supply_profile_id,
            "label": SUPPLY_PROFILES.get(supply_profile_id, {}).get("label", ""),
            "detected_model": None if detected_profile is None else getattr(detected_profile, "profile_id", None),
            "port": self._selected_hmp_port_name(),
            "baud": int(getattr(self, "baudrate", 0) or 0),
            "channel": int(getattr(self, "channel_select", 0) or 0),
            "voltage_limit_v": float(getattr(self, "max_voltage", 0.0) or 0.0),
            "current_resolution_mA": self._current_resolution_mA(),
            "min_positive_current_mA": self._min_positive_current_mA(),
            "shared_broker": bool(self._using_shared_broker()),
            "broker_host": self._shared_broker_host() if self._using_shared_broker() else None,
            "broker_port": self._shared_broker_port() if self._using_shared_broker() else None,
            "broker_owned_by_app": bool(self._owned_shared_broker_server is not None),
            "broker_source": "owned"
            if self._owned_shared_broker_server is not None
            else ("existing" if self._using_shared_broker() else "direct"),
            "readback_requested_hz": self._requested_hmp_readback_hz(),
            "readback_effective_hz": self._effective_hmp_command_hz(),
            "readback_cadence_generation": int(
                getattr(self, "_shared_broker_cadence_generation", 0)
            ),
        }
        diameter_um = self._diameter_um()
        return {
            "schema": "current_annealing_logger_metadata_v1",
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "data_file": output.name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "output_file": str(output_path),
            "composition": self._ui_text("lineEdit_composition"),
            "microwire": self._ui_text("lineEdit_microwire"),
            "sample": self._ui_text("lineEdit_sample"),
            "load": self._ui_text("lineEdit_load"),
            "notes": self._ui_text("lineEdit_notes"),
            "start_current_mA": int(getattr(self, "start_current_mA", 1)),
            "max_current_mA": int(getattr(self, "max_current_mA", 10)),
            "step_mA": float(getattr(self, "current_step_mA", 1.0) or 0.0),
            "reverse_enabled": reverse,
            "loops": loops,
            "loops_infinite": infinite,
            "supply": hardware_payload,
            "supply_display": supply,
            "supply_profile": supply_profile_id,
            "hardware": hardware_payload,
            "microwire_geometry": {
                "diameter_um": diameter_um,
                "diameter_mm": None if diameter_um is None else diameter_um / 1000.0,
                "diameter_imported": bool(getattr(self, "_metadata_diameter_imported", False)),
                "builder_project": self._ui_text("lineEdit_builder_project"),
                "fabrication_folder": self._ui_text("lineEdit_fabrication_folder"),
            },
            "source_control": self._source_control_metadata(source_provenance_token),
            "recipe": {
                "start_current_mA": float(getattr(self, "start_current_mA", 0.0) or 0.0),
                "max_current_mA": float(getattr(self, "max_current_mA", 0.0) or 0.0),
                "current_ramp_rate_mA_s": float(getattr(self, "current_step_mA", 0.0) or 0.0),
                "reverse_enabled": reverse,
                "loops": int(loops),
                "loops_infinite": bool(infinite),
                "infinite_loops": bool(infinite),
                "max_voltage_action": str(getattr(self, "max_voltage_action", MAX_VOLTAGE_DEFAULT_ACTION)),
            },
        }

    def _metadata_path(self, output_path: str) -> Path:
        output = Path(output_path)
        return output.parent / "metadata" / output.stem / "metadata.json"

    def _write_metadata_file(
        self,
        output_path: str,
        *,
        source_provenance_token: object | None = None,
    ) -> bool:
        output = Path(output_path)
        metadata_dir = output.parent / "metadata" / output.stem
        try:
            metadata_dir.mkdir(parents=True, exist_ok=True)
            self._metadata_path(output_path).write_text(
                json.dumps(
                    self._metadata_payload(
                        output_path,
                        source_provenance_token=source_provenance_token,
                    ),
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            message = f"Metadata sidecar write failed for {metadata_dir}: {exc}"
            LOGGER.error(message)
            self._last_run_error = message
            self._set_process_state("failed", message)
            self._show_status_message(message, timeout_ms=0)
            try:
                QtWidgets.QMessageBox.critical(self, "Metadata not saved", message)
            except Exception:
                pass
            return False
        return True

    def _write_source_provenance_completion(self, output_path: str, token: object) -> None:
        if token != self._source_provenance_token or output_path != self._source_provenance_output_path:
            return
        snapshot = self._source_provenance_cache.snapshot(token)
        if snapshot is None or snapshot["capture_state"] == CAPTURE_PENDING:
            return
        metadata_path = self._metadata_path(output_path)
        try:
            if token != self._source_provenance_token or output_path != self._source_provenance_output_path:
                return
            patch_source_control_metadata(metadata_path, snapshot, ensure_ascii=False)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            message = f"Source provenance metadata update failed for {metadata_path}: {exc}"
            LOGGER.error(message)
            self._show_status_message(message, timeout_ms=12000)
        finally:
            if token == self._source_provenance_token and output_path == self._source_provenance_output_path:
                self._release_source_provenance()

    def _evacuate_existing_output_for_replacement(self, output_path: str) -> None:
        output = Path(output_path)
        moved: list[str] = []
        metadata_dir = output.parent / "metadata" / output.stem
        for path in (output, metadata_dir):
            if not path.exists():
                continue
            destination = _move_path_to_trash(path)
            moved.append(str(destination or path))
        if moved:
            self._show_status_message(
                "Moved previous output to Trash before replacing: " + "; ".join(moved),
                timeout_ms=12000,
            )

    def prepare_output_file(self) -> bool:
        """Create or prepare the output file, prompting if it exists.

        Returns True if ready to proceed, False if the user canceled.
        """
        self._sync_runtime_settings()
        self._store_loop_preferences()
        path = self.build_log_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass

        mode = "w"
        if os.path.exists(path):
            msg = QtWidgets.QMessageBox(self)
            msg.setWindowTitle("File exists")
            msg.setIcon(QtWidgets.QMessageBox.Icon.Question)
            base = os.path.basename(path)
            msg.setText(f"'{base}' already exists.")
            msg.setInformativeText("Choose an action:")
            replace_btn = msg.addButton("Replace", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
            continue_btn = msg.addButton("Continue", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = msg.addButton("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is cancel_btn:
                return False
            elif clicked is continue_btn:
                mode = "a"
            else:
                mode = "w"
                try:
                    self._evacuate_existing_output_for_replacement(path)
                except OSError as exc:
                    QtWidgets.QMessageBox.critical(
                        self,
                        "Error",
                        f"Failed to move previous output to Trash before replacing {path}: {exc}",
                    )
                    return False
        try:
            if mode == "a":
                self._ensure_log_header(path)
            with open(path, mode, encoding="utf-8") as fh:
                if mode != "a":
                    fh.write("# Current (mA)\tVoltage (V)\tResistance (Ohm)\n")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self, "Error", f"Failed to open {path}: {exc}"
            )
            return False

        self.f_name = path
        source_provenance_token = self._request_source_provenance(path)
        if not self._write_metadata_file(
            path,
            source_provenance_token=source_provenance_token,
        ):
            if source_provenance_token == self._source_provenance_token:
                self._release_source_provenance()
            return False
        # subsequent writes will append
        return True

    def populate_ports(self):
        if hasattr(self.ui, 'comboBox_port'):
            self.ui.comboBox_port.clear()
            # 1) Normal OS-reported ports
            seen: set[str] = set()
            port_infos = list(QSerialPortInfo.availablePorts())

            def _identity(info: QSerialPortInfo) -> SerialPortIdentity:
                sysloc = info.systemLocation() if hasattr(info, 'systemLocation') else info.portName()
                try:
                    manufacturer = info.manufacturer()
                except Exception:
                    manufacturer = ""
                try:
                    vid = info.vendorIdentifier() if info.hasVendorIdentifier() else None
                except Exception:
                    vid = None
                try:
                    pid = info.productIdentifier() if info.hasProductIdentifier() else None
                except Exception:
                    pid = None
                try:
                    description = info.description()
                except Exception:
                    description = ""
                return SerialPortIdentity(
                    device=str(sysloc or info.portName()),
                    description=str(description or ""),
                    manufacturer=str(manufacturer or ""),
                    vid=vid,
                    pid=pid,
                )

            for info in sorted(port_infos, key=lambda item: hmp_port_preference_key(_identity(item))):
                sysloc = info.systemLocation() if hasattr(info, 'systemLocation') else info.portName()
                name = info.portName()
                label = name
                try:
                    if info.description():
                        label += f" - {info.description()}"
                except Exception:
                    pass
                self.ui.comboBox_port.addItem(label, userData=(sysloc or name))
                seen.add(sysloc or name)
            # 2) Extra virtual symlinks (macOS/Linux): /dev/cu.ttyV*
            try:
                import platform
                from glob import glob
                if platform.system() in {"Darwin", "Linux"}:
                    extras = sorted(set(glob("/dev/cu.ttyV*") + glob("/dev/ttyV*") + glob(str(Path.cwd()/"ttyV*"))))
                    for path in extras:
                        rp = os.path.realpath(path)
                        name = os.path.basename(rp) if rp.startswith('/dev/') else os.path.basename(path)
                        label = f"{os.path.basename(path)} - Virtual pair"
                        if name not in seen:
                            self.ui.comboBox_port.insertItem(0, label, userData=name)
                            seen.add(name)
            except Exception:
                pass
            if self.ui.comboBox_port.count() > 0:
                self.port_name = self.ui.comboBox_port.currentData()

    def _teardown_gui_callbacks(self) -> None:
        if self._callbacks_torn_down:
            return
        self._callbacks_torn_down = True
        timer_callbacks = (
            (self.timer, self.handle_update_serial_response_label),
            (self.time_timer, self.update_time_estimate),
            (self.timer_command, self.handle_send_new_command),
            (self._fabrication_poll_timer, self._poll_fabrication_tasks),
            (self._source_provenance_poll_timer, self._poll_source_provenance_capture),
            (self._delay_timer, self._finish_simple_delay),
        )
        for timer, callback in timer_callbacks:
            timer.stop()
            try:
                timer.timeout.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        self.wait = False
        self._disconnect_serial_ready_read()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        try:
            WINDOWS.remove(self)
        except ValueError:
            pass
        if self._callbacks_torn_down:
            super().closeEvent(event)
            return
        if self._close_in_progress:
            event.accept()
            return
        self._close_in_progress = True
        try:
            self._window_closing = True
            self._closing_safe_end = True
            try:
                if self.ser_mcu.isOpen():
                    self.handle_connect_port_clicked()
            finally:
                self._closing_safe_end = False
            self._teardown_gui_callbacks()
            self._release_source_provenance()
            self._cancel_fabrication_folder_load()
            self._wait_for_fabrication_tasks(timeout_ms=250)
            self._release_experiment_sleep_guard()
            self._stop_owned_shared_broker()

            super().closeEvent(event)
        finally:
            self._close_in_progress = False

    # --- Overlay helpers
    def _setup_connect_overlay(self) -> None:
        scroll = getattr(self.ui, 'left_scroll', None)
        if scroll is None:
            self._overlay = None
            return
        ov = QtWidgets.QFrame(scroll.viewport())
        # Stronger blur/dim overlay
        ov.setStyleSheet("background: rgba(0,0,0,160);")
        layout = QtWidgets.QVBoxLayout(ov)
        layout.setContentsMargins(0, 0, 0, 0)
        msg = QtWidgets.QLabel(self._connect_overlay_message())
        msg.setStyleSheet("color: white; font-size: 18px; font-weight: 700;")
        msg.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(msg)
        layout.addStretch(1)
        self._overlay = ov
        self._overlay_label = msg
        self._position_connect_overlay()
        ov.hide()

    def _position_connect_overlay(self) -> None:
        scroll = getattr(self.ui, 'left_scroll', None)
        ov = getattr(self, '_overlay', None)
        if scroll is None or ov is None:
            return
        try:
            serial_frame = getattr(self.ui, 'frame_serial_settings', None)
            if serial_frame is not None:
                pt = serial_frame.mapTo(scroll.viewport(), QtCore.QPoint(0, serial_frame.height()))
                y = pt.y() + 8
            else:
                y = 0
            vp = scroll.viewport().rect()
            ov.setGeometry(0, max(0, y), vp.width(), max(0, vp.height()-max(0, y)))
        except Exception:
            ov.setGeometry(scroll.viewport().rect())

    def _show_connect_overlay(self, show: bool) -> None:
        overlay = getattr(self, '_overlay', None)
        if overlay is None:
            return
        try:
            label = getattr(self, "_overlay_label", None)
            if isinstance(label, QtWidgets.QLabel):
                label.setText(self._connect_overlay_message())
            overlay.setVisible(False)
        except Exception:
            pass

    def resizeEvent(self, ev: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(ev)
        self._resize_pyqtgraph_placeholders()
        scroll = getattr(self.ui, 'left_scroll', None)
        overlay = getattr(self, '_overlay', None)
        if overlay is not None and scroll is not None:
            try:
                self._position_connect_overlay()
            except Exception:
                pass


WINDOWS: list[QtWidgets.QWidget] = []


def main() -> QtWidgets.QWidget:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        qt_app = QtWidgets.QApplication(sys.argv)
        owns_app = True
    else:
        qt_app = cast(QtWidgets.QApplication, app)

    ensure_app_theme(qt_app)
    _apply_app_font_to_matplotlib(qt_app)

    window = MainWindow()
    window.showMaximized()
    WINDOWS.append(window)

    if owns_app:
        sys.exit(qt_app.exec())
    return window


if __name__ == "__main__":
    main()
    
