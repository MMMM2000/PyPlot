from __future__ import annotations

from pathlib import Path
import json

import pytest

from data_logging.shared_power_supply.bench_guard import (
    BenchLockBusy,
    acquire_bench_lock,
    identify_hmp_with_blank_retry,
    probe_hmp_bench,
    read_lock_info,
)
from data_logging.shared_power_supply.profiles import HMP4030_PROFILE
from data_logging.shared_power_supply import bench_guard


def test_bench_lock_is_atomic_and_records_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "bench.lock"
    lock = acquire_bench_lock(owner="thread-a", purpose="hardware smoke", lock_path=lock_path)
    try:
        info = read_lock_info(lock_path)
        assert info is not None
        assert info.owner == "thread-a"
        assert info.purpose == "hardware smoke"
        with pytest.raises(BenchLockBusy):
            acquire_bench_lock(owner="thread-b", purpose="other smoke", lock_path=lock_path)
    finally:
        lock.release()

    assert read_lock_info(lock_path) is None


def test_bench_lock_recovers_verified_stale_owner(tmp_path: Path, monkeypatch) -> None:
    lock_path = tmp_path / "bench.lock"
    lock_path.write_text(
        json.dumps(
            {
                "owner": "dead-controller",
                "purpose": "interrupted hardware run",
                "pid": 424242,
                "cwd": str(tmp_path),
                "created_at_utc": "2026-08-10T15:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bench_guard, "_pid_is_running", lambda pid: pid != 424242)

    lock = acquire_bench_lock(owner="replacement", lock_path=lock_path)
    try:
        info = read_lock_info(lock_path)
        assert info is not None
        assert info.owner == "replacement"
        assert info.pid != 424242
    finally:
        lock.release()


def test_bench_probe_reports_unavailable_when_driver_cannot_connect() -> None:
    class FailingDriver:
        def __init__(self, **_kwargs: object) -> None:
            self.closed = False

        def connect(self) -> None:
            raise RuntimeError("port busy")

        def close(self) -> None:
            self.closed = True

    result = probe_hmp_bench(driver_factory=FailingDriver)

    assert result.available is False
    assert "port busy" in result.message


def test_identify_hmp_retries_transient_blank_response() -> None:
    class FakeDriver:
        def __init__(self) -> None:
            self.profile = None
            self.calls = 0

        def identify(self) -> str:
            self.calls += 1
            if self.calls == 1:
                return ""
            self.profile = object()
            return "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62"

    driver = FakeDriver()

    idn = identify_hmp_with_blank_retry(driver, sleep_fn=lambda _seconds: None)

    assert idn.startswith("ROHDE&SCHWARZ,HMP4040")
    assert driver.calls == 2


def test_identify_hmp_does_not_hide_unsupported_nonblank_response() -> None:
    class FakeDriver:
        def __init__(self) -> None:
            self.profile = None
            self.calls = 0

        def identify(self) -> str:
            self.calls += 1
            return "OTHER,DEVICE"

    driver = FakeDriver()

    idn = identify_hmp_with_blank_retry(driver, sleep_fn=lambda _seconds: None)

    assert idn == "OTHER,DEVICE"
    assert driver.profile is None
    assert driver.calls == 1


def test_bench_probe_reads_requested_channels() -> None:
    class FakeDriver:
        def __init__(self, **_kwargs: object) -> None:
            self.closed = False

        def connect(self) -> None:
            pass

        def identify(self) -> str:
            return "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62"

        def output_state(self, *, channel: int) -> bool:
            return channel == 3

        def measure(self, *, channel: int) -> dict[str, float]:
            return {"voltage_V": float(channel), "current_mA": float(channel * 10)}

        def close(self) -> None:
            self.closed = True

    result = probe_hmp_bench(channels=(1, 3), driver_factory=FakeDriver)

    assert result.available is True
    assert result.idn.startswith("ROHDE&SCHWARZ")
    assert result.channel_readbacks == {
        1: {"output_on": False, "readback": {"voltage_V": 1.0, "current_mA": 10.0}},
        3: {"output_on": True, "readback": {"voltage_V": 3.0, "current_mA": 30.0}},
    }


def test_bench_probe_skips_channels_not_present_on_detected_model() -> None:
    class FakeDriver:
        def __init__(self, **_kwargs: object) -> None:
            self.closed = False
            self.profile = HMP4030_PROFILE

        def connect(self) -> None:
            pass

        def identify(self) -> str:
            return "HAMEG,HMP4030,022982747,HW50020001/SW2.50"

        def output_state(self, *, channel: int) -> bool:
            if channel > self.profile.channel_count:
                raise AssertionError("probe should not touch unavailable channels")
            return False

        def measure(self, *, channel: int) -> dict[str, float]:
            if channel > self.profile.channel_count:
                raise AssertionError("probe should not touch unavailable channels")
            return {"voltage_V": float(channel), "current_mA": float(channel * 10)}

        def close(self) -> None:
            self.closed = True

    result = probe_hmp_bench(channels=(1, 3, 4), driver_factory=FakeDriver)

    assert result.available is True
    assert sorted(result.channel_readbacks or {}) == [1, 3]
