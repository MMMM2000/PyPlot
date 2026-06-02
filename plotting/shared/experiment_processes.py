from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


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


def _gui_python_executable(path: Path) -> Path:
    if sys.platform != "win32":
        return path
    if path.name.lower() != "python.exe":
        return path
    pythonw = path.with_name("pythonw.exe")
    if pythonw.exists():
        return pythonw
    return path


def build_experiment_process_command(
    spec: ExperimentProcessSpec,
    *,
    executable: Path | None = None,
) -> list[str]:
    python_exe = _gui_python_executable(
        Path(sys.executable) if executable is None else executable
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
    env["PYPLOT_EXPERIMENT_PROCESS"] = "1"
    env["PYPLOT_EXPERIMENT_NAME"] = spec.display_name
    env["PYPLOT_EXPERIMENT_RESOURCE_TAG"] = spec.resource_tag
    return env


def launch_experiment_process(
    spec: ExperimentProcessSpec,
    *,
    popen_factory: PopenFactory = subprocess.Popen,
) -> subprocess.Popen[Any]:
    kwargs: dict[str, Any] = {
        "cwd": str(experiment_process_cwd()),
        "env": build_experiment_process_env(spec),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    return popen_factory(build_experiment_process_command(spec), **kwargs)
