"""English, layout-based UI for the Current Annealing Logger.

This UI mirrors the controls expected by ``current_annealing_logger.py``
but presents them with modern layouts and English labels. Object names
match the English identifiers used throughout the logger logic.
"""

from PyQt6 import QtCore, QtGui, QtWidgets
from typing import Optional
try:
    # Reuse InfoLineEdit from data logger for inline info and validation
    from data_logging.data_logger.file_name_builder import InfoLineEdit
except Exception:
    InfoLineEdit = QtWidgets.QLineEdit  # type: ignore[assignment]


class Ui_MainWindow(object):
    def __init__(self) -> None:
        self.left_scroll: Optional[QtWidgets.QScrollArea] = None
        self.plot_container: Optional[QtWidgets.QFrame] = None
        self.label_live_voltage: Optional[QtWidgets.QLabel] = None
        self.label_set_current: Optional[QtWidgets.QLabel] = None
        self.lcd_current_mA: Optional[QtWidgets.QLCDNumber] = None
        self.lcd_resistance: Optional[QtWidgets.QLCDNumber] = None
        self.toolButton_sample_up: Optional[QtWidgets.QToolButton] = None
        self.toolButton_sample_down: Optional[QtWidgets.QToolButton] = None

    def setupUi(self, MainWindow: QtWidgets.QMainWindow) -> None:
        MainWindow.setObjectName("CurrentAnnealingMainWindow")
        MainWindow.resize(880, 720)

        font = QtGui.QFont()
        font.setPointSize(10)
        MainWindow.setFont(font)

        self.centralWidget = QtWidgets.QWidget(MainWindow)
        MainWindow.setCentralWidget(self.centralWidget)

        # Root layout: settings on the left, plots on the right
        root = QtWidgets.QHBoxLayout(self.centralWidget)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        left_container = QtWidgets.QWidget(self.centralWidget)
        left_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        left_container_layout = QtWidgets.QVBoxLayout(left_container)
        left_container_layout.setContentsMargins(0, 0, 0, 0)
        left_container_layout.setSpacing(8)

        left_panel = QtWidgets.QWidget(left_container)
        # Allow the left column to shrink to viewport width without forcing
        # a horizontal scrollbar in the scroll area.
        left_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        main_layout = QtWidgets.QVBoxLayout(left_panel)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(12)
        left_scroll = QtWidgets.QScrollArea(left_container)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        # Avoid horizontal scrollbar; let content wrap/stack vertically
        left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents
        )
        left_scroll.setWidget(left_panel)
        # Expose scroll for overlays from logic
        self.left_scroll = left_scroll
        left_container_layout.addWidget(left_scroll, stretch=1)

        sticky_buttons_frame = QtWidgets.QFrame(left_container)
        sticky_buttons_frame.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        sticky_buttons_frame.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        sticky_buttons_layout = QtWidgets.QHBoxLayout(sticky_buttons_frame)
        sticky_buttons_layout.setContentsMargins(8, 0, 8, 0)
        sticky_buttons_layout.setSpacing(8)
        left_container_layout.addWidget(sticky_buttons_frame)

        root.addWidget(left_container, stretch=0)

        # Right plot container
        self.plot_container = QtWidgets.QFrame(self.centralWidget)
        # Remove frame to avoid bright border lines in dark themes
        self.plot_container.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.plot_container.setMinimumWidth(480)
        self.plot_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        root.addWidget(self.plot_container, stretch=1)

        # ------------------------------------------------------------------
        # Serial basics (frame_serial_settings)
        # ------------------------------------------------------------------
        self.frame_serial_settings = QtWidgets.QFrame(self.centralWidget)
        self.frame_serial_settings.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.frame_serial_settings.setFrameShadow(QtWidgets.QFrame.Shadow.Raised)
        main_layout.addWidget(self.frame_serial_settings)

        gb_serial = QtWidgets.QGroupBox("Serial settings", self.frame_serial_settings)
        gb_layout = QtWidgets.QHBoxLayout(gb_serial)
        self.groupBox_serial_settings = gb_serial

        # Port selection (modernized): list available ports with names
        self.label_port = QtWidgets.QLabel("Port:")
        gb_layout.addWidget(self.label_port)
        self.comboBox_port = QtWidgets.QComboBox()
        gb_layout.addWidget(self.comboBox_port)
        self.pushButton_refresh_ports = QtWidgets.QPushButton("Refresh")
        gb_layout.addWidget(self.pushButton_refresh_ports)

        # Legacy numeric COM spin kept for compatibility, but hidden
        self.label_port_number = QtWidgets.QLabel("COM:")
        self.label_port_number.hide()
        self.spinBox_port_number = QtWidgets.QSpinBox()
        self.spinBox_port_number.setRange(1, 127)
        self.spinBox_port_number.setValue(3)
        self.spinBox_port_number.hide()

        # Baudrate combo
        self.label_baudrate = QtWidgets.QLabel("Baud:")
        gb_layout.addWidget(self.label_baudrate)
        self.comboBox_baudrate = QtWidgets.QComboBox()
        self.comboBox_baudrate.addItems([
            "921600",
            "460800",
            "115200",
            "57600",
            "19200",
            "9600",
        ])
        self.comboBox_baudrate.setCurrentText("115200")
        gb_layout.addWidget(self.comboBox_baudrate)

        gb_layout.addStretch(1)

        self.pushButton_connect_port = QtWidgets.QPushButton("Connect to port")
        gb_layout.addWidget(self.pushButton_connect_port)

        # Fit the group box into the frame
        frame_layout_serial = QtWidgets.QVBoxLayout(self.frame_serial_settings)
        frame_layout_serial.setContentsMargins(0, 0, 0, 0)
        frame_layout_serial.addWidget(gb_serial)

        # ------------------------------------------------------------------
        # Mode of operation (simple combo box)
        # ------------------------------------------------------------------
        self.frame_operation_mode = QtWidgets.QFrame(self.centralWidget)
        self.frame_operation_mode.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        main_layout.addWidget(self.frame_operation_mode)

        frame_layout_mode = QtWidgets.QHBoxLayout(self.frame_operation_mode)
        frame_layout_mode.setContentsMargins(0, 0, 0, 0)
        self.label_mode = QtWidgets.QLabel("Mode of operation:")
        frame_layout_mode.addWidget(self.label_mode)
        self.comboBox_mode = QtWidgets.QComboBox()
        self.comboBox_mode.addItems([
            "Raw VCP",
            "Manual annealing",
            "Automatic annealing",
        ])
        self.comboBox_mode.setCurrentIndex(2)
        frame_layout_mode.addWidget(self.comboBox_mode)
        frame_layout_mode.addStretch(1)

        # ------------------------------------------------------------------
        # Process settings (frame_process_settings)
        # ------------------------------------------------------------------
        self.frame_process_settings = QtWidgets.QFrame(self.centralWidget)
        self.frame_process_settings.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.frame_process_settings.setFrameShadow(QtWidgets.QFrame.Shadow.Raised)
        main_layout.addWidget(self.frame_process_settings)

        gb_proc = QtWidgets.QGroupBox("Process settings", self.frame_process_settings)
        self.groupBox_process_settings = gb_proc
        grid = QtWidgets.QGridLayout(gb_proc)
        # Make the main text fields expand and keep the buttons narrow
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        # Log file location (separate directory and file name)
        self.label_log_dir = QtWidgets.QLabel("Directory:")
        self.lineEdit_log_dir = QtWidgets.QLineEdit()
        self.lineEdit_log_dir.setMinimumWidth(360)
        self.lineEdit_log_dir.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.pushButton_open_dir = QtWidgets.QPushButton("Open")
        self.pushButton_browse_dir = QtWidgets.QPushButton("Browse")
        dir_btns = QtWidgets.QHBoxLayout()
        dir_btns.setContentsMargins(0, 0, 0, 0)
        dir_btns.setSpacing(4)
        dir_btns.addWidget(self.pushButton_open_dir)
        dir_btns.addWidget(self.pushButton_browse_dir)
        grid.addWidget(self.label_log_dir, 0, 0)
        grid.addWidget(self.lineEdit_log_dir, 0, 1)
        grid.addLayout(dir_btns, 0, 2)

        self.label_log_file = QtWidgets.QLabel("File name:")
        self.lineEdit_log_file = QtWidgets.QLineEdit()
        self.lineEdit_log_file.setMinimumWidth(300)
        self.lineEdit_log_file.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.label_extension = QtWidgets.QLabel(".txt")
        grid.addWidget(self.label_log_file, 1, 0)
        grid.addWidget(self.lineEdit_log_file, 1, 1)
        grid.addWidget(self.label_extension, 1, 2)

        # Legacy single-path widgets kept (hidden) for compatibility with code
        self.label_log_file_legacy = QtWidgets.QLabel("Log file:")
        self.label_log_file_legacy.hide()
        self.lineEdit_log_file_full = QtWidgets.QLineEdit()
        self.lineEdit_log_file_full.setPlaceholderText("data/sample.txt")
        self.lineEdit_log_file_full.hide()
        self.pushButton_select_filename = QtWidgets.QPushButton("...")
        self.pushButton_select_filename.hide()

        # Hold current [mA]
        self.label_max_current = QtWidgets.QLabel("Max current [mA]:")
        self.spinBox_max_current = QtWidgets.QSpinBox()
        self.spinBox_max_current.setRange(1, 10_000)
        self.spinBox_max_current.setValue(10)
        self.spinBox_max_current.setMaximumWidth(80)
        # Move one row down to avoid overlap with File name
        grid.addWidget(self.label_max_current, 2, 0)
        grid.addWidget(self.spinBox_max_current, 2, 1)

        # Hold time [s]
        self.label_hold_duration = QtWidgets.QLabel("Hold time [s]:")
        self.spinBox_hold_duration = QtWidgets.QSpinBox()
        self.spinBox_hold_duration.setRange(1, 36000)
        # Default hold time 1 second
        self.spinBox_hold_duration.setValue(1)
        self.spinBox_hold_duration.setMaximumWidth(80)
        # Shift down by one row
        grid.addWidget(self.label_hold_duration, 3, 0)
        grid.addWidget(self.spinBox_hold_duration, 3, 1)

        # Hold/Stop button and elapsed time
        # Hold button + Step control in one row to save space
        hold_and_step = QtWidgets.QHBoxLayout()
        self.pushButton_hold_current = QtWidgets.QPushButton("Hold current now!")
        self.pushButton_hold_current.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.pushButton_hold_current.setMinimumWidth(200)
        hold_and_step.addWidget(self.pushButton_hold_current, stretch=1)
        hold_and_step.addSpacing(12)
        self.label_step = QtWidgets.QLabel("Step [mA]:")
        self.spinBox_step_mA = QtWidgets.QSpinBox()
        self.spinBox_step_mA.setRange(1, 10000)
        self.spinBox_step_mA.setValue(1)
        self.spinBox_step_mA.setMaximumWidth(80)
        hold_and_step.addWidget(self.label_step)
        hold_and_step.addWidget(self.spinBox_step_mA)
        grid.addLayout(hold_and_step, 2, 2)

        self.label_elapsed = QtWidgets.QLabel("Elapsed:")
        self.lcd_elapsed_seconds = QtWidgets.QLCDNumber()
        self.lcd_elapsed_seconds.setSegmentStyle(QtWidgets.QLCDNumber.SegmentStyle.Filled)
        self.label_seconds_unit = QtWidgets.QLabel("s")
        h = QtWidgets.QHBoxLayout()
        h.addWidget(self.label_elapsed)
        h.addWidget(self.lcd_elapsed_seconds)
        h.addWidget(self.label_seconds_unit)
        h.addStretch(1)
        # Align with Hold time row
        grid.addLayout(h, 3, 2)

        # Reverse sweep and loops controls
        rev = QtWidgets.QHBoxLayout()
        self.checkBox_reverse = QtWidgets.QCheckBox("Reverse to zero after max")
        self.checkBox_reverse.setChecked(True)
        self.spinBox_loops = QtWidgets.QSpinBox()
        self.spinBox_loops.setRange(0, 100000)
        self.spinBox_loops.setSpecialValueText("∞")
        self.spinBox_loops.setValue(1)
        self.checkBox_infinite_loops = QtWidgets.QCheckBox("∞")
        self.checkBox_infinite_loops.setToolTip("Repeat indefinitely")
        rev.addWidget(self.checkBox_reverse)
        rev.addSpacing(12)
        rev.addWidget(QtWidgets.QLabel("Loops:"))
        rev.addWidget(self.spinBox_loops)
        rev.addWidget(self.checkBox_infinite_loops)
        rev.addStretch(1)
        grid.addLayout(rev, 4, 0, 1, 3)

        # Voltage limit behaviour
        limit_layout = QtWidgets.QHBoxLayout()
        limit_layout.addWidget(QtWidgets.QLabel("When the 30 V limit is hit:"))
        self.comboBox_max_voltage_action = QtWidgets.QComboBox()
        self.comboBox_max_voltage_action.addItem("Ask every time", "ask")
        self.comboBox_max_voltage_action.addItem("Hold current (stop increasing)", "hold")
        self.comboBox_max_voltage_action.addItem("Reverse to zero", "reverse")
        self.comboBox_max_voltage_action.addItem("Stop measurement", "stop")
        self.comboBox_max_voltage_action.setToolTip(
            "Choose how the logger reacts when the power supply reaches its 30 V compliance limit"
        )
        limit_layout.addWidget(self.comboBox_max_voltage_action)
        limit_layout.addStretch(1)
        grid.addLayout(limit_layout, 5, 0, 1, 3)

        # Name builder (file name preset)
        gb_name = QtWidgets.QGroupBox("File name preset")
        name_grid = QtWidgets.QGridLayout(gb_name)
        self.comboBox_name_preset = QtWidgets.QComboBox()
        self.comboBox_name_preset.addItems(["Current annealing", "Custom"])
        name_grid.addWidget(QtWidgets.QLabel("Preset:"), 0, 0)
        name_grid.addWidget(self.comboBox_name_preset, 0, 1)
        # Fields for the "Current annealing" preset
        self.lineEdit_composition = InfoLineEdit("Chemical composition, e.g., Ni51Fe26Ga21")
        try:
            self.lineEdit_composition.set_validation(r"^[A-Za-z0-9]+$", "Use only letters and numbers")  # type: ignore[attr-defined]
        except Exception:
            pass
        self.lineEdit_composition.setText("Ni51Fe26Ga21")
        self.lineEdit_composition.setMinimumWidth(300)
        self.lineEdit_microwire = InfoLineEdit("Microwire identifier, e.g., 1_2")
        try:
            self.lineEdit_microwire.set_validation(r"^[A-Za-z0-9_]+$", "Use only letters, numbers, or '_' ")  # type: ignore[attr-defined]
        except Exception:
            pass
        self.lineEdit_microwire.setText("1_2")
        self.lineEdit_microwire.setMinimumWidth(300)
        self.lineEdit_sample = InfoLineEdit("Sample, e.g., s1 or s2-1")
        try:
            self.lineEdit_sample.set_validation(r"^s\d+(?:-\d+)?$", "Use pattern like s1 or s2-1")  # type: ignore[attr-defined]
        except Exception:
            pass
        self.lineEdit_sample.setText("s1")
        self.lineEdit_sample.setMinimumWidth(300)
        self.label_composition = QtWidgets.QLabel("Composition:")
        name_grid.addWidget(self.label_composition, 1, 0)
        name_grid.addWidget(self.lineEdit_composition, 1, 1)
        self.label_microwire = QtWidgets.QLabel("Microwire:")
        name_grid.addWidget(self.label_microwire, 2, 0)
        name_grid.addWidget(self.lineEdit_microwire, 2, 1)
        self.label_sample = QtWidgets.QLabel("Sample:")
        name_grid.addWidget(self.label_sample, 3, 0)
        self.sample_row_widget = QtWidgets.QWidget()
        sample_row = QtWidgets.QHBoxLayout(self.sample_row_widget)
        sample_row.setContentsMargins(0, 0, 0, 0)
        sample_row.setSpacing(4)
        sample_row.addWidget(self.lineEdit_sample)
        sample_row.setStretch(0, 1)
        arrow_layout = QtWidgets.QVBoxLayout()
        arrow_layout.setContentsMargins(0, 0, 0, 0)
        arrow_layout.setSpacing(2)
        self.toolButton_sample_up = QtWidgets.QToolButton()
        self.toolButton_sample_up.setArrowType(QtCore.Qt.ArrowType.UpArrow)
        self.toolButton_sample_up.setFixedWidth(22)
        self.toolButton_sample_up.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.toolButton_sample_up.setToolTip("Increase sample number")
        self.toolButton_sample_down = QtWidgets.QToolButton()
        self.toolButton_sample_down.setArrowType(QtCore.Qt.ArrowType.DownArrow)
        self.toolButton_sample_down.setFixedWidth(22)
        self.toolButton_sample_down.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.toolButton_sample_down.setToolTip("Decrease sample number")
        arrow_layout.addWidget(self.toolButton_sample_up)
        arrow_layout.addWidget(self.toolButton_sample_down)
        sample_row.addLayout(arrow_layout)
        name_grid.addWidget(self.sample_row_widget, 3, 1)
        # Field for the "Custom" preset
        self.lineEdit_custom_name = InfoLineEdit("Custom file name (safe characters)")
        self.lineEdit_custom_name.setMinimumWidth(300)
        self.label_custom_name = QtWidgets.QLabel("Custom name:")
        name_grid.addWidget(self.label_custom_name, 4, 0)
        name_grid.addWidget(self.lineEdit_custom_name, 4, 1)
        # Hidden by default; shown only when "Custom" preset is selected
        self.label_custom_name.hide()
        self.lineEdit_custom_name.hide()
        name_grid.addItem(
            QtWidgets.QSpacerItem(
                0,
                0,
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Minimum,
            ),
            5,
            0,
        )
        self.pushButton_reset_preset = QtWidgets.QPushButton("Reset")
        name_grid.addWidget(
            self.pushButton_reset_preset,
            5,
            1,
            1,
            1,
            QtCore.Qt.AlignmentFlag.AlignRight,
        )
        grid.addWidget(gb_name, 6, 0, 1, 3)

        # Process progress and time remaining
        self.progressBar_process = QtWidgets.QProgressBar()
        grid.addWidget(self.progressBar_process, 7, 0, 1, 3)
        self.label_time_remaining = QtWidgets.QLabel("Time remaining: N/A")
        grid.addWidget(self.label_time_remaining, 8, 0, 1, 3)
        self.label_time_to_limit = QtWidgets.QLabel("To 30 V: N/A")
        grid.addWidget(self.label_time_to_limit, 9, 0, 1, 3)

        # Live values group
        self.groupBox_live_values = QtWidgets.QGroupBox("Live values")
        lv = QtWidgets.QGridLayout(self.groupBox_live_values)
        lcd_current = QtWidgets.QLCDNumber()
        lcd_current.setSegmentStyle(QtWidgets.QLCDNumber.SegmentStyle.Filled)
        lcd_current.setDigitCount(6)
        lcd_current.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.label_mA = QtWidgets.QLabel("mA")
        lcd_resistance = QtWidgets.QLCDNumber()
        lcd_resistance.setSegmentStyle(QtWidgets.QLCDNumber.SegmentStyle.Filled)
        lcd_resistance.setDigitCount(6)
        lcd_resistance.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.lcd_current_mA = lcd_current
        self.lcd_resistance = lcd_resistance
        self.label_Ohm = QtWidgets.QLabel("Ohm")
        lv.addWidget(lcd_current, 0, 0)
        lv.addWidget(self.label_mA, 0, 1)
        lv.addWidget(lcd_resistance, 0, 2)
        lv.addWidget(self.label_Ohm, 0, 3)
        grid.addWidget(self.groupBox_live_values, 10, 0, 1, 3)

        # Hold resistance and percent
        hr_layout = QtWidgets.QHBoxLayout()
        self.label_resistance_at_hold_current = QtWidgets.QLabel("0")
        self.label_resistance_percent_from_hold = QtWidgets.QLabel("0")
        self.label_resistance_ohm_suffix = QtWidgets.QLabel("Ohm")
        self.label_percent_suffix = QtWidgets.QLabel("%")
        hr_layout.addWidget(QtWidgets.QLabel("Hold resistance:"))
        hr_layout.addWidget(self.label_resistance_at_hold_current)
        hr_layout.addWidget(self.label_resistance_ohm_suffix)
        hr_layout.addSpacing(16)
        hr_layout.addWidget(QtWidgets.QLabel("Percent from hold:"))
        hr_layout.addWidget(self.label_resistance_percent_from_hold)
        hr_layout.addWidget(self.label_percent_suffix)
        hr_layout.addStretch(1)
        grid.addLayout(hr_layout, 11, 0, 1, 3)

        # Start/Stop and reverse buttons (pinned below the scroll area)
        self.pushButton_start_process = QtWidgets.QPushButton("Start annealing process")
        bfont = QtGui.QFont()
        bfont.setPointSize(12)
        self.pushButton_start_process.setFont(bfont)
        self.pushButton_start_process.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.pushButton_reverse_now = QtWidgets.QPushButton("Reverse current now")
        self.pushButton_reverse_now.setFont(bfont)
        self.pushButton_reverse_now.setEnabled(False)
        self.pushButton_reverse_now.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        sticky_buttons_layout.addWidget(self.pushButton_start_process)
        sticky_buttons_layout.addWidget(self.pushButton_reverse_now)

        left_container_layout.setStretch(0, 1)

        frame_layout_proc = QtWidgets.QVBoxLayout(self.frame_process_settings)
        frame_layout_proc.setContentsMargins(0, 0, 0, 0)
        frame_layout_proc.addWidget(gb_proc)

        # ------------------------------------------------------------------
        # Commands and responses (collapsible)
        # ------------------------------------------------------------------
        self.frame_command_and_response = QtWidgets.QFrame(self.centralWidget)
        self.frame_command_and_response.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        main_layout.addWidget(self.frame_command_and_response)

        frame_layout_cmd = QtWidgets.QVBoxLayout(self.frame_command_and_response)
        frame_layout_cmd.setContentsMargins(0, 0, 0, 0)

        # Toggle header
        header = QtWidgets.QToolButton()
        header.setText("Commands and responses")
        header.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        header.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        header.setCheckable(True)
        header.setChecked(False)
        frame_layout_cmd.addWidget(header)

        # Content container
        self._cmd_container = QtWidgets.QWidget()
        vcmd = QtWidgets.QVBoxLayout(self._cmd_container)
        vcmd.setContentsMargins(8, 4, 8, 8)

        hl = QtWidgets.QHBoxLayout()
        self.lineEdit_serial_command = QtWidgets.QLineEdit()
        self.pushButton_send_serial_command = QtWidgets.QPushButton("Send")
        hl.addWidget(self.lineEdit_serial_command, stretch=1)
        hl.addWidget(self.pushButton_send_serial_command)
        vcmd.addLayout(hl)

        self.label_last_command = QtWidgets.QLabel("")
        self.label_last_command.setWordWrap(True)
        vcmd.addWidget(self.label_last_command)
        self.label_serial_response = QtWidgets.QLabel("")
        self.label_serial_response.setWordWrap(True)
        vcmd.addWidget(self.label_serial_response)

        frame_layout_cmd.addWidget(self._cmd_container)
        self._cmd_container.setVisible(False)

        def _toggle_cmds(checked: bool) -> None:
            self._cmd_container.setVisible(checked)
            header.setArrowType(QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow)

        header.toggled.connect(_toggle_cmds)

        # Status bar
        self.statusBar = QtWidgets.QStatusBar(MainWindow)
        MainWindow.setStatusBar(self.statusBar)

        QtCore.QMetaObject.connectSlotsByName(MainWindow)
