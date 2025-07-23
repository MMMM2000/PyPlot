import sys
from PyQt5 import QtCore, QtWidgets, QtSerialPort
from PyQt5.QtSerialPort import QSerialPortInfo


class LoggerWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Serial Logger v2")

        # central widget and layout
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)

        # serial settings group
        settings_group = QtWidgets.QGroupBox("Serial settings", self)
        main_layout.addWidget(settings_group)
        settings_layout = QtWidgets.QGridLayout(settings_group)

        settings_layout.addWidget(QtWidgets.QLabel("Port:"), 0, 0)
        self.port_combo = QtWidgets.QComboBox()
        settings_layout.addWidget(self.port_combo, 0, 1)

        settings_layout.addWidget(QtWidgets.QLabel("Baud:"), 0, 2)
        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems([
            "921600", "460800", "230400", "115200",
            "57600", "38400", "19200", "9600"
        ])
        self.baud_combo.setCurrentIndex(0)
        settings_layout.addWidget(self.baud_combo, 0, 3)

        self.refresh_button = QtWidgets.QPushButton("Refresh")
        settings_layout.addWidget(self.refresh_button, 0, 4)

        self.connect_button = QtWidgets.QPushButton("Connect")
        main_layout.addWidget(self.connect_button)

        # command/response group
        self.cmd_group = QtWidgets.QGroupBox("Commands and responses")
        self.cmd_group.setEnabled(False)
        main_layout.addWidget(self.cmd_group)
        cmd_layout = QtWidgets.QVBoxLayout(self.cmd_group)

        hlayout = QtWidgets.QHBoxLayout()
        self.cmd_edit = QtWidgets.QLineEdit()
        self.send_button = QtWidgets.QPushButton("Send")
        hlayout.addWidget(self.cmd_edit)
        hlayout.addWidget(self.send_button)
        cmd_layout.addLayout(hlayout)

        self.response_label = QtWidgets.QLabel()
        self.response_label.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        cmd_layout.addWidget(self.response_label)

        # logging controls
        log_layout = QtWidgets.QGridLayout()
        cmd_layout.addLayout(log_layout)
        log_layout.addWidget(QtWidgets.QLabel("Log file:"), 0, 0)
        self.log_edit = QtWidgets.QLineEdit("log.txt")
        log_layout.addWidget(self.log_edit, 0, 1)
        self.browse_button = QtWidgets.QPushButton("Browse")
        log_layout.addWidget(self.browse_button, 0, 2)

        log_layout.addWidget(QtWidgets.QLabel("Samples:"), 1, 0)
        self.sample_spin = QtWidgets.QSpinBox()
        self.sample_spin.setMinimum(1)
        self.sample_spin.setMaximum(1_000_000)
        self.sample_spin.setValue(1000)
        log_layout.addWidget(self.sample_spin, 1, 1)
        self.record_button = QtWidgets.QPushButton("Record")
        log_layout.addWidget(self.record_button, 1, 2)

        # timer for UI updates
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_response)
        self.timer.start(10)

        # serial port setup
        self.serial = QtSerialPort.QSerialPort()
        self.serial.readyRead.connect(self.read_data)
        self.serial_lock = QtCore.QMutex()

        # log state
        self.log_file = None
        self.sample_count = 0
        self.sample_index = 0
        self.logging = False

        # connect signals
        self.refresh_button.clicked.connect(self.populate_ports)
        self.connect_button.clicked.connect(self.toggle_connection)
        self.send_button.clicked.connect(self.send_command)
        self.browse_button.clicked.connect(self.browse_log_file)
        self.record_button.clicked.connect(self.start_logging)

        self.populate_ports()

    def populate_ports(self):
        self.port_combo.clear()
        for info in QSerialPortInfo.availablePorts():
            self.port_combo.addItem(info.portName())

    def toggle_connection(self):
        if not self.serial.isOpen():
            if self.port_combo.count() == 0:
                return
            self.serial.setPortName(self.port_combo.currentText())
            self.serial.setBaudRate(int(self.baud_combo.currentText()))
            self.serial.setDataBits(QtSerialPort.QSerialPort.Data8)
            self.serial.setParity(QtSerialPort.QSerialPort.NoParity)
            self.serial.setStopBits(QtSerialPort.QSerialPort.OneStop)
            if self.serial.open(QtCore.QIODevice.ReadWrite):
                self.connect_button.setText("Disconnect")
                self.cmd_group.setEnabled(True)
        else:
            self.serial.close()
            self.connect_button.setText("Connect")
            self.cmd_group.setEnabled(False)

    def send_command(self):
        if self.serial.isOpen():
            data = self.cmd_edit.text().strip() + "\n"
            self.serial.write(data.encode("ascii"))

    def read_data(self):
        self.serial_lock.lock()
        if self.serial.canReadLine():
            line = bytes(self.serial.readLine()).decode("ascii", errors="ignore")
            self.response_label.setText(line)
            if self.logging:
                self.log_file.write(line.strip(">"))
                self.sample_index += 1
                if self.sample_index >= self.sample_count:
                    self.log_file.close()
                    self.logging = False
                    self.record_button.setEnabled(True)
        self.serial_lock.unlock()

    def update_response(self):
        pass  # placeholder for periodic updates if needed

    def browse_log_file(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Select log file", self.log_edit.text())
        if path:
            self.log_edit.setText(path)

    def start_logging(self):
        path = self.log_edit.text()
        if not path:
            return
        try:
            self.log_file = open(path, "w")
        except OSError:
            return
        self.sample_count = self.sample_spin.value()
        self.sample_index = 0
        self.logging = True
        self.record_button.setEnabled(False)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = LoggerWindow()
    window.show()
    sys.exit(app.exec())
