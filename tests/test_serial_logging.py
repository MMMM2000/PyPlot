from pathlib import Path

import os
from PyQt6 import QtWidgets

from data_logger.data_logger import MainWindow
import data_logger.serial_port as serial_port


def test_serial_port_context_opens_and_closes(monkeypatch):
    class FakePort:
        FlowControl = type('FlowControl', (), {'NoFlowControl': 0})
        DataBits = type('DataBits', (), {'Data8': 8})
        Parity = type('Parity', (), {'NoParity': 0})
        StopBits = type('StopBits', (), {'OneStop': 1})

        def __init__(self):
            self.opened = False
            self.closed = False
            self.params = {}

        def setPortName(self, name):
            self.params['name'] = name

        def setBaudRate(self, rate):
            self.params['baudrate'] = rate

        def setFlowControl(self, flow):
            self.params['flow'] = flow

        def setDataBits(self, bits):
            self.params['bits'] = bits

        def setParity(self, parity):
            self.params['parity'] = parity

        def setStopBits(self, stop):
            self.params['stop'] = stop

        def open(self, mode):
            self.opened = True
            return True

        def clear(self):
            pass

        def close(self):
            self.closed = True

    qtserial = type('QtSerialPortModule', (), {'QSerialPort': FakePort})
    monkeypatch.setattr(serial_port, 'QtSerialPort', qtserial)

    with serial_port.serial_connection('COM1', 9600) as port:
        assert isinstance(port, FakePort)
        assert port.opened
        test_port = port
    assert test_port.closed


def test_read_from_port_logs_data(tmp_path: Path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(log_dir=str(tmp_path))

    class FakeSerial:
        def __init__(self):
            self.lines = [b">123\n"]

        def canReadLine(self):
            return bool(self.lines)

        def readLine(self):
            return self.lines.pop(0)

    window.serial = FakeSerial()
    window.logging_on = True
    window.log_file = open(tmp_path / 'out.txt', 'w')
    window.sample_count = 1
    window.sample_idx = 0

    window.read_from_port()

    assert window.log_file.closed
    assert (tmp_path / 'out.txt').read_text() == "123\n"
    assert window.sample_idx == 1


def test_use_subdir_creates_folder(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(log_dir=str(tmp_path))

    window.ui.checkBox_subdir.setChecked(True)
    window.ui.lineEdit_log_file.setText("FeSiBP 156_2 s2-1a 74mA 2,5a")

    window.start_logging()
    path1 = tmp_path / "FeSiBP 156_2 s2-1a 74mA" / "FeSiBP 156_2 s2-1a 74mA 2,5a.txt"
    assert path1.exists()
    window.cancel_logging()

    # Next load uses same folder
    window.ui.lineEdit_log_file.setText("FeSiBP 156_2 s2-1a 74mA 5a")
    window.start_logging()
    path2 = tmp_path / "FeSiBP 156_2 s2-1a 74mA" / "FeSiBP 156_2 s2-1a 74mA 5a.txt"
    assert path2.exists()
    window.cancel_logging()

    # Changing the sample number should create a new sibling folder, not a nested one
    window.ui.lineEdit_log_file.setText("FeSiBP 156_2 s2-1b 74mA 2,5a")
    window.start_logging()
    path3 = tmp_path / "FeSiBP 156_2 s2-1b 74mA" / "FeSiBP 156_2 s2-1b 74mA 2,5a.txt"
    assert path3.exists()
    # Ensure no nested directory was created inside the previous folder
    assert not (tmp_path / "FeSiBP 156_2 s2-1a 74mA" / "FeSiBP 156_2 s2-1b 74mA").exists()
    window.cancel_logging()
