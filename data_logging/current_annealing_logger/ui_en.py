"""English, layout-based UI for the Current Annealing Logger.

This UI mirrors the controls expected by ``current_annealing_logger.py``
but presents them with modern layouts and English labels. Object names
are preserved for compatibility with the existing logic.
"""

from PyQt6 import QtCore, QtGui, QtWidgets
try:
    # Reuse InfoLineEdit from data logger for inline info and validation
    from data_logging.data_logger.file_name_builder import InfoLineEdit
except Exception:
    InfoLineEdit = QtWidgets.QLineEdit  # type: ignore[assignment]


class Ui_MainWindow(object):
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

        left_panel = QtWidgets.QWidget(self.centralWidget)
        # Allow the left column to shrink to viewport width without forcing
        # a horizontal scrollbar in the scroll area.
        left_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        main_layout = QtWidgets.QVBoxLayout(left_panel)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(12)
        left_scroll = QtWidgets.QScrollArea(self.centralWidget)
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
        root.addWidget(left_scroll, stretch=0)

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
        # Serial basics (frame_zakladne_nastavenia_portu)
        # ------------------------------------------------------------------
        self.frame_zakladne_nastavenia_portu = QtWidgets.QFrame(self.centralWidget)
        self.frame_zakladne_nastavenia_portu.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.frame_zakladne_nastavenia_portu.setFrameShadow(QtWidgets.QFrame.Shadow.Raised)
        main_layout.addWidget(self.frame_zakladne_nastavenia_portu)

        gb_serial = QtWidgets.QGroupBox("Serial settings", self.frame_zakladne_nastavenia_portu)
        gb_layout = QtWidgets.QHBoxLayout(gb_serial)
        self.groupBox_zakladne_nastavenia_portu = gb_serial

        # Port selection (modernized): list available ports with names
        self.label_port = QtWidgets.QLabel("Port:")
        gb_layout.addWidget(self.label_port)
        self.comboBox_port = QtWidgets.QComboBox()
        gb_layout.addWidget(self.comboBox_port)
        self.pushButton_refresh_ports = QtWidgets.QPushButton("Refresh")
        gb_layout.addWidget(self.pushButton_refresh_ports)

        # Legacy numeric COM spin kept for compatibility, but hidden
        self.label_cislo_portu = QtWidgets.QLabel("COM:")
        self.label_cislo_portu.hide()
        self.spinBox_cislo_portu = QtWidgets.QSpinBox()
        self.spinBox_cislo_portu.setRange(1, 127)
        self.spinBox_cislo_portu.setValue(3)
        self.spinBox_cislo_portu.hide()

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
        self.comboBox_baudrate.setCurrentText("9600")
        gb_layout.addWidget(self.comboBox_baudrate)

        gb_layout.addStretch(1)

        self.pushButton_pripojPort = QtWidgets.QPushButton("Connect to port")
        gb_layout.addWidget(self.pushButton_pripojPort)

        # Fit the group box into the frame
        frame_layout_serial = QtWidgets.QVBoxLayout(self.frame_zakladne_nastavenia_portu)
        frame_layout_serial.setContentsMargins(0, 0, 0, 0)
        frame_layout_serial.addWidget(gb_serial)

        # ------------------------------------------------------------------
        # Modus operandi (frame_modus_operandi)
        # ------------------------------------------------------------------
        self.frame_modus_operandi = QtWidgets.QFrame(self.centralWidget)
        self.frame_modus_operandi.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.frame_modus_operandi.setFrameShadow(QtWidgets.QFrame.Shadow.Raised)
        main_layout.addWidget(self.frame_modus_operandi)

        gb_mode = QtWidgets.QGroupBox("Mode of operation", self.frame_modus_operandi)
        self.groupBox_modus_operandi = gb_mode
        mode_layout = QtWidgets.QHBoxLayout(gb_mode)
        self.radioButton_raw_VCP = QtWidgets.QRadioButton("Raw VCP")
        self.radioButton_manualne_zihanie = QtWidgets.QRadioButton("Manual annealing")
        self.radioButton_automatizovane_zihanie = QtWidgets.QRadioButton("Automatic annealing")
        # Default to automatic annealing
        self.radioButton_automatizovane_zihanie.setChecked(True)
        mode_layout.addWidget(self.radioButton_raw_VCP)
        mode_layout.addWidget(self.radioButton_manualne_zihanie)
        mode_layout.addWidget(self.radioButton_automatizovane_zihanie)
        mode_layout.addStretch(1)

        frame_layout_mode = QtWidgets.QVBoxLayout(self.frame_modus_operandi)
        frame_layout_mode.setContentsMargins(0, 0, 0, 0)
        frame_layout_mode.addWidget(gb_mode)

        # ------------------------------------------------------------------
        # Process settings (frame_nastavenia_procesu)
        # ------------------------------------------------------------------
        self.frame_nastavenia_procesu = QtWidgets.QFrame(self.centralWidget)
        self.frame_nastavenia_procesu.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.frame_nastavenia_procesu.setFrameShadow(QtWidgets.QFrame.Shadow.Raised)
        main_layout.addWidget(self.frame_nastavenia_procesu)

        gb_proc = QtWidgets.QGroupBox("Process settings", self.frame_nastavenia_procesu)
        self.groupBox_nastavenia_procesu = gb_proc
        grid = QtWidgets.QGridLayout(gb_proc)

        # Log file location (separate directory and file name)
        self.label_log_dir = QtWidgets.QLabel("Directory:")
        self.lineEdit_log_dir = QtWidgets.QLineEdit()
        self.pushButton_browse_dir = QtWidgets.QPushButton("Browse")
        grid.addWidget(self.label_log_dir, 0, 0)
        grid.addWidget(self.lineEdit_log_dir, 0, 1)
        grid.addWidget(self.pushButton_browse_dir, 0, 2)

        self.label_log_file = QtWidgets.QLabel("File name:")
        self.lineEdit_log_file = QtWidgets.QLineEdit()
        self.label_extension = QtWidgets.QLabel(".txt")
        grid.addWidget(self.label_log_file, 1, 0)
        grid.addWidget(self.lineEdit_log_file, 1, 1)
        grid.addWidget(self.label_extension, 1, 2)

        # Legacy single-path widgets kept (hidden) for compatibility with code
        self.label_logfile = QtWidgets.QLabel("Log file:")
        self.label_logfile.hide()
        self.lineEdit_log_subor = QtWidgets.QLineEdit()
        self.lineEdit_log_subor.setPlaceholderText("data/sample.txt")
        self.lineEdit_log_subor.hide()
        self.pushButton_select_filename = QtWidgets.QPushButton("...")
        self.pushButton_select_filename.hide()

        # Hold current [mA]
        self.label_hodnota_staly_prud = QtWidgets.QLabel("Max current [mA]:")
        self.spinBox_hodnota_staly_prud = QtWidgets.QSpinBox()
        self.spinBox_hodnota_staly_prud.setRange(1, 10_000)
        self.spinBox_hodnota_staly_prud.setValue(10)
        # Move one row down to avoid overlap with File name
        grid.addWidget(self.label_hodnota_staly_prud, 2, 0)
        grid.addWidget(self.spinBox_hodnota_staly_prud, 2, 1)

        # Hold time [s]
        self.label_logfile_doba_staleho_prudu = QtWidgets.QLabel("Hold time [s]:")
        self.spinBox_doba_staly_prud = QtWidgets.QSpinBox()
        self.spinBox_doba_staly_prud.setRange(1, 36000)
        # Default hold time 1 second
        self.spinBox_doba_staly_prud.setValue(1)
        # Shift down by one row
        grid.addWidget(self.label_logfile_doba_staleho_prudu, 3, 0)
        grid.addWidget(self.spinBox_doba_staly_prud, 3, 1)

        # Hold/Stop button and elapsed time
        # Hold button + Step control in one row to save space
        hold_and_step = QtWidgets.QHBoxLayout()
        self.pushButton_start_stop_drzania_prudu = QtWidgets.QPushButton("Hold current now!")
        hold_and_step.addWidget(self.pushButton_start_stop_drzania_prudu)
        hold_and_step.addSpacing(8)
        self.label_step = QtWidgets.QLabel("Step [mA]:")
        self.spinBox_step_mA = QtWidgets.QSpinBox()
        self.spinBox_step_mA.setRange(1, 10000)
        self.spinBox_step_mA.setValue(1)
        self.spinBox_step_mA.setMaximumWidth(90)
        hold_and_step.addWidget(self.label_step)
        hold_and_step.addWidget(self.spinBox_step_mA)
        hold_and_step.addStretch(1)
        grid.addLayout(hold_and_step, 2, 2)

        self.label_logfile_uplynulo = QtWidgets.QLabel("Elapsed:")
        self.lcdNumber_uplynute_sekundy = QtWidgets.QLCDNumber()
        self.lcdNumber_uplynute_sekundy.setSegmentStyle(QtWidgets.QLCDNumber.SegmentStyle.Filled)
        self.label_logfile_s = QtWidgets.QLabel("s")
        h = QtWidgets.QHBoxLayout()
        h.addWidget(self.label_logfile_uplynulo)
        h.addWidget(self.lcdNumber_uplynute_sekundy)
        h.addWidget(self.label_logfile_s)
        h.addStretch(1)
        # Align with Hold time row
        grid.addLayout(h, 3, 2)

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
        self.lineEdit_microwire = InfoLineEdit("Microwire identifier, e.g., 1_2")
        try:
            self.lineEdit_microwire.set_validation(r"^[A-Za-z0-9_]+$", "Use only letters, numbers, or '_' ")  # type: ignore[attr-defined]
        except Exception:
            pass
        self.lineEdit_microwire.setText("1_2")
        self.lineEdit_sample = InfoLineEdit("Sample, e.g., s1 or s2-1")
        try:
            self.lineEdit_sample.set_validation(r"^s\d+(?:-\d+)?$", "Use pattern like s1 or s2-1")  # type: ignore[attr-defined]
        except Exception:
            pass
        self.lineEdit_sample.setText("s1")
        name_grid.addWidget(QtWidgets.QLabel("Composition:"), 1, 0)
        name_grid.addWidget(self.lineEdit_composition, 1, 1)
        name_grid.addWidget(QtWidgets.QLabel("Microwire:"), 2, 0)
        name_grid.addWidget(self.lineEdit_microwire, 2, 1)
        name_grid.addWidget(QtWidgets.QLabel("Sample:"), 3, 0)
        name_grid.addWidget(self.lineEdit_sample, 3, 1)
        # Field for the "Custom" preset
        self.lineEdit_custom_name = InfoLineEdit("Custom file name (safe characters)")
        self.label_custom_name = QtWidgets.QLabel("Custom name:")
        name_grid.addWidget(self.label_custom_name, 4, 0)
        name_grid.addWidget(self.lineEdit_custom_name, 4, 1)
        # Hidden by default; shown only when "Custom" preset is selected
        self.label_custom_name.hide()
        self.lineEdit_custom_name.hide()
        grid.addWidget(gb_name, 4, 0, 1, 3)

        # Reverse sweep and loops controls
        rev = QtWidgets.QHBoxLayout()
        self.checkBox_reverse = QtWidgets.QCheckBox("Reverse to zero after max")
        self.spinBox_loops = QtWidgets.QSpinBox()
        self.spinBox_loops.setRange(1, 100000)
        self.spinBox_loops.setValue(1)
        self.checkBox_infinite_loops = QtWidgets.QCheckBox("∞")
        self.checkBox_infinite_loops.setToolTip("Repeat indefinitely")
        rev.addWidget(self.checkBox_reverse)
        rev.addSpacing(12)
        rev.addWidget(QtWidgets.QLabel("Loops:"))
        rev.addWidget(self.spinBox_loops)
        rev.addWidget(self.checkBox_infinite_loops)
        rev.addStretch(1)
        grid.addLayout(rev, 5, 0, 1, 3)

        # Process progress and time remaining
        self.progressBar_process = QtWidgets.QProgressBar()
        grid.addWidget(self.progressBar_process, 6, 0, 1, 3)
        self.label_time_remaining = QtWidgets.QLabel("Time remaining: N/A")
        grid.addWidget(self.label_time_remaining, 7, 0, 1, 3)

        # Live values group
        self.groupBox_aktualne_hodnoty = QtWidgets.QGroupBox("Live values")
        lv = QtWidgets.QGridLayout(self.groupBox_aktualne_hodnoty)
        self.lcdNumber_aktualny_prud_mA = QtWidgets.QLCDNumber()
        self.lcdNumber_aktualny_prud_mA.setSegmentStyle(QtWidgets.QLCDNumber.SegmentStyle.Filled)
        self.lcdNumber_aktualny_prud_mA.setDigitCount(6)
        self.lcdNumber_aktualny_prud_mA.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.label_mA = QtWidgets.QLabel("mA")
        self.lcdNumber_aktualny_odpor = QtWidgets.QLCDNumber()
        self.lcdNumber_aktualny_odpor.setSegmentStyle(QtWidgets.QLCDNumber.SegmentStyle.Filled)
        self.lcdNumber_aktualny_odpor.setDigitCount(6)
        self.lcdNumber_aktualny_odpor.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.label_Ohm = QtWidgets.QLabel("Ohm")
        lv.addWidget(self.lcdNumber_aktualny_prud_mA, 0, 0)
        lv.addWidget(self.label_mA, 0, 1)
        lv.addWidget(self.lcdNumber_aktualny_odpor, 0, 2)
        lv.addWidget(self.label_Ohm, 0, 3)
        grid.addWidget(self.groupBox_aktualne_hodnoty, 8, 0, 1, 3)

        # Hold resistance and percent
        hr_layout = QtWidgets.QHBoxLayout()
        self.label_resistance_at_hold_current = QtWidgets.QLabel("0")
        self.label_resistance_percento_from_hold = QtWidgets.QLabel("0")
        self.label_resistance_percento_from_hold_3 = QtWidgets.QLabel("Ohm")
        self.label_resistance_percento_from_hold_2 = QtWidgets.QLabel("%")
        hr_layout.addWidget(QtWidgets.QLabel("Hold resistance:"))
        hr_layout.addWidget(self.label_resistance_at_hold_current)
        hr_layout.addWidget(self.label_resistance_percento_from_hold_3)
        hr_layout.addSpacing(16)
        hr_layout.addWidget(QtWidgets.QLabel("Percent from hold:"))
        hr_layout.addWidget(self.label_resistance_percento_from_hold)
        hr_layout.addWidget(self.label_resistance_percento_from_hold_2)
        hr_layout.addStretch(1)
        grid.addLayout(hr_layout, 9, 0, 1, 3)

        # Start/Stop process button
        self.pushButton_spusti_proces = QtWidgets.QPushButton("Start annealing process")
        bfont = QtGui.QFont()
        bfont.setPointSize(12)
        self.pushButton_spusti_proces.setFont(bfont)
        grid.addWidget(self.pushButton_spusti_proces, 10, 0, 1, 3)

        frame_layout_proc = QtWidgets.QVBoxLayout(self.frame_nastavenia_procesu)
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
        self.lineEdit_prikaz_portu = QtWidgets.QLineEdit()
        self.pushButton_posli_prikaz_portu = QtWidgets.QPushButton("Send")
        hl.addWidget(self.lineEdit_prikaz_portu, stretch=1)
        hl.addWidget(self.pushButton_posli_prikaz_portu)
        vcmd.addLayout(hl)

        self.label_prikaz_portu = QtWidgets.QLabel("")
        self.label_prikaz_portu.setWordWrap(True)
        vcmd.addWidget(self.label_prikaz_portu)
        self.label_odpoved_portu = QtWidgets.QLabel("")
        self.label_odpoved_portu.setWordWrap(True)
        vcmd.addWidget(self.label_odpoved_portu)

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
