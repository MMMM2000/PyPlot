import sys
import os
import pathlib
from typing import Any, cast, List

from PyQt6 import QtCore, QtWidgets, QtSerialPort
from PyQt6.QtSerialPort import QSerialPortInfo

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parent))
    from logger_ui import Ui_MainWindow
else:
    from .logger_ui import Ui_MainWindow

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
        self.log_dir = log_dir
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.setWindowTitle("Data Logger")

        self.ui.lineEdit_log_dir.setText(self.log_dir)
        self.ui.pushButton_browse_dir.clicked.connect(self.choose_log_dir)

        # runtime state
        self.port_response = ""
        self.connected     = False
        self.port_name     = ""
        self.baudrate      = int(self.ui.comboBox_baudrate.currentText())
        self.ui.groupBox_commands.setEnabled(False)

        self.serial = QtSerialPort.QSerialPort()
        self.lock   = QtCore.QMutex()

        # update the on-screen response label every 10 ms
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_response_label)
        self.timer.start(10)

        # logging state
        self.log_file     = None  # will become an open file in start_logging()
        self.sample_count = 2000
        self.sample_idx   = 0
        self.logging_on   = False

        # set up progress bar (Pylance needs cast to know it exists)
        cast(Any, self.ui).progressBar_logging.setMaximum(self.sample_count)
        cast(Any, self.ui).pushButton_cancel.setEnabled(False)
        cast(Any, self.ui).progressBar_logging.setValue(0)

        os.makedirs(self.log_dir, exist_ok=True)

        # fill port list and set defaults
        self.populate_ports()
        self.ui.comboBox_baudrate.setCurrentIndex(0)  # highest bitrate
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())

        # show only the base name without extension
        self.ui.lineEdit_log_file.setText(DEFAULT_LOG_FILE_NAME)
        self.ui.lineEdit_log_file.returnPressed.connect(self.start_logging)
        self.ui.lineEdit_port_command.setText(DEFAULT_PORT_COMMAND)

        # connect signals
        self.ui.pushButton_connect_port.clicked.connect(self.toggle_connection)
        self.ui.comboBox_port.currentIndexChanged.connect(self.update_port_name)
        self.ui.comboBox_baudrate.currentIndexChanged.connect(self.update_baudrate)
        self.ui.pushButton_send_command.clicked.connect(self.send_command)
        self.ui.pushButton_record.clicked.connect(self.start_logging)
        self.ui.pushButton_cancel.clicked.connect(self.cancel_logging)

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
            self.serial.setPortName(self.port_name)
            self.serial.setBaudRate(self.baudrate)
            self.serial.setFlowControl(QtSerialPort.QSerialPort.FlowControl.NoFlowControl)
            self.serial.setDataBits(QtSerialPort.QSerialPort.DataBits.Data8)
            self.serial.setParity(QtSerialPort.QSerialPort.Parity.NoParity)
            self.serial.setStopBits(QtSerialPort.QSerialPort.StopBits.OneStop)
            if self.serial.open(QtCore.QIODeviceBase.OpenModeFlag.ReadWrite):
                self.serial.clear()
                self.serial.readyRead.connect(self.read_from_port)
                self.connected = True
                self.ui.pushButton_connect_port.setText("Disconnect")
                self.ui.groupBox_commands.setEnabled(True)
        else:
            self.serial.close()
            self.connected = False
            self.ui.pushButton_connect_port.setText("Connect to port")
            self.ui.groupBox_commands.setEnabled(False)

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
            self.log_dir = new_dir
            self.ui.lineEdit_log_dir.setText(new_dir)

    def read_from_port(self):
        """
        Read a line from the serial port whenever data arrives.
        Decode from ASCII, update the display, and log to file if active.
        """
        if not self.serial.canReadLine():
            return

        self.lock.lock()
        raw = self.serial.readLine()
        # PyQt6 returns a QByteArray; at runtime bytes(raw) works fine.
        raw_bytes = bytes(raw)            # type: ignore[arg-type]
        self.port_response = raw_bytes.decode('ascii')

        if self.logging_on:
            assert self.log_file is not None

            # strip leading '>' if present, then write
            self.log_file.write(self.port_response.lstrip(">"))
            self.sample_idx += 1
            cast(Any, self.ui).progressBar_logging.setValue(self.sample_idx)

            if self.sample_idx >= self.sample_count:
                self.log_file.close()
                self.logging_on = False
                self.ui.pushButton_record.setEnabled(True)
                self.ui.pushButton_cancel.setEnabled(False)

        self.lock.unlock()

    def update_response_label(self):
        """Refresh the on-screen label with the latest port_response."""
        self.ui.label_port_response.setText(self.port_response)

    def send_command(self):
        """Send the text from the command line edit down the serial port."""
        cmd = self.ui.lineEdit_port_command.text() + "\n"
        self.serial.write(cmd.encode('ascii'))

    def start_logging(self):
        """
        Prompt the user for a log-file location, open the file,
        and begin writing incoming samples to it.
        """
        file_base = self.ui.lineEdit_log_file.text()
        initial   = os.path.join(self.log_dir, f"{file_base}.txt")
        path, _   = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select log file",
            initial,
            "Text files (*.txt)"
        )
        if not path:
            return

        if not path.endswith(".txt"):
            path += ".txt"

        self.log_dir = os.path.dirname(path)
        self.ui.lineEdit_log_dir.setText(self.log_dir)
        file_base = os.path.splitext(os.path.basename(path))[0]
        self.ui.lineEdit_log_file.setText(file_base)
        full_path = path
        try:
            self.log_file = open(full_path, "w")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to open {full_path}: {exc}")
            return

        self.sample_count = self.ui.spinBox_log_sample_count.value()
        self.sample_idx   = 0
        self.logging_on   = True

        self.ui.pushButton_record.setEnabled(False)
        self.ui.pushButton_cancel.setEnabled(True)

        cast(Any, self.ui).progressBar_logging.setMaximum(self.sample_count)
        cast(Any, self.ui).progressBar_logging.setValue(0)

    def cancel_logging(self):
        """Abort the current logging session."""
        if not self.logging_on:
            return
        assert self.log_file is not None

        self.log_file.close()
        self.logging_on = False
        self.ui.pushButton_record.setEnabled(True)
        self.ui.pushButton_cancel.setEnabled(False)

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

    window = MainWindow(log_dir)
    window.show()

    WINDOWS.append(window)

    if owns_app:
        sys.exit(app.exec())
    return window

if __name__ == "__main__":
    main()
