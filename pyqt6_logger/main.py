import sys
import os
from PyQt6 import QtCore, QtWidgets, QtSerialPort
from PyQt6.QtSerialPort import QSerialPortInfo
from mainwindow_GUI import Ui_MainWindow

# =============================================================================
#                            USER CONFIGURATION
#
# 1) LOG_DIR: default directory where logged data will be stored. Modify this
#    path to your preferred location. The value can still be overridden via
#    the --log-dir command line option or the LOG_DIR environment variable.
LOG_DIR = (
    "G:/Shared drives/Projekty/VAIA/WP1 - MicroWire Development/"
    "stress depencence/data"
)

# 2) DEFAULT_PORT_COMMAND: command pre-filled in the command box when the GUI
#    starts. Adjust to match the most common command for your logger.
DEFAULT_PORT_COMMAND = ">2050;1270;1;"

# 3) DEFAULT_LOG_FILE_NAME: suggested file name for new recordings. This value
#    only affects the default text shown in the GUI.
DEFAULT_LOG_FILE_NAME = "FeSiBP 156_2 s2-1a 74mA 2,5a.txt"
# =============================================================================

DEFAULT_LOG_DIR = os.getenv("LOG_DIR", LOG_DIR)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, log_dir=DEFAULT_LOG_DIR):
        super().__init__()
        self.log_dir = log_dir
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

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
        self.log_file     = None   # will become an open file in start_logging()
        self.sample_count = 2000
        self.sample_idx   = 0
        self.logging_on   = False
        self.ui.progressBar_logging.setMaximum(self.sample_count)

        os.makedirs(self.log_dir, exist_ok=True)

        # fill port list and set defaults
        self.populate_ports()
        self.ui.comboBox_baudrate.setCurrentIndex(0)  # highest bitrate
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())

        self.ui.lineEdit_log_file.setText(DEFAULT_LOG_FILE_NAME)
        self.ui.lineEdit_port_command.setText(DEFAULT_PORT_COMMAND)

        # connect signals
        self.ui.pushButton_connect_port.clicked.connect(self.toggle_connection)
        self.ui.comboBox_port.currentIndexChanged.connect(self.update_port_name)
        self.ui.comboBox_baudrate.currentIndexChanged.connect(self.update_baudrate)
        self.ui.pushButton_send_command.clicked.connect(self.send_command)
        self.ui.pushButton_record.clicked.connect(self.start_logging)
        self.ui.pushButton_cancel.clicked.connect(self.cancel_logging)

        self.ui.progressBar_logging.setValue(0)
        self.ui.pushButton_cancel.setEnabled(False)

    def populate_ports(self):
        """Scan available serial ports and populate the combo box."""
        self.ui.comboBox_port.clear()
        for info in QSerialPortInfo.availablePorts():
            self.ui.comboBox_port.addItem(info.portName())
        if self.ui.comboBox_port.count() > 0:
            self.port_name = self.ui.comboBox_port.currentText()

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
        self.port_name = self.ui.comboBox_port.currentText()

    def update_baudrate(self):
        """Keep self.baudrate in sync with the combo box selection."""
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())

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
            # assert so Pylance knows log_file is not None here
            assert self.log_file is not None

            # strip leading '>' if present, then write
            self.log_file.write(self.port_response.lstrip(">"))
            self.sample_idx += 1
            self.ui.progressBar_logging.setValue(self.sample_idx)

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
        file_name = self.ui.lineEdit_log_file.text()
        initial   = os.path.join(self.log_dir, file_name)
        path, _   = QtWidgets.QFileDialog.getSaveFileName(self, "Select log file", initial)
        if not path:
            return

        file_name = os.path.basename(path)
        self.ui.lineEdit_log_file.setText(file_name)
        full_path = os.path.join(self.log_dir, file_name)
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
        self.ui.progressBar_logging.setMaximum(self.sample_count)
        self.ui.progressBar_logging.setValue(0)

    def cancel_logging(self):
        """Abort the current logging session."""
        if not self.logging_on:
            return

        assert self.log_file is not None
        self.log_file.close()
        self.logging_on = False
        self.ui.pushButton_record.setEnabled(True)
        self.ui.pushButton_cancel.setEnabled(False)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Serial data logger (PyQt6)")
    parser.add_argument(
        "--log-dir",
        help="Directory to save logs [env: LOG_DIR]",
    )
    args = parser.parse_args()

    log_dir = args.log_dir or DEFAULT_LOG_DIR

    app    = QtWidgets.QApplication(sys.argv)
    window = MainWindow(log_dir)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()