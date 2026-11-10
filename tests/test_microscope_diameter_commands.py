"""Reviewed diameter selection and asynchronous project actions without hardware."""
import copy
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
    settings = QtCore.QSettings(str(tmp_path / "private.ini"), QtCore.QSettings.Format.IniFormat)
    result = MainWindow(enumerate_cameras=False, settings=settings)
    qtbot.addWidget(result)
    result.auto_save.setChecked(False)
    source = tmp_path / "synthetic.pydpj"
    result.project_path.setText(str(source))
    result.output_path.setText(str(tmp_path / "captures"))
    binding = dict(binding_type="project_table_row", section="microscope", row_index=0,
                   composition="Synthetic", microwire="1/2")
    result._catalog_loaded("project", [SampleChoice("Synthetic", "1/2", "project", str(source), "a" * 64, (binding,))])
    result.set_image(demo_image(), camera_id="synthetic-camera")
    result.handle_command(dict(action="calibrate", start=[100, 100], end=[200, 100],
                               known_um=50, source="Synthetic calibration"))
    result.handle_command(dict(action="measure", kind="core", start=[400, 320], end=[400, 360]))
    result.handle_command(dict(action="measure", kind="outer", start=[500, 320], end=[500, 400]))
    yield result
    if result.annotation_task:
        qtbot.waitUntil(lambda: result.annotation_task is None, timeout=5000)
    result.autosave_timer.stop()
    result.dirty = False


def test_cli_diameter_commands_have_explicit_measurement_numbers():
    parser = build_parser()
    assert command_from_args(parser.parse_args(["review", "--measurements", "1", "2", "--note", "Checked"])) == {
        "action": "review", "measurements": [1, 2], "note": "Checked", "unreview": False}
    assert command_from_args(parser.parse_args(["prepare-diameter-update", "--measurements", "2"])) == {
        "action": "prepare_diameter_update", "measurements": [2]}
    assert command_from_args(parser.parse_args(["publish-diameter-update", "--review-sha256", "b" * 64])) == {
        "action": "publish_diameter_update", "review_sha256": "b" * 64}


def test_review_is_explicit_selective_and_saved_in_revisions(window):
    before = copy.deepcopy(window.records)
    window.table.selectRow(0)
    status = window.handle_command(dict(action="review", measurements=[1], note="Core boundary checked"))
    assert status["measurements"][0]["review_status"] == "Reviewed"
    assert window.records[0]["width_um"] == before[0]["width_um"] == 20
    assert window.records[1] == before[1]
    assert window.table.item(0, 6).text() == "Reviewed"
    assert window._selected_measurements() == [1]
    window._autosave(force=True)
    directory = window.active_session
    original = (directory / "session.json").read_bytes()
    window.handle_command(dict(action="review", measurements=[1], unreview=True, note="Needs refocus"))
    revision = window._autosave(force=True)
    payload = json.loads((revision / "session.json").read_text(encoding="utf-8"))
    assert payload["measurements"][0]["review_status"] == "Unreviewed"
    assert len(payload["measurements"][0]["review_history"]) == 2
    assert (directory / "session.json").read_bytes() == original
    assert "review_status" in (revision / "measurements.csv").read_text(encoding="utf-8")


@pytest.mark.parametrize("numbers", [[], [0], [3], [True], [1, 1], None])
def test_bad_review_does_not_change_existing_rows(window, numbers):
    before = copy.deepcopy(window.records)
    with pytest.raises(ValueError):
        window.handle_command(dict(action="review", measurements=numbers))
    assert window.records == before


def test_pixel_only_and_apparent_rows_cannot_be_approved_for_import(window):
    before = copy.deepcopy(window.records)
    window.records[0]["kind"] = "apparent"
    with pytest.raises(ValueError, match="apparent"):
        window.handle_command(dict(action="review", measurements=[1]))
    window.records = before
    window.records[0]["calibration_status"] = "Uncalibrated"
    with pytest.raises(ValueError, match="Calibrated"):
        window.handle_command(dict(action="review", measurements=[1]))
    assert window.records[0]["review_status"] == "Unreviewed"


@dataclass(frozen=True)
class FakeDiameterReview:
    source_path: str
    review_path: str
    session_directory: str
    summaries: dict
    composition: str = "Synthetic"
    microwire: str = "1/2"
    candidate_sha256: str = "b" * 64

    def to_dict(self):
        return asdict(self)


def test_reviewed_diameter_prepare_publish_is_async_bound_and_refreshes_project(window, tmp_path, qtbot, monkeypatch):
    calls = []
    def prepare(source, root, **kwargs):
        calls.append(("prepare", kwargs))
        from data_logging.microscope_logger.diameter_review import summarize_reviewed_diameters
        return FakeDiameterReview(str(source), str(tmp_path / "review" / "review.json"),
            str(kwargs["session_directory"]), summarize_reviewed_diameters(window.records, "Synthetic", "1/2", kwargs["measurement_ids"]))
    def publish(review, **kwargs):
        calls.append(("publish", kwargs))
        return {"published_sha256": "b" * 64}
    monkeypatch.setitem(sys.modules, "data_logging.microscope_logger.project_diameters", SimpleNamespace(
        prepare_diameter_update=prepare, publish_diameter_update=publish))
    refreshed = []
    monkeypatch.setattr(window, "_load_catalog", refreshed.append)
    with pytest.raises(ValueError, match="Reviewed"):
        window.handle_command(dict(action="prepare_diameter_update", measurements=[1, 2]))
    window.handle_command(dict(action="review", measurements=[1, 2], note="Physical boundaries checked"))
    window.handle_command(dict(action="prepare_diameter_update", measurements=[1, 2]))
    qtbot.waitUntil(lambda: window.annotation_task is None, timeout=5000)
    assert window.annotation_job["state"] == "ready"
    assert window.annotation_job["kind"] == "diameters"
    assert calls[0][1]["measurement_ids"] == (1, 2)
    assert window.annotation_job["review"]["summaries"]["outer"]["mean_um"] == 40
    with pytest.raises(ValueError, match="does not match"):
        window.handle_command(dict(action="publish_diameter_update", review_sha256="c" * 64))
    window.notes.setPlainText("Changed after review")
    with pytest.raises(ValueError, match="capture changed after review"):
        window.handle_command(dict(action="publish_diameter_update", review_sha256="b" * 64))
    window.notes.clear()
    window.dirty = False  # Restore the synthetic pre-edit snapshot for the remaining guards.
    reviewed = copy.deepcopy(window.records[0])
    window.records[0]["review_status"] = "Unreviewed"
    with pytest.raises(ValueError, match="measurement changed"):
        window.handle_command(dict(action="publish_diameter_update", review_sha256="b" * 64))
    window.records[0] = reviewed
    window.handle_command(dict(action="publish_diameter_update", review_sha256="b" * 64))
    qtbot.waitUntil(lambda: window.annotation_task is None, timeout=5000)
    assert window.annotation_job["state"] == "published"
    assert window.project_update_receipts[-1]["diameters"]["core"]["mean_um"] == 20
    assert refreshed == ["project"]
    assert calls[1][1]["backup"] == tmp_path / "review" / "original.pydpj"


def test_gui_cancel_does_not_approve_rows(window, monkeypatch):
    window.table.selectRow(0)
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda *_: QtWidgets.QMessageBox.StandardButton.Cancel)
    window._review_selected_gui()
    assert window.records[0]["review_status"] == "Unreviewed"


def test_publication_freezes_mutating_commands_and_review_withdrawal(window):
    before = copy.deepcopy(window.records)
    window.annotation_task = (object(), object())
    window.annotation_job = dict(kind="diameters", state="publishing")
    try:
        for command in (dict(action="review", measurements=[1], unreview=True),
                        dict(action="configure", objective="10x"), dict(action="restore_session", path="anything"),
                        dict(action="measure", start=[0, 0], end=[0, 10])):
            with pytest.raises(ValueError, match="Wait for diameter publication"):
                window.handle_command(command)
        with pytest.raises(ValueError, match="Wait for the project publication"):
            window.review_measurements([1], unreview=True)
        assert window.handle_command(dict(action="status"))["project_update"]["state"] == "publishing"
        assert window.records == before
    finally:
        window.annotation_task = None


def test_complete_app_review_and_publication_to_synthetic_project(window, qtbot):
    from microwire_data_builder import project_package as pp, ui
    from data_logging.microscope_logger.samples import load_project_samples
    source = Path(window.project_path.text())
    rows = []
    for wire, key in (("1/2", "Synthetic|1|2"), ("1/3", "Synthetic|1|3")):
        row = {column: None for column in ui.MICROSCOPE_TABLE_COLUMNS}
        row.update(Composition="Synthetic", Microwire=wire, _key=key, _images=[])
        rows.append(row)
    pp.write_project_package(source, dict(kind=pp.PROJECT_KIND, sections={
        "microscope": dict(section="microscope", columns=list(ui.MICROSCOPE_TABLE_COLUMNS), rows=rows,
                           index=[0, 1], extra={}, sources=[], processed={}, payloads={}),
        "assemble": dict(section="assemble", columns=["Composition", "Microwire", "d (µm)", "D (µm)", "d/D"],
                         rows=[{"Composition": "Synthetic", "Microwire": "1/2", "d (µm)": None, "D (µm)": None, "d/D": None}],
                         index=[0], extra={}, sources=[], processed={}, payloads={})}))
    original = source.read_bytes()
    window._catalog_loaded("project", load_project_samples(source))
    window.records.clear()
    window.handle_command(dict(action="measure", kind="core", start=[400, 320], end=[400, 360]))
    window.handle_command(dict(action="measure", kind="outer", start=[500, 320], end=[500, 400]))
    window.handle_command(dict(action="review", measurements=[1, 2], note="Synthetic acceptance only"))
    window.handle_command(dict(action="prepare_diameter_update", measurements=[1, 2]))
    qtbot.waitUntil(lambda: window.annotation_task is None, timeout=90000)
    assert window.annotation_job["state"] == "ready", window.annotation_job
    review = window.diameter_review
    window.handle_command(dict(action="publish_diameter_update", review_sha256=review.candidate_sha256))
    qtbot.waitUntil(lambda: window.annotation_task is None, timeout=90000)
    assert window.annotation_job["state"] == "published", window.annotation_job
    qtbot.waitUntil(lambda: not window.catalog_tasks, timeout=15000)
    assert (Path(review.review_path).parent / "original.pydpj").read_bytes() == original
    index = pp.inspect_project_package(source, verify_entries=True)
    row = index.read_section("microscope", load_payloads=False)["rows"][0]
    assert row["d (µm)"] == 20 and row["D (µm)"] == 40 and row["d/D"] == 0.5
    assert index.read_section("microscope", load_payloads=False)["rows"][1]["d (µm)"] is None
    assert window._sample()["source_sha256"] != "a" * 64
    assert window._sample()["source_sha256"] == window.annotation_job["receipt"]["published_sha256"]
