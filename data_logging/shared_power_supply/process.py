"""Spawn-safe OS-process host for the shared HMP broker.

The visible application process must not own the serial driver used by an
authoritative experiment controller.  This module keeps the serial driver,
broker scheduler, and TCP server together in a dedicated process while
exposing only a small lifecycle supervisor to Qt applications.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import multiprocessing
import os
from queue import Empty
import time
import traceback
from typing import Any


@dataclass(frozen=True)
class BrokerChannelConfig:
    channel: int
    role: str
    voltage_limit_v: float | None = None
    current_limit_a: float | None = None


@dataclass(frozen=True)
class BrokerProcessConfig:
    port_name: str
    baudrate: int
    host: str
    port: int
    channels: tuple[BrokerChannelConfig, ...]
    confirmation_name: str = "TMA dedicated HMP broker"
    driver_timeout_s: float = 0.7
    parent_pid: int = 0
    parent_loss_grace_s: float = 5.0
    driver_factory_module: str = ""
    driver_factory_name: str = ""
    driver_factory_options_json: str = "{}"


@dataclass(frozen=True)
class BrokerProcessReady:
    host: str
    port: int
    owner_pid: int
    profile_id: str
    port_name: str


def _publish_ready_payload(ready_queue: Any, payload: object) -> None:
    """Publish one startup result and synchronously flush the child feeder.

    ``multiprocessing.Queue.put`` returns before its feeder thread necessarily
    writes the payload to the parent-side pipe. A fast startup failure can
    therefore exit first and make the supervisor report only exit code 1. The
    broker publishes exactly one startup result, so it is safe to close and
    join the child-side feeder immediately after that result is queued.
    """

    ready_queue.put(payload)
    try:
        ready_queue.close()
        ready_queue.join_thread()
    except (AttributeError, OSError, ValueError):
        # Test doubles and interpreter shutdown may not expose a live feeder.
        pass


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return True
    if os.name == "nt":
        # ``os.kill(pid, 0)`` is not a reliable existence probe on Windows:
        # some missing PIDs raise a generic OSError that is indistinguishable
        # from access denial. Query a non-mutating process handle instead.
        import ctypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            int(pid),
        )
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # Access denied means that the process exists but is protected.
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _run_broker_process(
    config: BrokerProcessConfig,
    ready_queue: Any,
    stop_event: Any,
) -> None:
    # Keep all hardware imports inside the spawned process.
    from .bench_guard import identify_hmp_with_blank_retry
    from .broker import SharedPowerSupplyBroker
    from .driver import HmpSerialDriver
    from .protocol import start_broker_server

    driver: Any = None
    server: Any = None
    thread: Any = None
    broker: SharedPowerSupplyBroker | None = None
    try:
        if config.driver_factory_module and config.driver_factory_name:
            factory_module = importlib.import_module(config.driver_factory_module)
            factory = getattr(factory_module, config.driver_factory_name)
            driver = factory(config)
        else:
            driver = HmpSerialDriver(
                port_name=config.port_name,
                baudrate=config.baudrate,
                timeout_s=config.driver_timeout_s,
            )
        driver.connect()
        idn_text = identify_hmp_with_blank_retry(driver)
        if driver.profile is None:
            raise RuntimeError(f"Unsupported shared HMP response: {idn_text}")
        broker = SharedPowerSupplyBroker(driver, driver.profile)
        for channel in config.channels:
            broker.assign_role(
                channel=channel.channel,
                role=channel.role,
                confirmed=True,
                voltage_limit_v=channel.voltage_limit_v,
                current_limit_a=channel.current_limit_a,
            )
        broker.confirm_profile(name=config.confirmation_name)
        server, thread = start_broker_server(
            broker,
            host=config.host,
            port=config.port,
        )
        actual_host, actual_port = server.server_address
        _publish_ready_payload(
            ready_queue,
            BrokerProcessReady(
                host=str(actual_host),
                port=int(actual_port),
                owner_pid=os.getpid(),
                profile_id=driver.profile.profile_id,
                port_name=driver.port_name,
            ),
        )

        parent_missing_since_s: float | None = None
        while not stop_event.wait(0.05):
            if _pid_exists(config.parent_pid):
                parent_missing_since_s = None
                continue
            now_s = time.monotonic()
            if parent_missing_since_s is None:
                parent_missing_since_s = now_s
                continue
            if now_s - parent_missing_since_s < config.parent_loss_grace_s:
                continue
            # If the visible supervisor disappears, fail safe after allowing
            # the authoritative controller heartbeat path time to react.
            broker.emergency_all_outputs_off(intent="emergency_stop_all")
            break
    except BaseException as exc:
        try:
            _publish_ready_payload(
                ready_queue,
                {
                    "error": str(exc) or exc.__class__.__name__,
                    "traceback": traceback.format_exc(),
                },
            )
        except Exception:
            pass
        # The supervisor receives the durable traceback through ``ready_queue``.
        # Exit non-zero without also dumping an unstructured child traceback to
        # the launcher console/log.
        raise SystemExit(1) from None
    finally:
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread is not None:
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass
        if broker is not None:
            try:
                broker.stop_scheduler()
            except Exception:
                pass
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass


class SharedPowerSupplyBrokerProcess:
    """UI-side lifecycle supervisor for a process-owned HMP broker."""

    def __init__(self, config: BrokerProcessConfig) -> None:
        self.config = config
        self._context = multiprocessing.get_context("spawn")
        self._ready_queue = self._context.Queue(maxsize=1)
        self._stop_event = self._context.Event()
        self._process: multiprocessing.Process | None = None
        self._ready: BrokerProcessReady | None = None

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def exitcode(self) -> int | None:
        return None if self._process is None else self._process.exitcode

    @property
    def ready(self) -> BrokerProcessReady | None:
        return self._ready

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("shared power-supply broker process already started")
        self._process = self._context.Process(
            target=_run_broker_process,
            args=(self.config, self._ready_queue, self._stop_event),
            name="TmaPowerSupplyBroker",
        )
        self._process.start()

    def wait_until_ready(self, *, timeout_s: float = 10.0) -> BrokerProcessReady:
        process = self._process
        if process is None:
            raise RuntimeError("shared power-supply broker process is not started")
        deadline_s = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            try:
                payload = self._ready_queue.get_nowait()
            except Empty:
                payload = None
            if isinstance(payload, BrokerProcessReady):
                self._ready = payload
                return payload
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            if not process.is_alive():
                # The child may have exited immediately after publishing its
                # failure. Give the queue pipe one short bounded drain before
                # falling back to an otherwise opaque exit-code message.
                try:
                    payload = self._ready_queue.get(timeout=0.25)
                except Empty:
                    payload = None
                if isinstance(payload, BrokerProcessReady):
                    self._ready = payload
                    return payload
                if isinstance(payload, dict) and payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                raise RuntimeError(
                    "shared power-supply broker process exited before ready"
                    if process.exitcode is None
                    else (
                        "shared power-supply broker process exited with code "
                        f"{process.exitcode} before ready"
                    )
                )
            remaining_s = deadline_s - time.monotonic()
            if remaining_s <= 0.0:
                raise TimeoutError(
                    "shared power-supply broker process did not report ready before timeout"
                )
            time.sleep(min(0.01, remaining_s))

    def is_alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def close(self, *, timeout_s: float = 2.0, force: bool = False) -> bool:
        self._stop_event.set()
        process = self._process
        if process is None:
            return True
        process.join(timeout=max(0.0, timeout_s))
        if process.is_alive() and force:
            process.terminate()
            process.join(timeout=max(0.0, timeout_s))
        return not process.is_alive()


__all__ = [
    "BrokerChannelConfig",
    "BrokerProcessConfig",
    "BrokerProcessReady",
    "SharedPowerSupplyBrokerProcess",
]
