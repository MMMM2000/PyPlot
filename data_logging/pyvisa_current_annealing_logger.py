"""PyVISA-based current annealing logger.

A lightweight alternative to the QtSerialPort implementation that talks to
instruments using the VISA protocol via the ``pyvisa`` package. It provides a
very small GUI that can connect to a VISA resource, periodically query voltage
and current, and append readings to a text file.

This is intentionally minimal – it is meant as a starting point for comparing a
PyVISA approach with the existing serial logger.
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
        self.output_view = QtWidgets.QPlainTextEdit(readOnly=True)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.resource_combo)
        top.addWidget(self.refresh_button)
        top.addWidget(self.connect_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.log_button)
        layout.addWidget(self.output_view)

        self.refresh_button.clicked.connect(self.refresh_resources)
        self.connect_button.clicked.connect(self.handle_connect)
        self.log_button.clicked.connect(self.handle_log)

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
            self.timer.stop()
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

        download_dir = Path.home() / "Downloads"
        download_dir.mkdir(exist_ok=True)
        fname = download_dir / f"anneal_log_{int(time.time())}.txt"
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
