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
                "current_set_mA",
                "current_measured_mA",
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
                    "current_set_mA": current,
                    "current_measured_mA": current * 0.95,
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


def test_run_quality_excludes_short_failed_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-short"
    _write_run(run_dir, rows=3, stop_reason="wire_break_or_contact_loss")

    quality = analyze_run_quality(run_dir)

    assert not quality.include_in_optimization_summary
    assert "stop_reason:wire_break_or_contact_loss" in quality.exclusion_reasons
    assert "measurement_rows<100" in quality.exclusion_reasons


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
