"""Bounded logging and electrical fault checks; no hardware access."""
from __future__ import annotations

import csv
import math
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread


@dataclass(frozen=True)
class ElectricalLimits:
    short_resistance_ohm: float | None = None
    open_current_fraction: float | None = None
    active_above_mA: float = 10.0
    open_confirm_s: float = 0.2

    def __post_init__(self):
        if self.short_resistance_ohm is not None and (
            not math.isfinite(self.short_resistance_ohm) or self.short_resistance_ohm <= 0
        ):
            raise ValueError("Short-circuit resistance must be positive and finite.")
        if self.open_current_fraction is not None and (
            not math.isfinite(self.open_current_fraction) or not 0 < self.open_current_fraction < 1
        ):
            raise ValueError("Contact-loss current fraction must be between zero and one.")
        if not math.isfinite(self.active_above_mA) or self.active_above_mA <= 0:
            raise ValueError("Protection current floor must be positive and finite.")
        if not math.isfinite(self.open_confirm_s) or self.open_confirm_s <= 0:
            raise ValueError("Contact-loss confirmation time must be positive and finite.")

    @property
    def enabled(self):
        return self.short_resistance_ohm is not None or self.open_current_fraction is not None


class ElectricalMonitor:
    def __init__(self, limits: ElectricalLimits):
        self.limits = limits
        self.low_since = None
        self.fault = None

    def check(self, now: float, target: float, current, voltage) -> str | None:
        if self.fault or not self.limits.enabled:
            return self.fault
        if current is None or voltage is None or not all(
            math.isfinite(float(v)) and float(v) >= 0 for v in (current, voltage)
        ):
            self.fault = "Invalid electrical protection measurement"
        elif target < self.limits.active_above_mA:
            # Includes intentional zero-current steps; no V/I at zero current.
            self.low_since = None
        else:
            current, voltage = float(current), float(voltage)
            if (self.limits.short_resistance_ohm is not None
                    and current >= self.limits.active_above_mA
                    and voltage / (current / 1000) <= self.limits.short_resistance_ohm):
                self.fault = "Possible short circuit: resistance below configured minimum"
            if self.limits.open_current_fraction is not None:
                if current < target * self.limits.open_current_fraction:
                    if self.low_since is None:
                        self.low_since = now
                    if now - self.low_since >= self.limits.open_confirm_s:
                        self.fault = "Possible broken wire/contact loss or insufficient voltage compliance: current collapsed"
                else:
                    self.low_since = None
        return self.fault


class BufferedCsv:
    """One bounded queue and a daemon disk thread. Never wait for disk while energized.

    Queue saturation or writer failure is fatal to acquisition, not silently lossy.
    Closing is bounded and must happen only AFTER hardware shutdown.
    """
    def __init__(self, path: Path, fields, *, capacity=2048):
        self.path, self.fields = path, fields
        self.queue = Queue(maxsize=capacity)
        self.stopping = Event()
        self.ready = Event()
        self.error = None
        self.rows = 0
        self.thread = Thread(target=self._run, name="current-program-csv", daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(2.0):
            raise RuntimeError("CSV writer did not initialize within 2 seconds")
        self.check()

    def check(self):
        if self.error is not None:
            raise RuntimeError(f"CSV writer failed: {self.error}")

    def submit(self, row, *, flush=False):
        self.check()
        try:
            self.queue.put_nowait((dict(row), flush))
        except Full:
            raise RuntimeError("CSV queue full; stopping output to avoid unbounded logging backlog") from None

    def close(self):
        self.stopping.set()
        if self.thread.ident is not None:
            self.thread.join(2.0)
            if self.thread.is_alive():
                raise RuntimeError("CSV writer still blocked; output shutdown attempted, log may be incomplete")
        self.check()

    def _run(self):
        try:
            with self.path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.fields)
                writer.writeheader()
                handle.flush()
                self.ready.set()
                last_flush = time.monotonic()
                while not self.stopping.is_set() or not self.queue.empty():
                    try:
                        row, flush = self.queue.get(timeout=0.1)
                    except Empty:
                        flush = False
                    else:
                        writer.writerow(row)
                        self.rows += 1
                    if flush or time.monotonic() - last_flush >= 1.0:
                        handle.flush()
                        last_flush = time.monotonic()
                handle.flush()
        except Exception as exc:
            self.error = exc
        finally:
            self.ready.set()
