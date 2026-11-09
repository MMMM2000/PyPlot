"""Offline app commands and autosave, without camera or persistent preferences."""
import hashlib
import json

from PyQt6 import QtCore, QtWidgets
import pytest

from data_logging.microscope_logger.microscope_logger import MainWindow, demo_image


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)
    view = MainWindow(enumerate_cameras=False, settings=settings)
    qtbot.addWidget(view)
    yield view
    view.autosave_timer.stop()
    view.dirty = False


def test_commands_configure_manual_objective_and_sample(window, tmp_path):
    status = window.handle_command(dict(action="configure", objective="10x", composition="Invented", microwire="1/2",
                                        output_directory=str(tmp_path / "images")))
    assert status["objective_manual"]
    assert status["objective"] == "10×"
    assert status["sample"]["microwire"] == "1/2"
    assert not (tmp_path / "images").exists()
    with pytest.raises(ValueError, match="Unknown"):
        window.handle_command(dict(action="configure", script="bad"))


def test_parallel_and_auto_commands_keep_pixels_only(window):
    window.set_image(demo_image())
    command = dict(a=[200, 320], b=[1000, 320], c=[200, 400])
    manual = window.handle_command(dict(action="parallel", **command))["measurements"][-1]
    assert manual["width_px"] == pytest.approx(80)
    assert manual["width_um"] is None
    assert manual["method"] == "parallel_edges"
    auto = window.handle_command(dict(action="auto", **command))["measurements"][-1]
    assert auto["width_px"] == pytest.approx(80, abs=1)
    assert auto["analysis"]["requires_review"]
    assert auto["review_status"] == "Unreviewed"
    core = window.handle_command(dict(action="auto", kind="core", samples=7, **command))["measurements"][-1]
    assert core["kind"] == "core"
    assert core["analysis"]["summary"]["requested"] == 7
    assert window.image == demo_image()


def test_auto_capture_and_measurement_revisions(window, tmp_path):
    window.handle_command(dict(action="configure", composition="Invented", microwire="2/3", output_directory=str(tmp_path)))
    window.last_frame = demo_image()
    window.capture_frame()
    directory = window.active_session
    assert directory and (directory / "image.png").exists()
    initial = (directory / "session.json").read_bytes()
    original_hash = hashlib.sha256((directory / "image.png").read_bytes()).hexdigest()
    window.handle_command(dict(action="measure", start=[400, 320], end=[400, 400]))
    pointer = json.loads((directory / "latest.json").read_text())
    payload_path = directory / pointer["manifest"]
    payload = json.loads(payload_path.read_text())
    assert payload["measurements"][0]["width_px"] == 80
    assert payload["measurements"][0]["sample"]["microwire"] == "2/3"
    assert payload["image"]["sha256"] == original_hash
    assert (directory / "session.json").read_bytes() == initial
    assert hashlib.sha256(payload_path.read_bytes()).hexdigest() == pointer["manifest_sha256"]
    assert len(list(directory.rglob("image.png"))) == 1
    assert not window.dirty
    window.set_image(demo_image())
    window.dirty = False
    restored = window.handle_command(dict(action="restore_session", path=str(directory)))
    assert restored["measurements"][0]["width_px"] == 80
    assert restored["sample"]["microwire"] == "2/3"
    assert window.active_session == directory


def test_bad_measurement_is_reported_instead_of_success(window):
    with pytest.raises(ValueError, match="Select a camera"):
        window.handle_command(dict(action="start"))
    with pytest.raises(ValueError):
        window.handle_command(dict(action="measure", start=[0, 0], end=[10, 10]))
    window.set_image(demo_image())
    with pytest.raises(ValueError, match="different endpoints"):
        window.handle_command(dict(action="measure", start=[10, 10], end=[10, 10]))
    assert not window.records


def test_invalid_configuration_is_not_partially_applied(window):
    window.composition_combo.setCurrentText("Preserve")
    with pytest.raises(ValueError, match="Camera not found"):
        window.handle_command(dict(action="configure", composition="Wrong", camera_name="Missing"))
    assert window.composition_combo.currentText() == "Preserve"


def test_failed_save_retains_image_and_records(window, tmp_path):
    window.set_image(demo_image())
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("preserve")
    window.output_path.setText(str(blocked))
    window.line_drawn([400, 320], [400, 400])
    assert window._autosave() is None
    assert window.autosave_error
    assert window.dirty and window.records
    assert blocked.read_text() == "preserve"


def test_loaded_images_do_not_read_tma_defaults(window):
    assert not window.use_tma_defaults
    assert not window.project_path.text()


def test_parallel_tool_uses_three_native_points(window, qtbot):
    window.set_image(demo_image())
    window.action_combo.setCurrentIndex(2)
    window.show()
    qtbot.wait(20)
    for x, y in ((200, 320), (1000, 320), (200, 400)):
        position = window.view.mapFromScene(QtCore.QPointF(x, y))
        qtbot.mouseClick(window.view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=position)
    assert len(window.records) == 1
    assert window.records[0]["width_px"] == pytest.approx(80, abs=2)


def test_preview_drops_excess_frames_before_decode(window, monkeypatch):
    from data_logging.microscope_logger import microscope_logger as module
    clock = [1.0]
    decoded = []
    class Frame:
        def isValid(self):
            return True
        def toImage(self):
            decoded.append(True)
            return demo_image()
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    window.camera = object()
    window._on_frame(Frame())
    clock[0] = 1.01
    window._on_frame(Frame())
    clock[0] = 1.11
    window._on_frame(Frame())
    window.camera = None
    assert window.frames_received == 3
    assert len(decoded) == 2
    assert window.last_frame == demo_image()


def test_restored_sample_binding_survives_another_capture(window, tmp_path):
    from data_logging.microscope_logger.samples import SampleChoice
    window._catalog_loaded("project", [SampleChoice("Invented", "1/1", "project", "fixture.pydpj", "a" * 64)])
    window.output_path.setText(str(tmp_path))
    window.auto_save.setChecked(True)
    window.last_frame = demo_image()
    window.capture_frame()
    directory = window.active_session
    assert directory is not None, window.autosave_error
    window.catalogs.clear()
    window.restore_session(directory)
    assert window._sample()["source_kind"] == "project"
    window.last_frame = demo_image()
    window.capture_frame()
    assert window._sample()["source_kind"] == "project"
