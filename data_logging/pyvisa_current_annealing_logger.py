"""PyVISA-based current annealing logger.

A lightweight alternative to the QtSerialPort implementation that talks to
instruments using the VISA protocol via ``pyvisa``/``pyvisa-py``. The GUI can
connect to a VISA resource, periodically query voltage and current, and append
readings to a user-chosen log file. Basic live readouts of voltage and current
are shown while logging.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PyQt6 import QtCore, QtWidgets
import pyvisa

from plotting.utils import apply_system_theme


class PyVISAAnnealingLogger(QtWidgets.QWidget):
    """Simple GUI that logs measurements via PyVISA."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PyVISA Current Annealing Logger")

        self.rm = pyvisa.ResourceManager()
        self.inst: pyvisa.resources.Resource | None = None
        self.logfile = None

        self.resource_combo = QtWidgets.QComboBox()
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.log_button = QtWidgets.QPushButton("Start Log")
        self.log_button.setEnabled(False)

        self.dir_edit = QtWidgets.QLineEdit(str(Path.home() / "Downloads"))
        self.file_edit = QtWidgets.QLineEdit("anneal_log.txt")
        self.dir_button = QtWidgets.QPushButton("Browse…")

        self.voltage_label = QtWidgets.QLabel("V: --")
        self.current_label = QtWidgets.QLabel("I: --")

        self.output_view = QtWidgets.QPlainTextEdit(readOnly=True)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.resource_combo)
        top.addWidget(self.refresh_button)
        top.addWidget(self.connect_button)

        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(self.dir_edit)
        path_row.addWidget(self.dir_button)
        path_row.addWidget(self.file_edit)

        values_row = QtWidgets.QHBoxLayout()
        values_row.addWidget(self.voltage_label)
        values_row.addWidget(self.current_label)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(path_row)
        layout.addWidget(self.log_button)
        layout.addLayout(values_row)
        layout.addWidget(self.output_view)

        self.refresh_button.clicked.connect(self.refresh_resources)
        self.connect_button.clicked.connect(self.handle_connect)
        self.log_button.clicked.connect(self.handle_log)
        self.dir_button.clicked.connect(self.choose_dir)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll_once)

        self.refresh_resources()

    # ------------------------------------------------------------------ utils
    def log(self, msg: str) -> None:
        self.output_view.appendPlainText(msg)

    def refresh_resources(self) -> None:
        """Populate the resource dropdown with available VISA devices."""
        try:
            resources = sorted(self.rm.list_resources())
        except Exception as exc:  # pragma: no cover - hardware dependent
            resources = []
            self.log(f"Error listing resources: {exc}")
        self.resource_combo.clear()
        for r in resources:
            self.resource_combo.addItem(r)

    # ------------------------------------------------------------------- slots
    def handle_connect(self) -> None:
        if self.inst is not None:
            if self.timer.isActive():
                self.handle_log()
            try:
                self.inst.close()
            except Exception:
                pass
            self.inst = None
            self.connect_button.setText("Connect")
            self.log_button.setEnabled(False)
            return

        resource = self.resource_combo.currentText().strip()
        if not resource:
            QtWidgets.QMessageBox.warning(self, "No resource", "Select a VISA resource")
            return
        try:
            self.inst = self.rm.open_resource(resource)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))
            self.inst = None
            return
        self.connect_button.setText("Disconnect")
        self.log_button.setEnabled(True)
        self.log(f"Connected to {resource}")

    def handle_log(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
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
        self.log_button.setText("Stop Log")
        self.log(f"Logging to {fname}")
        self.timer.start(1000)  # query once per second

    def poll_once(self) -> None:
        if self.inst is None or self.logfile is None:
            return
        try:
            voltage = float(self.inst.query("MEAS:VOLT?"))
            current = float(self.inst.query("MEAS:CURR?"))
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.log(f"Query failed: {exc}")
            self.handle_log()
            return
        line = f"{time.time():.3f}	{voltage}	{current}\n"
        try:
            self.logfile.write(line)
            self.logfile.flush()
        except Exception:  # pragma: no cover - disk issues
            self.log("Failed to write to log file")
            self.handle_log()
            return
        self.log(line.strip())
        self.voltage_label.setText(f"V: {voltage:.3f}")
        self.current_label.setText(f"I: {current:.3f}")

    def choose_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select log directory", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)


# ---------------------------------------------------------------------------
def main() -> QtWidgets.QWidget:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    win = PyVISAAnnealingLogger()
    win.show()
    return win


if __name__ == "__main__":  # pragma: no cover - manual launch
    main()
