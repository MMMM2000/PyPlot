"""Bounded, single-owner Keithley I/O for the Current Annealing logger.

100 Hz is an application target, not an instrument throughput guarantee.
The GUI controls the ramp at 50 Hz; a stalled GUI shuts down the output.
"""
from __future__ import annotations

import math
import time
from collections import deque
from threading import Event, Lock, Thread

from experiments.current_program_logger import Keithley2636Adapter

READ_HZ = 100
CONTROL_HZ = 50
NPLC = 0.1


class AnnealingKeithley(Keithley2636Adapter):
    """Exclusive VISA ownership and output-off verification on every release."""

    output_off_verified = False

    def open(self) -> None:
        if not self.resource_name:
            raise RuntimeError("Select or enter the Keithley VISA resource first.")
        self._locked = False
        try:
            self._manager = self._resource_manager_factory()
            device = self._manager.open_resource(self.resource_name)
            self._instrument = device
            device.lock_excl(timeout=1000)
            self._locked = True
            device.timeout = 1000
            device.write_termination = "\n"
            device.read_termination = "\n"
            self.identity = device.query("*IDN?").strip()
            if "KEITHLEY" not in self.identity.upper() or "2636" not in self.identity.upper():
                raise RuntimeError(f"VISA resource is not a Keithley 2636: {self.identity}")
            output = float(device.query(f"print({self._smu}.source.output)"))
            off = float(device.query(f"print({self._smu}.OUTPUT_OFF)"))
            if not math.isfinite(output) or output != off:
                raise RuntimeError("Keithley output is already ON; refusing to take over.")
        except BaseException:
            self._release()
            raise

    def _release(self) -> None:
        device, manager = self._instrument, self._manager
        self._instrument = self._manager = None
        try:
            if device is not None:
                try:
                    if getattr(self, "_locked", False):
                        device.unlock()
                finally:
                    device.close()
        finally:
            self._locked = False
            if manager is not None:
                manager.close()

    def configure(self, *, voltage_v: float, current_mA: float) -> None:
        self.output_off_verified = False
        if not math.isfinite(current_mA) or not 0 <= current_mA <= self.current_limit_mA:
            raise ValueError("Invalid initial current or current above the run ceiling.")
        if not math.isfinite(voltage_v) or not 0.1 <= voltage_v <= 200:
            raise ValueError("Keithley voltage compliance must be between 0.1 and 200 V.")
        device = self._device()
        output = float(device.query(f"print({self._smu}.source.output)"))
        off = float(device.query(f"print({self._smu}.OUTPUT_OFF)"))
        if output != off:
            raise RuntimeError("Keithley output changed to ON before Start; refusing takeover.")
        super().configure(voltage_v=voltage_v, current_mA=current_mA)
        self._device().write(f"{self._smu}.measure.nplc = {NPLC}")
        # Fresh autozero once per run; no stale zero cache from a previous run.
        self._device().write(f"{self._smu}.measure.autozero = {self._smu}.AUTOZERO_ONCE")

    def close(self) -> None:
        errors = []
        device = self._instrument
        if device is not None:
            for command in (f"{self._smu}.source.output = {self._smu}.OUTPUT_OFF",
                            f"{self._smu}.source.leveli = 0"):
                try:
                    device.write(command)
                except Exception as exc:
                    errors.append(str(exc))
            try:
                output = float(device.query(f"print({self._smu}.source.output)"))
                off = float(device.query(f"print({self._smu}.OUTPUT_OFF)"))
                level = float(device.query(f"print({self._smu}.source.leveli)"))
                self.output_off_verified = output == off and math.isclose(level, 0, abs_tol=1e-12)
                if not self.output_off_verified:
                    errors.append(f"Unsafe readback: output={output}, current={level} A")
            except Exception as exc:
                errors.append(f"Output-off readback failed: {exc}")
        self._release()
        if errors:
            raise RuntimeError("Keithley shutdown not confirmed: " + "; ".join(errors))


class KeithleyAcquisition:
    """Only this worker accesses VISA while running; queue overflow fails closed."""

    def __init__(self, adapter: AnnealingKeithley, voltage_v: float, initial_mA: float) -> None:
        self.adapter = adapter
        self.voltage_v = voltage_v
        self.target_mA = initial_mA
        self.stop_event = Event()
        self.lock = Lock()
        self.samples: deque[tuple[float, float, float, float]] = deque()
        self.last_heartbeat = time.monotonic()
        self.error = ""
        self.started_at: float | None = None
        self.thread = Thread(target=self._run, name="annealing-keithley", daemon=True)

    def set_target(self, current_mA: float) -> None:
        if not math.isfinite(current_mA) or not 0 <= current_mA <= self.adapter.current_limit_mA:
            self.stop_event.set()
            raise ValueError("Keithley setpoint exceeds the fixed run current ceiling.")
        with self.lock:
            self.target_mA = current_mA
            self.last_heartbeat = time.monotonic()

    def drain(self) -> list[tuple[float, float, float, float]]:
        with self.lock:
            result = list(self.samples)
            self.samples.clear()
        return result

    def _run(self) -> None:
        try:
            self.adapter.configure(voltage_v=self.voltage_v, current_mA=self.target_mA)
            self.adapter.warm_up_measurement()
            self.started_at = time.monotonic()
            with self.lock:
                self.last_heartbeat = self.started_at
            last_target = self.target_mA
            low_since = None
            while not self.stop_event.is_set():
                tick = time.monotonic()
                with self.lock:
                    target, heartbeat = self.target_mA, self.last_heartbeat
                if tick - heartbeat > 0.75:
                    raise RuntimeError("Annealing control stopped responding; output disabled.")
                if target != last_target:
                    self.adapter.set_current(target)
                    last_target = target
                values = self.adapter.measure()
                current, voltage = values["current_mA"], values["voltage_V"]
                now = time.monotonic()
                if not math.isfinite(current) or not math.isfinite(voltage):
                    raise RuntimeError("Invalid Keithley current/voltage measurement.")
                if target > 0 and current < target * 0.25:
                    low_since = now if low_since is None else low_since
                    if now - low_since >= 0.5:
                        raise RuntimeError("Keithley contact loss / low current; output disabled.")
                else:
                    low_since = None
                with self.lock:
                    if len(self.samples) >= 200:
                        raise RuntimeError("Annealing sample buffer full; output disabled.")
                    self.samples.append((now, target, current, voltage))
                self.stop_event.wait(max(0, 1 / READ_HZ - (time.monotonic() - tick)))
        except Exception as exc:
            self.error = str(exc)
        finally:
            try:
                self.adapter.close()
            except Exception as exc:
                self.error = "; ".join(filter(None, (self.error, str(exc))))

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("Keithley I/O has not stopped; output-off state is unverified.")
        if self.error:
            raise RuntimeError(self.error)
