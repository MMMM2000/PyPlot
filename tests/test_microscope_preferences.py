"""Restart and capture ownership checks with private settings and fake frames."""
import json
from PyQt6 import QtCore, QtWidgets
from data_logging.microscope_logger.microscope_logger import MainWindow, demo_image


def make_window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    settings = QtCore.QSettings(str(tmp_path / "private.ini"), QtCore.QSettings.Format.IniFormat)
    w = MainWindow(enumerate_cameras=False, settings=settings)
    qtbot.addWidget(w)
    w.auto_save.setChecked(False)
    return w


def test_restart_remembers_settings_and_sample_scoped_annotations(qtbot, tmp_path, monkeypatch):
    w = make_window(qtbot, tmp_path, monkeypatch)
    w.handle_command(dict(action="configure", composition="Invented A", microwire="1/4", brittle=True, objective="40x", zoom=2.5))
    w.handle_command(dict(action="configure", composition="Invented B", microwire="2/1", brittle=False))
    w.handle_command(dict(action="configure", composition="Invented A", microwire="1/4"))
    w.action_combo.setCurrentIndex(2); w.kind_combo.setCurrentIndex(2)
    w.known_um.setValue(75); w.reference_source.setText("Synthetic reference")
    w.notes.setPlainText("Belongs to this image only")
    w.camera_combo.addItem("Synthetic camera"); w.format_combo.addItems(["Mode A", "Mode B"])
    w.format_combo.setCurrentIndex(1)
    w.dirty = False; w.close()
    other = make_window(qtbot, tmp_path, monkeypatch)
    assert other._sample_key() == ("Invented A", "1/4")
    assert other._sample()["brittle"] is True
    assert other.objective_combo.currentText() == "40×" and other.zoom_spin.value() == 2.5
    assert other.action_combo.currentIndex() == other.kind_combo.currentIndex() == 2
    assert other.known_um.value() == 75 and other.reference_source.text() == "Synthetic reference"
    assert not other.notes.toPlainText() and other.image.isNull() and other.camera is None
    other.camera_combo.addItem("Synthetic camera"); other.format_combo.addItems(["Mode A", "Mode B"])
    other._restore_format_preference()
    assert other.format_combo.currentText() == "Mode B"
    other.handle_command(dict(action="configure", composition="Invented B", microwire="2/1"))
    assert other._sample()["brittle"] is False
    other.handle_command(dict(action="configure", microwire="2/2"))
    assert other._sample()["brittle"] is None


def test_camera_capture_keeps_identity_and_brittle_when_controls_change(qtbot, tmp_path, monkeypatch):
    w = make_window(qtbot, tmp_path, monkeypatch)
    w.output_path.setText(str(tmp_path / "captures")); w.auto_save.setChecked(True)
    w.handle_command(dict(action="configure", composition="Invented A", microwire="1/4", brittle=True))
    w.last_frame = demo_image(); w.frame_camera_id = "synthetic camera"; w.capture_frame()
    first = w.active_session
    original = (first / "session.json").read_bytes()
    w.handle_command(dict(action="configure", composition="Invented B", microwire="2/1", brittle=False))
    w.line_drawn([400, 320], [400, 400])
    revision = w._autosave(force=True)
    saved = json.loads((revision / "session.json").read_text())
    assert saved["metadata"]["sample"]["composition"] == "Invented A"
    assert saved["metadata"]["sample"]["brittle"] is True
    assert saved["measurements"][0]["sample"]["brittle"] is True
    assert (first / "session.json").read_bytes() == original
    w.last_frame = demo_image(); w.capture_frame()
    assert w._image_sample()["composition"] == "Invented B"
    assert w._image_sample()["brittle"] is False
    w.dirty = False
    assert w.restore_session(first)
    w.handle_command(dict(action="configure", brittle=False))
    assert w._export_payload()[2]["sample"]["brittle"] is True


def test_invalid_preferences_do_not_prevent_startup(qtbot, tmp_path, monkeypatch):
    settings = QtCore.QSettings(str(tmp_path / "private.ini"), QtCore.QSettings.Format.IniFormat)
    settings.setValue("ui_preferences", json.dumps(dict(annotations="invalid", zoom="bad", boundary=999, objective=4)))
    settings.sync()
    w = make_window(qtbot, tmp_path, monkeypatch)
    assert w.zoom_spin.value() == 1 and w.kind_combo.currentIndex() == 0
    assert w._sample()["brittle"] is None
