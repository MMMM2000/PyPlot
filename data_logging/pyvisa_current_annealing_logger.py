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
from pathlib import Path

from PyQt6 import QtCore, QtWidgets, QtGui
import pyvisa
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
)
from matplotlib.figure import Figure

from plotting.utils import apply_system_theme


class PyVISAAnnealingLogger(QtWidgets.QWidget):
    """GUI that performs current annealing and logging using PyVISA."""

    def __init__(self) -> None:  # pragma: no cover - mostly UI wiring
        super().__init__()
        self.setWindowTitle("PyVISA Current Annealing Logger")

        self.rm = pyvisa.ResourceManager()
        self.inst: pyvisa.resources.Resource | None = None
        self.logfile: Path | None = None

        # ------------------------------------------------------------------ widgets
        # connection
        self.resource_combo = QtWidgets.QComboBox()
        self.resource_combo.setEditable(True)
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.connect_button = QtWidgets.QPushButton("Connect")

        # logging path
        self.dir_edit = QtWidgets.QLineEdit(str(Path.home() / "Downloads"))
        self.file_edit = QtWidgets.QLineEdit("anneal_log.txt")
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

        # readouts
        self.voltage_label = QtWidgets.QLabel("V: --")
        self.current_label = QtWidgets.QLabel("I: --")
        self.output_view = QtWidgets.QPlainTextEdit(readOnly=True)

        # live plots using matplotlib (Resistance vs current and sample count)
        self.fig = Figure()
        self.canvas = FigureCanvas(self.fig)
        self.ax_ri = self.fig.add_subplot(211)
        self.ax_rn = self.fig.add_subplot(212)
        (self.line_ri,) = self.ax_ri.plot([], [], "y")
        (self.line_rn,) = self.ax_rn.plot([], [], "c")
        self.ax_ri.set_xlabel("Current [mA]")
        self.ax_ri.set_ylabel("Resistance [Ohm]")
        self.ax_rn.set_xlabel("N [-]")
        self.ax_rn.set_ylabel("Resistance [Ohm]")
        self.curr_mA: list[float] = []
        self.res_data: list[float] = []
        self.n_data: list[int] = []
        self.sample_idx = 0

        # ------------------------------------------------------------------ layout
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.resource_combo)
        top.addWidget(self.refresh_button)
        top.addWidget(self.connect_button)

        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(self.dir_edit)
        path_row.addWidget(self.dir_button)
        path_row.addWidget(self.file_edit)

        config_row = QtWidgets.QHBoxLayout()
        config_row.addWidget(QtWidgets.QLabel("Max"))
        config_row.addWidget(self.max_spin)
        config_row.addWidget(QtWidgets.QLabel("Step"))
        config_row.addWidget(self.step_spin)
        config_row.addWidget(QtWidgets.QLabel("Interval"))
        config_row.addWidget(self.interval_spin)
        config_row.addWidget(self.reverse_after)

        proc_row = QtWidgets.QHBoxLayout()
        proc_row.addWidget(self.start_button)
        proc_row.addWidget(self.stop_button)
        proc_row.addWidget(self.reverse_button)

        values_row = QtWidgets.QHBoxLayout()
        values_row.addWidget(self.voltage_label)
        values_row.addWidget(self.current_label)

        left = QtWidgets.QVBoxLayout()
        left.addLayout(top)
        left.addLayout(path_row)
        left.addWidget(self.log_button)
        left.addLayout(config_row)
        left.addLayout(proc_row)
        left.addLayout(values_row)
        left.addWidget(self.output_view)

        layout = QtWidgets.QHBoxLayout(self)
        layout.addLayout(left)
        layout.addWidget(self.canvas, stretch=1)

        # ---------------------------------------------------------------- connections
        self.refresh_button.clicked.connect(self.refresh_resources)
        self.connect_button.clicked.connect(self.handle_connect)
        self.dir_button.clicked.connect(self.choose_dir)
        self.log_button.clicked.connect(self.handle_log)
        self.start_button.clicked.connect(self.start_process)
        self.stop_button.clicked.connect(self.stop_process)
        self.reverse_button.clicked.connect(self.reverse_now)

        # timers
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.timeout.connect(self.poll_once)
        self.process_timer = QtCore.QTimer(self)
        self.process_timer.timeout.connect(self.process_step)

        # state
        self.current_set = 0.0
        self.ramping_up = True

        self.refresh_resources()

    # ------------------------------------------------------------------ utilities
    def log(self, msg: str) -> None:
        self.output_view.appendPlainText(msg)

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
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select log directory", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)

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
        try:
            self.logfile = open(fname, "a", encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))
            self.logfile = None
            return
        self.log(f"Logging to {fname}")
        self.log_button.setText("Stop Log")
        self.curr_mA.clear(); self.res_data.clear(); self.n_data.clear()
        self.sample_idx = 0
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
        if self.logfile is not None:
            line = f"{time.time():.3f}      {voltage}       {current}       {resistance}\n"
            try:
                self.logfile.write(line)
                self.logfile.flush()
            except Exception:  # pragma: no cover - disk issues
                self.log("Failed to write to log file")
                self.handle_log()
        self.log(f"V={voltage:.3f} I={current:.3f} R={resistance:.3f}")
        self.voltage_label.setText(f"V: {voltage:.3f}")
        self.current_label.setText(f"I: {current:.3f}")
        self.curr_mA.append(current * 1000.0)
        self.res_data.append(resistance)
        self.sample_idx += 1
        self.n_data.append(self.sample_idx)
        if len(self.res_data) > 1000:
            self.curr_mA = self.curr_mA[-1000:]
            self.res_data = self.res_data[-1000:]
            self.n_data = self.n_data[-1000:]
        self.line_ri.set_data(self.curr_mA, self.res_data)
        self.line_rn.set_data(self.n_data, self.res_data)
        self.ax_ri.relim(); self.ax_ri.autoscale_view()
        self.ax_rn.relim(); self.ax_rn.autoscale_view()
        self.canvas.draw_idle()

    # ----------------------------------------------------------- annealing logic
    def start_process(self) -> None:
        if self.inst is None:
            QtWidgets.QMessageBox.warning(self, "Not connected", "Connect to an instrument first")
            return
        self.current_set = 0.0
        self.ramping_up = True
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.reverse_button.setEnabled(True)
        self.curr_mA.clear(); self.res_data.clear(); self.n_data.clear()
        self.sample_idx = 0
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
        self.current_set = 0.0
        self.ramping_up = True

    def reverse_now(self) -> None:
        if self.process_timer.isActive():
            self.ramping_up = False

    def process_step(self) -> None:
        if self.inst is None:
            self.stop_process()
            return
        step = self.step_spin.value()
        max_i = self.max_spin.value()
        if self.ramping_up:
            self.current_set += step
            if self.current_set >= max_i:
                if self.reverse_after.isChecked():
                    self.ramping_up = False
                else:
                    self.stop_process()
        else:
            self.current_set -= step
            if self.current_set <= 0:
                self.stop_process()
                return
        try:
            self.inst.write(f"CURR {max(self.current_set, 0):.4f}")
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
    apply_system_theme(app)
    win = PyVISAAnnealingLogger()
    win.showMaximized()
    return win


if __name__ == "__main__":  # pragma: no cover - manual launch
    main()

