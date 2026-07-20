from __future__ import annotations

from dataclasses import FrozenInstanceError
import os
import time

import pytest

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
