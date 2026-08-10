from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_logging.mini_dma_logger import bench_automation


@pytest.fixture(autouse=True)
def _avoid_real_bench_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "data_logging.shared_power_supply.bench_guard.wait_for_bench_lock",
        lambda **_kwargs: nullcontext(),
    )


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


def test_mini_dma_bench_plan_accepts_utf8_bom(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    payload = {
        "schema_version": 1,
        "kind": "mini_dma_bench_sequence",
        "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
    }
    plan_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))

    summary = bench_automation.run_mini_dma_bench_plan(plan_path)

    assert summary["mode"] == "dry_run"
    assert summary["runs"][0]["status"] == "validated"


def test_mini_dma_bench_plan_dry_run_reports_hardware_overrides(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "hardware": {
                    "supply_profile": "shared_hmp_broker",
                    "supply_port": "COM4",
                    "supply_baud": 115200,
                    "shared_broker_host": "127.0.0.1",
                    "shared_broker_port": 8765,
                    "scale_port": "COM5",
                    "scale_baud": 256000,
                    "scale_request_command": "SI",
                    "scale_line_ending": "\\r\\n",
                    "scale_poll_interval_ms": 50,
                    "current_sweep_channel": 4,
                    "motor_supply_enabled": True,
                    "motor_supply_channel": 3,
                    "motor_supply_voltage_v": 12.0,
                    "motor_supply_current_limit_a": 0.5,
                    "tic_full_steps_per_mm": 100.0,
                    "tic_step_mode": "8",
                    "tic_current_limit_mA": 343,
                    "tic_max_speed": 10000000,
                    "tic_max_accel": 100000,
                    "tic_max_decel": 100000,
                    "supply_voltage_limit_v": 32.05,
                    "manual_current_mA": 1.0,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )

    summary = bench_automation.run_mini_dma_bench_plan(plan_path)

    assert summary["hardware"] == {
        "supply_profile": "shared_hmp_broker",
        "supply_port": "COM4",
        "supply_baud": 115200,
        "shared_broker_host": "127.0.0.1",
        "shared_broker_port": 8765,
        "scale_port": "COM5",
        "scale_baud": 256000,
        "scale_request_command": "SI",
        "scale_line_ending": "\\r\\n",
        "scale_poll_interval_ms": 50,
        "current_sweep_channel": 4,
        "motor_supply_enabled": True,
        "motor_supply_channel": 3,
        "motor_supply_voltage_v": 12.0,
        "motor_supply_current_limit_a": 0.5,
        "tic_full_steps_per_mm": 100.0,
        "tic_step_mode": "8",
        "tic_current_limit_mA": 343,
        "tic_max_speed": 10000000,
        "tic_max_accel": 100000,
        "tic_max_decel": 100000,
        "supply_voltage_limit_v": 32.05,
        "manual_current_mA": 1.0,
    }


def test_mini_dma_bench_plan_waits_for_serial_scan_before_hardware_overrides(tmp_path: Path) -> None:
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
                "hardware": {"scale_port": "COM6"},
                "length_setup": {
                    "starting_length_mm": 20.0,
                    "preload_length_mm": 20.4,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )
    events: list[str] = []

    class _FakeApp:
        def processEvents(self) -> None:
            events.append("process")
            if windows:
                windows[0]._serial_port_scan_completed = True

    class _FakeCombo:
        def findData(self, value: object) -> int:
            events.append(f"find:{value}")
            return 0

        def setCurrentIndex(self, _index: int) -> None:
            events.append("selected")

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._serial_port_scan_completed = False
            self.combo_scale_port = _FakeCombo()
            self._automation_active = False
            self._session_active = False
            self._session_json_path = tmp_path / "logs" / "run01" / "metadata.json"
            windows.append(self)

        def _start_serial_port_enumeration(self) -> bool:
            events.append("scan")
            return True

        def set_length_setup_automation_values(
            self,
            *,
            starting_length_mm: float | None,
            preload_length_mm: float | None,
        ) -> None:
            pass

        def _load_recipe_from_path(self, path: Path) -> None:
            pass

        def _start_session(self, *, enable_logging: bool, record_initial_point: bool) -> None:
            pass

        def _start_auto_ramp(self) -> None:
            pass

        def close(self) -> None:
            pass

    windows: list[_FakeWindow] = []
    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=lambda _seconds: None,
    )

    assert summary["runs"][0]["status"] == "completed"
    assert events.index("scan") < events.index("find:COM6")
    assert events.index("process") < events.index("find:COM6")


def test_wait_for_tma_history_scan_blocks_until_current_root_is_ready(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class _FakeWindow:
        _tma_history_root: Path | None = None

        def _current_tma_history_root(self) -> Path:
            return tmp_path

        def _start_pending_tma_history_scan(self) -> None:
            events.append("start")

    window = _FakeWindow()

    class _FakeApp:
        def processEvents(self) -> None:
            events.append("process")
            window._tma_history_root = tmp_path

    bench_automation._wait_for_tma_history_scan(
        window,
        app=_FakeApp(),
        sleep_fn=lambda _seconds: events.append("sleep"),
    )

    assert events == ["start", "process", "sleep", "process"]


def test_recipe_with_first_overheating_skips_tma_history_scan() -> None:
    class _Combo:
        def currentData(self) -> str:
            return "current_sweep_stress"

    class _Checkbox:
        def isChecked(self) -> bool:
            return True

    class _Window:
        combo_recipe_mode = _Combo()
        check_current_sweep_first_overheating = _Checkbox()

        def _is_constant_current_strain_sweep_mode(self, _mode: str) -> bool:
            return False

    assert bench_automation._recipe_needs_tma_history_scan(_Window()) is False


def test_execute_run_hands_completed_first_overheating_preflight_to_isolated_start(
    monkeypatch,
) -> None:
    class _Checkbox:
        def isChecked(self) -> bool:
            return True

    class _Combo:
        def currentData(self) -> str:
            return "current_sweep_stress"

    class _Window:
        combo_recipe_mode = _Combo()
        check_current_sweep_first_overheating = _Checkbox()
        _controller_process_prior_run_preflight_complete = False
        _control_process_enabled = True
        _controller_process_mode = False
        _session_active = False
        _automation_active = False

        def _is_constant_current_strain_sweep_mode(self, _mode: str) -> bool:
            return False

        def _load_recipe_from_path(self, _path: Path) -> None:
            return None

        def _start_auto_ramp(self) -> None:
            assert self._controller_process_prior_run_preflight_complete is True

    window = _Window()
    monkeypatch.setattr(bench_automation, "_apply_sample_identity", lambda *_args: None)
    monkeypatch.setattr(bench_automation, "_apply_length_setup_automation", lambda *_args: None)
    monkeypatch.setattr(bench_automation, "_prefer_next_output_run", lambda *_args: None)
    monkeypatch.setattr(bench_automation, "_ensure_measurement_logging_session", lambda *_args: None)

    run = bench_automation.MiniDmaBenchRun(
        name="bounded",
        recipe_path=Path("bounded.recipe.json"),
        repeat_index=0,
        max_run_duration_s=1.0,
    )
    sample = bench_automation.MiniDmaSampleIdentity(
        composition="Ni50Fe27Ga23",
        microwire="12/5",
        sample_name="Ni50Fe27Ga23 12/5",
        diameter_mm=0.00935,
    )
    result = bench_automation._execute_run(
        run,
        app=SimpleNamespace(processEvents=lambda: None),
        window=window,
        sample_identity=sample,
        guardrails=bench_automation.MiniDmaBenchGuardrails(),
        sleep_fn=lambda _seconds: None,
        total_deadline_s=None,
    )

    assert result["status"] == "not_started"


def test_process_isolated_bench_does_not_open_ui_owned_logging_session() -> None:
    events: list[str] = []

    class _Window:
        _control_process_enabled = True
        _controller_process_mode = False
        _session_active = False

        def _start_session(self, *, enable_logging: bool, record_initial_point: bool) -> None:
            events.append(f"start:{enable_logging}:{record_initial_point}")

    bench_automation._ensure_measurement_logging_session(_Window())

    assert events == []


def test_in_process_bench_still_opens_logging_session() -> None:
    events: list[str] = []

    class _Window:
        _control_process_enabled = False
        _controller_process_mode = False
        _session_active = False

        def _start_session(self, *, enable_logging: bool, record_initial_point: bool) -> None:
            events.append(f"start:{enable_logging}:{record_initial_point}")

    bench_automation._ensure_measurement_logging_session(_Window())

    assert events == ["start:True:False"]


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

        def _start_session(self, *, enable_logging: bool, record_initial_point: bool) -> None:
            events.append(("session", (enable_logging, record_initial_point)))

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
    assert ("session", (True, False)) in events
    assert events.index(("session", (True, False))) < events.index(("start", None))
    assert ("start", None) in events


def test_mini_dma_bench_plan_replaces_stale_execute_summary_while_running(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "kind": bench_automation.PLAN_KIND,
                "mode": "execute",
                "state": "completed",
                "runs": [{"name": "old-run", "status": "completed"}],
            }
        ),
        encoding="utf-8",
    )
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
                "default_max_run_duration_s": 10,
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
    windows: list[object] = []
    observed_running_summaries: list[dict[str, object]] = []

    class _FakeApp:
        def processEvents(self) -> None:
            pass

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._automation_active = False
            self._session_active = False
            self._session_json_path = tmp_path / "logs" / "run01" / "metadata.json"
            windows.append(self)

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
            self._automation_active = True
            self._session_active = True

        def _stop_session(self, *, reason: str, detail: str) -> None:
            self._session_active = False

        def close(self) -> None:
            self._automation_active = False
            self._session_active = False

    def _sleep(_seconds: float) -> None:
        observed_running_summaries.append(json.loads(summary_path.read_text(encoding="utf-8")))
        assert windows
        windows[-1]._automation_active = False  # type: ignore[attr-defined]

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=_sleep,
    )

    assert observed_running_summaries
    running = observed_running_summaries[0]
    assert running["state"] == "running"
    assert running["run_count"] == 0
    assert running["planned_run_count"] == 1
    assert running["runs"] == []
    assert "old-run" not in json.dumps(running)
    assert summary["state"] == "completed"
    assert summary["run_count"] == 1


def test_mini_dma_bench_plan_applies_hardware_overrides_before_start(tmp_path: Path) -> None:
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
                "default_max_run_duration_s": 1,
                "length_setup": {
                    "starting_length_mm": 20.0,
                    "preload_length_mm": 20.4,
                },
                "hardware": {
                    "supply_profile": "shared_hmp_broker",
                    "supply_port": "COM4",
                    "supply_baud": 115200,
                    "shared_broker_host": "127.0.0.1",
                    "shared_broker_port": 8765,
                    "scale_port": "COM5",
                    "scale_baud": 256000,
                    "scale_request_command": "SI",
                    "scale_line_ending": "\\r\\n",
                    "scale_poll_interval_ms": 50,
                    "current_sweep_channel": 4,
                    "motor_supply_enabled": True,
                    "motor_supply_channel": 3,
                    "motor_supply_voltage_v": 12.0,
                    "motor_supply_current_limit_a": 0.5,
                    "tic_full_steps_per_mm": 100.0,
                    "tic_step_mode": "8",
                    "tic_current_limit_mA": 343,
                    "tic_max_speed": 10000000,
                    "tic_max_accel": 100000,
                    "tic_max_decel": 100000,
                    "supply_voltage_limit_v": 32.05,
                    "manual_current_mA": 1.0,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )
    events: list[tuple[str, object]] = []

    class _FakeCombo:
        def __init__(self, values: list[object]) -> None:
            self.values = values
            self.current: object | None = None

        def findData(self, value: object) -> int:
            try:
                return self.values.index(value)
            except ValueError:
                return -1

        def findText(self, value: str) -> int:
            try:
                return [str(item) for item in self.values].index(str(value))
            except ValueError:
                return -1

        def setCurrentIndex(self, index: int) -> None:
            self.current = self.values[index]
            events.append(("combo", self.current))

    class _FakeSpin:
        def __init__(self) -> None:
            self.value: float | int | None = None

        def setValue(self, value: float | int) -> None:
            self.value = value
            events.append(("spin", value))

    class _FakeLineEdit:
        def __init__(self) -> None:
            self.value = ""

        def setText(self, value: str) -> None:
            self.value = value
            events.append(("text", value))

    class _FakeCheck:
        def __init__(self) -> None:
            self.checked = False

        def setChecked(self, value: bool) -> None:
            self.checked = bool(value)
            events.append(("check", self.checked))

    class _FakeApp:
        def processEvents(self) -> None:
            pass

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._automation_active = False
            self._session_active = False
            self._session_json_path = tmp_path / "logs" / "run01" / "metadata.json"
            self.combo_supply_profile = _FakeCombo(["hmp4040", "shared_hmp_broker"])
            self.combo_supply_port = _FakeCombo(["COM3", "COM4"])
            self.combo_supply_baud = _FakeCombo(["9600", "115200"])
            self.edit_shared_broker_host = _FakeLineEdit()
            self.spin_shared_broker_port = _FakeSpin()
            self.combo_scale_port = _FakeCombo(["COM5", "COM6"])
            self.combo_scale_baud = _FakeCombo(["9600", "256000"])
            self.edit_scale_request = _FakeLineEdit()
            self.edit_scale_terminator = _FakeLineEdit()
            self.spin_scale_interval = _FakeSpin()
            self.combo_current_sweep_supply_channel = _FakeCombo([0, 1, 2, 3, 4])
            self.check_motor_supply_power = _FakeCheck()
            self.combo_motor_supply_channel = _FakeCombo([0, 1, 2, 3, 4])
            self.spin_motor_supply_voltage = _FakeSpin()
            self.spin_motor_supply_current_limit = _FakeSpin()
            self.spin_full_steps_per_mm = _FakeSpin()
            self.combo_tic_step_mode = _FakeCombo(["full", "2", "4", "8"])
            self.spin_tic_current_limit_mA = _FakeSpin()
            self.spin_tic_max_speed = _FakeSpin()
            self.spin_tic_max_accel = _FakeSpin()
            self.spin_tic_max_decel = _FakeSpin()
            self.spin_supply_voltage_limit = _FakeSpin()
            self.spin_supply_manual_current = _FakeSpin()

        def _sync_tic_units_per_mm_from_full_steps(self, *, persist: bool = True) -> None:
            events.append(("sync_tic_units", persist))

        def _persist_settings_if_enabled(self) -> None:
            events.append(("persist", None))

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
            events.append(("start", None))

        def close(self) -> None:
            return

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=lambda _seconds: None,
    )

    assert summary["runs"][0]["status"] == "completed"
    assert events.index(("combo", "shared_hmp_broker")) < events.index(("start", None))
    assert ("combo", "COM4") in events
    assert ("combo", "115200") in events
    assert ("text", "127.0.0.1") in events
    assert ("combo", "COM5") in events
    assert ("combo", "256000") in events
    assert ("text", "SI") in events
    assert ("text", "\\r\\n") in events
    assert ("spin", 50) in events
    assert ("combo", 4) in events
    assert ("check", True) in events
    assert ("combo", 3) in events
    assert ("spin", 12.0) in events
    assert ("spin", 0.5) in events
    assert ("spin", 100.0) in events
    assert ("combo", "8") in events
    assert ("sync_tic_units", False) in events
    assert ("spin", 343) in events
    assert ("spin", 10000000) in events
    assert events.count(("spin", 100000)) >= 2


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


def test_mini_dma_bench_plan_stops_session_when_recipe_automation_stops(tmp_path: Path) -> None:
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
                "default_max_run_duration_s": 10,
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
    run_dir = tmp_path / "logs" / "run01"
    run_dir.mkdir(parents=True)
    (run_dir / "control_trace.csv").write_text(
        "\n".join(
            [
                "elapsed_s,timestamp_utc,recipe_mode,task_text,automation_phase,automation_basis,automation_target_value,plateau_index,decision,current_value,error_value,tolerance,sensitivity_per_mm,motor_step_mm,correction_mm,backlash_mm,command_speed_mm_s,required_fresh_samples,post_move_sample_count,target_mm,effective_target_mm,result,reason",
                "12.5,2026-06-01 20:00:00,current_sweep_stress,Manual mode,idle,stress_mpa,50,,wait,104.0,-54.0,0.17,124.0,0.00125,0.08,0,,,,,,stopped,correction_travel_limit",
            ]
        ),
        encoding="utf-8",
    )
    events: list[tuple[str, object]] = []

    class _FakeApp:
        def processEvents(self) -> None:
            events.append(("process", None))

    class _FakeLabel:
        def text(self) -> str:
            return "Manual mode"

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._automation_active = False
            self._session_active = False
            self._session_json_path = run_dir / "metadata.json"
            self.label_task_status = _FakeLabel()
            self._session_stop_reason = "recipe_control_stop"
            self._session_stop_detail = "Recipe stopped before completion and recovery was offered."

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
            self._automation_active = True
            self._session_active = True

        def _stop_session(self, *, reason: str, detail: str) -> None:
            events.append(("stop_session", (reason, detail)))
            self._session_active = False
            self._session_stop_reason = reason
            self._session_stop_detail = detail

        def _session_stop_metadata(self) -> dict[str, object]:
            return {
                "reason": self._session_stop_reason,
                "category": "fault",
                "label": "Recipe stopped by control/error condition",
                "detail": self._session_stop_detail,
                "recorded_utc": "2026-06-01 20:00:00",
            }

        def close(self) -> None:
            self._automation_active = False
            self._session_active = False

    window = _FakeWindow()

    def _window_factory(**_kwargs: object) -> _FakeWindow:
        return window

    def _sleep(_seconds: float) -> None:
        window._automation_active = False

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_window_factory,
        sleep_fn=_sleep,
    )

    assert summary["runs"][0]["status"] == "stopped"
    stop_events = [event for event in events if event[0] == "stop_session"]
    assert stop_events
    assert stop_events[-1][1][0] == "recipe_control_stop"
    assert stop_events[-1][1][1] == "Recipe stopped before completion and recovery was offered."
    run_summary = summary["runs"][0]
    assert run_summary["task_text"] == "Manual mode"
    assert run_summary["stop_metadata"]["reason"] == "recipe_control_stop"
    assert run_summary["stop_metadata"]["detail"] == "Recipe stopped before completion and recovery was offered."
    assert run_summary["control_trace_stop"]["reason"] == "correction_travel_limit"


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


def test_mini_dma_bench_plan_acquires_shared_hmp_lock_for_execution(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    lock_path = tmp_path / "hmp.lock"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": bench_automation.MINI_DMA_BENCH_CONFIRMATION,
                "default_max_run_duration_s": 1,
                "bench_lock": {
                    "owner": "codex-test",
                    "purpose": "coordinated hardware smoke",
                    "timeout_s": 12.5,
                    "lock_path": str(lock_path),
                },
                "length_setup": {
                    "starting_length_mm": 20.0,
                    "preload_length_mm": 20.4,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )
    lock_calls: list[dict[str, object]] = []
    events: list[tuple[str, object]] = []

    class _FakeApp:
        def processEvents(self) -> None:
            events.append(("process", None))

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._automation_active = False
            self._session_active = False

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
            events.append(("start", None))

        def close(self) -> None:
            events.append(("close", None))

    def _lock_factory(**kwargs: object):
        lock_calls.append(kwargs)
        return nullcontext()

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        bench_lock_factory=_lock_factory,
        sleep_fn=lambda _seconds: None,
    )

    assert summary["mode"] == "execute"
    assert summary["bench_lock"]["enabled"] is True
    assert summary["bench_lock"]["owner"] == "codex-test"
    assert summary["bench_lock"]["purpose"] == "coordinated hardware smoke"
    assert lock_calls == [
        {
            "owner": "codex-test",
            "purpose": "coordinated hardware smoke",
            "timeout_s": 12.5,
            "lock_path": lock_path,
        }
    ]
    assert ("start", None) in events


def test_mini_dma_bench_plan_applies_sample_identity_before_recipe_start(tmp_path: Path) -> None:
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
                "default_max_run_duration_s": 1,
                "bench_lock": {"enabled": False},
                "sample_identity": {
                    "composition": "Ni50Fe27Ga23",
                    "microwire": "12/2",
                    "condition": "heat shield",
                    "sample_name": "Ni50Fe27Ga23 12/2 heat shield",
                    "log_name": "Ni50Fe27Ga23 12_2 heat shield iso-stress",
                    "builder_project_path": "G:/My Drive/1 Projects/Praha/microwire_project.pydpj",
                    "diameter_mm": 0.0191,
                },
                "length_setup": {
                    "starting_length_mm": 48.0,
                    "preload_length_mm": 48.4,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )
    events: list[tuple[str, object]] = []

    class _FakeText:
        def __init__(self, name: str) -> None:
            self.name = name
            self.value = ""

        def setText(self, value: str) -> None:
            self.value = value
            events.append((self.name, value))

    class _FakeSpin:
        def __init__(self) -> None:
            self.value = 0.0

        def setValue(self, value: float) -> None:
            self.value = value
            events.append(("diameter", value))

    class _FakeApp:
        def processEvents(self) -> None:
            pass

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            self._automation_active = False
            self._session_active = False
            self.edit_name_composition = _FakeText("composition")
            self.edit_name_wire = _FakeText("microwire")
            self.edit_name_specimen = _FakeText("specimen")
            self.edit_name_condition = _FakeText("condition")
            self.edit_sample_name = _FakeText("sample_name")
            self.edit_log_name = _FakeText("log_name")
            self.edit_project_path = _FakeText("builder_project")
            self.spin_diameter = _FakeSpin()

        def _sync_auto_name_fields(self) -> None:
            events.append(("sync", None))

        def _stop_builder_project_import_thread(self) -> None:
            events.append(("stop_builder_import", None))

        def _mark_diameter_imported(self, imported: bool) -> None:
            events.append(("diameter_imported", imported))

        def _persist_settings_if_enabled(self) -> None:
            events.append(("persist", None))

        def set_length_setup_automation_values(
            self,
            *,
            starting_length_mm: float | None,
            preload_length_mm: float | None,
        ) -> None:
            pass

        def _load_recipe_from_path(self, path: Path) -> None:
            events.append(("recipe", path.name))
            self.spin_diameter.setValue(0.017)

        def _start_auto_ramp(self) -> None:
            events.append(("start", self.spin_diameter.value))

        def close(self) -> None:
            pass

    bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=lambda _seconds: None,
    )

    recipe_index = events.index(("recipe", "iso-stress.recipe.json"))
    assert events.index(("composition", "Ni50Fe27Ga23")) < recipe_index
    assert events.index(("microwire", "12/2")) < recipe_index
    assert events.index(("condition", "heat shield")) < recipe_index
    assert events.index(("sample_name", "Ni50Fe27Ga23 12/2 heat shield")) < recipe_index
    assert events.index(("log_name", "Ni50Fe27Ga23 12_2 heat shield iso-stress")) < recipe_index
    assert events.index(("builder_project", "G:\\My Drive\\1 Projects\\Praha\\microwire_project.pydpj")) < recipe_index
    assert ("diameter", 0.0191) in events
    assert ("diameter", 0.017) in events
    assert events[-4:] == [
        ("diameter", 0.0191),
        ("diameter_imported", True),
        ("persist", None),
        ("start", 0.0191),
    ]
    assert events.count(("stop_builder_import", None)) == 2


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
