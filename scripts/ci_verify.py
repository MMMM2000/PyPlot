from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FOCUSED_TARGETS = [
    "tests/test_launcher.py::test_launcher_detects_pyplot_automation_flags",
]
SELECTED_ENV_KEYS = [
    "QT_QPA_PLATFORM",
    "MPLBACKEND",
    "TEMP",
    "TMP",
    "TMPDIR",
    "MPLCONFIGDIR",
    "MICROWIRE_BUILDER_STORAGE_ROOT",
    "MICROWIRE_BUILDER_SETTINGS_FILE",
    "UV_CACHE_DIR",
    "PIP_CACHE_DIR",
    "PYTEST_QSETTINGS_ROOT",
]


def _default_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{os.getpid()}"


def _repo_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _safe_path_token(raw: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw)
    return token[:48] or "run"


def _temp_root_for_run(run_id: str, run_root: Path) -> Path:
    if os.name == "nt":
        # tests/conftest.py deliberately falls back to C:/tmp when the temp path
        # is long. Use a short, ignored, workspace-local per-run root up front
        # so Excel/xlsxwriter tests do not collide in a shared temp directory.
        return REPO_ROOT / "artifacts" / "t" / _safe_path_token(run_id)
    return run_root / "temp"


def _has_pytest_target(args: Iterable[str]) -> bool:
    skip_next = False
    value_options = {
        "-k",
        "-m",
        "--maxfail",
        "--tb",
        "--capture",
        "--durations",
        "--junitxml",
        "--rootdir",
        "--confcutdir",
        "--basetemp",
    }
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in value_options:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        normalized = arg.replace("\\", "/")
        if normalized.startswith("tests") or normalized.endswith(".py") or "::" in arg:
            return True
    return False


def _command_line(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return " ".join(subprocess.list2cmdline([part]) for part in command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run PyPlot pytest checks with Windows-friendly temp/cache isolation."
        ),
        epilog=(
            "Any unrecognized arguments are passed through to pytest, for example "
            "scripts/ci_verify.py --mode focused tests/test_launcher.py -q."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("focused", "full"),
        default="focused",
        help=(
            "focused runs supplied pytest targets, or a tiny launcher smoke target "
            "when none are supplied; full defaults to the full tests directory."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command and isolated environment without running pytest.",
    )
    parser.add_argument(
        "--artifacts-dir",
        default="artifacts/test-runs",
        help="Workspace-scoped directory for per-run temp roots and pytest basetemp.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id for deterministic artifact paths.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to invoke pytest. Defaults to this interpreter.",
    )
    parser.add_argument(
        "--keep-pytest-cache",
        action="store_true",
        help="Do not add '-p no:cacheprovider'. Disabled by default for Windows runs.",
    )
    return parser


def prepare_environment(args: argparse.Namespace) -> tuple[dict[str, str], Path]:
    artifacts_dir = _repo_path(args.artifacts_dir)
    run_id = args.run_id or _default_run_id()
    run_root = artifacts_dir / run_id

    temp_root = _temp_root_for_run(run_id, run_root)
    basetemp = run_root / "pytest-basetemp"
    qsettings_root = run_root / "qsettings"
    builder_root = run_root / "microwire-builder-storage"
    builder_settings = run_root / "builder-settings.ini"
    mpl_cache = REPO_ROOT / "artifacts" / "mpl-cache"
    uv_cache = REPO_ROOT / "artifacts" / "uv-cache"
    pip_cache = REPO_ROOT / "artifacts" / "pip-cache"

    for path in (
        temp_root,
        basetemp,
        qsettings_root,
        builder_root,
        mpl_cache,
        uv_cache,
        pip_cache,
    ):
        path.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "MPLBACKEND": "Agg",
            "TEMP": str(temp_root),
            "TMP": str(temp_root),
            "TMPDIR": str(temp_root),
            "MPLCONFIGDIR": str(mpl_cache),
            "MICROWIRE_BUILDER_STORAGE_ROOT": str(builder_root),
            "MICROWIRE_BUILDER_SETTINGS_FILE": str(builder_settings),
            "UV_CACHE_DIR": str(uv_cache),
            "PIP_CACHE_DIR": str(pip_cache),
            "PYTEST_QSETTINGS_ROOT": str(qsettings_root),
            "PYTEST_GUI_HEADLESS": env.get("PYTEST_GUI_HEADLESS", "1"),
        }
    )
    return env, basetemp


def build_pytest_command(args: argparse.Namespace, basetemp: Path) -> list[str]:
    pytest_args = [arg for arg in args.pytest_args if arg != "--"]
    if not _has_pytest_target(pytest_args):
        default_targets = ["tests"] if args.mode == "full" else DEFAULT_FOCUSED_TARGETS
        pytest_args = [*default_targets, *pytest_args]

    command = [
        args.python,
        "-m",
        "pytest",
        "--basetemp",
        str(basetemp),
    ]
    if not args.keep_pytest_cache:
        command.extend(["-p", "no:cacheprovider"])
    command.extend(pytest_args)
    return command


def print_dry_run(args: argparse.Namespace, env: dict[str, str], command: list[str]) -> None:
    print(f"mode={args.mode}")
    print(f"cwd={REPO_ROOT}")
    print(f"command={_command_line(command)}")
    print("environment:")
    for key in SELECTED_ENV_KEYS:
        print(f"  {key}={env.get(key, '')}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, pytest_args = parser.parse_known_args(argv)
    args.pytest_args = [arg for arg in pytest_args if arg != "--"]
    env, basetemp = prepare_environment(args)
    command = build_pytest_command(args, basetemp)

    if args.dry_run:
        print_dry_run(args, env, command)
        return 0

    print(f"PyPlot verification mode: {args.mode}", flush=True)
    print(f"Pytest basetemp: {basetemp}", flush=True)
    print(
        f"Microwire Builder storage: {env['MICROWIRE_BUILDER_STORAGE_ROOT']}",
        flush=True,
    )
    print(
        f"Microwire Builder settings: {env['MICROWIRE_BUILDER_SETTINGS_FILE']}",
        flush=True,
    )
    return subprocess.run(command, cwd=REPO_ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
