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
    assert "--workers" in result.stdout
    assert "--dist" in result.stdout


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
    assert "PYPLOT_TEST_TEMP_ISOLATED=1" in result.stdout
    if os.name == "nt":
        expected_temp = REPO_ROOT / "artifacts" / "t" / "unit"
        expected_basetemp = REPO_ROOT / "artifacts" / "p" / "unit"
        env_lines = {
            key: value
            for line in result.stdout.splitlines()
            if "=" in line
            for key, value in [line.strip().split("=", 1)]
        }
        assert Path(env_lines["TEMP"]).resolve() == expected_temp.resolve()
        assert Path(env_lines["TMP"]).resolve() == expected_temp.resolve()
        assert "--basetemp" in result.stdout
        assert Path(env_lines["pytest_basetemp"]).samefile(expected_basetemp)
    assert "--basetemp" in result.stdout
    assert "-p no:cacheprovider" in result.stdout
    assert (
        "tests/test_launcher.py::test_launcher_detects_pyplot_automation_flags"
        in result.stdout
    )


def test_ci_verify_full_mode_uses_parallel_and_serial_lanes(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--mode",
            "full",
            "--dry-run",
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--run-id",
            "parallel-unit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "lane=parallel" in result.stdout
    assert "-n 4" in result.stdout
    assert "--dist worksteal" in result.stdout
    assert "not serial" in result.stdout
    assert "lane=serial" in result.stdout
    assert "-n 0" in result.stdout


def test_ci_verify_workers_zero_keeps_one_lane(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--mode",
            "full",
            "--workers",
            "0",
            "--dry-run",
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--run-id",
            "serial-unit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.count("lane=") == 1
    assert "lane=serial" in result.stdout
    assert "-n " not in result.stdout


def test_ci_verify_basetemp_is_workspace_scoped_and_run_isolated(tmp_path: Path) -> None:
    from scripts.ci_verify import _basetemp_for_run

    first = _basetemp_for_run('run-first', tmp_path/'first')
    second = _basetemp_for_run('run-second', tmp_path/'second')
    assert first != second
    if os.name == 'nt':
        assert first.resolve().is_relative_to((REPO_ROOT/'artifacts').resolve())
        assert second.resolve().is_relative_to((REPO_ROOT/'artifacts').resolve())
    else:
        assert first == tmp_path/'first'/'pytest-basetemp'
        assert second == tmp_path/'second'/'pytest-basetemp'


def test_ci_verify_supports_deep_fixture_paths_without_machine_settings(tmp_path: Path) -> None:
    from scripts.ci_verify import build_parser, prepare_environment

    args = build_parser().parse_args(['--artifacts-dir', str(tmp_path/'artifacts'),
                                      '--run-id', 'long-path-regression'])
    _env, base = prepare_environment(args)
    payload = base/('a'*80)/('b'*80)/('c'*80)/'payload.bin'
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b'isolated fixture')
    assert payload.read_bytes() == b'isolated fixture'
