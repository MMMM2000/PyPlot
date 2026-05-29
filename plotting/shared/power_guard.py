from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from types import TracebackType


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class NoopSleepGuard:
    def acquire(self) -> None:
        return None

    def release(self) -> None:
        return None

    def __enter__(self) -> "NoopSleepGuard":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


class WindowsSleepGuard:
    def __init__(
        self,
        *,
        set_execution_state: Callable[[int], int | None] | None = None,
        keep_display_awake: bool = True,
    ) -> None:
        self._set_execution_state = set_execution_state or ctypes.windll.kernel32.SetThreadExecutionState
        self._keep_display_awake = bool(keep_display_awake)
        self._depth = 0

    def acquire(self) -> None:
        if self._depth == 0:
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            if self._keep_display_awake:
                flags |= ES_DISPLAY_REQUIRED
            result = self._set_execution_state(flags)
            if result == 0:
                raise OSError("SetThreadExecutionState failed while enabling experiment sleep prevention.")
        self._depth += 1

    def release(self) -> None:
        if self._depth <= 0:
            return
        self._depth -= 1
        if self._depth == 0:
            result = self._set_execution_state(ES_CONTINUOUS)
            if result == 0:
                raise OSError("SetThreadExecutionState failed while releasing experiment sleep prevention.")

    def __enter__(self) -> "WindowsSleepGuard":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def create_experiment_sleep_guard(reason: str, *, keep_display_awake: bool = True) -> NoopSleepGuard | WindowsSleepGuard:
    if sys.platform != "win32":
        return NoopSleepGuard()
    return WindowsSleepGuard(keep_display_awake=keep_display_awake)
