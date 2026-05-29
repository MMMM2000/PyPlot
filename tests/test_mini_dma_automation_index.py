from __future__ import annotations

import json
from pathlib import Path

from scripts import mini_dma_automation_index


def test_mini_dma_automation_index_extracts_metadata(tmp_path: Path) -> None:
    source = tmp_path / "automated"
    run_dir = source / "Ni50Fe27Ga23 19_8 iso-stress_run02"
    run_dir.mkdir(parents=True)
    (source / "metadata").mkdir()
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "created_utc": "2026-05-28 10:00:00",
                "sample_name": "Ni50Fe27Ga23 19/8",
                "name_fields": {"composition": "Ni50Fe27Ga23", "microwire": "19/8"},
                "initial_length_mm": 61.0,
                "wire_diameter_mm": 0.0089,
                "recipe_mode": "current_sweep_stress",
                "recipe_summary": "Started iso-stress current sweep",
                "stop": {"reason": "recipe_completed", "label": "Completed", "detail": "done"},
                "source_control": {"branch": "codex/test", "commit": "abc123"},
                "control_logic": {"version": "2026-05-28.1", "fingerprint": "sha256:test"},
                "logging": {"raw_scale_sample_count": 42},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "measurement.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    (run_dir / "setup.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    rows = mini_dma_automation_index.discover_runs(
        [mini_dma_automation_index.SourceRoot("automated", source)]
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["source_name"] == "automated"
    assert row["sample_name"] == "Ni50Fe27Ga23 19/8"
    assert row["composition"] == "Ni50Fe27Ga23"
    assert row["microwire"] == "19/8"
    assert row["stop_reason"] == "recipe_completed"
    assert row["git_commit"] == "abc123"
    assert row["raw_scale_sample_count"] == 42
    assert row["measurement_rows"] == 2
    assert row["setup_rows"] == 1


def test_mini_dma_automation_index_uses_metadata_sidecar_tree(tmp_path: Path) -> None:
    source = tmp_path / "automated"
    metadata_dir = source / "metadata" / "metadata_only_run"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "metadata.json").write_text(
        json.dumps(
            {
                "created_utc": "2026-05-29 12:00:00",
                "sample_name": "Ni50Fe27Ga23 19/9",
                "name_fields": {"composition": "Ni50Fe27Ga23", "microwire": "19/9"},
                "stop": {"reason": "wire_break_or_contact_loss", "label": "Wire break"},
                "logging": {"raw_scale_sample_count": 12},
            }
        ),
        encoding="utf-8",
    )

    rows = mini_dma_automation_index.discover_runs(
        [mini_dma_automation_index.SourceRoot("automated", source)]
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["run_name"] == "metadata_only_run"
    assert row["metadata_exists"] == "true"
    assert row["sample_name"] == "Ni50Fe27Ga23 19/9"
    assert row["stop_reason"] == "wire_break_or_contact_loss"
    assert row["raw_scale_sample_count"] == 12
    assert row["measurement_rows"] is None


def test_mini_dma_automation_index_deduplicates_run_folder_and_metadata_sidecar(
    tmp_path: Path,
) -> None:
    source = tmp_path / "automated"
    run_dir = source / "same_run"
    sidecar_dir = source / "metadata" / "same_run"
    run_dir.mkdir(parents=True)
    sidecar_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps({"sample_name": "root metadata"}),
        encoding="utf-8",
    )
    (sidecar_dir / "metadata.json").write_text(
        json.dumps({"sample_name": "sidecar metadata"}),
        encoding="utf-8",
    )

    rows = mini_dma_automation_index.discover_runs(
        [mini_dma_automation_index.SourceRoot("automated", source)]
    )

    assert len(rows) == 1
    assert rows[0]["run_name"] == "same_run"
    assert rows[0]["sample_name"] == "root metadata"


def test_mini_dma_automation_index_writes_csv_and_jsonl(tmp_path: Path) -> None:
    rows = [
        {
            column: ""
            for column in mini_dma_automation_index.INDEX_COLUMNS
        }
    ]
    rows[0]["source_name"] = "automated"
    rows[0]["run_name"] = "run01"
    output_dir = tmp_path / "history"

    mini_dma_automation_index.write_index(rows, output_dir)

    assert (output_dir / "runs_index.csv").exists()
    assert (output_dir / "runs_index.jsonl").exists()
    assert "run01" in (output_dir / "runs_index.csv").read_text(encoding="utf-8")
    assert "run01" in (output_dir / "runs_index.jsonl").read_text(encoding="utf-8")
