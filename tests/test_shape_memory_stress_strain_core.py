from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from plotting.plugins.shape_memory_stress_strain import core


def test_load_manual_stress_strain_file_parses_headered_txt(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text(
        "\n".join(
            [
                "Displacement\tLoad\tStrain\tStress",
                "mm\tg\t%\tMPa",
                "0\t0\t0\t0",
                "0.01\t0.100\t0.050\t1.2",
                "0.02\t0,150\t0,100\t1,8",
            ]
        ),
        encoding="utf-8",
    )
    frame = core.load_manual_stress_strain_file(path)
    assert list(frame.columns) == ["displacement_mm", "load_g", "strain_pct", "stress_mpa"]
    assert len(frame) == 3
    assert frame["stress_mpa"].iloc[-1] == pytest.approx(1.8)


def test_segment_building_labels_loading_unloading_cycles() -> None:
    strains = [0.0, 0.1, 0.2, 0.1, 0.0, 0.2]
    styles = core.build_segment_styles(strains)
    labels = [segment.label for segment in styles]
    assert labels == ["Loading 1", "Unloading 1", "Loading 2"]


def test_make_shape_memory_figure_creates_two_axes() -> None:
    frame = pd.DataFrame(
        {
            "displacement_mm": [0.0, 0.01, 0.02, 0.01],
            "load_g": [0.0, 0.1, 0.2, 0.05],
            "strain_pct": [0.0, 0.05, 0.1, 0.02],
            "stress_mpa": [0.0, 1.0, 2.0, 0.5],
        }
    )
    figure = core.make_shape_memory_figure(frame, title="Example")
    assert len(figure.axes) == 2
