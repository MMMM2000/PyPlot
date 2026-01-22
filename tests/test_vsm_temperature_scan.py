from __future__ import annotations

from pathlib import Path

import importlib.util
import sys

import pandas as pd
import pytest

MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "experiments"
    / "simple_scripts"
    / "vsm_temperature_scan.py"
)

spec = importlib.util.spec_from_file_location("vsm_temperature_scan", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules["vsm_temperature_scan"] = module
try:
    spec.loader.exec_module(module)  # type: ignore[call-arg]
except ImportError as exc:  # pragma: no cover - environment guard
    pytest.skip(f"VSM temperature scan dependencies missing: {exc}", allow_module_level=True)


def test_build_series_orders_sections_and_fields() -> None:
    processor = module.VSMTemperatureScanProcessor()
    processor.set_split_directions(True)
    frame = pd.DataFrame(
        {
            "temperature": [10, 20, 30, 40, 10, 20, 30, 40],
            "field": [5, 5, 5, 5, 10000, 10000, 10000, 10000],
            "signal": [1, 2, 3, 4, 5, 6, 7, 8],
            "section_index": [0, 0, 1, 1, 0, 0, 1, 1],
        }
    )

    series = processor._build_series(frame)

    order = [(int(entry.field), entry.segment_index) for entry in series]
    assert order == [(10000, 0), (10000, 1), (5, 0), (5, 1)]


def test_plot_title_includes_field_labels() -> None:
    processor = module.VSMTemperatureScanProcessor()
    title = processor._plot_title("Sample", "VSM Temperature Scan", [5, 10000])
    assert "Sample - VSM Temperature Scan" in title
    assert "5 Oe" in title
    assert "10000 Oe" in title


def test_combine_dual_field_entries_merges_high_low() -> None:
    processor = module.VSMTemperatureScanProcessor()
    low_frame = pd.DataFrame(
        {"temperature": [10, 20], "field": [5, 5], "signal": [1.0, 1.2]}
    )
    high_frame = pd.DataFrame(
        {"temperature": [10, 20], "field": [10000, 10000], "signal": [2.0, 2.2]}
    )
    other_frame = pd.DataFrame(
        {"temperature": [10, 20], "field": [8000, 8000], "signal": [3.0, 3.2]}
    )
    entries = [
        module.VSMEntry(path=Path("low.txt"), sample="Sample", dataframe=low_frame),
        module.VSMEntry(path=Path("high.txt"), sample="Sample", dataframe=high_frame),
        module.VSMEntry(path=Path("other.txt"), sample="Other", dataframe=other_frame),
    ]

    combined = processor._combine_dual_field_entries(entries)

    assert len(combined) == 2
    combined_sample = next(entry for entry in combined if entry.sample == "Sample")
    fields = set(combined_sample.dataframe["field"].tolist())
    assert 5 in fields
    assert 10000 in fields


def test_axis_label_uses_kilooe_for_high_fields() -> None:
    processor = module.VSMTemperatureScanProcessor()
    label = processor._axis_label_for_fields([10000], base="Magnetization")
    assert "10kOe" in label
