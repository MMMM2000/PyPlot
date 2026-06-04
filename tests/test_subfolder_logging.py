import os
import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
from PyQt6 import QtWidgets

from data_logging.data_logger.data_logger import MainWindow


def test_subfolder_creation(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(log_dir=str(tmp_path))
    try:
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
    finally:
        window.close()
        app.processEvents()


def test_subfolder_name_sanitized(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(log_dir=str(tmp_path))
    try:
        window.ui.checkBox_subdir.setChecked(True)

        def save(*args, **kwargs):
            return str(tmp_path / "bad<>sub s2-1a 74mA 2,5a.txt"), ""

        monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", save)
        window.ui.lineEdit_log_file.setText("bad<>sub s2-1a 74mA 2,5a")
        window.start_logging()
        assert window.log_dir == str(tmp_path / "bad__sub s2-1a 74mA")
        assert (tmp_path / "bad__sub s2-1a 74mA" / "bad__sub s2-1a 74mA 2,5a.txt").exists()
        window.cancel_logging()
    finally:
        window.close()
        app.processEvents()
