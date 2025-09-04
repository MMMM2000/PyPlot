"""English, layout-based UI for the Current Annealing Logger.

This UI mirrors the controls expected by ``current_annealing_logger.py``
but presents them with modern layouts and English labels. Object names
are preserved for compatibility with the existing logic.
"""

from PyQt6 import QtCore, QtGui, QtWidgets


class Ui_MainWindow(object):
    def setupUi(self, MainWindow: QtWidgets.QMainWindow) -> None:
        MainWindow.setObjectName("CurrentAnnealingMainWindow")
        MainWindow.resize(880, 720)

        font = QtGui.QFont()
        font.setPointSize(10)
        MainWindow.setFont(font)

        self.centralWidget = QtWidgets.QWidget(MainWindow)
        MainWindow.setCentralWidget(self.centralWidget)

        main_layout = QtWidgets.QVBoxLayout(self.centralWidget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

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

        # Port number spin
        self.label_cislo_portu = QtWidgets.QLabel("COM:")
        gb_layout.addWidget(self.label_cislo_portu)
        self.spinBox_cislo_portu = QtWidgets.QSpinBox()
        self.spinBox_cislo_portu.setRange(1, 127)
        self.spinBox_cislo_portu.setValue(3)
        gb_layout.addWidget(self.spinBox_cislo_portu)

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
        self.radioButton_raw_VCP.setChecked(True)
        self.radioButton_manualne_zihanie = QtWidgets.QRadioButton("Manual annealing")
        self.radioButton_automatizovane_zihanie = QtWidgets.QRadioButton("Automatic annealing")
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

        # Log file selection
        self.label_logfile = QtWidgets.QLabel("Log file:")
        self.lineEdit_log_subor = QtWidgets.QLineEdit()
        self.lineEdit_log_subor.setPlaceholderText("data/sample.txt")
        self.pushButton_select_filename = QtWidgets.QPushButton("...")
        grid.addWidget(self.label_logfile, 0, 0)
        grid.addWidget(self.lineEdit_log_subor, 0, 1)
        grid.addWidget(self.pushButton_select_filename, 0, 2)

        # Hold current [mA]
        self.label_hodnota_staly_prud = QtWidgets.QLabel("Hold current [mA]:")
        self.spinBox_hodnota_staly_prud = QtWidgets.QSpinBox()
        self.spinBox_hodnota_staly_prud.setRange(1, 10_000)
        self.spinBox_hodnota_staly_prud.setValue(10)
        grid.addWidget(self.label_hodnota_staly_prud, 1, 0)
        grid.addWidget(self.spinBox_hodnota_staly_prud, 1, 1)

        # Hold time [s]
        self.label_logfile_doba_staleho_prudu = QtWidgets.QLabel("Hold time [s]:")
        self.spinBox_doba_staly_prud = QtWidgets.QSpinBox()
        self.spinBox_doba_staly_prud.setRange(1, 36000)
        self.spinBox_doba_staly_prud.setValue(10)
        grid.addWidget(self.label_logfile_doba_staleho_prudu, 2, 0)
        grid.addWidget(self.spinBox_doba_staly_prud, 2, 1)

        # Hold/Stop button and elapsed time
        self.pushButton_start_stop_drzania_prudu = QtWidgets.QPushButton("Hold current now!")
        grid.addWidget(self.pushButton_start_stop_drzania_prudu, 1, 2)

        self.label_logfile_uplynulo = QtWidgets.QLabel("Elapsed:")
        self.lcdNumber_uplynute_sekundy = QtWidgets.QLCDNumber()
        self.lcdNumber_uplynute_sekundy.setSegmentStyle(QtWidgets.QLCDNumber.SegmentStyle.Filled)
        self.label_logfile_s = QtWidgets.QLabel("s")
        h = QtWidgets.QHBoxLayout()
        h.addWidget(self.label_logfile_uplynulo)
        h.addWidget(self.lcdNumber_uplynute_sekundy)
        h.addWidget(self.label_logfile_s)
        h.addStretch(1)
        grid.addLayout(h, 2, 2)

        # Live values group
        self.groupBox_aktualne_hodnoty = QtWidgets.QGroupBox("Live values")
        lv = QtWidgets.QGridLayout(self.groupBox_aktualne_hodnoty)
        self.lcdNumber_aktualny_prud_mA = QtWidgets.QLCDNumber()
        self.lcdNumber_aktualny_prud_mA.setSegmentStyle(QtWidgets.QLCDNumber.SegmentStyle.Filled)
        self.label_mA = QtWidgets.QLabel("mA")
        self.lcdNumber_aktualny_odpor = QtWidgets.QLCDNumber()
        self.lcdNumber_aktualny_odpor.setSegmentStyle(QtWidgets.QLCDNumber.SegmentStyle.Filled)
        self.label_Ohm = QtWidgets.QLabel("Ohm")
        lv.addWidget(self.lcdNumber_aktualny_prud_mA, 0, 0)
        lv.addWidget(self.label_mA, 0, 1)
        lv.addWidget(self.lcdNumber_aktualny_odpor, 0, 2)
        lv.addWidget(self.label_Ohm, 0, 3)
        grid.addWidget(self.groupBox_aktualne_hodnoty, 3, 0, 1, 3)

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
        grid.addLayout(hr_layout, 4, 0, 1, 3)

        # Start/Stop process button
        self.pushButton_spusti_proces = QtWidgets.QPushButton("Start annealing process")
        bfont = QtGui.QFont()
        bfont.setPointSize(12)
        self.pushButton_spusti_proces.setFont(bfont)
        grid.addWidget(self.pushButton_spusti_proces, 5, 0, 1, 3)

        frame_layout_proc = QtWidgets.QVBoxLayout(self.frame_nastavenia_procesu)
        frame_layout_proc.setContentsMargins(0, 0, 0, 0)
        frame_layout_proc.addWidget(gb_proc)

        # ------------------------------------------------------------------
        # Commands and responses (frame_command_and_response)
        # ------------------------------------------------------------------
        self.frame_command_and_response = QtWidgets.QFrame(self.centralWidget)
        self.frame_command_and_response.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.frame_command_and_response.setFrameShadow(QtWidgets.QFrame.Shadow.Raised)
        main_layout.addWidget(self.frame_command_and_response)

        gb_cmd = QtWidgets.QGroupBox("Commands and responses", self.frame_command_and_response)
        self.groupBox_prikazy_a_odpovede = gb_cmd
        vcmd = QtWidgets.QVBoxLayout(gb_cmd)

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

        frame_layout_cmd = QtWidgets.QVBoxLayout(self.frame_command_and_response)
        frame_layout_cmd.setContentsMargins(0, 0, 0, 0)
        frame_layout_cmd.addWidget(gb_cmd)

        # Status bar
        self.statusBar = QtWidgets.QStatusBar(MainWindow)
        MainWindow.setStatusBar(self.statusBar)

        QtCore.QMetaObject.connectSlotsByName(MainWindow)

