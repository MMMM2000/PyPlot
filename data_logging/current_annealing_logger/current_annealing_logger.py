# -*- coding: utf-8 -*-
"""Current Annealing Logger for HMP4030.

Modern PyQt6 application that logs voltage/current from an HMP4030 power
source during current annealing. Includes automatic and manual modes,
live plotting, file naming presets, port discovery, and robust handling
of contact loss and device timeouts.
"""

import sys
import os
import time
import math
from pathlib import Path
from collections import deque
from PyQt6 import QtCore, QtWidgets, QtSerialPort, QtGui
from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtSerialPort import QSerialPortInfo

from .ui_en import Ui_MainWindow
from plotting.utils import ensure_app_theme, format_annealing_title, show_plots, install_standard_menu

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.figure import Figure
try:
    from matplotlib.backends.backend_qt5agg import (
        FigureCanvasQTAgg as FigureCanvas,
        NavigationToolbar2QT as NavigationToolbar,
    )
except Exception:
    FigureCanvas = None
    NavigationToolbar = None


fig_size = plt.rcParams["figure.figsize"]
fig_size[0] = 19 #19
fig_size[1] = 10 #10
plt.rcParams["figure.figsize"] = fig_size
plt.rcParams["font.family"] = "Palatino Linotype"
plt.rcParams["font.size"] = 14

def _default_download_dir() -> str:
    home = Path.home()
    candidates = [
        Path(os.environ.get("USERPROFILE", "")) / "Downloads",
        home / "Downloads",
        home / "downloads",
    ]
    for p in candidates:
        try:
            if p and p.exists():
                return str(p)
        except Exception:
            continue
    p = home / "Downloads"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(p)

DEFAULT_LOG_DIR = _default_download_dir()

DEFAULT_PRESET = {
    "preset": 0,
    "composition": "Ni51Fe26Ga21",
    "microwire": "1_2",
    "sample": "s1",
    "custom_name": "",
}


class MainWindow(QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        # Window title and size cap for laptop screens
        self.setWindowTitle("Current Annealing Logger")
        try:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                self.resize(
                    min(self.width() or 880, max(640, avail.width() - 80)),
                    min(self.height() or 720, max(480, avail.height() - 80)),
                )
        except Exception:
            pass
        install_standard_menu(self, help_topic="logger_current_annealing")
        # Remember last log directory and file separately
        self.settings = QtCore.QSettings("microwire", "current_annealing")
        if hasattr(self.ui, 'lineEdit_log_dir'):
            self.ui.lineEdit_log_dir.setText(
                self.settings.value("log_dir", DEFAULT_LOG_DIR, type=str)
            )
        if hasattr(self.ui, 'lineEdit_log_file'):
            self.ui.lineEdit_log_file.setText(
                self.settings.value("log_file", "anneal_log", type=str)
            )
        self.restore_name_preset()
        try:
            last_max = int(self.settings.value("max_current", 10))
            self.ui.spinBox_hodnota_staly_prud.setValue(last_max)
        except Exception:
            pass
        self.init_live_values()
        self.odpoved_portu = ''
        self.prikaz_portu = ''
        self.pripojene = False
        self.cislo_portu = self.ui.spinBox_cislo_portu.value()
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())
        self.ser_mcu = QtSerialPort.QSerialPort()
        self.zamok = QtCore.QMutex()
        self.timer = QtCore.QTimer()
        self.timer.stop();
        self.timer.timeout.connect(self.handle_update_label_odpoved_portu)
        self.timer.start(50)
        # timer for time remaining label
        self.time_timer = QtCore.QTimer()
        self.time_timer.timeout.connect(self.update_time_estimate)
        self.time_timer.start(1000)
        
        #tu je casovac na prud
        self.sekundy = 0
        self.prud_timer_on = False
        self.timer_prud = QtCore.QTimer()
        self.timer_prud.stop();
        self.timer_prud.timeout.connect(self.handle_update_lcdNumber_uplynute_sekundy)
        
        #tu je casovac na prikazy - posielanie
        self.command_number = 0
        self.timer_command = QtCore.QTimer()
        self.timer_command.stop();
        self.timer_command.timeout.connect(self.handle_send_new_command)
        
        self.f_name = None
        self.f_out = None
        self.pocet_vzoriek = 1000
        self.vzorka_N = 0
        self.zaznam_on = False
        self.napatie = True
        
        self.percento_pokles_R = 10
        self.doba_staly_prud = 1
        self.hodnota_staly_prud = 10
        self.modus_operandi = 0 #0 - VCp, 1- manual, 2 - automat
        self.proces_on = False
        
        self.current_current_set = 0.001
        self.current_current_read = 0
        self.current_increment = 0.001
        self.temp_resistance_maximum = 0
        self.current_voltage = 0
        self.current_resistance = 0
        self.open_threshold = 30
        self.max_voltage = 30.0
        self._max_voltage_dialog = False
        self.direction_ascending = True
        self.sample_ready = False
        self.force_stop_at_zero = False
        # Debug + progress/time tracking
        self.DEBUG = False
        self.sample_rate: float | None = None
        self._rate_window: deque[float] = deque(maxlen=200)
        self.last_sample_time: float | None = None
        self._finish_time: float | None = None
        self.step_idx = 0
        self.total_steps = 0
        self._contact_lost = False
        self._zero_current_count = 0
        self._nonzero_current_seen = False
        self._process_start_time: float | None = None
        self._last_nonzero_current_time: float | None = None
        self._contact_grace_period = 5.0
        self._last_serial_rx: float | None = None
        self._serial_quiet_failures = 0
        
        # print("Číslo portu: COM" + str(self.cislo_portu))
        # print("Baudrate: " + str(self.baudrate))

        # Populate modern port list if available
        self.port_name = ""
        if hasattr(self.ui, 'comboBox_port'):
            try:
                self.populate_ports()
            except Exception:
                pass
        self._set_port_controls_enabled(True)
        
        #prepojenie signalov a slotov
        self.ui.pushButton_pripojPort.clicked.connect(self.handle_pushButton_pripojPort_clicked)
        self.ui.spinBox_cislo_portu.valueChanged.connect(self.handle_spinBox_cislo_portu_valueChanged)
        self.ui.comboBox_baudrate.currentIndexChanged.connect(self.handle_comboBox_baudrate_currentIndexChanged)
        self.ui.pushButton_posli_prikaz_portu.clicked.connect(self.handle_pushButton_posli_prikaz_portu_clicked)
        
        if hasattr(self.ui, 'comboBox_mode'):
            self.ui.comboBox_mode.currentIndexChanged.connect(self.handle_mode_changed)
        
        self.ui.spinBox_hodnota_staly_prud.valueChanged.connect(self.handle_spinBox_hodnota_staly_prud_valueChanged)
        self.ui.spinBox_doba_staly_prud.valueChanged.connect(self.handle_spinBox_doba_staly_prud_valueChanged)
        self.ui.pushButton_start_stop_drzania_prudu.clicked.connect(self.handle_pushButton_start_stop_drzania_prudu_clicked)
        
        self.ui.pushButton_spusti_proces.clicked.connect(self.handle_pushButton_spusti_proces_clicked)
        self.ui.lineEdit_log_subor.textChanged.connect(self.handle_lineEdit_log_subor_text_changed)
        self.ui.pushButton_select_filename.clicked.connect(self.handle_select_filename_en)
        # Also hook legacy browse button to new unified handler
        if hasattr(self.ui, 'pushButton_select_filename'):
            self.ui.pushButton_select_filename.clicked.connect(self.handle_browse_full_file)
        if hasattr(self.ui, 'pushButton_reverse_now'):
            self.ui.pushButton_reverse_now.clicked.connect(self.handle_pushButton_reverse_now_clicked)
            self.ui.pushButton_reverse_now.setEnabled(False)
        # New UI pieces: port dropdown and separate log directory/name
        if hasattr(self.ui, 'comboBox_port'):
            self.ui.comboBox_port.currentIndexChanged.connect(self.handle_comboBox_port_changed)
        if hasattr(self.ui, 'pushButton_refresh_ports'):
            self.ui.pushButton_refresh_ports.clicked.connect(self.populate_ports)
        if hasattr(self.ui, 'pushButton_browse_dir'):
            self.ui.pushButton_browse_dir.clicked.connect(self.handle_browse_log_dir)
        if hasattr(self.ui, 'pushButton_open_dir'):
            self.ui.pushButton_open_dir.clicked.connect(self.open_log_dir)
        if hasattr(self.ui, 'lineEdit_log_dir'):
            self.ui.lineEdit_log_dir.textChanged.connect(self.sync_full_log_path)
        if hasattr(self.ui, 'lineEdit_log_file'):
            self.ui.lineEdit_log_file.textChanged.connect(self.sync_full_log_path)
        # Name builder and planned duration estimation
        if hasattr(self.ui, 'comboBox_name_preset'):
            self.ui.comboBox_name_preset.currentIndexChanged.connect(self.update_file_name_from_preset)
        for name in ('lineEdit_composition','lineEdit_microwire','lineEdit_sample','lineEdit_custom_name'):
            if hasattr(self.ui, name):
                getattr(self.ui, name).textChanged.connect(self.update_file_name_from_preset)
        if hasattr(self.ui, 'pushButton_reset_preset'):
            self.ui.pushButton_reset_preset.clicked.connect(self.reset_name_preset)
        if hasattr(self.ui, 'checkBox_reverse'):
            self.ui.checkBox_reverse.toggled.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'spinBox_loops'):
            self.ui.spinBox_loops.valueChanged.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'checkBox_infinite_loops'):
            self.ui.checkBox_infinite_loops.toggled.connect(self.handle_checkBox_infinite_loops_toggled)
        if hasattr(self.ui, 'spinBox_step_mA'):
            self.ui.spinBox_step_mA.valueChanged.connect(self.handle_step_changed)
        self.ui.spinBox_hodnota_staly_prud.valueChanged.connect(self.update_file_name_from_preset)
        self.ui.spinBox_hodnota_staly_prud.valueChanged.connect(self.update_planned_time_label)
        self.ui.spinBox_doba_staly_prud.valueChanged.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'checkBox_infinite_loops'):
            self.ui.checkBox_infinite_loops.toggled.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'spinBox_step_mA'):
            self.ui.spinBox_step_mA.valueChanged.connect(self.handle_step_changed)

        # Initialize planned estimate and file name once
        try:
            self.update_file_name_from_preset()
            self.update_planned_time_label()
        except Exception:
            pass
        # Apply initial mode selection
        try:
            if hasattr(self.ui, 'comboBox_mode'):
                self.handle_mode_changed(self.ui.comboBox_mode.currentIndex())
        except Exception:
            pass
        
        #nio a tu defaultne enable disable na prvky
        self.ui.frame_nastavenia_procesu.setEnabled(False)
        self.ui.frame_command_and_response.setEnabled(False)
        self.ui.frame_modus_operandi.setEnabled(False)

        # Connection overlay over the left panel until port is connected
        self._setup_connect_overlay()
        if hasattr(self, 'pripojene') and not self.pripojene:
            self._show_connect_overlay(True)
        
        self.max_resistance = 0
        
        self.resistance_at_hold_current = 0
        self.resistance_percento_from_hold = 0
        
        self.commands_init = [
                                #"*IDN?\n",
                                "*RST\n",
                                "SYST:REM\n",
                                "INST:NSEL 3\n",
                                "CURR 0.001\n",
                                "VOLT 30.0\n",
                                "OUTP ON\n"
                                
        ]
        
        
        self.commands_safe_end = [
                                "INST:NSEL 3\n",
                                "OUTP OFF\n",
                                "VOLT 1.0\n",
                                "CURR 0.001\n",
                                "SYST:LOC\n",
                                "OUTP:GEN 0\n"
        ]
        
        self.f_out = None
        self.f_name = self.build_log_path() if hasattr(self, 'build_log_path') else self.ui.lineEdit_log_subor.text()
        
        
        #premenne na kreslenie grafu z dat
        self.prev_value_x = None
        self.curr_value_x = 0
        self.prev_value_y = None
        self.curr_value_y = 0
        self.first_sample = True
        
        self.fig = None
        self.ax1 = None
        self.ax2 = None
                
        self.ciara_marker="o"
        self.ciara_linestyle="-"
        self.ciara_color="r"
        
        self.line1 = None
        self.line2 = None
        # Initialize progress UI defaults
        if hasattr(self.ui, 'progressBar_process'):
            self.ui.progressBar_process.setMaximum(0)
            self.ui.progressBar_process.setValue(0)
        if hasattr(self.ui, 'label_time_remaining'):
            self.ui.label_time_remaining.setText("Time remaining: N/A")
        # Current step defaults
        try:
            self.current_step_mA = self.ui.spinBox_step_mA.value()
        except Exception:
            self.current_step_mA = 1
        self.current_step_A = self.current_step_mA / 1000.0

        # Show initial placeholder plot on the right
        try:
            self.init_graph_window()
            if getattr(self, 'ax1', None) is not None:
                self.ax1.text(
                    0.5, 0.5, 'No data yet', transform=self.ax1.transAxes,
                    ha='center', va='center', fontsize=14, fontweight='bold',
                    color=self.palette().color(QtGui.QPalette.ColorRole.Text),
                    bbox=dict(facecolor='k', alpha=0.35, edgecolor='none', pad=3),
                )
            if getattr(self, 'ax2', None) is not None:
                self.ax2.text(
                    0.5, 0.5, 'No data yet', transform=self.ax2.transAxes,
                    ha='center', va='center', fontsize=14, fontweight='bold',
                    color=self.palette().color(QtGui.QPalette.ColorRole.Text),
                    bbox=dict(facecolor='k', alpha=0.35, edgecolor='none', pad=3),
                )
            if getattr(self, 'canvas', None) is not None:
                self.canvas.draw()
        except Exception:
            pass
        try:
            self.adjustSize()
        except Exception:
            pass

    # utilities
    def dbg(self, *args):
        if getattr(self, 'DEBUG', False):
            try:
                print(*args)
            except Exception:
                pass

    def _set_port_controls_enabled(self, enabled: bool) -> None:
        for name in ('spinBox_cislo_portu', 'comboBox_baudrate', 'comboBox_port', 'pushButton_refresh_ports'):
            w = getattr(self.ui, name, None)
            if w is not None:
                w.setEnabled(enabled)

    def handle_checkBox_infinite_loops_toggled(self, checked: bool) -> None:
        if hasattr(self.ui, 'spinBox_loops'):
            if checked:
                self.ui.spinBox_loops.setValue(0)
                self.ui.spinBox_loops.setEnabled(False)
            else:
                if self.ui.spinBox_loops.value() == 0:
                    self.ui.spinBox_loops.setValue(1)
                self.ui.spinBox_loops.setEnabled(True)
        self.update_planned_time_label()

    #definovanie slotov
    def handle_pushButton_pripojPort_clicked(self):
        if(self.pripojene == False):
            # print('Pripájam port')

            # Use selected port name from dropdown if available
            port_name = ''
            if hasattr(self, 'port_name') and self.port_name:
                port_name = self.port_name
            else:
                try:
                    port_name = 'COM' + str(self.cislo_portu)
                except Exception:
                    port_name = ''
            if not port_name:
                QtWidgets.QMessageBox.warning(self, "No port", "Please select a serial port")
                return
            try:
                import os as _os
                name = _os.path.basename(port_name) if '/' in port_name else port_name
            except Exception:
                name = port_name
            self.ser_mcu.setPortName(name)
            self.ser_mcu.setBaudRate(self.baudrate)
            self.ser_mcu.setFlowControl(QtSerialPort.QSerialPort.FlowControl.NoFlowControl)
            self.ser_mcu.setDataBits(QtSerialPort.QSerialPort.DataBits.Data8)
            self.ser_mcu.setParity(QtSerialPort.QSerialPort.Parity.NoParity)
            self.ser_mcu.setStopBits(QtSerialPort.QSerialPort.StopBits.OneStop)
            
            # print(self.ser_mcu)
            
            if self.ser_mcu.open(QtCore.QIODeviceBase.OpenModeFlag.ReadWrite):
                    # print('Port pripojený')
                    self.ser_mcu.clear()
                    self.ser_mcu.readyRead.connect(self.handle_ser_mcu_readyRead)
                    self.pripojene = True
                    self.ui.pushButton_pripojPort.setText('Disconnect')
                    self.ui.frame_modus_operandi.setEnabled(True)
                    self._set_port_controls_enabled(False)
                    self.ui.frame_command_and_response.setEnabled(True)
                    # Respect the selected mode rather than forcing raw VCP
                    try:
                        if hasattr(self.ui, 'comboBox_mode'):
                            self.handle_mode_changed(self.ui.comboBox_mode.currentIndex())
                        else:
                            self.handle_radioButton_raw_VCP_clicked()
                    except Exception:
                        self.handle_radioButton_raw_VCP_clicked()
                    self._show_connect_overlay(False)
            else:
                    # print('Pripojenie portu zlyhalo')
                    pass

        else:
            if self.proces_on == True:
                self.handle_pushButton_spusti_proces_clicked()
            else:
                self.send_safe_end_commands()
            # print('Odpájam port')
            # Proactively disconnect signal-slot before closing the port
            try:
                self.ser_mcu.readyRead.disconnect(self.handle_ser_mcu_readyRead)
            except Exception:
                pass
            self.ser_mcu.close()
            self.pripojene = False
            self.ui.pushButton_pripojPort.setText('Pripojiť sa k portu')
            self.ui.pushButton_pripojPort.setText('Connect to port')
            self._show_connect_overlay(True)
            self.ui.frame_command_and_response.setEnabled(False)
            self.ui.frame_nastavenia_procesu.setEnabled(False)
            self.ui.frame_modus_operandi.setEnabled(False)
            self._set_port_controls_enabled(True)

    def handle_spinBox_cislo_portu_valueChanged(self):
        self.cislo_portu = self.ui.spinBox_cislo_portu.value()
        # print("Číslo portu: COM" + str(self.cislo_portu))
            
    def handle_comboBox_baudrate_currentIndexChanged(self):
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())
        # print("Baudrate: " + str(self.baudrate))

    def handle_comboBox_port_changed(self):
        """Update selected port name from the dropdown."""
        try:
            data = self.ui.comboBox_port.currentData()
            if data:
                self.port_name = str(data)
            else:
                # fallback to the text
                text = self.ui.comboBox_port.currentText()
                self.port_name = text.split(" - ")[0]
        except Exception:
            pass

    def handle_ser_mcu_readyRead(self):
        if(self.ser_mcu.canReadLine()):
            #print("Prisla lajna")
            self.zamok.lock()
            self.odpoved_portu = str(self.ser_mcu.readLine(),'ascii')
            try:
                self._last_serial_rx = time.monotonic()
            except Exception:
                self._last_serial_rx = None
            # reduce console spam
            # print(self.odpoved_portu)
            if((self.modus_operandi > 0) and (self.proces_on == True)):
                if(self.napatie == True):
                    try:
                        self.current_voltage = float(self.odpoved_portu.strip())
                        if hasattr(self.ui, 'label_live_voltage'):
                            self.ui.label_live_voltage.display(f"{self.current_voltage:.2f}")
                    except ValueError:
                        # Ignore non-numeric responses (e.g., from config commands)
                        self.zamok.unlock()
                        return
                else:
                    try:
                        self.current_current_read = float(self.odpoved_portu.strip())
                    except ValueError:
                        self.zamok.unlock()
                        return
                    if abs(self.current_current_read) < 1e-12:
                        try:
                            now = time.monotonic()
                        except Exception:
                            now = None
                        self._zero_current_count += 1
                        # Treat a zero reading as a valid response so callers
                        # waiting on ``sample_ready`` do not interpret the
                        # timeout as a communication failure.
                        self.sample_ready = True
                        if not self._nonzero_current_seen:
                            # Ignore sustained zero readings until we have
                            # confirmed the setup is capable of sourcing
                            # current at least once. This prevents false
                            # alarms immediately after a process starts when
                            # the supply has not ramped yet.
                            self._last_nonzero_current_time = None
                            self.zamok.unlock()
                            return
                        zero_limit = 6
                        zero_delay = 2.0
                        if (
                            now is not None
                            and self._process_start_time is not None
                            and (now - self._process_start_time) < self._contact_grace_period
                        ):
                            self.zamok.unlock()
                            return
                        if (
                            now is not None
                            and self._last_nonzero_current_time is not None
                            and (now - self._last_nonzero_current_time) < zero_delay
                        ):
                            self.zamok.unlock()
                            return
                        if self._zero_current_count < zero_limit:
                            self.zamok.unlock()
                            return
                        if not self._contact_lost:
                            self._contact_lost = True
                            QtWidgets.QMessageBox.warning(
                                self,
                                "Contact lost",
                                "Measured current is zero. The wire likely burned through. Stopping the process.",
                            )
                            if self.proces_on:
                                self.handle_pushButton_spusti_proces_clicked()
                        self.zamok.unlock()
                        return
                    self._zero_current_count = 0
                    self._contact_lost = False
                    self._nonzero_current_seen = True
                    try:
                        self._last_nonzero_current_time = time.monotonic()
                    except Exception:
                        self._last_nonzero_current_time = None
                    try:
                        self.current_resistance = self.current_voltage / self.current_current_read
                    except ZeroDivisionError:
                        self.zamok.unlock()
                        return
                    #na tomto mieste zapiseme data do suboru
                    if not self.first_sample:
                        if(not self.f_out):
                            try:
                                from os import makedirs
                                from os.path import dirname
                                makedirs(dirname(self.f_name), exist_ok=True)
                            except Exception:
                                pass
                            self.f_out = open(self.f_name, "a")
                        if(self.f_out):
                            self.line = str(self.current_current_read) + "\t" + str(self.current_voltage) +"\t" + str(self.current_resistance) + "\n"
                            self.f_out.write(self.line)
                            self.f_out.close()
                            self.f_out = None

                            # progress and rate tracking on each sample
                            now = time.perf_counter()
                            if self.last_sample_time is not None:
                                dt = now - self.last_sample_time
                                if dt > 0:
                                    rate = 1.0 / dt
                                    self._rate_window.append(rate)
                                    self.sample_rate = sum(self._rate_window) / len(self._rate_window)
                                    if self.total_steps:
                                        remaining = max(0, self.total_steps - self.step_idx)
                                        self._finish_time = now + (remaining / self.sample_rate) if self.sample_rate else None
                            self.last_sample_time = now
                            self.step_idx += 1
                            if hasattr(self.ui, 'progressBar_process') and self.total_steps:
                                self.ui.progressBar_process.setMaximum(self.total_steps)
                                self.ui.progressBar_process.setValue(min(self.step_idx, self.total_steps))
                if (
                    self.current_increment > 0
                    and self.current_voltage >= self.max_voltage
                    and not self._max_voltage_dialog
                ):
                    self.handle_max_voltage()
                self.sample_ready = True
                #print("tutaj lala")
            self.zamok.unlock()
            #print(self.odpoved_portu)
                    
    def handle_update_label_odpoved_portu(self):
        self.ui.label_odpoved_portu.setText(self.odpoved_portu)

    def update_time_estimate(self):
        label = getattr(self.ui, 'label_time_remaining', None)
        if label is None:
            return
        # Show a planned estimate when idle; measured when running
        if not self.proces_on:
            secs = self.compute_planned_seconds()
            if secs is None:
                label.setText("Time remaining: ∞")
            else:
                label.setText(self._format_secs("Time remaining", secs))
            return
        now = time.perf_counter()
        if self._finish_time is not None:
            secs = max(0, int(self._finish_time - now + 0.999))
        else:
            if not self.sample_rate or not self.total_steps:
                label.setText("Time remaining: N/A")
                return
            remaining = max(0, self.total_steps - self.step_idx)
            secs = int((remaining / self.sample_rate) + 0.999)
        label.setText(self._format_secs("Time remaining", secs))

    def _format_secs(self, prefix: str, secs: int) -> str:
        if secs >= 3600:
            h = secs // 3600
            m = (secs % 3600) // 60
            s = secs % 60
            return f"{prefix}: {h}h {m:02d}m {s:02d}s"
        elif secs >= 60:
            m = secs // 60
            s = secs % 60
            return f"{prefix}: {m}m {s:02d}s"
        else:
            return f"{prefix}: {secs}s"

    def compute_planned_seconds(self) -> int | None:
        """Estimate duration based on UI parameters, even when idle.

        Assumes 1 mA per second ramp rate (timer_command = 1000 ms).
        """
        try:
            max_mA = int(self.ui.spinBox_hodnota_staly_prud.value())
            hold_s = int(self.ui.spinBox_doba_staly_prud.value())
            loops = int(self.ui.spinBox_loops.value()) if hasattr(self.ui, 'spinBox_loops') else 1
            reverse = bool(self.ui.checkBox_reverse.isChecked()) if hasattr(self.ui, 'checkBox_reverse') else False
            infinite = bool(self.ui.checkBox_infinite_loops.isChecked()) if hasattr(self.ui, 'checkBox_infinite_loops') else False
            step_mA = int(self.ui.spinBox_step_mA.value()) if hasattr(self.ui, 'spinBox_step_mA') else 1
        except Exception:
            return None
        if infinite:
            return None
        # steps up from 1 mA to max in increments of step_mA
        up_steps = max(0, math.ceil(max(0, max_mA - 1) / max(1, step_mA)))
        down_steps = up_steps if reverse else 0
        per_loop = up_steps + hold_s + down_steps
        return per_loop * max(1, loops)

    def update_planned_time_label(self):
        label = getattr(self.ui, 'label_time_remaining', None)
        if label is None:
            return
        secs = self.compute_planned_seconds()
        if secs is None:
            label.setText("Time remaining: N/A")
        else:
            label.setText(self._format_secs("Time remaining", secs))

    def update_file_name_from_preset(self):
        # Build file name based on naming preset
        if not hasattr(self.ui, 'comboBox_name_preset'):
            return
        preset = self.ui.comboBox_name_preset.currentText().strip().lower()
        if preset.startswith('current'):
            comp = getattr(self.ui, 'lineEdit_composition', None)
            wire = getattr(self.ui, 'lineEdit_microwire', None)
            sample = getattr(self.ui, 'lineEdit_sample', None)
            comp_s = comp.text().strip() if comp is not None else ''
            wire_s = wire.text().strip() if wire is not None else ''
            sample_s = sample.text().strip() if sample is not None else ''
            try:
                max_mA = int(self.ui.spinBox_hodnota_staly_prud.value())
            except Exception:
                max_mA = 0
            parts = [p for p in [comp_s, wire_s, sample_s, f"{max_mA}mA"] if p]
            base = " ".join(parts) if parts else "anneal_log"
            # Show only preset fields
            for name in ('lineEdit_composition','lineEdit_microwire','lineEdit_sample'):
                if hasattr(self.ui, name):
                    getattr(self.ui, name).setVisible(True)
            if hasattr(self.ui, 'label_custom_name'):
                self.ui.label_custom_name.setVisible(False)
            if hasattr(self.ui, 'lineEdit_custom_name'):
                self.ui.lineEdit_custom_name.setVisible(False)
        else:
            custom = getattr(self.ui, 'lineEdit_custom_name', None)
            base = custom.text().strip() if custom is not None and custom.text().strip() else 'anneal_log'
            # Show only custom name field
            for name in ('lineEdit_composition','lineEdit_microwire','lineEdit_sample'):
                if hasattr(self.ui, name):
                    getattr(self.ui, name).setVisible(False)
            if hasattr(self.ui, 'label_custom_name'):
                self.ui.label_custom_name.setVisible(True)
            if hasattr(self.ui, 'lineEdit_custom_name'):
                self.ui.lineEdit_custom_name.setVisible(True)
        if hasattr(self.ui, 'lineEdit_log_file'):
            self.ui.lineEdit_log_file.setText(base)
        self.store_name_preset()

    def store_name_preset(self):
        s = self.settings
        s.setValue("preset", self.ui.comboBox_name_preset.currentIndex())
        s.setValue("composition", self.ui.lineEdit_composition.text())
        s.setValue("microwire", self.ui.lineEdit_microwire.text())
        s.setValue("sample", self.ui.lineEdit_sample.text())
        s.setValue("custom_name", self.ui.lineEdit_custom_name.text())

    def restore_name_preset(self):
        try:
            self.ui.comboBox_name_preset.blockSignals(True)
            self.ui.lineEdit_composition.blockSignals(True)
            self.ui.lineEdit_microwire.blockSignals(True)
            self.ui.lineEdit_sample.blockSignals(True)
            self.ui.lineEdit_custom_name.blockSignals(True)
        except Exception:
            pass
        s = self.settings
        self.ui.comboBox_name_preset.setCurrentIndex(int(s.value("preset", DEFAULT_PRESET["preset"])))
        self.ui.lineEdit_composition.setText(s.value("composition", DEFAULT_PRESET["composition"]))
        self.ui.lineEdit_microwire.setText(s.value("microwire", DEFAULT_PRESET["microwire"]))
        self.ui.lineEdit_sample.setText(s.value("sample", DEFAULT_PRESET["sample"]))
        self.ui.lineEdit_custom_name.setText(s.value("custom_name", DEFAULT_PRESET["custom_name"]))
        try:
            self.ui.comboBox_name_preset.blockSignals(False)
            self.ui.lineEdit_composition.blockSignals(False)
            self.ui.lineEdit_microwire.blockSignals(False)
            self.ui.lineEdit_sample.blockSignals(False)
            self.ui.lineEdit_custom_name.blockSignals(False)
        except Exception:
            pass

    def reset_name_preset(self):
        self.settings.clear()
        self.restore_name_preset()
        self.update_file_name_from_preset()

    def open_log_dir(self) -> None:
        try:
            path = self.ui.lineEdit_log_dir.text().strip()
            if not path:
                return
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        except Exception:
            pass

    def handle_step_changed(self):
        try:
            self.current_step_mA = int(self.ui.spinBox_step_mA.value())
        except Exception:
            self.current_step_mA = 1
        self.current_step_A = self.current_step_mA/1000.0
        self.update_planned_time_label()

    def handle_pushButton_posli_prikaz_portu_clicked(self):
        self.prikaz_portu = self.ui.lineEdit_prikaz_portu.text() + "\n"
        self.send_serial_command()
        
    def send_serial_command(self):
        self.ser_mcu.write(bytes(self.prikaz_portu, encoding='ascii'))
        self.ui.label_prikaz_portu.setText(self.prikaz_portu)
        # print('Poslaný príkaz: ' + self.prikaz_portu)
        
    def handle_radioButton_raw_VCP_clicked(self):
        self.modus_operandi = 0
        self.ui.frame_nastavenia_procesu.setEnabled(False)

    def handle_radioButton_manualne_zihanie_clicked(self):
        self.modus_operandi = 1
        self.ui.frame_nastavenia_procesu.setEnabled(True)
        self.ui.spinBox_hodnota_staly_prud.setEnabled(False)
        self.ui.spinBox_doba_staly_prud.setEnabled(False)
        self.ui.pushButton_start_stop_drzania_prudu.setEnabled(True)

    def handle_radioButton_automatizovane_zihanie_clicked(self):
        self.modus_operandi = 2
        self.ui.frame_nastavenia_procesu.setEnabled(True)
        self.ui.spinBox_hodnota_staly_prud.setEnabled(True)
        self.ui.spinBox_doba_staly_prud.setEnabled(True)
        self.ui.pushButton_start_stop_drzania_prudu.setEnabled(False)

    def handle_mode_changed(self, index: int) -> None:
        if index == 0:
            self.handle_radioButton_raw_VCP_clicked()
        elif index == 1:
            self.handle_radioButton_manualne_zihanie_clicked()
        else:
            self.handle_radioButton_automatizovane_zihanie_clicked()
        
    def handle_spinBox_hodnota_staly_prud_valueChanged(self):
        self.hodnota_staly_prud = self.ui.spinBox_hodnota_staly_prud.value()
        try:
            self.settings.setValue("max_current", self.hodnota_staly_prud)
        except Exception:
            pass
        
    def handle_spinBox_doba_staly_prud_valueChanged(self):
        self.doba_staly_prud = self.ui.spinBox_doba_staly_prud.value()
        # print("Doba staly prud: ", self.doba_staly_prud)
        
    def handle_pushButton_start_stop_drzania_prudu_clicked(self):
        if(self.prud_timer_on == False):
            self.current_increment = 0.000
            self.ciara_color="g"
            self.sekundy = 0
            self.resistance_at_hold_current = self.current_resistance
            self.ui.label_resistance_at_hold_current.setText("{:.1f}".format(self.resistance_at_hold_current))
            self.timer_prud.start(1000)
            self.prud_timer_on = True
            self.ui.pushButton_start_stop_drzania_prudu.setText("Stop prúdu teraz!")
        else:
            self.timer_prud.stop()
            self.current_increment = -self.current_step_A
            self.ciara_color="b"
            self.prud_timer_on = False
            self.ui.pushButton_start_stop_drzania_prudu.setText("Držať prúd teraz!")

    def handle_update_lcdNumber_uplynute_sekundy(self):
        self.sekundy += 1
        self.resistance_percento_from_hold = self.current_resistance/self.resistance_at_hold_current*100
        self.ui.label_resistance_percento_from_hold.setText("{:.1f}".format(self.resistance_percento_from_hold))
        self.ui.lcdNumber_uplynute_sekundy.display(self.sekundy)
    
    def handle_pushButton_spusti_proces_clicked(self):
        if(self.proces_on == False):
            self.proces_on = True
            self.sekundy = 0
            self._max_voltage_dialog = False
            self._contact_lost = False
            self._zero_current_count = 0
            try:
                self._process_start_time = time.monotonic()
            except Exception:
                self._process_start_time = None
            self._nonzero_current_seen = False
            self._last_nonzero_current_time = None
            self.ui.frame_modus_operandi.setEnabled(False)
            self._set_process_controls_enabled(False)
            if hasattr(self.ui, 'pushButton_reverse_now'):
                self.ui.pushButton_reverse_now.setEnabled(True)
            self.force_stop_at_zero = False
            self.command_number = 0
            self.vzorka_N = 0
            self.prev_value_x = None
            self.prev_value_y = None
            self.first_sample = True
            # print("Proces bezi")
            self.ui.pushButton_spusti_proces.setText("Stop annealing process")
            if(self.modus_operandi == 0):
                # print("Spusteny raw VCP mod")
                pass
                
            elif(self.modus_operandi == 1):
                # Prepare output file with overwrite prompt
                if not self.prepare_output_file():
                    self.proces_on = False
                    self._process_start_time = None
                    self.ui.pushButton_spusti_proces.setText("Start annealing process")
                    return
                if hasattr(self.ui, 'progressBar_process'):
                    self.ui.progressBar_process.setMaximum(0)
                    self.ui.progressBar_process.setValue(0)
                if hasattr(self.ui, 'label_time_remaining'):
                    self.ui.label_time_remaining.setText("Time remaining: N/A")
                self.current_increment = self.current_step_A
                self.current_current_set = 0.001
                self.ui.label_set_current.display("{:.1f}".format(self.current_current_set*1000))
                self.temp_resistance_maximum = 0
                self.current_voltage = 0
                self.current_resistance = 0
                self.ui.lcdNumber_aktualny_prud_mA.display("0")
                if hasattr(self.ui, 'label_live_voltage'):
                    self.ui.label_live_voltage.display("0")
                self.ui.lcdNumber_uplynute_sekundy.display(0)
                self.ui.label_resistance_at_hold_current.setText("0")
                self.ui.label_resistance_percento_from_hold.setText("0")
                self.ciara_marker="o"
                self.ciara_linestyle="-"
                self.ciara_color="r"
                self.init_graph_window()
                self.send_init_commands()
                # Immediately request the first sample instead of waiting
                # for the one‑second timer interval to elapse.  This avoids
                # an unnecessary pause after the user presses *Start*.
                self.handle_send_new_command()
                self.timer_command.start(1000)
                # print("Spusteny mod manualneho zihania")
                
            elif(self.modus_operandi == 2):
                # Prepare output file with overwrite prompt
                if not self.prepare_output_file():
                    self.proces_on = False
                    self.ui.pushButton_spusti_proces.setText("Start annealing process")
                    return
                self.current_increment = self.current_step_A
                self.current_current_set = 0.001
                self.ui.label_set_current.display("{:.1f}".format(self.current_current_set*1000))
                self.temp_resistance_maximum = 0
                self.current_voltage = 0
                self.current_resistance = 0
                self.ui.lcdNumber_aktualny_prud_mA.display("0")
                if hasattr(self.ui, 'label_live_voltage'):
                    self.ui.label_live_voltage.display("0")
                # reverse + loop configuration
                self.reverse_enabled = getattr(self.ui, 'checkBox_reverse', None) is not None and self.ui.checkBox_reverse.isChecked()
                self.loop_target = self.ui.spinBox_loops.value() if hasattr(self.ui, 'spinBox_loops') else 1
                self.infinite_loops = bool(self.ui.checkBox_infinite_loops.isChecked()) if hasattr(self.ui, 'checkBox_infinite_loops') else False
                self.loop_idx = 0
                # progress plan
                step_mA = self.current_step_mA if hasattr(self, 'current_step_mA') else 1
                up_steps = max(0, math.ceil(max(0, int(self.ui.spinBox_hodnota_staly_prud.value()) - 1) / max(1, step_mA)))
                hold_steps = int(self.ui.spinBox_doba_staly_prud.value())
                down_steps = up_steps if self.reverse_enabled else 0
                per_loop = up_steps + hold_steps + down_steps
                if self.infinite_loops:
                    self.total_steps = 0
                else:
                    self.total_steps = max(0, per_loop * int(self.loop_target or 1))
                self.step_idx = 0
                if hasattr(self.ui, 'progressBar_process'):
                    if self.total_steps:
                        self.ui.progressBar_process.setMaximum(self.total_steps)
                        self.ui.progressBar_process.setValue(0)
                    else:
                        self.ui.progressBar_process.setMaximum(0)
                self.ui.lcdNumber_uplynute_sekundy.display(0)
                self.ui.label_resistance_at_hold_current.setText("0")
                self.ui.label_resistance_percento_from_hold.setText("0")
                self.ciara_marker="o"
                self.ciara_linestyle="-"
                self.ciara_color="r"
                self.init_graph_window()
                self.send_init_commands()
                # Kick off the first acquisition immediately so the
                # measurement starts without a one‑second delay.
                self.handle_send_new_command()
                self.timer_command.start(1000)
                # print("Spusteny mod automatizovaneho zihania")
                
            else:
                pass
        else:
            self.stop_annealing()
    def handle_pushButton_reverse_now_clicked(self):
        """Immediately ramp current down toward zero."""
        if not self.proces_on:
            return
        try:
            self.timer_prud.stop()
        except Exception:
            pass
        self.prud_timer_on = False
        self.current_increment = -abs(self.current_step_A)
        self.ciara_color = "b"
        self.force_stop_at_zero = True

    def _set_process_controls_enabled(self, enabled: bool) -> None:
        if not hasattr(self.ui, 'groupBox_nastavenia_procesu'):
            return
        keep = {self.ui.pushButton_spusti_proces}
        if hasattr(self.ui, 'pushButton_reverse_now'):
            keep.add(self.ui.pushButton_reverse_now)
        if hasattr(self.ui, 'progressBar_process'):
            keep.add(self.ui.progressBar_process)
        if hasattr(self.ui, 'label_time_remaining'):
            keep.add(self.ui.label_time_remaining)
        if hasattr(self.ui, 'groupBox_aktualne_hodnoty'):
            keep.add(self.ui.groupBox_aktualne_hodnoty)
        for child in self.ui.groupBox_nastavenia_procesu.findChildren(QtWidgets.QWidget):
            if child in keep:
                continue
            child.setEnabled(enabled)

    def stop_annealing(self):
        """Abort the annealing run and power down the supply safely."""
        self.proces_on = False
        self.wait = False  # break any pending delays
        self.force_stop_at_zero = False
        self._contact_lost = False
        self._zero_current_count = 0
        self._nonzero_current_seen = False
        self._process_start_time = None
        self._last_nonzero_current_time = None
        try:
            self.timer_command.stop()
            self.timer_prud.stop()
        except Exception:
            pass
        self.prud_timer_on = False
        if self.f_out:
            self.f_out.close()
            self.f_out = None
        if self.modus_operandi == 1:
            self.ui.pushButton_start_stop_drzania_prudu.setText("Držať prúd teraz!")
        # Immediately ramp the supply to zero before running the shutdown sequence
        try:
            for cmd in ("INST:NSEL 3\n", "CURR 0.000\n", "OUTP OFF\n"):
                self.prikaz_portu = cmd
                self.send_serial_command()
                self.simple_delay(100)
        except Exception:
            pass
        try:
            self.send_safe_end_commands()
        except Exception:
            pass
        self.ui.pushButton_spusti_proces.setText("Start annealing process")
        self._set_process_controls_enabled(True)
        if hasattr(self.ui, 'pushButton_reverse_now'):
            self.ui.pushButton_reverse_now.setEnabled(False)
        self.ui.frame_modus_operandi.setEnabled(True)
        self.ui.label_set_current.display("0")
        self._max_voltage_dialog = False
        
    def handle_send_new_command(self):
        if not self.proces_on:
            return

        #manual zihanie
        if self.modus_operandi == 1:
            # print("Prikaz manualneho zihania cislo ", self.command_number)
            self.sample_ready = False
            self.napatie = True
            self.prikaz_portu = "MEAS:VOLT?\n"
            #pre simulator tento prikaz, inak pre zdroj ten prvy
            #self.prikaz_portu = "*RRAWO\n"
            self.send_serial_command()
            # wait boundedly, allow stopping
            if not self.wait_for_sample(3000):
                if not self.proces_on:
                    return
                self.warn_no_response_and_abort()
                return
                
            self.sample_ready = False
            self.napatie = False
            self.prikaz_portu = "MEAS:CURR?\n"
            #pre simulator tento prikaz, inak pre zdroj ten prvy
            #self.prikaz_portu = "*RRAWO\n"
            self.send_serial_command()
            if not self.wait_for_sample(3000):
                if not self.proces_on:
                    return
                self.warn_no_response_and_abort()
                return
                
            self.curr_value_x = self.current_current_read*1000
            self.curr_value_y = self.current_resistance
            self.ui.lcdNumber_aktualny_prud_mA.display("{:.1f}".format(self.curr_value_x))
            if hasattr(self.ui, 'label_live_voltage'):
                self.ui.label_live_voltage.display("{:.2f}".format(self.current_voltage))

            #a striggrujeme indikaciu novej vzorky kvoli sekvencovaniu prikazov
            if self.first_sample:
                self.first_sample = False
                self.prev_value_x = None
                self.prev_value_y = None
            else:
                self.vzorka_N +=1
                if self.prev_value_x is not None:
                    #pridame novu vzroku do grafu
                    self.line1 = Line2D([self.prev_value_x, self.curr_value_x], [self.prev_value_y, self.curr_value_y], color=self.ciara_color, marker=self.ciara_marker, linestyle=self.ciara_linestyle)
                    self.ax1.add_line(self.line1)

                    self.line2 = Line2D([self.vzorka_N-1, self.vzorka_N], [self.prev_value_y, self.curr_value_y], color=self.ciara_color, marker=self.ciara_marker, linestyle=self.ciara_linestyle)
                    self.ax2.add_line(self.line2)

                    # Voliteľné: dynamické prispôsobenie rozsahov
                    self.ax1.relim()
                    self.ax1.autoscale_view()
                    self.ax2.relim()
                    self.ax2.autoscale_view()

                    self.fig.canvas.draw()
                    self.fig.canvas.flush_events()

                self.prev_value_x = self.curr_value_x
                self.prev_value_y = self.curr_value_y


            #iteracia prudu
            self.current_current_set += self.current_increment
            self.ui.label_set_current.display("{:.1f}".format(self.current_current_set*1000))

            #vypnutie ako pri tlacidle
            if(self.current_current_set < 0.001):
                self.handle_pushButton_spusti_proces_clicked()

            if not self.proces_on:
                return
            self.prikaz_portu = f"CURR {self.current_current_set:.3f}\n"
            self.send_serial_command()
           
            
                
            
            
        elif self.modus_operandi == 2:
            # print("Prikaz automatizovaneho zihania cislo ", self.command_number)
            self.sample_ready = False
            self.napatie = True
            self.prikaz_portu = "MEAS:VOLT?\n"
            #pre simulator tento prikaz, inak pre zdroj ten prvy
            #self.prikaz_portu = "*RRAWO\n"
            self.send_serial_command()
            if not self.wait_for_sample(3000):
                if not self.proces_on:
                    return
                self.warn_no_response_and_abort()
                return
                
            self.sample_ready = False
            self.napatie = False
            self.prikaz_portu = "MEAS:CURR?\n"
            #pre simulator tento prikaz, inak pre zdroj ten prvy
            #self.prikaz_portu = "*RRAWO\n"
            self.send_serial_command()
            if not self.wait_for_sample(3000):
                if not self.proces_on:
                    return
                self.warn_no_response_and_abort()
                return
                
            self.curr_value_x = self.current_current_read*1000
            self.curr_value_y = self.current_resistance
            self.ui.lcdNumber_aktualny_prud_mA.display("{:.1f}".format(self.curr_value_x))
            if hasattr(self.ui, 'label_live_voltage'):
                self.ui.label_live_voltage.display("{:.2f}".format(self.current_voltage))

            #a striggrujeme indikaciu novej vzorky kvoli sekvencovaniu prikazov
            if self.first_sample:
                self.first_sample = False
                self.prev_value_x = None
                self.prev_value_y = None
            else:
                self.vzorka_N +=1
                if self.prev_value_x is not None:
                    #pridame novu vzroku do grafu
                    self.line1 = Line2D([self.prev_value_x, self.curr_value_x], [self.prev_value_y, self.curr_value_y], color=self.ciara_color, marker=self.ciara_marker, linestyle=self.ciara_linestyle)
                    self.ax1.add_line(self.line1)

                    self.line2 = Line2D([self.vzorka_N-1, self.vzorka_N], [self.prev_value_y, self.curr_value_y], color=self.ciara_color, marker=self.ciara_marker, linestyle=self.ciara_linestyle)
                    self.ax2.add_line(self.line2)

                    # Voliteľné: dynamické prispôsobenie rozsahov
                    self.ax1.relim()
                    self.ax1.autoscale_view()
                    self.ax2.relim()
                    self.ax2.autoscale_view()

                    self.fig.canvas.draw()
                    self.fig.canvas.flush_events()

                self.prev_value_x = self.curr_value_x
                self.prev_value_y = self.curr_value_y
            
            
                      
            #zholdujeme prud ako keby tlacidlom
            if (self.current_current_set >= (self.hodnota_staly_prud/1000.0)) and (self.current_increment > 0):
                if(self.prud_timer_on == False):
                    self.current_increment = 0.000
                    self.ciara_color="g"
                    self.sekundy = 0
                    self.resistance_at_hold_current = self.current_resistance
                    self.ui.label_resistance_at_hold_current.setText("{:.1f}".format(self.resistance_at_hold_current))
                    self.timer_prud.start(1000)
                    self.prud_timer_on = True
            
            #iteracia prudu
            self.current_current_set += self.current_increment
            self.ui.label_set_current.display("{:.1f}".format(self.current_current_set*1000))

            # end of hold: either reverse (if enabled) or stop
            if(self.prud_timer_on and (self.sekundy >= self.doba_staly_prud)):
                self.timer_prud.stop()
                self.prud_timer_on = False
                if getattr(self, 'reverse_enabled', False):
                    self.current_increment = -self.current_step_A
                    self.ciara_color = "b"
                else:
                    self.handle_pushButton_spusti_proces_clicked()

            if not self.proces_on:
                return
            self.prikaz_portu = f"CURR {self.current_current_set:.3f}\n"
            self.send_serial_command()
            # completed descending to zero? manage loops or stop
            if (self.current_increment < 0) and (self.current_current_set < self.current_step_A):
                if getattr(self, 'force_stop_at_zero', False) or not getattr(self, 'reverse_enabled', False):
                    self.handle_pushButton_spusti_proces_clicked()
                else:
                    self.loop_idx = int(getattr(self, 'loop_idx', 0)) + 1
                    if self.infinite_loops or (self.loop_idx < int(getattr(self, 'loop_target', 1))):
                        # prepare next loop
                        self.current_increment = self.current_step_A
                        self.current_current_set = 0.001
                        self.ciara_color = "r"
                        self.direction_ascending = True
                        self.sekundy = 0
                    else:
                        self.handle_pushButton_spusti_proces_clicked()

        else:
            pass
        
        self.command_number +=1
        
        
        

    def send_safe_end_commands(self):
        # print("teraz posielam univerzalnu zostavu prikazov pri ukonceni")
        for i in range(0, len(self.commands_safe_end)):
            self.prikaz_portu = self.commands_safe_end[i]
            self.send_serial_command()
            self.simple_delay(200)
            

    def send_init_commands(self):
        # print("teraz posielam univerzalnu zostavu prikazov pri spusteni")
        for cmd in self.commands_init:
            if not self.proces_on:
                break
            self.prikaz_portu = cmd
            self.send_serial_command()
            # The original implementation paused for a full second between
            # initialisation commands, which caused a noticeable start-up
            # delay.  A brief 200 ms gap gives the supply time to process
            # each command while keeping the UI responsive.
            self.simple_delay(200)
            
    def simple_delay(self, delay_ms):
        self.wait = True
        QtCore.QTimer.singleShot(delay_ms, lambda: setattr(self, 'wait', False))
        
        while self.wait:
            QtWidgets.QApplication.processEvents()
        
    def wait_for_sample(self, timeout_ms: int) -> bool:
        """Spin the event loop until a sample arrives, stop requested, or timeout."""
        self.wait = False
        elapsed = 0
        step = 20
        retries = 0
        limit = max(step, int(timeout_ms))
        while self.proces_on and not self.sample_ready:
            self.simple_delay(step)
            if self.sample_ready or not self.proces_on:
                break
            elapsed += step
            if elapsed >= limit:
                recent = False
                try:
                    now = time.monotonic()
                    if self._last_serial_rx is not None and (now - self._last_serial_rx) < 0.75:
                        recent = True
                except Exception:
                    recent = False
                if recent:
                    elapsed = 0
                    continue
                if retries == 0:
                    retries = 1
                    elapsed = 0
                    continue
                break
        ok = bool(self.sample_ready)
        if not ok:
            self._serial_quiet_failures += 1
        else:
            self._serial_quiet_failures = 0
        self.sample_ready = False
        return ok

    def warn_no_response_and_abort(self) -> None:
        QtWidgets.QMessageBox.warning(
            self,
            "No response",
            "No response from power supply. Is it turned on? Aborting the process.",
        )
        if self.proces_on:
            self.stop_annealing()
        self._serial_quiet_failures = 0

    def handle_lineEdit_log_subor_text_changed(self):
        # Sync f_name from separate directory + file name controls
        try:
            self.f_name = self.build_log_path()
        except Exception:
            self.f_name = self.ui.lineEdit_log_subor.text()
        # print("Zaznam subor:", self.f_name)

    def init_live_values(self) -> None:
        box = getattr(self.ui, "groupBox_aktualne_hodnoty", None)
        if box is None:
            return
        for child in box.findChildren(QtWidgets.QWidget):
            child.deleteLater()
        old_layout = box.layout()
        if old_layout is not None:
            QtWidgets.QWidget().setLayout(old_layout)
        layout = QtWidgets.QFormLayout(box)
        layout.setContentsMargins(6, 6, 6, 6)
        self.label_live_current = QtWidgets.QLabel("0")
        self.label_live_set = QtWidgets.QLabel("0")
        self.label_live_voltage = QtWidgets.QLabel("0")
        for lbl in (self.label_live_current, self.label_live_set, self.label_live_voltage):
            lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addRow("Set current (mA)", self.label_live_set)
        layout.addRow("Current (mA)", self.label_live_current)
        layout.addRow("Voltage (V)", self.label_live_voltage)
        # Alias old names for compatibility
        self.ui.lcdNumber_aktualny_prud_mA = self.label_live_current
        self.ui.label_set_current = self.label_live_set
        self.ui.label_live_voltage = self.label_live_voltage
        self.ui.lcdNumber_aktualny_prud_mA.display = self.label_live_current.setText
        self.ui.label_set_current.display = self.label_live_set.setText
        self.ui.label_live_voltage.display = self.label_live_voltage.setText

    def handle_max_voltage(self) -> None:
        self._max_voltage_dialog = True
        self.current_increment = 0
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Voltage limit reached")
        msg.setText("Power supply reached 30 V. What do you want to do?")
        hold_btn = msg.addButton("Hold current", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        reverse_btn = msg.addButton("Reverse to zero", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        stop_btn = msg.addButton("Stop measurement", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is reverse_btn:
            self.current_increment = -abs(self.current_step_A)
            self.ciara_color = "b"
            self.force_stop_at_zero = True
            self.direction_ascending = False
        elif clicked is stop_btn:
            self.handle_pushButton_spusti_proces_clicked()
        else:
            pass
    
    def init_graph_window(self):
        """
        self.x_data_ax1 = []
        self.y_data_ax1 = []
        self.x_data_ax2 = []
        self.y_data_ax2 = []
        self.n_counter = 0
        """
    
        # Create an embedded Matplotlib figure on the right panel
        if hasattr(self.ui, 'plot_container'):
            container = self.ui.plot_container
            layout = container.layout()
            if layout is None:
                layout = QtWidgets.QVBoxLayout(container)
                # Zero margins to eliminate bright edge lines and maximize canvas area
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(0)
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()

            # Align matplotlib colors with Qt palette for a native look
            try:
                app = QtWidgets.QApplication.instance()
                scheme = app.styleHints().colorScheme()
                palette = app.palette()
                win = palette.color(QtGui.QPalette.ColorRole.Window)
                base = palette.color(QtGui.QPalette.ColorRole.Base)
                text = palette.color(QtGui.QPalette.ColorRole.Text)
                win_rgb = (win.redF(), win.greenF(), win.blueF())
                base_rgb = (base.redF(), base.greenF(), base.blueF())
                text_rgb = (text.redF(), text.greenF(), text.blueF())
            except Exception:
                scheme = QtCore.Qt.ColorScheme.Light
                win_rgb = (1, 1, 1)
                base_rgb = (1, 1, 1)
                text_rgb = (0, 0, 0)

            self.fig = Figure(facecolor=win_rgb, constrained_layout=True)
            self.canvas = FigureCanvas(self.fig) if FigureCanvas is not None else None
            try:
                _title = format_annealing_title(Path(self.f_name).stem)
            except Exception:
                _title = format_annealing_title(self.f_name)
            self.fig.suptitle(_title, color=text_rgb)
            if NavigationToolbar is not None and self.canvas is not None:
                self.toolbar = NavigationToolbar(self.canvas, container)
                layout.addWidget(self.toolbar)
            if self.canvas is not None:
                layout.addWidget(self.canvas, stretch=1)

            self.ax1 = self.fig.add_subplot(211)
            self.ax1.set_facecolor(base_rgb)
            self.ax1.set_xlabel("Current [mA]")
            self.ax1.set_ylabel("Resistance [Ohm]")
            self.ax1.grid(True, color=(0.35,0.35,0.35,0.5) if scheme == QtCore.Qt.ColorScheme.Dark else (0.8,0.8,0.8,0.8))
            for spine in self.ax1.spines.values():
                spine.set_color(text_rgb)
            self.ax1.tick_params(colors=text_rgb)
            self.ax1.xaxis.label.set_color(text_rgb)
            self.ax1.yaxis.label.set_color(text_rgb)

            self.line1 = Line2D([], [], color=self.ciara_color, marker=self.ciara_marker, linestyle=self.ciara_linestyle)
            self.ax1.add_line(self.line1)

            self.ax2 = self.fig.add_subplot(212)
            self.ax2.set_facecolor(base_rgb)
            self.ax2.set_xlabel("N [-]")
            self.ax2.set_ylabel("Resistance [Ohm]")
            self.ax2.grid(True, color=(0.35,0.35,0.35,0.5) if scheme == QtCore.Qt.ColorScheme.Dark else (0.8,0.8,0.8,0.8))
            for spine in self.ax2.spines.values():
                spine.set_color(text_rgb)
            self.ax2.tick_params(colors=text_rgb)
            self.ax2.xaxis.label.set_color(text_rgb)
            self.ax2.yaxis.label.set_color(text_rgb)
            self.line2 = Line2D([], [], color=self.ciara_color, marker=self.ciara_marker, linestyle=self.ciara_linestyle)
            self.ax2.add_line(self.line2)
            # Let Matplotlib compute proper spacing; avoid text overlap
            if self.canvas is not None:
                self.canvas.draw()
        else:
            # Fallback to separate window
            self.fig = plt.figure(constrained_layout=True)
            self.ax1 = self.fig.add_subplot(211)
            self.ax1.set_xlabel("Current [mA]")
            self.ax1.set_ylabel("Resistance [Ohm]")
            self.ax1.grid(True)
            self.line1 = Line2D([], [], color=self.ciara_color, marker=self.ciara_marker, linestyle=self.ciara_linestyle)
            self.ax1.add_line(self.line1)
            self.ax2 = self.fig.add_subplot(212)
            self.ax2.set_xlabel("N [-]")
            self.ax2.set_ylabel("Resistance [Ohm]")
            self.ax2.grid(True)
            self.line2 = Line2D([], [], color=self.ciara_color, marker=self.ciara_marker, linestyle=self.ciara_linestyle)
            self.ax2.add_line(self.line2)
            plt.ion()
            show_plots()
        
        
    def handle_pushButton_select_filename_clicked(self):
        self.f_name, _ = QFileDialog.getSaveFileName(
            self,
            "Uložiť súbor",
            "data",
            "Textové súbory (*.txt);;Všetky súbory (*)"
        )

        if self.f_name:
            if not self.f_name.endswith(".txt"):
                self.f_name += ".txt"
            
            self.ui.lineEdit_log_subor.setText(self.f_name)


    def handle_select_filename_en(self):
        # Choose full path via Save dialog and update new fields when available
        start_dir = self.ui.lineEdit_log_dir.text() if hasattr(self.ui, 'lineEdit_log_dir') else DEFAULT_LOG_DIR
        fpath, _ = QFileDialog.getSaveFileName(
            self,
            "Save file",
            start_dir,
            "Text files (*.txt);;All files (*)"
        )

        if fpath:
            if not fpath.endswith(".txt"):
                fpath += ".txt"
            d = os.path.dirname(fpath)
            b = os.path.splitext(os.path.basename(fpath))[0]
            if hasattr(self.ui, 'lineEdit_log_dir'):
                self.ui.lineEdit_log_dir.setText(d)
            if hasattr(self.ui, 'lineEdit_log_file'):
                self.ui.lineEdit_log_file.setText(b)
            self.ui.lineEdit_log_subor.setText(fpath)
            self.settings.setValue("log_dir", d)
            self.settings.setValue("log_file", b)

    def handle_browse_log_dir(self):
        start_dir = self.ui.lineEdit_log_dir.text() if hasattr(self.ui, 'lineEdit_log_dir') else DEFAULT_LOG_DIR
        new_dir = QFileDialog.getExistingDirectory(self, "Select log directory", start_dir)
        if new_dir and hasattr(self.ui, 'lineEdit_log_dir'):
            self.ui.lineEdit_log_dir.setText(new_dir)
            self.settings.setValue("log_dir", new_dir)

    def handle_browse_full_file(self):
        # Unified handler to select full path then split into directory + base name
        start_dir = self.ui.lineEdit_log_dir.text() if hasattr(self.ui, 'lineEdit_log_dir') else DEFAULT_LOG_DIR
        fpath, _ = QFileDialog.getSaveFileName(
            self,
            "Save file",
            start_dir,
            "Text files (*.txt);;All files (*)"
        )
        if fpath:
            if not fpath.endswith(".txt"):
                fpath += ".txt"
            d = os.path.dirname(fpath)
            b = os.path.splitext(os.path.basename(fpath))[0]
            if hasattr(self.ui, 'lineEdit_log_dir'):
                self.ui.lineEdit_log_dir.setText(d)
            if hasattr(self.ui, 'lineEdit_log_file'):
                self.ui.lineEdit_log_file.setText(b)
            self.ui.lineEdit_log_subor.setText(fpath)
            self.settings.setValue("log_dir", d)
            self.settings.setValue("log_file", b)

    def sync_full_log_path(self):
        # Update hidden full-path edit and internal f_name
        full = self.build_log_path()
        if hasattr(self.ui, 'lineEdit_log_subor'):
            self.ui.lineEdit_log_subor.setText(full)
        self.f_name = full
        try:
            d = self.ui.lineEdit_log_dir.text().strip()
            b = self.ui.lineEdit_log_file.text().strip()
            if d:
                self.settings.setValue("log_dir", d)
            if b:
                self.settings.setValue("log_file", b)
        except Exception:
            pass

    def build_log_path(self) -> str:
        try:
            d = self.ui.lineEdit_log_dir.text().strip()
            b = self.ui.lineEdit_log_file.text().strip()
            if not b:
                b = "anneal_log"
            if d:
                os.makedirs(d, exist_ok=True)
                return os.path.join(d, f"{b}.txt")
        except Exception:
            pass
        # Fallback to legacy full-path field if present
        try:
            t = self.ui.lineEdit_log_subor.text().strip()
            if t:
                return t
        except Exception:
            pass
        return os.path.join(DEFAULT_LOG_DIR, "anneal_log.txt")

    def prepare_output_file(self) -> bool:
        """Create or prepare the output file, prompting if it exists.

        Returns True if ready to proceed, False if the user canceled.
        """
        path = self.build_log_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass

        mode = "w"
        if os.path.exists(path):
            msg = QtWidgets.QMessageBox(self)
            msg.setWindowTitle("File exists")
            msg.setIcon(QtWidgets.QMessageBox.Icon.Question)
            base = os.path.basename(path)
            msg.setText(f"'{base}' already exists.")
            msg.setInformativeText("Choose an action:")
            replace_btn = msg.addButton("Replace", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
            continue_btn = msg.addButton("Continue", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = msg.addButton("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is cancel_btn:
                return False
            elif clicked is continue_btn:
                mode = "a"
            else:
                mode = "w"
        try:
            with open(path, mode):
                pass
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self, "Error", f"Failed to open {path}: {exc}"
            )
            return False

        self.f_name = path
        # subsequent writes will append
        return True

    def populate_ports(self):
        if hasattr(self.ui, 'comboBox_port'):
            self.ui.comboBox_port.clear()
            # 1) Normal OS-reported ports
            seen: set[str] = set()
            for info in QSerialPortInfo.availablePorts():
                sysloc = info.systemLocation() if hasattr(info, 'systemLocation') else info.portName()
                name = info.portName()
                label = name
                try:
                    if info.description():
                        label += f" - {info.description()}"
                except Exception:
                    pass
                self.ui.comboBox_port.addItem(label, userData=(sysloc or name))
                seen.add(sysloc or name)
            # 2) Extra virtual symlinks (macOS/Linux): /dev/cu.ttyV*
            try:
                import platform
                from glob import glob
                if platform.system() in {"Darwin", "Linux"}:
                    extras = sorted(set(glob("/dev/cu.ttyV*") + glob("/dev/ttyV*") + glob(str(Path.cwd()/"ttyV*"))))
                    for path in extras:
                        rp = os.path.realpath(path)
                        name = os.path.basename(rp) if rp.startswith('/dev/') else os.path.basename(path)
                        label = f"{os.path.basename(path)} - Virtual pair"
                        if name not in seen:
                            self.ui.comboBox_port.insertItem(0, label, userData=name)
                            seen.add(name)
            except Exception:
                pass
            if self.ui.comboBox_port.count() > 0:
                self.port_name = self.ui.comboBox_port.currentData()

    def closeEvent(self, event):
        if self.ser_mcu.isOpen():
            self.handle_pushButton_pripojPort_clicked()
            # self.ser_mcu.close()
            # print("Sériový port zatvorený")

        event.accept()

    # --- Overlay helpers
    def _setup_connect_overlay(self) -> None:
        scroll = getattr(self.ui, 'left_scroll', None)
        if scroll is None:
            self._overlay = None
            return
        ov = QtWidgets.QFrame(scroll.viewport())
        # Stronger blur/dim overlay
        ov.setStyleSheet("background: rgba(0,0,0,160);")
        layout = QtWidgets.QVBoxLayout(ov)
        layout.setContentsMargins(0, 0, 0, 0)
        msg = QtWidgets.QLabel("Connect COM port to enable settings")
        msg.setStyleSheet("color: white; font-size: 18px; font-weight: 700;")
        msg.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(msg)
        layout.addStretch(1)
        self._overlay = ov
        self._position_connect_overlay()
        ov.hide()

    def _position_connect_overlay(self) -> None:
        scroll = getattr(self.ui, 'left_scroll', None)
        ov = getattr(self, '_overlay', None)
        if scroll is None or ov is None:
            return
        try:
            serial_frame = getattr(self.ui, 'frame_zakladne_nastavenia_portu', None)
            if serial_frame is not None:
                pt = serial_frame.mapTo(scroll.viewport(), QtCore.QPoint(0, serial_frame.height()))
                y = pt.y() + 8
            else:
                y = 0
            vp = scroll.viewport().rect()
            ov.setGeometry(0, max(0, y), vp.width(), max(0, vp.height()-max(0, y)))
        except Exception:
            ov.setGeometry(scroll.viewport().rect())

    def _show_connect_overlay(self, show: bool) -> None:
        if getattr(self, '_overlay', None) is not None:
            try:
                self._overlay.setVisible(bool(show))
            except Exception:
                pass

    def resizeEvent(self, ev: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(ev)
        scroll = getattr(self.ui, 'left_scroll', None)
        if getattr(self, '_overlay', None) is not None and scroll is not None:
            try:
                self._position_connect_overlay()
            except Exception:
                pass


WINDOWS: list[QtWidgets.QWidget] = []


def main() -> QtWidgets.QWidget:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        owns_app = True

    ensure_app_theme(app)

    window = MainWindow()
    window.showMaximized()
    WINDOWS.append(window)

    if owns_app:
        sys.exit(app.exec())
    return window


if __name__ == "__main__":
    main()
    
