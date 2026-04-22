from __future__ import annotations

from pathlib import Path
import importlib
from types import SimpleNamespace

import matplotlib
import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="Qt widgets backend is unavailable", exc_type=ImportError)

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
import pandas as pd


rvst_core = importlib.import_module("plotting.plugins.r_vs_t.core")
rvst_plugin_module = importlib.import_module("plotting.plugins.r_vs_t.r_vs_t_plugin")


def test_format_rvst_title_formats_composition_and_microwire_suffix() -> None:
    title = rvst_core.format_rvst_title("Ni50Fe27Ga23_10_1wgca10")
    assert title == "Ni50Fe27Ga23".translate(str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")) + " 10/1 wgca10"


def test_format_rvst_title_handles_extra_suffix_token() -> None:
    title = rvst_core.format_rvst_title("Ni50Fe27Ga23_5_4_wg")
    assert title == "Ni50Fe27Ga23".translate(str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")) + " 5/4 wg"


def test_load_file_reads_semicolon_rvst_csv(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    path.write_text(
        "iso_time;t_elapsed_s;sp_c;pv_c;resistance_ohm\n"
        "2026-02-03T18:48:52;0.122;-60.000;-42.500;0.065962\n"
        "2026-02-03T18:49:01;10.125;-60.000;-42.600;0.065963\n",
        encoding="utf-8",
    )

    df = rvst_core.load_file(path)

    assert list(df.columns) == [
        "iso_time",
        "t_elapsed_s",
        "sp_c",
        "pv_c",
        "resistance_ohm",
        "_source_row_id",
    ]
    assert df["pv_c"].tolist() == pytest.approx([-42.5, -42.6])
    assert df["resistance_ohm"].tolist() == pytest.approx([0.065962, 0.065963])
    assert df["_source_row_id"].tolist() == [0, 1]


def test_load_file_filters_impossible_temperature_glitches(tmp_path: Path) -> None:
    path = tmp_path / "temp_glitch.csv"
    path.write_text(
        "iso_time;t_elapsed_s;sp_c;pv_c;resistance_ohm\n"
        "2026-01-20T17:53:28;0.0;38.000;37.8;0.078300\n"
        "2026-01-20T17:53:30;2.0;38.000;9999.0;0.078313\n"
        "2026-01-20T17:53:31;3.0;38.000;518.0;0.078319\n"
        "2026-01-20T17:53:32;4.0;38.000;38.2;0.078324\n",
        encoding="utf-8",
    )

    df = rvst_core.load_file(path)

    assert df["pv_c"].tolist() == pytest.approx([37.8, 38.2])


def test_load_file_filters_isolated_near_zero_resistance_dropout(tmp_path: Path) -> None:
    path = tmp_path / "resistance_dropout.csv"
    path.write_text(
        "iso_time;t_elapsed_s;sp_c;pv_c;resistance_ohm\n"
        "2026-02-01T12:55:39;0.123;-60.000;-9.200;0.022801\n"
        "2026-02-01T12:55:49;10.129;-60.000;-9.400;30.6938\n"
        "2026-02-01T12:55:59;20.130;-60.000;-9.500;30.6704\n"
        "2026-02-01T12:56:09;30.175;-60.000;-9.800;30.6455\n",
        encoding="utf-8",
    )

    df = rvst_core.load_file(path)

    assert df["resistance_ohm"].tolist() == pytest.approx([30.6938, 30.6704, 30.6455])


def test_split_heating_cooling_separates_cycles() -> None:
    df = pd.DataFrame(
        {
            "pv_c": [-10.0, -5.0, 0.0, 5.0, 0.0, -5.0, -10.0, -5.0, 0.0],
            "resistance_ohm": [1.0, 1.1, 1.2, 1.3, 1.25, 1.15, 1.05, 1.1, 1.2],
        }
    )

    segments = rvst_core.split_heating_cooling(df)

    assert [segment.label for segment in segments] == [
        "Heating 1",
        "Cooling 1",
        "Heating 2",
    ]
    assert segments[0].x.tolist() == pytest.approx([-10.0, -5.0, 0.0, 5.0])
    assert segments[1].x.tolist() == pytest.approx([5.0, 0.0, -5.0, -10.0])
    assert segments[2].x.tolist() == pytest.approx([-10.0, -5.0, 0.0])


def test_split_heating_cooling_uses_setpoint_direction_when_measured_temperature_is_noisy() -> None:
    df = pd.DataFrame(
        {
            "sp_c": [-10.0, -10.0, -8.0, -8.0, -6.0, -6.0, -8.0, -8.0, -10.0, -10.0],
            "pv_c": [-10.0, -10.4, -8.1, -8.5, -6.2, -6.4, -7.8, -8.3, -9.7, -10.3],
            "resistance_ohm": [1.0, 1.01, 1.1, 1.11, 1.2, 1.21, 1.15, 1.14, 1.05, 1.04],
        }
    )

    segments = rvst_core.split_heating_cooling(df)

    assert [segment.label for segment in segments] == ["Heating 1", "Cooling 1"]


def test_split_heating_cooling_preserves_source_row_ids() -> None:
    df = pd.DataFrame(
        {
            "_source_row_id": [10, 11, 12, 13, 14, 15],
            "sp_c": [-10.0, -8.0, -6.0, -8.0, -10.0, -12.0],
            "pv_c": [-10.1, -8.2, -6.2, -7.9, -10.1, -12.2],
            "resistance_ohm": [1.0, 1.1, 1.2, 1.15, 1.05, 0.95],
        }
    )

    segments = rvst_core.split_heating_cooling(df)

    assert [segment.frame["_source_row_id"].tolist() for segment in segments] == [
        [10, 11, 12],
        [12, 13, 14, 15],
    ]


def test_plot_one_uses_measured_temperature_and_cycle_legend_labels() -> None:
    df = pd.DataFrame(
        {
            "sp_c": [-10.0, -8.0, -6.0, -4.0, -6.0, -8.0, -10.0, -8.0, -6.0],
            "pv_c": [-10.1, -8.1, -6.2, -4.1, -5.9, -8.1, -10.2, -8.2, -6.1],
            "resistance_ohm": [1.0, 1.1, 1.2, 1.3, 1.24, 1.15, 1.05, 1.1, 1.18],
        }
    )

    fig, _ = rvst_core.plot_one(df, rvst_core.format_rvst_title("Ni50Fe27Ga23_10_1wgca10"))
    try:
        ax = fig.axes[0]
        assert ax.get_xlabel() == "Temperature [°C]"
        assert ax.get_ylabel() == "Resistance [Ω]"
        legend = ax.get_legend()
        assert legend is not None
        labels = [text.get_text() for text in legend.get_texts()]
        assert labels == ["Heating 1", "Cooling 1", "Heating 2"]
    finally:
        plt.close(fig)


def test_plot_one_uses_distinct_warm_and_cool_cycle_colors() -> None:
    df = pd.DataFrame(
        {
            "sp_c": [-10.0, -8.0, -6.0, -4.0, -6.0, -8.0, -10.0, -8.0, -6.0, -4.0],
            "pv_c": [-10.1, -8.2, -6.1, -4.2, -5.8, -8.0, -10.1, -8.1, -6.0, -4.1],
            "resistance_ohm": [1.0, 1.1, 1.2, 1.3, 1.24, 1.15, 1.05, 1.1, 1.18, 1.26],
        }
    )

    fig, _ = rvst_core.plot_one(df, rvst_core.format_rvst_title("Ni50Fe27Ga23_10_1wgca10"))
    try:
        ax = fig.axes[0]
        colors = [mcolors.to_hex(line.get_color()) for line in ax.get_lines()]
        assert colors == ["#dc2626", "#2563eb", "#f97316"]
    finally:
        plt.close(fig)


def test_plot_residuals_one_uses_residual_axis_label() -> None:
    df = pd.DataFrame(
        {
            "sp_c": [-10.0, -8.0, -6.0, -4.0, -6.0, -8.0, -10.0],
            "pv_c": [-10.1, -8.2, -6.1, -4.2, -5.8, -8.0, -10.1],
            "resistance_ohm": [1.0, 1.1, 1.2, 1.3, 1.24, 1.15, 1.05],
        }
    )

    fig, exported = rvst_core.plot_residuals_one(df, rvst_core.format_rvst_title("Ni50Fe27Ga23_10_1wgca10"))
    try:
        ax = fig.axes[0]
        assert ax.get_ylabel().startswith("Residual [") and ax.get_ylabel().endswith("]")
        assert exported.endswith("_residual")
    finally:
        plt.close(fig)


def test_rvst_plugin_uses_fixed_size_plot_canvas(monkeypatch: pytest.MonkeyPatch) -> None:
    from PyQt6 import QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])

    class FakeCanvas(QtWidgets.QWidget):
        def __init__(self, figure: object) -> None:
            super().__init__()
            self.figure = figure
            self.focus_policy = None
            self.minimum_size = None
            self.size_policy = None

        def setFocusPolicy(self, value: object) -> None:
            self.focus_policy = value

        def setMinimumSize(self, width: int, height: int) -> None:
            self.minimum_size = (width, height)

        def setSizePolicy(self, horizontal: object, vertical: object) -> None:
            self.size_policy = (horizontal, vertical)

    class FakeTabWidget:
        def addTab(self, tab: object, label: str) -> None:
            self.last = (tab, label)

    class FakeHost:
        def __init__(self) -> None:
            self.tab_widget = FakeTabWidget()
            self._plugin_last_directories = {}
            self._registered: list[tuple[object, object, object, object]] = []

        def _register_plot_tab(self, tab: object, canvas: object, ax: object, descriptor: object) -> None:
            self._registered.append((tab, canvas, ax, descriptor))

        def _worksheet_key(self, workbook_key: object, segment_label: object) -> str:
            return f"{workbook_key}::{segment_label}"

    class FakeGraphLineState:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    class FakeTabDescriptor:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    fake_window_module = SimpleNamespace(
        PlotFigureCanvas=FakeCanvas,
        GraphLineState=FakeGraphLineState,
        TabDescriptor=FakeTabDescriptor,
    )
    monkeypatch.setattr(rvst_plugin_module, "window_api", lambda: fake_window_module)

    plugin = rvst_plugin_module.RVsTPlugin(FakeHost(), "R vs T")
    df = pd.DataFrame(
        {
            "sp_c": [-10.0, -8.0, -6.0, -4.0],
            "pv_c": [-10.1, -8.1, -6.2, -4.1],
            "resistance_ohm": [1.0, 1.1, 1.2, 1.3],
            "_source_row_id": [0, 1, 2, 3],
        }
    )

    tab = plugin._create_plot_tab("sample.csv", df)

    assert tab is not None
    assert plugin.host._registered
    _, canvas, _, _ = plugin.host._registered[0]
    assert isinstance(canvas, FakeCanvas)


def test_replace_plot_tab_updates_existing_tab_contents(monkeypatch: pytest.MonkeyPatch) -> None:
    from PyQt6 import QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])

    class FakeTabWidget:
        def __init__(self) -> None:
            self.widgets: list[object] = []
            self.current_index = -1

        def addTab(self, tab: object, label: str) -> None:
            _ = label
            self.widgets.append(tab)

        def indexOf(self, tab: object) -> int:
            try:
                return self.widgets.index(tab)
            except ValueError:
                return -1

        def setCurrentIndex(self, index: int) -> None:
            self.current_index = index

    class FakeHost:
        def __init__(self) -> None:
            self.tab_widget = FakeTabWidget()
            self._tab_descriptors: dict[object, object] = {}
            self.replaced: list[tuple[object, object, object, object]] = []

        def _replace_plot_tab_contents(
            self,
            tab: object,
            canvas: object,
            ax: object,
            descriptor: object,
        ) -> None:
            self.replaced.append((tab, canvas, ax, descriptor))

    host = FakeHost()
    plugin = rvst_plugin_module.RVsTPlugin(host, "R vs T")
    old_tab = object()
    path_str = "sample.csv"
    host.tab_widget.widgets = [old_tab]
    host._tab_descriptors[old_tab] = SimpleNamespace(metadata={"source_file": path_str})
    plugin._plot_tabs = [old_tab]
    plugin._data_by_file[path_str] = pd.DataFrame({"pv_c": [1.0], "resistance_ohm": [2.0]})

    monkeypatch.setattr(
        plugin,
        "_build_replacement_plot_content",
        lambda _path, _df: ("canvas", "axes", {"descriptor": True}),
    )

    replacement = plugin._replace_plot_tab_for_source(path_str)

    assert replacement is old_tab
    assert host.replaced == [(old_tab, "canvas", "axes", {"descriptor": True})]
    assert host.tab_widget.current_index == 0
