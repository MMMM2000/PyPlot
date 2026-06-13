from __future__ import annotations
from pathlib import Path

import importlib

import matplotlib
import numpy as np
import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="Qt widgets backend is unavailable", exc_type=ImportError)

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import colors as mcolors


anneal_core = importlib.import_module("plotting.plugins.current_annealing.core")
anneal_plugin_mod = importlib.import_module(
    "plotting.plugins.current_annealing.current_annealing_plugin"
)


def test_load_file_trims_burnthrough_point(tmp_path: Path) -> None:
    path = tmp_path / "burn.txt"
    path.write_text("0.01 0.02 2\n0.12 0.24 2\n0.07 0.14 2\n")
    df = anneal_core.load_file(path)
    values = df["I_mA"].tolist()
    assert values == pytest.approx([10.0, 120.0])


def test_load_file_trims_resistance_spike(tmp_path: Path) -> None:
    path = tmp_path / "burn_resistance.txt"
    path.write_text(
        "0.095 0.019 200\n0.100 0.020 210\n0.095 0.019 310\n"
    )
    df = anneal_core.load_file(path)
    assert df["I_mA"].tolist() == pytest.approx([95.0, 100.0])
    assert df["R_Ohm"].tolist() == pytest.approx([200.0, 210.0])


def test_load_file_handles_decimal_commas(tmp_path: Path) -> None:
    path = tmp_path / "comma_values.txt"
    path.write_text("0,001 0,002 1000,5\n0,002 0,004 1001,5\n")
    df = anneal_core.load_file(path)
    assert df["I_mA"].tolist() == pytest.approx([1.0, 2.0])
    assert df["R_Ohm"].tolist() == pytest.approx([1000.5, 1001.5])


def test_load_file_keeps_milliamp_input_without_multiplying_by_1000(tmp_path: Path) -> None:
    path = tmp_path / "already_ma_80mA.txt"
    path.write_text("2 0.1 100\n4 0.2 110\n6 0.3 120\n")
    df = anneal_core.load_file(path)
    assert df["I_mA"].tolist() == pytest.approx([2.0, 4.0, 6.0])


def test_load_file_uses_filename_target_to_keep_amp_input_in_physical_range(tmp_path: Path) -> None:
    path = tmp_path / "amp_input_80mA.txt"
    path.write_text("0.02 0.1 100\n0.04 0.2 110\n0.08 0.3 120\n")
    df = anneal_core.load_file(path)
    assert df["I_mA"].tolist() == pytest.approx([20.0, 40.0, 80.0])


def test_load_file_rejects_currents_above_expected_annealing_ceiling(tmp_path: Path) -> None:
    path = tmp_path / "too_high_1500mA.txt"
    path.write_text("1200 0.1 100\n1500 0.2 110\n")
    with pytest.raises(ValueError, match="1000 mA ceiling"):
        anneal_core.load_file(path)


def test_summarize_transition_currents_detects_paired_annealing_transition() -> None:
    up_current = np.linspace(1.0, 100.0, 160)
    down_current = np.linspace(100.0, 1.0, 160)
    up_drop = np.clip(1.0 - np.abs(up_current - 42.5) / 7.5, 0.0, 1.0)
    down_rise = np.clip((7.0 - down_current) / 3.0, 0.0, 1.0)
    up_resistance = 100.0 + (0.12 * up_current) - (12.0 * up_drop)
    down_resistance = (
        80.0
        + (10.0 * down_rise)
    )
    df = pd.DataFrame(
        {
            "I_mA": np.r_[up_current, down_current],
            "R_Ohm": np.r_[up_resistance, down_resistance],
        }
    )

    summary = anneal_core.summarize_transition_currents(df)

    assert summary.as_current_mA == pytest.approx(35.0, abs=1.0)
    assert summary.af_current_mA == pytest.approx(42.5, abs=1.0)
    assert summary.ms_current_mA == pytest.approx(7.2, abs=1.0)
    assert summary.mf_current_mA == pytest.approx(4.1, abs=1.0)
    assert summary.ms_current_mA < summary.af_current_mA
    assert summary.mf_current_mA < summary.af_current_mA
    assert anneal_core.format_transition_summary(summary) == (
        "As 35 mA, Af 43 mA, Ms 7 mA, Mf 3 mA"
    )


def test_summarize_transition_currents_requires_paired_transition() -> None:
    up_current = np.linspace(1.0, 100.0, 160)
    down_current = np.linspace(100.0, 1.0, 160)
    resistance = np.r_[
        100.0 + 0.05 * up_current,
        100.0 + 0.05 * down_current,
    ]
    df = pd.DataFrame(
        {
            "I_mA": np.r_[up_current, down_current],
            "R_ohm": resistance,
        }
    )

    summary = anneal_core.summarize_transition_currents(df)

    assert anneal_core.format_transition_summary(summary) == ""


def test_summarize_transition_currents_rejects_upward_heating_kink() -> None:
    up_current = np.linspace(1.0, 100.0, 160)
    down_current = np.linspace(100.0, 1.0, 160)
    up_fraction = np.clip((up_current - 35.0) / 15.0, 0.0, 1.0)
    down_fraction = np.clip((down_current - 30.0) / 25.0, 0.0, 1.0)
    up_resistance = (
        (80.0 + 0.02 * up_current) * (1.0 - up_fraction)
        + (120.0 + 0.04 * up_current) * up_fraction
    )
    down_resistance = (
        (120.0 - 0.01 * down_current) * (1.0 - down_fraction)
        + (80.0 + 0.004 * down_current) * down_fraction
    )
    df = pd.DataFrame(
        {
            "I_mA": np.r_[up_current, down_current],
            "R_Ohm": np.r_[up_resistance, down_resistance],
        }
    )

    summary = anneal_core.summarize_transition_currents(df)

    assert anneal_core.format_transition_summary(summary) == ""


def test_summarize_transition_currents_rejects_wrong_signed_cooling_kink() -> None:
    up_current = np.linspace(1.0, 100.0, 160)
    down_current = np.linspace(100.0, 1.0, 160)
    up_drop = np.clip(1.0 - np.abs(up_current - 42.5) / 7.5, 0.0, 1.0)
    wrong_cooling_drop = np.clip((7.0 - down_current) / 3.0, 0.0, 1.0)
    up_resistance = 100.0 + (0.12 * up_current) - (12.0 * up_drop)
    down_resistance = 80.0 - (10.0 * wrong_cooling_drop)
    df = pd.DataFrame(
        {
            "I_mA": np.r_[up_current, down_current],
            "R_Ohm": np.r_[up_resistance, down_resistance],
        }
    )

    summary = anneal_core.summarize_transition_currents(df)

    assert summary.as_current_mA == pytest.approx(35.0, abs=1.0)
    assert summary.af_current_mA == pytest.approx(42.5, abs=1.0)
    assert summary.ms_current_mA is None
    assert summary.mf_current_mA is None
    assert anneal_core.format_transition_summary(summary) == ""


def test_summarize_transition_currents_detects_real_local_heating_drop() -> None:
    path = (
        Path(__file__).resolve().parent.parent
        / "sample_data"
        / "database_builder"
        / "current annealing data"
        / "Ni50Fe27Ga23 10_4 s2a 80mA.txt"
    )
    df = anneal_core.load_file(path)

    summary = anneal_core.summarize_transition_currents(df)

    assert summary.as_current_mA == pytest.approx(22.8, abs=0.6)
    assert summary.af_current_mA == pytest.approx(25.7, abs=0.6)
    assert summary.ms_current_mA == pytest.approx(6.7, abs=0.8)
    assert summary.mf_current_mA == pytest.approx(4.7, abs=0.8)
    assert summary.ms_current_mA < summary.af_current_mA
    assert summary.mf_current_mA < summary.af_current_mA


def test_plot_one_bridges_increasing_to_decreasing_segment() -> None:
    df = pd.DataFrame(
        {
            "I_mA": [0.0, 20.0, 40.0, 60.0, 50.0, 40.0],
            "R_Ohm": [100.0, 150.0, 200.0, 250.0, 260.0, 270.0],
        }
    )
    fig, _ = anneal_core.plot_one(df, "Sample", figsize=(4.0, 2.25))
    ax = fig.axes[0]
    blue_lines = []
    for line in ax.lines:
        try:
            color = mcolors.to_hex(line.get_color()).lower()
        except Exception:
            continue
        if color in {"#2563eb", "#0000ff"}:
            blue_lines.append(line)
    assert blue_lines, "Expected a decreasing (blue) segment"
    first_blue = blue_lines[0]
    blue_x = list(first_blue.get_xdata())
    assert blue_x[0] == pytest.approx(60.0)
    assert blue_x[1] == pytest.approx(50.0)
    width, height = fig.get_size_inches()
    assert width == pytest.approx(4.0)
    assert height == pytest.approx(2.25)
    plt.close(fig)


def test_plot_one_uses_shared_default_label_style_and_size() -> None:
    df = pd.DataFrame(
        {
            "I_mA": [0.0, 10.0, 20.0, 10.0, 0.0],
            "R_Ohm": [100.0, 120.0, 140.0, 130.0, 110.0],
        }
    )
    fig, _ = anneal_core.plot_one(df, "Anneal")
    ax = fig.axes[0]
    assert ax.get_xlabel() == "Current [mA]"
    assert ax.get_ylabel() == "Resistance [Ω]"
    width, height = fig.get_size_inches()
    assert width == pytest.approx(6.0)
    assert height == pytest.approx(4.0)
    plt.close(fig)


def test_plot_one_can_show_power_top_axis() -> None:
    df = pd.DataFrame(
        {
            "I_mA": [0.0, 10.0, 20.0, 30.0],
            "R_Ohm": [100.0, 110.0, 120.0, 130.0],
        }
    )
    fig, _ = anneal_core.plot_one(df, "Anneal", show_power_top_axis=True)
    try:
        assert len(fig.axes) == 2
        top_ax = fig.axes[1]
        assert top_ax.get_xlabel() == "Power [mW]"
        assert top_ax.get_xlim() == pytest.approx(fig.axes[0].get_xlim())
        assert any(label.get_text() for label in top_ax.get_xticklabels())
    finally:
        plt.close(fig)


def test_plot_one_legend_text_color_follows_line_color() -> None:
    df = pd.DataFrame(
        {
            "I_mA": [0.0, 10.0, 20.0, 10.0, 0.0],
            "R_Ohm": [100.0, 120.0, 140.0, 130.0, 110.0],
        }
    )
    fig, _ = anneal_core.plot_one(df, "Anneal")
    try:
        ax = fig.axes[0]
        legend = ax.get_legend()
        assert legend is not None
        handles_attr = getattr(legend, "legendHandles", None)
        if handles_attr is None:
            handles_attr = getattr(legend, "legend_handles", [])
        handle_colors = [mcolors.to_hex(handle.get_color()) for handle in handles_attr]
        text_colors = [mcolors.to_hex(text.get_color()) for text in legend.get_texts()]
        assert text_colors
        assert text_colors == handle_colors[: len(text_colors)]
    finally:
        plt.close(fig)


def test_plot_one_uses_distinct_cycle_colors_and_labels() -> None:
    df = pd.DataFrame(
        {
            "I_mA": [0.0, 10.0, 20.0, 10.0, 0.0, 10.0, 20.0, 10.0, 0.0],
            "R_Ohm": [100.0, 120.0, 140.0, 130.0, 110.0, 125.0, 145.0, 135.0, 115.0],
        }
    )
    fig, _ = anneal_core.plot_one(df, "Anneal")
    try:
        ax = fig.axes[0]
        legend = ax.get_legend()
        assert legend is not None
        labels = [text.get_text() for text in legend.get_texts()]
        assert labels == ["Increasing 1", "Decreasing 1", "Increasing 2", "Decreasing 2"]
        colors = [mcolors.to_hex(line.get_color()).lower() for line in ax.lines]
        assert colors[:4] == ["#dc2626", "#2563eb", "#f97316", "#0ea5e9"]
    finally:
        plt.close(fig)


class _DummyTitleLabel:
    def __init__(self) -> None:
        self.text = ""


class _DummyLegendLabel:
    def __init__(self) -> None:
        self.text = ""


class _DummyLayerNoTitle:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def label(self, name: str):
        if name == "Title":
            return None
        if name == "Legend":
            return _DummyLegendLabel()
        return None

    def lt_exec(self, command: str) -> bool:
        self.commands.append(command)
        return True


class _DummyGraph:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def lt_exec(self, command: str) -> bool:
        self.commands.append(command)
        return True

    def activate(self) -> None:
        return None


class _DummyColumn:
    LongName = ""
    Units = ""
    Comment = ""
    Type = 0


class _DummyWorksheetObj:
    def __init__(self) -> None:
        self._columns: dict[int, _DummyColumn] = {}

    def Columns(self, index: int) -> _DummyColumn:
        if index not in self._columns:
            self._columns[index] = _DummyColumn()
        return self._columns[index]


class _DummyWorksheet:
    def __init__(self) -> None:
        self.obj = _DummyWorksheetObj()
        self.columns: dict[int, list[float]] = {}

    def from_list(self, index: int, values: list[float]) -> None:
        self.columns[index] = list(values)

    def set_label(self, _index: int, _value: str, _code: str) -> None:
        return None

    def header_rows(self, _value: str) -> None:
        return None


class _DummyPlot:
    def __init__(self, index: int) -> None:
        self.index = index
        self.legend = ""
        self.color = ""
        self.line_width = 0.0
        self.symbol_shape = 0
        self.symbol_size = 0.0
        self.symbol_edge_color = ""
        self.symbol_fill_color = ""


class _DummyLayer:
    def __init__(self) -> None:
        self._plots: list[_DummyPlot] = []
        self._legend = _DummyLegendLabel()

    @property
    def plot_count(self) -> int:
        return len(self._plots)

    def __len__(self) -> int:
        return len(self._plots)

    def add_plot(self, _worksheet, coly: int, colx: int, type: str):  # noqa: A002
        _ = (coly, colx, type)
        plot = _DummyPlot(len(self._plots) + 1)
        self._plots.append(plot)
        return plot

    def label(self, name: str):
        if name == "Legend":
            return self._legend
        if name == "Title":
            return _DummyTitleLabel()
        return None

    def rescale(self) -> None:
        return None


def test_set_graph_title_uses_ltalk_fallback_when_title_label_missing() -> None:
    layer = _DummyLayerNoTitle()
    graph = _DummyGraph()
    anneal_core._set_graph_title(layer, 'Ni50Fe27Ga23 "sample"', graph=graph)  # noqa: SLF001
    combined = layer.commands + graph.commands
    assert combined
    assert any(
        'label -s -n title "Ni50Fe27Ga23 ""sample""";' in cmd
        or 'title -s "Ni50Fe27Ga23 ""sample""";' in cmd
        for cmd in combined
    )


def test_origin_directional_legend_does_not_embed_sample_title() -> None:
    layer = _DummyLayer()
    graph = _DummyGraph()
    worksheet = _DummyWorksheet()
    currents = anneal_core.np.array([0.0, 20.0, 40.0, 60.0, 45.0, 30.0], dtype=float)
    resistances = anneal_core.np.array([100.0, 120.0, 150.0, 180.0, 175.0, 170.0], dtype=float)

    anneal_core._plot_origin_experimental(  # noqa: SLF001
        origin_any=None,
        workbook=None,
        worksheet=worksheet,
        graph=graph,
        layer=layer,
        currents=currents,
        resistances=resistances,
        legend_label="Ni50Fe27Ga23 1_1",
    )

    legend_text = layer.label("Legend").text
    assert "Increasing 1" in legend_text
    assert "Decreasing 1" in legend_text
    assert "Ni50Fe27Ga23" not in legend_text


def test_origin_directional_export_keeps_cycle_specific_series_colors() -> None:
    layer = _DummyLayer()
    graph = _DummyGraph()
    worksheet = _DummyWorksheet()
    currents = anneal_core.np.array(
        [0.0, 20.0, 40.0, 20.0, 0.0, 20.0, 40.0, 20.0, 0.0],
        dtype=float,
    )
    resistances = anneal_core.np.array(
        [100.0, 120.0, 150.0, 140.0, 130.0, 125.0, 155.0, 145.0, 135.0],
        dtype=float,
    )

    anneal_core._plot_origin_experimental(  # noqa: SLF001
        origin_any=None,
        workbook=None,
        worksheet=worksheet,
        graph=graph,
        layer=layer,
        currents=currents,
        resistances=resistances,
        legend_label="Ni50Fe27Ga23 1_1",
    )

    assert len(layer._plots) == 4  # noqa: SLF001 - test helper state
    assert [plot.color for plot in layer._plots] == [  # noqa: SLF001 - test helper state
        "#dc2626",
        "#2563eb",
        "#f97316",
        "#0ea5e9",
    ]


def test_current_annealing_plugin_uses_shared_origin_export() -> None:
    assert (
        anneal_plugin_mod.CurrentAnnealingPlugin.uses_shared_plot_workbooks is True
    )


def test_current_annealing_plugin_exposes_no_redundant_settings_widget() -> None:
    plugin = anneal_plugin_mod.CurrentAnnealingPlugin(object(), "Current Annealing")
    assert plugin.settings_widget() is None


def test_current_annealing_open_origin_delegates_to_shared_host_export() -> None:
    class _Host:
        def __init__(self) -> None:
            self.called = False

        def _open_origin_shared(self) -> None:
            self.called = True

    host = _Host()
    plugin = anneal_plugin_mod.CurrentAnnealingPlugin(host, "Current Annealing")
    plugin._plot_tabs = [object()]  # noqa: SLF001 - bypass generate() for delegation check
    plugin.open_origin()
    assert host.called is True
