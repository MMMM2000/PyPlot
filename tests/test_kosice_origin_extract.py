from __future__ import annotations

from pathlib import Path

import pandas as pd

from microwire_data_builder.kosice_origin_extract import (
    OriginWorksheetExport,
    infer_sample_key,
    normalized_manual_stress_frame,
    normalized_manual_stress_traces,
    write_builder_ready_manual_stress_txt,
)
from plotting.plugins.shape_memory_stress_strain.core import load_manual_stress_strain_file


def test_infer_sample_key_from_origin_workbook_sheet_names() -> None:
    assert infer_sample_key("Stress-Strain-Ni50Fe27Ga23-CuCo", "Ni50Fe27Ga23 12_2") == (
        "Ni50Fe27Ga23 12/2"
    )


def test_normalized_manual_stress_frame_maps_origin_labels() -> None:
    frame = pd.DataFrame(
        {
            "A": [0.0, 0.1, 0.2],
            "B": [0.0, 10.0, 20.0],
            "C": [0.0, 0.5, 1.0],
            "D": [0.0, 35.0, 70.0],
        }
    )

    normalized, source_columns, units = normalized_manual_stress_frame(
        frame,
        column_labels=["Extension", "Force", "Strain", "Stress"],
        unit_labels=["mm", "g", "%", "MPa"],
    )

    assert normalized.columns.tolist() == [
        "displacement_mm",
        "load_g",
        "strain_pct",
        "stress_mpa",
    ]
    assert normalized["stress_mpa"].tolist() == [0.0, 35.0, 70.0]
    assert source_columns == {
        "displacement_mm": "A",
        "load_g": "B",
        "strain_pct": "C",
        "stress_mpa": "D",
    }
    assert units == {
        "displacement_mm": "mm",
        "load_g": "g",
        "strain_pct": "%",
        "stress_mpa": "MPa",
    }


def test_normalized_manual_stress_traces_pairs_repeated_origin_columns() -> None:
    frame = pd.DataFrame(
        {
            "A": [0.0, 0.1, 0.2],
            "B": [0.0, 10.0, 20.0],
            "C": [0.0, 0.3, 0.6],
            "D": [0.0, 30.0, 60.0],
            "E": [0.0, 0.5, 1.0],
            "F": [0.0, 35.0, 70.0],
            "G": [0.0, 1.5, 3.0],
            "H": [0.0, 40.0, 80.0],
        }
    )

    traces = normalized_manual_stress_traces(
        frame,
        column_labels=[
            "Displacement (mm)",
            "Load (g)",
            "Displacement (mm)",
            "Load (g)",
            "Strain (%)",
            "Stress (MPa)",
            "Strain (%)",
            "Stress (MPa)",
        ],
        unit_labels=["11mm", "", "12mm", "", "11mm", "", "12mm", ""],
    )

    assert [trace.trace_label for trace in traces] == ["trace01", "trace02"]
    assert traces[0].source_columns == {
        "displacement_mm": "A",
        "load_g": "B",
        "strain_pct": "E",
        "stress_mpa": "F",
    }
    assert traces[1].source_columns == {
        "displacement_mm": "C",
        "load_g": "D",
        "strain_pct": "G",
        "stress_mpa": "H",
    }
    assert traces[1].frame["stress_mpa"].tolist() == [0.0, 40.0, 80.0]


def test_write_builder_ready_manual_stress_txt_matches_existing_parser(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "displacement_mm": [0.0, 0.1, 0.2],
            "load_g": [0.0, 10.0, 20.0],
            "strain_pct": [0.0, 0.5, 1.0],
            "stress_mpa": [0.0, 35.0, 70.0],
        }
    )
    path = tmp_path / "Ni50Fe27Ga23 12_2 kosice.txt"

    write_builder_ready_manual_stress_txt(frame, path)
    parsed = load_manual_stress_strain_file(path)

    assert parsed["displacement_mm"].tolist() == [0.1, 0.2]
    assert parsed["load_g"].tolist() == [10.0, 20.0]
    assert parsed["strain_pct"].tolist() == [0.5, 1.0]
    assert parsed["stress_mpa"].tolist() == [35.0, 70.0]


def test_origin_worksheet_export_manifest_entry() -> None:
    export = OriginWorksheetExport(
        sample_key="Ni50Fe27Ga23 12/2",
        workbook="Book1",
        workbook_long_name="Stress-Strain-Ni50Fe27Ga23-CuCo",
        sheet="Ni50Fe27Ga23 12_2",
        sheet_long_name="",
        source_columns={
            "displacement_mm": "A",
            "load_g": "B",
            "strain_pct": "C",
            "stress_mpa": "D",
        },
        units={"stress_mpa": "MPa"},
        row_count=42,
        output_csv_path="artifacts/kosice_origin_extract/normalized_csv/sample.csv",
        output_txt_path="artifacts/kosice_origin_extract/builder_txt/sample.txt",
    )

    entry = export.as_manifest_entry()

    assert entry["sample_key"] == "Ni50Fe27Ga23 12/2"
    assert entry["row_count"] == 42
    assert entry["source_columns"] == {
        "displacement_mm": "A",
        "load_g": "B",
        "strain_pct": "C",
        "stress_mpa": "D",
    }
