"""Independent expected values for recipe boundaries and reached current."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from plotting.plugins.mini_dma import core
from plotting.plugins.mini_dma.cycle_strain import cycle_strain_metrics


def sweep():
    command = [1., 10., 20., 30., 40., 40., 30., 20., 10., 5., 1.]
    return pd.DataFrame({
        "elapsed_s": np.arange(len(command), dtype=float),
        "current_set_mA": command, "current_mA": command,
        "strain_pct": [0, 0, -2, -5, -8, -9, -7, -5, -2, -1, 0],
        "resistance_ohm": 100.0, "automation_phase": "current",
        "automation_target_value": 50.0, "plateau_index": 0.0,
    })


def run(frame):
    return core.MiniDmaRun(Path("missing"), Path("missing/measurement.csv"), frame, "Synthetic 1-1")


def test_summary_excludes_stale_point_before_loading_without_changing_target_identity():
    stale = sweep().iloc[:1].assign(strain_pct=-9)
    ramp = pd.concat([stale] * 3).assign(automation_phase="target_ramp", strain_pct=[-6, -3, 0])
    data = pd.concat([stale, ramp, sweep()], ignore_index=True)
    data["elapsed_s"] = np.arange(len(data), dtype=float)
    target, = core.summarize_current_sweep(run(data)).targets
    assert target.target_label == "50 MPa"
    assert target.max_strain_pct == 9
    assert target.cycle_strain["heating_strain_pct"] == -9
    assert target.cycle_strain["cooling_strain_pct"] == 9
    assert target.cycle_strain["residual_strain_pct"] == 0
    assert target.cycle_strain["start_elapsed_s"] == 4


def test_commanded_turnaround_is_not_moved_by_measured_overshoot():
    data = sweep()
    data.loc[6, "current_mA"] = 40.2
    target, = core.summarize_current_sweep(run(data)).targets
    assert target.max_current_mA == 40
    assert target.cycle_strain["turnaround_elapsed_s"] == 5
    assert target.cycle_strain["heating_strain_pct"] == -9
    assert target.cycle_strain["cooling_strain_pct"] == 9


def test_partial_return_retains_observation_without_claiming_cycle_recovery():
    data = sweep()
    data.loc[6:, "current_set_mA"] = [39, 38, 37, 36, 35]
    data["current_mA"] = data["current_set_mA"]
    result = cycle_strain_metrics(data)
    assert result["cooling_recorded"] is True
    assert result["cooling_complete"] is False
    assert result["heating_strain_pct"] == -9
    assert result["cooling_strain_pct"] is None
    assert result["residual_strain_pct"] is None


def test_two_cycles_keep_range_and_reached_setpoint_without_choosing_a_cycle():
    data = pd.concat([sweep(), sweep()], ignore_index=True)
    data["elapsed_s"] = np.arange(len(data), dtype=float)
    target, = core.summarize_current_sweep(run(data)).targets
    assert target.max_strain_pct == 9
    assert target.max_current_mA == 40
    assert target.cycle_strain["status"] == "multiple_cycles_range_only"
    for key in ("heating_strain_pct", "cooling_strain_pct", "residual_strain_pct"):
        assert target.cycle_strain[key] is None


def test_reached_setpoint_and_corrected_strain_reach_builder_export(tmp_path):
    import launcher
    from microwire_data_builder.core import MiniDmaRecord

    data = sweep()
    data.loc[6, "current_mA"] = 40.2
    source = tmp_path / "missing iso-stress"
    record = MiniDmaRecord(source, "Synthetic 1-1", data)
    sections = {"mini_dma": {"payloads": {"mini_dma_records": [record]}, "extra": {
        "mini_dma_transition_reviews": {"records": {f"{source}::50 MPa": {
            "status": "accepted_auto", "sample": "Synthetic 1-1", "target_label": "50 MPa",
            "values": {"As": 20.0, "Af": 30.0},
        }}}
    }}}
    result = launcher._expanded_tma_export_frame_from_sections(sections)
    assert len(result) == 1
    assert result.iloc[0]["TMA max current (mA)"] == 40
    assert result.iloc[0]["TMA heating strain (%)"] == -9
    assert result.iloc[0]["TMA cooling strain (%)"] == 9
    assert result.iloc[0]["TMA residual strain (%)"] == 0


def test_embedded_frame_and_saved_load_label_win_over_changed_external_run(tmp_path):
    import launcher
    from microwire_data_builder.core import MiniDmaRecord

    source = tmp_path / "iso-stress"
    source.mkdir()
    changed = sweep()
    changed["strain_pct"] *= 2
    changed.to_csv(source / "measurement.csv", index=False)
    record = MiniDmaRecord(source, "Synthetic 1-1", sweep(),
                           strain_summary=("50 MPa / 1.99 g: 123% @ 1 mA",))
    sections = {"mini_dma": {"payloads": {"mini_dma_records": [record]}, "extra": {
        "mini_dma_transition_reviews": {"records": {f"{source}::50 MPa / 1.99 g": {
            "status": "accepted_auto", "sample": "Synthetic 1-1", "target_label": "50 MPa / 1.99 g",
            "values": {"As": 20.0, "Af": 30.0},
        }}}
    }}}
    result = launcher._expanded_tma_export_frame_from_sections(sections)
    assert len(result) == 1
    assert result.iloc[0]["TMA load (g)"] == 1.99
    assert result.iloc[0]["TMA strain (%)"] == 9
    assert result.iloc[0]["TMA heating strain (%)"] == -9
    assert result.iloc[0]["TMA As"] == 20


@pytest.mark.parametrize("accepted", [True, False])
def test_break_event_joins_only_its_exact_review_or_stays_event_only(tmp_path, accepted):
    import launcher

    source = str(tmp_path / "break run")
    sibling = str(tmp_path / "sibling run")
    target = "50 MPa / 1.99 g"
    reviews = {
        f"{path}::{target}": {
            "status": "accepted_auto" if path == sibling or accepted else "excluded",
            "sample": "Synthetic 1-1", "target_label": target,
            "run_label": Path(path).name, "values": {"As": 20., "Af": 30.},
        }
        for path in (source, sibling)
    }
    event = {
        "event_id": "synthetic-event", "include_for_export": True,
        "composition": "Synthetic", "microwire": "1/1", "source_path": source,
        "review_id": f"{source}::{target}", "target_label": target,
        "electrical_break": True, "current_mA": 38., "stress_mpa": 50.,
        "source_event": {"event_measured_current_mA": 37.8, "stress_mpa": 49.1},
    }
    sections = {"mini_dma": {"extra": {
        "mini_dma_transition_reviews": {"records": reviews},
        "accepted_break_events": {"version": 1, "events": [event]},
    }}}
    result = launcher._expanded_tma_export_frame_from_sections(sections)
    assert len(result) == 2
    broken, = result.loc[result["TMA break current (mA)"].notna()].to_dict("records")
    assert broken["TMA break current (mA)"] == 38.
    assert broken["TMA break stress (MPa)"] == 50.
    if accepted:
        assert broken["TMA As"] == 20.
    else:
        assert pd.isna(broken["TMA As"])
    sibling_row = result.loc[result["TMA run"] == "sibling run"].iloc[0]
    assert pd.isna(sibling_row["TMA break current (mA)"])
    assert sibling_row["TMA As"] == 20.


def test_break_register_does_not_export_held_duplicates_or_mechanical_values(tmp_path):
    import launcher

    event = {
        "event_id": "mechanical", "include_for_export": True,
        "composition": "Synthetic", "microwire": "1/1",
        "source_path": str(tmp_path / "run"), "target_label": "20 MPa / 0.8 g",
        "electrical_break": False, "current_mA": 40., "stress_mpa": 20.,
    }
    events = [event, event.copy(), {**event, "event_id": "held"},
              {**event, "event_id": "unqualified", "include_for_export": False}]
    sections = {"mini_dma": {"extra": {"accepted_break_events": {
        "version": 1, "events": events, "excluded_event_ids": ["held"],
    }}}}
    result = launcher._expanded_tma_export_frame_from_sections(sections)
    assert len(result) == 1
    assert result.iloc[0]["TMA stress (MPa)"] == 20.
    assert pd.isna(result.iloc[0]["TMA break current (mA)"])
    assert pd.isna(result.iloc[0]["TMA break stress (MPa)"])


def test_break_register_survives_builder_section_and_native_package_save(tmp_path, qtbot):
    import logging
    import launcher
    from microwire_data_builder import project_package, ui

    register = {"version": 1, "events": [{
        "event_id": "synthetic", "include_for_export": True, "electrical_break": True,
        "composition": "Synthetic", "microwire": "1/1", "source_path": str(tmp_path / "run"),
        "target_label": "20 MPa / 0.8 g", "current_mA": 39.6, "stress_mpa": 20.,
        "source_event": {"source_sha256": "1" * 64, "event_measured_current_mA": 0.},
    }]}
    section = ui.MiniDmaSection(logging.getLogger(__name__), lambda *_args: None)
    qtbot.addWidget(section)
    section.import_project_payload({"section": "mini_dma", "columns": [], "rows": [],
                                    "extra": {"accepted_break_events": register}})
    payload = section.export_project_payload()
    path = tmp_path / "saved.pydpj"
    project_package.write_project_package(path, {
        "kind": project_package.PROJECT_KIND, "sections": {"mini_dma": payload},
    })
    saved = project_package.inspect_project_package(path, verify_entries=True).read_section("mini_dma")
    assert saved["extra"]["accepted_break_events"] == register
    exported = launcher._expanded_tma_export_frame_from_sections({"mini_dma": saved})
    assert len(exported) == 1
    assert exported.iloc[0]["TMA break current (mA)"] == 39.6
    assert exported.iloc[0]["TMA break stress (MPa)"] == 20.
