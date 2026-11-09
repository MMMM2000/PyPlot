"""Offline GUI acceptance checks using invented images and sample identities."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets
import pytest

from data_logging.microscope_logger import microscope_logger as app_module
from data_logging.microscope_logger.samples import SampleChoice


def _image(width=200, height=120):
    image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("#f1f0ed"))
    painter = QtGui.QPainter(image)
    painter.fillRect(10, 40, width - 20, 40, QtGui.QColor("#202123"))
    painter.end()
    return image


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    def unexpected_camera_access(*args, **kwargs):
        pytest.fail("Offline GUI test attempted camera access")

    monkeypatch.setattr(app_module, "QCamera", unexpected_camera_access)
    monkeypatch.setattr(app_module, "QMediaDevices", unexpected_camera_access)
    # pytest-qt closes registered windows before this fixture's finalizer runs.
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args, **kwargs:
                        QtWidgets.QMessageBox.StandardButton.Discard)
    settings = QtCore.QSettings(str(tmp_path / "private-settings.ini"),
                                QtCore.QSettings.Format.IniFormat)
    view = app_module.MainWindow(enumerate_cameras=False, settings=settings)
    qtbot.addWidget(view)
    yield view
    view.dirty = False
    view.close()


def _calibrate(window):
    window.action_combo.setCurrentIndex(1)
    window.view.line_drawn.emit((20.0, 20.0), (120.0, 20.0))
    window.reference_source.setText("Synthetic reference: 50 um over 100 pixels")
    window.known_um.setValue(50)
    window.calibrate()


def test_offline_window_never_enumerates_or_starts_a_camera(window):
    assert window.camera is None
    assert window.media_devices is None
    assert window.camera_combo.count() == 0
    assert not window.capture_button.isEnabled()
    window.toggle_preview()
    assert window.camera is None
    assert "Select a camera" in window.statusBar().currentMessage()


def test_reference_calibration_and_native_pixel_measurements(window):
    window.set_image(_image(), camera_id="synthetic-camera")
    window.view.line_drawn.emit((50.0, 40.0), (50.0, 80.0))
    assert window.records[0]["width_px"] == 40
    assert window.records[0]["width_um"] is None
    assert window.records[0]["calibration_status"] == "Uncalibrated"

    window.action_combo.setCurrentIndex(1)
    window.view.line_drawn.emit((20.0, 20.0), (120.0, 20.0))
    window.calibrate()
    assert window.calibration is None  # An undescribed reference is rejected.
    assert "Describe the known reference" in window.statusBar().currentMessage()
    assert len(window.records) == 1  # Reference line is not a diameter record.

    _calibrate(window)
    assert window.calibration.um_per_pixel == 0.5
    assert "0.5" in window.scale_status.text()
    window.kind_combo.setCurrentIndex(1)
    window.view.line_drawn.emit((70.0, 40.0), (70.0, 80.0))
    assert window.records[1]["width_px"] == 40
    assert window.records[1]["width_um"] == 20
    assert window.records[1]["kind"] == "outer"
    assert window.records[1]["calibration"]["source"].startswith("Synthetic reference")
    assert window.records[0]["width_um"] is None  # No retroactive relabelling.
    assert window.table.rowCount() == 2


def test_two_mouse_clicks_measure_native_coordinates_after_view_zoom(window, qtbot):
    window.set_image(_image())
    window.show()
    qtbot.waitUntil(lambda: window.view.viewport().width() > 100)
    window.view.scale(1.25, 1.25)
    start = window.view.mapFromScene(QtCore.QPointF(50, 40))
    end = window.view.mapFromScene(QtCore.QPointF(50, 80))
    qtbot.mouseClick(window.view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
    assert window.view.pending is not None
    qtbot.mouseClick(window.view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=end)
    assert window.view.pending is None
    assert len(window.records) == 1
    assert window.records[0]["width_px"] == pytest.approx(40, abs=0.5)
    assert window.records[0]["width_um"] is None


@pytest.mark.parametrize("change", ["objective", "zoom", "size", "camera"])
def test_changed_acquisition_context_invalidates_scale(window, change):
    window.set_image(_image(), camera_id="synthetic-camera")
    _calibrate(window)
    assert window.calibration is not None
    if change == "objective":
        window.objective_combo.setCurrentText("10×")
    elif change == "zoom":
        window.zoom_spin.setValue(2)
    elif change == "size":
        window.set_image(_image(220, 120), camera_id="synthetic-camera")
    else:
        window.set_image(_image(), camera_id="different-synthetic-camera")
    assert window.calibration is None
    assert "Uncalibrated" in window.scale_status.text()
    window.line_drawn((50, 40), (50, 80))
    assert window.records[-1]["width_um"] is None


def test_catalog_dropdowns_and_record_sample_snapshots(window):
    project_first = SampleChoice("SyntheticA", "12/2", "project", "fixture.pydpj", "a" * 64,
                                 ({"section": "assemble", "row_index": 0},))
    project_second = SampleChoice("SyntheticB", "3/1", "project", "fixture.pydpj", "a" * 64,
                                  ({"section": "assemble", "row_index": 1},))
    fabrication_first = SampleChoice("SyntheticA", "12/2", "fabrication", "fixture-fabrication",
                                     "b" * 64, ({"draw": 12, "piece": 2},))
    window._catalog_loaded("project", [project_first, project_second])
    window._catalog_loaded("fabrication", [fabrication_first])
    assert [window.composition_combo.itemText(i) for i in range(2)] == ["SyntheticA", "SyntheticB"]
    assert window.wire_combo.currentText() == "12/2"
    window.set_image(_image())
    window.line_drawn((50, 40), (50, 80))
    first = window.records[0]
    assert first["sample"]["source_kind"] == "project"
    assert [s["source_kind"] for s in first["sample"]["matching_sources"]] == ["project", "fabrication"]
    window.composition_combo.setCurrentText("SyntheticB")
    assert window.wire_combo.currentText() == "3/1"
    window.line_drawn((70, 40), (70, 80))
    assert window.records[1]["sample"]["composition"] == "SyntheticB"
    assert first["sample"]["composition"] == "SyntheticA"
    assert first["sample"]["source_rows"][0]["row_index"] == 0
    window.composition_combo.setEditText("Manual synthetic sample")
    window.wire_combo.setEditText("unknown-piece")
    window.line_drawn((90, 40), (90, 80))
    assert window.records[-1]["sample"] == {
        "composition": "Manual synthetic sample", "microwire": "unknown-piece", "source_kind": "manual",
        "brittle": None, "brittle_observed_utc": None}
    assert first["sample"]["microwire"] == "12/2"


def test_export_keeps_original_pixels_and_per_record_selection(window, tmp_path):
    source = tmp_path / "synthetic-original.png"
    assert _image().save(str(source), "PNG")
    source_bytes = source.read_bytes()
    assert window.load_image(source)
    window.composition_combo.setEditText("First synthetic sample")
    window.wire_combo.setEditText("1/1")
    window.line_drawn((50, 40), (50, 80))
    window.composition_combo.setEditText("Second synthetic sample")
    window.wire_combo.setEditText("2/1")
    _calibrate(window)
    window.line_drawn((70, 40), (70, 80))
    window.notes.setPlainText("Synthetic GUI export; no research data")
    directory = window.export_session(tmp_path / "exports")
    payload = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    assert payload["metadata"]["sample"]["composition"] == "Second synthetic sample"
    assert [r["sample"]["composition"] for r in payload["measurements"]] == [
        "First synthetic sample", "Second synthetic sample"]
    assert [r["width_um"] for r in payload["measurements"]] == [None, 20]
    assert payload["metadata"]["source_file_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    saved_image = QtGui.QImage(str(directory / "image.png"))
    assert saved_image == _image()  # Scene overlays never alter saved original pixels.
    assert source.read_bytes() == source_bytes
    with (directory / "measurements.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["width_um"] == ""
    assert float(rows[1]["width_um"]) == 20
    assert not window.dirty
