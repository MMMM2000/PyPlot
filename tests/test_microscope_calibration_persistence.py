"""Synthetic calibration recovery checks; no cameras or persistent app stores."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets
import pytest

from data_logging.microscope_logger import microscope_logger as module


@pytest.fixture
def windows(qtbot, tmp_path, monkeypatch):
    def no_camera(*args, **kwargs):
        pytest.fail("Calibration persistence test attempted camera access")

    monkeypatch.setattr(module, "QCamera", no_camera)
    monkeypatch.setattr(module, "QMediaDevices", no_camera)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    created = []

    def create(name="private.ini"):
        settings = QtCore.QSettings(str(tmp_path / name), QtCore.QSettings.Format.IniFormat)
        window = module.MainWindow(enumerate_cameras=False, settings=settings, use_tma_defaults=False)
        window.auto_save.setChecked(False)
        qtbot.addWidget(window)
        created.append(window)
        return window

    yield create
    for window in created:
        window.autosave_timer.stop()
        window.dirty = False


def image(color="white"):
    result = QtGui.QImage(200, 120, QtGui.QImage.Format.Format_RGB32)
    result.fill(QtGui.QColor(color))
    return result


def calibrate(window, *, known_um=50):
    window.action_combo.setCurrentIndex(1)
    window.line_drawn((10, 10), (110, 10))
    window.reference_source.setText("Invented calibration standard: 50 um interval")
    window.known_um.setValue(known_um)
    return window.calibrate()


def measurement_values(records):
    """Normalize only JSON containers and relocated reference-file pointers."""
    values = json.loads(json.dumps(records))
    for record in values:
        evidence = record.get("calibration_evidence")
        if evidence:
            evidence.pop("image_path", None)
            evidence.pop("image_file", None)
    return values


def saved_capture(windows, tmp_path, *, camera_id="synthetic-camera"):
    source = windows("source.ini")
    source.set_image(image(), camera_id=camera_id)
    assert calibrate(source) is True
    source.line_drawn((50, 40), (50, 80))
    directory = source.export_session(tmp_path / "captures")
    return source, directory


def test_calibration_is_durable_before_original_window_closes(windows):
    original = windows()
    original.set_image(image(), camera_id="synthetic-camera")
    original.line_drawn((50, 40), (50, 80))
    prior_rows = copy.deepcopy(original.records)
    assert calibrate(original) is True
    # Inspect disk as well as another QSettings instance, which can share a cache.
    assert "calibration_profiles=" in Path(original.settings.fileName()).read_text()
    assert original.records == prior_rows
    reopened = windows()
    reopened.set_image(image("gray"), camera_id="synthetic-camera")
    assert reopened.calibration.um_per_pixel == 0.5
    reopened.line_drawn((50, 40), (50, 80))
    assert reopened.records[0]["width_um"] == 20


def test_pixel_only_removal_is_durable_and_old_window_close_cannot_restore_it(windows):
    original = windows()
    original.set_image(image(), camera_id="synthetic-camera")
    assert calibrate(original)
    stale = windows()
    stale.set_image(image(), camera_id="synthetic-camera")
    assert stale.calibration is not None
    assert original.clear_calibration() is True
    assert json.loads(original.settings.value("calibration_profiles", type=str)) == {}
    stale.dirty = False
    stale.close()
    reopened = windows()
    reopened.set_image(image(), camera_id="synthetic-camera")
    assert reopened.calibration is None


class FailedSettings:
    """A private real INI store whose sync reports a deterministic write error."""
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def status(self):
        return QtCore.QSettings.Status.AccessError


def test_settings_failure_preserves_previous_scale_and_reports_failure(windows):
    window = windows()
    window.set_image(image(), camera_id="synthetic-camera")
    assert calibrate(window)
    before = copy.deepcopy(window.profiles)
    window.settings = FailedSettings(window.settings)
    assert calibrate(window, known_um=100) is False
    assert "Could not save calibration profiles" in window.statusBar().currentMessage()
    assert window.profiles == before
    assert window.calibration.um_per_pixel == 0.5
    assert json.loads(window.settings.value("calibration_profiles", type=str)) == json.loads(json.dumps(before))
    assert window.clear_calibration() is False
    assert window.calibration.um_per_pixel == 0.5


def test_reference_failure_does_not_publish_profile(windows, monkeypatch):
    window = windows()
    window.set_image(image(), camera_id="synthetic-camera")
    monkeypatch.setattr(window, "_image_png", lambda: (_ for _ in ()).throw(OSError("synthetic disk failure")))
    assert calibrate(window) is False
    assert not window.profiles
    assert window.calibration is None
    assert not window.settings.value("calibration_profiles", "", type=str)
    assert "Could not preserve calibration reference" in window.statusBar().currentMessage()


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_missing_or_changed_reference_invalidates_profile_and_session_copy_repairs_it(windows, tmp_path, damage):
    window, directory = saved_capture(windows, tmp_path)
    reference = Path(window.calibration_evidence["image_path"])
    if damage == "missing":
        reference.unlink()
    else:
        reference.write_bytes(b"deliberately changed synthetic reference")
    recorded = copy.deepcopy(window.records)
    window._context_changed()
    assert window.calibration is None
    assert "Saved calibration unavailable" in window.scale_status.text()
    assert window.records == recorded
    window.line_drawn((60, 40), (60, 80))
    assert window.records[-1]["width_um"] is None
    assert window.restore_session(directory)
    assert window.calibration.um_per_pixel == 0.5
    recovered = Path(window.calibration_evidence["image_path"])
    assert recovered.parent == directory
    assert hashlib.sha256(recovered.read_bytes()).hexdigest() == window.calibration_evidence["image_sha256"]
    assert measurement_values(window.records) == measurement_values(recorded)
    reopened = windows("source.ini")
    reopened.set_image(image("gray"), camera_id="synthetic-camera")
    assert reopened.calibration.um_per_pixel == 0.5


@pytest.mark.parametrize("method", ["manual", "parallel"])
def test_reference_lost_after_activation_cannot_produce_new_um_values(windows, method):
    window = windows()
    window.set_image(image(), camera_id="synthetic-camera")
    assert calibrate(window)
    window.line_drawn((50, 40), (50, 80))
    old_row = copy.deepcopy(window.records[0])
    Path(window.calibration_evidence["image_path"]).unlink()
    if method == "manual":
        window.line_drawn((70, 40), (70, 80))
    else:
        window.guides_drawn((10, 40), (110, 40), (10, 80), automatic=False)
    assert window.calibration is None
    assert window.records[-1]["width_um"] is None
    assert window.records[0] == old_row
    assert "Saved calibration unavailable" in window.scale_status.text()


def test_restoring_missing_profile_recovers_scale_for_future_matching_captures(windows, tmp_path):
    _, directory = saved_capture(windows, tmp_path)
    recovered = windows("recovered.ini")
    assert recovered.profiles == {}
    assert recovered.restore_session(directory)
    assert recovered.calibration.um_per_pixel == 0.5
    assert len(recovered.profiles) == 1
    assert not recovered.dirty
    # Recovery is durable even when this window is never closed cleanly.
    reopened = windows("recovered.ini")
    reopened.set_image(image("gray"), camera_id="synthetic-camera")
    assert reopened.calibration.um_per_pixel == 0.5
    reopened.objective_combo.setCurrentText("10×")
    assert reopened.calibration is None
    reopened.objective_combo.setCurrentText("4×")
    assert reopened.calibration.um_per_pixel == 0.5
    reopened.zoom_spin.setValue(2)
    assert reopened.calibration is None


def test_restored_unknown_camera_profile_stays_bound_to_original_pixels(windows, tmp_path):
    _, directory = saved_capture(windows, tmp_path, camera_id="")
    recovered = windows("recovered.ini")
    assert recovered.restore_session(directory)
    recovered.set_image(image("gray"))
    assert recovered.calibration is None
    recovered.set_image(image())
    assert recovered.calibration.um_per_pixel == 0.5


@pytest.mark.parametrize("change", ["context", "shape", "missing-evidence", "endpoints"])
def test_invalid_saved_profile_cannot_enable_a_scale(windows, change):
    window = windows()
    window.set_image(image(), camera_id="synthetic-camera")
    assert calibrate(window)
    key = window._profile_key()
    if change == "context":
        window.profiles[key]["calibration"]["camera_id"] = "wrong-camera"
    elif change == "shape":
        window.profiles[key] = "malformed profile"
    elif change == "missing-evidence":
        window.profiles[key].pop("evidence")
    else:
        window.profiles[key]["evidence"]["end"] = [100, 10]
    window._context_changed()
    assert window.calibration is None
    assert "Saved calibration unavailable" in window.scale_status.text()
    window.line_drawn((50, 40), (50, 80))
    assert window.records[-1]["width_um"] is None


@pytest.mark.parametrize("change", ["objective", "zoom", "zoom-precision", "size", "camera", "evidence", "metadata"])
def test_invalid_restore_is_rejected_before_current_image_or_rows_change(windows, tmp_path, change):
    _, directory = saved_capture(windows, tmp_path)
    path = directory / "session.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if change == "objective":
        payload["metadata"]["objective"] = "10x"
    elif change == "zoom":
        payload["metadata"]["zoom"] = 21
    elif change == "zoom-precision":
        payload["metadata"]["zoom"] = payload["metadata"]["calibration"]["zoom"] = 1.001
    elif change == "size":
        payload["metadata"]["image_size"] = [100, 120]
    elif change == "camera":
        payload["metadata"]["calibration"]["camera_id"] = "another-camera"
    elif change == "evidence":
        payload["metadata"]["calibration_evidence"] = None
    else:
        payload["metadata"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")
    current = windows("current.ini")
    current.set_image(image("black"))
    current.line_drawn((20, 40), (20, 80))
    before = copy.deepcopy(current.records)
    with pytest.raises(ValueError):
        current.restore_session(directory)
    assert current.image == image("black")
    assert current.records == before
    assert not current.profiles


def test_restore_settings_failure_leaves_current_capture_unchanged(windows, tmp_path):
    _, directory = saved_capture(windows, tmp_path)
    current = windows("current.ini")
    current.set_image(image("black"))
    current.line_drawn((20, 40), (20, 80))
    before = copy.deepcopy(current.records)
    current.settings = FailedSettings(current.settings)
    with pytest.raises(OSError, match="Could not save calibration profiles"):
        current.restore_session(directory)
    assert current.image == image("black")
    assert current.records == before
    assert not current.profiles


def test_open_saved_session_button_delegates_and_cancel_leaves_capture(windows, tmp_path, monkeypatch):
    _, directory = saved_capture(windows, tmp_path)
    current = windows("current.ini")
    current.set_image(image("black"))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(directory))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Cancel)
    current.open_session_button.click()
    assert current.image == image("black")
    assert not current.profiles
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    current.open_session_button.click()
    assert current.image == image()
    assert current.calibration.um_per_pixel == 0.5
    assert "Opened saved session" in current.statusBar().currentMessage()


def test_open_saved_session_reports_invalid_directory_without_replacing_image(windows, tmp_path, monkeypatch):
    current = windows()
    current.set_image(image("black"))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "absent"))
    assert current.choose_session() is False
    assert "Could not open saved session" in current.statusBar().currentMessage()
    assert current.image == image("black")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    assert current.choose_session() is False
