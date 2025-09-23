from __future__ import annotations
from pathlib import Path

import importlib

import matplotlib
import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="Qt widgets backend is unavailable", exc_type=ImportError)

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import pandas as pd


anneal_core = importlib.import_module("plotting.current_annealing.core")


def test_load_file_trims_burnthrough_point(tmp_path: Path) -> None:
    path = tmp_path / "burn.txt"
    path.write_text("0.01 0.02 2\n0.12 0.24 2\n0.07 0.14 2\n")
    df = anneal_core.load_file(path)
    values = df["I_mA"].tolist()
    assert values == pytest.approx([10.0, 120.0])


def test_plot_one_bridges_increasing_to_decreasing_segment() -> None:
    df = pd.DataFrame(
        {
            "I_mA": [0.0, 20.0, 40.0, 60.0, 50.0, 40.0],
            "R_Ohm": [100.0, 150.0, 200.0, 250.0, 260.0, 270.0],
        }
    )
    fig, _ = anneal_core.plot_one(df, "Sample")
    ax = fig.axes[0]
    blue_lines = [line for line in ax.lines if line.get_color() == "b"]
    assert blue_lines, "Expected a decreasing (blue) segment"
    first_blue = blue_lines[0]
    blue_x = list(first_blue.get_xdata())
    assert blue_x[0] == pytest.approx(60.0)
    assert blue_x[1] == pytest.approx(50.0)
    width, height = fig.get_size_inches()
    assert width == pytest.approx(4.0)
    assert height == pytest.approx(2.25)
    plt.close(fig)
