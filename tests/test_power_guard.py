from __future__ import annotations

import importlib


def test_windows_sleep_guard_acquires_once_and_releases_to_continuous_state() -> None:
    power_guard = importlib.import_module("plotting.shared.power_guard")
    calls: list[int] = []
    guard = power_guard.WindowsSleepGuard(set_execution_state=calls.append)

    guard.acquire()
    guard.acquire()
    guard.release()
    guard.release()

    assert calls == [
        power_guard.ES_CONTINUOUS
        | power_guard.ES_SYSTEM_REQUIRED
        | power_guard.ES_DISPLAY_REQUIRED,
        power_guard.ES_CONTINUOUS,
    ]


def test_sleep_guard_context_releases_after_exception() -> None:
    power_guard = importlib.import_module("plotting.shared.power_guard")
    calls: list[int] = []
    guard = power_guard.WindowsSleepGuard(set_execution_state=calls.append)

    try:
        with guard:
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert calls[-1] == power_guard.ES_CONTINUOUS


def test_create_experiment_sleep_guard_returns_noop_on_non_windows(monkeypatch) -> None:
    power_guard = importlib.import_module("plotting.shared.power_guard")
    monkeypatch.setattr(power_guard.sys, "platform", "linux")

    guard = power_guard.create_experiment_sleep_guard("test")
    guard.acquire()
    guard.release()

    assert isinstance(guard, power_guard.NoopSleepGuard)
