"""Regression checks for frozen-image identity and asynchronous preview boundaries."""
import hashlib
import json

from PyQt6 import QtCore, QtGui, QtWidgets
import pytest

from data_logging.microscope_logger import microscope_logger as module


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)
    result = module.MainWindow(enumerate_cameras=False, settings=settings)
    qtbot.addWidget(result)
    yield result
    result.dirty = False
    result.camera = None


def image(color="white"):
    result = QtGui.QImage(200, 120, QtGui.QImage.Format.Format_RGB32)
    result.fill(QtGui.QColor(color))
    return result


def calibrate(window):
    window.action_combo.setCurrentIndex(1)
    window.line_drawn((10, 10), (110, 10))
    window.reference_source.setText("Invented 100 um reference")
    window.known_um.setValue(100)
    window.calibrate()


def test_unknown_camera_scale_is_bound_to_exact_image(window):
    window.set_image(image())
    calibrate(window)
    assert window.calibration is not None
    window.set_image(image("gray"))
    assert window.calibration is None
    window.set_image(image())
    assert window.calibration is not None


def test_reference_image_is_portable_and_integrity_checked(window, tmp_path):
    window.set_image(image())
    calibrate(window)
    window.line_drawn((50, 40), (50, 80))
    exported = window.export_session(tmp_path / "exports")
    payload = json.loads((exported / "session.json").read_text())
    evidence = payload["measurements"][0]["calibration_evidence"]
    assert hashlib.sha256((exported / evidence["image_file"]).read_bytes()).hexdigest() == evidence["image_sha256"]
    assert evidence["start"] == [10, 10]
    assert payload["measurements"][0]["width_um"] == 40
    from pathlib import Path
    Path(evidence["image_path"]).write_bytes(b"damaged reference")
    with pytest.raises(ValueError, match="reference image has changed"):
        window.export_session(tmp_path / "exports")


def test_source_hash_belongs_to_loaded_bytes(window, tmp_path):
    source = tmp_path / "image.png"
    image().save(str(source))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    window.load_image(source)
    image("black").save(str(source))
    exported = window.export_session(tmp_path / "exports")
    payload = json.loads((exported / "session.json").read_text())
    assert payload["metadata"]["source_file_sha256"] == before
    assert QtGui.QImage(str(exported / "image.png")) == image()


def test_stopped_or_stale_preview_cannot_replace_frozen_image(window):
    class BadFrame:
        def toImage(self):
            pytest.fail("A stale preview frame was decoded")
    window.set_image(image())
    window._on_frame(BadFrame(), window.preview_generation)
    window.camera = object()
    window._on_frame(BadFrame(), window.preview_generation - 1)
    window._camera_error(None, "stale error", window.preview_generation - 1)
    window.camera = None
    assert window.image == image()
    assert not window.capture_button.isEnabled()


def test_notes_after_export_remain_unsaved(window, tmp_path):
    window.set_image(image())
    window.export_session(tmp_path)
    assert not window.dirty
    window.notes.setPlainText("Updated specimen notes")
    assert window.dirty


def test_refresh_preserves_selected_and_typed_samples(window):
    from data_logging.microscope_logger.samples import SampleChoice
    window._catalog_loaded("project", [SampleChoice("A", "1/1", "project", "source", "a" * 64),
                                       SampleChoice("A", "2/1", "project", "source", "a" * 64)])
    window.wire_combo.setCurrentText("2/1")
    window._catalog_loaded("fabrication", [])
    assert window.wire_combo.currentText() == "2/1"
    window.composition_combo.setCurrentText("Manual")
    window.wire_combo.setCurrentText("Unknown")
    window._refresh_samples()
    assert window.composition_combo.currentText() == "Manual"
    assert window.wire_combo.currentText() == "Unknown"


def test_stale_catalog_result_is_not_applied(window, tmp_path):
    window.project_path.setText(str(tmp_path / "new.pydpj"))
    window._catalog_received("project", str(tmp_path / "old.pydpj"), [])
    assert "project" not in window.catalogs
    assert "path changed" in window.sample_status.text()
