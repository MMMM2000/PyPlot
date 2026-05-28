from __future__ import annotations

from pathlib import Path

import matplotlib
import pytest
from PyQt6 import QtWidgets

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt  # noqa: E402

from plotting.plugins import builtin_plugin_registry
from plotting.plugins.mini_dma import core
from plotting.plugins.mini_dma.mini_dma_plugin import MiniDmaPlugin


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
        assert strain_ax.get_xlabel() == "Current [mA] (79.9 mA = 279 A/mm², d = 19.1 µm)"
        assert strain_ax.get_ylabel() == "Strain [%]"
        assert resistance_ax.get_ylabel() == "Resistance [Ohm]"
        assert strain_ax.lines[0].get_label() == "50 MPa / 1.46 g"
        assert strain_ax.get_legend().get_title().get_text() == "Stress / load"
    finally:
        plt.close(strain_fig)
        plt.close(resistance_fig)


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
        assert ax.get_ylabel() == "Strain from trace-minimum length [%]"
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
        assert ax.get_ylabel() == "Strain from global minimum length [%]"
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
