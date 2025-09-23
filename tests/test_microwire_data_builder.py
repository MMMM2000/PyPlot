"""Tests for the microwire data builder core logic."""

from __future__ import annotations

import math
from pathlib import Path

import importlib.util
import sys

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
_curve_features = core._curve_features
_header_key = core._header_key
_load_annealing = core._load_annealing
_metadata_from_path = core._metadata_from_path


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


def test_annealing_loader_and_features(tmp_path: Path) -> None:
    content = "0.1 0.2 2.0\n0.2 0.4 2.5\n0.3 0.9 3.0\n"
    path = tmp_path / "anneal.txt"
    path.write_text(content)
    df = _load_annealing(path)
    assert list(df.columns) == ["I_A", "V_V", "R_ohm"]
    features = _curve_features(df)
    assert features["points"] == 3
    assert math.isclose(features["current_min_A"], 0.1, rel_tol=1e-6)
    assert math.isclose(features["current_max_A"], 0.3, rel_tol=1e-6)
    assert features["nonlinearity_mae_frac"] >= 0.0
    assert math.isclose(features["slope_dR_dI_ohm_per_A"], 5.0, rel_tol=1e-6)


def test_header_normaliser_variants() -> None:
    assert _header_key("hmotnosť") == "mass_g"
    assert _header_key("P.Č") == "piece_y"
    assert _header_key("d (µm)") == "d_um"
    assert _header_key("D (µm)") == "D_um"
    assert _header_key("d/D") == "d_over_D"


def test_build_database_integration(tmp_path: Path) -> None:
    base = Path("sample_data/database_builder")
    anneal_file = base / "current annealing data" / "Ni55Fe18Ga27 4_1 s1 1000mA.txt"
    composition_file = base / "microwire data" / "Ni55Fe18Ga27" / "Ni55Fe18Ga27.xlsx"
    piece_dir = base / "microwire data" / "Ni55Fe18Ga27" / "4.Ni55Fe18Ga27 26112024 0850"
    piece_file = sorted(piece_dir.glob("*.xlsx"))[0]

    config = BuilderConfig(
        fabrication_files=[composition_file, piece_file],
        annealing_files=[anneal_file],
        output_dir=tmp_path / "out",
        make_plots=False,
        export_excel=False,
    )

    result = build_database(config)
    df = result.dataframe
    assert len(df) == 1
    row = df.iloc[0]
    assert row["composition_token"] == "Ni55Fe18Ga27"
    assert row["draw_x"] == 4
    assert row["piece_y"] == 1
    assert math.isclose(float(row["piece_turns"]), 3.0, rel_tol=1e-6)
    assert row["production_datetime"] == "2024-11-26 08:50:00"
    assert Path(result.csv_path).exists()
