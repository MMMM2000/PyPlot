from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_logging.mini_dma_logger import bench_automation


def _write_recipe(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "recipe": {
                    "mode": "current_sweep_strain",
                    "setup": {
                        "preload_stress_mpa": 20.0,
                        "preload_duration_s": 5.0,
                    },
                    "current_sweep": {
                        "basis": "strain_pct",
                        "target_start": 0.0,
                        "target_end": 1.0,
                        "target_step": 1.0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def _write_minimal_trace_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "wire_diameter_mm": 0.0125,
                "stop": {"detail": "synthetic complete"},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "control_trace.csv").write_text(
        "\n".join(
            [
                "elapsed_s,automation_phase,automation_basis,automation_target_value,decision,result,current_value,error_value,tolerance,sensitivity_per_mm,motor_step_mm",
                "1.0,current,stress_mpa,30.0,accept,reached,0.0,30.0,45.0,45000.0,0.001",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_mini_dma_bench_plan_rejects_unarmed_execution(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(bench_automation.MiniDmaBenchAutomationError, match="armed"):
        bench_automation.load_mini_dma_bench_plan(plan_path)


def test_mini_dma_bench_plan_dry_run_validates_recipe_paths(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    summary_path = tmp_path / "summary.json"
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "summary_path": str(summary_path),
                "runs": [{"name": "trial", "recipe_path": str(recipe_path), "repeat": 2}],
            }
        ),
        encoding="utf-8",
    )

    summary = bench_automation.run_mini_dma_bench_plan(plan_path)

    assert summary["mode"] == "dry_run"
    assert summary["run_count"] == 2
    assert summary["runs"][0]["status"] == "validated"
    assert summary_path.exists()


def test_mini_dma_bench_plan_requires_automated_lengths_for_execution(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": bench_automation.MINI_DMA_BENCH_CONFIRMATION,
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(bench_automation.MiniDmaBenchAutomationError, match="length_setup"):
        bench_automation.load_mini_dma_bench_plan(plan_path)


def test_mini_dma_bench_plan_executes_runs_with_automated_setup_lengths(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": bench_automation.MINI_DMA_BENCH_CONFIRMATION,
                "log_dir": str(tmp_path / "logs"),
                "default_max_run_duration_s": 1,
                "length_setup": {
                    "starting_length_mm": 20.0,
                    "preload_length_mm": 20.4,
                },
                "guardrails": {
                    "allow_mechanical_slack_takeup": True,
                    "mechanical_slack_max_seek_mm": 10.0,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )
    events: list[tuple[str, object]] = []

    class _FakeApp:
        def processEvents(self) -> None:
            events.append(("process", None))

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            events.append(("init", log_dir))
            self._automation_active = False
            self._session_active = False
            self._session_json_path = tmp_path / "logs" / "run01" / "metadata.json"

        def set_length_setup_automation_values(
            self,
            *,
            starting_length_mm: float | None,
            preload_length_mm: float | None,
        ) -> None:
            events.append(("lengths", (starting_length_mm, preload_length_mm)))

        def _load_recipe_from_path(self, path: Path) -> None:
            events.append(("recipe", path.name))

        def set_bench_mechanical_slack_takeup(self, *, allow: bool, max_seek_mm: float | None) -> None:
            events.append(("slack_takeup", (allow, max_seek_mm)))

        def _start_auto_ramp(self) -> None:
            events.append(("start", None))

        def close(self) -> None:
            events.append(("close", None))

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=lambda _seconds: None,
    )

    assert summary["mode"] == "execute"
    assert summary["runs"][0]["status"] == "completed"
    assert ("lengths", (20.0, 20.4)) in events
    assert ("recipe", "iso-strain.recipe.json") in events
    assert ("slack_takeup", (True, 10.0)) in events
    assert ("start", None) in events

def test_mini_dma_bench_plan_records_startup_log_when_not_started(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    summary_path = tmp_path / "summary.json"
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": bench_automation.MINI_DMA_BENCH_CONFIRMATION,
                "summary_path": str(summary_path),
                "default_max_run_duration_s": 1,
                "bench_lock": {"enabled": False},
                "length_setup": {
                    "starting_length_mm": 20.0,
                    "preload_length_mm": 20.4,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )

    class _FakeApp:
        def processEvents(self) -> None:
            pass

    class _FakeLog:
        def toPlainText(self) -> str:
            return "Scale port is unavailable; recipe did not start."

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._automation_active = False
            self._session_active = False
            self._session_json_path = None
            self.log_output = _FakeLog()

        def set_length_setup_automation_values(
            self,
            *,
            starting_length_mm: float | None,
            preload_length_mm: float | None,
        ) -> None:
            return

        def _load_recipe_from_path(self, path: Path) -> None:
            return

        def _start_auto_ramp(self) -> None:
            return

        def close(self) -> None:
            return

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=lambda _seconds: None,
    )

    run = summary["runs"][0]
    assert run["status"] == "not_started"
    assert "Scale port is unavailable" in run["startup_log_tail"]
    written = json.loads(summary_path.read_text(encoding="utf-8"))
    assert "Scale port is unavailable" in written["runs"][0]["startup_log_tail"]

def test_mini_dma_bench_plan_writes_control_trace_replay_after_run(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    run_dir = tmp_path / "logs" / "run01"
    _write_minimal_trace_run(run_dir)
    summary_path = tmp_path / "summary.json"
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": bench_automation.MINI_DMA_BENCH_CONFIRMATION,
                "log_dir": str(tmp_path / "logs"),
                "summary_path": str(summary_path),
                "default_max_run_duration_s": 1,
                "length_setup": {
                    "starting_length_mm": 20.0,
                    "preload_length_mm": 20.4,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )

    class _FakeApp:
        def processEvents(self) -> None:
            pass

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._automation_active = False
            self._session_active = False
            self._session_json_path = run_dir / "metadata.json"

        def set_length_setup_automation_values(
            self,
            *,
            starting_length_mm: float | None,
            preload_length_mm: float | None,
        ) -> None:
            pass

        def _load_recipe_from_path(self, path: Path) -> None:
            pass

        def _start_auto_ramp(self) -> None:
            pass

        def close(self) -> None:
            pass

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=lambda _seconds: None,
    )

    replay = summary["runs"][0]["control_trace_replay"]
    assert replay["status"] == "written"
    assert replay["summary"]["step_floor_only_accept_count"] == 1
    assert (run_dir / "diagnostics" / "control_trace_replay" / "control_trace_replay.csv").exists()
    written_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert written_summary["runs"][0]["control_trace_replay"]["status"] == "written"


def test_mini_dma_bench_plan_uses_next_run_for_existing_output(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": bench_automation.MINI_DMA_BENCH_CONFIRMATION,
                "log_dir": str(tmp_path / "logs"),
                "default_max_run_duration_s": 1,
                "length_setup": {
                    "starting_length_mm": 20.0,
                    "preload_length_mm": 20.4,
                },
                "guardrails": {
                    "allow_mechanical_slack_takeup": True,
                    "mechanical_slack_max_seek_mm": 10.0,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )
    events: list[tuple[str, object]] = []

    class _FakeApp:
        def processEvents(self) -> None:
            events.append(("process", None))

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._automation_active = False
            self._session_active = False
            self._session_json_path = tmp_path / "logs" / "run02" / "metadata.json"

        def set_length_setup_automation_values(
            self,
            *,
            starting_length_mm: float | None,
            preload_length_mm: float | None,
        ) -> None:
            pass

        def _load_recipe_from_path(self, path: Path) -> None:
            pass

        def _ask_existing_output_action(self, paths: object) -> str:
            return "cancel"

        def _start_auto_ramp(self) -> None:
            events.append(("collision_action", self._ask_existing_output_action(())))

        def close(self) -> None:
            pass

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=lambda _seconds: None,
    )

    assert summary["runs"][0]["status"] == "completed"
    assert ("collision_action", "next") in events


def test_mini_dma_bench_plan_timeout_records_automation_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": bench_automation.MINI_DMA_BENCH_CONFIRMATION,
                "log_dir": str(tmp_path / "logs"),
                "default_max_run_duration_s": 0.2,
                "length_setup": {
                    "starting_length_mm": 20.0,
                    "preload_length_mm": 20.4,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )
    clock = {"now": 100.0}
    events: list[tuple[str, object]] = []

    monkeypatch.setattr(bench_automation.time, "monotonic", lambda: clock["now"])

    class _FakeApp:
        def processEvents(self) -> None:
            events.append(("process", None))

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._automation_active = True
            self._session_active = True
            self._session_json_path = tmp_path / "logs" / "run01" / "metadata.json"

        def set_length_setup_automation_values(
            self,
            *,
            starting_length_mm: float | None,
            preload_length_mm: float | None,
        ) -> None:
            return

        def _load_recipe_from_path(self, path: Path) -> None:
            return

        def _start_auto_ramp(self) -> None:
            return

        def _stop_auto_ramp(self, **kwargs: object) -> None:
            events.append(("stop_auto", kwargs))
            self._automation_active = False

        def _stop_session(self, **kwargs: object) -> None:
            events.append(("stop_session", kwargs))
            self._session_active = False

        def close(self) -> None:
            events.append(("close", None))

    def _sleep(seconds: float) -> None:
        clock["now"] += max(0.05, seconds)

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=_sleep,
    )

    assert summary["runs"][0]["status"] == "timeout"
    stop_auto = [payload for name, payload in events if name == "stop_auto"][0]
    stop_session = [payload for name, payload in events if name == "stop_session"][0]
    assert stop_auto["stop_reason"] == "automation_timeout"
    assert stop_session["reason"] == "automation_timeout"


def test_mini_dma_bench_plan_high_stress_guard_disables_current_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipe_path = tmp_path / "iso-stress.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": bench_automation.MINI_DMA_BENCH_CONFIRMATION,
                "length_setup": {
                    "starting_length_mm": 20.0,
                    "preload_length_mm": 20.4,
                },
                "guardrails": {
                    "max_stress_mpa": 300.0,
                    "recovery_stress_mpa": 50.0,
                    "wire_break_stops_plan": True,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )
    clock = {"now": 100.0}
    events: list[tuple[str, object]] = []

    monkeypatch.setattr(bench_automation.time, "monotonic", lambda: clock["now"])

    class _FakeApp:
        def processEvents(self) -> None:
            events.append(("process", None))

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._automation_active = True
            self._session_active = True
            self._session_json_path = tmp_path / "logs" / "run01" / "metadata.json"

        def set_length_setup_automation_values(
            self,
            *,
            starting_length_mm: float | None,
            preload_length_mm: float | None,
        ) -> None:
            return

        def _load_recipe_from_path(self, path: Path) -> None:
            return

        def _start_auto_ramp(self) -> None:
            return

        def _bench_latest_stress_mpa(self) -> float:
            return 320.0

        def _wire_break_detected(self) -> bool:
            return False

        def _disable_supply_output(self) -> None:
            events.append(("disable_supply", None))

        def start_bench_stress_recovery(self, target_stress_mpa: float, *, reason: str) -> bool:
            events.append(("recover", (target_stress_mpa, reason)))
            self._automation_active = False
            self._session_active = False
            return True

        def close(self) -> None:
            events.append(("close", None))

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=lambda seconds: clock.__setitem__("now", clock["now"] + max(0.05, seconds)),
    )

    run = summary["runs"][0]
    assert run["status"] == "guard_recovered"
    assert run["guard_events"][0]["type"] == "high_stress"
    assert run["guard_events"][0]["stress_mpa"] == pytest.approx(320.0)
    assert ("disable_supply", None) in events
    assert ("recover", (50.0, "bench high-stress guard")) in events


def test_mini_dma_bench_plan_wire_break_stops_remaining_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipe_path = tmp_path / "iso-stress.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": bench_automation.MINI_DMA_BENCH_CONFIRMATION,
                "length_setup": {
                    "starting_length_mm": 20.0,
                    "preload_length_mm": 20.4,
                },
                "guardrails": {
                    "max_stress_mpa": 300.0,
                    "recovery_stress_mpa": 50.0,
                    "wire_break_stops_plan": True,
                },
                "runs": [
                    {"name": "trial-a", "recipe_path": str(recipe_path)},
                    {"name": "trial-b", "recipe_path": str(recipe_path)},
                ],
            }
        ),
        encoding="utf-8",
    )
    clock = {"now": 100.0}

    monkeypatch.setattr(bench_automation.time, "monotonic", lambda: clock["now"])

    class _FakeApp:
        def processEvents(self) -> None:
            return

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._automation_active = True
            self._session_active = True
            self._session_json_path = tmp_path / "logs" / "run01" / "metadata.json"

        def set_length_setup_automation_values(
            self,
            *,
            starting_length_mm: float | None,
            preload_length_mm: float | None,
        ) -> None:
            return

        def _load_recipe_from_path(self, path: Path) -> None:
            return

        def _start_auto_ramp(self) -> None:
            return

        def _wire_break_detected(self) -> bool:
            return True

        def _disable_supply_output(self) -> None:
            self._automation_active = False
            self._session_active = False

        def close(self) -> None:
            return

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=lambda seconds: clock.__setitem__("now", clock["now"] + max(0.05, seconds)),
    )

    assert summary["runs"][0]["status"] == "wire_break"
    assert summary["runs"][1]["status"] == "skipped_after_wire_break"
