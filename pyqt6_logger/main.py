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

        self.port_response = ""
        self.connected = False
        self.port_name = ""
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())
        self.ui.groupBox_commands.setEnabled(False)
        self.serial = QtSerialPort.QSerialPort()
        self.lock = QtCore.QMutex()
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_response_label)
        self.timer.start(10)

        self.log_file = None
        self.sample_count = 2000
        self.sample_idx = 0
        self.logging_on = False

        os.makedirs(self.log_dir, exist_ok=True)

        self.populate_ports()
        self.ui.comboBox_baudrate.setCurrentIndex(0)  # highest bitrate
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())

        self.ui.lineEdit_log_file.setText(DEFAULT_LOG_FILE_NAME)
        self.ui.lineEdit_port_command.setText(DEFAULT_PORT_COMMAND)

        self.ui.pushButton_connect_port.clicked.connect(self.toggle_connection)
        self.ui.comboBox_port.currentIndexChanged.connect(self.update_port_name)
        self.ui.comboBox_baudrate.currentIndexChanged.connect(self.update_baudrate)
        self.ui.pushButton_send_command.clicked.connect(self.send_command)
        self.ui.pushButton_record.clicked.connect(self.start_logging)

    def populate_ports(self):
        self.ui.comboBox_port.clear()
        for info in QSerialPortInfo.availablePorts():
            self.ui.comboBox_port.addItem(info.portName())
        if self.ui.comboBox_port.count() > 0:
            self.port_name = self.ui.comboBox_port.currentText()

    def toggle_connection(self):
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
        self.port_name = self.ui.comboBox_port.currentText()

    def update_baudrate(self):
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())

    def read_from_port(self):
        if self.serial.canReadLine():
            self.lock.lock()
            self.port_response = bytes(self.serial.readLine()).decode('ascii')
            if self.logging_on:
                self.log_file.write(self.port_response.strip(">"))
                self.sample_idx += 1
                if self.sample_idx >= self.sample_count:
                    self.log_file.close()
                    self.logging_on = False
                    self.ui.pushButton_record.setEnabled(True)
            self.lock.unlock()

    def update_response_label(self):
        self.ui.label_port_response.setText(self.port_response)

    def send_command(self):
        self.serial.write((self.ui.lineEdit_port_command.text() + "\n").encode('ascii'))

    def start_logging(self):
        file_name = self.ui.lineEdit_log_file.text()
        initial = os.path.join(self.log_dir, file_name)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Select log file", initial)
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
        self.sample_idx = 0
        self.logging_on = True
        self.ui.pushButton_record.setEnabled(False)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Serial data logger (PyQt6)")
    parser.add_argument(
        "--log-dir",
        help="Directory to save logs [env: LOG_DIR]",
    )
    args = parser.parse_args()

    log_dir = args.log_dir or DEFAULT_LOG_DIR

    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(log_dir)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
