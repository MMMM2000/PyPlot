import sys
import os
from pathlib import Path
import time
import math
from typing import Any, cast, List
from collections import deque

from PyQt6 import QtCore, QtWidgets, QtSerialPort, QtGui
from PyQt6.QtSerialPort import QSerialPortInfo

from .logger_ui import UiMainWindow
from .file_name_builder import FileNameBuilderWidget, InfoLineEdit
from .serial_port import serial_connection

from plotting.utils import apply_system_theme

# =============================================================================
#                            USER CONFIGURATION
#
# 1) LOG_DIR: default directory where logged data will be stored. Modify this
#    path to your preferred location. The value can still be overridden via
#    the --log-dir command line option or the LOG_DIR environment variable.
# Use a logs folder in the user's home directory by default. This path works on
# all platforms and can be overridden via the ``LOG_DIR`` environment variable
# or the ``--log-dir`` command line option.
LOG_DIR = str(Path.home() / "python_plot_logs")

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
        self.sample_rate: float | None = None
        self._rate_window: deque[float] = deque(maxlen=1000)
        self.last_sample_time: float | None = None
        self._last_time_secs: int | None = None
        self._finish_time: float | None = None

        cast(Any, self.ui).progressBar_logging.setMaximum(self.sample_count)
        cast(Any, self.ui).pushButton_cancel.setEnabled(False)
        cast(Any, self.ui).progressBar_logging.setValue(0)
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
        self.ui.pushButton_cancel.clicked.connect(self.cancel_logging)
        refresh_btn = getattr(self.ui, "pushButton_refresh_ports", None)
        if refresh_btn is not None:
            refresh_btn.clicked.connect(self.populate_ports)
        self.ui.spinBox_log_sample_count.valueChanged.connect(self.update_time_estimate)

        self.update_time_estimate()

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

        self.lock.lock()
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

        self.lock.unlock()

    def update_response_label(self):
        """Refresh the on-screen label with the latest port_response."""
        self.ui.label_port_response.setText(self.port_response)

    def update_time_estimate(self) -> None:
        """Update the estimated logging time display."""
        label = getattr(self.ui, "label_time_estimate", None)
        if label is None:
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

    def send_command(self):
        """Send the text from the command line edit down the serial port."""
        cmd = self.ui.lineEdit_port_command.text() + "\n"
        if self.serial is not None:
            self.serial.write(cmd.encode('ascii'))

    def start_logging(self):
        """Open the selected log file and begin writing incoming samples."""
        file_base = self.ui.lineEdit_log_file.text().strip()
        if not file_base:
            return

        use_sub = self.ui.checkBox_subdir.isChecked()
        target_dir = self.root_log_dir
        if use_sub:
            parts = file_base.split()
            if len(parts) > 1:
                folder = " ".join(parts[:-1])
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
            self.log_file = open(full_path, mode)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self, "Error", f"Failed to open {full_path}: {exc}"
            )
            return

        self.sample_count = self.ui.spinBox_log_sample_count.value()
        self.sample_idx   = 0
        self.logging_on   = True
        self._finish_time = None

        self.ui.pushButton_record.setEnabled(False)
        self.ui.pushButton_cancel.setEnabled(True)

        cast(Any, self.ui).progressBar_logging.setMaximum(self.sample_count)
        cast(Any, self.ui).progressBar_logging.setValue(0)
        self.update_time_estimate()

    def cancel_logging(self):
        """Abort the current logging session."""
        if not self.logging_on:
            return
        assert self.log_file is not None

        self.log_file.close()
        self.logging_on = False
        self.ui.pushButton_record.setEnabled(True)
        self.ui.pushButton_cancel.setEnabled(False)
        self._finish_time = None
        self.update_time_estimate()

def main(argv: List[str] | None = None) -> QtWidgets.QWidget:
    """Launch the data logger window and return the created widget.

    When called from another running Qt application (e.g. :class:`launcher.MasterLauncher`)
    no additional :class:`~PyQt6.QtWidgets.QApplication` instance will be created
    and control is returned immediately after showing the window. The caller's
    event loop continues running in this case.
    """

    import argparse

    parser = argparse.ArgumentParser(description="Serial data logger (PyQt6)")
    parser.add_argument(
        "--log-dir",
        help="Directory to save logs [env: LOG_DIR]",
    )
    args = parser.parse_args(argv)

    log_dir = args.log_dir or DEFAULT_LOG_DIR

    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        owns_app = True

    apply_system_theme(app)

    window = MainWindow(log_dir)
    window.show()

    WINDOWS.append(window)

    if owns_app:
        sys.exit(app.exec())
    return window

if __name__ == "__main__":
    main()
