"""PyVISA-based current annealing logger with ramp/reverse controls.

This GUI mirrors the serial-port current annealing logger but talks to VISA
instruments via ``pyvisa``/``pyvisa-py``.  It can:

* list and connect to VISA resources
* start/stop a current annealing ramp with optional automatic reversal
* reverse immediately on user request
* log voltage/current readings to a file while showing live readouts

Missing hardware is tolerated; all instrument interactions are guarded so the
UI remains responsive even if the device vanishes mid-run.
"""

from __future__ import annotations

import sys
import time
import math
from pathlib import Path

from PyQt6 import QtCore, QtWidgets, QtGui
import pyvisa
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
)
from matplotlib.figure import Figure

from plotting.utils import ensure_app_theme, install_standard_menu, theme_manager


class PyVISAAnnealingLogger(QtWidgets.QWidget):
    """GUI that performs current annealing and logging using PyVISA."""

    def __init__(self) -> None:  # pragma: no cover - mostly UI wiring
        super().__init__()
        self.setWindowTitle("PyVISA Current Annealing Logger")

        self.rm = pyvisa.ResourceManager()
        self.inst: pyvisa.resources.Resource | None = None
        self.logfile: Path | None = None
        self.settings = QtCore.QSettings("microwire", "pyvisa_annealing")

        # ------------------------------------------------------------------ widgets
        # connection
        self.resource_combo = QtWidgets.QComboBox()
        self.resource_combo.setEditable(True)
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.connect_button = QtWidgets.QPushButton("Connect")

        # logging path
        default_dir = self.settings.value("log_dir", str(Path.home() / "Downloads"), type=str)
        default_file = self.settings.value("log_file", "anneal_log.txt", type=str)
        self.dir_edit = QtWidgets.QLineEdit(default_dir)
        self.file_edit = QtWidgets.QLineEdit(default_file)
        self.dir_button = QtWidgets.QPushButton("Browse…")
        self.log_button = QtWidgets.QPushButton("Start Log")
        self.log_button.setEnabled(False)

        # annealing controls
        self.start_button = QtWidgets.QPushButton("Start annealing")
        self.start_button.setEnabled(False)
        self.stop_button = QtWidgets.QPushButton("Stop annealing")
        self.stop_button.setEnabled(False)
        self.reverse_button = QtWidgets.QPushButton("Reverse current now")
        self.reverse_button.setEnabled(False)
        self.reverse_after = QtWidgets.QCheckBox("Reverse to zero after max")
        self.reverse_after.setChecked(True)
        self.max_spin = QtWidgets.QDoubleSpinBox()
        self.max_spin.setRange(0.001, 10.0)
        self.max_spin.setValue(1.0)
        self.max_spin.setSuffix(" A")
        self.step_spin = QtWidgets.QDoubleSpinBox()
        self.step_spin.setRange(0.001, 1.0)
        self.step_spin.setValue(0.01)
        self.step_spin.setSingleStep(0.001)
        self.step_spin.setSuffix(" A")
        self.interval_spin = QtWidgets.QSpinBox()
        self.interval_spin.setRange(50, 5000)
        self.interval_spin.setValue(200)
        self.interval_spin.setSuffix(" ms")
        self.dwell_spin = QtWidgets.QDoubleSpinBox()
        self.dwell_spin.setRange(0.0, 600.0)
        self.dwell_spin.setDecimals(1)
        self.dwell_spin.setSingleStep(0.5)
        self.dwell_spin.setValue(5.0)
        self.dwell_spin.setSuffix(" s")
        self.loop_spin = QtWidgets.QSpinBox()
        self.loop_spin.setRange(0, 1000)
        self.loop_spin.setSpecialValueText("∞")
        self.loop_spin.setValue(1)

        # readouts
        self.voltage_value = QtWidgets.QLabel("--")
        self.current_value = QtWidgets.QLabel("--")
        self.set_value = QtWidgets.QLabel("--")
        for lbl in (self.voltage_value, self.current_value, self.set_value):
            lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            lbl.setMinimumWidth(60)
        self.output_view = QtWidgets.QPlainTextEdit(readOnly=True)

        # live plots using matplotlib (Resistance vs current and sample count)
        self.fig = Figure()
        self.canvas = FigureCanvas(self.fig)
        self.ax_ri = self.fig.add_subplot(211)
        self.ax_rn = self.fig.add_subplot(212)
        (self.line_ri_up,) = self.ax_ri.plot([], [], color="tab:orange")
        (self.line_ri_down,) = self.ax_ri.plot([], [], color="tab:blue")
        (self.line_rn_up,) = self.ax_rn.plot([], [], color="tab:orange")
        (self.line_rn_down,) = self.ax_rn.plot([], [], color="tab:blue")
        self.ax_ri.set_xlabel("Current [mA]")
        self.ax_ri.set_ylabel("Resistance [Ohm]")
        self.ax_rn.set_xlabel("N [-]")
        self.ax_rn.set_ylabel("Resistance [Ohm]")
        self.curr_mA_up: list[float] = []
        self.res_up: list[float] = []
        self.curr_mA_down: list[float] = []
        self.res_down: list[float] = []
        self.n_up: list[int] = []
        self.n_down: list[int] = []
        self.sample_idx = 0
        self.first_sample = True
        self.update_plot_colors()

        # ------------------------------------------------------------------ layout
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.resource_combo)
        top.addWidget(self.refresh_button)
        top.addWidget(self.connect_button)

        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(self.dir_edit)
        path_row.addWidget(self.dir_button)
        path_row.addWidget(self.file_edit)

        config_row = QtWidgets.QGridLayout()
        config_row.addWidget(QtWidgets.QLabel("Max"), 0, 0)
        config_row.addWidget(self.max_spin, 0, 1)
        config_row.addWidget(QtWidgets.QLabel("Step"), 0, 2)
        config_row.addWidget(self.step_spin, 0, 3)
        config_row.addWidget(QtWidgets.QLabel("Interval"), 0, 4)
        config_row.addWidget(self.interval_spin, 0, 5)
        config_row.addWidget(QtWidgets.QLabel("Dwell"), 0, 6)
        config_row.addWidget(self.dwell_spin, 0, 7)
        config_row.addWidget(self.reverse_after, 1, 0, 1, 4)
        config_row.addWidget(QtWidgets.QLabel("Loops"), 1, 4)
        config_row.addWidget(self.loop_spin, 1, 5)
        config_row.setColumnStretch(7, 1)

        proc_row = QtWidgets.QHBoxLayout()
        proc_row.addWidget(self.start_button)
        proc_row.addWidget(self.stop_button)
        proc_row.addWidget(self.reverse_button)

        values_box = QtWidgets.QWidget()
        values_layout = QtWidgets.QFormLayout(values_box)
        values_layout.addRow("Voltage [V]", self.voltage_value)
        values_layout.addRow("Current [mA]", self.current_value)
        values_layout.addRow("Set current [mA]", self.set_value)

        self.estimate_label = QtWidgets.QLabel("Estimated run time: --")

        left_column = QtWidgets.QVBoxLayout()
        left_column.addLayout(top)
        left_column.addLayout(path_row)
        left_column.addWidget(self.log_button)
        left_column.addLayout(config_row)
        left_column.addWidget(self.estimate_label)
        left_column.addLayout(proc_row)
        left_column.addWidget(values_box)
        left_column.addWidget(self.output_view)

        content = QtWidgets.QWidget(self)
        content_layout = QtWidgets.QHBoxLayout(content)
        content_layout.addLayout(left_column)
        content_layout.addWidget(self.canvas, stretch=1)

        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.addWidget(content)
        install_standard_menu(self, help_topic="logger_pyvisa_current_annealing")
        theme_manager().theme_changed.connect(self._apply_theme_update)

        # ---------------------------------------------------------------- connections
        self.refresh_button.clicked.connect(self.refresh_resources)
        self.connect_button.clicked.connect(self.handle_connect)
        self.dir_button.clicked.connect(self.choose_dir)
        self.log_button.clicked.connect(self.handle_log)
        self.start_button.clicked.connect(self.start_process)
        self.stop_button.clicked.connect(self.stop_process)
        self.reverse_button.clicked.connect(self.reverse_now)
        self.max_spin.valueChanged.connect(self.update_time_estimate)
        self.step_spin.valueChanged.connect(self.update_time_estimate)
        self.interval_spin.valueChanged.connect(self.update_time_estimate)
        self.dwell_spin.valueChanged.connect(self.update_time_estimate)
        self.loop_spin.valueChanged.connect(self.update_time_estimate)
        self.reverse_after.toggled.connect(self.update_time_estimate)

        # timers
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.timeout.connect(self.poll_once)
        self.process_timer = QtCore.QTimer(self)
        self.process_timer.timeout.connect(self.process_step)

        # state
        self.current_set = 0.0
        self.ramping_up = True
        self.max_voltage = 30.0
        self._max_voltage_dialog = False
        self._loop_target = 1
        self._loops_completed = 0
        self._hold_remaining_ms = 0.0
        self._nonzero_seen = False
        self._zero_count = 0
        self._zero_limit = 6
        self._last_nonzero_time: float | None = None
        self._contact_grace = 2.0

        self.refresh_resources()
        self.update_time_estimate()

    # ------------------------------------------------------------------ utilities
    def log(self, msg: str) -> None:
        self.output_view.appendPlainText(msg)

    def update_time_estimate(self) -> None:
        step = max(self.step_spin.value(), 1e-6)
        interval = max(self.interval_spin.value(), 1) / 1000.0
        max_i = max(self.max_spin.value(), 0.0)
        dwell = max(self.dwell_spin.value(), 0.0)
        loops = self.loop_spin.value()
        if max_i <= 0.0 or step <= 0.0:
            self.estimate_label.setText("Estimated run time: --")
            return
        steps = math.ceil(max_i / step)
        up_time = steps * interval
        cycle_time = up_time + dwell
        if self.reverse_after.isChecked():
            cycle_time = up_time * 2 + dwell
        if loops == 0:
            self.estimate_label.setText("Estimated run time: ∞ (continuous)")
        else:
            total = cycle_time * loops
            if total < 120:
                self.estimate_label.setText(f"Estimated run time: {total:.1f} s")
            else:
                self.estimate_label.setText(f"Estimated run time: {total/60:.1f} min")

    def refresh_resources(self) -> None:
        try:
            resources = sorted(self.rm.list_resources())
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.log(f"Resource query failed: {exc}")
            resources = []
        for local in ("ttyV0", "ttyV1"):
            path = Path(local)
            if path.exists():
                res = f"ASRL{path.resolve()}::INSTR"
                if res not in resources:
                    resources.append(res)
        self.resource_combo.clear()
        for r in resources:
            self.resource_combo.addItem(r)

    def update_plot_colors(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        palette = app.palette()
        scheme = app.styleHints().colorScheme()
        win = palette.color(QtGui.QPalette.ColorRole.Window)
        base = palette.color(QtGui.QPalette.ColorRole.Base)
        text = palette.color(QtGui.QPalette.ColorRole.Text)
        win_rgb = (win.redF(), win.greenF(), win.blueF())
        base_rgb = (base.redF(), base.greenF(), base.blueF())
        text_rgb = (text.redF(), text.greenF(), text.blueF())
        self.fig.set_facecolor(win_rgb)
        for ax in (self.ax_ri, self.ax_rn):
            ax.set_facecolor(base_rgb)
            ax.tick_params(colors=text_rgb)
            ax.xaxis.label.set_color(text_rgb)
            ax.yaxis.label.set_color(text_rgb)
            for spine in ax.spines.values():
                spine.set_color(text_rgb)
            ax.grid(True, color=(0.35,0.35,0.35,0.5) if scheme == QtCore.Qt.ColorScheme.Dark else (0.8,0.8,0.8,0.8))

    def _apply_theme_update(self, _: str) -> None:
        self.update_plot_colors()

    # -------------------------------------------------------------------- slots
    def disconnect(self) -> None:
        """Close the instrument and reset UI state."""
        if self.process_timer.isActive():
            self.stop_process()
        if self.poll_timer.isActive():
            self.handle_log()
        elif self.logfile is not None:
            try:
                self.logfile.close()
            except Exception:
                pass
            self.logfile = None
            self.log_button.setText("Start Log")
        if self.inst is not None:
            try:
                self.inst.close()
            except Exception:
                pass
            self.inst = None
        self.connect_button.setText("Connect")
        self.log_button.setEnabled(False)
        self.start_button.setEnabled(False)

    def handle_connect(self) -> None:
        if self.inst is not None:
            self.disconnect()
            return

        resource = self.resource_combo.currentText().strip()
        if not resource:
            QtWidgets.QMessageBox.warning(self, "No resource", "Select a VISA resource")
            return
        try:
            self.inst = self.rm.open_resource(resource)
            self.inst.timeout = 1000  # shorten blocking operations
            if resource.upper().startswith("ASRL"):
                try:
                    self.inst.baud_rate = 115200
                except Exception:
                    pass
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))
            self.inst = None
            return
        self.connect_button.setText("Disconnect")
        self.log_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.log(f"Connected to {resource}")

    def choose_dir(self) -> None:  # pragma: no cover - interactive
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select log directory", self.dir_edit.text()
        )
        if path:
            self.dir_edit.setText(path)
            self.settings.setValue("log_dir", path)

    # -------------------------------------------------------------------- logging
    def handle_log(self) -> None:
        if self.poll_timer.isActive():
            self.poll_timer.stop()
            if self.logfile is not None:
                try:
                    self.logfile.close()
                except Exception:
                    pass
            self.logfile = None
            self.log_button.setText("Start Log")
            return

        log_dir = Path(self.dir_edit.text()).expanduser()
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            QtWidgets.QMessageBox.critical(self, "Error", f"Cannot create {log_dir}")
            return
        fname = log_dir / self.file_edit.text()
        self.settings.setValue("log_dir", str(log_dir))
        self.settings.setValue("log_file", fname.name)
        try:
            self.logfile = open(fname, "a", encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))
            self.logfile = None
            return
        self.log(f"Logging to {fname}")
        self.log_button.setText("Stop Log")
        self.curr_mA_up.clear(); self.res_up.clear()
        self.curr_mA_down.clear(); self.res_down.clear()
        self.n_up.clear(); self.n_down.clear()
        self.sample_idx = 0
        self.first_sample = True
        self.poll_timer.start(1000)

    def poll_once(self) -> None:
        if self.inst is None:
            return
        try:
            voltage = float(self.inst.query("MEAS:VOLT?"))
            current = float(self.inst.query("MEAS:CURR?"))
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.log(f"Query failed: {exc}")
            self.stop_process()
            self.handle_log()
            return
        resistance = voltage / current if abs(current) > 1e-9 else float("inf")
        if self.first_sample:
            self.first_sample = False
            self.voltage_value.setText(f"{voltage:.3f}")
            self.current_value.setText(f"{current*1000:.3f}")
            self.set_value.setText(f"{self.current_set*1000:.3f}")
            return
        if self.logfile is not None:
            line = f"{time.time():.3f}      {voltage}       {current}       {resistance}\n"
            try:
                self.logfile.write(line)
                self.logfile.flush()
            except Exception:  # pragma: no cover - disk issues
                self.log("Failed to write to log file")
                self.handle_log()
        self.log(f"V={voltage:.3f} I={current:.3f} R={resistance:.3f}")
        self.voltage_value.setText(f"{voltage:.3f}")
        self.current_value.setText(f"{current*1000:.3f}")
        self.set_value.setText(f"{self.current_set*1000:.3f}")
        self.sample_idx += 1
        if self.ramping_up:
            self.curr_mA_up.append(current * 1000.0)
            self.res_up.append(resistance)
            self.n_up.append(self.sample_idx)
            if len(self.res_up) > 1000:
                self.curr_mA_up = self.curr_mA_up[-1000:]
                self.res_up = self.res_up[-1000:]
                self.n_up = self.n_up[-1000:]
        else:
            self.curr_mA_down.append(current * 1000.0)
            self.res_down.append(resistance)
            self.n_down.append(self.sample_idx)
            if len(self.res_down) > 1000:
                self.curr_mA_down = self.curr_mA_down[-1000:]
                self.res_down = self.res_down[-1000:]
                self.n_down = self.n_down[-1000:]
        self.line_ri_up.set_data(self.curr_mA_up, self.res_up)
        self.line_ri_down.set_data(self.curr_mA_down, self.res_down)
        self.line_rn_up.set_data(self.n_up, self.res_up)
        self.line_rn_down.set_data(self.n_down, self.res_down)
        self.ax_ri.relim(); self.ax_ri.autoscale_view()
        self.ax_rn.relim(); self.ax_rn.autoscale_view()
        self.canvas.draw_idle()
        now = time.monotonic()
        if abs(current) > 1e-3:
            self._nonzero_seen = True
            self._zero_count = 0
            self._last_nonzero_time = now
        elif self._nonzero_seen and self.process_timer.isActive():
            self._zero_count += 1
            timed_out = (
                self._last_nonzero_time is not None
                and (now - self._last_nonzero_time) > self._contact_grace
            )
            if self._zero_count >= self._zero_limit or timed_out:
                self.log("Contact lost — stopping annealing")
                QtWidgets.QMessageBox.warning(
                    self,
                    "Contact lost",
                    "Current fell to zero after ramping. The process has been stopped.",
                )
                self.stop_process()
                self.handle_log()
                self._nonzero_seen = False
                return
        if (
            self.ramping_up
            and self.process_timer.isActive()
            and voltage >= self.max_voltage
            and not self._max_voltage_dialog
        ):
            self.handle_voltage_limit()

    # ----------------------------------------------------------- annealing logic
    def handle_voltage_limit(self) -> None:
        self._max_voltage_dialog = True
        self._hold_remaining_ms = 0.0
        self.process_timer.stop()
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Voltage limit reached")
        msg.setText("Power supply reached 30 V. What do you want to do?")
        hold_btn = msg.addButton("Hold current", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        reverse_btn = msg.addButton("Reverse to zero", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        stop_btn = msg.addButton("Stop measurement", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is reverse_btn:
            self.ramping_up = False
            self.process_timer.start(self.interval_spin.value())
        elif clicked is stop_btn:
            self.stop_process()
        else:
            pass

    def start_process(self) -> None:
        if self.inst is None:
            QtWidgets.QMessageBox.warning(self, "Not connected", "Connect to an instrument first")
            return
        self.current_set = 0.0
        self.ramping_up = True
        self._max_voltage_dialog = False
        self._loop_target = self.loop_spin.value()
        self._loops_completed = 0
        self._hold_remaining_ms = 0.0
        self._nonzero_seen = False
        self._zero_count = 0
        self._last_nonzero_time = None
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.reverse_button.setEnabled(True)
        self.curr_mA_up.clear(); self.res_up.clear()
        self.curr_mA_down.clear(); self.res_down.clear()
        self.n_up.clear(); self.n_down.clear()
        self.sample_idx = 0
        self.first_sample = True
        self.set_value.setText("0.000")
        self.process_timer.start(self.interval_spin.value())

    def stop_process(self) -> None:
        self.process_timer.stop()
        if self.inst is not None:
            try:
                self.inst.write("CURR 0")
            except Exception:
                pass
        self.start_button.setEnabled(self.inst is not None)
        self.stop_button.setEnabled(False)
        self.reverse_button.setEnabled(False)
        self.set_value.setText("0.000")
        self._max_voltage_dialog = False
        self.current_set = 0.0
        self.ramping_up = True
        self._hold_remaining_ms = 0.0
        self._nonzero_seen = False
        self._zero_count = 0
        self._last_nonzero_time = None

    def reverse_now(self) -> None:
        if self.inst is None:
            return
        self.ramping_up = False
        self._hold_remaining_ms = 0.0
        if not self.process_timer.isActive():
            self.process_timer.start(self.interval_spin.value())

    def process_step(self) -> None:
        if self.inst is None:
            self.stop_process()
            return
        if self._hold_remaining_ms > 0.0:
            self._hold_remaining_ms = max(
                0.0, self._hold_remaining_ms - self.interval_spin.value()
            )
            if self._hold_remaining_ms <= 0.0:
                if self.reverse_after.isChecked():
                    self.ramping_up = False
                else:
                    self.stop_process()
                    return
            try:
                self.inst.write(f"CURR {max(self.current_set, 0):.4f}")
                self.set_value.setText(f"{self.current_set*1000:.3f}")
            except Exception as exc:
                self.log(f"Write failed: {exc}")
                self.stop_process()
            return
        step = self.step_spin.value()
        max_i = self.max_spin.value()
        if self.ramping_up:
            self.current_set = min(max_i, self.current_set + step)
            if self.current_set >= max_i - 1e-9:
                dwell_ms = self.dwell_spin.value() * 1000.0
                if self.reverse_after.isChecked():
                    self.ramping_up = False
                    if dwell_ms > 0:
                        self._hold_remaining_ms = dwell_ms
                else:
                    if dwell_ms > 0:
                        self._hold_remaining_ms = dwell_ms
                    else:
                        self.stop_process()
                        return
        else:
            self.current_set = max(0.0, self.current_set - step)
            if self.current_set <= 0.0 + 1e-9:
                if self.reverse_after.isChecked():
                    self._loops_completed += 1
                    if self._loop_target != 0 and self._loops_completed >= self._loop_target:
                        self.stop_process()
                        return
                    self.current_set = 0.0
                    self.ramping_up = True
                else:
                    self.stop_process()
                    return
        try:
            self.inst.write(f"CURR {max(self.current_set, 0):.4f}")
            self.set_value.setText(f"{self.current_set*1000:.3f}")
        except Exception as exc:
            self.log(f"Write failed: {exc}")
            self.stop_process()

    # -------------------------------------------------------------------- Qt
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # pragma: no cover - GUI
        self.disconnect()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
def main() -> QtWidgets.QWidget:  # pragma: no cover - manual use
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    ensure_app_theme(app)
    win = PyVISAAnnealingLogger()
    win.showMaximized()
    return win


if __name__ == "__main__":  # pragma: no cover - manual launch
    main()

