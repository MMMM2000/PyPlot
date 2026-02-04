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
from datetime import datetime
from pathlib import Path
from collections import deque
from importlib import import_module
from typing import Any, Deque, Dict, List, Optional, SupportsBytes, TextIO, Tuple, cast

from PyQt6 import QtCore, QtWidgets, QtSerialPort, QtGui
from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtSerialPort import QSerialPortInfo

from .ui_en import Ui_MainWindow
from plotting.shared.utils import ensure_app_theme, format_annealing_title, show_plots, install_standard_menu
from data_logging.naming_history import LineEditHistory
from data_logging.data_logger.file_name_builder import composition_warning_state

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.figure import Figure

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
    "hold": "Hold current (stop increasing)",
    "reverse": "Reverse to zero",
    "stop": "Stop measurement",
}

SUPPLY_PROFILES: Dict[str, Dict[str, Any]] = {
    "hmp4030": {
        "label": "HMP4030 (original)",
        "start_current_mA": 1,
        "min_start_current_mA": 1,
        "max_voltage": 30.0,
        "channel_select": 3,
        "reset_on_start": True,
        "voltage_first": False,
    },
    "owon_spe6102": {
        "label": "Owon SPE6102",
        "start_current_mA": 10,
        "min_start_current_mA": 10,
        "max_voltage": 62.0,
        "channel_select": 0,
        "reset_on_start": False,
        "voltage_first": True,
    },
}


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
            tabs = QtWidgets.QTabWidget(self)
            for idx, entry in enumerate(entries, start=1):
                tab = QtWidgets.QWidget()
                tab_layout = QtWidgets.QVBoxLayout(tab)
                tab_layout.setContentsMargins(0, 0, 0, 0)
                tab_layout.setSpacing(6)
                title = entry.get("title") or f"Measurement {idx}"
                currents = entry.get("currents", [])
                resistances = entry.get("resistances", [])
                timestamp = entry.get("timestamp", "")
                source = entry.get("source", "")
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
                            ax.plot(currents, resistances, color='#d32f2f', marker='o', linestyle='None')
                        else:
                            for point in range(1, len(currents)):
                                prev_c, curr_c = currents[point - 1], currents[point]
                                prev_r, curr_r = resistances[point - 1], resistances[point]
                                diff = curr_c - prev_c
                                if abs(diff) <= 0.2:
                                    color = '#27ae60'
                                elif diff >= 0:
                                    color = '#d32f2f'
                                else:
                                    color = '#1976d2'
                                ax.plot(
                                    [prev_c, curr_c],
                                    [prev_r, curr_r],
                                    color=color,
                                    marker='o',
                                    linestyle='-',
                                )
                    tab_layout.addWidget(canvas)
                else:
                    placeholder = QtWidgets.QLabel("Matplotlib backend not available.")
                    placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                    placeholder.setMinimumHeight(160)
                    tab_layout.addWidget(placeholder)
                details: list[str] = []
                if timestamp:
                    details.append(timestamp)
                if source:
                    details.append(source)
                if details:
                    info = QtWidgets.QLabel("\n".join(details))
                    info.setWordWrap(True)
                    tab_layout.addWidget(info)
                tab_layout.addStretch(1)
                tabs.addTab(tab, title)
            layout.addWidget(tabs)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


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
        self.max_voltage = 30.0
        self.reset_on_start = True
        self.channel_select = 3
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
        self.timer = QtCore.QTimer()
        self.timer.stop();
        self.timer.timeout.connect(self.handle_update_serial_response_label)
        self.timer.start(50)
        # timer for time remaining label
        self.time_timer = QtCore.QTimer()
        self.time_timer.timeout.connect(self.update_time_estimate)
        self.time_timer.start(1000)
        
        # Timer managing the hold-current duration
        self.elapsed_seconds = 0
        self.hold_timer_running = False
        self.hold_timer = QtCore.QTimer()
        self.hold_timer.stop();
        self.hold_timer.timeout.connect(self.handle_hold_timer_timeout)
        
        # Timer that schedules outgoing commands
        self.command_number = 0
        self.timer_command = QtCore.QTimer()
        self.timer_command.stop();
        self.timer_command.timeout.connect(self.handle_send_new_command)
        
        self.f_name: str | None = None
        self.f_out: TextIO | None = None
        self.sample_window_size = 1000
        self.sample_index = 0
        self.recording_enabled = False
        self.expecting_voltage = True

        self.resistance_drop_percent = 10
        self.hold_duration_s = 1
        if not hasattr(self, "max_current_mA"):
            self.max_current_mA = 10
        self.operation_mode = 2  # 0 - VCP, 1 - manual, 2 - automatic (default auto)
        self.process_running = False

        self.current_current_set = self._start_current_A()
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
        self._segment_lines_ax1: list[Line2D] = []
        self._segment_lines_ax2: list[Line2D] = []
        self._placeholder_text_ax1: Any = None
        self._placeholder_text_ax2: Any = None
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
        self.ui.pushButton_send_serial_command.clicked.connect(self.handle_send_serial_command_clicked)
        
        
        self.ui.spinBox_max_current.valueChanged.connect(self.handle_max_current_value_changed)
        if hasattr(self.ui, 'spinBox_max_voltage'):
            self.ui.spinBox_max_voltage.valueChanged.connect(self.handle_max_voltage_value_changed)
        if hasattr(self.ui, 'spinBox_channel'):
            self.ui.spinBox_channel.valueChanged.connect(self.handle_channel_select_value_changed)
        if hasattr(self.ui, 'checkBox_reset_on_start'):
            self.ui.checkBox_reset_on_start.toggled.connect(self.handle_reset_on_start_toggled)
        if hasattr(self.ui, 'spinBox_start_current'):
            self.ui.spinBox_start_current.valueChanged.connect(self.handle_start_current_value_changed)
        self.ui.spinBox_hold_duration.valueChanged.connect(self.handle_hold_duration_value_changed)
        self.ui.pushButton_hold_current.clicked.connect(self.handle_hold_current_button_clicked)
        
        self.ui.pushButton_start_process.clicked.connect(self.handle_toggle_process_clicked)
        self.ui.lineEdit_log_file_full.textChanged.connect(self.handle_legacy_log_path_changed)
        self.ui.pushButton_select_filename.clicked.connect(self.handle_select_filename_en)
        # Also hook legacy browse button to new unified handler
        if hasattr(self.ui, 'pushButton_select_filename'):
            self.ui.pushButton_select_filename.clicked.connect(self.handle_browse_full_file)
        if hasattr(self.ui, 'pushButton_reverse_now'):
            self.ui.pushButton_reverse_now.clicked.connect(self.handle_pushButton_reverse_now_clicked)
            self.ui.pushButton_reverse_now.setEnabled(False)
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
        self.ui.spinBox_max_current.valueChanged.connect(self.update_file_name_from_preset)
        self.ui.spinBox_max_current.valueChanged.connect(self.update_planned_time_label)
        self.ui.spinBox_hold_duration.valueChanged.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'checkBox_infinite_loops'):
            self.ui.checkBox_infinite_loops.toggled.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'spinBox_step_mA'):
            self.ui.spinBox_step_mA.valueChanged.connect(self.handle_step_changed)

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
        
        # Disable process controls by default until a port is connected
        self.ui.frame_process_settings.setEnabled(False)
        self.ui.frame_command_and_response.setEnabled(False)

        # Connection overlay over the left panel until port is connected
        self._setup_connect_overlay()
        if hasattr(self, 'is_connected') and not self.is_connected:
            self._show_connect_overlay(True)
        
        self.max_resistance = 0
        
        self.resistance_at_hold_current = 0
        self.resistance_percent_from_hold = 0
        
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
                
        self.line_color="r"
        # Initialize progress UI defaults
        if hasattr(self.ui, 'progressBar_process'):
            self.ui.progressBar_process.setMaximum(0)
            self.ui.progressBar_process.setValue(0)
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

    # utilities
    def dbg(self, *args):
        if getattr(self, 'DEBUG', False):
            try:
                print(*args)
            except Exception:
                pass

    def _record_zero_placeholder(self) -> None:
        """Visualise leading zero-current samples without persisting them."""

        if self._nonzero_current_seen:
            return
        ax1 = getattr(self, 'ax1', None)
        ax2 = getattr(self, 'ax2', None)
        if ax1 is None or ax2 is None:
            return

        if self._zero_placeholder_line1 is None:
            marker = Line2D([], [], linestyle='None', marker='o', color=self.line_color)
            try:
                marker.set_markersize(5)
            except Exception:
                pass
            ax1.add_line(marker)
            self._zero_placeholder_line1 = marker
        if self._zero_placeholder_line2 is None:
            marker = Line2D([], [], linestyle='None', marker='o', color=self.line_color)
            try:
                marker.set_markersize(5)
            except Exception:
                pass
            ax2.add_line(marker)
            self._zero_placeholder_line2 = marker

        self._zero_placeholder_count += 1
        zeros = [0.0] * self._zero_placeholder_count
        indices = list(range(self._zero_placeholder_count))
        if self._zero_placeholder_line1 is not None:
            self._zero_placeholder_line1.set_data(zeros, zeros)
        if self._zero_placeholder_line2 is not None:
            self._zero_placeholder_line2.set_data(indices, zeros)

        for axis in (ax1, ax2):
            axis.relim()
            axis.autoscale_view()

        canvas = getattr(self, 'canvas', None)
        if canvas is not None:
            try:
                canvas.draw_idle()
                canvas.flush_events()
            except Exception:
                canvas.draw()
        self._zero_placeholders_active = True

    def _clear_zero_placeholders(self) -> None:
        """Remove any temporary zero-current markers from the plots."""

        if not self._zero_placeholders_active and self._zero_placeholder_count == 0:
            return

        for line in (self._zero_placeholder_line1, self._zero_placeholder_line2):
            if line is None:
                continue
            try:
                line.remove()
            except Exception:
                pass

        self._zero_placeholder_line1 = None
        self._zero_placeholder_line2 = None
        self._zero_placeholder_count = 0
        self._zero_placeholders_active = False

        ax1 = getattr(self, 'ax1', None)
        ax2 = getattr(self, 'ax2', None)
        for axis in (ax1, ax2):
            if axis is not None:
                axis.relim()
                axis.autoscale_view()

        canvas = getattr(self, 'canvas', None)
        if canvas is not None:
            try:
                canvas.draw_idle()
                canvas.flush_events()
            except Exception:
                canvas.draw()

    def _display_ui_value(self, attr: str, text: str) -> None:
        if attr == 'label_live_voltage':
            self._set_live_voltage_text(text)
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
            if len(entries) >= 3:
                break
        return entries

    def _update_history_button_state(self) -> None:
        button = getattr(self.ui, 'pushButton_show_history', None)
        if isinstance(button, QtWidgets.QPushButton):
            button.setEnabled(bool(self._measurement_history))

    def _save_measurement_history(self) -> None:
        payload: List[Dict[str, Any]] = []
        for entry in self._measurement_history[:3]:
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
        self._clear_segment_lines()

    def _clear_segment_lines(self) -> None:
        for container in (self._segment_lines_ax1, self._segment_lines_ax2):
            for line in list(container):
                try:
                    line.remove()
                except Exception:
                    pass
            container.clear()

    def _remove_placeholder_text(self) -> None:
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

    def _append_measurement_sample(self, current_mA: float, resistance: float) -> None:
        if not math.isfinite(current_mA) or not math.isfinite(resistance):
            return
        self._remove_placeholder_text()
        self._samples_current.append(float(current_mA))
        self._samples_resistance.append(float(resistance))
        if len(self._samples_current) > 1:
            step_value = abs(float(getattr(self, 'current_step_mA', 1) or 1))
            tolerance = max(0.5, step_value * 0.6)
            trimmed_currents: List[float] = []
            trimmed_resistances: List[float] = []
            total = len(self._samples_current)
            for idx, (curr, res) in enumerate(zip(self._samples_current, self._samples_resistance)):
                if abs(curr - 1.0) <= tolerance and idx < total - 1:
                    continue
                trimmed_currents.append(curr)
                trimmed_resistances.append(res)
            if len(trimmed_currents) != len(self._samples_current):
                self._samples_current = trimmed_currents
                self._samples_resistance = trimmed_resistances
        self.sample_index = len(self._samples_current)
        self._redraw_segments()

    def _redraw_segments(self) -> None:
        ax1 = getattr(self, 'ax1', None)
        ax2 = getattr(self, 'ax2', None)
        if ax1 is None or ax2 is None:
            return
        self._clear_segment_lines()
        currents = list(self._samples_current)
        resistances = list(self._samples_resistance)
        if not currents:
            canvas = getattr(self, 'canvas', None)
            if canvas is not None:
                try:
                    canvas.draw_idle()
                except Exception:
                    canvas.draw()
            return
        if len(currents) == 1:
            marker1 = Line2D([currents[0]], [resistances[0]], color='r', marker='o', linestyle='None')
            marker2 = Line2D([1], [resistances[0]], color='r', marker='o', linestyle='None')
            ax1.add_line(marker1)
            ax2.add_line(marker2)
            self._segment_lines_ax1.append(marker1)
            self._segment_lines_ax2.append(marker2)
        else:
            step_value = abs(float(getattr(self, 'current_step_mA', 1) or 1))
            tolerance = max(0.5, step_value * 0.6)
            for idx in range(1, len(currents)):
                prev_c = currents[idx - 1]
                curr_c = currents[idx]
                prev_r = resistances[idx - 1]
                curr_r = resistances[idx]
                diff = curr_c - prev_c
                if abs(diff) <= tolerance * 0.2:
                    color = '#27ae60'
                elif diff >= 0:
                    color = '#d32f2f'
                else:
                    color = '#1976d2'
                seg1 = Line2D([prev_c, curr_c], [prev_r, curr_r], color=color, marker='o', linestyle='-')
                seg2 = Line2D([idx, idx + 1], [prev_r, curr_r], color=color, marker='o', linestyle='-')
                ax1.add_line(seg1)
                ax2.add_line(seg2)
                self._segment_lines_ax1.append(seg1)
                self._segment_lines_ax2.append(seg2)
        for axis in (ax1, ax2):
            axis.relim()
            axis.autoscale_view()
        canvas = getattr(self, 'canvas', None)
        if canvas is not None:
            try:
                canvas.draw_idle()
                canvas.flush_events()
            except Exception:
                canvas.draw()

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
        self._measurement_history = self._measurement_history[:3]
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

    def _set_port_controls_enabled(self, enabled: bool) -> None:
        for name in ('spinBox_port_number', 'comboBox_baudrate', 'comboBox_port', 'pushButton_refresh_ports'):
            w = getattr(self.ui, name, None)
            if w is not None:
                w.setEnabled(enabled)

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

    def _write_sample_to_file(self, *, initial_sample: bool) -> None:
        """Persist the latest sample to disk if appropriate."""

        if initial_sample or not self.f_name:
            return
        current_mA = float(self.current_current_read) * 1000.0
        voltage = float(self.current_voltage)
        resistance = float(self.current_resistance)
        if not math.isfinite(current_mA) or not math.isfinite(resistance):
            return
        if not self.f_out:
            try:
                Path(self.f_name).parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            try:
                self.f_out = open(self.f_name, "a", encoding="utf-8")
            except OSError:
                self.f_out = None
        if self.f_out:
            line = "\t".join(
                [
                    self._format_sample_value(current_mA),
                    self._format_sample_value(voltage),
                    self._format_sample_value(resistance),
                ]
            ) + "\n"
            self.f_out.write(line)
            self.f_out.close()
            self.f_out = None

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
                self.ser_mcu.readyRead.connect(self.handle_ser_mcu_readyRead)
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

        else:
            if self.process_running:
                self.handle_toggle_process_clicked()
            else:
                self.send_safe_end_commands()
            # Proactively disconnect signal-slot before closing the port
            try:
                self.ser_mcu.readyRead.disconnect(self.handle_ser_mcu_readyRead)
            except Exception:
                pass
            self.ser_mcu.close()
            self.is_connected = False
            self.ui.pushButton_connect_port.setText('Connect to port')
            self._show_connect_overlay(True)
            self.ui.frame_command_and_response.setEnabled(False)
            self.ui.frame_process_settings.setEnabled(False)
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

    def handle_ser_mcu_readyRead(self):
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
                    if abs(self.current_current_read) < 1e-12:
                        self._skip_current_sample = True
                        try:
                            now = time.monotonic()
                        except Exception:
                            now = None
                        self._zero_current_count += 1
                        # Treat a zero reading as a valid response so callers
                        # waiting on ``sample_ready`` do not interpret the
                        # timeout as a communication failure.
                        self.sample_ready = True
                        if not self._nonzero_current_seen:
                            self._record_zero_placeholder()
                            # Ignore sustained zero readings until we have
                            # confirmed the setup is capable of sourcing
                            # current at least once. This prevents false
                            # alarms immediately after a process starts when
                            # the supply has not ramped yet.
                            self._last_nonzero_current_time = None
                            self.lock.unlock()
                            return
                        zero_limit = 6
                        zero_delay = 2.0
                        if (
                            now is not None
                            and self._process_start_time is not None
                            and (now - self._process_start_time) < self._contact_grace_period
                        ):
                            self.lock.unlock()
                            return
                        if (
                            now is not None
                            and self._last_nonzero_current_time is not None
                            and (now - self._last_nonzero_current_time) < zero_delay
                        ):
                            self.lock.unlock()
                            return
                        if self._zero_current_count < zero_limit:
                            self.lock.unlock()
                            return
                        if not self._contact_lost:
                            self._contact_lost = True
                            QtWidgets.QMessageBox.warning(
                                self,
                                "Contact lost",
                                "Measured current is zero. The wire likely burned through. Stopping the process.",
                            )
                            if self.process_running:
                                self.stop_annealing("Contact lost; stopping measurement.", show_dialog=False)
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
                    initial_sample = self.first_sample
                    self._write_sample_to_file(initial_sample=initial_sample)
                    if not initial_sample:
                        # progress and rate tracking on each sample
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
                if (
                    self.current_increment > 0
                    and self.current_voltage >= self.max_voltage
                    and not self._max_voltage_dialog
                ):
                    self.handle_max_voltage()
                self.sample_ready = True
            self.lock.unlock()
                    
    def handle_update_serial_response_label(self):
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
        step_mA = abs(int(getattr(self, 'current_step_mA', 1) or 1))
        if step_mA <= 0:
            step_mA = 1
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
        try:
            hold_steps = int(self.ui.spinBox_hold_duration.value())
        except Exception:
            hold_steps = int(getattr(self, 'hold_duration_s', 0))
        if limit_value < planned_max - tolerance:
            hold_steps = 0
        reverse_steps = ascend_steps if getattr(self, 'reverse_enabled', False) else 0
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

    def update_time_estimate(self):
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

    @staticmethod
    def _percent_from_hold(current_resistance: float, hold_resistance: float) -> float | None:
        try:
            current_resistance = float(current_resistance)
            hold_resistance = float(hold_resistance)
        except Exception:
            return None
        if not math.isfinite(current_resistance) or not math.isfinite(hold_resistance):
            return None
        if hold_resistance == 0.0:
            return None
        return (current_resistance / hold_resistance) * 100.0

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

    def _init_supply_profile(self) -> None:
        combo = getattr(self.ui, 'comboBox_supply', None)
        if not isinstance(combo, QtWidgets.QComboBox):
            return
        combo.blockSignals(True)
        combo.clear()
        for key, profile in SUPPLY_PROFILES.items():
            combo.addItem(profile["label"], key)
        stored = self.settings.value("supply_profile", "hmp4030")
        idx = combo.findData(stored)
        if idx < 0:
            idx = combo.findData("hmp4030")
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        selected = combo.currentData()
        if isinstance(selected, str):
            self._apply_supply_profile(selected)

    def _apply_supply_profile(self, profile_id: str) -> None:
        profile = SUPPLY_PROFILES.get(profile_id, SUPPLY_PROFILES["hmp4030"])
        self.supply_profile_id = profile_id
        self.min_start_current_mA = int(profile.get("min_start_current_mA", 1))
        self.voltage_first = bool(profile.get("voltage_first", False))
        # Apply defaults to UI and internal state.
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
        volt_spin = getattr(self.ui, 'spinBox_max_voltage', None)
        if isinstance(volt_spin, QtWidgets.QSpinBox):
            default_voltage = float(profile.get("max_voltage", 30.0))
            voltage_value = self._load_profile_setting(profile_id, "max_voltage", default_voltage, float)
            volt_spin.blockSignals(True)
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
        channel_spin = getattr(self.ui, 'spinBox_channel', None)
        if isinstance(channel_spin, QtWidgets.QSpinBox):
            default_channel = int(profile.get("channel_select", 0))
            channel_value = int(self._load_profile_setting(profile_id, "channel_select", default_channel, int))
            channel_spin.blockSignals(True)
            channel_spin.setValue(int(channel_value))
            channel_spin.blockSignals(False)
            self.channel_select = int(channel_spin.value())
        max_spin = getattr(self.ui, 'spinBox_max_current', None)
        if isinstance(max_spin, QtWidgets.QSpinBox):
            if max_spin.value() < self.start_current_mA:
                max_spin.blockSignals(True)
                max_spin.setValue(self.start_current_mA)
                max_spin.blockSignals(False)
            self.max_current_mA = int(max_spin.value())
            try:
                self.settings.setValue("max_current", self.max_current_mA)
            except Exception:
                pass
        try:
            self.settings.setValue("supply_profile", profile_id)
        except Exception:
            pass
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
        if isinstance(step_spin, QtWidgets.QSpinBox):
            try:
                step_spin.interpretText()
            except Exception:
                pass
            try:
                self.current_step_mA = int(step_spin.value())
                self.current_step_A = self.current_step_mA / 1000.0
            except Exception:
                pass
        hold_spin = getattr(self.ui, 'spinBox_hold_duration', None)
        if isinstance(hold_spin, QtWidgets.QSpinBox):
            try:
                hold_spin.interpretText()
            except Exception:
                pass
            try:
                self.hold_duration_s = int(hold_spin.value())
            except Exception:
                pass
        volt_spin = getattr(self.ui, 'spinBox_max_voltage', None)
        if isinstance(volt_spin, QtWidgets.QSpinBox):
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
            commands_init.append(f"CURR {start_a:.3f}\n")
        else:
            commands_init.append(f"CURR {start_a:.3f}\n")
            commands_init.append(f"VOLT {limit_v:.1f}\n")
        commands_init.append("OUTP ON\n")
        self.commands_init = commands_init
        safe_end = [
            "OUTP OFF\n",
            "VOLT 1.0\n",
            f"CURR {start_a:.3f}\n",
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
            hold_s = int(self.ui.spinBox_hold_duration.value())
            loops = int(self.ui.spinBox_loops.value()) if hasattr(self.ui, 'spinBox_loops') else 1
            reverse = bool(self.ui.checkBox_reverse.isChecked()) if hasattr(self.ui, 'checkBox_reverse') else False
            infinite = bool(self.ui.checkBox_infinite_loops.isChecked()) if hasattr(self.ui, 'checkBox_infinite_loops') else False
            step_mA = int(self.ui.spinBox_step_mA.value()) if hasattr(self.ui, 'spinBox_step_mA') else 1
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
        step_mA = max(1, step_mA)
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
        self.ui.lineEdit_microwire.setText(s.value("microwire", DEFAULT_PRESET["microwire"]))
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
            self.current_step_mA = int(self.ui.spinBox_step_mA.value())
        except Exception:
            self.current_step_mA = 1
        self.current_step_A = self.current_step_mA/1000.0
        self.update_planned_time_label()

    def handle_send_serial_command_clicked(self):
        self.serial_command = self.ui.lineEdit_serial_command.text() + "\n"
        self.send_serial_command()
        
    def send_serial_command(self):
        self.ser_mcu.write(bytes(self.serial_command, encoding='ascii'))
        self.ui.label_last_command.setText(self.serial_command)
        
    def handle_raw_vcp_mode_selected(self):
        self.operation_mode = 0
        self.ui.frame_process_settings.setEnabled(False)

    def handle_manual_mode_selected(self):
        self.operation_mode = 1
        self.ui.frame_process_settings.setEnabled(True)
        self.ui.spinBox_max_current.setEnabled(False)
        self.ui.spinBox_hold_duration.setEnabled(False)
        self.ui.pushButton_hold_current.setEnabled(True)

    def handle_automatic_mode_selected(self):
        self.operation_mode = 2
        self.ui.frame_process_settings.setEnabled(True)
        self.ui.spinBox_max_current.setEnabled(True)
        self.ui.spinBox_hold_duration.setEnabled(True)
        self.ui.pushButton_hold_current.setEnabled(False)

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
                self._refresh_command_profiles()
            except Exception:
                pass

    def handle_max_voltage_value_changed(self):
        spin = getattr(self.ui, 'spinBox_max_voltage', None)
        if not isinstance(spin, QtWidgets.QSpinBox):
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
        
    def handle_hold_duration_value_changed(self):
        self.hold_duration_s = self.ui.spinBox_hold_duration.value()
        
    def handle_hold_current_button_clicked(self):
        if not self.hold_timer_running:
            self.current_increment = 0.000
            self.line_color="g"
            self.elapsed_seconds = 0
            self.resistance_at_hold_current = self.current_resistance
            self.ui.label_resistance_at_hold_current.setText("{:.1f}".format(self.resistance_at_hold_current))
            self.hold_timer.start(1000)
            self.hold_timer_running = True
            self.ui.pushButton_hold_current.setText("Stop current now!")
            self.direction_ascending = False
            self._reset_voltage_projection()
        else:
            self.hold_timer.stop()
            self.current_increment = -self.current_step_A
            self.line_color="b"
            self.hold_timer_running = False
            self.ui.pushButton_hold_current.setText("Hold current now!")
            self.direction_ascending = False
            self._reset_voltage_projection()

    def handle_hold_timer_timeout(self):
        self.elapsed_seconds += 1
        percent = self._percent_from_hold(self.current_resistance, self.resistance_at_hold_current)
        if percent is None:
            self.resistance_percent_from_hold = 0.0
            self.ui.label_resistance_percent_from_hold.setText("N/A")
            return
        self.resistance_percent_from_hold = percent
        self.ui.label_resistance_percent_from_hold.setText(f"{self.resistance_percent_from_hold:.1f}")

    def handle_show_history_clicked(self) -> None:
        dialog = MeasurementHistoryDialog(self, list(self._measurement_history))
        dialog.exec()

    def handle_toggle_process_clicked(self):
        if not self.process_running:
            self.process_running = True
            self._update_mode_action_state()
            self._sync_runtime_settings()
            self._refresh_command_profiles()
            self.elapsed_seconds = 0
            self._max_voltage_dialog = False
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
                    return
                self._record_name_history()
                if hasattr(self.ui, 'progressBar_process'):
                    self.ui.progressBar_process.setMaximum(0)
                    self.ui.progressBar_process.setValue(0)
                if hasattr(self.ui, 'label_time_remaining'):
                    self.ui.label_time_remaining.setText("Time remaining: N/A")
                if hasattr(self.ui, 'label_time_to_limit'):
                    self.ui.label_time_to_limit.setText(self._format_voltage_limit_label())
                self.current_increment = self.current_step_A
                self.direction_ascending = True
                self.current_current_set = self._start_current_A()
                self._display_ui_value('label_set_current', f"{self.current_current_set*1000:.1f}")
                self.temp_resistance_maximum = 0
                self.current_voltage = 0
                self.current_resistance = 0
                self._display_ui_value('lcd_current_mA', "0")
                self._display_ui_value('label_live_voltage', "0")
                self.ui.label_resistance_at_hold_current.setText("0")
                self.ui.label_resistance_percent_from_hold.setText("0")
                self.line_color="r"
                self.init_graph_window()
                self.send_init_commands()
                # Immediately request the first sample instead of waiting
                # for the one-second timer interval to elapse.  This avoids
                # an unnecessary pause after the user presses *Start*.
                self.handle_send_new_command()
                self.timer_command.start(1000)
                
            elif(self.operation_mode == 2):
                # Prepare output file with overwrite prompt
                if not self.prepare_output_file():
                    self.process_running = False
                    self._process_start_time = None
                    self.ui.pushButton_start_process.setText("Start annealing process")
                    self._restore_idle_controls()
                    return
                self._record_name_history()
                self.current_increment = self.current_step_A
                self.direction_ascending = True
                self.current_current_set = self._start_current_A()
                self._display_ui_value('label_set_current', f"{self.current_current_set*1000:.1f}")
                self.temp_resistance_maximum = 0
                self.current_voltage = 0
                self.current_resistance = 0
                self._display_ui_value('lcd_current_mA', "0")
                self._display_ui_value('label_live_voltage', "0")
                self._warn_start_at_max()
                # reverse + loop configuration
                self.reverse_enabled = getattr(self.ui, 'checkBox_reverse', None) is not None and self.ui.checkBox_reverse.isChecked()
                self.loop_target = self.ui.spinBox_loops.value() if hasattr(self.ui, 'spinBox_loops') else 1
                self.infinite_loops = bool(self.ui.checkBox_infinite_loops.isChecked()) if hasattr(self.ui, 'checkBox_infinite_loops') else False
                self.loop_idx = 0
                # progress plan
                step_mA = self.current_step_mA if hasattr(self, 'current_step_mA') else 1
                try:
                    start_mA = int(self.ui.spinBox_start_current.value()) if hasattr(self.ui, 'spinBox_start_current') else 1
                except Exception:
                    start_mA = 1
                min_start = int(getattr(self, "min_start_current_mA", 1))
                min_start = max(1, min_start)
                max_mA = int(self.ui.spinBox_max_current.value())
                if max_mA < min_start:
                    start_mA = max_mA
                else:
                    start_mA = max(min_start, min(start_mA, max_mA))
                step_mA = max(1, step_mA)
                up_steps = max(0, math.ceil(max(0, int(self.ui.spinBox_max_current.value()) - start_mA) / step_mA))
                hold_steps = int(self.ui.spinBox_hold_duration.value())
                down_steps = up_steps if self.reverse_enabled else 0
                per_loop = max(1, up_steps + hold_steps + down_steps)
                self._init_loop_tracking(per_loop, int(self.loop_target or 1), self.infinite_loops)
                if hasattr(self.ui, 'progressBar_process'):
                    if self.total_steps:
                        self.ui.progressBar_process.setMaximum(self.total_steps)
                        self.ui.progressBar_process.setValue(0)
                    else:
                        self.ui.progressBar_process.setMaximum(0)
                if hasattr(self.ui, 'label_time_to_limit'):
                    self.ui.label_time_to_limit.setText(self._format_voltage_limit_label())
                self.ui.label_resistance_at_hold_current.setText("0")
                self.ui.label_resistance_percent_from_hold.setText("0")
                self.line_color="r"
                self.init_graph_window()
                self.send_init_commands()
                # Kick off the first acquisition immediately so the
                # measurement starts without a one-second delay.
                self.handle_send_new_command()
                self.timer_command.start(1000)
                
            else:
                pass
        else:
            self.stop_annealing("Stopped by user.", show_dialog=False)
    def handle_pushButton_reverse_now_clicked(self):
        """Immediately ramp current down toward zero."""
        if not self.process_running:
            return
        try:
            self.hold_timer.stop()
        except Exception:
            pass
        self.hold_timer_running = False
        self.current_increment = -abs(self.current_step_A)
        self.line_color = "b"
        self.force_stop_at_zero = True
        self.direction_ascending = False
        self._reset_voltage_projection()

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
        if hasattr(self.ui, 'groupBox_live_values'):
            keep.add(self.ui.groupBox_live_values)
        for child in self.ui.groupBox_process_settings.findChildren(QtWidgets.QWidget):
            if child in keep:
                continue
            child.setEnabled(enabled)

    def _restore_idle_controls(self) -> None:
        """Re-enable process controls after a start attempt is canceled."""

        self._set_process_controls_enabled(True)
        self._update_mode_action_state()
        if hasattr(self.ui, 'pushButton_reverse_now'):
            self.ui.pushButton_reverse_now.setEnabled(False)

    def stop_annealing(self, reason: str | None = None, *, show_dialog: bool = False):
        """Abort the annealing run and power down the supply safely."""
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
        self._reset_sample_buffers()
        try:
            self.timer_command.stop()
            self.hold_timer.stop()
        except Exception:
            pass
        self.hold_timer_running = False
        if self.f_out:
            self.f_out.close()
            self.f_out = None
        if self.operation_mode == 1:
            self.ui.pushButton_hold_current.setText("Hold current now!")
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
        self._update_mode_action_state()
        self._reset_voltage_projection()
        if hasattr(self.ui, 'label_time_to_limit'):
            self.ui.label_time_to_limit.setText(self._format_voltage_limit_label())
        self._display_ui_value('label_set_current', "0")
        self._max_voltage_dialog = False
        self.first_sample = True
        self._reset_loop_tracking()
        message = reason or "Measurement stopped."
        self._show_status_message(message, timeout_ms=15000)
        if show_dialog:
            try:
                QtWidgets.QMessageBox.information(self, "Measurement stopped", message)
            except Exception:
                pass
        
    def handle_send_new_command(self):
        if not self.process_running:
            return

        self._sync_runtime_settings()

        # Manual annealing
        if self.operation_mode == 1:
            self.sample_ready = False
            self.expecting_voltage = True
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
            skip_sample = bool(self._skip_current_sample)
            if not skip_sample:
                if self.first_sample:
                    self.first_sample = False
                self._append_measurement_sample(float(self.curr_value_x), float(self.curr_value_y))


            # Iterate the current set point
            self.current_current_set += self.current_increment
            self._display_ui_value('label_set_current', f"{self.current_current_set*1000:.1f}")

            # Stop the process once we are below the configured start current.
            if self.current_current_set < self._start_current_A():
                self.stop_annealing("Reached minimum current; stopping measurement.", show_dialog=True)

            if not self.process_running:
                return
            self.serial_command = f"CURR {self.current_current_set:.3f}\n"
            self.send_serial_command()
           
            
                
            
            
        elif self.operation_mode == 2:
            self.sample_ready = False
            self.expecting_voltage = True
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
            skip_sample = bool(self._skip_current_sample)
            if not skip_sample:
                if self.first_sample:
                    self.first_sample = False
                self._append_measurement_sample(float(self.curr_value_x), float(self.curr_value_y))



            if not skip_sample:
                self._record_voltage_progress()

            # Trigger the hold-current routine as if the button were pressed
            if (self.current_current_set >= (self.max_current_mA/1000.0)) and (self.current_increment > 0):
                if not self.hold_timer_running:
                    self.current_increment = 0.000
                    self.line_color="g"
                    self.elapsed_seconds = 0
                    self.resistance_at_hold_current = self.current_resistance
                    self.ui.label_resistance_at_hold_current.setText("{:.1f}".format(self.resistance_at_hold_current))
                    self.hold_timer.start(1000)
                    self.hold_timer_running = True
                    self.direction_ascending = False
                    self._reset_voltage_projection()
            
            # Iterate the current set point
            self.current_current_set += self.current_increment
            self._display_ui_value('label_set_current', f"{self.current_current_set*1000:.1f}")

            # end of hold: either reverse (if enabled) or stop
            if (self.hold_timer_running and (self.elapsed_seconds >= self.hold_duration_s)):
                self.hold_timer.stop()
                self.hold_timer_running = False
                if getattr(self, 'reverse_enabled', False):
                    self.current_increment = -self.current_step_A
                    self.line_color = "b"
                    self.direction_ascending = False
                    self._reset_voltage_projection()
                else:
                    self.stop_annealing("Hold complete; stopping measurement.", show_dialog=True)

            if not self.process_running:
                return
            self.serial_command = f"CURR {self.current_current_set:.3f}\n"
            self.send_serial_command()
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
                    self.current_increment = self.current_step_A
                    self.current_current_set = self._start_current_A()
                    self.line_color = "r"
                    self.direction_ascending = True
                    self._reset_voltage_projection()
                    self.elapsed_seconds = 0
                else:
                    self.stop_annealing("Run complete; stopping measurement.", show_dialog=True)

        else:
            pass
        
        self.command_number +=1
        
        
        

    def send_safe_end_commands(self):
        for i in range(0, len(self.commands_safe_end)):
            self.serial_command = self.commands_safe_end[i]
            self.send_serial_command()
            self.simple_delay(200)
            

    def send_init_commands(self):
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
            
    def simple_delay(self, delay_ms):
        self.wait = True
        QtCore.QTimer.singleShot(delay_ms, lambda: setattr(self, 'wait', False))
        
        while self.wait:
            QtWidgets.QApplication.processEvents()
        
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
            self.stop_annealing("No response from power supply. Measurement stopped.", show_dialog=False)
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
        self.label_live_voltage = QtWidgets.QLabel("0")
        for lbl in (self.label_live_current, self.label_live_set, self.label_live_voltage):
            lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addRow("Set current (mA)", self.label_live_set)
        layout.addRow("Current (mA)", self.label_live_current)
        layout.addRow("Voltage (V)", self.label_live_voltage)
        # Alias old names for compatibility
        self.ui.lcd_current_mA = self.label_live_current
        self.ui.label_set_current = self.label_live_set
        self.ui.label_live_voltage = self.label_live_voltage
        self._voltage_default_style = self.label_live_voltage.styleSheet() or ""
        self._voltage_warning_style = "color: #c0392b;"
        setattr(self.ui.lcd_current_mA, "display", self.label_live_current.setText)
        setattr(self.ui.label_set_current, "display", self.label_live_set.setText)
        setattr(self.ui.label_live_voltage, "display", self._set_live_voltage_text)

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
        except Exception:
            pass

    def _show_status_message(self, message: str, timeout_ms: int = 10000) -> None:
        try:
            status_bar = self.statusBar()
            if status_bar is not None:
                status_bar.showMessage(message, timeout_ms)
        except Exception:
            pass

    def _adjust_progress_for_reverse(self) -> None:
        if not self.total_steps:
            return
        step_mA = abs(int(getattr(self, 'current_step_mA', 1) or 1))
        if step_mA <= 0:
            step_mA = 1
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
        if action not in MAX_VOLTAGE_ACTION_LABELS:
            action = MAX_VOLTAGE_DEFAULT_ACTION
        limit_label = f"{self._format_voltage_limit()} V"
        if action == "reverse":
            step = abs(getattr(self, "current_step_A", 0.0))
            if step == 0.0:
                step = abs(getattr(self, "current_step_mA", 1)) / 1000.0
                if step == 0.0:
                    step = 0.001
            self.current_increment = -step
            self.line_color = "b"
            next_loop = int(getattr(self, 'loop_idx', 0)) + 1
            if self._has_remaining_loops(next_loop):
                self.force_stop_at_zero = False
            else:
                self.force_stop_at_zero = not bool(getattr(self, "reverse_enabled", False))
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
        else:  # hold current
            self.current_increment = 0
            self.force_stop_at_zero = False
            self.direction_ascending = False
            self._note_voltage_limit_reached()
            self.update_time_estimate()
            self._show_status_message(f"{limit_label} reached — holding current.")

    def _show_max_voltage_prompt(self) -> None:
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Voltage limit reached")
        msg.setText(f"Power supply reached {self._format_voltage_limit()} V. What do you want to do?")
        _hold_btn = msg.addButton("Hold current", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
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
            self._apply_max_voltage_action("hold")
    
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
    
        # Create an embedded Matplotlib figure on the right panel
        if hasattr(self.ui, 'plot_container'):
            container = self.ui.plot_container
            layout = container.layout()
            if layout is None:
                layout = QtWidgets.QVBoxLayout(container)
                # Zero margins to eliminate bright edge lines and maximize canvas area
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(0)
            while layout.count():
                item = layout.takeAt(0)
                if item is None:
                    continue
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

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
            self.ax1.grid(True, color=(0.35,0.35,0.35,0.5) if scheme == QtCore.Qt.ColorScheme.Dark else (0.8,0.8,0.8,0.8))
            for spine in self.ax1.spines.values():
                spine.set_color(text_rgb)
            self.ax1.tick_params(colors=text_rgb)
            self.ax1.xaxis.label.set_color(text_rgb)
            self.ax1.yaxis.label.set_color(text_rgb)

            self.ax2 = self.fig.add_subplot(212)
            self.ax2.set_facecolor(base_rgb)
            self.ax2.set_xlabel("N")
            self.ax2.set_ylabel("Resistance [Ohm]")
            self.ax2.grid(True, color=(0.35,0.35,0.35,0.5) if scheme == QtCore.Qt.ColorScheme.Dark else (0.8,0.8,0.8,0.8))
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
            # Fallback to separate window
            self.fig = plt.figure(constrained_layout=True)
            self.ax1 = self.fig.add_subplot(211)
            self.ax1.set_xlabel("Current [mA]")
            self.ax1.set_ylabel("Resistance [Ohm]")
            self.ax1.grid(True)
            self.ax2 = self.fig.add_subplot(212)
            self.ax2.set_xlabel("N")
            self.ax2.set_ylabel("Resistance [Ohm]")
            self.ax2.grid(True)
            self._zero_placeholder_line1 = None
            self._zero_placeholder_line2 = None
            self._zero_placeholder_count = 0
            self._zero_placeholders_active = False
            plt.ion()
            show_plots()
        
        
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

    def prepare_output_file(self) -> bool:
        """Create or prepare the output file, prompting if it exists.

        Returns True if ready to proceed, False if the user canceled.
        """
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
        # subsequent writes will append
        return True

    def populate_ports(self):
        if hasattr(self.ui, 'comboBox_port'):
            self.ui.comboBox_port.clear()
            # 1) Normal OS-reported ports
            seen: set[str] = set()
            for info in QSerialPortInfo.availablePorts():
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

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if self.ser_mcu.isOpen():
            self.handle_connect_port_clicked()
            # self.ser_mcu.close()

        super().closeEvent(event)

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
        msg = QtWidgets.QLabel("Connect COM port to enable settings")
        msg.setStyleSheet("color: white; font-size: 18px; font-weight: 700;")
        msg.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(msg)
        layout.addStretch(1)
        self._overlay = ov
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
            overlay.setVisible(bool(show))
        except Exception:
            pass

    def resizeEvent(self, ev: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(ev)
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
    
