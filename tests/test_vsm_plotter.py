from __future__ import annotations

from pathlib import Path

import importlib.util
import sys

import pandas as pd
import pytest

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
        "Time since start [s]",
        "Applied Field [Oe]",
        "Signal parallel with sample [emu]",
    ]
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3


def test_read_vsm_file_handles_inline_header_and_noise(tmp_path: Path) -> None:
    path = tmp_path / "202507101320-Hys-a050-T-30-00.VSM-Hys-Data"
    content = """@@End of Header.
Random metadata line
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


def test_read_vsm_file_prefers_column_block_to_noise(tmp_path: Path) -> None:
    path = tmp_path / "202507101115-Hys-a000-T-30-00.VSM-Hys-Data"
    content = """Random preamble text
1k Oe: Field Setting Par : FTCR = 5000; DR = 10000;
@@Columns
Column 0: Time since start, Time [s]
Column 1: Raw Applied Field, Applied Field [Oe]
Column 2: Field Angle, Field Angle [deg]
@@END Columns
@@End of Header.
Time_since_start Raw_Applied_Field Field_Angle
@@Data
New Section: Section 0:
1.0 100.0 0.0
2.0 -100.0 0.0
@@END Data
"""
    path.write_text(content)

    df = module._read_vsm_file(path)

    assert list(df.columns) == [
        "Time since start [s]",
        "Raw Applied Field [Oe]",
        "Field Angle [deg]",
    ]
    assert df.iloc[0].tolist() == [1.0, 100.0, 0.0]


def test_parse_temperature_and_angle(tmp_path: Path) -> None:
    path = _write_sample(tmp_path)
    assert module._parse_temperature(path) == -30.0
    assert module._parse_angle(path) == 140.0


def test_parse_metadata_falls_back_to_header(tmp_path: Path) -> None:
    path = tmp_path / "fallback.VSM-Hys-Data"
    content = """@Filename: C\\data\\Ni\\20250712-Hys-a095-T-45-00.VSM-Hys-Data
@@Data
New Section: Section 0:
1.0 2.0 3.0
@@END Data
"""
    path.write_text(content)

    # Ensure cached values from previous tests do not leak in.
    module._metadata_from_file.cache_clear()  # type: ignore[attr-defined]

    assert module._parse_angle(path) == 95.0
    assert module._parse_temperature(path) == -45.0


def test_parse_handles_zero_angle_token(tmp_path: Path) -> None:
    path = tmp_path / "202507101115-Hys-a000-T-30-00.VSM-Hys-Data"
    path.write_text("@@Data\nNew Section: Section 0:\n1 2 3\n@@END Data\n")

    module._metadata_from_file.cache_clear()  # type: ignore[attr-defined]

    assert module._parse_angle(path) == 0.0
    assert module._parse_temperature(path) == -30.0


def test_parse_metadata_from_set_temperature_line(tmp_path: Path) -> None:
    path = tmp_path / "no_tokens.VSM-Hys-Data"
    content = """Action 0:      Set Field Angle to -15.5 [deg]
Action 1:      Set Sample Temperature to -29.5989 [degC]
@@Data
New Section: Section 0:
1 2 3
@@END Data
"""
    path.write_text(content)

    module._metadata_from_file.cache_clear()  # type: ignore[attr-defined]

    assert module._parse_angle(path) == -15.5
    assert module._parse_temperature(path) == -29.5989


def test_parse_metadata_from_angle_offset(tmp_path: Path) -> None:
    path = tmp_path / "offset_only.VSM-Hys-Data"
    content = """Sample Angle Offset = 42.0
@@Data
New Section: Section 0:
1 2 3
@@END Data
"""
    path.write_text(content)

    module._metadata_from_file.cache_clear()  # type: ignore[attr-defined]

    assert module._parse_angle(path) == 42.0


def test_read_vsm_file_rejects_empty(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.VSM-Hys-Data"
    empty_path.write_text("@@Data\n@@END Data\n")
    try:
        module._read_vsm_file(empty_path)
    except ValueError as exc:
        assert "No data rows" in str(exc)
    else:  # pragma: no cover - ensure failure is surfaced
        raise AssertionError("Expected ValueError for empty VSM file")


def test_safe_float_handles_dash_tokens() -> None:
    assert module._safe_float("-30-00") == -30.0
    assert module._safe_float("30-50") == 30.50
    assert module._safe_float("+12-345") == 12.345
    assert module._safe_float("000-") == 0.0


def test_metadata_can_be_derived_from_columns(tmp_path: Path) -> None:
    content = """@@Columns
Column 0: Time since start, Time [s]
Column 1: Field Angle, Field Angle [deg]
Column 2: Temperature, Sample Temperature [degC]
Column 3: Applied Field, Applied Field [Oe]
@@END Columns
@@End of Header.
@@Data
New Section: Section 0:
0.0 12.0 -29.8 0.0
1.0 12.0 -30.2 1.0
2.0 12.0 -30.0 -1.0
@@END Data
"""
    path = tmp_path / "no_header_metadata.VSM-Hys-Data"
    path.write_text(content)

    df = module._read_vsm_file(path)
    angle, temperature = module._derive_metadata_from_dataframe(df)

    assert angle == pytest.approx(12.0)
    assert temperature == pytest.approx(-30.0, abs=0.2)
