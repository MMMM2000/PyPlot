from __future__ import annotations

import copy
import logging
import math
from pathlib import Path

import pandas as pd
import pytest

from microwire_data_builder import ui
from microwire_data_builder.core import MeasurementMetadata, MeasurementRecord


def record(tmp_path: Path, number: int = 1) -> MeasurementRecord:
    name = f"Ni50Fe26Ga23Co1 1_1 run{number}.txt"
    return MeasurementRecord(
        path=tmp_path/name,
        metadata=MeasurementMetadata(
            composition_token="Ni50Fe26Ga23Co1", draw_x=1, piece_y=1,
            setpoint_mA=90, alt_variant=False, measurement_id=f"source-{number}",
            file_name=name, relpath=name, timestamp_mtime_utc="2026-10-05T00:00:00+00:00",
        ),
        dataframe=pd.DataFrame({"I_mA": [1.0, 90.0], "R_Ohm": [100.0, 120.0]}),
        sanity_ok=True, sanity_error=0.0,
    )


def review(r: MeasurementRecord, diameter: float, currents=(68.4, 30.9)) -> dict:
    entries = [{
        "source_path": str(r.path), "composition": r.metadata.composition_token,
        "microwire": "1/1", "cycle": i, "As_label": f"As{i}",
        "core_diameter_um": diameter, "diameter_kind": "author_documented_microscopy",
        "exact_raw_match_verified": True, "raw_sha256": "a"*64,
    } for i in range(1, len(currents)+1)]
    return {"status": ui.TRANSITION_REVIEW_STATUS_MANUAL_ADJUSTED,
            "content_identity": "sha256:"+"a"*64,
            "final_values_mA": {f"As{i}": value for i, value in enumerate(currents, 1)},
            "project_review": {"author_report_entries": entries}}


@pytest.fixture
def sections(qapp):
    annealing = ui.AnnealingSection(logging.getLogger("test"), lambda *_: None)
    microscope = ui.MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    density = ui.CurrentDensitySection(annealing, microscope, logging.getLogger("test"), lambda *_: None)
    yield annealing, microscope, density
    for widget in (density, annealing, microscope):
        widget.hide()
        widget.deleteLater()
    qapp.processEvents()


def calculated(sections, records, reviews, microscope_d=20.0):
    annealing, microscope, density = sections
    key = "Ni50Fe26Ga23Co1|1|1"
    annealing._record_groups = {key: records}
    annealing._all_records = records
    annealing._transition_reviews = reviews
    microscope.data.table = pd.DataFrame([{
        "Composition": "Ni50Fe26Ga23Co1", "Microwire": "1/1",
        ui.MICROSCOPE_D_COLUMN: microscope_d, "_key": key,
    }])
    return density._calculate_frame()


def test_piece_diameter_changes_only_its_acquisition(sections, tmp_path):
    first, sibling = record(tmp_path), record(tmp_path, 2)
    rid = ui._transition_record_id_for_annealing_record(first)
    frame = calculated(sections, [first, sibling], {rid: review(first, 15.7)})
    first_row = frame.loc[frame["_record_id"] == rid].iloc[0]
    other_row = frame.loc[frame["_record_id"] != rid].iloc[0]
    assert first_row[ui.MICROSCOPE_D_COLUMN] == 15.7
    assert first_row[ui.CURRENT_DENSITY_AS_DENSITY_COLUMN] == pytest.approx(0.0684/(math.pi*(.0157/2)**2))
    assert first_row[ui.CURRENT_DENSITY_PER_LABEL_COLUMNS["As2"]] == pytest.approx(0.0309/(math.pi*(.0157/2)**2))
    assert other_row[ui.MICROSCOPE_D_COLUMN] == 20.0
    assert sections[1].data.table.iloc[0][ui.MICROSCOPE_D_COLUMN] == 20.0


@pytest.mark.parametrize("diameter,current", [
    (15.7, 68.4), (15.7, 30.9), (13.4, 48.1), (13.4, 20.5),
    (10.6, 47.3), (10.6, 20.7), (15.8, 50.8), (15.8, 29),
    (16.4, 54.6), (20.9, 69.3), (16.9, 26.7),
])
def test_all_report_diameter_and_current_pairs(sections, tmp_path, diameter, current):
    r = record(tmp_path);rid = ui._transition_record_id_for_annealing_record(r)
    rv = review(r, diameter, (current,));before = copy.deepcopy(rv)
    frame = calculated(sections, [r], {rid: rv}, microscope_d=None)
    row = frame.iloc[0]
    assert row[ui.MICROSCOPE_D_COLUMN] == diameter
    assert row[ui.CURRENT_DENSITY_AS_DENSITY_COLUMN] == pytest.approx((current/1000)/(math.pi*(diameter/2000)**2))
    assert rv == before


def test_conflicting_cycle_diameters_do_not_use_generic_sibling_diameter(sections, tmp_path):
    r = record(tmp_path);rv = review(r, 15.7)
    rv["project_review"]["author_report_entries"][1]["core_diameter_um"] = 19.0
    rid = ui._transition_record_id_for_annealing_record(r)
    row = calculated(sections, [r], {rid: rv}).iloc[0]
    assert pd.isna(row[ui.CURRENT_DENSITY_AS_DENSITY_COLUMN])
    assert "Conflicting acquisition microscopy diameters" in row["Notes"]


@pytest.mark.parametrize("change", ["path", "unverified", "status", "hash", "invalid", "portable_conflict"])
def test_unusable_provenance_cannot_supply_a_diameter(sections, tmp_path, change):
    r = record(tmp_path);rv = review(r, 15.7)
    if change == "path":
        for entry in rv["project_review"]["author_report_entries"]:
            entry["source_path"] = str(tmp_path/"different-piece.txt")
    elif change == "unverified":
        for entry in rv["project_review"]["author_report_entries"]:
            entry["exact_raw_match_verified"] = False
    elif change == "status":
        rv["status"] = ui.TRANSITION_REVIEW_STATUS_NEEDS_ATTENTION
    elif change == "hash":
        rv["content_identity"] = "sha256:"+"b"*64
    elif change == "portable_conflict":
        rv["portable_conflict"] = {"reason": "source review differs"}
    else:
        rv["project_review"]["author_report_entries"][0]["core_diameter_um"] = -1
    rid = ui._transition_record_id_for_annealing_record(r)
    row = calculated(sections, [r], {rid: rv}).iloc[0]
    if change in {"hash", "invalid", "portable_conflict"}:
        assert pd.isna(row[ui.CURRENT_DENSITY_AS_DENSITY_COLUMN])
    else:
        assert row[ui.MICROSCOPE_D_COLUMN] == 20.0


def test_same_piece_microscope_value_is_retained(sections, tmp_path):
    r = record(tmp_path);rid = ui._transition_record_id_for_annealing_record(r)
    row = calculated(sections, [r], {rid: review(r, 15.7)}, microscope_d=15.7).iloc[0]
    assert row[ui.MICROSCOPE_D_COLUMN] == 15.7
