"""Spawn-safe process and IPC foundation for authoritative TMA control.

This module deliberately has no Qt, serial, Tic, or PSU imports.  Production
hardware is not wired to it yet; the simulated backend exists so lifecycle and
IPC invariants can be proved before device ownership moves out of the UI
process.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import multiprocessing
import os
from queue import Empty, Full
from threading import Event as ThreadEvent, Lock, Thread
import time
from typing import Any, Protocol


class ControlPolicy(str, Enum):
    PRAGUE = "prague"
    KOSICE = "kosice"


class ControlState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    EMERGENCY = "emergency"
    FAULTED = "faulted"


class ControlCommandKind(str, Enum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"


class ControlEventKind(str, Enum):
    READY = "ready"
    STARTED = "started"
    PAUSED = "paused"
    RESUMED = "resumed"
    STOPPED = "stopped"
    RECIPE_COMPLETE = "recipe_complete"
    EMERGENCY = "emergency"
    FAULT = "fault"
    COMMAND_REJECTED = "command_rejected"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class ControlSessionIdentity:
    session_id: str
    generation: int

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id must not be empty")
        if self.generation <= 0:
            raise ValueError("generation must be positive")


@dataclass(frozen=True, slots=True)
class ControlStartRequest:
    identity: ControlSessionIdentity
    policy: ControlPolicy
    control_interval_s: float = 0.02
    snapshot_interval_s: float = 0.10
    parent_heartbeat_timeout_s: float = 2.0
    recipe_tick_limit: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy, ControlPolicy):
            raise ValueError("policy must be an explicit ControlPolicy")
        if self.control_interval_s <= 0.0:
            raise ValueError("control_interval_s must be positive")
        if self.snapshot_interval_s <= 0.0:
            raise ValueError("snapshot_interval_s must be positive")
        if self.parent_heartbeat_timeout_s <= 0.0:
            raise ValueError("parent_heartbeat_timeout_s must be positive")
        if self.recipe_tick_limit is not None and self.recipe_tick_limit <= 0:
            raise ValueError("recipe_tick_limit must be positive when provided")


@dataclass(frozen=True, slots=True)
class ControlCommand:
    kind: ControlCommandKind
    sequence: int
    identity: ControlSessionIdentity
    start_request: ControlStartRequest | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ControlCommandKind):
            raise ValueError("kind must be an explicit ControlCommandKind")
        if self.sequence <= 0:
            raise ValueError("command sequence must be positive")
        if self.kind is ControlCommandKind.START:
            if self.start_request is None:
                raise ValueError("start command requires a start_request")
            if self.start_request.identity != self.identity:
                raise ValueError("start command identities do not match")
        elif self.start_request is not None:
            raise ValueError("only start commands may carry a start_request")


ReadbackValue = float | int | str | bool | None


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    identity: ControlSessionIdentity | None
    state: ControlState
    sequence: int
    monotonic_s: float
    tick_count: int
    last_command_sequence: int
    policy: ControlPolicy | None
    owner_pid: int
    dropped_event_count: int
    readback: tuple[tuple[str, ReadbackValue], ...]

    def readback_value(self, name: str) -> ReadbackValue:
        return dict(self.readback).get(name)


@dataclass(frozen=True, slots=True)
class ControlEvent:
    kind: ControlEventKind
    sequence: int
    identity: ControlSessionIdentity | None
    monotonic_s: float
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SimulatedBackendConfig:
    """Deterministic fake-driver settings used only for software verification."""

    crash_after_ticks: int | None = None

    def __post_init__(self) -> None:
        if self.crash_after_ticks is not None and self.crash_after_ticks <= 0:
            raise ValueError("crash_after_ticks must be positive when provided")


class ControlBackend(Protocol):
    def start(self, request: ControlStartRequest) -> None: ...

    def tick(self, now_s: float) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None: ...

    def emergency_stop(self, reason: str) -> None: ...

    def readback(self) -> tuple[tuple[str, ReadbackValue], ...]: ...

    def close(self) -> None: ...


class SimulatedControlBackend:
    """Small process-owned fake for lifecycle, timing, and safety tests."""

    def __init__(self, config: SimulatedBackendConfig) -> None:
        self._config = config
        self._owner_pid = os.getpid()
        self._policy: ControlPolicy | None = None
        self._tick_count = 0
        self._output_enabled = False
        self._emergency_reason = ""

    def start(self, request: ControlStartRequest) -> None:
        self._policy = request.policy
        self._tick_count = 0
        self._output_enabled = True
        self._emergency_reason = ""

    def tick(self, now_s: float) -> None:
        del now_s
        next_tick = self._tick_count + 1
        if self._config.crash_after_ticks == next_tick:
            raise RuntimeError(f"simulated backend failure at tick {next_tick}")
        self._tick_count = next_tick

    def pause(self) -> None:
        self._output_enabled = False

    def resume(self) -> None:
        self._output_enabled = True

    def stop(self) -> None:
        self._output_enabled = False

    def emergency_stop(self, reason: str) -> None:
        self._output_enabled = False
        self._emergency_reason = str(reason)

    def readback(self) -> tuple[tuple[str, ReadbackValue], ...]:
        return (
            ("backend_owner_pid", self._owner_pid),
            ("backend_tick_count", self._tick_count),
            ("output_enabled", self._output_enabled),
            ("emergency_reason", self._emergency_reason),
            ("policy", None if self._policy is None else self._policy.value),
        )

    def close(self) -> None:
        self._output_enabled = False


class ControlBackpressureError(RuntimeError):
    """Raised when the bounded operator-command channel is full."""


def _replace_latest(channel: Any, value: object) -> None:
    """Put one latest-value item without ever blocking the producer."""

    try:
        channel.put_nowait(value)
        return
    except Full:
        pass
    try:
        channel.get_nowait()
    except Empty:
        pass
    try:
        channel.put_nowait(value)
    except Full:
        # A competing producer won the slot; that value is at least as new.
        pass


class _ControlProcessRuntime:
    def __init__(
        self,
        *,
        command_queue: Any,
        heartbeat_queue: Any,
        snapshot_queue: Any,
        event_queue: Any,
        emergency_event: Any,
        shutdown_event: Any,
        backend_config: SimulatedBackendConfig,
    ) -> None:
        self._command_queue = command_queue
        self._heartbeat_queue = heartbeat_queue
        self._snapshot_queue = snapshot_queue
        self._event_queue = event_queue
        self._emergency_event = emergency_event
        self._shutdown_event = shutdown_event
        self._backend: ControlBackend = SimulatedControlBackend(backend_config)
        self._identity: ControlSessionIdentity | None = None
        self._request: ControlStartRequest | None = None
        self._state = ControlState.IDLE
        self._tick_count = 0
        self._last_command_sequence = 0
        self._snapshot_sequence = 0
        self._event_sequence = 0
        self._dropped_event_count = 0
        self._highest_generation_by_session: dict[str, int] = {}
        self._last_parent_heartbeat_s = time.monotonic()
        self._next_tick_s = time.monotonic()
        self._next_snapshot_s = time.monotonic()

    def run(self) -> None:
        self._emit_event(ControlEventKind.READY)
        self._publish_snapshot()
        try:
            while True:
                now_s = time.monotonic()
                self._drain_heartbeat()
                if self._emergency_event.is_set():
                    self._emergency_event.clear()
                    self._enter_emergency("operator emergency request")
                if self._shutdown_event.is_set():
                    self._safe_shutdown("supervisor shutdown")
                    return
                self._check_parent_heartbeat(now_s)

                timeout_s = 0.01
                if self._state is ControlState.RUNNING:
                    timeout_s = min(timeout_s, max(0.0, self._next_tick_s - now_s))
                try:
                    command = self._command_queue.get(timeout=timeout_s)
                except Empty:
                    command = None
                if command is not None:
                    self._handle_command(command)

                now_s = time.monotonic()
                if self._state is ControlState.RUNNING and now_s >= self._next_tick_s:
                    self._backend.tick(now_s)
                    self._tick_count += 1
                    request = self._request
                    if request is not None and request.recipe_tick_limit == self._tick_count:
                        self._backend.stop()
                        self._state = ControlState.STOPPED
                        self._emit_event(ControlEventKind.RECIPE_COMPLETE)
                        self._publish_snapshot()
                    else:
                        interval_s = 0.02 if request is None else request.control_interval_s
                        self._next_tick_s = max(self._next_tick_s + interval_s, now_s)
                if now_s >= self._next_snapshot_s:
                    self._publish_snapshot()
        except BaseException as exc:
            self._state = ControlState.FAULTED
            try:
                self._backend.emergency_stop(f"control process fault: {exc}")
            finally:
                self._emit_event(ControlEventKind.FAULT, detail=str(exc) or exc.__class__.__name__)
                self._publish_snapshot()
            raise SystemExit(1) from None
        finally:
            self._backend.close()

    def _drain_heartbeat(self) -> None:
        latest_s: float | None = None
        while True:
            try:
                latest_s = float(self._heartbeat_queue.get_nowait())
            except Empty:
                break
        if latest_s is not None:
            self._last_parent_heartbeat_s = latest_s

    def _check_parent_heartbeat(self, now_s: float) -> None:
        request = self._request
        if request is None or self._state not in {ControlState.RUNNING, ControlState.PAUSED}:
            return
        if now_s - self._last_parent_heartbeat_s > request.parent_heartbeat_timeout_s:
            self._enter_emergency("parent heartbeat timeout")

    def _handle_command(self, command: object) -> None:
        if not isinstance(command, ControlCommand):
            self._reject("invalid command payload")
            return
        if command.sequence <= self._last_command_sequence:
            self._reject("non-monotonic command sequence", identity=command.identity)
            return
        self._last_command_sequence = command.sequence
        if command.kind is ControlCommandKind.START:
            self._handle_start(command)
            return
        if command.identity != self._identity:
            self._reject("stale or unrelated session identity", identity=command.identity)
            return
        if command.kind is ControlCommandKind.PAUSE:
            if self._state is not ControlState.RUNNING:
                self._reject("pause requires a running session", identity=command.identity)
                return
            self._backend.pause()
            self._state = ControlState.PAUSED
            self._emit_event(ControlEventKind.PAUSED)
        elif command.kind is ControlCommandKind.RESUME:
            if self._state is not ControlState.PAUSED:
                self._reject("resume requires a paused session", identity=command.identity)
                return
            self._backend.resume()
            self._state = ControlState.RUNNING
            self._next_tick_s = time.monotonic()
            self._emit_event(ControlEventKind.RESUMED)
        elif command.kind is ControlCommandKind.STOP:
            if self._state not in {ControlState.RUNNING, ControlState.PAUSED}:
                self._reject("stop requires an active session", identity=command.identity)
                return
            self._backend.stop()
            self._state = ControlState.STOPPED
            self._emit_event(ControlEventKind.STOPPED)
        self._publish_snapshot()

    def _handle_start(self, command: ControlCommand) -> None:
        request = command.start_request
        assert request is not None
        if self._state not in {ControlState.IDLE, ControlState.STOPPED}:
            self._reject("start requires an idle or stopped process", identity=command.identity)
            return
        previous_generation = self._highest_generation_by_session.get(
            request.identity.session_id,
            0,
        )
        if request.identity.generation <= previous_generation:
            self._reject("start generation is stale", identity=command.identity)
            return
        self._identity = request.identity
        self._request = request
        self._highest_generation_by_session[request.identity.session_id] = (
            request.identity.generation
        )
        self._tick_count = 0
        self._backend.start(request)
        self._state = ControlState.RUNNING
        now_s = time.monotonic()
        self._last_parent_heartbeat_s = now_s
        self._next_tick_s = now_s
        self._next_snapshot_s = now_s
        self._emit_event(ControlEventKind.STARTED)
        self._publish_snapshot()

    def _enter_emergency(self, reason: str) -> None:
        if self._state in {ControlState.EMERGENCY, ControlState.FAULTED}:
            return
        self._backend.emergency_stop(reason)
        self._state = ControlState.EMERGENCY
        self._emit_event(ControlEventKind.EMERGENCY, detail=reason)
        self._publish_snapshot()

    def _safe_shutdown(self, reason: str) -> None:
        if self._state in {ControlState.RUNNING, ControlState.PAUSED}:
            self._backend.emergency_stop(reason)
            self._state = ControlState.STOPPED
        self._emit_event(ControlEventKind.SHUTDOWN, detail=reason)
        self._publish_snapshot()

    def _reject(
        self,
        detail: str,
        *,
        identity: ControlSessionIdentity | None = None,
    ) -> None:
        self._emit_event(ControlEventKind.COMMAND_REJECTED, detail=detail, identity=identity)

    def _emit_event(
        self,
        kind: ControlEventKind,
        *,
        detail: str = "",
        identity: ControlSessionIdentity | None = None,
    ) -> None:
        self._event_sequence += 1
        event = ControlEvent(
            kind=kind,
            sequence=self._event_sequence,
            identity=self._identity if identity is None else identity,
            monotonic_s=time.monotonic(),
            detail=detail,
        )
        try:
            self._event_queue.put_nowait(event)
            return
        except Full:
            self._dropped_event_count += 1
        try:
            self._event_queue.get_nowait()
        except Empty:
            pass
        try:
            self._event_queue.put_nowait(event)
        except Full:
            self._dropped_event_count += 1

    def _publish_snapshot(self) -> None:
        self._snapshot_sequence += 1
        now_s = time.monotonic()
        snapshot = ControlSnapshot(
            identity=self._identity,
            state=self._state,
            sequence=self._snapshot_sequence,
            monotonic_s=now_s,
            tick_count=self._tick_count,
            last_command_sequence=self._last_command_sequence,
            policy=None if self._request is None else self._request.policy,
            owner_pid=os.getpid(),
            dropped_event_count=self._dropped_event_count,
            readback=self._backend.readback(),
        )
        _replace_latest(self._snapshot_queue, snapshot)
        request = self._request
        interval_s = 0.1 if request is None else request.snapshot_interval_s
        self._next_snapshot_s = now_s + interval_s


def _run_control_process(
    command_queue: Any,
    heartbeat_queue: Any,
    snapshot_queue: Any,
    event_queue: Any,
    emergency_event: Any,
    shutdown_event: Any,
    backend_config: SimulatedBackendConfig,
) -> None:
    runtime = _ControlProcessRuntime(
        command_queue=command_queue,
        heartbeat_queue=heartbeat_queue,
        snapshot_queue=snapshot_queue,
        event_queue=event_queue,
        emergency_event=emergency_event,
        shutdown_event=shutdown_event,
        backend_config=backend_config,
    )
    runtime.run()


class MiniDmaControlProcess:
    """UI-side supervisor for the bounded process-control channels."""

    def __init__(
        self,
        *,
        backend_config: SimulatedBackendConfig | None = None,
        command_capacity: int = 32,
        event_capacity: int = 64,
        heartbeat_interval_s: float | None = 0.10,
        mp_context: str = "spawn",
    ) -> None:
        if command_capacity <= 0 or event_capacity <= 0:
            raise ValueError("IPC capacities must be positive")
        if heartbeat_interval_s is not None and heartbeat_interval_s <= 0.0:
            raise ValueError("heartbeat_interval_s must be positive when provided")
        self._context = multiprocessing.get_context(mp_context)
        self._command_queue = self._context.Queue(maxsize=command_capacity)
        self._heartbeat_queue = self._context.Queue(maxsize=1)
        self._snapshot_queue = self._context.Queue(maxsize=1)
        self._event_queue = self._context.Queue(maxsize=event_capacity)
        self._emergency_event = self._context.Event()
        self._shutdown_event = self._context.Event()
        self._backend_config = backend_config or SimulatedBackendConfig()
        self._heartbeat_interval_s = heartbeat_interval_s
        self._heartbeat_stop = ThreadEvent()
        self._heartbeat_thread: Thread | None = None
        self._process: multiprocessing.Process | None = None
        self._command_sequence = 0
        self._sequence_lock = Lock()

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def exitcode(self) -> int | None:
        return None if self._process is None else self._process.exitcode

    def is_alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def start_process(self) -> None:
        if self._process is not None:
            raise RuntimeError("control process was already started")
        self._process = self._context.Process(
            target=_run_control_process,
            name="MiniDMAControlProcess",
            args=(
                self._command_queue,
                self._heartbeat_queue,
                self._snapshot_queue,
                self._event_queue,
                self._emergency_event,
                self._shutdown_event,
                self._backend_config,
            ),
        )
        self._process.start()
        if self._heartbeat_interval_s is not None:
            self._heartbeat_thread = Thread(
                target=self._heartbeat_loop,
                name="MiniDMAControlHeartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()

    def start_session(self, request: ControlStartRequest) -> int:
        return self._send(ControlCommandKind.START, request.identity, start_request=request)

    def pause(self, identity: ControlSessionIdentity) -> int:
        return self._send(ControlCommandKind.PAUSE, identity)

    def resume(self, identity: ControlSessionIdentity) -> int:
        return self._send(ControlCommandKind.RESUME, identity)

    def stop(self, identity: ControlSessionIdentity) -> int:
        return self._send(ControlCommandKind.STOP, identity)

    def emergency_stop(self) -> None:
        """Use the out-of-band safety path; it cannot be blocked by commands."""

        self._emergency_event.set()

    def poll_latest_snapshot(self) -> ControlSnapshot | None:
        latest: ControlSnapshot | None = None
        while True:
            try:
                candidate = self._snapshot_queue.get_nowait()
            except Empty:
                return latest
            if isinstance(candidate, ControlSnapshot):
                latest = candidate

    def poll_events(self) -> tuple[ControlEvent, ...]:
        events: list[ControlEvent] = []
        while True:
            try:
                candidate = self._event_queue.get_nowait()
            except Empty:
                return tuple(events)
            if isinstance(candidate, ControlEvent):
                events.append(candidate)

    def close(self, *, timeout_s: float = 2.0) -> bool:
        self._heartbeat_stop.set()
        self._shutdown_event.set()
        heartbeat_thread = self._heartbeat_thread
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=min(0.5, max(0.0, timeout_s)))
        process = self._process
        if process is None:
            return True
        process.join(timeout=max(0.0, timeout_s))
        return not process.is_alive()

    def _send(
        self,
        kind: ControlCommandKind,
        identity: ControlSessionIdentity,
        *,
        start_request: ControlStartRequest | None = None,
    ) -> int:
        if not self.is_alive():
            raise RuntimeError("control process is not running")
        with self._sequence_lock:
            self._command_sequence += 1
            sequence = self._command_sequence
        command = ControlCommand(
            kind=kind,
            sequence=sequence,
            identity=identity,
            start_request=start_request,
        )
        try:
            self._command_queue.put_nowait(command)
        except Full as exc:
            raise ControlBackpressureError("control command queue is full") from exc
        return sequence

    def _heartbeat_loop(self) -> None:
        assert self._heartbeat_interval_s is not None
        while not self._heartbeat_stop.is_set():
            _replace_latest(self._heartbeat_queue, time.monotonic())
            self._heartbeat_stop.wait(self._heartbeat_interval_s)


__all__ = [
    "ControlBackpressureError",
    "ControlCommand",
    "ControlCommandKind",
    "ControlEvent",
    "ControlEventKind",
    "ControlPolicy",
    "ControlSessionIdentity",
    "ControlSnapshot",
    "ControlStartRequest",
    "ControlState",
    "MiniDmaControlProcess",
    "SimulatedBackendConfig",
    "SimulatedControlBackend",
]
