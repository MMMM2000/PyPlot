from __future__ import annotations

from pathlib import Path

import matplotlib
import pytest

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt

from plotting.plugins import builtin_plugin_registry
from plotting.plugins.mini_dma import core

SAMPLE_RUN = Path("sample_data/mini dma/Ni50Fe27Ga23 12_2 test_run32")


def test_load_run_accepts_folder_and_metadata_sample_name() -> None:
    run = core.load_run(SAMPLE_RUN)

    assert run.measurement_path.name == "measurement.csv"
    assert run.sample_name == "Ni50Fe27Ga23 12_2 test"
    assert len(run.frame) > 3000
    assert "current_mA" in run.frame.columns


def test_current_sweep_groups_by_target_mpa() -> None:
    run = core.load_run(SAMPLE_RUN / "measurement.csv")
    groups = core.current_sweep_groups(run.frame)

    targets = [target for target, _group in groups]
    assert targets == pytest.approx([50, 100, 150, 200, 250, 300, 350, 400, 450])
    assert all(len(group) >= 2 for _target, group in groups)


def test_make_figures_create_one_line_per_target() -> None:
    run = core.load_run(SAMPLE_RUN)

    strain_fig = core.make_strain_current_figure(run)
    resistance_fig = core.make_resistance_current_figure(run)
    try:
        strain_ax = strain_fig.axes[0]
        resistance_ax = resistance_fig.axes[0]
        assert strain_ax.get_xlabel() == "Measured current [mA]"
        assert strain_ax.get_ylabel() == "Strain [%]"
        assert resistance_ax.get_ylabel() == "Resistance [Ohm]"
        assert len(strain_ax.lines) == 9
        assert len(resistance_ax.lines) == 9
        assert max(max(line.get_ydata()) for line in resistance_ax.lines) < 250.0
        assert [line.get_label() for line in strain_ax.lines][:3] == [
            "50 MPa",
            "100 MPa",
            "150 MPa",
        ]
    finally:
        plt.close(strain_fig)
        plt.close(resistance_fig)


def test_strain_current_figure_can_zero_each_trace_minimum() -> None:
    run = core.load_run(SAMPLE_RUN)

    fig = core.make_strain_current_figure(run, zero_minimum_strain=True)
    try:
        ax = fig.axes[0]
        assert ax.get_ylabel() == "Strain relative to trace minimum [%]"
        assert len(ax.lines) == 9
        for line in ax.lines:
            assert min(line.get_ydata()) == pytest.approx(0.0)
    finally:
        plt.close(fig)


def test_build_plot_frame_pairs_current_with_requested_y_column() -> None:
    run = core.load_run(SAMPLE_RUN)
    frame = core.build_plot_frame(run, y_column="strain_pct")

    assert "50_MPa_current_mA" in frame.columns
    assert "50_MPa_strain_pct" in frame.columns
    assert "450_MPa_current_mA" in frame.columns
    assert "450_MPa_strain_pct" in frame.columns


def test_plugin_is_registered() -> None:
    registry = builtin_plugin_registry()

    assert "Mini DMA" in registry
