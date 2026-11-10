"""Reviewed diameter state and embedded evidence survive normal Builder use."""
from __future__ import annotations

import copy
import hashlib
import logging
from pathlib import Path

import pandas as pd
from PyQt6 import QtCore, QtGui, QtWidgets
import pytest

from microwire_data_builder import core, project_package as pp, safe_codec, ui
from microwire_data_builder.storage import MiniDatabaseData, MiniDatabaseStore


PAYLOAD_ID = "microscope_diameter_evidence_" + "a" * 32
HISTORY_KEY = "microscope_diameter_history"
SAMPLE_KEY = "SyntheticAlloy|1|1"


@pytest.fixture
def isolated(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "private-store"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(tmp_path / "private-builder.ini"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", "1")
    for name, value in (("_memory_data", {}), ("_memory_payloads", {}),
                        ("_payload_loaders", {}), ("_payload_tombstones", set())):
        monkeypatch.setattr(MiniDatabaseStore, name, value)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    created = []

    def create(*, whole_builder=False):
        widget = (ui.BuilderWindow() if whole_builder else
                  ui.MicroscopeSection(logging.getLogger("synthetic-diameter-test"), lambda *_: None))
        qtbot.addWidget(widget)
        if whole_builder:
            widget._auto_open_last = False
            widget._auto_open_latest_database = False
        created.append(widget)
        return widget

    yield create
    for widget in created:
        if isinstance(widget, ui.BuilderWindow):
            widget._dirty = False
        else:
            widget._shutdown_background_threads()


def _row(*, d=5.0, D=80.0):
    return {"Composition": "SyntheticAlloy", "Microwire": "1/1", "_key": SAMPLE_KEY,
            ui.MICROSCOPE_D_COLUMN: d, ui.MICROSCOPE_CAP_D_COLUMN: D,
            "_core_image": None, "_glass_image": None, "_images": [],
            core.BRITTLE_OVERRIDE_COLUMN: False}


def test_microscope_refresh_retains_referenced_diameter_evidence_without_decoding(isolated):
    section = isolated()
    discarded = "microscope_diameter_evidence_" + "b" * 32
    history = [{"event_id": "synthetic-review", "payload_id": PAYLOAD_ID}]
    section.apply_data(MiniDatabaseData(table=pd.DataFrame([_row()]), extra={
        "payloads": {PAYLOAD_ID: PAYLOAD_ID, discarded: discarded}, HISTORY_KEY: history,
        "overrides": {SAMPLE_KEY: {"d": 12.0, "D": 36.0}},
        "validated": {SAMPLE_KEY: {"d": 12.0, "D": 36.0, "d_reviewed": True, "D_reviewed": True}},
    }))

    def unexpected_decode():
        pytest.fail("Microscope refresh eagerly decoded retained diameter evidence")

    section.store.register_payload_loader(PAYLOAD_ID, unexpected_decode)
    section.store.register_payload_loader(discarded, lambda: {"obsolete": True})
    result = section.process([])
    section._handle_worker_finished(result)
    assert section.store.has_payload_loader(PAYLOAD_ID)
    assert section.data.extra["payloads"][PAYLOAD_ID] == PAYLOAD_ID
    assert section.data.extra[HISTORY_KEY] == history
    assert PAYLOAD_ID not in section.store.payload_tombstones()
    assert not section.store.has_payload_loader(discarded)
    assert discarded not in section.data.extra["payloads"]


def _synthetic_project(tmp_path, overrides):
    image = QtGui.QImage(64, 32, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("white"))
    source_image = tmp_path / "image.png"
    assert image.save(str(source_image))
    raw = source_image.read_bytes()
    row = _row()
    # New app captures are linked without relabelling historical core/glass image
    # fields, which would also change legacy glass-only Brittleness inference.
    row.update(_images=[str(source_image)])
    other = _row(d=9.0, D=60.0)
    other.update(Microwire="1/1oe", _key=SAMPLE_KEY + "|oe")
    history = [{"event_id": "synthetic-reviewed-session", "payload_id": PAYLOAD_ID,
                "selected_rows": [1, 2], "image_sha256": hashlib.sha256(raw).hexdigest(),
                "session_id": "c" * 32, "summary": copy.deepcopy(overrides)}]
    evidence = {"files": {"image.png": raw, "session.json": b'{"synthetic":true}'},
                "selected_records": [{"kind": "core", "width_um": overrides["d"]}]}
    extra = {
        "overrides": {SAMPLE_KEY: dict(overrides), SAMPLE_KEY + "|oe": {"d": 9.0, "D": 60.0}},
        "validated": {SAMPLE_KEY: {**overrides, "d_reviewed": True, "D_reviewed": True,
                                    "timestamp": "2026-11-09T12:00:00Z", "sources": [{
                                        "path": str(source_image), "key": str(source_image).lower(),
                                        "mtime": source_image.stat().st_mtime, "size": len(raw)}],
                                    "existing_review_note": "preserve this older review detail"}},
        HISTORY_KEY: history, "payloads": {PAYLOAD_ID: PAYLOAD_ID},
        "existing_setting": {"must_survive": [1, 2, 3]},
    }
    source = tmp_path / "synthetic-source.pydpj"
    pp.write_project_package(source, {"kind": pp.PROJECT_KIND, "sections": {
        "microscope": {"section": "microscope", "title": "Microscope",
                       "columns": list(row), "rows": [row, other], "index": [0, 1],
                       "extra": extra, "sources": [str(source_image)], "processed": {},
                       "payloads": {PAYLOAD_ID: safe_codec.encode_envelope(evidence)}}}})
    return source, extra, evidence


@pytest.mark.parametrize("overrides", [{"d": 12.0, "D": 52.0},
                                       {"d": 8.0, "D": 48.0},
                                       {"d": 12.0, "D": 48.0}], ids=["core-update", "outer-update", "both-update"])
def test_reviewed_diameters_images_history_and_embedded_bytes_survive_builder_rebuild_and_save(
        isolated, tmp_path, overrides):
    source, extra, evidence = _synthetic_project(tmp_path, overrides)
    original = pp.inspect_project_package(source, verify_entries=True)
    original_bytes = source.read_bytes()
    window = isolated(whole_builder=True)
    window.load_project_synchronously_for_automation(source)
    section = window.microscope_section
    row = section.data.table.loc[section.data.table["_key"] == SAMPLE_KEY].iloc[0]
    assert row[ui.MICROSCOPE_D_COLUMN] == overrides["d"]
    assert row[ui.MICROSCOPE_CAP_D_COLUMN] == overrides["D"]
    assert row["d/D"] == round(overrides["d"] / overrides["D"], 3)
    assert section._is_cell_reviewed(SAMPLE_KEY, ui.MICROSCOPE_D_COLUMN)
    assert section._is_cell_reviewed(SAMPLE_KEY, ui.MICROSCOPE_CAP_D_COLUMN)
    assert row["_core_image"] is None and row["_glass_image"] is None
    assert Path(row["_images"][0]).read_bytes() == evidence["files"]["image.png"]
    assert section.store.has_payload_loader(PAYLOAD_ID)
    section._handle_worker_finished(section.process([]))
    assert section.store.has_payload_loader(PAYLOAD_ID)
    assert section.data.extra[HISTORY_KEY] == extra[HISTORY_KEY]
    assert section.data.extra["existing_setting"] == extra["existing_setting"]
    assert section.data.extra["validated"][SAMPLE_KEY]["existing_review_note"].startswith("preserve")
    rebuilt = section._build_microscope_index_from_table(section.data.table)
    assert rebuilt[("SyntheticAlloy", 1, 1, None)].best_core() == overrides["d"]
    assert rebuilt[("SyntheticAlloy", 1, 1, None)].best_glass() == overrides["D"]
    assert rebuilt[("SyntheticAlloy", 1, 1, "oe")].best_core() == 9.0
    assert rebuilt[("SyntheticAlloy", 1, 1, "oe")].best_glass() == 60.0

    saved = tmp_path / "synthetic-builder-resaved.pydpj"
    window._write_project_file(saved)
    assert saved.is_file()
    assert source.read_bytes() == original_bytes
    reopened = pp.inspect_project_package(saved, verify_entries=True)
    payload_path = original.sections["microscope"]["payloads"][PAYLOAD_ID]
    assert reopened.sections["microscope"]["payloads"][PAYLOAD_ID] == payload_path
    assert reopened.read_entry(payload_path) == original.read_entry(payload_path)
    assert safe_codec.decode_envelope(reopened.read_payload("microscope", PAYLOAD_ID)) == evidence
    for digest, descriptor in original.blobs.items():
        assert reopened.blobs[digest] == descriptor
        assert reopened.read_entry(descriptor["path"]) == original.read_entry(descriptor["path"])

    window.load_project_synchronously_for_automation(saved)
    restored = window.microscope_section
    row = restored.data.table.loc[restored.data.table["_key"] == SAMPLE_KEY].iloc[0]
    assert row[ui.MICROSCOPE_D_COLUMN] == overrides["d"]
    assert row[ui.MICROSCOPE_CAP_D_COLUMN] == overrides["D"]
    assert restored.data.extra[HISTORY_KEY] == extra[HISTORY_KEY]
    assert restored.data.extra["existing_setting"] == extra["existing_setting"]
    assert restored._is_cell_reviewed(SAMPLE_KEY, ui.MICROSCOPE_D_COLUMN)
    assert restored._is_cell_reviewed(SAMPLE_KEY, ui.MICROSCOPE_CAP_D_COLUMN)
