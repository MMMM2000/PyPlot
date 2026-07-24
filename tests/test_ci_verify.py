from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import os


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "ci_verify.py"


def test_ci_verify_help_lists_modes() -> None:
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--mode" in result.stdout
    assert "focused" in result.stdout
    assert "full" in result.stdout


def test_ci_verify_dry_run_uses_isolated_windows_defaults(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--mode",
            "focused",
            "--dry-run",
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--run-id",
            "unit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "QT_QPA_PLATFORM=offscreen" in result.stdout
    assert "MPLBACKEND=Agg" in result.stdout
    assert "MICROWIRE_BUILDER_STORAGE_ROOT=" in result.stdout
    assert "UV_CACHE_DIR=" in result.stdout
    if os.name == "nt":
        expected_temp = REPO_ROOT / "artifacts" / "t" / "unit"
        assert f"TEMP={expected_temp}" in result.stdout
        assert f"TMP={expected_temp}" in result.stdout
    assert "--basetemp" in result.stdout
    assert "-p no:cacheprovider" in result.stdout
    assert (
        "tests/test_launcher.py::test_launcher_detects_pyplot_automation_flags"
        in result.stdout
    )
