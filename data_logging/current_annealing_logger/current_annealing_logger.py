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
from pathlib import Path
from collections import deque
from PyQt6 import QtCore, QtWidgets, QtSerialPort
from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtSerialPort import QSerialPortInfo

from .ui_en import Ui_MainWindow
from plotting.utils import apply_system_theme

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

DEFAULT_LOG_DIR = str(Path.home() / "python_anneal_logs")


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
        # Provide sensible defaults for separate directory and file name
        if hasattr(self.ui, 'lineEdit_log_dir'):
            if not self.ui.lineEdit_log_dir.text().strip():
                self.ui.lineEdit_log_dir.setText(DEFAULT_LOG_DIR)
        if hasattr(self.ui, 'lineEdit_log_file'):
            if not self.ui.lineEdit_log_file.text().strip():
                self.ui.lineEdit_log_file.setText("anneal_log")
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
        self.direction_ascending = True
        self.sample_ready = False
        # Debug + progress/time tracking
        self.DEBUG = False
        self.sample_rate: float | None = None
        self._rate_window: deque[float] = deque(maxlen=200)
        self.last_sample_time: float | None = None
        self._finish_time: float | None = None
        self.step_idx = 0
        self.total_steps = 0
        
        # print("Číslo portu: COM" + str(self.cislo_portu))
        # print("Baudrate: " + str(self.baudrate))

        # Populate modern port list if available
        self.port_name = ""
        if hasattr(self.ui, 'comboBox_port'):
            try:
                self.populate_ports()
            except Exception:
                pass
        
        #prepojenie signalov a slotov
        self.ui.pushButton_pripojPort.clicked.connect(self.handle_pushButton_pripojPort_clicked)
        self.ui.spinBox_cislo_portu.valueChanged.connect(self.handle_spinBox_cislo_portu_valueChanged)
        self.ui.comboBox_baudrate.currentIndexChanged.connect(self.handle_comboBox_baudrate_currentIndexChanged)
        self.ui.pushButton_posli_prikaz_portu.clicked.connect(self.handle_pushButton_posli_prikaz_portu_clicked)
        
        self.ui.radioButton_raw_VCP.clicked.connect(self.handle_radioButton_raw_VCP_clicked)
        self.ui.radioButton_manualne_zihanie.clicked.connect(self.handle_radioButton_manualne_zihanie_clicked)
        self.ui.radioButton_automatizovane_zihanie.clicked.connect(self.handle_radioButton_automatizovane_zihanie_clicked)
        
        self.ui.spinBox_hodnota_staly_prud.valueChanged.connect(self.handle_spinBox_hodnota_staly_prud_valueChanged)
        self.ui.spinBox_doba_staly_prud.valueChanged.connect(self.handle_spinBox_doba_staly_prud_valueChanged)
        self.ui.pushButton_start_stop_drzania_prudu.clicked.connect(self.handle_pushButton_start_stop_drzania_prudu_clicked)
        
        self.ui.pushButton_spusti_proces.clicked.connect(self.handle_pushButton_spusti_proces_clicked)
        self.ui.lineEdit_log_subor.textChanged.connect(self.handle_lineEdit_log_subor_text_changed)
        self.ui.pushButton_select_filename.clicked.connect(self.handle_select_filename_en)
        # Also hook legacy browse button to new unified handler
        if hasattr(self.ui, 'pushButton_select_filename'):
            self.ui.pushButton_select_filename.clicked.connect(self.handle_browse_full_file)
        # New UI pieces: port dropdown and separate log directory/name
        if hasattr(self.ui, 'comboBox_port'):
            self.ui.comboBox_port.currentIndexChanged.connect(self.handle_comboBox_port_changed)
        if hasattr(self.ui, 'pushButton_refresh_ports'):
            self.ui.pushButton_refresh_ports.clicked.connect(self.populate_ports)
        if hasattr(self.ui, 'pushButton_browse_dir'):
            self.ui.pushButton_browse_dir.clicked.connect(self.handle_browse_log_dir)
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
        if hasattr(self.ui, 'checkBox_reverse'):
            self.ui.checkBox_reverse.toggled.connect(self.update_planned_time_label)
        if hasattr(self.ui, 'spinBox_loops'):
            self.ui.spinBox_loops.valueChanged.connect(self.update_planned_time_label)
        self.ui.spinBox_hodnota_staly_prud.valueChanged.connect(self.update_file_name_from_preset)
        self.ui.spinBox_hodnota_staly_prud.valueChanged.connect(self.update_planned_time_label)
        self.ui.spinBox_doba_staly_prud.valueChanged.connect(self.update_planned_time_label)

        # Initialize planned estimate and file name once
        try:
            self.update_file_name_from_preset()
            self.update_planned_time_label()
        except Exception:
            pass
        
        #nio a tu defaultne enable disable na prvky
        self.ui.frame_nastavenia_procesu.setEnabled(False)
        self.ui.frame_command_and_response.setEnabled(False)
        self.ui.frame_modus_operandi.setEnabled(False)
        
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
        self.prev_value_x = 0
        self.curr_value_x = 0
        self.prev_value_y = 0
        self.curr_value_y = 0
        
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
        
        
        
    # utilities
    def dbg(self, *args):
        if getattr(self, 'DEBUG', False):
            try:
                print(*args)
            except Exception:
                pass

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
            self.ser_mcu.setPortName(port_name)
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
                    self.ui.frame_zakladne_nastavenia_portu.setEnabled(False)
                    self.ui.frame_command_and_response.setEnabled(True)
                    self.handle_radioButton_raw_VCP_clicked()
            else:
                    # print('Pripojenie portu zlyhalo')
                    pass

        else:
            if self.proces_on == True:
                self.handle_pushButton_spusti_proces_clicked()
            else:
                self.send_safe_end_commands()
            # print('Odpájam port')
            self.ser_mcu.close()
            self.pripojene = False
            self.ui.pushButton_pripojPort.setText('Pripojiť sa k portu')
            self.ui.pushButton_pripojPort.setText('Connect to port')
            self.ui.frame_command_and_response.setEnabled(False)
            self.ui.frame_zakladne_nastavenia_portu.setEnabled(True)
            self.ui.frame_nastavenia_procesu.setEnabled(False)
            self.ui.frame_modus_operandi.setEnabled(False)

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
            # reduce console spam
            # print(self.odpoved_portu)
            if((self.modus_operandi > 0) and (self.proces_on == True)):
                if(self.napatie == True):
                    self.current_voltage = float(self.odpoved_portu.strip())
                else:
                    self.current_current_read = float(self.odpoved_portu.strip())
                    try:
                        if abs(self.current_current_read) < 1e-12:
                            raise ZeroDivisionError
                        self.current_resistance = self.current_voltage/self.current_current_read
                    except ZeroDivisionError:
                        if not hasattr(self, '_contact_lost') or not self._contact_lost:
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
                    #na tomto mieste zapiseme data do suboru
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
                label.setText("Time remaining: N/A")
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
        except Exception:
            return None
        up = max(0, max_mA - 1)  # from 1 mA to max
        down = up if reverse else 0
        per_loop = up + hold_s + down
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
        else:
            custom = getattr(self.ui, 'lineEdit_custom_name', None)
            base = custom.text().strip() if custom is not None and custom.text().strip() else 'anneal_log'
        if hasattr(self.ui, 'lineEdit_log_file'):
            self.ui.lineEdit_log_file.setText(base)

    def handle_pushButton_posli_prikaz_portu_clicked(self):
        self.prikaz_portu = self.ui.lineEdit_prikaz_portu.text() + "\n"
        self.send_serial_command()
        
    def send_serial_command(self):
        self.ser_mcu.write(bytes(self.prikaz_portu, encoding='ascii'))
        self.ui.label_prikaz_portu.setText(self.prikaz_portu)
        # print('Poslaný príkaz: ' + self.prikaz_portu)
        
    def handle_radioButton_raw_VCP_clicked(self):
        self.ui.radioButton_raw_VCP.setChecked(True)
        self.modus_operandi = 0
        self.ui.frame_nastavenia_procesu.setEnabled(False)
        # print("ModOp: raw VCP ", self.modus_operandi)
        
    def handle_radioButton_manualne_zihanie_clicked(self):
        self.ui.radioButton_manualne_zihanie.setChecked(True)
        self.modus_operandi = 1
        self.ui.frame_nastavenia_procesu.setEnabled(True)
        self.ui.spinBox_hodnota_staly_prud.setEnabled(False)
        self.ui.spinBox_doba_staly_prud.setEnabled(False)
        self.ui.pushButton_start_stop_drzania_prudu.setEnabled(True)
        # print("ModOp: manualne zihanie ", self.modus_operandi)
        
    def handle_radioButton_automatizovane_zihanie_clicked(self):
        self.ui.radioButton_automatizovane_zihanie.setChecked(True)
        self.modus_operandi = 2
        self.ui.frame_nastavenia_procesu.setEnabled(True)
        self.ui.spinBox_hodnota_staly_prud.setEnabled(True)
        self.ui.spinBox_doba_staly_prud.setEnabled(True)
        self.ui.pushButton_start_stop_drzania_prudu.setEnabled(False)
        # print("ModOp: automatizovane zihanie ", self.modus_operandi)
        
    def handle_spinBox_hodnota_staly_prud_valueChanged(self):
        self.hodnota_staly_prud = self.ui.spinBox_hodnota_staly_prud.value()
        # print("Hodnota staleho prudu: ", self.hodnota_staly_prud)
        
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
            self.current_increment = -0.001
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
            self.ui.frame_modus_operandi.setEnabled(False)
            self.ui.groupBox_nastavenia_procesu.setEnabled(False)
            self.command_number = 0
            self.vzorka_N = 0
            # print("Proces bezi")
            self.ui.pushButton_spusti_proces.setText("Stop process")
            if(self.modus_operandi == 0):
                # print("Spusteny raw VCP mod")
                pass
                
            elif(self.modus_operandi == 1):
                # Prepare output file with overwrite prompt
                if not self.prepare_output_file():
                    self.proces_on = False
                    self.ui.pushButton_spusti_proces.setText("Start annealing process")
                    return
                if hasattr(self.ui, 'progressBar_process'):
                    self.ui.progressBar_process.setMaximum(0)
                    self.ui.progressBar_process.setValue(0)
                if hasattr(self.ui, 'label_time_remaining'):
                    self.ui.label_time_remaining.setText("Time remaining: N/A")
                self.current_increment = 0.001
                self.current_current_set = 0.001
                self.temp_resistance_maximum = 0
                self.current_voltage = 0
                self.current_resistance = 0
                self.ui.lcdNumber_uplynute_sekundy.display(0)
                self.ui.label_resistance_at_hold_current.setText("0")
                self.ui.label_resistance_percento_from_hold.setText("0")
                self.ciara_marker="o"
                self.ciara_linestyle="-"
                self.ciara_color="r"
                self.init_graph_window()
                self.send_init_commands()
                self.timer_command.start(1000)
                # print("Spusteny mod manualneho zihania")
                
            elif(self.modus_operandi == 2):
                # Prepare output file with overwrite prompt
                if not self.prepare_output_file():
                    self.proces_on = False
                    self.ui.pushButton_spusti_proces.setText("Start annealing process")
                    return
                self.current_increment = 0.001
                self.current_current_set = 0.001
                self.temp_resistance_maximum = 0
                self.current_voltage = 0
                self.current_resistance = 0
                # reverse + loop configuration
                self.reverse_enabled = getattr(self.ui, 'checkBox_reverse', None) is not None and self.ui.checkBox_reverse.isChecked()
                self.loop_target = self.ui.spinBox_loops.value() if hasattr(self.ui, 'spinBox_loops') else 1
                self.loop_idx = 0
                # progress plan
                up_steps = max(0, int(self.ui.spinBox_hodnota_staly_prud.value()) - 1)
                hold_steps = int(self.ui.spinBox_doba_staly_prud.value())
                down_steps = up_steps if self.reverse_enabled else 0
                per_loop = up_steps + hold_steps + down_steps
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
                self.timer_command.start(1000)
                # print("Spusteny mod automatizovaneho zihania")
                
            else:
                pass
        else:
            self.proces_on = False
            self.timer_command.stop()
            self.timer_prud.stop()
            self.prud_timer_on = False
            if(self.f_out):
                self.f_out.close()
                self.f_out = None
            if(self.modus_operandi == 1):
                self.ui.pushButton_start_stop_drzania_prudu.setText("Držať prúd teraz!")
            self.send_safe_end_commands()
            # print("Proces zastaveny")
            self.ui.pushButton_spusti_proces.setText("Start annealing process")
            self.ui.groupBox_nastavenia_procesu.setEnabled(True)
            self.ui.frame_modus_operandi.setEnabled(True)
        
    def handle_send_new_command(self):
        
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
                self.warn_no_response_and_abort()
                return
                
            self.sample_ready = False
            self.napatie = False
            self.prikaz_portu = "MEAS:CURR?\n"
            #pre simulator tento prikaz, inak pre zdroj ten prvy
            #self.prikaz_portu = "*RRAWO\n"
            self.send_serial_command()
            if not self.wait_for_sample(3000):
                self.warn_no_response_and_abort()
                return
                
            self.curr_value_x = self.current_current_read*1000
            self.curr_value_y = self.current_resistance
            self.ui.lcdNumber_aktualny_prud_mA.display("{:.1f}".format(self.curr_value_x))
            self.ui.lcdNumber_aktualny_odpor.display("{:.1f}".format(self.curr_value_y))
           
            
            #a striggrujeme indikaciu novej vzorky kvoli sekvencovaniu prikazov
            self.vzorka_N +=1
            
            if(self.vzorka_N > 1):
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
            
            #vypnutie ako pri tlacidle
            if(self.current_current_set < 0.001):
                self.handle_pushButton_spusti_proces_clicked()
            
            self.prikaz_portu = self.prikaz_portu = f"CURR {self.current_current_set:.3f}\n"
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
                self.warn_no_response_and_abort()
                return
                
            self.sample_ready = False
            self.napatie = False
            self.prikaz_portu = "MEAS:CURR?\n"
            #pre simulator tento prikaz, inak pre zdroj ten prvy
            #self.prikaz_portu = "*RRAWO\n"
            self.send_serial_command()
            if not self.wait_for_sample(3000):
                self.warn_no_response_and_abort()
                return
                
            self.curr_value_x = self.current_current_read*1000
            self.curr_value_y = self.current_resistance
            self.ui.lcdNumber_aktualny_prud_mA.display("{:.1f}".format(self.curr_value_x))
            self.ui.lcdNumber_aktualny_odpor.display("{:.1f}".format(self.curr_value_y))
           
            
            #a striggrujeme indikaciu novej vzorky kvoli sekvencovaniu prikazov
            self.vzorka_N +=1
            
                       
            if(self.vzorka_N > 1):
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
            if(self.current_current_set > (self.hodnota_staly_prud/1000.0)):
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
            
            # end of hold: either reverse (if enabled) or stop
            if(self.prud_timer_on and (self.sekundy >= self.doba_staly_prud)):
                self.timer_prud.stop()
                self.prud_timer_on = False
                if getattr(self, 'reverse_enabled', False):
                    self.current_increment = -0.001
                    self.ciara_color = "b"
                else:
                    self.handle_pushButton_spusti_proces_clicked()
            
            self.prikaz_portu = self.prikaz_portu = f"CURR {self.current_current_set:.3f}\n"
            self.send_serial_command()
            # completed descending to zero? manage loops
            if getattr(self, 'reverse_enabled', False) and (self.current_current_set < 0.001):
                self.loop_idx = int(getattr(self, 'loop_idx', 0)) + 1
                if self.loop_idx < int(getattr(self, 'loop_target', 1)):
                    # prepare next loop
                    self.current_increment = 0.001
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
            self.simple_delay(1000)
            

    def send_init_commands(self):
        # print("teraz posielam univerzalnu zostavu prikazov pri spusteni")
        for i in range(0, len(self.commands_init)):
            self.prikaz_portu = self.commands_init[i]
            self.send_serial_command()
            self.simple_delay(1000)
            
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
        while not self.sample_ready and self.proces_on and elapsed < timeout_ms:
            self.simple_delay(step)
            elapsed += step
        ok = self.sample_ready
        self.sample_ready = False
        return ok

    def warn_no_response_and_abort(self) -> None:
        QtWidgets.QMessageBox.warning(
            self,
            "No response",
            "No response from power supply. Is it turned on? Aborting the process.",
        )
        if self.proces_on:
            self.handle_pushButton_spusti_proces_clicked()

    def handle_lineEdit_log_subor_text_changed(self):
        # Sync f_name from separate directory + file name controls
        try:
            self.f_name = self.build_log_path()
        except Exception:
            self.f_name = self.ui.lineEdit_log_subor.text()
        # print("Zaznam subor:", self.f_name)
    
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
                layout.setContentsMargins(4, 4, 4, 4)
                layout.setSpacing(4)
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()

            try:
                scheme = QtWidgets.QApplication.instance().styleHints().colorScheme()
                if scheme == QtCore.Qt.ColorScheme.Dark:
                    matplotlib.style.use('dark_background')
                else:
                    matplotlib.style.use('default')
            except Exception:
                pass

            self.fig = Figure()
            self.canvas = FigureCanvas(self.fig) if FigureCanvas is not None else None
            if NavigationToolbar is not None and self.canvas is not None:
                self.toolbar = NavigationToolbar(self.canvas, container)
                layout.addWidget(self.toolbar)
            if self.canvas is not None:
                layout.addWidget(self.canvas, stretch=1)

            try:
                import os as _os
                _title = _os.path.basename(self.f_name)
            except Exception:
                _title = self.f_name
            self.fig.suptitle(_title)

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
            if self.canvas is not None:
                self.canvas.draw()
        else:
            # Fallback to separate window
            self.fig = plt.figure()
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
            plt.show()
        
        
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

    def handle_browse_log_dir(self):
        start_dir = self.ui.lineEdit_log_dir.text() if hasattr(self.ui, 'lineEdit_log_dir') else DEFAULT_LOG_DIR
        new_dir = QFileDialog.getExistingDirectory(self, "Select log directory", start_dir)
        if new_dir and hasattr(self.ui, 'lineEdit_log_dir'):
            self.ui.lineEdit_log_dir.setText(new_dir)

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

    def sync_full_log_path(self):
        # Update hidden full-path edit and internal f_name
        full = self.build_log_path()
        if hasattr(self.ui, 'lineEdit_log_subor'):
            self.ui.lineEdit_log_subor.setText(full)
        self.f_name = full

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
            for info in QSerialPortInfo.availablePorts():
                label = info.portName()
                if info.description():
                    label += f" - {info.description()}"
                self.ui.comboBox_port.addItem(label, userData=info.portName())
            if self.ui.comboBox_port.count() > 0:
                self.port_name = self.ui.comboBox_port.currentData()

    def closeEvent(self, event):
        if self.ser_mcu.isOpen():
            self.handle_pushButton_pripojPort_clicked()
            # self.ser_mcu.close()
            # print("Sériový port zatvorený")

        event.accept()


WINDOWS: list[QtWidgets.QWidget] = []


def main() -> QtWidgets.QWidget:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        owns_app = True

    apply_system_theme(app)

    window = MainWindow()
    window.showMaximized()
    WINDOWS.append(window)

    if owns_app:
        sys.exit(app.exec())
    return window


if __name__ == "__main__":
    main()
    
