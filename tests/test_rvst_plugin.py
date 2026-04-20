from __future__ import annotations

from pathlib import Path
import importlib

import matplotlib
import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="Qt widgets backend is unavailable", exc_type=ImportError)

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import pandas as pd


rvst_core = importlib.import_module("plotting.plugins.r_vs_t.core")


def test_format_rvst_title_formats_composition_and_microwire_suffix() -> None:
    title = rvst_core.format_rvst_title("Ni50Fe27Ga23_10_1wgca10")
    assert title == "Ni₅₀Fe₂₇Ga₂₃ 10/1 wgca10"


def test_format_rvst_title_handles_extra_suffix_token() -> None:
    title = rvst_core.format_rvst_title("Ni50Fe27Ga23_5_4_wg")
    assert title == "Ni₅₀Fe₂₇Ga₂₃ 5/4 wg"


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
    ]
    assert df["pv_c"].tolist() == pytest.approx([-42.5, -42.6])
    assert df["resistance_ohm"].tolist() == pytest.approx([0.065962, 0.065963])


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


def test_plot_one_uses_measured_temperature_and_grouped_legend_labels() -> None:
    df = pd.DataFrame(
        {
            "pv_c": [-10.0, -5.0, 0.0, 5.0, 0.0, -5.0],
            "resistance_ohm": [1.0, 1.1, 1.2, 1.3, 1.25, 1.15],
        }
    )

    fig, _ = rvst_core.plot_one(df, "Ni₅₀Fe₂₇Ga₂₃ 10/1 wgca10")
    try:
        ax = fig.axes[0]
        assert ax.get_xlabel() == "Temperature [°C]"
        assert ax.get_ylabel() == "Resistance [Ω]"
        legend = ax.get_legend()
        assert legend is not None
        labels = [text.get_text() for text in legend.get_texts()]
        assert labels == ["Heating", "Cooling"]
    finally:
        plt.close(fig)
