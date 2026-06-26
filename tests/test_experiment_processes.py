from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from plotting.shared.experiment_processes import (
    ExperimentProcessSpec,
    build_experiment_process_env,
    build_experiment_process_command,
    experiment_process_log_path,
    launch_experiment_process,
)


def test_experiment_process_command_uses_module_entrypoint() -> None:
    spec = ExperimentProcessSpec(
        display_name="AC Susceptibility Logger",
        module="data_logging.ac_susceptibility_logger.ac_susceptibility_logger",
        resource_tag="ac_susceptibility",
    )

    command = build_experiment_process_command(spec, executable=Path("python.exe"))

    assert command == [
        "python.exe",
        "-m",
        "data_logging.ac_susceptibility_logger.ac_susceptibility_logger",
    ]


def test_experiment_process_command_uses_launcher_entrypoint_when_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = ExperimentProcessSpec(
        display_name="Current Annealing Logger",
        module="data_logging.current_annealing_logger.current_annealing_logger",
        resource_tag="current_annealing",
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    command = build_experiment_process_command(
        spec, executable=Path("C:/apps/PyPlot/launcher.exe")
    )

    assert command == [
        str(Path("C:/apps/PyPlot/launcher.exe")),
        "--experiment-process",
        "current_annealing",
    ]


def test_experiment_process_env_tags_run_and_removes_headless_qt() -> None:
    spec = ExperimentProcessSpec(
        display_name="Current Annealing Logger",
        module="data_logging.current_annealing_logger.current_annealing_logger",
        resource_tag="current_annealing",
    )
    parent_env = {
        "QT_QPA_PLATFORM": "offscreen",
        "QT_PLUGIN_PATH": "C:/bad/qt/plugins",
        "PATH": "C:/tools",
    }

    child_env = build_experiment_process_env(spec, parent_env=parent_env)

    assert child_env["PYPLOT_EXPERIMENT_PROCESS"] == "1"
    assert child_env["PYPLOT_EXPERIMENT_NAME"] == "Current Annealing Logger"
    assert child_env["PYPLOT_EXPERIMENT_RESOURCE_TAG"] == "current_annealing"
    assert child_env["PYTHONUNBUFFERED"] == "1"
    assert "QT_QPA_PLATFORM" not in child_env
    assert "QT_PLUGIN_PATH" not in child_env
    assert child_env["PATH"] == "C:/tools"


def test_launch_experiment_process_starts_child_from_repo_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = ExperimentProcessSpec(
        display_name="TMA Logger",
        module="data_logging.mini_dma_logger.mini_dma_logger",
        resource_tag="mini_dma",
    )
    calls: list[dict[str, object]] = []

    class _FakePopen:
        def __init__(self, args: list[str], **kwargs: object) -> None:
            calls.append({"args": args, **kwargs})
            self.pid = 1234

    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setenv("PYPLOT_EXPERIMENT_LOG_DIR", str(tmp_path / "experiment-logs"))
    monkeypatch.chdir(tmp_path)

    process = launch_experiment_process(spec, popen_factory=_FakePopen)

    assert process.pid == 1234
    assert calls
    assert calls[0]["args"] == [
        str(tmp_path / "python.exe"),
        "-m",
        "data_logging.mini_dma_logger.mini_dma_logger",
    ]
    assert calls[0]["cwd"] == str(Path(__file__).resolve().parents[1])
    assert calls[0]["stdin"] is subprocess.DEVNULL
    stdout = calls[0]["stdout"]
    assert stdout is not subprocess.DEVNULL
    assert calls[0]["stderr"] is subprocess.STDOUT
    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert env["PYTHONFAULTHANDLER"] == "1"
    log_path = Path(env["PYPLOT_EXPERIMENT_LOG_PATH"])
    assert log_path.parent == tmp_path / "experiment-logs"
    assert log_path.exists()
    assert "launching TMA Logger" in log_path.read_text(encoding="utf-8")


def test_experiment_process_log_path_uses_ignored_logs_dir() -> None:
    spec = ExperimentProcessSpec(
        display_name="TMA Logger",
        module="data_logging.mini_dma_logger.mini_dma_logger",
        resource_tag="mini_dma",
    )

    path = experiment_process_log_path(spec, pid=123)

    assert path.parent == Path(__file__).resolve().parents[1] / "logs" / "experiment_processes"
    assert path.name.endswith("-123-mini_dma.log")
