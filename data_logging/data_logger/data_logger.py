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
import pandas as pd
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
try:
    import pyqtgraph as pg  # Optional realtime backend
except Exception:
    pg = None

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
        # Open directory button
        if hasattr(self.ui, 'pushButton_open_dir'):
            self.ui.pushButton_open_dir.clicked.connect(self.open_log_dir)

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
        self._rate_window: deque[float] = deque(maxlen=10)
        self._rate_start = time.perf_counter()
        self._rate_samples = 0
        self._last_time_secs: int | None = None
        self._finish_time: float | None = None

        cast(Any, self.ui).progressBar_logging.setMaximum(self.sample_count)
        cast(Any, self.ui).pushButton_cancel.setEnabled(False)
        cast(Any, self.ui).progressBar_logging.setValue(0)
        cast(Any, self.ui).progressBar_logging.setToolTip("")
        self.ui.checkBox_subdir.setChecked(True)

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
        self.ui.spinBox_log_sample_count.valueChanged.connect(self._sync_window_max)

        self.update_time_estimate()
        # Overlay to prompt connection; disable settings until connected
        self._setup_connect_overlay()
        self._apply_connected_state()


        # Live plotting setup
        self._last_draw = 0.0
        self._init_live_plot()
        # Overlay for batch collection
        self._record_overlay = None
        self._setup_record_overlay()
        # Optional realtime backend (use PyQtGraph for realtime when enabled)
        rt = getattr(self.ui, 'checkBox_rt_plot', None)
        if rt is not None:
            rt.stateChanged.connect(self._toggle_rt_plot)
        fps_box = getattr(self.ui, 'spinBox_rt_fps', None)
        if fps_box is not None:
            fps_box.valueChanged.connect(self._update_rt_fps)
        gl_cb = getattr(self.ui, 'checkBox_rt_gl', None)
        if gl_cb is not None:
            gl_cb.toggled.connect(self._apply_pg_opengl)
            self._apply_pg_opengl()
        # Pre-create a PlotWidget to avoid first-use stalls or restarts
        self._prewarm_rt_backend()
        win_box = getattr(self.ui, 'spinBox_rt_window', None)
        if win_box is not None:
            win_box.valueChanged.connect(self._update_rt_window)
            win_box.setSingleStep(500)
            self._rt_window = int(win_box.value())
            self._sync_window_max()
        else:
            self._rt_window = 2000
        # Defaults for realtime plotting
        fps_initial = getattr(self.ui, 'spinBox_rt_fps', None)
        self._pg_min_interval = 1.0 / float(fps_initial.value() if fps_initial is not None else 30)
        self._pg_last_draw = 0.0
        self._pg_timer: QtCore.QTimer | None = None
        if getattr(self, 'name_builder', None) is not None:
            try:
                self.name_builder.combo_format.currentIndexChanged.connect(self._on_format_changed)
                self.name_builder.s_load.valueChanged.connect(self._send_emulator_mode)
                self.name_builder.s_dir.currentIndexChanged.connect(self._send_emulator_mode)
                self.name_builder.t_temp.currentIndexChanged.connect(self._send_emulator_mode)
                # Clear graph on identity change
                self.name_builder.s_comp.textChanged.connect(self._clear_on_identity_change)
                self.name_builder.s_sample.textChanged.connect(self._clear_on_identity_change)
                self.name_builder.s_number.textChanged.connect(self._clear_on_identity_change)
                self.name_builder.s_end.currentIndexChanged.connect(self._clear_on_identity_change)
            except Exception:
                pass
        # Buffers for stress means
        self._batch_values = []
        self._rt_means = {}

    def populate_ports(self):
        """Scan available serial ports and populate the combo box."""
        self.ui.comboBox_port.clear()
        seen: set[str] = set()
        # 1) OS-reported ports via Qt
        for info in QSerialPortInfo.availablePorts():
            sysloc = info.systemLocation() if hasattr(info, 'systemLocation') else info.portName()
            name = info.portName()
            label = name
            try:
                if info.description():
                    label += f" - {info.description()}"
            except Exception:
                pass
            # Store systemLocation when available; logger falls back to opening
            # by basename if needed (serial_port handles both).
            self.ui.comboBox_port.addItem(label, userData=(sysloc or name))
            seen.add(sysloc or name)
        # 2) Extra virtual symlinks (macOS/Linux): /dev/cu.ttyV* and /dev/ttyV*
        try:
            import platform
            from glob import glob
            if platform.system() in {"Darwin", "Linux"}:
                extras = sorted(set(glob("/dev/cu.ttyV*") + glob("/dev/ttyV*") + glob(str(Path.cwd()/"ttyV*"))))
                for path in extras:
                    rp = os.path.realpath(path)
                    # Prefer the final /dev/* node name so QSerialPort can open it
                    name = os.path.basename(rp) if rp.startswith('/dev/') else os.path.basename(path)
                    label = f"{os.path.basename(path)} - Virtual pair"
                    if name not in seen:
                        self.ui.comboBox_port.insertItem(0, label, userData=name)
                        seen.add(name)
        except Exception:
            pass
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
            # Reset rate tracking on fresh connection
            self.sample_rate = None
            self._rate_window.clear()
            self._rate_samples = 0
            self._rate_start = time.perf_counter()
            # Inform emulator of selected format
            try:
                self._send_emulator_mode()
            except Exception:
                pass
            self._apply_connected_state()
        else:
            # Proactively disconnect the signal before closing the port
            try:
                if self.serial is not None:
                    self.serial.readyRead.disconnect(self.read_from_port)
            except Exception:
                pass
            if self._serial_ctx is not None:
                self._serial_ctx.__exit__(None, None, None)
                self._serial_ctx = None
            self.serial = None
            self.connected = False
            self.ui.pushButton_connect_port.setText("Connect to port")
            self.ui.groupBox_commands.setEnabled(False)
            self.ui.label_connection_indicator.setText("\u25cf Disconnected")
            self.ui.label_connection_indicator.setStyleSheet("color: red;")
            self.sample_rate = None
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

    def open_log_dir(self) -> None:
        """Open the current log directory in the system file manager."""
        try:
            path = self.ui.lineEdit_log_dir.text().strip() or self.log_dir
            if not path:
                return
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        except Exception:
            pass

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
            extra_lines: list[bytes] = []
            while self.serial.canReadLine():
                extra_lines.append(bytes(self.serial.readLine()))  # type: ignore[arg-type]

            now = time.perf_counter()
            n_lines = 1 + len(extra_lines)
            self._rate_samples += n_lines
            if now - self._rate_start >= 1.0:
                inst = self._rate_samples / (now - self._rate_start)
                self._rate_window.append(inst)
                self.sample_rate = sum(self._rate_window) / len(self._rate_window)
                self._rate_samples = 0
                self._rate_start = now

            if self.paused:
                return
            if self.logging_on:
                assert self.log_file is not None

                # strip leading '>' if present, then write
                self.log_file.write(self.port_response.lstrip(">"))
                self.sample_idx += 1
                cast(Any, self.ui).progressBar_logging.setValue(self.sample_idx)
                # Keep Cancel enabled while logging
                try:
                    self.ui.pushButton_cancel.setEnabled(True)
                except Exception:
                    pass

                if self.sample_rate:
                    remaining_samples = self.sample_count - self.sample_idx
                    self._finish_time = now + remaining_samples / self.sample_rate

                if self.sample_idx >= self.sample_count:
                    # finalize this batch
                    try:
                        fmt_now = self._current_format()
                        if fmt_now == 'Stress' and self._batch_values:
                            import numpy as _np
                            m = float(_np.mean(_np.asarray(self._batch_values, dtype=float)))
                            try:
                                ld = float(self.name_builder.s_load.value())
                                d = str(self.name_builder.s_dir.currentData())
                            except Exception:
                                ld, d = 0.0, 'a'
                            self._rt_means[(d, float(ld))] = m
                    except Exception:
                        pass
                    self.log_file.close()
                    self.logging_on = False
                    self.ui.pushButton_record.setEnabled(True)
                    # Reset record button text/state
                    try:
                        self.ui.pushButton_record.setText("Record")
                        self.ui.pushButton_record.setToolTip("Start logging")
                    except Exception:
                        pass
                    self.ui.pushButton_cancel.setEnabled(False)
                    self._finish_time = None
                    # Update plot to include the new mean and hide overlay
                    try:
                        # Ensure Matplotlib canvas for final rendering (switch from PG if active)
                        if getattr(self.ui, 'checkBox_rt_plot', None) is not None and self.ui.checkBox_rt_plot.isChecked() and pg is not None:
                            self._stop_pg_timer()
                            self._init_live_plot()
                        if fmt_now == 'Stress':
                            self._draw_stress_live()
                        elif fmt_now == 'Temperature':
                            self._draw_temp_final()
                        elif fmt_now == 'Maxion':
                            self._draw_maxion_final()
                        self._show_record_overlay(False)
                    except Exception:
                        pass
                # Keep emulator streaming continuously; no STOP
            # Feed live plot while logging only when realtime is enabled
            rt_enabled = getattr(self.ui, 'checkBox_rt_plot', None) is not None and self.ui.checkBox_rt_plot.isChecked()
            if self.logging_on and not self.paused and rt_enabled:
                try:
                    self._ingest_live_sample(self.port_response)
                except Exception:
                    pass
            # Always accumulate batch values for stress, even when realtime is off
            if self.logging_on and not self.paused and self._current_format() == 'Stress' and not rt_enabled:
                parts0 = [p.strip() for p in self.port_response.strip().lstrip('>').split(';') if p.strip()]
                if len(parts0) >= 4:
                    try:
                        yv0 = float(parts0[3].replace(',', '.'))
                    except Exception:
                        yv0 = None
                    if yv0 is not None and yv0 > 0:
                        try:
                            load0 = float(self.name_builder.s_load.value())
                            d0 = str(self.name_builder.s_dir.currentData())
                        except Exception:
                            load0, d0 = 0.0, 'a'
                        key0 = (d0, float(load0))
                        buf0 = self._rt_data.setdefault('stress', {})
                        arr0 = buf0.setdefault(key0, [])
                        arr0.append(yv0)
                        self._batch_values.append(yv0)

            # Process any additional lines captured above without re-sampling the rate
            for raw_bytes in extra_lines:
                self.port_response = raw_bytes.decode('ascii')
                if not self.paused and self.logging_on:
                    assert self.log_file is not None
                    self.log_file.write(self.port_response.lstrip(">"))
                    self.sample_idx += 1
                    cast(Any, self.ui).progressBar_logging.setValue(self.sample_idx)
                    if self.sample_rate:
                        remaining_samples = self.sample_count - self.sample_idx
                        self._finish_time = now + remaining_samples / self.sample_rate
                    if self.sample_idx >= self.sample_count:
                        try:
                            if self._current_format() == 'Stress' and self._batch_values:
                                import numpy as _np
                                m = float(_np.mean(_np.asarray(self._batch_values, dtype=float)))
                                try:
                                    ld = float(self.name_builder.s_load.value())
                                    d = str(self.name_builder.s_dir.currentData())
                                except Exception:
                                    ld, d = 0.0, 'a'
                                self._rt_means[(d, float(ld))] = m
                        except Exception:
                            pass
                        self.log_file.close()
                        self.logging_on = False
                        self.ui.pushButton_record.setEnabled(True)
                        # Reset record button text/state
                        try:
                            self.ui.pushButton_record.setText("Record")
                            self.ui.pushButton_record.setToolTip("Start logging")
                        except Exception:
                            pass
                        self.ui.pushButton_cancel.setEnabled(False)
                        self._finish_time = None
                        try:
                            fmt_now2 = self._current_format()
                            # Ensure Matplotlib canvas for final rendering (switch from PG if active)
                            if getattr(self.ui, 'checkBox_rt_plot', None) is not None and self.ui.checkBox_rt_plot.isChecked() and pg is not None:
                                self._stop_pg_timer()
                                self._init_live_plot()
                            if fmt_now2 == 'Stress':
                                self._draw_stress_live()
                            elif fmt_now2 == 'Temperature':
                                self._draw_temp_final()
                            elif fmt_now2 == 'Maxion':
                                self._draw_maxion_final()
                            self._show_record_overlay(False)
                        except Exception:
                            pass
                if self.logging_on and not self.paused and rt_enabled:
                    try:
                        self._ingest_live_sample(self.port_response)
                    except Exception:
                        pass
                # Always accumulate for stress batches even when realtime plot is off
                try:
                    if self.logging_on and not self.paused and self._current_format() == 'Stress' and not rt_enabled:
                        parts = [p.strip() for p in self.port_response.strip().lstrip('>').split(';') if p.strip()]
                        if len(parts) >= 4:
                            yv = float(parts[3].replace(',', '.'))
                            try:
                                load = float(self.name_builder.s_load.value())
                                d = str(self.name_builder.s_dir.currentData())
                            except Exception:
                                load, d = 0.0, 'a'
                            key = (d, float(load))
                            buf = self._rt_data.setdefault('stress', {})
                            arr = buf.setdefault(key, [])
                            arr.append(yv)
                            self._batch_values.append(yv)
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
            try:
                self.ui.label_input_rate.setText("Input: Paused")
            except Exception:
                pass
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
            try:
                self.ui.label_input_rate.setText(f"Input: {self.sample_rate:.0f} Hz")
            except Exception:
                pass
        else:
            self._last_time_secs = None
            label.setText("Time remaining: N/A")
            try:
                self.ui.label_input_rate.setText("Input: N/A")
            except Exception:
                pass

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
                try:
                    self._apply_stress_title()
                except Exception:
                    self.ax.set_title("Stress dependence (live)", color=self._plot_fg)
            elif mode == "Temperature":
                self.ax.set_xlabel("Temperature (°C)", color=self._plot_fg)
                self.ax.set_ylabel("T1+T2 (µs)", color=self._plot_fg)
                try:
                    self._apply_temp_title()
                except Exception:
                    self.ax.set_title("Temperature dependence", color=self._plot_fg)
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

    def _current_identity(self) -> tuple[str, str, str, str, str]:
        comp = getattr(self.name_builder.s_comp, 'text', lambda: "")().strip()
        sample = getattr(self.name_builder.s_sample, 'text', lambda: "")().strip()
        number = getattr(self.name_builder.s_number, 'text', lambda: "")().strip()
        end = str(getattr(self.name_builder.s_end, 'currentData', lambda: "")())
        anneal = getattr(self.name_builder.s_anneal, 'text', lambda: "")().strip()
        return (comp, sample, number, end, anneal)

    def _clear_on_identity_change(self) -> None:
        try:
            self._rt_data = {}
            self._rt_means = {}
            if hasattr(self, 'ax'):
                self._reset_plot_for_mode(self._current_format())
        except Exception:
            pass

    # --- Title helpers ------------------------------------------------------
    def _apply_temp_title(self) -> None:
        if not hasattr(self, 'ax'):
            return
        try:
            comp = self.name_builder.t_comp.text().strip()
            sample = self.name_builder.t_sample.text().strip()
            anneal = self.name_builder.t_anneal.text().strip()
            label = "T1+T2 (µs)"
            self.ax.set_title(f"{comp} {sample} {anneal} — {label}", color=self._plot_fg)
        except Exception:
            self.ax.set_title("Temperature dependence", color=self._plot_fg)

    # --- Realtime backend toggle (pyqtgraph) --------------------------------
    def _clear_plot_container(self) -> QtWidgets.QVBoxLayout | None:
        container = getattr(self.ui, 'plot_container', None)
        if container is None:
            return None
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
        return layout

    def _toggle_rt_plot(self) -> None:
        enabled = bool(self.ui.checkBox_rt_plot.isChecked())
        if enabled and pg is not None:
            try:
                self._apply_pg_opengl()
                pg.setConfigOptions(antialias=False)
            except Exception:
                pass
            mode = self._current_format()
            layout = self._clear_plot_container()
            if layout is None:
                return
            self._pg_last_draw = 0.0
            if mode == 'Maxion':
                self.pg_plots = [pg.PlotWidget(), pg.PlotWidget(), pg.PlotWidget()]
                for i, w in enumerate(self.pg_plots, start=1):
                    w.setBackground(self.palette().color(QtGui.QPalette.ColorRole.Base))
                    w.showGrid(x=True, y=True, alpha=0.3)
                    w.setLabel('bottom', 'N')
                    w.setLabel('left', 'T1+T2 (arb units)')
                    w.setTitle(f'Channel {i} T1+T2')
                    layout.addWidget(w)
                self.pg_scatters = [pg.ScatterPlotItem(size=1, pen=None, brush=pg.mkBrush(140,140,140)) for _ in range(3)]
                for sc, w in zip(self.pg_scatters, self.pg_plots):
                    w.addItem(sc)
                from collections import deque as _dq
                self.pg_data = [_dq(maxlen=self._rt_window) for _ in range(3)]
                self._start_pg_timer()
            elif mode == 'Temperature':
                self.pg_plot = pg.PlotWidget()
                self.pg_plot.setBackground(self.palette().color(QtGui.QPalette.ColorRole.Base))
                self.pg_plot.showGrid(x=True, y=True, alpha=0.3)
                self.pg_plot.setLabel('bottom', 'N')
                self.pg_plot.setLabel('left', 'T1+T2 (µs)')
                self.pg_plot.setTitle('Temperature dependence (live)')
                layout.addWidget(self.pg_plot)
                self.pg_scatter = pg.ScatterPlotItem(size=1, pen=None, brush=pg.mkBrush(140,140,140))
                self.pg_plot.addItem(self.pg_scatter)
                from collections import deque as _dq
                self.pg_temp_data = _dq(maxlen=self._rt_window)
                self.pg_temp_count = 0
                self._start_pg_timer()
            else:
                self._init_live_plot()
                self._stop_pg_timer()
            self._send_emulator_mode()
        else:
            self._init_live_plot()
            self._stop_pg_timer()

    def _update_rt_fps(self) -> None:
        try:
            fps = min(60.0, float(self.ui.spinBox_rt_fps.value()))
            self._pg_min_interval = max(0.001, 1.0 / max(1.0, fps))
            # Update QTimer interval if running
            if self._pg_timer is not None:
                self._pg_timer.setInterval(int(1000 * self._pg_min_interval))
        except Exception:
            self._pg_min_interval = 1.0 / 30.0

    def _sync_window_max(self) -> None:
        """Keep realtime window cap in sync with recording sample count."""
        try:
            max_samples = int(self.ui.spinBox_log_sample_count.value())
            win_box = getattr(self.ui, 'spinBox_rt_window', None)
            if win_box is not None:
                win_box.setMaximum(max_samples)
                if win_box.value() > max_samples:
                    win_box.setValue(max_samples)
        except Exception:
            pass
        self._update_rt_window()

    def _update_rt_window(self) -> None:
        try:
            self._rt_window = max(1, int(self.ui.spinBox_rt_window.value()))
        except Exception:
            self._rt_window = max(1, self._rt_window)
        try:
            from collections import deque as _dq
            if hasattr(self, 'pg_data'):
                self.pg_data = [_dq(list(d)[-self._rt_window:], maxlen=self._rt_window) for d in self.pg_data]
            if hasattr(self, 'pg_temp_data'):
                self.pg_temp_data = _dq(list(self.pg_temp_data)[-self._rt_window:], maxlen=self._rt_window)
            if getattr(self.ui, 'checkBox_rt_plot', None) is not None and self.ui.checkBox_rt_plot.isChecked():
                mode = self._current_format()
                if pg is not None and mode == 'Maxion' and hasattr(self, 'pg_plots'):
                    for p in self.pg_plots:
                        p.setXRange(1, max(2, self._rt_window), padding=0)
                elif pg is not None and mode == 'Temperature' and hasattr(self, 'pg_plot'):
                    self.pg_plot.setXRange(1, max(2, self._rt_window), padding=0)
                elif hasattr(self, 'ax'):
                    cur = getattr(self, 'sample_idx', 0)
                    self.ax.set_xlim(max(1, cur - self._rt_window + 1), max(self._rt_window, cur))
                    if hasattr(self, 'canvas'):
                        self.canvas.draw_idle()
        except Exception:
            pass

    def _start_pg_timer(self) -> None:
        if pg is None:
            return
        if self._pg_timer is None:
            self._pg_timer = QtCore.QTimer(self)
            self._pg_timer.timeout.connect(self._pg_update_plots)
        self._pg_timer.setInterval(int(1000 * self._pg_min_interval))
        self._pg_timer.start()

    def _stop_pg_timer(self) -> None:
        t = getattr(self, '_pg_timer', None)
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass

    def _apply_pg_opengl(self) -> None:
        if pg is None:
            return
        try:
            use_gl = bool(self.ui.checkBox_rt_gl.isChecked()) if getattr(self.ui, 'checkBox_rt_gl', None) is not None else True
            pg.setConfigOptions(useOpenGL=use_gl)
        except Exception:
            pass

    def _prewarm_rt_backend(self) -> None:
        """Instantiate a hidden PlotWidget so first toggle doesn't restart."""
        if pg is None:
            return
        try:
            self._rt_dummy = pg.PlotWidget()
            self._rt_dummy.show()
            QtWidgets.QApplication.processEvents()
            self._rt_dummy.hide()
        except Exception:
            pass

    def _pg_update_plots(self) -> None:
        # Called by QTimer at selected FPS
        mode = self._current_format()
        if pg is None:
            return
        now = time.perf_counter()
        if now - getattr(self, '_pg_last_draw', 0.0) < getattr(self, '_pg_min_interval', 0.03):
            return
        if mode == 'Maxion' and hasattr(self, 'pg_plots') and hasattr(self, 'pg_scatters'):
            for i in range(3):
                data = list(self.pg_data[i])
                if data:
                    xs = list(range(1, len(data) + 1))
                    ys = [v for v in data]
                    self.pg_scatters[i].setData(xs, ys)
                try:
                    self.pg_plots[i].setXRange(1, max(2, self._rt_window), padding=0)
                except Exception:
                    pass
        elif mode == 'Temperature' and hasattr(self, 'pg_plot') and hasattr(self, 'pg_scatter'):
            data = list(self.pg_temp_data)
            if data:
                xs = list(range(1, len(data) + 1))
                ys = [v for v in data]
                self.pg_scatter.setData(xs, ys)
            try:
                self.pg_plot.setXRange(1, max(2, self._rt_window), padding=0)
            except Exception:
                pass
        self._pg_last_draw = now

    # --- Batch overlay -------------------------------------------------------
    def _setup_record_overlay(self) -> None:
        container = getattr(self.ui, 'plot_container', None)
        if container is None:
            return
        ov = QtWidgets.QFrame(container)
        ov.setStyleSheet("background: rgba(0,0,0,160);")
        lay = QtWidgets.QVBoxLayout(ov)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        msg = QtWidgets.QLabel("Loading data…")
        msg.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet("color: white; font-size: 16px; font-weight: 600;")
        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedWidth(220)
        bar.setMaximumHeight(8)
        lay.addWidget(msg, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(bar, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        self._record_overlay = ov
        self._position_record_overlay()
        ov.hide()

    def _position_record_overlay(self) -> None:
        container = getattr(self.ui, 'plot_container', None)
        ov = getattr(self, '_record_overlay', None)
        if container is None or ov is None:
            return
        ov.setGeometry(container.rect())

    def _show_record_overlay(self, show: bool) -> None:
        ov = getattr(self, '_record_overlay', None)
        if ov is not None:
            self._position_record_overlay()
            ov.setVisible(bool(show))
            if show:
                try:
                    QtWidgets.QApplication.processEvents()
                except Exception:
                    pass

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
            cmd = f"MODE STRESS LOAD={load} DIR={d}\r\n"
        elif fmt == "Temperature":
            try:
                t = self.name_builder.t_temp.currentText()
            except Exception:
                t = "25C"
            cmd = f"MODE TEMP T={t}\r\n"
        elif fmt == "Maxion":
            cmd = "MODE MAXION\r\n"
        if cmd:
            try:
                cmd_b = cmd.encode('ascii')
                self.serial.write(cmd_b)
                try:
                    self.serial.flush()
                except Exception:
                    pass
                try:
                    self.serial.clear(QtSerialPort.QSerialPort.Direction.Input)
                except Exception:
                    pass
            except Exception:
                cmd_b = None
            if cmd_b is not None:
                def _resend() -> None:
                    if self.serial is None:
                        return
                    try:
                        self.serial.write(cmd_b)
                        try:
                            self.serial.flush()
                        except Exception:
                            pass
                        try:
                            self.serial.clear(QtSerialPort.QSerialPort.Direction.Input)
                        except Exception:
                            pass
                    except Exception:
                        pass
                try:
                    QtCore.QTimer.singleShot(50, _resend)
                    QtCore.QTimer.singleShot(150, _resend)
                    QtCore.QTimer.singleShot(300, _resend)
                except Exception:
                    pass

    def _on_format_changed(self) -> None:
        # Rebuild the plotting surface for the new mode
        if getattr(self.ui, 'checkBox_rt_plot', None) is not None and self.ui.checkBox_rt_plot.isChecked() and pg is not None:
            self._toggle_rt_plot()
        else:
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
            if len(vals) < 6:
                return
            sums = [vals[0] + vals[1], vals[2] + vals[3], vals[4] + vals[5]]
            # PyQtGraph realtime if active
            if getattr(self.ui, 'checkBox_rt_plot', None) is not None and self.ui.checkBox_rt_plot.isChecked() and pg is not None and hasattr(self, 'pg_plots') and hasattr(self, 'pg_scatters'):
                for i in range(3):
                    self.pg_data[i].append(sums[i])
            else:
                store = self._rt_data.setdefault('maxion', [[], [], []])
                for i in range(3):
                    store[i].append(sums[i])
                    if len(store[i]) > self._rt_window:
                        del store[i][0:len(store[i]) - self._rt_window]
                    ax = self.ax_ch[i]
                    ax.cla()
                    ax.set_facecolor(self._plot_bg)
                    ax.set_title(f"Channel {i+1} T1+T2", color=self._plot_fg)
                    ax.set_xlabel("N", color=self._plot_fg)
                    ax.set_ylabel("T1+T2 (arb units)", color=self._plot_fg)
                    ax.grid(True, color=(0.35,0.35,0.35,0.5))
                    for spine in ax.spines.values():
                        spine.set_color(self._plot_fg)
                    ax.tick_params(colors=self._plot_fg)
                    try:
                        ax.set_xlim(1, max(2, self._rt_window))
                    except Exception:
                        pass
                    x = np.arange(1, len(store[i]) + 1)
                    ax.scatter(x, store[i], s=0.2)
                self._draw_throttled(min_interval=1.0)
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
            # Append to batch for mean computation
            self._batch_values.append(y)
            # Do not redraw per sample; draw once at end of batch
        elif fmt == 'Temperature':
            if len(vals) < 4:
                return
            y = vals[3]
            # PyQtGraph realtime if active
            if getattr(self.ui, 'checkBox_rt_plot', None) is not None and self.ui.checkBox_rt_plot.isChecked() and pg is not None and hasattr(self, 'pg_plot') and hasattr(self, 'pg_scatter'):
                self.pg_temp_count += 1
                self.pg_temp_data.append(y)
            else:
                series = self._rt_data.setdefault('temp_series', [])
                series.append(y)
                if len(series) > self._rt_window:
                    del series[0:len(series) - self._rt_window]
                self.ax.cla()
                self.ax.set_facecolor(self._plot_bg)
                self.ax.set_xlabel("N", color=self._plot_fg)
                self.ax.set_ylabel("T1+T2 (µs)", color=self._plot_fg)
                try:
                    self._apply_temp_title()
                except Exception:
                    self.ax.set_title("Temperature dependence", color=self._plot_fg)
                self.ax.grid(True, color=(0.35,0.35,0.35,0.5))
                try:
                    self.ax.set_xlim(1, max(2, self._rt_window))
                except Exception:
                    pass
                x = np.arange(1, len(series) + 1)
                self.ax.scatter(x, series, s=0.2, c='#6B6B6B')
                self._draw_throttled(min_interval=1.0)
        return

    # --- Connect overlay and UI gating -------------------------------------
    def _setup_connect_overlay(self) -> None:
        container = getattr(self.ui, 'left_panel', None)
        if container is None:
            self._overlay = None
            return
        ov = QtWidgets.QFrame(container)
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
        container = getattr(self.ui, 'left_panel', None)
        ov = getattr(self, '_overlay', None)
        if container is None or ov is None:
            return
        try:
            serial = getattr(self.ui, 'groupBox_serial', None)
            if serial is not None:
                pt = serial.mapTo(container, QtCore.QPoint(0, serial.height()))
                y = pt.y() + 8
            else:
                y = 0
            rc = container.rect()
            ov.setGeometry(0, max(0, y), rc.width(), max(0, rc.height()-max(0, y)))
        except Exception:
            ov.setGeometry(container.rect())

    def resizeEvent(self, ev: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(ev)
        container = getattr(self.ui, 'left_panel', None)
        if getattr(self, '_overlay', None) is not None and container is not None:
            try:
                self._position_connect_overlay()
            except Exception:
                pass
        try:
            self._position_record_overlay()
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

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        """Ensure the serial port is cleanly closed when the window closes."""
        try:
            # Best‑effort disconnect of signal
            if self.serial is not None:
                try:
                    self.serial.readyRead.disconnect(self.read_from_port)
                except Exception:
                    pass
            # Hide overlay, if shown
            try:
                self._show_record_overlay(False)
            except Exception:
                pass
        finally:
            try:
                if self._serial_ctx is not None:
                    self._serial_ctx.__exit__(None, None, None)
                    self._serial_ctx = None
            except Exception:
                pass
            self.serial = None
            self.connected = False
            try:
                self._stop_pg_timer()
            except Exception:
                pass
            event.accept()

    def send_command(self):
        """Send the text from the command line edit down the serial port."""
        cmd = self.ui.lineEdit_port_command.text() + "\n"
        if self.serial is not None:
            self.serial.write(cmd.encode('ascii'))

    # --- Final renderers for completed batches ------------------------------
    def _draw_temp_final(self) -> None:
        """Render the finished temperature log to look like the plotting script."""
        try:
            from plotting.temperature_dependence import core as T
            from plotting.common import maybe_handle_outliers
        except Exception:
            return
        fn = self.ui.lineEdit_log_file.text().strip()
        if not fn:
            return
        path = os.path.join(self.log_dir, f"{fn}.txt")
        try:
            data = T.load_data([path])
            data = maybe_handle_outliers(data)
        except Exception:
            return
        var = 'sum'
        # Prepare theme
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(self._plot_bg)
        # Raw continuous
        if T.PLOT_MODE in ("raw", "both"):
            sub = data[data["continuous"]]
            if not sub.empty:
                self.ax.scatter(
                    sub["temp"], sub[var], c=T.OVERALL_COLOR, s=T.MARKER_SIZE, marker=T.MARKER, label="raw 25-100C",
                )
            # Discrete temps
            disc = data[~data["continuous"]]
            for temp in sorted(disc["temp"].dropna().unique()):
                s = disc[disc["temp"] == temp]
                if s.empty:
                    continue
                jitter = np.random.uniform(-T.JITTER_SPAN, T.JITTER_SPAN, len(s))
                color = T.RAW_COLORS.get(int(temp), next(iter(T.RAW_COLORS.values())))
                self.ax.scatter(
                    s["temp"].astype(float) + jitter,
                    s[var],
                    c=color, s=T.MARKER_SIZE, marker=T.MARKER, label=f"raw {int(temp)}°C",
                )
        # Processed line for continuous
        if T.PLOT_MODE in ("processed", "both"):
            sub = data[data["continuous"]].sort_values("temp")
            if not sub.empty:
                med = sub[var].rolling(T.MED_WINDOW, center=True, min_periods=1).median()
                proc = med.rolling(T.MA_WINDOW, center=True, min_periods=1).mean()
                self.ax.plot(sub["temp"], proc, color=T.PROC_COLOR, linewidth=T.PROC_LW, label=f"med{T.MED_WINDOW}+mwa{T.MA_WINDOW}")
        # Labels and style
        try:
            comp = str(data["composition"].iat[0])
            sample = str(data["sample"].iat[0])
            anneal = str(data["anneal"].iat[0])
            title = f"{comp} {sample} {anneal} — {T.LABELS[var]}"
        except Exception:
            title = "Temperature dependence"
        self.ax.set_xlabel("Temperature (°C)", color=self._plot_fg)
        self.ax.set_ylabel(T.LABELS.get(var, "T1+T2 (µs)"), color=self._plot_fg)
        self.ax.set_title(title, color=self._plot_fg)
        self.ax.grid(True, color=(0.35,0.35,0.35,0.5))
        self.ax.tick_params(colors=self._plot_fg)
        for spine in self.ax.spines.values():
            spine.set_color(self._plot_fg)
        try:
            self.ax.legend(loc='best', fontsize=8)
        except Exception:
            pass
        self.canvas.draw_idle()

    def _draw_maxion_final(self) -> None:
        """Render the finished Maxion log to match the maxion plotting script."""
        try:
            from plotting.maxion_continuous import core as M
            from plotting.common import maybe_handle_outliers_series
        except Exception:
            return
        fn = self.ui.lineEdit_log_file.text().strip()
        if not fn:
            return
        path = os.path.join(self.log_dir, f"{fn}.txt")
        try:
            df = M.load_file(path)
        except Exception:
            return
        try:
            head, coils = M.parse_name(Path(path).stem)
        except Exception:
            head, coils = None, None
        self.fig.clear()
        self.ax_ch = [self.fig.add_subplot(311), self.fig.add_subplot(312), self.fig.add_subplot(313)]
        for i, ax in enumerate(self.ax_ch, start=1):
            ax.set_facecolor(self._plot_bg)
            # Build series
            y = (df[f"ch{i}_t1"].astype(float) + df[f"ch{i}_t2"].astype(float))
            try:
                y = maybe_handle_outliers_series(y, f"{Path(path).name}_CH{i}")
            except Exception:
                pass
            x = np.arange(1, len(y) + 1)
            # Raw only (no legend), per your requirement
            ax.scatter(x, y.to_numpy(), s=M.MARKER_SIZE)
            ax.set_xlabel("Sample index", color=self._plot_fg)
            ax.set_ylabel("T1+T2 (arb units)", color=self._plot_fg)
            if head is None or coils is None:
                title = f"CH{i} T1+T2"
            else:
                title = f"Head {head} — {coils} coils — CH{i} T1+T2"
            ax.set_title(title, color=self._plot_fg)
            ax.grid(True)
            ax.tick_params(colors=self._plot_fg)
            for spine in ax.spines.values():
                spine.set_color(self._plot_fg)
        self.canvas.draw_idle()

    def start_logging(self):
        """Open the selected log file, begin logging, or toggle pause."""
        if self.logging_on:
            self.paused = not self.paused
            if self.paused:
                self.ui.pushButton_record.setText("Resume")
                self.ui.pushButton_record.setToolTip("Resume logging")
                self._finish_time = None
                # Hide overlay while paused
                try:
                    self._show_record_overlay(False)
                except Exception:
                    pass
            else:
                self.ui.pushButton_record.setText("Pause")
                self.ui.pushButton_record.setToolTip("Pause logging")
                # Show overlay again if realtime is disabled
                try:
                    rt = getattr(self.ui, 'checkBox_rt_plot', None)
                    self._show_record_overlay(bool(rt is None or not rt.isChecked()))
                except Exception:
                    pass
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
        # Reset batch state, and if overwriting the file (mode == 'w') also
        # replace existing raw/mean for this (dir, load) in the graph.
        try:
            if self._current_format() == 'Stress':
                self._batch_values = []
                if mode == 'w':
                    load = float(self.name_builder.s_load.value())
                    d = str(self.name_builder.s_dir.currentData())
                    key = (d, float(load))
                    if 'stress' in getattr(self, '_rt_data', {}):
                        self._rt_data['stress'][key] = []
                    if key in getattr(self, '_rt_means', {}):
                        self._rt_means.pop(key, None)
        except Exception:
            pass
        # Show overlay if realtime is disabled
        try:
            rt = getattr(self.ui, 'checkBox_rt_plot', None)
            self._show_record_overlay(bool(rt is None or not rt.isChecked()))
        except Exception:
            pass
        # Emulator streams continuously; no RUN command needed

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
        try:
            self._show_record_overlay(False)
        except Exception:
            pass

    # --- Stress live helpers ------------------------------------------------
    def _apply_stress_title(self) -> None:
        if not hasattr(self, 'ax'):
            return
        try:
            comp = self.name_builder.s_comp.text().strip()
            title = self.name_builder.s_sample.text().strip()
            number = self.name_builder.s_number.text().strip()
            end = str(self.name_builder.s_end.currentData())
            anneal = self.name_builder.s_anneal.text().strip()
            samp = f"{number}{end}"
            label = "T1+T2 (μs)"
            self.ax.set_title(f"{comp} {title} {samp} {anneal} — {label}", color=self._plot_fg)
        except Exception:
            self.ax.set_title("Stress dependence (live)", color=self._plot_fg)

    def _draw_stress_live(self) -> None:
        if not hasattr(self, 'ax'):
            return
        buf = self._rt_data.get('stress', {})
        self.ax.cla()
        self.ax.set_facecolor(self._plot_bg)
        self.ax.set_xlabel("Applied load (g)", color=self._plot_fg)
        self.ax.set_ylabel("T1+T2 (μs)", color=self._plot_fg)
        self._apply_stress_title()
        self.ax.grid(True, color=(0.35,0.35,0.35,0.5))
        RAW_COLORS = {'a': '#45A1D6', 'b': '#F09C67'}
        MEAN_COLORS = {'a': '#00306E', 'b': '#965308'}
        # Raw scatter jittered around load ± 0.5
        for (dirc, ld), yy in buf.items():
            x_center = ld + (-0.5 if dirc=='a' else +0.5)
            xs = [x_center + random.uniform(-0.5, 0.5) for _ in yy]
            # Plot all points collected for this batch
            self.ax.scatter(xs, yy, s=0.6, c=RAW_COLORS.get(dirc,'gray'))
        # Means: line+scatter exactly at x=load, slightly darker colors
        if self._rt_means:
            for d in ('a','b'):
                pts = sorted([(ld, m) for (dirc, ld), m in self._rt_means.items() if dirc==d], key=lambda t: t[0])
                if not pts:
                    continue
                xs_m = [ld for ld, _ in pts]
                ys_m = [m for _, m in pts]
                self.ax.plot(xs_m, ys_m, 'o-', color=MEAN_COLORS.get(d, 'gray'), markersize=8, linewidth=2)
        # X ticks at loads
        try:
            loads = sorted({ld for (_d, ld) in buf.keys()})
            if loads:
                self.ax.set_xticks(loads)
        except Exception:
            pass
        # Delta if 2+ means for 'a'
        try:
            means_a = sorted([(ld, v) for (d, ld), v in self._rt_means.items() if d=='a'], key=lambda t: t[0])
            if len(means_a) >= 2:
                delta = means_a[-1][1] - means_a[0][1]
                self.ax.text(0.95, 0.05, f"Δ={delta:.2f} μs", transform=self.ax.transAxes, ha='right', va='bottom', fontsize=10, bbox=dict(facecolor='white', alpha=0.6))
        except Exception:
            pass
        # Legend: raw ↑/↓ and mean ↑/↓
        try:
            import matplotlib.lines as mlines
            raw_up = mlines.Line2D([], [], color=RAW_COLORS['a'], marker='o', linestyle='None', markersize=6, label='raw \u2191')
            raw_dn = mlines.Line2D([], [], color=RAW_COLORS['b'], marker='o', linestyle='None', markersize=6, label='raw \u2193')
            mean_up = mlines.Line2D([], [], color=MEAN_COLORS['a'], marker='o', linestyle='-', linewidth=2, markersize=8, label='mean \u2191')
            mean_dn = mlines.Line2D([], [], color=MEAN_COLORS['b'], marker='o', linestyle='-', linewidth=2, markersize=8, label='mean \u2193')
            self.ax.legend(handles=[raw_up, raw_dn, mean_up, mean_dn], loc='best', fontsize=8)
        except Exception:
            pass
        self._draw_throttled()

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
