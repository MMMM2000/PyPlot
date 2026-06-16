from __future__ import annotations

import csv
import json
from pathlib import Path

from data_logging.mini_dma_logger.run_quality import analyze_and_write_run_quality, analyze_run_quality
from scripts.mini_dma_run_quality import main as run_quality_main


def _write_run(run_dir: Path, *, rows: int = 120, stop_reason: str = "recipe_completed") -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": "Ni50Fe27Ga23 19/8",
                "name_fields": {"composition": "Ni50Fe27Ga23", "microwire": "19/8"},
                "initial_length_mm": 61.0,
                "wire_diameter_mm": 0.0089,
                "recipe_mode": "current_sweep_stress",
                "recipe_summary": "iso-stress normal measurement",
                "stop": {"reason": stop_reason, "detail": "done"},
                "control_logic": {"version": "2026-06-01.1", "fingerprint": "sha256:test"},
                "source_control": {"branch": "main", "commit": "abc123"},
                "heating": {"voltage_limit_v": 5.0},
            }
        ),
        encoding="utf-8",
    )
    with (run_dir / "measurement.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "elapsed_s",
                "automation_phase",
                "automation_target_value",
                "stress_mpa",
                "strain_pct",
                "current_set_mA",
                "current_measured_mA",
                "voltage_V",
            ],
        )
        writer.writeheader()
        for index in range(rows):
            half = rows // 2
            current = 1.0 + index if index <= half else 1.0 + (rows - index)
            stress = 50.0 + (index % 5) - 2.0
            phase = "current_hold" if 30 <= index < 35 else "current"
            writer.writerow(
                {
                    "elapsed_s": index * 0.5,
                    "automation_phase": phase,
                    "automation_target_value": 50.0,
                    "stress_mpa": stress,
                    "strain_pct": index * 0.01,
                    "current_set_mA": current,
                    "current_measured_mA": current * 0.95,
                    "voltage_V": 2.0,
                }
            )


def test_run_quality_includes_completed_current_loop(tmp_path: Path) -> None:
    run_dir = tmp_path / "run01"
    _write_run(run_dir)

    quality = analyze_run_quality(run_dir)

    assert quality.include_in_optimization_summary
    assert quality.run_type == "normal_measurement"
    assert quality.current_loop_count_estimate >= 1
    assert quality.stress_error_max_abs_mpa == 2.0
    assert quality.current_hold_elapsed_s > 0.0
    assert quality.control_logic_version == "2026-06-01.1"
    assert quality.stop_classification == "completed"
    assert quality.stress_error_by_phase["current"]["count"] > 0
    assert quality.current_hold_window_count == 1
    assert quality.current_hold_windows[0]["recovered_after_s"] is not None
    assert "missing:control_trace.csv" in quality.metadata_warnings


def test_run_quality_excludes_short_failed_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-short"
    _write_run(run_dir, rows=3, stop_reason="wire_break_or_contact_loss")

    quality = analyze_run_quality(run_dir)

    assert not quality.include_in_optimization_summary
    assert "measurement_rows<100" in quality.exclusion_reasons
    assert "current_loops<1" in quality.exclusion_reasons


def test_run_quality_includes_wire_break_after_useful_sweep_data(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-wire-break"
    _write_run(run_dir, rows=120, stop_reason="wire_break_or_contact_loss")

    quality = analyze_run_quality(run_dir)

    assert quality.include_in_optimization_summary
    assert quality.run_type == "normal_measurement"
    assert "stop_reason:wire_break_or_contact_loss" not in quality.exclusion_reasons
    assert "stopped:wire_break_or_contact_loss" in quality.biggest_problems
    assert quality.stop_classification == "fault_wire_break_or_contact_loss"


def test_run_quality_reports_limit_events_and_hold_overshoot(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-limit"
    _write_run(run_dir)
    (run_dir / "measurement.csv").write_text(
        "\n".join(
            [
                "elapsed_s,automation_phase,automation_target_value,stress_mpa,current_set_mA,current_measured_mA,voltage_V",
                "0,current,50,62,10,9,2",
                "1,current_hold,50,60,20,5,4.95",
                "2,current_hold,50,48,20,5,5.00",
                "3,current_hold,50,50.5,20,19,2",
                "4,current,50,50,10,10,2",
            ]
        ),
        encoding="utf-8",
    )
    (run_dir / "control_trace.csv").write_text(
        "\n".join(
            [
                "elapsed_s,automation_phase,automation_basis,decision,result,reason",
                "1.0,current_limit_unwind,stress_mpa,voltage_limit_unwind,current_limit,voltage_limit",
            ]
        ),
        encoding="utf-8",
    )

    quality = analyze_run_quality(run_dir, min_measurement_rows=1, min_current_loops=0)

    assert quality.voltage_limit_event_count == 2
    assert quality.current_compliance_event_count == 2
    assert len(quality.voltage_current_limit_events) == 2
    assert quality.current_hold_overshoot_max_mpa == 2.0
    assert quality.current_hold_recovery_time_max_s == 1.0
    assert "voltage_limit_events" in quality.biggest_problems
    assert "current_compliance_events" in quality.biggest_problems


def test_run_quality_degrades_with_missing_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-no-metadata"
    run_dir.mkdir()
    (run_dir / "measurement.csv").write_text(
        "\n".join(
            [
                "elapsed_s,automation_phase,automation_target_value,stress_mpa,current_set_mA,current_measured_mA",
                "0,current,50,51,1,1",
                "1,current,50,50,2,2",
            ]
        ),
        encoding="utf-8",
    )

    quality = analyze_run_quality(run_dir, min_measurement_rows=1, min_current_loops=0)

    assert quality.measurement_rows == 2
    assert "missing:metadata.json" in quality.metadata_warnings
    assert quality.stop_classification == "unknown"


def test_run_quality_writes_cache(tmp_path: Path) -> None:
    run_dir = tmp_path / "run01"
    _write_run(run_dir)

    quality = analyze_and_write_run_quality(run_dir)

    cache = run_dir / "run_quality.json"
    assert cache.exists()
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["analyzer_version"] == quality.analyzer_version
    assert payload["include_in_optimization_summary"] is True


def test_run_quality_cli_writes_cache(tmp_path: Path) -> None:
    run_dir = tmp_path / "run01"
    _write_run(run_dir)

    assert run_quality_main([str(run_dir), "--write"]) == 0

    assert (run_dir / "run_quality.json").exists()


def test_run_quality_cli_can_generate_core_plot_batch_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run01"
    output_dir = tmp_path / "core-plots"
    _write_run(run_dir)

    assert run_quality_main([str(run_dir), "--write", "--core-plots", "--core-plot-dir", str(output_dir)]) == 0

    image = output_dir / "run01_stress_time_strain_current.png"
    summary = output_dir / "run01_stress_time_strain_current.json"
    assert (run_dir / "run_quality.json").exists()
    assert image.exists()
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["image_path"] == str(image)
    assert payload["summary_path"] == str(summary)
    assert payload["run_quality_path"] == str(run_dir / "run_quality.json")
