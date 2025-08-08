import os
from PyQt6 import QtWidgets

from data_logger.data_logger import MainWindow


def test_subfolder_creation(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(log_dir=str(tmp_path))
    window.ui.checkBox_subdir.setChecked(True)

    # First save using base 's2-1a'
    def save1(*args, **kwargs):
        return str(tmp_path / "FeSiBP 156_2 s2-1a 74mA 2,5a.txt"), ""
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", save1)
    window.ui.lineEdit_log_file.setText("FeSiBP 156_2 s2-1a 74mA 2,5a")
    window.start_logging()
    assert window.log_dir == str(tmp_path / "FeSiBP 156_2 s2-1a 74mA")
    assert (tmp_path / "FeSiBP 156_2 s2-1a 74mA" / "FeSiBP 156_2 s2-1a 74mA 2,5a.txt").exists()
    window.cancel_logging()

    # Change to new sample 's2-1b'; dialog still returns path inside previous folder
    def save2(*args, **kwargs):
        return str(tmp_path / "FeSiBP 156_2 s2-1a 74mA" / "FeSiBP 156_2 s2-1b 74mA 2,5a.txt"), ""
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", save2)
    window.ui.lineEdit_log_file.setText("FeSiBP 156_2 s2-1b 74mA 2,5a")
    window.start_logging()
    assert window.log_dir == str(tmp_path / "FeSiBP 156_2 s2-1b 74mA")
    assert (tmp_path / "FeSiBP 156_2 s2-1b 74mA" / "FeSiBP 156_2 s2-1b 74mA 2,5a.txt").exists()
    window.cancel_logging()
