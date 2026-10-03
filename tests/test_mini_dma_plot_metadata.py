from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from plotting.plugins.mini_dma import core


def _many_curve_run(tmp_path: Path) -> core.MiniDmaRun:
    records = []
    for cycle in range(401):
        for point, current in enumerate((0.0, 5.0, 10.0, 5.0, 0.0)):
            records.append({
                "elapsed_s": cycle * 5.0 + point,
                "automation_phase": "current",
                "automation_target_value": 20.0,
                "plateau_index": float("nan") if cycle == 0 else float(cycle),
                "current_mA": current,
                "strain_pct": cycle * 0.01 + point * 0.1,
                "resistance_ohm": 100.0 + point,
            })
    return core.MiniDmaRun(
        path=tmp_path, measurement_path=tmp_path / "measurement.csv",
        frame=pd.DataFrame.from_records(records), sample_name="synthetic fatigue",
        initial_length_mm=40.0, wire_diameter_mm=0.02,
    )


@pytest.mark.parametrize("quantity", ["strain_pct", "resistance_ohm"])
def test_long_history_reads_metadata_once_and_keeps_every_curve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quantity: str
) -> None:
    run = _many_curve_run(tmp_path)
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps({"current_sweep": {
        "first_overheating": True, "first_overheating_target_mpa": 20.0,
    }}), encoding="utf-8")
    reads = []
    original = Path.read_text

    def counted_read(path: Path, *args, **kwargs):
        if path == metadata_path:
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read)
    make = core.make_strain_current_figure if quantity == "strain_pct" else core.make_resistance_current_figure
    figure = make(run)
    try:
        groups = core.current_sweep_groups(run.frame)
        lines = figure.axes[0].lines
        assert len(reads) == 1
        assert len(lines) == len(groups) == 401
        for line, (_target, group) in zip(lines, groups, strict=True):
            np.testing.assert_array_equal(line.get_xdata(), group["current_mA"])
            np.testing.assert_array_equal(line.get_ydata(), group[quantity])
        assert lines[0].get_label().startswith("1st:")
        assert lines[0].get_linestyle() == "--"
        assert lines[0].get_marker() == "D"
        assert all(not line.get_label().startswith("1st:") for line in lines[1:])
        assert all(line.get_linestyle() == "-" and line.get_marker() == "o" for line in lines[1:])
    finally:
        figure.clear()


def test_next_figure_reads_fresh_metadata_without_a_global_cache(tmp_path: Path) -> None:
    run = _many_curve_run(tmp_path)
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps({"current_sweep": {
        "first_overheating": True, "first_overheating_target_mpa": 20.0,
    }}), encoding="utf-8")
    first = core.make_strain_current_figure(run)
    path.write_text(json.dumps({"current_sweep": {"first_overheating": False}}), encoding="utf-8")
    second = core.make_strain_current_figure(run)
    try:
        assert first.axes[0].lines[0].get_linestyle() == "--"
        assert second.axes[0].lines[0].get_linestyle() == "-"
        assert not second.axes[0].lines[0].get_label().startswith("1st:")
        assert len(first.axes[0].lines) == len(second.axes[0].lines) == 401
    finally:
        first.clear()
        second.clear()
