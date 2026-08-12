from __future__ import annotations

import csv
import json
from pathlib import Path

from data_logging.current_annealing_logger.session import (
    CurrentAnnealingSessionWriter,
    _approximate_power_at_currents,
    _summary_outcome,
    next_run_directory,
)


def test_session_writer_logs_enriched_measurements_and_summaries(tmp_path: Path) -> None:
    run_dir = next_run_directory(tmp_path, "Ni50 test")
    writer = CurrentAnnealingSessionWriter(
        run_dir,
        {"sample": {"name": "Ni50 test", "diameter_um": 10.0}, "recipe": {"loops": 1}},
    )
    writer.append(
        phase="current_ramp",
        cycle_index=1,
        direction="heating",
        set_current_mA=10.0,
        measured_current_mA=10.0,
        voltage_V=1.25,
        diameter_um=10.0,
        elapsed_s=0.0,
    )
    writer.append(
        phase="current_ramp",
        cycle_index=1,
        direction="cooling",
        set_current_mA=5.0,
        measured_current_mA=5.0,
        voltage_V=0.625,
        diameter_um=10.0,
        elapsed_s=2.0,
    )
    summary = writer.finalize(state="completed", reason="recipe_complete")

    with (run_dir / "measurement.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert float(rows[0]["power_mW"]) == 12.5
    assert float(rows[0]["current_density_A_mm2"]) > 100.0
    assert float(rows[1]["energy_J"]) > 0.0
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["session_state"] == "completed"
    assert metadata["point_count"] == 2
    assert summary["point_count"] == 2
    assert (run_dir / "run_summary.png").is_file()
    assert (run_dir / "run_summary_detail.png").is_file()
    assert json.loads((run_dir / "run_summary_status.json").read_text())["status"] == "complete"


def test_next_run_directory_never_reuses_existing_folder(tmp_path: Path) -> None:
    first = next_run_directory(tmp_path, "anneal")
    first.mkdir()
    assert next_run_directory(tmp_path, "anneal").name == "anneal_run02"


def test_summary_excludes_startup_rows_without_a_valid_resistance(tmp_path: Path) -> None:
    run_dir = next_run_directory(tmp_path, "startup transient")
    writer = CurrentAnnealingSessionWriter(
        run_dir,
        {
            "start_current_mA": 1.0,
            "hardware": {
                "min_positive_current_mA": 1.0,
                "current_resolution_mA": 0.2,
            },
            "recipe": {"loops": 1},
        },
    )
    writer.append(
        phase="current_ramp",
        cycle_index=1,
        direction="heating",
        set_current_mA=1.0,
        measured_current_mA=0.2,
        voltage_V=0.0,
        diameter_um=10.0,
        elapsed_s=0.0,
    )
    writer.append(
        phase="current_ramp",
        cycle_index=1,
        direction="heating",
        set_current_mA=1.0,
        measured_current_mA=1.0,
        voltage_V=0.2,
        diameter_um=10.0,
        elapsed_s=1.0,
    )
    writer.append(
        phase="current_ramp",
        cycle_index=1,
        direction="cooling",
        set_current_mA=1.0,
        measured_current_mA=1.0,
        voltage_V=0.18,
        diameter_um=10.0,
        elapsed_s=2.0,
    )

    summary = writer.finalize(state="completed", reason="recipe_complete")

    assert summary["point_count"] == 3
    assert summary["valid_resistance_point_count"] == 2
    assert summary["excluded_resistance_point_count"] == 1
    assert summary["resistance_current_floor_mA"] == 1.0
    assert summary["resistance_current_tolerance_mA"] == 0.1
    assert summary["schema"] == "current_annealing_run_summary_v2"
    assert summary["cycle_series_labels"] == ["Heating 1", "Cooling 1"]
    assert summary["outcome"]["kind"] == "completed"


def test_approximate_power_axis_uses_cycle_median() -> None:
    rows = [
        {"cycle_index": 1, "direction": "heating", "measured_current_mA": 10, "power_mW": 100},
        {"cycle_index": 1, "direction": "cooling", "measured_current_mA": 10, "power_mW": 120},
        {"cycle_index": 2, "direction": "heating", "measured_current_mA": 10, "power_mW": 140},
        {"cycle_index": 2, "direction": "cooling", "measured_current_mA": 10, "power_mW": 160},
    ]

    assert _approximate_power_at_currents(rows, [10.0, 20.0]) == [130.0, None]


def test_contact_loss_is_reported_as_valid_experimental_outcome() -> None:
    outcome = _summary_outcome(
        {
            "session_state": "completed",
            "stop": {"reason": "contact_lost", "detail": "continuity check failed"},
        }
    )

    assert outcome["kind"] == "contact_lost"
    assert outcome["is_valid_experimental_outcome"] is True
    assert "contact lost" in outcome["label"].lower()


def test_failed_active_cycle_is_not_reported_as_completed(tmp_path: Path) -> None:
    run_dir = next_run_directory(tmp_path, "contact loss")
    writer = CurrentAnnealingSessionWriter(
        run_dir,
        {"sample": {"name": "contact loss"}, "recipe": {"loops": 2}},
    )
    for cycle, direction, elapsed in (
        (1, "heating", 0.0),
        (1, "cooling", 1.0),
        (2, "heating", 2.0),
        (2, "cooling", 3.0),
    ):
        writer.append(
            phase="current_ramp",
            cycle_index=cycle,
            direction=direction,
            set_current_mA=1.0,
            measured_current_mA=1.0,
            voltage_V=0.2,
            diameter_um=10.0,
            elapsed_s=elapsed,
        )

    summary = writer.finalize(
        state="failed", reason="contact_lost", detail="continuity check failed"
    )

    assert summary["highest_cycle_index"] == 2
    assert summary["completed_cycles"] == 1
    assert summary["outcome"]["kind"] == "contact_lost"
