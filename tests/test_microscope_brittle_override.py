"""Explicit brittleness annotations override legacy image inference without data loss."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from microwire_data_builder import core
from microwire_data_builder import ui
from microwire_data_builder.storage import MiniDatabaseData, MiniDatabaseStore


@pytest.fixture
def section(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "private-store"))
    monkeypatch.setattr(MiniDatabaseStore, "_memory_data", {})
    monkeypatch.setattr(MiniDatabaseStore, "_memory_payloads", {})
    monkeypatch.setattr(MiniDatabaseStore, "_payload_loaders", {})
    monkeypatch.setattr(MiniDatabaseStore, "_payload_tombstones", set())
    result = ui.MicroscopeSection(logging.getLogger("synthetic-brittle-test"), lambda *_: None)
    qtbot.addWidget(result)
    yield result
    result._shutdown_background_threads()
    result.close()


def row(tmp_path, override="absent", *, brittle_name=False, core_image=False):
    glass = tmp_path / ("SyntheticAlloy 1_1 glass brittle.jpg" if brittle_name else "SyntheticAlloy 1_1 glass.jpg")
    result = {
        "Composition": "SyntheticAlloy", "Microwire": "1/1", "_key": "SyntheticAlloy|1|1",
        ui.MICROSCOPE_D_COLUMN: None, ui.MICROSCOPE_CAP_D_COLUMN: 30.0,
        core.BRITTLE_COLUMN: None, "_glass_image": str(glass),
        "_core_image": str(tmp_path / "SyntheticAlloy 1_1 core.jpg") if core_image else None,
        "_images": [str(glass)],
    }
    if override != "absent":
        result[core.BRITTLE_OVERRIDE_COLUMN] = override
    return result


@pytest.mark.parametrize("flag", [False, np.bool_(False)])
@pytest.mark.parametrize("brittle_name", [False, True])
def test_explicit_false_suppresses_glass_only_and_filename_inference(section, tmp_path, flag, brittle_name):
    data = row(tmp_path, flag, brittle_name=brittle_name)
    data[core.BRITTLE_COLUMN] = "brittle"
    section.apply_data(MiniDatabaseData(table=pd.DataFrame([data])))
    actual = section.data.table.iloc[0]
    assert actual[core.BRITTLE_COLUMN] is None
    assert core.brittle_override_value(actual[core.BRITTLE_OVERRIDE_COLUMN]) is False
    section._normalise_brittle_column()
    assert section.data.table.iloc[0][core.BRITTLE_COLUMN] is None
    assert "missing core label" in section._status_for_row(actual)


def test_explicit_true_wins_without_a_brittle_image_name(section, tmp_path):
    section.apply_data(MiniDatabaseData(table=pd.DataFrame([row(tmp_path, True, core_image=True)])))
    actual = section.data.table.iloc[0]
    assert actual[core.BRITTLE_COLUMN] == "brittle"
    assert core.brittle_override_value(actual[core.BRITTLE_OVERRIDE_COLUMN]) is True


@pytest.mark.parametrize("override", ["absent", None, "false", "true", 0, 1])
def test_missing_or_non_boolean_override_keeps_historical_inference(section, tmp_path, override):
    section.apply_data(MiniDatabaseData(table=pd.DataFrame([row(tmp_path, override)])))
    assert section.data.table.iloc[0][core.BRITTLE_COLUMN] == "brittle"


def test_legacy_core_and_glass_without_override_are_not_brittle(section, tmp_path):
    section.apply_data(MiniDatabaseData(table=pd.DataFrame([row(tmp_path, core_image=True)])))
    assert section.data.table.iloc[0][core.BRITTLE_COLUMN] is None


def test_explicit_false_survives_project_section_roundtrip_and_diameter_transforms(section, tmp_path):
    section.apply_data(MiniDatabaseData(table=pd.DataFrame([row(tmp_path, False, brittle_name=True)])))
    payload = section.export_project_payload()
    assert payload["rows"][0][core.BRITTLE_OVERRIDE_COLUMN] is False
    section.import_project_payload(payload)
    section._apply_overrides_to_table()
    actual = section.data.table.iloc[0]
    assert actual[core.BRITTLE_COLUMN] is None
    assert core.brittle_override_value(actual[core.BRITTLE_OVERRIDE_COLUMN]) is False
    key = ("SyntheticAlloy", 1, 1, None)
    index = section._build_microscope_index_from_table(section.data.table)
    assert index[key].brittle is False
    assert index[key].brittle_override is False
    cloned = ui._apply_microscope_overrides(index, {})
    assert cloned[key].brittle_override is False
    frame = ui._microscope_index_to_frame(cloned, {})
    assert frame.iloc[0][core.BRITTLE_COLUMN] is None
    assert core.brittle_override_value(frame.iloc[0][core.BRITTLE_OVERRIDE_COLUMN]) is False


@pytest.mark.parametrize("flag", [False, True])
def test_merge_does_not_replace_explicit_flags_with_worker_inference(section, tmp_path, flag):
    existing = pd.DataFrame([row(tmp_path, flag)])
    inferred = {"_key": "SyntheticAlloy|1|1", core.BRITTLE_COLUMN: "brittle",
                core.BRITTLE_OVERRIDE_COLUMN: None}
    merged = section._merge_rows_into_frame(existing, [inferred])
    assert core.brittle_override_value(merged.iloc[0][core.BRITTLE_OVERRIDE_COLUMN]) is flag
    assert merged.iloc[0][core.BRITTLE_COLUMN] == ("brittle" if flag else None)
    changed = section._merge_rows_into_frame(merged, [{"_key": "SyntheticAlloy|1|1",
        core.BRITTLE_OVERRIDE_COLUMN: not flag}])
    assert core.brittle_override_value(changed.iloc[0][core.BRITTLE_OVERRIDE_COLUMN]) is not flag


def test_image_ref_refresh_keeps_false_override(section, tmp_path):
    existing = row(tmp_path, False, brittle_name=True)
    glass = str(tmp_path / "Ni50Fe27Ga23 1_1 glass brittle.jpg")
    existing.update(Composition="Ni50Fe27Ga23", _key="Ni50Fe27Ga23|1|1", _glass_image=glass, _images=[glass])
    section.apply_data(MiniDatabaseData(table=pd.DataFrame([existing])))
    from pathlib import Path
    rows = section._build_image_ref_rows([Path(existing["_glass_image"])],
        {("Ni50Fe27Ga23", 1, 1, None)}, section.data.table)
    assert rows[0][core.BRITTLE_OVERRIDE_COLUMN] is False
    assert rows[0][core.BRITTLE_COLUMN] is None


def test_expected_key_filter_preserves_explicit_annotation(section, tmp_path):
    section.apply_data(MiniDatabaseData(table=pd.DataFrame([row(tmp_path, False)])))
    frame = section._build_initial_table_frame({("DifferentSyntheticAlloy", 9, 9, None)})
    annotated = frame.loc[frame["Composition"] == "SyntheticAlloy"].iloc[0]
    assert core.brittle_override_value(annotated[core.BRITTLE_OVERRIDE_COLUMN]) is False


def test_legacy_empty_table_adds_expected_rows_with_unknown_override(section):
    section.apply_data(MiniDatabaseData(table=pd.DataFrame()))
    frame = section._build_initial_table_frame({("SyntheticAlloy", 1, 1, None)})
    assert list(frame.columns) == ui.MICROSCOPE_TABLE_COLUMNS
    assert len(frame) == 1
    assert frame.iloc[0][core.BRITTLE_OVERRIDE_COLUMN] is None


def test_incremental_measurement_keeps_legacy_inference_unless_explicit(section, tmp_path):
    key = ("SyntheticAlloy", 1, 1, None)
    legacy = row(tmp_path)
    legacy[core.BRITTLE_COLUMN] = "brittle"
    incoming = section._record_to_row(key, core.MicroscopeMeasurements())
    assert core.BRITTLE_COLUMN not in incoming
    merged = section._merge_rows_into_frame(pd.DataFrame([legacy]), [incoming])
    assert merged.iloc[0][core.BRITTLE_COLUMN] == "brittle"
    incoming = section._record_to_row(key, core.MicroscopeMeasurements(brittle_override=False))
    merged = section._merge_rows_into_frame(merged, [incoming])
    assert merged.iloc[0][core.BRITTLE_COLUMN] is None


@pytest.mark.parametrize("flag", [False, True])
def test_core_database_output_honors_explicit_bool(tmp_path, flag):
    key = ("SyntheticAlloy", 1, 1, None)
    index = {key: core.MicroscopeMeasurements(brittle=not flag, brittle_override=flag)}
    result = core.build_database(core.BuilderConfig(fabrication_files=[], annealing_files=[],
        output_dir=tmp_path / "out"), measurement_records=[], microscope_index=index, skip_exports=True)
    assert result.dataframe.iloc[0][core.BRITTLE_COLUMN] == ("brittle" if flag else None)
    assert core.BRITTLE_OVERRIDE_COLUMN not in result.dataframe.columns
