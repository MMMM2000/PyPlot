from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FOCUSED_TARGETS = [
    "tests/test_launcher.py::test_launcher_detects_pyplot_automation_flags",
]
DEFAULT_SOFTWARE_TARGETS = [
    "tests/test_ci_verify.py",
    "tests/test_parallel_test_isolation.py",
    "tests/test_fixture_inputs.py",
    "tests/test_filename_parsing.py",
    "tests/test_vsm_temperature_scan.py",
    "tests/test_microwire_project_package.py",
    "tests/test_microwire_safe_codec.py",
    "tests/test_builder_update_integrity.py",
    "tests/test_power_guard.py",
    "tests/test_hmp_bench_guard.py",
    "tests/test_shared_power_supply_broker.py",
    "tests/test_experiment_processes.py",
    "tests/test_config.py",
    "tests/test_gui_smoke_headless.py",
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
    "PYPLOT_TEST_TEMP_ISOLATED",
]


def _default_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{os.getpid()}"


def _repo_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _temp_paths(
    run_id: str, run_root: Path, configured_root: str | None
) -> tuple[Path, Path]:
    if configured_root or os.name == "nt":
        parent = (
            _repo_path(configured_root)
            if configured_root
            else REPO_ROOT / "artifacts" / "t"
        )
        # Keep the resolved path short even when a descriptive run id is used.
        # Always append our own run directory: pytest deletes its basetemp.
        private_root = parent / hashlib.sha256(run_id.encode("ascii")).hexdigest()[:10]
        return private_root / "t", private_root / "p"
    return run_root / "temp", run_root / "pytest-basetemp"


def _preflight_directory(path: Path) -> None:
    """Exercise create/write/read/rename/delete without touching existing files."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".probe-", dir=path) as probe:
            source = Path(probe) / "write"
            source.write_bytes(b"pyplot-verification")
            if source.read_bytes() != b"pyplot-verification":
                raise OSError("temporary-file readback did not match")
            source.rename(Path(probe) / "renamed")
    except OSError as exc:
        raise ValueError(
            f"Verification directory preflight failed for {path}: {exc}. "
            "Choose a writable ASCII --temp-root (or PYPLOT_VERIFY_TEMP_ROOT) "
            "and --artifacts-dir; no fallback directory was used."
        ) from exc


def _windows_short_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(  # type: ignore[attr-defined]
        str(path), buffer, len(buffer)
    )
    if not length or length >= len(buffer):
        return path
    return Path(buffer.value)


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
        choices=("focused", "software", "full"),
        default="focused",
        help=(
            "focused runs supplied pytest targets, or a tiny launcher smoke target "
            "when none are supplied; software defaults to representative local "
            "software checks; full defaults to the full tests directory."
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
        help="Directory for per-run isolated settings, storage and reports.",
    )
    parser.add_argument(
        "--temp-root",
        default=os.environ.get("PYPLOT_VERIFY_TEMP_ROOT"),
        help=(
            "Parent for private temp/basetemp directories. Defaults to artifacts/t "
            "on Windows; PYPLOT_VERIFY_TEMP_ROOT supplies an explicit alternative. "
            "Use a short writable ASCII path for deep fixtures."
        ),
    )
    parser.add_argument(
        "--junit-dir",
        default=None,
        help="Write separate parallel.xml/serial.xml pytest reports in this directory.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Unique run id: 1-48 ASCII letters, digits, hyphens or underscores.",
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
            "Number of pytest-xdist workers. Full mode defaults to 4, software to 2, "
            "and focused to 0. Use 0 to force one serial pytest process."
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
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", run_id):
        raise ValueError(
            "--run-id must be 1-48 ASCII letters, digits, hyphens or underscores, "
            "starting with a letter or digit"
        )
    run_root = artifacts_dir / run_id

    temp_root, basetemp = _temp_paths(run_id, run_root, args.temp_root)
    if not str(temp_root).isascii():
        raise ValueError(
            "--temp-root must resolve to an ASCII path; choose a short writable parent"
        )
    qsettings_root = run_root / "qsettings"
    builder_root = run_root / "microwire-builder-storage"
    mpl_cache = run_root / "matplotlib"
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
        _preflight_directory(path)
    if args.junit_dir:
        _preflight_directory(_repo_path(args.junit_dir))

    # Preserve the same directories while presenting their 8.3 aliases to
    # Windows subprocesses. This saves enough path budget for deeply nested
    # Builder fixtures without moving artifacts outside the workspace.
    if os.name == "nt":
        temp_root = _windows_short_path(temp_root)
        basetemp = _windows_short_path(basetemp)
        # Deep synthetic Drive/payload paths can exceed MAX_PATH even with
        # 8.3 aliases. Opt this disposable tree into Win32 extended paths
        # without requiring the machine-wide LongPathsEnabled registry flag.
        raw_base = str(basetemp)
        if not raw_base.startswith('\\\\?\\'):
            basetemp = Path('\\\\?\\UNC\\' + raw_base[2:] if raw_base.startswith('\\\\')
                            else '\\\\?\\' + raw_base)
        qsettings_root = _windows_short_path(qsettings_root)
        builder_root = _windows_short_path(builder_root)
        mpl_cache = _windows_short_path(mpl_cache)
        uv_cache = _windows_short_path(uv_cache)
        pip_cache = _windows_short_path(pip_cache)

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
            "PYPLOT_TEST_TEMP_ISOLATED": "1",
        }
    )
    return env, basetemp


def _resolved_workers(args: argparse.Namespace) -> int:
    workers = args.workers
    if workers is None:
        workers = {"full": 4, "software": 2, "focused": 0}[args.mode]
    if workers < 0:
        raise ValueError("--workers must be zero or greater")
    return workers


def _selected_pytest_args(args: argparse.Namespace) -> list[str]:
    pytest_args = [arg for arg in args.pytest_args if arg != "--"]
    if not _has_pytest_target(pytest_args):
        default_targets = {
            "full": ["tests"],
            "software": DEFAULT_SOFTWARE_TARGETS,
            "focused": DEFAULT_FOCUSED_TARGETS,
        }[args.mode]
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
    command.append("-ra")
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
        basetemp_index = command.index("--basetemp") + 1
        print(f"pytest_basetemp={command[basetemp_index]}")
    print("environment:")
    for key in SELECTED_ENV_KEYS:
        print(f"  {key}={env.get(key, '')}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, pytest_args = parser.parse_known_args(argv)
    args.pytest_args = [arg for arg in pytest_args if arg != "--"]
    try:
        _resolved_workers(args)
        if any(
            option.startswith("--") and "--basetemp".startswith(option)
            for arg in args.pytest_args
            for option in [arg.split("=", 1)[0]]
        ):
            raise ValueError(
                "Use --temp-root instead of overriding pytest --basetemp; "
                "the runner owns disposable lane directories"
            )
        env, basetemp = prepare_environment(args)
        commands = build_pytest_commands(args, basetemp)
        if args.junit_dir:
            for lane, command in commands:
                report = _repo_path(args.junit_dir) / f"{lane}.xml"
                command.extend(["--junitxml", str(report)])
    except ValueError as exc:
        parser.error(str(exc))

    resolved_length = len(str(basetemp.resolve()))
    if os.name == "nt" and resolved_length > 60:
        print(
            f"Path preflight warning: resolved basetemp is {resolved_length} characters. "
            "Extended paths protect deep fixtures; libraries that drop that prefix may "
            "still need a shorter writable --temp-root (or PYPLOT_VERIFY_TEMP_ROOT). "
            "Short aliases can be expanded by pytest; no fallback was used.",
            file=sys.stderr,
            flush=True,
        )

    if args.dry_run:
        print_dry_run(args, env, commands)
        return 0

    print(f"PyPlot verification mode: {args.mode}", flush=True)
    print(f"Pytest basetemp: {basetemp}", flush=True)
    print(f"Resolved basetemp length: {resolved_length}", flush=True)
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
