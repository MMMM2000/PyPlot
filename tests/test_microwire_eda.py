from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from PyQt6 import QtWidgets

from microwire_eda import ui as eda_ui
from microwire_eda.core import (
    ROW_SCOPE_FILTERED,
    MicrowireEdaConfig,
    apply_row_scope,
    canonicalise_frame,
    detect_input_kind,
    generate_report,
    load_analysis_frame,
    load_input_frame,
    MicrowireEdaResult,
)
from microwire_data_builder.ui import BuilderWindow


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def _sample_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Composition": ["Ni50Fe27Ga23"] * 10,
            "Microwire": [f"{idx + 1}/1" for idx in range(10)],
            "d (µm)": [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
            "D (µm)": [40, 42, 44, 46, 48, 50, 52, 54, 56, 58],
            "d/D": [0.25, 0.262, 0.273, 0.283, 0.292, 0.30, 0.308, 0.315, 0.321, 0.328],
            "Core temperature (°C)": [1000, 1002, 1005, 1008, 1010, 1012, 1015, 1018, 1020, 1022],
            "Glass temperature (°C)": [400, 401, 402, 403, 404, 405, 406, 407, 408, 409],
            "Winding speed (m/min)": [30, 32, 34, 36, 38, 40, 42, 44, 46, 48],
            "Glass feeding (mm/min)": [2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9],
            "Underpressure": [40, 41, 42, 43, 44, 45, 46, 47, 48, 49],
            "Length (m)": [5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
            "Mass (g)": [20, 21, 22, 23, 24, 25, 26, 27, 28, 29],
            "e/a": [7.7, 7.71, 7.72, 7.73, 7.74, 7.75, 7.76, 7.77, 7.78, 7.79],
            "As current density (A/mm^2)": [200, 210, 220, 230, 240, 250, 260, 270, 280, 290],
            "Ms current density (A/mm^2)": [180, 190, 200, 210, 220, 230, 240, 250, 260, 270],
            "Low mA value (mA)": [50, 55, 60, 65, 70, 75, 80, 85, 90, 95],
            "As1 (mA)": [90, 92, 94, 96, 98, 100, 102, 104, 106, 108],
            "Af1 (mA)": [95, 97, 99, 101, 103, 105, 107, 109, 111, 113],
            "Ms1 (mA)": [85, 87, 89, 91, 93, 95, 97, 99, 101, 103],
            "Mf1 (mA)": [80, 82, 84, 86, 88, 90, 92, 94, 96, 98],
            "Strain (%)": [6.0, 7.0, 8.0, 9.0, 10.0, None, None, None, None, None],
            "Fracture strain (%)": [None, None, None, None, None, 3.0, 3.5, 4.0, 4.5, 5.0],
            "Stress (MPa)": [120, 130, 140, 150, 160, 170, 180, 190, 200, 210],
            "Production datetime": pd.date_range("2025-01-01", periods=10, freq="D"),
        }
    )


def _project_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "kind": "MicrowireDataBuilder",
        "sections": {
            "assemble": {
                "rows": rows,
            }
        },
    }


def test_detect_input_kind_and_project_loading(tmp_path: Path) -> None:
    project_path = tmp_path / "example.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "kind": "MicrowireDataBuilder",
                "sections": {
                    "assemble": {
                        "rows": [
                            {"Composition": "Ni50Fe27Ga23", "Microwire": "5/4", "d (µm)": 12.0},
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert detect_input_kind(project_path) == "project"
    frame, kind = load_input_frame(MicrowireEdaConfig(input_path=project_path))
    assert kind == "project"
    assert frame.iloc[0]["Microwire"] == "5/4"


def test_canonicalise_frame_maps_aliases_and_derives_outcomes() -> None:
    frame = pd.DataFrame(
        {
            "Composition": ["Ni50Fe27Ga23", "Ni50Fe27Ga23"],
            "Microwire": ["5/4", "6/4"],
            "d (痠)": [12.0, 13.0],
            "D (µm)": [40.0, 42.0],
            "Temperature (°C)": [1010.0, 1015.0],
            "Strain": [-5.0, None],
            "Fracture strain (%)": [None, 3.5],
        }
    )

    clean = canonicalise_frame(frame)

    assert "d (µm)" in clean.columns
    assert clean.loc[0, "Core temperature (°C)"] == pytest.approx(1010.0)
    assert clean.loc[0, "strain_abs"] == pytest.approx(5.0)
    assert clean.loc[0, "is_broken"] == 0
    assert clean.loc[1, "fracture_strain_abs"] == pytest.approx(3.5)
    assert clean.loc[1, "is_broken"] == 1


def test_canonicalise_frame_merges_duplicate_canonical_alias_columns() -> None:
    frame = pd.DataFrame(
        [
            ["Ni50Fe27Ga23", "5/4", None, 12.0],
            ["Ni50Fe27Ga23", "6/4", 13.0, None],
        ],
        columns=["Composition", "Microwire", "d (痠)", "d (µm)"],
    )

    clean = canonicalise_frame(frame)

    assert "d (µm) (2)" not in clean.columns
    assert clean["d (µm)"].tolist() == [12.0, 13.0]


def test_canonicalise_frame_preserves_outer_diameter_aliases() -> None:
    frame = pd.DataFrame(
        [
            ["Ni50Fe27Ga23", "5/4", 12.0, 40.0],
            ["Ni50Fe27Ga23", "6/4", 13.0, 42.0],
        ],
        columns=["Composition", "Microwire", "d (痠)", "D (μm)"],
    )

    clean = canonicalise_frame(frame)

    assert clean["d (µm)"].tolist() == [12.0, 13.0]
    assert clean["D (µm)"].tolist() == [40.0, 42.0]


def test_generate_report_honors_filtered_scope_and_writes_outputs(tmp_path: Path) -> None:
    frame = _sample_dataframe()
    config = MicrowireEdaConfig(
        source_dataframe=frame,
        row_scope=ROW_SCOPE_FILTERED,
        filtered_row_indices=(0, 1, 2, 3, 4, 5, 6, 7),
        output_dir=tmp_path / "report",
        report_title="Unit Test EDA",
        export_png_bundle=True,
        export_pdf_bundle=False,
    )

    scoped, applied = apply_row_scope(frame, config)
    assert applied == ROW_SCOPE_FILTERED
    assert len(scoped.index) == 8

    result = generate_report(config)

    assert result.report_path.exists()
    assert result.workbook_path.exists()
    assert result.csv_path.exists()
    assert result.manifest_path.exists()
    assert result.row_counts["all_rows"] == 8
    assert result.row_counts["known_outcome"] == 8
    assert result.figure_paths


def test_generate_report_skips_png_bundle_when_disabled(tmp_path: Path) -> None:
    frame = _sample_dataframe()
    config = MicrowireEdaConfig(
        source_dataframe=frame,
        output_dir=tmp_path / "report",
        report_title="Unit Test EDA No PNG",
        export_png_bundle=False,
        export_pdf_bundle=False,
    )

    result = generate_report(config)

    assert result.report_path.exists()
    assert result.workbook_path.exists()
    assert result.csv_path.exists()
    assert result.figure_paths == []
    figures_dir = result.output_dir / "figures"
    assert not figures_dir.exists() or not any(figures_dir.glob("*.png"))


def test_generate_report_modern_endpoints_work_without_breakage_labels(tmp_path: Path) -> None:
    frame = _sample_dataframe().drop(columns=["Fracture stress (MPa)"], errors="ignore")
    config = MicrowireEdaConfig(
        source_dataframe=frame.drop(columns=["is_broken"], errors="ignore"),
        output_dir=tmp_path / "report",
        report_title="Endpoint First",
        export_png_bundle=False,
        export_pdf_bundle=False,
        include_legacy_breakage_analysis=True,
    )

    result = generate_report(config)

    assert result.report_path.exists()
    assert result.row_counts["numeric_strain"] == 5
    assert result.row_counts["known_outcome"] == 10
    assert result.tables["process_strain_correlations"].empty is False
    assert result.findings
    assert result.tables["endpoint_coverage"].empty is False


def test_generate_report_writes_findings_and_uses_project_copy(tmp_path: Path) -> None:
    project_path = tmp_path / "source_project.pydpj"
    source_rows = _sample_dataframe().head(6).copy()
    source_rows["Production datetime"] = source_rows["Production datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    payload = _project_payload(source_rows.to_dict(orient="records"))
    original_text = json.dumps(payload, ensure_ascii=False)
    project_path.write_text(original_text, encoding="utf-8")

    config = MicrowireEdaConfig(
        input_path=project_path,
        output_dir=tmp_path / "report",
        report_title="Copy Safe",
        working_copy_dir=tmp_path / "working",
        copy_project=True,
        export_png_bundle=False,
        export_pdf_bundle=False,
    )

    result = generate_report(config)

    assert result.copied_project_path is not None
    assert result.copied_project_path.exists()
    assert result.copied_project_path != project_path
    assert project_path.read_text(encoding="utf-8") == original_text
    assert result.findings_json_path is not None and result.findings_json_path.exists()
    assert result.findings_md_path is not None and result.findings_md_path.exists()

    findings_payload = json.loads(result.findings_json_path.read_text(encoding="utf-8"))
    assert findings_payload["kind"] == "MicrowireEDAFindings"
    assert findings_payload["copied_project_path"] == str(result.copied_project_path)
    assert findings_payload["findings"]
    assert "## Findings" in result.findings_md_path.read_text(encoding="utf-8")


def test_load_analysis_frame_rebuilds_project_when_assemble_rows_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "missing_assemble.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "kind": "MicrowireDataBuilder",
                "sections": {
                    "assemble": {
                        "rows": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    rebuilt = _sample_dataframe().head(4).copy()

    monkeypatch.setattr(
        "microwire_eda.core._rebuild_project_frame_via_builder",
        lambda path, progress_callback=None: rebuilt,
    )

    frame, kind, working_path, copied_project_path, used_rebuild = load_analysis_frame(
        MicrowireEdaConfig(
            input_path=project_path,
            output_dir=tmp_path / "report",
            working_copy_dir=tmp_path / "working",
            copy_project=True,
        )
    )

    assert kind == "project"
    assert used_rebuild is True
    assert copied_project_path is not None
    assert working_path == copied_project_path
    assert frame.equals(rebuilt)


def test_generate_report_disables_composition_outputs_when_requested(tmp_path: Path) -> None:
    frame = _sample_dataframe()
    config = MicrowireEdaConfig(
        source_dataframe=frame,
        output_dir=tmp_path / "report",
        report_title="No Cohorts",
        export_png_bundle=False,
        export_pdf_bundle=False,
        include_composition_splits=False,
    )

    result = generate_report(config)

    assert result.tables["composition_summary"].empty
    assert result.tables["per_composition_process_strain_signals"].empty
    assert "cohorts" in result.skipped_sections


def test_builder_launch_passes_filtered_assemble_rows(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ensure_app()
    window = BuilderWindow()
    qtbot.addWidget(window)
    try:
        frame = _sample_dataframe().iloc[[4, 2, 0]].reset_index(drop=True)
        window._project_path = tmp_path / "example.pydpj"
        window.assembly_section._raw_preview_frame = frame.copy()
        window.assembly_section._preview_row_index_map = [2, 0]
        captured: dict[str, Any] = {}

        def _capture(config: object, logger: object) -> None:
            captured["config"] = config

        monkeypatch.setattr("microwire_data_builder.ui._open_microwire_eda_window", _capture)

        window._analyze_assemble_data()
        qtbot.wait(10)

        config = captured["config"]
        assert config.row_scope == ROW_SCOPE_FILTERED
        assert tuple(config.filtered_row_indices) == (0, 1)
        assert list(config.source_dataframe["Microwire"]) == ["1/1", "5/1"]
    finally:
        window.close()


def test_eda_window_shows_progress_dialog(qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report_path = tmp_path / "report.html"
    workbook_path = tmp_path / "summary.xlsx"
    csv_path = tmp_path / "dataset.csv"
    manifest_path = tmp_path / "manifest.json"
    for path in (report_path, workbook_path, csv_path, manifest_path):
        path.write_text("", encoding="utf-8")

    fake_result = MicrowireEdaResult(
        config=MicrowireEdaConfig(),
        input_kind="dataframe",
        output_dir=tmp_path,
        report_path=report_path,
        workbook_path=workbook_path,
        csv_path=csv_path,
        manifest_path=manifest_path,
        pdf_path=None,
        figure_paths=[],
        skipped_sections={},
        row_counts={"all_rows": 3},
        tables={},
    )

    def _fake_run(self) -> None:
        self.progress.emit("Working...")
        self.finished.emit(fake_result)

    monkeypatch.setattr(eda_ui._EdaWorker, "run", _fake_run)

    window = eda_ui.MicrowireEdaWindow(
        MicrowireEdaConfig(source_dataframe=_sample_dataframe().head(3), filtered_row_indices=(0, 1, 2))
    )
    qtbot.addWidget(window)
    window._run_analysis()
    qtbot.waitUntil(lambda: window._last_result is not None, timeout=5000)

    assert window._progress_dialog is None
    assert "Working..." in window.log_view.toPlainText()
