from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from PyQt6 import QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import launcher as launcher_module
from microwire_data_builder import ui as builder_ui
from microwire_data_builder import safe_codec
from microwire_data_builder import project_package
from microwire_data_builder.core import (
    MeasurementMetadata,
    MeasurementRecord,
    MiniDmaRecord,
    ShapeMemoryStressStrainRecord,
)
from plotting.pyplot.app import PyPlotWorkbench
from plotting.pyplot.window import TabDescriptor
from plotting.shared.toolkit import theme_manager


def _write_hysteresis_source(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "150 6.2e-10",
                "75 6.1e-10",
                "0 -6.0e-10",
                "-75 -6.1e-10",
                "-150 -6.2e-10",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def _write_synthetic_assemble_project(path: Path) -> Path:
    payload = {
        "version": 2,
        "kind": "MicrowireDataBuilder",
        "saved_at": "2026-06-17 09:30",
        "sections": {
            "mini_dma": {
                "section": "mini_dma",
                "columns": ["Composition", "Microwire", "Mini DMA transition currents by stress/load"],
                "rows": [
                    {
                        "Composition": "Ni50Fe27Ga23",
                        "Microwire": "12/2",
                        "Mini DMA transition currents by stress/load": [
                            "50 MPa / 1.46 g: As 30 mA, Af 70 mA, Ms 65 mA, Mf 25 mA"
                        ],
                    }
                ],
                "extra": {
                    "mini_dma_transition_reviews": {
                        "schema_version": 1,
                        "records": {
                            "G:/runs/run01::50 MPa / 1.46 g": {
                                "status": "accepted",
                                "sample": "Ni50Fe27Ga23 12_2",
                                "run_label": "run01",
                                "target_label": "50 MPa / 1.46 g",
                                "values": {"As": 31.0, "Af": 71.0, "Ms": 66.0, "Mf": 26.0},
                            },
                            "G:/runs/run01::75 MPa / 2.19 g": {
                                "status": "no_transition",
                                "sample": "Ni50Fe27Ga23 12_2",
                                "run_label": "run01",
                                "target_label": "75 MPa / 2.19 g",
                            },
                            "G:/runs/run01::100 MPa / 2.92 g": {
                                "status": "accepted",
                                "sample": "Ni50Fe27Ga23 12_2",
                                "run_label": "run01",
                                "target_label": "100 MPa / 2.92 g",
                                "values": {"As": 35.0, "Af": 74.0},
                                "cleared_labels": ["Ms", "Mf"],
                            },
                            "G:/runs/run01::125 MPa / 3.65 g": {
                                "status": "excluded",
                                "sample": "Ni50Fe27Ga23 12_2",
                                "run_label": "run01",
                                "target_label": "125 MPa / 3.65 g",
                            },
                        },
                    },
                },
            },
            "transition_temps": {
                "section": "transition_temps",
                "columns": ["Composition", "Microwire", "As (C)", "Af (C)"],
                "rows": [
                    {
                        "Composition": "Ni50Fe27Ga23",
                        "Microwire": "12/2",
                        "As (C)": -23.5,
                        "Af (C)": 18.25,
                    }
                ],
            },
            "assemble": {
                "section": "assemble",
                "title": "Assemble",
                "columns": [
                    "Composition",
                    "Microwire",
                    "Mini DMA graphs",
                    "Mini DMA strain by stress/load",
                        "Mini DMA transition currents by stress/load",
                        "Current annealing transition status",
                        "VSM transition temp status",
                        "Mini DMA transition status",
                        "d (\u00b5m)",
                        "As (C)",
                        "Af (C)",
                        "As (mA)",
                    "Ms (mA)",
                    "As1 (mA)",
                    "Af1 (mA)",
                    "As current density (A/mm^2)",
                    "J_As1 (A/mm^2)",
                    "Data source",
                    "Source label",
                    "_sources",
                    "internal review note",
                    "provenance file",
                    "object summary",
                ],
                "rows": [
                    {
                        "Composition": "Ni50Fe27Ga23",
                        "Microwire": "12/2",
                        "Mini DMA graphs": ["run01", "run02"],
                        "Mini DMA transition currents by stress/load": [
                            "50 MPa / 1.46 g: As 30 mA, Af 70 mA, Ms 65 mA, Mf 25 mA",
                            "150 MPa / 4.38 g: As 40 mA, Af 80 mA, Ms 72 mA, Mf 33 mA",
                        ],
                        "Mini DMA strain by stress/load": [
                            "50 MPa / 1.46 g: 5.16% @ 15 mA",
                            "75 MPa / 2.19 g: 0.2% @ 20 mA",
                            "100 MPa / 2.92 g: 3.5% @ 22 mA",
                            "125 MPa / 3.65 g: 9.9% @ 25 mA",
                            "150 MPa / 4.38 g: 6.5% @ 28 mA",
                        ],
                        "Current annealing transition status": "No transition",
                        "VSM transition temp status": "No transition",
                        "Mini DMA transition status": "No transition",
                        "d (\u00b5m)": 20.0,
                        "As (C)": -23.5,
                        "Af (C)": 18.25,
                        "As (mA)": 30,
                        "Ms (mA)": 25,
                        "As1 (mA)": 30,
                        "Af1 (mA)": 70,
                        "As current density (A/mm^2)": 95.5,
                        "J_As1 (A/mm^2)": 95.5,
                        "Data source": "Measured",
                        "Source label": "Ko\u0161ice",
                        "_sources": ["G:/internal/run01"],
                        "internal review note": "needs source check",
                        "provenance file": "G:/internal/provenance.json",
                        "object summary": {"fit": "accepted", "points": 120},
                    }
                ],
            },
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_microwire_assemble_export_cli_writes_public_workbook_and_manifest(
    tmp_path: Path,
) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    project_path = _write_synthetic_assemble_project(tmp_path / "synthetic.pydpj")
    workbook_path = tmp_path / "public.xlsx"

    exit_code = launcher_module._run_microwire_assemble_export_cli(  # noqa: SLF001
        argparse.Namespace(
            microwire_assemble_export=str(project_path),
            microwire_assemble_output=str(workbook_path),
            microwire_assemble_manifest=None,
            microwire_assemble_preset="public",
            microwire_assemble_rebuild=False,
            microwire_assemble_rebuild_section=None,
            microwire_assemble_working_copy_dir=str(tmp_path / "working"),
            microwire_assemble_copy_project=True,
            out=None,
        )
    )

    assert exit_code == 0
    manifest_path = workbook_path.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_project"] == str(project_path.resolve())
    assert manifest["source_saved_at"] == "2026-06-17 09:30"
    assert manifest["source_row_count"] == 1
    assert manifest["row_count"] == 6
    assert manifest["sections_represented"] == ["assemble", "mini_dma", "transition_temps"]
    assert "Data source" in manifest["dropped_columns"]
    assert "Source label" in manifest["dropped_columns"]
    assert "_sources" in manifest["dropped_columns"]
    assert "internal review note" in manifest["dropped_columns"]
    assert "provenance file" in manifest["dropped_columns"]
    assert "TMA transition currents by stress/load" in manifest["dropped_columns"]
    assert "TMA strain by stress/load" in manifest["dropped_columns"]
    assert manifest["hidden_sheets"] == []
    assert manifest["extra_sheets"] == {}
    assert manifest["analysis_sheet"]["row_count"] == 6
    assert manifest["git_commit"]

    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    assert workbook.sheetnames == ["Analysis"]
    analysis_headers = [cell.value for cell in workbook["Analysis"][1]]
    assert "Analysis row type" not in analysis_headers
    assert "CA As1 (mA)" in analysis_headers
    assert "CA J_As1 (A/mm^2)" in analysis_headers
    assert "VSM As (\u00b0C)" in analysis_headers
    assert "TMA target type" in analysis_headers
    assert "TMA As (mA)" in analysis_headers
    assert "TMA strain (%)" in analysis_headers
    assert "TMA J_As (A/mm^2)" in analysis_headers
    assert "Video end length (m)" not in analysis_headers
    assert "Video wire range (m)" not in analysis_headers
    assert "Notes" not in analysis_headers
    assert "CA current (mA)" not in analysis_headers
    assert "Data source" not in analysis_headers
    assert "As1 (mA)" not in analysis_headers
    assert "Af1 (mA)" not in analysis_headers
    assert "TMA strain by stress/load" not in analysis_headers
    analysis_rows = [
        dict(zip(analysis_headers, [cell.value for cell in row_cells], strict=False))
        for row_cells in workbook["Analysis"].iter_rows(min_row=2)
    ]
    assert analysis_rows[0]["CA As1 (mA)"] == "No transition"
    assert analysis_rows[0]["CA J_As1 (A/mm^2)"] == 95.5
    assert analysis_rows[1]["VSM As (\u00b0C)"] == "No transition"
    assert analysis_rows[2]["TMA target type"] == "Stress/load target"
    assert analysis_rows[2]["TMA strain (%)"] == 5.16
    headers = analysis_headers
    assert "Data source" not in headers
    assert "Source label" not in headers
    assert "_sources" not in headers
    assert "internal review note" not in headers
    assert "provenance file" not in headers
    assert "As (mA)" not in headers
    assert "Ms (mA)" not in headers
    assert "As current density (A/mm^2)" not in headers
    assert "As1 (mA)" not in headers
    assert "Af1 (mA)" not in headers
    assert "J_As1 (A/mm^2)" not in headers
    assert "Current annealing transition status" not in headers
    assert "VSM transition temp status" not in headers
    assert "TMA transition status" not in headers
    assert "TMA transition currents by stress/load" not in headers
    assert "TMA strain by stress/load" not in headers
    tma_headers = analysis_headers
    tma_rows = [
        dict(zip(tma_headers, [cell.value for cell in row_cells], strict=False))
        for row_cells in workbook["Analysis"].iter_rows(min_row=4)
    ]
    assert [entry["TMA target"] for entry in tma_rows] == [
        "50 MPa / 1.46 g",
        "75 MPa / 2.19 g",
        "100 MPa / 2.92 g",
        "150 MPa / 4.38 g",
    ]
    first = tma_rows[0]
    assert first["Composition"] == "Ni50Fe27Ga23"
    assert first["Microwire"] == "12/2"
    assert first["TMA run"] == "run01"
    assert first["TMA stress (MPa)"] == 50
    assert first["TMA load (g)"] == 1.46
    assert first["TMA strain (%)"] == 5.16
    assert first["TMA strain peak current (mA)"] == 15
    assert first["TMA As (mA)"] == 31
    assert first["TMA Af (mA)"] == 71
    assert first["TMA Ms (mA)"] == 66
    assert first["TMA Mf (mA)"] == 26
    assert first["TMA J_As (A/mm^2)"] == pytest.approx(98.676, rel=1e-3)
    assert first["TMA J_Af (A/mm^2)"] == pytest.approx(226.0, rel=1e-3)
    assert first["TMA J_Ms (A/mm^2)"] == pytest.approx(210.085, rel=1e-3)
    assert first["TMA J_Mf (A/mm^2)"] == pytest.approx(82.761, rel=1e-3)
    assert {tma_rows[1][label] for label in ("TMA As (mA)", "TMA Af (mA)", "TMA Ms (mA)", "TMA Mf (mA)")} == {
        "No transition"
    }
    assert {tma_rows[1][label] for label in ("TMA J_As (A/mm^2)", "TMA J_Af (A/mm^2)", "TMA J_Ms (A/mm^2)", "TMA J_Mf (A/mm^2)")} == {
        "No transition"
    }
    assert tma_rows[2]["TMA As (mA)"] == 35
    assert tma_rows[2]["TMA Af (mA)"] == 74
    assert tma_rows[2]["TMA Ms (mA)"] == "Not observed"
    assert tma_rows[2]["TMA Mf (mA)"] == "Not observed"
    assert tma_rows[2]["TMA J_Ms (A/mm^2)"] == "Not observed"
    assert tma_rows[2]["TMA J_Mf (A/mm^2)"] == "Not observed"
    assert tma_rows[3]["TMA As (mA)"] == 40
    assert "125 MPa / 3.65 g" not in {entry["TMA target"] for entry in tma_rows}


@pytest.mark.parametrize("force_rebuild", [False, True])
def test_saved_public_projection_exports_only_selected_tma_family_structure(
    tmp_path: Path,
    force_rebuild: bool,
) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    project_path = _write_synthetic_assemble_project(tmp_path / "selected.pydpj")
    payload = json.loads(project_path.read_text(encoding="utf-8"))
    payload["sections"]["assemble"]["selected_columns"] = [
        "d (µm)",
        "Mini DMA transition status",
    ]
    payload["sections"]["assemble"]["column_order"] = ["d (µm)"]
    if force_rebuild:
        payload["sections"]["mini_dma"]["payloads"] = {
            "mini_dma_records": builder_ui._encode_project_payload(  # noqa: SLF001
                [
                    MiniDmaRecord(
                        path=tmp_path / "run01",
                        sample="Ni50Fe27Ga23 12_2",
                        data=pd.DataFrame(),
                        key=("Ni50Fe27Ga23", 12, 2),
                        label="run01",
                        strain_summary=("50 MPa / 1.46 g: 5.16% @ 15 mA",),
                    )
                ]
            )
        }
        payload["sections"]["fabrication"] = {
            "section": "fabrication",
            "columns": ["Composition", "Draw", "Piece", "Length (m)", "d (µm)"],
            "rows": [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Draw": 12,
                    "Piece": 2,
                    "Length (m)": 1.2,
                    "d (µm)": 20.0,
                }
            ],
        }
    project_path.write_text(json.dumps(payload), encoding="utf-8")
    workbook_path = tmp_path / "selected.xlsx"

    manifest = launcher_module._export_builder_assemble_workbook(  # noqa: SLF001
        source_project=project_path,
        output_path=workbook_path,
        copy_project=True,
        working_copy_dir=tmp_path / "working",
        force_rebuild=force_rebuild,
        rebuild_sections=["mini_dma"] if force_rebuild else None,
    )

    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    worksheet = workbook["Analysis"]
    headers = [cell.value for cell in worksheet[1]]
    assert headers[:3] == ["Composition", "Microwire", "d (µm)"]
    assert "TMA transition status" in headers
    assert "TMA target" in headers
    assert "TMA strain (%)" in headers
    assert "TMA J_As (A/mm^2)" in headers
    assert "CA graph" not in headers
    assert "CA As1 (mA)" not in headers
    assert "VSM scan" not in headers
    assert "VSM As (°C)" not in headers
    assert manifest["row_count"] == 4
    rows = [
        dict(zip(headers, [cell.value for cell in cells], strict=False))
        for cells in worksheet.iter_rows(min_row=2)
    ]
    targets = [row["TMA target"] for row in rows]
    if force_rebuild:
        assert targets.count("50 MPa / 1.46 g") == 2
        assert set(targets) == {
            "50 MPa / 1.46 g",
            "75 MPa / 2.19 g",
            "100 MPa / 2.92 g",
        }
    else:
        assert targets == [
            "50 MPa / 1.46 g",
            "75 MPa / 2.19 g",
            "100 MPa / 2.92 g",
            "150 MPa / 4.38 g",
        ]
    no_transition = next(row for row in rows if row["TMA target"] == "75 MPa / 2.19 g")
    not_observed = next(row for row in rows if row["TMA target"] == "100 MPa / 2.92 g")
    assert no_transition["TMA As (mA)"] == "No transition"
    assert not_observed["TMA Ms (mA)"] == "Not observed"
    assert "125 MPa / 3.65 g" not in {row["TMA target"] for row in rows}


def test_public_projection_without_measurement_family_keeps_one_compact_row(
    tmp_path: Path,
) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook_path = tmp_path / "identity_and_length.xlsx"
    frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "Length (m)": 1.2,
                "Notes": "hidden",
            }
        ]
    )
    tma_frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "TMA target": "50 MPa / 1 g",
            }
        ]
    )

    info = launcher_module._write_assemble_workbook(  # noqa: SLF001
        output_path=workbook_path,
        frame=frame,
        preset="public",
        tma_frame=tma_frame,
        selected_columns=["Length (m)"],
        column_order=["Length (m)"],
    )

    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    headers = [cell.value for cell in workbook["Analysis"][1]]
    assert headers == ["Composition", "Microwire", "Length (m)"]
    assert info["row_count"] == 1
    assert info["analysis_sheet"]["row_count"] == 1


def test_public_family_projection_without_detail_rows_keeps_explicit_status(
    tmp_path: Path,
) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook_path = tmp_path / "tma_status_only.xlsx"

    launcher_module._write_assemble_workbook(  # noqa: SLF001
        output_path=workbook_path,
        frame=pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    "TMA transition status": "No transition",
                    "Notes": "hidden",
                }
            ]
        ),
        preset="public",
        tma_frame=pd.DataFrame(),
        selected_columns=["TMA transition status"],
        column_order=["TMA transition status"],
    )

    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    worksheet = workbook["Analysis"]
    headers = [cell.value for cell in worksheet[1]]
    assert headers[:3] == ["Composition", "Microwire", "TMA transition status"]
    assert "TMA target" in headers
    assert "CA graph" not in headers
    assert "VSM scan" not in headers
    assert worksheet.cell(2, 3).value == "No transition"


def test_public_projection_keeps_compact_fallback_for_sample_without_detail_row(
    tmp_path: Path,
) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook_path = tmp_path / "mixed_detail.xlsx"
    frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "TMA transition status": "Reviewed",
            },
            {
                "Composition": "Ni44Fe27Ga23Cu3Co3",
                "Microwire": "1/2",
                "TMA transition status": "No transition",
            },
        ]
    )
    tma_frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "TMA target": "50 MPa / 1 g",
                "TMA strain (%)": 2.5,
            }
        ]
    )

    launcher_module._write_assemble_workbook(  # noqa: SLF001
        output_path=workbook_path,
        frame=frame,
        preset="public",
        tma_frame=tma_frame,
        selected_columns=["TMA transition status"],
    )

    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    worksheet = workbook["Analysis"]
    headers = [cell.value for cell in worksheet[1]]
    rows = [
        dict(zip(headers, [cell.value for cell in cells], strict=False))
        for cells in worksheet.iter_rows(min_row=2)
    ]
    assert {(row["Composition"], row["Microwire"]) for row in rows} == {
        ("Ni50Fe27Ga23", "12/2"),
        ("Ni44Fe27Ga23Cu3Co3", "1/2"),
    }
    fallback = next(row for row in rows if row["Microwire"] == "1/2")
    assert fallback["TMA transition status"] == "No transition"
    assert fallback["TMA target"] is None


def test_full_workbook_ignores_public_projection_and_keeps_legacy_analysis_schema(
    tmp_path: Path,
) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook_path = tmp_path / "full.xlsx"
    frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "d (µm)": 20.0,
                "Length (m)": 1.2,
            }
        ]
    )
    tma_frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "TMA target": "50 MPa / 1 g",
            }
        ]
    )

    launcher_module._write_assemble_workbook(  # noqa: SLF001
        output_path=workbook_path,
        frame=frame,
        preset="full",
        tma_frame=tma_frame,
        selected_columns=["Length (m)"],
        column_order=["Length (m)"],
    )

    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    headers = [cell.value for cell in workbook["Analysis"][1]]
    assert "d (µm)" in headers
    assert "TMA target" in headers
    assert "CA As1 (mA)" in headers
    assert "VSM scan" in headers
    assert workbook.sheetnames[:3] == ["Analysis", "Assemble", "TMA targets"]


def test_public_analysis_export_groups_expanded_rows_by_sample(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook_path = tmp_path / "grouped_analysis.xlsx"
    frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "d (\u00b5m)": 12.5,
                "D (\u00b5m)": 60.3,
            },
            {
                "Composition": "Ni44Fe27Ga23Cu3Co3",
                "Microwire": "1/2",
                "d (\u00b5m)": 13.9,
                "D (\u00b5m)": 51.8,
            },
        ]
    )
    annealing_frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "Annealing run": "Ni50Fe27Ga23 12_2 100mA.txt",
                "Annealing current (mA)": 100,
                "Annealing As1": 29,
            },
            {
                "Composition": "Ni44Fe27Ga23Cu3Co3",
                "Microwire": "1/2",
                "Annealing run": "Ni44Fe27Ga23Cu3Co3 1_2 60mA.txt",
                "Annealing current (mA)": 60,
                "Annealing As1": 38,
            },
        ]
    )
    vsm_frame = pd.DataFrame(
        [
            {
                "Composition": "Ni44Fe27Ga23Cu3Co3",
                "Microwire": "1/2",
                "VSM scan": "scan-a",
                "VSM As": "No transition",
            },
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "VSM scan": "scan-b",
                "VSM As": 55,
            },
        ]
    )
    tma_frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "TMA run": "run-b",
                "TMA target": "50 MPa / 1 g",
                "TMA target type": "Stress/load target",
                "TMA stress (MPa)": 50,
                "TMA strain (%)": 1.2,
            },
            {
                "Composition": "Ni44Fe27Ga23Cu3Co3",
                "Microwire": "1/2",
                "TMA run": "run-a",
                "TMA target": "1st: 20MPa / 0.31g",
                "TMA target type": "First overheating",
                "TMA stress (MPa)": 20,
                "TMA strain (%)": 0.3,
            },
            {
                "Composition": "Ni44Fe27Ga23Cu3Co3",
                "Microwire": "1/2",
                "TMA run": "run-a",
                "TMA target": "20 MPa / 0.31 g",
                "TMA target type": "Stress/load target",
                "TMA stress (MPa)": 20,
                "TMA strain (%)": 0.6,
            },
        ]
    )

    launcher_module._write_assemble_workbook(  # noqa: SLF001
        output_path=workbook_path,
        frame=frame,
        preset="public",
        extra_frames={
            launcher_module.ANNEALING_TRANSITION_EXPORT_SHEET: annealing_frame,
            launcher_module.VSM_TRANSITION_EXPORT_SHEET: vsm_frame,
            launcher_module.TMA_TARGET_EXPORT_SHEET: tma_frame,
        },
    )

    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    assert workbook.sheetnames == ["Analysis"]
    worksheet = workbook["Analysis"]
    headers = [cell.value for cell in worksheet[1]]
    rows = [
        dict(zip(headers, [cell.value for cell in row_cells], strict=False))
        for row_cells in worksheet.iter_rows(min_row=2)
    ]
    identities = [(row["Composition"], row["Microwire"]) for row in rows]
    assert identities == [
        ("Ni44Fe27Ga23Cu3Co3", "1/2"),
        ("Ni44Fe27Ga23Cu3Co3", "1/2"),
        ("Ni44Fe27Ga23Cu3Co3", "1/2"),
        ("Ni44Fe27Ga23Cu3Co3", "1/2"),
        ("Ni50Fe27Ga23", "12/2"),
        ("Ni50Fe27Ga23", "12/2"),
        ("Ni50Fe27Ga23", "12/2"),
    ]
    sample_rows = rows[:4]
    assert [row["CA graph"] for row in sample_rows] == [
        "Ni44Fe27Ga23Cu3Co3 1_2 60mA.txt",
        None,
        None,
        None,
    ]
    assert sample_rows[1]["VSM scan"] == "scan-a"
    assert [row["TMA target type"] for row in sample_rows[2:]] == [
        "First overheating",
        "Stress/load target",
    ]
    assert "Analysis row type" not in headers


def test_public_assemble_workbook_excludes_oe_and_collapses_non_identity_suffixes(
    tmp_path: Path,
) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook_path = tmp_path / "public_suffixes.xlsx"
    frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "5/4",
                "d (\u00b5m)": None,
                "D (\u00b5m)": None,
                "Source label": "Praha",
            },
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "5/4noload",
                "d (\u00b5m)": 12.0,
                "D (\u00b5m)": 42.0,
                "Source label": "Praha",
            },
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "5/4oe",
                "d (\u00b5m)": 13.0,
                "D (\u00b5m)": 43.0,
                "Source label": "Praha",
            },
        ]
    )
    tma_frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "5/4No1",
                "TMA run": "run-a",
                "TMA target": "50 MPa / 1 g",
                "TMA strain (%)": 2.5,
            },
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "5/4No1",
                "TMA run": "run-b",
                "TMA target": "100 MPa / 2 g",
                "TMA strain (%)": 3.5,
            },
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "5/4oe",
                "TMA run": "run-oe",
                "TMA target": "50 MPa / 1 g",
                "TMA strain (%)": 4.5,
            },
        ]
    )

    info = launcher_module._write_assemble_workbook(  # noqa: SLF001
        output_path=workbook_path,
        frame=frame,
        preset="public",
        tma_frame=tma_frame,
    )

    assert info["row_count"] == 2
    assert info["public_filters"]["assemble"] == {
        "excluded_oe_rows": 1,
        "normalised_suffix_rows": 1,
        "collapsed_suffix_rows": 1,
    }
    assert info["public_filters"]["TMA targets"] == {
        "excluded_oe_rows": 1,
        "normalised_suffix_rows": 2,
    }
    assert info["analysis_sheet"]["row_count"] == 2
    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    assert workbook.sheetnames == ["Analysis"]
    analysis_headers = [cell.value for cell in workbook["Analysis"][1]]
    analysis_rows = [
        dict(zip(analysis_headers, [cell.value for cell in row_cells], strict=False))
        for row_cells in workbook["Analysis"].iter_rows(min_row=2)
    ]
    assert [row["Microwire"] for row in analysis_rows] == ["5/4", "5/4"]
    assert [row["TMA target"] for row in analysis_rows] == ["50 MPa / 1 g", "100 MPa / 2 g"]
    assert [row["TMA run"] for row in analysis_rows] == ["run-a", "run-b"]


def test_saved_video_table_values_overlay_rebuilt_assemble_rows() -> None:
    frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "Length (m)": float("nan"),
                "Video wire range (m)": float("nan"),
            }
        ]
    )
    sections = {
        "videos": {
            "rows": [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    "Production datetime": "2025-07-18 09:35:00",
                    "Length (m)": 12.56,
                    "Mass (g)": 2.42,
                    "Video end length (m)": 120.0,
                    "Video wire range (m)": "110-120",
                }
            ]
        }
    }

    result = launcher_module._overlay_saved_video_table_values(frame, sections)  # noqa: SLF001

    assert result.loc[0, "Production datetime"] == "2025-07-18 09:35:00"
    assert result.loc[0, "Length (m)"] == 12.56
    assert result.loc[0, "Mass (g)"] == 2.42
    assert result.loc[0, "Video end length (m)"] == 120.0
    assert result.loc[0, "Video wire range (m)"] == "110-120"


def test_expanded_transition_export_frames_use_review_records() -> None:
    sections = {
        "annealing": {
            "extra": {
                "transition_reviews": {
                    "records": {
                        "anneal-accepted": {
                            "status": "manual_adjusted",
                            "composition": "Ni50Fe27Ga23",
                            "microwire": "12/2",
                            "graph_label": "Ni50Fe27Ga23 12_2 100mA",
                            "setpoint_mA": 100,
                            "final_values_mA": {"As1": 30.0, "Af1": 70.0},
                        },
                        "anneal-none": {
                            "status": "no_transition",
                            "composition": "Ni50Fe27Ga23",
                            "microwire": "12/3",
                            "graph_label": "Ni50Fe27Ga23 12_3 80mA",
                            "setpoint_mA": 80,
                        },
                        "anneal-excluded": {
                            "status": "excluded",
                            "composition": "Ni50Fe27Ga23",
                            "microwire": "12/4",
                            "graph_label": "excluded",
                        },
                    }
                }
            }
        },
        "vsm_temperature_scan": {
            "extra": {
                "transition_reviews": {
                    "records": {
                        "vsm-accepted": {
                            "status": "accepted_auto",
                            "sample": "Ni50Fe27Ga23 12_2",
                            "record_label": "VSM 12_2 scan",
                            "final_values_C": {"As": -20.0, "Af": 15.0, "Ms": 5.0, "Mf": -30.0},
                        },
                        "vsm-none": {
                            "status": "no_transition",
                            "sample": "Ni50Fe27Ga23 12_3",
                            "record_label": "VSM 12_3 scan",
                        },
                        "vsm-excluded": {
                            "status": "excluded",
                            "sample": "Ni50Fe27Ga23 12_4",
                            "record_label": "excluded",
                        },
                    }
                }
            }
        },
    }

    annealing = launcher_module._expanded_annealing_transition_frame_from_sections(sections)  # noqa: SLF001
    vsm = launcher_module._expanded_vsm_transition_frame_from_sections(sections)  # noqa: SLF001

    annealing_rows = annealing.to_dict(orient="records")
    assert annealing_rows[0]["Composition"] == "Ni50Fe27Ga23"
    assert annealing_rows[0]["Microwire"] == "12/2"
    assert annealing_rows[0]["Annealing run"] == "Ni50Fe27Ga23 12_2 100mA"
    assert annealing_rows[0]["Annealing current (mA)"] == 100
    assert annealing_rows[0]["Annealing As1"] == 30.0
    assert annealing_rows[0]["Annealing Af1"] == 70.0
    assert all(
        pd.isna(annealing_rows[0][column])
        for column in (
            "Annealing Ms1",
            "Annealing Mf1",
            "Annealing As2",
            "Annealing Af2",
            "Annealing Ms2",
            "Annealing Mf2",
        )
    )
    assert annealing_rows[1:] == [
        {
            "Composition": "Ni50Fe27Ga23",
            "Microwire": "12/3",
            "Annealing run": "Ni50Fe27Ga23 12_3 80mA",
            "Annealing current (mA)": 80,
            "Annealing As1": "No transition",
            "Annealing Af1": "No transition",
            "Annealing Ms1": "No transition",
            "Annealing Mf1": "No transition",
            "Annealing As2": "No transition",
            "Annealing Af2": "No transition",
            "Annealing Ms2": "No transition",
            "Annealing Mf2": "No transition",
        },
    ]
    assert vsm.to_dict(orient="records") == [
        {
            "Composition": "Ni50Fe27Ga23",
            "Microwire": "12/2",
            "VSM scan": "VSM 12_2 scan",
            "VSM As": -20.0,
            "VSM Af": 15.0,
            "VSM Ms": 5.0,
            "VSM Mf": -30.0,
        },
        {
            "Composition": "Ni50Fe27Ga23",
            "Microwire": "12/3",
            "VSM scan": "VSM 12_3 scan",
            "VSM As": "No transition",
            "VSM Af": "No transition",
            "VSM Ms": "No transition",
            "VSM Mf": "No transition",
        },
    ]


def test_vsm_transition_export_reads_transition_temps_section_reviews() -> None:
    sections = {
        "transition_temps": {
            "extra": {
                "transition_reviews": {
                    "records": {
                        "vsm-ts:accepted": {
                            "status": "manual_adjusted",
                            "group_key": "Ni50Fe27Ga23|12|2",
                            "sample": "Ni50Fe27Ga23 12_2",
                            "record_label": "20260630-TSCN-a000",
                            "final_values_C": {"As": 30.0, "Af": 65.0},
                        },
                        "vsm-ts:none": {
                            "status": "no_transition",
                            "group_key": "Ni50Fe27Ga23|12|3",
                            "sample": "Ni50Fe27Ga23 12_3",
                            "record_label": "20260630-TSCN-a090",
                        },
                    }
                }
            }
        }
    }

    compact = launcher_module._transition_reviews_to_compact_map(  # noqa: SLF001
        sections["transition_temps"]["extra"]["transition_reviews"]["records"]
    )
    assert compact["Ni50Fe27Ga23|12|2"]["As"] == 30.0
    assert compact["Ni50Fe27Ga23|12|2"]["Af"] == 65.0
    assert compact["Ni50Fe27Ga23|12|2"]["__review_status__"] == "Manual adjusted"
    assert compact["Ni50Fe27Ga23|12|3"]["__review_status__"] == "No transition"
    assert compact["Ni50Fe27Ga23|12|3"]["__included__"] is False

    vsm = launcher_module._expanded_vsm_transition_frame_from_sections(sections)  # noqa: SLF001
    rows = vsm.to_dict(orient="records")
    assert rows[0]["Composition"] == "Ni50Fe27Ga23"
    assert rows[0]["Microwire"] == "12/2"
    assert rows[0]["VSM scan"] == "20260630-TSCN-a000"
    assert rows[0]["VSM As"] == 30.0
    assert rows[0]["VSM Af"] == 65.0
    assert pd.isna(rows[0]["VSM Ms"])
    assert pd.isna(rows[0]["VSM Mf"])
    assert rows[1:] == [
        {
            "Composition": "Ni50Fe27Ga23",
            "Microwire": "12/3",
            "VSM scan": "20260630-TSCN-a090",
            "VSM As": "No transition",
            "VSM Af": "No transition",
            "VSM Ms": "No transition",
            "VSM Mf": "No transition",
        },
    ]


def test_tma_target_export_includes_strain_only_record_payloads() -> None:
    records = [
        MiniDmaRecord(
            path=Path("G:/runs/Ni50Fe27Ga23 6_6 run01"),
            sample="Ni50Fe27Ga23 6-6",
            data=pd.DataFrame(),
            label="iso-stress - Ni50Fe27Ga23 6_6 run01",
            strain_summary=(
                "20 MPa / 0.37 g: 0.86% @ 59 mA",
                "50 MPa / 0.93 g: 0.63% @ 40 mA",
            ),
        ),
        MiniDmaRecord(
            path=Path("G:/runs/Ni50Fe27Ga23 6_6 run02"),
            sample="Ni50Fe27Ga23 6-6",
            data=pd.DataFrame(),
            label="iso-stress - Ni50Fe27Ga23 6_6 run02",
            strain_summary=("50 MPa / 0.93 g: 0.72% @ 42 mA",),
        ),
    ]
    encoded = safe_codec.encode_envelope(records)
    frame = launcher_module._expanded_tma_export_frame_from_sections(  # noqa: SLF001
        {
            "mini_dma": {
                "payloads": {
                    "mini_dma_records": encoded
                }
            }
        }
    )

    assert len(frame.index) == 3
    rows = frame.to_dict(orient="records")
    assert [row["TMA run"] for row in rows] == [
        "iso-stress - Ni50Fe27Ga23 6_6 run01",
        "iso-stress - Ni50Fe27Ga23 6_6 run01",
        "iso-stress - Ni50Fe27Ga23 6_6 run02",
    ]
    assert [row["TMA target"] for row in rows] == [
        "20 MPa / 0.37 g",
        "50 MPa / 0.93 g",
        "50 MPa / 0.93 g",
    ]
    assert [row["TMA strain (%)"] for row in rows] == [0.86, 0.63, 0.72]
    assert [row["TMA strain peak current (mA)"] for row in rows] == [59.0, 40.0, 42.0]
    assert all(row["TMA As"] is None or row["TMA As"] != row["TMA As"] for row in rows)


def test_tma_target_export_recalculates_stale_strain_summary_from_raw_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_run = tmp_path / "Ni50Fe27Ga23 12_2 raw_run"
    raw_run.mkdir()
    records = [
        MiniDmaRecord(
            path=raw_run,
            sample="Ni50Fe27Ga23 12-2",
            data=pd.DataFrame(),
            label="raw run",
            strain_summary=("50 MPa / 1.46 g: 0.08% @ 80 mA",),
        ),
    ]
    encoded = safe_codec.encode_envelope(records)

    def fake_import_module(name: str) -> object:
        if name != "plotting.plugins.mini_dma.core":
            return importlib.import_module(name)
        return SimpleNamespace(
            load_run=lambda path: SimpleNamespace(path=path),
            summarize_current_sweep=lambda run: SimpleNamespace(run=run),
            format_current_sweep_strain_summary=lambda summary: (
                "50 MPa / 1.46 g: 10.81% @ 4 mA",
            ),
        )

    import importlib

    monkeypatch.setattr(launcher_module, "import_module", fake_import_module)
    frame = launcher_module._expanded_tma_export_frame_from_sections(  # noqa: SLF001
        {
            "mini_dma": {
                "payloads": {
                    "mini_dma_records": encoded
                }
            }
        }
    )

    rows = frame.to_dict(orient="records")
    assert len(rows) == 1
    assert rows[0]["TMA strain (%)"] == 10.81
    assert rows[0]["TMA strain peak current (mA)"] == 4.0


def test_builder_automation_recipe_exports_assemble_public_workbook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    monkeypatch.delenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", raising=False)
    project_path = _write_synthetic_assemble_project(tmp_path / "synthetic.pydpj")
    output_project = tmp_path / "working" / "updated.pydpj"
    workbook_path = tmp_path / "exports" / "assemble_public.xlsx"
    manifest_path = tmp_path / "exports" / "assemble_public.manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "commands": [
                    {
                        "action": "export_assemble",
                        "preset": "public",
                        "output": str(workbook_path),
                        "manifest_path": str(manifest_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["workbook"] == str(workbook_path.resolve())
    assert manifest["row_count"] == 6
    assert "Data source" in manifest["dropped_columns"]
    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    assert workbook.sheetnames == ["Analysis"]
    assert [cell.value for cell in workbook["Analysis"][1]][:2] == ["Composition", "Microwire"]
    assert "MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS" not in os.environ


def test_forced_assemble_export_restores_temporary_headless_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = _write_synthetic_assemble_project(tmp_path / "synthetic.pydpj")
    output_path = tmp_path / "public.xlsx"
    monkeypatch.delenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(
        launcher_module,
        "_run_builder_rebuild_assemble_command_lightweight",
        lambda **_kwargs: {"status": "ok"},
    )

    _payload, copied_project, rebuild_result = (
        launcher_module._prepare_assemble_export_project_payload(  # noqa: SLF001
            source_project=project_path,
            output_path=output_path,
            working_copy_dir=tmp_path / "working",
            copy_project=True,
            force_rebuild=True,
            rebuild_sections=None,
        )
    )

    assert copied_project is not None
    assert rebuild_result == {"status": "ok"}
    assert "MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS" not in os.environ
    assert "QT_QPA_PLATFORM" not in os.environ


def test_builder_update_filters_existing_records_under_refresh_root(tmp_path: Path) -> None:
    refresh_root = tmp_path / "mini DMA"
    stale_path = refresh_root / "Ni46Fe27Ga23Co2Cu2 2_8 iso-stress" / "measurement.csv"
    fresh_path = refresh_root / "Ni46Fe27Ga23Cu2Co2 2_8 iso-stress" / "measurement.csv"
    unrelated_path = tmp_path / "other" / "measurement.csv"

    records = [
        SimpleNamespace(path=str(stale_path), label="stale"),
        SimpleNamespace(path=str(fresh_path), label="fresh"),
        SimpleNamespace(path=str(unrelated_path), label="unrelated"),
    ]

    filtered = launcher_module._filter_builder_records_outside_refresh_roots(
        records,
        [refresh_root],
    )

    assert [record.label for record in filtered] == ["unrelated"]


def test_microwire_word_graph_sections_record_reference_and_origin_status() -> None:
    from microwire_data_builder.core import OriginArtifact

    source_only = {
        "Manual stress/strain graphs": [
            "20mA fracture -- Ni52Fe15Ga27Co6 2/1oe",
            "30mA fracture -- Ni52Fe15Ga27Co6 2/1oe",
        ],
    }
    with_origin = {
        "Manual stress/strain graphs": ["30mA"],
        "Manual stress/strain graphs (Origin)": "shape_memory.oggu",
    }

    source_section = launcher_module._microwire_word_graph_sections_for_row(source_only)[
        "Manual stress/strain"
    ]
    assert source_section["included"] is True
    assert source_section["reason"] == "reference_content"
    assert source_section["graphs"] == []
    assert source_section["references"] == [
        "20mA fracture -- Ni52Fe15Ga27Co6 2/1oe",
        "30mA fracture -- Ni52Fe15Ga27Co6 2/1oe",
    ]

    missing_section = launcher_module._microwire_word_graph_sections_for_row(with_origin)[
        "Manual stress/strain"
    ]
    assert missing_section["included"] is True
    assert missing_section["reason"] == "reference_content"
    assert missing_section["graphs"] == []
    assert missing_section["references"] == ["30mA"]
    assert missing_section["missing_origin_descriptors"] == ["shape_memory.oggu"]

    artifact_section = launcher_module._microwire_word_graph_sections_for_row(
        with_origin,
        {
            "shape_memory.oggu": OriginArtifact(
                descriptor="shape_memory.oggu",
                object_path=Path("shape_memory.oggu"),
                display_text="shape memory",
            )
        },
    )["Manual stress/strain"]
    assert artifact_section["included"] is True
    assert artifact_section["reason"] == "accepted_origin_object"
    assert artifact_section["graphs"] == ["shape_memory.oggu"]
    assert artifact_section["references"] == ["30mA"]


def test_microwire_word_graph_sections_accept_legacy_shape_memory_columns() -> None:
    from microwire_data_builder.core import OriginArtifact

    row = {
        "Shape memory stress/strain graphs": ["30mA"],
        "Shape memory stress/strain graphs (Origin)": "legacy_shape_memory.oggu",
    }

    section = launcher_module._microwire_word_graph_sections_for_row(
        row,
        {
            "legacy_shape_memory.oggu": OriginArtifact(
                descriptor="legacy_shape_memory.oggu",
                object_path=Path("legacy_shape_memory.oggu"),
                display_text="legacy shape memory",
            )
        },
    )["Manual stress/strain"]
    assert section["included"] is True
    assert section["reason"] == "accepted_origin_object"
    assert section["graphs"] == ["legacy_shape_memory.oggu"]
    assert section["references"] == ["30mA"]


def _wait_for_registry(window: launcher_module.MasterLauncher, app: QtWidgets.QApplication) -> None:
    for _ in range(40):
        app.processEvents()
        if getattr(window, "_registry_loaded", False):
            return
    raise AssertionError("Launcher registry did not finish loading in time.")


@pytest.mark.parametrize(
    ("name", "module", "resource_tag"),
    [
        (
            "Current Annealing Logger",
            "data_logging.current_annealing_logger.current_annealing_logger",
            "current_annealing",
        ),
        (
            "AC Susceptibility Logger",
            "data_logging.ac_susceptibility_logger.ac_susceptibility_logger",
            "ac_susceptibility",
        ),
        (
            "TMA Logger",
            "data_logging.mini_dma_logger.mini_dma_logger",
            "mini_dma",
        ),
    ],
)
def test_hardware_experiment_loggers_launch_in_child_process(
    name: str,
    module: str,
    resource_tag: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[object] = []
    monkeypatch.setattr(
        launcher_module,
        "launch_experiment_process",
        lambda spec: launched.append(spec),
    )

    result = launcher_module.LOGGERS[name]()

    assert result is None
    assert launched
    spec = launched[0]
    assert getattr(spec, "display_name") == name
    assert getattr(spec, "module") == module
    assert getattr(spec, "resource_tag") == resource_tag


def test_launcher_plotting_list_refreshes_using_last_opened_order(
    monkeypatch,
) -> None:
    app = _ensure_app()
    fake_registry = {
        "loggers": {},
        "plotters": {
            "ZZ Plot A": lambda: None,
            "ZZ Plot B": lambda: None,
        },
        "emulators": {},
    }
    monkeypatch.setattr(launcher_module, "_build_registry", lambda: fake_registry)

    window = launcher_module.MasterLauncher()
    try:
        _wait_for_registry(window, app)
        assert window._sort_modes.get("plotters") == "last_used"  # noqa: SLF001 - test hook

        now = time.time()
        window._settings.setValue("launcher_last_order/seq", 200)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot A", 100)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot B", 200)
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot A", now - 100.0)  # noqa: SLF001 - test hook
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot B", now)  # noqa: SLF001 - test hook
        window._refresh_list("plotters")  # noqa: SLF001 - test hook
        app.processEvents()
        assert window.plot_list.item(0).text() == "ZZ Plot B"

        # Simulate tool usage while the launcher is hidden, then restore it:
        # _restore_launcher should refresh the visible order from settings.
        window._settings.setValue("launcher_last_order/seq", 250)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot A", 250)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot B", 200)
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot A", now + 200.0)  # noqa: SLF001 - test hook
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot B", now)  # noqa: SLF001 - test hook
        window.hide()
        window._restore_launcher()  # noqa: SLF001 - test hook
        app.processEvents()
        assert window.plot_list.item(0).text() == "ZZ Plot A"
    finally:
        window.close()
        app.processEvents()


def test_graph_option_defaults_apply_figure_size_to_new_plot_tabs() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._graph_option_defaults_global = window._clean_graph_option_payload(  # noqa: SLF001 - test hook
            {
                "figure_width": 8.4,
                "figure_height": 5.6,
                "figure_width_auto": False,
                "figure_height_auto": False,
            }
        )

        fig = Figure(figsize=(3.0, 3.0))
        axes = fig.add_subplot(111)
        axes.set_title("Example")
        axes.set_xlabel("X")
        axes.set_ylabel("Y")
        canvas = FigureCanvas(fig)

        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)

        descriptor = TabDescriptor(
            kind="unit_test",
            title="Example",
            root_label="Example Plot",
            x_label="X",
            y_label="Y",
            canvas=canvas,
            axes=axes,
            lines={},
            metadata={"plugin": "Unit Test Plugin"},
        )
        index = window.tab_widget.addTab(tab, "Example Plot")
        window.tab_widget.setCurrentIndex(index)
        window._register_plot_tab(tab, canvas, axes, descriptor)  # noqa: SLF001 - test hook

        width_in, height_in = fig.get_size_inches()
        assert width_in == pytest.approx(8.4, rel=1e-3)
        assert height_in == pytest.approx(5.6, rel=1e-3)
    finally:
        window.close()
        app.processEvents()


def test_launcher_detects_pyplot_automation_flags() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(  # noqa: SLF001 - internal parser
        [
            "--pyplot-plugin",
            "Hysteresis Loops",
            "--pyplot-import",
            "sample_data/hysteresis_loops",
            "--pyplot-plot",
        ]
    )
    assert launcher_module._is_pyplot_automation_requested(args) is True  # noqa: SLF001


def test_launcher_detects_pyplot_session_flags() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(  # noqa: SLF001 - internal parser
        [
            "--pyplot-session-send",
            "--pyplot-session-id",
            "example-session",
            "--pyplot-session-command-json",
            '{"action":"state"}',
        ]
    )
    assert launcher_module._is_pyplot_session_requested(args) is True  # noqa: SLF001


def test_pyplot_session_command_payload_includes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_payloads: list[dict[str, object]] = []
    socket_timeouts: list[float] = []

    class FakeSocket:
        def __init__(self) -> None:
            self._response_sent = False

        def __enter__(self) -> "FakeSocket":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def settimeout(self, timeout: float) -> None:
            socket_timeouts.append(timeout)

        def sendall(self, raw: bytes) -> None:
            sent_payloads.append(json.loads(raw.decode("utf-8")))

        def recv(self, _size: int) -> bytes:
            if self._response_sent:
                return b""
            self._response_sent = True
            return b'{"status":"ok"}\n'

    def fake_create_connection(address: tuple[str, int], timeout: float) -> FakeSocket:
        assert address == ("127.0.0.1", 4567)
        socket_timeouts.append(timeout)
        return FakeSocket()

    monkeypatch.setattr(
        launcher_module,
        "_get_session_record",
        lambda _session_id: {"host": "127.0.0.1", "port": 4567, "token": "secret"},
    )
    monkeypatch.setattr(launcher_module.socket, "create_connection", fake_create_connection)

    response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
        "session-id",
        {"action": "open_origin"},
        timeout_s=240.0,
    )

    assert response == {"status": "ok"}
    assert sent_payloads == [
        {
            "token": "secret",
            "command": {"action": "open_origin"},
            "timeout_s": 240.0,
        }
    ]
    assert socket_timeouts == [240.0, 240.0]


def test_launcher_detects_microwire_eda_cli_flags() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(
        [
            "--microwire-eda",
            "sample.pydpj",
            "--rows",
            "filtered",
            "--out",
            "artifacts/eda",
        ]
    )
    assert launcher_module._is_microwire_eda_requested(args) is True  # noqa: SLF001
    assert args.rows == "filtered"
    assert args.out == "artifacts/eda"
    assert args.microwire_eda_copy_project is True
    assert args.microwire_eda_force_project_rebuild is False


def test_launcher_detects_mini_dma_bench_plan_flag() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(
        [
            "--mini-dma-bench-plan",
            "bench-plan.json",
        ]
    )
    assert launcher_module._is_mini_dma_bench_requested(args) is True  # noqa: SLF001
    assert args.mini_dma_bench_plan == "bench-plan.json"


def test_launcher_detects_tma_bench_plan_alias() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(
        [
            "--tma-bench-plan",
            "bench-plan.json",
        ]
    )
    assert launcher_module._is_mini_dma_bench_requested(args) is True  # noqa: SLF001
    assert args.mini_dma_bench_plan == "bench-plan.json"


def test_launcher_detects_microwire_word_report_cli_flags() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(
        [
            "--microwire-word-report",
            "Ni50Fe27Ga23_12_2.csv",
            "--microwire-word-sample",
            "Ni50Fe27Ga23 12/2",
            "--out",
            "artifacts/word-report",
        ]
    )

    assert launcher_module._is_microwire_word_report_requested(args) is True  # noqa: SLF001
    assert args.microwire_word_report == "Ni50Fe27Ga23_12_2.csv"
    assert args.microwire_word_sample == "Ni50Fe27Ga23 12/2"
    assert args.microwire_word_origin is True
    assert args.out == "artifacts/word-report"


def test_launcher_detects_microwire_word_job_flag() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(
        [
            "--microwire-word-job",
            "jobs/word-export.json",
        ]
    )

    assert launcher_module._is_microwire_word_job_requested(args) is True  # noqa: SLF001
    assert args.microwire_word_job == "jobs/word-export.json"


def test_run_microwire_word_job_dry_run_writes_status_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "database.pydpj"
    source.write_text('{"sections": {}}', encoding="utf-8")
    job_path = tmp_path / "word-job.json"
    job_path.write_text(
        json.dumps(
            {
                "version": 1,
                "job_type": "microwire_word_export",
                "job_id": "dry_word",
                "source": str(source),
                "output_dir": str(tmp_path / "reports"),
                "sample": "Ni50Fe27Ga23 12/2",
                "include_origin": True,
                "force_project_rebuild": True,
                "graphs_only": True,
                "dry_run": True,
                "paths": {
                    "status": str(tmp_path / "status.json"),
                    "progress": str(tmp_path / "progress.json"),
                    "manifest": str(tmp_path / "manifest.json"),
                    "log": str(tmp_path / "job.log"),
                    "cancel": str(tmp_path / "cancel.requested"),
                },
            }
        ),
        encoding="utf-8-sig",
    )
    args = argparse.Namespace(microwire_word_job=str(job_path))

    exit_code = launcher_module._run_microwire_word_job_cli(args)  # noqa: SLF001

    assert exit_code == 0
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert status["state"] == "succeeded"
    assert status["dry_run"] is True
    assert progress["events"][-1]["event"] == "validated"
    assert manifest["job_type"] == "microwire_word_export"
    assert manifest["dry_run"] is True
    assert "--microwire-word-report" in manifest["equivalent_command"]
    assert "--microwire-word-graphs-only" in manifest["equivalent_command"]
    output = capsys.readouterr().out
    assert "dry_run=true" in output
    assert "manifest=" in output


def test_run_microwire_word_job_honors_pre_start_cancel(tmp_path: Path) -> None:
    source = tmp_path / "database.pydpj"
    source.write_text('{"sections": {}}', encoding="utf-8")
    cancel = tmp_path / "cancel.requested"
    cancel.write_text("stop", encoding="utf-8")
    job_path = tmp_path / "word-job.json"
    job_path.write_text(
        json.dumps(
            {
                "version": 1,
                "job_type": "microwire_word_export",
                "job_id": "cancelled_word",
                "source": str(source),
                "dry_run": True,
                "paths": {
                    "status": str(tmp_path / "status.json"),
                    "progress": str(tmp_path / "progress.json"),
                    "manifest": str(tmp_path / "manifest.json"),
                    "log": str(tmp_path / "job.log"),
                    "cancel": str(cancel),
                },
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(microwire_word_job=str(job_path))

    exit_code = launcher_module._run_microwire_word_job_cli(args)  # noqa: SLF001

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 130
    assert status["state"] == "cancelled"
    assert manifest["state"] == "cancelled"


def test_run_microwire_word_report_cli_accepts_rvst_csv(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "Ni50Fe27Ga23_12_2.csv"
    source.write_text(
        "\n".join(
            [
                "iso_time;t_elapsed_s;sp_c;pv_c;resistance_ohm",
                "2026-02-06T08:22:38;0.1;-100;-40.5;43.2903",
                "2026-02-06T08:22:48;10.1;-90;-39.0;43.2882",
                "2026-02-06T08:22:58;20.1;-80;-37.5;43.2700",
            ]
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "reports"
    args = argparse.Namespace(
        microwire_word_report=str(source),
        microwire_word_sample="Ni50Fe27Ga23 12/2",
        microwire_word_force_project_rebuild=False,
        microwire_word_origin=False,
        out=str(output_dir),
    )

    exit_code = launcher_module._run_microwire_word_report_cli(args)  # noqa: SLF001 - internal automation hook

    report_path = output_dir / "Ni50Fe27Ga23_12-2.docx"
    assert exit_code == 0
    assert report_path.exists()
    output = capsys.readouterr().out
    assert "reports=1" in output
    assert str(report_path) in output


def test_microwire_word_report_project_merges_section_rows_and_rvst(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "Praha"
    project_path = tmp_path / "copied" / "microwire_project_copy.pydpj"
    project_path.parent.mkdir()
    annealing_path = data_root / "current annealing" / "Ni50Fe27Ga23 12_2 s1 1000mA.txt"
    annealing_path.parent.mkdir(parents=True)
    annealing_path.write_text("0.1 40 1\n0.2 41 1\n", encoding="utf-8")
    rvt_path = data_root / "RvsT" / "RvsT" / "Ni50Fe27Ga23_12_2.csv"
    rvt_path.parent.mkdir(parents=True)
    rvt_path.write_text(
        "\n".join(
            [
                "iso_time;t_elapsed_s;sp_c;pv_c;resistance_ohm",
                "2026-02-06T08:22:38;0.1;-100;-40.5;43.2903",
                "2026-02-06T08:22:48;10.1;-90;-39.0;43.2882",
            ]
        ),
        encoding="utf-8",
    )
    mini_dma_path = data_root / "mini DMA" / "Ni50Fe27Ga23 12_2 test_run32" / "measurement.csv"
    mini_dma_path.parent.mkdir(parents=True)
    mini_dma_path.write_text(
        "\n".join(
            [
                "elapsed_s,automation_phase,automation_target_value,plateau_index,strain_pct,resistance_ohm,current_measured_mA",
                "0.1,current,50,1,0.0,100.0,1.0",
                "0.2,current,50,1,0.1,101.0,2.0",
            ]
        ),
        encoding="utf-8",
    )
    project_path.write_text(
        json.dumps(
            {
                "kind": "microwire_data_builder",
                "version": 1,
                "sections": {
                    "assemble": {"rows": [], "columns": []},
                    "annealing": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "_sources": [str(annealing_path)],
                            }
                        ]
                    },
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "d (µm)": 19.1,
                                "D (µm)": 58.6,
                                "d/D": 0.326,
                                "_core_image": str(tmp_path / "core.jpg"),
                            }
                        ]
                    },
                    "vsm_temperature_scan": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "VSM temperature scan graphs": ["scan-a", "scan-b"],
                            }
                        ]
                    },
                    "strain": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "Legacy strain": 1.37,
                            }
                        ]
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        microwire_word_sample="Ni50Fe27Ga23 12/2",
        microwire_word_origin=False,
    )

    frame, origin_artifacts = launcher_module._load_microwire_word_report_frame(  # noqa: SLF001
        project_path,
        args,
        tmp_path / "reports",
    )

    copied_projects = list((tmp_path / "reports" / "_project_copy").glob("*.pydpj"))
    assert len(copied_projects) == 1
    assert copied_projects[0] != project_path
    assert copied_projects[0].read_text(encoding="utf-8") == project_path.read_text(encoding="utf-8")
    assert origin_artifacts == {}
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["Composition"] == "Ni50Fe27Ga23"
    assert row["Microwire"] == "12/2"
    assert row["d (µm)"] == 19.1
    assert row["D (µm)"] == 58.6
    assert row["d/D"] == 0.326
    assert row["Legacy strain"] == 1.37
    assert row["Figure — 1000 mA"] == annealing_path.name
    assert row["VSM temperature scan graphs"] == ["scan-a", "scan-b"]
    assert row["R vs T graphs"] == [rvt_path.name]
    assert row["R vs T points"] == 2
    assert row["R vs T temperature range (deg C)"] == "-40.5 to -39"
    assert row["TMA graphs"] == mini_dma_path.parent.name


def test_microwire_word_report_project_replaces_stale_mini_dma_sources_with_active_runs(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "Praha"
    project_path = data_root / "microwire_project_copy.pydpj"
    project_path.parent.mkdir(parents=True)
    active_a = data_root / "mini DMA" / "Ni50Fe27Ga23 12_2 heat shield iso-stress_run03"
    active_b = data_root / "mini DMA" / "Ni50Fe27Ga23 12_2 baseline-50mpa-01"
    archived = data_root / "mini DMA" / "archive" / "Ni50Fe27Ga23 12_2 old_run01"
    for path in (active_a, active_b, archived):
        path.mkdir(parents=True)
        (path / "measurement.csv").write_text(
            "\n".join(
                [
                    "elapsed_s,automation_phase,automation_target_value,plateau_index,strain_pct,resistance_ohm,current_measured_mA",
                    "0.1,current,50,1,0.0,100.0,1.0",
                    "0.2,current,50,1,0.1,101.0,2.0",
                ]
            ),
            encoding="utf-8",
        )
    project_path.write_text(
        json.dumps(
            {
                "kind": "microwire_data_builder",
                "version": 1,
                "sections": {
                    "mini_dma": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "Mini DMA graphs": ["stale archived run"],
                                "_sources": [str(archived)],
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        microwire_word_sample="Ni50Fe27Ga23 12/2",
        microwire_word_origin=False,
    )

    frame, _origin_artifacts = launcher_module._load_microwire_word_report_frame(  # noqa: SLF001
        project_path,
        args,
        tmp_path / "reports",
    )

    assert len(frame) == 1
    row = frame.iloc[0]
    assert set(row["_word_mini_dma_sources"]) == {str(active_a), str(active_b)}
    mini_dma_graphs = row["TMA graphs"]
    assert set(mini_dma_graphs) == {active_a.name, active_b.name}
    assert archived.name not in mini_dma_graphs
    assert "stale archived run" not in mini_dma_graphs


def test_microwire_word_report_project_blocks_stale_mini_dma_when_newest_active_run_unfinished(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "Praha"
    project_path = data_root / "microwire_project_copy.pydpj"
    project_path.parent.mkdir(parents=True)
    old_finished = data_root / "mini DMA" / "Ni50Fe27Ga23 12_2 old_run01"
    newest_running = data_root / "mini DMA" / "Ni50Fe27Ga23 12_2 active_run02"
    for path, metadata in (
        (
            old_finished,
            {
                "sample_name": "Ni50Fe27Ga23 12/2",
                "created_utc": "2026-06-01 09:00:00",
                "session_state": "finished",
                "finished_utc": "2026-06-01 09:20:00",
            },
        ),
        (
            newest_running,
            {
                "sample_name": "Ni50Fe27Ga23 12/2",
                "created_utc": "2026-06-01 10:00:00",
                "session_state": "running",
            },
        ),
    ):
        path.mkdir(parents=True)
        (path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (path / "measurement.csv").write_text(
            "\n".join(
                [
                    "elapsed_s,automation_phase,automation_target_value,plateau_index,strain_pct,resistance_ohm,current_measured_mA",
                    "0.1,current,50,1,0.0,100.0,1.0",
                    "0.2,current,50,1,0.1,101.0,2.0",
                ]
            ),
            encoding="utf-8",
        )
    project_path.write_text(
        json.dumps(
            {
                "kind": "microwire_data_builder",
                "version": 1,
                "sections": {
                    "mini_dma": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "Mini DMA graphs": ["stale old run"],
                                "_sources": [str(old_finished)],
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    frame, _origin_artifacts = launcher_module._load_microwire_word_report_frame(  # noqa: SLF001
        project_path,
        argparse.Namespace(microwire_word_sample="Ni50Fe27Ga23 12/2", microwire_word_origin=False),
        tmp_path / "reports",
    )

    assert len(frame) == 1
    row = frame.iloc[0]
    assert not launcher_module._word_project_value_items(row.get("_word_mini_dma_sources"))  # noqa: SLF001
    assert not launcher_module._word_project_value_items(row.get("TMA graphs"))  # noqa: SLF001


def test_microwire_word_report_project_uses_shape_memory_payload_sources(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "Praha"
    project_path = data_root / "microwire_project_copy.pydpj"
    project_path.parent.mkdir(parents=True)
    manual_root = data_root / "manual stress-strain"
    first_path = manual_root / "Ni50Fe27Ga23 12_2 0mA.txt"
    second_path = manual_root / "Ni50Fe27Ga23 12_2 50mA fracture.txt"
    manual_root.mkdir(parents=True)
    for path in (first_path, second_path):
        path.write_text("0.1 0.01\n0.2 0.02\n", encoding="utf-8")
    records = [
        ShapeMemoryStressStrainRecord(
            key=("Ni50Fe27Ga23", 12, 2, None),
            sample="Ni50Fe27Ga23 12-2",
            label="0mA - Ni50Fe27Ga23 12_2 0mA",
            path=first_path,
            data=pd.DataFrame(),
        ),
        ShapeMemoryStressStrainRecord(
            key=("Ni50Fe27Ga23", 12, 2, None),
            sample="Ni50Fe27Ga23 12-2",
            label="50mA fracture - Ni50Fe27Ga23 12_2 50mA fracture",
            path=second_path,
            data=pd.DataFrame(),
        ),
    ]
    encoded_records = safe_codec.encode_envelope(records)
    project_path.write_text(
        json.dumps(
            {
                "kind": "microwire_data_builder",
                "version": 2,
                "sections": {
                    "shape_memory_stress_strain": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "Manual stress/strain graphs": "0mA - Ni50Fe27Ga23 12_2 0mA",
                                "_sources": [str(first_path)],
                            }
                        ],
                        "payloads": {
                            "shape_memory_stress_strain_records": encoded_records,
                        },
                    },
                    "assemble": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "Manual stress/strain graphs": [
                                    "0mA - Ni50Fe27Ga23 12_2 0mA",
                                    "50mA fracture - Ni50Fe27Ga23 12_2 50mA fracture",
                                ],
                            }
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        microwire_word_sample="Ni50Fe27Ga23 12/2",
        microwire_word_origin=False,
    )

    frame, origin_artifacts = launcher_module._load_microwire_word_report_frame(  # noqa: SLF001
        project_path,
        args,
        tmp_path / "reports",
    )

    assert origin_artifacts == {}
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["Manual stress/strain graphs"] == [
        "0mA - Ni50Fe27Ga23 12_2 0mA",
        "50mA fracture - Ni50Fe27Ga23 12_2 50mA fracture",
    ]
    assert row["_word_shape_memory_stress_strain_sources"] == [
        str(first_path),
        str(second_path),
    ]


def test_microwire_word_report_project_exports_rvst_through_pyplot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "Praha"
    project_path = data_root / "microwire_project_copy.pydpj"
    project_path.parent.mkdir(parents=True)
    rvt_path = data_root / "RvsT" / "RvsT" / "Ni50Fe27Ga23_12_2.csv"
    rvt_path.parent.mkdir(parents=True)
    rvt_path.write_text(
        "\n".join(
            [
                "iso_time;t_elapsed_s;sp_c;pv_c;resistance_ohm",
                "2026-02-06T08:22:38;0.1;-100;-40.5;43.2903",
                "2026-02-06T08:22:48;10.1;-90;-39.0;43.2882",
            ]
        ),
        encoding="utf-8",
    )
    project_path.write_text(
        json.dumps(
            {
                "kind": "microwire_data_builder",
                "version": 1,
                "sections": {
                    "assemble": {"rows": [], "columns": []},
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                            }
                        ]
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    captured: list[tuple[str, list[Path]]] = []

    def fake_export_pyplot_origin_artifacts_for_paths(**kwargs: object) -> list[object]:
        captured.append(
            (
                str(kwargs["plugin_name"]),
                [Path(path) for path in kwargs["paths"]],  # type: ignore[index]
                str(kwargs.get("plot_mode") or "raw"),
            )
        )
        descriptor = "rvst_residual.oggu" if kwargs.get("plot_mode") == "residual" else "rvst.oggu"
        return [
            argparse.Namespace(
                descriptor=descriptor,
                display_text="R vs T residual from PyPlot" if kwargs.get("plot_mode") == "residual" else "R vs T from PyPlot",
            )
        ]

    monkeypatch.setattr(
        launcher_module,
        "_export_pyplot_origin_artifacts_for_paths",
        fake_export_pyplot_origin_artifacts_for_paths,
    )
    args = argparse.Namespace(
        microwire_word_sample="Ni50Fe27Ga23 12/2",
        microwire_word_origin=True,
    )

    frame, origin_artifacts = launcher_module._load_microwire_word_report_frame(  # noqa: SLF001
        project_path,
        args,
        tmp_path / "reports",
    )

    assert captured == [("R vs T", [rvt_path], "raw"), ("R vs T", [rvt_path], "residual")]
    assert origin_artifacts["rvst.oggu"].display_text == "R vs T from PyPlot"
    assert origin_artifacts["rvst_residual.oggu"].display_text == "R vs T residual from PyPlot"
    assert frame.iloc[0]["R vs T graphs (Origin)"] == "rvst.oggu"
    assert frame.iloc[0]["R vs T residual graphs (Origin)"] == "rvst_residual.oggu"


@pytest.mark.parametrize(
    ("name", "module", "resource_tag"),
    [
        (
            "TMA Logger",
            "data_logging.mini_dma_logger.mini_dma_logger",
            "mini_dma",
        ),
        (
            "Current Annealing Logger",
            "data_logging.current_annealing_logger.current_annealing_logger",
            "current_annealing",
        ),
    ],
)
def test_hardware_experiment_loggers_launch_in_child_process(
    name: str,
    module: str,
    resource_tag: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[object] = []
    monkeypatch.setattr(
        launcher_module,
        "launch_experiment_process",
        lambda spec: launched.append(spec),
    )

    result = launcher_module.LOGGERS[name]()

    assert result is None
    assert launched
    spec = launched[0]
    assert getattr(spec, "display_name") == name
    assert getattr(spec, "module") == module
    assert getattr(spec, "resource_tag") == resource_tag


def test_experiment_process_cli_dispatches_registered_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    class _Module:
        @staticmethod
        def main() -> None:
            called.append("main")

    monkeypatch.setattr(
        launcher_module,
        "import_module",
        lambda module: _Module
        if module == "data_logging.current_annealing_logger.current_annealing_logger"
        else pytest.fail(f"unexpected module import: {module}"),
    )

    args, _qt_args = launcher_module._parse_launcher_args(
        ["--experiment-process", "current_annealing"]
    )

    assert launcher_module._is_experiment_process_requested(args)
    assert launcher_module._run_experiment_process(args) == 0
    assert called == ["main"]


def test_run_microwire_eda_cli_passes_copy_safe_and_findings_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def _fake_generate_report(config: object) -> object:
        captured["config"] = config
        return argparse.Namespace(
            report_path=tmp_path / "report.html",
            workbook_path=tmp_path / "summary.xlsx",
            csv_path=tmp_path / "dataset.csv",
            manifest_path=tmp_path / "manifest.json",
            findings_json_path=tmp_path / "findings.json",
            findings_md_path=tmp_path / "findings.md",
            copied_project_path=tmp_path / "working" / "copy.pydpj",
            findings=[{"headline": "Top signal"}],
        )

    import microwire_eda.core as eda_core

    monkeypatch.setattr(eda_core, "generate_report", _fake_generate_report)

    args = argparse.Namespace(
        microwire_eda=str(tmp_path / "source.pydpj"),
        rows="all",
        out=str(tmp_path / "artifacts"),
        microwire_eda_title="CLI EDA",
        microwire_eda_working_copy_dir=str(tmp_path / "working"),
        microwire_eda_copy_project=True,
        microwire_eda_force_project_rebuild=True,
        microwire_eda_legacy_breakage=False,
        microwire_eda_composition_splits=False,
        microwire_eda_findings=True,
    )

    exit_code = launcher_module._run_microwire_eda_cli(args)  # noqa: SLF001 - internal automation hook

    assert exit_code == 0
    config = captured["config"]
    assert getattr(config, "copy_project") is True
    assert getattr(config, "force_project_rebuild") is True
    assert getattr(config, "working_copy_dir") == tmp_path / "working"
    assert getattr(config, "include_legacy_breakage_analysis") is False
    assert getattr(config, "include_composition_splits") is False
    assert getattr(config, "write_findings") is True
    output = capsys.readouterr().out
    assert "findings_json=" in output
    assert "copied_project=" in output
    assert "finding=Top signal" in output


def test_launcher_pyplot_automation_generates_summary_and_artifacts(tmp_path: Path) -> None:
    _ensure_app()
    source = _write_hysteresis_source(tmp_path / "250C sample.dat")
    screenshot_path = tmp_path / "window.png"
    plot_path = tmp_path / "plot.png"
    summary_path = tmp_path / "summary.json"
    args = argparse.Namespace(
        pyplot_list_plugins=False,
        pyplot_plugin="Hysteresis Loops",
        pyplot_import=[str(source)],
        pyplot_plot=True,
        pyplot_open_graph_format=True,
        pyplot_open_origin=False,
        pyplot_screenshot=str(screenshot_path),
        pyplot_plot_image=str(plot_path),
        pyplot_summary_json=str(summary_path),
        pyplot_show_window=False,
        pyplot_wait_ms=0,
        visual_check=False,
    )

    exit_code = launcher_module._run_pyplot_automation(args, [])  # noqa: SLF001 - internal automation hook

    assert exit_code == 0
    assert screenshot_path.exists()
    assert plot_path.exists()
    assert summary_path.exists()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["plugin"] == "Hysteresis Loops"
    assert summary["tab_count"] >= 1
    assert summary["current_tab_has_axes"] is True
    assert summary["graph_format_visible"] is True
    assert summary["status"] == "ok"
    assert summary["kind"] == "pyplot"
    assert summary["version"] == 1
    assert summary["window_image"] == str(screenshot_path.resolve())
    assert summary["current_plot_image"] == str(plot_path.resolve())


@pytest.mark.parametrize(
    ("recipe_payload", "message_fragment"),
    [
        (None, "file not found"),
        ("{not-json", "not valid JSON"),
        ({"kind": "builder", "version": 1}, "project"),
        ({"kind": "pyplot", "version": 99}, "Only version 1 is supported"),
        ({"kind": "pyplot", "version": 1, "plugin": "Nope"}, "Unknown PyPlot plugin"),
    ],
)
def test_automation_recipe_validation_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    recipe_payload: dict[str, object] | str | None,
    message_fragment: str,
) -> None:
    recipe_path = tmp_path / "recipe.json"
    if isinstance(recipe_payload, dict):
        recipe_path.write_text(json.dumps(recipe_payload), encoding="utf-8")
    elif isinstance(recipe_payload, str):
        recipe_path.write_text(recipe_payload, encoding="utf-8")
    args = argparse.Namespace(automation_recipe=str(recipe_path))

    exit_code = launcher_module._run_automation_recipe(args, [])  # noqa: SLF001 - internal automation hook

    assert exit_code == 2
    assert message_fragment in capsys.readouterr().out


def test_write_json_keeps_existing_valid_json_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "project.pydpj"
    target.write_text(json.dumps({"kind": "old", "version": 1}), encoding="utf-8")
    real_replace = launcher_module.os.replace

    def fail_target_replace(source: object, destination: object) -> None:
        if Path(destination) == target:
            raise OSError("simulated replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(launcher_module.os, "replace", fail_target_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        launcher_module._write_json(  # noqa: SLF001 - exercise atomic writer
            target,
            {"kind": "new", "version": 1},
        )

    assert json.loads(target.read_text(encoding="utf-8")) == {"kind": "old", "version": 1}
    assert not list(tmp_path.glob(".project.pydpj.*.tmp"))


def test_builder_automation_recipe_updates_vsm_temperature_scan_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    scan_path = tmp_path / "202602010101-TSCN-a000-example.txt"
    scan_path.write_text(
        "\n".join(
            [
                "@Samplename: Ni50Fe27Ga23 5-4",
                "@@End of Header.",
                "Time_since_start Applied_Field Signal_X_direction Sample_Temperature_For_Plot_",
                "New Section: Section 0:",
                "0 10000 0.00051 25.0",
                "1 10000 0.00050 26.0",
            ]
        ),
        encoding="utf-8",
    )
    bad_scan_path = tmp_path / "bad-scan.txt"
    bad_scan_path.write_text("not a VSM temperature scan", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "vsm_temperature_scan",
                        "paths": [str(scan_path), str(bad_scan_path)],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    second_exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )
    assert second_exit_code == 0
    assert project_path.read_bytes() != output_project.read_bytes()
    output_payload = project_package.load_project(output_project)
    section_payload = output_payload["sections"]["vsm_temperature_scan"]
    assert section_payload["rows"]
    assert section_payload["payloads"]["vsm_temperature_scan_records"]["encoding"] == safe_codec.CODEC_ENCODING
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "ok"
    assert manifest["copied_project"] == str(output_project.resolve())
    assert manifest["commands"][0]["record_count"] == 1
    assert manifest["commands"][0]["updated_count"] == 1
    assert manifest["commands"][0]["skipped_count"] == 1
    assert manifest["commands"][0]["skipped_sources"] == [str(bad_scan_path)]
    assert '"kind": "builder"' in capsys.readouterr().out


def test_builder_automation_recipe_updates_annealing_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 2,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    annealing_path = tmp_path / "Ni50Fe27Ga23 12_2 s1 1000mA.txt"
    annealing_path.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n", encoding="utf-8")
    bad_path = tmp_path / "bad_annealing.txt"
    bad_path.write_text("not valid annealing data\n", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "annealing",
                        "paths": [str(annealing_path), str(bad_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["annealing"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = project_package.load_project(output_project)
    section_payload = output_payload["sections"]["annealing"]
    assert section_payload["payloads"]["annealing_records"]["encoding"] == safe_codec.CODEC_ENCODING
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["Composition"] == "Ni50Fe27Ga23"
    assert row["Microwire"] == "12/2"
    assert row["_sources"] == [str(annealing_path)]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "annealing"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 1
    assert update_command["skipped_count"] == 1
    assert update_command["skipped_sources"] == [str(bad_path)]
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"


def test_builder_rebuild_assemble_overlays_saved_transition_reviews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    metadata = MeasurementMetadata(
        composition_token="Ni50Fe27Ga23",
        draw_x=10,
        piece_y=4,
        setpoint_mA=80,
        alt_variant=False,
        measurement_id="reviewed-transition",
        file_name="Ni50Fe27Ga23 10_4 80mA.txt",
        relpath="Ni50Fe27Ga23 10_4 80mA.txt",
        timestamp_mtime_utc="2026-06-19T09:00:00+00:00",
    )
    record = MeasurementRecord(
        path=tmp_path / metadata.file_name,
        metadata=metadata,
        dataframe=launcher_module.pd.DataFrame(
            {"I_mA": [1.0, 20.0, 40.0, 80.0], "R_Ohm": [100.0, 98.0, 110.0, 120.0]}
        ),
        sanity_ok=True,
        sanity_error=0.0,
    )
    record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-06-19 09:30",
                "sections": {
                    "annealing": {
                        "section": "annealing",
                        "columns": ["Composition", "Microwire"],
                        "rows": [{"Composition": "Ni50Fe27Ga23", "Microwire": "10/4"}],
                        "extra": {
                            builder_ui.TRANSITION_REVIEW_EXTRA_KEY: {
                                "schema_version": builder_ui.TRANSITION_REVIEW_SCHEMA_VERSION,
                                "records": {
                                    record_id: {
                                        "status": builder_ui.TRANSITION_REVIEW_STATUS_MANUAL_ADJUSTED,
                                        "included": True,
                                        "auto_values_mA": {"As1": 12.0, "Af1": 22.0},
                                        "manual_values_mA": {"As1": 14.0},
                                        "final_values_mA": {"As1": 14.0, "Af1": 22.0},
                                    }
                                },
                            }
                        },
                        "payloads": {
                            "annealing_records": builder_ui._encode_project_payload([record]),  # noqa: SLF001
                        },
                    },
                    "microscope": {
                        "section": "microscope",
                        "columns": [
                            "Composition",
                            "Microwire",
                            builder_ui.MICROSCOPE_D_COLUMN,
                            "_key",
                        ],
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "10/4",
                                builder_ui.MICROSCOPE_D_COLUMN: 20.0,
                                "_key": "Ni50Fe27Ga23|10|4",
                            }
                        ],
                    },
                    "current_density": {
                        "section": "current_density",
                        "columns": [
                            "Composition",
                            "Microwire",
                            builder_ui.MICROSCOPE_D_COLUMN,
                            "As1 (mA)",
                            "Af1 (mA)",
                            "J_As1 (A/mm^2)",
                            "J_Af1 (A/mm^2)",
                            "Notes",
                            "_group_key",
                        ],
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "10/4",
                                builder_ui.MICROSCOPE_D_COLUMN: None,
                                "As1 (mA)": 14.0,
                                "Af1 (mA)": 22.0,
                                "J_As1 (A/mm^2)": None,
                                "J_Af1 (A/mm^2)": None,
                                "Notes": "Missing diameter",
                                "_group_key": "Ni50Fe27Ga23|10|4",
                            }
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    output_project = tmp_path / "out" / "updated.pydpj"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "commands": [
                    {
                        "action": "rebuild_assemble",
                        "sections": ["annealing", "microscope", "current_density"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = project_package.load_project(output_project)
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "10/4"
    assert assemble_row["As1 (mA)"] == pytest.approx(14.0)
    assert assemble_row["Af1 (mA)"] == pytest.approx(22.0)
    assert assemble_row["J_As1 (A/mm^2)"] == pytest.approx(
        (14.0 / 1000.0) / (np.pi * 0.01 * 0.01)
    )
    assert assemble_row["J_Af1 (A/mm^2)"] == pytest.approx(
        (22.0 / 1000.0) / (np.pi * 0.01 * 0.01)
    )


def test_builder_automation_recipe_updates_microscope_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    core_image = tmp_path / "Ni50Fe27Ga23 12_2 core.jpg"
    glass_image = tmp_path / "Ni50Fe27Ga23 12_2 glass.jpg"
    core_image.write_bytes(b"core image")
    glass_image.write_bytes(b"glass image")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "microscope",
                        "paths": [str(core_image), str(glass_image)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["microscope"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = project_package.load_project(output_project)
    section_payload = output_payload["sections"]["microscope"]
    assert section_payload["payloads"]["microscope_index"]["encoding"] == safe_codec.CODEC_ENCODING
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["Composition"] == "Ni50Fe27Ga23"
    assert row["Microwire"] == "12/2"
    assert row["_core_image"] == str(core_image)
    assert row["_glass_image"] == str(glass_image)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "microscope"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 2
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"


def test_lightweight_assemble_rebuild_is_complete_and_preserves_user_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ensure_app()
    previous_assemble = {
        "section": "assemble",
        "title": "Assemble",
        "columns": ["Composition", "Microwire", "Old"],
        "rows": [
            {"Composition": "Ni50Fe27Ga23", "Microwire": "12/2", "Old": 1}
        ],
        "index": [0],
        "selected_columns": ["Composition", "Microwire", "FMR graphs"],
        "column_order": ["Microwire", "Composition", "FMR graphs"],
        "sort_spec": [["Microwire", True]],
        "search_query": "12/2",
        "source_filter": "All sources",
        "export_settings": {"sections": {"fmr": True}},
        "graph_preview": True,
        "show_imported": True,
        "show_oe_samples": False,
        "imported_sources": ["saved.xlsx"],
        "imported_rows": [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "Imported value": 7.5,
            },
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "13/1",
                "Imported value": 8.5,
            },
        ],
    }
    sections: dict[str, object] = {
        "assemble": previous_assemble,
        "vsm_hysteresis": {
            "columns": ["Composition", "Microwire"],
            "rows": [{"Composition": "Ni50Fe27Ga23", "Microwire": "12/2"}],
        },
        "fmr": {
            "columns": ["Composition", "Microwire"],
            "rows": [{"Composition": "Ni50Fe27Ga23", "Microwire": "12/2"}],
        },
        "fabrication": {
            "columns": [
                "Composition",
                "Microwire",
                "Draw",
                "Piece",
                "Length (m)",
                builder_ui.MICROSCOPE_D_COLUMN,
                builder_ui.MICROSCOPE_CAP_D_COLUMN,
                "d/D",
            ],
            "rows": [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    "Draw": 12,
                    "Piece": 2,
                    "Length (m)": 1.25,
                    builder_ui.MICROSCOPE_D_COLUMN: 18.0,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: 30.0,
                    "d/D": 0.6,
                }
            ],
        },
        "shape_memory_stress_strain": {
            "columns": [
                "Composition",
                "Microwire",
                builder_ui.SHAPE_MEMORY_LOAD_COLUMN,
            ],
            "rows": [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    builder_ui.SHAPE_MEMORY_LOAD_COLUMN: 4.2,
                }
            ],
        },
        "strain": {
            "columns": ["Composition", "Microwire", "Legacy strain"],
            "rows": [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    "Legacy strain": 2.3,
                }
            ],
        },
        "videos": {
            "columns": ["Composition", "Microwire"],
            "rows": [{"Composition": "Ni50Fe27Ga23", "Microwire": "12/2"}],
            "extra": {"overrides": {"Ni50Fe27Ga23|12|2": {"Length (m)": 1.2}}},
        },
    }
    captured: dict[str, object] = {}

    def fake_build_database(_config: object, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            dataframe=pd.DataFrame(
                [
                    {
                        "Composition": "Ni50Fe27Ga23",
                        "Microwire": "12/2",
                        "VSM hysteresis graphs": ["loop"],
                        "FMR graphs": ["fmr"],
                    }
                ]
            )
        )

    monkeypatch.setattr(builder_ui, "build_database", fake_build_database)
    result = launcher_module._run_builder_rebuild_assemble_command_lightweight(  # noqa: SLF001
        builder_ui=builder_ui,
        command={"action": "rebuild_assemble", "sections": ["vsm_hysteresis"]},
        command_index=0,
        sections=sections,  # type: ignore[arg-type]
        output_project=tmp_path / "copy.pydpj",
    )

    assert result["requested_sections"] == ["vsm_hysteresis"]
    assert {"fabrication", "fmr", "shape_memory_stress_strain", "strain", "videos", "vsm_hysteresis"}.issubset(
        result["sections"]
    )
    rebuilt = sections["assemble"]
    assert isinstance(rebuilt, dict)
    for key in (
        "selected_columns",
        "column_order",
        "sort_spec",
        "search_query",
        "source_filter",
        "export_settings",
        "graph_preview",
        "show_imported",
        "show_oe_samples",
        "imported_sources",
        "imported_rows",
    ):
        assert rebuilt[key] == previous_assemble[key]
    rows = rebuilt["rows"]
    assert len(rows) == 2
    assert rows[0]["Imported value"] == pytest.approx(7.5)
    assert rows[1]["Microwire"] == "13/1"
    assert rows[1]["Imported value"] == pytest.approx(8.5)
    fabrication_index = captured["fabrication_index"]
    fabrication_piece = fabrication_index.get_piece("Ni50Fe27Ga23", 12, 2)
    assert fabrication_piece["length_m"] == pytest.approx(1.25)
    assert fabrication_piece["d_um"] == pytest.approx(18.0)
    assert fabrication_piece["D_um"] == pytest.approx(30.0)
    assert fabrication_piece["d_over_D"] == pytest.approx(0.6)
    assert captured["shape_memory_entries"]["Ni50Fe27Ga23|12|2"][  # type: ignore[index]
        builder_ui.SHAPE_MEMORY_LOAD_COLUMN
    ] == pytest.approx(4.2)
    assert captured["strain_entries"]["Ni50Fe27Ga23|12|2"]["Legacy strain"] == pytest.approx(2.3)  # type: ignore[index]
    assert captured["video_overrides"] == {
        "Ni50Fe27Ga23|12|2": {"Length (m)": 1.2}
    }


def test_saved_imported_assemble_values_fill_all_duplicate_sample_rows() -> None:
    frame = pd.DataFrame(
        [
            {"Composition": "Ni50Fe27Ga23", "Microwire": "12/2", "Imported": None},
            {"Composition": "Ni50Fe27Ga23", "Microwire": "12/2", "Imported": None},
        ]
    )
    previous = {
        "show_imported": True,
        "imported_rows": [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "Imported": 7.5,
            }
        ],
    }

    updated = launcher_module._overlay_saved_imported_assemble_rows(frame, previous)  # noqa: SLF001

    assert updated["Imported"].tolist() == [7.5, 7.5]


def test_lightweight_assemble_rebuild_uses_visible_records_and_reviewed_microscope_table(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ensure_app()
    visible_path = tmp_path / "visible.csv"
    hidden_path = tmp_path / "hidden.csv"
    visible_record = builder_ui.FmrRecord(
        path=visible_path,
        sample="Ni50Fe27Ga23 12/2",
        data=pd.DataFrame({"field": [0.0], "signal": [1.0]}),
        key=("Ni50Fe27Ga23", 12, 2),
        label="visible",
    )
    hidden_record = builder_ui.FmrRecord(
        path=hidden_path,
        sample="Ni50Fe27Ga23 12/2",
        data=pd.DataFrame({"field": [0.0], "signal": [2.0]}),
        key=("Ni50Fe27Ga23", 12, 2),
        label="hidden",
    )
    sections: dict[str, object] = {
        "fmr": {
            "rows": [{"Composition": "Ni50Fe27Ga23", "Microwire": "12/2"}],
            "extra": {
                "hidden_paths": [str(hidden_path)],
                "payloads": {"fmr_records": "fmr_records"},
            },
            "payloads": {
                "fmr_records": builder_ui._encode_project_payload(  # noqa: SLF001
                    [visible_record, hidden_record]
                )
            },
        },
        "microscope": {
            "columns": [
                "Composition",
                "Microwire",
                "_key",
                builder_ui.MICROSCOPE_D_COLUMN,
                builder_ui.MICROSCOPE_CAP_D_COLUMN,
            ],
            "rows": [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    builder_ui.MICROSCOPE_D_COLUMN: "22,0",
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: "44.0",
                },
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2oe",
                    "_key": "Ni50Fe27Ga23|12|2|oe",
                    builder_ui.MICROSCOPE_D_COLUMN: 8.0,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: 40.0,
                },
            ],
            "extra": {"show_other_ends": False},
        },
    }
    captured: dict[str, object] = {}

    def fake_build_database(_config: object, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            dataframe=pd.DataFrame(
                [{"Composition": "Ni50Fe27Ga23", "Microwire": "12/2"}]
            )
        )

    monkeypatch.setattr(builder_ui, "build_database", fake_build_database)

    launcher_module._run_builder_rebuild_assemble_command_lightweight(  # noqa: SLF001
        builder_ui=builder_ui,
        command={"action": "rebuild_assemble", "sections": ["fmr", "microscope"]},
        command_index=0,
        sections=sections,  # type: ignore[arg-type]
        output_project=tmp_path / "copy.pydpj",
    )

    fmr_records = captured["fmr_records"]
    assert [record.path for record in fmr_records] == [visible_path]  # type: ignore[union-attr]
    microscope_index = captured["microscope_index"]
    assert set(microscope_index) == {("Ni50Fe27Ga23", 12, 2, None)}  # type: ignore[arg-type]
    measurements = microscope_index[("Ni50Fe27Ga23", 12, 2, None)]  # type: ignore[index]
    assert measurements.best_core() == pytest.approx(22.0)
    assert measurements.best_glass() == pytest.approx(44.0)


def test_builder_automation_recipe_updates_vsm_hysteresis_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    hysteresis_path = tmp_path / "Ni50Fe27Ga23 12_2 202507101320-Hys-a140-T-30-00.VSM-Hys-Data"
    hysteresis_path.write_text(
        "\n".join(
            [
                "@Section 0",
                "Column 0: Time since start, Time [s]",
                "Column 1: Applied Field, Applied Field [Oe]",
                "Column 2: Signal parallel with sample, Moment [emu]",
                "@@END Columns",
                "@@End of Header.",
                "@@Data",
                "New Section: Section 0:",
                "0.0 0.0 0.0",
                "1.0 5.0 0.2",
                "2.0 -5.0 -0.2",
                "@@END Data",
            ]
        ),
        encoding="utf-8",
    )
    bad_path = tmp_path / "bad_hysteresis.VSM-Hys-Data"
    bad_path.write_text("not a VSM hysteresis file", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "vsm_hysteresis",
                        "paths": [str(hysteresis_path), str(bad_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["vsm_hysteresis"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = project_package.load_project(output_project)
    section_payload = output_payload["sections"]["vsm_hysteresis"]
    assert section_payload["payloads"]["vsm_hysteresis_records"]["encoding"] == safe_codec.CODEC_ENCODING
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["_sample"] == "Ni50Fe27Ga23 12-2"
    expected_graphs = ["T-30C — 202507101320-Hys-a140-T-30-00"]
    assert row["VSM hysteresis graphs"] == expected_graphs
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "vsm_hysteresis"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 1
    assert update_command["skipped_count"] == 1
    assert update_command["skipped_sources"] == [str(bad_path)]
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"
    assert assemble_row["VSM hysteresis graphs"] == expected_graphs


def test_builder_automation_recipe_updates_dma_iso_stress_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    source_fixture = Path("tests/fixtures/dma_iso_stress/minimal_iso_stress.txt")
    dma_path = tmp_path / "Ni50Fe27Ga23 12_2.txt"
    dma_path.write_text(source_fixture.read_text(encoding="utf-8"), encoding="utf-8")
    bad_path = tmp_path / "bad_dma.txt"
    bad_path.write_text("not valid DMA data\n", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "dma_iso_stress",
                        "paths": [str(dma_path), str(bad_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["dma_iso_stress"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = project_package.load_project(output_project)
    section_payload = output_payload["sections"]["dma_iso_stress"]
    assert section_payload["payloads"]["dma_iso_stress_records"]["encoding"] == safe_codec.CODEC_ENCODING
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["_sample"] == "Ni50Fe27Ga23 12-2"
    expected_graphs = ["Ni50Fe27Ga23 12_2"]
    assert row["DMA iso-stress graphs"] == expected_graphs
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "dma_iso_stress"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 1
    assert update_command["skipped_count"] == 1
    assert update_command["skipped_sources"] == [str(bad_path)]
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"
    assert assemble_row["DMA iso-stress graphs"] == expected_graphs


def test_builder_automation_recipe_updates_fmr_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    fmr_path = tmp_path / "Ni50Fe27Ga23 12_2.csv"
    fmr_path.write_text(
        "\n".join(
            [
                "Sample Name,Ni50Fe27Ga23 12_2",
                "Freq,35.8 GHz",
                "Time,Field,X,Y",
                "s,Oe,V,V",
                "0,-100,1.0,0.1",
                "1,0,0.5,0.2",
                "2,100,0.2,0.3",
            ]
        ),
        encoding="utf-8",
    )
    bad_path = tmp_path / "bad_fmr.csv"
    bad_path.write_text("not valid FMR data\n", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "fmr",
                        "paths": [str(fmr_path), str(bad_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["fmr"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = project_package.load_project(output_project)
    section_payload = output_payload["sections"]["fmr"]
    assert section_payload["payloads"]["fmr_records"]["encoding"] == safe_codec.CODEC_ENCODING
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["_sample"] == "Ni50Fe27Ga23 12-2"
    expected_graphs = ["Ni50Fe27Ga23 12_2"]
    assert row["FMR graphs"] == expected_graphs
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "fmr"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 1
    assert update_command["skipped_count"] == 1
    assert update_command["skipped_sources"] == [str(bad_path)]
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"
    assert assemble_row["FMR graphs"] == expected_graphs


def test_builder_automation_recipe_updates_shape_memory_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    shape_path = tmp_path / "Ni50Fe27Ga23 12_2.txt"
    shape_path.write_text(
        "\n".join(
            [
                "Displacement\tLoad\tStrain\tStress",
                "mm\tg\t%\tMPa",
                "0.00\t0.00\t0.00\t0.00",
                "0.10\t5.00\t0.20\t25.00",
                "0.20\t8.00\t0.40\t50.00",
                "0.15\t4.00\t0.30\t35.00",
            ]
        ),
        encoding="utf-8",
    )
    bad_path = tmp_path / "bad_shape_memory.txt"
    bad_path.write_text("not valid manual stress strain data\n", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "shape_memory_stress_strain",
                        "paths": [str(shape_path), str(bad_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["shape_memory_stress_strain"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = project_package.load_project(output_project)
    section_payload = output_payload["sections"]["shape_memory_stress_strain"]
    assert section_payload["payloads"]["shape_memory_stress_strain_records"]["encoding"] == safe_codec.CODEC_ENCODING
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["_sample"] == "Ni50Fe27Ga23 12-2"
    expected_graphs = ["Ni50Fe27Ga23 12_2"]
    assert row["Manual stress/strain graphs"] == expected_graphs
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "shape_memory_stress_strain"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 1
    assert update_command["skipped_count"] == 1
    assert update_command["skipped_sources"] == [str(bad_path)]
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"
    assert assemble_row["Manual stress/strain graphs"] == expected_graphs


def _write_mini_dma_run(path: Path, *, sample_name: str = "Ni50Fe27Ga23 12_2") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": sample_name,
                "initial_length_mm": 10.0,
                "created_utc": "2026-06-01 09:00:00",
                "session_state": "finished",
                "finished_utc": "2026-06-01 09:10:00",
            }
        ),
        encoding="utf-8",
    )
    rows = [
        "elapsed_s,automation_phase,automation_target_value,plateau_index,strain_pct,stress_mpa,resistance_ohm,current_set_mA,current_measured_mA,position_mm",
        "0,current,50,1,0.00,50,100,1,1,0.000",
        "1,current,50,1,0.05,50,101,10,10,0.005",
        "2,current,50,1,0.10,50,102,20,20,0.010",
        "3,current,100,2,0.15,100,110,1,1,0.015",
        "4,current,100,2,0.25,100,112,10,10,0.025",
        "5,current,100,2,0.35,100,114,20,20,0.035",
    ]
    (path / "measurement.csv").write_text("\n".join(rows), encoding="utf-8")
    return path


def _write_transition_mini_dma_run(
    path: Path,
    *,
    sample_name: str = "Ni50Fe27Ga23 12_2",
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": sample_name,
                "initial_length_mm": 10.0,
                "wire_diameter_mm": 0.0191,
                "created_utc": "2026-06-01 09:00:00",
                "session_state": "finished",
                "finished_utc": "2026-06-01 09:10:00",
            }
        ),
        encoding="utf-8",
    )
    heating_current = np.linspace(1.0, 100.0, 120)
    cooling_current = np.linspace(100.0, 1.0, 120)

    def piecewise(current: np.ndarray, start: float, finish: float) -> np.ndarray:
        before = 4.0 - current * 0.002
        start_value = 4.0 - start * 0.002
        transition = start_value - (current - start) * 0.04
        finish_value = start_value - (finish - start) * 0.04
        after = finish_value - (current - finish) * 0.003
        return np.where(current < start, before, np.where(current <= finish, transition, after))

    current = np.concatenate([heating_current, cooling_current])
    strain = np.concatenate(
        [
            piecewise(heating_current, 30.0, 70.0),
            piecewise(cooling_current, 25.0, 65.0),
        ]
    )
    rows = [
        "elapsed_s,automation_phase,automation_target_value,plateau_index,strain_pct,stress_mpa,resistance_ohm,current_set_mA,current_measured_mA,position_mm"
    ]
    for index, (current_mA, strain_pct) in enumerate(zip(current, strain, strict=True)):
        rows.append(
            ",".join(
                [
                    str(index),
                    "current",
                    "50",
                    "1",
                    f"{strain_pct:.6f}",
                    "50",
                    "100",
                    f"{current_mA:.6f}",
                    f"{current_mA:.6f}",
                    f"{strain_pct / 10.0:.6f}",
                ]
            )
        )
    (path / "measurement.csv").write_text("\n".join(rows), encoding="utf-8")
    return path


def test_builder_automation_recipe_updates_mini_dma_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    run_path = _write_mini_dma_run(tmp_path / "Ni50Fe27Ga23 12_2 test_run01")
    bad_run = tmp_path / "bad-run"
    bad_run.mkdir()
    (bad_run / "measurement.csv").write_text("not,a,mini,dma\n", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "mini_dma",
                        "paths": [str(run_path), str(bad_run)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["mini_dma"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = project_package.load_project(output_project)
    section_payload = output_payload["sections"]["mini_dma"]
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["TMA strain by stress/load"] == [
        "50 MPa: 0.1% @ 20 mA",
        "100 MPa: 0.2% @ 20 mA",
    ]
    assert row["TMA break point"] == ""
    assert section_payload["payloads"]["mini_dma_records"]["encoding"] == safe_codec.CODEC_ENCODING
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    command = manifest["commands"][0]
    assert command["section"] == "mini_dma"
    assert command["candidate_count"] == 1
    assert command["record_count"] == 1
    assert command["updated_count"] == 1
    assert command["skipped_count"] == 0
    assert command["skipped_sources"] == []
    rebuild_command = manifest["commands"][1]
    assert rebuild_command["action"] == "rebuild_assemble"
    assert rebuild_command["status"] == "ok"
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["TMA graphs"] == [run_path.name]
    assert assemble_row["TMA strain by stress/load"] == [
        "50 MPa: 0.1% @ 20 mA",
        "100 MPa: 0.2% @ 20 mA",
    ]


def test_builder_automation_recipe_updates_mini_dma_transition_currents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    run_path = _write_transition_mini_dma_run(tmp_path / "Ni50Fe27Ga23 12_2 test_run02")
    output_project = tmp_path / "out" / "updated.pydpj"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "mini_dma",
                        "paths": [str(run_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["mini_dma"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = project_package.load_project(output_project)
    row = output_payload["sections"]["mini_dma"]["rows"][0]
    assert row["TMA transition currents by stress/load"] == [
        "50 MPa / 1.46 g: As 30 mA, Af 70 mA, Ms 65 mA, Mf 25 mA"
    ]
    assemble_row = output_payload["sections"]["assemble"]["rows"][0]
    assert assemble_row["TMA transition currents by stress/load"] == [
        "50 MPa / 1.46 g: As 30 mA, Af 70 mA, Ms 65 mA, Mf 25 mA"
    ]


def test_builder_automation_recipe_promotes_database_latest_and_archives_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    database_dir = tmp_path / "microwire_database"
    database_dir.mkdir()
    latest_project = database_dir / "microwire_database_latest.pydpj"
    latest_project.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    latest_manifest = database_dir / "update_manifest_latest.json"
    latest_manifest.write_text(
        json.dumps({"kind": "builder", "status": "old"}),
        encoding="utf-8",
    )
    scan_path = tmp_path / "202602010101-TSCN-a000-example.txt"
    scan_path.write_text(
        "\n".join(
            [
                "@Samplename: Ni50Fe27Ga23 5-4",
                "@@End of Header.",
                "Time_since_start Applied_Field Signal_X_direction Sample_Temperature_For_Plot_",
                "New Section: Section 0:",
                "0 10000 0.00051 25.0",
                "1 10000 0.00050 26.0",
            ]
        ),
        encoding="utf-8",
    )
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "database_dir": str(database_dir),
                "timestamp": "2026-05-26_1512",
                "commands": [
                    {
                        "action": "update_section",
                        "section": "vsm_temperature_scan",
                        "paths": [str(scan_path)],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    assert latest_project.exists()
    assert (database_dir / "update_manifest_latest.json").exists()
    archived_project = database_dir / "archive" / "microwire_database_2026-05-26_1512.pydpj"
    archived_manifest = database_dir / "archive" / "update_manifest_2026-05-26_1512.json"
    assert archived_project.exists()
    assert archived_manifest.exists()
    latest_payload = project_package.load_project(latest_project)
    assert latest_payload["sections"]["vsm_temperature_scan"]["rows"]
    latest_manifest_payload = json.loads(
        (database_dir / "update_manifest_latest.json").read_text(encoding="utf-8")
    )
    assert latest_manifest_payload["database"]["latest_project"] == str(latest_project.resolve())
    assert latest_manifest_payload["database"]["archived_project"] == str(archived_project.resolve())


def test_builder_database_promotion_keeps_latest_when_project_copy_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_dir = tmp_path / "microwire_database"
    latest_project = database_dir / "microwire_database_latest.pydpj"
    latest_manifest = database_dir / "update_manifest_latest.json"
    output_project = tmp_path / "working" / "microwire_database_2026-05-26_1512.pydpj"
    manifest_path = tmp_path / "working" / "update_manifest_2026-05-26_1512.json"
    database_paths = {
        "database_dir": database_dir,
        "database_name": "microwire_database",
        "timestamp": "2026-05-26_1512",
        "latest_project": latest_project,
        "latest_manifest": latest_manifest,
        "archive_dir": database_dir / "archive",
        "archive_project": database_dir / "archive" / "microwire_database_2026-05-26_1512.pydpj",
        "archive_manifest": database_dir / "archive" / "update_manifest_2026-05-26_1512.json",
    }
    old_payload = {
        "version": 1,
        "kind": "MicrowireDataBuilder",
        "saved_at": "2026-05-25 10:00",
        "sections": {},
    }
    new_payload = {
        "version": 1,
        "kind": "MicrowireDataBuilder",
        "saved_at": "2026-05-26 15:12",
        "sections": {"vsm_temperature_scan": {"rows": [{"Sample": "Ni50Fe27Ga23 5-4"}]}},
    }
    launcher_module._write_json(latest_project, old_payload)  # noqa: SLF001
    launcher_module._write_json(latest_manifest, {"kind": "builder", "status": "old"})  # noqa: SLF001
    launcher_module._write_json(output_project, new_payload)  # noqa: SLF001
    real_copy_file_atomic = launcher_module._copy_file_atomic  # noqa: SLF001

    def fail_latest_project_copy(source: Path, target: Path) -> None:
        if target == latest_project:
            raise OSError("simulated latest project copy failure")
        real_copy_file_atomic(source, target)

    monkeypatch.setattr(launcher_module, "_copy_file_atomic", fail_latest_project_copy)

    with pytest.raises(OSError, match="simulated latest project copy failure"):
        launcher_module._promote_builder_database_latest(  # noqa: SLF001
            database_paths=database_paths,
            output_project=output_project,
            manifest_path=manifest_path,
            manifest={
                "kind": "builder",
                "version": 1,
                "status": "ok",
                "source_project": str(latest_project),
                "copied_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [],
            },
        )

    assert json.loads(latest_project.read_text(encoding="utf-8")) == old_payload
    assert latest_manifest.exists()
    assert not (database_dir / "archive" / "microwire_database_2026-05-26_1512.pydpj").exists()


def test_builder_automation_recipe_can_exclude_named_subdirectories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    data_root = tmp_path / "mini_dma"
    good_run = _write_mini_dma_run(data_root / "good_run")
    archived_run = _write_mini_dma_run(data_root / "archive" / "old_run", sample_name="Ni50Fe27Ga23 12_3")
    tests_run = _write_mini_dma_run(data_root / "tests" / "fixture_run", sample_name="Ni50Fe27Ga23 12_4")
    cache_run = _write_mini_dma_run(data_root / "cache" / "scratch_run", sample_name="Ni50Fe27Ga23 12_5")
    invalid_run = data_root / "Ni50Fe27Ga23 12_6 notes"
    invalid_run.mkdir(parents=True)
    (invalid_run / "measurement.csv").write_text("not,a,mini,dma\n1,2,3,4\n", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "output_project": str(output_project),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "mini_dma",
                        "paths": [str(data_root)],
                        "exclude_dir_names": ["archive"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    payload = project_package.load_project(output_project)
    rows = payload["sections"]["mini_dma"]["rows"]
    assert len(rows) == 1
    assert str(good_run) in rows[0]["_sources"]
    assert str(archived_run) not in rows[0]["_sources"]
    assert str(tests_run) not in rows[0]["_sources"]
    assert str(cache_run) not in rows[0]["_sources"]
    assert str(invalid_run) not in rows[0]["_sources"]


def test_automation_recipe_rejects_origin_when_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "plugin": "Hysteresis Loops",
                "open_origin": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher_module, "_origin_is_available", lambda: False)
    args = argparse.Namespace(automation_recipe=str(recipe_path))

    exit_code = launcher_module._run_automation_recipe(args, [])  # noqa: SLF001 - internal automation hook

    assert exit_code == 2
    assert "Origin automation is unavailable" in capsys.readouterr().out


def test_automation_recipe_generates_manifest_and_plot_exports(tmp_path: Path) -> None:
    _ensure_app()
    first = _write_hysteresis_source(tmp_path / "250C Sample A.dat")
    second = _write_hysteresis_source(tmp_path / "300C Sample B.dat")
    manifest_path = tmp_path / "artifacts" / "manifest.json"
    window_path = tmp_path / "artifacts" / "window.png"
    current_plot_path = tmp_path / "artifacts" / "current.png"
    plot_dir = tmp_path / "artifacts" / "plots"
    recipe_path = tmp_path / "job.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "plugin": "Hysteresis Loops",
                "imports": [first.name, second.name],
                "generate": True,
                "exports": {
                    "window_image": "artifacts/window.png",
                    "current_plot_image": "artifacts/current.png",
                    "plot_images_dir": "artifacts/plots",
                },
                "manifest_path": "artifacts/manifest.json",
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(automation_recipe=str(recipe_path))

    exit_code = launcher_module._run_automation_recipe(args, [])  # noqa: SLF001 - internal automation hook

    assert exit_code == 0
    assert manifest_path.exists()
    assert window_path.exists()
    assert current_plot_path.exists()
    assert plot_dir.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "ok"
    assert manifest["plugin"] == "Hysteresis Loops"
    assert manifest["kind"] == "pyplot"
    assert manifest["version"] == 1
    assert manifest["window_image"] == str(window_path.resolve())
    assert manifest["current_plot_image"] == str(current_plot_path.resolve())
    assert manifest["imported_paths"] == [str(first.resolve()), str(second.resolve())]
    assert manifest["plot_image_paths"]
    for index, exported in enumerate(manifest["plot_image_paths"], start=1):
        path = Path(exported)
        assert path.exists()
        assert path.name.startswith(f"{index:02d}-")


def test_automation_recipe_can_save_and_reload_pyplot_project(tmp_path: Path) -> None:
    _ensure_app()
    source = _write_hysteresis_source(tmp_path / "250C sample.dat")
    project_path = tmp_path / "artifacts" / "saved_project.pypj"
    save_manifest_path = tmp_path / "artifacts" / "save_manifest.json"
    load_manifest_path = tmp_path / "artifacts" / "load_manifest.json"

    save_recipe = tmp_path / "save_job.json"
    save_recipe.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "plugin": "Hysteresis Loops",
                "imports": [source.name],
                "generate": True,
                "save_project": "artifacts/saved_project.pypj",
                "manifest_path": "artifacts/save_manifest.json",
            }
        ),
        encoding="utf-8",
    )
    save_args = argparse.Namespace(automation_recipe=str(save_recipe))

    save_exit_code = launcher_module._run_automation_recipe(save_args, [])  # noqa: SLF001 - internal automation hook

    assert save_exit_code == 0
    assert project_path.exists()

    load_recipe = tmp_path / "load_job.json"
    load_recipe.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "load_project": "artifacts/saved_project.pypj",
                "manifest_path": "artifacts/load_manifest.json",
            }
        ),
        encoding="utf-8",
    )
    load_args = argparse.Namespace(automation_recipe=str(load_recipe))

    load_exit_code = launcher_module._run_automation_recipe(load_args, [])  # noqa: SLF001 - internal automation hook

    assert load_exit_code == 0
    save_manifest = json.loads(save_manifest_path.read_text(encoding="utf-8"))
    load_manifest = json.loads(load_manifest_path.read_text(encoding="utf-8"))
    assert save_manifest["saved_project"] == str(project_path.resolve())
    assert load_manifest["loaded_project"] == str(project_path.resolve())
    assert load_manifest["plugin"] == "Hysteresis Loops"
    assert load_manifest["tab_count"] >= 1
    assert load_manifest["workbook_count"] >= 1


def test_automation_recipe_can_build_graphs_and_layout_figure(tmp_path: Path) -> None:
    _ensure_app()
    csv_path = tmp_path / "builder.csv"
    csv_path.write_text(
        "\n".join(
            [
                "field,flux_a,flux_b,flux_c,flux_d",
                "0,1.0,3.5,0.8,2.6",
                "1,2.0,2.7,1.4,2.3",
                "2,3.2,1.9,1.8,1.7",
                "3,4.0,1.2,2.1,1.0",
            ]
        ),
        encoding="utf-8",
    )
    recipe_path = tmp_path / "layout_job.json"
    manifest_path = tmp_path / "layout_manifest.json"
    plot_dir = tmp_path / "plots"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "imports": [csv_path.name],
                "build_graphs": [
                    {
                        "title": "Graph 1",
                        "series": [
                            {"workbook": "builder.csv", "worksheet": "builder", "x_column": "field", "y_column": "flux_a", "label": "A"},
                            {"workbook": "builder.csv", "worksheet": "builder", "x_column": "field", "y_column": "flux_b", "label": "B"},
                        ],
                    },
                    {
                        "title": "Graph 2",
                        "series": [
                            {"workbook": "builder.csv", "worksheet": "builder", "x_column": "field", "y_column": "flux_c", "label": "C"},
                            {"workbook": "builder.csv", "worksheet": "builder", "x_column": "field", "y_column": "flux_d", "label": "D"},
                        ],
                    },
                ],
                "create_figures": [
                    {
                        "title": "Two Panel Figure",
                        "rows": 2,
                        "cols": 1,
                        "share_x": True,
                        "share_y": True,
                        "panel_labels": "lower",
                        "source_titles": ["Graph 1", "Graph 2"],
                    }
                ],
                "exports": {
                    "plot_images_dir": "plots"
                },
                "manifest_path": "layout_manifest.json",
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001 - internal automation hook
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["tab_count"] >= 3
    assert "Two Panel Figure" in manifest["tab_labels"]
    assert plot_dir.exists()


def test_automation_recipe_can_export_all_figures_batch(tmp_path: Path) -> None:
    _ensure_app()
    csv_path = tmp_path / "batch.csv"
    csv_path.write_text(
        "\n".join(
            [
                "field,flux_a,flux_b",
                "0,1.0,3.5",
                "1,2.0,2.7",
                "2,3.2,1.9",
                "3,4.0,1.2",
            ]
        ),
        encoding="utf-8",
    )
    recipe_path = tmp_path / "batch_job.json"
    manifest_path = tmp_path / "batch_manifest.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "imports": [csv_path.name],
                "build_graphs": [
                    {
                        "title": "Batch Graph",
                        "series": [
                            {"workbook": "batch.csv", "worksheet": "batch", "x_column": "field", "y_column": "flux_a", "label": "A"},
                            {"workbook": "batch.csv", "worksheet": "batch", "x_column": "field", "y_column": "flux_b", "label": "B"},
                        ],
                    }
                ],
                "exports": {
                    "all_figures": {
                        "dir": "exports",
                        "format": "png",
                        "dpi": 200
                    }
                },
                "manifest_path": "batch_manifest.json",
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["all_figure_export_paths"]
    for exported in manifest["all_figure_export_paths"]:
        assert Path(exported).exists()


def test_automation_recipe_can_capture_review_screenshots(tmp_path: Path) -> None:
    _ensure_app()
    csv_path = tmp_path / "review.csv"
    csv_path.write_text(
        "\n".join(
            [
                "field,flux_a",
                "0,1.0",
                "1,2.0",
                "2,3.2",
                "3,4.0",
            ]
        ),
        encoding="utf-8",
    )
    recipe_path = tmp_path / "review_job.json"
    manifest_path = tmp_path / "review_manifest.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "imports": [csv_path.name],
                "build_graphs": [
                    {
                        "title": "Review Graph",
                        "series": [
                            {"workbook": "review.csv", "worksheet": "review", "x_column": "field", "y_column": "flux_a", "label": "A"},
                        ],
                    }
                ],
                "exports": {
                    "review_screenshots": {
                        "dir": "review_artifacts",
                        "dark_gui": True
                    }
                },
                "manifest_path": "review_manifest.json",
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["review_paths"]
    for exported in manifest["review_paths"]:
        assert Path(exported).exists()


def test_live_pyplot_session_can_plot_capture_and_close(tmp_path: Path) -> None:
    source = _write_hysteresis_source(tmp_path / "250C session.dat")
    info_path = tmp_path / "session-info.json"
    launcher_path = Path(launcher_module.__file__).resolve()
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONUNBUFFERED"] = "1"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [
            sys.executable,
            str(launcher_path),
            "--pyplot-session-start",
            "--pyplot-plugin",
            "Hysteresis Loops",
            "--pyplot-session-info-file",
            str(info_path),
        ],
        cwd=str(launcher_path.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    try:
        info: dict[str, object] | None = None
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if info_path.exists():
                try:
                    info = json.loads(info_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    info = None
                if isinstance(info, dict) and info.get("session_id"):
                    break
            if process.poll() is not None:
                break
            time.sleep(0.2)
        assert process.poll() is None, process.stderr.read() if process.stderr is not None else ""
        assert isinstance(info, dict)
        session_id = str(info["session_id"])

        import_response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
            session_id,
            {
                "action": "import_paths",
                "paths": [str(source)],
            },
        )
        assert import_response["status"] == "ok"

        generate_response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
            session_id,
            {
                "action": "generate",
            },
        )
        assert generate_response["status"] == "ok"
        assert generate_response["state"]["plugin"] == "Hysteresis Loops"
        assert generate_response["state"]["tab_count"] >= 1

        plot_path = tmp_path / "live-session-plot.png"
        capture_response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
            session_id,
            {
                "action": "capture_current_plot",
                "path": str(plot_path),
            },
        )
        assert capture_response["status"] == "ok"
        assert plot_path.exists()

        state_response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
            session_id,
            {
                "action": "state",
            },
        )
        assert state_response["status"] == "ok"
        assert state_response["result"]["tab_count"] >= 1

        close_response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
            session_id,
            {
                "action": "close",
            },
        )
        assert close_response["status"] == "ok"
        assert close_response["closing"] is True
        process.wait(timeout=20)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def test_review_capture_collapses_extra_tabs_and_restores_visibility(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window.resize(1500, 960)
        window.show()
        app.processEvents()

        def _add_plot(title: str, offset: float) -> QtWidgets.QWidget:
            fig = Figure(figsize=(4.8, 3.2))
            axes = fig.add_subplot(111)
            axes.plot([0.0, 1.0, 2.0], [offset, offset + 0.8, offset + 1.6], label=title)
            axes.set_title(title)
            axes.set_xlabel("X")
            axes.set_ylabel("Y")
            canvas = FigureCanvas(fig)
            tab = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(canvas)
            descriptor = TabDescriptor(
                kind="unit_test",
                title=title,
                root_label=title,
                x_label="X",
                y_label="Y",
                canvas=canvas,
                axes=axes,
                lines={},
                metadata={"plugin": "Unit Test Plugin"},
            )
            index = window.tab_widget.addTab(tab, title)
            window.tab_widget.setCurrentIndex(index)
            window._register_plot_tab(tab, canvas, axes, descriptor)  # noqa: SLF001
            return tab

        first = _add_plot("First Graph", 0.0)
        second = _add_plot("Second Graph", 1.0)
        window.tab_widget.setCurrentWidget(second)
        app.processEvents()

        visibility_before = [
            bool(window.tab_widget.isTabVisible(index))
            for index in range(window.tab_widget.count())
        ]
        review_paths = launcher_module._capture_review_screenshots(  # noqa: SLF001
            window,
            app,
            tmp_path,
        )

        assert review_paths
        assert (tmp_path / "pyplot-gui.png").exists()
        assert (tmp_path / "current-figure.png").exists()
        assert window.tab_widget.currentWidget() is second
        visibility_after = [
            bool(window.tab_widget.isTabVisible(index))
            for index in range(window.tab_widget.count())
        ]
        assert visibility_after == visibility_before
        assert first is not None
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_forced_dark_theme_applies_dark_palette() -> None:
    app = _ensure_app()
    manager = theme_manager()
    previous = manager.current_mode()
    try:
        manager.set_mode("dark")
        app.processEvents()
        color = app.palette().color(app.palette().ColorRole.Window)
        assert color.lightness() < 100
    finally:
        manager.set_mode(previous)
        app.processEvents()
