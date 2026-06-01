from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import pytest
from PyQt6 import QtWidgets

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt  # noqa: E402

from plotting.plugins import builtin_plugin_registry
from plotting.plugins.mini_dma import core
from plotting.plugins.mini_dma.mini_dma_plugin import MiniDmaPlugin
from plotting.pyplot.window import PyPlotWindow


SAMPLE_RUN = Path("sample_data/mini dma/Ni50Fe27Ga23 12_2 test_run32")
_APP_REF: QtWidgets.QApplication | None = None


def _ensure_qapp() -> QtWidgets.QApplication:
    global _APP_REF
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    _APP_REF = app
    return app


def test_load_run_accepts_folder_and_metadata_sample_name() -> None:
    run = core.load_run(SAMPLE_RUN)

    assert run.measurement_path.name == "measurement.csv"
    assert run.sample_name == "Ni50Fe27Ga23 12_2 test"
    assert run.wire_diameter_mm == pytest.approx(0.0191)
    assert len(run.frame) > 3000
    assert "current_mA" in run.frame.columns


def test_current_sweep_groups_by_target_mpa() -> None:
    run = core.load_run(SAMPLE_RUN / "measurement.csv")
    groups = core.current_sweep_groups(run.frame)

    targets = [target for target, _group in groups]
    assert targets == [50.0, 100.0, 150.0, 200.0, 250.0, 300.0, 350.0, 400.0, 450.0]
    assert all(len(group) >= 2 for _target, group in groups)


def test_current_sweep_groups_preserve_return_leg_duplicate_states() -> None:
    frame = pd.DataFrame(
        {
            "elapsed_s": [0.0, 1.0, 2.0, 3.0],
            "automation_phase": ["current"] * 4,
            "automation_target_value": [50.0] * 4,
            "plateau_index": [1] * 4,
            "strain_pct": [0.0, 0.5, 0.5, 0.0],
            "resistance_ohm": [100.0] * 4,
            "current_mA": [10.0, 20.0, 20.0, 10.0],
        }
    )

    groups = core.current_sweep_groups(frame)

    assert len(groups) == 1
    _target, group = groups[0]
    assert group["current_mA"].tolist() == [10.0, 20.0, 10.0]


def test_make_figures_create_one_line_per_target() -> None:
    run = core.load_run(SAMPLE_RUN)

    strain_fig = core.make_strain_current_figure(run)
    resistance_fig = core.make_resistance_current_figure(run)
    try:
        strain_ax = strain_fig.axes[0]
        resistance_ax = resistance_fig.axes[0]
        assert len(strain_ax.lines) == 9
        assert len(resistance_ax.lines) == 9
        assert all(line.get_marker() == "o" for line in strain_ax.lines)
        assert all(line.get_marker() == "o" for line in resistance_ax.lines)
        assert strain_ax.get_xlabel() == "Current [mA] (80 mA = 279 A/mm², d = 19.1 µm)"
        assert strain_ax.get_ylabel() == "Strain [%] (l₀ = 58.3 mm)"
        assert resistance_ax.get_ylabel() == "Resistance [Ohm]"
        assert strain_ax.lines[0].get_label() == "50 MPa / 1.46 g"
        assert strain_ax.get_legend().get_title().get_text() == "Stress / load"
    finally:
        plt.close(strain_fig)
        plt.close(resistance_fig)


def test_mini_dma_axis_labels_survive_origin_export_label_splitting() -> None:
    x_label = "Current [mA] (80 mA = 279 A/mm², d = 19.1 µm)"
    y_label = "Strain [%] (l₀ = 52.8 mm)"

    assert PyPlotWindow._label_parts(x_label) == (x_label, "")
    assert PyPlotWindow._label_parts(y_label) == (y_label, "")
    assert PyPlotWindow._label_parts("Resistance [Ohm]") == ("Resistance", "Ohm")


def test_origin_line_symbol_style_uses_mini_dma_markers() -> None:
    class _FakePlot:
        def __init__(self) -> None:
            self.commands: list[str] = []
            self.symbol_shape = 0
            self.symbol_size = 0.0

        def set_cmd(self, command: str, *_args: object) -> None:
            self.commands.append(command)

    plot = _FakePlot()

    assert PyPlotWindow._origin_marker_active("o", 3.5) is True
    PyPlotWindow._apply_origin_plot_style(
        plot,
        color="#1f77b4",
        show_symbols=True,
        symbol_size=6.0,
    )

    assert "-k 2" in plot.commands
    assert "-kf 0" in plot.commands
    assert "-z 6" in plot.commands
    assert plot.symbol_shape == 2
    assert plot.symbol_size == pytest.approx(6.0)


def test_resistance_current_figure_can_show_power_top_axis() -> None:
    run = core.load_run(SAMPLE_RUN)

    fig = core.make_resistance_current_figure(run, show_power_top_axis=True)
    try:
        assert len(fig.axes) == 2
        top_ax = fig.axes[1]
        assert top_ax.get_xlabel() == "Power [mW]"
        assert top_ax.get_xlim() == pytest.approx(fig.axes[0].get_xlim())
        assert any(label.get_text() for label in top_ax.get_xticklabels())
    finally:
        plt.close(fig)


def test_strain_current_figure_can_show_power_top_axis() -> None:
    run = core.load_run(SAMPLE_RUN)

    fig = core.make_strain_current_figure(run, show_power_top_axis=True)
    try:
        assert len(fig.axes) == 2
        top_ax = fig.axes[1]
        assert top_ax.get_xlabel() == "Power [mW]"
        assert top_ax.get_xlim() == pytest.approx(fig.axes[0].get_xlim())
        assert any(label.get_text() for label in top_ax.get_xticklabels())
    finally:
        plt.close(fig)


def test_strain_current_figure_can_use_each_trace_minimum_as_l0() -> None:
    run = core.load_run(SAMPLE_RUN)

    fig = core.make_strain_current_figure(run, zero_minimum_strain=True)
    try:
        ax = fig.axes[0]
        assert ax.get_ylabel() == "Strain [%] (per-curve l₀)"
        assert len(ax.lines) == 9
        groups = core.current_sweep_groups(run.frame)
        for line, (_target, group) in zip(ax.lines, groups, strict=True):
            y_values = line.get_ydata()
            assert min(y_values) == pytest.approx(0.0)
            assert max(y_values) >= 0.0

        first_target, first_group = groups[0]
        assert first_target == pytest.approx(50.0)
        shifted = first_group["strain_pct"] - first_group["strain_pct"].min()
        recalculated = core.strain_from_trace_minimum_length(run, first_group)
        assert recalculated.max() != pytest.approx(shifted.max())
    finally:
        plt.close(fig)


def test_strain_current_figure_can_use_global_minimum_as_shared_l0() -> None:
    run = core.load_run(SAMPLE_RUN)

    fig = core.make_strain_current_figure(
        run,
        strain_baseline_mode=core.STRAIN_BASELINE_GLOBAL_MINIMUM,
    )
    try:
        ax = fig.axes[0]
        assert ax.get_ylabel() == "Strain [%] (l₀ = 52.8 mm)"
        minima = [min(line.get_ydata()) for line in ax.lines]
        assert min(minima) == pytest.approx(0.0)
        assert any(value > 0.0 for value in minima)
    finally:
        plt.close(fig)


def test_build_plot_frame_pairs_current_with_requested_y_column() -> None:
    run = core.load_run(SAMPLE_RUN)
    frame = core.build_plot_frame(run, y_column="strain_pct")

    assert "50_MPa_current_mA" in frame.columns
    assert "50_MPa_strain_pct" in frame.columns
    assert "450_MPa_current_mA" in frame.columns
    assert "450_MPa_strain_pct" in frame.columns


def test_power_axis_points_use_current_and_resistance() -> None:
    run = core.load_run(SAMPLE_RUN)

    currents, resistances = core.power_axis_points(run)

    assert len(currents) == len(resistances)
    assert len(currents) > 1000
    assert min(currents) >= 0.0
    assert min(resistances) > 0.0


def test_summarize_current_sweep_reports_per_target_strain_with_per_curve_l0() -> None:
    run = core.load_run(SAMPLE_RUN)

    summary = core.summarize_current_sweep(run)

    assert len(summary.targets) == 9
    first = summary.targets[0]
    assert first.stress_mpa == pytest.approx(50.0)
    assert first.load_g == pytest.approx(1.46, abs=0.01)
    assert first.l0_mm == pytest.approx(52.8, abs=0.1)
    assert first.max_current_mA == pytest.approx(79.9, abs=0.2)
    assert first.max_strain_pct >= first.strain_at_max_current_pct >= 0.0
    lines = core.format_current_sweep_strain_summary(summary)
    assert lines[0].startswith("50 MPa / 1.46 g:")
    assert "@ 80 mA" in lines[0]
    assert core.format_current_sweep_break_summary(summary) == ""


def test_strain_summary_reports_strain_at_max_current_not_absolute_max() -> None:
    summary = core.CurrentSweepSummary(
        targets=(
            core.CurrentSweepTargetSummary(
                stress_mpa=50.0,
                load_g=1.56,
                l0_mm=60.0,
                max_current_mA=80.0,
                max_strain_pct=2.5,
                strain_at_max_current_pct=1.25,
            ),
        ),
    )

    assert core.format_current_sweep_strain_summary(summary) == [
        "50 MPa / 1.56 g: 1.25% @ 80 mA"
    ]


def test_summarize_current_sweep_estimates_transition_currents_from_up_down_legs() -> None:
    heating_current = np.linspace(1.0, 100.0, 120)
    cooling_current = np.linspace(100.0, 1.0, 120)

    def piecewise(current: np.ndarray, start: float, finish: float) -> np.ndarray:
        before = 0.1 + current * 0.002
        start_value = 0.1 + start * 0.002
        transition = start_value + (current - start) * 0.04
        finish_value = start_value + (finish - start) * 0.04
        after = finish_value + (current - finish) * 0.003
        return np.where(current < start, before, np.where(current <= finish, transition, after))

    frame = pd.DataFrame(
        {
            "elapsed_s": np.arange(240, dtype=float),
            "automation_phase": ["current"] * 240,
            "automation_target_value": [50.0] * 240,
            "plateau_index": [1] * 240,
            "strain_pct": np.concatenate(
                [
                    piecewise(heating_current, 30.0, 70.0),
                    piecewise(cooling_current, 25.0, 65.0),
                ]
            ),
            "resistance_ohm": [100.0] * 240,
            "current_mA": np.concatenate([heating_current, cooling_current]),
            "current_set_mA": np.concatenate([heating_current, cooling_current]),
            "current_measured_mA": np.concatenate([heating_current, cooling_current]),
        }
    )
    run = core.MiniDmaRun(
        path=Path("run"),
        measurement_path=Path("run") / "measurement.csv",
        frame=frame,
        sample_name="Ni50Fe27Ga23 12_2",
    )

    summary = core.summarize_current_sweep(run)

    target = summary.targets[0]
    assert target.as_current_mA == pytest.approx(30.0, abs=1.5)
    assert target.af_current_mA == pytest.approx(70.0, abs=1.5)
    assert target.ms_current_mA == pytest.approx(65.0, abs=1.5)
    assert target.mf_current_mA == pytest.approx(25.0, abs=1.5)
    lines = core.format_current_sweep_transition_summary(summary)
    assert lines == ["50 MPa: As 30 mA, Af 70 mA, Ms 65 mA, Mf 25 mA"]


def test_summarize_current_sweep_detects_voltage_limit_break() -> None:
    frame = pd.DataFrame(
        {
            "elapsed_s": [0.0, 1.0, 2.0],
            "automation_phase": ["current", "current", "current"],
            "automation_target_value": [400.0, 400.0, 400.0],
            "plateau_index": [1, 1, 1],
            "strain_pct": [0.0, 0.5, 0.7],
            "resistance_ohm": [100.0, 110.0, 0.0],
            "current_mA": [5.0, 20.0, 0.2],
            "current_set_mA": [5.0, 20.0, 35.0],
            "current_measured_mA": [5.0, 20.0, 0.2],
            "voltage_V": [0.5, 2.0, 9.9],
            "position_mm": [0.0, 0.05, 0.07],
        }
    )
    run = core.MiniDmaRun(
        path=Path("run"),
        measurement_path=Path("run") / "measurement.csv",
        frame=frame,
        sample_name="Ni50Fe27Ga23 12_3",
        initial_length_mm=10.0,
        wire_diameter_mm=0.0191,
    )

    summary = core.summarize_current_sweep(run, voltage_limit_v=10.0)

    assert summary.break_point is not None
    assert summary.break_point.stress_mpa == pytest.approx(400.0)
    assert summary.break_point.load_g == pytest.approx(11.69, abs=0.01)
    assert summary.break_point.current_mA == pytest.approx(35.0)
    assert "400 MPa / 11.69 g @ 35 mA" in core.format_current_sweep_break_summary(summary)


def test_plugin_is_registered() -> None:
    registry = builtin_plugin_registry()

    assert "Mini DMA" in registry


def test_plugin_defaults_to_global_strain_baseline_and_power_axis() -> None:
    app = _ensure_qapp()
    host = QtWidgets.QWidget()
    plugin = MiniDmaPlugin(host, "Mini DMA")
    try:
        plugin.settings_widget()
        assert plugin._strain_baseline_mode() == core.STRAIN_BASELINE_GLOBAL_MINIMUM
        assert plugin._show_power_top_axis_enabled() is True
        plugin._set_strain_baseline_mode(core.STRAIN_BASELINE_PER_TARGET_MINIMUM)
        assert plugin._strain_baseline_mode() == core.STRAIN_BASELINE_PER_TARGET_MINIMUM
    finally:
        host.close()
        app.processEvents()
