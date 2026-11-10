"""Display enlargement is visible and cannot change calibrated pixel geometry."""
from PyQt6 import QtCore, QtGui, QtWidgets
import pytest

from data_logging.microscope_logger.microscope_logger import MainWindow, demo_image


def test_view_zoom_reports_display_scale_without_changing_measurement(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    settings = QtCore.QSettings(str(tmp_path / "private.ini"), QtCore.QSettings.Format.IniFormat)
    window = MainWindow(enumerate_cameras=False, settings=settings)
    qtbot.addWidget(window)
    window.auto_save.setChecked(False)
    window.set_image(demo_image(), camera_id="synthetic-camera")
    window.handle_command(dict(action="calibrate", start=[100, 100], end=[200, 100],
                               known_um=50, source="Synthetic 50 um reference"))
    context = window._context()
    profile_key = window._profile_key()
    before = window.command_status()
    assert before["calibration"]["um_per_pixel"] == 0.5
    assert before["acquisition_zoom_manual"] is True
    assert before["acquisition_zoom"] == 1

    event = QtGui.QWheelEvent(QtCore.QPointF(200, 200), QtCore.QPointF(200, 200),
        QtCore.QPoint(0, 0), QtCore.QPoint(0, 120), QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False)
    window.view.wheelEvent(event)
    after = window.command_status()
    assert after["view_zoom"] == pytest.approx(before["view_zoom"] * 1.2)
    assert window.view_scale_label.text() == f"View {100 * after['view_zoom']:.0f}%"
    assert window._context() == context
    assert window._profile_key() == profile_key
    assert after["calibration"] == before["calibration"]
    row = window.handle_command(dict(action="measure", start=[400, 320], end=[400, 400]))["measurements"][-1]
    assert row["width_px"] == 80
    assert row["width_um"] == 40

    window.view.fit_image()
    assert window.view_scale_label.text() == f"View {100 * window.command_status()['view_zoom']:.0f}%"
    assert window.image == demo_image()
    window.dirty = False


def test_status_rechecks_removed_reference_before_reporting_calibrated(qtbot, tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    settings = QtCore.QSettings(str(tmp_path / "private.ini"), QtCore.QSettings.Format.IniFormat)
    window = MainWindow(enumerate_cameras=False, settings=settings)
    qtbot.addWidget(window)
    window.auto_save.setChecked(False)
    window.set_image(demo_image(), camera_id="synthetic-camera")
    window.handle_command(dict(action="calibrate", start=[100, 100], end=[200, 100],
                               known_um=50, source="Synthetic 50 um reference"))
    Path(window.calibration_evidence["image_path"]).unlink()
    status = window.command_status()
    assert status["calibration_status"] == "Uncalibrated"
    assert status["calibration"] is None
    assert status["calibration_error"]
    window.dirty = False
