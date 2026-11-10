"""Persistent image-state indicators tested with fake camera frames only."""
from types import SimpleNamespace
from PyQt6 import QtCore, QtWidgets
import pytest
from data_logging.microscope_logger import microscope_logger as module


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    w = module.MainWindow(enumerate_cameras=False, settings=QtCore.QSettings(str(tmp_path/'private.ini'), QtCore.QSettings.Format.IniFormat))
    w.auto_save.setChecked(False)
    qtbot.addWidget(w)
    yield w
    w.camera = None
    w.dirty = False


def fake_preview(w):
    signal = SimpleNamespace(disconnect=lambda: None)
    w.camera = SimpleNamespace(errorOccurred=signal, stop=lambda:None, deleteLater=lambda:None, cameraFormat=lambda:None)
    w.sink = SimpleNamespace(videoFrameChanged=signal, deleteLater=lambda:None)
    w.session = SimpleNamespace(setCamera=lambda x:None, setVideoSink=lambda x:None, deleteLater=lambda:None)


def test_real_frame_required_for_live_and_stalled_or_late_frames_are_clear(window, monkeypatch):
    assert window.image_state_badge.text() == 'NO IMAGE'
    fake_preview(window)
    window._refresh_preview_state()
    assert window.image_view_state == 'starting'
    frame = SimpleNamespace(isValid=lambda:True, toImage=module.demo_image)
    monkeypatch.setattr(module.time, 'monotonic', lambda:100.)
    window._on_frame(frame)
    assert window.command_status()['image_view_state'] == 'live'
    window.statusBar().showMessage('Some unrelated status')
    assert window.image_state_badge.text() == 'LIVE'
    monkeypatch.setattr(module.time, 'monotonic', lambda:103.)
    window._refresh_preview_state()
    assert window.image_view_state == 'stalled'
    window._on_frame(frame)
    assert window.image_view_state == 'live'
    old_generation = window.preview_generation
    window.stop_preview()
    assert window.image_view_state == 'paused'
    window._on_frame(frame, old_generation)
    assert window.image_view_state == 'paused'


def test_static_capture_file_and_saved_session_labels(window, tmp_path):
    window.set_image(module.demo_image())
    assert window.image_view_state == 'still'
    window.last_frame = module.demo_image()
    window.capture_frame()
    assert window.image_state_badge.text() == 'CAPTURED'
    saved = window.export_session(tmp_path/'saved')
    window.dirty = False
    assert window.restore_session(saved)
    assert window.image_view_state == 'saved'
    path = tmp_path/'opened.png'; module.demo_image().save(str(path))
    window.dirty = False
    assert window.load_image(path)
    assert window.image_state_badge.text() == 'OPENED IMAGE'
    window.stop_preview()
    assert window.image_view_state == 'opened'


def test_camera_error_cannot_leave_live_indicator(window):
    fake_preview(window)
    window._set_image_state('live')
    window._camera_error(None, 'Synthetic failure')
    assert window.camera is None
    assert window.image_state_badge.text() == 'CAMERA ERROR'
