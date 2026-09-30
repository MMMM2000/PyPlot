from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import launcher
from microwire_data_builder import project_package, safe_codec, storage, ui
from microwire_data_builder import universal_video_builder as uvb


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_storage_root", lambda: tmp_path / "store")
    cls = storage.MiniDatabaseStore
    for name in ("_memory_data", "_memory_payloads", "_pending_section_values",
                 "_pending_payload_values", "_payload_loaders"):
        monkeypatch.setattr(cls, name, {})
    for name in ("_pending_sections", "_pending_payloads", "_blocked_sections",
                 "_blocked_payloads", "_payload_tombstones"):
        monkeypatch.setattr(cls, name, set())
    monkeypatch.setattr(cls, "_memory_transactions", [])
    monkeypatch.setattr(cls, "_disk_writes_suspended", 0)


def _record(path: Path, sample: str = "Ni50Fe27Ga23 1_1"):
    return ui.VsmHysteresisRecord(
        path=path, sample=sample,
        data=pd.DataFrame({"field": np.arange(100, dtype=float),
                           "signal": np.arange(100, dtype=float) / 100}),
        key=("Ni50Fe27Ga23", 1, 1), temperature=120, label="T120C · NG CA",
    )


def _project(section, records):
    column = (ui.VSM_HYSTERESIS_COLUMN if section == "vsm_hysteresis"
              else ui.VSM_TEMPERATURE_SCAN_COLUMN)
    table = ui._graph_records_to_frame(records, column, sample_column="_sample")
    return {"kind": "MicrowireDataBuilder", "version": 3, "sections": {
        section: {"section": section, "title": section,
                  "columns": table.columns.tolist(),
                  "rows": table.to_dict(orient="records"),
                  "index": table.index.tolist(), "extra": {},
                  "sources": [], "processed": {},
                  "payloads": {section + "_records": safe_codec.encode_envelope(records)}}}}


def test_package_record_loading_bounds_each_record_without_dropping_a_large_list(
    tmp_path, monkeypatch,
):
    records = [_record(tmp_path / f"loop{i}.dat") for i in range(3)]
    target = tmp_path / "large-list.pydpj"
    index = project_package.write_project_package(target, _project("vsm_hysteresis", records))
    monkeypatch.setattr(safe_codec, "MAX_DECODE_ITEMS", 1000)
    with pytest.raises(safe_codec.SafeCodecError, match="Aggregate codec item budget"):
        safe_codec.decode_envelope(project_package.load_project(target)["sections"]["vsm_hysteresis"]["payloads"]["vsm_hysteresis_records"])
    assert len(project_package.ProjectPayloadResolver(index).load("vsm_hysteresis", "vsm_hysteresis_records")) == 3
    restored = project_package.ProjectPayloadResolver(index).load_record_list(
        "vsm_hysteresis", "vsm_hysteresis_records"
    )
    assert [r.path for r in restored] == [r.path for r in records]
    for actual, expected in zip(restored, records):
        pd.testing.assert_frame_equal(actual.data, expected.data)
    selected = project_package.ProjectPayloadResolver(index).load_records_for_paths(
        "vsm_hysteresis", "vsm_hysteresis_records", [r.path for r in records]
    )
    assert len(selected) == 3
    with pytest.raises(safe_codec.SafeCodecError):
        project_package.ProjectPayloadResolver(index, budget=project_package.ReadBudget(limit=1)).load_record_list(
            "vsm_hysteresis", "vsm_hysteresis_records"
        )


def test_hysteresis_preview_does_not_rewrite_saved_sample_or_label(qtbot, tmp_path):
    section = ui.VsmHysteresisSection(logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(section)
    record = _record(tmp_path / "Ni50Fe27Ga23 1_1 NG CA" / "loop.dat",
                     sample="Ni50Fe27Ga23 1_1 NG CA")
    before = (record.sample, record.label)
    section._set_record_groups([record])
    assert (record.sample, record.label) == before


@pytest.mark.parametrize("record_class", [ui.VsmHysteresisRecord, ui.FmrRecord,
                                           ui.DmaIsoStressRecord, ui.ShapeMemoryStressStrainRecord])
def test_treatment_variant_survives_safe_record_round_trip(record_class, tmp_path):
    values = dict(path=tmp_path / "record.dat", sample="Ni50Fe27Ga23 1_1", label="NG CA")
    if record_class is ui.DmaIsoStressRecord:
        values["datasets"] = {1: ([0.0, 1.0], [0.0, 2.0])}
    else:
        values["data"] = pd.DataFrame({"x": [1.0]})
    record = record_class(**values)
    record.variant = "NG CA"
    restored = safe_codec.decode_envelope(safe_codec.encode_envelope(record))
    assert restored.variant == "NG CA"


def test_unreadable_refresh_preserves_previous_curve_and_saved_table(qtbot, tmp_path):
    source = tmp_path / "sources" / "scan.txt"
    source.parent.mkdir()
    source.write_text("temporarily unavailable numeric data", encoding="utf-8")
    record = ui.VsmTemperatureScanRecord(
        path=source, sample="Ni50Fe27Ga23 1_1 NG CA",
        data=pd.DataFrame({"temperature": [20.0, 30.0], "signal": [1.0, 2.0],
                           "field": [50.0, 50.0], "section": [0, 0]}),
        key=("Ni50Fe27Ga23", 1, 1), label="reviewed old scan",
    )
    project = tmp_path / "source.pydpj"
    payload = _project("vsm_temperature_scan", [record])
    project_package.write_project_package(project, payload)
    old_bytes = project.read_bytes()
    output = tmp_path / "updated.pydpj"
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"kind": "builder", "version": 1,
        "project": str(project), "output_project": str(output),
        "working_copy_dir": str(tmp_path / "working"),
        "commands": [{"action": "update_section", "section": "vsm_temperature_scan",
                      "paths": [str(source.parent)]}]}), encoding="utf-8")
    sentinel = object()
    original_memory = {"caller-state": sentinel}
    storage.MiniDatabaseStore._memory_payloads = original_memory
    original_loaders = {("unrelated", "records"): lambda: sentinel}
    storage.MiniDatabaseStore._payload_loaders = original_loaders
    assert launcher._run_builder_automation_recipe(recipe) == 0
    assert storage.MiniDatabaseStore._memory_payloads is original_memory
    assert storage.MiniDatabaseStore._payload_loaders is original_loaders
    updated = project_package.load_project(output)
    restored = safe_codec.decode_envelope(updated["sections"]["vsm_temperature_scan"]["payloads"]["vsm_temperature_scan_records"])
    assert len(restored) == 1
    assert restored[0].label == record.label
    pd.testing.assert_frame_equal(restored[0].data, record.data)
    assert project.read_bytes() == old_bytes
    manifest = json.loads(output.with_suffix(".manifest.json").read_text())
    assert manifest["commands"][0]["skipped_count"] == 1
    assert manifest["commands"][0]["retained_skipped_count"] == 1


def _promotion_paths(tmp_path):
    db = tmp_path / "database"
    db.mkdir()
    paths = launcher._builder_database_paths(
        {"database_dir": str(db), "timestamp": "synthetic"}, base_dir=tmp_path)
    paths["latest_project"].write_bytes(b"old project")
    paths["latest_manifest"].write_text('{"status":"old"}', encoding="utf-8")
    output = tmp_path / "output.pydpj"
    output.write_bytes(b"new project")
    return paths, output


@pytest.mark.parametrize("failed_target", ["working", "latest"])
def test_database_promotion_rolls_back_project_when_manifest_write_fails(
    tmp_path, monkeypatch, failed_target,
):
    paths, output = _promotion_paths(tmp_path)
    working = tmp_path / "working.json"
    failed_path = working if failed_target == "working" else paths["latest_manifest"]
    original = launcher._write_json
    def fail_once(path, payload):
        if path == failed_path:
            raise OSError("synthetic manifest write failure")
        original(path, payload)
    monkeypatch.setattr(launcher, "_write_json", fail_once)
    with pytest.raises(OSError, match="synthetic manifest write failure"):
        launcher._promote_builder_database_latest(
            database_paths=paths, output_project=output, manifest_path=working,
            manifest={"status": "ok", "commands": []})
    assert paths["latest_project"].read_bytes() == b"old project"
    assert json.loads(paths["latest_manifest"].read_text()) == {"status": "old"}


def test_database_promotion_rejects_a_concurrent_live_project_edit(tmp_path):
    paths, output = _promotion_paths(tmp_path)
    paths["expected_latest_project_sha256"] = hashlib.sha256(b"old project").hexdigest()
    paths["latest_project"].write_bytes(b"a newer manual save")
    with pytest.raises(launcher._AutomationRecipeError, match="changed"):
        launcher._promote_builder_database_latest(
            database_paths=paths, output_project=output,
            manifest_path=tmp_path / "working.json", manifest={"status": "ok"})
    assert paths["latest_project"].read_bytes() == b"a newer manual save"


@pytest.mark.parametrize("section_class", [ui.VideoSection, uvb.UniversalVideoSection])
def test_video_cumulative_length_does_not_guess_across_missing_pieces(qtbot, section_class):
    section = section_class(logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(section)
    frame = pd.DataFrame([
        {"Composition": "Ni50Fe27Ga23", "Draw": 1, "Piece": 1, "Length (m)": 5.0},
        {"Composition": "Ni50Fe27Ga23", "Draw": 1, "Piece": 3, "Length (m)": 10.0},
    ])
    cumulative = section._build_cumulative_lengths(frame, frame)
    assert cumulative[("Ni50Fe27Ga23", 1, 1)] == 5.0
    assert cumulative[("Ni50Fe27Ga23", 1, 3)] is None


@pytest.mark.parametrize("length,end,cumulative", [(12.56,105.75,18.84), (0.3,1.4,0.3)])
def test_video_range_retains_fractional_metres(qtbot, length, end, cumulative):
    section = ui.VideoSection(logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(section)
    row={"Composition":"Ni50Fe27Ga23","Draw":12,"Piece":2,
         "Length (m)":length,ui.VIDEO_END_LENGTH_COLUMN:end}
    expected = "86.91-99.47" if length == 12.56 else "1.1-1.4"
    assert section._compute_video_range(row,{("Ni50Fe27Ga23",12,2):cumulative}) == expected


@pytest.mark.parametrize("length,end,cumulative", [(-1,10,2), (1,-10,2), (5,3,5), (0,10,0)])
def test_video_range_rejects_impossible_intervals(qtbot, length, end, cumulative):
    section = ui.VideoSection(logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(section)
    row={"Composition":"Ni50Fe27Ga23","Draw":1,"Piece":1,
         "Length (m)":length,ui.VIDEO_END_LENGTH_COLUMN:end}
    assert section._compute_video_range(row,{("Ni50Fe27Ga23",1,1):cumulative}) is None


def test_video_range_recalculates_after_a_manual_length_override(qtbot):
    section = ui.VideoSection(logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(section)
    section._overrides = {"Ni50Fe27Ga23|1|1": {"Length (m)": 5.0}}
    frame = pd.DataFrame([{"Composition": "Ni50Fe27Ga23", "Draw": 1, "Piece": 1,
        "_group_key": "Ni50Fe27Ga23|1|1", "Length (m)": 10.0,
        ui.VIDEO_END_LENGTH_COLUMN: 10.0}])
    updated = section._apply_overrides_to_table(frame)
    assert updated.iloc[0][ui.VIDEO_MW_LENGTH_COLUMN] == "5-10"
    assert updated.iloc[0]["_cumulative_length_m"] == 5.0


def test_partial_fabrication_refresh_keeps_other_pieces_and_manual_values(qtbot, tmp_path, monkeypatch):
    index = ui.FabricationIndex()
    index.set_piece("Ni50Fe27Ga23", 1, 1, {"length_m": 5.0})
    frame = ui._fabrication_index_to_frame(index)
    frame[ui.GLASS_TEMPERATURE_COLUMN] = 212.4
    sections = {"fabrication": {"section": "fabrication", "extra": {},
        "columns": frame.columns.tolist(), "rows": frame.to_dict(orient="records"),
        "payloads": {"fabrication_index": index, "fabrication_index_raw": index}}}
    incoming = ui.FabricationIndex()
    incoming.set_piece("Ni50Fe27Ga23", 2, 1, {"length_m": 7.0})
    monkeypatch.setattr(ui.FabricationSection, "process", lambda *_: ui.SectionProcessResult(
        table=ui._fabrication_index_to_frame(incoming), processed={},
        payloads={"fabrication_index": incoming, "fabrication_index_raw": incoming}))
    folder = tmp_path / "new-spreadsheets"
    folder.mkdir()
    launcher._run_builder_update_section_command(builder_ui=ui,
        command={"paths": [str(folder)]}, command_index=0, section_name="fabrication",
        sections=sections, base_dir=tmp_path)
    result = sections["fabrication"]
    for name in ("fabrication_index", "fabrication_index_raw"):
        restored = safe_codec.decode_envelope(result["payloads"][name])
        assert restored.get_piece("Ni50Fe27Ga23", 1, 1)["length_m"] == 5.0
        assert restored.get_piece("Ni50Fe27Ga23", 2, 1)["length_m"] == 7.0
    saved = pd.DataFrame(result["rows"])
    row = saved[(saved.Draw == 1) & (saved.Piece == 1)].iloc[0]
    assert row[ui.GLASS_TEMPERATURE_COLUMN] == 212.4


def test_record_decoder_keeps_all_safety_limits(tmp_path, monkeypatch):
    record = _record(tmp_path / "a.dat")
    payload = safe_codec.encode_envelope([record, record])
    with pytest.raises(safe_codec.SafeCodecError, match="byte budget"):
        safe_codec.decode_record_list_envelope(payload, max_total_bytes=1)
    monkeypatch.setattr(safe_codec, "MAX_DECODE_ITEMS", 100)
    with pytest.raises(safe_codec.SafeCodecError, match="item budget"):
        safe_codec.decode_record_list_envelope(payload)
    with pytest.raises(safe_codec.SafeCodecError, match="record"):
        safe_codec.decode_record_list_envelope(safe_codec.encode_envelope(["not a record"]))


def test_corrupt_modern_payload_stops_an_assemble_rebuild():
    payload = {"encoding": "microwire-json", "version": 1, "value": {"$type": "invalid"}}
    with pytest.raises(launcher._AutomationRecipeError, match="Cannot restore"):
        launcher._decode_builder_section_payload(ui,
            {"vsm_hysteresis": {"payloads": {"vsm_hysteresis_records": payload}}},
            "vsm_hysteresis", "vsm_hysteresis_records")


@pytest.mark.skipif(os.name != "nt", reason="Windows path spelling is case-insensitive")
def test_reimported_windows_path_replaces_the_existing_curve(tmp_path):
    previous = _record(tmp_path / "Scan.dat")
    incoming = _record(tmp_path / "SCAN.DAT")
    incoming.temperature = 200
    merged = launcher._merge_builder_records([previous], [incoming])
    assert len(merged) == 1 and merged[0] is incoming


@pytest.mark.parametrize("section_class,record_class,payload_name", [
    (ui.DmaIsoStressSection, ui.DmaIsoStressRecord, "dma_iso_stress_records"),
    (ui.ShapeMemoryStressStrainSection, ui.ShapeMemoryStressStrainRecord, "shape_memory_stress_strain_records"),
    (ui.FmrSection, ui.FmrRecord, "fmr_records"),
])
def test_graph_previews_preserve_original_record_metadata(qtbot, tmp_path, section_class, record_class, payload_name):
    section = section_class(logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(section)
    values = {"path": tmp_path / "record.dat", "sample": "Ni50Fe27Ga23 1_1 NG CA", "label": "manual label"}
    if record_class is ui.DmaIsoStressRecord:
        values["datasets"] = {1: ([0.0, 1.0], [0.0, 2.0])}
    else:
        values["data"] = pd.DataFrame({"x": [1.0]})
    record = record_class(**values)
    section.store.save_payload(payload_name, [record])
    section._refresh_record_groups()
    assert (record.sample, record.label, record.variant) == (values["sample"], "manual label", None)


def test_archive_preparation_failure_leaves_database_and_no_temp_files(tmp_path, monkeypatch):
    paths, output = _promotion_paths(tmp_path)
    original = launcher._prepare_file_archive
    def prepare(source, archive):
        if source == paths["latest_manifest"]:
            raise OSError("second archive failed")
        return original(source, archive)
    monkeypatch.setattr(launcher, "_prepare_file_archive", prepare)
    with pytest.raises(OSError, match="second archive failed"):
        launcher._promote_builder_database_latest(database_paths=paths, output_project=output,
            manifest_path=tmp_path / "manifest.json", manifest={"status": "ok"})
    assert paths["latest_project"].read_bytes() == b"old project"
    assert list(paths["archive_dir"].iterdir()) == []


def test_video_refresh_restores_fabrication_lengths_and_manual_readings(qtbot, tmp_path):
    index = ui.FabricationIndex()
    index.set_piece("Ni50Fe27Ga23", 12, 1, {"length_m": 6.28})
    index.set_piece("Ni50Fe27Ga23", 12, 2, {"length_m": 12.56})
    fabrication = ui._fabrication_index_to_frame(index)
    summary = ui.VideoMetricsSummary(sources={tmp_path / "video.mkv"})
    videos = {("Ni50Fe27Ga23", 12, 2): summary}
    frame = ui._video_index_to_frame(videos, fabrication)
    frame[ui.VIDEO_END_LENGTH_COLUMN] = 105.75
    frame[ui.CORE_TEMPERATURE_COLUMN] = 998.2
    def section(name, table, payloads):
        return {"section": name, "extra": {}, "columns": table.columns.tolist(),
            "rows": table.to_dict(orient="records"), "payloads": {
                key: safe_codec.encode_envelope(value) for key, value in payloads.items()}}
    source = tmp_path / "source.pydpj"
    project_package.write_project_package(source, {"kind": "MicrowireDataBuilder", "version": 3,
        "sections": {"fabrication": section("fabrication", fabrication,
            {"fabrication_index": index, "fabrication_index_raw": index}),
            "videos": section("videos", frame, {"video_index": videos})}})
    folder = tmp_path / "new-videos"
    folder.mkdir()
    output = tmp_path / "output.pydpj"
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"kind": "builder", "version": 1, "project": str(source),
        "output_project": str(output), "working_copy_dir": str(tmp_path / "working"),
        "commands": [{"action": "update_section", "section": "videos", "paths": [str(folder)]}]}))
    assert launcher._run_builder_automation_recipe(recipe) == 0
    saved = pd.DataFrame(project_package.load_project(output)["sections"]["videos"]["rows"])
    row = saved[(saved.Draw == 12) & (saved.Piece == 2)].iloc[0]
    assert row[ui.CORE_TEMPERATURE_COLUMN] == 998.2
    assert row[ui.VIDEO_MW_LENGTH_COLUMN] == "86.91-99.47"


def test_promotion_copy_rechecks_target_immediately_before_replacement(tmp_path, monkeypatch):
    paths, output = _promotion_paths(tmp_path)
    original = launcher._fsync_file
    def concurrent_save(path):
        original(path)
        if path.name.startswith(".microwire_database_latest.pydpj."):
            paths["latest_project"].write_bytes(b"new manual save while copying")
    monkeypatch.setattr(launcher, "_fsync_file", concurrent_save)
    with pytest.raises(launcher._AutomationRecipeError, match="changed"):
        launcher._promote_builder_database_latest(database_paths=paths, output_project=output,
            manifest_path=tmp_path / "manifest.json", manifest={"status": "ok"})
    assert paths["latest_project"].read_bytes() == b"new manual save while copying"


def test_zero_and_nonfinite_piece_lengths_block_following_intervals():
    from microwire_data_builder.video_lengths import cumulative_piece_lengths
    for invalid in (0.0, -1.0, float("nan"), float("inf"), None):
        lengths = {("Comp", 1, 1): invalid, ("Comp", 1, 2): 2.0}
        assert cumulative_piece_lengths(lengths) == {("Comp", 1, 1): None, ("Comp", 1, 2): None}


def test_vsm_merge_does_not_match_missing_sample_names_or_blank_sources():
    column = ui.VSM_HYSTERESIS_COLUMN
    old = pd.DataFrame([{"_sample": None, "_sources": None, column: None, "note": "old"}])
    new = pd.DataFrame([{"_sample": None, "_sources": ["b.dat"], column: ["new"]}])
    result = launcher._merge_builder_vsm_table_rows(old, new, column)
    assert len(result) == 2 and result.iloc[0]["note"] == "old"


def test_manifest_cannot_overwrite_the_source_project(qtbot, tmp_path):
    source = tmp_path / "source.pydpj"
    project_package.write_project_package(source, {"kind": "MicrowireDataBuilder", "version": 3, "sections": {}})
    source_bytes = source.read_bytes()
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"kind": "builder", "version": 1, "project": str(source),
        "manifest_path": str(source), "output_project": str(tmp_path / "output.pydpj"),
        "working_copy_dir": str(tmp_path / "working"), "commands": [{"action": "rebuild_assemble"}]}))
    assert launcher._run_builder_automation_recipe(recipe) == 2
    assert source.read_bytes() == source_bytes


def test_records_without_source_paths_do_not_overwrite_each_other(tmp_path):
    a, b = _record(tmp_path / "a.dat"), _record(tmp_path / "b.dat")
    a.path, b.path = None, None
    merged = launcher._merge_builder_records([a], [b])
    assert len(merged) == 2 and merged[0] is a and merged[1] is b


def test_legacy_hysteresis_preview_retains_explicit_treatment_in_saved_label(qtbot, tmp_path):
    section = ui.VsmHysteresisSection(logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(section)
    record = _record(tmp_path / "old.dat")
    section._set_record_groups([record])
    display = next(iter(section._record_groups.values()))[0]
    assert display.variant == "NG CA" and "NG CA" in display.label
    assert record.variant is None and record.label == "T120C · NG CA"


def test_legacy_treatments_do_not_merge_into_one_hysteresis_plot_group(tmp_path):
    a, b = _record(tmp_path / "a.dat"), _record(tmp_path / "b.dat")
    b.label = "T120C — as cast"
    groups = ui._group_vsm_hysteresis_plot_groups([a, b])
    assert len(groups) == 2
    assert {group.variant for group in groups} == {"NG CA", "as cast"}
    assert a.variant is None and b.variant is None
