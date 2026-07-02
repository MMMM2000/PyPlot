from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from data_logging.mini_dma_logger.campaign import load_campaign, validate_campaign
from scripts.mini_dma_report import generate_report


def _write_manifest(path: Path, root: Path, run_dir: Path | None = None) -> None:
    payload = {
        "schema_version": 1,
        "kind": "mini_dma_optimization_campaign",
        "campaign": {
            "id": "test-campaign",
            "title": "Test campaign",
            "root": str(root),
        },
        "objective": {
            "primary": "minimize stress error and recovery time while balancing measurement time",
            "optimize_for_generalization": True,
            "avoid_sample_specific_magic_values": True,
        },
        "sample": {
            "composition": "Ni50Fe27Ga23",
            "microwire": "12/2",
            "length_mm": 52.0,
            "diameter_mm": 0.0191,
        },
        "control_source": {
            "required_base_ref": "origin/main",
            "required_branch_prefix": "codex/",
            "require_clean_git": True,
            "require_up_to_date_with_base": True,
            "approved_control_logic_version": "latest-known-good",
        },
        "hardware": {
            "current_channel": "CH4",
            "current_voltage_limit_v": 32.05,
        },
        "safety": {
            "max_stress_mpa": 300.0,
            "max_correction_travel_fraction": 0.15,
        },
        "run_plan": {
            "stages": [
                {
                    "id": "baseline_1p0",
                    "recipe_path": "plans/recipes/baseline.recipe.json",
                    "current_ramp_rate_mA_s": 1.0,
                    "repeat": 1,
                }
            ]
        },
        "reporting": {
            "report_path": "reports/mini_dma_optimization_report.pdf",
            "image_dir": "reports/images",
            "summary_path": "analysis/summary.json",
            "highlight_current_hold": True,
        },
    }
    if run_dir is not None:
        payload["runs"] = [{"path": str(run_dir)}]
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_campaign_check_derives_geometry_travel_limit(tmp_path: Path) -> None:
    manifest = tmp_path / "campaign.json"
    _write_manifest(manifest, tmp_path)

    result = validate_campaign(manifest, skip_git=True)

    assert result.ok
    assert result.derived["max_correction_travel_mm"] == 7.8
    assert result.derived["report_path"].endswith("reports\\mini_dma_optimization_report.pdf") or result.derived[
        "report_path"
    ].endswith("reports/mini_dma_optimization_report.pdf")


def test_campaign_check_rejects_missing_control_source(tmp_path: Path) -> None:
    manifest = tmp_path / "campaign.json"
    _write_manifest(manifest, tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    del payload["control_source"]["approved_control_logic_version"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_campaign(manifest, skip_git=True)

    assert not result.ok
    assert "missing required field: control_source.approved_control_logic_version" in result.errors


def test_yaml_campaign_template_parses() -> None:
    template = Path("docs/automation_templates/mini_dma_campaign.yaml")

    payload = load_campaign(template)

    assert payload["kind"] == "mini_dma_optimization_campaign"
    assert payload["sample"]["length_mm"] == 52.0
    assert payload["run_plan"]["stages"][0]["id"] == "baseline_0p8"


def test_campaign_manifest_accepts_utf8_bom(tmp_path: Path) -> None:
    manifest = tmp_path / "campaign.json"
    root = tmp_path / "campaign-root"
    root.mkdir()
    _write_manifest(manifest, root)
    manifest.write_bytes(b"\xef\xbb\xbf" + manifest.read_bytes())

    result = validate_campaign(manifest, skip_git=True)

    assert result.ok


def test_mini_dma_report_generates_standard_outputs(tmp_path: Path) -> None:
    run_dir = tmp_path / "raw_runs" / "run01"
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "stop": {"reason": "completed", "detail": "done"},
                "source_control": {"branch": "main", "commit": "abc123"},
            }
        ),
        encoding="utf-8",
    )
    with (run_dir / "measurement.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "elapsed_s",
                "stress_mpa",
                "strain_pct",
                "current_measured_mA",
                "current_set_mA",
                "automation_phase",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "elapsed_s": "0",
                "stress_mpa": "50",
                "strain_pct": "0",
                "current_measured_mA": "1",
                "current_set_mA": "1",
                "automation_phase": "current",
            }
        )
        writer.writerow(
            {
                "elapsed_s": "1",
                "stress_mpa": "55",
                "strain_pct": "0.5",
                "current_measured_mA": "10",
                "current_set_mA": "10",
                "automation_phase": "current_hold",
            }
        )
        writer.writerow(
            {
                "elapsed_s": "2",
                "stress_mpa": "51",
                "strain_pct": "0.8",
                "current_measured_mA": "20",
                "current_set_mA": "20",
                "automation_phase": "current",
            }
        )
    manifest = tmp_path / "campaign.json"
    _write_manifest(manifest, tmp_path, run_dir)

    summary = generate_report(manifest)

    assert Path(summary["report_path"]).exists()
    assert (tmp_path / "analysis" / "summary.json").exists()
    assert len(summary["runs"]) == 1
    assert Path(summary["runs"][0]["image_path"]).exists()
    assert summary["runs"][0]["hold_spans"] == 1


def test_campaign_check_cli_json(tmp_path: Path) -> None:
    manifest = tmp_path / "campaign.json"
    _write_manifest(manifest, tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/mini_dma_campaign_check.py",
            str(manifest),
            "--skip-git",
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["campaign_id"] == "test-campaign"
