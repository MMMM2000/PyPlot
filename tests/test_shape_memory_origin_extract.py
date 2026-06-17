from __future__ import annotations

import json
from pathlib import Path

from plotting.plugins.shape_memory_stress_strain.origin_extract import (
    OriginColumn,
    infer_manual_column_map,
    infer_sample_key,
    load_origin_extract_manifest,
    normalize_column_name,
    normalize_origin_extract_manifest,
)


def test_infer_sample_key_prefers_full_draw_piece() -> None:
    assert infer_sample_key("Stress Ni50Fe27Ga23 11-1 s1") == "Ni50Fe27Ga23 11/1"


def test_normalize_column_name_uses_units() -> None:
    assert normalize_column_name("Stress", units="MPa") == "stress_mpa"
    assert normalize_column_name("Strain", units="%") == "strain_pct"


def test_infer_manual_column_map_detects_origin_labels() -> None:
    columns = (
        OriginColumn(0, "A", "Displacement", "mm"),
        OriginColumn(1, "B", "Load", "g"),
        OriginColumn(2, "C", "Strain", "%"),
        OriginColumn(3, "D", "Stress", "MPa"),
    )

    mapping = infer_manual_column_map(columns)

    assert mapping == {
        "displacement_mm": "Displacement",
        "load_g": "Load",
        "strain_pct": "Strain",
        "stress_mpa": "Stress",
    }


def test_normalize_origin_extract_manifest_marks_candidates(tmp_path: Path) -> None:
    payload = {
        "worksheets": [
            {
                "sample_key": "Ni50Fe27Ga23 11/1",
                "csv_path": "csv/sheet.csv",
                "row_count": "12",
                "columns": [
                    {"index": 0, "short_name": "A", "long_name": "Strain", "units": "%"},
                    {"index": 1, "short_name": "B", "long_name": "Stress", "units": "MPa"},
                ],
            }
        ]
    }

    normalized = normalize_origin_extract_manifest(
        payload,
        manifest_path=tmp_path / "manifest.json",
    )

    worksheet = normalized["worksheets"][0]
    assert normalized["worksheet_count"] == 1
    assert normalized["candidate_count"] == 1
    assert worksheet["candidate_manual_stress_strain"] is True
    assert worksheet["row_count"] == 12
    assert worksheet["csv_path"] == str((tmp_path / "csv" / "sheet.csv").resolve())


def test_load_origin_extract_manifest_normalizes_relative_paths(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "worksheets": [
                    {
                        "csv_path": "csv/raw.csv",
                        "columns": [{"long_name": "Stress", "units": "MPa"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = load_origin_extract_manifest(manifest)

    assert payload["worksheets"][0]["csv_path"] == str((tmp_path / "csv" / "raw.csv").resolve())
