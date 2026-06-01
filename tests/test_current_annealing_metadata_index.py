from __future__ import annotations

import json
from pathlib import Path

from scripts import current_annealing_metadata_index


def test_current_annealing_metadata_index_extracts_sidecar_metadata(tmp_path: Path) -> None:
    source = tmp_path / "annealing"
    metadata_dir = source / "metadata" / "Ni50Fe27Ga23_19_7_test"
    metadata_dir.mkdir(parents=True)
    (source / "Ni50Fe27Ga23_19_7_test.txt").write_text(
        "# Current (mA)\tVoltage (V)\tResistance (Ohm)\n"
        "1.000000\t0.500000\t500.000000\n"
        "1.200000\t0.610000\t508.333333\n",
        encoding="utf-8",
    )
    (metadata_dir / "metadata.json").write_text(
        json.dumps(
            {
                "schema": "current_annealing_logger_metadata_v1",
                "created_utc": "2026-05-29T12:00:00Z",
                "data_file": "Ni50Fe27Ga23_19_7_test.txt",
                "composition": "Ni50Fe27Ga23",
                "microwire": "19/7",
                "sample": "test",
                "load": "free",
                "recipe": {
                    "start_current_mA": 1.0,
                    "max_current_mA": 20.0,
                    "current_ramp_rate_mA_s": 0.2,
                    "reverse_enabled": True,
                    "loops": 2,
                    "loops_infinite": False,
                    "max_voltage_action": "reverse",
                },
                "supply": {
                    "profile_id": "shared_hmp_broker",
                    "label": "Shared HMP broker",
                    "channel": 1,
                    "voltage_limit_v": 32.05,
                    "shared_broker": True,
                    "broker_source": "existing",
                },
                "source_control": {"branch": "codex/current-annealing-pyqtgraph", "commit": "abc123"},
            }
        ),
        encoding="utf-8",
    )

    rows = current_annealing_metadata_index.discover_runs(
        [current_annealing_metadata_index.SourceRoot("annealing", source)]
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["source_name"] == "annealing"
    assert row["run_name"] == "Ni50Fe27Ga23_19_7_test"
    assert row["composition"] == "Ni50Fe27Ga23"
    assert row["microwire"] == "19/7"
    assert row["current_ramp_rate_mA_s"] == 0.2
    assert row["supply_profile"] == "shared_hmp_broker"
    assert row["supply_channel"] == 1
    assert row["git_commit"] == "abc123"
    assert row["data_rows"] == 2


def test_current_annealing_metadata_index_writes_csv_and_jsonl(tmp_path: Path) -> None:
    rows = [
        {
            column: ""
            for column in current_annealing_metadata_index.INDEX_COLUMNS
        }
    ]
    rows[0]["source_name"] = "annealing"
    rows[0]["run_name"] = "run01"
    output_dir = tmp_path / "history"

    current_annealing_metadata_index.write_index(rows, output_dir)

    csv_text = (output_dir / "current_annealing_index.csv").read_text(encoding="utf-8")
    jsonl_text = (output_dir / "current_annealing_index.jsonl").read_text(encoding="utf-8")
    assert "run01" in csv_text
    assert "run01" in jsonl_text
