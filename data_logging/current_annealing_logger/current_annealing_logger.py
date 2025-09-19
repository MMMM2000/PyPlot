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
from pathlib import Path
from collections import deque
from typing import Any, Deque, Optional, TextIO, Tuple, cast

from PyQt6 import QtCore, QtWidgets, QtSerialPort, QtGui
from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtSerialPort import QSerialPortInfo

from .ui_en import Ui_MainWindow
from plotting.utils import ensure_app_theme, format_annealing_title, show_plots, install_standard_menu
from data_logging.naming_history import LineEditHistory

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.figure import Figure

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
except Exception:
    try:
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    except Exception:  # pragma: no cover - backend optional
        FigureCanvas = None  # type: ignore[assignment]

try:
    from matplotlib.backends.backend_qt import NavigationToolbar2QT as NavigationToolbar
except Exception:
    try:
        from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
    except Exception:  # pragma: no cover - backend optional
        NavigationToolbar = None  # type: ignore[assignment]


fig_size = plt.rcParams["figure.figsize"]
fig_size[0] = 19 #19
fig_size[1] = 10 #10
plt.rcParams["figure.figsize"] = fig_size
plt.rcParams["font.family"] = "Palatino Linotype"
plt.rcParams["font.size"] = 14

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
    "custom_name": "",
}

MAX_VOLTAGE_DEFAULT_ACTION = "ask"
MAX_VOLTAGE_ACTION_LABELS = {
    "ask": "Ask every time",
    "hold": "Hold current (stop increasing)",
    "reverse": "Reverse to zero",
    "stop": "Stop measurement",
}


class MainWindow(QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()
        self.ui = cast(Any, Ui_MainWindow())
        self.ui.setupUi(self)
        self.history_settings = QtCore.QSettings("microwire", "naming_history")
        self.name_history = LineEditHistory(self.history_settings, parent=self)
        self._sample_pattern = re.compile(r"^(.*?)(\d+)(.*)$")
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
        install_standard_menu(self, help_topic="logger_current_annealing")
        # Remember last log directory and file separately
        self.settings = QtCore.QSettings("microwire", "current_annealing")
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
        self.max_current_mA = 10
        self.operation_mode = 0  # 0 - VCP, 1 - manual, 2 - automatic
        self.process_running = False

        self.current_current_set = 0.001
        self.current_current_read = 0.0
        self.current_increment = 0.001
        self.temp_resistance_maximum = 0.0
        self.current_voltage = 0.0
        self.current_resistance = 0.0
        self.open_threshold = 30
        self.max_voltage = 30.0
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
        self._contact_lost = False
        self._zero_current_count = 0
        self._nonzero_current_seen = False
        self._skip_current_sample = False
        self._process_start_time: float | None = None
        self._last_nonzero_current_time: float | None = None
        self._contact_grace_period = 5.0
        self._last_serial_rx: float | None = None
        self._serial_quiet_failures = 0
        self._voltage_history: Deque[Tuple[float, float, float]] = deque(maxlen=60)
        self._time_to_voltage_limit: float | None = None
        self._estimated_limit_current_mA: float | None = None

        self.prev_value_x: float | None = None
        self.prev_value_y: float | None = None
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
        
        if hasattr(self.ui, 'comboBox_mode'):
            self.ui.comboBox_mode.currentIndexChanged.connect(self.handle_mode_changed)
        
        self.ui.spinBox_max_current.valueChanged.connect(self.handle_max_current_value_changed)
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
        # New UI pieces: port dropdown and separate log directory/name
        if hasattr(self.ui, 'comboBox_port'):
            self.ui.comboBox_port.currentIndexChanged.connect(self.handle_comboBox_port_changed)
        if hasattr(self.ui, 'pushButton_refresh_ports'):
            self.ui.pushButton_refresh_ports.clicked.connect(self.populate_ports)
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
        for name in ('lineEdit_composition','lineEdit_microwire','lineEdit_sample','lineEdit_custom_name'):
            if hasattr(self.ui, name):
                getattr(self.ui, name).textChanged.connect(self.update_file_name_from_preset)
        self.name_history.register('composition', getattr(self.ui, 'lineEdit_composition', None))
        self.name_history.register('microwire', getattr(self.ui, 'lineEdit_microwire', None))
        sample_edit = getattr(self.ui, 'lineEdit_sample', None)
        if isinstance(sample_edit, QtWidgets.QLineEdit):
            sample_edit.installEventFilter(self)
        sample_up = getattr(self.ui, 'toolButton_sample_up', None)
        if isinstance(sample_up, QtWidgets.QAbstractButton):
            sample_up.setAutoRepeat(True)
            sample_up.setAutoRepeatDelay(200)
            sample_up.setAutoRepeatInterval(120)
            sample_up.clicked.connect(lambda: self._nudge_sample(1))
        sample_down = getattr(self.ui, 'toolButton_sample_down', None)
        if isinstance(sample_down, QtWidgets.QAbstractButton):
            sample_down.setAutoRepeat(True)
            sample_down.setAutoRepeatDelay(200)
            sample_down.setAutoRepeatInterval(120)
            sample_down.clicked.connect(lambda: self._nudge_sample(-1))
        if hasattr(self.ui, 'pushButton_reset_preset'):
            self.ui.pushButton_reset_preset.clicked.connect(self.reset_name_preset)
        if hasattr(self.ui, 'checkBox_reverse'):
            self.ui.checkBox_reverse.toggled.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'spinBox_loops'):
            self.ui.spinBox_loops.valueChanged.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'checkBox_infinite_loops'):
            self.ui.checkBox_infinite_loops.toggled.connect(self.handle_checkBox_infinite_loops_toggled)
        if hasattr(self.ui, 'spinBox_step_mA'):
            self.ui.spinBox_step_mA.valueChanged.connect(self.handle_step_changed)
        self.ui.spinBox_max_current.valueChanged.connect(self.update_file_name_from_preset)
        self.ui.spinBox_max_current.valueChanged.connect(self.update_planned_time_label)
        self.ui.spinBox_hold_duration.valueChanged.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'checkBox_infinite_loops'):
            self.ui.checkBox_infinite_loops.toggled.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'spinBox_step_mA'):
            self.ui.spinBox_step_mA.valueChanged.connect(self.handle_step_changed)

        # Initialize planned estimate and file name once
        try:
            self.update_file_name_from_preset()
            self.update_planned_time_label()
        except Exception:
            pass
        # Apply initial mode selection
        try:
            if hasattr(self.ui, 'comboBox_mode'):
                self.handle_mode_changed(self.ui.comboBox_mode.currentIndex())
        except Exception:
            pass
        
        # Disable process controls by default until a port is connected
        self.ui.frame_process_settings.setEnabled(False)
        self.ui.frame_command_and_response.setEnabled(False)
        self.ui.frame_operation_mode.setEnabled(False)

        # Connection overlay over the left panel until port is connected
        self._setup_connect_overlay()
        if hasattr(self, 'is_connected') and not self.is_connected:
            self._show_connect_overlay(True)
        
        self.max_resistance = 0
        
        self.resistance_at_hold_current = 0
        self.resistance_percent_from_hold = 0
        
        self.commands_init = [
                                #"*IDN?\n",
                                "*RST\n",
                                "SYST:REM\n",
                                "INST:NSEL 3\n",
                                "CURR 0.001\n",
                                "VOLT 30.0\n",
                                "OUTP ON\n"
                                
        ]
        
        
        self.commands_safe_end = [
                                "INST:NSEL 3\n",
                                "OUTP OFF\n",
                                "VOLT 1.0\n",
                                "CURR 0.001\n",
                                "SYST:LOC\n",
                                "OUTP:GEN 0\n"
        ]
        
        self.f_out = None
        self.f_name = self.build_log_path() if hasattr(self, 'build_log_path') else self.ui.lineEdit_log_file_full.text()
        
        
        # Variables used for plotting data
        self.prev_value_x = None
        self.curr_value_x = 0.0
        self.prev_value_y = None
        self.curr_value_y = 0.0
        self.first_sample = True

        self.fig = None
        self.ax1 = None
        self.ax2 = None
                
        self.line_marker="o"
        self.line_style="-"
        self.line_color="r"
        
        self.line1 = None
        self.line2 = None
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
                ax1.text(
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
                ax2.text(
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

    def _display_ui_value(self, attr: str, text: str) -> None:
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

    def _nudge_sample(self, delta: int) -> None:
        edit = getattr(self.ui, 'lineEdit_sample', None)
        if not isinstance(edit, QtWidgets.QLineEdit):
            return
        text = edit.text().strip()
        match = self._sample_pattern.match(text) if text else None
        if match is None:
            prefix, number, suffix = ('s', '0', '')
        else:
            prefix, number, suffix = match.groups()
            if not prefix:
                prefix = 's'
        try:
            value = int(number)
        except ValueError:
            value = 0
        value = max(0, value + delta)
        new_text = f"{prefix}{value}{suffix}"
        edit.setText(new_text)
        edit.selectAll()

    def _record_name_history(self) -> None:
        for key, attr in (('composition', 'lineEdit_composition'), ('microwire', 'lineEdit_microwire')):
            widget = getattr(self.ui, attr, None)
            if isinstance(widget, QtWidgets.QLineEdit):
                self.name_history.remember(key, widget.text())

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if event.type() == QtCore.QEvent.Type.KeyPress and isinstance(obj, QtWidgets.QLineEdit):
            if obj is getattr(self.ui, 'lineEdit_sample', None):
                key_event = cast(QtGui.QKeyEvent, event)
                if key_event.key() == QtCore.Qt.Key.Key_Up:
                    self._nudge_sample(1)
                    return True
                if key_event.key() == QtCore.Qt.Key.Key_Down:
                    self._nudge_sample(-1)
                    return True
        return super().eventFilter(obj, event)

    def _set_port_controls_enabled(self, enabled: bool) -> None:
        for name in ('spinBox_port_number', 'comboBox_baudrate', 'comboBox_port', 'pushButton_refresh_ports'):
            w = getattr(self.ui, name, None)
            if w is not None:
                w.setEnabled(enabled)

    def handle_checkBox_infinite_loops_toggled(self, checked: bool) -> None:
        if hasattr(self.ui, 'spinBox_loops'):
            if checked:
                self.ui.spinBox_loops.setValue(0)
                self.ui.spinBox_loops.setEnabled(False)
            else:
                if self.ui.spinBox_loops.value() == 0:
                    self.ui.spinBox_loops.setValue(1)
                self.ui.spinBox_loops.setEnabled(True)
        self.update_planned_time_label()

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
                self.ui.frame_operation_mode.setEnabled(True)
                self._set_port_controls_enabled(False)
                self.ui.frame_command_and_response.setEnabled(True)
                # Respect the selected mode rather than forcing raw VCP
                try:
                    if hasattr(self.ui, 'comboBox_mode'):
                        self.handle_mode_changed(self.ui.comboBox_mode.currentIndex())
                    else:
                        self.handle_raw_vcp_mode_selected()
                except Exception:
                    self.handle_raw_vcp_mode_selected()
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
            self.ui.frame_operation_mode.setEnabled(False)
            self._set_port_controls_enabled(True)

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
                self.serial_response = bytes(raw_line).decode('ascii', errors='ignore')
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
                                self.handle_toggle_process_clicked()
                        self.lock.unlock()
                        return
                    self._skip_current_sample = False
                    self._zero_current_count = 0
                    self._contact_lost = False
                    self._nonzero_current_seen = True
                    try:
                        self._last_nonzero_current_time = time.monotonic()
                    except Exception:
                        self._last_nonzero_current_time = None
                    try:
                        self.current_resistance = self.current_voltage / self.current_current_read
                    except ZeroDivisionError:
                        self.lock.unlock()
                        return
                    # Persist each sample to disk immediately after it arrives
                    if not self.first_sample and self.f_name:
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
                            line = f"{self.current_current_read}\t{self.current_voltage}\t{self.current_resistance}\n"
                            self.f_out.write(line)
                            self.f_out.close()
                            self.f_out = None

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
        """Clear the running 30 V projection state."""

        self._voltage_history.clear()
        self._time_to_voltage_limit = None
        self._estimated_limit_current_mA = None

    def _note_voltage_limit_reached(self) -> None:
        """Record that the 30 V ceiling has been hit."""

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

    def _update_voltage_projection(self, timestamp: float) -> None:
        """Update the projection to the 30 V limit using recent samples."""

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
        prefix = "To 30 V"
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

    def compute_planned_seconds(self) -> int | None:
        """Estimate duration based on UI parameters, even when idle.

        Assumes 1 mA per second ramp rate (timer_command = 1000 ms).
        """
        try:
            max_mA = int(self.ui.spinBox_max_current.value())
            hold_s = int(self.ui.spinBox_hold_duration.value())
            loops = int(self.ui.spinBox_loops.value()) if hasattr(self.ui, 'spinBox_loops') else 1
            reverse = bool(self.ui.checkBox_reverse.isChecked()) if hasattr(self.ui, 'checkBox_reverse') else False
            infinite = bool(self.ui.checkBox_infinite_loops.isChecked()) if hasattr(self.ui, 'checkBox_infinite_loops') else False
            step_mA = int(self.ui.spinBox_step_mA.value()) if hasattr(self.ui, 'spinBox_step_mA') else 1
        except Exception:
            return None
        if infinite:
            return None
        # steps up from 1 mA to max in increments of step_mA
        up_steps = max(0, math.ceil(max(0, max_mA - 1) / max(1, step_mA)))
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
            self.ui.label_time_to_limit.setText("To 30 V: N/A")

    def update_file_name_from_preset(self):
        # Build file name based on naming preset
        if not hasattr(self.ui, 'comboBox_name_preset'):
            return
        preset = self.ui.comboBox_name_preset.currentText().strip().lower()
        if preset.startswith('current'):
            comp = getattr(self.ui, 'lineEdit_composition', None)
            wire = getattr(self.ui, 'lineEdit_microwire', None)
            sample = getattr(self.ui, 'lineEdit_sample', None)
            comp_s = comp.text().strip() if comp is not None else ''
            wire_s = wire.text().strip() if wire is not None else ''
            sample_s = sample.text().strip() if sample is not None else ''
            try:
                max_mA = int(self.ui.spinBox_max_current.value())
            except Exception:
                max_mA = 0
            parts = [p for p in [comp_s, wire_s, sample_s, f"{max_mA}mA"] if p]
            base = " ".join(parts) if parts else "anneal_log"
            # Show only preset fields
            for name in (
                'lineEdit_composition',
                'lineEdit_microwire',
                'lineEdit_sample',
                'sample_row_widget',
            ):
                widget = getattr(self.ui, name, None)
                if widget is not None:
                    widget.setVisible(True)
            for name in ('label_composition', 'label_microwire', 'label_sample'):
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
            ):
                widget = getattr(self.ui, name, None)
                if widget is not None:
                    widget.setVisible(False)
            for name in ('label_composition', 'label_microwire', 'label_sample'):
                label = getattr(self.ui, name, None)
                if label is not None:
                    label.setVisible(False)
            if hasattr(self.ui, 'label_custom_name'):
                self.ui.label_custom_name.setVisible(True)
            if hasattr(self.ui, 'lineEdit_custom_name'):
                self.ui.lineEdit_custom_name.setVisible(True)
        if hasattr(self.ui, 'lineEdit_log_file'):
            self.ui.lineEdit_log_file.setText(base)
        self.store_name_preset()

    def store_name_preset(self):
        s = self.settings
        s.setValue("preset", self.ui.comboBox_name_preset.currentIndex())
        s.setValue("composition", self.ui.lineEdit_composition.text())
        s.setValue("microwire", self.ui.lineEdit_microwire.text())
        s.setValue("sample", self.ui.lineEdit_sample.text())
        s.setValue("custom_name", self.ui.lineEdit_custom_name.text())

    def restore_name_preset(self):
        try:
            self.ui.comboBox_name_preset.blockSignals(True)
            self.ui.lineEdit_composition.blockSignals(True)
            self.ui.lineEdit_microwire.blockSignals(True)
            self.ui.lineEdit_sample.blockSignals(True)
            self.ui.lineEdit_custom_name.blockSignals(True)
        except Exception:
            pass
        s = self.settings
        self.ui.comboBox_name_preset.setCurrentIndex(int(s.value("preset", DEFAULT_PRESET["preset"])))
        self.ui.lineEdit_composition.setText(s.value("composition", DEFAULT_PRESET["composition"]))
        self.ui.lineEdit_microwire.setText(s.value("microwire", DEFAULT_PRESET["microwire"]))
        self.ui.lineEdit_sample.setText(s.value("sample", DEFAULT_PRESET["sample"]))
        self.ui.lineEdit_custom_name.setText(s.value("custom_name", DEFAULT_PRESET["custom_name"]))
        try:
            self.ui.comboBox_name_preset.blockSignals(False)
            self.ui.lineEdit_composition.blockSignals(False)
            self.ui.lineEdit_microwire.blockSignals(False)
            self.ui.lineEdit_sample.blockSignals(False)
            self.ui.lineEdit_custom_name.blockSignals(False)
        except Exception:
            pass

    def reset_name_preset(self):
        self.settings.clear()
        self.restore_name_preset()
        self.update_file_name_from_preset()

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
        
    def handle_max_current_value_changed(self):
        self.max_current_mA = self.ui.spinBox_max_current.value()
        try:
            self.settings.setValue("max_current", self.max_current_mA)
        except Exception:
            pass
        
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
        self.resistance_percent_from_hold = self.current_resistance/self.resistance_at_hold_current*100
        self.ui.label_resistance_percent_from_hold.setText("{:.1f}".format(self.resistance_percent_from_hold))
        self.ui.lcd_elapsed_seconds.display(self.elapsed_seconds)
    
    def handle_toggle_process_clicked(self):
        if not self.process_running:
            self.process_running = True
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
            self.ui.frame_operation_mode.setEnabled(False)
            self._set_process_controls_enabled(False)
            if hasattr(self.ui, 'pushButton_reverse_now'):
                self.ui.pushButton_reverse_now.setEnabled(True)
            self.force_stop_at_zero = False
            self.command_number = 0
            self.sample_index = 0
            self.prev_value_x = None
            self.prev_value_y = None
            self.first_sample = True
            self.direction_ascending = True
            self._reset_voltage_projection()
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
                    self.ui.label_time_to_limit.setText("To 30 V: N/A")
                self.current_increment = self.current_step_A
                self.direction_ascending = True
                self.current_current_set = 0.001
                self._display_ui_value('label_set_current', f"{self.current_current_set*1000:.1f}")
                self.temp_resistance_maximum = 0
                self.current_voltage = 0
                self.current_resistance = 0
                self._display_ui_value('lcd_current_mA', "0")
                self._display_ui_value('label_live_voltage', "0")
                self.ui.lcd_elapsed_seconds.display(0)
                self.ui.label_resistance_at_hold_current.setText("0")
                self.ui.label_resistance_percent_from_hold.setText("0")
                self.line_marker="o"
                self.line_style="-"
                self.line_color="r"
                self.init_graph_window()
                self.send_init_commands()
                # Immediately request the first sample instead of waiting
                # for the one‑second timer interval to elapse.  This avoids
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
                self.current_current_set = 0.001
                self._display_ui_value('label_set_current', f"{self.current_current_set*1000:.1f}")
                self.temp_resistance_maximum = 0
                self.current_voltage = 0
                self.current_resistance = 0
                self._display_ui_value('lcd_current_mA', "0")
                self._display_ui_value('label_live_voltage', "0")
                # reverse + loop configuration
                self.reverse_enabled = getattr(self.ui, 'checkBox_reverse', None) is not None and self.ui.checkBox_reverse.isChecked()
                self.loop_target = self.ui.spinBox_loops.value() if hasattr(self.ui, 'spinBox_loops') else 1
                self.infinite_loops = bool(self.ui.checkBox_infinite_loops.isChecked()) if hasattr(self.ui, 'checkBox_infinite_loops') else False
                self.loop_idx = 0
                # progress plan
                step_mA = self.current_step_mA if hasattr(self, 'current_step_mA') else 1
                up_steps = max(0, math.ceil(max(0, int(self.ui.spinBox_max_current.value()) - 1) / max(1, step_mA)))
                hold_steps = int(self.ui.spinBox_hold_duration.value())
                down_steps = up_steps if self.reverse_enabled else 0
                per_loop = up_steps + hold_steps + down_steps
                if self.infinite_loops:
                    self.total_steps = 0
                else:
                    self.total_steps = max(0, per_loop * int(self.loop_target or 1))
                self.step_idx = 0
                if hasattr(self.ui, 'progressBar_process'):
                    if self.total_steps:
                        self.ui.progressBar_process.setMaximum(self.total_steps)
                        self.ui.progressBar_process.setValue(0)
                    else:
                        self.ui.progressBar_process.setMaximum(0)
                if hasattr(self.ui, 'label_time_to_limit'):
                    self.ui.label_time_to_limit.setText("To 30 V: N/A")
                self.ui.lcd_elapsed_seconds.display(0)
                self.ui.label_resistance_at_hold_current.setText("0")
                self.ui.label_resistance_percent_from_hold.setText("0")
                self.line_marker="o"
                self.line_style="-"
                self.line_color="r"
                self.init_graph_window()
                self.send_init_commands()
                # Kick off the first acquisition immediately so the
                # measurement starts without a one‑second delay.
                self.handle_send_new_command()
                self.timer_command.start(1000)
                
            else:
                pass
        else:
            self.stop_annealing()
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
        frame = getattr(self.ui, 'frame_operation_mode', None)
        if frame is not None:
            frame.setEnabled(True)
        if hasattr(self.ui, 'pushButton_reverse_now'):
            self.ui.pushButton_reverse_now.setEnabled(False)

    def stop_annealing(self):
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
            for cmd in ("INST:NSEL 3\n", "CURR 0.000\n", "OUTP OFF\n"):
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
        self.ui.frame_operation_mode.setEnabled(True)
        self._reset_voltage_projection()
        if hasattr(self.ui, 'label_time_to_limit'):
            self.ui.label_time_to_limit.setText("To 30 V: N/A")
        self._display_ui_value('label_set_current', "0")
        self._max_voltage_dialog = False
        
    def handle_send_new_command(self):
        if not self.process_running:
            return

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
                    self.prev_value_x = None
                    self.prev_value_y = None
                else:
                    self.sample_index += 1
                    if self.prev_value_x is not None and self.prev_value_y is not None:
                        ax1 = getattr(self, 'ax1', None)
                        ax2 = getattr(self, 'ax2', None)
                        if ax1 is not None and ax2 is not None:
                            prev_x = float(self.prev_value_x)
                            prev_y = float(self.prev_value_y)
                            curr_x = float(self.curr_value_x)
                            curr_y = float(self.curr_value_y)
                            self.line1 = Line2D(
                                [prev_x, curr_x],
                                [prev_y, curr_y],
                                color=self.line_color,
                                marker=self.line_marker,
                                linestyle=self.line_style,
                            )
                            ax1.add_line(self.line1)

                            self.line2 = Line2D(
                                [self.sample_index - 1, self.sample_index],
                                [prev_y, curr_y],
                                color=self.line_color,
                                marker=self.line_marker,
                                linestyle=self.line_style,
                            )
                            ax2.add_line(self.line2)

                            for axis in (ax1, ax2):
                                axis.relim()
                                axis.autoscale_view()

                            fig = getattr(self, 'fig', None)
                            canvas = getattr(fig, 'canvas', None) if fig is not None else None
                            if canvas is not None:
                                canvas.draw()
                                canvas.flush_events()

                self.prev_value_x = self.curr_value_x
                self.prev_value_y = self.curr_value_y


            # Iterate the current set point
            self.current_current_set += self.current_increment
            self._display_ui_value('label_set_current', f"{self.current_current_set*1000:.1f}")

            # Stop the process just like pressing the stop button
            if(self.current_current_set < 0.001):
                self.handle_toggle_process_clicked()

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
                    self.prev_value_x = None
                    self.prev_value_y = None
                else:
                    self.sample_index += 1
                    if self.prev_value_x is not None and self.prev_value_y is not None:
                        ax1 = getattr(self, 'ax1', None)
                        ax2 = getattr(self, 'ax2', None)
                        if ax1 is not None and ax2 is not None:
                            prev_x = float(self.prev_value_x)
                            prev_y = float(self.prev_value_y)
                            curr_x = float(self.curr_value_x)
                            curr_y = float(self.curr_value_y)
                            self.line1 = Line2D(
                                [prev_x, curr_x],
                                [prev_y, curr_y],
                                color=self.line_color,
                                marker=self.line_marker,
                                linestyle=self.line_style,
                            )
                            ax1.add_line(self.line1)

                            self.line2 = Line2D(
                                [self.sample_index - 1, self.sample_index],
                                [prev_y, curr_y],
                                color=self.line_color,
                                marker=self.line_marker,
                                linestyle=self.line_style,
                            )
                            ax2.add_line(self.line2)

                            for axis in (ax1, ax2):
                                axis.relim()
                                axis.autoscale_view()

                            fig = getattr(self, 'fig', None)
                            canvas = getattr(fig, 'canvas', None) if fig is not None else None
                            if canvas is not None:
                                canvas.draw()
                                canvas.flush_events()

                self.prev_value_x = self.curr_value_x
                self.prev_value_y = self.curr_value_y



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
                    self.handle_toggle_process_clicked()

            if not self.process_running:
                return
            self.serial_command = f"CURR {self.current_current_set:.3f}\n"
            self.send_serial_command()
            # completed descending to zero? manage loops or stop
            if (self.current_increment < 0) and (self.current_current_set < self.current_step_A):
                if getattr(self, 'force_stop_at_zero', False) or not getattr(self, 'reverse_enabled', False):
                    self.handle_toggle_process_clicked()
                else:
                    self.loop_idx = int(getattr(self, 'loop_idx', 0)) + 1
                    if self.infinite_loops or (self.loop_idx < int(getattr(self, 'loop_target', 1))):
                        # prepare next loop
                        self.current_increment = self.current_step_A
                        self.current_current_set = 0.001
                        self.line_color = "r"
                        self.direction_ascending = True
                        self._reset_voltage_projection()
                        self.elapsed_seconds = 0
                    else:
                        self.handle_toggle_process_clicked()

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
            # The original implementation paused for a full second between
            # initialisation commands, which caused a noticeable start-up
            # delay.  A brief 200 ms gap gives the supply time to process
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
            self.stop_annealing()
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
        self.ui.lcd_current_mA.display = self.label_live_current.setText
        self.ui.label_set_current.display = self.label_live_set.setText
        self.ui.label_live_voltage.display = self.label_live_voltage.setText

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
            self.statusBar().showMessage(message, timeout_ms)
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
        new_total = max(self.step_idx + remaining_steps, self.step_idx)
        if new_total <= 0:
            return
        self.total_steps = new_total
        if hasattr(self.ui, 'progressBar_process'):
            self.ui.progressBar_process.setMaximum(self.total_steps)
            self.ui.progressBar_process.setValue(min(self.step_idx, self.total_steps))
        self._finish_time = None

    def _apply_max_voltage_action(self, action: str) -> None:
        if action not in MAX_VOLTAGE_ACTION_LABELS:
            action = MAX_VOLTAGE_DEFAULT_ACTION
        if action == "reverse":
            step = abs(getattr(self, "current_step_A", 0.0))
            if step == 0.0:
                step = abs(getattr(self, "current_step_mA", 1)) / 1000.0
                if step == 0.0:
                    step = 0.001
            self.current_increment = -step
            self.line_color = "b"
            self.force_stop_at_zero = True
            self.direction_ascending = False
            self._note_voltage_limit_reached()
            self._adjust_progress_for_reverse()
            self.update_time_estimate()
            self._show_status_message("30 V reached — reversing to zero.")
        elif action == "stop":
            self._show_status_message("30 V reached — stopping measurement.")
            self.direction_ascending = False
            self._note_voltage_limit_reached()
            self.update_time_estimate()
            if self.process_running:
                self.handle_toggle_process_clicked()
        else:  # hold current
            self.current_increment = 0
            self.force_stop_at_zero = False
            self.direction_ascending = False
            self._note_voltage_limit_reached()
            self.update_time_estimate()
            self._show_status_message("30 V reached — holding current.")

    def _show_max_voltage_prompt(self) -> None:
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Voltage limit reached")
        msg.setText("Power supply reached 30 V. What do you want to do?")
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
                    scheme = app.styleHints().colorScheme()
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

            self.line1 = Line2D([], [], color=self.line_color, marker=self.line_marker, linestyle=self.line_style)
            self.ax1.add_line(self.line1)

            self.ax2 = self.fig.add_subplot(212)
            self.ax2.set_facecolor(base_rgb)
            self.ax2.set_xlabel("N [-]")
            self.ax2.set_ylabel("Resistance [Ohm]")
            self.ax2.grid(True, color=(0.35,0.35,0.35,0.5) if scheme == QtCore.Qt.ColorScheme.Dark else (0.8,0.8,0.8,0.8))
            for spine in self.ax2.spines.values():
                spine.set_color(text_rgb)
            self.ax2.tick_params(colors=text_rgb)
            self.ax2.xaxis.label.set_color(text_rgb)
            self.ax2.yaxis.label.set_color(text_rgb)
            self.line2 = Line2D([], [], color=self.line_color, marker=self.line_marker, linestyle=self.line_style)
            self.ax2.add_line(self.line2)
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
            self.line1 = Line2D([], [], color=self.line_color, marker=self.line_marker, linestyle=self.line_style)
            self.ax1.add_line(self.line1)
            self.ax2 = self.fig.add_subplot(212)
            self.ax2.set_xlabel("N [-]")
            self.ax2.set_ylabel("Resistance [Ohm]")
            self.ax2.grid(True)
            self.line2 = Line2D([], [], color=self.line_color, marker=self.line_marker, linestyle=self.line_style)
            self.ax2.add_line(self.line2)
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
            b = os.path.splitext(os.path.basename(fpath))[0]
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
            b = os.path.splitext(os.path.basename(fpath))[0]
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
            b = self.ui.lineEdit_log_file.text().strip()
            if not b:
                b = "anneal_log"
            if d:
                os.makedirs(d, exist_ok=True)
                return os.path.join(d, f"{b}.txt")
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
            with open(path, mode):
                pass
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

    window = MainWindow()
    window.showMaximized()
    WINDOWS.append(window)

    if owns_app:
        sys.exit(qt_app.exec())
    return window


if __name__ == "__main__":
    main()
    
