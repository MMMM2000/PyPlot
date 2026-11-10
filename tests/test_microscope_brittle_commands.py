"""Sample annotation controls and immutable measurement provenance, offline."""
import csv
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from PyQt6 import QtCore, QtWidgets
import pytest

from data_logging.microscope_logger.control import build_parser, command_from_args
from data_logging.microscope_logger.microscope_logger import MainWindow, demo_image
from data_logging.microscope_logger.samples import SampleChoice


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)
    view = MainWindow(enumerate_cameras=False, settings=settings)
    view.auto_save.setChecked(False)
    qtbot.addWidget(view)
    yield view
    if view.annotation_task:
        qtbot.waitUntil(lambda: view.annotation_task is None, timeout=5000)
    view.autosave_timer.stop()
    view.dirty = False


@pytest.mark.parametrize("argument,value", [("yes", True), ("no", False), ("unknown", None)])
def test_cli_annotation_values(argument, value):
    assert command_from_args(build_parser().parse_args(["configure", "--brittle", argument])) == {
        "action": "configure", "brittle": value}


def test_toggle_is_sample_scoped_and_never_defaults_to_false(window):
    assert window.command_status()["sample"]["brittle"] is None
    window.handle_command(dict(action="configure", composition="Invented", microwire="1/2", brittle=True))
    assert window.command_status()["sample"]["brittle"] is True
    window.handle_command(dict(action="configure", microwire="1/3"))
    assert window.command_status()["sample"]["brittle"] is None
    window.handle_command(dict(action="configure", brittle=False))
    assert window.command_status()["sample"]["brittle"] is False
    window.handle_command(dict(action="configure", microwire="1/2"))
    assert window.command_status()["sample"]["brittle"] is True
    with pytest.raises(ValueError, match="true, false or null"):
        window.handle_command(dict(action="configure", composition="Wrong", brittle="false"))
    assert window.composition_combo.currentText() == "Invented"


def test_saved_project_value_prefills_without_inventing_an_observation(window):
    window._catalog_loaded("project", [SampleChoice("Invented", "1/2", "project", "synthetic.pydpj", "a" * 64, brittle=True)])
    assert window.brittle_toggle.checkState() == QtCore.Qt.CheckState.Checked
    assert window._sample()["brittle"] is True
    assert window._sample()["brittle_observed_utc"] is None
    window.handle_command(dict(action="configure", brittle=False))
    window._catalog_loaded("project", [SampleChoice("Invented", "1/2", "project", "synthetic.pydpj", "b" * 64, brittle=True)])
    assert window._sample()["brittle"] is False


def test_annotation_survives_json_csv_revision_and_restore(window, tmp_path):
    window.handle_command(dict(action="configure", composition="Invented", microwire="1/2", brittle=True,
                               output_directory=str(tmp_path)))
    window.set_image(demo_image())
    window.line_drawn([400, 320], [400, 400])
    first = window._autosave(force=True)
    original = (first / "session.json").read_bytes()
    assert first.name.startswith("Invented__1_2__")
    assert json.loads(original)["metadata"]["sample"]["brittle"] is True
    with (first / "measurements.csv").open(newline="", encoding="utf-8") as stream:
        assert list(csv.DictReader(stream))[0]["brittle"] == "True"
    window.handle_command(dict(action="configure", brittle=False))
    window.line_drawn([500, 320], [500, 400])
    revision = window._autosave(force=True)
    payload = json.loads((revision / "session.json").read_text(encoding="utf-8"))
    assert payload["metadata"]["sample"]["brittle"] is False
    assert [row["sample"]["brittle"] for row in payload["measurements"]] == [True, False]
    with (revision / "measurements.csv").open(newline="", encoding="utf-8") as stream:
        assert [r["brittle"] for r in csv.DictReader(stream)] == ["True", "False"]
    assert (first / "session.json").read_bytes() == original
    window.handle_command(dict(action="configure", brittle=None))
    window.dirty = False
    assert window.restore_session(first)
    assert window.command_status()["sample"]["brittle"] is False


@dataclass(frozen=True)
class FakeReview:
    source_path: Path
    review_path: Path
    composition: str = "Invented"
    microwire: str = "1/2"
    brittle: bool = True
    candidate_sha256: str = "b" * 64

    def to_dict(self):
        return {key: str(value) if isinstance(value, Path) else value for key, value in asdict(self).items()}


def test_project_update_is_explicit_async_and_bound_to_review(window, tmp_path, qtbot, monkeypatch):
    calls = []
    review = FakeReview(tmp_path / "source.pydpj", tmp_path / "review" / "review.json")

    def prepare(source, workdir, **kwargs):
        calls.append((source, workdir, kwargs))
        return review

    def publish(value, **kwargs):
        calls.append((value, kwargs))
        return {"new_sha256": "b" * 64}

    monkeypatch.setitem(sys.modules, "data_logging.microscope_logger.project_annotations",
                        SimpleNamespace(prepare_brittle_update=prepare, publish_brittle_update=publish))
    window.handle_command(dict(action="configure", composition="Invented", microwire="1/2", brittle=True,
                               project_path=str(review.source_path), output_directory=str(tmp_path)))
    assert calls == []  # Toggling never silently rewrites a database.
    window.handle_command(dict(action="prepare_brittle_update", expected_sha256="a" * 64))
    qtbot.waitUntil(lambda: window.annotation_task is None, timeout=5000)
    assert window.annotation_job["state"] == "ready"
    assert calls[0][2]["brittle"] is True
    with pytest.raises(ValueError, match="reviewed candidate"):
        window.handle_command(dict(action="publish_brittle_update"))
    with pytest.raises(ValueError, match="does not match"):
        window.handle_command(dict(action="publish_brittle_update", review_sha256="c" * 64))
    window.handle_command(dict(action="configure", brittle=False))
    with pytest.raises(ValueError, match="changed"):
        window.handle_command(dict(action="publish_brittle_update", review_sha256="b" * 64))
    window.handle_command(dict(action="configure", brittle=True))
    window.handle_command(dict(action="publish_brittle_update", review_sha256="b" * 64))
    qtbot.waitUntil(lambda: window.annotation_task is None, timeout=5000)
    assert window.annotation_job["state"] == "published"
    assert calls[1][1]["backup"] == review.review_path.parent / "original.pydpj"
    assert window.project_update_receipts[0]["brittle"] is True


def test_unknown_annotation_never_prepares_project_change(window, tmp_path):
    window.handle_command(dict(action="configure", composition="Invented", microwire="1/2", project_path=str(tmp_path / "source.pydpj")))
    with pytest.raises(ValueError, match="not assessed"):
        window.handle_command(dict(action="prepare_brittle_update", workdir=str(tmp_path), expected_sha256="a" * 64))
    assert window.annotation_task is None


def test_duplicate_prepare_does_not_clear_inflight_review(window, tmp_path):
    review = FakeReview(tmp_path / "source.pydpj", tmp_path / "review.json")
    window.annotation_review = review
    window.annotation_task = (object(), object())
    try:
        with pytest.raises(ValueError, match="already running"):
            window.prepare_brittle_update()
        assert window.annotation_review is review
    finally:
        window.annotation_task = None
