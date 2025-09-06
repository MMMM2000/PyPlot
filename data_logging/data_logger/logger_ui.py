"""Modern UI layout for the data logger.

This layout uses vertical stacking with spacious margins and a solid
background for a contemporary look. It mirrors the controls of the original
interface while avoiding fixed geometries so widgets cannot overlap.
"""

from PyQt6 import QtCore, QtGui, QtWidgets

from .file_name_builder import FileNameBuilderWidget


class UiMainWindow(object):
    """Two-panel layout: settings left, live plot right."""

    def setupUi(self, MainWindow: QtWidgets.QMainWindow) -> None:
        MainWindow.setObjectName("MainWindowModernA")
        MainWindow.resize(1000, 700)

        font = QtGui.QFont()
        font.setPointSize(10)
        MainWindow.setFont(font)

        self.centralWidget = QtWidgets.QWidget(MainWindow)
        MainWindow.setCentralWidget(self.centralWidget)

        root = QtWidgets.QHBoxLayout(self.centralWidget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Left side scrollable settings panel
        left_panel = QtWidgets.QWidget(self.centralWidget)
        left_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(12)
        left_scroll = QtWidgets.QScrollArea(self.centralWidget)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        left_scroll.setWidget(left_panel)
        # Expose for runtime overlay handling
        self.left_scroll = left_scroll
        self.left_panel = left_panel
        root.addWidget(left_scroll, stretch=0)

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
        left_layout.addWidget(self.groupBox_serial)

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
        left_layout.addWidget(self.groupBox_cmd)

        # --- Logging controls ------------------------------------------------
        self.groupBox_log = QtWidgets.QGroupBox("Logging")
        log_layout = QtWidgets.QGridLayout(self.groupBox_log)
        log_layout.setColumnStretch(0, 0)
        log_layout.setColumnStretch(1, 1)
        log_layout.setColumnStretch(2, 0)

        log_layout.addWidget(QtWidgets.QLabel("Directory:"), 0, 0)
        self.lineEdit_log_dir = QtWidgets.QLineEdit()
        self.lineEdit_log_dir.setMinimumWidth(280)
        self.lineEdit_log_dir.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        log_layout.addWidget(self.lineEdit_log_dir, 0, 1)
        self.pushButton_browse_dir = QtWidgets.QPushButton("Browse")
        self.pushButton_browse_dir.setMaximumWidth(90)
        log_layout.addWidget(self.pushButton_browse_dir, 0, 2)

        log_layout.addWidget(QtWidgets.QLabel("File name:"), 1, 0)
        self.lineEdit_log_file = QtWidgets.QLineEdit()
        self.lineEdit_log_file.setMinimumWidth(280)
        self.lineEdit_log_file.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
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

        self.label_time_estimate = QtWidgets.QLabel("Time remaining: N/A")
        log_layout.addWidget(self.label_time_estimate, 3, 0, 1, 3)

        self.progressBar_logging = QtWidgets.QProgressBar()
        self.pushButton_cancel = QtWidgets.QPushButton("Cancel")
        self.checkBox_subdir = QtWidgets.QCheckBox("Use subfolder")
        self.checkBox_subdir.setToolTip('Unsupported characters (<>:"/\\|?*) are replaced with underscores.')
        log_layout.addWidget(self.progressBar_logging, 4, 0, 1, 2)
        log_layout.addWidget(self.pushButton_cancel, 4, 2)
        log_layout.addWidget(self.checkBox_subdir, 4, 3)

        # Optional realtime plotting toggle
        self.checkBox_rt_plot = QtWidgets.QCheckBox("Realtime plot (experimental)")
        self.checkBox_rt_plot.setChecked(False)
        log_layout.addWidget(self.checkBox_rt_plot, 5, 0, 1, 2)

        self.file_name_builder = FileNameBuilderWidget(self.groupBox_log, self.lineEdit_log_file)
        log_layout.addWidget(self.file_name_builder, 6, 0, 1, 4)

        left_layout.addWidget(self.groupBox_log)

        # Right plot container -----------------------------------------------
        self.plot_container = QtWidgets.QFrame(self.centralWidget)
        self.plot_container.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.plot_container.setMinimumWidth(520)
        self.plot_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        root.addWidget(self.plot_container, stretch=1)

        # --- Status bar ------------------------------------------------------
        self.statusbar = QtWidgets.QStatusBar(MainWindow)
        self.label_connection_indicator = QtWidgets.QLabel("\u25cf Disconnected")
        self.label_connection_indicator.setStyleSheet("color: red;")
        self.statusbar.addPermanentWidget(self.label_connection_indicator)
        MainWindow.setStatusBar(self.statusbar)

        # Compatibility aliases for existing logic
        self.groupBox_commands = self.groupBox_cmd
        self.comboBox_baudrate = self.comboBox_baud
        self.pushButton_connect_port = self.pushButton_connect

        QtCore.QMetaObject.connectSlotsByName(MainWindow)
