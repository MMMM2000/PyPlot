"""Tests for the microwire data builder core logic."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import importlib.util
import logging
import sys
import time

import pandas as pd
import numpy as np
import pytest
from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

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
MiniDmaRecord = core.MiniDmaRecord
VsmTemperatureScanRecord = core.VsmTemperatureScanRecord
SHAPE_MEMORY_STRESS_STRAIN_COLUMN = core.SHAPE_MEMORY_STRESS_STRAIN_COLUMN
MINI_DMA_COLUMN = core.MINI_DMA_COLUMN
MINI_DMA_STRAIN_COLUMN = core.MINI_DMA_STRAIN_COLUMN
MINI_DMA_TRANSITION_COLUMN = core.MINI_DMA_TRANSITION_COLUMN
MINI_DMA_BREAK_COLUMN = core.MINI_DMA_BREAK_COLUMN
ANNEALING_TRANSITION_COLUMN = core.ANNEALING_TRANSITION_COLUMN
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


def test_microscope_key_preserves_decimal_composition_token() -> None:
    parsed = core._microscope_key(Path("Mn58.1Ni4.3Si18.5Sn18.8 3_2 glass.jpg"))
    assert parsed == ("Mn58.1Ni4.3Si18.5Sn18.8", 3, 2, None)


def test_microscope_key_draw_piece_starts_after_composition_boundary() -> None:
    assert core._microscope_key(Path("Ni44Fe27Ga23Cu3Co3_1-5 glass.jpg")) == (
        "Ni44Fe27Ga23Cu3Co3",
        1,
        5,
        None,
    )
    assert core._microscope_key(Path("Ni44Fe27Ga23Cu3Co3 1_5 core.jpg")) == (
        "Ni44Fe27Ga23Cu3Co3",
        1,
        5,
        None,
    )
    assert core._microscope_key(Path("Ni46Fe27Ga23Cu2Co2-2_1-No1 glass.jpg")) == (
        "Ni46Fe27Ga23Cu2Co2",
        2,
        1,
        "No1",
    )


def test_microscope_key_ignores_google_drive_shortcut_ancestors(tmp_path: Path) -> None:
    video_path = (
        tmp_path
        / ".shortcut-targets-by-id"
        / "1-8FX4i_bNyyVH8wvIQz5KdrZuCTYH2xT"
        / "databaza mikrodrotov"
        / "Ni50Fe27Ga23"
        / "6.Ni50Fe27Ga23 20052024 1140"
        / "2024-05-20 11-37-53.mkv"
    )
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    assert core._microscope_key(video_path) is None
    assert core._draw_key(video_path) == ("Ni50Fe27Ga23", 6)


def test_collect_video_metrics_uses_draw_folder_for_shortcut_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = (
        tmp_path
        / ".shortcut-targets-by-id"
        / "1-8FX4i_bNyyVH8wvIQz5KdrZuCTYH2xT"
        / "databaza mikrodrotov"
        / "Ni50Fe27Ga23"
        / "3.Ni50Fe27Ga23 17042024 0850"
        / "2024-04-17 08-44-39.mkv"
    )
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    class FakeVideoResult:
        def median_temperature(self) -> float | None:
            return 401.2

        def median_underpressure(self) -> float | None:
            return 52.0

        def median_winding_speed(self) -> float | None:
            return 64.0

        def median_glass_feed(self) -> float | None:
            return 3.4

    monkeypatch.setattr(core, "extract_video_metrics", lambda *args, **kwargs: FakeVideoResult())

    aggregated = core._collect_video_metrics([video_path], logging.getLogger("test"))

    assert set(aggregated.keys()) == {("Ni50Fe27Ga23", 3, None)}
    summary = aggregated[("Ni50Fe27Ga23", 3, None)]
    assert summary.temperature() == pytest.approx(401.2)
    assert video_path in summary.sources


def _ensure_qapp() -> QtWidgets.QApplication:
    global _APP_REF
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    _APP_REF = app
    return app


def _wait_for_qt(predicate, *, timeout_ms: int = 3000) -> None:
    app = _ensure_qapp()
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QtTest.QTest.qWait(10)
    app.processEvents()
    assert predicate()


def test_dataframe_model_sort_invalidates_cached_decoration_rows() -> None:
    _ensure_qapp()
    frame = pd.DataFrame(
        {
            "Name": ["alpha", "beta"],
            "Sort": [2, 1],
            "Preview": ["", ""],
        }
    )
    model = builder_ui.DataFrameModel(frame)
    pixmaps = {
        "alpha": QtGui.QPixmap(4, 4),
        "beta": QtGui.QPixmap(4, 4),
    }
    seen: list[str] = []

    def decoration_provider(row: pd.Series, column: str) -> QtGui.QPixmap | None:
        if column != "Preview":
            return None
        name = str(row.get("Name"))
        seen.append(name)
        return pixmaps[name]

    model.set_decoration_provider(decoration_provider)

    assert (
        model.data(
            model.index(0, 2),
            QtCore.Qt.ItemDataRole.DecorationRole,
        )
        is pixmaps["alpha"]
    )
    assert seen == ["alpha"]

    model.sort(1, QtCore.Qt.SortOrder.AscendingOrder)

    assert (
        model.data(
            model.index(0, 0),
            QtCore.Qt.ItemDataRole.DisplayRole,
        )
        == "beta"
    )
    assert (
        model.data(
            model.index(0, 2),
            QtCore.Qt.ItemDataRole.DecorationRole,
        )
        is pixmaps["beta"]
    )
    assert seen[-1] == "beta"


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
            "10000 Oe \u2191 S1",
            "10000 Oe \u2193 S2",
            "5 Oe \u2191 S1",
            "5 Oe \u2193 S2",
        ]
    finally:
        builder_ui.plt.close(figure)


def test_vsm_hysteresis_section_preview_icon_width_scales_with_group_count() -> None:
    _ensure_qapp()
    section = builder_ui.VsmHysteresisSection(logging.getLogger("test"), lambda *_: None)
    try:
        section._preview_group_count = 2
        assert section._preview_icon_width() == (
            builder_ui.ANNEALING_GRAPH_WIDTH * 2 + section._preview_spacing
        )
    finally:
        section.close()


def test_plot_vsm_hysteresis_figure_defaults_to_zoomed_preview_range(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(tmp_path / "builder.ini"))
    record = builder_ui.VsmHysteresisRecord(
        path=Path("loop.dat"),
        sample="Sample",
        data=pd.DataFrame(
            {
                "Applied Field For Plot": [-10000.0, -5000.0, 0.0, 5000.0, 10000.0],
                "Signal X direction": [-1.0, -0.7, 0.0, 0.7, 1.0],
            }
        ),
        angle=0.0,
    )

    figure = builder_ui._plot_vsm_hysteresis_figure(
        [record],
        logging.getLogger("test"),
        width_px=720,
        height_px=360,
    )
    assert figure is not None
    try:
        xlim = figure.axes[0].get_xlim()
        assert xlim == pytest.approx((-1000.0, 1000.0))
    finally:
        builder_ui.plt.close(figure)


def test_plot_vsm_hysteresis_figure_respects_auto_preview_range(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))
    settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.Format.IniFormat)
    settings.setValue(builder_ui.VSM_HYSTERESIS_PREVIEW_RANGE_SETTING, "auto")
    settings.sync()

    record = builder_ui.VsmHysteresisRecord(
        path=Path("loop.dat"),
        sample="Sample",
        data=pd.DataFrame(
            {
                "Applied Field For Plot": [-10000.0, -5000.0, 0.0, 5000.0, 10000.0],
                "Signal X direction": [-1.0, -0.7, 0.0, 0.7, 1.0],
            }
        ),
        angle=0.0,
    )

    figure = builder_ui._plot_vsm_hysteresis_figure(
        [record],
        logging.getLogger("test"),
        width_px=720,
        height_px=360,
    )
    assert figure is not None
    try:
        xlim = figure.axes[0].get_xlim()
        assert xlim[0] < -9000.0
        assert xlim[1] > 9000.0
    finally:
        builder_ui.plt.close(figure)


def test_filter_vsm_hysteresis_records_by_angle_mode_keeps_only_focus_angles() -> None:
    records = [
        builder_ui.VsmHysteresisRecord(
            path=Path("loop_a000.dat"),
            sample="Sample",
            data=pd.DataFrame({"Applied Field For Plot": [0.0], "Signal X direction": [0.0]}),
            angle=0.0,
        ),
        builder_ui.VsmHysteresisRecord(
            path=Path("loop_a030.dat"),
            sample="Sample",
            data=pd.DataFrame({"Applied Field For Plot": [0.0], "Signal X direction": [0.0]}),
            angle=30.0,
        ),
        builder_ui.VsmHysteresisRecord(
            path=Path("loop_a090.dat"),
            sample="Sample",
            data=pd.DataFrame({"Applied Field For Plot": [0.0], "Signal X direction": [0.0]}),
            angle=90.0,
        ),
        builder_ui.VsmHysteresisRecord(
            path=Path("loop_a180.dat"),
            sample="Sample",
            data=pd.DataFrame({"Applied Field For Plot": [0.0], "Signal X direction": [0.0]}),
            angle=180.0,
        ),
    ]

    filtered = builder_ui._filter_vsm_hysteresis_records_by_angle_mode(records, "0_90")

    assert [record.angle for record in filtered] == [0.0, 90.0]


def test_plot_vsm_hysteresis_figure_can_limit_preview_to_0_and_90_deg() -> None:
    records = [
        builder_ui.VsmHysteresisRecord(
            path=Path("loop_a000.dat"),
            sample="Sample",
            data=pd.DataFrame(
                {
                    "Applied Field For Plot": [-1000.0, 0.0, 1000.0],
                    "Signal X direction": [-1.0, 0.0, 1.0],
                }
            ),
            angle=0.0,
        ),
        builder_ui.VsmHysteresisRecord(
            path=Path("loop_a045.dat"),
            sample="Sample",
            data=pd.DataFrame(
                {
                    "Applied Field For Plot": [-1000.0, 0.0, 1000.0],
                    "Signal X direction": [-0.5, 0.0, 0.5],
                }
            ),
            angle=45.0,
        ),
        builder_ui.VsmHysteresisRecord(
            path=Path("loop_a090.dat"),
            sample="Sample",
            data=pd.DataFrame(
                {
                    "Applied Field For Plot": [-1000.0, 0.0, 1000.0],
                    "Signal X direction": [-0.8, 0.0, 0.8],
                }
            ),
            angle=90.0,
        ),
    ]

    figure = builder_ui._plot_vsm_hysteresis_figure(
        records,
        logging.getLogger("test"),
        width_px=720,
        height_px=360,
        angle_filter_mode="0_90",
    )
    assert figure is not None
    try:
        labels = [line.get_label() for line in figure.axes[0].get_lines()]
        assert labels == ["0°", "90°"]
    finally:
        builder_ui.plt.close(figure)


def test_vsm_hysteresis_section_preview_range_change_persists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))
    section = builder_ui.VsmHysteresisSection(logging.getLogger("test"), lambda *_: None)
    try:
        assert section.preview_range_combo.currentData() == "1000"
        section._pixmap_cache["sample|Graph"] = QtGui.QPixmap(10, 10)
        section.preview_range_combo.setCurrentIndex(section.preview_range_combo.findData("auto"))
        QtWidgets.QApplication.processEvents()

        settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.Format.IniFormat)
        assert settings.value(builder_ui.VSM_HYSTERESIS_PREVIEW_RANGE_SETTING) == "auto"
        assert section._pixmap_cache == {}
    finally:
        section.close()


def test_vsm_hysteresis_section_angle_filter_change_persists_and_filters_groups(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))
    section = builder_ui.VsmHysteresisSection(logging.getLogger("test"), lambda *_: None)
    try:
        assert section.angle_filter_combo.currentData() == "all"
        records = [
            builder_ui.VsmHysteresisRecord(
                path=Path("loop_a000.dat"),
                sample="Sample",
                data=pd.DataFrame(
                    {
                        "Applied Field For Plot": [-1000.0, 0.0, 1000.0],
                        "Signal X direction": [-1.0, 0.0, 1.0],
                    }
                ),
                angle=0.0,
            ),
            builder_ui.VsmHysteresisRecord(
                path=Path("loop_a045.dat"),
                sample="Sample",
                data=pd.DataFrame(
                    {
                        "Applied Field For Plot": [-1000.0, 0.0, 1000.0],
                        "Signal X direction": [-0.5, 0.0, 0.5],
                    }
                ),
                angle=45.0,
            ),
            builder_ui.VsmHysteresisRecord(
                path=Path("loop_a090.dat"),
                sample="Sample",
                data=pd.DataFrame(
                    {
                        "Applied Field For Plot": [-1000.0, 0.0, 1000.0],
                        "Signal X direction": [-0.8, 0.0, 0.8],
                    }
                ),
                angle=90.0,
            ),
        ]
        section.store.save_payload("vsm_hysteresis_records", records)
        section._refresh_record_groups()
        assert [record.angle for record in section._record_groups["Sample"]] == [0.0, 45.0, 90.0]

        section.angle_filter_combo.setCurrentIndex(section.angle_filter_combo.findData("0_90"))
        QtWidgets.QApplication.processEvents()

        settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.Format.IniFormat)
        assert settings.value(builder_ui.VSM_HYSTERESIS_ANGLE_FILTER_SETTING) == "0_90"
        assert [record.angle for record in section._record_groups["Sample"]] == [0.0, 90.0]
    finally:
        section.close()


def test_vsm_temperature_section_preview_icon_width_scales_with_group_count() -> None:
    _ensure_qapp()
    section = builder_ui.VsmTemperatureScanSection(logging.getLogger("test"), lambda *_: None)
    try:
        section._preview_group_count = 2
        assert section._preview_icon_width() == (
            builder_ui.ANNEALING_GRAPH_WIDTH * 2 + section._preview_spacing
        )
    finally:
        section.close()


def test_vsm_temperature_section_combines_preview_pixmaps_side_by_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    section = builder_ui.VsmTemperatureScanSection(logging.getLogger("test"), lambda *_: None)
    try:
        record = core.VsmTemperatureScanRecord(
            path=Path("scan.txt"),
            sample="Ni48Fe27Ga21Cu2 2/4",
            data=pd.DataFrame({"temperature": [20.0, 30.0], "field": [5.0, 5.0], "signal": [1.0, 0.9]}),
            key="Ni48Fe27Ga21Cu2|2|4",
            label="scan",
        )
        section._record_groups = {record.sample: [record, record]}
        section._preview_group_count = 2

        def _fake_preview_items(*args: object, **kwargs: object) -> list[builder_ui._GraphPreviewItem]:
            pixmap_a = QtGui.QPixmap(builder_ui.ANNEALING_GRAPH_WIDTH, builder_ui.ANNEALING_GRAPH_HEIGHT)
            pixmap_a.fill(QtGui.QColor("#d94f4f"))
            pixmap_b = QtGui.QPixmap(builder_ui.ANNEALING_GRAPH_WIDTH, builder_ui.ANNEALING_GRAPH_HEIGHT)
            pixmap_b.fill(QtGui.QColor("#2e8b57"))
            return [
                builder_ui._GraphPreviewItem("A", pixmap_a),
                builder_ui._GraphPreviewItem("B", pixmap_b),
            ]

        monkeypatch.setattr(builder_ui, "_vsm_temperature_preview_items", _fake_preview_items)

        row = pd.Series(
            {
                "Composition": "Ni48Fe27Ga21Cu2",
                "Microwire": "2/4",
                "Sample": record.sample,
            }
        )
        pixmap = section._preview_decoration(row, builder_ui.VSM_TEMPERATURE_SCAN_COLUMN)

        assert isinstance(pixmap, QtGui.QPixmap)
        assert not pixmap.isNull()
        assert pixmap.width() == section._preview_icon_width()
        assert pixmap.height() == section._preview_icon_height()
        assert pixmap.width() > builder_ui.ANNEALING_GRAPH_WIDTH
    finally:
        section.close()


def test_vsm_temperature_visible_preview_defers_pixmap_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    section = builder_ui.VsmTemperatureScanSection(logging.getLogger("test"), lambda *_: None)
    try:
        pixmap = QtGui.QPixmap(16, 16)
        pixmap.fill(QtGui.QColor("#2e8b57"))
        render_calls: list[int] = []
        monkeypatch.setattr(
            section,
            "_render_preview_pixmap",
            lambda records: render_calls.append(len(records)) or pixmap,
        )
        section._record_groups = {"Sample": [object(), object()]}  # type: ignore[list-item]
        section.show()
        assert section.table_view is not None
        section.table_view.show()
        QtWidgets.QApplication.processEvents()

        result = section._preview_decoration(
            pd.Series({"_sample": "Sample"}),
            builder_ui.VSM_TEMPERATURE_SCAN_COLUMN,
        )

        assert result is None
        assert render_calls == []
        assert "Sample|VSM temperature scan graphs" in section._preview_render_pending

        QtWidgets.QApplication.processEvents()

        assert render_calls == [2]
        assert section._pixmap_cache["Sample|VSM temperature scan graphs"] is pixmap
    finally:
        section.close()
        QtWidgets.QApplication.processEvents()


def test_plot_vsm_temperature_scan_figure_can_use_smoothed_preview_mode() -> None:
    processor = VSMTemperatureScanProcessor()
    processor.set_show_smoothed(True)
    processor.set_smoothing_windows(3, 3)
    record = core.VsmTemperatureScanRecord(
        path=Path("scan.txt"),
        sample="Sample",
        data=pd.DataFrame(
            {
                "temperature": [20.0, 40.0, 60.0, 80.0, 100.0],
                "field": [5.0, 5.0, 5.0, 5.0, 5.0],
                "signal": [1.0, 0.7, 1.1, 0.6, 1.0],
                "section_index": [0, 0, 0, 0, 0],
            }
        ),
    )

    raw_figure = builder_ui._plot_vsm_temperature_scan_figure(
        record,
        processor,
        width_px=720,
        height_px=360,
        preview_mode="raw",
    )
    smooth_figure = builder_ui._plot_vsm_temperature_scan_figure(
        record,
        processor,
        width_px=720,
        height_px=360,
        preview_mode="smoothed",
    )
    assert raw_figure is not None
    assert smooth_figure is not None
    try:
        raw_y = raw_figure.axes[0].get_lines()[0].get_ydata()
        smooth_y = smooth_figure.axes[0].get_lines()[0].get_ydata()
        assert list(raw_y) != list(smooth_y)
        assert "Smoothed" in smooth_figure.axes[0].get_title()
    finally:
        builder_ui.plt.close(raw_figure)
        builder_ui.plt.close(smooth_figure)


def test_vsm_temperature_section_preview_mode_change_persists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))
    section = builder_ui.VsmTemperatureScanSection(logging.getLogger("test"), lambda *_: None)
    try:
        assert section.preview_mode_combo.currentData() == "raw"
        section._pixmap_cache["sample|Graph"] = QtGui.QPixmap(10, 10)
        section.preview_mode_combo.setCurrentIndex(section.preview_mode_combo.findData("smoothed"))
        QtWidgets.QApplication.processEvents()

        settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.Format.IniFormat)
        assert settings.value(builder_ui.VSM_TEMPERATURE_PREVIEW_MODE_SETTING) == "smoothed"
        assert section._pixmap_cache == {}
    finally:
        section.close()


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


def test_render_measurement_pixmap_keeps_pyplot_legend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    captured_figures = []
    original = builder_ui.plot_annealing_curve

    def _wrapped(*args: object, **kwargs: object) -> object:
        figure, config = original(*args, **kwargs)
        captured_figures.append(figure)
        return figure, config

    monkeypatch.setattr(builder_ui, "plot_annealing_curve", _wrapped)
    record = SimpleNamespace(
        dataframe=pd.DataFrame(
            {
                "I_mA": [10.0, 50.0, 100.0, 60.0, 20.0],
                "R_ohm": [120.0, 150.0, 180.0, 160.0, 130.0],
            }
        ),
        path=Path("Ni50Fe27Ga23 11_1 s1 1000mA.txt"),
        metadata=None,
    )

    try:
        pixmap = builder_ui._render_measurement_pixmap(record, logging.getLogger("test"))
        assert isinstance(pixmap, QtGui.QPixmap)
        assert not pixmap.isNull()
        assert captured_figures
        axes = captured_figures[0].axes[0]
        title = axes.get_title()
        assert "Ni" in title and "Fe" in title and "Ga" in title
        assert "11/1" in title
        assert "1000" in title and "mA" in title
        assert axes.get_legend() is not None
    finally:
        for figure in captured_figures:
            builder_ui.plt.close(figure)


def test_render_measurement_pixmap_passes_wire_diameter_to_axis_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    captured_figures = []
    original = builder_ui.plot_annealing_curve

    def _wrapped(*args: object, **kwargs: object) -> object:
        figure, config = original(*args, **kwargs)
        captured_figures.append(figure)
        return figure, config

    monkeypatch.setattr(builder_ui, "plot_annealing_curve", _wrapped)
    record = SimpleNamespace(
        dataframe=pd.DataFrame(
            {
                "I_mA": [0.0, 50.0, 100.0, 50.0],
                "R_ohm": [120.0, 150.0, 180.0, 160.0],
            }
        ),
        path=Path("Ni50Fe27Ga23 11_1 s1 1000mA.txt"),
        metadata=None,
    )

    try:
        pixmap = builder_ui._render_measurement_pixmap(
            record,
            logging.getLogger("test"),
            wire_diameter_um=20.0,
        )
        assert isinstance(pixmap, QtGui.QPixmap)
        assert captured_figures
        assert captured_figures[0].axes[0].get_xlabel() == (
            "Current [mA] (100 mA = 318 A/mm², d = 20 µm)"
        )
        assert captured_figures[0].axes[1].get_xlabel() == "Current density [A/mm²]"
    finally:
        for figure in captured_figures:
            builder_ui.plt.close(figure)


def test_render_measurement_pixmap_omits_auto_transition_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    captured_figures = []
    original = builder_ui.plot_annealing_curve

    def _wrapped(*args: object, **kwargs: object) -> object:
        figure, config = original(*args, **kwargs)
        captured_figures.append(figure)
        return figure, config

    monkeypatch.setattr(builder_ui, "plot_annealing_curve", _wrapped)
    def _fail_if_called(_frame: object) -> object:
        raise AssertionError("normal annealing previews should not compute transition markers")

    monkeypatch.setattr(builder_ui, "summarize_annealing_transition_loops", _fail_if_called)
    record = SimpleNamespace(
        dataframe=pd.DataFrame(
            {
                "I_mA": [10.0, 25.0, 40.0, 38.0, 20.0],
                "R_ohm": [120.0, 118.0, 150.0, 140.0, 125.0],
            }
        ),
        path=Path("Ni50Fe27Ga23 11_1 s1 1000mA.txt"),
        metadata=None,
    )

    try:
        pixmap = builder_ui._render_measurement_pixmap(record, logging.getLogger("test"))
        assert isinstance(pixmap, QtGui.QPixmap)
        assert captured_figures
        axes = captured_figures[0].axes[0]
        labels = [text.get_text() for text in axes.get_legend().get_texts()]
        assert "As 25 mA" not in labels
        assert "Af 40 mA" not in labels
        assert "Ms 38 mA" not in labels
        assert "Mf 20 mA" not in labels
    finally:
        for figure in captured_figures:
            builder_ui.plt.close(figure)


def test_annealing_display_keeps_pyplot_title_and_axis_labels() -> None:
    display = builder_ui._AnnealingPlotDisplay.__new__(builder_ui._AnnealingPlotDisplay)
    record = SimpleNamespace(
        dataframe=pd.DataFrame(
            {
                "I_mA": [0.0, 50.0, 100.0, 50.0],
                "R_ohm": [120.0, 150.0, 180.0, 160.0],
            }
        ),
        path=Path("Ni50Fe27Ga23 1_1 1000 mA.csv"),
        metadata=None,
    )

    figure = display._build_figure(record)
    try:
        axes = figure.axes[0]
        assert axes.get_title()
        assert "1/1" in axes.get_title()
        assert "1000 mA" in axes.get_title()
        assert axes.get_xlabel() == "Current [mA]"
        assert axes.get_ylabel()
        assert axes.get_legend() is not None
    finally:
        builder_ui.plt.close(figure)


def test_annealing_display_review_mode_marks_auto_transition_currents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    display = builder_ui._AnnealingPlotDisplay.__new__(builder_ui._AnnealingPlotDisplay)
    display._logger = logging.getLogger("test")  # noqa: SLF001
    display._show_transition_markers = True  # noqa: SLF001
    monkeypatch.setattr(
        builder_ui,
        "summarize_annealing_transition_loops",
        lambda _frame: (
            SimpleNamespace(
                as_current_mA=25.0,
                af_current_mA=40.0,
                ms_current_mA=38.0,
                mf_current_mA=20.0,
                loop_index=1,
            ),
        ),
    )
    record = SimpleNamespace(
        dataframe=pd.DataFrame(
            {
                "I_mA": [10.0, 25.0, 40.0, 38.0, 20.0],
                "R_ohm": [120.0, 118.0, 150.0, 140.0, 125.0],
            }
        ),
        path=Path("Ni50Fe27Ga23 11_1 s1 1000mA.txt"),
        metadata=None,
    )

    figure = display._build_figure(record)
    try:
        axes = figure.axes[0]
        labels = [text.get_text() for text in axes.get_legend().get_texts()]
        assert labels == ["Increasing 1"]
        inline_labels = {text.get_text() for text in axes.texts}
        assert {"As1", "Af1", "Ms1", "Mf1"}.issubset(inline_labels)
    finally:
        builder_ui.plt.close(figure)


def test_annealing_transition_review_entries_show_auto_summary() -> None:
    path = Path("Ni50Fe27Ga23 11_1 1000mA.txt")
    record = MeasurementRecord(
        path=path,
        metadata=MeasurementMetadata(
            composition_token="Ni50Fe27Ga23",
            draw_x=11,
            piece_y=1,
            setpoint_mA=1000.0,
            alt_variant=False,
            measurement_id=path.stem,
            file_name=path.name,
            relpath=path.name,
            timestamp_mtime_utc="2026-06-15T00:00:00+00:00",
        ),
        dataframe=pd.DataFrame(
            {
                "I_mA": [10.0, 25.0, 40.0, 38.0, 20.0],
                "R_ohm": [120.0, 118.0, 150.0, 140.0, 125.0],
            }
        ),
        sanity_ok=True,
        sanity_error=None,
        transition_summary=("1000mA: As 25 mA, Af 40 mA, Ms 38 mA, Mf 20 mA",),
    )

    entries = builder_ui._annealing_transition_review_entries(
        [record],
        logging.getLogger("test"),
    )

    assert len(entries) == 1
    assert entries[0].status == "auto candidates"
    assert "Ni50Fe27Ga23" in entries[0].title
    assert entries[0].summary_lines == (
        "1000mA: As 25 mA, Af 40 mA, Ms 38 mA, Mf 20 mA",
    )


def test_annealing_transition_review_entries_show_multi_loop_auto_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path("Ni50Fe27Ga23 11_1 1000mA.txt")
    record = MeasurementRecord(
        path=path,
        metadata=MeasurementMetadata(
            composition_token="Ni50Fe27Ga23",
            draw_x=11,
            piece_y=1,
            setpoint_mA=1000.0,
            alt_variant=False,
            measurement_id=path.stem,
            file_name=path.name,
            relpath=path.name,
            timestamp_mtime_utc="2026-06-15T00:00:00+00:00",
        ),
        dataframe=pd.DataFrame({"I_mA": [10.0, 20.0], "R_ohm": [120.0, 130.0]}),
        sanity_ok=True,
        sanity_error=None,
        transition_summary=(),
    )
    monkeypatch.setattr(
        builder_ui,
        "_annealing_transition_summary",
        lambda _frame, *, label=None: (
            f"{label} loop 1: As 25 mA, Af 40 mA, Ms 38 mA, Mf 20 mA",
            f"{label} loop 2: As 50 mA, Af 60 mA",
        ),
    )

    entries = builder_ui._annealing_transition_review_entries(
        [record],
        logging.getLogger("test"),
    )

    assert entries[0].status == "auto candidates"
    assert entries[0].summary_lines == (
        "Ni50Fe27Ga23 11_1 1000mA.txt loop 1: As 25 mA, Af 40 mA, Ms 38 mA, Mf 20 mA",
        "Ni50Fe27Ga23 11_1 1000mA.txt loop 2: As 50 mA, Af 60 mA",
    )


def test_annealing_section_opens_transition_review_for_visible_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    path = Path("Ni50Fe27Ga23 11_1 1000mA.txt")
    record = MeasurementRecord(
        path=path,
        metadata=MeasurementMetadata(
            composition_token="Ni50Fe27Ga23",
            draw_x=11,
            piece_y=1,
            setpoint_mA=1000.0,
            alt_variant=False,
            measurement_id=path.stem,
            file_name=path.name,
            relpath=path.name,
            timestamp_mtime_utc="2026-06-15T00:00:00+00:00",
        ),
        dataframe=pd.DataFrame({"I_mA": [10.0, 20.0], "R_ohm": [120.0, 130.0]}),
        sanity_ok=True,
        sanity_error=None,
        transition_summary=("1000mA: As 11 mA, Af 13 mA",),
    )
    opened: list[list[MeasurementRecord]] = []

    class _FakeDialog:
        def __init__(
            self,
            records: list[MeasurementRecord],
            *_args: object,
            **_kwargs: object,
        ) -> None:
            opened.append(list(records))

        def exec(self) -> int:
            return 0

    try:
        section._all_records = [record]
        section._record_groups = {"Ni50Fe27Ga23|11|1": [record]}
        monkeypatch.setattr(builder_ui, "_AnnealingTransitionReviewDialog", _FakeDialog)

        section._open_transition_review()

        assert opened == [[record]]
    finally:
        section._shutdown_background_threads()
        section.close()


def test_annealing_section_preview_uses_row_diameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    record = MeasurementRecord(
        path=Path("Ni50Fe27Ga23 11_1 1000mA.txt"),
        metadata=MeasurementMetadata(
            composition_token="Ni50Fe27Ga23",
            draw_x=11,
            piece_y=1,
            setpoint_mA=1000.0,
            alt_variant=False,
            measurement_id="Ni50Fe27Ga23 11_1 1000mA",
            file_name="Ni50Fe27Ga23 11_1 1000mA.txt",
            relpath="Ni50Fe27Ga23 11_1 1000mA.txt",
            timestamp_mtime_utc="2026-06-16T00:00:00+00:00",
        ),
        dataframe=pd.DataFrame({"I_mA": [0.0, 100.0], "R_ohm": [120.0, 180.0]}),
        sanity_ok=True,
        sanity_error=None,
    )
    captured: list[float | None] = []

    def _fake_render(
        _record: MeasurementRecord | None,
        _logger: logging.Logger,
        **kwargs: object,
    ) -> QtGui.QPixmap:
        captured.append(kwargs.get("wire_diameter_um"))  # type: ignore[arg-type]
        return QtGui.QPixmap(10, 10)

    monkeypatch.setattr(builder_ui, "_render_measurement_pixmap", _fake_render)
    try:
        section._record_groups = {"Ni50Fe27Ga23|11|1": [record]}
        row = pd.Series(
            {
                "_group_key": "Ni50Fe27Ga23|11|1",
                builder_ui.MICROSCOPE_D_COLUMN: 20.0,
            }
        )

        pixmap = section._preview_decoration(row, builder_ui.ANNEALING_HIGH_GRAPH_COLUMN)

        assert isinstance(pixmap, QtGui.QPixmap)
        assert captured == [20.0]
    finally:
        section._shutdown_background_threads()
        section.close()


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
        for source_name, name in [
            ("Praha", "Ni50Fe27Ga23 5-4 s1 loop.txt"),
            ("Kosice", "Ni50Fe27Ga23 6-4 s1 loop.txt"),
        ]:
            path = tmp_path / source_name / name
            path.parent.mkdir(parents=True, exist_ok=True)
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
        assert builder_ui.SOURCE_LABEL_COLUMN in frame.columns
        assert set(frame[builder_ui.SOURCE_LABEL_COLUMN].tolist()) == {"Praha", "Ko\u0161ice"}

        section.search_edit.setText("6/4")
        assert section.table_view.model().rowCount() == 1

        section.search_edit.clear()
        index = section.source_filter_combo.findText("Ko\u0161ice")
        assert index >= 0
        section.source_filter_combo.setCurrentIndex(index)
        assert section.table_view.model().rowCount() == 1
        source_row = section._search_proxy.map_row_to_source(0)
        assert source_row is not None
        assert section.model.frame().iloc[source_row]["Microwire"] == "6/4"
    finally:
        section._shutdown_background_threads()
        section.close()


def test_assemble_preview_filters_by_source_label() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection(
        {},
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    "_sources": ["G:/My Drive/1 Projects/Praha/mini DMA/run01"],
                },
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/3",
                    "_sources": [
                        "G:/Shared drives/Charakterizacia mikrodrotov/shape memory database/Kosice/run01"
                    ],
                },
            ]
        )

        assembly._update_preview(frame)

        assert builder_ui.SOURCE_LABEL_COLUMN in assembly.preview_model.frame().columns
        index = assembly.source_filter_combo.findText("Ko\u0161ice")
        assert index >= 0
        assembly.source_filter_combo.setCurrentIndex(index)
        assert assembly.preview_model.rowCount() == 1
        assert assembly.preview_model.frame().iloc[0]["Microwire"] == "12/3"
    finally:
        assembly.close()


def test_source_label_detection_recognizes_kosice_path_families() -> None:
    frame = pd.DataFrame(
        [
            {"_sources": ["G:/data/Kosice/Ni44Fe27Ga23Cu3Co3_1-1.dat"]},
            {"_sources": ["G:/data/Ko\u0161ice/Ni44Fe27Ga23Cu3Co3_1-2.dat"]},
            {"_sources": ["G:/Shared/shape memory database/run01"]},
            {"_sources": ["G:/Shared/databaza mikrodrotov/run02"]},
        ]
    )

    with_labels = builder_ui._with_source_label_column(frame)

    assert builder_ui.SOURCE_LABEL_COLUMN in with_labels.columns
    assert set(with_labels[builder_ui.SOURCE_LABEL_COLUMN].tolist()) == {"Ko\u0161ice"}


def test_annealing_section_migrates_low_graph_column_to_other_annealing() -> None:
    _ensure_qapp()
    section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "5/4",
                    "Graph — 1000 mA": "high.png",
                    "Graph — low mA": "low.png",
                    "_group_key": "Ni50Fe27Ga23|5|4",
                    "_sources": [],
                }
            ]
        )
        section.data.table = frame
        section.model.set_frame(frame)

        section._sanitize_graph_columns()

        migrated = section.model.frame()
        assert "Graph — low mA" not in migrated.columns
        assert builder_ui.ANNEALING_OTHER_GRAPH_COLUMN in migrated.columns
        assert migrated.loc[0, builder_ui.ANNEALING_OTHER_GRAPH_COLUMN] == "low.png"
    finally:
        section._shutdown_background_threads()
        section.close()


def test_annealing_high_preview_shows_missing_1000_placeholder_with_available_setpoints() -> None:
    _ensure_qapp()
    section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    try:
        def record(setpoint_mA: float, file_name: str) -> builder_ui.MeasurementRecord:
            return builder_ui.MeasurementRecord(
                path=Path(file_name),
                metadata=core.MeasurementMetadata(
                    composition_token="Ni42Fe27Ga23Cu4Co4",
                    draw_x=1,
                    piece_y=2,
                    setpoint_mA=setpoint_mA,
                    alt_variant=False,
                    file_name=file_name,
                    measurement_id=file_name,
                    relpath=file_name,
                    timestamp_mtime_utc="2026-06-18T00:00:00+00:00",
                ),
                dataframe=pd.DataFrame(
                    {
                        "I_mA": [10.0, 20.0, 30.0],
                        "R_ohm": [35.0, 40.0, 45.0],
                    }
                ),
                sanity_ok=True,
                sanity_error=None,
            )

        key = "Ni42Fe27Ga23Cu4Co4|1|2"
        records = [
            record(60.0, "Ni42Fe27Ga23Cu4Co4 1_2 60mA.txt"),
            record(120.0, "Ni42Fe27Ga23Cu4Co4 1_2 120mA.txt"),
        ]
        section._record_groups = {key: records}
        row = pd.Series(
            {
                "Composition": "Ni42Fe27Ga23Cu4Co4",
                "Microwire": "1/2",
                builder_ui.ANNEALING_HIGH_GRAPH_COLUMN: None,
                builder_ui.ANNEALING_OTHER_GRAPH_COLUMN: None,
                "_group_key": key,
                "_sources": [],
            }
        )

        pixmap = section._preview_decoration(row, builder_ui.ANNEALING_HIGH_GRAPH_COLUMN)
        tooltip = section._tooltip_for_cell(row, builder_ui.ANNEALING_HIGH_GRAPH_COLUMN)

        assert isinstance(pixmap, QtGui.QPixmap)
        assert not pixmap.isNull()
        assert tooltip is not None
        assert "No exact 1000 mA measurement available" in tooltip
        assert "60, 120 mA" in tooltip
    finally:
        section._shutdown_background_threads()
        section.close()


def test_imported_rows_gain_imported_source_label_in_assemble_preview() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_args: None)
    try:
        measured = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "5/4",
                    "Data source": "Measured",
                }
            ]
        )
        assembly._imported_rows = {
            "Ni50Fe27Ga23|5|4": {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "5/4",
                "Notes": "imported note",
            }
        }

        merged = assembly._merge_imported_rows(measured)
        with_labels = builder_ui._with_source_label_column(merged)

        assert with_labels.loc[0, "Data source"] == "Measured + Imported"
        assert with_labels.loc[0, builder_ui.SOURCE_LABEL_COLUMN] == "Imported"
    finally:
        assembly.close()


def test_builder_window_import_menu_uses_source_wording(qtbot) -> None:
    _ensure_qapp()
    window = builder_ui.BuilderWindow()
    qtbot.addWidget(window)
    try:
        assert window._show_imported_action is not None
        assert window._separate_imported_action is not None
        assert window._remove_imported_action is not None
        assert window._show_imported_action.text() == "Show imported workbook rows"
        assert window._separate_imported_action.text() == "Separate imported source rows"
        assert window._remove_imported_action.text() == "Remove imported workbook data"
        assert window._imported_item is not None
        assert window._imported_item.text(0) == builder_ui.ANNEALING_IMPORTED_ITEM_LABEL
    finally:
        window.close()


def test_annealing_section_merges_both_legacy_graph_columns_into_other_annealing() -> None:
    _ensure_qapp()
    section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "5/4",
                    "Graph — other mA": "other.png",
                    "Graph — low mA": "low.png",
                    "_group_key": "Ni50Fe27Ga23|5|4",
                    "_sources": [],
                }
            ]
        )
        section.data.table = frame
        section.model.set_frame(frame)

        section._sanitize_graph_columns()

        migrated = section.model.frame()
        merged = migrated.loc[0, builder_ui.ANNEALING_OTHER_GRAPH_COLUMN]
        assert "Graph — other mA" not in migrated.columns
        assert "Graph — low mA" not in migrated.columns
        assert merged == ["other.png", "low.png"]
    finally:
        section._shutdown_background_threads()
        section.close()


def test_video_review_completion_ignores_blank_notes() -> None:
    section = builder_ui.VideoSection.__new__(builder_ui.VideoSection)
    section._overrides = {}
    section._fabrication_lookup_cache = {}
    source_key = (str(Path("C:/videos/source.mkv")),)
    section._video_source_status_cache = {source_key: True}
    section._editable_columns = lambda: {"Notes", builder_ui.VIDEO_END_LENGTH_COLUMN}  # type: ignore[method-assign]

    row = pd.Series(
        {
            "Composition": "Ni50Fe27Ga23",
            "Draw": 6,
            "Piece": 2,
            "_group_key": "Ni50Fe27Ga23|6|2",
            "Notes": "",
            builder_ui.VIDEO_END_LENGTH_COLUMN: 42.0,
            "_sources": list(source_key),
        }
    )

    assert section._completion_state(row, "Notes") is None
    assert section._row_has_review_gaps(row) is False


def test_single_asset_reference_unwraps_single_item_lists() -> None:
    assert core._single_asset_reference("other.png") == "other.png"
    assert core._single_asset_reference(["other.png"]) == "other.png"
    assert core._single_asset_reference(["one.png", "two.png"]) is None


def test_collapse_asset_references_returns_scalar_for_single_item() -> None:
    assert core._collapse_asset_references(["other.png"]) == "other.png"
    assert core._collapse_asset_references(["other.png", "other.png"]) == "other.png"
    assert core._collapse_asset_references(["one.png", "two.png"]) == ["one.png", "two.png"]


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


def test_microscope_expected_keys_ignore_annealing_filename_notes(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.MicroscopeSection(logging.getLogger("test"), lambda *_args: None)
    annealing_store = builder_ui.MiniDatabaseStore("annealing")
    original_records = annealing_store.load_payload("annealing_records")

    def _record(file_name: str) -> MeasurementRecord:
        return MeasurementRecord(
            path=tmp_path / file_name,
            metadata=core.MeasurementMetadata(
                composition_token="Ni46Fe27Ga23Cu2Co2",
                draw_x=2,
                piece_y=1,
                setpoint_mA=1000,
                alt_variant=False,
                measurement_id=file_name,
                file_name=file_name,
                relpath=file_name,
                timestamp_mtime_utc="2026-06-25T00:00:00+00:00",
            ),
            dataframe=pd.DataFrame({"I_A": [0.1], "V_V": [0.2], "R_ohm": [2.0]}),
            sanity_ok=True,
            sanity_error=None,
        )

    try:
        annealing_store.save_payload(
            "annealing_records",
            [
                _record("Ni46Fe27Ga23Cu2Co2-2_1-No1.dat"),
                _record("Ni46Fe27Ga23Cu2Co2_2-1_noload.dat"),
            ],
        )

        expected = section._expected_microwire_keys()

        assert ("Ni46Fe27Ga23Cu2Co2", 2, 1, None) in expected
        assert ("Ni46Fe27Ga23Cu2Co2", 2, 1, "No1") not in expected
        assert ("Ni46Fe27Ga23Cu2Co2", 2, 1, "noload") not in expected
    finally:
        annealing_store.save_payload("annealing_records", original_records)
        section._shutdown_background_threads()
        section.close()


def test_microscope_expected_keys_preserve_other_end_suffix(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.MicroscopeSection(logging.getLogger("test"), lambda *_args: None)
    annealing_store = builder_ui.MiniDatabaseStore("annealing")
    original_records = annealing_store.load_payload("annealing_records")
    try:
        record = MeasurementRecord(
            path=tmp_path / "Ni50Fe27Ga23 10-5oe.txt",
            metadata=core.MeasurementMetadata(
                composition_token="Ni50Fe27Ga23",
                draw_x=10,
                piece_y=5,
                setpoint_mA=1000,
                alt_variant=False,
                measurement_id="other-end",
                file_name="Ni50Fe27Ga23 10-5oe.txt",
                relpath="Ni50Fe27Ga23 10-5oe.txt",
                timestamp_mtime_utc="2026-06-25T00:00:00+00:00",
            ),
            dataframe=pd.DataFrame({"I_A": [0.1], "V_V": [0.2], "R_ohm": [2.0]}),
            sanity_ok=True,
            sanity_error=None,
        )
        annealing_store.save_payload("annealing_records", [record])

        assert ("Ni50Fe27Ga23", 10, 5, "oe") in section._expected_microwire_keys()
    finally:
        annealing_store.save_payload("annealing_records", original_records)
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


def test_build_database_includes_mini_dma_strain_and_break_summary(tmp_path: Path) -> None:
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
        dataframe=pd.DataFrame({"I_A": [0.1], "V_V": [0.2], "R_ohm": [2.0], "I_mA": [100.0]}),
        sanity_ok=True,
        sanity_error=0.0,
    )
    mini_dma = MiniDmaRecord(
        path=tmp_path / "Ni50Fe27Ga23 5_4 mini_run01",
        sample="Ni50Fe27Ga23 5_4",
        data=pd.DataFrame({"current_mA": [20.0]}),
        key=("Ni50Fe27Ga23", 5, 4, None),
        label="mini_run01",
        strain_summary=("50 MPa / 1.46 g: 0.1% @ 20 mA",),
        transition_summary=("50 MPa / 1.46 g: As 30 mA, Af 70 mA, Ms 65 mA, Mf 25 mA",),
        break_summary="400 MPa / 11.69 g @ 35 mA",
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
        mini_dma_records=[mini_dma],
        fabrication_index=FabricationIndex(),
        skip_exports=True,
    )

    row = result.dataframe.iloc[0]
    assert row[MINI_DMA_COLUMN] == ["mini_run01"]
    assert row[MINI_DMA_STRAIN_COLUMN] == ["50 MPa / 1.46 g: 0.1% @ 20 mA"]
    assert row[MINI_DMA_TRANSITION_COLUMN] == [
        "50 MPa / 1.46 g: As 30 mA, Af 70 mA, Ms 65 mA, Mf 25 mA"
    ]
    assert row[MINI_DMA_BREAK_COLUMN] == ["400 MPa / 11.69 g @ 35 mA"]


def test_build_database_keeps_mini_dma_only_rows_with_column_filter(tmp_path: Path) -> None:
    mini_dma = MiniDmaRecord(
        path=tmp_path / "Ni50Fe27Ga23 12_2 with glass iso-stress_run08",
        sample="Ni50Fe27Ga23 12_2 with glass",
        data=pd.DataFrame({"current_mA": [20.0]}),
        key=("Ni50Fe27Ga23", 12, 2, None),
        label="with glass iso-stress_run08",
        strain_summary=("50 MPa / 1.46 g: 5.16% @ 15 mA",),
    )

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[],
            output_dir=tmp_path,
            make_plots=False,
            export_formats=(),
            plot_backends=(),
            column_filter=("Composition", "Microwire", MINI_DMA_STRAIN_COLUMN),
        ),
        measurement_records=[],
        mini_dma_records=[mini_dma],
        fabrication_index=FabricationIndex(),
        skip_exports=True,
    )

    assert list(result.dataframe.columns) == [
        "Composition",
        "Microwire",
        MINI_DMA_STRAIN_COLUMN,
    ]
    row = result.dataframe.iloc[0]
    assert row["Composition"] == "Ni50Fe27Ga23"
    assert row["Microwire"] == "12/2"
    assert row[MINI_DMA_STRAIN_COLUMN] == ["50 MPa / 1.46 g: 5.16% @ 15 mA"]


def test_build_database_recomputes_stale_mini_dma_strain_summary(tmp_path: Path) -> None:
    mini_dma = MiniDmaRecord(
        path=tmp_path / "missing_raw_run",
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(
            {
                "elapsed_s": [0.0, 1.0, 2.0],
                "automation_phase": ["current", "current", "current"],
                "automation_target_value": [50.0, 50.0, 50.0],
                "current_mA": [10.0, 30.0, 20.0],
                "strain_pct": [5.0, 3.0, 6.0],
                "resistance_ohm": [100.0, 100.0, 100.0],
            }
        ),
        key=("Ni50Fe27Ga23", 12, 2, None),
        label="saved run",
        strain_summary=("50 MPa: 0% @ 30 mA",),
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
        measurement_records=[],
        mini_dma_records=[mini_dma],
        fabrication_index=FabricationIndex(),
        skip_exports=True,
    )

    row = result.dataframe.iloc[0]
    assert row["Microwire"] == "12/2"
    assert row[MINI_DMA_STRAIN_COLUMN] == ["50 MPa: 3% @ 20 mA"]


def test_mini_dma_section_frame_accepts_multiple_break_summaries(tmp_path: Path) -> None:
    first = MiniDmaRecord(
        path=tmp_path / "Ni50Fe27Ga23 5_4 run01",
        sample="Ni50Fe27Ga23 5_4",
        data=pd.DataFrame({"current_mA": [20.0]}),
        key=("Ni50Fe27Ga23", 5, 4, None),
        label="run01",
        break_summary="400 MPa / 11.69 g @ 35 mA",
    )
    second = MiniDmaRecord(
        path=tmp_path / "Ni50Fe27Ga23 5_4 run02",
        sample="Ni50Fe27Ga23 5_4",
        data=pd.DataFrame({"current_mA": [30.0]}),
        key=("Ni50Fe27Ga23", 5, 4, None),
        label="run02",
        break_summary="450 MPa / 13.15 g @ 42 mA",
    )

    frame = builder_ui._mini_dma_records_to_frame([first, second])

    assert len(frame) == 1
    assert frame.iloc[0][MINI_DMA_BREAK_COLUMN] == [
        "400 MPa / 11.69 g @ 35 mA",
        "450 MPa / 13.15 g @ 42 mA",
    ]


def test_mini_dma_section_collect_candidates_uses_only_report_measurements(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    good = tmp_path / "Ni50Fe27Ga23 12_2 iso-stress_run01" / "measurement.csv"
    sidecar = tmp_path / "Ni50Fe27Ga23 12_2 iso-stress_run01" / "control_trace_replay.csv"
    archived = tmp_path / "archive" / "Ni50Fe27Ga23 12_2 old_run" / "measurement.csv"
    automated = tmp_path / "automated" / "Ni50Fe27Ga23 12_2 draft_run" / "measurement.csv"
    control_test = tmp_path / "automated_control_tests" / "probe_run" / "measurement.csv"
    history = tmp_path / "automation_history" / "campaign" / "history_run" / "measurement.csv"
    tests_fixture = tmp_path / "tests" / "fixture_run" / "measurement.csv"
    cache_fixture = tmp_path / "cache" / "scratch_run" / "measurement.csv"
    invalid = tmp_path / "Ni50Fe27Ga23 12_3 notes" / "measurement.csv"
    mini_dma_csv = (
        "elapsed_s,automation_phase,automation_target_value,plateau_index,"
        "strain_pct,resistance_ohm,current_measured_mA,current_set_mA\n"
        "0,current,50,1,0.1,100,10,10\n"
    )
    for path in (
        good,
        sidecar,
        archived,
        automated,
        control_test,
        history,
        tests_fixture,
        cache_fixture,
        invalid,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
    good.write_text(mini_dma_csv, encoding="utf-8")
    for path in (archived, automated, control_test, history, tests_fixture, cache_fixture):
        path.write_text(mini_dma_csv, encoding="utf-8")
    sidecar.write_text("placeholder", encoding="utf-8")
    invalid.write_text("not,a,mini,dma\n1,2,3,4\n", encoding="utf-8")
    section = builder_ui.MiniDmaSection(logging.getLogger("test"), lambda *_args: None)
    try:
        section.data = MiniDatabaseData(sources=[str(tmp_path)])

        candidates = section._collect_candidates()
    finally:
        section.close()
        section.deleteLater()

    assert candidates == [good]


def _write_reportable_mini_dma_measurement(
    path: Path,
    *,
    sample_name: str,
    created_utc: str,
    session_state: str = "finished",
    finished_utc: str | None = "2026-06-01 00:10:00",
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    metadata = {
        "sample_name": sample_name,
        "created_utc": created_utc,
        "session_state": session_state,
        "initial_length_mm": 10.0,
    }
    if finished_utc is not None:
        metadata["finished_utc"] = finished_utc
    (path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (path / "measurement.csv").write_text(
        "\n".join(
            [
                "elapsed_s,automation_phase,automation_target_value,plateau_index,strain_pct,stress_mpa,resistance_ohm,current_set_mA,current_measured_mA,position_mm",
                "0,current,50,1,0.0,50,100,1,1,0.0",
                "1,current,50,1,0.1,50,101,20,20,0.1",
            ]
        ),
        encoding="utf-8",
    )
    return path / "measurement.csv"


def test_mini_dma_reportable_gating_skips_older_finished_when_newest_unfinished(
    tmp_path: Path,
) -> None:
    old_finished = _write_reportable_mini_dma_measurement(
        tmp_path / "Ni50Fe27Ga23 12_2 run01",
        sample_name="Ni50Fe27Ga23 12/2",
        created_utc="2026-06-01 09:00:00",
        finished_utc="2026-06-01 09:20:00",
    )
    newest_running = _write_reportable_mini_dma_measurement(
        tmp_path / "Ni50Fe27Ga23 12_2 run02",
        sample_name="Ni50Fe27Ga23 12/2",
        created_utc="2026-06-01 10:00:00",
        session_state="running",
        finished_utc=None,
    )

    accepted, audit = builder_ui.MiniDmaSection.reportable_measurements(
        [old_finished, newest_running],
        sources=[str(tmp_path)],
    )

    assert accepted == []
    assert len(audit) == 2
    assert all(not row["reportable"] for row in audit)
    assert all("newest active run is unfinished" in row["skip_reason"] for row in audit)


def test_mini_dma_reportable_gating_imports_finished_group_when_newest_finished(
    tmp_path: Path,
) -> None:
    first = _write_reportable_mini_dma_measurement(
        tmp_path / "Ni50Fe27Ga23 12_2 run01",
        sample_name="Ni50Fe27Ga23 12/2",
        created_utc="2026-06-01 09:00:00",
        finished_utc="2026-06-01 09:20:00",
    )
    newest = _write_reportable_mini_dma_measurement(
        tmp_path / "Ni50Fe27Ga23 12_2 run02",
        sample_name="Ni50Fe27Ga23 12/2",
        created_utc="2026-06-01 10:00:00",
        finished_utc="2026-06-01 10:20:00",
    )

    accepted, audit = builder_ui.MiniDmaSection.reportable_measurements(
        [first, newest],
        sources=[str(tmp_path)],
    )

    assert accepted == [first, newest]
    assert {row["reportable"] for row in audit} == {True}


def test_mini_dma_reportable_gating_keeps_variant_groups_separate(tmp_path: Path) -> None:
    base_old = _write_reportable_mini_dma_measurement(
        tmp_path / "Ni50Fe27Ga23 12_2 run01",
        sample_name="Ni50Fe27Ga23 12/2",
        created_utc="2026-06-01 09:00:00",
        finished_utc="2026-06-01 09:20:00",
    )
    base_running = _write_reportable_mini_dma_measurement(
        tmp_path / "Ni50Fe27Ga23 12_2 run02",
        sample_name="Ni50Fe27Ga23 12/2",
        created_utc="2026-06-01 10:00:00",
        session_state="running",
        finished_utc=None,
    )
    glass_finished = _write_reportable_mini_dma_measurement(
        tmp_path / "Ni50Fe27Ga23 12_2 glass run01",
        sample_name="Ni50Fe27Ga23 12/2 glass",
        created_utc="2026-06-01 09:30:00",
        finished_utc="2026-06-01 09:50:00",
    )

    accepted, audit = builder_ui.MiniDmaSection.reportable_measurements(
        [base_old, base_running, glass_finished],
        sources=[str(tmp_path)],
    )

    assert accepted == [glass_finished]
    blocked = [row for row in audit if row["sample"] == "Ni50Fe27Ga23 12_2"]
    assert blocked and all("newest active run is unfinished" in row["skip_reason"] for row in blocked)
    glass = [row for row in audit if row["sample"] == "Ni50Fe27Ga23 12_2 glass"]
    assert len(glass) == 1 and glass[0]["reportable"] is True


def test_mini_dma_section_process_reports_gated_skip_reason(tmp_path: Path) -> None:
    _ensure_qapp()
    old_finished = _write_reportable_mini_dma_measurement(
        tmp_path / "Ni50Fe27Ga23 12_2 run01",
        sample_name="Ni50Fe27Ga23 12/2",
        created_utc="2026-06-01 09:00:00",
        finished_utc="2026-06-01 09:20:00",
    )
    newest_running = _write_reportable_mini_dma_measurement(
        tmp_path / "Ni50Fe27Ga23 12_2 run02",
        sample_name="Ni50Fe27Ga23 12/2",
        created_utc="2026-06-01 10:00:00",
        session_state="running",
        finished_utc=None,
    )
    section = builder_ui.MiniDmaSection(logging.getLogger("test"), lambda *_args: None)
    try:
        section.data = MiniDatabaseData(sources=[str(tmp_path)])

        result = section.process([old_finished, newest_running])
    finally:
        section.close()
        section.deleteLater()

    assert result.table.empty
    reportability = result.extra["mini_dma_reportability"]
    assert len(reportability) == 2
    assert all("newest active run is unfinished" in row["skip_reason"] for row in reportability)


def _sample_mini_dma_record() -> MiniDmaRecord:
    run_path = Path("sample_data/mini dma/Ni50Fe27Ga23 12_2 test_run32")
    return MiniDmaRecord(
        path=run_path,
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(),
        key=("Ni50Fe27Ga23", 12, 2, None),
        label=run_path.name,
    )


def _write_iso_current_mini_dma_run(tmp_path: Path) -> Path:
    run_path = tmp_path / "Ni50Fe27Ga23 12_2 iso-current"
    run_path.mkdir()
    (run_path / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": "Ni50Fe27Ga23 12_2 iso-current",
                "created_utc": "2026-06-01 11:00:00",
                "session_state": "finished",
                "finished_utc": "2026-06-01 11:10:00",
                "initial_length_mm": 58.0,
                "wire_diameter_mm": 0.02,
                "recipe": {"recipe_mode": "constant_current_strain_sweep"},
            }
        ),
        encoding="utf-8",
    )
    rows: list[dict[str, float | str | int]] = []
    for current_mA, offset in ((20.0, 0.0), (40.0, 0.12)):
        for index, strain_pct in enumerate((0.0, 0.25, 0.5, 0.75), start=1):
            rows.append(
                {
                    "elapsed_s": len(rows),
                    "recipe_mode": "constant_current_strain_sweep",
                    "automation_phase": "target_ramp",
                    "automation_target_value": 0.0,
                    "plateau_index": index,
                    "strain_pct": strain_pct + offset,
                    "current_relative_strain_pct": strain_pct,
                    "current_l0_mm": 58.0,
                    "current_relative_position_mm": strain_pct / 100.0 * 58.0,
                    "stress_mpa": 35.0 + offset * 30.0 + strain_pct * 42.0,
                    "load_g": 1.0 + strain_pct * 2.0 + offset,
                    "resistance_ohm": 100.0 + current_mA,
                    "current_measured_mA": current_mA,
                    "current_set_mA": current_mA,
                }
            )
    pd.DataFrame(rows).to_csv(run_path / "measurement.csv", index=False)
    return run_path


def _write_current_sweep_mini_dma_run_with_iso_columns(tmp_path: Path) -> Path:
    run_path = tmp_path / "Ni50Fe27Ga23 12_2 iso-stress_run01"
    run_path.mkdir()
    (run_path / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": "Ni50Fe27Ga23 12_2 iso-stress",
                "created_utc": "2026-06-01 11:00:00",
                "session_state": "finished",
                "finished_utc": "2026-06-01 11:10:00",
                "initial_length_mm": 58.0,
                "wire_diameter_mm": 0.02,
                "recipe": {"recipe_mode": "current_sweep_stress"},
            }
        ),
        encoding="utf-8",
    )
    rows: list[dict[str, float | str | int]] = []
    for stress_mpa in (50.0, 100.0):
        for current_mA, strain_pct in ((10.0, 0.1), (20.0, 0.3), (30.0, 0.6)):
            rows.append(
                {
                    "elapsed_s": len(rows),
                    "recipe_mode": "current_sweep_stress",
                    "automation_phase": "current",
                    "automation_target_value": stress_mpa,
                    "plateau_index": int(stress_mpa),
                    "strain_pct": strain_pct,
                    "current_relative_strain_pct": strain_pct,
                    "current_l0_mm": 58.0,
                    "current_relative_position_mm": strain_pct / 100.0 * 58.0,
                    "stress_mpa": stress_mpa,
                    "load_g": stress_mpa / 50.0,
                    "resistance_ohm": 100.0 + current_mA,
                    "current_measured_mA": current_mA,
                    "current_set_mA": current_mA,
                }
            )
    pd.DataFrame(rows).to_csv(run_path / "measurement.csv", index=False)
    return run_path


def _write_iso_strain_mini_dma_run(tmp_path: Path) -> Path:
    run_path = tmp_path / "Ni50Fe27Ga23 11_1 iso-strain_run09"
    run_path.mkdir()
    (run_path / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": "Ni50Fe27Ga23 11_1 iso-strain",
                "created_utc": "2026-06-01 11:00:00",
                "session_state": "finished",
                "finished_utc": "2026-06-01 11:10:00",
                "initial_length_mm": 58.0,
                "wire_diameter_mm": 0.02,
                "recipe": {"recipe_mode": "current_sweep_strain"},
            }
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "elapsed_s": index,
                "recipe_mode": "current_sweep_strain",
                "automation_phase": "current",
                "automation_target_value": 0.25,
                "plateau_index": 1,
                "strain_pct": 0.25,
                "stress_mpa": 30.0 + index,
                "load_g": 1.0 + index / 10.0,
                "resistance_ohm": 100.0 + index,
                "current_measured_mA": 10.0 + index,
                "current_set_mA": 10.0 + index,
            }
            for index in range(4)
        ]
    ).to_csv(run_path / "measurement.csv", index=False)
    return run_path


def test_mini_dma_transition_review_entries_use_real_stress_targets() -> None:
    entries = builder_ui._mini_dma_transition_review_entries(  # noqa: SLF001
        [_sample_mini_dma_record()],
        logging.getLogger("test"),
    )

    assert len(entries) >= 9
    labels = {entry.target_label for entry in entries}
    assert "50 MPa / 1.46 g" in labels
    assert "350 MPa / 10.23 g" in labels
    statuses = {entry.status for entry in entries}
    assert "accepted" in statuses
    assert statuses & {"partial", "rejected"}


def test_mini_dma_transition_review_dialog_loads_selected_run_lazily() -> None:
    _ensure_qapp()
    dialog = builder_ui._MiniDmaTransitionReviewDialog(  # noqa: SLF001
        [_sample_mini_dma_record()],
        logging.getLogger("test"),
    )
    try:
        assert dialog.tree.topLevelItemCount() == 1
        assert not dialog._entries_by_run  # noqa: SLF001
        run_key = dialog._runs[0].key  # noqa: SLF001
        entries = builder_ui._mini_dma_transition_review_entries(  # noqa: SLF001
            [_sample_mini_dma_record()],
            logging.getLogger("test"),
        )
        dialog._handle_load_finished(  # noqa: SLF001
            builder_ui._MiniDmaTransitionReviewLoadResult(run_key, entries)  # noqa: SLF001
        )
        assert dialog._entries_by_run  # noqa: SLF001
        assert dialog._visible_refs  # noqa: SLF001
        dialog.rejected_only_check.setChecked(True)
        assert dialog.rejected_only_check.isChecked()
        assert not dialog.accepted_only_check.isChecked()
        assert all(
            dialog._entries_by_run[key][index].status == "rejected"  # noqa: SLF001
            for key, index in dialog._visible_refs  # noqa: SLF001
        )
        dialog.show_fit_lines_check.setChecked(False)
        dialog.show_markers_check.setChecked(False)
        dialog._redraw_current()  # noqa: SLF001
        assert dialog.canvas is not None
    finally:
        dialog.close()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_transition_review_actions_persist_target_status() -> None:
    _ensure_qapp()
    stored: dict[str, dict[str, object]] = {}

    def _set_review(record_id: str, payload: dict[str, object]) -> None:
        stored[record_id] = dict(payload)

    dialog = builder_ui._MiniDmaTransitionReviewDialog(  # noqa: SLF001
        [_sample_mini_dma_record()],
        logging.getLogger("test"),
        review_provider=lambda: stored,
        review_setter=_set_review,
    )
    try:
        run_key = dialog._runs[0].key  # noqa: SLF001
        entries = builder_ui._mini_dma_transition_review_entries(  # noqa: SLF001
            [_sample_mini_dma_record()],
            logging.getLogger("test"),
        )
        dialog._handle_load_finished(  # noqa: SLF001
            builder_ui._MiniDmaTransitionReviewLoadResult(run_key, entries)  # noqa: SLF001
        )
        first_ref = dialog._visible_refs[0]  # noqa: SLF001
        dialog.tree.setCurrentItem(dialog._tree_items[first_ref])  # noqa: SLF001

        dialog._set_current_review(  # noqa: SLF001
            builder_ui.MINI_DMA_REVIEW_STATUS_NO_TRANSITION,
            move_next=False,
        )

        assert len(stored) == 1
        payload = next(iter(stored.values()))
        assert payload["status"] == builder_ui.MINI_DMA_REVIEW_STATUS_NO_TRANSITION
        assert payload["target_label"] == entries[first_ref[1]].target_label
        assert dialog._tree_items[first_ref].text(1) == "No transition"  # noqa: SLF001
        expected = builder_ui._transition_review_status_color("No transition")  # noqa: SLF001
        assert dialog._tree_items[first_ref].foreground(1).color().name() == expected  # noqa: SLF001
    finally:
        dialog.close()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_transition_review_status_update_skips_tree_rebuild_without_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    stored: dict[str, dict[str, object]] = {}

    def _set_review(record_id: str, payload: dict[str, object]) -> None:
        stored[record_id] = dict(payload)

    dialog = builder_ui._MiniDmaTransitionReviewDialog(  # noqa: SLF001
        [_sample_mini_dma_record()],
        logging.getLogger("test"),
        review_provider=lambda: stored,
        review_setter=_set_review,
    )
    try:
        run_key = dialog._runs[0].key  # noqa: SLF001
        entries = builder_ui._mini_dma_transition_review_entries(  # noqa: SLF001
            [_sample_mini_dma_record()],
            logging.getLogger("test"),
        )
        dialog._handle_load_finished(  # noqa: SLF001
            builder_ui._MiniDmaTransitionReviewLoadResult(run_key, entries)  # noqa: SLF001
        )
        first_ref = dialog._visible_refs[0]  # noqa: SLF001
        dialog.tree.setCurrentItem(dialog._tree_items[first_ref])  # noqa: SLF001
        refresh_calls = 0

        def _count_refresh_tree() -> None:
            nonlocal refresh_calls
            refresh_calls += 1

        monkeypatch.setattr(dialog, "_refresh_tree", _count_refresh_tree)

        dialog._set_current_review(  # noqa: SLF001
            builder_ui.MINI_DMA_REVIEW_STATUS_NO_TRANSITION,
            move_next=False,
        )

        assert refresh_calls == 0
        assert dialog._tree_items[first_ref].text(1) == "No transition"  # noqa: SLF001
        assert "Done 1" in dialog.status_label.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_transition_review_counts_unloaded_saved_reviews() -> None:
    _ensure_qapp()
    record = _sample_mini_dma_record()
    entries = builder_ui._mini_dma_transition_review_entries(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
    )
    entry = entries[0]
    record_id = builder_ui._mini_dma_review_record_id(record, entry.target_label)  # noqa: SLF001
    stored: dict[str, dict[str, object]] = {
        record_id: {
            "status": builder_ui.MINI_DMA_REVIEW_STATUS_NO_TRANSITION,
            "sample": entry.sample,
            "run_label": entry.run_label,
            "target_label": entry.target_label,
            "auto_status": entry.status,
        }
    }

    dialog = builder_ui._MiniDmaTransitionReviewDialog(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
        review_provider=lambda: stored,
    )
    try:
        assert not dialog._entries_by_run  # noqa: SLF001
        assert "Done 1" in dialog.status_label.text()
        run_key = dialog._runs[0].key  # noqa: SLF001
        run_item = dialog._run_items[run_key]  # noqa: SLF001
        assert run_item.text(1) == "1 saved review(s)"
        assert run_item.childCount() == 1
        assert run_item.child(0).text(0) == entry.target_label
        assert run_item.child(0).text(1) == "No transition"
    finally:
        dialog.close()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_transition_review_next_unreviewed_loads_unloaded_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    dialog = builder_ui._MiniDmaTransitionReviewDialog(  # noqa: SLF001
        [_sample_mini_dma_record()],
        logging.getLogger("test"),
    )
    try:
        loaded_requests: list[tuple[str, bool]] = []

        def _record_load(key: str, *, select_first: bool = False) -> None:
            loaded_requests.append((key, select_first))

        monkeypatch.setattr(dialog, "_ensure_run_loaded", _record_load)
        dialog._select_next_unreviewed()  # noqa: SLF001

        assert loaded_requests == [(dialog._runs[0].key, False)]  # noqa: SLF001
        assert dialog._pending_select_unreviewed is True  # noqa: SLF001
    finally:
        dialog.close()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_transition_review_click_places_manual_target_values() -> None:
    _ensure_qapp()
    stored: dict[str, dict[str, object]] = {}

    def _set_review(record_id: str, payload: dict[str, object]) -> None:
        stored[record_id] = dict(payload)

    record = _sample_mini_dma_record()
    entries = builder_ui._mini_dma_transition_review_entries(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
    )
    assert len(entries) >= 2
    dialog = builder_ui._MiniDmaTransitionReviewDialog(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
        review_provider=lambda: stored,
        review_setter=_set_review,
    )
    try:
        run_key = dialog._runs[0].key  # noqa: SLF001
        dialog._handle_load_finished(  # noqa: SLF001
            builder_ui._MiniDmaTransitionReviewLoadResult(run_key, entries)  # noqa: SLF001
        )
        first_ref = dialog._visible_refs[0]  # noqa: SLF001
        second_ref = dialog._visible_refs[1]  # noqa: SLF001
        first_entry = dialog._entries_by_run[first_ref[0]][first_ref[1]]  # noqa: SLF001
        second_entry = dialog._entries_by_run[second_ref[0]][second_ref[1]]  # noqa: SLF001
        dialog.tree.setCurrentItem(dialog._tree_items[first_ref])  # noqa: SLF001
        dialog.transition_controls.set_target("Af")  # noqa: SLF001
        dialog._handle_canvas_click(SimpleNamespace(button=1, xdata=42.5))  # noqa: SLF001

        first_id = builder_ui._mini_dma_review_record_id(  # noqa: SLF001
            first_entry.record,
            first_entry.target_label,
        )
        second_id = builder_ui._mini_dma_review_record_id(  # noqa: SLF001
            second_entry.record,
            second_entry.target_label,
        )
        assert first_id in stored
        assert second_id not in stored
        payload = stored[first_id]
        assert payload["status"] == builder_ui.MINI_DMA_REVIEW_STATUS_ACCEPTED
        assert payload["manual_values_mA"] == {"Af": pytest.approx(42.5)}
        assert payload["values"]["Af"] == pytest.approx(42.5)
        assert dialog._tree_items[first_ref].text(1) == "Manual adjusted"  # noqa: SLF001

        dialog.tree.setCurrentItem(dialog._tree_items[second_ref])  # noqa: SLF001
        assert dialog.transition_controls.values()["Af"] is None  # noqa: SLF001
    finally:
        dialog.close()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_transition_review_manual_override_keeps_auto_values() -> None:
    _ensure_qapp()
    stored: dict[str, dict[str, object]] = {}

    def _set_review(record_id: str, payload: dict[str, object]) -> None:
        stored[record_id] = dict(payload)

    record = _sample_mini_dma_record()
    entries = builder_ui._mini_dma_transition_review_entries(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
    )
    entry_index, entry = next(
        (
            (index, candidate)
            for index, candidate in enumerate(entries)
            if builder_ui._mini_dma_transition_values_from_summary(candidate.target_summary)  # noqa: SLF001
        ),
        (None, None),
    )
    assert entry is not None
    auto_values = builder_ui._mini_dma_transition_values_from_summary(entry.target_summary)  # noqa: SLF001
    assert auto_values

    dialog = builder_ui._MiniDmaTransitionReviewDialog(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
        review_provider=lambda: stored,
        review_setter=_set_review,
    )
    try:
        run_key = dialog._runs[0].key  # noqa: SLF001
        dialog._handle_load_finished(  # noqa: SLF001
            builder_ui._MiniDmaTransitionReviewLoadResult(run_key, entries)  # noqa: SLF001
        )
        ref = (run_key, int(entry_index))
        dialog.tree.setCurrentItem(dialog._tree_items[ref])  # noqa: SLF001
        dialog.transition_controls.set_target("As")  # noqa: SLF001
        dialog._handle_canvas_click(SimpleNamespace(button=1, xdata=31.25))  # noqa: SLF001

        record_id = builder_ui._mini_dma_review_record_id(entry.record, entry.target_label)  # noqa: SLF001
        payload = stored[record_id]
        assert payload["manual_values_mA"] == {"As": pytest.approx(31.25)}
        assert payload["values"]["As"] == pytest.approx(31.25)
        for label, value in auto_values.items():
            if label == "As":
                continue
            assert payload["values"][label] == pytest.approx(value)
    finally:
        dialog.close()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_transition_review_drag_marker_persists_on_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    stored: dict[str, dict[str, object]] = {}

    def _set_review(record_id: str, payload: dict[str, object]) -> None:
        stored[record_id] = dict(payload)

    record = _sample_mini_dma_record()
    entries = builder_ui._mini_dma_transition_review_entries(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
    )
    entry_index, entry = next(
        (
            (index, candidate)
            for index, candidate in enumerate(entries)
            if builder_ui._mini_dma_transition_values_from_summary(candidate.target_summary).get("Af") is not None  # noqa: SLF001
        ),
        (None, None),
    )
    assert entry is not None
    auto_values = builder_ui._mini_dma_transition_values_from_summary(entry.target_summary)  # noqa: SLF001
    start_value = auto_values["Af"]
    dialog = builder_ui._MiniDmaTransitionReviewDialog(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
        review_provider=lambda: stored,
        review_setter=_set_review,
    )
    redraws = 0
    original = dialog._plot_entry  # noqa: SLF001

    def _counting_plot(*args: object, **kwargs: object) -> object:
        nonlocal redraws
        redraws += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(dialog, "_plot_entry", _counting_plot)
    try:
        run_key = dialog._runs[0].key  # noqa: SLF001
        dialog._handle_load_finished(  # noqa: SLF001
            builder_ui._MiniDmaTransitionReviewLoadResult(run_key, entries)  # noqa: SLF001
        )
        ref = (run_key, int(entry_index))
        dialog.tree.setCurrentItem(dialog._tree_items[ref])  # noqa: SLF001
        axis = dialog.figure.axes[0]
        redraws = 0

        dialog._handle_canvas_press(  # noqa: SLF001
            SimpleNamespace(button=1, xdata=start_value, canvas=dialog.canvas, inaxes=axis)
        )
        dialog._handle_canvas_motion(  # noqa: SLF001
            SimpleNamespace(button=1, xdata=start_value + 4.0, canvas=dialog.canvas, inaxes=axis)
        )
        assert redraws == 0
        dialog._handle_canvas_release(  # noqa: SLF001
            SimpleNamespace(button=1, xdata=start_value + 4.0, canvas=dialog.canvas, inaxes=axis)
        )

        record_id = builder_ui._mini_dma_review_record_id(entry.record, entry.target_label)  # noqa: SLF001
        assert stored[record_id]["manual_values_mA"]["Af"] == pytest.approx(start_value + 4.0, abs=1e-3)
        assert stored[record_id]["values"]["Af"] == pytest.approx(start_value + 4.0, abs=1e-3)
        assert redraws == 1
    finally:
        dialog.close()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_transition_review_clear_selected_suppresses_only_that_label() -> None:
    _ensure_qapp()
    stored: dict[str, dict[str, object]] = {}

    def _set_review(record_id: str, payload: dict[str, object]) -> None:
        stored[record_id] = dict(payload)

    record = _sample_mini_dma_record()
    entries = builder_ui._mini_dma_transition_review_entries(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
    )
    entry_index, entry = next(
        (
            (index, candidate)
            for index, candidate in enumerate(entries)
            if {"As", "Af", "Ms", "Mf"}.issubset(
                builder_ui._mini_dma_transition_values_from_summary(candidate.target_summary)  # noqa: SLF001
            )
        ),
        (None, None),
    )
    assert entry is not None
    dialog = builder_ui._MiniDmaTransitionReviewDialog(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
        review_provider=lambda: stored,
        review_setter=_set_review,
    )
    try:
        run_key = dialog._runs[0].key  # noqa: SLF001
        dialog._handle_load_finished(  # noqa: SLF001
            builder_ui._MiniDmaTransitionReviewLoadResult(run_key, entries)  # noqa: SLF001
        )
        ref = (run_key, int(entry_index))
        dialog.tree.setCurrentItem(dialog._tree_items[ref])  # noqa: SLF001
        for label in ("Ms", "Mf"):
            dialog.transition_controls.set_target(label)
            dialog.transition_controls._clear_selected()  # noqa: SLF001

        record_id = builder_ui._mini_dma_review_record_id(entry.record, entry.target_label)  # noqa: SLF001
        payload = stored[record_id]
        assert payload["status"] == builder_ui.MINI_DMA_REVIEW_STATUS_ACCEPTED
        assert set(payload["cleared_labels"]) == {"Ms", "Mf"}
        assert set(payload["values"]) == {"As", "Af"}
        assert dialog._tree_items[ref].text(1) == "Manual adjusted"  # noqa: SLF001

        section = builder_ui.MiniDmaSection.__new__(builder_ui.MiniDmaSection)
        section._all_mini_dma_records = [record]  # noqa: SLF001
        section._transition_reviews = stored  # noqa: SLF001
        reviewed = section.records_with_reviewed_transitions([record])
        assert len(reviewed) == 1
        summary = "\n".join(reviewed[0].transition_summary)
        assert "As" in summary
        assert "Af" in summary
        assert "Ms" not in summary
        assert "Mf" not in summary
    finally:
        dialog.close()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_transition_review_skips_unsupported_run_modes(tmp_path: Path) -> None:
    iso_current = MiniDmaRecord(
        path=_write_iso_current_mini_dma_run(tmp_path),
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(),
        key=("Ni50Fe27Ga23", 12, 2, None),
        label="iso-current",
    )
    iso_strain = MiniDmaRecord(
        path=_write_iso_strain_mini_dma_run(tmp_path),
        sample="Ni50Fe27Ga23 11_1",
        data=pd.DataFrame(),
        key=("Ni50Fe27Ga23", 11, 1, None),
        label="iso-strain",
    )

    entries = builder_ui._mini_dma_transition_review_entries(  # noqa: SLF001
        [iso_current, iso_strain],
        logging.getLogger("test"),
    )

    assert entries == []


def test_dma_transitions_view_lists_run_target_rows() -> None:
    _ensure_qapp()
    record = _sample_mini_dma_record()
    entries = builder_ui._mini_dma_transition_review_entries(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
    )
    assert entries
    reviewed_entry = entries[0]
    review_id = builder_ui._mini_dma_review_record_id(reviewed_entry.record, reviewed_entry.target_label)  # noqa: SLF001
    fake_mini_dma_section = SimpleNamespace(
        logger=logging.getLogger("test"),
        _all_mini_dma_records=[record],
        _refresh_record_groups=lambda: None,
        transition_reviews_snapshot=lambda: {
            review_id: {
                "status": builder_ui.MINI_DMA_REVIEW_STATUS_ACCEPTED,
                "sample": reviewed_entry.sample,
                "run_label": reviewed_entry.run_label,
                "target_label": reviewed_entry.target_label,
                "values": builder_ui._mini_dma_transition_values_from_summary(reviewed_entry.target_summary),  # noqa: SLF001
            }
        },
        _open_transition_review=lambda: None,
    )
    section = builder_ui.DmaTransitionsSection(fake_mini_dma_section)
    try:
        section.refresh_data()
        table = section.summary_table

        assert table.rowCount() == len(entries)
        assert table.item(0, 0).text() == "Ni50Fe27Ga23"
        assert table.item(0, 1).text() == "12/2"
        assert table.item(0, 2).text() == record.label
        assert table.item(0, 3).text() == reviewed_entry.target_label
        assert table.item(0, 4).text() == "Accepted"
        assert "total=" in table.item(0, 6).text()
        assert "TMA target row(s)" in section.status_label.text()
    finally:
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_transition_review_worker_finishes_for_unsupported_run(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    record = MiniDmaRecord(
        path=_write_iso_strain_mini_dma_run(tmp_path),
        sample="Ni50Fe27Ga23 11_1",
        data=pd.DataFrame(),
        key=("Ni50Fe27Ga23", 11, 1, None),
        label="iso-strain",
    )
    worker = builder_ui._MiniDmaTransitionReviewLoadWorker(  # noqa: SLF001
        "run-key",
        record,
        logging.getLogger("test"),
    )
    results: list[object] = []
    worker.finished.connect(results.append)

    worker.run()

    assert len(results) == 1
    result = results[0]
    assert isinstance(result, builder_ui._MiniDmaTransitionReviewLoadResult)  # noqa: SLF001
    assert result.entries == []
    assert not result.error


def test_mini_dma_transition_review_dialog_shows_empty_state_for_unsupported_run(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    record = MiniDmaRecord(
        path=_write_iso_current_mini_dma_run(tmp_path),
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(),
        key=("Ni50Fe27Ga23", 12, 2, None),
        label="iso-current",
    )
    dialog = builder_ui._MiniDmaTransitionReviewDialog(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
    )
    try:
        run_key = dialog._runs[0].key  # noqa: SLF001
        dialog._current_run_key = run_key  # noqa: SLF001
        dialog._pending_select_key = run_key  # noqa: SLF001
        dialog._handle_load_finished(  # noqa: SLF001
            builder_ui._MiniDmaTransitionReviewLoadResult(run_key, [])  # noqa: SLF001
        )

        assert "No supported current-sweep transition targets" in dialog.empty_label.text()
        assert dialog._entries_by_run[run_key] == []  # noqa: SLF001
    finally:
        dialog.close()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_section_opens_transition_review_for_all_records_without_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    section = builder_ui.MiniDmaSection(logging.getLogger("test"), lambda *_args: None)
    opened: list[MiniDmaRecord] = []

    class FakeReviewDialog:
        def __init__(
            self,
            records: list[MiniDmaRecord],
            _logger: logging.Logger,
            _parent: QtWidgets.QWidget | None = None,
            **_kwargs: object,
        ) -> None:
            opened.extend(records)

        def exec(self) -> int:
            return int(QtWidgets.QDialog.DialogCode.Accepted)

    try:
        record = _sample_mini_dma_record()
        section._record_groups = {record.sample: [record]}  # noqa: SLF001
        monkeypatch.setattr(builder_ui, "_MiniDmaTransitionReviewDialog", FakeReviewDialog)

        section._open_transition_review()  # noqa: SLF001

        assert opened == [record]
    finally:
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_section_cleans_and_snapshots_transition_reviews() -> None:
    _ensure_qapp()
    section = builder_ui.MiniDmaSection(logging.getLogger("test"), lambda *_args: None)
    try:
        section.set_transition_review_for_target(
            "run::50 MPa",
            {
                "status": builder_ui.MINI_DMA_REVIEW_STATUS_ACCEPTED,
                "sample": "Ni50Fe27Ga23 12_2",
                "target_label": "50 MPa",
                "values": {"As": 30.0, "Af": 70.0, "bad": "ignored"},
            },
        )

        snapshot = section.transition_reviews_snapshot()

        assert snapshot["run::50 MPa"]["status"] == builder_ui.MINI_DMA_REVIEW_STATUS_ACCEPTED
        assert snapshot["run::50 MPa"]["values"] == {"As": 30.0, "Af": 70.0}
    finally:
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_section_applies_reviewed_transition_values_to_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "store"))
    section = builder_ui.MiniDmaSection(logging.getLogger("test"), lambda *_args: None)
    try:
        record = MiniDmaRecord(
            path=tmp_path / "Ni50Fe27Ga23 12_2 iso-stress_run01",
            sample="Ni50Fe27Ga23 12_2",
            data=pd.DataFrame(),
            key=("Ni50Fe27Ga23", 12, 2),
            label="iso-stress_run01",
            transition_summary=("50 MPa / 1.46 g: As 30 mA, Af 70 mA",),
        )
        section._all_mini_dma_records = [record]  # noqa: SLF001
        record_id = builder_ui._mini_dma_review_record_id(record, "50 MPa / 1.46 g")  # noqa: SLF001
        section.set_transition_review_for_target(
            record_id,
            {
                "status": builder_ui.MINI_DMA_REVIEW_STATUS_ACCEPTED,
                "sample": record.sample,
                "run_label": record.label or "",
                "target_label": "50 MPa / 1.46 g",
                "auto_values_mA": {"As": 30.0, "Af": 70.0},
                "manual_values_mA": {"Af": 64.0},
                "values": {"As": 30.0, "Af": 64.0},
            },
        )

        reviewed = section.records_with_reviewed_transitions([record])

        assert reviewed[0].transition_summary == ("50 MPa / 1.46 g: As 30 mA, Af 64 mA",)
        section._apply_transition_reviews_to_table()  # noqa: SLF001
        assert "Af 64 mA" in str(section.model.frame().iloc[0][MINI_DMA_TRANSITION_COLUMN])
    finally:
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_transition_review_defers_heavy_store_and_table_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "store"))
    section = builder_ui.MiniDmaSection(logging.getLogger("test"), lambda *_args: None)
    store_calls = 0
    table_calls = 0

    def _fake_store() -> None:
        nonlocal store_calls
        store_calls += 1

    def _fake_apply() -> None:
        nonlocal table_calls
        table_calls += 1

    section._store_transition_reviews = _fake_store  # type: ignore[method-assign]  # noqa: SLF001
    section._apply_transition_reviews_to_table = _fake_apply  # type: ignore[method-assign]  # noqa: SLF001
    try:
        section.set_transition_review_for_target(
            "run::50 MPa",
            {
                "status": builder_ui.MINI_DMA_REVIEW_STATUS_NO_TRANSITION,
                "sample": "Ni50Fe27Ga23 12_2",
                "target_label": "50 MPa",
            },
        )

        assert section.transition_reviews_snapshot()["run::50 MPa"]["status"] == builder_ui.MINI_DMA_REVIEW_STATUS_NO_TRANSITION
        assert store_calls == 0
        assert table_calls == 0
    finally:
        section._transition_review_store_timer.stop()  # noqa: SLF001
        section._transition_table_apply_timer.stop()  # noqa: SLF001
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_transition_temps_counts_auto_estimated_unannotated_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcessor:
        def estimate_transition_points(self, _data: pd.DataFrame) -> dict[str, float]:
            return {"As": 30.0, "Af": 70.0}

    section = builder_ui.TransitionTempsSection.__new__(builder_ui.TransitionTempsSection)
    section.logger = logging.getLogger("test")
    section._record_groups = {  # noqa: SLF001
        "Ni50Fe27Ga23|12|2|": [
            VsmTemperatureScanRecord(
                path=Path("scan.txt"),
                sample="Ni50Fe27Ga23 12_2",
                data=pd.DataFrame({"Temperature": [0.0, 1.0], "Signal": [1.0, 2.0]}),
                key=("Ni50Fe27Ga23", 12, 2),
                label="scan",
            )
        ],
        "Ni50Fe27Ga23|12|3|": [
            VsmTemperatureScanRecord(
                path=Path("scan2.txt"),
                sample="Ni50Fe27Ga23 12_3",
                data=pd.DataFrame({"Temperature": [0.0, 1.0], "Signal": [1.0, 2.0]}),
                key=("Ni50Fe27Ga23", 12, 3),
                label="scan2",
            )
        ],
    }
    monkeypatch.setattr(builder_ui, "_get_vsm_temp_processor", lambda _logger: FakeProcessor())
    frame = pd.DataFrame(
        {
            "_group_key": ["Ni50Fe27Ga23|12|2|", "Ni50Fe27Ga23|12|3|"],
        }
    )
    manual = pd.Series([False, True], index=frame.index)

    assert section._auto_estimated_transition_count(  # noqa: SLF001
        frame,
        manually_annotated=manual,
    ) == 1


def test_transition_temps_populates_from_vsm_scan_memory_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "store"))
    scan = builder_ui.VsmTemperatureScanRecord(
        path=tmp_path / "Ni50Fe27Ga23 12_2 temp_scan.txt",
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(
            {
                "temperature": [0.0, 20.0, 40.0],
                "field": [10000.0, 10000.0, 10000.0],
                "signal": [0.0, 1.0, 2.0],
                "section_index": [0, 0, 0],
            }
        ),
        key=("Ni50Fe27Ga23", 12, 2),
        label="temp scan",
    )
    fake_vsm_section = SimpleNamespace(
        store=SimpleNamespace(load_payload=lambda _name: None),
        _all_records=[scan],
        _record_groups_by_key={},
        _hidden_paths=set(),
    )
    section = builder_ui.TransitionTempsSection(
        fake_vsm_section,
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section.refresh_data()
        frame = section.model.frame()

        assert len(frame.index) == 1
        assert frame.iloc[0]["Composition"] == "Ni50Fe27Ga23"
        assert frame.iloc[0]["Microwire"] == "12/2"
        assert frame.iloc[0]["_group_key"] == "Ni50Fe27Ga23|12|2"
        assert section.status_label.text().startswith("0 of 1 scan row")
    finally:
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_transition_temps_uses_one_row_per_vsm_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "store"))

    def _scan(label: str) -> builder_ui.VsmTemperatureScanRecord:
        return builder_ui.VsmTemperatureScanRecord(
            path=tmp_path / f"Ni50Fe27Ga23 12_2 {label}.txt",
            sample="Ni50Fe27Ga23 12_2",
            data=pd.DataFrame(
                {
                    "temperature": [0.0, 20.0, 40.0],
                    "field": [10000.0, 10000.0, 10000.0],
                    "signal": [0.0, 1.0, 2.0],
                    "section_index": [0, 0, 0],
                }
            ),
            key=("Ni50Fe27Ga23", 12, 2),
            label=label,
        )

    scans = [_scan("scan-a"), _scan("scan-b")]
    fake_vsm_section = SimpleNamespace(
        store=SimpleNamespace(load_payload=lambda _name: None),
        _all_records=scans,
        _record_groups_by_key={},
        _hidden_paths=set(),
    )
    section = builder_ui.TransitionTempsSection(
        fake_vsm_section,
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section.refresh_data()
        frame = section.model.frame()

        assert len(frame.index) == 2
        assert frame["_group_key"].tolist() == ["Ni50Fe27Ga23|12|2", "Ni50Fe27Ga23|12|2"]
        assert frame["_record_id"].nunique() == 2
        assert frame["Graph"].tolist() == ["scan-a", "scan-b"]
        assert "scan row(s)" in section.status_label.text()

        captured: list[list[builder_ui.VsmTemperatureScanRecord]] = []
        assert section._preview_panel is not None  # noqa: SLF001
        original_update = section._preview_panel.update_selection  # noqa: SLF001

        def _capture_update(title: str, records: list[builder_ui.VsmTemperatureScanRecord], values: dict[str, float], *args: object, **kwargs: object) -> None:
            captured.append(list(records))
            original_update(title, records, values, *args, **kwargs)

        section._preview_panel.update_selection = _capture_update  # type: ignore[method-assign]  # noqa: SLF001
        proxy_index = section._search_proxy.mapFromSource(section.model.index(1, 0))  # noqa: SLF001
        section.table_view.setCurrentIndex(proxy_index)
        section.table_view.selectRow(proxy_index.row())
        section._update_preview()  # noqa: SLF001

        assert captured[-1] == [scans[1]]
    finally:
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_transition_temps_preview_click_persists_manual_temperature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "store"))
    scan = builder_ui.VsmTemperatureScanRecord(
        path=tmp_path / "Ni50Fe27Ga23 12_2 temp_scan.txt",
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(
            {
                "temperature": [0.0, 20.0, 40.0],
                "field": [10000.0, 10000.0, 10000.0],
                "signal": [0.0, 1.0, 0.5],
                "section_index": [0, 0, 0],
            }
        ),
        key=("Ni50Fe27Ga23", 12, 2),
        label="temp scan",
    )
    fake_vsm_section = SimpleNamespace(
        store=SimpleNamespace(load_payload=lambda _name: None),
        _all_records=[scan],
        _record_groups_by_key={},
        _hidden_paths=set(),
    )
    section = builder_ui.TransitionTempsSection(
        fake_vsm_section,
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section._auto_values_for_records = lambda _records: {"As": 12.0, "Af": 32.0}  # type: ignore[method-assign]  # noqa: SLF001
        section.refresh_data()
        source_index = section.model.index(0, 0)
        proxy_index = section._search_proxy.mapFromSource(source_index)  # noqa: SLF001
        section.table_view.setCurrentIndex(proxy_index)
        section.table_view.selectRow(proxy_index.row())
        section._preview_panel.set_target("Af")  # noqa: SLF001
        section._preview_panel._handle_click(SimpleNamespace(button=1, xdata=37.5))  # noqa: SLF001

        key = "Ni50Fe27Ga23|12|2"
        snapshot = section.transition_points_snapshot()
        frame = section.model.frame()
        assert snapshot[key]["Af"] == pytest.approx(37.5)
        review = next(iter(section.transition_reviews_snapshot().values()))
        assert review["status"] == builder_ui.TRANSITION_REVIEW_STATUS_MANUAL_ADJUSTED
        assert review["manual_values_C"] == {"Af": pytest.approx(37.5)}
        assert frame.iloc[0][builder_ui.TRANSITION_TEMP_AF_COLUMN] == pytest.approx(37.5)
        assert frame.iloc[0]["Review status"] == "Manual adjusted"
        assert section._preview_panel._auto_value_labels["As"].text() == "Auto: 12"  # noqa: SLF001
        assert section._preview_panel._value_labels["Af"].text() == "37.5"  # noqa: SLF001
        page = section._preview_panel._tab_widget.currentWidget()  # noqa: SLF001
        canvas = page.layout().itemAt(0).widget()
        text_labels = {text.get_text() for text in canvas.figure.axes[0].texts}
        assert "auto As" in text_labels
        assert "Af" in text_labels
    finally:
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_transition_temps_project_payload_preserves_review_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "store"))
    scan = builder_ui.VsmTemperatureScanRecord(
        path=tmp_path / "Ni50Fe27Ga23 12_2 temp_scan.txt",
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(
            {
                "temperature": [0.0, 20.0, 40.0],
                "field": [10000.0, 10000.0, 10000.0],
                "signal": [0.0, 1.0, 0.5],
                "section_index": [0, 0, 0],
            }
        ),
        key=("Ni50Fe27Ga23", 12, 2),
        label="temp scan",
    )
    fake_vsm_section = SimpleNamespace(
        store=SimpleNamespace(load_payload=lambda _name: None),
        _all_records=[scan],
        _record_groups_by_key={},
        _hidden_paths=set(),
    )
    section = builder_ui.TransitionTempsSection(
        fake_vsm_section,
        logging.getLogger("test"),
        lambda *_args: None,
    )
    restored = builder_ui.TransitionTempsSection(
        fake_vsm_section,
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section._auto_values_for_record = lambda _record: {"As": 12.0, "Af": 32.0}  # type: ignore[method-assign]  # noqa: SLF001
        section.refresh_data()
        source_index = section.model.index(0, 0)
        proxy_index = section._search_proxy.mapFromSource(source_index)  # noqa: SLF001
        section.table_view.setCurrentIndex(proxy_index)
        section.table_view.selectRow(proxy_index.row())
        section._mark_current_scan_no_transition()  # noqa: SLF001

        payload = section.export_project_payload()

        assert payload["section"] == "transition_temps"
        records = payload["extra"][builder_ui.VSM_TRANSITION_REVIEW_EXTRA_KEY]["records"]
        assert len(records) == 1

        restored.import_project_payload(payload)

        restored_reviews = restored.transition_reviews_snapshot()
        assert len(restored_reviews) == 1
        review = next(iter(restored_reviews.values()))
        assert review["status"] == builder_ui.TRANSITION_REVIEW_STATUS_NO_TRANSITION
        assert restored.model.frame().iloc[0]["Review status"] == "No transition"
    finally:
        section.close()
        restored.close()
        section.deleteLater()
        restored.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_transition_temps_preview_drag_marker_and_clear_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "store"))
    scan = builder_ui.VsmTemperatureScanRecord(
        path=tmp_path / "Ni50Fe27Ga23 12_2 temp_scan.txt",
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(
            {
                "temperature": [0.0, 20.0, 40.0],
                "field": [10000.0, 10000.0, 10000.0],
                "signal": [0.0, 1.0, 0.5],
                "section_index": [0, 0, 0],
            }
        ),
        key=("Ni50Fe27Ga23", 12, 2),
        label="temp scan",
    )
    fake_vsm_section = SimpleNamespace(
        store=SimpleNamespace(load_payload=lambda _name: None),
        _all_records=[scan],
        _record_groups_by_key={},
        _hidden_paths=set(),
    )
    section = builder_ui.TransitionTempsSection(
        fake_vsm_section,
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section._auto_values_for_record = lambda _record: {"As": 12.0, "Af": 32.0, "Ms": 8.0, "Mf": 4.0}  # type: ignore[method-assign]  # noqa: SLF001
        section.refresh_data()
        source_index = section.model.index(0, 0)
        proxy_index = section._search_proxy.mapFromSource(source_index)  # noqa: SLF001
        section.table_view.setCurrentIndex(proxy_index)
        section.table_view.selectRow(proxy_index.row())
        panel = section._preview_panel  # noqa: SLF001
        assert panel is not None
        section._accept_current_scan_and_next()  # noqa: SLF001
        panel._emit_clear_label("Ms")  # noqa: SLF001
        panel._emit_clear_label("Mf")  # noqa: SLF001
        page = panel._tab_widget.currentWidget()  # noqa: SLF001
        canvas = page.layout().itemAt(0).widget()
        axis = canvas.figure.axes[0]
        panel._handle_button_press(SimpleNamespace(button=1, xdata=12.0, canvas=canvas, inaxes=axis))  # noqa: SLF001
        panel._handle_motion(SimpleNamespace(button=1, xdata=15.5, canvas=canvas, inaxes=axis))  # noqa: SLF001
        panel._handle_button_release(SimpleNamespace(button=1, xdata=15.5, canvas=canvas, inaxes=axis))  # noqa: SLF001

        review = next(iter(section.transition_reviews_snapshot().values()))
        assert review["status"] == builder_ui.TRANSITION_REVIEW_STATUS_MANUAL_ADJUSTED
        assert review["manual_values_C"] == {"As": pytest.approx(15.5), "Af": pytest.approx(32.0)}
        assert "Ms" not in review["final_values_C"]
        assert "Mf" not in review["final_values_C"]
        assert "#4ade80" in panel.review_status_label.styleSheet()  # noqa: SLF001
    finally:
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_transition_temps_queue_marks_no_transition_and_shows_scan_counts(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    scan1 = builder_ui.VsmTemperatureScanRecord(
        path=tmp_path / "Ni50Fe27Ga23 12_2 scan-a.txt",
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(
            {
                "temperature": [0.0, 20.0, 40.0],
                "field": [10000.0, 10000.0, 10000.0],
                "signal": [0.0, 1.0, 0.5],
                "section_index": [0, 0, 0],
            }
        ),
        key=("Ni50Fe27Ga23", 12, 2),
        label="scan-a",
    )
    scan2 = builder_ui.VsmTemperatureScanRecord(
        path=tmp_path / "Ni50Fe27Ga23 12_2 scan-b.txt",
        sample="Ni50Fe27Ga23 12_2",
        data=scan1.data.copy(),
        key=("Ni50Fe27Ga23", 12, 2),
        label="scan-b",
    )
    fake_vsm_section = SimpleNamespace(
        store=SimpleNamespace(load_payload=lambda _name: None),
        _all_records=[scan1, scan2],
        _record_groups_by_key={},
        _hidden_paths=set(),
    )
    section = builder_ui.TransitionTempsSection(
        fake_vsm_section,
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section._auto_values_for_record = lambda _record: {"As": 12.0, "Af": 32.0}  # type: ignore[method-assign]  # noqa: SLF001
        section.refresh_data()
        source_index = section.model.index(0, 0)
        proxy_index = section._search_proxy.mapFromSource(source_index)  # noqa: SLF001
        section.table_view.setCurrentIndex(proxy_index)
        section.table_view.selectRow(proxy_index.row())

        frame = section.model.frame()
        assert len(frame.index) == 2
        assert frame.iloc[0]["Scans"] == 1
        assert frame.iloc[0]["Review status"] == "Auto candidates"
        assert "Total 2" in section._preview_panel.review_counts_label.text()  # noqa: SLF001

        section._mark_current_scan_no_transition()  # noqa: SLF001

        reviews = section.transition_reviews_snapshot()
        assert len(reviews) == 1
        review = next(iter(reviews.values()))
        assert review["status"] == builder_ui.TRANSITION_REVIEW_STATUS_NO_TRANSITION
        assert review["included"] is False
        frame = section.model.frame()
        assert frame.iloc[0]["No transition"] == 1
        assert frame.iloc[0]["Unreviewed"] == 0
        assert frame.iloc[0]["Review status"] == "No transition"
        assert frame.iloc[1]["Unreviewed"] == 1
        assert frame.iloc[1]["Review status"] == "Auto candidates"
    finally:
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_mini_dma_preview_items_render_real_thumbnail() -> None:
    _ensure_qapp()
    items = builder_ui._mini_dma_preview_items(  # noqa: SLF001
        [_sample_mini_dma_record()],
        logging.getLogger("test"),
        width_px=builder_ui.ANNEALING_GRAPH_WIDTH,
        height_px=builder_ui.ANNEALING_GRAPH_HEIGHT,
    )

    assert len(items) == 1
    assert not items[0].pixmap.isNull()
    assert items[0].pixmap.width() > 0
    assert items[0].pixmap.height() > 0


def test_mini_dma_preview_items_route_current_sweep_and_iso_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    calls: list[str] = []

    def _figure(kind: str):
        calls.append(kind)
        figure = builder_ui.Figure(figsize=(2, 1))
        axis = figure.add_subplot(111)
        axis.plot([0, 1], [0, 1])
        return figure

    monkeypatch.setattr(
        builder_ui.mini_dma_core,
        "make_strain_current_figure",
        lambda *_args, **_kwargs: _figure("strain-current"),
    )
    monkeypatch.setattr(
        builder_ui.mini_dma_core,
        "make_iso_current_figure",
        lambda *_args, **_kwargs: _figure("iso-current"),
    )
    current_sweep = MiniDmaRecord(
        path=_write_current_sweep_mini_dma_run_with_iso_columns(tmp_path),
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(),
        key=("Ni50Fe27Ga23", 12, 2, None),
        label="current-sweep",
    )
    iso_current = MiniDmaRecord(
        path=_write_iso_current_mini_dma_run(tmp_path),
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(),
        key=("Ni50Fe27Ga23", 12, 2, None),
        label="iso-current",
    )

    items = builder_ui._mini_dma_preview_items(  # noqa: SLF001
        [current_sweep, iso_current],
        logging.getLogger("test"),
        width_px=builder_ui.ANNEALING_GRAPH_WIDTH,
        height_px=builder_ui.ANNEALING_GRAPH_HEIGHT,
    )

    assert len(items) == 2
    assert calls == ["strain-current", "iso-current"]


def test_mini_dma_preview_items_skip_insufficient_sweeps_without_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _ensure_qapp()
    run_path = tmp_path / "Ni50Fe27Ga23 12_2 short_run"
    run_path.mkdir()
    pd.DataFrame(
        {
            "elapsed_s": [0.0],
            "automation_phase": ["current"],
            "automation_target_value": [50.0],
            "plateau_index": [1],
            "strain_pct": [0.0],
            "resistance_ohm": [100.0],
            "current_measured_mA": [10.0],
            "current_set_mA": [10.0],
        }
    ).to_csv(run_path / "measurement.csv", index=False)
    record = MiniDmaRecord(
        path=run_path,
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(),
        key=("Ni50Fe27Ga23", 12, 2, None),
        label=run_path.name,
    )
    logger = logging.getLogger("test.mini_dma_preview")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        items = builder_ui._mini_dma_preview_items(  # noqa: SLF001
            [record],
            logger,
            width_px=builder_ui.ANNEALING_GRAPH_WIDTH,
            height_px=builder_ui.ANNEALING_GRAPH_HEIGHT,
        )

    assert items == []
    assert not caplog.records


def test_mini_dma_preview_items_render_iso_current_without_current_sweep_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _ensure_qapp()
    run_path = _write_iso_current_mini_dma_run(tmp_path)
    record = MiniDmaRecord(
        path=run_path,
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(),
        key=("Ni50Fe27Ga23", 12, 2, None),
        label=run_path.name,
    )
    logger = logging.getLogger("test.mini_dma_iso_current_preview")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        items = builder_ui._mini_dma_preview_items(  # noqa: SLF001
            [record],
            logger,
            width_px=builder_ui.ANNEALING_GRAPH_WIDTH,
            height_px=builder_ui.ANNEALING_GRAPH_HEIGHT,
        )

    assert len(items) == 1
    assert not items[0].pixmap.isNull()
    assert not caplog.records


def test_mini_dma_section_process_accepts_iso_current_without_sweep_summary_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _ensure_qapp()
    run_path = _write_iso_current_mini_dma_run(tmp_path)
    logger = logging.getLogger("test.mini_dma_iso_current_process")
    section = builder_ui.MiniDmaSection(logger, lambda *_args: None)
    try:
        section.data = MiniDatabaseData(sources=[str(tmp_path)])
        with caplog.at_level(logging.ERROR, logger=logger.name):
            result = section.process([run_path / "measurement.csv"])
    finally:
        section.close()
        section.deleteLater()

    assert len(result.payloads["mini_dma_records"]) == 1
    assert not result.table.empty
    assert not caplog.records


def test_mini_dma_section_preview_decoration_uses_side_by_side_cached_graph_pixmap() -> None:
    _ensure_qapp()
    section = builder_ui.MiniDmaSection(logging.getLogger("test"), lambda *_args: None)
    try:
        record = _sample_mini_dma_record()
        second = MiniDmaRecord(
            path=record.path,
            sample=record.sample,
            data=record.data,
            key=record.key,
            label=f"{record.label} duplicate",
        )
        section._record_groups = {record.sample: [record, second]}  # noqa: SLF001
        section._record_groups_by_key = {  # noqa: SLF001
            "Ni50Fe27Ga23|12|2": [record, second],
        }
        section._preview_group_count = 2  # noqa: SLF001
        section._update_preview_icon_size()  # noqa: SLF001
        row = pd.Series(
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                "_sample": record.sample,
                builder_ui.MINI_DMA_COLUMN: [record.label],
            }
        )

        pixmap = section._preview_decoration(row, builder_ui.MINI_DMA_COLUMN)  # noqa: SLF001
        cached = section._preview_decoration(row, builder_ui.MINI_DMA_COLUMN)  # noqa: SLF001

        assert pixmap is not None
        assert cached is pixmap
        assert not pixmap.isNull()
        assert pixmap.width() == section._preview_icon_width()  # noqa: SLF001
        assert pixmap.height() == section._preview_icon_height()  # noqa: SLF001
        assert pixmap.width() > builder_ui.ANNEALING_GRAPH_WIDTH
        assert pixmap.height() == builder_ui.ANNEALING_GRAPH_HEIGHT
    finally:
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_combine_pixmaps_side_by_side_packs_aspect_scaled_images_compactly() -> None:
    _ensure_qapp()
    first = QtGui.QPixmap(100, 50)
    first.fill(QtGui.QColor("#ff0000"))
    second = QtGui.QPixmap(100, 50)
    second.fill(QtGui.QColor("#0000ff"))

    combined = builder_ui._combine_pixmaps_side_by_side(  # noqa: SLF001
        [first, second],
        width_px=500,
        height_px=50,
        spacing=6,
        scale_to_fit=True,
    )

    assert combined is not None
    image = combined.toImage()
    assert image.pixelColor(10, 25) == QtGui.QColor("#ff0000")
    assert image.pixelColor(110, 25) == QtGui.QColor("#0000ff")
    assert image.pixelColor(240, 25).alpha() == 0


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


def test_compare_section_defers_matrix_build_until_visible() -> None:
    _ensure_qapp()
    section = builder_ui.CompareSection({}, logging.getLogger("test"), lambda *_args: None)
    host = QtWidgets.QMainWindow()
    build_calls = 0

    def fake_build_matrix_frame() -> pd.DataFrame:
        nonlocal build_calls
        build_calls += 1
        return pd.DataFrame({"Composition": ["Ni50Fe27Ga23"], "Microwire": ["10/1"]})

    section._build_matrix_frame = fake_build_matrix_frame  # type: ignore[method-assign]
    payload = {
        "columns": ["Composition", "Microwire"],
        "rows": [{"Composition": "Ni50Fe27Ga23", "Microwire": "10/1"}],
        "extra": {"compare_view_mode": "matrix"},
    }
    try:
        section.import_project_payload(payload)
        assert build_calls == 0
        host.setCentralWidget(section)
        host.show()
        QtWidgets.QApplication.processEvents()
        assert build_calls >= 1
    finally:
        host.close()
        section._shutdown_background_threads()
        section.close()


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


def test_filename_parser_extracts_kosice_dat_metadata(tmp_path: Path) -> None:
    path = tmp_path / "Ni44Fe27Ga23Cu3Co3_1-5.dat"
    path.write_text("Cycle\tIset_mA\tIreal_mA\tVoltage_V\tResistance_Ohm\tPower_W\n")
    metadata = _metadata_from_path(path)
    assert metadata.composition_token == "Ni44Fe27Ga23Cu3Co3"
    assert metadata.draw_x == 1
    assert metadata.piece_y == 5
    assert metadata.setpoint_mA is None


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


def test_annealing_loader_respects_text_header_current_milliamp_units(tmp_path: Path) -> None:
    path = tmp_path / "Ni46Fe23Ga23Co8 2_1 30mA for VSM with glass 2loops.txt"
    path.write_text("# Current (mA)\tVoltage (V)\tResistance (Ohm)\n0.1\t22.231\t222310\n")
    df = _load_annealing(path)
    assert df["I_A"].tolist() == pytest.approx([0.0001])
    assert df["I_mA"].tolist() == pytest.approx([0.1])
    ok, error = _resistance_sanity_check(df)
    assert ok is True
    assert error is not None
    assert error < 1e-6


def test_annealing_loader_reads_kosice_cycle_dat(tmp_path: Path) -> None:
    path = tmp_path / "Ni44Fe27Ga23Cu3Co3_1-5.dat"
    path.write_text(
        "\n".join(
            [
                "Cycle\tIset_mA\tIreal_mA\tVoltage_V\tResistance_Ohm\tPower_W",
                "1\t1.00\t1.00\t0.09300\t93.00000\t0.00009",
                "1\t2.00\t1.90\t0.20700\t108.94737\t0.00039",
                "2\t3.00\t2.90\t0.31500\t108.62069\t0.00091",
            ]
        )
        + "\n"
    )
    df = _load_annealing(path)
    assert list(df.columns) == ["I_A", "V_V", "R_ohm", "I_mA", "Cycle"]
    assert df["I_A"].tolist() == pytest.approx([0.001, 0.0019, 0.0029])
    assert df["I_mA"].tolist() == pytest.approx([1.0, 1.9, 2.9])
    assert df["R_ohm"].tolist() == pytest.approx([93.0, 108.94737, 108.62069])
    assert df["Cycle"].tolist() == pytest.approx([1.0, 1.0, 2.0])
    ok, error = _resistance_sanity_check(df)
    assert ok is True
    assert error is not None
    assert error < 1e-6


def test_annealing_loader_reads_kosice_legacy_four_column_dat(tmp_path: Path) -> None:
    path = tmp_path / "Ni46Fe27Ga23Cu2Co2-2_1-No1.dat"
    path.write_text(
        "\n".join(
            [
                "ID",
                "Iset(mA)      Ireal (mA)    Ureal (mA)    R(ohm)",
                " 0.001         0.0013        0.413         317.692307692308",
                " 0.002         0.0022        0.772         350.909090909091",
                " 0.003         0.0032        1.1           343.75",
            ]
        )
        + "\n"
    )
    df = _load_annealing(path)
    assert list(df.columns) == ["I_A", "V_V", "R_ohm", "I_mA"]
    assert df["I_A"].tolist() == pytest.approx([0.0013, 0.0022, 0.0032])
    assert df["I_mA"].tolist() == pytest.approx([1.3, 2.2, 3.2])
    assert df["R_ohm"].tolist() == pytest.approx([317.692307692308, 350.909090909091, 343.75])
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
    assert row[builder_ui.MICROSCOPE_STATUS_COLUMN] == "Image found; enter/review d and D"
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
                    BRITTLE_COLUMN: "brittle",
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


def test_microscope_apply_data_marks_existing_brittle_glass_rows(tmp_path: Path) -> None:
    _ensure_qapp()
    glass_path = tmp_path / "TestCompH 1-1 glass brittle.jpg"
    glass_path.write_bytes(b"test")
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "TestCompH",
                    "Microwire": "1/1",
                    builder_ui.MICROSCOPE_D_COLUMN: None,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: 28.2,
                    "d/D": None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "TestCompH|1|1",
                    "_core_image": None,
                    "_glass_image": str(glass_path),
                    "_images": [str(glass_path)],
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        row = section._row_for_key("TestCompH|1|1")
        assert row is not None
        assert row.get(BRITTLE_COLUMN) == "brittle"
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_prepopulate_preserves_existing_diameters(tmp_path: Path) -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        key = "Ni46Fe23Ga23Co8|1|1"
        existing_core = tmp_path / "existing core.jpg"
        existing_glass = tmp_path / "existing glass.jpg"
        new_core = tmp_path / "Ni46Fe23Ga23Co8 1_1 core.jpg"
        new_glass = tmp_path / "Ni46Fe23Ga23Co8 1_1 glass.jpg"
        for path in (existing_core, existing_glass, new_core, new_glass):
            path.write_bytes(b"test")

        section.apply_data(
            MiniDatabaseData(
                table=pd.DataFrame(
                    [
                        {
                            "Composition": "Ni46Fe23Ga23Co8",
                            "Microwire": "1/1",
                            builder_ui.MICROSCOPE_D_COLUMN: 6.7,
                            builder_ui.MICROSCOPE_CAP_D_COLUMN: 34.4,
                            "d/D": round(6.7 / 34.4, 3),
                            BRITTLE_COLUMN: None,
                            builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                            builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                            "_key": key,
                            "_core_image": str(existing_core),
                            "_glass_image": str(existing_glass),
                            "_images": [str(existing_core), str(existing_glass)],
                        }
                    ]
                )
            )
        )

        section._expected_keys_current = {("Ni46Fe23Ga23Co8", 1, 1, None)}
        section._prepopulate_image_refs([new_core, new_glass])

        row = section._row_for_key(key)
        assert row is not None
        assert row[builder_ui.MICROSCOPE_D_COLUMN] == pytest.approx(6.7)
        assert row[builder_ui.MICROSCOPE_CAP_D_COLUMN] == pytest.approx(34.4)
        assert row["d/D"] == pytest.approx(round(6.7 / 34.4, 3))
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_record_to_row_does_not_duplicate_single_side_measurement() -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        key = ("Ni50Fe27Ga23", 1, 1, None)
        measurement = builder_ui.MicroscopeMeasurements(
            core=[
                builder_ui.MicroscopeDetection(
                    value=15.0,
                    image_path=Path("core.jpg"),
                    source="manual",
                )
            ]
        )
        measurement.core[0].category = "core"

        row = section._record_to_row(key, measurement)

        assert row[builder_ui.MICROSCOPE_D_COLUMN] == pytest.approx(15.0)
        assert pd.isna(row[builder_ui.MICROSCOPE_CAP_D_COLUMN])
        assert pd.isna(row["d/D"])
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_index_to_frame_does_not_duplicate_single_side_measurement() -> None:
    measurement = builder_ui.MicroscopeMeasurements(
        core=[
            builder_ui.MicroscopeDetection(
                value=30.0,
                image_path=Path("core.jpg"),
                source="manual",
            )
        ]
    )
    measurement.core[0].category = "core"

    frame = builder_ui._microscope_index_to_frame(
        {("Ni50Fe27Ga23", 2, 4, None): measurement},
        {},
    )

    assert len(frame.index) == 1
    row = frame.iloc[0]
    assert row[builder_ui.MICROSCOPE_D_COLUMN] == pytest.approx(30.0)
    assert pd.isna(row[builder_ui.MICROSCOPE_CAP_D_COLUMN])
    assert pd.isna(row["d/D"])


def test_microscope_prepopulate_batches_table_updates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        fit_calls: list[str] = []
        monkeypatch.setattr(section, "_auto_fit_columns", lambda: fit_calls.append("fit"))
        section._expected_keys_current = {
            ("Ni50Fe27Ga23", 1, 1, None),
            ("Ni50Fe27Ga23", 1, 2, None),
        }
        paths = [
            tmp_path / "Ni50Fe27Ga23 1_1 core.jpg",
            tmp_path / "Ni50Fe27Ga23 1_1 glass.jpg",
            tmp_path / "Ni50Fe27Ga23 1_2 core.jpg",
            tmp_path / "Ni50Fe27Ga23 1_2 glass.jpg",
        ]
        for path in paths:
            path.write_bytes(b"test")

        section._prepopulate_image_refs(paths)

        assert len(fit_calls) == 1
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_apply_override_skips_autosize_for_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "2/4",
                    builder_ui.MICROSCOPE_D_COLUMN: 30.0,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                    "d/D": None,
                    BRITTLE_COLUMN: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "Ni50Fe27Ga23|2|4",
                    "_core_image": None,
                    "_glass_image": None,
                    "_images": [],
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        section._select_row_for_key("Ni50Fe27Ga23|2|4", builder_ui.MICROSCOPE_D_COLUMN)
        QtWidgets.QApplication.processEvents()
        section.d_edit.setText("31")

        full_resets: list[bool] = []
        saves: list[bool] = []
        original_set_frame = section.model.set_frame

        def _count_set_frame(frame: pd.DataFrame | None) -> None:
            full_resets.append(True)
            original_set_frame(frame)

        monkeypatch.setattr(section.model, "set_frame", _count_set_frame)
        monkeypatch.setattr(section.store, "save", lambda data: saves.append(True))
        monkeypatch.setattr(section, "_advance_to_next_pending", lambda column: None)

        section._apply_override(builder_ui.MICROSCOPE_D_COLUMN)

        updated = section.model.frame()
        assert full_resets == []
        assert saves == []
        assert updated.at[0, builder_ui.MICROSCOPE_D_COLUMN] == pytest.approx(31.0)
        assert section._validated["Ni50Fe27Ga23|2|4"]["d_reviewed"] is True

        section._persist_review_state()
        assert saves == [True]
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_process_merges_existing_rows_when_refresh_scans_new_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        section.reset_to_blank()
        monkeypatch.setattr(section, "_expected_microwire_keys", lambda: set())
        section._expected_keys_current = set()
        old_key = ("TestCompOld", 1, 1, None)
        old_row = {
            "Composition": "TestCompOld",
            "Microwire": "1/1",
            builder_ui.MICROSCOPE_D_COLUMN: 10.0,
            builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
            "d/D": None,
            BRITTLE_COLUMN: None,
            builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
            builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
            "_key": "TestCompOld|1|1",
            "_core_image": str(tmp_path / "old_core.jpg"),
            "_glass_image": None,
            "_images": [str(tmp_path / "old_core.jpg")],
        }
        section.apply_data(
            MiniDatabaseData(
                table=pd.DataFrame([old_row]),
                processed={str(tmp_path / "old_core.jpg"): 1.0},
                extra={
                    "overrides": {"TestCompOld|1|1": {"d": 10.0}},
                    "validated": {
                        "TestCompOld|1|1": {
                            "d": 10.0,
                            "d_reviewed": True,
                        }
                    },
                },
            )
        )
        section._overrides = {"TestCompOld|1|1": {"d": 10.0}}
        section._validated = {
            "TestCompOld|1|1": {
                "d": 10.0,
                "d_reviewed": True,
            }
        }
        new_core = tmp_path / "Ni50Fe27Ga23 2_3 core.jpg"
        new_glass = tmp_path / "Ni50Fe27Ga23 2_3 glass.jpg"
        for path in (new_core, new_glass):
            path.write_bytes(b"test")

        result = section.process([new_core, new_glass])

        assert set(result.payloads["microscope_index"].keys()) == {
            ("TestCompOld", 1, 1, None),
            ("Ni50Fe27Ga23", 2, 3, None),
        }
        old_measurement = result.payloads["microscope_index"][("TestCompOld", 1, 1, None)]
        assert len(old_measurement.core) == 1
        assert len(old_measurement.glass) == 0
        assert old_measurement.best_core() == pytest.approx(10.0)
        keys = set(result.table["_key"].tolist())
        assert keys == {"TestCompOld|1|1", "Ni50Fe27Ga23|2|3"}
        old_result_row = result.table.loc[result.table["_key"] == "TestCompOld|1|1"].iloc[0]
        new_result_row = result.table.loc[result.table["_key"] == "Ni50Fe27Ga23|2|3"].iloc[0]
        assert old_result_row[builder_ui.MICROSCOPE_D_COLUMN] == pytest.approx(10.0)
        assert pd.isna(old_result_row[builder_ui.MICROSCOPE_CAP_D_COLUMN])
        assert pd.isna(new_result_row[builder_ui.MICROSCOPE_D_COLUMN])
        assert pd.isna(new_result_row[builder_ui.MICROSCOPE_CAP_D_COLUMN])
        assert str(new_core) in new_result_row["_images"]
        assert str(new_glass) in new_result_row["_images"]
        assert result.extra["overrides"]["TestCompOld|1|1"]["d"] == pytest.approx(10.0)
        assert "TestCompOld|1|1" in result.extra["validated"]
        assert str(tmp_path / "old_core.jpg") in result.processed
        assert str(new_core) in result.processed
        assert str(new_glass) in result.processed
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_refresh_does_not_mutate_table_before_worker_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "2/4",
                    builder_ui.MICROSCOPE_D_COLUMN: 30.0,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                    "d/D": None,
                    BRITTLE_COLUMN: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": "Ni50Fe27Ga23|2|4",
                    "_core_image": None,
                    "_glass_image": None,
                    "_images": [],
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame.copy(), extra={}))
        monkeypatch.setattr(
            section,
            "_expected_microwire_keys",
            lambda: {("Ni50Fe27Ga23", 2, 4, None), ("Ni50Fe27Ga23", 2, 5, None)},
        )
        called: list[bool] = []

        def _fake_refresh(self: builder_ui.MiniDatabaseSection) -> None:
            called.append(True)

        monkeypatch.setattr(builder_ui.MiniDatabaseSection, "refresh", _fake_refresh)

        section.refresh()

        assert called == [True]
        refreshed = section.model.frame()
        assert list(refreshed["_key"]) == ["Ni50Fe27Ga23|2|4"]
        row = refreshed.iloc[0]
        assert row[builder_ui.MICROSCOPE_D_COLUMN] == pytest.approx(30.0)
        assert pd.isna(row[builder_ui.MICROSCOPE_CAP_D_COLUMN])
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_process_preserves_existing_index_payload_when_ocr_is_deferred(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        section.reset_to_blank()
        monkeypatch.setattr(section, "_expected_microwire_keys", lambda: set())
        section._expected_keys_current = set()

        old_key = ("TestCompOld", 1, 1, None)
        old_core = tmp_path / "TestCompOld 1_1 core.jpg"
        old_glass = tmp_path / "TestCompOld 1_1 glass.jpg"
        old_core.write_bytes(b"core")
        old_glass.write_bytes(b"glass")

        existing_index = {
            old_key: builder_ui.MicroscopeMeasurements(
                core=[builder_ui.MicroscopeDetection(value=10.0, image_path=old_core)],
                glass=[builder_ui.MicroscopeDetection(value=40.0, image_path=old_glass)],
            )
        }
        section.store.save_payload("microscope_index", existing_index)

        section.apply_data(
            MiniDatabaseData(
                table=pd.DataFrame(
                    [
                        {
                            "Composition": "TestCompOld",
                            "Microwire": "1/1",
                            builder_ui.MICROSCOPE_D_COLUMN: 10.0,
                            builder_ui.MICROSCOPE_CAP_D_COLUMN: 40.0,
                            "d/D": 0.25,
                            BRITTLE_COLUMN: None,
                            builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                            builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                            "_key": "TestCompOld|1|1",
                            "_core_image": str(old_core),
                            "_glass_image": str(old_glass),
                            "_images": [str(old_core), str(old_glass)],
                        }
                    ]
                ),
                extra={"payloads": {"microscope_index": "microscope_index"}},
            )
        )

        result = section.process([old_core, old_glass])

        payload = result.payloads["microscope_index"]
        assert isinstance(payload, dict)
        assert set(payload.keys()) == {old_key}
        measurement = payload[old_key]
        assert measurement.best_core() == pytest.approx(10.0)
        assert measurement.best_glass() == pytest.approx(40.0)
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_apply_data_rehydrates_reviewed_diameters() -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        key = "Ni46Fe23Ga23Co8|1|1"
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni46Fe23Ga23Co8",
                    "Microwire": "1/1",
                    builder_ui.MICROSCOPE_D_COLUMN: None,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                    "d/D": None,
                    BRITTLE_COLUMN: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                    builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                    "_key": key,
                    "_core_image": None,
                    "_glass_image": None,
                    "_images": [],
                }
            ]
        )

        section.apply_data(
            MiniDatabaseData(
                table=frame,
                extra={
                    "validated": {
                        key: {
                            "d": 6.7,
                            "D": 34.4,
                            "d_reviewed": True,
                            "D_reviewed": True,
                        }
                    }
                },
            )
        )

        updated = section.model.frame()
        assert updated.at[0, builder_ui.MICROSCOPE_D_COLUMN] == pytest.approx(6.7)
        assert updated.at[0, builder_ui.MICROSCOPE_CAP_D_COLUMN] == pytest.approx(34.4)
        assert updated.at[0, "d/D"] == pytest.approx(round(6.7 / 34.4, 3))
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_handle_worker_finished_rehydrates_reviewed_diameters() -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        key = "Ni46Fe23Ga23Co8|1|1"
        section._validated = {
            key: {
                "d": 6.7,
                "D": 34.4,
                "d_reviewed": True,
                "D_reviewed": True,
            }
        }
        result = builder_ui.SectionProcessResult(
            table=pd.DataFrame(
                [
                    {
                        "Composition": "Ni46Fe23Ga23Co8",
                        "Microwire": "1/1",
                        builder_ui.MICROSCOPE_D_COLUMN: None,
                        builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                        "d/D": None,
                        BRITTLE_COLUMN: None,
                        builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                        builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                        "_key": key,
                        "_core_image": None,
                        "_glass_image": None,
                        "_images": [],
                    }
                ]
            ),
            processed={},
            payloads={},
            extra={"validated": dict(section._validated)},
        )

        section._handle_worker_finished(result)

        updated = section.model.frame()
        assert updated.at[0, builder_ui.MICROSCOPE_D_COLUMN] == pytest.approx(6.7)
        assert updated.at[0, builder_ui.MICROSCOPE_CAP_D_COLUMN] == pytest.approx(34.4)
        assert updated.at[0, "d/D"] == pytest.approx(round(6.7 / 34.4, 3))
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
        updated = section.model.frame()
        assert updated.at[0, builder_ui.MICROSCOPE_STATUS_COLUMN] == "Values need review"
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_status_distinguishes_missing_images_and_other_ends(tmp_path: Path) -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        core_path = tmp_path / "TestCompA 1_2 core.jpg"
        core_path.write_bytes(b"core")
        frame = pd.DataFrame(
            [
                {
                    "Composition": "TestCompA",
                    "Microwire": "1/1",
                    builder_ui.MICROSCOPE_D_COLUMN: None,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                    "d/D": None,
                    "_key": "TestCompA|1|1",
                    "_core_image": None,
                    "_glass_image": None,
                    "_images": [],
                },
                {
                    "Composition": "TestCompA",
                    "Microwire": "1/2",
                    builder_ui.MICROSCOPE_D_COLUMN: None,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                    "d/D": None,
                    "_key": "TestCompA|1|2",
                    "_core_image": str(core_path),
                    "_glass_image": None,
                    "_images": [str(core_path)],
                },
                {
                    "Composition": "TestCompA",
                    "Microwire": "1/3oe",
                    builder_ui.MICROSCOPE_D_COLUMN: 7.0,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: 28.0,
                    "d/D": 0.25,
                    "_key": "TestCompA|1|3|oe",
                    "_core_image": str(core_path),
                    "_glass_image": str(core_path),
                    "_images": [str(core_path)],
                },
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        updated = section.model.frame()
        assert updated.at[0, builder_ui.MICROSCOPE_STATUS_COLUMN] == "Missing image"
        assert updated.at[1, builder_ui.MICROSCOPE_STATUS_COLUMN] == "Image found; missing glass label"
        assert updated.at[2, builder_ui.MICROSCOPE_STATUS_COLUMN] == "Other end - Values need review"
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_status_column_allows_text_when_loaded_as_float() -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "TestCompA",
                    "Microwire": "1/1",
                    builder_ui.MICROSCOPE_D_COLUMN: 7.0,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: 28.0,
                    "d/D": 0.25,
                    builder_ui.MICROSCOPE_STATUS_COLUMN: float("nan"),
                    "_key": "TestCompA|1|1",
                    "_core_image": "core.jpg",
                    "_glass_image": "glass.jpg",
                    "_images": ["core.jpg", "glass.jpg"],
                }
            ]
        )

        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        updated = section.model.frame()

        assert updated.at[0, builder_ui.MICROSCOPE_STATUS_COLUMN] == "Values need review"
        assert updated[builder_ui.MICROSCOPE_STATUS_COLUMN].dtype == object
    finally:
        section._shutdown_background_threads()
        section.close()


def test_microscope_expected_rows_show_unlinked_status_and_source_label() -> None:
    _ensure_qapp()
    section = MicroscopeSection(logging.getLogger("test"), lambda *_: None)
    try:
        key = ("Ni44Fe27Ga23Cu3Co3", 1, 1, None)
        key_text = "Ni44Fe27Ga23Cu3Co3|1|1"
        section._expected_keys_current = {key}
        section._expected_key_source_labels = {key_text: "Ko\u0161ice"}
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni44Fe27Ga23Cu3Co3",
                    "Microwire": "1/1",
                    builder_ui.MICROSCOPE_D_COLUMN: None,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: None,
                    "d/D": None,
                    builder_ui.SOURCE_LABEL_COLUMN: "Ko\u0161ice",
                    "_key": key_text,
                    "_core_image": None,
                    "_glass_image": None,
                    "_images": [],
                }
            ]
        )

        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        updated = section.model.frame()

        assert updated.at[0, builder_ui.MICROSCOPE_STATUS_COLUMN] == (
            "Expected from annealing; no microscope image linked"
        )
        assert updated.at[0, builder_ui.SOURCE_LABEL_COLUMN] == "Ko\u0161ice"
        assert section._row_missing_images(updated.iloc[0]) is False
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


def test_fabrication_augments_rows_for_microscope_only_samples() -> None:
    _ensure_qapp()
    microscope_store = builder_ui.MiniDatabaseStore("microscope")
    microscope_original = microscope_store.load()
    section = builder_ui.FabricationSection(logging.getLogger("test"), lambda *_: None)
    try:
        microscope_frame = pd.DataFrame(
            [
                {
                    "Composition": "TestCompI",
                    "Microwire": "3/2",
                    builder_ui.MICROSCOPE_D_COLUMN: 7.0,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: 25.0,
                    "d/D": 0.28,
                    "_key": "TestCompI|3|2",
                }
            ]
        )
        microscope_store.save(MiniDatabaseData(table=microscope_frame))

        base = pd.DataFrame(columns=builder_ui._fabrication_index_to_frame(FabricationIndex()).columns)
        relevant_map = {"TestCompI": {3: {2}}}
        raw_index = builder_ui.FabricationIndex()
        raw_index.set_draw(
            "TestCompI",
            3,
            {
                "fabrication_temperature_c": 405.0,
                "mass_g": 2.3,
                "winding_speed_m_per_min": 66.0,
                "_source_path": "Measured",
            },
        )
        raw_index.set_piece(
            "TestCompI",
            3,
            2,
            {
                "length_m": 12.5,
                "piece_date": "2026-03-17",
                "_source_path": "Measured",
            },
        )
        updated = section._augment_table_with_relevant_microscope_rows(
            base,
            relevant_map,
            source_index=raw_index,
        )

        assert len(updated.index) == 1
        row = updated.iloc[0]
        assert row["Composition"] == "TestCompI"
        assert row["Draw"] == 3
        assert row["Piece"] == 2
        assert row["Data source"] == "Microscope only"
        assert pd.isna(row[builder_ui.MICROSCOPE_D_COLUMN])
        assert pd.isna(row[builder_ui.MICROSCOPE_CAP_D_COLUMN])
        assert pd.isna(row["d/D"])
        assert row["Length (m)"] == pytest.approx(12.5)
        assert row[builder_ui.CORE_TEMPERATURE_COLUMN] == pytest.approx(405.0)
        assert row["Mass (g)"] == pytest.approx(2.3)
    finally:
        microscope_store.save(microscope_original)
        section.close()


def test_fabrication_appends_placeholder_for_measured_wire_without_fabrication_data() -> None:
    _ensure_qapp()
    microscope_store = builder_ui.MiniDatabaseStore("microscope")
    microscope_original = microscope_store.load()
    annealing_store = builder_ui.MiniDatabaseStore("annealing")
    annealing_original = annealing_store.load()
    section = builder_ui.FabricationSection(logging.getLogger("test"), lambda *_: None)
    try:
        microscope_frame = pd.DataFrame(
            [
                {
                    "Composition": "TestCompJ",
                    "Microwire": "4/7",
                    builder_ui.MICROSCOPE_D_COLUMN: 11.2,
                    builder_ui.MICROSCOPE_CAP_D_COLUMN: 44.1,
                    "d/D": 0.254,
                    "_key": "TestCompJ|4|7",
                }
            ]
        )
        microscope_store.save(MiniDatabaseData(table=microscope_frame))
        annealing_store.save(MiniDatabaseData(table=pd.DataFrame()))

        base = pd.DataFrame(columns=builder_ui._fabrication_index_to_frame(builder_ui.FabricationIndex()).columns)
        relevant_map = {"TestCompJ": {4: {7}}}
        updated = section._augment_table_with_relevant_microscope_rows(
            base,
            relevant_map,
            source_index=builder_ui.FabricationIndex(),
        )

        assert len(updated.index) == 1
        row = updated.iloc[0]
        assert row["Composition"] == "TestCompJ"
        assert row["Data source"] == "Microscope only"
        assert pd.isna(row[builder_ui.MICROSCOPE_D_COLUMN])
        assert pd.isna(row[builder_ui.MICROSCOPE_CAP_D_COLUMN])
        assert pd.isna(row["d/D"])
    finally:
        microscope_store.save(microscope_original)
        annealing_store.save(annealing_original)
        section.close()


def test_fabrication_source_label_prefers_measured_when_annealing_and_microscope_exist() -> None:
    _ensure_qapp()
    section = builder_ui.FabricationSection(logging.getLogger("test"), lambda *_: None)
    try:
        label = section._source_label_for_key(
            ("TestCompDual", 1, 2),
            {("TestCompDual", 1, 2)},
            {("TestCompDual", 1, 2)},
        )
        assert label == "Measured"
    finally:
        section.close()


def test_apply_fabrication_source_labels_adds_missing_data_source_column() -> None:
    _ensure_qapp()
    section = builder_ui.FabricationSection(logging.getLogger("test"), lambda *_: None)
    try:
        section._measured_keys_by_source = lambda: ({("TestCompSource", 2, 3)}, set())
        frame = pd.DataFrame(
            [
                {
                    "Composition": "TestCompSource",
                    "Draw": 2,
                    "Piece": 3,
                }
            ]
        )

        updated = section._apply_fabrication_source_labels(frame)

        assert "Data source" in updated.columns
        assert updated.loc[0, "Data source"] == "Measured"
    finally:
        section.close()


def test_assembly_import_dedupes_duplicate_columns() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    try:
        payload = {
            "columns": ["Composition", "Core temperature (°C)", "Core temperature (°C)"],
            "rows": [
                {
                    "Composition": "Ni46Fe23Ga23Co8",
                    "Core temperature (°C)": 123.4,
                }
            ],
            "index": [0],
        }

        assembly.import_project_payload(payload)

        frame = assembly._raw_preview_frame
        assert isinstance(frame, pd.DataFrame)
        assert list(frame.columns).count("Core temperature (°C)") == 1
    finally:
        assembly.close()


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
    assert result.dataframe.iloc[0][BRITTLE_COLUMN] == "brittle"


def test_build_database_adds_microscope_only_brittle_rows(tmp_path: Path) -> None:
    microscope_index = {
        ("Ni50Fe27Ga23", 2, 1, None): core.MicroscopeMeasurements(
            glass=[
                core.MicroscopeDetection(
                    value=28.2,
                    image_path=tmp_path / "Ni50Fe27Ga23 2-1 glass brittle.jpg",
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
        measurement_records=[],
        microscope_index=microscope_index,
        skip_exports=True,
    )

    assert len(result.dataframe.index) == 1
    row = result.dataframe.iloc[0]
    assert row["Composition"] == "Ni50Fe27Ga23"
    assert row["Microwire"] == "2/1"
    assert row["Data source"] == "Microscope only"
    assert row[BRITTLE_COLUMN] == "brittle"


def test_shape_memory_section_normalises_current_columns_from_sources(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    "_group_key": "Ni50Fe27Ga23|10|1",
                    "_sources": [
                        str(tmp_path / "Ni50Fe27Ga23 10_1 30mA fracture.txt"),
                        str(tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt"),
                    ],
                    builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN: str(
                        tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt"
                    ),
                    builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN: str(
                        tmp_path / "Ni50Fe27Ga23 10_1 30mA fracture.txt"
                    ),
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.59,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.4,
                    SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 11.52,
                    SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 21.05,
                    SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 935.3,
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        updated = section.model.frame()
        assert len(updated.index) == 2
        assert updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == 30.0
        assert pd.isna(updated.at[0, builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN])
        assert pd.isna(updated.at[1, builder_ui.SHAPE_MEMORY_CURRENT_COLUMN])
        assert updated.at[1, builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN] == 30.0
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_clears_orphan_fracture_current_with_pd_na() -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    "_group_key": "Ni50Fe27Ga23|10|1",
                    "_sources": [],
                    builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN: 50.0,
                    builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_DENSITY_COLUMN: 300.0,
                    builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN: "fracture-source.txt",
                    SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: pd.NA,
                    SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: np.nan,
                    SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: "",
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        row = section.model.frame().iloc[0]
        assert pd.isna(row[builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN])
        assert pd.isna(row[builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_DENSITY_COLUMN])
        assert pd.isna(row[builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN])
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_process_restores_saved_record_entries(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        standard_path = tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt"
        fracture_path = tmp_path / "Ni50Fe27Ga23 10_1 30mA fracture.txt"
        content = "\n".join(
            [
                "Displacement\tLoad\tStrain\tStress",
                "mm\tg\t%\tMPa",
                "0\t0\t0\t0",
                "0.01\t0.10\t0.05\t0.9",
                "0.02\t0.20\t0.10\t1.8",
            ]
        )
        standard_path.write_text(content, encoding="utf-8")
        fracture_path.write_text(content, encoding="utf-8")
        section.data.extra["record_entries"] = {
            str(standard_path): {
                SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                SHAPE_MEMORY_STRAIN_COLUMN: 18.59,
                SHAPE_MEMORY_STRESS_COLUMN: 568.4,
                builder_ui.SHAPE_MEMORY_CURRENT_COLUMN: 30.0,
                builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN: str(standard_path),
            },
            str(fracture_path): {
                SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 11.52,
                SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 21.05,
                SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 935.3,
                builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN: 30.0,
                builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN: str(fracture_path),
            },
        }

        result = section.process([standard_path, fracture_path])
        section._handle_worker_finished(result)
        frame = section.model.frame()
        assert len(frame.index) == 2
        standard_row = frame.iloc[0]
        fracture_row = frame.iloc[1]
        assert standard_row[SHAPE_MEMORY_STRAIN_COLUMN] == pytest.approx(18.59)
        assert standard_row[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == pytest.approx(30.0)
        assert pd.isna(standard_row[builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN])
        assert fracture_row[SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN] == pytest.approx(21.05)
        assert fracture_row[builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN] == pytest.approx(30.0)
        assert pd.isna(fracture_row[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN])
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_defaults_preview_panel_closed() -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        assert section._preview_panel_visible() is False
        assert section._preview_toggle is not None
        assert section._preview_toggle.isChecked() is False
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_merges_legacy_current_columns_and_hides_sources() -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    "_group_key": "Ni50Fe27Ga23|10|1",
                    "_sources": [],
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.59,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.4,
                    SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 11.52,
                    SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 21.05,
                    SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 935.3,
                    "Current (mA)": 20.0,
                    "Current density (A/mm^2)": 140.0,
                    "Fracture current (mA)": 25.0,
                    "Fracture current density (A/mm^2)": 175.0,
                    builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN: "standard.txt",
                    builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN: "fracture.txt",
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        updated = section.model.frame()
        assert "Current (mA)" not in updated.columns
        assert "Current density (A/mm^2)" not in updated.columns
        assert "Fracture current (mA)" not in updated.columns
        assert "Fracture current density (A/mm^2)" not in updated.columns
        assert updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == pytest.approx(20.0)
        assert updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_DENSITY_COLUMN] == pytest.approx(140.0)
        assert updated.at[0, builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN] == pytest.approx(25.0)
        assert updated.at[0, builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_DENSITY_COLUMN] == pytest.approx(175.0)
        assert section.table_view is not None
        standard_index = updated.columns.get_loc(builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN)
        fracture_index = updated.columns.get_loc(builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN)
        assert section.table_view.isColumnHidden(standard_index)
        assert section.table_view.isColumnHidden(fracture_index)
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_replaces_existing_manual_pick(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        path = tmp_path / "Ni50Fe27Ga23 5-4 30mA.txt"
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
        first = builder_ui._ShapeMemoryPointSelection(
            index=0,
            displacement_mm=0.01,
            load_g=0.10,
            strain_pct=0.05,
            stress_mpa=0.9,
        )
        second = builder_ui._ShapeMemoryPointSelection(
            index=1,
            displacement_mm=0.02,
            load_g=0.20,
            strain_pct=0.10,
            stress_mpa=1.8,
        )
        section._apply_picked_selection("standard", first)
        section._apply_picked_selection("standard", second)
        frame = section.model.frame()
        assert frame.at[0, SHAPE_MEMORY_DISPLACEMENT_COLUMN] == pytest.approx(0.02)
        assert frame.at[0, SHAPE_MEMORY_LOAD_COLUMN] == pytest.approx(0.20)
        assert frame.at[0, SHAPE_MEMORY_STRAIN_COLUMN] == pytest.approx(0.10)
        assert frame.at[0, SHAPE_MEMORY_STRESS_COLUMN] == pytest.approx(1.8)
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_expands_to_one_row_per_graph(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        paths = [
            tmp_path / "Ni50Fe27Ga23 10_4 25mA.txt",
            tmp_path / "Ni50Fe27Ga23 10_4 25mA fracture.txt",
            tmp_path / "Ni50Fe27Ga23 10_4 40mA.txt",
        ]
        content = "\n".join(
            [
                "Displacement\tLoad\tStrain\tStress",
                "mm\tg\t%\tMPa",
                "0\t0\t0\t0",
                "0.01\t0.10\t0.05\t0.9",
                "0.02\t0.20\t0.10\t1.8",
            ]
        )
        for path in paths:
            path.write_text(content, encoding="utf-8")

        section.data.extra["record_entries"] = {
            str(paths[0]): {
                SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.7,
                SHAPE_MEMORY_LOAD_COLUMN: 7.715,
                SHAPE_MEMORY_STRAIN_COLUMN: 13.628,
                SHAPE_MEMORY_STRESS_COLUMN: 513.246,
            },
            str(paths[1]): {
                SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 18.834,
                SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 22.994,
                SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 1252.946,
            },
            str(paths[2]): {
                SHAPE_MEMORY_DISPLACEMENT_COLUMN: 4.1,
                SHAPE_MEMORY_LOAD_COLUMN: 8.4,
                SHAPE_MEMORY_STRAIN_COLUMN: 15.1,
                SHAPE_MEMORY_STRESS_COLUMN: 580.0,
            },
        }

        result = section.process(paths)
        section._handle_worker_finished(result)
        frame = section.model.frame()
        assert len(frame.index) == 3
        assert frame.iloc[0][builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == pytest.approx(25.0)
        assert pd.isna(frame.iloc[0][builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN])
        assert frame.iloc[1][builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN] == pytest.approx(25.0)
        assert pd.isna(frame.iloc[1][builder_ui.SHAPE_MEMORY_CURRENT_COLUMN])
        assert frame.iloc[2][builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == pytest.approx(40.0)
        assert list(frame[builder_ui._SHAPE_MEMORY_GROUP_ORDER_COLUMN]) == [0, 1, 2]
        assert all(len(value) == 1 for value in frame["_sources"])
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_preview_panel_shows_saved_row_values(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        record = ShapeMemoryStressStrainRecord(
            path=tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt",
            sample="Ni50Fe27Ga23 10/1",
            data=pd.DataFrame(
                {
                    "displacement_mm": [0.0, 0.01],
                    "load_g": [0.0, 0.10],
                    "strain_pct": [0.0, 0.05],
                    "stress_mpa": [0.0, 0.9],
                }
            ),
            key=("Ni50Fe27Ga23", 10, 1),
            label="30mA",
        )
        section._record_groups = {"Ni50Fe27Ga23 10/1": [record]}
        section._record_groups_by_key = {"Ni50Fe27Ga23|10|1": [record]}
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    "_group_key": "Ni50Fe27Ga23|10|1",
                    "_sources": [str(record.path)],
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.59,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.4,
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        section._record_groups = {"Ni50Fe27Ga23 10/1": [record]}
        section._record_groups_by_key = {"Ni50Fe27Ga23|10|1": [record]}
        section.table_view.selectRow(0)
        _ensure_qapp().processEvents()
        section._update_preview()
        panel = section._preview_panel
        assert panel is not None
        assert panel._picked_labels["displacement_mm"].text() == "3.8 mm"
        assert panel._picked_labels["load_g"].text() == "7 g"
    finally:
        section._shutdown_background_threads()
        section.close()


def test_assembly_expands_shape_memory_rows_per_current() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/4",
                    builder_ui.MICROSCOPE_D_COLUMN: 13.7,
                    "d/D": 0.194,
                    SHAPE_MEMORY_STRESS_STRAIN_COLUMN: ["25mA fracture", "25mA", "40mA"],
                }
            ]
        )
        fracture_record = ShapeMemoryStressStrainRecord(
            path=Path("Ni50Fe27Ga23 10_4 25mA fracture.txt"),
            sample="Ni50Fe27Ga23 10/4",
            data=pd.DataFrame(),
            key=("Ni50Fe27Ga23", 10, 4),
            label="25mA fracture",
        )
        standard_25 = ShapeMemoryStressStrainRecord(
            path=Path("Ni50Fe27Ga23 10_4 25mA.txt"),
            sample="Ni50Fe27Ga23 10/4",
            data=pd.DataFrame(),
            key=("Ni50Fe27Ga23", 10, 4),
            label="25mA",
        )
        standard_40 = ShapeMemoryStressStrainRecord(
            path=Path("Ni50Fe27Ga23 10_4 40mA.txt"),
            sample="Ni50Fe27Ga23 10/4",
            data=pd.DataFrame(),
            key=("Ni50Fe27Ga23", 10, 4),
            label="40mA",
        )
        assembly._cached_shape_memory_stress_strain_groups = {
            "Ni50Fe27Ga23|10|4": [fracture_record, standard_25, standard_40]
        }
        assembly._cached_shape_memory_entries = {}
        assembly._cached_shape_memory_record_entries = {
            str(standard_25.path): {
                SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.7,
                SHAPE_MEMORY_LOAD_COLUMN: 7.715,
                SHAPE_MEMORY_STRAIN_COLUMN: 13.628,
                SHAPE_MEMORY_STRESS_COLUMN: 513.246,
            },
            str(fracture_record.path): {
                SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 18.834,
                SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 22.994,
                SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 1252.946,
            },
            str(standard_40.path): {
                SHAPE_MEMORY_DISPLACEMENT_COLUMN: 4.1,
                SHAPE_MEMORY_LOAD_COLUMN: 8.4,
                SHAPE_MEMORY_STRAIN_COLUMN: 15.1,
                SHAPE_MEMORY_STRESS_COLUMN: 580.0,
            },
        }

        expanded = assembly._expand_shape_memory_preview_rows(frame)

        assert len(expanded.index) == 2
        first = expanded.iloc[0]
        second = expanded.iloc[1]
        assert first[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == 25.0
        assert first[builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN] == 25.0
        assert first[SHAPE_MEMORY_STRAIN_COLUMN] == pytest.approx(13.628)
        assert first[SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN] == pytest.approx(22.994)
        assert second[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == 40.0
        assert pd.isna(second[builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN])
        assert second[SHAPE_MEMORY_STRAIN_COLUMN] == pytest.approx(15.1)
    finally:
        assembly.close()


def test_assembly_does_not_keep_fracture_current_without_saved_values() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/4",
                    builder_ui.MICROSCOPE_D_COLUMN: 13.7,
                    SHAPE_MEMORY_STRESS_STRAIN_COLUMN: ["25mA fracture", "25mA"],
                }
            ]
        )
        fracture_record = ShapeMemoryStressStrainRecord(
            path=Path("Ni50Fe27Ga23 10_4 25mA fracture.txt"),
            sample="Ni50Fe27Ga23 10/4",
            data=pd.DataFrame(),
            key=("Ni50Fe27Ga23", 10, 4),
            label="25mA fracture",
        )
        standard_record = ShapeMemoryStressStrainRecord(
            path=Path("Ni50Fe27Ga23 10_4 25mA.txt"),
            sample="Ni50Fe27Ga23 10/4",
            data=pd.DataFrame(),
            key=("Ni50Fe27Ga23", 10, 4),
            label="25mA",
        )
        assembly._cached_shape_memory_stress_strain_groups = {
            "Ni50Fe27Ga23|10|4": [fracture_record, standard_record]
        }
        assembly._cached_shape_memory_entries = {}
        assembly._cached_shape_memory_record_entries = {
            str(standard_record.path): {
                SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.7,
                SHAPE_MEMORY_LOAD_COLUMN: 7.715,
                SHAPE_MEMORY_STRAIN_COLUMN: 13.628,
                SHAPE_MEMORY_STRESS_COLUMN: 513.246,
            }
        }

        expanded = assembly._expand_shape_memory_preview_rows(frame)
        row = expanded.iloc[0]
        assert row[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == 25.0
        assert pd.isna(row[builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN])
    finally:
        assembly.close()


def test_assembly_skips_shape_memory_rows_without_any_saved_values() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/4",
                    builder_ui.MICROSCOPE_D_COLUMN: 13.7,
                    SHAPE_MEMORY_STRESS_STRAIN_COLUMN: ["25mA", "40mA"],
                }
            ]
        )
        record_25 = ShapeMemoryStressStrainRecord(
            path=Path("Ni50Fe27Ga23 10_4 25mA.txt"),
            sample="Ni50Fe27Ga23 10/4",
            data=pd.DataFrame(),
            key=("Ni50Fe27Ga23", 10, 4),
            label="25mA",
        )
        record_40 = ShapeMemoryStressStrainRecord(
            path=Path("Ni50Fe27Ga23 10_4 40mA.txt"),
            sample="Ni50Fe27Ga23 10/4",
            data=pd.DataFrame(),
            key=("Ni50Fe27Ga23", 10, 4),
            label="40mA",
        )
        assembly._cached_shape_memory_stress_strain_groups = {
            "Ni50Fe27Ga23|10|4": [record_25, record_40]
        }
        assembly._cached_shape_memory_entries = {}
        assembly._cached_shape_memory_record_entries = {
            str(record_40.path): {
                SHAPE_MEMORY_DISPLACEMENT_COLUMN: 4.1,
                SHAPE_MEMORY_LOAD_COLUMN: 8.4,
                SHAPE_MEMORY_STRAIN_COLUMN: 15.1,
                SHAPE_MEMORY_STRESS_COLUMN: 580.0,
            }
        }

        expanded = assembly._expand_shape_memory_preview_rows(frame)
        assert len(expanded.index) == 1
        row = expanded.iloc[0]
        assert row[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == pytest.approx(40.0)
        assert row[SHAPE_MEMORY_STRAIN_COLUMN] == pytest.approx(15.1)
    finally:
        assembly.close()


def test_assembly_restores_shape_memory_values_from_live_section_sources() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    builder_ui.MICROSCOPE_D_COLUMN: 19.1,
                    SHAPE_MEMORY_STRESS_STRAIN_COLUMN: ["0mA", "50mA fracture"],
                }
            ]
        )
        standard_record = ShapeMemoryStressStrainRecord(
            path=Path("Ni50Fe27Ga23 12_2 0mA.txt"),
            sample="Ni50Fe27Ga23 12/2",
            data=pd.DataFrame(),
            key=("Ni50Fe27Ga23", 12, 2),
            label="0mA",
        )
        fracture_record = ShapeMemoryStressStrainRecord(
            path=Path("Ni50Fe27Ga23 12_2 50mA fracture.txt"),
            sample="Ni50Fe27Ga23 12/2",
            data=pd.DataFrame(),
            key=("Ni50Fe27Ga23", 12, 2),
            label="50mA fracture",
        )
        section_frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    SHAPE_MEMORY_STRESS_STRAIN_COLUMN: "0mA",
                    builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN: str(standard_record.path),
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 7.85,
                    SHAPE_MEMORY_LOAD_COLUMN: 19.3,
                    SHAPE_MEMORY_STRAIN_COLUMN: 22.69,
                    SHAPE_MEMORY_STRESS_COLUMN: 660.574,
                },
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    SHAPE_MEMORY_STRESS_STRAIN_COLUMN: "50mA fracture",
                    builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN: str(fracture_record.path),
                    SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 16.912,
                    SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 18.564,
                    SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 1077.382,
                },
            ]
        )
        section.apply_data(
            MiniDatabaseData(
                table=section_frame,
                extra={"preview_panel_visible": False, "graph_column_visible": False},
            )
        )
        assembly.sections["shape_memory_stress_strain"] = section
        assembly._cached_shape_memory_stress_strain_groups = {
            "Ni50Fe27Ga23|12|2": [standard_record, fracture_record]
        }
        assembly._cached_shape_memory_entries = {}
        assembly._cached_shape_memory_record_entries = {}

        expanded = assembly._expand_shape_memory_preview_rows(frame)

        assert len(expanded.index) == 2
        standard_row = expanded.iloc[0]
        fracture_row = expanded.iloc[1]
        assert standard_row[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == pytest.approx(0.0)
        assert standard_row[SHAPE_MEMORY_STRAIN_COLUMN] == pytest.approx(22.69)
        assert standard_row[SHAPE_MEMORY_STRESS_COLUMN] == pytest.approx(660.574)
        assert pd.isna(standard_row[builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN])
        assert fracture_row[builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN] == pytest.approx(50.0)
        assert fracture_row[SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN] == pytest.approx(18.564)
        assert fracture_row[SHAPE_MEMORY_FRACTURE_STRESS_COLUMN] == pytest.approx(1077.382)
        assert pd.isna(fracture_row[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN])
    finally:
        section._shutdown_background_threads()
        section.close()
        assembly.close()


def test_assembly_preserves_multiple_shape_memory_rows_with_same_current_from_live_section() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        standard_path = Path("Ni50Fe27Ga23 11_1 50mA.txt")
        fracture_a_path = Path("Ni50Fe27Ga23 11_1 fracture 50mA a.txt")
        fracture_b_path = Path("Ni50Fe27Ga23 11_1 fracture 50mA b.txt")
        section_frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "11/1",
                    builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN: "fracture 50mA",
                    builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN: str(fracture_a_path),
                    builder_ui.SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 4.9,
                    builder_ui.SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 14.17,
                    builder_ui.SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 312.155,
                },
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "11/1",
                    builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN: "50mA",
                    builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN: str(standard_path),
                    builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN: str(fracture_b_path),
                    builder_ui.SHAPE_MEMORY_DISPLACEMENT_COLUMN: 5.7,
                    builder_ui.SHAPE_MEMORY_LOAD_COLUMN: 13.312,
                    builder_ui.SHAPE_MEMORY_STRAIN_COLUMN: 17.636,
                    builder_ui.SHAPE_MEMORY_STRESS_COLUMN: 848.043,
                    builder_ui.SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 16.912,
                    builder_ui.SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 18.564,
                    builder_ui.SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 1077.382,
                },
            ]
        )
        section.apply_data(
            MiniDatabaseData(
                table=section_frame,
                extra={"preview_panel_visible": False, "graph_column_visible": False},
            )
        )
        assembly.sections["shape_memory_stress_strain"] = section
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "11/1",
                    builder_ui.MICROSCOPE_D_COLUMN: 14.0,
                    builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN: ["50mA", "fracture 50mA"],
                }
            ]
        )

        expanded = assembly._expand_shape_memory_preview_rows(frame)

        assert len(expanded.index) == 2
        first = expanded.iloc[0]
        second = expanded.iloc[1]
        assert pd.isna(first[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN])
        assert first[builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN] == pytest.approx(50.0)
        assert first[builder_ui.SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN] == pytest.approx(14.17)
        assert second[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == pytest.approx(50.0)
        assert second[builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN] == pytest.approx(50.0)
        assert second[builder_ui.SHAPE_MEMORY_STRAIN_COLUMN] == pytest.approx(17.636)
        assert second[builder_ui.SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN] == pytest.approx(18.564)
    finally:
        section._shutdown_background_threads()
        section.close()
        assembly.close()


def test_assembly_keeps_values_but_not_current_when_only_sample_fallback_exists() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        standard_record = ShapeMemoryStressStrainRecord(
            path=Path("Ni50Fe27Ga23 12_1 0mA.txt"),
            sample="Ni50Fe27Ga23 12/1",
            data=pd.DataFrame(),
            key=("Ni50Fe27Ga23", 12, 1),
            label="0mA",
        )
        section_frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/1",
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.591,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.441,
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=section_frame, extra={}))
        assembly.sections["shape_memory_stress_strain"] = section
        assembly._cached_shape_memory_stress_strain_groups = {
            "Ni50Fe27Ga23|12|1": [standard_record]
        }
        assembly._cached_shape_memory_entries = section.entries_snapshot()
        assembly._cached_shape_memory_record_entries = {}
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/1",
                    builder_ui.MICROSCOPE_D_COLUMN: 19.1,
                    SHAPE_MEMORY_STRESS_STRAIN_COLUMN: ["0mA"],
                }
            ]
        )

        expanded = assembly._expand_shape_memory_preview_rows(frame)

        assert len(expanded.index) == 1
        row = expanded.iloc[0]
        assert row[SHAPE_MEMORY_STRAIN_COLUMN] == pytest.approx(18.591)
        assert row[SHAPE_MEMORY_STRESS_COLUMN] == pytest.approx(568.441)
        assert pd.isna(row[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN])
        assert pd.isna(row[builder_ui.SHAPE_MEMORY_CURRENT_DENSITY_COLUMN])
    finally:
        section._shutdown_background_threads()
        section.close()
        assembly.close()


def test_assembly_hides_oe_samples_by_default_and_can_show_them() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {"Composition": "Ni50Fe27Ga23", "Microwire": "1/2", "Strain (%)": 7.75},
                {"Composition": "Ni50Fe27Ga23", "Microwire": "1/2oe", "Strain (%)": 7.85},
            ]
        )

        assembly._update_preview(frame)
        hidden_frame = assembly.preview_model.frame()
        assert list(hidden_frame["Microwire"]) == ["1/2"]

        assembly.set_show_oe_samples(True)
        shown_frame = assembly.preview_model.frame()
        assert list(shown_frame["Microwire"]) == ["1/2", "1/2oe"]
    finally:
        assembly.close()


def test_assembly_sort_uses_microwire_as_tie_breaker_for_equal_strain() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {"Composition": "Ni50Fe27Ga23", "Microwire": "9/3", "Strain (%)": 7.85},
                {"Composition": "Ni53Fe16Ga27Co4", "Microwire": "1/2", "Strain (%)": 7.85},
                {"Composition": "Ni50Fe27Ga23", "Microwire": "10/4oe", "Strain (%)": 7.85},
            ]
        )
        assembly._sort_spec = [("Strain (%)", True)]

        sorted_frame, _ = assembly._apply_sort_spec(frame)

        assert list(sorted_frame["Microwire"]) == ["1/2", "9/3", "10/4oe"]
    finally:
        assembly.close()


def test_assembly_sort_treats_numeric_text_as_numeric_for_strain() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {"Composition": "A", "Microwire": "1/2", "Strain (%)": "6.61"},
                {"Composition": "B", "Microwire": "1/2", "Strain (%)": "7.68"},
                {"Composition": "C", "Microwire": "1/2", "Strain (%)": "7.80"},
            ]
        )
        assembly._sort_spec = [("Strain (%)", False), ("Microwire", True)]

        sorted_frame, _ = assembly._apply_sort_spec(frame)

        assert list(sorted_frame["Strain (%)"]) == ["7.80", "7.68", "6.61"]
    finally:
        assembly.close()


def test_assembly_shape_memory_sort_keeps_sample_rows_grouped_and_positions_by_best_row() -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "A",
                    "Microwire": "1/1",
                    builder_ui.SHAPE_MEMORY_CURRENT_COLUMN: 25.0,
                    builder_ui._SHAPE_MEMORY_GROUP_KEY_COLUMN: "A|1|1",
                    builder_ui._SHAPE_MEMORY_GROUP_ORDER_COLUMN: 0,
                },
                {
                    "Composition": "A",
                    "Microwire": "1/1",
                    builder_ui.SHAPE_MEMORY_CURRENT_COLUMN: 40.0,
                    builder_ui._SHAPE_MEMORY_GROUP_KEY_COLUMN: "A|1|1",
                    builder_ui._SHAPE_MEMORY_GROUP_ORDER_COLUMN: 1,
                },
                {
                    "Composition": "B",
                    "Microwire": "1/1",
                    builder_ui.SHAPE_MEMORY_CURRENT_COLUMN: 30.0,
                    builder_ui._SHAPE_MEMORY_GROUP_KEY_COLUMN: "B|1|1",
                    builder_ui._SHAPE_MEMORY_GROUP_ORDER_COLUMN: 0,
                },
            ]
        )
        assembly._sort_spec = [(builder_ui.SHAPE_MEMORY_CURRENT_COLUMN, False)]

        sorted_frame, _ = assembly._apply_sort_spec(frame)

        assert sorted_frame[builder_ui._SHAPE_MEMORY_GROUP_KEY_COLUMN].tolist() == [
            "A|1|1",
            "A|1|1",
            "B|1|1",
        ]
        assert sorted_frame[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN].tolist() == [40.0, 25.0, 30.0]
    finally:
        assembly.close()


def test_fabrication_import_project_payload_prefers_saved_rows_over_stale_raw_index() -> None:
    _ensure_qapp()
    section = builder_ui.FabricationSection(logging.getLogger("test"), lambda *_: None)
    try:
        stale_index = builder_ui.FabricationIndex()
        stale_index.set_piece(
            "Ni50Fe27Ga23",
            1,
            1,
            {
                "length_m": None,
                "_source_path": "Measured",
            },
        )
        section.store.load_payload = lambda key, *_args, **_kwargs: stale_index if key in {
            "fabrication_index",
            section.raw_index_payload_name,
        } else None  # type: ignore[method-assign]
        section._load_relevant_map = lambda: ({}, set())  # type: ignore[method-assign]
        payload = {
            "columns": [
                "Composition",
                "Microwire",
                "Draw",
                "Piece",
                "Length (m)",
                "Mass (g)",
                "Production datetime",
                "Data source",
                "_source_paths",
            ],
            "rows": [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "1/1",
                    "Draw": 1,
                    "Piece": 1,
                    "Length (m)": 12.34,
                    "Mass (g)": 5.67,
                    "Production datetime": "2025-03-26 08:15",
                    "Data source": "Measured",
                    "_source_paths": ["C:/fabrication/source.xlsx"],
                }
            ],
        }

        section.import_project_payload(payload)
        section.set_import_separation(True)

        frame = section.model.frame()
        assert frame.loc[0, "Length (m)"] == pytest.approx(12.34)
        assert frame.loc[0, "Mass (g)"] == pytest.approx(5.67)
        assert frame.loc[0, "Production datetime"] == "2025-03-26 08:15"
    finally:
        section.close()


def test_fabrication_filter_index_keeps_all_available_pieces_on_relevant_draw() -> None:
    _ensure_qapp()
    section = builder_ui.FabricationSection(logging.getLogger("test"), lambda *_: None)
    try:
        index = builder_ui.FabricationIndex()
        index.set_draw(
            "Ni50Fe27Ga23",
            5,
            {"mass_g": 1.2, "_source_path": "C:/fabrication/source.xlsx"},
        )
        for piece in range(0, 8):
            index.set_piece(
                "Ni50Fe27Ga23",
                5,
                piece,
                {"length_m": float(piece), "_source_path": "C:/fabrication/source.xlsx"},
            )

        filtered = section._filter_index(
            index,
            {"Ni50Fe27Ga23": {5: {4}}},
            {"Ni50Fe27Ga23"},
        )

        assert sorted(
            piece
            for (composition, draw, piece) in filtered.piece_level.keys()
            if composition == "Ni50Fe27Ga23" and draw == 5
        ) == [1, 2, 3, 4, 5, 6, 7]
    finally:
        section.close()


def test_fabrication_augment_table_adds_all_available_sibling_pieces_from_same_draw() -> None:
    _ensure_qapp()
    section = builder_ui.FabricationSection(logging.getLogger("test"), lambda *_: None)
    try:
        index = builder_ui.FabricationIndex()
        index.set_draw(
            "Ni50Fe27Ga23",
            5,
            {"mass_g": 1.2, "_source_path": "C:/fabrication/source.xlsx"},
        )
        for piece in range(0, 8):
            index.set_piece(
                "Ni50Fe27Ga23",
                5,
                piece,
                {"length_m": float(piece), "_source_path": "C:/fabrication/source.xlsx"},
            )
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "5/4",
                    "Draw": 5,
                    "Piece": 4,
                    "Data source": "Measured",
                }
            ]
        )

        augmented = section._augment_table_with_relevant_microscope_rows(
            frame,
            {"Ni50Fe27Ga23": {5: {4}}},
            source_index=index,
        )

        assert augmented["Draw"].tolist() == [5, 5, 5, 5, 5, 5, 5]
        assert augmented["Piece"].tolist() == [4, 1, 2, 3, 5, 6, 7]
        assert augmented["Data source"].tolist() == [
            "Measured",
            "Fabrication only",
            "Fabrication only",
            "Fabrication only",
            "Fabrication only",
            "Fabrication only",
            "Fabrication only",
        ]
    finally:
        section.close()


def test_fabrication_filter_index_limits_draw_to_last_meaningful_piece() -> None:
    _ensure_qapp()
    section = builder_ui.FabricationSection(logging.getLogger("test"), lambda *_: None)
    try:
        index = builder_ui.FabricationIndex()
        index.set_draw("Ni50Fe27Ga23", 5, {"mass_g": 1.2})
        for piece in range(0, 21):
            payload = {"length_m": 0.0}
            if piece == 2:
                payload = {"piece_date": "2024-04-26", "notes": "hruby podla mna cez 2m"}
            elif piece == 4:
                payload = {"piece_date": "2024-04-26", "notes": "hruby"}
            elif piece >= 9:
                payload = {"length_m": None}
            index.set_piece("Ni50Fe27Ga23", 5, piece, payload)

        filtered = section._filter_index(
            index,
            {"Ni50Fe27Ga23": {5: {4}}},
            {"Ni50Fe27Ga23"},
        )

        assert sorted(
            piece
            for (composition, draw, piece) in filtered.piece_level.keys()
            if composition == "Ni50Fe27Ga23" and draw == 5
        ) == [1, 2, 3, 4]
    finally:
        section.close()


def test_fabrication_filter_index_keeps_rows_up_to_last_meaningful_piece_on_draw() -> None:
    _ensure_qapp()
    section = builder_ui.FabricationSection(logging.getLogger("test"), lambda *_: None)
    try:
        index = builder_ui.FabricationIndex()
        index.set_draw("Ni50Fe27Ga23", 6, {"mass_g": 1.2})
        for piece in range(0, 21):
            if piece == 0:
                payload = {"length_m": 0.0, "piece_date": "2024-05-20", "notes": "150m"}
            elif 1 <= piece <= 6:
                payload = {
                    "length_m": float(piece),
                    "piece_date": "2024-05-20",
                    "fabrication_resistance_ohm": float(100 + piece),
                }
            elif piece in {7, 8}:
                payload = {"length_m": 0.0}
            else:
                payload = {"length_m": None}
            index.set_piece("Ni50Fe27Ga23", 6, piece, payload)

        filtered = section._filter_index(
            index,
            {"Ni50Fe27Ga23": {6: {2}}},
            {"Ni50Fe27Ga23"},
        )

        assert sorted(
            piece
            for (composition, draw, piece) in filtered.piece_level.keys()
            if composition == "Ni50Fe27Ga23" and draw == 6
        ) == [1, 2, 3, 4, 5, 6]
    finally:
        section.close()


def test_video_import_project_payload_preserves_saved_rows_with_fabrication_present(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    section = builder_ui.VideoSection(logging.getLogger("test"), lambda *_: None)
    try:
        video_path = tmp_path / "sample_video.mkv"
        video_path.write_bytes(b"video")
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        section._fabrication_table = lambda: pd.DataFrame(  # type: ignore[method-assign]
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "1/1",
                    "Draw": 1,
                    "Piece": 1,
                    "Length (m)": 4.2,
                    "Mass (g)": 2.5,
                    "Production datetime": "2025-03-10 09:00",
                }
            ]
        )

        payload = {
            "columns": [
                "Composition",
                "Microwire",
                "Draw",
                "Piece",
                "Length (m)",
                "Mass (g)",
                "Production datetime",
                builder_ui.VIDEO_END_LENGTH_COLUMN,
                "_sources",
                "_group_key",
            ],
            "rows": [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "1/1",
                    "Draw": 1,
                    "Piece": 1,
                    "Length (m)": 4.2,
                    "Mass (g)": 2.5,
                    "Production datetime": "2025-03-10 09:00",
                    builder_ui.VIDEO_END_LENGTH_COLUMN: 123.4,
                    "_sources": [str(video_path)],
                    "_group_key": "Ni50Fe27Ga23|1|1",
                }
            ],
        }

        section.import_project_payload(payload)
        section.sync_with_fabrication()

        frame = section.model.frame()
        row = frame.iloc[0]
        assert row["Mass (g)"] == pytest.approx(2.5)
        assert row["Production datetime"] == "2025-03-10 09:00"
        assert row[builder_ui.VIDEO_END_LENGTH_COLUMN] == pytest.approx(123.4)
        assert row["_sources"] == [str(video_path)]
        assert section._row_missing_video_files(row) is False
    finally:
        section.close()


def test_video_filter_candidates_for_relevance_keeps_piece_paths_present_in_relevant_map(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    section = builder_ui.VideoSection(logging.getLogger("test"), lambda *_: None)
    try:
        candidates = [
            tmp_path / "Ni50Fe27Ga23 5_0 sample.mkv",
            tmp_path / "Ni50Fe27Ga23 5_1 sample.mkv",
            tmp_path / "Ni50Fe27Ga23 5_2 sample.mkv",
            tmp_path / "Ni50Fe27Ga23 5_5 sample.mkv",
            tmp_path / "Ni50Fe27Ga23 6_1 sample.mkv",
        ]

        filtered = section._filter_candidates_for_relevance(
            candidates,
            {"Ni50Fe27Ga23": {5: {1, 2, 3, 4}}},
            {"Ni50Fe27Ga23"},
        )

        assert filtered == [candidates[1], candidates[2]]
    finally:
        section.close()


def test_builder_load_project_preserves_saved_fabrication_and_video_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        source_xlsx = tmp_path / "fabrication.xlsx"
        source_xlsx.write_text("dummy", encoding="utf-8")
        video_path = tmp_path / "video.mkv"
        video_path.write_bytes(b"video")
        window.settings.setValue(window._project_settings_key("separate_imported"), True)

        project_path = tmp_path / "integrity_project.pydpj"
        payload = {
            "kind": window.PROJECT_KIND,
            "version": window.PROJECT_VERSION,
            "sections": {
                "fabrication": {
                    "columns": [
                        "Composition",
                        "Microwire",
                        "Draw",
                        "Piece",
                        "Length (m)",
                        "Mass (g)",
                        "Production datetime",
                        "Data source",
                        "_source_paths",
                    ],
                    "rows": [
                        {
                            "Composition": "Ni50Fe27Ga23",
                            "Microwire": "10/1",
                            "Draw": 10,
                            "Piece": 1,
                            "Length (m)": 7.0,
                            "Mass (g)": 1.85,
                            "Production datetime": "2025-03-26 08:15",
                            "Data source": "Measured",
                            "_source_paths": [str(source_xlsx)],
                        }
                    ],
                },
                "videos": {
                    "columns": [
                        "Composition",
                        "Microwire",
                        "Draw",
                        "Piece",
                        "Length (m)",
                        "Mass (g)",
                        "Production datetime",
                        builder_ui.VIDEO_END_LENGTH_COLUMN,
                        "_sources",
                        "_group_key",
                    ],
                    "rows": [
                        {
                            "Composition": "Ni50Fe27Ga23",
                            "Microwire": "10/1",
                            "Draw": 10,
                            "Piece": 1,
                            "Length (m)": 7.0,
                            "Mass (g)": 1.85,
                            "Production datetime": "2025-03-26 08:15",
                            builder_ui.VIDEO_END_LENGTH_COLUMN: 208.15,
                            "_sources": [str(video_path)],
                            "_group_key": "Ni50Fe27Ga23|10|1",
                        }
                    ],
                },
            },
        }
        project_path.write_text(json.dumps(payload), encoding="utf-8")

        window._load_project_from_path(project_path)
        QtWidgets.QApplication.processEvents()

        fabrication_frame = window.fabrication_section.model.frame()
        assert fabrication_frame.loc[0, "Length (m)"] == pytest.approx(7.0)
        assert fabrication_frame.loc[0, "Mass (g)"] == pytest.approx(1.85)
        assert fabrication_frame.loc[0, "Production datetime"] == "2025-03-26 08:15"

        video_frame = window.video_section.model.frame()
        video_row = video_frame.iloc[0]
        assert video_row["Mass (g)"] == pytest.approx(1.85)
        assert video_row["Production datetime"] == "2025-03-26 08:15"
        assert video_row[builder_ui.VIDEO_END_LENGTH_COLUMN] == pytest.approx(208.15)
        assert window.video_section._row_missing_video_files(video_row) is False
    finally:
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_builder_project_load_runs_single_fabrication_video_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    window = BuilderWindow()
    try:
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "information",
            lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Ok,
        )
        sync_calls: list[str] = []
        monkeypatch.setattr(
            window.video_section,
            "sync_with_fabrication",
            lambda: sync_calls.append("sync"),
        )
        project_path = tmp_path / "single_sync_project.pydpj"
        project_path.write_text(
            json.dumps(
                {
                    "kind": window.PROJECT_KIND,
                    "version": window.PROJECT_VERSION,
                    "sections": {
                        "fabrication": {
                            "columns": ["Composition", "Microwire"],
                            "rows": [
                                {
                                    "Composition": "Ni50Fe27Ga23",
                                    "Microwire": "10/1",
                                }
                            ],
                        },
                        "videos": {
                            "columns": ["Composition", "Microwire"],
                            "rows": [
                                {
                                    "Composition": "Ni50Fe27Ga23",
                                    "Microwire": "10/1",
                                }
                            ],
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        window._load_project_from_path(project_path)

        assert sync_calls == ["sync"]
    finally:
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_safe_plot_stem_removes_path_separators() -> None:
    stem = _safe_plot_stem("Ni55Fe18Ga27 4/1 s1 1000mA")
    assert "/" not in stem
    assert stem.endswith("1000mA")


def test_build_database_integration(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
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
    assert row["Other annealing files"] == [anneal_files[1].name]
    assert pd.isna(row[core.STRAIN_COLUMN])
    assert pd.isna(row[builder_ui.MICROSCOPE_D_COLUMN])
    assert pd.isna(row[builder_ui.MICROSCOPE_CAP_D_COLUMN])
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
    assert "Figure — other annealing" in result.dataframe.columns
    assert "Figure — 1000 mA (Origin)" in result.dataframe.columns
    assert "Figure — other annealing (Origin)" in result.dataframe.columns
    assert set(result.plot_paths) == {produced[high.name].name, produced[low.name].name}
    assert row["Figure — 1000 mA"] == produced[high.name].name
    assert row["Figure — other annealing"] == produced[low.name].name
    assert pd.isna(row["Figure — 1000 mA (Origin)"])
    assert pd.isna(row["Figure — other annealing (Origin)"])
    assert row["Other annealing files"] == [low.name]
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
    assert "Figure — other annealing (Origin)" in result.dataframe.columns
    assert "Figure — 1000 mA" in result.dataframe.columns
    assert "Figure — other annealing" in result.dataframe.columns
    assert row["Figure — 1000 mA (Origin)"] == origin_records[high.name].descriptor
    assert row["Figure — other annealing (Origin)"] == origin_records[low.name].descriptor
    assert pd.isna(row["Figure — 1000 mA"])
    assert pd.isna(row["Figure — other annealing"])
    assert pd.isna(row[core.STRAIN_COLUMN])


def test_word_report_export_embeds_available_origin_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    high = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 1_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.1\n0.1 0.2 2.1\n")

    captured_pyplot: list[tuple[str, list[Path]]] = []

    def fake_pyplot_origin(
        *,
        paths: list[Path],
        plugin_name: str,
        origin_dir: Path,
        descriptor_prefix: str,
        display_prefix: str,
        log: logging.Logger | None,
    ) -> list[OriginArtifact]:
        del descriptor_prefix, display_prefix, log
        captured_pyplot.append((plugin_name, list(paths)))
        origin_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[OriginArtifact] = []
        for source in paths:
            artifact_path = origin_dir / f"{source.stem}.oggu"
            artifact_path.write_bytes(b"origin graph object")
            artifacts.append(
                OriginArtifact(
                    descriptor=artifact_path.name,
                    object_path=artifact_path,
                    display_text=f"Origin graph for {source.stem}",
                )
            )
        return artifacts

    captured_insertions: list[tuple[Path, list[core.WordOleInsertion]]] = []

    def fake_embed(
        docx_path: Path,
        insertions: list[core.WordOleInsertion],
        log: logging.Logger,
    ) -> None:
        captured_insertions.append((docx_path, list(insertions)))

    monkeypatch.setattr(core, "export_pyplot_origin_artifacts_for_paths", fake_pyplot_origin)
    monkeypatch.setattr(core, "_embed_origin_objects_with_word", fake_embed)

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[high, low],
            output_dir=tmp_path / "out",
            make_plots=True,
            plot_backends=("origin",),
            export_formats=("word",),
            column_filter=("Composition", "Microwire"),
        )
    )

    assert "word" in result.exports
    assert result.exports["word"].is_dir()
    assert len(result.word_reports) == 1
    assert result.word_reports[0].name == "Ni55Fe18Ga27_1-1.docx"
    assert captured_pyplot == [
        ("Current Annealing", [high]),
        ("Current Annealing", [low]),
    ]
    assert len(captured_insertions) == 1
    assert captured_insertions[0][0] == result.word_reports[0]
    assert {item.object_path.name for item in captured_insertions[0][1]} == {
        f"{high.stem}.oggu",
        f"{low.stem}.oggu",
    }

    from zipfile import ZipFile

    with ZipFile(result.word_reports[0], "r") as archive:
        names = set(archive.namelist())
        assert "word/document.xml" in names
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "Ni55Fe18Ga27 1/1" in document_xml
    assert "Microwire data" in document_xml
    assert "Stress/strain current (mA)" in document_xml
    assert 'w:pStyle w:val="Heading1"' in document_xml
    assert "Microscope and dimensions" in document_xml
    assert "Current annealing" in document_xml
    assert "VSM temperature scan" not in document_xml
    assert "DMA iso-stress" not in document_xml
    assert "Measurement references" not in document_xml
    assert "Graph:" not in document_xml
    assert "Book:" not in document_xml
    assert "Origin object placeholder" in document_xml


def test_word_report_export_writes_sample_header_and_page_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core, "_embed_pictures_with_word", lambda *args, **kwargs: None)
    monkeypatch.setattr(core, "_embed_origin_objects_with_word", lambda *args, **kwargs: None)

    reports = core.export_word_reports(
        pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                }
            ]
        ),
        tmp_path / "reports",
        origin_artifacts={},
    )

    assert len(reports) == 1
    from zipfile import ZipFile

    with ZipFile(reports[0], "r") as archive:
        names = set(archive.namelist())
        assert "word/header1.xml" in names
        assert "word/footer1.xml" in names
        assert "word/_rels/document.xml.rels" in names
        document_xml = archive.read("word/document.xml").decode("utf-8")
        header_xml = archive.read("word/header1.xml").decode("utf-8")
        footer_xml = archive.read("word/footer1.xml").decode("utf-8")
        rels_xml = archive.read("word/_rels/document.xml.rels").decode("utf-8")

    assert 'w:headerReference w:type="default" r:id="rId3"' in document_xml
    assert 'w:footerReference w:type="default" r:id="rId4"' in document_xml
    assert "Ni50Fe27Ga23 12/2" in header_xml
    assert 'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"' in rels_xml
    assert 'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"' in rels_xml
    assert "PAGE" in footer_xml
    assert "NUMPAGES" in footer_xml


def test_word_report_export_accepts_clipboard_only_origin_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_insertions: list[core.WordOleInsertion] = []

    def fake_embed(
        docx_path: Path,
        insertions: list[core.WordOleInsertion],
        log: logging.Logger,
    ) -> None:
        del docx_path, log
        captured_insertions.extend(insertions)

    monkeypatch.setattr(core, "_embed_origin_objects_with_word", fake_embed)

    descriptor = "live-origin-graph.oggu"
    reports = core.export_word_reports(
        pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    f"{core.FIGURE_COLUMNS[0]} (Origin)": descriptor,
                }
            ]
        ),
        tmp_path / "reports",
        origin_artifacts={
            descriptor: OriginArtifact(
                descriptor=descriptor,
                object_path=None,
                graph_name="Graph1",
                display_text="Live Origin graph",
                clipboard_fallback=True,
            )
        },
    )

    assert len(reports) == 1
    assert len(captured_insertions) == 1
    insertion = captured_insertions[0]
    assert insertion.object_path == Path(descriptor)
    assert insertion.graph_name == "Graph1"
    assert insertion.clipboard_fallback is True


def test_word_report_microwire_data_uses_requested_column_order_and_empty_values() -> None:
    values = core._word_assemble_values(
        pd.Series(
            {
                "Microwire": "12/2",
                "Composition": "Ni50Fe27Ga23",
                "Stress/strain current (mA)": "",
                "As (°C)": np.nan,
                "Custom data": "kept",
                "Data source": "hidden",
                "R vs T graphs (Origin)": "hidden.oggu",
            }
        )
    )

    labels = [label for label, _value in values]
    value_map = dict(values)
    assert labels[:4] == [
        "Composition",
        "Microwire",
        "e/a",
        "Strain (%)",
    ]
    assert labels.index("Stress/strain current (mA)") < labels.index("As (°C)")
    assert value_map["Stress/strain current (mA)"] == ""
    assert value_map["As (°C)"] == ""
    assert "Custom data" not in value_map
    assert "Data source" not in value_map
    assert "R vs T graphs (Origin)" not in value_map


def test_word_report_microwire_data_table_only_expands_multi_value_rows() -> None:
    table_xml = core._word_microwire_data_table(
        [
            ("Composition", ["Ni50Fe27Ga23"]),
            ("Strain (%)", ["22.6904", "21.8579", "4.85437"]),
            ("As (Â°C)", [""]),
        ]
    )

    rows = re.findall(r"<w:tr>(.*?)</w:tr>", table_xml)
    assert len(rows) == 3
    assert rows[0].count("<w:tc>") == 4
    assert rows[1].count("<w:tc>") == 4
    assert rows[2].count("<w:tc>") == 4
    assert "Composition" in rows[0]
    assert "As (" in rows[2]


def test_word_report_microwire_data_table_fills_columns_top_to_bottom() -> None:
    table_xml = core._word_microwire_data_table(
        [
            ("A", ["1"]),
            ("B", ["2"]),
            ("C", ["3"]),
            ("D", ["4"]),
            ("E", ["5"]),
        ]
    )

    rows = re.findall(r"<w:tr>(.*?)</w:tr>", table_xml)
    assert len(rows) == 3
    assert "A" in rows[0] and "D" in rows[0]
    assert "B" in rows[1] and "E" in rows[1]
    assert "C" in rows[2]


def test_word_report_sections_start_on_new_pages() -> None:
    xml, _origin_insertions, _picture_insertions = core._word_document_xml(
        pd.Series({"Composition": "Ni50Fe27Ga23", "Microwire": "12/2"}),
        0,
        {},
        {},
    )

    assert xml.count('w:type="page"') == 1
    assert xml.index("Microwire data") < xml.index('w:type="page"') < xml.index("Microscope and dimensions")


def test_word_report_skips_empty_measurement_sections() -> None:
    xml, origin_insertions, _picture_insertions = core._word_document_xml(
        pd.Series({"Composition": "Ni50Fe27Ga23", "Microwire": "12/2"}),
        0,
        {},
        {},
    )

    assert "Microwire data" in xml
    assert "Microscope and dimensions" in xml
    assert "Current annealing" not in xml
    assert "TMA" not in xml
    assert "Not measured yet." in xml
    assert origin_insertions == []


def test_word_report_includes_reference_only_measurement_section() -> None:
    xml, origin_insertions, _picture_insertions = core._word_document_xml(
        pd.Series(
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                core.MINI_DMA_COLUMN: "Ni50Fe27Ga23 12_2 iso-current",
            }
        ),
        0,
        {},
        {},
    )

    assert "TMA" in xml
    assert "Graphs in Assemble" in xml
    assert "Ni50Fe27Ga23 12_2 iso-current" in xml
    assert "Current annealing" not in xml
    assert origin_insertions == []


def test_word_report_rejects_mismatched_origin_descriptor() -> None:
    descriptor = "Ni51Fe27Ga23_13-3_current.oggu"
    xml, origin_insertions, _picture_insertions = core._word_document_xml(
        pd.Series(
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                f"{core.FIGURE_COLUMNS[0]} (Origin)": descriptor,
            }
        ),
        0,
        {
            descriptor: OriginArtifact(
                descriptor=descriptor,
                object_path=Path(descriptor),
                display_text="Ni51Fe27Ga23 13/3 current annealing",
            )
        },
        {},
    )

    assert "Current annealing" not in xml
    assert "Origin object placeholder" not in xml
    assert origin_insertions == []

    manifest = core.word_report_section_manifest_for_row(
        pd.Series(
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                f"{core.FIGURE_COLUMNS[0]} (Origin)": descriptor,
            }
        ),
        {
            descriptor: OriginArtifact(
                descriptor=descriptor,
                object_path=Path(descriptor),
                display_text="Ni51Fe27Ga23 13/3 current annealing",
            )
        },
    )
    current_section = next(item for item in manifest if item["title"] == "Current annealing")
    assert current_section["included"] is False
    assert current_section["status"] == "invalid"
    assert current_section["reason"] == "content_failed_sample_validation"
    assert current_section["invalid_origin_descriptors"] == [descriptor]


def test_word_report_rejects_origin_descriptor_for_wrong_wire_without_composition() -> None:
    descriptor = "13-3_current.oggu"
    row = pd.Series(
        {
            "Composition": "Ni50Fe27Ga23",
            "Microwire": "12/2",
            f"{core.FIGURE_COLUMNS[0]} (Origin)": descriptor,
        }
    )
    artifact = OriginArtifact(
        descriptor=descriptor,
        object_path=Path(descriptor),
        display_text="13/3 current annealing",
    )

    manifest = core.word_report_section_manifest_for_row(row, {descriptor: artifact})
    current_section = next(item for item in manifest if item["title"] == "Current annealing")

    assert current_section["included"] is False
    assert current_section["status"] == "invalid"
    assert current_section["reason"] == "content_failed_sample_validation"
    assert current_section["invalid_origin_descriptors"] == [descriptor]


def test_word_export_manifest_records_skipped_and_invalid_sections(
    tmp_path: Path,
) -> None:
    import launcher

    descriptor = "Ni51Fe27Ga23_13-3_current.oggu"
    frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                core.MINI_DMA_COLUMN: "Ni50Fe27Ga23 12_2 iso-current",
                f"{core.FIGURE_COLUMNS[0]} (Origin)": descriptor,
            }
        ]
    )
    output_dir = tmp_path / "reports"
    output_dir.mkdir()

    manifest_json, _manifest_csv = launcher._write_microwire_word_manifest(
        frame,
        [output_dir / "Ni50Fe27Ga23_12-2.docx"],
        output_dir,
        source_path=tmp_path / "copy.pydpj",
        copied_project=None,
        include_origin=True,
        origin_artifacts={
            descriptor: OriginArtifact(
                descriptor=descriptor,
                object_path=Path(descriptor),
                display_text="Ni51Fe27Ga23 13/3 current annealing",
            )
        },
    )

    payload = json.loads(manifest_json.read_text(encoding="utf-8"))
    report = payload["reports"][0]
    assert report["graph_sections"] == ["TMA"]
    assert "TMA" in report["included_sections"]
    assert "Current annealing" in report["invalid_sections"]
    assert "R vs T" in report["skipped_sections"]
    assert report["sections"]["Current annealing"]["status"] == "invalid"
    assert report["sections"]["Current annealing"]["invalid_origin_descriptors"] == [descriptor]
    assert report["sections"]["R vs T"]["reason"] == "no_section_content"


def test_word_export_manifest_records_ole_embedding_results(
    tmp_path: Path,
) -> None:
    import launcher

    descriptor = "current.oggu"
    artifact_path = tmp_path / descriptor
    artifact_path.write_bytes(b"origin graph object")
    frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                f"{core.FIGURE_COLUMNS[0]} (Origin)": descriptor,
            }
        ]
    )
    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    report_path = output_dir / "Ni50Fe27Ga23_12-2.docx"

    manifest_json, _manifest_csv = launcher._write_microwire_word_manifest(
        frame,
        [report_path],
        output_dir,
        source_path=tmp_path / "copy.pydpj",
        copied_project=None,
        include_origin=True,
        origin_artifacts={
            descriptor: OriginArtifact(
                descriptor=descriptor,
                object_path=artifact_path,
                display_text="Current annealing Origin graph",
            )
        },
        ole_embedding_results={
            report_path: [
                core.WordOleEmbeddingResult(
                    bookmark_name="OriginGraph1",
                    descriptor=descriptor,
                    label="Current annealing Origin graph",
                    object_path=str(artifact_path),
                    attempted=True,
                    inserted=True,
                    status="succeeded",
                )
            ]
        },
    )

    payload = json.loads(manifest_json.read_text(encoding="utf-8"))
    current = payload["reports"][0]["sections"]["Current annealing"]
    assert current["included"] is True, current
    assert current["origin_artifacts_accepted"] == [descriptor]
    assert current["origin_artifacts_attempted"] == [descriptor]
    assert current["ole_insertions_attempted"] == [descriptor]
    assert current["ole_insertions_succeeded"] == [descriptor]
    assert current["ole_insertions_failed"] == []
    assert current["ole_insertions"][0]["status"] == "succeeded"
    assert current["ole_insertions"][0]["bookmark"] == "OriginGraph1"


def test_build_database_word_export_uses_pyplot_origin_for_measurement_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    high = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    high.write_text("0.1 40 1\n0.2 41 1\n", encoding="utf-8")
    vsm_path = tmp_path / "Ni55Fe18Ga27 1_1 scan.VSM-TSCN-Data"
    vsm_path.write_text("stub", encoding="utf-8")
    captured: list[tuple[str, list[Path]]] = []
    captured_insertions: list[core.WordOleInsertion] = []

    def fake_pyplot_origin(
        *,
        paths: list[Path],
        plugin_name: str,
        origin_dir: Path,
        descriptor_prefix: str,
        display_prefix: str,
        log: logging.Logger | None,
    ) -> list[OriginArtifact]:
        del origin_dir, descriptor_prefix, display_prefix, log
        captured.append((plugin_name, list(paths)))
        artifact_name = (
            "current.oggu"
            if plugin_name == "Current Annealing"
            else "vsm-temperature.oggu"
        )
        artifact_path = tmp_path / artifact_name
        artifact_path.write_bytes(b"origin graph object")
        return [
            OriginArtifact(
                descriptor=artifact_path.name,
                object_path=artifact_path,
                display_text=f"{plugin_name} Origin graph",
            )
        ]

    def fake_embed(
        docx_path: Path,
        insertions: list[core.WordOleInsertion],
        log: logging.Logger,
    ) -> None:
        del docx_path, log
        captured_insertions.extend(insertions)

    monkeypatch.setattr(core, "export_pyplot_origin_artifacts_for_paths", fake_pyplot_origin)
    monkeypatch.setattr(core, "_embed_origin_objects_with_word", fake_embed)

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[high],
            output_dir=tmp_path / "out",
            make_plots=True,
            plot_backends=("origin",),
            export_formats=("word",),
        ),
        vsm_temperature_scan_records=[
            core.VsmTemperatureScanRecord(
                path=vsm_path,
                sample="Ni55Fe18Ga27 1/1",
                data=pd.DataFrame(),
                key=("Ni55Fe18Ga27", 1, 1),
                label="temperature scan",
            )
        ],
    )

    assert {plugin_name: paths for plugin_name, paths in captured} == {
        "Current Annealing": [high],
        "VSM Temperature Scan": [vsm_path],
    }
    assert result.dataframe.iloc[0][core.VSM_TEMPERATURE_SCAN_ORIGIN_COLUMN] == "vsm-temperature.oggu"
    assert result.origin_artifacts["vsm-temperature.oggu"].object_path == tmp_path / "vsm-temperature.oggu"
    assert {item.object_path.name for item in captured_insertions} == {
        "current.oggu",
        "vsm-temperature.oggu",
    }, (
        result.dataframe.iloc[0].dropna().to_dict(),
        result.origin_artifacts,
    )


def test_word_report_export_embeds_microscope_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "core-crop.png"
    image_path.write_bytes(b"fake png")
    captured_insertions: list[tuple[Path, list[core.WordPictureInsertion]]] = []

    def fake_embed_pictures(
        docx_path: Path,
        insertions: list[core.WordPictureInsertion],
        log: logging.Logger,
    ) -> None:
        captured_insertions.append((docx_path, list(insertions)))

    monkeypatch.setattr(core, "_embed_pictures_with_word", fake_embed_pictures)
    monkeypatch.setattr(core, "_embed_origin_objects_with_word", lambda *args, **kwargs: None)

    frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "12/2",
                core.DIAMETER_COLUMN: 13.2,
                core.GLASS_DIAMETER_COLUMN: 26.4,
                core.DIAMETER_RATIO_COLUMN: 0.5,
                core.MICROSCOPE_IMAGE_COLUMNS[0]: "core-crop",
            }
        ]
    )

    reports = core.export_word_reports(
        frame,
        tmp_path / "reports",
        microscope_crops={"core-crop": image_path},
    )

    assert len(reports) == 1
    assert len(captured_insertions) == 1
    assert captured_insertions[0][0] == reports[0]
    assert [item.image_path for item in captured_insertions[0][1]] == [image_path]
    assert captured_insertions[0][1][0].label == "Core diameter image"

    from zipfile import ZipFile

    with ZipFile(reports[0], "r") as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "Microscope and dimensions" in document_xml
    assert "Core diameter image" in document_xml
    assert "d/D" in document_xml


def test_assembly_export_dialog_word_reports_enable_origin(qtbot) -> None:
    dialog = builder_ui._AssemblyExportDialog(
        output_dir=".",
        output_name="microwire_database",
        export_csv=False,
        export_excel=False,
        export_html=False,
        export_word=False,
        export_matplotlib=False,
        export_origin=False,
    )
    qtbot.addWidget(dialog)

    dialog.word_checkbox.setChecked(True)

    settings = dialog.export_settings()
    assert settings["export_word"] is True
    assert settings["export_origin"] is True


def test_build_database_groups_all_non_anchor_measurements_into_other_bucket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    high = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    mid = tmp_path / "Ni55Fe18Ga27 1_1 140mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 1_1 90mA.txt"
    for path in (high, mid, low):
        path.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")

    produced: dict[str, Path] = {}

    def fake_plot(df, source: Path, plot_dir: Path, figsize: tuple[float, float]) -> Path:
        plot_dir.mkdir(parents=True, exist_ok=True)
        out_path = plot_dir / f"{source.stem}.png"
        out_path.write_text("stub")
        produced[source.name] = out_path
        return out_path

    monkeypatch.setattr(core, "_plot_measurement_matplotlib", fake_plot)

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[high, mid, low],
            output_dir=tmp_path / "out",
            make_plots=True,
        )
    )

    row = result.dataframe.iloc[0]
    assert row["File 1000 mA"] == high.name
    assert row["Other annealing files"] == [low.name, mid.name]
    assert row["Figure — other annealing"] == [
        produced[low.name].name,
        produced[mid.name].name,
    ]


def test_build_database_without_exact_1000_keeps_all_measurements_in_other_bucket(
    tmp_path: Path,
) -> None:
    first = tmp_path / "Ni55Fe18Ga27 1_1 120mA.txt"
    second = tmp_path / "Ni55Fe18Ga27 1_1 140mA.txt"
    for path in (first, second):
        path.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[first, second],
            output_dir=tmp_path / "out",
            make_plots=False,
            export_formats=(),
        )
    )

    row = result.dataframe.iloc[0]
    assert pd.isna(row["File 1000 mA"])
    assert row["Other annealing files"] == [first.name, second.name]
    assert result.stats.missing_high_measurement == 1


def test_build_database_prefers_non_variant_exact_1000_as_anchor(tmp_path: Path) -> None:
    base_path = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    variant_path = tmp_path / "Ni55Fe18Ga27 1_1 s2a 1000mA.txt"
    follow_up_path = tmp_path / "Ni55Fe18Ga27 1_1 140mA.txt"
    for path in (base_path, variant_path, follow_up_path):
        path.write_text("placeholder", encoding="utf-8")

    def measurement(
        path: Path,
        *,
        setpoint: int,
        alt_variant: bool,
        transition_summary: tuple[str, ...] = (),
    ) -> MeasurementRecord:
        return MeasurementRecord(
            path=path,
            metadata=MeasurementMetadata(
                composition_token="Ni55Fe18Ga27",
                draw_x=1,
                piece_y=1,
                setpoint_mA=setpoint,
                alt_variant=alt_variant,
                measurement_id=path.stem,
                file_name=path.name,
                relpath=path.name,
                timestamp_mtime_utc="2026-03-23T00:00:00+00:00",
            ),
            dataframe=pd.DataFrame(
                {"I_A": [0.1], "V_V": [0.2], "R_ohm": [2.0], "I_mA": [100.0]}
            ),
            sanity_ok=True,
            sanity_error=0.0,
            transition_summary=transition_summary,
        )

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[],
            output_dir=tmp_path / "out",
            make_plots=False,
            export_formats=(),
        ),
        measurement_records=[
            measurement(
                base_path,
                setpoint=1000,
                alt_variant=False,
                transition_summary=("1000mA: As 35 mA, Af 50 mA, Ms 55 mA, Mf 30 mA",),
            ),
            measurement(
                variant_path,
                setpoint=1000,
                alt_variant=True,
                transition_summary=("1000mA: As 35 mA, Af 50 mA, Ms 55 mA, Mf 30 mA",),
            ),
            measurement(
                follow_up_path,
                setpoint=140,
                alt_variant=False,
                transition_summary=("140mA: As 40 mA, Af 52 mA, Ms 58 mA, Mf 33 mA",),
            ),
        ],
        fabrication_index=FabricationIndex(),
        skip_exports=True,
    )

    row = result.dataframe.iloc[0]
    assert row["File 1000 mA"] == base_path.name
    assert row["Other annealing files"] == [follow_up_path.name, variant_path.name]
    assert row[ANNEALING_TRANSITION_COLUMN] == [
        "1000mA: As 35 mA, Af 50 mA, Ms 55 mA, Mf 30 mA",
        "140mA: As 40 mA, Af 52 mA, Ms 58 mA, Mf 33 mA",
    ]


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


def test_build_database_estimates_transition_temps_from_vsm_scan_records(tmp_path: Path) -> None:
    anneal_path = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    anneal_path.write_text("0.1 0.2 2.0\n")
    heating_x = np.linspace(0.0, 100.0, 101)
    cooling_x = heating_x[::-1]
    heating_y = np.piecewise(
        heating_x,
        [heating_x <= 30.0, (heating_x > 30.0) & (heating_x < 70.0), heating_x >= 70.0],
        [
            lambda value: 0.01 * value,
            lambda value: 0.3 + 0.09 * (value - 30.0),
            lambda value: 3.9 + 0.012 * (value - 70.0),
        ],
    )
    cooling_y = np.piecewise(
        cooling_x,
        [cooling_x <= 25.0, (cooling_x > 25.0) & (cooling_x < 65.0), cooling_x >= 65.0],
        [
            lambda value: 0.01 * value,
            lambda value: 0.25 + 0.09 * (value - 25.0),
            lambda value: 3.85 + 0.012 * (value - 65.0),
        ],
    )
    scan = VsmTemperatureScanRecord(
        path=tmp_path / "scan.txt",
        sample="Ni55Fe18Ga27 1_1",
        data=pd.DataFrame(
            {
                "temperature": np.concatenate([heating_x, cooling_x]),
                "field": [10000.0] * 202,
                "signal": np.concatenate([heating_y, cooling_y]),
                "section_index": [0] * 101 + [1] * 101,
            }
        ),
        key=("Ni55Fe18Ga27", 1, 1, None),
        label="scan",
    )

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[anneal_path],
            output_dir=tmp_path / "out",
            make_plots=False,
            export_formats=(),
            plot_backends=(),
        ),
        vsm_temperature_scan_records=[scan],
        skip_exports=True,
    )

    row = result.dataframe.iloc[0]
    assert row["As (°C)"] == pytest.approx(30.0, abs=1.0)
    assert row["Af (°C)"] == pytest.approx(70.0, abs=1.0)
    assert row["Ms (°C)"] == pytest.approx(65.0, abs=1.0)
    assert row["Mf (°C)"] == pytest.approx(25.0, abs=1.0)


def test_build_database_respects_blocked_vsm_transition_review(tmp_path: Path) -> None:
    heating_x = np.linspace(0.0, 100.0, 101)
    cooling_x = heating_x[::-1]
    heating_y = np.piecewise(
        heating_x,
        [heating_x <= 30.0, (heating_x > 30.0) & (heating_x < 70.0), heating_x >= 70.0],
        [
            lambda value: 0.01 * value,
            lambda value: 0.3 + 0.09 * (value - 30.0),
            lambda value: 3.9 + 0.012 * (value - 70.0),
        ],
    )
    cooling_y = np.piecewise(
        cooling_x,
        [cooling_x <= 25.0, (cooling_x > 25.0) & (cooling_x < 65.0), cooling_x >= 65.0],
        [
            lambda value: 0.01 * value,
            lambda value: 0.25 + 0.09 * (value - 25.0),
            lambda value: 3.85 + 0.012 * (value - 65.0),
        ],
    )
    scan = VsmTemperatureScanRecord(
        path=tmp_path / "scan.txt",
        sample="Ni55Fe18Ga27 1_1",
        data=pd.DataFrame(
            {
                "temperature": np.concatenate([heating_x, cooling_x]),
                "field": [10000.0] * 202,
                "signal": np.concatenate([heating_y, cooling_y]),
                "section_index": [0] * 101 + [1] * 101,
            }
        ),
        key=("Ni55Fe18Ga27", 1, 1, None),
        label="scan",
    )

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[],
            output_dir=tmp_path / "out",
            make_plots=False,
            export_formats=(),
            plot_backends=(),
        ),
        vsm_temperature_scan_records=[scan],
        transition_temps={
            "Ni55Fe18Ga27|1|1": {
                "__review_status__": "no_transition",
                "__included__": False,
            }
        },
        skip_exports=True,
    )

    row = result.dataframe.iloc[0]
    assert pd.isna(row[core.TRANSITION_TEMP_AS_COLUMN])
    assert pd.isna(row[core.TRANSITION_TEMP_AF_COLUMN])
    assert pd.isna(row[core.TRANSITION_TEMP_MS_COLUMN])
    assert pd.isna(row[core.TRANSITION_TEMP_MF_COLUMN])


def test_build_database_reports_current_annealing_blocked_transition_statuses(
    tmp_path: Path,
) -> None:
    no_transition_path = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    excluded_path = tmp_path / "Ni55Fe18Ga27 1_2 1000mA.txt"
    no_transition_path.write_text("0.1 0.2 2.0\n")
    excluded_path.write_text("0.1 0.2 2.0\n")

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[no_transition_path, excluded_path],
            output_dir=tmp_path / "out",
            export_formats=(),
        ),
        current_density_entries={
            "Ni55Fe18Ga27|1|1": {
                core.CURRENT_ANNEALING_TRANSITION_STATUS_COLUMN: "No transition",
                core.CURRENT_ANNEALING_TRANSITION_COUNTS_COLUMN: (
                    "total=1; accepted=0; manual=0; no_transition=1; excluded=0; "
                    "needs_attention=0; unreviewed=0; auto_candidates=0"
                ),
            },
            "Ni55Fe18Ga27|1|2": {
                core.CURRENT_ANNEALING_TRANSITION_STATUS_COLUMN: "Excluded",
                core.CURRENT_ANNEALING_TRANSITION_COUNTS_COLUMN: (
                    "total=1; accepted=0; manual=0; no_transition=0; excluded=1; "
                    "needs_attention=0; unreviewed=0; auto_candidates=0"
                ),
            },
        },
        skip_exports=True,
    )

    rows = {
        row["Microwire"]: row
        for row in result.dataframe.to_dict(orient="records")
    }
    assert pd.isna(rows["1/1"]["As1 (mA)"])
    assert rows["1/1"][core.CURRENT_ANNEALING_TRANSITION_STATUS_COLUMN] == "No transition"
    assert pd.isna(rows["1/2"]["As1 (mA)"])
    assert rows["1/2"][core.CURRENT_ANNEALING_TRANSITION_STATUS_COLUMN] == "Excluded"


def test_build_database_reports_vsm_blocked_transition_statuses(tmp_path: Path) -> None:
    scans = [
        VsmTemperatureScanRecord(
            path=tmp_path / f"scan_{piece}.txt",
            sample=f"Ni55Fe18Ga27 1_{piece}",
            data=pd.DataFrame(),
            key=("Ni55Fe18Ga27", 1, piece, None),
            label=f"scan {piece}",
        )
        for piece in (1, 2)
    ]

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[],
            output_dir=tmp_path / "out",
            export_formats=(),
        ),
        vsm_temperature_scan_records=scans,
        transition_temps={
            "Ni55Fe18Ga27|1|1": {
                "__review_status__": "no_transition",
                "__included__": False,
            },
            "Ni55Fe18Ga27|1|2": {
                "__review_status__": "excluded",
                "__included__": False,
            },
        },
        skip_exports=True,
    )

    rows = {
        row["Microwire"]: row
        for row in result.dataframe.to_dict(orient="records")
    }
    assert pd.isna(rows["1/1"][core.TRANSITION_TEMP_AS_COLUMN])
    assert rows["1/1"][core.VSM_TRANSITION_TEMP_STATUS_COLUMN] == "No transition"
    assert pd.isna(rows["1/2"][core.TRANSITION_TEMP_AS_COLUMN])
    assert rows["1/2"][core.VSM_TRANSITION_TEMP_STATUS_COLUMN] == "Excluded"


def test_build_database_reports_mini_dma_blocked_transition_statuses(tmp_path: Path) -> None:
    records = []
    reviews: dict[str, dict[str, object]] = {}
    for piece, status in (
        (1, builder_ui.MINI_DMA_REVIEW_STATUS_NO_TRANSITION),
        (2, builder_ui.MINI_DMA_REVIEW_STATUS_EXCLUDED),
    ):
        run_path = tmp_path / f"Ni55Fe18Ga27 1_{piece} run"
        record = MiniDmaRecord(
            path=run_path,
            sample=f"Ni55Fe18Ga27 1_{piece}",
            data=pd.DataFrame(),
            key=("Ni55Fe18Ga27", 1, piece, None),
            label=run_path.name,
            transition_summary=(),
        )
        records.append(record)
        reviews[f"{run_path.resolve()}::50 MPa / 1.46 g"] = {
            "status": status,
            "sample": record.sample,
            "run_label": record.label,
            "target_label": "50 MPa / 1.46 g",
        }

    result = build_database(
        BuilderConfig(
            fabrication_files=[],
            annealing_files=[],
            output_dir=tmp_path / "out",
            export_formats=(),
        ),
        mini_dma_records=records,
        mini_dma_transition_reviews=reviews,
        skip_exports=True,
    )

    rows = {
        row["Microwire"]: row
        for row in result.dataframe.to_dict(orient="records")
    }
    assert pd.isna(rows["1/1"][core.MINI_DMA_TRANSITION_COLUMN])
    assert rows["1/1"][core.MINI_DMA_TRANSITION_STATUS_COLUMN] == "No transition"
    assert pd.isna(rows["1/2"][core.MINI_DMA_TRANSITION_COLUMN])
    assert rows["1/2"][core.MINI_DMA_TRANSITION_STATUS_COLUMN] == "Excluded"


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


def test_microscope_key_groups_non_identity_suffixes_with_base_sample() -> None:
    assert core._microscope_key(Path("Ni46Fe27Ga23Cu2Co2 2_1No1 glass.png")) == (
        "Ni46Fe27Ga23Cu2Co2",
        2,
        1,
        None,
    )
    assert core._microscope_key(Path("Ni44Fe27Ga23Cu3Co3 1_1noload core.jpg")) == (
        "Ni44Fe27Ga23Cu3Co3",
        1,
        1,
        None,
    )


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


def test_video_index_to_frame_propagates_draw_level_sources_to_piece_rows() -> None:
    summary = core.VideoMetricsSummary(
        sources={Path("G:/videos/Ni50Fe27Ga23_draw6.mkv")},
        temperatures=[395.0],
        winding_speeds=[71.0],
    )
    fabrication_frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "6/2",
                "Data source": "Measured",
                "e/a": 7.85,
                "Tt est (°C)": 396.9,
                "Draw": 6,
                "Piece": 2,
                "Length (m)": 27.88,
            }
        ]
    )

    frame = builder_ui._video_index_to_frame(
        {("Ni50Fe27Ga23", 6, None): summary},
        fabrication_frame,
    )

    assert len(frame.index) == 1
    row = frame.iloc[0]
    assert row["Microwire"] == "6/2"
    assert row["_sources"] == [str(Path("G:/videos/Ni50Fe27Ga23_draw6.mkv"))]
    assert float(row[builder_ui.CORE_TEMPERATURE_COLUMN]) == pytest.approx(395.0)
    assert float(row["Winding speed (m/min)"]) == pytest.approx(71.0)


def test_video_index_to_frame_prefers_video_metrics_over_fabrication_values() -> None:
    summary = core.VideoMetricsSummary(
        sources={Path("G:/videos/Ni50Fe27Ga23_draw6.mkv")},
        temperatures=[395.0],
        underpressures=[-0.72],
        winding_speeds=[71.0],
        glass_feeds=[4.5],
    )
    fabrication_frame = pd.DataFrame(
        [
            {
                "Composition": "Ni50Fe27Ga23",
                "Microwire": "6/2",
                "Data source": "Measured",
                "e/a": 7.85,
                "Draw": 6,
                "Piece": 2,
                builder_ui.CORE_TEMPERATURE_COLUMN: 320.0,
                "Underpressure": -0.1,
                "Winding speed (m/min)": 20.0,
                "Glass feeding (mm/min)": 1.2,
            }
        ]
    )

    frame = builder_ui._video_index_to_frame(
        {("Ni50Fe27Ga23", 6, None): summary},
        fabrication_frame,
    )

    row = frame.iloc[0]
    assert float(row[builder_ui.CORE_TEMPERATURE_COLUMN]) == pytest.approx(395.0)
    assert float(row["Underpressure"]) == pytest.approx(-0.72)
    assert float(row["Winding speed (m/min)"]) == pytest.approx(71.0)
    assert float(row["Glass feeding (mm/min)"]) == pytest.approx(4.5)


def test_build_database_skips_microscope_crop_output_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        include_microscope_crops=False,
        highlight_ocr_values=True,
    )

    result = build_database(config)

    assert result.microscope_crops == {}
    assert not (config.output_dir / "microscope_crops").exists()


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
        "Figure — other annealing",
    ]
    legacy_row = {
        "Composition": "Ni55Fe18Ga27",
        "Microwire": "4/1",
        "d (µm)": 8.0,
        "D (µm)": 40.0,
        "d/D": 0.2,
        "Length (m)": 5.0,
        "Figure — 1000 mA": "high.png",
        "Figure — other annealing": "low.png",
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
        transition_group = groups.get("Annealing transitions")
        assert isinstance(transition_group, list)
        assert ANNEALING_TRANSITION_COLUMN in transition_group
        assert core.CURRENT_ANNEALING_TRANSITION_STATUS_COLUMN in transition_group
        assert core.CURRENT_ANNEALING_TRANSITION_COUNTS_COLUMN in transition_group
        assert "As1 (mA)" in transition_group
        assert "Af1 (mA)" in transition_group
        assert "As2 (mA)" in transition_group
        assert "Mf2-Af2 (mA)" in transition_group
        assert "Current density" not in groups
        assert "Current annealing transitions" not in groups
        vsm_group = groups.get("VSM transitions")
        assert isinstance(vsm_group, list)
        assert list(core.TRANSITION_TEMP_COLUMNS) == [
            column for column in vsm_group if column in core.TRANSITION_TEMP_COLUMNS
        ]
        assert core.VSM_TRANSITION_TEMP_STATUS_COLUMN in vsm_group
        assert core.VSM_TRANSITION_TEMP_COUNTS_COLUMN in vsm_group
        assert "Transition temps" not in groups
        mini_dma_group = groups.get("TMA")
        assert isinstance(mini_dma_group, list)
        assert core.MINI_DMA_COLUMN in mini_dma_group
        assert core.MINI_DMA_ORIGIN_COLUMN in mini_dma_group
        assert MINI_DMA_STRAIN_COLUMN in mini_dma_group
        assert MINI_DMA_TRANSITION_COLUMN in mini_dma_group
        assert core.MINI_DMA_TRANSITION_STATUS_COLUMN in mini_dma_group
        assert core.MINI_DMA_TRANSITION_COUNTS_COLUMN in mini_dma_group
        assert MINI_DMA_BREAK_COLUMN in mini_dma_group
    finally:
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_builder_transitions_workspace_hosts_peer_views() -> None:
    _ensure_qapp()
    window = BuilderWindow()
    window._auto_open_last = False
    try:
        tab_labels = [
            window.tab_widget.tabText(index)
            for index in range(window.tab_widget.count())
        ]
        assert "Transitions" in tab_labels
        assert "Current annealing transitions" not in tab_labels
        assert "Transition temps" not in tab_labels

        transitions = window.transitions_section
        assert transitions.tab_widget.tabText(0) == "Annealing"
        assert transitions.tab_widget.tabText(1) == "VSM"
        assert transitions.tab_widget.tabText(2) == "TMA"
        assert isinstance(transitions.tab_widget.widget(0), builder_ui._AnnealingTransitionWorkspace)  # noqa: SLF001
        assert isinstance(transitions.tab_widget.widget(1), builder_ui._VsmTransitionWorkspace)  # noqa: SLF001
        assert isinstance(transitions.tab_widget.widget(2), builder_ui._MiniDmaTransitionWorkspace)  # noqa: SLF001
        assert transitions.tab_widget.widget(0) is not window.current_density_section
        assert transitions.tab_widget.widget(1) is not window.transition_temps_section
        assert transitions.tab_widget.widget(2) is not window.dma_transitions_section
        assert transitions.annealing_workspace._dialog is not None  # noqa: SLF001
        assert transitions.annealing_workspace._dialog.findChild(QtWidgets.QTreeWidget) is not None  # noqa: SLF001
        assert transitions.vsm_workspace.tree.headerItem().text(0) == "VSM scan"
        assert transitions.dma_workspace._dialog is not None  # noqa: SLF001
        assert transitions.dma_workspace._dialog.findChild(QtWidgets.QTreeWidget) is not None  # noqa: SLF001
        assert window.current_density_section.section_key == "current_density"
        assert window.transition_temps_section.section_key == "transition_temps"

        window.show_transitions_view("vsm")
        assert window.tab_widget.currentWidget() is transitions
        assert transitions.tab_widget.currentIndex() == 1

        assert window.annealing_section.review_transitions_button.text() == "Transitions..."
        assert window.mini_dma_section.review_transitions_button.text() == "Transitions..."
        assert window.vsm_temperature_transitions_button.text() == "Transitions..."
    finally:
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_project_load_refreshes_visible_transition_workspace_reviews() -> None:
    _ensure_qapp()
    window = BuilderWindow()
    window._auto_open_last = False
    try:
        record = MeasurementRecord(
            path=Path("Ni44Fe27Ga23Cu3Co3 1_2 100mA.txt"),
            metadata=MeasurementMetadata(
                composition_token="Ni44Fe27Ga23Cu3Co3",
                draw_x=1,
                piece_y=2,
                setpoint_mA=100,
                alt_variant=False,
                measurement_id="loaded-record",
                file_name="Ni44Fe27Ga23Cu3Co3 1_2 100mA.txt",
                relpath="Ni44Fe27Ga23Cu3Co3 1_2 100mA.txt",
                timestamp_mtime_utc="2026-06-29T12:00:00+00:00",
            ),
            dataframe=pd.DataFrame({"I_mA": [1.0, 100.0], "R_Ohm": [100.0, 220.0]}),
            sanity_ok=True,
            sanity_error=0.0,
        )
        record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
        window.annealing_section._all_records = [record]  # noqa: SLF001
        window.annealing_section._record_groups = {}  # noqa: SLF001
        window.annealing_section._transition_reviews = {  # noqa: SLF001
            record_id: {
                "transition_record_id": record_id,
                "status": builder_ui.TRANSITION_REVIEW_STATUS_NO_TRANSITION,
                "included": False,
                "source_path": str(record.path),
                "graph_label": record.path.name,
                "sample_key": "Ni44Fe27Ga23Cu3Co3|1|2",
            }
        }

        window.transitions_section.tab_widget.setCurrentIndex(0)
        window._refresh_sections_after_project_load()  # noqa: SLF001

        dialog = window.transitions_section.annealing_workspace._dialog  # noqa: SLF001
        assert dialog is not None
        tree = dialog.findChild(QtWidgets.QTreeWidget)
        assert tree is not None
        assert tree.topLevelItemCount() == 1
        assert tree.topLevelItem(0).text(1) == "No transition"
        assert "Done 1" in dialog._counts_label.text()  # noqa: SLF001
    finally:
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_review_defers_dependent_refresh() -> None:
    _ensure_qapp()
    section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    emitted = 0

    def _record() -> MeasurementRecord:
        return MeasurementRecord(
            path=Path("Ni44Fe27Ga23Cu3Co3 1_2 100mA.txt"),
            metadata=MeasurementMetadata(
                composition_token="Ni44Fe27Ga23Cu3Co3",
                draw_x=1,
                piece_y=2,
                setpoint_mA=100,
                alt_variant=False,
                measurement_id="deferred-review-refresh",
                file_name="Ni44Fe27Ga23Cu3Co3 1_2 100mA.txt",
                relpath="Ni44Fe27Ga23Cu3Co3 1_2 100mA.txt",
                timestamp_mtime_utc="2026-06-29T12:00:00+00:00",
            ),
            dataframe=pd.DataFrame({"I_mA": [1.0, 100.0], "R_Ohm": [100.0, 120.0]}),
            sanity_ok=True,
            sanity_error=0.0,
        )

    def _count_emit() -> None:
        nonlocal emitted
        emitted += 1

    try:
        record = _record()
        record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
        section._all_records = [record]  # noqa: SLF001
        section.data_updated.connect(_count_emit)

        section.set_transition_review_for_record(
            record_id,
            {
                "status": builder_ui.TRANSITION_REVIEW_STATUS_NO_TRANSITION,
                "included": False,
            },
        )

        assert section.transition_reviews_snapshot()[record_id]["status"] == builder_ui.TRANSITION_REVIEW_STATUS_NO_TRANSITION
        assert emitted == 0
    finally:
        section._transition_review_store_timer.stop()  # noqa: SLF001
        section._transition_review_update_timer.stop()  # noqa: SLF001
        section.close()
        section.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_no_transition_defers_next_graph_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()

    def _record(piece: int) -> MeasurementRecord:
        name = f"Ni44Fe27Ga23Cu3Co3 1_{piece} 100mA.txt"
        return MeasurementRecord(
            path=Path(name),
            metadata=MeasurementMetadata(
                composition_token="Ni44Fe27Ga23Cu3Co3",
                draw_x=1,
                piece_y=piece,
                setpoint_mA=100,
                alt_variant=False,
                measurement_id=name,
                file_name=name,
                relpath=name,
                timestamp_mtime_utc="2026-06-29T12:00:00+00:00",
            ),
            dataframe=pd.DataFrame({"I_mA": [1.0, 100.0], "R_Ohm": [100.0, 120.0]}),
            sanity_ok=True,
            sanity_error=0.0,
        )

    stored: dict[str, dict[str, object]] = {}

    def _set_review(record_id: str, payload: dict[str, object]) -> None:
        stored[record_id] = dict(payload)

    dialog = builder_ui._AnnealingTransitionReviewDialog(  # noqa: SLF001
        [_record(1), _record(2)],
        logging.getLogger("test"),
        transition_reviews_provider=lambda: stored,
        transition_reviews_setter=_set_review,
    )
    render_calls = 0

    def _count_render(*_args: object, **_kwargs: object) -> None:
        nonlocal render_calls
        render_calls += 1

    try:
        monkeypatch.setattr(dialog._display, "set_record", _count_render)  # noqa: SLF001
        QtWidgets.QApplication.processEvents()
        render_calls = 0

        dialog._mark_current_no_transition()  # noqa: SLF001

        assert render_calls == 0
        assert len(stored) == 1
        assert next(iter(stored.values()))["status"] == builder_ui.TRANSITION_REVIEW_STATUS_NO_TRANSITION
        assert dialog._current_item is dialog._tree.topLevelItem(1)  # noqa: SLF001
    finally:
        dialog.close()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_view_uses_one_row_per_graph() -> None:
    _ensure_qapp()
    annealing_section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    microscope_section = MicroscopeSection(logging.getLogger("test"), lambda *_args: None)
    section = builder_ui.CurrentDensitySection(
        annealing_section,
        microscope_section,
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        key_text = "Ni44Fe27Ga23Cu3Co3|1|2"

        def _record(setpoint: int) -> MeasurementRecord:
            name = f"Ni44Fe27Ga23Cu3Co3 1_2 {setpoint}mA graph.txt"
            return MeasurementRecord(
                path=Path(name),
                metadata=MeasurementMetadata(
                    composition_token="Ni44Fe27Ga23Cu3Co3",
                    draw_x=1,
                    piece_y=2,
                    setpoint_mA=setpoint,
                    alt_variant=False,
                    measurement_id=name,
                    file_name=name,
                    relpath=name,
                    timestamp_mtime_utc="2026-06-24T00:00:00+00:00",
                ),
                dataframe=pd.DataFrame({"I_mA": [1.0, float(setpoint)], "R_Ohm": [100.0, 120.0]}),
                sanity_ok=True,
                sanity_error=0.0,
            )

        records = [_record(60), _record(80)]
        annealing_section._record_groups = {key_text: records}  # noqa: SLF001
        microscope_section.apply_data(
            MiniDatabaseData(
                table=pd.DataFrame(
                    [
                        {
                            "Composition": "Ni44Fe27Ga23Cu3Co3",
                            "Microwire": "1/2",
                            builder_ui.MICROSCOPE_D_COLUMN: 20.0,
                            "_key": key_text,
                        }
                    ]
                )
            )
        )
        section.refresh_data()
        frame = section.model.frame()

        assert len(frame.index) == 2
        assert frame["_group_key"].tolist() == [key_text, key_text]
        assert frame["_record_id"].nunique() == 2
        assert all("graph" in label for label in frame["Graph"].astype(str))

        captured: list[MeasurementRecord | None] = []
        assert section._preview_panel is not None  # noqa: SLF001
        section._preview_panel.update_selection = lambda _key, high, _other, _values: captured.append(high)  # type: ignore[method-assign]  # noqa: SLF001
        proxy_index = section._search_proxy.mapFromSource(section.model.index(1, 0))  # noqa: SLF001
        section.table_view.setCurrentIndex(proxy_index)
        section.table_view.selectRow(proxy_index.row())
        section._update_preview()  # noqa: SLF001

        assert captured[-1] is records[1]
    finally:
        for widget in (section, annealing_section, microscope_section):
            widget.hide()
            widget.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_current_density_collects_auto_annealing_transition_points() -> None:
    _ensure_qapp()
    annealing_section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    microscope_section = MicroscopeSection(logging.getLogger("test"), lambda *_args: None)
    section = builder_ui.CurrentDensitySection(
        annealing_section,
        microscope_section,
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        annealing_section._phase_points = {}  # noqa: SLF001
        up_current = np.linspace(1.0, 100.0, 160)
        down_current = np.linspace(100.0, 1.0, 160)
        up_drop = np.clip(1.0 - np.abs(up_current - 42.5) / 7.5, 0.0, 1.0)
        down_rise = np.clip((7.0 - down_current) / 3.0, 0.0, 1.0)
        frame = pd.DataFrame(
            {
                "I_mA": np.r_[up_current, down_current],
                "R_Ohm": np.r_[
                    100.0 + (0.12 * up_current) - (12.0 * up_drop),
                    80.0 + (10.0 * down_rise),
                ],
            }
        )
        metadata = MeasurementMetadata(
            composition_token="Ni50Fe27Ga23",
            draw_x=10,
            piece_y=4,
            setpoint_mA=80,
            alt_variant=False,
            measurement_id="auto-transition",
            file_name="Ni50Fe27Ga23 10_4 s2a 80mA.txt",
            relpath="Ni50Fe27Ga23 10_4 s2a 80mA.txt",
            timestamp_mtime_utc="2026-06-08T00:00:00+00:00",
        )
        record = MeasurementRecord(
            path=Path(metadata.file_name),
            metadata=metadata,
            dataframe=frame,
            sanity_ok=True,
            sanity_error=0.0,
        )
        key_text = "Ni50Fe27Ga23|10|4"
        annealing_section._transition_reviews = {}  # noqa: SLF001
        annealing_section._record_groups = {key_text: [record]}  # noqa: SLF001

        auto_points = section._collect_phase_points()  # noqa: SLF001

        key = ("Ni50Fe27Ga23", 10, 4, None)
        assert auto_points[key]["As1"] == pytest.approx(35.0, abs=1.0)
        assert auto_points[key]["Af1"] == pytest.approx(42.5, abs=1.0)
        assert auto_points[key]["Ms1"] == pytest.approx(7.2, abs=1.0)
        assert auto_points[key]["Mf1"] == pytest.approx(3.0, abs=1.0)

        annealing_section._phase_points = {key_text: {"As1": 11.0, "Ms1": 5.0}}  # noqa: SLF001
        legacy_ignored_points = section._collect_phase_points()  # noqa: SLF001
        assert legacy_ignored_points[key]["As1"] == pytest.approx(auto_points[key]["As1"])
        assert legacy_ignored_points[key]["Ms1"] == pytest.approx(auto_points[key]["Ms1"])

        record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
        annealing_section.set_transition_review_for_record(
            record_id,
            {
                "status": builder_ui.TRANSITION_REVIEW_STATUS_MANUAL_ADJUSTED,
                "included": True,
                "auto_values_mA": dict(auto_points[key]),
                "manual_values_mA": {"As1": 11.0, "Ms1": 5.0},
                "final_values_mA": {"As1": 11.0, "Ms1": 5.0},
            },
        )
        merged_points = section._collect_phase_points()  # noqa: SLF001
        assert merged_points[key]["As1"] == pytest.approx(11.0)
        assert merged_points[key]["Ms1"] == pytest.approx(5.0)
        assert merged_points[key]["Af1"] == pytest.approx(auto_points[key]["Af1"])
        assert merged_points[key]["Mf1"] == pytest.approx(auto_points[key]["Mf1"])
    finally:
        for widget in (section, annealing_section, microscope_section):
            widget.hide()
            widget.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_review_prune_remaps_changed_record_id() -> None:
    _ensure_qapp()
    section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    try:
        source_path = Path("G:/current annealing/Ni50Fe27Ga23 6_4a s2 30mA.txt")
        metadata = MeasurementMetadata(
            composition_token="Ni50Fe27Ga23",
            draw_x=6,
            piece_y=4,
            setpoint_mA=30,
            alt_variant=True,
            measurement_id="new-id",
            file_name=source_path.name,
            relpath=source_path.name,
            timestamp_mtime_utc="2026-06-08T00:00:00+00:00",
        )
        record = MeasurementRecord(
            path=source_path,
            metadata=metadata,
            dataframe=pd.DataFrame({"I_mA": [1.0, 30.0], "R_Ohm": [100.0, 120.0]}),
            sanity_ok=True,
            sanity_error=0.0,
        )
        new_record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
        old_record_id = "ca:old-stale-id"
        assert new_record_id != old_record_id
        section._all_records = [record]  # noqa: SLF001
        section._record_groups = {}  # noqa: SLF001
        section._transition_reviews = {  # noqa: SLF001
            old_record_id: {
                "transition_record_id": old_record_id,
                "status": builder_ui.TRANSITION_REVIEW_STATUS_NO_TRANSITION,
                "included": False,
                "source_path": str(source_path),
                "graph_label": source_path.name,
                "sample_key": "Ni50Fe27Ga23|6|4|a",
                "updated_at": "2026-06-19T11:12:33+00:00",
            }
        }

        section._prune_transition_reviews(store=False)  # noqa: SLF001
        snapshot = section.transition_reviews_snapshot()

        assert old_record_id not in snapshot
        assert snapshot[new_record_id]["status"] == builder_ui.TRANSITION_REVIEW_STATUS_NO_TRANSITION
        assert snapshot[new_record_id]["source_path"] == str(source_path)
    finally:
        section.close()


def test_annealing_transition_review_setter_defers_store_save(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    try:
        source_path = Path("Ni50Fe27Ga23 10_4 80mA.txt")
        metadata = MeasurementMetadata(
            composition_token="Ni50Fe27Ga23",
            draw_x=10,
            piece_y=4,
            setpoint_mA=80,
            alt_variant=False,
            measurement_id="manual-transition",
            file_name=source_path.name,
            relpath=source_path.name,
            timestamp_mtime_utc="2026-06-08T00:00:00+00:00",
        )
        record = MeasurementRecord(
            path=source_path,
            metadata=metadata,
            dataframe=pd.DataFrame({"I_mA": [1.0, 80.0], "R_Ohm": [100.0, 120.0]}),
            sanity_ok=True,
            sanity_error=0.0,
        )
        record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
        section._all_records = [record]  # noqa: SLF001
        save_calls: list[object] = []
        monkeypatch.setattr(section.store, "save", lambda data: save_calls.append(data))

        section.set_transition_review_for_record(
            record_id,
            {
                "status": builder_ui.TRANSITION_REVIEW_STATUS_NO_TRANSITION,
                "included": False,
            },
        )

        assert save_calls == []
        assert record_id in section.transition_reviews_snapshot()
        assert builder_ui.TRANSITION_REVIEW_EXTRA_KEY in section.data.extra

        section._store_transition_reviews()  # noqa: SLF001
        assert len(save_calls) == 1
    finally:
        section.close()


def test_current_density_manual_editor_values_persist_to_snapshot() -> None:
    _ensure_qapp()
    annealing_section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    microscope_section = MicroscopeSection(logging.getLogger("test"), lambda *_args: None)
    section = builder_ui.CurrentDensitySection(
        annealing_section,
        microscope_section,
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        key_text = "Ni50Fe27Ga23|10|4"
        annealing_section._transition_reviews = {}  # noqa: SLF001
        metadata = MeasurementMetadata(
            composition_token="Ni50Fe27Ga23",
            draw_x=10,
            piece_y=4,
            setpoint_mA=80,
            alt_variant=False,
            measurement_id="manual-transition",
            file_name="Ni50Fe27Ga23 10_4 80mA.txt",
            relpath="Ni50Fe27Ga23 10_4 80mA.txt",
            timestamp_mtime_utc="2026-06-08T00:00:00+00:00",
        )
        annealing_section._record_groups = {  # noqa: SLF001
            key_text: [
                MeasurementRecord(
                    path=Path(metadata.file_name),
                    metadata=metadata,
                    dataframe=pd.DataFrame({"I_mA": [1.0, 80.0], "R_Ohm": [100.0, 120.0]}),
                    sanity_ok=True,
                    sanity_error=0.0,
                )
            ]
        }
        microscope_section.apply_data(
            MiniDatabaseData(
                table=pd.DataFrame(
                    [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "10/4",
                                builder_ui.MICROSCOPE_D_COLUMN: 20.0,
                                "_key": key_text,
                            }
                    ]
                )
            )
        )
        section.refresh_data()
        section.table_view.selectRow(0)
        section.table_view.setCurrentIndex(section._search_proxy.index(0, 0))  # noqa: SLF001
        QtWidgets.QApplication.processEvents()

        section._apply_phase_values(  # noqa: SLF001
            {
                "As1": 12.0,
                "Af1": 18.0,
                "Ms1": 16.0,
                "Mf1": 9.0,
                "As2": 22.0,
                "Af2": 28.0,
                "Ms2": 26.0,
                "Mf2": 19.0,
            }
        )

        record = annealing_section._record_groups[key_text][0]  # noqa: SLF001
        record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
        stored = annealing_section.transition_reviews_snapshot()
        assert stored[record_id]["final_values_mA"]["Af1"] == pytest.approx(18.0)
        snapshot = section.current_density_snapshot()
        assert snapshot[key_text]["As1 (mA)"] == pytest.approx(12.0)
        assert snapshot[key_text]["Af2 (mA)"] == pytest.approx(28.0)
        assert snapshot[key_text]["As2-As1 (mA)"] == pytest.approx(10.0)
        assert snapshot[key_text]["As current density (A/mm^2)"] == pytest.approx(
            (12.0 / 1000.0) / (np.pi * 0.01 * 0.01)
        )
        assert snapshot[key_text]["J_As1 (A/mm^2)"] == pytest.approx(
            (12.0 / 1000.0) / (np.pi * 0.01 * 0.01)
        )
        assert snapshot[key_text]["J_Af1 (A/mm^2)"] == pytest.approx(
            (18.0 / 1000.0) / (np.pi * 0.01 * 0.01)
        )
    finally:
        for widget in (section, annealing_section, microscope_section):
            widget.hide()
            widget.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_review_pick_writes_selected_manual_label() -> None:
    _ensure_qapp()
    frame = pd.DataFrame({"I_mA": [1.0, 20.0, 40.0], "R_Ohm": [100.0, 95.0, 110.0]})
    metadata = MeasurementMetadata(
        composition_token="Ni50Fe27Ga23",
        draw_x=10,
        piece_y=4,
        setpoint_mA=40,
        alt_variant=False,
        measurement_id="review-transition",
        file_name="Ni50Fe27Ga23 10_4 40mA.txt",
        relpath="Ni50Fe27Ga23 10_4 40mA.txt",
        timestamp_mtime_utc="2026-06-08T00:00:00+00:00",
    )
    record = MeasurementRecord(
        path=Path(metadata.file_name),
        metadata=metadata,
        dataframe=frame,
        sanity_ok=True,
        sanity_error=0.0,
    )
    stored: dict[str, dict[str, object]] = {}

    def _set_values(key: str, values: dict[str, object]) -> None:
        stored[key] = dict(values)

    dialog = builder_ui._AnnealingTransitionReviewDialog(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
        transition_reviews_provider=lambda: stored,
        transition_reviews_setter=_set_values,
    )
    try:
        dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))  # noqa: SLF001
        dialog._phase_controls.set_target("Af1")  # noqa: SLF001
        dialog._handle_plot_pick(21.5)  # noqa: SLF001

        record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
        payload = stored[record_id]
        assert payload["final_values_mA"]["Af1"] == pytest.approx(21.5)
        assert dialog._tree.topLevelItem(0).text(1) == "Manual adjusted"  # noqa: SLF001
        assert "Reviewed: I_Af1 21.5 mA" in dialog._summary_label.text()  # noqa: SLF001
    finally:
        dialog.hide()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_plot_display_single_click_picks_transition_value() -> None:
    _ensure_qapp()
    display = builder_ui._AnnealingPlotDisplay(  # noqa: SLF001
        "Current annealing transition review",
        logging.getLogger("test"),
        show_transition_markers=True,
    )
    picked: list[float] = []
    display.valuePicked.connect(picked.append)
    try:
        display._handle_click(SimpleNamespace(button=1, xdata=18.25))  # noqa: SLF001

        assert picked == [pytest.approx(18.25)]
        assert "18.25 mA" in display.cursor_label.text()
    finally:
        display.hide()
        display.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_markers_use_inline_labels_without_legend_clutter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    figure = builder_ui.Figure(figsize=(4, 3))
    axis = figure.add_subplot(111)
    axis.plot([1.0, 60.0], [100.0, 140.0], label="Increasing 1")
    summary = SimpleNamespace(
        loop_index=1,
        as_current_mA=12.0,
        af_current_mA=24.0,
        ms_current_mA=36.0,
        mf_current_mA=18.0,
    )
    monkeypatch.setattr(builder_ui, "summarize_annealing_transition_loops", lambda _df: (summary,))
    try:
        builder_ui._add_annealing_transition_markers(  # noqa: SLF001
            figure,
            pd.DataFrame({"I_mA": [1.0, 60.0], "R_Ohm": [100.0, 140.0]}),
        )

        text_labels = {text.get_text() for text in axis.texts}
        assert {"As1", "Af1", "Ms1", "Mf1"}.issubset(text_labels)
        legend_labels = axis.get_legend_handles_labels()[1]
        assert legend_labels == ["Increasing 1"]
    finally:
        builder_ui.plt.close(figure)


def test_reviewed_transition_markers_use_inline_current_labels() -> None:
    figure = builder_ui.Figure(figsize=(4, 3))
    axis = figure.add_subplot(111)
    axis.plot([1.0, 60.0], [100.0, 140.0], label="Increasing 1")
    try:
        builder_ui._add_reviewed_transition_markers(  # noqa: SLF001
            figure,
            {"As1": 12.5, "Af1": 42.0},
        )

        text_labels = {text.get_text() for text in axis.texts}
        assert {"I_As1", "I_Af1"}.issubset(text_labels)
        assert all(line.get_label() == "_nolegend_" for line in axis.lines[1:])
    finally:
        builder_ui.plt.close(figure)


def test_phase_point_editor_displays_auto_and_reviewed_values_distinctly() -> None:
    _ensure_qapp()
    controls = builder_ui._PhasePointEditorControls(  # noqa: SLF001
        title="Reviewed transition currents I_As/I_Af/I_Ms/I_Mf (mA)"
    )
    try:
        controls.set_auto_values({"As1": 12.0, "Af1": 24.0})  # noqa: SLF001
        controls.set_values({"As1": 14.0})

        assert controls._auto_labels["As1"].text() == "Auto: 12"  # noqa: SLF001
        assert controls._auto_labels["Af1"].text() == "Auto: 24"  # noqa: SLF001
        assert controls._edits["As1"].text() == "14"  # noqa: SLF001
        assert controls._edits["Af1"].text() == ""  # noqa: SLF001
        assert "#22c55e" in controls._edits["As1"].styleSheet()  # noqa: SLF001
        assert "#fbbf24" in controls._auto_labels["As1"].styleSheet()  # noqa: SLF001
    finally:
        controls.hide()
        controls.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_review_manual_values_are_record_scoped() -> None:
    _ensure_qapp()

    def _record(setpoint: int, name: str) -> MeasurementRecord:
        return MeasurementRecord(
            path=Path(name),
            metadata=MeasurementMetadata(
                composition_token="Ni44Fe27Ga23Cu3Co3",
                draw_x=1,
                piece_y=2,
                setpoint_mA=setpoint,
                alt_variant=False,
                measurement_id=name,
                file_name=name,
                relpath=name,
                timestamp_mtime_utc="2026-06-19T00:00:00+00:00",
            ),
            dataframe=pd.DataFrame({"I_mA": [1.0, float(setpoint)], "R_Ohm": [100.0, 120.0]}),
            sanity_ok=True,
            sanity_error=0.0,
        )

    first = _record(60, "Ni44Fe27Ga23Cu3Co3 1_2 60mA 2loops.txt")
    second = _record(70, "Ni44Fe27Ga23Cu3Co3 1_2 70mA 2loops.txt")
    stored: dict[str, dict[str, object]] = {}

    def _set_values(key: str, values: dict[str, object]) -> None:
        stored[key] = dict(values)

    dialog = builder_ui._AnnealingTransitionReviewDialog(  # noqa: SLF001
        [first, second],
        logging.getLogger("test"),
        transition_reviews_provider=lambda: stored,
        transition_reviews_setter=_set_values,
    )
    try:
        dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))  # noqa: SLF001
        dialog._phase_controls.set_target("As1")  # noqa: SLF001
        dialog._handle_plot_pick(37.428)  # noqa: SLF001
        dialog._handle_plot_pick(38.125)  # noqa: SLF001
        dialog._phase_controls.set_target("Af1")  # noqa: SLF001
        dialog._handle_plot_pick(52.961)  # noqa: SLF001

        first_id = builder_ui._transition_record_id_for_annealing_record(first)  # noqa: SLF001
        second_id = builder_ui._transition_record_id_for_annealing_record(second)  # noqa: SLF001
        assert first_id in stored
        assert second_id not in stored

        dialog._tree.setCurrentItem(dialog._tree.topLevelItem(1))  # noqa: SLF001
        values = dialog._phase_controls.values()  # noqa: SLF001
        assert values["As1"] is None
        assert values["Af1"] is None
        assert stored[first_id]["final_values_mA"]["As1"] == pytest.approx(38.125)
        assert stored[first_id]["final_values_mA"]["Af1"] == pytest.approx(52.961)
    finally:
        dialog.hide()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_review_actions_update_visible_statuses() -> None:
    _ensure_qapp()

    def _record(setpoint: int, name: str) -> MeasurementRecord:
        return MeasurementRecord(
            path=Path(name),
            metadata=MeasurementMetadata(
                composition_token="Ni44Fe27Ga23Cu3Co3",
                draw_x=1,
                piece_y=2,
                setpoint_mA=setpoint,
                alt_variant=False,
                measurement_id=name,
                file_name=name,
                relpath=name,
                timestamp_mtime_utc="2026-06-19T00:00:00+00:00",
            ),
            dataframe=pd.DataFrame({"I_mA": [1.0, float(setpoint)], "R_Ohm": [100.0, 120.0]}),
            sanity_ok=True,
            sanity_error=0.0,
        )

    records = [
        _record(60, "Ni44Fe27Ga23Cu3Co3 1_2 60mA 2loops.txt"),
        _record(70, "Ni44Fe27Ga23Cu3Co3 1_2 70mA 2loops.txt"),
        _record(80, "Ni44Fe27Ga23Cu3Co3 1_2 80mA 2loops.txt"),
    ]
    stored: dict[str, dict[str, object]] = {}

    def _set_values(key: str, values: dict[str, object]) -> None:
        stored[key] = dict(values)

    dialog = builder_ui._AnnealingTransitionReviewDialog(  # noqa: SLF001
        records,
        logging.getLogger("test"),
        transition_reviews_provider=lambda: stored,
        transition_reviews_setter=_set_values,
    )
    try:
        dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))  # noqa: SLF001
        dialog._phase_controls.set_target("As1")  # noqa: SLF001
        dialog._handle_plot_pick(12.5)  # noqa: SLF001
        dialog._accept_current_and_next()  # noqa: SLF001
        assert dialog._tree.topLevelItem(0).text(1) == "Accepted"  # noqa: SLF001

        dialog._mark_current_no_transition()  # noqa: SLF001
        assert dialog._tree.topLevelItem(1).text(1) == "No transition"  # noqa: SLF001

        dialog._exclude_current_graph()  # noqa: SLF001
        assert dialog._tree.topLevelItem(2).text(1) == "Excluded"  # noqa: SLF001
        detail = dialog._summary_label.text()  # noqa: SLF001
        assert "Review state: Excluded" in detail
        assert "Review state: Unreviewed" not in detail
        assert "No transition" not in detail
    finally:
        dialog.hide()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_review_counts_update_after_actions() -> None:
    _ensure_qapp()

    def _record(setpoint: int, name: str) -> MeasurementRecord:
        return MeasurementRecord(
            path=Path(name),
            metadata=MeasurementMetadata(
                composition_token="Ni44Fe27Ga23Cu3Co3",
                draw_x=1,
                piece_y=2,
                setpoint_mA=setpoint,
                alt_variant=False,
                measurement_id=name,
                file_name=name,
                relpath=name,
                timestamp_mtime_utc="2026-06-19T00:00:00+00:00",
            ),
            dataframe=pd.DataFrame({"I_mA": [1.0, float(setpoint)], "R_Ohm": [100.0, 120.0]}),
            sanity_ok=True,
            sanity_error=0.0,
        )

    records = [
        _record(60, "Ni44Fe27Ga23Cu3Co3 1_2 60mA 2loops.txt"),
        _record(70, "Ni44Fe27Ga23Cu3Co3 1_2 70mA 2loops.txt"),
        _record(80, "Ni44Fe27Ga23Cu3Co3 1_2 80mA 2loops.txt"),
    ]
    stored: dict[str, dict[str, object]] = {}

    def _set_values(key: str, values: dict[str, object]) -> None:
        stored[key] = dict(values)

    dialog = builder_ui._AnnealingTransitionReviewDialog(  # noqa: SLF001
        records,
        logging.getLogger("test"),
        transition_reviews_provider=lambda: stored,
        transition_reviews_setter=_set_values,
    )
    try:
        assert "Total 3" in dialog._counts_label.text()  # noqa: SLF001
        assert "Open 3" in dialog._counts_label.text()  # noqa: SLF001

        dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))  # noqa: SLF001
        dialog._phase_controls.set_target("As1")  # noqa: SLF001
        dialog._handle_plot_pick(12.5)  # noqa: SLF001
        assert "Done 1" in dialog._counts_label.text()  # noqa: SLF001
        assert "Manual 1" in dialog._counts_label.text()  # noqa: SLF001
        assert "Open 2" in dialog._counts_label.text()  # noqa: SLF001

        dialog._tree.setCurrentItem(dialog._tree.topLevelItem(1))  # noqa: SLF001
        dialog._mark_current_no_transition()  # noqa: SLF001
        assert "Done 2" in dialog._counts_label.text()  # noqa: SLF001
        assert "No transition 1" in dialog._counts_label.text()  # noqa: SLF001

        dialog._tree.setCurrentItem(dialog._tree.topLevelItem(2))  # noqa: SLF001
        dialog._exclude_current_graph()  # noqa: SLF001
        assert "Done 3" in dialog._counts_label.text()  # noqa: SLF001
        assert "Open 0" in dialog._counts_label.text()  # noqa: SLF001
        assert "Excluded 1" in dialog._counts_label.text()  # noqa: SLF001
    finally:
        dialog.hide()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_review_pick_updates_markers_in_place() -> None:
    _ensure_qapp()
    record = MeasurementRecord(
        path=Path("Ni44Fe27Ga23Cu3Co3 1_2 60mA 2loops.txt"),
        metadata=MeasurementMetadata(
            composition_token="Ni44Fe27Ga23Cu3Co3",
            draw_x=1,
            piece_y=2,
            setpoint_mA=60,
            alt_variant=False,
            measurement_id="review-in-place",
            file_name="Ni44Fe27Ga23Cu3Co3 1_2 60mA 2loops.txt",
            relpath="Ni44Fe27Ga23Cu3Co3 1_2 60mA 2loops.txt",
            timestamp_mtime_utc="2026-06-19T00:00:00+00:00",
        ),
        dataframe=pd.DataFrame({"I_mA": [1.0, 30.0, 60.0], "R_Ohm": [100.0, 112.0, 120.0]}),
        sanity_ok=True,
        sanity_error=0.0,
    )
    stored: dict[str, dict[str, object]] = {}

    def _set_values(key: str, values: dict[str, object]) -> None:
        stored[key] = dict(values)

    dialog = builder_ui._AnnealingTransitionReviewDialog(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
        transition_reviews_provider=lambda: stored,
        transition_reviews_setter=_set_values,
    )
    try:
        dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))  # noqa: SLF001
        original_canvas = dialog._display._canvas  # noqa: SLF001
        dialog._phase_controls.set_target("Af1")  # noqa: SLF001
        dialog._handle_plot_pick(24.5)  # noqa: SLF001
        QtWidgets.QApplication.processEvents()

        assert dialog._display._canvas is original_canvas  # noqa: SLF001
        assert original_canvas is not None
        axis = original_canvas.figure.axes[0]
        gids = {
            artist.get_gid()
            for artist in list(axis.lines) + list(axis.texts)
            if hasattr(artist, "get_gid")
        }
        assert "reviewed_transition_marker" in gids
        assert "reviewed_transition_label" in gids
    finally:
        dialog.hide()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_review_drag_marker_persists_without_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    record = MeasurementRecord(
        path=Path("Ni44Fe27Ga23Cu3Co3 1_2 60mA drag.txt"),
        metadata=MeasurementMetadata(
            composition_token="Ni44Fe27Ga23Cu3Co3",
            draw_x=1,
            piece_y=2,
            setpoint_mA=60,
            alt_variant=False,
            measurement_id="review-drag",
            file_name="Ni44Fe27Ga23Cu3Co3 1_2 60mA drag.txt",
            relpath="Ni44Fe27Ga23Cu3Co3 1_2 60mA drag.txt",
            timestamp_mtime_utc="2026-06-19T00:00:00+00:00",
        ),
        dataframe=pd.DataFrame({"I_mA": [1.0, 30.0, 60.0], "R_Ohm": [100.0, 112.0, 120.0]}),
        sanity_ok=True,
        sanity_error=0.0,
    )
    stored: dict[str, dict[str, object]] = {}

    def _set_values(key: str, values: dict[str, object]) -> None:
        stored[key] = dict(values)

    dialog = builder_ui._AnnealingTransitionReviewDialog(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
        transition_reviews_provider=lambda: stored,
        transition_reviews_setter=_set_values,
    )
    calls = 0
    original = dialog._display.set_record  # noqa: SLF001

    def _counting_set_record(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(dialog._display, "set_record", _counting_set_record)  # noqa: SLF001
    try:
        dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))  # noqa: SLF001
        dialog._phase_controls.set_target("Af1")  # noqa: SLF001
        dialog._handle_plot_pick(24.5)  # noqa: SLF001
        canvas = dialog._display._canvas  # noqa: SLF001
        assert canvas is not None
        axis = canvas.figure.axes[0]
        calls = 0

        dialog._display._handle_button_press(  # noqa: SLF001
            SimpleNamespace(button=1, xdata=24.5, canvas=canvas, inaxes=axis)
        )
        dialog._display._handle_motion(  # noqa: SLF001
            SimpleNamespace(button=1, xdata=31.25, canvas=canvas, inaxes=axis)
        )
        assert calls == 0
        dialog._display._handle_button_release(  # noqa: SLF001
            SimpleNamespace(button=1, xdata=31.25, canvas=canvas, inaxes=axis)
        )

        record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
        assert stored[record_id]["manual_values_mA"]["Af1"] == pytest.approx(31.25)
        assert calls == 0
    finally:
        dialog.hide()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_review_clear_selected_keeps_partial_values() -> None:
    _ensure_qapp()
    record = MeasurementRecord(
        path=Path("Ni44Fe27Ga23Cu3Co3 1_2 partial.txt"),
        metadata=MeasurementMetadata(
            composition_token="Ni44Fe27Ga23Cu3Co3",
            draw_x=1,
            piece_y=2,
            setpoint_mA=60,
            alt_variant=False,
            measurement_id="partial-clear",
            file_name="Ni44Fe27Ga23Cu3Co3 1_2 partial.txt",
            relpath="Ni44Fe27Ga23Cu3Co3 1_2 partial.txt",
            timestamp_mtime_utc="2026-06-19T00:00:00+00:00",
        ),
        dataframe=pd.DataFrame({"I_mA": [1.0, 30.0, 60.0], "R_Ohm": [100.0, 112.0, 120.0]}),
        sanity_ok=True,
        sanity_error=0.0,
    )
    stored: dict[str, dict[str, object]] = {}

    def _set_values(key: str, values: dict[str, object]) -> None:
        stored[key] = dict(values)

    dialog = builder_ui._AnnealingTransitionReviewDialog(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
        transition_reviews_provider=lambda: stored,
        transition_reviews_setter=_set_values,
    )
    try:
        for label, value in {"As1": 10.0, "Af1": 20.0, "Ms1": 30.0, "Mf1": 40.0}.items():
            dialog._phase_controls.set_target(label)  # noqa: SLF001
            dialog._handle_plot_pick(value)  # noqa: SLF001
        for label in ("Ms1", "Mf1"):
            dialog._phase_controls.set_target(label)  # noqa: SLF001
            dialog._phase_controls._clear_selected()  # noqa: SLF001

        record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
        payload = stored[record_id]
        assert payload["status"] == builder_ui.TRANSITION_REVIEW_STATUS_MANUAL_ADJUSTED
        assert payload["manual_values_mA"] == {"As1": pytest.approx(10.0), "Af1": pytest.approx(20.0)}
        assert dialog._tree.topLevelItem(0).text(1) == "Manual adjusted"  # noqa: SLF001
    finally:
        dialog.hide()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_review_accept_next_redraws_only_next_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()

    def _record(setpoint: int, name: str) -> MeasurementRecord:
        return MeasurementRecord(
            path=Path(name),
            metadata=MeasurementMetadata(
                composition_token="Ni44Fe27Ga23Cu3Co3",
                draw_x=1,
                piece_y=2,
                setpoint_mA=setpoint,
                alt_variant=False,
                measurement_id=name,
                file_name=name,
                relpath=name,
                timestamp_mtime_utc="2026-06-19T00:00:00+00:00",
            ),
            dataframe=pd.DataFrame({"I_mA": [1.0, float(setpoint)], "R_Ohm": [100.0, 120.0]}),
            sanity_ok=True,
            sanity_error=0.0,
        )

    records = [
        _record(60, "Ni44Fe27Ga23Cu3Co3 1_2 60mA 2loops.txt"),
        _record(70, "Ni44Fe27Ga23Cu3Co3 1_2 70mA 2loops.txt"),
    ]
    stored: dict[str, dict[str, object]] = {}

    def _set_values(key: str, values: dict[str, object]) -> None:
        stored[key] = dict(values)

    dialog = builder_ui._AnnealingTransitionReviewDialog(  # noqa: SLF001
        records,
        logging.getLogger("test"),
        transition_reviews_provider=lambda: stored,
        transition_reviews_setter=_set_values,
    )
    calls = 0
    original = dialog._display.set_record  # noqa: SLF001

    def _counting_set_record(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(dialog._display, "set_record", _counting_set_record)  # noqa: SLF001
    try:
        dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))  # noqa: SLF001
        calls = 0
        dialog._phase_controls.set_target("As1")  # noqa: SLF001
        dialog._handle_plot_pick(12.5)  # noqa: SLF001
        dialog._accept_current_and_next()  # noqa: SLF001

        assert calls == 1
        assert dialog._current_record_id == builder_ui._transition_record_id_for_annealing_record(records[1])  # noqa: SLF001
    finally:
        dialog.hide()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_transition_review_common_actions_are_subsecond() -> None:
    _ensure_qapp()
    from scripts.benchmark_current_annealing_review import run_benchmark

    results = run_benchmark(iterations=2, record_count=8)

    for action in (
        "graph_click_line_placement",
        "accept_next",
        "no_transition_next",
        "exclude_graph_next",
        "next_unreviewed",
    ):
        median_ms = float(results["actions"][action]["median_ms"])
        assert median_ms < 1000.0, f"{action} median was {median_ms:.1f} ms"


def test_builder_review_smoothness_benchmark_runs_headless() -> None:
    _ensure_qapp()
    from scripts.benchmark_builder_review_smoothness import run_benchmark

    results = run_benchmark(iterations=1, record_count=6)

    assert "current_annealing" in results
    assert "mini_dma" in results
    mini_accept = results["mini_dma"]["actions"]["accept_next"]
    assert float(mini_accept["median_ms"]) < 1000.0
    assert "event_loop_max_lag_ms" in mini_accept


def test_annealing_transition_review_no_transition_detail_is_not_excluded() -> None:
    _ensure_qapp()
    record = MeasurementRecord(
        path=Path("Ni44Fe27Ga23Cu3Co3 1_2 60mA 2loops.txt"),
        metadata=MeasurementMetadata(
            composition_token="Ni44Fe27Ga23Cu3Co3",
            draw_x=1,
            piece_y=2,
            setpoint_mA=60,
            alt_variant=False,
            measurement_id="no-transition-status",
            file_name="Ni44Fe27Ga23Cu3Co3 1_2 60mA 2loops.txt",
            relpath="Ni44Fe27Ga23Cu3Co3 1_2 60mA 2loops.txt",
            timestamp_mtime_utc="2026-06-19T00:00:00+00:00",
        ),
        dataframe=pd.DataFrame({"I_mA": [1.0, 60.0], "R_Ohm": [100.0, 120.0]}),
        sanity_ok=True,
        sanity_error=0.0,
    )
    stored: dict[str, dict[str, object]] = {}

    def _set_values(key: str, values: dict[str, object]) -> None:
        stored[key] = dict(values)

    dialog = builder_ui._AnnealingTransitionReviewDialog(  # noqa: SLF001
        [record],
        logging.getLogger("test"),
        transition_reviews_provider=lambda: stored,
        transition_reviews_setter=_set_values,
    )
    try:
        dialog._mark_current_no_transition()  # noqa: SLF001
        detail = dialog._summary_label.text()  # noqa: SLF001

        assert dialog._tree.topLevelItem(0).text(1) == "No transition"  # noqa: SLF001
        assert "Review state: No transition" in detail
        assert "Excluded from current-density" not in detail
        record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
        assert stored[record_id]["status"] == builder_ui.TRANSITION_REVIEW_STATUS_NO_TRANSITION
        assert stored[record_id]["included"] is False
    finally:
        dialog.hide()
        dialog.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_current_density_ignores_excluded_transition_review_records() -> None:
    _ensure_qapp()
    annealing_section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    microscope_section = MicroscopeSection(logging.getLogger("test"), lambda *_args: None)
    section = builder_ui.CurrentDensitySection(
        annealing_section,
        microscope_section,
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        key_text = "Ni50Fe27Ga23|10|4"
        annealing_section._transition_reviews = {}  # noqa: SLF001
        record = MeasurementRecord(
            path=Path("Ni50Fe27Ga23 10_4 80mA.txt"),
            metadata=MeasurementMetadata(
                composition_token="Ni50Fe27Ga23",
                draw_x=10,
                piece_y=4,
                setpoint_mA=80,
                alt_variant=False,
                measurement_id="excluded-transition",
                file_name="Ni50Fe27Ga23 10_4 80mA.txt",
                relpath="Ni50Fe27Ga23 10_4 80mA.txt",
                timestamp_mtime_utc="2026-06-19T00:00:00+00:00",
            ),
            dataframe=pd.DataFrame({"I_mA": [1.0, 80.0], "R_Ohm": [100.0, 120.0]}),
            sanity_ok=True,
            sanity_error=0.0,
        )
        annealing_section._record_groups = {key_text: [record]}  # noqa: SLF001
        record_id = builder_ui._transition_record_id_for_annealing_record(record)  # noqa: SLF001
        annealing_section.set_transition_review_for_record(
            record_id,
            {
                "status": builder_ui.TRANSITION_REVIEW_STATUS_EXCLUDED,
                "included": False,
                "final_values_mA": {"As1": 12.0, "Af1": 18.0},
            },
        )

        phase_points = section._collect_reviewed_transition_phase_points()  # noqa: SLF001

        assert ("Ni50Fe27Ga23", 10, 4, None) not in phase_points
        assert annealing_section.transition_reviews_snapshot()[record_id]["status"] == (
            builder_ui.TRANSITION_REVIEW_STATUS_EXCLUDED
        )
    finally:
        for widget in (section, annealing_section, microscope_section):
            widget.hide()
            widget.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_annealing_section_maps_legacy_columns_and_keeps_horizontal_scrolling() -> None:
    _ensure_qapp()
    section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni55Fe18Ga27",
                    "Microwire": "4/1",
                    "Graph — 1000 mA": "high.png",
                    "Graph — low mA": "legacy-low.png",
                    "Graph — other mA": "legacy-other.png",
                    "_group_key": "Ni55Fe18Ga27|4|1",
                    "_sources": [],
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))

        applied = section.model.frame()
        assert "Graph — 1000 mA" in applied.columns
        assert builder_ui.ANNEALING_OTHER_GRAPH_COLUMN in applied.columns
        assert "Graph — low mA" not in applied.columns
        assert "Graph — other mA" not in applied.columns

        header = section.table_view.horizontalHeader()
        assert header is not None
        assert header.stretchLastSection() is False
        assert (
            section.table_view.horizontalScrollMode()
            == QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_background_matches_same_sample_rows() -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        frame = pd.DataFrame(
            [
                {
                    "Composition": "A",
                    "Microwire": "1/1",
                    builder_ui._SHAPE_MEMORY_GROUP_KEY_COLUMN: "A|1|1",
                    builder_ui._SHAPE_MEMORY_GROUP_ORDER_COLUMN: 0,
                },
                {
                    "Composition": "A",
                    "Microwire": "1/1",
                    builder_ui._SHAPE_MEMORY_GROUP_KEY_COLUMN: "A|1|1",
                    builder_ui._SHAPE_MEMORY_GROUP_ORDER_COLUMN: 1,
                },
                {
                    "Composition": "B",
                    "Microwire": "1/1",
                    builder_ui._SHAPE_MEMORY_GROUP_KEY_COLUMN: "B|1|1",
                    builder_ui._SHAPE_MEMORY_GROUP_ORDER_COLUMN: 0,
                },
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        model_frame = section.model.frame()
        first = section._background_brush_for_cell(model_frame.iloc[0], "Composition")
        second = section._background_brush_for_cell(model_frame.iloc[1], "Composition")
        third = section._background_brush_for_cell(model_frame.iloc[2], "Composition")
        assert first is not None and second is not None and third is not None
        assert first.color().name() == second.color().name()
        assert first.color().name() != third.color().name()
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_can_hide_graph_column() -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN: "30mA",
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={"graph_column_visible": True}))
        assert section._graph_column_toggle is not None
        graph_index = int(section.model.frame().columns.get_loc(builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN))
        assert section.table_view is not None
        assert section.table_view.isColumnHidden(graph_index) is False
        section._graph_column_toggle.setChecked(False)
        assert section.table_view.isColumnHidden(graph_index) is True
        assert section.data.extra["graph_column_visible"] is False
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_hiding_graph_column_restores_normal_row_height() -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN: "30mA",
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={"graph_column_visible": True}))
        assert section.table_view is not None
        expanded_height = section.table_view.verticalHeader().defaultSectionSize()
        section._graph_column_toggle.setChecked(False)
        collapsed_height = section.table_view.verticalHeader().defaultSectionSize()
        assert collapsed_height < expanded_height
        assert collapsed_height <= section.table_view.fontMetrics().height() + 8
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_does_not_infer_current_without_linked_source() -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/1",
                    "_sources": ["Ni50Fe27Ga23 12_1 0mA.txt"],
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.591,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.441,
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        updated = section.model.frame()
        assert pd.isna(updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_COLUMN])
        assert pd.isna(updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_DENSITY_COLUMN])
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_expanded_empty_rows_do_not_keep_currents(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        standard_path = tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt"
        empty_path = tmp_path / "Ni50Fe27Ga23 10_1 70mA.txt"
        fracture_path = tmp_path / "Ni50Fe27Ga23 10_1 30mA fracture.txt"
        for path in (standard_path, empty_path, fracture_path):
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
        section.data.extra["record_entries"] = {
            str(standard_path): {
                SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                SHAPE_MEMORY_STRAIN_COLUMN: 18.591,
                SHAPE_MEMORY_STRESS_COLUMN: 568.441,
            },
            str(fracture_path): {
                SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 11.518,
                SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 21.058,
                SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 935.329,
            },
        }
        result = section.process([standard_path, empty_path, fracture_path])
        section._handle_worker_finished(result)
        frame = section.model.frame()
        empty_row = frame.iloc[1]
        assert pd.isna(empty_row[builder_ui.SHAPE_MEMORY_CURRENT_COLUMN])
        assert pd.isna(empty_row[builder_ui.SHAPE_MEMORY_CURRENT_DENSITY_COLUMN])
        assert pd.isna(empty_row[builder_ui.SHAPE_MEMORY_STRAIN_COLUMN])
        assert pd.isna(empty_row[builder_ui.SHAPE_MEMORY_STRESS_COLUMN])
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_does_not_keep_current_for_sample_fallback_values(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        path = tmp_path / "Ni50Fe27Ga23 12_1 0mA.txt"
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
        section.store.load_payload = lambda key, *_args, **_kwargs: [  # type: ignore[method-assign]
            ShapeMemoryStressStrainRecord(
                path=path,
                sample="Ni50Fe27Ga23 12/1",
                data=pd.DataFrame(),
                key=("Ni50Fe27Ga23", 12, 1),
                label="0mA",
            )
        ] if key == "shape_memory_stress_strain_records" else None
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/1",
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.591,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.441,
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        updated = section.model.frame()
        assert pd.isna(updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_COLUMN])
        assert pd.isna(updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_DENSITY_COLUMN])
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_expanded_rows_fill_current_density_from_microscope_snapshot(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        standard_path = tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt"
        standard_path.write_text(
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
        section.store.load_payload = lambda key, *_args, **_kwargs: [  # type: ignore[method-assign]
            ShapeMemoryStressStrainRecord(
                path=standard_path,
                sample="Ni50Fe27Ga23 10/1",
                data=pd.DataFrame(),
                key=("Ni50Fe27Ga23", 10, 1),
                label="30mA",
            )
        ] if key == "shape_memory_stress_strain_records" else None
        section.set_microscope_snapshot(
            pd.DataFrame(
                [
                    {
                        "Composition": "Ni50Fe27Ga23",
                        "Microwire": "10/1",
                        builder_ui.MICROSCOPE_D_COLUMN: 12.4,
                        "_key": "Ni50Fe27Ga23|10|1",
                    }
                ]
            )
        )
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN: str(standard_path),
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.591,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.441,
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        updated = section.model.frame()
        assert updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == pytest.approx(30.0)
        assert updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_DENSITY_COLUMN] == pytest.approx(
            248.42082688641315,
            rel=1e-6,
        )
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_fills_current_density_without_microscope_key_column(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        standard_path = tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt"
        standard_path.write_text("stub", encoding="utf-8")
        section.store.load_payload = lambda key, *_args, **_kwargs: [  # type: ignore[method-assign]
            ShapeMemoryStressStrainRecord(
                path=standard_path,
                sample="Ni50Fe27Ga23 10/1",
                data=pd.DataFrame(),
                key=("Ni50Fe27Ga23", 10, 1),
                label="30mA",
            )
        ] if key == "shape_memory_stress_strain_records" else None
        section.set_microscope_snapshot(
            pd.DataFrame(
                [
                    {
                        "Composition": "Ni50Fe27Ga23",
                        "Microwire": "10/1",
                        builder_ui.MICROSCOPE_D_COLUMN: 12.4,
                    }
                ]
            )
        )
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN: str(standard_path),
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.591,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.441,
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        updated = section.model.frame()
        assert updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == pytest.approx(30.0)
        assert updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_DENSITY_COLUMN] == pytest.approx(
            248.42082688641315,
            rel=1e-6,
        )
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_expands_saved_rows_without_live_payloads(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        standard_path = tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt"
        fracture_path = tmp_path / "Ni50Fe27Ga23 10_1 fracture 30mA.txt"
        standard_path.write_text("stub", encoding="utf-8")
        fracture_path.write_text("stub", encoding="utf-8")
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN: [
                        "30mA",
                        "fracture 30mA",
                    ],
                    "_sources": [str(standard_path), str(fracture_path)],
                    builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN: str(standard_path),
                    builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN: str(fracture_path),
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.591,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.441,
                    builder_ui.SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 11.518,
                    builder_ui.SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 21.058,
                    builder_ui.SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 935.329,
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        section.set_microscope_snapshot(
            pd.DataFrame(
                [
                    {
                        "Composition": "Ni50Fe27Ga23",
                        "Microwire": "10/1",
                        builder_ui.MICROSCOPE_D_COLUMN: 12.4,
                    }
                ]
            )
        )
        updated = section.model.frame()
        assert len(updated.index) == 2
        assert list(updated[builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN]) == [
            "30mA",
            "fracture 30mA",
        ]
        assert updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_COLUMN] == pytest.approx(30.0)
        assert updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_DENSITY_COLUMN] == pytest.approx(
            248.42082688641315,
            rel=1e-6,
        )
        assert updated.at[1, builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_COLUMN] == pytest.approx(
            30.0
        )
        assert updated.at[
            1, builder_ui.SHAPE_MEMORY_FRACTURE_CURRENT_DENSITY_COLUMN
        ] == pytest.approx(248.42082688641315, rel=1e-6)
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_drops_duplicate_placeholder_rows_without_live_payloads(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        standard_path = tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt"
        fracture_path = tmp_path / "Ni50Fe27Ga23 10_1 fracture 30mA.txt"
        standard_path.write_text("stub", encoding="utf-8")
        fracture_path.write_text("stub", encoding="utf-8")
        section.store.load_payload = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN: "30mA",
                    "_sources": [str(standard_path)],
                    builder_ui._SHAPE_MEMORY_GROUP_KEY_COLUMN: "Ni50Fe27Ga23|10|1",
                    builder_ui._SHAPE_MEMORY_GROUP_ORDER_COLUMN: 0,
                    builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN: str(standard_path),
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.591,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.441,
                },
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN: "fracture 30mA",
                    "_sources": [str(fracture_path)],
                    builder_ui._SHAPE_MEMORY_GROUP_KEY_COLUMN: "Ni50Fe27Ga23|10|1",
                    builder_ui._SHAPE_MEMORY_GROUP_ORDER_COLUMN: 1,
                    builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN: str(fracture_path),
                    builder_ui.SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 11.518,
                    builder_ui.SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 21.058,
                    builder_ui.SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 935.329,
                },
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    builder_ui._SHAPE_MEMORY_GROUP_KEY_COLUMN: "Ni50Fe27Ga23|10|1",
                    builder_ui._SHAPE_MEMORY_GROUP_ORDER_COLUMN: 2,
                },
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        updated = section.model.frame()
        assert len(updated.index) == 2
        assert list(updated[builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN]) == [
            "30mA",
            "fracture 30mA",
        ]
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_ignores_stale_cached_payloads_when_payload_map_missing(
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        stale_path = tmp_path / "Ni50Fe27Ga23 10_1 70mA.txt"
        standard_path = tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt"
        fracture_path = tmp_path / "Ni50Fe27Ga23 10_1 fracture 30mA.txt"
        stale_path.write_text("stub", encoding="utf-8")
        standard_path.write_text("stub", encoding="utf-8")
        fracture_path.write_text("stub", encoding="utf-8")
        section.store.save_payload(
            "shape_memory_stress_strain_records",
            [
                ShapeMemoryStressStrainRecord(
                    path=stale_path,
                    sample="Ni50Fe27Ga23 10/1",
                    data=pd.DataFrame(),
                    key=("Ni50Fe27Ga23", 10, 1),
                    label="70mA",
                )
            ],
        )
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN: [
                        "30mA",
                        "fracture 30mA",
                    ],
                    "_sources": [str(standard_path), str(fracture_path)],
                    builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN: str(standard_path),
                    builder_ui._SHAPE_MEMORY_FRACTURE_SOURCE_COLUMN: str(fracture_path),
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.591,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.441,
                    builder_ui.SHAPE_MEMORY_FRACTURE_LOAD_COLUMN: 11.518,
                    builder_ui.SHAPE_MEMORY_FRACTURE_STRAIN_COLUMN: 21.058,
                    builder_ui.SHAPE_MEMORY_FRACTURE_STRESS_COLUMN: 935.329,
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        updated = section.model.frame()
        assert len(updated.index) == 2
        assert list(updated[builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN]) == [
            "30mA",
            "fracture 30mA",
        ]
        assert "70mA" not in "".join(str(value) for value in updated[builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN])
    finally:
        section.store.clear_payload("shape_memory_stress_strain_records")
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_preview_panel_reuses_existing_tabs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    panel = builder_ui._ShapeMemoryPreviewPanel(logging.getLogger("test"))
    record = ShapeMemoryStressStrainRecord(
        path=tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt",
        sample="Ni50Fe27Ga23 10/1",
        data=pd.DataFrame(
            {
                "displacement_mm": [0.0, 0.01],
                "load_g": [0.0, 0.10],
                "strain_pct": [0.0, 0.05],
                "stress_mpa": [0.0, 0.9],
            }
        ),
        key=("Ni50Fe27Ga23", 10, 1),
        label="30mA",
    )
    call_count = {"count": 0}
    original = builder_ui._plot_shape_memory_stress_strain_figure

    def _wrapped(*args: object, **kwargs: object) -> object:
        call_count["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(builder_ui, "_plot_shape_memory_stress_strain_figure", _wrapped)
    try:
        panel.update_selection("Ni50Fe27Ga23 10/1", [record])
        assert call_count["count"] == 1
        assert len(panel._tab_canvases) == 1
        first_canvas = panel._tab_canvases[0]
        panel.update_selection("Ni50Fe27Ga23 10/1", [record])
        assert call_count["count"] == 1
        assert len(panel._tab_canvases) == 1
        assert panel._tab_canvases[0] is first_canvas
    finally:
        panel.close()


def test_annealing_section_widens_other_graph_column_for_multiple_measurements() -> None:
    _ensure_qapp()
    section = builder_ui.AnnealingSection(logging.getLogger("test"), lambda *_args: None)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni55Fe18Ga27",
                    "Microwire": "4/1",
                    builder_ui.ANNEALING_HIGH_GRAPH_COLUMN: "high.png",
                    builder_ui.ANNEALING_OTHER_GRAPH_COLUMN: ["low-1.png", "low-2.png", "low-3.png"],
                    "Other annealing files": ["low-1.txt", "low-2.txt", "low-3.txt"],
                    "_group_key": "Ni55Fe18Ga27|4|1",
                    "_sources": [],
                }
            ]
        )
        section.apply_data(MiniDatabaseData(table=frame, extra={}))
        _ensure_qapp().processEvents()

        expected_width = (
            builder_ui.ANNEALING_GRAPH_WIDTH * 3 + section._preview_spacing * 2
        )
        assert section._preview_other_count == 3
        assert section.table_view.iconSize().width() >= expected_width
        column_index = section.model.frame().columns.get_loc(builder_ui.ANNEALING_OTHER_GRAPH_COLUMN)
        assert section.table_view.columnWidth(column_index) >= expected_width
    finally:
        section._shutdown_background_threads()
        section.close()


def test_shape_memory_section_can_clear_selected_values(tmp_path: Path) -> None:
    _ensure_qapp()
    section = builder_ui.ShapeMemoryStressStrainSection(
        logging.getLogger("test"),
        lambda *_args: None,
    )
    try:
        standard_path = tmp_path / "Ni50Fe27Ga23 10_1 30mA.txt"
        standard_path.write_text("stub", encoding="utf-8")
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "10/1",
                    builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN: "30mA",
                    "_sources": [str(standard_path)],
                    builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN: str(standard_path),
                    SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                    SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                    SHAPE_MEMORY_STRAIN_COLUMN: 18.591,
                    SHAPE_MEMORY_STRESS_COLUMN: 568.441,
                    builder_ui.SHAPE_MEMORY_CURRENT_COLUMN: 30.0,
                    builder_ui.SHAPE_MEMORY_CURRENT_DENSITY_COLUMN: 248.421,
                }
            ]
        )
        section.apply_data(
            MiniDatabaseData(
                table=frame,
                extra={
                    "record_entries": {
                        str(standard_path): {
                            SHAPE_MEMORY_DISPLACEMENT_COLUMN: 3.8,
                            SHAPE_MEMORY_LOAD_COLUMN: 7.0,
                            SHAPE_MEMORY_STRAIN_COLUMN: 18.591,
                            SHAPE_MEMORY_STRESS_COLUMN: 568.441,
                            builder_ui.SHAPE_MEMORY_CURRENT_COLUMN: 30.0,
                            builder_ui.SHAPE_MEMORY_CURRENT_DENSITY_COLUMN: 248.421,
                        }
                    }
                },
            )
        )
        assert section.table_view is not None
        section.table_view.selectRow(0)
        QtWidgets.QApplication.processEvents()
        section._clear_selected_values()
        updated = section.model.frame()
        assert pd.isna(updated.at[0, SHAPE_MEMORY_DISPLACEMENT_COLUMN])
        assert pd.isna(updated.at[0, SHAPE_MEMORY_LOAD_COLUMN])
        assert pd.isna(updated.at[0, SHAPE_MEMORY_STRAIN_COLUMN])
        assert pd.isna(updated.at[0, SHAPE_MEMORY_STRESS_COLUMN])
        assert pd.isna(updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_COLUMN])
        assert pd.isna(updated.at[0, builder_ui.SHAPE_MEMORY_CURRENT_DENSITY_COLUMN])
        record_entries = section.record_entries_snapshot()
        assert str(standard_path) not in record_entries
        assert updated.at[0, builder_ui._SHAPE_MEMORY_STANDARD_SOURCE_COLUMN] == str(
            standard_path
        )
    finally:
        section._shutdown_background_threads()
        section.close()


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


def test_builder_section_visibility_menu_persists_hidden_tabs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))

    window = BuilderWindow()
    try:
        action = window._section_visibility_actions["current_density"]
        transitions = window.transitions_section
        index = transitions.tab_widget.indexOf(window.current_density_section)
        assert index >= 0
        assert transitions.tab_widget.isTabVisible(index)

        window._toggle_section_visibility("current_density", False)

        assert not action.isChecked()
        assert not transitions.tab_widget.isTabVisible(index)
        assert window.tab_widget.indexOf(transitions) >= 0
        assert "current_density" in json.loads(
            str(window.settings.value(window._project_settings_key("hidden_sections")))
        )
        window.settings.sync()
    finally:
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()

    restored = BuilderWindow()
    try:
        restored_index = restored.transitions_section.tab_widget.indexOf(restored.current_density_section)
        assert restored_index >= 0
        assert not restored.transitions_section.tab_widget.isTabVisible(restored_index)
        assert not restored._section_visibility_actions["current_density"].isChecked()
    finally:
        restored._dirty = False
        restored.hide()
        restored.deleteLater()
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


def test_builder_database_latest_resolver_prefers_root_latest(tmp_path: Path) -> None:
    database_dir = tmp_path / "microwire_database"
    archive_dir = database_dir / "archive"
    working_dir = database_dir / "_working"
    archive_dir.mkdir(parents=True)
    working_dir.mkdir()
    latest = database_dir / "microwire_database_latest.pydpj"
    archived = archive_dir / "microwire_database_2026-05-26_1732_1.pydpj"
    working = working_dir / "microwire_database_2026-05-27_0930.pydpj"
    for path in (latest, archived, working):
        path.write_text("{}", encoding="utf-8")

    assert builder_ui._resolve_latest_database_project(archived) == latest
    assert builder_ui._resolve_latest_database_project(working) == latest
    assert builder_ui._resolve_latest_database_project(latest) == latest


def test_builder_database_latest_resolver_leaves_normal_projects(tmp_path: Path) -> None:
    project_path = tmp_path / "ordinary_project.pydpj"
    project_path.write_text("{}", encoding="utf-8")

    assert builder_ui._resolve_latest_database_project(project_path) == project_path


def test_builder_auto_open_prefers_configured_latest_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))
    database_dir = tmp_path / "microwire_database"
    database_dir.mkdir()
    latest = database_dir / "microwire_database_latest.pydpj"
    latest.write_text("{}", encoding="utf-8")
    old_project = tmp_path / "microwire_project.pydpj"
    old_project.write_text("{}", encoding="utf-8")
    window = BuilderWindow()
    try:
        window._auto_open_latest_database = True
        window._auto_open_last = True
        window._database_project_dir = database_dir
        window.settings.setValue(window._project_settings_key("last_path"), str(old_project))
        opened: list[Path] = []
        monkeypatch.setattr(window, "_load_project_from_path", lambda path: opened.append(path))

        window._maybe_auto_open_last_project()

        assert opened == [latest]
    finally:
        window._auto_open_latest_database = False
        window._auto_open_last = False
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_builder_auto_open_startup_skips_initial_section_store_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))
    database_dir = tmp_path / "microwire_database"
    database_dir.mkdir()
    latest = database_dir / "microwire_database_latest.pydpj"
    latest.write_text("{}", encoding="utf-8")
    settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.Format.IniFormat)
    settings.setValue("project/auto_open_latest_database", 1)
    settings.setValue("project/database_dir", str(database_dir))
    settings.sync()
    load_calls: list[str] = []

    def _unexpected_load(self: builder_ui.MiniDatabaseStore) -> MiniDatabaseData:
        load_calls.append(self.section)
        return MiniDatabaseData()

    monkeypatch.setattr(builder_ui.MiniDatabaseStore, "load", _unexpected_load)

    window = BuilderWindow()
    try:
        assert window._startup_auto_open_candidate == latest
        assert load_calls == []
    finally:
        window._auto_open_latest_database = False
        window._auto_open_last = False
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_builder_auto_open_skips_reentrant_project_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))
    database_dir = tmp_path / "microwire_database"
    database_dir.mkdir()
    latest = database_dir / "microwire_database_latest.pydpj"
    latest.write_text("{}", encoding="utf-8")
    window = BuilderWindow()
    try:
        window._auto_open_latest_database = True
        window._database_project_dir = database_dir
        window._project_load_in_progress = True
        opened: list[Path] = []
        monkeypatch.setattr(window, "_load_project_from_path", lambda path: opened.append(path))

        window._maybe_auto_open_last_project()

        assert opened == []
    finally:
        window._project_load_in_progress = False
        window._auto_open_latest_database = False
        window._auto_open_last = False
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_builder_auto_open_load_suppresses_loaded_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))
    database_dir = tmp_path / "microwire_database"
    database_dir.mkdir()
    latest = database_dir / "microwire_database_latest.pydpj"
    settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.Format.IniFormat)
    settings.setValue("project/auto_open_latest_database", 1)
    settings.setValue("project/database_dir", str(database_dir))
    settings.sync()
    latest.write_text(
        json.dumps(
            {
                "kind": BuilderWindow.PROJECT_KIND,
                "version": BuilderWindow.PROJECT_VERSION,
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    information_calls: list[str] = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "information",
        lambda _parent, title, *_args, **_kwargs: information_calls.append(str(title)),
    )

    window = BuilderWindow()
    try:
        window._maybe_auto_open_last_project()
        _wait_for_qt(lambda: window._project_path == latest)

        assert information_calls == []
        assert window._project_path == latest
    finally:
        window._auto_open_latest_database = False
        window._auto_open_last = False
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_builder_auto_open_prepares_project_off_gui_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _ensure_qapp()
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))
    latest = tmp_path / "large_auto_open.pydpj"
    latest.write_text(
        json.dumps(
            {
                "kind": BuilderWindow.PROJECT_KIND,
                "version": BuilderWindow.PROJECT_VERSION,
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    main_thread = app.thread()
    prepare_threads: list[QtCore.QThread] = []

    def _fake_prepare(path: Path) -> builder_ui._PreparedProjectLoad:
        prepare_threads.append(QtCore.QThread.currentThread())
        QtCore.QThread.msleep(50)
        return builder_ui._PreparedProjectLoad(
            target=path,
            payload={
                "kind": BuilderWindow.PROJECT_KIND,
                "version": BuilderWindow.PROJECT_VERSION,
                "sections": {},
            },
            byte_count=10_000_000,
            decoded_payload_count=0,
            read_ms=45.0,
            json_ms=55.0,
            decode_ms=0.0,
        )

    monkeypatch.setattr(builder_ui, "_prepare_project_payload_for_gui", _fake_prepare)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "information",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Ok,
    )

    window = BuilderWindow()
    try:
        window._auto_open_in_progress = True
        window._load_project_from_path(latest)
        window._auto_open_in_progress = False

        assert window._project_path is None
        assert window._project_load_in_progress is True
        assert prepare_threads == [] or all(thread is not main_thread for thread in prepare_threads)

        _wait_for_qt(lambda: window._project_path == latest)

        assert prepare_threads
        assert all(thread is not main_thread for thread in prepare_threads)
        assert window._project_load_in_progress is False
    finally:
        window._auto_open_latest_database = False
        window._auto_open_last = False
        window._auto_open_in_progress = False
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_project_payload_prepare_predecodes_section_payloads(tmp_path: Path) -> None:
    project_path = tmp_path / "payload_project.pydpj"
    stored_payload = {"records": [1, 2, 3]}
    encoded = builder_ui._encode_project_payload(stored_payload)
    assert encoded is not None
    project_path.write_text(
        json.dumps(
            {
                "kind": BuilderWindow.PROJECT_KIND,
                "version": BuilderWindow.PROJECT_VERSION,
                "sections": {
                    "annealing": {
                        "columns": [],
                        "rows": [],
                        "payloads": {"annealing_payload": encoded},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    prepared = builder_ui._prepare_project_payload_for_gui(project_path)
    annealing_payload = prepared.payload["sections"]["annealing"]

    assert prepared.decoded_payload_count == 1
    assert annealing_payload[builder_ui.PROJECT_DECODED_PAYLOADS_KEY] == {
        "annealing_payload": stored_payload
    }


def test_builder_startup_auto_open_scheduler_no_env_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))
    monkeypatch.delenv("MICROWIRE_BUILDER_ENABLE_STARTUP_AUTO_OPEN", raising=False)
    scheduled_delays: list[int] = []
    window = BuilderWindow()
    try:
        monkeypatch.setattr(
            builder_ui.QtCore.QTimer,
            "singleShot",
            lambda delay, _callback: scheduled_delays.append(int(delay)),
        )
        window.schedule_startup_auto_open(25)
        window.schedule_startup_auto_open(99)

        assert scheduled_delays == [25]
        assert window._startup_auto_open_scheduled is True
    finally:
        window._auto_open_latest_database = False
        window._auto_open_last = False
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_builder_main_shows_placeholder_before_scheduling_auto_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_qapp()
    events: list[object] = []

    class FakeBuilderWindow(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            placeholder = next(
                (
                    widget
                    for widget in app.topLevelWidgets()
                    if widget is not self
                    and any(
                        label.text() == "Loading Microwire Data Builder..."
                        for label in widget.findChildren(QtWidgets.QLabel)
                    )
                ),
                None,
            )
            events.append(
                (
                    "construct",
                    placeholder is not None,
                    placeholder.isVisible() if isinstance(placeholder, QtWidgets.QWidget) else False,
                )
            )

        def show(self) -> None:
            events.append("show")
            super().show()

        def schedule_startup_auto_open(self, delay_ms: int = 150) -> None:
            events.append(("schedule", delay_ms, self.isVisible()))

    monkeypatch.setattr(builder_ui, "BuilderWindow", FakeBuilderWindow)

    window = builder_ui.main()
    try:
        assert isinstance(window, FakeBuilderWindow)
        assert events == [
            ("construct", True, True),
            "show",
            ("schedule", 150, True),
        ]
        assert not any(
            widget.windowTitle() == "Microwire Data Builder" and widget.isVisible()
            for widget in app.topLevelWidgets()
            if widget is not window
        )
    finally:
        if isinstance(window, QtWidgets.QWidget):
            window.hide()
            window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_builder_settings_menu_names_latest_database_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    settings_path = tmp_path / "builder.ini"
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(settings_path))
    window = BuilderWindow()
    window._auto_open_last = False
    try:
        settings_menu = next(
            menu
            for menu in window.menuBar().findChildren(QtWidgets.QMenu)
            if menu.title() == "Settings"
        )
        action_texts = [action.text() for action in settings_menu.actions()]

        assert "Open last/recent project on startup" in action_texts
        assert "Open latest database project on startup" in action_texts
    finally:
        window._auto_open_latest_database = False
        window._auto_open_last = False
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


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


def test_load_project_suppressed_dialogs_skip_progress_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    monkeypatch.setenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", "1")
    window = BuilderWindow()
    window._auto_open_last = False
    progress_dialog_attempts: list[object] = []

    class _UnexpectedProgressDialog:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            progress_dialog_attempts.append(_args)
            raise AssertionError("suppressed Builder project loads should not create dialogs")

    try:
        monkeypatch.setattr(QtWidgets, "QProgressDialog", _UnexpectedProgressDialog)
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "information",
            lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Ok,
        )
        project_path = tmp_path / "partial_project.pydpj"
        payload = {
            "kind": window.PROJECT_KIND,
            "version": window.PROJECT_VERSION,
            "sections": {},
        }
        project_path.write_text(json.dumps(payload), encoding="utf-8")

        window._load_project_from_path(project_path)

        assert progress_dialog_attempts == []
        assert window._project_path == project_path
    finally:
        window._dirty = False
        window.hide()
        window.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_assemble_prepare_inputs_allows_export_without_annealing_section_selected(
    qtbot,
    tmp_path: Path,
) -> None:
    _ensure_qapp()
    window = builder_ui.BuilderWindow()
    qtbot.addWidget(window)
    try:
        record = core.MeasurementRecord(
            path=tmp_path / "Ni50Fe27Ga23 5_4 s1 1000mA.txt",
            metadata=core.MeasurementMetadata(
                composition_token="Ni50Fe27Ga23",
                draw_x=5,
                piece_y=4,
                setpoint_mA=1000,
                alt_variant=False,
                measurement_id="anneal-1",
                file_name="Ni50Fe27Ga23 5_4 s1 1000mA.txt",
                relpath="Ni50Fe27Ga23 5_4 s1 1000mA.txt",
                timestamp_mtime_utc="2026-03-24T00:00:00+00:00",
            ),
            dataframe=pd.DataFrame({"I_A": [0.1], "V_V": [0.2], "R_ohm": [2.0]}),
            sanity_ok=True,
            sanity_error=None,
        )
        window.annealing_section.store.save_payload("annealing_records", [record])

        payload = window.assembly_section._prepare_builder_inputs(
            {"fabrication"},
            require_payloads=False,
        )

        assert payload is not None
        annealing_records = payload[1]
        assert len(annealing_records) == 1
    finally:
        window.close()


def test_assemble_prepare_inputs_respects_hide_other_ends_setting(qtbot, tmp_path: Path) -> None:
    _ensure_qapp()
    window = builder_ui.BuilderWindow()
    qtbot.addWidget(window)
    try:
        window.microscope_section._show_other_ends = False
        microscope_index = {
            ("Ni50Fe27Ga23", 5, 4, None): core.MicroscopeMeasurements(),
            ("Ni50Fe27Ga23", 5, 4, "oe"): core.MicroscopeMeasurements(),
        }
        window.microscope_section.store.save_payload("microscope_index", microscope_index)

        payload = window.assembly_section._prepare_builder_inputs(
            {"microscope"},
            require_payloads=False,
        )

        assert payload is not None
        prepared_index = payload[9]
        assert ("Ni50Fe27Ga23", 5, 4, None) in prepared_index
        assert ("Ni50Fe27Ga23", 5, 4, "oe") not in prepared_index
    finally:
        window.close()


def test_assemble_prepare_inputs_repairs_stale_microscope_payload(
    qtbot,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _ensure_qapp()
    log = logging.getLogger("test")
    microscope = builder_ui.MicroscopeSection(log, lambda *_: None)
    assembly = builder_ui.AssemblySection({"microscope": microscope}, log, lambda *_: None)
    qtbot.addWidget(microscope)
    qtbot.addWidget(assembly)
    try:
        key = ("Ni50Fe27Ga23", 5, 4, None)
        core_path = tmp_path / "Ni50Fe27Ga23 5_4 core.jpg"
        glass_path = tmp_path / "Ni50Fe27Ga23 5_4 glass.jpg"
        core_path.write_bytes(b"core")
        glass_path.write_bytes(b"glass")
        stale = core.MicroscopeMeasurements(
            core=[
                core.MicroscopeDetection(
                    value=10.0,
                    image_path=core_path,
                    source="manual",
                )
            ],
            glass=[
                core.MicroscopeDetection(
                    value=30.0,
                    image_path=glass_path,
                    source="manual",
                )
            ],
        )
        stale.core[0].category = "core"
        stale.glass[0].category = "glass"
        microscope.store.save_payload("microscope_index", {key: stale})
        microscope.apply_data(
            builder_ui.MiniDatabaseData(
                table=pd.DataFrame(
                    [
                        {
                            "Composition": "Ni50Fe27Ga23",
                            "Microwire": "5/4",
                            builder_ui.MICROSCOPE_D_COLUMN: 12.0,
                            builder_ui.MICROSCOPE_CAP_D_COLUMN: 36.0,
                            "d/D": round(12.0 / 36.0, 3),
                            BRITTLE_COLUMN: None,
                            builder_ui.MICROSCOPE_IMAGE_COLUMNS[0]: None,
                            builder_ui.MICROSCOPE_IMAGE_COLUMNS[1]: None,
                            "_key": "Ni50Fe27Ga23|5|4",
                            "_core_image": str(core_path),
                            "_glass_image": str(glass_path),
                            "_images": [str(core_path), str(glass_path)],
                        }
                    ]
                ),
                extra={"payloads": {"microscope_index": "microscope_index"}},
            )
        )

        with caplog.at_level(logging.WARNING):
            payload = assembly._prepare_builder_inputs(
                {"microscope"},
                require_payloads=False,
            )

        assert payload is not None
        prepared_index = payload[9]
        assert prepared_index[key].best_core() == pytest.approx(12.0)
        assert prepared_index[key].best_glass() == pytest.approx(36.0)
        repaired = microscope.store.load_payload("microscope_index")
        assert repaired[key].best_core() == pytest.approx(12.0)
        assert "Microscope saved payload is stale" in caplog.text
    finally:
        assembly.close()
        microscope._shutdown_background_threads()
        microscope.close()


def test_build_database_include_fabrication_draw_siblings_limits_to_last_meaningful_piece(
    tmp_path: Path,
) -> None:
    fabrication_index = FabricationIndex()
    fabrication_index.set_draw(
        "Ni50Fe27Ga23",
        5,
        {
            "production_datetime": "2025-03-26 08:15",
            "mass_g": 1.85,
        },
    )
    for piece in range(0, 21):
        payload = {"length_m": 0.0}
        if piece == 2:
            payload = {"piece_date": "2024-04-26", "notes": "hruby podla mna cez 2m"}
        elif piece == 4:
            payload = {"piece_date": "2024-04-26", "notes": "hruby"}
        elif piece >= 9:
            payload = {"length_m": None}
        fabrication_index.set_piece(
            "Ni50Fe27Ga23",
            5,
            piece,
            payload,
        )

    record = MeasurementRecord(
        path=tmp_path / "Ni50Fe27Ga23 5_4 1000mA.txt",
        metadata=MeasurementMetadata(
            composition_token="Ni50Fe27Ga23",
            draw_x=5,
            piece_y=4,
            alt_variant=False,
            setpoint_mA=1000,
            file_name="Ni50Fe27Ga23 5_4 1000mA.txt",
            measurement_id="m1",
            relpath="Ni50Fe27Ga23 5_4 1000mA.txt",
            timestamp_mtime_utc="2026-03-26T00:00:00+00:00",
        ),
            dataframe=pd.DataFrame(
                {
                    "I_A": [0.1, 0.2],
                    "V_V": [0.2, 0.4],
                    "R_ohm": [2.0, 2.0],
                    "I_mA": [100.0, 200.0],
                }
            ),
        sanity_ok=True,
        sanity_error=None,
    )
    config = BuilderConfig(
        annealing_files=[],
        fabrication_files=[],
        output_dir=tmp_path / "out",
        make_plots=False,
        export_formats=(),
        plot_backends=(),
    )

    result = build_database(
        config,
        logger=logging.getLogger("test"),
        fabrication_index=fabrication_index,
        measurement_records=[record],
        include_fabrication_draw_siblings=True,
        skip_exports=True,
    )

    frame = result.dataframe
    sample_rows = frame.loc[frame["Composition"] == "Ni50Fe27Ga23", ["Microwire", "Data source"]]
    assert sample_rows["Microwire"].tolist() == ["5/1", "5/2", "5/3", "5/4"]
    assert sample_rows["Data source"].tolist() == [
        "Fabrication only",
        "Fabrication only",
        "Fabrication only",
        "Measured",
    ]


def test_build_database_excel_export_skips_microscope_crops_when_disabled(
    tmp_path: Path,
) -> None:
    fabrication_index = FabricationIndex()
    fabrication_index.set_piece("Ni50Fe27Ga23", 5, 4, {"length_m": 4.0})
    microscope_index = {
        ("Ni50Fe27Ga23", 5, 4, None): core.MicroscopeMeasurements(
            core=[
                core.MicroscopeDetection(
                    value=12.4,
                    image_path=tmp_path / "core.png",
                    source="manual",
                    category="core",
                )
            ]
        )
    }
    record = MeasurementRecord(
        path=tmp_path / "Ni50Fe27Ga23 5_4 1000mA.txt",
        metadata=MeasurementMetadata(
            composition_token="Ni50Fe27Ga23",
            draw_x=5,
            piece_y=4,
            alt_variant=False,
            setpoint_mA=1000,
            file_name="Ni50Fe27Ga23 5_4 1000mA.txt",
            measurement_id="m1",
            relpath="Ni50Fe27Ga23 5_4 1000mA.txt",
            timestamp_mtime_utc="2026-03-26T00:00:00+00:00",
        ),
        dataframe=pd.DataFrame(
            {
                "I_A": [0.1, 0.2],
                "V_V": [0.2, 0.4],
                "R_ohm": [2.0, 2.0],
                "I_mA": [100.0, 200.0],
            }
        ),
        sanity_ok=True,
        sanity_error=None,
    )

    output_dir = tmp_path / "export"
    result = build_database(
        BuilderConfig(
            annealing_files=[],
            fabrication_files=[],
            output_dir=output_dir,
            make_plots=False,
            export_formats=("excel",),
            plot_backends=(),
            include_microscope_crops=False,
        ),
        fabrication_index=fabrication_index,
        measurement_records=[record],
        microscope_index=microscope_index,
    )

    assert (output_dir / "microwire_database.xlsx").exists()
    assert not (output_dir / "microscope_crops").exists()
    assert result.microscope_crops == {}


def test_assemble_prepare_inputs_keeps_fabrication_and_video_baselines_when_not_selected(
    qtbot,
) -> None:
    _ensure_qapp()
    log = logging.getLogger("test")
    fabrication = builder_ui.FabricationSection(log, lambda *_: None)
    videos = builder_ui.VideoSection(log, lambda *_: None)
    sections = {
        "fabrication": fabrication,
        "videos": videos,
    }
    assembly = builder_ui.AssemblySection(sections, log, lambda *_: None)
    qtbot.addWidget(fabrication)
    qtbot.addWidget(videos)
    qtbot.addWidget(assembly)
    try:
        payload_index = builder_ui.FabricationIndex()
        payload_index.set_draw(
            "Ni50Fe27Ga23",
            6,
            {
                "mass_g": 5.67,
                "production_datetime": "2025-03-26 08:15",
                "winding_speed_m_per_min": 20.0,
                "glass_feed_mm_per_min": 1.2,
                "underpressure": -0.1,
            },
        )
        payload_index.set_piece(
            "Ni50Fe27Ga23",
            6,
            2,
            {
                "length_m": 27.88,
                "fabrication_resistance_ohm": 71.0,
            },
        )
        assembly._load_payload = lambda section_key, payload_key: payload_index if (section_key, payload_key) == ("fabrication", "fabrication_index") else None  # type: ignore[method-assign]
        fabrication_frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "6/2",
                    "Draw": 6,
                    "Piece": 2,
                    "Length (m)": 27.88,
                    "Production datetime": "2025-03-26 08:15",
                    "Mass (g)": 5.67,
                    "Resistance (Ω)": 71.0,
                    builder_ui.CORE_TEMPERATURE_COLUMN: 320.0,
                    "Glass temperature (°C)": 210.0,
                    "Winding speed (m/min)": 20.0,
                    "Glass feeding (mm/min)": 1.2,
                    "Underpressure": -0.1,
                    "Data source": "Measured",
                }
            ]
        )
        fabrication.apply_data(builder_ui.MiniDatabaseData(table=fabrication_frame))

        video_summary = core.VideoMetricsSummary(
            sources={Path("G:/videos/Ni50Fe27Ga23_draw6.mkv")},
            temperatures=[395.0],
            underpressures=[-0.72],
            winding_speeds=[71.0],
            glass_feeds=[4.5],
        )
        videos.store.save_payload(
            "video_index",
            {("Ni50Fe27Ga23", 6, None): video_summary},
        )

        payload = assembly._prepare_builder_inputs(
            {"shape_memory_stress_strain"},
            require_payloads=False,
        )

        assert payload is not None
        fabrication_index = payload[0]
        video_index = payload[10]

        piece_info = fabrication_index.get_piece("Ni50Fe27Ga23", 6, 2)
        draw_info = fabrication_index.get_draw("Ni50Fe27Ga23", 6)
        assert piece_info.get("length_m") == pytest.approx(27.88)
        assert draw_info.get("mass_g") == pytest.approx(5.67)
        assert draw_info.get("production_datetime") == "2025-03-26 08:15"
        assert draw_info.get("winding_speed_m_per_min") == pytest.approx(20.0)
        assert ("Ni50Fe27Ga23", 6, None) in video_index
    finally:
        assembly.close()
        videos.close()
        fabrication.close()


def test_assemble_prepare_inputs_merges_fabrication_payload_with_visible_table(
    qtbot,
) -> None:
    _ensure_qapp()
    log = logging.getLogger("test")
    fabrication = builder_ui.FabricationSection(log, lambda *_: None)
    videos = builder_ui.VideoSection(log, lambda *_: None)
    sections = {
        "fabrication": fabrication,
        "videos": videos,
    }
    assembly = builder_ui.AssemblySection(sections, log, lambda *_: None)
    qtbot.addWidget(fabrication)
    qtbot.addWidget(videos)
    qtbot.addWidget(assembly)
    try:
        payload_index = builder_ui.FabricationIndex()
        payload_index.set_draw(
            "Ni50Fe27Ga23",
            6,
            {
                "mass_g": 5.67,
                "production_datetime": "2025-03-26 08:15",
                "winding_speed_m_per_min": 20.0,
                "glass_feed_mm_per_min": 1.2,
                "underpressure": -0.1,
            },
        )
        payload_index.set_piece(
            "Ni50Fe27Ga23",
            6,
            2,
            {
                "length_m": 27.88,
                "fabrication_resistance_ohm": 71.0,
            },
        )

        original_load_payload = assembly._load_payload

        def fake_load_payload(section_key: str, payload_key: str):
            if (section_key, payload_key) == ("fabrication", "fabrication_index"):
                return payload_index
            return original_load_payload(section_key, payload_key)

        assembly._load_payload = fake_load_payload  # type: ignore[method-assign]

        fabrication.apply_data(
            builder_ui.MiniDatabaseData(
                table=pd.DataFrame(
                    [
                        {
                            "Composition": "Ni50Fe27Ga23",
                            "Microwire": "6/2",
                            "Draw": 6,
                            "Piece": 2,
                            "Length (m)": 27.88,
                            "Data source": "Measured",
                        }
                    ]
                )
            )
        )

        payload = assembly._prepare_builder_inputs(
            {"shape_memory_stress_strain"},
            require_payloads=False,
        )

        assert payload is not None
        fabrication_index = payload[0]
        piece_info = fabrication_index.get_piece("Ni50Fe27Ga23", 6, 2)
        draw_info = fabrication_index.get_draw("Ni50Fe27Ga23", 6)
        assert piece_info.get("length_m") == pytest.approx(27.88)
        assert piece_info.get("fabrication_resistance_ohm") == pytest.approx(71.0)
        assert draw_info.get("mass_g") == pytest.approx(5.67)
        assert draw_info.get("production_datetime") == "2025-03-26 08:15"
        assert draw_info.get("winding_speed_m_per_min") == pytest.approx(20.0)
        assert draw_info.get("glass_feed_mm_per_min") == pytest.approx(1.2)
        assert draw_info.get("underpressure") == pytest.approx(-0.1)
    finally:
        assembly.close()
        videos.close()
        fabrication.close()


def test_assemble_import_project_payload_preserves_hidden_columns_and_order(qtbot) -> None:
    _ensure_qapp()
    window = builder_ui.BuilderWindow()
    qtbot.addWidget(window)
    try:
        payload = {
            "columns": ["Composition", "Microwire", "Brittle", builder_ui.GLASS_PULL_COLUMN],
            "rows": [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "5/4",
                    "Brittle": "brittle",
                    builder_ui.GLASS_PULL_COLUMN: None,
                }
            ],
            "selected_columns": ["Composition", "Microwire"],
            "column_order": ["Microwire", "Composition"],
        }

        window.assembly_section.import_project_payload(payload)
        QtWidgets.QApplication.processEvents()

        assert window.assembly_section._current_preview_column_order()[:2] == [
            "Microwire",
            "Composition",
        ]
    finally:
        window.close()


def test_assemble_default_visible_columns_do_not_disable_export_sections(qtbot) -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(assembly)
    try:
        assert all(assembly._section_states.values())  # noqa: SLF001

        visible_columns = assembly._resolve_selected_columns(  # noqa: SLF001
            [
                "Composition",
                "Microwire",
                "d (µm)",
                "D (µm)",
                "TMA strain by stress/load",
            ]
        )

        assert visible_columns == [
            "Composition",
            "Microwire",
            "d (µm)",
            "D (µm)",
        ]
        assert all(assembly._section_states.values())  # noqa: SLF001
        assert len(assembly._selected_sections()) == len(assembly._section_choices)  # noqa: SLF001
    finally:
        assembly.close()


def test_assemble_column_header_move_marks_project_dirty(qtbot) -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(assembly)
    try:
        changed: list[bool] = []
        assembly.data_updated.connect(lambda: changed.append(True))
        assembly.preview_model.set_frame(
            pd.DataFrame(
                [
                    {
                        "Composition": "Ni50Fe27Ga23",
                        "Microwire": "12/2",
                        "d (Âµm)": 12.5,
                    }
                ]
            )
        )

        assembly._handle_preview_column_moved()  # noqa: SLF001

        assert changed
        assert assembly._column_order  # noqa: SLF001
    finally:
        assembly.close()


def test_assemble_expanded_excel_export_writes_tma_target_sheet(qtbot, tmp_path, monkeypatch) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(assembly)
    try:
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "12/2",
                    "Production datetime": "2026-06-30 09:00",
                    "Video wire range (m)": "0.10-0.12",
                    "TMA strain by stress/load": [
                        "1st: 50MPa / 0.83g: 1.36% @ 7 mA",
                        "50 MPa / 0.83 g: 5.71% @ 15 mA",
                    ],
                    "TMA transition currents by stress/load": [
                        "1st: 50MPa / 0.83g: As 20 mA, Af 30 mA",
                        "50 MPa / 0.83 g: As 40 mA, Af 50 mA",
                    ],
                }
            ]
        )
        compact_frame = frame.drop(columns=["Production datetime", "Video wire range (m)"])
        assembly._raw_preview_frame = frame  # noqa: SLF001
        section_payloads = {
            "assemble": {
                "columns": list(frame.columns),
                "rows": frame.to_dict(orient="records"),
            }
        }
        monkeypatch.setattr(
            assembly,
            "_current_project_section_payloads",
            lambda: section_payloads,
        )

        output = tmp_path / "expanded.xlsx"
        assembly._write_expanded_excel_export(output, compact_frame)  # noqa: SLF001

        workbook = openpyxl.load_workbook(output, read_only=True, data_only=True)
        assert workbook.sheetnames[:3] == ["Analysis", "Assemble", "TMA targets"]
        analysis_headers = [cell.value for cell in next(workbook["Analysis"].iter_rows(max_row=1))]
        assert "Analysis row type" in analysis_headers
        assert "TMA target type" in analysis_headers
        assert "TMA As (mA)" in analysis_headers
        assert "TMA strain (%)" in analysis_headers
        assert "Production datetime" in analysis_headers
        assert "Video wire range (m)" in analysis_headers
        assert "TMA strain by stress/load" not in analysis_headers
        analysis_rows = [
            dict(zip(analysis_headers, row, strict=False))
            for row in workbook["Analysis"].iter_rows(min_row=2, values_only=True)
        ]
        assert [row["Analysis row type"] for row in analysis_rows] == ["TMA", "TMA"]
        assert [row["TMA target type"] for row in analysis_rows] == [
            "First overheating",
            "Stress/load target",
        ]
        assert [row["TMA As (mA)"] for row in analysis_rows] == [20, 40]
        assert [row["Production datetime"] for row in analysis_rows] == [
            "2026-06-30 09:00",
            "2026-06-30 09:00",
        ]
        assemble_headers = [cell.value for cell in next(workbook["Assemble"].iter_rows(max_row=1))]
        assert "Production datetime" not in assemble_headers
        assert "Video wire range (m)" not in assemble_headers
        assert "TMA strain by stress/load" not in assemble_headers
        assert "TMA transition currents by stress/load" not in assemble_headers
        tma_headers = [cell.value for cell in next(workbook["TMA targets"].iter_rows(max_row=1))]
        rows = [
            dict(zip(tma_headers, row, strict=False))
            for row in workbook["TMA targets"].iter_rows(min_row=2, values_only=True)
        ]
        assert [row["TMA target"] for row in rows] == [
            "1st: 50MPa / 0.83g",
            "50 MPa / 0.83 g",
        ]
        assert [row["TMA target type"] for row in rows] == [
            "First overheating",
            "Stress/load target",
        ]
        assert rows[0]["TMA As"] == 20
        assert rows[1]["TMA As"] == 40
    finally:
        assembly.close()


def test_assemble_preview_status_reports_hidden_oe_rows(qtbot) -> None:
    _ensure_qapp()
    assembly = builder_ui.AssemblySection({}, logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(assembly)
    try:
        assembly._raw_preview_frame = pd.DataFrame(  # noqa: SLF001
            [
                {"Composition": "Ni50Fe27Ga23", "Microwire": "1/1"},
                {"Composition": "Ni50Fe27Ga23", "Microwire": "1/2oe"},
            ]
        )
        assembly._show_oe_samples = False  # noqa: SLF001

        assembly._refresh_preview_frame()  # noqa: SLF001

        assert assembly.status_label.text() == (
            "Preview ready - 1 of 2 row(s) shown (OE samples hidden)."
        )
        assert len(assembly._selected_sections()) == len(assembly._section_choices)  # noqa: SLF001
    finally:
        assembly.close()
