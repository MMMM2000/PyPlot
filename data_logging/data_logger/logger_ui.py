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
        MainWindow.resize(1200, 720)

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
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        # Fix settings width to one third of the available screen width
        try:
            screen = QtGui.QGuiApplication.primaryScreen()
            avail = screen.availableGeometry() if screen is not None else QtCore.QRect(0, 0, 1440, 900)
            fixed_settings_w = max(260, int(avail.width() / 3))
        except Exception:
            fixed_settings_w = 480
        left_panel.setMinimumWidth(fixed_settings_w)
        left_panel.setMaximumWidth(fixed_settings_w)
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(12)
        # Expose for runtime overlay handling and add directly (no scroll)
        self.left_panel = left_panel
        # Fixed-width settings panel
        root.addWidget(left_panel, stretch=0)

        # --- Serial settings -------------------------------------------------
        self.groupBox_serial = QtWidgets.QGroupBox("Serial")
        serial_layout = QtWidgets.QHBoxLayout(self.groupBox_serial)
        serial_layout.setSpacing(6)
        serial_layout.setContentsMargins(8, 8, 8, 8)
        # Single row: Port + Refresh + Baud + Connect
        serial_layout.addWidget(QtWidgets.QLabel("Port:"))
        self.comboBox_port = QtWidgets.QComboBox()
        # Keep the port box compact; it need not expand across the row
        self.comboBox_port.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Fixed)
        self.comboBox_port.setMinimumWidth(110)
        self.comboBox_port.setMaximumWidth(160)
        serial_layout.addWidget(self.comboBox_port, stretch=1)
        self.pushButton_refresh_ports = QtWidgets.QPushButton("Refresh")
        self.pushButton_refresh_ports.setMinimumWidth(64)
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
        self.comboBox_baud.setMinimumWidth(70)
        self.comboBox_baud.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
        serial_layout.addWidget(self.comboBox_baud)
        self.pushButton_connect = QtWidgets.QPushButton("Connect")
        self.pushButton_connect.setMinimumWidth(80)
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
        log_layout.setColumnStretch(3, 0)
        log_layout.setColumnStretch(4, 0)

        log_layout.addWidget(QtWidgets.QLabel("Directory:"), 0, 0)
        self.lineEdit_log_dir = QtWidgets.QLineEdit()
        self.lineEdit_log_dir.setMinimumWidth(120)
        self.lineEdit_log_dir.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        log_layout.addWidget(self.lineEdit_log_dir, 0, 1)
        spacer_dir = QtWidgets.QSpacerItem(40, 20, QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Minimum)
        log_layout.addItem(spacer_dir, 0, 2)
        self.pushButton_browse_dir = QtWidgets.QPushButton("Browse")
        self.pushButton_browse_dir.setMinimumWidth(70)
        self.pushButton_browse_dir.setMaximumWidth(80)
        log_layout.addWidget(self.pushButton_browse_dir, 0, 3)
        self.pushButton_open_dir = QtWidgets.QPushButton("Open")
        self.pushButton_open_dir.setMinimumWidth(60)
        self.pushButton_open_dir.setMaximumWidth(70)
        log_layout.addWidget(self.pushButton_open_dir, 0, 4)

        log_layout.addWidget(QtWidgets.QLabel("File name:"), 1, 0)
        self.lineEdit_log_file = QtWidgets.QLineEdit()
        self.lineEdit_log_file.setMinimumWidth(120)
        self.lineEdit_log_file.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        log_layout.addWidget(self.lineEdit_log_file, 1, 1)
        spacer_ext = QtWidgets.QSpacerItem(40, 20, QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Minimum)
        log_layout.addItem(spacer_ext, 1, 2)
        self.label_extension = QtWidgets.QLabel(".txt")
        self.label_extension.setFixedWidth(36)
        self.label_extension.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        log_layout.addWidget(self.label_extension, 1, 3)
        self.pushButton_build_name = QtWidgets.QPushButton("Build name")
        self.pushButton_build_name.hide()
        # keep out of the way
        log_layout.addWidget(self.pushButton_build_name, 1, 4)

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
        self.label_input_rate = QtWidgets.QLabel("Input: N/A")
        self.label_input_rate.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        log_layout.addWidget(self.label_input_rate, 3, 3)

        self.progressBar_logging = QtWidgets.QProgressBar()
        self.pushButton_cancel = QtWidgets.QPushButton("Cancel")
        self.checkBox_subdir = QtWidgets.QCheckBox("Use subfolder")
        self.checkBox_subdir.setToolTip('Unsupported characters (<>:"/\\|?*) are replaced with underscores.')
        log_layout.addWidget(self.progressBar_logging, 4, 0, 1, 2)
        log_layout.addWidget(self.pushButton_cancel, 4, 2)
        log_layout.addWidget(self.checkBox_subdir, 4, 3)

        # Optional realtime plotting toggle + FPS control
        self.checkBox_rt_plot = QtWidgets.QCheckBox("Realtime plot (experimental)")
        self.checkBox_rt_plot.setChecked(False)
        log_layout.addWidget(self.checkBox_rt_plot, 5, 0, 1, 2)
        self.label_rt_fps = QtWidgets.QLabel("FPS:")
        self.spinBox_rt_fps = QtWidgets.QSpinBox()
        self.spinBox_rt_fps.setRange(1, 120)
        self.spinBox_rt_fps.setValue(30)
        log_layout.addWidget(self.label_rt_fps, 5, 2)
        log_layout.addWidget(self.spinBox_rt_fps, 5, 3)
        self.checkBox_rt_gl = QtWidgets.QCheckBox("OpenGL accel")
        self.checkBox_rt_gl.setChecked(True)
        log_layout.addWidget(self.checkBox_rt_gl, 5, 4)

        self.file_name_builder = FileNameBuilderWidget(self.groupBox_log, self.lineEdit_log_file)
        log_layout.addWidget(self.file_name_builder, 6, 0, 1, 4)

        left_layout.addWidget(self.groupBox_log)

        # Right plot container -----------------------------------------------
        self.plot_container = QtWidgets.QFrame(self.centralWidget)
        self.plot_container.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.plot_container.setMinimumWidth(560)
        self.plot_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        root.addWidget(self.plot_container, stretch=1)

        # Prevent window from being resized too small to fit controls
        try:
            MainWindow.setMinimumSize(fixed_settings_w + 700, 640)
        except Exception:
            MainWindow.setMinimumSize(1100, 640)

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
