from __future__ import annotations

import csv
from dataclasses import dataclass, FrozenInstanceError
import json
import inspect
import multiprocessing
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from PyQt6 import QtWidgets

from experiments.thermal_camera_viewer import ThermalFrame

from data_logging.mini_dma_logger.control_process import (
    BackendFactorySpec,
    ControlBackpressureError,
    ControlEvent,
    ControlEventKind,
    ControlPolicy,
    ControlSessionIdentity,
    ControlSnapshot,
    ControlStartRejected,
    ControlStartRequest,
    ControlState,
    MiniDmaControlProcess,
    SimulatedBackendConfig,
)
from data_logging.mini_dma_logger.production_control_backend import (
    ProductionMiniDmaBackend,
    _apply_window_configuration,
    capture_window_configuration,
)


def test_control_process_spawn_entrypoint_preserves_legacy_positional_abi() -> None:
    """A running Windows parent may spawn after source files are updated."""

    from data_logging.mini_dma_logger import control_process as module

    parameters = tuple(inspect.signature(module._run_control_process).parameters.values())
    assert tuple(parameter.name for parameter in parameters[:9]) == (
        "command_queue",
        "heartbeat_queue",
        "snapshot_queue",
        "event_queue",
        "fault_connection",
        "emergency_event",
        "shutdown_event",
        "backend_config",
        "backend_factory_spec",
    )
    assert parameters[9].name == "hold_bypass_queue"
    assert parameters[9].default is None


def test_windows_pythonw_parent_selects_console_spawn_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from data_logging.mini_dma_logger import control_process as module

    python = tmp_path / "python.exe"
    pythonw = tmp_path / "pythonw.exe"
    python.touch()
    pythonw.touch()
    selected: list[str] = []
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module.sys, "executable", str(pythonw))
    monkeypatch.setattr(module.multiprocessing, "set_executable", selected.append)

    actual = module._prepare_windows_spawn_executable()

    assert actual == python
    assert selected == [str(python)]


def test_apply_window_configuration_preserves_text_only_combo_selection() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    combo = QtWidgets.QComboBox()
    combo.addItems(["600", "9600", "19200"])
    host = SimpleNamespace(combo_scale_baud=combo)

    _apply_window_configuration(
        host,
        {
            "widgets": {
                "combo_scale_baud": {
                    "kind": "combo",
                    "index": 1,
                    "data": None,
                    "text": "9600",
                }
            }
        },
    )

    assert combo.currentText() == "9600"
    del combo
    del app


def test_legacy_nine_argument_windows_parent_can_spawn_updated_child() -> None:
    """Exercise the exact old-parent/new-child boundary that failed live."""

    from data_logging.mini_dma_logger import control_process as module

    context = multiprocessing.get_context("spawn")
    command_queue = context.Queue(maxsize=2)
    heartbeat_queue = context.Queue(maxsize=1)
    snapshot_queue = context.Queue(maxsize=1)
    event_queue = context.Queue(maxsize=2)
    fault_parent, fault_child = context.Pipe(duplex=False)
    emergency_event = context.Event()
    shutdown_event = context.Event()
    process = context.Process(
        target=module._run_control_process,
        args=(
            command_queue,
            heartbeat_queue,
            snapshot_queue,
            event_queue,
            fault_child,
            emergency_event,
            shutdown_event,
            SimulatedBackendConfig(),
            None,
        ),
    )
    process.start()
    try:
        snapshot = snapshot_queue.get(timeout=5.0)
        assert snapshot.state is ControlState.IDLE
        assert process.is_alive()
        assert not fault_parent.poll()
    finally:
        shutdown_event.set()
        process.join(timeout=5.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
    assert process.exitcode == 0


def _wait_for_snapshot(
    process: MiniDmaControlProcess,
    predicate,
    *,
    timeout_s: float = 3.0,
) -> ControlSnapshot:
    deadline_s = time.monotonic() + timeout_s
    latest: ControlSnapshot | None = None
    while time.monotonic() < deadline_s:
        snapshot = process.poll_latest_snapshot()
        if snapshot is not None:
            latest = snapshot
            if predicate(snapshot):
                return snapshot
        if not process.is_alive() and process.exitcode is not None:
            break
        time.sleep(0.01)
    raise AssertionError(
        f"timed out waiting for control snapshot; latest={latest!r}, "
        f"alive={process.is_alive()}, exitcode={process.exitcode}"
    )


def _wait_for_event(
    process: MiniDmaControlProcess,
    kind: ControlEventKind,
    *,
    timeout_s: float = 3.0,
) -> ControlEvent:
    deadline_s = time.monotonic() + timeout_s
    observed: list[ControlEvent] = []
    while time.monotonic() < deadline_s:
        events = process.poll_events()
        observed.extend(events)
        for event in events:
            if event.kind is kind:
                return event
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {kind}; observed={observed!r}")


def _identity(generation: int = 1) -> ControlSessionIdentity:
    return ControlSessionIdentity("offline-test-session", generation)


def test_ipc_payloads_are_immutable_and_validate_identity() -> None:
    identity = _identity()
    request = ControlStartRequest(identity=identity, policy=ControlPolicy.PRAGUE)

    with pytest.raises(FrozenInstanceError):
        request.control_interval_s = 1.0  # type: ignore[misc]
    with pytest.raises(ValueError, match="session_id"):
        ControlSessionIdentity("", 1)
    with pytest.raises(ValueError, match="generation"):
        ControlSessionIdentity("session", 0)
    with pytest.raises(ValueError, match="explicit ControlPolicy"):
        ControlStartRequest(identity=identity, policy="prague")  # type: ignore[arg-type]


def test_operator_command_channel_reports_backpressure() -> None:
    class _UnstartedAliveProcess:
        @staticmethod
        def is_alive() -> bool:
            return True

    process = MiniDmaControlProcess(command_capacity=1)
    process._process = _UnstartedAliveProcess()  # type: ignore[assignment]
    identity = _identity()
    request = ControlStartRequest(identity=identity, policy=ControlPolicy.PRAGUE)
    try:
        process.start_session(request)
        with pytest.raises(ControlBackpressureError, match="queue is full"):
            process.start_session(request)
    finally:
        process._command_queue.close()
        process._command_queue.join_thread()


def test_spawned_process_reports_ready_before_session_start() -> None:
    process = MiniDmaControlProcess(heartbeat_interval_s=0.02)
    try:
        process.start_process()

        ready = process.wait_until_ready(timeout_s=3.0)

        assert ready.state is ControlState.IDLE
        assert ready.identity is None
        assert ready.owner_pid == process.pid
        assert process.is_alive()
    finally:
        assert process.close()


def test_momentary_hold_bypass_is_latest_value_and_pause_clears_it() -> None:
    process = MiniDmaControlProcess(heartbeat_interval_s=0.02)
    identity = _identity()
    try:
        process.start_process()
        process.wait_until_ready(timeout_s=3.0)
        process.start_session(
            ControlStartRequest(
                identity=identity,
                policy=ControlPolicy.PRAGUE,
                snapshot_interval_s=0.02,
            )
        )
        _wait_for_snapshot(
            process,
            lambda item: item.state is ControlState.RUNNING,
        )

        process.set_current_hold_bypass(identity, True)
        active = _wait_for_snapshot(
            process,
            lambda item: item.readback_value("current_hold_bypass_active") is True,
        )
        assert active.identity == identity

        process.set_current_hold_bypass(identity, False)
        released = _wait_for_snapshot(
            process,
            lambda item: item.readback_value("current_hold_bypass_active") is False,
        )
        assert released.state is ControlState.RUNNING

        process.set_current_hold_bypass(identity, True)
        _wait_for_snapshot(
            process,
            lambda item: item.readback_value("current_hold_bypass_active") is True,
        )
        process.pause(identity)
        paused = _wait_for_snapshot(
            process,
            lambda item: item.state is ControlState.PAUSED,
        )
        assert paused.readback_value("current_hold_bypass_active") is False
    finally:
        assert process.close(timeout_s=2.0, force=True)


def test_production_backend_process_reports_ready_without_acquiring_hardware() -> None:
    process = MiniDmaControlProcess(
        heartbeat_interval_s=0.02,
        backend_factory_spec=BackendFactorySpec(
            module="data_logging.tma_logger.production_control_backend",
            factory="create_production_backend",
        ),
    )
    try:
        process.start_process()

        ready = process.wait_until_ready(timeout_s=10.0)

        assert ready.state is ControlState.IDLE
        assert ready.identity is None
        assert ready.owner_pid == process.pid
        assert ready.readback_value("started") is False
        assert ready.readback_value("scale_connected") is None
        assert ready.readback_value("supply_connected") is None
        assert ready.readback_value("tic_connected") is None
    finally:
        assert process.close()


@pytest.mark.parametrize("policy", [ControlPolicy.PRAGUE, ControlPolicy.KOSICE])
def test_spawned_production_adapter_runs_fake_hardware_lifecycle(
    policy: ControlPolicy,
    tmp_path: Path,
) -> None:
    process = MiniDmaControlProcess(
        heartbeat_interval_s=0.02,
        backend_factory_spec=BackendFactorySpec(
            module="data_logging.tma_logger.fake_production_backend",
            factory="create_fake_production_backend",
        ),
    )
    identity = ControlSessionIdentity(f"fake-{policy.value}", 1)
    output_dir = tmp_path / policy.value
    payload = {
        "schema_version": 1,
        "widgets": {},
        "starting_length_mm": 50.0,
        "output_collision_action": "replace",
        "cadence_downgrade_accepted": True,
        "supply_lease_owner": f"tma-test-{policy.value}",
        "fake_policy": "kosice" if policy is ControlPolicy.KOSICE else "prague",
        "fake_output_dir": str(output_dir),
    }
    try:
        process.start_process()
        process.wait_until_ready(timeout_s=10.0)
        process.start_session(
            ControlStartRequest(
                identity=identity,
                policy=policy,
                config_json=json.dumps(payload),
                control_interval_s=0.01,
                snapshot_interval_s=0.02,
                parent_heartbeat_timeout_s=1.0,
            )
        )
        running = _wait_for_snapshot(
            process,
            lambda item: (
                item.identity == identity
                and item.state is ControlState.RUNNING
                and item.tick_count >= 2
            ),
            timeout_s=10.0,
        )
        assert running.owner_pid != os.getpid()
        assert running.readback_value("scale_connected") is True
        assert running.readback_value("supply_connected") is True
        assert running.readback_value("tic_connected") is True
        assert running.readback_value("ir_connected") is True

        process.pause(identity)
        paused = _wait_for_snapshot(
            process,
            lambda item: item.state is ControlState.PAUSED,
            timeout_s=3.0,
        )
        assert paused.readback_value("automation_paused") is True

        process.resume(identity)
        _wait_for_snapshot(
            process,
            lambda item: item.state is ControlState.RUNNING,
            timeout_s=3.0,
        )
        completed = _wait_for_snapshot(
            process,
            lambda item: item.state is ControlState.STOPPED,
            timeout_s=10.0,
        )
        completion_event = _wait_for_event(
            process,
            ControlEventKind.RECIPE_COMPLETE,
            timeout_s=3.0,
        )
        assert completion_event.detail == "production recipe completed"
        assert completed.readback_value("supply_output_enabled") is False
        rows = list(csv.DictReader((output_dir / "fake_measurement.csv").open(
            newline="",
            encoding="utf-8",
        )))
        assert rows
        expected_policy = (
            "kosice_adaptive"
            if policy is ControlPolicy.KOSICE
            else "prague_legacy"
        )
        assert {row["policy"] for row in rows} == {expected_policy}
    finally:
        assert process.close(timeout_s=2.0, force=True)


def test_wait_until_ready_reports_child_exit_detail() -> None:
    class _ExitedProcess:
        exitcode = 17

        @staticmethod
        def is_alive() -> bool:
            return False

    process = MiniDmaControlProcess()
    process._process = _ExitedProcess()  # type: ignore[assignment]
    try:
        with pytest.raises(
            RuntimeError,
            match="exited with code 17 before reporting ready",
        ):
            process.wait_until_ready(timeout_s=0.1)
    finally:
        process._process = None
        process._command_queue.close()
        process._command_queue.join_thread()


def test_spawned_backend_bootstrap_failure_reports_original_detail() -> None:
    process = MiniDmaControlProcess(
        heartbeat_interval_s=0.02,
        backend_factory_spec=BackendFactorySpec(
            module="data_logging.tma_logger.missing_backend_for_test",
            factory="create_backend",
        ),
    )
    try:
        process.start_process()

        with pytest.raises(RuntimeError, match="missing_backend_for_test"):
            process.wait_until_ready(timeout_s=3.0)

        detail, fault_traceback = process.poll_fault_detail()
        assert "missing_backend_for_test" in detail
        assert "ModuleNotFoundError" in fault_traceback
    finally:
        assert process.close(timeout_s=2.0, force=True)


def test_out_of_band_emergency_bypasses_a_full_command_channel() -> None:
    class _UnstartedAliveProcess:
        @staticmethod
        def is_alive() -> bool:
            return True

    process = MiniDmaControlProcess(command_capacity=1)
    process._process = _UnstartedAliveProcess()  # type: ignore[assignment]
    identity = _identity()
    request = ControlStartRequest(identity=identity, policy=ControlPolicy.PRAGUE)
    try:
        process.start_session(request)
        with pytest.raises(ControlBackpressureError, match="queue is full"):
            process.start_session(request)

        process.emergency_stop()

        assert process._emergency_event.is_set()
    finally:
        process._command_queue.close()
        process._command_queue.join_thread()


@pytest.mark.parametrize("policy", [ControlPolicy.PRAGUE, ControlPolicy.KOSICE])
def test_spawned_process_owns_backend_and_ticks_during_ui_thread_stall(
    policy: ControlPolicy,
) -> None:
    process = MiniDmaControlProcess(heartbeat_interval_s=0.02)
    identity = _identity()
    try:
        process.start_process()
        process.start_session(
            ControlStartRequest(
                identity=identity,
                policy=policy,
                control_interval_s=0.01,
                snapshot_interval_s=0.02,
                parent_heartbeat_timeout_s=0.20,
            )
        )
        started = _wait_for_snapshot(
            process,
            lambda item: item.state is ControlState.RUNNING and item.tick_count >= 2,
        )
        assert started.owner_pid == process.pid
        assert started.owner_pid != os.getpid()
        assert started.readback_value("backend_owner_pid") == process.pid
        assert started.readback_value("policy") == policy.value

        # Deliberately stop polling from this (UI-equivalent) thread. The
        # supervisor heartbeat thread and child control clock remain independent.
        before = started.tick_count
        time.sleep(0.20)
        after = _wait_for_snapshot(process, lambda item: item.tick_count > before + 5)
        assert after.state is ControlState.RUNNING
        assert after.readback_value("output_enabled") is True
    finally:
        assert process.close()


def test_pause_resume_stop_are_session_scoped_and_safe() -> None:
    process = MiniDmaControlProcess(heartbeat_interval_s=0.02)
    identity = _identity()
    try:
        process.start_process()
        process.start_session(
            ControlStartRequest(
                identity=identity,
                policy=ControlPolicy.KOSICE,
                control_interval_s=0.01,
            )
        )
        running = _wait_for_snapshot(process, lambda item: item.tick_count >= 2)

        process.pause(identity)
        paused = _wait_for_snapshot(process, lambda item: item.state is ControlState.PAUSED)
        paused_ticks = paused.tick_count
        assert paused.readback_value("output_enabled") is False
        time.sleep(0.08)
        still_paused = _wait_for_snapshot(
            process,
            lambda item: item.state is ControlState.PAUSED and item.sequence > paused.sequence,
        )
        assert still_paused.tick_count == paused_ticks

        process.resume(identity)
        resumed = _wait_for_snapshot(
            process,
            lambda item: item.state is ControlState.RUNNING and item.tick_count > paused_ticks,
        )
        assert resumed.tick_count > running.tick_count
        assert resumed.readback_value("output_enabled") is True

        process.stop(identity)
        stopped = _wait_for_snapshot(process, lambda item: item.state is ControlState.STOPPED)
        assert stopped.readback_value("output_enabled") is False
    finally:
        assert process.close()


def test_stale_session_command_is_rejected_without_changing_active_generation() -> None:
    process = MiniDmaControlProcess(heartbeat_interval_s=0.02)
    identity = _identity(2)
    stale_identity = _identity(1)
    try:
        process.start_process()
        process.start_session(ControlStartRequest(identity=identity, policy=ControlPolicy.PRAGUE))
        _wait_for_snapshot(process, lambda item: item.state is ControlState.RUNNING)

        process.pause(stale_identity)
        rejected = _wait_for_event(process, ControlEventKind.COMMAND_REJECTED)
        assert rejected.identity == stale_identity
        assert "stale" in rejected.detail
        current = _wait_for_snapshot(process, lambda item: item.state is ControlState.RUNNING)
        assert current.identity == identity
    finally:
        assert process.close()


def test_parent_heartbeat_timeout_uses_emergency_safe_path() -> None:
    process = MiniDmaControlProcess(heartbeat_interval_s=None)
    identity = _identity()
    try:
        process.start_process()
        process.start_session(
            ControlStartRequest(
                identity=identity,
                policy=ControlPolicy.KOSICE,
                control_interval_s=0.01,
                snapshot_interval_s=0.02,
                parent_heartbeat_timeout_s=0.12,
            )
        )
        emergency = _wait_for_snapshot(
            process,
            lambda item: item.state is ControlState.EMERGENCY,
        )
        assert emergency.readback_value("output_enabled") is False
        assert emergency.readback_value("emergency_reason") == "parent heartbeat timeout"
    finally:
        assert process.close()


def test_out_of_band_emergency_stops_output() -> None:
    process = MiniDmaControlProcess(heartbeat_interval_s=0.02, command_capacity=1)
    identity = _identity()
    try:
        process.start_process()
        process.start_session(ControlStartRequest(identity=identity, policy=ControlPolicy.PRAGUE))
        _wait_for_snapshot(process, lambda item: item.state is ControlState.RUNNING)

        process.emergency_stop()
        emergency = _wait_for_snapshot(process, lambda item: item.state is ControlState.EMERGENCY)
        assert emergency.readback_value("output_enabled") is False
        assert emergency.readback_value("emergency_reason") == "operator emergency request"
    finally:
        assert process.close()


def test_backend_fault_attempts_emergency_stop_and_reports_fault() -> None:
    process = MiniDmaControlProcess(
        backend_config=SimulatedBackendConfig(crash_after_ticks=3),
        heartbeat_interval_s=0.02,
    )
    identity = _identity()
    try:
        process.start_process()
        process.start_session(
            ControlStartRequest(
                identity=identity,
                policy=ControlPolicy.KOSICE,
                control_interval_s=0.01,
                snapshot_interval_s=0.01,
            )
        )
        faulted = _wait_for_snapshot(process, lambda item: item.state is ControlState.FAULTED)
        assert faulted.readback_value("output_enabled") is False
        assert "simulated backend failure" in str(faulted.readback_value("emergency_reason"))
        fault = _wait_for_event(process, ControlEventKind.FAULT)
        assert "simulated backend failure" in fault.detail
        deadline_s = time.monotonic() + 2.0
        while process.is_alive() and time.monotonic() < deadline_s:
            time.sleep(0.01)
        assert process.exitcode not in {None, 0}
        fault_detail, fault_traceback = process.poll_fault_detail()
        assert "simulated backend failure at tick 3" in fault_detail
        assert "RuntimeError: simulated backend failure at tick 3" in fault_traceback
    finally:
        assert process.close()


def test_runtime_configuration_update_is_session_scoped() -> None:
    process = MiniDmaControlProcess(heartbeat_interval_s=0.02)
    identity = _identity()
    try:
        process.start_process()
        process.start_session(
            ControlStartRequest(identity=identity, policy=ControlPolicy.PRAGUE)
        )
        _wait_for_snapshot(process, lambda item: item.state is ControlState.RUNNING)

        process.update_config(identity, '{"runtime_update":true}')
        updated = _wait_for_event(process, ControlEventKind.CONFIG_UPDATED)
        assert updated.identity == identity
        assert "configuration updated" in updated.detail
        acknowledged = _wait_for_snapshot(
            process,
            lambda item: item.last_command_sequence >= 2,
        )
        assert acknowledged.state is ControlState.RUNNING
        assert acknowledged.last_command_result == "accepted"
        assert "configuration updated" in acknowledged.last_command_detail
    finally:
        assert process.close()


class _FakeProductionWindow:
    def __init__(self, **_kwargs: object) -> None:
        self._controller_process_cadence_downgrade_accepted = False
        self._first_overheating_preflight_decision = None
        self._automation_active = False
        self._automation_paused = False
        self._automation_phase = "idle"
        self._automation_name = "current_sweep_stress"
        self._automation_index = 0
        self._automation_steps = [object(), object()]
        self._current_position_mm = 1.25
        self._supply_snapshot = {"current_mA": 2.0, "voltage_V": 0.4}
        self._supply_effective_readback_hz = 2.0
        self._supply_output_enabled = False
        self._supply_last_setpoint_mA = 2.0
        self._supply_controller = None
        self._scale_thread = object()
        self._tic_controller = object()
        self._tic_command_dispatcher = None
        self._session_active = False
        self._session_points = [object()]
        self._session_base_path = None
        self._session_stop_reason = None
        self._session_stop_detail = None
        self._last_tic_vin_v = 12.0
        self.starting_length_mm = None
        self.closed = False
        self.runtime_update_calls = 0
        self.supply_disable_calls = 0
        self.motor_supply_disable_calls = 0
        self.lifecycle_calls: list[str] = []
        self._preserve_motor_supply_on_close = False
        self._preserve_current_supply_on_close = False
        self._elastocaloric_release_confirmed = False
        self._elastocaloric_prepared_ready = False
        self._elastocaloric_prepared_baseline_mm = None
        self._elastocaloric_prepared_current_mA = None
        self._elastocaloric_continue_prepared_requested = False
        self._control_process_log_sink = None
        self.spin_initial_length = QtWidgets.QDoubleSpinBox()
        self.spin_initial_length.setValue(57.25)
        self.spin_elastocaloric_hold_mA = QtWidgets.QDoubleSpinBox()
        self.spin_elastocaloric_hold_mA.setValue(2.0)
        self.spin_steps_per_mm = QtWidgets.QDoubleSpinBox()
        self.spin_steps_per_mm.setRange(1.0, 100000.0)
        self.spin_steps_per_mm.setValue(800.0)

    def _build_automation_recipe(self) -> tuple[list[object], str, int]:
        return (
            [SimpleNamespace(action="starting_length_prompt"), SimpleNamespace(action="wait")],
            "fake recipe",
            50,
        )

    def _preflight_recipe_hardware(
        self,
        _steps: list[object],
        *,
        show_progress: bool,
    ) -> bool:
        assert show_progress is False
        self.lifecycle_calls.append("hardware_preflight")
        return True

    def _refresh_tic_status(self) -> None:
        return None

    def _refresh_supply_snapshot(self, *, force: bool = False) -> dict[str, float | None]:
        assert force is True
        return dict(self._supply_snapshot)

    def _current_sweep_supply_channel(self) -> int:
        return 4

    def _supply_channel_output_state(self, _channel: int) -> bool:
        return True

    def _has_fresh_scale_reading(self) -> bool:
        return True

    def _latest_ir_snapshot(self) -> dict[str, float]:
        return {"sample_age_s": 0.01}

    def set_length_setup_automation_values(
        self,
        *,
        starting_length_mm: float | None,
        preload_length_mm: float | None,
    ) -> None:
        del preload_length_mm
        self.starting_length_mm = starting_length_mm

    def _start_auto_ramp(self) -> None:
        self.lifecycle_calls.append("recipe_start")
        self._automation_active = True
        self._session_active = True
        self._supply_output_enabled = True
        self._automation_phase = "current"

    def _pause_recipe(self) -> None:
        self._automation_paused = True
        self._supply_output_enabled = False

    def _resume_paused_recipe(self) -> None:
        self._automation_paused = False
        self._supply_output_enabled = True

    def _stop_auto_ramp(self, **_kwargs: object) -> None:
        self._automation_active = False
        self._session_active = False
        self._supply_output_enabled = False

    def _disable_supply_output(self) -> None:
        self.supply_disable_calls += 1
        self._supply_output_enabled = False

    def _disable_motor_supply_output(self) -> None:
        self.motor_supply_disable_calls += 1

    def _apply_current_sweep_pending_overrides(self, *, show_message: bool) -> bool:
        assert show_message is False
        self.runtime_update_calls += 1
        return True

    def _current_effective_load_g(self) -> float:
        return 0.5

    def _is_elastocaloric_mode(self, mode: str | None = None) -> bool:
        return mode == "elastocaloric_effect"

    def _current_distribution_value(self, basis: str) -> float:
        assert basis == "stress_mpa"
        return 25.0

    def _scale_reading_age_s(self) -> float:
        return 0.05

    def _current_task_summary(self) -> str:
        return "fake production task"

    def _capture_live_plot_point(self) -> object:
        @dataclass
        class _PlotPoint:
            elapsed_s: float
            load_g: float

        return _PlotPoint(elapsed_s=1.5, load_g=0.5)

    def _session_stop_metadata(self) -> dict[str, object | None]:
        reason = self._session_stop_reason
        is_fault = reason == "wire_break_or_contact_loss"
        return {
            "reason": reason,
            "category": "fault" if is_fault else "unknown",
            "label": "Wire break or contact loss" if is_fault else "Unknown stop reason",
            "detail": self._session_stop_detail,
        }

    def close(self) -> None:
        self.closed = True


def test_production_backend_owns_recipe_lifecycle_and_readback() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    request = ControlStartRequest(
        identity=_identity(),
        policy=ControlPolicy.PRAGUE,
        config_json=(
            '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
            '"output_collision_action":"replace",'
            '"cadence_downgrade_accepted":true}'
        ),
    )

    backend.start(request)
    assert backend._window.lifecycle_calls == ["hardware_preflight", "recipe_start"]
    assert backend._window._controller_process_output_collision_action == "replace"
    readback = dict(backend.readback())
    assert readback["backend_owner_pid"] == os.getpid()
    assert readback["automation_active"] is True
    assert readback["supply_output_enabled"] is True
    assert readback["stress_mpa"] == pytest.approx(25.0)
    assert readback["plot_elapsed_s"] == pytest.approx(1.5)
    assert readback["plot_load_g"] == pytest.approx(0.5)

    backend.pause()
    assert dict(backend.readback())["automation_paused"] is True
    assert dict(backend.readback())["supply_output_enabled"] is False
    backend.resume()
    assert dict(backend.readback())["supply_output_enabled"] is True
    accepted, detail = backend.update_config(
        '{"schema_version":1,"runtime_update":true,"widgets":{}}'
    )
    assert accepted is True
    assert detail == "current-sweep runtime settings applied"
    backend.stop()
    assert backend.completion_detail() == "production recipe completed"
    assert dict(backend.readback())["supply_output_enabled"] is False
    assert backend._window._preserve_motor_supply_on_close is True
    assert backend._window.motor_supply_disable_calls == 0
    backend.close()


def test_production_backend_preserves_run_relative_terminal_readback() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    backend.start(
        ControlStartRequest(
            identity=_identity(),
            policy=ControlPolicy.PRAGUE,
            config_json=(
                '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
                '"output_collision_action":"replace",'
                '"cadence_downgrade_accepted":true}'
            ),
        )
    )
    backend._window._recipe_terminal_readback = {
        "load_g": 0.005,
        "stress_mpa": 0.16,
        "plot_elapsed_s": 88.5,
        "plot_load_g": 0.005,
    }
    backend._window._automation_active = False

    readback = dict(backend.readback())

    assert readback["load_g"] == pytest.approx(0.005)
    assert readback["stress_mpa"] == pytest.approx(0.16)
    assert readback["plot_elapsed_s"] == pytest.approx(88.5)
    assert readback["plot_load_g"] == pytest.approx(0.005)
    backend.close()


def test_production_backend_preserves_elastocaloric_current_after_confirmed_release() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    request = ControlStartRequest(
        identity=_identity(),
        policy=ControlPolicy.PRAGUE,
        config_json=(
            '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
            '"output_collision_action":"replace",'
            '"cadence_downgrade_accepted":true}'
        ),
    )

    backend.start(request)
    backend._window._automation_name = "elastocaloric_effect"
    backend._window._elastocaloric_release_confirmed = True
    backend._window._automation_active = False

    assert backend.completion_detail() == "production recipe completed"
    assert backend._window._preserve_current_supply_on_close is True
    readback = dict(backend.readback())
    assert readback["elastocaloric_release_confirmed"] is True
    assert readback["preserve_current_supply_on_close"] is True
    backend.close()


def test_production_backend_reuses_prepared_elastocaloric_window_without_preflight() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    backend.start(
        ControlStartRequest(
            identity=_identity(),
            policy=ControlPolicy.PRAGUE,
            config_json=(
                '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
                '"output_collision_action":"replace","cadence_downgrade_accepted":true}'
            ),
        )
    )
    window = backend._window
    window._automation_active = False
    window._session_active = False
    window._elastocaloric_prepared_ready = True
    window._elastocaloric_prepared_baseline_mm = 1.25
    window._elastocaloric_prepared_current_mA = 2.0
    backend._stopped = True

    backend.start(
        ControlStartRequest(
            identity=ControlSessionIdentity("prepared-next", 2),
            policy=ControlPolicy.PRAGUE,
            config_json=(
                '{"schema_version":1,"widgets":{},'
                '"continue_prepared_elastocaloric":true}'
            ),
        )
    )

    assert backend._window is window
    assert window.lifecycle_calls == [
        "hardware_preflight",
        "recipe_start",
        "recipe_start",
    ]
    assert window._elastocaloric_continue_prepared_requested is True
    backend.close()


def test_production_backend_runs_stationary_thermal_response_as_prepared_recipe() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    backend.start(
        ControlStartRequest(
            identity=_identity(),
            policy=ControlPolicy.PRAGUE,
            config_json=(
                '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
                '"output_collision_action":"replace","cadence_downgrade_accepted":true}'
            ),
        )
    )
    window = backend._window
    window._automation_active = False
    window._session_active = False
    window._elastocaloric_prepared_ready = True
    window._elastocaloric_prepared_baseline_mm = 1.25
    window._elastocaloric_prepared_current_mA = 2.0
    backend._stopped = True

    backend.start(
        ControlStartRequest(
            identity=ControlSessionIdentity("prepared-thermal", 2),
            policy=ControlPolicy.PRAGUE,
            config_json=(
                '{"schema_version":1,"widgets":{},'
                '"continue_prepared_elastocaloric":true,'
                '"thermal_response_diagnostic":{"baseline_s":5.0,'
                '"step_down_mA":0.5,"low_hold_s":5.0,"recovery_s":5.0,'
                '"cycles":3,"roi_pixel_count":8}}'
            ),
        )
    )

    assert window._thermal_response_diagnostic_config["cycles"] == 3
    assert window._thermal_response_roi_indices == ()
    assert window._elastocaloric_release_confirmed is True
    assert window._current_position_mm == pytest.approx(1.25)
    backend.close()


def test_production_backend_marks_stationary_preparation_reusable() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)

    backend.start(
        ControlStartRequest(
            identity=_identity(),
            policy=ControlPolicy.PRAGUE,
            config_json=(
                '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
                '"output_collision_action":"replace","cadence_downgrade_accepted":true,'
                '"stationary_thermal_preparation":{"target_current_mA":30.0,'
                '"ramp_rate_mA_s":5.0,"baseline_s":5.0}}'
            ),
        )
    )

    window = backend._window
    assert window._stationary_thermal_preparation_config["target_current_mA"] == 30.0
    assert window._elastocaloric_prepared_baseline_mm == pytest.approx(1.25)
    assert window._elastocaloric_prepared_current_mA == pytest.approx(30.0)
    assert window._elastocaloric_release_confirmed is True
    backend.close()


def test_prepared_elastocaloric_rejection_does_not_trigger_output_shutdown() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    backend.start(
        ControlStartRequest(
            identity=_identity(),
            policy=ControlPolicy.PRAGUE,
            config_json=(
                '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
                '"output_collision_action":"replace","cadence_downgrade_accepted":true}'
            ),
        )
    )
    window = backend._window
    window._automation_active = False
    window._session_active = False
    window._elastocaloric_prepared_ready = True
    window._elastocaloric_prepared_baseline_mm = 1.25
    window._elastocaloric_prepared_current_mA = 30.0
    window.spin_elastocaloric_hold_mA.setValue(30.0)
    backend._stopped = True

    with pytest.raises(ControlStartRejected, match="CH4 current"):
        backend.start(
            ControlStartRequest(
                identity=ControlSessionIdentity("prepared-rejected", 2),
                policy=ControlPolicy.PRAGUE,
                config_json=(
                    '{"schema_version":1,"widgets":{},'
                    '"continue_prepared_elastocaloric":true}'
                ),
            )
        )

    assert window.supply_disable_calls == 0
    assert window.motor_supply_disable_calls == 0
    backend.close()


def test_production_backend_exposes_bounded_latest_thermal_preview() -> None:
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    frame = ThermalFrame(
        elapsed_ms=125,
        ambient_c=23.5,
        values=(20.0, 21.0, 22.0, 23.0),
        raw_read_us=11000,
        sequence=7,
        width=2,
        height=2,
    )
    window = SimpleNamespace(_latest_ir_frame=frame)

    first = backend._latest_ir_preview_json(window)
    second = backend._latest_ir_preview_json(window)
    payload = json.loads(first)

    assert second is first
    assert payload["sequence"] == 7
    assert payload["width"] == 2
    assert payload["height"] == 2
    assert payload["values"] == [20.0, 21.0, 22.0, 23.0]


def test_production_backend_exposes_wire_break_terminal_metadata() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    backend.start(
        ControlStartRequest(
            identity=_identity(),
            policy=ControlPolicy.PRAGUE,
            config_json=(
                '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
                '"output_collision_action":"replace",'
                '"cadence_downgrade_accepted":true}'
            ),
        )
    )
    backend._window._session_stop_reason = "wire_break_or_contact_loss"
    backend._window._session_stop_detail = "synthetic wire break"
    backend._window._automation_active = False

    readback = dict(backend.readback())

    assert readback["session_stop_reason"] == "wire_break_or_contact_loss"
    assert readback["session_stop_category"] == "fault"
    assert readback["session_stop_label"] == "Wire break or contact loss"
    assert readback["session_stop_detail"] == "synthetic wire break"
    backend.close()


def test_production_backend_emergency_does_not_preserve_motor_supply() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    backend.start(
        ControlStartRequest(
            identity=_identity(),
            policy=ControlPolicy.PRAGUE,
            config_json=(
                '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
                '"output_collision_action":"replace",'
                '"cadence_downgrade_accepted":true}'
            ),
        )
    )

    backend.emergency_stop("synthetic emergency")

    assert backend._window._preserve_motor_supply_on_close is False
    assert backend._window.motor_supply_disable_calls == 1
    assert backend._window.supply_disable_calls == 1
    backend.close()


def test_production_backend_natural_completion_preserves_motor_supply() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    backend.start(
        ControlStartRequest(
            identity=_identity(),
            policy=ControlPolicy.PRAGUE,
            config_json=(
                '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
                '"output_collision_action":"replace",'
                '"cadence_downgrade_accepted":true}'
            ),
        )
    )

    backend._window._automation_active = False

    assert backend.completion_detail() == "production recipe completed"
    assert backend._window._preserve_motor_supply_on_close is True
    backend.close()


def test_production_backend_rejects_policy_profile_mismatch() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app

    class _KosiceWindow(_FakeProductionWindow):
        @staticmethod
        def _force_control_profile() -> object:
            return SimpleNamespace(value="kosice_adaptive")

    backend = ProductionMiniDmaBackend(window_factory=_KosiceWindow)

    with pytest.raises(ValueError, match="IPC policy does not match"):
        backend.start(
            ControlStartRequest(
                identity=_identity(),
                policy=ControlPolicy.PRAGUE,
                config_json=(
                    '{"schema_version":1,"widgets":{},'
                    '"starting_length_mm":57.25,'
                    '"output_collision_action":"replace",'
                    '"cadence_downgrade_accepted":true}'
                ),
            )
        )
    backend.close()


def test_production_backend_validates_kosice_policy_after_hardware_probe() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app

    class _DetectedKosiceWindow(_FakeProductionWindow):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self.profile = "prague_legacy"

        def _force_control_profile(self) -> object:
            return SimpleNamespace(value=self.profile)

        def _preflight_recipe_hardware(
            self,
            steps: list[object],
            *,
            show_progress: bool,
        ) -> bool:
            accepted = super()._preflight_recipe_hardware(
                steps,
                show_progress=show_progress,
            )
            self.profile = "kosice_adaptive"
            return accepted

    backend = ProductionMiniDmaBackend(window_factory=_DetectedKosiceWindow)
    backend.start(
        ControlStartRequest(
            identity=_identity(),
            policy=ControlPolicy.KOSICE,
            config_json=(
                '{"schema_version":1,"widgets":{},'
                '"starting_length_mm":57.25,'
                '"output_collision_action":"replace",'
                '"cadence_downgrade_accepted":true}'
            ),
        )
    )

    assert backend._window.lifecycle_calls == ["hardware_preflight", "recipe_start"]
    backend.close()


def test_production_backend_readback_exposes_bounded_ui_log_tail() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    backend.start(
        ControlStartRequest(
            identity=_identity(),
            policy=ControlPolicy.PRAGUE,
            config_json=(
                '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
                '"output_collision_action":"replace",'
                '"cadence_downgrade_accepted":true}'
            ),
        )
    )

    for index in range(40):
        backend._window._control_process_log_sink(f"[00:00:00] child line {index}")

    readback = dict(backend.readback())
    tail = json.loads(str(readback["ui_log_tail_json"]))
    assert readback["ui_log_sequence"] == 40
    assert len(tail) == 32
    assert tail[0] == [9, "[00:00:00] child line 8"]
    assert tail[-1] == [40, "[00:00:00] child line 39"]
    backend.close()


def test_production_backend_real_window_starts_next_run_without_child_dialog(
    tmp_path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from data_logging.mini_dma_logger import mini_dma_logger as mini_dma_mod

    source = mini_dma_mod.MainWindow(
        log_dir=str(tmp_path),
        persist_settings=False,
        control_process_enabled=False,
    )
    qtbot.addWidget(source)
    source.edit_log_name.setText("existing_sample")
    source._controller_process_output_collision_action = mini_dma_mod.OUTPUT_COLLISION_NEXT
    source.check_zero_position_on_start.setChecked(False)
    source.check_tare_on_start.setChecked(False)
    (tmp_path / "existing_sample").mkdir()
    (tmp_path / "existing_sample" / mini_dma_mod.SESSION_METADATA_JSON).write_text(
        '{"session_state":"finished"}',
        encoding="utf-8",
    )
    created: list[mini_dma_mod.MainWindow] = []

    def _factory(**_kwargs: object) -> mini_dma_mod.MainWindow:
        window = mini_dma_mod.MainWindow(
            log_dir=str(tmp_path),
            persist_settings=False,
            control_process_enabled=False,
            controller_process_mode=True,
        )
        qtbot.addWidget(window)
        created.append(window)
        window._build_automation_recipe = lambda: (  # type: ignore[method-assign]
            [mini_dma_mod.AutomationStep("wait", duration_s=1.0)],
            "Synthetic production recipe",
            50,
        )
        window._preflight_recipe_hardware = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
        window._prepare_continuity_current_for_recipe = lambda _steps: True  # type: ignore[method-assign]
        window._start_automation_control_loop = lambda _interval_ms: None  # type: ignore[method-assign]
        window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
        return window

    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QMessageBox,
        "exec",
        lambda *_args, **_kwargs: pytest.fail(
            "controller child must use the transferred output choice"
        ),
    )
    payload = capture_window_configuration(
        source,
        starting_length_mm=57.522,
        cadence_downgrade_accepted=True,
    )
    backend = ProductionMiniDmaBackend(window_factory=_factory)

    try:
        backend.start(
            ControlStartRequest(
                identity=_identity(),
                policy=ControlPolicy.PRAGUE,
                config_json=payload,
            )
        )

        assert created
        child = created[0]
        assert child.spin_initial_length.value() == pytest.approx(57.522)
        assert child._automation_active is True
        assert child._session_active is True
        assert child.edit_log_name.text() == "existing_sample_run02"
        assert (tmp_path / "existing_sample_run02" / "measurement.csv").exists()
        assert (
            child._controller_process_output_collision_action
            == mini_dma_mod.OUTPUT_COLLISION_NEXT
        )
    finally:
        if created:
            created[0]._automation_active = False
        backend.close()
        source.close()


def test_production_backend_rejects_missing_ui_collected_starting_length() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    request = ControlStartRequest(
        identity=_identity(),
        policy=ControlPolicy.PRAGUE,
        config_json=(
            '{"schema_version":1,"widgets":{},"starting_length_mm":null,'
            '"prior_run_preflight_complete":true,'
            '"cadence_downgrade_accepted":true}'
        ),
    )

    with pytest.raises(
        ValueError,
        match="must be collected by the visible UI",
    ):
        backend.start(request)

    assert backend._window.lifecycle_calls == ["hardware_preflight"]
    assert dict(backend.readback())["automation_active"] is False
    backend.close()


def test_production_backend_rejects_start_when_child_hardware_preflight_fails() -> None:
    class _FailingPreflightWindow(_FakeProductionWindow):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.log_output = QtWidgets.QPlainTextEdit()
            self.log_output.setPlainText(
                "Preflight: trying COM6\nScale connection failed: synthetic timeout"
            )

        def _preflight_recipe_hardware(
            self,
            _steps: list[object],
            *,
            show_progress: bool,
        ) -> bool:
            assert show_progress is False
            self.lifecycle_calls.append("hardware_preflight")
            self._controller_process_error = "synthetic scale connection failure"
            return False

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FailingPreflightWindow)
    request = ControlStartRequest(
        identity=_identity(),
        policy=ControlPolicy.PRAGUE,
        config_json=(
            '{"schema_version":1,"widgets":{},"starting_length_mm":null,'
            '"prior_run_preflight_complete":true,'
            '"cadence_downgrade_accepted":true}'
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="synthetic scale connection failure.*Scale connection failed",
    ):
        backend.start(request)

    assert backend._window.lifecycle_calls == ["hardware_preflight"]
    assert dict(backend.readback())["automation_active"] is False
    backend.close()


def test_production_backend_rejects_non_runtime_configuration_update() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    del app
    backend = ProductionMiniDmaBackend(window_factory=_FakeProductionWindow)
    request = ControlStartRequest(
        identity=_identity(),
        policy=ControlPolicy.PRAGUE,
        config_json=(
            '{"schema_version":1,"widgets":{},"starting_length_mm":57.25,'
            '"prior_run_preflight_complete":true,'
            '"cadence_downgrade_accepted":true}'
        ),
    )
    backend.start(request)

    accepted, detail = backend.update_config(
        '{"schema_version":1,"widgets":{}}'
    )

    assert accepted is False
    assert "not marked as runtime-safe" in detail
    backend.close()


def test_capture_window_configuration_is_json_and_does_not_retain_qt_objects(
    qapp: QtWidgets.QApplication,
) -> None:
    assert qapp is not None
    class _Window:
        def __init__(self) -> None:
            self.current = QtWidgets.QDoubleSpinBox()
            self.current.setValue(12.5)
            self.count = QtWidgets.QSpinBox()
            self.count.setValue(7)
            self.mode = QtWidgets.QComboBox()
            self.mode.addItem("Prague", "prague")
            self.enabled = QtWidgets.QCheckBox()
            self.enabled.setChecked(True)
            self.name = QtWidgets.QLineEdit("sample")
            self._first_overheating_preflight_decision = {"action": "continue"}
            self._controller_process_output_collision_action = "next"

    payload = capture_window_configuration(
        _Window(),
        starting_length_mm=57.0,
        cadence_downgrade_accepted=True,
    )
    assert '"starting_length_mm":57.0' in payload
    assert '"prior_run_preflight_complete":true' in payload
    assert '"output_collision_action":"next"' in payload
    assert '"kind":"integer_spin","value":7' in payload
    assert '"kind":"decimal_spin","value":12.5' in payload
    assert '"value":12.5' in payload
    assert '"data":"prague"' in payload
    assert "PyQt6" not in payload


def test_window_configuration_round_trip_preserves_qt_spin_box_types(
    qapp: QtWidgets.QApplication,
) -> None:
    assert qapp is not None

    class _Window:
        def __init__(self) -> None:
            self.count = QtWidgets.QSpinBox()
            self.current = QtWidgets.QDoubleSpinBox()
            self._first_overheating_preflight_decision = None

    source = _Window()
    source.count.setValue(17)
    source.current.setValue(12.5)
    payload = json.loads(
        capture_window_configuration(
            source,
            starting_length_mm=None,
            cadence_downgrade_accepted=True,
        )
    )
    target = _Window()

    _apply_window_configuration(target, payload)

    assert target.count.value() == 17
    assert target.current.value() == pytest.approx(12.5)


def test_window_configuration_round_trip_restores_unenumerated_serial_ports(
    qapp: QtWidgets.QApplication,
) -> None:
    assert qapp is not None

    class _Window:
        def __init__(self) -> None:
            self.combo_scale_port = QtWidgets.QComboBox()
            self.combo_supply_port = QtWidgets.QComboBox()
            self._first_overheating_preflight_decision = None

    source = _Window()
    source.combo_scale_port.addItem("COM6 - saved scale", "COM6")
    source.combo_supply_port.addItem("COM5 - HMP USB", "COM5")
    payload = json.loads(
        capture_window_configuration(
            source,
            starting_length_mm=None,
            cadence_downgrade_accepted=True,
        )
    )
    target = _Window()

    _apply_window_configuration(target, payload)

    assert target.combo_scale_port.currentData() == "COM6"
    assert target.combo_supply_port.currentData() == "COM5"


def test_real_tma_window_configuration_round_trip_accepts_all_widget_types(
    tmp_path,
    qtbot,
) -> None:
    from data_logging.mini_dma_logger.mini_dma_logger import MainWindow

    source = MainWindow(
        log_dir=str(tmp_path / "source"),
        persist_settings=False,
        control_process_enabled=False,
    )
    target = MainWindow(
        log_dir=str(tmp_path / "target"),
        persist_settings=False,
        control_process_enabled=False,
        controller_process_mode=True,
    )
    qtbot.addWidget(source)
    qtbot.addWidget(target)
    payload = json.loads(
        capture_window_configuration(
            source,
            starting_length_mm=None,
            cadence_downgrade_accepted=True,
        )
    )

    _apply_window_configuration(target, payload)

    integer_widgets = [
        candidate
        for candidate in vars(source).values()
        if isinstance(candidate, QtWidgets.QSpinBox)
    ]
    decimal_widgets = [
        candidate
        for candidate in vars(source).values()
        if isinstance(candidate, QtWidgets.QDoubleSpinBox)
    ]
    assert integer_widgets
    assert decimal_widgets
    source.close()
    target.close()
