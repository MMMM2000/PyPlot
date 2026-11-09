"""Synthetic command calibration checks with private Qt settings and storage."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets
import pytest

from data_logging.microscope_logger.calibration_commands import calibrate_from_command
from data_logging.microscope_logger.control import build_parser, command_from_args
from data_logging.microscope_logger.microscope_logger import MainWindow


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    settings = QtCore.QSettings(str(tmp_path / "private.ini"), QtCore.QSettings.Format.IniFormat)
    result = MainWindow(enumerate_cameras=False, settings=settings)
    qtbot.addWidget(result)
    result.auto_save.setChecked(False)
    yield result
    result.autosave_timer.stop()
    result.camera = None
    result.dirty = False


def image(*, color="white", size=(200, 120)):
    result = QtGui.QImage(*size, QtGui.QImage.Format.Format_RGB32)
    result.fill(QtGui.QColor(color))
    return result


def command(**updates):
    result = dict(action="calibrate", start=[10, 10], end=[110, 10],
                  known_um=50, source="Synthetic reference: invented 50 um interval")
    result.update(updates)
    return result


def snapshot(window):
    return dict(last_line=copy.deepcopy(window.last_line), action=window.action_combo.currentIndex(),
                known=window.known_um.value(), source=window.reference_source.text(),
                calibration=window.calibration, evidence=copy.deepcopy(window.calibration_evidence),
                profiles=copy.deepcopy(window.profiles), records=copy.deepcopy(window.records),
                lines=list(window.view.lines))


def test_command_calibrates_native_pixels_and_preserves_reference_evidence(window, tmp_path):
    window.set_image(image(), camera_id="synthetic-camera")
    window.output_path.setText(str(tmp_path / "captures"))
    window.auto_save.setChecked(True)
    status = calibrate_from_command(window, command())
    assert status["calibration_status"] == "Calibrated"
    assert status["calibration"]["um_per_pixel"] == 0.5
    assert window.calibration.um_per_pixel == 0.5
    assert window.action_combo.currentIndex() == 0
    assert not window.records  # A reference is not a specimen measurement.
    evidence = window.calibration_evidence
    assert evidence["start"] == [10, 10] and evidence["end"] == [110, 10]
    reference = Path(evidence["image_path"])
    assert hashlib.sha256(reference.read_bytes()).hexdigest() == evidence["image_sha256"]
    saved = Path(status["active_session"])
    manifest = json.loads((saved / "session.json").read_text(encoding="utf-8"))
    assert manifest["metadata"]["calibration"]["um_per_pixel"] == 0.5
    assert (saved / f"reference_{evidence['image_sha256']}.png").read_bytes() == reference.read_bytes()
    assert not window.dirty


@pytest.mark.parametrize("known_um", [0.001, 1000000])
def test_supported_reference_length_limits_are_not_clamped(window, known_um):
    window.set_image(image(), camera_id="synthetic-camera")
    calibrate_from_command(window, command(known_um=known_um, start=[10, 10], end=[70, 90]))
    assert window.calibration.reference_pixels == 100
    assert window.calibration.reference_length_um == known_um
    assert window.calibration.um_per_pixel == known_um / 100


@pytest.mark.parametrize("updates", [
    {"start": [-1, 10]}, {"end": [200, 10]}, {"end": [10, 120]},
    {"start": [10, 10], "end": [10, 10]}, {"start": [10]},
    {"start": "12"}, {"end": [float("nan"), 1]}, {"start": [True, 10]},
    {"known_um": 0}, {"known_um": -1}, {"known_um": 0.0001},
    {"known_um": 1000001}, {"known_um": float("inf")}, {"known_um": float("nan")},
    {"known_um": True}, {"known_um": "invalid"}, {"known_um": 1.0001},
    {"source": ""}, {"source": " \t\n"}, {"source": 123},
    {"unexpected": "rejected"},
])
def test_invalid_request_preserves_existing_reference_and_scale(window, updates):
    window.set_image(image(), camera_id="synthetic-camera")
    calibrate_from_command(window, command())
    before = snapshot(window)
    with pytest.raises(ValueError):
        calibrate_from_command(window, command(**updates))
    assert snapshot(window) == before


@pytest.mark.parametrize("missing", ["start", "end", "known_um", "source"])
def test_missing_required_value_preserves_existing_reference(window, missing):
    window.set_image(image(), camera_id="synthetic-camera")
    calibrate_from_command(window, command())
    before = snapshot(window)
    request = command()
    request.pop(missing)
    with pytest.raises(ValueError):
        calibrate_from_command(window, request)
    assert snapshot(window) == before


def test_calibration_requires_frozen_image_and_does_not_touch_camera(window):
    before = snapshot(window)
    with pytest.raises(ValueError, match="frozen still"):
        calibrate_from_command(window, command())
    assert snapshot(window) == before
    window.set_image(image(), camera_id="synthetic-camera")
    fake_camera = object()
    window.camera = fake_camera
    before = snapshot(window)
    with pytest.raises(ValueError, match="frozen still"):
        calibrate_from_command(window, command())
    assert window.camera is fake_camera
    assert snapshot(window) == before
    window.camera = None


def test_known_camera_profile_reuse_requires_matching_acquisition_context(window):
    window.set_image(image(), camera_id="synthetic-camera")
    calibrate_from_command(window, command())
    window.set_image(image(color="gray"), camera_id="synthetic-camera")
    assert window.calibration.um_per_pixel == 0.5
    window.objective_combo.setCurrentText("10×")
    assert window.calibration is None
    window.objective_combo.setCurrentText("4×")
    assert window.calibration.um_per_pixel == 0.5
    window.zoom_spin.setValue(2)
    assert window.calibration is None
    window.zoom_spin.setValue(1)
    assert window.calibration.um_per_pixel == 0.5
    window.set_image(image(size=(100, 60)), camera_id="synthetic-camera")
    assert window.calibration is None
    window.set_image(image(), camera_id="different-synthetic-camera")
    assert window.calibration is None
    window.set_image(image(color="black"), camera_id="synthetic-camera")
    assert window.calibration.um_per_pixel == 0.5


def test_calibration_does_not_rewrite_existing_measurements(window):
    window.set_image(image(), camera_id="synthetic-camera")
    window.line_drawn((20, 40), (20, 80))
    before = copy.deepcopy(window.records)
    calibrate_from_command(window, command())
    assert window.records == before
    assert window.records[0]["width_um"] is None
    window.line_drawn((50, 40), (50, 80))
    assert window.records[1]["width_um"] == 20


def test_gui_calibration_failure_is_not_reported_as_command_success(window, monkeypatch):
    window.set_image(image(), camera_id="synthetic-camera")
    def fail_calibration():
        window.statusBar().showMessage("Synthetic reference preservation failure")
        return False
    monkeypatch.setattr(window, "calibrate", fail_calibration)
    saved = []
    monkeypatch.setattr(window, "_autosave", lambda: saved.append(True))
    with pytest.raises(ValueError, match="Synthetic reference preservation failure"):
        calibrate_from_command(window, command())
    assert not saved


def test_cli_parser_to_window_command_reports_scale_and_reference(window):
    window.set_image(image(), camera_id="synthetic-camera")
    parsed = build_parser().parse_args([
        "calibrate", "--start", "10", "10", "--end", "70", "90",
        "--known-um", "50", "--source", "Synthetic diagonal reference",
    ])
    request = command_from_args(parsed)
    assert request == dict(action="calibrate", start=[10, 10], end=[70, 90],
                           known_um=50, source="Synthetic diagonal reference")
    status = window.handle_command(request)
    assert status["ok"] and not status["preview_active"]
    assert status["calibration_status"] == "Calibrated"
    assert status["calibration"]["um_per_pixel"] == 0.5
    assert status["calibration"]["source"] == "Synthetic diagonal reference"
    assert status["calibration"]["camera_id"] == "synthetic-camera"
    assert status["calibration_evidence"]["end"] == [70, 90]
    assert not status["measurements"]
