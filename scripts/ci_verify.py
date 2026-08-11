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


def _basetemp_for_run(run_id: str, run_root: Path) -> Path:
    if os.name == "nt":
        # xdist adds popen-gwN and pytest adds the test name below basetemp.
        # Keep this disposable path short enough for tests which intentionally
        # reproduce deep Google Drive directory structures.
        return Path("C:/tmp/pyt") / _safe_path_token(run_id)
    return run_root / "pytest-basetemp"


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
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "Number of pytest-xdist workers. Full mode defaults to 4; focused "
            "mode defaults to 0. Use 0 to force one serial pytest process."
        ),
    )
    parser.add_argument(
        "--dist",
        choices=("worksteal", "load", "loadfile", "loadscope"),
        default="worksteal",
        help="pytest-xdist distribution strategy for the parallel lane.",
    )
    return parser


def prepare_environment(args: argparse.Namespace) -> tuple[dict[str, str], Path]:
    artifacts_dir = _repo_path(args.artifacts_dir)
    run_id = args.run_id or _default_run_id()
    run_root = artifacts_dir / run_id

    temp_root = _temp_root_for_run(run_id, run_root)
    basetemp = _basetemp_for_run(run_id, run_root)
    qsettings_root = run_root / "qsettings"
    builder_root = run_root / "microwire-builder-storage"
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
            "UV_CACHE_DIR": str(uv_cache),
            "PIP_CACHE_DIR": str(pip_cache),
            "PYTEST_QSETTINGS_ROOT": str(qsettings_root),
            "PYTEST_GUI_HEADLESS": env.get("PYTEST_GUI_HEADLESS", "1"),
        }
    )
    return env, basetemp


def _resolved_workers(args: argparse.Namespace) -> int:
    workers = args.workers
    if workers is None:
        workers = 4 if args.mode == "full" else 0
    if workers < 0:
        raise ValueError("--workers must be zero or greater")
    return workers


def _selected_pytest_args(args: argparse.Namespace) -> list[str]:
    pytest_args = [arg for arg in args.pytest_args if arg != "--"]
    if not _has_pytest_target(pytest_args):
        default_targets = ["tests"] if args.mode == "full" else DEFAULT_FOCUSED_TARGETS
        pytest_args = [*default_targets, *pytest_args]
    return pytest_args


def _with_marker_filter(pytest_args: list[str], lane_filter: str) -> list[str]:
    result: list[str] = []
    index = 0
    combined = False
    while index < len(pytest_args):
        arg = pytest_args[index]
        if arg in {"-m", "--markexpr"} and index + 1 < len(pytest_args):
            result.extend([arg, f"({pytest_args[index + 1]}) and ({lane_filter})"])
            combined = True
            index += 2
            continue
        if arg.startswith("--markexpr="):
            expression = arg.split("=", 1)[1]
            result.append(f"--markexpr=({expression}) and ({lane_filter})")
            combined = True
            index += 1
            continue
        result.append(arg)
        index += 1
    if not combined:
        result.extend(["-m", lane_filter])
    return result


def _base_pytest_command(
    args: argparse.Namespace,
    basetemp: Path,
    pytest_args: list[str],
) -> list[str]:

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


def build_pytest_command(args: argparse.Namespace, basetemp: Path) -> list[str]:
    """Build the legacy single-process command used when workers are disabled."""

    return _base_pytest_command(args, basetemp, _selected_pytest_args(args))


def build_pytest_commands(
    args: argparse.Namespace,
    basetemp: Path,
) -> list[tuple[str, list[str]]]:
    workers = _resolved_workers(args)
    pytest_args = _selected_pytest_args(args)
    if workers == 0:
        return [("serial", _base_pytest_command(args, basetemp, pytest_args))]

    parallel_args = _with_marker_filter(pytest_args, "not serial")
    parallel = _base_pytest_command(args, basetemp / "parallel", parallel_args)
    parallel[3:3] = ["-n", str(workers), "--dist", args.dist]

    serial_args = _with_marker_filter(pytest_args, "serial")
    serial = _base_pytest_command(args, basetemp / "serial", serial_args)
    serial[3:3] = ["-n", "0"]
    return [("parallel", parallel), ("serial", serial)]


def print_dry_run(
    args: argparse.Namespace,
    env: dict[str, str],
    commands: list[tuple[str, list[str]]],
) -> None:
    print(f"mode={args.mode}")
    print(f"cwd={REPO_ROOT}")
    for lane, command in commands:
        print(f"lane={lane}")
        print(f"command={_command_line(command)}")
    print("environment:")
    for key in SELECTED_ENV_KEYS:
        print(f"  {key}={env.get(key, '')}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, pytest_args = parser.parse_known_args(argv)
    args.pytest_args = [arg for arg in pytest_args if arg != "--"]
    try:
        env, basetemp = prepare_environment(args)
        commands = build_pytest_commands(args, basetemp)
    except ValueError as exc:
        parser.error(str(exc))

    if args.dry_run:
        print_dry_run(args, env, commands)
        return 0

    print(f"PyPlot verification mode: {args.mode}", flush=True)
    print(f"Pytest basetemp: {basetemp}", flush=True)
    print(
        f"Microwire Builder storage: {env['MICROWIRE_BUILDER_STORAGE_ROOT']}",
        flush=True,
    )
    saw_tests = False
    for lane, command in commands:
        print(f"Running {lane} lane: {_command_line(command)}", flush=True)
        returncode = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            check=False,
        ).returncode
        if returncode == 5:
            print(f"No tests selected for {lane} lane.", flush=True)
            continue
        saw_tests = True
        if returncode != 0:
            return returncode
    return 0 if saw_tests else 5


if __name__ == "__main__":
    raise SystemExit(main())
