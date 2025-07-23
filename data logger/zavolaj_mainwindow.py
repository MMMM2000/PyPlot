# -*- coding: utf-8 -*-
"""
Príklad Qt 6 - Program pre komunikáciu cez COM (sériový) port
"""

import sys
from PyQt5 import QtCore, QtWidgets, QtSerialPort
from mainwindow_GUI import Ui_MainWindow

class MainWindow_prog(QtWidgets.QMainWindow):
    
    def __init__(self):
        super(MainWindow_prog, self).__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.odpoved_portu = ''
        self.prikaz_portu = ''
        self.pripojene = False
        self.cislo_portu = self.ui.spinBox_port_number.value()
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())
        self.ui.groupBox_commands.setEnabled(False)
        self.ser_mcu = QtSerialPort.QSerialPort()
        self.zamok = QtCore.QMutex()
        self.timer = QtCore.QTimer()
        self.timer.stop();
        self.timer.timeout.connect(self.handle_update_label_odpoved_portu)
        self.timer.start(10)
        
        self.f_name = None
        self.f_out = None
        self.pocet_vzoriek = 1000
        self.vzorka_N = 0
        self.zaznam_on = False
        
        print("Číslo portu: COM" + str(self.cislo_portu))
        print("Baudrate: " + str(self.baudrate))
        
        #prepojenie signalov a slotov
        self.ui.pushButton_connect.clicked.connect(self.handle_pushButton_pripojPort_clicked)
        self.ui.spinBox_port_number.valueChanged.connect(self.handle_spinBox_cislo_portu_valueChanged)
        self.ui.comboBox_baudrate.currentIndexChanged.connect(self.handle_comboBox_baudrate_currentIndexChanged)
        self.ui.pushButton_send.clicked.connect(self.handle_pushButton_posli_prikaz_portu_clicked)
        self.ui.pushButton_start_log.clicked.connect(self.handle_pushButton_log_zaznam_clicked)
        
        
        
    #definovanie slotov
    def handle_pushButton_pripojPort_clicked(self):
        if(self.pripojene == False):
            print('Pripájam port')

            self.ser_mcu.setPortName('COM' + str(self.cislo_portu))
            self.ser_mcu.setBaudRate(self.baudrate)
            self.ser_mcu.setFlowControl(QtSerialPort.QSerialPort.NoFlowControl);
            self.ser_mcu.setDataBits(QtSerialPort.QSerialPort.Data8);
            self.ser_mcu.setParity(QtSerialPort.QSerialPort.NoParity);
            self.ser_mcu.setStopBits(QtSerialPort.QSerialPort.OneStop);
            
            print(self.ser_mcu)
            
            if self.ser_mcu.open(QtCore.QIODevice.ReadWrite):
                    print('Port pripojený')
                    self.ser_mcu.clear()
                    self.ser_mcu.readyRead.connect(self.handle_ser_mcu_readyRead)
                    self.pripojene = True
                    self.ui.pushButton_connect.setText('Odpoj port')
                    self.ui.groupBox_commands.setEnabled(True)
            else:
                    print('Pripojenie portu zlyhalo')

        else:
            print('Odpájam port')
            self.ser_mcu.close()
            self.pripojene = False
            self.ui.pushButton_connect.setText('Pripojiť sa k portu')
            self.ui.groupBox_commands.setEnabled(False)

    def handle_spinBox_cislo_portu_valueChanged(self):
        self.cislo_portu = self.ui.spinBox_port_number.value()
        print("Číslo portu: COM" + str(self.cislo_portu))
            
    def handle_comboBox_baudrate_currentIndexChanged(self):
        self.baudrate = int(self.ui.comboBox_baudrate.currentText())
        print("Baudrate: " + str(self.baudrate))

    def handle_ser_mcu_readyRead(self):
        if(self.ser_mcu.canReadLine()):
            self.zamok.lock()
            self.odpoved_portu = str(self.ser_mcu.readLine(),'ascii')
            if(self.zaznam_on == True):
                self.f_out.write(self.odpoved_portu.strip(">"))
                self.vzorka_N += 1
                if(self.vzorka_N >= self.pocet_vzoriek):
                    self.f_out.close()
                    self.zaznam_on = False
                    self.ui.pushButton_start_log.setEnabled(True)
            self.zamok.unlock()
            #print(self.odpoved_portu)
                    
    def handle_update_label_odpoved_portu(self):
        self.ui.label_response.setText(self.odpoved_portu)

    def handle_pushButton_posli_prikaz_portu_clicked(self):
        self.prikaz_portu = self.ui.lineEdit_command.text() + "\n"
        print('Poslaný príkaz: ' + self.prikaz_portu)
        self.ser_mcu.write(bytes(self.prikaz_portu, encoding='ascii'))
    
    def handle_pushButton_log_zaznam_clicked(self):            
        self.f_name = self.ui.lineEdit_logfile.text()
        self.f_out = open(self.f_name, "w")
        self.pocet_vzoriek = self.ui.spinBox_sample_count.value()
        self.vzorka_N = 0
        self.zaznam_on = True
        self.ui.pushButton_start_log.setEnabled(False)


if __name__ == "__main__":
    if not QtWidgets.QApplication.instance():
        app = QtWidgets.QApplication(sys.argv)
    else:
        app = QtWidgets.QApplication.instance()
    print("Aplikácia spustená.")
    application = MainWindow_prog()
    application.show()
    sys.exit(app.exec())  
    