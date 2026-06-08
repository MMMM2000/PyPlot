from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from data_logging.mini_dma_logger.campaign import load_campaign, validate_campaign


def _write_manifest(path: Path, root: Path) -> None:
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

