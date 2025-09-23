"""Tests for the microwire data builder core logic."""

from __future__ import annotations

from pathlib import Path

import importlib.util
import sys

import pandas as pd
import pytest

CORE_PATH = (
    Path(__file__).resolve().parent.parent
    / "experiments"
    / "microwire_data_builder"
    / "core.py"
)

spec = importlib.util.spec_from_file_location("microwire_data_builder_core", CORE_PATH)
assert spec and spec.loader
core = importlib.util.module_from_spec(spec)
sys.modules["microwire_data_builder_core"] = core
spec.loader.exec_module(core)

BuilderConfig = core.BuilderConfig
build_database = core.build_database
_header_key = core._header_key
_load_annealing = core._load_annealing
_metadata_from_path = core._metadata_from_path
_resistance_sanity_check = core._resistance_sanity_check
_safe_plot_stem = core._safe_plot_stem


def test_filename_parser_extracts_metadata(tmp_path: Path) -> None:
    path = tmp_path / "Ni50Fe27Ga23 6_4a s2 30mA.txt"
    path.write_text("0.1 0.2 2.0\n")
    metadata = _metadata_from_path(path)
    assert metadata.composition_token == "Ni50Fe27Ga23"
    assert metadata.draw_x == 6
    assert metadata.piece_y == 4
    assert metadata.alt_variant is True
    assert metadata.setpoint_mA == 30
    assert metadata.file_name == path.name
    assert metadata.measurement_id


def test_annealing_loader_and_sanity_check(tmp_path: Path) -> None:
    content = "0.1 0.2 2.0\n0.2 0.4 2.0\n0.3 0.6 2.0\n"
    path = tmp_path / "anneal.txt"
    path.write_text(content)
    df = _load_annealing(path)
    assert list(df.columns) == ["I_A", "V_V", "R_ohm"]
    ok, error = _resistance_sanity_check(df)
    assert ok is True
    assert error is not None
    assert error < 1e-6


def test_header_normaliser_variants() -> None:
    assert _header_key("hmotnosť") == "mass_g"
    assert _header_key("P.Č") == "piece_y"
    assert _header_key("d (µm)") == "d_um"
    assert _header_key("D (µm)") == "D_um"
    assert _header_key("d/D") == "d_over_D"
    assert _header_key("Poznámka") == "notes"


def test_safe_plot_stem_removes_path_separators() -> None:
    stem = _safe_plot_stem("Ni55Fe18Ga27 4/1 s1 1000mA")
    assert "/" not in stem
    assert stem.endswith("1000mA")


def test_build_database_integration(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    base = Path("sample_data/database_builder")
    anneal_files = [
        base / "current annealing data" / "Ni55Fe18Ga27 4_1 s1 1000mA.txt",
        base / "current annealing data" / "Ni55Fe18Ga27 4_1 s2 100mA.txt",
    ]
    composition_file = base / "microwire data" / "Ni55Fe18Ga27" / "Ni55Fe18Ga27.xlsx"
    piece_dir = base / "microwire data" / "Ni55Fe18Ga27" / "4.Ni55Fe18Ga27 26112024 0850"
    piece_file = sorted(piece_dir.glob("*.xlsx"))[0]

    config = BuilderConfig(
        fabrication_files=[composition_file, piece_file],
        annealing_files=anneal_files,
        output_dir=tmp_path / "out",
    )

    result = build_database(config)
    df = result.dataframe
    assert len(df) == 1
    row = df.iloc[0]
    assert list(df.columns) == core.OUTPUT_COLUMNS
    assert row["Composition"] == "Ni55Fe18Ga27"
    assert row["Microwire"] == "4/1"
    assert row["File 1000 mA"] == anneal_files[0].name
    assert row["File low mA"] == anneal_files[1].name
    assert row["Low mA value (mA)"] == 100
    assert float(row["d (µm)"]) > 0.0
    assert row["Production datetime"] == "2024-11-26 08:50:00"
    assert "csv" in result.exports
    assert Path(result.exports["csv"]).exists()


def test_build_database_populates_plot_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    high = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 1_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.1\n0.1 0.2 2.1\n")

    produced: dict[str, Path] = {}

    def fake_plot(df, source: Path, plot_dir: Path) -> Path:
        plot_dir.mkdir(parents=True, exist_ok=True)
        out_path = plot_dir / f"{source.stem}.png"
        out_path.write_text("stub")
        produced[source.name] = out_path
        return out_path

    monkeypatch.setattr(core, "_plot_measurement_matplotlib", fake_plot)

    config = BuilderConfig(
        fabrication_files=[],
        annealing_files=[high, low],
        output_dir=tmp_path / "out",
        make_plots=True,
    )

    result = build_database(config)
    assert result.plot_paths
    assert not result.origin_targets
    row = result.dataframe.iloc[0]
    assert row["Figure — 1000 mA"] == produced[high.name].name
    assert row["Figure — low mA"] == produced[low.name].name
    assert pd.isna(row["Figure — 1000 mA (Origin)"])
    assert pd.isna(row["Figure — low mA (Origin)"])
    assert set(result.plot_paths) == {produced[high.name], produced[low.name]}
    assert row["Low mA value (mA)"] == 120


def test_build_database_origin_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    high = tmp_path / "Ni55Fe18Ga27 1_1 1000mA.txt"
    low = tmp_path / "Ni55Fe18Ga27 1_1 120mA.txt"
    high.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n")
    low.write_text("0.05 0.1 2.1\n0.1 0.2 2.1\n")

    origin_records: dict[str, str] = {}

    def fake_origin(df, source: Path) -> str:
        origin_records[source.name] = f"Origin:{source.stem}"
        return origin_records[source.name]

    monkeypatch.setattr(core, "_plot_measurement_origin", fake_origin)

    config = BuilderConfig(
        fabrication_files=[],
        annealing_files=[high, low],
        output_dir=tmp_path / "out",
        make_plots=True,
        plot_backends=("origin",),
    )

    result = build_database(config)
    assert not result.plot_paths
    assert set(result.origin_targets) == set(origin_records.values())
    row = result.dataframe.iloc[0]
    assert row["Figure — 1000 mA (Origin)"] == origin_records[high.name]
    assert row["Figure — low mA (Origin)"] == origin_records[low.name]
    assert pd.isna(row["Figure — 1000 mA"])
    assert pd.isna(row["Figure — low mA"])
