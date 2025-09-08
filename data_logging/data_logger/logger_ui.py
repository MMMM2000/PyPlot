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
        # Settings pane width: match Current Annealing Logger feel
        # Use ~40% of screen with sensible bounds for consistency across devices
        try:
            screen = QtGui.QGuiApplication.primaryScreen()
            avail = screen.availableGeometry() if screen is not None else QtCore.QRect(0, 0, 1440, 900)
            fixed_settings_w = min(640, max(520, int(avail.width() * 0.40)))
        except Exception:
            fixed_settings_w = 560
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
        self.lineEdit_port_command.setMinimumWidth(240)
        cmd_row.addWidget(self.lineEdit_port_command, stretch=1)
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

        log_layout.addWidget(QtWidgets.QLabel("Directory:"), 0, 0)
        self.lineEdit_log_dir = QtWidgets.QLineEdit()
        self.lineEdit_log_dir.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        log_layout.addWidget(self.lineEdit_log_dir, 0, 1)
        self.pushButton_browse_dir = QtWidgets.QPushButton("Browse")
        self.pushButton_browse_dir.setFixedWidth(80)
        log_layout.addWidget(self.pushButton_browse_dir, 0, 2)
        self.pushButton_open_dir = QtWidgets.QPushButton("Open")
        self.pushButton_open_dir.setFixedWidth(70)
        log_layout.addWidget(self.pushButton_open_dir, 0, 3)

        log_layout.addWidget(QtWidgets.QLabel("File name:"), 1, 0)
        self.lineEdit_log_file = QtWidgets.QLineEdit()
        self.lineEdit_log_file.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        log_layout.addWidget(self.lineEdit_log_file, 1, 1)
        self.label_extension = QtWidgets.QLabel(".txt")
        self.label_extension.setFixedWidth(36)
        self.label_extension.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        log_layout.addWidget(self.label_extension, 1, 2)
        self.pushButton_build_name = QtWidgets.QPushButton("Build name")
        self.pushButton_build_name.hide()
        # keep out of the way
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

        # Optional realtime plotting controls
        rt_row = QtWidgets.QHBoxLayout()
        rt_row.setSpacing(6)
        self.checkBox_rt_plot = QtWidgets.QCheckBox("Realtime plot (experimental)")
        self.checkBox_rt_plot.setChecked(False)
        rt_row.addWidget(self.checkBox_rt_plot)
        self.label_rt_fps = QtWidgets.QLabel("FPS:")
        rt_row.addWidget(self.label_rt_fps)
        self.spinBox_rt_fps = QtWidgets.QSpinBox()
        self.spinBox_rt_fps.setRange(1, 60)
        self.spinBox_rt_fps.setValue(30)
        self.spinBox_rt_fps.setMaximumWidth(60)
        rt_row.addWidget(self.spinBox_rt_fps)
        self.label_rt_window = QtWidgets.QLabel("Window:")
        rt_row.addWidget(self.label_rt_window)
        self.spinBox_rt_window = QtWidgets.QSpinBox()
        self.spinBox_rt_window.setRange(1, 1_000_000)
        self.spinBox_rt_window.setSingleStep(500)
        self.spinBox_rt_window.setValue(2000)
        self.spinBox_rt_window.setMaximumWidth(80)
        rt_row.addWidget(self.spinBox_rt_window)
        self.checkBox_rt_gl = QtWidgets.QCheckBox("OpenGL accel")
        self.checkBox_rt_gl.setChecked(True)
        rt_row.addWidget(self.checkBox_rt_gl)
        rt_row.addStretch(1)
        log_layout.addLayout(rt_row, 5, 0, 1, 4)

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
