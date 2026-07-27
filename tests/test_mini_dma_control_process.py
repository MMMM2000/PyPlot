from __future__ import annotations

from dataclasses import dataclass, FrozenInstanceError
import json
import os
import time
from types import SimpleNamespace

import pytest
from PyQt6 import QtWidgets

from data_logging.mini_dma_logger.control_process import (
    ControlBackpressureError,
    ControlEvent,
    ControlEventKind,
    ControlPolicy,
    ControlSessionIdentity,
    ControlSnapshot,
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
        self._last_tic_vin_v = 12.0
        self.starting_length_mm = None
        self.closed = False
        self.runtime_update_calls = 0
        self.supply_disable_calls = 0
        self.motor_supply_disable_calls = 0
        self.lifecycle_calls: list[str] = []
        self.spin_initial_length = QtWidgets.QDoubleSpinBox()
        self.spin_initial_length.setValue(57.25)

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
            '"cadence_downgrade_accepted":true}'
        ),
    )

    backend.start(request)
    assert backend._window.lifecycle_calls == ["hardware_preflight", "recipe_start"]
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
    backend.close()


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

    payload = capture_window_configuration(
        _Window(),
        starting_length_mm=57.0,
        cadence_downgrade_accepted=True,
    )
    assert '"starting_length_mm":57.0' in payload
    assert '"prior_run_preflight_complete":true' in payload
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
