from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from plotting.plugins.mini_dma import core
from plotting.plugins.mini_dma.cycle_strain import cycle_strain_metrics


def cycle_frame() -> pd.DataFrame:
    current = [0, 10, 20, 30, 40, 50, 40, 30, 20, 10, 0]
    position = [4, 4, 3, 2, 1, 0, 0.6, 1.2, 1.8, 2.4, 3]
    return pd.DataFrame({
        "elapsed_s": np.arange(11, dtype=float), "current_mA": current,
        "current_set_mA": current, "strain_pct": position, "position_mm": position,
        "automation_phase": "current", "automation_target_value": 50.0,
        "plateau_index": 0.0, "resistance_ohm": 10.0,
    })


def write_run(tmp_path: Path, *, repeated: bool = False) -> Path:
    path = tmp_path / "Synthetic 1_1 iso-stress"
    path.mkdir()
    frame = cycle_frame()
    if repeated:
        frame["plateau_index"] = np.nan
        second = cycle_frame()
        second["elapsed_s"] += 20
        second["position_mm"] *= 0.5
        second["strain_pct"] *= 0.5
        frame = pd.concat([frame, second], ignore_index=True)
    frame.to_csv(path / "measurement.csv", index=False)
    (path / "metadata.json").write_text(json.dumps({
        "initial_length_mm": 100.0,
        "current_sweep": {"first_overheating": repeated, "first_overheating_target_mpa": 50.0},
    }))
    return path


def test_signed_changes_use_one_reference_and_preserve_offset():
    frame = cycle_frame()
    values = cycle_strain_metrics(frame, 100 + frame["position_mm"])
    assert values["reference_length_mm"] == 100
    assert values["heating_strain_pct"] == pytest.approx(-4)
    assert values["cooling_strain_pct"] == pytest.approx(3)
    assert values["residual_strain_pct"] == pytest.approx(-1)
    assert values["heating_strain_pct"] + values["cooling_strain_pct"] == pytest.approx(values["residual_strain_pct"])
    assert values["start_length_mm"] == 104
    assert values["turnaround_length_mm"] == 100
    assert values["cooling_end_length_mm"] == 103
    assert values["cooling_end_current_mA"] == 0
    assert values["cooling_duration_s"] == 5


@pytest.mark.parametrize("tail", [[], [0.0], [49.0, 0.0]])
def test_aborted_or_absent_cooling_never_becomes_zero_residual(tail):
    frame = cycle_frame().iloc[:6].copy()
    for current in tail:
        row = frame.iloc[-1].copy()
        row["current_mA"] = current
        row["elapsed_s"] += 1
        frame.loc[len(frame)] = row
    result = cycle_strain_metrics(frame, 100 + frame["position_mm"])
    assert result["heating_strain_pct"] == pytest.approx(-4)
    assert result["cooling_recorded"] is False
    assert result["cooling_strain_pct"] is None
    assert result["residual_strain_pct"] is None


def test_high_current_hold_ends_at_shared_turnaround():
    frame = cycle_frame()
    hold = frame.iloc[5].copy()
    hold["position_mm"] = -1.0
    hold["strain_pct"] = -1.0
    hold["automation_phase"] = "current_hold"
    frame = pd.concat([frame.iloc[:6], hold.to_frame().T, frame.iloc[6:]], ignore_index=True)
    frame["elapsed_s"] = range(len(frame))
    result = cycle_strain_metrics(frame, 100 + frame["position_mm"])
    assert result["turnaround_length_mm"] == 99
    assert result["heating_strain_pct"] == pytest.approx(-500 / 99)
    assert result["cooling_strain_pct"] == pytest.approx(400 / 99)
    assert result["residual_strain_pct"] == pytest.approx(-100 / 99)
    at_turn = core.interpolate_transition_strain_pct(
        frame["current_mA"], frame["strain_pct"], {"Ms": 50.0}
    )
    assert at_turn["Ms"] == pytest.approx(-1.0)


def test_uncalibrated_recorded_strain_has_explicit_unknown_length():
    result = cycle_strain_metrics(cycle_frame())
    assert result["reference_method"] == "recorded_strain_pct"
    assert result["reference_length_mm"] is None
    assert result["start_length_mm"] is None
    assert result["residual_strain_pct"] == pytest.approx(-1)


@pytest.mark.parametrize("bad_length", [np.nan, np.inf, -1.0])
def test_invalid_calibrated_length_does_not_silently_use_recorded_strain(bad_length):
    frame = cycle_frame()
    length = 100 + frame["position_mm"]
    length.iloc[3] = bad_length
    result = cycle_strain_metrics(frame, length)
    assert result["reference_method"] == "invalid_length_trace"
    assert result["residual_strain_pct"] is None


def test_summary_keeps_first_overheating_repeats_and_zero_current_endpoints(tmp_path):
    path = write_run(tmp_path, repeated=True)
    summary = core.summarize_current_sweep(core.load_run(path))
    assert len(summary.targets) == 2
    assert [x.sweep_index for x in summary.targets] == [1, 2]
    assert [x.first_overheating for x in summary.targets] == [True, False]
    assert "1st:" in summary.targets[0].target_label
    assert "sweep 1/2" in summary.targets[0].target_label
    assert "sweep 2/2" in summary.targets[1].target_label
    assert [x.cycle_strain["cooling_end_current_mA"] for x in summary.targets] == [0, 0]
    assert [x.cycle_strain["residual_strain_pct"] for x in summary.targets] == pytest.approx([-1, -0.5])


@pytest.mark.parametrize("absent_cooling", [False, True])
def test_logger_save_builder_import_and_export_retain_cycle_metrics(tmp_path, qtbot, absent_cooling):
    from microwire_data_builder import ui as builder_ui
    from microwire_data_builder.core import MiniDmaRecord
    from plotting.shared.transition_review_dialog import _build_tma_review_dialog
    from plotting.shared.transition_review import load_review
    import launcher

    path = write_run(tmp_path)
    run = core.load_run(path)
    dialog = _build_tma_review_dialog(None, path)
    qtbot.addWidget(dialog)
    assert "Heating strain: -4%" in dialog.cycle_strain_label.text()
    assert "Cooling strain: +3%" in dialog.cycle_strain_label.text()
    assert "Residual strain: -1%" in dialog.cycle_strain_label.text()
    expected_residual = None if absent_cooling else pytest.approx(-1)
    if absent_cooling:
        dialog.cooling_branch_check.setChecked(False)
        assert "Cooling strain: unavailable" in dialog.cycle_strain_label.text()
        assert "Residual strain: unavailable" in dialog.cycle_strain_label.text()
    for label in tuple(dialog.choice_buttons):
        dialog.choice_buttons[label]["not_observed"].click()
    dialog._save_and_accept()
    saved = load_review(path / "transition_review.json")
    assert saved["targets"][0]["status"] == "no_transition"
    assert saved["targets"][0]["cycle_strain"]["residual_strain_pct"] == expected_residual
    records = [MiniDmaRecord(path, "Synthetic 1-1", run.frame)]
    reviews = {}
    assert builder_ui._import_portable_tma_reviews(records, reviews, logging.getLogger(__name__))
    assert next(iter(reviews.values()))["cycle_strain"]["residual_strain_pct"] == expected_residual
    normalized = builder_ui.MiniDmaSection._clean_transition_review_payload(
        next(iter(reviews)), next(iter(reviews.values())),
    )
    assert normalized["cycle_strain"]["residual_strain_pct"] == expected_residual
    frame = launcher._expanded_tma_export_frame_from_sections({"mini_dma": {
        "payloads": {"mini_dma_records": records},
        "extra": {"mini_dma_transition_reviews": {"records": reviews}},
    }})
    assert len(frame) == 1
    assert frame.iloc[0]["TMA residual strain (%)"] == expected_residual
    assert frame.iloc[0]["TMA As"] == "No transition"
    workbook_path = tmp_path / "analysis.xlsx"
    launcher._write_assemble_workbook(
        output_path=workbook_path, preset="public", tma_frame=frame,
        frame=pd.DataFrame([{"Composition": "Synthetic", "Microwire": "1/1"}]),
    )
    import openpyxl
    workbook = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    rows = list(workbook["Analysis"].values)
    exported = dict(zip(rows[0], rows[1]))
    assert exported["TMA heating strain (%)"] == pytest.approx(-4)
    assert exported["TMA cooling strain (%)"] == (None if absent_cooling else pytest.approx(3))
    assert exported["TMA residual strain (%)"] == expected_residual
    workbook.close()


@pytest.mark.parametrize("legacy_label", [False, True])
def test_excluding_repeat_does_not_exclude_first_overheating(tmp_path, legacy_label):
    from microwire_data_builder.core import MiniDmaRecord
    import launcher

    path = write_run(tmp_path, repeated=True)
    run = core.load_run(path)
    target = "50 MPa" if legacy_label else "50 MPa · sweep 2/2"
    frame = launcher._expanded_tma_export_frame_from_sections({"mini_dma": {
        "payloads": {"mini_dma_records": [MiniDmaRecord(path, "Synthetic 1-1", run.frame)]},
        "extra": {"mini_dma_transition_reviews": {"records": {f"{path}::{target}": {
            "status": "excluded", "sample": "Synthetic 1-1", "target_label": target,
        }}}},
    }})
    assert len(frame) == 1
    assert frame.iloc[0]["TMA target type"] == "First overheating"
    assert frame.iloc[0]["TMA residual strain (%)"] == pytest.approx(-1)


def test_repeated_ordinary_sweeps_do_not_share_metrics_or_exclusions(tmp_path):
    from microwire_data_builder.core import MiniDmaRecord
    import launcher

    path = write_run(tmp_path, repeated=True)
    metadata = json.loads((path / "metadata.json").read_text())
    metadata["current_sweep"]["first_overheating"] = False
    (path / "metadata.json").write_text(json.dumps(metadata))
    run = core.load_run(path)
    record = MiniDmaRecord(path, "Synthetic 1-1", run.frame)
    source_alias = str(path).replace("\\", "/").replace("/", "//").upper()
    reviews = {f"{source_alias}::50 MPa · sweep 1/2": {
        "status": "excluded", "sample": "Synthetic 1-1",
        "target_label": "50 MPa · sweep 1/2",
    }}
    frame = launcher._expanded_tma_export_frame_from_sections({"mini_dma": {
        "payloads": {"mini_dma_records": [record]},
        "extra": {"mini_dma_transition_reviews": {"records": reviews}},
    }})
    assert len(frame) == 1
    assert frame.iloc[0]["TMA target"].endswith("sweep 2/2")
    assert frame.iloc[0]["TMA residual strain (%)"] == pytest.approx(-0.5)


def test_source_free_record_uses_embedded_trace_without_inventing_length(tmp_path):
    from microwire_data_builder.core import MiniDmaRecord, mini_dma_cycle_strain_rows

    record = MiniDmaRecord(tmp_path / "missing iso-stress", "Synthetic 1-1", cycle_frame())
    rows = mini_dma_cycle_strain_rows(record)
    assert len(rows) == 1
    assert rows[0]["cycle_strain"]["reference_method"] == "recorded_strain_pct"
    assert rows[0]["cycle_strain"]["reference_length_mm"] is None
    assert rows[0]["cycle_strain"]["residual_strain_pct"] == pytest.approx(-1)
