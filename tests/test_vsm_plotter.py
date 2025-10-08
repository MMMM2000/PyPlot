from __future__ import annotations

from pathlib import Path

import importlib.util
import sys

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parent.parent / "experiments" / "vsm_plotter.py"

spec = importlib.util.spec_from_file_location("vsm_plotter", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules["vsm_plotter"] = module
try:
    spec.loader.exec_module(module)  # type: ignore[call-arg]
except ImportError as exc:  # pragma: no cover - environment guard
    import pytest

    pytest.skip(f"VSM plotter dependencies missing: {exc}", allow_module_level=True)


def _write_sample(tmp_path: Path) -> Path:
    content = """@Section 0
Column 0: Time since start, Time [s]
Column 1: Applied Field, Applied Field [Oe]
Column 2: Signal parallel with sample, Moment [emu]
@@END Columns
@@End of Header.
@Time at start of measurement: 10:43:37
@@Data
New Section: Section 0:
0.0 0.0 0.0
1.0 5.0 0.2
2.0 -5.0 -0.2
@@END Data
"""
    path = tmp_path / "202507101320-Hys-a140-T-30-00.VSM-Hys-Data"
    path.write_text(content)
    return path


def test_read_vsm_file_parses_numeric_columns(tmp_path: Path) -> None:
    path = _write_sample(tmp_path)
    df = module._read_vsm_file(path)
    assert list(df.columns)[:3] == [
        "Time since start",
        "Applied Field",
        "Signal parallel with sample",
    ]
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3


def test_read_vsm_file_handles_inline_header_and_noise(tmp_path: Path) -> None:
    path = tmp_path / "202507101320-Hys-a050-T-30-00.VSM-Hys-Data"
    content = """Random metadata line
Instrument 1k Oe: Field Setting Par : FTCR = 5000; DR = 10000;
Time_since_start Applied_Field Loop
@@Data
New Section: Section 0:
0.0 0.0 0.0
@@END Data.
Notes between sections
@@Final Manipulated Data
New Section: Section 0:
1.0 5.0 0.2
2.0 -5.0 -0.3
@@END Data
"""
    path.write_text(content)

    df = module._read_vsm_file(path)

    assert list(df.columns) == ["Time since start", "Applied Field", "Loop"]
    assert len(df) == 2
    assert df.iloc[0].tolist() == [1.0, 5.0, 0.2]


def test_parse_temperature_and_angle(tmp_path: Path) -> None:
    path = _write_sample(tmp_path)
    assert module._parse_temperature(path) == -30.0
    assert module._parse_angle(path) == 140.0


def test_read_vsm_file_rejects_empty(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.VSM-Hys-Data"
    empty_path.write_text("@@Data\n@@END Data\n")
    try:
        module._read_vsm_file(empty_path)
    except ValueError as exc:
        assert "No data rows" in str(exc)
    else:  # pragma: no cover - ensure failure is surfaced
        raise AssertionError("Expected ValueError for empty VSM file")
