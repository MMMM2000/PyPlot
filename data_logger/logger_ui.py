"""Default modern UI for the data logger.

This layout uses vertical stacking with spacious margins and a solid
background for a contemporary look. It mirrors the controls of the original
interface while avoiding fixed geometries so widgets cannot overlap.
"""

from PyQt6 import QtCore, QtGui, QtWidgets


class Ui_MainWindow(object):
    """Simple vertical layout used as the default UI."""

    def setupUi(self, MainWindow: QtWidgets.QMainWindow) -> None:
        MainWindow.setObjectName("MainWindowModernA")
        MainWindow.resize(720, 540)

        font = QtGui.QFont()
        font.setPointSize(10)
        MainWindow.setFont(font)

        self.centralWidget = QtWidgets.QWidget(MainWindow)
        MainWindow.setCentralWidget(self.centralWidget)

        main_layout = QtWidgets.QVBoxLayout(self.centralWidget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # --- Serial settings -------------------------------------------------
        self.groupBox_serial = QtWidgets.QGroupBox("Serial")
        serial_layout = QtWidgets.QHBoxLayout(self.groupBox_serial)
        serial_layout.addWidget(QtWidgets.QLabel("Port:"))
        self.comboBox_port = QtWidgets.QComboBox()
        serial_layout.addWidget(self.comboBox_port)
        self.pushButton_refresh_ports = QtWidgets.QPushButton("Refresh")
        serial_layout.addWidget(self.pushButton_refresh_ports)
        serial_layout.addWidget(QtWidgets.QLabel("Baud:"))
        self.comboBox_baud = QtWidgets.QComboBox()
        self.comboBox_baud.addItems([
            "921600",
            "460800",
            "115200",
            "57600",
            "19200",
            "9600",
        ])
        serial_layout.addWidget(self.comboBox_baud)
        self.pushButton_connect = QtWidgets.QPushButton("Connect")
        serial_layout.addWidget(self.pushButton_connect)
        main_layout.addWidget(self.groupBox_serial)

        # --- Command controls -----------------------------------------------
        self.groupBox_cmd = QtWidgets.QGroupBox("Commands")
        cmd_layout = QtWidgets.QVBoxLayout(self.groupBox_cmd)
        cmd_row = QtWidgets.QHBoxLayout()
        self.lineEdit_port_command = QtWidgets.QLineEdit()
        cmd_row.addWidget(self.lineEdit_port_command)
        self.pushButton_send_command = QtWidgets.QPushButton("Send")
        cmd_row.addWidget(self.pushButton_send_command)
        cmd_layout.addLayout(cmd_row)
        self.label_port_response = QtWidgets.QLabel("Port response")
        self.label_port_response.setWordWrap(True)
        cmd_layout.addWidget(self.label_port_response)
        main_layout.addWidget(self.groupBox_cmd)

        # --- Logging controls ------------------------------------------------
        from .data_logger import FileNameBuilderWidget
        self.groupBox_log = QtWidgets.QGroupBox("Logging")
        log_layout = QtWidgets.QGridLayout(self.groupBox_log)

        log_layout.addWidget(QtWidgets.QLabel("Directory:"), 0, 0)
        self.lineEdit_log_dir = QtWidgets.QLineEdit()
        log_layout.addWidget(self.lineEdit_log_dir, 0, 1)
        self.pushButton_browse_dir = QtWidgets.QPushButton("Browse")
        log_layout.addWidget(self.pushButton_browse_dir, 0, 2)

        log_layout.addWidget(QtWidgets.QLabel("File name:"), 1, 0)
        self.lineEdit_log_file = QtWidgets.QLineEdit()
        log_layout.addWidget(self.lineEdit_log_file, 1, 1)
        self.label_extension = QtWidgets.QLabel(".txt")
        log_layout.addWidget(self.label_extension, 1, 2)
        self.pushButton_build_name = QtWidgets.QPushButton("Build name")
        self.pushButton_build_name.hide()
        log_layout.addWidget(self.pushButton_build_name, 1, 3)

        self.pushButton_record = QtWidgets.QPushButton("Record")
        self.spinBox_log_sample_count = QtWidgets.QSpinBox()
        self.spinBox_log_sample_count.setRange(1, 1_000_000)
        self.spinBox_log_sample_count.setValue(2000)
        self.label_samples = QtWidgets.QLabel("samples")
        log_layout.addWidget(self.pushButton_record, 2, 0)
        log_layout.addWidget(self.spinBox_log_sample_count, 2, 1)
        log_layout.addWidget(self.label_samples, 2, 2)

        self.label_time_estimate = QtWidgets.QLabel("Est. time: N/A")
        log_layout.addWidget(self.label_time_estimate, 3, 0, 1, 3)

        self.progressBar_logging = QtWidgets.QProgressBar()
        self.pushButton_cancel = QtWidgets.QPushButton("Cancel")
        self.checkBox_subdir = QtWidgets.QCheckBox("Use subfolder")
        log_layout.addWidget(self.progressBar_logging, 4, 0, 1, 2)
        log_layout.addWidget(self.pushButton_cancel, 4, 2)
        log_layout.addWidget(self.checkBox_subdir, 4, 3)

        self.file_name_builder = FileNameBuilderWidget(self.groupBox_log, self.lineEdit_log_file)
        log_layout.addWidget(self.file_name_builder, 5, 0, 1, 4)

        main_layout.addWidget(self.groupBox_log)

        # --- Status bar ------------------------------------------------------
        self.statusbar = QtWidgets.QStatusBar(MainWindow)
        self.label_connection_indicator = QtWidgets.QLabel("● Disconnected")
        self.label_connection_indicator.setStyleSheet("color: red;")
        self.statusbar.addPermanentWidget(self.label_connection_indicator)
        self.pushButton_switch_ui = QtWidgets.QPushButton("Switch UI")
        self.statusbar.addPermanentWidget(self.pushButton_switch_ui)
        MainWindow.setStatusBar(self.statusbar)

        # Compatibility aliases for existing logic
        self.groupBox_commands = self.groupBox_cmd
        self.comboBox_baudrate = self.comboBox_baud
        self.pushButton_connect_port = self.pushButton_connect

        QtCore.QMetaObject.connectSlotsByName(MainWindow)

