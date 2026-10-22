from __future__ import annotations

from pathlib import Path

import pytest

from plotting.plugins.vsm_isotherms import core as module



def test_load_parses_header_metadata_for_legacy_filename(vsm_data_dir: Path) -> None:
    processor = module.VSMIsothermProcessor()
    path = vsm_data_dir / "legacy-VIR-03.VSM-VIR-DATA"

    entries = processor.load([path])

    assert len(entries) == 1
    entry = entries[0]
    assert entry.sample == "Synthetic alloy"
    assert entry.angle == 0.0
    assert entry.temperature == pytest.approx(140.0, abs=1.0)
    assert list(entry.dataframe.columns) == ["field", "signal"]
    assert len(entry.dataframe.index) == 101
    assert entry.dataframe.iloc[-1].tolist() == pytest.approx([10000.0, 1.4])


def test_group_by_sample_and_angle_keeps_zero_and_ninety_separate(vsm_data_dir: Path) -> None:
    processor = module.VSMIsothermProcessor()
    paths = [
        vsm_data_dir / "synthetic-a000-T150.VSM-VIR-Data",
        vsm_data_dir / "synthetic-a090-T100.VSM-VIR-Data",
    ]

    entries = processor.load(paths)
    grouped = processor.group_by_sample_angle(entries)

    angles = sorted(round(angle) for _, angle in grouped.keys())
    assert angles == [0, 90]


def test_group_by_sample_and_angle_collapses_duplicate_temperature_runs(vsm_data_dir: Path) -> None:
    processor = module.VSMIsothermProcessor()
    paths = sorted(vsm_data_dir.glob("*.VSM-VIR-DATA")) + sorted(vsm_data_dir.glob("*.VSM-VIR-Data"))

    entries = processor.load(paths)
    grouped = processor.group_by_sample_angle(entries)

    zero_entries = next(values for (_, angle), values in grouped.items() if round(angle) == 0)
    ninety_entries = next(values for (_, angle), values in grouped.items() if round(angle) == 90)

    zero_150 = [entry for entry in zero_entries if round(entry.temperature) == 150]
    ninety_150 = [entry for entry in ninety_entries if round(entry.temperature) == 150]

    assert len(zero_150) == 1
    assert len(ninety_150) == 1
    assert len(zero_150[0].dataframe.index) == 101
    assert len(ninety_150[0].dataframe.index) == 101


def test_load_ignores_non_vir_extensions(tmp_path: Path) -> None:
    vir_path = tmp_path / "example.VSM-VIR-DATA"
    hys_path = tmp_path / "example.VSM-HYS-DATA"
    text = """
@Samplename: Test Sample
Set Field Angle to 0
Set Sample Temperature to 25
@@End of Header.
Time_since_start Applied_Field_For_Plot_ Signal_X_direction
@@Data
0 0 0
1 100 0.1
2 200 0.2
@@END Data
""".strip()
    vir_path.write_text(text, encoding="utf-8")
    hys_path.write_text(text, encoding="utf-8")

    processor = module.VSMIsothermProcessor()
    entries = processor.load([vir_path, hys_path])

    assert len(entries) == 1
    assert entries[0].path == vir_path


def test_compute_entropy_returns_temperature_curve(vsm_data_dir: Path) -> None:
    processor = module.VSMIsothermProcessor()
    paths = sorted(vsm_data_dir.glob("*a090-T*.VSM-VIR-Data"))[:10]
    entries = processor.load(paths)
    grouped = processor.group_by_sample_angle(entries)
    angle_group = next(values for (_, angle), values in grouped.items() if round(angle) == 90)

    entropy = processor.compute_entropy(angle_group, temperature_bin_c=5.0)

    assert entropy is not None
    assert entropy.max_delta_field > 1000
    assert "temperature" in entropy.frame.columns
    entropy_columns = [col for col in entropy.frame.columns if col.startswith("dS_")]
    assert entropy_columns
    assert len(entropy.frame.index) >= 2


def test_compute_entropy_accepts_custom_field_levels(vsm_data_dir: Path) -> None:
    processor = module.VSMIsothermProcessor()
    paths = sorted(vsm_data_dir.glob("*a090-T*.VSM-VIR-Data"))[:10]
    entries = processor.load(paths)
    grouped = processor.group_by_sample_angle(entries)
    angle_group = next(values for (_, angle), values in grouped.items() if round(angle) == 90)

    entropy = processor.compute_entropy(
        angle_group,
        temperature_bin_c=5.0,
        field_levels_oe=[2000, 5000, 10000, 20000],
    )

    assert entropy is not None
    assert entropy.max_delta_field == pytest.approx(10000.0)
    # M=T*H/1e6 gives -integral(dM/dT dH) = -H^2/2e6.
    assert entropy.frame["dS_5000Oe"].tolist() == pytest.approx([-12.5] * 4)
    assert entropy.frame["dS_10000Oe"].tolist() == pytest.approx([-50.0] * 4)
    columns = list(entropy.frame.columns)
    assert "temperature" in columns
    assert "dS_2000Oe" in columns
    assert "dS_5000Oe" in columns
    if entropy.max_delta_field >= 10000:
        assert "dS_10000Oe" in columns
    else:
        assert "dS_10000Oe" not in columns
    assert "dS_20000Oe" not in columns
