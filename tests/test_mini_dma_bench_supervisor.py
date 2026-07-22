from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from data_logging.mini_dma_logger import bench_supervisor


def _write_recipe(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "recipe": {
                    "mode": "current_sweep_stress",
                    "setup": {"enabled": True},
                    "current_sweep": {
                        "basis": "stress_mpa",
                        "target_start": 50.0,
                        "target_end": 50.0,
                        "target_step": 50.0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def _write_plan(plan_path: Path, recipe_path: Path, *, summary_path: Path | None = None) -> None:
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": "MINI_DMA_BENCH_ARMED",
                "summary_path": None if summary_path is None else str(summary_path),
                "log_dir": "runs",
                "length_setup": {
                    "starting_length_mm": 52.0,
                    "preload_length_mm": 52.0,
                },
                "bench_lock": {
                    "enabled": True,
                    "timeout_s": 0,
                    "owner": "test-owner",
                    "lock_path": "bench.lock",
                },
                "hardware": {
                    "supply_port": "COM4",
                    "supply_baud": 115200,
                    "current_sweep_channel": 3,
                    "motor_supply_enabled": True,
                    "motor_supply_channel": 2,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )


class _FakePopen:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.pid = 12345
        self._polls = [None, 0]
        _FakePopen.instances.append(self)

    instances: list["_FakePopen"] = []

    def poll(self) -> int | None:
        if len(self._polls) > 1:
            return self._polls.pop(0)
        return self._polls[0]

    def terminate(self) -> None:
        self._polls = [1]

    def kill(self) -> None:
        self._polls = [1]

    def wait(self, timeout: float | None = None) -> int:
        return self.poll() or 0


class _NeverExitsPopen(_FakePopen):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._polls = [None]
        self.terminated = False

    def poll(self) -> int | None:
        return self._polls[0]

    def terminate(self) -> None:
        self.terminated = True
        self._polls = [1]

    def wait(self, timeout: float | None = None) -> int:
        return self._polls[0] or 0


def test_supervised_mini_dma_bench_writes_status_and_safe_off(tmp_path: Path, monkeypatch) -> None:
    recipe_path = tmp_path / "recipe.json"
    plan_path = tmp_path / "bench-plan.json"
    summary_path = tmp_path / "summary.json"
    status_path = tmp_path / "status.json"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    _write_recipe(recipe_path)
    _write_plan(plan_path, recipe_path, summary_path=summary_path)
    summary_path.write_text(json.dumps({"runs": [{"status": "completed"}]}), encoding="utf-8")

    safe_off_calls: list[dict[str, Any]] = []

    def _fake_safe_off(**kwargs: Any) -> dict[str, Any]:
        safe_off_calls.append(dict(kwargs))
        return {
            "status": "ok",
            "channels": list(kwargs["channels"]),
            "states": {"2": {"output_on": False}, "3": {"output_on": False}},
        }

    monkeypatch.setattr(bench_supervisor.time, "sleep", lambda _seconds: None)
    _FakePopen.instances.clear()

    result = bench_supervisor.run_supervised_mini_dma_bench(
        plan_path,
        python_executable="python-test",
        launcher_path="launcher.py",
        status_path=status_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        poll_interval_s=0.1,
        popen_factory=_FakePopen,
        safe_off_fn=_fake_safe_off,
    )

    assert result["state"] == "completed"
    assert result["child_pid"] == 12345
    assert result["child_returncode"] == 0
    assert result["summary"] == {"runs": [{"status": "completed"}]}
    assert result["safe_off"]["status"] == "ok"
    assert safe_off_calls == [{"channels": (3, 2), "port_name": "COM4", "baudrate": 115200}]
    saved = json.loads(status_path.read_text(encoding="utf-8"))
    assert saved["child_pid"] == 12345
    assert saved["safe_off"]["states"]["2"]["output_on"] is False
    assert saved["safe_off"]["states"]["3"]["output_on"] is False
    assert _FakePopen.instances[0].args[0][:3] == [
        "python-test",
        "launcher.py",
        "--mini-dma-bench-plan",
    ]
    assert _FakePopen.instances[0].kwargs["stdin"] is subprocess.DEVNULL


def test_supervisor_terminates_child_after_finished_metadata(tmp_path: Path, monkeypatch) -> None:
    recipe_path = tmp_path / "recipe.json"
    plan_path = tmp_path / "bench-plan.json"
    status_path = tmp_path / "status.json"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    _write_recipe(recipe_path)
    _write_plan(plan_path, recipe_path, summary_path=tmp_path / "summary.json")
    lock_path = tmp_path / "bench.lock"
    lock_path.write_text(json.dumps({"pid": 12345, "owner": "test-owner"}), encoding="utf-8")
    run_dir = tmp_path / "runs" / "run01"
    run_dir.mkdir(parents=True)
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "session_state": "finished",
                "stop": {"reason": "closed_loop_no_progress"},
            }
        ),
        encoding="utf-8",
    )
    old_time = bench_supervisor.time.time()
    os.utime(metadata_path, (old_time, old_time))

    def _fake_safe_off(**kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "channels": list(kwargs["channels"]),
            "states": {"2": {"output_on": False}, "3": {"output_on": False}},
        }

    monkeypatch.setattr(bench_supervisor.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bench_supervisor, "FINISHED_METADATA_CHILD_GRACE_S", 0.0)
    _FakePopen.instances.clear()

    result = bench_supervisor.run_supervised_mini_dma_bench(
        plan_path,
        python_executable="python-test",
        launcher_path="launcher.py",
        status_path=status_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        poll_interval_s=0.1,
        popen_factory=_NeverExitsPopen,
        safe_off_fn=_fake_safe_off,
    )

    assert result["state"] == "failed"
    assert result["child_returncode"] == 1
    assert result["supervisor_recovery"]["reason"] == "finished_metadata_child_still_running"
    assert result["supervisor_recovery"]["stop"]["reason"] == "closed_loop_no_progress"
    assert result["safe_off"]["states"]["2"]["output_on"] is False
    assert result["safe_off"]["states"]["3"]["output_on"] is False
    assert result["lock"] is None
    assert not lock_path.exists()
    assert isinstance(_FakePopen.instances[0], _NeverExitsPopen)
    assert _FakePopen.instances[0].terminated is True


def test_supervisor_treats_finished_recipe_completed_metadata_as_completed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recipe_path = tmp_path / "recipe.json"
    plan_path = tmp_path / "bench-plan.json"
    status_path = tmp_path / "status.json"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    _write_recipe(recipe_path)
    _write_plan(plan_path, recipe_path, summary_path=tmp_path / "summary.json")
    lock_path = tmp_path / "bench.lock"
    lock_path.write_text(json.dumps({"pid": 12345, "owner": "test-owner"}), encoding="utf-8")
    run_dir = tmp_path / "runs" / "run01"
    run_dir.mkdir(parents=True)
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "session_state": "finished",
                "stop": {
                    "reason": "recipe_completed",
                    "category": "normal",
                    "detail": "Recipe completed.",
                },
            }
        ),
        encoding="utf-8",
    )
    old_time = bench_supervisor.time.time()
    os.utime(metadata_path, (old_time, old_time))

    def _fake_safe_off(**kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "channels": list(kwargs["channels"]),
            "states": {"2": {"output_on": False}, "3": {"output_on": False}},
        }

    monkeypatch.setattr(bench_supervisor.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bench_supervisor, "FINISHED_METADATA_CHILD_GRACE_S", 0.0)
    _FakePopen.instances.clear()

    result = bench_supervisor.run_supervised_mini_dma_bench(
        plan_path,
        python_executable="python-test",
        launcher_path="launcher.py",
        status_path=status_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        poll_interval_s=0.1,
        popen_factory=_NeverExitsPopen,
        safe_off_fn=_fake_safe_off,
    )

    assert result["state"] == "completed"
    assert result["child_returncode"] == 0
    assert result["supervisor_recovery"]["reason"] == "finished_metadata_child_still_running"
    assert result["supervisor_recovery"]["stop"]["reason"] == "recipe_completed"
    assert result["lock"] is None
    assert not lock_path.exists()
    assert isinstance(_FakePopen.instances[0], _NeverExitsPopen)
    assert _FakePopen.instances[0].terminated is True


def test_safe_channel_off_retries_transient_serial_access_error(monkeypatch) -> None:
    class _FakeDriver:
        instances: list["_FakeDriver"] = []

        def __init__(self, **_kwargs: Any) -> None:
            self.index = len(self.instances)
            self.closed = False
            self.instances.append(self)

        def connect(self) -> None:
            if self.index == 0:
                raise RuntimeError("could not open port 'COM3': PermissionError(13, 'Access is denied.')")

        def set_output(self, *, channel: int, output_on: bool) -> None:
            self.output = (channel, output_on)

        def output_state(self, *, channel: int) -> bool:
            return channel == 3

        def measure(self, *, channel: int) -> dict[str, float]:
            return {"voltage_V": 12.0 if channel == 3 else 0.0, "current_mA": 100.0 if channel == 3 else 0.0}

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(bench_supervisor.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bench_supervisor, "identify_hmp_with_blank_retry", lambda *_args, **_kwargs: "HMP4040")

    result = bench_supervisor._safe_channel_off(  # noqa: SLF001
        channel=4,
        port_name="COM3",
        baudrate=115200,
        driver_factory=_FakeDriver,
        attempts=2,
        retry_s=0.0,
    )

    assert result["status"] == "ok"
    assert result["attempt"] == 2
    assert result["states"]["4"]["output_on"] is False
    assert len(_FakeDriver.instances) == 2
    assert all(driver.closed for driver in _FakeDriver.instances)


def test_release_child_lock_allows_owner_purpose_match_with_pid_mismatch(tmp_path: Path) -> None:
    lock_path = tmp_path / "bench.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 13920,
                "owner": "codex-mini-dma-live-opt-recovery-2658",
                "purpose": "TMA 12/2 48 mm 80 mA optimization recovery run 5 at 0.8 mA/s",
            }
        ),
        encoding="utf-8",
    )

    bench_supervisor._release_child_lock_if_held(  # noqa: SLF001
        lock_path,
        56228,
        owner="codex-mini-dma-live-opt-recovery-2658",
        purpose="TMA 12/2 48 mm 80 mA optimization recovery run 5 at 0.8 mA/s",
    )

    assert not lock_path.exists()


def test_normalize_windows_path_env_keeps_one_path_key() -> None:
    env = bench_supervisor._normalize_windows_path_env(  # noqa: SLF001
        {"PATH": "A", "Path": "B", "OTHER": "C"}
    )

    path_keys = [key for key in env if key.lower() == "path"]
    assert path_keys == ["Path"]
    assert env["Path"] == "A"
    assert env["OTHER"] == "C"


def test_supervisor_main_prints_ascii_json_for_windows_console(monkeypatch, capsys) -> None:
    def _fake_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "state": "completed",
            "child_returncode": 0,
            "path": "C:/Users/Martin EliÃ¡Å¡/PyPlot",
            "tail": "bad char \ufffd",
        }

    monkeypatch.setattr(bench_supervisor, "run_supervised_mini_dma_bench", _fake_run)

    assert bench_supervisor.main(["bench-plan.json"]) == 0

    out = capsys.readouterr().out
    assert "\\u00e1" in out
    assert "\\ufffd" in out
