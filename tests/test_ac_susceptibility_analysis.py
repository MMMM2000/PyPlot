from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from data_logging.ac_susceptibility_logger import analysis


def test_compute_apparent_susceptibility_uses_empty_coil_and_filling_factor() -> None:
    config = analysis.SusceptibilityAnalysisConfig(
        sweep_files=(Path("sweep.tsv"),),
        baseline_file=Path("baseline.tsv"),
        output_dir=Path("out"),
        sample=analysis.SampleGeometry(name="test", core_diameter_um=100.0),
        excitation_coil=analysis.ExcitationCoilGeometry(inner_diameter_mm=1.0),
    )
    baseline = pd.DataFrame(
        {
            "frequency_hz": [1000.0],
            "excitation_mA": [5.0],
            "l_empty_h": [10e-6],
            "l_empty_nH": [10_000.0],
            "l_empty_noise_nH": [1.0],
            "r_empty_ohm": [1.0],
            "n_empty": [3],
        }
    )
    points = pd.DataFrame(
        {
            "frequency_hz": [1000.0],
            "excitation_mA": [5.0],
            "h_ac_oe": [2.0],
            "direction": ["up"],
            "current_set_mA": [60.0],
            "l_wire_h": [10.3e-6],
            "l_wire_nH": [10_300.0],
            "l_wire_noise_nH": [2.0],
            "r_wire_ohm": [1.0 + 2.0 * math.pi * 1000.0 * 10e-6 * 0.01 * 0.5],
            "current_actual_mA": [59.9],
            "n_reads": [5],
            "negative_fraction": [0.0],
        }
    )

    result = analysis.compute_apparent_susceptibility(points, baseline, config)

    assert result.loc[0, "filling_factor"] == pytest.approx(0.01)
    assert result.loc[0, "relative_l_change"] == pytest.approx(0.03)
    assert result.loc[0, "chi_prime_app"] == pytest.approx(3.0)
    assert result.loc[0, "chi_double_prime_app"] == pytest.approx(0.5)
    assert result.loc[0, "delta_l_vs_empty_nH"] == pytest.approx(300.0)


def test_format_frequency_uses_khz_for_readability() -> None:
    assert analysis.format_frequency(200.0) == "200 Hz"
    assert analysis.format_frequency(1000.0) == "1 kHz"
    assert analysis.format_frequency(20_000.0) == "20 kHz"
    assert analysis.format_frequency(100_000.0) == "100 kHz"


def test_load_sample_geometry_from_project_prefers_microscope_diameter(tmp_path: Path) -> None:
    project = tmp_path / "project.pydpj"
    project.write_text(
        """
{
  "sections": {
    "videos": {
      "rows": [
        {"Composition": "Ni50Fe27Ga23", "Microwire": "12/2", "d (µm)": "17", "D (µm)": "55"}
      ]
    },
    "microscope": {
      "rows": [
        {"Composition": "Ni50Fe27Ga23", "Microwire": "12/2", "d (µm)": 19.1, "D (µm)": 58.6}
      ]
    }
  }
}
""",
        encoding="utf-8",
    )

    geometry = analysis.load_sample_geometry_from_project(project, "Ni50Fe27Ga23", "12/2")

    assert geometry.name == "Ni50Fe27Ga23 12/2"
    assert geometry.core_diameter_um == pytest.approx(19.1)
    assert geometry.glass_diameter_um == pytest.approx(58.6)


def test_run_analysis_writes_repeatable_tables_report_and_plots(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.tsv"
    sweep = tmp_path / "sweep.tsv"
    out_dir = tmp_path / "analysis"
    preview_dir = tmp_path / "preview"
    _write_baseline(baseline)
    _write_sweep(sweep)

    config = analysis.SusceptibilityAnalysisConfig(
        sweep_files=(sweep,),
        baseline_file=baseline,
        output_dir=out_dir,
        sample=analysis.SampleGeometry(name="synthetic", core_diameter_um=100.0),
        excitation_coil=analysis.ExcitationCoilGeometry(inner_diameter_mm=1.0),
    )
    outputs = analysis.run_analysis(config)
    copied = analysis.copy_preview_images(outputs, preview_dir)

    expected = [
        "empty_coil_baseline_summary.csv",
        "point_medians.csv",
        "apparent_complex_susceptibility_points.csv",
        "apparent_susceptibility_change_by_direction.csv",
        "apparent_susceptibility_condition_ranking.csv",
        "origin_condition_summary.csv",
        "origin_chi_prime_curves.csv",
        "origin_chi_double_prime_curves.csv",
        "recommended_chi_prime_curves.png",
        "recommended_chi_double_prime_curves.png",
        "top_complex_susceptibility_curves.png",
        "all_conditions_delta_chi_curves_grid.png",
        "all_conditions_delta_chi_heatmap.png",
        "all_conditions_snr_heatmap.png",
        "all_conditions_percent_heatmap.png",
        "SUSCEPTIBILITY_REPORT.md",
        "SUSCEPTIBILITY_EQUATION_AUDIT.md",
        "analysis_metadata.json",
    ]
    for name in expected:
        assert (out_dir / name).exists(), name
        assert (out_dir / name).stat().st_size > 0, name

    ranking = pd.read_csv(out_dir / "apparent_susceptibility_condition_ranking.csv")
    assert ranking.loc[0, "frequency_hz"] == pytest.approx(1000.0)
    assert ranking.loc[0, "excitation_mA"] == pytest.approx(5.0)
    assert ranking.loc[0, "mean_abs_delta_chi_prime"] == pytest.approx(2.0)
    assert ranking.loc[0, "mean_chi_prime_martensite_window"] == pytest.approx(1.0)
    assert ranking.loc[0, "mean_chi_prime_austenite_window"] == pytest.approx(3.0)
    assert ranking.loc[0, "mean_chi_prime_austenite_over_martensite"] == pytest.approx(3.0)
    assert ranking.loc[0, "mean_percent_change_austenite_vs_martensite"] == pytest.approx(200.0)
    assert ranking.loc[0, "mean_percent_drop_martensite_to_austenite"] == pytest.approx(-200.0)
    assert ranking.loc[0, "mean_relative_l_change_martensite_window"] == pytest.approx(0.01)
    assert ranking.loc[0, "mean_relative_l_change_austenite_window"] == pytest.approx(0.03)
    assert ranking.loc[0, "mean_relative_l_change_austenite_over_martensite"] == pytest.approx(3.0)
    assert bool(ranking.loc[0, "recommended_quality"])

    report = (out_dir / "SUSCEPTIBILITY_REPORT.md").read_text(encoding="utf-8")
    assert "AC Susceptibility Analysis" in report
    assert "synthetic" in report
    assert "1 kHz" in report
    assert "relative_L_change = (L_wire - L_empty) / L_empty" in report
    assert "The filling factor is kept in the apparent susceptibility" in report
    assert "drop M->A" in report
    assert "origin_chi_prime_curves.csv" in report
    assert "origin_condition_summary.csv" in report
    audit = (out_dir / "SUSCEPTIBILITY_EQUATION_AUDIT.md").read_text(encoding="utf-8")
    assert "chi_prime_app = relative_l_change / filling_factor" in audit
    assert "chi_A_over_chi_M = chi_A / chi_M" in audit
    origin_prime = pd.read_csv(out_dir / "origin_chi_prime_curves.csv")
    assert "relative_l_change" in origin_prime.columns
    assert "wire_dc_resistance_ohm" in origin_prime.columns
    assert copied["chi_prime_plot"].exists()
    assert copied["complex_plot"].exists()
    assert copied["delta_curve_grid"].exists()
    assert copied["delta_heatmap"].exists()


def _write_baseline(path: Path) -> None:
    rows = []
    for repeat, delta in enumerate([-1e-10, 0.0, 1e-10, -1e-10, 0.0, 1e-10]):
        rows.append(
            [
                "2026-05-29T00:00:00Z",
                "1",
                str(repeat),
                "1000",
                "current",
                "0.005",
                "Ls-Rs",
                f"{10e-6 + delta:.12g}",
                "1.0",
                "0.005",
                "0.1",
                "OUT",
                "raw",
            ]
        )
    path.write_text("\n".join("\t".join(row) for row in rows) + "\n", encoding="utf-8")


def _write_sweep(path: Path) -> None:
    rows = []
    omega = 2.0 * math.pi * 1000.0
    for direction in ("up", "down"):
        for current_mA in [15.0, 16.0, 17.0, 18.0, 19.0, 60.0, 61.0, 62.0, 63.0, 64.0]:
            for repeat, delta in enumerate([-1e-10, 0.0, 1e-10]):
                chi_prime = 1.0 if current_mA < 20.0 else 3.0
                chi_loss = 0.2 if direction == "up" else 0.25
                l_wire = 10e-6 * (1.0 + 0.01 * chi_prime) + delta
                r_wire = 1.0 + omega * 10e-6 * 0.01 * chi_loss
                rows.append(
                    [
                        "2026-05-29T00:00:00Z",
                        f"{current_mA:.1f}",
                        "1",
                        "1",
                        "Ls-Rs",
                        "1000",
                        "current",
                        "0.005",
                        f"{current_mA / 1000.0:.6f}",
                        f"{current_mA / 1000.0:.6f}",
                        "1.0",
                        "100.0",
                        "0.01",
                        direction,
                        str(repeat),
                        f"{l_wire:.12g}",
                        f"{r_wire:.12g}",
                        "0.005",
                        "0.1",
                        "OUT",
                        "raw",
                        "sim",
                        "SIM",
                        "OK",
                        "",
                    ]
                )
    path.write_text("\n".join("\t".join(row) for row in rows) + "\n", encoding="utf-8")
