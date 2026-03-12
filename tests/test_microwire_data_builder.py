"""Tests for the microwire data builder core logic."""

from __future__ import annotations

import json
import inspect
from pathlib import Path
from types import SimpleNamespace

import importlib.util
import logging
import sys

import pandas as pd
import numpy as np
import pytest
from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

from microwire_data_builder import ocr as ocr_module
from microwire_data_builder import ui as builder_ui
from microwire_data_builder.ui import BuilderWindow, MicroscopeSection
from microwire_data_builder.storage import MiniDatabaseData
from plotting.plugins.vsm_temperature_scan.core import VSMTemperatureScanProcessor

CORE_PATH = Path(__file__).resolve().parent.parent / "microwire_data_builder" / "core.py"
_APP_REF: QtWidgets.QApplication | None = None

spec = importlib.util.spec_from_file_location("microwire_data_builder_core", CORE_PATH)
assert spec and spec.loader
core = importlib.util.module_from_spec(spec)
sys.modules["microwire_data_builder_core"] = core
spec.loader.exec_module(core)

BuilderConfig = core.BuilderConfig
build_database = core.build_database
_canonical_dimension_field = core._canonical_dimension_field
_header_key = core._header_key
_load_annealing = core._load_annealing
_metadata_from_path = core._metadata_from_path
_resistance_sanity_check = core._resistance_sanity_check
_safe_plot_stem = core._safe_plot_stem
_split_microwire_key = core._split_microwire_key
_microwire_key_from_string = core._microwire_key_from_string
OriginArtifact = core.OriginArtifact
FabricationIndex = core.FabricationIndex
MeasurementMetadata = core.MeasurementMetadata
MeasurementRecord = core.MeasurementRecord
ShapeMemoryStressStrainRecord = core.ShapeMemoryStressStrainRecord
SHAPE_MEMORY_STRESS_STRAIN_COLUMN = core.SHAPE_MEMORY_STRESS_STRAIN_COLUMN
SHAPE_MEMORY_DISPLACEMENT_COLUMN = core.SHAPE_MEMORY_DISPLACEMENT_COLUMN
SHAPE_MEMORY_LOAD_COLUMN = core.SHAPE_MEMORY_LOAD_COLUMN
SHAPE_MEMORY_STRAIN_COLUMN = core.SHAPE_MEMORY_STRAIN_COLUMN
SHAPE_MEMORY_STRESS_COLUMN = core.SHAPE_MEMORY_STRESS_COLUMN
SHAPE_MEMORY_FRACTURE_LOAD_COLUMN = core.SHAPE_MEMORY_FRACTURE_LOAD_COLUMN
SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN = core.SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN
SHAPE_MEMORY_FRACTURE_STRESS_COLUMN = core.SHAPE_MEMORY_FRACTURE_STRESS_COLUMN
BRITTLE_COLUMN = core.BRITTLE_COLUMN
_merged_header_row = core._merged_header_row
_parse_piece_rows = core._parse_piece_rows
_extract_microscope_diameters = core._extract_microscope_diameters


def _ensure_qapp() -> QtWidgets.QApplication:
    global _APP_REF
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    _APP_REF = app
    return app


def test_vsm_temperature_preview_keeps_dual_axis_legend_in_section_order() -> None:
    processor = VSMTemperatureScanProcessor()
    frame = pd.DataFrame(
        {
            "temperature": [10.0, 20.0, 20.0, 10.0, 10.0, 20.0, 20.0, 10.0],
            "field": [10000.0, 10000.0, 10000.0, 10000.0, 5.0, 5.0, 5.0, 5.0],
            "signal": [1.0, 1.1, 1.2, 1.1, 0.6, 0.7, 0.8, 0.7],
            "section_index": [0, 0, 1, 1, 0, 0, 1, 1],
        }
    )
    record = core.VsmTemperatureScanRecord(
        path=Path("scan.txt"),
        sample="Sample",
        data=frame,
    )

    figure = builder_ui._plot_vsm_temperature_scan_figure(
        record,
        processor,
        width_px=720,
        height_px=360,
    )
    assert figure is not None
    try:
        legend = figure.axes[0].get_legend()
        assert legend is not None
        labels = [text.get_text() for text in legend.get_texts()]
        assert labels == [
            "10000 Oe ↑",
            "10000 Oe ↓",
            "5 Oe ↑",
            "5 Oe ↓",
        ]
    finally:
        builder_ui.plt.close(figure)


def test_render_measurement_pixmap_uses_readable_default_preview_size() -> None:
    _ensure_qapp()
    record = SimpleNamespace(
        dataframe=pd.DataFrame(
            {
                "I_mA": [0.0, 50.0, 100.0, 50.0],
                "R_ohm": [120.0, 150.0, 180.0, 160.0],
            }
        ),
        metadata=None,
    )
    pixmap = builder_ui._render_measurement_pixmap(record, logging.getLogger("test"))
    assert isinstance(pixmap, QtGui.QPixmap)
    assert not pixmap.isNull()
    assert pixmap.width() >= builder_ui.ANNEALING_GRAPH_WIDTH
    assert pixmap.height() >= builder_ui.ANNEALING_GRAPH_HEIGHT


def test_shape_memory_preview_uses_dual_axis_overlay() -> None:
    record = ShapeMemoryStressStrainRecord(
        path=Path("loop.txt"),
        sample="Ni50Fe27Ga23 5/4",
        data=pd.DataFrame(
            {
                "displacement_mm": [0.0, 0.01, 0.02, 0.01],
                "load_g": [0.0, 0.15, 0.25, 0.05],
                "strain_pct": [0.0, 0.05, 0.10, 0.02],
                "stress_mpa": [0.0, 1.1, 2.0, 0.4],
            }
        ),
    )
    figure = builder_ui._plot_shape_memory_stress_strain_figure(
        record,
        width_px=720,
        height_px=360,
    )
    assert figure is not None
    try:
        assert len(figure.axes) == 3
        assert figure.axes[0].get_xlabel() == "Displacement (mm)"
    finally:
        builder_ui.plt.close(figure)


def test_shape_memory_section_groups_flat_folder_files_by_filename_sample(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    logger = logging.getLogger("test")
    section = builder_ui.ShapeMemoryStressStrainSection(logger, lambda *_args: None)
    try:
        paths = []
        for name in [
            "Ni50Fe27Ga23 5-4 s1 loop.txt",
            "Ni50Fe27Ga23 6-4 s1 loop.txt",
        ]:
            path = tmp_path / name
            path.write_text(
                "\n".join(
                    [
                        "Displacement\tLoad\tStrain\tStress",
                        "mm\tg\t%\tMPa",
                        "0\t0\t0\t0",
                        "0.01\t0.10\t0.05\t0.9",
                        "0.02\t0.20\t0.10\t1.8",
                    ]
                ),
                encoding="utf-8",
            )
            paths.append(path)

        section._active_candidates = paths
        result = section.process(paths)
        section._handle_worker_finished(result)

        frame = section.model.frame()
        assert isinstance(frame, pd.DataFrame)
        assert len(frame.index) == 2
        assert set(frame["Microwire"].tolist()) == {"5/4", "6/4"}
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_point_selection_reads_load_and_stress_axes() -> None:
    frame = pd.DataFrame(
        {
            "displacement_mm": [0.0, 0.01, 0.02],
            "load_g": [0.0, 0.10, 0.20],
            "strain_pct": [0.0, 0.05, 0.10],
            "stress_mpa": [0.0, 0.9, 1.8],
        }
    )

    load_pick = builder_ui._shape_memory_point_selection(
        frame,
        axis_kind="load",
        x_value=0.0102,
        y_value=0.11,
    )
    stress_pick = builder_ui._shape_memory_point_selection(
        frame,
        axis_kind="stress",
        x_value=0.049,
        y_value=0.95,
    )

    assert load_pick is not None
    assert load_pick.index == 1
    assert load_pick.load_g == pytest.approx(0.10)
    assert stress_pick is not None
    assert stress_pick.index == 1
    assert stress_pick.stress_mpa == pytest.approx(0.9)


def test_legacy_strain_formula_uses_reversed_ratio() -> None:
    section = builder_ui.StrainSection.__new__(builder_ui.StrainSection)
    section._strain_mode = builder_ui.StrainSection.STRAIN_MODE_LINEAR
    section._strain_offsets = {
        builder_ui.StrainSection.STRAIN_MODE_LINEAR: 0.0,
        builder_ui.StrainSection.STRAIN_MODE_DUAL_SUPPORT: 0.0,
    }
    section._clamp_span_mm = 0.0

    value = builder_ui.StrainSection._compute_strain_percent(section, 10.0, 12.0)

    assert value == pytest.approx(-16.6666667)


def test_search_filters_mini_database_section_rows(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        paths = []
        for name in [
            "Ni50Fe27Ga23 5-4 s1 loop.txt",
            "Ni50Fe27Ga23 6-4 s1 loop.txt",
        ]:
            path = tmp_path / name
            path.write_text(
                "\n".join(
                    [
                        "Displacement\tLoad\tStrain\tStress",
                        "mm\tg\t%\tMPa",
                        "0\t0\t0\t0",
                        "0.01\t0.10\t0.05\t0.9",
                        "0.02\t0.20\t0.10\t1.8",
                    ]
                ),
                encoding="utf-8",
            )
            paths.append(path)
        section._active_candidates = paths
        result = section.process(paths)
        section._handle_worker_finished(result)

        section.search_edit.setText("6/4")
        assert section.table_view.model().rowCount() == 1
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_section_can_hide_other_ends() -> None:
    _ensure_qapp()
    section = builder_ui.MicroscopeSection(logging.getLogger("test"), lambda *_args: None)
    try:
        frame = pd.DataFrame(
            [
                {"Composition": "Ni46Fe23Ga23Co8", "Microwire": "1/1", "_key": "Ni46Fe23Ga23Co8|1|1"},
                {"Composition": "Ni46Fe23Ga23Co8", "Microwire": "1/1oe", "_key": "Ni46Fe23Ga23Co8|1|1|oe"},
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={"show_other_ends": True}))
        assert section.table_view.model().rowCount() == 2

        section._toggle_other_ends(False)
        assert section.table_view.model().rowCount() == 1
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_preview_panel_double_click_updates_picked_values() -> None:
    _ensure_qapp()
    panel = builder_ui._ShapeMemoryPreviewPanel(logging.getLogger("test"))
    record = ShapeMemoryStressStrainRecord(
        path=Path("loop.txt"),
        sample="Ni50Fe27Ga23 5/4",
        data=pd.DataFrame(
            {
                "displacement_mm": [0.0, 0.01, 0.02],
                "load_g": [0.0, 0.10, 0.20],
                "strain_pct": [0.0, 0.05, 0.10],
                "stress_mpa": [0.0, 0.9, 1.8],
            }
        ),
        label="loop",
    )
    panel.update_selection(record.sample, [record])
    assert panel._tab_widget.count() == 1

    canvas = next(iter(panel._canvas_records))
    event = SimpleNamespace(
        dblclick=True,
        xdata=0.0101,
        ydata=0.11,
        inaxes=canvas.figure.axes[0],
        canvas=canvas,
    )
    panel._handle_click(event)

    assert panel._picked_labels["displacement_mm"].text() == "0.01 mm"
    assert panel._picked_labels["load_g"].text() == "0.1 g"
    assert panel._picked_labels["strain_pct"].text() == "0.05 %"
    assert panel._picked_labels["stress_mpa"].text() == "0.9 MPa"

    panel.clear("done")
    panel.close()


def test_shape_memory_section_double_click_stores_value_columns(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        path = tmp_path / "Ni50Fe27Ga23 5-4 s1 loop.txt"
        path.write_text(
            "\n".join(
                [
                    "Displacement\tLoad\tStrain\tStress",
                    "mm\tg\t%\tMPa",
                    "0\t0\t0\t0",
                    "0.01\t0.10\t0.05\t0.9",
                    "0.02\t0.20\t0.10\t1.8",
                ]
            ),
            encoding="utf-8",
        )
        section._active_candidates = [path]
        result = section.process([path])
        section._handle_worker_finished(result)
        section.table_view.selectRow(0)
        section._apply_picked_selection(
            "standard",
            builder_ui._ShapeMemoryPointSelection(
                index=1,
                displacement_mm=0.01,
                load_g=0.10,
                strain_pct=0.05,
                stress_mpa=0.9,
            )
        )

        frame = section.model.frame()
        assert frame.at[0, SHAPE_MEMORY_DISPLACEMENT_COLUMN] == pytest.approx(0.01)
        assert frame.at[0, SHAPE_MEMORY_LOAD_COLUMN] == pytest.approx(0.10)
        assert frame.at[0, SHAPE_MEMORY_STRAIN_COLUMN] == pytest.approx(0.05)
        assert frame.at[0, SHAPE_MEMORY_STRESS_COLUMN] == pytest.approx(0.9)

        section._apply_picked_selection(
            "fracture",
            builder_ui._ShapeMemoryPointSelection(
                index=1,
                displacement_mm=0.01,
                load_g=0.10,
                strain_pct=0.05,
                stress_mpa=0.9,
            ),
        )
        frame = section.model.frame()
        assert frame.at[0, SHAPE_MEMORY_FRACTURE_LOAD_COLUMN] == pytest.approx(0.10)
        assert frame.at[0, SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN] == pytest.approx(0.05)
        assert frame.at[0, SHAPE_MEMORY_FRACTURE_STRESS_COLUMN] == pytest.approx(0.9)
    finally:
        section._shutdown_background_threads()
        section.close()


def test_build_database_populates_shape_memory_graph_column(tmp_path: Path) -> None:
    anneal_path = tmp_path / "Ni50Fe27Ga23 5-4 1000mA.txt"
    anneal_path.write_text("placeholder", encoding="utf-8")
    metadata = MeasurementMetadata(
        composition_token="Ni50Fe27Ga23",
        draw_x=5,
        piece_y=4,
        setpoint_mA=1000,
        alt_variant=False,
        measurement_id="test-measurement",
        file_name=anneal_path.name,
        relpath=anneal_path.name,
        timestamp_mtime_utc="2026-03-11T00:00:00+00:00",
    )
    measurement = MeasurementRecord(
        path=anneal_path,
        metadata=metadata,
        dataframe=pd.DataFrame(
            {
                "I_A": [0.1, 0.2],
                "V_V": [0.2, 0.4],
                "R_ohm": [2.0, 2.0],
                "I_mA": [100.0, 200.0],
            }
        ),
        sanity_ok=True,
        sanity_error=0.0,
    )
    shape_memory = ShapeMemoryStressStrainRecord(
        path=tmp_path / "Ni50Fe27Ga23 5-4 loop.txt",
        sample="Ni50Fe27Ga23",
        data=pd.DataFrame(
            {
                "displacement_mm": [0.0, 0.01, 0.02],
                "load_g": [0.0, 0.1, 0.2],
                "strain_pct": [0.0, 0.05, 0.10],
                "stress_mpa": [0.0, 1.0, 2.0],
            }
        ),
        key=("Ni50Fe27Ga23", 5, 4, None),
        label="loop",
    )

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[],
            output_dir=tmp_path,
            make_plots=False,
            export_formats=(),
            plot_backends=(),
        ),
        measurement_records=[measurement],
        shape_memory_stress_strain_records=[shape_memory],
        fabrication_index=FabricationIndex(),
        skip_exports=True,
    )

    assert SHAPE_MEMORY_STRESS_STRAIN_COLUMN in result.dataframe.columns
    assert result.dataframe.iloc[0][SHAPE_MEMORY_STRESS_STRAIN_COLUMN] == ["loop"]


def test_build_database_populates_shape_memory_value_columns(tmp_path: Path) -> None:
    anneal_path = tmp_path / "Ni50Fe27Ga23 5-4 1000mA.txt"
    anneal_path.write_text("placeholder", encoding="utf-8")
    metadata = MeasurementMetadata(
        composition_token="Ni50Fe27Ga23",
        draw_x=5,
        piece_y=4,
        setpoint_mA=1000,
        alt_variant=False,
        measurement_id="test-measurement",
        file_name=anneal_path.name,
        relpath=anneal_path.name,
        timestamp_mtime_utc="2026-03-11T00:00:00+00:00",
    )
    measurement = MeasurementRecord(
        path=anneal_path,
        metadata=metadata,
        dataframe=pd.DataFrame(
            {"I_A": [0.1], "V_V": [0.2], "R_ohm": [2.0], "I_mA": [100.0]}
        ),
        sanity_ok=True,
        sanity_error=0.0,
    )

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[],
            output_dir=tmp_path,
            make_plots=False,
            export_formats=(),
            plot_backends=(),
        ),
        measurement_records=[measurement],
        shape_memory_entries={
            "Ni50Fe27Ga23|5|4": {
                SHAPE_MEMORY_DISPLACEMENT_COLUMN: 0.01,
                SHAPE_MEMORY_LOAD_COLUMN: 0.1,
                SHAPE_MEMORY_STRAIN_COLUMN: 0.05,
                SHAPE_MEMORY_STRESS_COLUMN: 0.9,
            }
        },
        fabrication_index=FabricationIndex(),
        skip_exports=True,
    )

    row = result.dataframe.iloc[0]
    assert row[SHAPE_MEMORY_DISPLACEMENT_COLUMN] == pytest.approx(0.01)
    assert row[SHAPE_MEMORY_LOAD_COLUMN] == pytest.approx(0.1)
    assert row[SHAPE_MEMORY_STRAIN_COLUMN] == pytest.approx(0.05)
    assert row[SHAPE_MEMORY_STRESS_COLUMN] == pytest.approx(0.9)
    assert row[SHAPE_MEMORY_FRACTURE_LOAD_COLUMN] in (None, "")


def test_build_database_populates_shape_memory_fracture_columns(tmp_path: Path) -> None:
    anneal_path = tmp_path / "Ni50Fe27Ga23 5-4 1000mA.txt"
    anneal_path.write_text("placeholder", encoding="utf-8")
    metadata = MeasurementMetadata(
        composition_token="Ni50Fe27Ga23",
        draw_x=5,
        piece_y=4,
        setpoint_mA=1000,
        alt_variant=False,
        measurement_id="test-measurement",
        file_name=anneal_path.name,
        relpath=anneal_path.name,
        timestamp_mtime_utc="2026-03-11T00:00:00+00:00",
    )
    measurement = MeasurementRecord(
        path=anneal_path,
        metadata=metadata,
        dataframe=pd.DataFrame(
            {"I_A": [0.1], "V_V": [0.2], "R_ohm": [2.0], "I_mA": [100.0]}
        ),
        sanity_ok=True,
        sanity_error=0.0,
    )

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[],
            output_dir=tmp_path,
            make_plots=False,
            export_formats=(),
            plot_backends=(),
        ),
        measurement_records=[measurement],
        shape_memory_entries={
            "Ni50Fe27Ga23|5|4": {
                SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 1.5,
                SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 8.2,
                SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 320.0,
            }
        },
        fabrication_index=FabricationIndex(),
        skip_exports=True,
    )

    row = result.dataframe.iloc[0]
    assert row[SHAPE_MEMORY_FRACTURE_LOAD_COLUMN] == pytest.approx(1.5)
    assert row[SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN] == pytest.approx(8.2)
    assert row[SHAPE_MEMORY_FRACTURE_STRESS_COLUMN] == pytest.approx(320.0)


def test_preview_export_frame_matches_visible_preview_order_and_values() -> None:
    _ensure_qapp()
    section = builder_ui.AssemblySection.__new__(builder_ui.AssemblySection)
    frame = pd.DataFrame(
        {
            "First": [1],
            "Graphs": [["A", "B"]],
            "Second": ["x"],
        }
    )
    section.preview_model = builder_ui.DataFrameModel(frame)
    section.preview_table = QtWidgets.QTableView()
    section.preview_table.setModel(section.preview_model)
    header = section.preview_table.horizontalHeader()
    header.moveSection(1, 0)

    export_frame = builder_ui.AssemblySection._preview_export_frame(section)

    assert list(export_frame.columns) == ["Graphs", "First", "Second"]
    assert export_frame.iloc[0]["Graphs"] == "A, B"


def test_assembly_exposes_compare_hook() -> None:
    from microwire_data_builder.ui import AssemblySection

    hook = getattr(AssemblySection, "attach_compare_section", None)
    assert callable(hook)


def test_get_paddle_ocr_disabled_on_py313_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.version_info < (3, 13):
        pytest.skip("runtime guard only applies on Python 3.13+")
    ocr_module._create_default_ocr.cache_clear()
    monkeypatch.delenv("MICROWIRE_DISABLE_PADDLE_OCR", raising=False)
    monkeypatch.delenv("MICROWIRE_ENABLE_PADDLE_OCR_UNSAFE", raising=False)
    called = {"value": False}

    def _stub():
        called["value"] = True
        return object()

    monkeypatch.setattr(ocr_module, "_create_default_ocr", _stub)
    monkeypatch.setattr(ocr_module, "_UNSAFE_RUNTIME_WARNED", False, raising=False)
    assert ocr_module.get_paddle_ocr(logging.getLogger("test")) is None
    assert called["value"] is False


def test_paddle_candidate_kwargs_include_ascii_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from microwire_data_builder import ocr

    monkeypatch.setattr(ocr, "_CACHE_ROOT", tmp_path)
    home_dir = tmp_path / "paddleocr_home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ocr, "_PADDLE_HOME", home_dir, raising=False)

    params = [
        inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter("lang", inspect.Parameter.KEYWORD_ONLY, default="en"),
        inspect.Parameter("home_path", inspect.Parameter.KEYWORD_ONLY, default=None),
        inspect.Parameter("use_angle_cls", inspect.Parameter.KEYWORD_ONLY, default=True),
    ]
    signature = inspect.Signature(parameters=params)

    combos = ocr._candidate_kwargs(signature)
    assert combos, "expected candidate kwargs to be generated"

    for combo in combos:
        if "home_path" in combo:
            value = combo["home_path"]
            assert value == str(home_dir)
            value.encode("ascii")


def test_filename_parser_extracts_metadata(tmp_path: Path) -> None:
    path = tmp_path / "Ni50Fe27Ga23 6_4a s2 30mA.txt"
    path.write_text("0.1 0.2 2.0\n")
    metadata = _metadata_from_path(path)
    assert metadata.composition_token == "Ni50Fe27Ga23"
    assert metadata.draw_x == 6
    assert metadata.piece_y == 4
    assert metadata.alt_variant is True
    assert metadata.setpoint_mA == 30
    assert metadata.file_name == path.name
    assert metadata.measurement_id


def test_split_microwire_key_rejects_non_integral_or_boolean_indices() -> None:
    assert _split_microwire_key(("Ni50Fe27Ga23", 3.25, 4, None)) is None
    assert _split_microwire_key(("Ni50Fe27Ga23", True, 4, None)) is None
    assert _split_microwire_key(("Ni50Fe27Ga23", 3, float("nan"), None)) is None
    assert _split_microwire_key(("Ni50Fe27Ga23", "3.0", "4.0", "a")) == (
        "Ni50Fe27Ga23",
        3,
        4,
        "a",
    )


def test_microwire_key_from_string_validates_numeric_indices() -> None:
    assert _microwire_key_from_string("Ni50Fe27Ga23|3.0|4.0|a") == (
        "Ni50Fe27Ga23",
        3,
        4,
        "a",
    )
    assert _microwire_key_from_string("Ni50Fe27Ga23|3.2|4|a") is None
    assert _microwire_key_from_string("Ni50Fe27Ga23|True|4") is None


def test_annealing_loader_and_sanity_check(tmp_path: Path) -> None:
    content = "0.1 0.2 2.0\n0.2 0.4 2.0\n0.3 0.6 2.0\n"
    path = tmp_path / "anneal.txt"
    path.write_text(content)
    df = _load_annealing(path)
    assert list(df.columns) == ["I_A", "V_V", "R_ohm", "I_mA"]
    expected_A = [0.1, 0.2, 0.3]
    assert df["I_A"].tolist() == pytest.approx(expected_A)
    assert df["I_mA"].tolist() == pytest.approx([value * 1_000.0 for value in expected_A])
    ok, error = _resistance_sanity_check(df)
    assert ok is True
    assert error is not None
    assert error < 1e-6


def test_annealing_loader_trims_burnthrough_spike(tmp_path: Path) -> None:
    path = tmp_path / "burn.txt"
    path.write_text("0.05 0.10 2.0\n0.06 0.12 2.0\n0.03 0.50 20.0\n")
    df = _load_annealing(path)
    assert len(df) == 2
    assert df["I_A"].tolist() == pytest.approx([0.05, 0.06])
    assert df["I_mA"].tolist() == pytest.approx([50.0, 60.0])
    assert df["R_ohm"].tolist() == pytest.approx([2.0, 2.0])


def test_header_normaliser_variants() -> None:
    assert _header_key("hmotnosť") == "mass_g"
    assert _header_key("P.Č") == "piece_y"
    assert _header_key("d (µm)") == "d_um"
    assert _header_key("D (µm)") == "D_um"
    assert _header_key("d/D") == "d_over_D"
    assert _header_key("Poznámka") == "notes"


def test_piece_header_backfill_extracts_diameters(tmp_path: Path) -> None:
    df = pd.DataFrame(
        [
            [
                "1.Ni46Fe23Ga23Co8 13.03.2025 09:15",
                None,
                None,
                "odpor",
                "d",
                "D",
                "d/D",
            ],
            ["P.Č", "Dátum", "Dĺžka (m)", None, None, None, None],
            ["1.", "45729", "6.4056", "2.15", "7", "25", "0.28"],
        ],
        dtype=object,
    )

    header_idx = 1
    header_values = _merged_header_row(df, header_idx)
    headers = [_header_key(value) for value in header_values]
    index = FabricationIndex()
    _parse_piece_rows(
        df.iloc[header_idx + 1 :],
        headers,
        "Ni46Fe23Ga23Co8",
        1,
        index,
        logging.getLogger("test"),
        tmp_path / "piece.xlsx",
    )
    record = index.get_piece("Ni46Fe23Ga23Co8", 1, 1)
    assert record["d_um"] == pytest.approx(7.0)
    assert record["D_um"] == pytest.approx(25.0)
    assert record["d_over_D"] == pytest.approx(0.28)
    assert record["fabrication_resistance_ohm"] == pytest.approx(2.15)
    display = record.get("d_um__display")
    assert isinstance(display, list) and "7" in display[0]


def test_fabrication_index_preserves_existing_length_on_blank_override() -> None:
    index = FabricationIndex()
    index.set_piece(
        "Ni50Fe27Ga23",
        2,
        5,
        {"length_m": 12.3456, "length_m_raw": "12.3456"},
    )
    index.set_piece(
        "Ni50Fe27Ga23",
        2,
        5,
        {"length_m": pd.NA, "length_m_raw": pd.NA},
    )
    index.set_piece(
        "Ni50Fe27Ga23",
        2,
        5,
        {"length_m": np.nan, "length_m_raw": ""},
    )

    record = index.get_piece("Ni50Fe27Ga23", 2, 5)
    assert record["length_m"] == pytest.approx(12.3456)
    assert record["length_m_raw"] == "12.3456"


def test_merged_header_row_combines_unit_suffix() -> None:
    df = pd.DataFrame(
        [
            ["Title", "d", "D", None],
            ["P.Č", "(µm)", "(µm)", "d/D"],
        ],
        dtype=object,
    )

    header = _merged_header_row(df, 1)
    normalised = [str(value).replace("μ", "µ") if value is not None else value for value in header]
    assert normalised[1] == "d (µm)"
    assert normalised[2] == "D (µm)"
    assert normalised[3] == "d/D"


def test_canonical_dimension_field_filters_non_diameter_columns() -> None:
    assert _canonical_dimension_field("glass_feed_mm_per_min") is None
    assert _canonical_dimension_field("core_diameter_um") == "d_um"
    assert _canonical_dimension_field("glass_diameter_um_raw") == "D_um"
    assert _canonical_dimension_field("ratio_d_core_to_D_glass") == "d_over_D"


def test_microscope_prepopulate_images(tmp_path: Path) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])

    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    section.reset_to_blank()
    section._expected_keys_current = {("Ni50Fe25Ga25", 3, 4)}

    core_path = tmp_path / "Ni50Fe25Ga25 3_4 core.jpg"
    glass_path = tmp_path / "Ni50Fe25Ga25 3_4 glass.jpg"
    other_path = tmp_path / "Ni50Fe25Ga25 3_4 overview.jpg"
    for path in (core_path, glass_path, other_path):
        path.write_bytes(b"")

    section._prepopulate_image_refs([core_path, glass_path, other_path])

    frame = section.data.table
    assert not frame.empty
    row = frame.iloc[0]
    assert row["_core_image"] == str(core_path)
    assert row["_glass_image"] == str(glass_path)
    images = row["_images"]
    assert isinstance(images, list)
    assert str(core_path) in images
    assert str(glass_path) in images
    assert str(other_path) in images


def test_microscope_prepopulate_keeps_other_end_images_when_not_in_expected_keys(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        section.reset_to_blank()
        section._expected_keys_current = {("Ni46Fe23Ga23Co8", 1, 1, None)}

        core_path = tmp_path / "Ni46Fe23Ga23Co8 1-1oe core.jpg"
        glass_path = tmp_path / "Ni46Fe23Ga23Co8 1-1oe glass.jpg"
        for path in (core_path, glass_path):
            path.write_bytes(b"test")

        section._prepopulate_image_refs([core_path, glass_path])

        row = section._row_for_key("Ni46Fe23Ga23Co8|1|1|oe")
        assert row is not None
        assert row["_core_image"] == str(core_path)
        assert row["_glass_image"] == str(glass_path)
        assert section._row_missing_images(row) is False
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_brittle_glass_only_row_is_not_treated_as_missing(tmp_path: Path) -> None:
    _ensure_qapp()
    glass_path = tmp_path / "TestCompG 1-1 glass brittle.jpg"
    glass_path.write_bytes(b"test")
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "TestCompG",
                    "Microwire": "1/1",
                    builder_ui.MICROSCOPE_D_COLUMN: None,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: 28.2,
                    "d/D": None,
                    BRITTLE_COLUMN: True,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "TestCompG|1|1",
                    "_core_image": None,
                    "_glass_image": str(glass_path),
                    "_images": [str(glass_path)],
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        row = section._row_for_key("TestCompG|1|1")
        assert row is not None
        assert section._row_missing_images(row) is False
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_collect_candidates_keeps_all_files_when_ocr_is_deferred(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        core_path = tmp_path / "Ni46Fe23Ga23Co8 1-1oe core.jpg"
        glass_path = tmp_path / "Ni46Fe23Ga23Co8 1-1oe glass.jpg"
        for path in (core_path, glass_path):
            path.write_bytes(b"test")

        section.set_sources([str(tmp_path)])
        section.data.processed = {
            str(core_path): core_path.stat().st_mtime,
            str(glass_path): glass_path.stat().st_mtime,
        }
        section.defer_ocr_checkbox.setChecked(True)

        candidates = section._collect_candidates()

        assert core_path in candidates
        assert glass_path in candidates
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_section_auto_selects_first_row_and_loads_previews(tmp_path: Path) -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        core_path = tmp_path / "core.png"
        glass_path = tmp_path / "glass.png"
        for path, color in (
            (core_path, QtGui.QColor("#1d4ed8")),
            (glass_path, QtGui.QColor("#16a34a")),
        ):
            image = QtGui.QImage(32, 24, QtGui.QImage.Format.Format_RGB32)
            image.fill(color)
            assert image.save(str(path))

        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni46Fe23Ga23Co8",
                    "Microwire": "1/1",
                    builder_ui.MICROSCOPE_D_COLUMN: 6.7,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: 34.4,
                    "d/D": 0.195,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "Ni46Fe23Ga23Co8|1|1",
                    "_core_image": str(core_path),
                    "_glass_image": str(glass_path),
                    "_images": [str(core_path), str(glass_path)],
                }
            ]
        )

        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        _ensure_qapp().processEvents()

        current = section.table_view.currentIndex()
        assert current.isValid()
        selected = section._selected_row()
        assert selected is not None
        assert selected["_key"] == "Ni46Fe23Ga23Co8|1|1"
        assert bool(getattr(section.core_preview_label, "_pixmap", None))
        assert bool(getattr(section.glass_preview_label, "_pixmap", None))
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_manual_enter_marks_reviewed_and_advances() -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "TestCompA",
                    "Microwire": "1/1oe",
                    builder_ui.MICROSCOPE_D_COLUMN: None,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                    "d/D": None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "TestCompA|1|1|oe",
                    "_core_image": None,
                    "_glass_image": None,
                    "_images": [],
                },
                {
                    "Composition": "TestCompA",
                    "Microwire": "1/2oe",
                    builder_ui.MICROSCOPE_D_COLUMN: None,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                    "d/D": None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "TestCompA|1|2|oe",
                    "_core_image": None,
                    "_glass_image": None,
                    "_images": [],
                },
            ]
        )

        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        section._select_row_for_key("TestCompA|1|1|oe", builder_ui.MICROSCOPE_D_COLUMN)
        _ensure_qapp().processEvents()

        section.d_edit.setText("12.3")
        section._apply_override(builder_ui.MICROSCOPE_D_COLUMN)
        _ensure_qapp().processEvents()
        row = section._selected_row()
        assert row is not None
        assert row["_key"] == "TestCompA|1|1|oe"

        section.D_edit.setText("45.6")
        section._apply_override(builder_ui.MICROSCOPE_CAP_D_COLUMN)
        _ensure_qapp().processEvents()

        row = section._selected_row()
        assert row is not None
        assert row["_key"] == "TestCompA|1|2|oe"
        entry = section._validated["TestCompA|1|1|oe"]
        assert entry["d_reviewed"] is True
        assert entry["D_reviewed"] is True
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_open_sources_enabled_for_current_cell_selection(tmp_path: Path) -> None:
    _ensure_qapp()
    image_path = tmp_path / "TestCompB 1-1oe glass.jpg"
    image_path.write_bytes(b"test")
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "TestCompB",
                    "Microwire": "1/1oe",
                    builder_ui.MICROSCOPE_D_COLUMN: 8.1,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: 32.7,
                    "d/D": 0.248,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "TestCompB|1|1|oe",
                    "_core_image": None,
                    "_glass_image": str(image_path),
                    "_images": [str(image_path)],
                }
            ]
        )

        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        section._select_row_for_key("TestCompB|1|1|oe", builder_ui.MICROSCOPE_CAP_D_COLUMN)
        _ensure_qapp().processEvents()

        assert section.open_sources_button.isEnabled() is True
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_return_key_marks_reviewed_and_advances() -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "TestCompC",
                    "Microwire": "1/1oe",
                    builder_ui.MICROSCOPE_D_COLUMN: 8.1,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: 32.7,
                    "d/D": 0.248,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "TestCompC|1|1|oe",
                    "_core_image": None,
                    "_glass_image": None,
                    "_images": [],
                },
                {
                    "Composition": "TestCompC",
                    "Microwire": "2/3oe",
                    builder_ui.MICROSCOPE_D_COLUMN: None,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                    "d/D": None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "TestCompC|2|3|oe",
                    "_core_image": None,
                    "_glass_image": None,
                    "_images": [],
                },
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        section._select_row_for_key("TestCompC|1|1|oe", builder_ui.MICROSCOPE_CAP_D_COLUMN)
        _ensure_qapp().processEvents()

        section.D_edit.setFocus()
        section.D_edit.selectAll()
        section.D_edit.setText("32.7")
        QtTest.QTest.keyClick(section.D_edit, QtCore.Qt.Key.Key_Return)
        _ensure_qapp().processEvents()

        row = section._selected_row()
        assert row is not None
        assert row["_key"] == "TestCompC|2|3|oe"
        entry = section._validated["TestCompC|1|1|oe"]
        assert entry["D_reviewed"] is True
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_table_cell_return_moves_to_next_unverified_cell() -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "TestCompF",
                    "Microwire": "1/1oe",
                    builder_ui.MICROSCOPE_D_COLUMN: None,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                    "d/D": None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "TestCompF|1|1|oe",
                    "_core_image": None,
                    "_glass_image": None,
                    "_images": [],
                },
                {
                    "Composition": "TestCompF",
                    "Microwire": "1/2oe",
                    builder_ui.MICROSCOPE_D_COLUMN: None,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                    "d/D": None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "TestCompF|1|2|oe",
                    "_core_image": None,
                    "_glass_image": None,
                    "_images": [],
                },
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        section.show()
        _ensure_qapp().processEvents()

        section._select_row_for_key("TestCompF|1|1|oe", builder_ui.MICROSCOPE_D_COLUMN)
        _ensure_qapp().processEvents()
        index = section.table_view.currentIndex()
        section.table_view.edit(index)
        _ensure_qapp().processEvents()
        editor = section.table_view.focusWidget()
        assert isinstance(editor, QtWidgets.QLineEdit)
        editor.selectAll()
        editor.setText("12.3")
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Return)
        _ensure_qapp().processEvents()
        QtTest.QTest.qWait(200)
        _ensure_qapp().processEvents()

        row = section._selected_row()
        assert row is not None
        assert row["_key"] == "TestCompF|1|1|oe"
        current = section.table_view.currentIndex()
        assert current.isValid()
        assert current.column() == list(section.model.frame().columns).index(builder_ui.MICROSCOPE_CAP_D_COLUMN)
        entry = section._validated["TestCompF|1|1|oe"]
        assert entry["d_reviewed"] is True
    finally:
        section._shutdown_background_threads()
        section.close()


def test_fabrication_relevant_map_includes_microscope_only_non_other_end() -> None:
    _ensure_qapp()
    microscope_store = builder_ui.MiniDatabaseStore("microscope")
    microscope_original = microscope_store.load()
    annealing_store = builder_ui.MiniDatabaseStore("annealing")
    annealing_original = annealing_store.load()
    section = builder_ui.FabricationSection(logging.getLogger("test"), lambda *_: None)
    try:
        microscope_frame = pd.DataFrame(
            [
                {"Composition": "TestCompD", "Microwire": "1/1", "_key": "TestCompD|1|1"},
                {"Composition": "TestCompD", "Microwire": "1/1oe", "_key": "TestCompD|1|1|oe"},
            ]
        )
        microscope_store.save(MiniDatabaseData(table=microscope_frame))
        annealing_store.save(MiniDatabaseData(table=pd.DataFrame()))

        relevant_map, relevant_compositions = section._load_relevant_map()

        assert "TestCompD" in relevant_compositions
        assert relevant_map["TestCompD"][1] == {1}
    finally:
        microscope_store.save(microscope_original)
        annealing_store.save(annealing_original)
        section.close()


def test_build_database_populates_brittle_column_from_microscope_index(tmp_path: Path) -> None:
    record = MeasurementRecord(
        path=tmp_path / "Ni50Fe27Ga23 5_4 s1 1000mA.txt",
        metadata=MeasurementMetadata(
            composition_token="Ni50Fe27Ga23",
            draw_x=5,
            piece_y=4,
            setpoint_mA=1000,
            alt_variant=False,
            file_name="Ni50Fe27Ga23 5_4 s1 1000mA.txt",
            measurement_id="m1",
            relpath="Ni50Fe27Ga23 5_4 s1 1000mA.txt",
            timestamp_mtime_utc="2026-03-12T00:00:00+00:00",
        ),
        dataframe=pd.DataFrame({"I_A": [1.0], "V_V": [1.0], "R_ohm": [1.0]}),
        sanity_ok=True,
        sanity_error=None,
    )
    microscope_index = {
        ("Ni50Fe27Ga23", 5, 4, None): core.MicroscopeMeasurements(
            glass=[
                core.MicroscopeDetection(
                    value=28.2,
                    image_path=tmp_path / "Ni50Fe27Ga23 5-4 glass brittle.jpg",
                    source="manual",
                    category="glass",
                )
            ],
            brittle=True,
        )
    }
    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[],
            output_dir=tmp_path / "out",
        ),
        measurement_records=[record],
        microscope_index=microscope_index,
        skip_exports=True,
    )
    assert BRITTLE_COLUMN in result.dataframe.columns
    assert bool(result.dataframe.iloc[0][BRITTLE_COLUMN]) is True


def test_safe_plot_stem_removes_path_separators() -> None:
    stem = _safe_plot_stem("Ni55Fe18Ga27 4/1 s1 1000mA")
    assert "/" not in stem
    assert stem.endswith("1000mA")


def test_build_database_integration(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    if ocr_module.get_paddle_ocr() is None:
        pytest.skip("PaddleOCR is not available in this environment.")
    base = Path("sample_data/database_builder")
    anneal_files = [
        base / "current annealing data" / "Ni55Fe18Ga27 4_1 s1 1000mA.txt",
        base / "current annealing data" / "Ni55Fe18Ga27 4_1 s2 100mA.txt",
    ]
    composition_file = base / "microwire data" / "Ni55Fe18Ga27" / "Ni55Fe18Ga27.xlsx"
    piece_dir = base / "microwire data" / "Ni55Fe18Ga27" / "4.Ni55Fe18Ga27 26112024 0850"
    piece_file = sorted(piece_dir.glob("*.xlsx"))[0]

    config = BuilderConfig(
        fabrication_files=[composition_file, piece_file],
        annealing_files=anneal_files,
        output_dir=tmp_path / "out",
    )

    result = build_database(config)
    df = result.dataframe
    assert len(df) == 1
    row = df.iloc[0]
    expected_columns = list(core.OUTPUT_COLUMNS)
    expected_columns.insert(expected_columns.index("d (µm)") + 1, "d (µm) image")
    expected_columns.insert(expected_columns.index("D (µm)") + 1, "D (µm) image")
    assert list(df.columns) == expected_columns
    assert row["Composition"] == "Ni55Fe18Ga27"
    assert row["Microwire"] == "4/1"
    assert row["File 1000 mA"] == anneal_files[0].name
    assert row["File low mA"] == anneal_files[1].name
    assert pd.isna(row[core.STRAIN_COLUMN])
    assert row["Low mA value (mA)"] == 100
    assert pd.notna(row["d (µm)"])
    assert pd.notna(row["D (µm)"])
    assert row["Production datetime"] == "2024-11-26 08:50:00"
    assert "csv" in result.exports
    assert Path(result.exports["csv"]).exists()


def test_build_database_populates_plot_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    high = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 1_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.1\n0.1 0.2 2.1\n")

    produced: dict[str, Path] = {}

    def fake_plot(df, source: Path, plot_dir: Path, figsize: tuple[float, float]) -> Path:
        plot_dir.mkdir(parents=True, exist_ok=True)
        out_path = plot_dir / f"{source.stem}.png"
        out_path.write_text("stub")
        produced[source.name] = out_path
        return out_path

    monkeypatch.setattr(core, "_plot_measurement_matplotlib", fake_plot)

    config = BuilderConfig(
        fabrication_files=[],
        annealing_files=[high, low],
        output_dir=tmp_path / "out",
        make_plots=True,
    )

    result = build_database(config)
    assert result.plot_paths
    assert not result.origin_artifacts
    row = result.dataframe.iloc[0]
    assert "Figure — 1000 mA" in result.dataframe.columns
    assert "Figure — low mA" in result.dataframe.columns
    assert "Figure — 1000 mA (Origin)" in result.dataframe.columns
    assert "Figure — low mA (Origin)" in result.dataframe.columns
    assert set(result.plot_paths) == {produced[high.name].name, produced[low.name].name}
    assert row["Figure — 1000 mA"] == produced[high.name].name
    assert row["Figure — low mA"] == produced[low.name].name
    assert pd.isna(row["Figure — 1000 mA (Origin)"])
    assert pd.isna(row["Figure — low mA (Origin)"])
    assert row["Low mA value (mA)"] == 120
    assert pd.isna(row[core.STRAIN_COLUMN])


def test_build_database_origin_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    high = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 1_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.1\n0.1 0.2 2.1\n")

    origin_records: dict[str, OriginArtifact] = {}

    def fake_origin(df, source: Path, origin_dir: Path, log: logging.Logger | None) -> OriginArtifact:
        origin_dir.mkdir(parents=True, exist_ok=True)
        descriptor = f"{source.stem}.oggu"
        artifact_path = origin_dir / descriptor
        artifact = OriginArtifact(descriptor=descriptor, object_path=artifact_path)
        origin_records[source.name] = artifact
        return artifact

    monkeypatch.setattr(core, "_plot_measurement_origin", fake_origin)

    config = BuilderConfig(
        fabrication_files=[],
        annealing_files=[high, low],
        output_dir=tmp_path / "out",
        make_plots=True,
        plot_backends=("origin",),
    )

    result = build_database(config)
    assert not result.plot_paths
    assert set(result.origin_artifacts.keys()) == {artifact.descriptor for artifact in origin_records.values()}
    row = result.dataframe.iloc[0]
    assert "Figure — 1000 mA (Origin)" in result.dataframe.columns
    assert "Figure — low mA (Origin)" in result.dataframe.columns
    assert "Figure — 1000 mA" in result.dataframe.columns
    assert "Figure — low mA" in result.dataframe.columns
    assert row["Figure — 1000 mA (Origin)"] == origin_records[high.name].descriptor
    assert row["Figure — low mA (Origin)"] == origin_records[low.name].descriptor
    assert pd.isna(row["Figure — 1000 mA"])
    assert pd.isna(row["Figure — low mA"])
    assert pd.isna(row[core.STRAIN_COLUMN])


def test_build_database_merges_current_density_and_transition_entries(tmp_path: Path) -> None:
    high = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 1_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.1\n0.1 0.2 2.1\n")

    config = BuilderConfig(
        fabrication_files=[],
        annealing_files=[high, low],
        output_dir=tmp_path / "out",
    )
    result = build_database(
        config,
        current_density_entries={
            "Ni55Fe18Ga27|1|1": {
                "As1 (mA)": 110.0,
                "Af1 (mA)": 145.0,
                "Ms1 (mA)": 180.0,
                "Mf1 (mA)": 165.0,
                "As2 (mA)": 120.0,
                "Af2 (mA)": 152.0,
                "Ms2 (mA)": 189.0,
                "Mf2 (mA)": 172.0,
                "As current density (A/mm^2)": 4.5,
                "Ms current density (A/mm^2)": 7.2,
                "As2-As1 (mA)": 10.0,
                "Af2-Af1 (mA)": 7.0,
                "Ms2-Ms1 (mA)": 9.0,
                "Mf2-Mf1 (mA)": 7.0,
                "Mf1-Af1 (mA)": 20.0,
                "Mf2-Af2 (mA)": 20.0,
                "Setpoints (mA)": [1000, 120],
                "Sources": ["Manual", "Import"],
                "Notes": "manual annotations",
            }
        },
        transition_temps={
            "Ni55Fe18Ga27|1|1": {
                "As": 22.5,
                "Af": 35.0,
                "Ms": 18.0,
                "Mf": 8.0,
            }
        },
    )

    row = result.dataframe.iloc[0]
    assert row["As (mA)"] == pytest.approx(110.0)
    assert row["Ms (mA)"] == pytest.approx(180.0)
    assert row["As1 (mA)"] == pytest.approx(110.0)
    assert row["Af2 (mA)"] == pytest.approx(152.0)
    assert row["Ms2-Ms1 (mA)"] == pytest.approx(9.0)
    assert row["Mf1-Af1 (mA)"] == pytest.approx(20.0)
    assert row["Setpoints (mA)"] == "1000, 120"
    assert row["Sources"] == "Manual, Import"
    assert row["Notes"] == "manual annotations"
    assert row["As (°C)"] == pytest.approx(22.5)
    assert row["Af (°C)"] == pytest.approx(35.0)
    assert row["Ms (°C)"] == pytest.approx(18.0)
    assert row["Mf (°C)"] == pytest.approx(8.0)


def test_excel_export_embeds_plot_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("openpyxl")
    from PIL import Image as PILImage

    high = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 1_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.1\n0.1 0.2 2.1\n")

    def fake_plot(df, source: Path, plot_dir: Path, figsize: tuple[float, float]) -> Path:
        plot_dir.mkdir(parents=True, exist_ok=True)
        out_path = plot_dir / f"{source.stem}.png"
        PILImage.new("RGB", (320, 200), color=(255, 0, 0)).save(out_path)
        return out_path

    monkeypatch.setattr(core, "_plot_measurement_matplotlib", fake_plot)

    custom_figsize = (5.5, 3.5)
    config = BuilderConfig(
        fabrication_files=[],
        annealing_files=[high, low],
        output_dir=tmp_path / "out",
        make_plots=True,
        export_formats=("excel",),
        matplotlib_figsize=custom_figsize,
    )

    result = build_database(config)
    excel_path = result.exports["excel"]
    from zipfile import ZipFile

    with ZipFile(excel_path, "r") as archive:
        assert any(
            name.startswith("xl/drawings/drawing") for name in archive.namelist()
        )


def test_excel_export_respects_high_dpi_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("openpyxl")
    from PIL import Image as PILImage
    from zipfile import ZipFile

    high = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 1_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.1\n0.1 0.2 2.1\n")

    def fake_plot(df, source: Path, plot_dir: Path, figsize: tuple[float, float]) -> Path:
        plot_dir.mkdir(parents=True, exist_ok=True)
        out_path = plot_dir / f"{source.stem}.png"
        PILImage.new("RGB", (1650, 1050), color=(0, 128, 0)).save(out_path, dpi=(300, 300))
        return out_path

    monkeypatch.setattr(core, "_plot_measurement_matplotlib", fake_plot)

    custom_figsize = (5.5, 3.5)
    config = BuilderConfig(
        fabrication_files=[],
        annealing_files=[high, low],
        output_dir=tmp_path / "out",
        make_plots=True,
        export_formats=("excel",),
        matplotlib_figsize=custom_figsize,
    )

    result = build_database(config)
    excel_path = result.exports["excel"]

    with ZipFile(excel_path, "r") as archive:
        assert any(
            name.startswith("xl/drawings/drawing") for name in archive.namelist()
        )


def test_microscope_images_populate_diameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    high = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 1_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.1\n0.1 0.2 2.1\n")
    core_img = tmp_path / "Ni55Fe18Ga27 1_1 core.jpg"
    glass_img = tmp_path / "Ni55Fe18Ga27 1_1 glass.jpg"
    core_img.write_bytes(b"core")
    glass_img.write_bytes(b"glass")

    def fake_extract(path: Path, logger: logging.Logger) -> core.MicroscopeOCRResult:
        result = core.MicroscopeOCRResult()
        name = path.name.lower()
        if 'core' in name:
            result.append_value(16.7)
            result.detections.append(core.MicroscopeDetection(value=16.7, image_path=core_img))
        elif 'glass' in name:
            result.append_value(134.4)
            result.append_value(212.4)
            result.detections.append(core.MicroscopeDetection(value=134.4, image_path=glass_img))
            result.detections.append(core.MicroscopeDetection(value=212.4, image_path=glass_img))
        return result

    monkeypatch.setattr(core, "_extract_microscope_diameters", fake_extract)

    config = BuilderConfig(
        fabrication_files=[],
        annealing_files=[high, low],
        output_dir=tmp_path / "out",
        microscope_files=[core_img, glass_img],
    )

    result = build_database(config)
    row = result.dataframe.iloc[0]
    d_col = "d (µm)"
    D_col = "D (µm)"
    ratio_col = "d/D"
    assert float(row[d_col]) == pytest.approx(16.7)
    assert float(row[D_col]) == pytest.approx(212.4)
    expected_ratio = round(16.7 / 212.4, 3)
    assert float(row[ratio_col]) == pytest.approx(expected_ratio)


def test_microscope_ocr_extracts_bracketed_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image as PILImage

    image_path = tmp_path / "Ni55Fe18Ga27 4_1 core.jpg"
    PILImage.new("RGB", (320, 180), color="white").save(image_path)

    class FakeOCR:
        def ocr(self, image, cls: bool = True):  # pragma: no cover - simple stub
            return [
                [
                    (
                        [[0, 0], [160, 0], [160, 40], [0, 40]],
                        ("[1]6.7um", 0.95),
                    ),
                    (
                        [[0, 60], [220, 60], [220, 110], [0, 110]],
                        ("[2]134.5um", 0.94),
                    ),
                ]
            ]

    monkeypatch.setattr(core, "get_paddle_ocr", lambda logger=None: FakeOCR())

    result = core._extract_microscope_diameters(image_path, logging.getLogger("test"))
    assert any(abs(value - 6.7) < 1e-3 for value in result.values)
    assert any(abs(value - 134.5) < 1e-3 for value in result.values)

    grouped, _ = core._group_microscope_measurements([image_path], logging.getLogger("test"))
    key = core._microscope_key(image_path)
    assert key in grouped
    measurements = grouped[key]
    assert measurements.best_core() == pytest.approx(6.7, rel=1e-3)
    assert measurements.best_glass() == pytest.approx(134.5, rel=1e-3)


def test_microscope_ocr_fallback_without_units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image as PILImage

    image_path = tmp_path / "Ni55Fe18Ga27 4_1 core.jpg"
    PILImage.new("RGB", (320, 180), color="white").save(image_path)

    class BareOCR:
        def ocr(self, image, cls: bool = True):  # pragma: no cover - simple stub
            return [
                [
                    (
                        [[0, 0], [160, 0], [160, 40], [0, 40]],
                        ("[1]6.7", 0.92),
                    ),
                ]
            ]

    monkeypatch.setattr(core, "get_paddle_ocr", lambda logger=None: BareOCR())

    result = core._extract_microscope_diameters(image_path, logging.getLogger("test"))
    assert any(abs(value - 6.7) < 1e-3 for value in result.values)

    grouped, _ = core._group_microscope_measurements([image_path], logging.getLogger("test"))
    key = core._microscope_key(image_path)
    assert key in grouped
    measurements = grouped[key]
    assert measurements.best_core() == pytest.approx(6.7, rel=1e-3)


def test_parse_microscope_candidates_prefers_primary_marker() -> None:
    values = core._parse_microscope_candidates([
        "2025/09/25 [116.7um extra [2] 20.0um",
    ])
    assert values == [pytest.approx(6.7)]


def test_parse_microscope_candidates_ignores_secondary() -> None:
    values = core._parse_microscope_candidates([
        "[2] 44.1um 18.5um",
    ])
    assert values == [pytest.approx(18.5)]


def test_parse_microscope_candidates_filters_outliers() -> None:
    sample_text = """5001000 . 7235.0um\n11]65.1um .\n25.0um"""
    values = core._parse_microscope_candidates([sample_text])
    assert values == [pytest.approx(65.1)]


def test_microscope_key_handles_additional_delimiters() -> None:
    dashed = Path("Ni50Fe27Ga23 5-4 core.jpg")
    spaced = Path("Ni50Fe27Ga23 5 4 glass.png")
    assert core._microscope_key(dashed) == ("Ni50Fe27Ga23", 5, 4, None)
    assert core._microscope_key(spaced) == ("Ni50Fe27Ga23", 5, 4, None)


def test_microscope_key_parses_other_end_suffix() -> None:
    sample = Path("Ni50Fe27Ga23 10-5oe glass.png")
    assert core._microscope_key(sample) == ("Ni50Fe27Ga23", 10, 5, "oe")


def test_video_metrics_populate_draw_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    high = tmp_path / "Ni55Fe18Ga27 4_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 4_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.1\n0.1 0.2 2.1\n")
    video_dir = tmp_path / "Ni55Fe18Ga27" / "4.Ni55Fe18Ga27 01012024 0800"
    video_dir.mkdir(parents=True, exist_ok=True)
    video_path = video_dir / "2025-07-02 11-44-34.mkv"
    video_path.write_bytes(b"video")

    class FakeVideoResult:
        def median_temperature(self) -> float | None:
            return 382.5

        def median_underpressure(self) -> float | None:
            return -0.85

        def median_winding_speed(self) -> float | None:
            return 12.5

        def median_glass_feed(self) -> float | None:
            return 37.2

    monkeypatch.setattr(core, "extract_video_metrics", lambda *args, **kwargs: FakeVideoResult())

    config = BuilderConfig(
        fabrication_files=[],
        annealing_files=[high, low],
        output_dir=tmp_path / "out",
        video_files=[video_path],
        highlight_ocr_values=True,
    )

    result = build_database(config)
    row = result.dataframe.iloc[0]
    temperature_column = "Core temperature (°C)"
    underpressure_column = "Underpressure"
    assert float(row[temperature_column]) == pytest.approx(382.5)
    assert float(row[underpressure_column]) == pytest.approx(-0.85)
    assert float(row["Winding speed (m/min)"]) == pytest.approx(12.5)
    assert float(row["Glass feeding (mm/min)"]) == pytest.approx(37.2)
    highlights = result.ocr_highlights
    for column in (
        "Core temperature (°C)",
        "Underpressure",
        "Winding speed (m/min)",
        "Glass feeding (mm/min)",
    ):
        assert column in highlights
        assert 0 in highlights[column]


def test_highlight_and_crop_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    high = tmp_path / "Ni55Fe18Ga27 4_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 4_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.1\n0.1 0.2 2.1\n")
    core_img = tmp_path / "Ni55Fe18Ga27 4_1 core.png"
    glass_img = tmp_path / "Ni55Fe18Ga27 4_1 glass.png"

    from PIL import Image

    Image.new("RGB", (40, 40), color="white").save(core_img)
    Image.new("RGB", (40, 40), color="white").save(glass_img)

    def fake_extract(path: Path, logger: logging.Logger) -> core.MicroscopeOCRResult:
        result = core.MicroscopeOCRResult()
        name = path.name.lower()
        if "core" in name:
            detection = core.MicroscopeDetection(
                value=10.0,
                image_path=core_img,
                bbox=(5, 5, 25, 25),
            )
            result.append_value(10.0)
            result.detections.append(detection)
        elif "glass" in name:
            detection = core.MicroscopeDetection(
                value=50.0,
                image_path=glass_img,
                bbox=(4, 4, 30, 30),
            )
            result.append_value(50.0)
            result.detections.append(detection)
        return result

    monkeypatch.setattr(core, "_extract_microscope_diameters", fake_extract)

    config = BuilderConfig(
        fabrication_files=[],
        annealing_files=[high, low],
        output_dir=tmp_path / "out",
        microscope_files=[core_img, glass_img],
        include_microscope_crops=True,
        highlight_ocr_values=True,
    )

    result = build_database(config)
    row = result.dataframe.iloc[0]
    assert "d (µm) image" in result.dataframe.columns
    assert "D (µm) image" in result.dataframe.columns
    crop_key = row["d (µm) image"]
    assert isinstance(crop_key, str) and crop_key in result.microscope_crops
    assert "d (µm)" in result.ocr_highlights
    assert 0 in result.ocr_highlights["d (µm)"]
    assert "D (µm)" in result.ocr_highlights
    assert 0 in result.ocr_highlights["D (µm)"]

def test_build_database_uses_strain_records(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    high = tmp_path / "Ni55Fe18Ga27 4_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 4_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.0\n0.1 0.2 2.0\n")
    strain_path = tmp_path / "strain.xlsx"
    pd.DataFrame(
        {
            "Composition": ["Ni55Fe18Ga27"],
            "Microwire": ["4/1"],
            "M length": [32],
            "A length": [30],
            "Strain %": [6.25],
        }
    ).to_excel(strain_path, index=False)
    config = BuilderConfig(
        fabrication_files=[],
        annealing_files=[high, low],
        output_dir=tmp_path / "out",
        strain_files=[strain_path],
    )
    result = build_database(config)
    row = result.dataframe.iloc[0]
    assert row[core.STRAIN_COLUMN] == "6.250%"
    columns = result.dataframe.columns.tolist()
    assert core.STRAIN_COLUMN in columns


def test_update_existing_exports_with_strain(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    strain_path = tmp_path / "strain.xlsx"
    pd.DataFrame(
        {
            "Composition": ["Ni55Fe18Ga27"],
            "Microwire": ["4/1"],
            "M length": [32],
            "A length": [30],
            "Strain %": [6.25],
        }
    ).to_excel(strain_path, index=False)
    strain_records = core._load_strain_records([strain_path], logging.getLogger("test"))

    legacy_columns = [
        "Composition",
        "Microwire",
        "d (µm)",
        "D (µm)",
        "d/D",
        "Length (m)",
        "Figure — 1000 mA",
        "Figure — low mA",
    ]
    legacy_row = {
        "Composition": "Ni55Fe18Ga27",
        "Microwire": "4/1",
        "d (µm)": 8.0,
        "D (µm)": 40.0,
        "d/D": 0.2,
        "Length (m)": 5.0,
        "Figure — 1000 mA": "high.png",
        "Figure — low mA": "low.png",
    }

    csv_path = tmp_path / "legacy.csv"
    pd.DataFrame([legacy_row], columns=legacy_columns).to_csv(csv_path, index=False)
    core._update_existing_csv_with_strain(csv_path, strain_records, core.OUTPUT_COLUMNS, logging.getLogger("test"))
    updated_csv = pd.read_csv(csv_path)
    assert updated_csv[core.STRAIN_COLUMN].iloc[0] == "6.250%"

    excel_path = tmp_path / "legacy.xlsx"
    pd.DataFrame([legacy_row], columns=legacy_columns).to_excel(excel_path, index=False)
    core._update_existing_excel_with_strain(excel_path, strain_records, logging.getLogger("test"))
    updated_excel = pd.read_excel(excel_path)
    columns = updated_excel.columns.tolist()
    assert core.STRAIN_COLUMN in columns
    assert updated_excel[core.STRAIN_COLUMN].iloc[0] == "6.250%"


def test_builder_column_groups_include_transition_and_current_density_columns() -> None:
    _ensure_qapp()
    window = BuilderWindow()
    window._auto_open_last = False
    try:
        assembly = getattr(window, "assembly_section", None)
        assert assembly is not None
        groups = assembly._column_groups(core.OUTPUT_COLUMNS)  # noqa: SLF001 - UI grouping helper
        current_density_group = groups.get("Current density")
        assert isinstance(current_density_group, list)
        assert "As1 (mA)" in current_density_group
        assert "Af1 (mA)" in current_density_group
        assert "As2 (mA)" in current_density_group
        assert "Mf2-Af2 (mA)" in current_density_group
        transition_group = groups.get("Transition temps")
        assert transition_group == [
            "As (°C)",
            "Af (°C)",
            "Ms (°C)",
            "Mf (°C)",
        ]
    finally:
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_builder_recent_projects_menu_updates(tmp_path: Path) -> None:
    _ensure_qapp()
    window = BuilderWindow()
    window._auto_open_last = False
    try:
        window._clear_recent_projects()
        QtWidgets.QApplication.processEvents()
        menu = window.findChild(QtWidgets.QMenu, "mw_builder_recent_projects")
        assert isinstance(menu, QtWidgets.QMenu)

        actions = menu.actions()
        assert actions, "expected placeholder action for empty recent list"
        assert actions[0].text() == "No recent projects"
        assert not actions[0].isEnabled()

        project_path = tmp_path / "example.pydpj"
        project_path.write_text("{}", encoding="utf-8")
        window._remember_recent_project(project_path)
        QtWidgets.QApplication.processEvents()

        entries = [action.text() for action in menu.actions()]
        assert any(project_path.stem in entry for entry in entries)
        assert window._recent_projects and str(project_path) == window._recent_projects[0]

        clear_action = next(
            (action for action in menu.actions() if action.text() == "Clear list"),
            None,
        )
        assert isinstance(clear_action, QtGui.QAction)
        # QAction.trigger() can deadlock with native menu plumbing in headless macOS runs.
        window._clear_recent_projects()
        QtWidgets.QApplication.processEvents()

        assert window._recent_projects == []
        refreshed_actions = menu.actions()
        assert refreshed_actions and refreshed_actions[0].text() == "No recent projects"
    finally:
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_builder_close_stops_pending_scan_threads(tmp_path: Path) -> None:
    _ensure_qapp()
    window = BuilderWindow()
    window._auto_open_last = False
    try:
        scan_root = tmp_path / "scan"
        scan_root.mkdir(parents=True, exist_ok=True)
        for idx in range(300):
            (scan_root / f"sample_{idx:04d}.txt").write_text("1 2 3\n", encoding="utf-8")

        scanned_sections = []
        for section in window.sections.values():
            if not hasattr(section, "set_sources") or not hasattr(section, "_request_pending_scan"):
                continue
            section.set_sources([str(scan_root)])
            section._request_pending_scan()
            scanned_sections.append(section)
        QtWidgets.QApplication.processEvents()

        window._dirty = False
        window.close()
        QtWidgets.QApplication.processEvents()

        for section in scanned_sections:
            pending_thread = getattr(section, "_pending_scan_thread", None)
            worker_thread = getattr(section, "_worker_thread", None)
            assert pending_thread is None
            assert worker_thread is None
    finally:
        if window.isVisible():
            window._dirty = False
            window.close()


def test_split_sample_variant_parses_suffix() -> None:
    from microwire_data_builder.ui import _split_sample_variant

    base, variant = _split_sample_variant("Ni50Fe27Ga23 5-4 no glass")
    assert base == "Ni50Fe27Ga23 5-4"
    assert variant == "no glass"

    base, variant = _split_sample_variant("Ni50Fe27Ga23 5-4")
    assert base == "Ni50Fe27Ga23 5-4"
    assert variant is None


def test_load_project_handles_missing_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    window = BuilderWindow()
    window._auto_open_last = False
    try:
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "information",
            lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Ok,
        )
        project_path = tmp_path / "partial_project.pydpj"
        fabrication_source = str(tmp_path / "fabrication.xlsx")
        payload = {
            "kind": window.PROJECT_KIND,
            "version": window.PROJECT_VERSION,
            "sections": {
                "fabrication": {
                    "sources": [fabrication_source],
                    "columns": ["col_a", "col_b"],
                    "rows": [["alpha", "beta"]],
                }
            },
        }
        project_path.write_text(json.dumps(payload), encoding="utf-8")

        window._load_project_from_path(project_path)
        QtWidgets.QApplication.processEvents()

        assert window._project_path == project_path
        assert window._recent_projects and str(project_path) == window._recent_projects[0]

        fabrication_section = window.sections.get("fabrication")
        assert fabrication_section is not None
        assert getattr(fabrication_section.data, "sources", []) == [fabrication_source]

        project_tree = getattr(window, "project_tree", None)
        assert isinstance(project_tree, QtWidgets.QTreeWidget)
        fabrication_item = None
        for index in range(project_tree.topLevelItemCount()):
            item = project_tree.topLevelItem(index)
            if item.text(0) == getattr(fabrication_section, "section_title", "Fabrication"):
                fabrication_item = item
                break
        assert fabrication_item is not None
        child_sources = [fabrication_item.child(i).text(1) for i in range(fabrication_item.childCount())]
        assert fabrication_source in child_sources

        for key, section in window.sections.items():
            if key == "fabrication":
                continue
            sources = getattr(getattr(section, "data", object()), "sources", [])
            assert list(sources) in ([], [fabrication_source])
    finally:
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()
