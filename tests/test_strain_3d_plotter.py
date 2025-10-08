from __future__ import annotations

from pathlib import Path

import importlib.util
import sys

import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "experiments" / "strain_3d_plotter.py"

try:
    spec = importlib.util.spec_from_file_location("strain_3d_plotter", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["strain_3d_plotter"] = module
    spec.loader.exec_module(module)
except ImportError as exc:  # pragma: no cover - environment guard
    pytest.skip(f"Strain 3D plotter dependencies missing: {exc}", allow_module_level=True)


def test_extract_element_counts_parses_all_tokens() -> None:
    counts = module._extract_element_counts("Ni49Fe28Ga21Si2")
    assert counts == {"Ni": 49.0, "Fe": 28.0, "Ga": 21.0, "Co": 0.0}


def test_extract_element_counts_handles_missing_composition() -> None:
    counts = module._extract_element_counts("")
    assert counts == {"Ni": 0.0, "Fe": 0.0, "Ga": 0.0, "Co": 0.0}


def test_auto_plot_combinations_only_include_strain_axis() -> None:
    combos = module._auto_plot_combinations(
        ["Strain", "d (µm)", "D (µm)", "Ni (%)"],
        "Strain",
        include_2d=True,
        include_3d=True,
    )
    assert all("Strain" in combo.labels for combo in combos)
    assert any(combo.dimension == 2 for combo in combos)
    assert any(combo.dimension == 3 for combo in combos)


def test_auto_plot_combinations_respects_disabled_dimensions() -> None:
    combos = module._auto_plot_combinations(
        ["Strain", "d (µm)", "D (µm)"],
        "Strain",
        include_2d=False,
        include_3d=True,
    )
    assert all(combo.dimension == 3 for combo in combos)
    combos = module._auto_plot_combinations(
        ["Strain", "d (µm)", "D (µm)"],
        "Strain",
        include_2d=True,
        include_3d=False,
    )
    assert all(combo.dimension == 2 for combo in combos)
