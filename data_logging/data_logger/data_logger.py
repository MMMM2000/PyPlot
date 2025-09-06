import sys
import os
from pathlib import Path
import time
import math
import re
from typing import Any, cast
from collections import deque

from PyQt6 import QtCore, QtWidgets, QtSerialPort, QtGui
from PyQt6.QtCore import QMutexLocker
from PyQt6.QtSerialPort import QSerialPortInfo

from .logger_ui import UiMainWindow
from .file_name_builder import FileNameBuilderWidget, InfoLineEdit
from .serial_port import serial_connection

from plotting.utils import apply_system_theme
import random
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
try:
    from matplotlib.backends.backend_qt5agg import (
        FigureCanvasQTAgg as FigureCanvas,
        NavigationToolbar2QT as NavigationToolbar,
    )
except Exception:
    FigureCanvas = None
    NavigationToolbar = None
from matplotlib.lines import Line2D

# =============================================================================
#                            USER CONFIGURATION
#
# 1) LOG_DIR: default directory where logged data will be stored. Modify this
#    path to your preferred location. The value can still be overridden via
#    the LOG_DIR environment variable.
# Use a logs folder in the user's home directory by default. This path works on
# all platforms and can be overridden via the ``LOG_DIR`` environment variable.
def _default_download_dir() -> str:
    # Cross-platform best-effort Downloads folder
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
    # Fallback: create ~/Downloads
    p = home / "Downloads"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(p)

LOG_DIR = _default_download_dir()

# 2) DEFAULT_PORT_COMMAND: command pre-filled in the command box when the GUI
#    starts. Adjust to match the most common command for your logger.
DEFAULT_PORT_COMMAND = ">2050;1270;1;"

# 3) DEFAULT_LOG_FILE_NAME: suggested file name for new recordings. This value
#    only affects the default text shown in the GUI. The ``.txt`` extension is
#    added automatically when saving.
DEFAULT_LOG_FILE_NAME = "FeSiBP 156_2 s2-1a 74mA 2,5a"
# =============================================================================

DEFAULT_LOG_DIR = os.getenv("LOG_DIR", LOG_DIR)

# Keep references to windows created via :func:`main` to prevent them from
# being garbage-collected when launched from another Qt application.
WINDOWS: list[QtWidgets.QWidget] = []


class MainWindow(QtWidgets.QMainWindow):

    def __init__(self, log_dir=DEFAULT_LOG_DIR):
        super().__init__()
        # ``log_dir`` represents the root directory where all recordings will be
        # placed.  When using subfolders we derive per-measurement directories
        # from this path, so keep a dedicated copy for reference.
        self.log_dir = log_dir
        self.root_log_dir = log_dir
        self.ui = UiMainWindow()
        self.ui.setupUi(self)
        self.setWindowTitle("Data Logger")

        self.ui.lineEdit_log_dir.setText(self.log_dir)
        self.ui.pushButton_browse_dir.clicked.connect(self.choose_log_dir)

        # runtime state
        self.port_response = ""
        self.connected = False
        self.port_name = ""
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())
        self.ui.groupBox_commands.setEnabled(False)

        self.serial: QtSerialPort.QSerialPort | None = None
        self._serial_ctx = None
        self.lock = QtCore.QMutex()

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_response_label)
        self.timer.start(10)

        # update time estimate once per second
        self.time_timer = QtCore.QTimer()
        self.time_timer.timeout.connect(self.update_time_estimate)
        self.time_timer.start(1000)

        # logging state
        self.log_file = None  # becomes an open file in start_logging()
        self.sample_count = 2000
        self.sample_idx = 0
        self.logging_on = False
        self.paused = False  # when True, data is read but not written
        self.sample_rate: float | None = None
        self._rate_window: deque[float] = deque(maxlen=1000)
        self.last_sample_time: float | None = None
        self._last_time_secs: int | None = None
        self._finish_time: float | None = None

        cast(Any, self.ui).progressBar_logging.setMaximum(self.sample_count)
        cast(Any, self.ui).pushButton_cancel.setEnabled(False)
        cast(Any, self.ui).progressBar_logging.setValue(0)
        cast(Any, self.ui).progressBar_logging.setToolTip("")
        self.ui.checkBox_subdir.setChecked(False)

        os.makedirs(self.log_dir, exist_ok=True)

        # fill port list and set defaults
        self.populate_ports()
        self.ui.comboBox_baudrate.setCurrentIndex(0)
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())

        self.ui.lineEdit_log_file.setText(DEFAULT_LOG_FILE_NAME)
        self.ui.lineEdit_log_file.returnPressed.connect(self.start_logging)
        self.ui.lineEdit_port_command.setText(DEFAULT_PORT_COMMAND)

        # hook into file name builder if present
        self.name_builder = getattr(self.ui, "file_name_builder", None)
        if self.name_builder is not None:
            load_edit = self.name_builder.s_load.lineEdit()
            if load_edit is not None:
                load_edit.returnPressed.connect(self.start_logging)

        # connect signals
        self.ui.pushButton_connect_port.clicked.connect(self.toggle_connection)
        self.ui.comboBox_port.currentIndexChanged.connect(self.update_port_name)
        self.ui.comboBox_baudrate.currentIndexChanged.connect(self.update_baudrate)
        self.ui.pushButton_send_command.clicked.connect(self.send_command)
        self.ui.pushButton_record.clicked.connect(self.start_logging)
        self.ui.pushButton_record.setToolTip("Start logging")
        self.ui.pushButton_cancel.clicked.connect(self.cancel_logging)
        self.ui.pushButton_cancel.setToolTip("Stop logging")
        refresh_btn = getattr(self.ui, "pushButton_refresh_ports", None)
        if refresh_btn is not None:
            refresh_btn.clicked.connect(self.populate_ports)
        self.ui.spinBox_log_sample_count.valueChanged.connect(self.update_time_estimate)

        self.update_time_estimate()
        # Overlay to prompt connection; disable settings until connected
        self._setup_connect_overlay()
        self._apply_connected_state()


        # Live plotting setup
        self._last_draw = 0.0
        self._init_live_plot()
        if getattr(self, 'name_builder', None) is not None:
            try:
                self.name_builder.combo_format.currentIndexChanged.connect(self._on_format_changed)
                self.name_builder.s_load.valueChanged.connect(self._send_emulator_mode)
                self.name_builder.s_dir.currentIndexChanged.connect(self._send_emulator_mode)
                self.name_builder.t_temp.currentIndexChanged.connect(self._send_emulator_mode)
            except Exception:
                pass

    def populate_ports(self):
        """Scan available serial ports and populate the combo box."""
        self.ui.comboBox_port.clear()
        for info in QSerialPortInfo.availablePorts():
            label = info.portName()
            if info.description():
                label += f" - {info.description()}"
            self.ui.comboBox_port.addItem(label, userData=info.portName())
        if self.ui.comboBox_port.count() > 0:
            self.port_name = self.ui.comboBox_port.currentData()

    def toggle_connection(self):
        """Open or close the serial port on button click."""
        if not self.connected:
            try:
                self._serial_ctx = serial_connection(self.port_name, self.baudrate)
                self.serial = self._serial_ctx.__enter__()
            except OSError as exc:
                QtWidgets.QMessageBox.critical(self, "Error", str(exc))
                return

            self.serial.readyRead.connect(self.read_from_port)
            self.connected = True
            self.ui.pushButton_connect_port.setText("Disconnect")
            self.ui.groupBox_commands.setEnabled(True)
            self.ui.label_connection_indicator.setText("\u25cf Connected")
            self.ui.label_connection_indicator.setStyleSheet("color: green;")
            # Inform emulator of selected format
            try:
                self._send_emulator_mode()
            except Exception:
                pass
            self._apply_connected_state()
        else:
            if self._serial_ctx is not None:
                self._serial_ctx.__exit__(None, None, None)
                self._serial_ctx = None
            self.serial = None
            self.connected = False
            self.ui.pushButton_connect_port.setText("Connect to port")
            self.ui.groupBox_commands.setEnabled(False)
            self.ui.label_connection_indicator.setText("\u25cf Disconnected")
            self.ui.label_connection_indicator.setStyleSheet("color: red;")
            self._apply_connected_state()

    def update_port_name(self):
        """Keep self.port_name in sync with the combo box selection."""
        self.port_name = self.ui.comboBox_port.currentData()

    def update_baudrate(self):
        """Keep self.baudrate in sync with the combo box selection."""
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())

    def choose_log_dir(self):
        """Prompt for a new directory in which to save log files."""
        new_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select log directory", self.log_dir
        )
        if new_dir:
            # Update both the current log directory and the root directory used
            # for deriving subfolders.
            self.log_dir = new_dir
            self.root_log_dir = new_dir
            self.ui.lineEdit_log_dir.setText(new_dir)

    def read_from_port(self):
        """
        Read a line from the serial port whenever data arrives.
        Decode from ASCII, update the display, and log to file if active.
        """
        if self.serial is None or not self.serial.canReadLine():
            return

        with QMutexLocker(self.lock):
            raw = self.serial.readLine()
            # PyQt6 returns a QByteArray; at runtime bytes(raw) works fine.
            raw_bytes = bytes(raw)            # type: ignore[arg-type]
            self.port_response = raw_bytes.decode('ascii')

            now = time.perf_counter()
            if self.last_sample_time is not None:
                dt = now - self.last_sample_time
                if dt > 0:
                    rate = 1.0 / dt
                    self._rate_window.append(rate)
                    self.sample_rate = sum(self._rate_window) / len(self._rate_window)
            self.last_sample_time = now

            if self.paused:
                return
            if self.logging_on:
                assert self.log_file is not None

                # strip leading '>' if present, then write
                self.log_file.write(self.port_response.lstrip(">"))
                self.sample_idx += 1
                cast(Any, self.ui).progressBar_logging.setValue(self.sample_idx)

                if self.sample_rate:
                    remaining_samples = self.sample_count - self.sample_idx
                    self._finish_time = now + remaining_samples / self.sample_rate

                if self.sample_idx >= self.sample_count:
                    self.log_file.close()
                    self.logging_on = False
                    self.ui.pushButton_record.setEnabled(True)
                    self.ui.pushButton_cancel.setEnabled(False)
                    self._finish_time = None
            # Feed live plot
            try:
                self._ingest_live_sample(self.port_response)
            except Exception:
                pass

    def update_response_label(self):
        """Refresh the on-screen label with the latest port_response."""
        self.ui.label_port_response.setText(self.port_response)

    def update_time_estimate(self) -> None:
        """Update the estimated logging time display."""
        label = getattr(self.ui, "label_time_estimate", None)
        if label is None:
            return
        if self.paused:
            self._last_time_secs = None
            label.setText("Time remaining: Paused")
            return
        if self.sample_rate:
            remaining = self.ui.spinBox_log_sample_count.value()
            if self.logging_on and self._finish_time is not None:
                secs = max(0, math.ceil(self._finish_time - time.perf_counter()))
            else:
                if self.logging_on:
                    remaining -= self.sample_idx
                secs = math.ceil(remaining / self.sample_rate)

            self._last_time_secs = secs
            if secs >= 3600:
                hours, rem = divmod(secs, 3600)
                minutes, seconds = divmod(rem, 60)
                label.setText(
                    f"Time remaining: {hours}h {minutes:02d}m {seconds:02d}s"
                )
            elif secs >= 60:
                minutes, seconds = divmod(secs, 60)
                label.setText(
                    f"Time remaining: {minutes}m {seconds:02d}s"
                )
            else:
                label.setText(f"Time remaining: {secs}s")
        else:
            self._last_time_secs = None
            label.setText("Time remaining: N/A")

    # --- Live plotting helpers ---------------------------------------------
    def _current_format(self) -> str:
        if getattr(self, "name_builder", None) is None:
            return "Custom"
        return self.name_builder.combo_format.currentText()

    def _init_live_plot(self) -> None:
        container = getattr(self.ui, "plot_container", None)
        if container is None or FigureCanvas is None:
            return
        layout = container.layout()
        if layout is None:
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        try:
            palette = self.palette()
            win = palette.color(QtGui.QPalette.ColorRole.Window)
            base = palette.color(QtGui.QPalette.ColorRole.Base)
            text = palette.color(QtGui.QPalette.ColorRole.Text)
            win_rgb = (win.redF(), win.greenF(), win.blueF())
            base_rgb = (base.redF(), base.greenF(), base.blueF())
            text_rgb = (text.redF(), text.greenF(), text.blueF())
        except Exception:
            win_rgb = (1, 1, 1)
            base_rgb = (1, 1, 1)
            text_rgb = (0, 0, 0)
        self.fig = Figure(facecolor=win_rgb, constrained_layout=True)
        self.canvas = FigureCanvas(self.fig)
        self.toolbar = NavigationToolbar(self.canvas, container)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, stretch=1)
        self._plot_bg = base_rgb
        self._plot_fg = text_rgb
        self._reset_plot_for_mode(self._current_format())

    def _reset_plot_for_mode(self, mode: str) -> None:
        if not hasattr(self, "fig") or FigureCanvas is None:
            return
        self.fig.clear()
        self._rt_data = {}
        self._temp_cont_counter = 0
        if mode == "Maxion":
            self.ax_ch = [self.fig.add_subplot(311), self.fig.add_subplot(312), self.fig.add_subplot(313)]
            for i, ax in enumerate(self.ax_ch, start=1):
                ax.set_facecolor(self._plot_bg)
                ax.set_title(f"Channel {i} T1+T2", color=self._plot_fg)
                ax.set_xlabel("Sample index", color=self._plot_fg)
                ax.set_ylabel("sum", color=self._plot_fg)
                ax.tick_params(colors=self._plot_fg)
                for spine in ax.spines.values():
                    spine.set_color(self._plot_fg)
                # Visible placeholder
                ax.text(
                    0.5, 0.5, 'No data yet', transform=ax.transAxes,
                    ha='center', va='center', fontsize=14, fontweight='bold',
                    color=self._plot_fg,
                    bbox=dict(facecolor='k', alpha=0.35, edgecolor='none', pad=3),
                )
        else:
            self.ax = self.fig.add_subplot(111)
            self.ax.set_facecolor(self._plot_bg)
            if mode == "Stress":
                self.ax.set_xlabel("Applied load (g)", color=self._plot_fg)
                self.ax.set_ylabel("T1+T2 (μs)", color=self._plot_fg)
                self.ax.set_title("Stress dependence (live)", color=self._plot_fg)
            elif mode == "Temperature":
                self.ax.set_xlabel("Temperature (°C)", color=self._plot_fg)
                self.ax.set_ylabel("T1+T2 (A·s)", color=self._plot_fg)
                self.ax.set_title("Temperature dependence (live)", color=self._plot_fg)
            else:
                self.ax.set_title("Live data", color=self._plot_fg)
            self.ax.grid(True, color=(0.35, 0.35, 0.35, 0.5))
            self.ax.tick_params(colors=self._plot_fg)
            for spine in self.ax.spines.values():
                spine.set_color(self._plot_fg)
            # Visible placeholder
            self.ax.text(
                0.5, 0.5, 'No data yet', transform=self.ax.transAxes,
                ha='center', va='center', fontsize=14, fontweight='bold',
                color=self._plot_fg,
                bbox=dict(facecolor='k', alpha=0.35, edgecolor='none', pad=3),
            )
        self.canvas.draw_idle()

    def _send_emulator_mode(self) -> None:
        if self.serial is None or getattr(self, "name_builder", None) is None:
            return
        fmt = self._current_format()
        cmd = None
        if fmt == "Stress":
            try:
                load = float(self.name_builder.s_load.value())
                d = self.name_builder.s_dir.currentData()
            except Exception:
                load, d = 2.5, 'a'
            cmd = f"MODE STRESS LOAD={load} DIR={d}\n"
        elif fmt == "Temperature":
            try:
                t = self.name_builder.t_temp.currentText()
            except Exception:
                t = "25C"
            cmd = f"MODE TEMP T={t}\n"
        elif fmt == "Maxion":
            cmd = "MODE MAXION\n"
        if cmd:
            try:
                self.serial.write(cmd.encode('ascii'))
            except Exception:
                pass

    def _on_format_changed(self) -> None:
        self._reset_plot_for_mode(self._current_format())
        self._send_emulator_mode()

    def _draw_throttled(self, min_interval: float = 0.05) -> None:
        if not hasattr(self, "canvas"):
            return
        now = time.perf_counter()
        if now - getattr(self, "_last_draw", 0.0) >= min_interval:
            try:
                self.canvas.draw_idle()
            except Exception:
                pass
            self._last_draw = now

    def _ingest_live_sample(self, line: str) -> None:
        fmt = self._current_format()
        parts = [p.strip() for p in line.strip().lstrip('>').split(';') if p.strip()]
        if not parts:
            return
        try:
            vals = [float(x.replace(',', '.')) for x in parts]
        except ValueError:
            return
        if fmt == 'Maxion':
            if len(vals) < 6 or not hasattr(self, 'ax_ch'):
                return
            sums = [vals[0] + vals[1], vals[2] + vals[3], vals[4] + vals[5]]
            store = self._rt_data.setdefault('maxion', [[], [], []])
            for i in range(3):
                store[i].append(sums[i])
                ax = self.ax_ch[i]
                x = np.arange(len(store[i]))
                ax.cla()
                ax.set_facecolor(self._plot_bg)
                ax.set_title(f"Channel {i+1} T1+T2", color=self._plot_fg)
                ax.set_xlabel("Sample index", color=self._plot_fg)
                ax.set_ylabel("sum", color=self._plot_fg)
                ax.grid(True, color=(0.35,0.35,0.35,0.5))
                for spine in ax.spines.values():
                    spine.set_color(self._plot_fg)
                ax.tick_params(colors=self._plot_fg)
                ax.scatter(x, store[i], s=0.2)
            self._draw_throttled()
            return
        if not hasattr(self, 'ax'):
            return
        if fmt == 'Stress':
            if len(vals) < 4:
                return
            y = vals[3]
            try:
                load = float(self.name_builder.s_load.value())
                d = str(self.name_builder.s_dir.currentData())
            except Exception:
                load, d = 0.0, 'a'
            key = (d, float(load))
            buf = self._rt_data.setdefault('stress', {})
            arr = buf.setdefault(key, [])
            arr.append(y)
            self.ax.cla()
            self.ax.set_facecolor(self._plot_bg)
            self.ax.set_xlabel("Applied load (g)", color=self._plot_fg)
            self.ax.set_ylabel("T1+T2 (μs)", color=self._plot_fg)
            self.ax.set_title("Stress dependence (live)", color=self._plot_fg)
            self.ax.grid(True, color=(0.35,0.35,0.35,0.5))
            colors = {'a': '#45A1D6', 'b': '#F09C67'}
            for (dirc, ld), yy in buf.items():
                x_center = ld + (-0.5 if dirc=='a' else +0.5)
                xs = [x_center + random.uniform(-0.5, 0.5) for _ in yy]
                self.ax.scatter(xs[-1000:], yy[-1000:], s=0.2, c=colors.get(dirc,'gray'), label=f"{ld:g}{dirc}")
            try:
                self.ax.legend(loc='best', markerscale=10, fontsize=8)
            except Exception:
                pass
            self._draw_throttled()
        elif fmt == 'Temperature':
            if len(vals) < 4:
                return
            y = vals[3]
            sub = self._rt_data.setdefault('temp', {'cont': [], 25: [], 100: []})
            try:
                t_sel = self.name_builder.t_temp.currentText()
            except Exception:
                t_sel = '25C'
            if t_sel == '25-100C':
                step = 0.05
                t = 25.0 + self._temp_cont_counter * step
                if t > 100.0:
                    self._temp_cont_counter = 0
                    t = 25.0
                self._temp_cont_counter += 1
                sub['cont'].append((t, y))
            else:
                temp_val = 25 if '25' in t_sel else 100
                sub[temp_val].append(y)
            self.ax.cla()
            self.ax.set_facecolor(self._plot_bg)
            self.ax.set_xlabel("Temperature (°C)", color=self._plot_fg)
            self.ax.set_ylabel("T1+T2 (A·s)", color=self._plot_fg)
            self.ax.set_title("Temperature dependence (live)", color=self._plot_fg)
            self.ax.grid(True, color=(0.35,0.35,0.35,0.5))
            if sub['cont']:
                tx, ty = zip(*sub['cont'])
                self.ax.scatter(list(tx)[-5000:], list(ty)[-5000:], s=0.2, c='#6B6B6B', label='25-100C')
            for tv, col in [(25,'#45A1D6'), (100,'#F09C67')]:
                if sub[tv]:
                    xs = [tv + random.uniform(-0.5, 0.5) for _ in sub[tv]]
                    self.ax.scatter(xs[-2000:], sub[tv][-2000:], s=0.2, c=col, label=f"{tv}°C")
            try:
                self.ax.legend(loc='best', markerscale=10, fontsize=8)
            except Exception:
                pass
            self._draw_throttled()
        return

    # --- Connect overlay and UI gating -------------------------------------
    def _setup_connect_overlay(self) -> None:
        scroll = getattr(self.ui, 'left_scroll', None)
        if scroll is None or not hasattr(scroll, 'viewport'):
            self._overlay = None
            return
        ov = QtWidgets.QFrame(scroll.viewport())
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
            serial = getattr(self.ui, 'groupBox_serial', None)
            if serial is not None:
                pt = serial.mapTo(scroll.viewport(), QtCore.QPoint(0, serial.height()))
                y = pt.y() + 8
            else:
                y = 0
            vp = scroll.viewport().rect()
            ov.setGeometry(0, max(0, y), vp.width(), max(0, vp.height()-max(0, y)))
        except Exception:
            ov.setGeometry(scroll.viewport().rect())

    def resizeEvent(self, ev: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(ev)
        scroll = getattr(self.ui, 'left_scroll', None)
        if getattr(self, '_overlay', None) is not None and scroll is not None:
            try:
                self._position_connect_overlay()
            except Exception:
                pass

    def _apply_connected_state(self) -> None:
        connected = bool(self.connected)
        # Enable/disable settings groups except the serial group
        for gb in ('groupBox_log', 'groupBox_cmd'):
            try:
                getattr(self.ui, gb).setEnabled(connected)
            except Exception:
                pass
        if getattr(self, '_overlay', None) is not None:
            try:
                self._position_connect_overlay()
                self._overlay.setVisible(not connected)
            except Exception:
                pass
        elif fmt == 'Temperature':
            if len(vals) < 4:
                return
            y = vals[3]
            sub = self._rt_data.setdefault('temp', {'cont': [], 25: [], 100: []})
            try:
                t_sel = self.name_builder.t_temp.currentText()
            except Exception:
                t_sel = '25C'
            if t_sel == '25-100C':
                step = 0.05
                t = 25.0 + self._temp_cont_counter * step
                if t > 100.0:
                    self._temp_cont_counter = 0
                    t = 25.0
                self._temp_cont_counter += 1
                sub['cont'].append((t, y))
            else:
                temp_val = 25 if '25' in t_sel else 100
                sub[temp_val].append(y)
            self.ax.cla()
            self.ax.set_facecolor(self._plot_bg)
            self.ax.set_xlabel("Temperature (°C)", color=self._plot_fg)
            self.ax.set_ylabel("T1+T2 (A·s)", color=self._plot_fg)
            self.ax.set_title("Temperature dependence (live)", color=self._plot_fg)
            self.ax.grid(True, color=(0.35,0.35,0.35,0.5))
            if sub['cont']:
                tx, ty = zip(*sub['cont'])
                self.ax.scatter(list(tx)[-5000:], list(ty)[-5000:], s=0.2, c='#6B6B6B', label='25-100C')
            for tv, col in [(25,'#45A1D6'), (100,'#F09C67')]:
                if sub[tv]:
                    xs = [tv + random.uniform(-0.5, 0.5) for _ in sub[tv]]
                    self.ax.scatter(xs[-2000:], sub[tv][-2000:], s=0.2, c=col, label=f"{tv}°C")
            try:
                self.ax.legend(loc='best', markerscale=10, fontsize=8)
            except Exception:
                pass
            self._draw_throttled()

    def send_command(self):
        """Send the text from the command line edit down the serial port."""
        cmd = self.ui.lineEdit_port_command.text() + "\n"
        if self.serial is not None:
            self.serial.write(cmd.encode('ascii'))

    def start_logging(self):
        """Open the selected log file, begin logging, or toggle pause."""
        if self.logging_on:
            self.paused = not self.paused
            if self.paused:
                self.ui.pushButton_record.setText("Resume")
                self.ui.pushButton_record.setToolTip("Resume logging")
                self._finish_time = None
            else:
                self.ui.pushButton_record.setText("Pause")
                self.ui.pushButton_record.setToolTip("Pause logging")
            self.update_time_estimate()
            return

        file_base = self.ui.lineEdit_log_file.text().strip()
        if not file_base:
            return

        use_sub = self.ui.checkBox_subdir.isChecked()
        target_dir = self.root_log_dir
        if use_sub:
            parts = file_base.split()
            if len(parts) > 1:
                folder = " ".join(parts[:-1])
                folder = re.sub(r'[<>:"/\\|?*]', "_", folder)
                target_dir = os.path.join(self.root_log_dir, folder)
        os.makedirs(target_dir, exist_ok=True)
        full_path = os.path.join(target_dir, f"{file_base}.txt")

        self.log_dir = target_dir
        self.ui.lineEdit_log_dir.setText(self.log_dir)
        self.ui.lineEdit_log_file.setText(file_base)

        # If the file already exists, ask the user what to do.
        mode = "w"
        if os.path.exists(full_path):
            msg = QtWidgets.QMessageBox(self)
            msg.setWindowTitle("File exists")
            msg.setIcon(QtWidgets.QMessageBox.Icon.Question)
            base = os.path.basename(full_path)
            msg.setText(f"'{base}' already exists.")
            msg.setInformativeText("Choose an action:")

            replace_btn = msg.addButton("Replace", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
            continue_btn = msg.addButton("Continue", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = msg.addButton("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)

            msg.exec()
            clicked = msg.clickedButton()
            if clicked is cancel_btn:
                return
            elif clicked is continue_btn:
                mode = "a"
            else:
                mode = "w"

        try:
            # Use line buffering so each newline is written promptly
            self.log_file = open(full_path, mode, buffering=1)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self, "Error", f"Failed to open {full_path}: {exc}"
            )
            return

        self.sample_count = self.ui.spinBox_log_sample_count.value()
        self.sample_idx = 0
        self.logging_on = True
        self.paused = False
        self._finish_time = None

        self.ui.pushButton_record.setText("Pause")
        self.ui.pushButton_record.setToolTip("Pause logging")
        self.ui.pushButton_cancel.setEnabled(True)

        cast(Any, self.ui).progressBar_logging.setMaximum(self.sample_count)
        cast(Any, self.ui).progressBar_logging.setValue(0)
        cast(Any, self.ui).progressBar_logging.setToolTip(
            f"0/{self.sample_count} samples"
        )
        self.update_time_estimate()

    def cancel_logging(self):
        """Abort the current logging session."""
        if not self.logging_on:
            return
        assert self.log_file is not None

        self.log_file.close()
        self.logging_on = False
        self.paused = False
        self.ui.pushButton_record.setText("Record")
        self.ui.pushButton_record.setToolTip("Start logging")
        self.ui.pushButton_cancel.setEnabled(False)
        cast(Any, self.ui).progressBar_logging.setToolTip("")
        self._finish_time = None
        self.update_time_estimate()

def main(log_dir: str | None = None) -> QtWidgets.QWidget:
    """Launch the data logger window and return the created widget.

    When called from another running Qt application (e.g. :class:`launcher.MasterLauncher`)
    no additional :class:`~PyQt6.QtWidgets.QApplication` instance will be created
    and control is returned immediately after showing the window. The caller's
    event loop continues running in this case.
    """

    log_dir = log_dir or DEFAULT_LOG_DIR

    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        owns_app = True

    apply_system_theme(app)

    window = MainWindow(log_dir)
    window.showMaximized()

    WINDOWS.append(window)

    if owns_app:
        sys.exit(app.exec())
    return window

if __name__ == "__main__":
    main()



