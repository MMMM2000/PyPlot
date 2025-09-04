# -*- coding: utf-8 -*-
"""
Paliho program/skript na zihanie s HMP4030, kde je aj raw komunikacia na seriovy port

zmeny od V3:
    
- doplnena do grafu extrakcia mena suboru z cesty k suboru
- uzatvaranie suboru po priadani kazdej vzorky
- system kreslenia prehodeny na modifikaciu dat objektu jedneho plotu v jednom grafe 
    namiesto pridavania objektov plot-u do grafickeho okna a spolahnutia sa na vykon NB/PC
- do GUI doplnena aktualna hodnota prudu v mA a odporu v Ohm
- program po ukonceni sa vrati do 1 V nastavenia
- doplnene automaticke zihanie s vypinanim do nuly namiesto postupneho chladnutia


"""

import sys
from PyQt6 import QtCore, QtWidgets, QtSerialPort
from PyQt6.QtWidgets import QFileDialog

from .ui_en import Ui_MainWindow
from plotting.utils import apply_system_theme

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


fig_size = plt.rcParams["figure.figsize"]
fig_size[0] = 19 #19
fig_size[1] = 10 #10
plt.rcParams["figure.figsize"] = fig_size
plt.rcParams["font.family"] = "Palatino Linotype"
plt.rcParams["font.size"] = 14

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
        # Provide a sensible default log file path
        if not self.ui.lineEdit_log_subor.text().strip():
            self.ui.lineEdit_log_subor.setText("data/anneal_log.txt")
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
        self.doba_staly_prud = 10
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
        
        print("Číslo portu: COM" + str(self.cislo_portu))
        print("Baudrate: " + str(self.baudrate))
        
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
        self.f_name = self.ui.lineEdit_log_subor.text()
        
        
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
        
        
        
    #definovanie slotov
    def handle_pushButton_pripojPort_clicked(self):
        if(self.pripojene == False):
            print('Pripájam port')

            self.ser_mcu.setPortName('COM' + str(self.cislo_portu))
            self.ser_mcu.setBaudRate(self.baudrate)
            self.ser_mcu.setFlowControl(QtSerialPort.QSerialPort.FlowControl.NoFlowControl)
            self.ser_mcu.setDataBits(QtSerialPort.QSerialPort.DataBits.Data8)
            self.ser_mcu.setParity(QtSerialPort.QSerialPort.Parity.NoParity)
            self.ser_mcu.setStopBits(QtSerialPort.QSerialPort.StopBits.OneStop)
            
            print(self.ser_mcu)
            
            if self.ser_mcu.open(QtCore.QIODeviceBase.OpenModeFlag.ReadWrite):
                    print('Port pripojený')
                    self.ser_mcu.clear()
                    self.ser_mcu.readyRead.connect(self.handle_ser_mcu_readyRead)
                    self.pripojene = True
                    self.ui.pushButton_pripojPort.setText('Disconnect')
                    self.ui.frame_modus_operandi.setEnabled(True)
                    self.ui.frame_zakladne_nastavenia_portu.setEnabled(False)
                    self.ui.frame_command_and_response.setEnabled(True)
                    self.handle_radioButton_raw_VCP_clicked()
            else:
                    print('Pripojenie portu zlyhalo')

        else:
            if self.proces_on == True:
                self.handle_pushButton_spusti_proces_clicked()
            else:
                self.send_safe_end_commands()
            print('Odpájam port')
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
        print("Číslo portu: COM" + str(self.cislo_portu))
            
    def handle_comboBox_baudrate_currentIndexChanged(self):
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())
        print("Baudrate: " + str(self.baudrate))

    def handle_ser_mcu_readyRead(self):
        if(self.ser_mcu.canReadLine()):
            #print("Prisla lajna")
            self.zamok.lock()
            self.odpoved_portu = str(self.ser_mcu.readLine(),'ascii')
            print(self.odpoved_portu)
            if((self.modus_operandi > 0) and (self.proces_on == True)):
                if(self.napatie == True):
                    self.current_voltage = float(self.odpoved_portu.strip())
                else:
                    self.current_current_read = float(self.odpoved_portu.strip())
                    self.current_resistance = self.current_voltage/self.current_current_read
                    #na tomto mieste zapiseme data do suboru
                    if(not self.f_out):
                        self.f_out = open(self.f_name, "a")
                    if(self.f_out):
                        self.line = str(self.current_current_read) + "\t" + str(self.current_voltage) +"\t" + str(self.current_resistance) + "\n"
                        self.f_out.write(self.line)
                        self.f_out.close()
                        self.f_out = None
                
                self.sample_ready = True
                #print("tutaj lala")
            self.zamok.unlock()
            #print(self.odpoved_portu)
                    
    def handle_update_label_odpoved_portu(self):
        self.ui.label_odpoved_portu.setText(self.odpoved_portu)

    def handle_pushButton_posli_prikaz_portu_clicked(self):
        self.prikaz_portu = self.ui.lineEdit_prikaz_portu.text() + "\n"
        self.send_serial_command()
        
    def send_serial_command(self):
        self.ser_mcu.write(bytes(self.prikaz_portu, encoding='ascii'))
        self.ui.label_prikaz_portu.setText(self.prikaz_portu)
        print('Poslaný príkaz: ' + self.prikaz_portu)
        
    def handle_radioButton_raw_VCP_clicked(self):
        self.ui.radioButton_raw_VCP.setChecked(True)
        self.modus_operandi = 0
        self.ui.frame_nastavenia_procesu.setEnabled(False)
        print("ModOp: raw VCP ", self.modus_operandi)
        
    def handle_radioButton_manualne_zihanie_clicked(self):
        self.ui.radioButton_manualne_zihanie.setChecked(True)
        self.modus_operandi = 1
        self.ui.frame_nastavenia_procesu.setEnabled(True)
        self.ui.spinBox_hodnota_staly_prud.setEnabled(False)
        self.ui.spinBox_doba_staly_prud.setEnabled(False)
        self.ui.pushButton_start_stop_drzania_prudu.setEnabled(True)
        print("ModOp: manualne zihanie ", self.modus_operandi)
        
    def handle_radioButton_automatizovane_zihanie_clicked(self):
        self.ui.radioButton_automatizovane_zihanie.setChecked(True)
        self.modus_operandi = 2
        self.ui.frame_nastavenia_procesu.setEnabled(True)
        self.ui.spinBox_hodnota_staly_prud.setEnabled(True)
        self.ui.spinBox_doba_staly_prud.setEnabled(True)
        self.ui.pushButton_start_stop_drzania_prudu.setEnabled(False)
        print("ModOp: automatizovane zihanie ", self.modus_operandi)
        
    def handle_spinBox_hodnota_staly_prud_valueChanged(self):
        self.hodnota_staly_prud = self.ui.spinBox_hodnota_staly_prud.value()
        print("Hodnota staleho prudu: ", self.hodnota_staly_prud)
        
    def handle_spinBox_doba_staly_prud_valueChanged(self):
        self.doba_staly_prud = self.ui.spinBox_doba_staly_prud.value()
        print("Doba staly prud: ", self.doba_staly_prud)
        
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
            print("Proces bezi")
            self.ui.pushButton_spusti_proces.setText("Zastav proces")
            if(self.modus_operandi == 0):
                print("Spusteny raw VCP mod")
                
            elif(self.modus_operandi == 1):
                self.f_out = open(self.f_name, "a")
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
                print("Spusteny mod manualneho zihania")
                
            elif(self.modus_operandi == 2):
                self.f_out = open(self.f_name, "a")
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
                print("Spusteny mod automatizovaneho zihania")
                
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
            print("Proces zastaveny")
            self.ui.pushButton_spusti_proces.setText("Spusti proces")
            self.ui.groupBox_nastavenia_procesu.setEnabled(True)
            self.ui.frame_modus_operandi.setEnabled(True)
        
    def handle_send_new_command(self):
        
        #manual zihanie
        if self.modus_operandi == 1:
            print("Prikaz manualneho zihania cislo ", self.command_number)
            self.sample_ready = False
            self.napatie = True
            self.prikaz_portu = "MEAS:VOLT?\n"
            #pre simulator tento prikaz, inak pre zdroj ten prvy
            #self.prikaz_portu = "*RRAWO\n"
            self.send_serial_command()
            #pockame na novu vzorku - ano da sa to krajsie, ale teraz je to snadno a rychle
            while(self.sample_ready == False):
                self.simple_delay(20)
                
            self.sample_ready = False
            self.napatie = False
            self.prikaz_portu = "MEAS:CURR?\n"
            #pre simulator tento prikaz, inak pre zdroj ten prvy
            #self.prikaz_portu = "*RRAWO\n"
            self.send_serial_command()
            #pockame na novu vzorku - ano da sa to krajsie, ale teraz je to snadno a rychle
            while(self.sample_ready == False):
                self.simple_delay(20)
                
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
            print("Prikaz automatizovaneho zihania cislo ", self.command_number)
            self.sample_ready = False
            self.napatie = True
            self.prikaz_portu = "MEAS:VOLT?\n"
            #pre simulator tento prikaz, inak pre zdroj ten prvy
            #self.prikaz_portu = "*RRAWO\n"
            self.send_serial_command()
            #pockame na novu vzorku - ano da sa to krajsie, ale teraz je to snadno a rychle
            while(self.sample_ready == False):
                self.simple_delay(20)
                
            self.sample_ready = False
            self.napatie = False
            self.prikaz_portu = "MEAS:CURR?\n"
            #pre simulator tento prikaz, inak pre zdroj ten prvy
            #self.prikaz_portu = "*RRAWO\n"
            self.send_serial_command()
            #pockame na novu vzorku - ano da sa to krajsie, ale teraz je to snadno a rychle
            while(self.sample_ready == False):
                self.simple_delay(20)
                
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
            
            #vypnutie ako pri tlacidle
            if(self.sekundy >= self.doba_staly_prud):
                self.handle_pushButton_spusti_proces_clicked()
            
            self.prikaz_portu = self.prikaz_portu = f"CURR {self.current_current_set:.3f}\n"
            self.send_serial_command()
            
        else:
            pass
        
        self.command_number +=1
        
        
        

    def send_safe_end_commands(self):
        print("teraz posielam univerzalnu zostavu prikazov pri ukonceni")
        for i in range(0, len(self.commands_safe_end)):
            self.prikaz_portu = self.commands_safe_end[i]
            self.send_serial_command()
            self.simple_delay(1000)
            

    def send_init_commands(self):
        print("teraz posielam univerzalnu zostavu prikazov pri spusteni")
        for i in range(0, len(self.commands_init)):
            self.prikaz_portu = self.commands_init[i]
            self.send_serial_command()
            self.simple_delay(1000)
            
    def simple_delay(self, delay_ms):
        self.wait = True
        QtCore.QTimer.singleShot(delay_ms, lambda: setattr(self, 'wait', False))
        
        while self.wait:
            QtWidgets.QApplication.processEvents()
        
    def handle_lineEdit_log_subor_text_changed(self):
        self.f_name = self.ui.lineEdit_log_subor.text()
        print("Zaznam subor:", self.f_name)
    
    def init_graph_window(self):
        """
        self.x_data_ax1 = []
        self.y_data_ax1 = []
        self.x_data_ax2 = []
        self.y_data_ax2 = []
        self.n_counter = 0
        """
    
        self.fig = plt.figure()
        self.fig.suptitle(self.f_name.rsplit("/", 1)[-1])
    
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
        self.f_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save file",
            "data",
            "Text files (*.txt);;All files (*)"
        )

        if self.f_name:
            if not self.f_name.endswith(".txt"):
                self.f_name += ".txt"
            self.ui.lineEdit_log_subor.setText(self.f_name)

    
    def closeEvent(self, event):
        if self.ser_mcu.isOpen():
            self.handle_pushButton_pripojPort_clicked()
            # self.ser_mcu.close()
            print("Sériový port zatvorený")

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
    window.show()
    WINDOWS.append(window)

    if owns_app:
        sys.exit(app.exec())
    return window


if __name__ == "__main__":
    main()
    
