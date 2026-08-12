from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class ExperimentProcessSpec:
    display_name: str
    module: str
    resource_tag: str


class PopenFactory(Protocol):
    def __call__(self, args: Sequence[str], **kwargs: Any) -> subprocess.Popen[Any]:
        ...


def experiment_process_cwd() -> Path:
    return Path(__file__).resolve().parents[2]


def experiment_process_log_dir(
    *, parent_env: Mapping[str, str] | None = None
) -> Path:
    env = os.environ if parent_env is None else parent_env
    configured = str(env.get("PYPLOT_EXPERIMENT_LOG_DIR", "")).strip()
    if configured:
        return Path(configured)
    return experiment_process_cwd() / "logs" / "experiment_processes"


def experiment_process_log_path(
    spec: ExperimentProcessSpec,
    *,
    parent_env: Mapping[str, str] | None = None,
    timestamp: datetime | None = None,
    pid: int | None = None,
) -> Path:
    stamp = (timestamp or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    tag = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in spec.resource_tag.strip().lower()
    ).strip("_") or "experiment"
    process_id = os.getpid() if pid is None else int(pid)
    return experiment_process_log_dir(parent_env=parent_env) / f"{stamp}-{process_id}-{tag}.log"


def gui_python_executable(path: Path) -> Path:
    if sys.platform != "win32" or path.name.lower() != "python.exe":
        return path
    pythonw = path.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else path


def control_python_executable(path: Path) -> Path:
    if sys.platform != "win32":
        return path
    if path.name.lower() != "pythonw.exe":
        return path
    # Experiment apps can spawn hardware-control children.  Keep the console
    # interpreter (the launcher already supplies CREATE_NO_WINDOW) so Windows
    # multiprocessing has a real stderr stream and a stable child executable.
    python = path.with_name("python.exe")
    if python.exists():
        return python
    return path


def build_experiment_process_command(
    spec: ExperimentProcessSpec,
    *,
    executable: Path | None = None,
) -> list[str]:
    requested_executable = Path(sys.executable) if executable is None else executable
    # Both TMA and Current Annealing start authoritative multiprocessing
    # children.  A pythonw parent can fail during Windows spawn reconstruction
    # before the child target has a chance to publish its fault detail.  Keep
    # these controller-owning experiment parents on the console interpreter;
    # CREATE_NO_WINDOW below still prevents a visible console window.
    controller_process_tags = {"tma", "current_annealing"}
    python_exe = (
        control_python_executable(requested_executable)
        if spec.resource_tag in controller_process_tags
        else gui_python_executable(requested_executable)
    )
    if getattr(sys, "frozen", False):
        return [str(python_exe), "--experiment-process", spec.resource_tag]
    return [str(python_exe), "-m", spec.module]


def build_experiment_process_env(
    spec: ExperimentProcessSpec,
    *,
    parent_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if parent_env is None else parent_env)
    if env.get("QT_QPA_PLATFORM", "").lower() in {"offscreen", "minimal", "headless"}:
        env.pop("QT_QPA_PLATFORM", None)
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        env.pop(key, None)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYPLOT_EXPERIMENT_PROCESS"] = "1"
    env["PYPLOT_EXPERIMENT_NAME"] = spec.display_name
    env["PYPLOT_EXPERIMENT_RESOURCE_TAG"] = spec.resource_tag
    return env


def _open_experiment_process_log(
    spec: ExperimentProcessSpec,
    env: Mapping[str, str],
) -> tuple[BinaryIO | None, Path | None]:
    log_path = experiment_process_log_path(spec, parent_env=env)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("ab", buffering=0)
    except OSError:
        return None, None
    header = (
        f"\n=== {datetime.now(timezone.utc).isoformat()} "
        f"launching {spec.display_name} ({spec.resource_tag}) ===\n"
    )
    handle.write(header.encode("utf-8", errors="replace"))
    return handle, log_path


def hidden_process_creationflags() -> int:
    if sys.platform != "win32":
        return 0
    return (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )


def launch_experiment_process(
    spec: ExperimentProcessSpec,
    *,
    popen_factory: PopenFactory = subprocess.Popen,
) -> subprocess.Popen[Any]:
    env = build_experiment_process_env(spec)
    log_handle, log_path = _open_experiment_process_log(spec, env)
    if log_path is not None:
        env["PYPLOT_EXPERIMENT_LOG_PATH"] = str(log_path)
    kwargs: dict[str, Any] = {
        "cwd": str(experiment_process_cwd()),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL if log_handle is None else log_handle,
        "stderr": subprocess.DEVNULL if log_handle is None else subprocess.STDOUT,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = hidden_process_creationflags()
    else:
        kwargs["start_new_session"] = True
    try:
        return popen_factory(build_experiment_process_command(spec), **kwargs)
    finally:
        if log_handle is not None:
            log_handle.close()
