from __future__ import annotations

from pathlib import Path

import pytest

from data_logging.shared_power_supply.bench_guard import (
    BenchLockBusy,
    acquire_bench_lock,
    identify_hmp_with_blank_retry,
    probe_hmp_bench,
    read_lock_info,
)


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
            self.reset_calls = 0

        def reset_io_buffers(self) -> None:
            self.reset_calls += 1

        def identify(self) -> str:
            self.calls += 1
            if self.calls < 4:
                return ""
            self.profile = object()
            return "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62"

    driver = FakeDriver()

    idn = identify_hmp_with_blank_retry(driver, sleep_fn=lambda _seconds: None)

    assert idn.startswith("ROHDE&SCHWARZ,HMP4040")
    assert driver.calls == 4
    assert driver.reset_calls == 4


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


def test_hmp_driver_reset_io_buffers_uses_serial_reset_methods() -> None:
    from data_logging.shared_power_supply.driver import HmpSerialDriver

    class FakeSerial:
        is_open = True

        def __init__(self) -> None:
            self.calls: list[str] = []

        def write(self, data: bytes) -> int:
            return len(data)

        def readline(self) -> bytes:
            return b""

        def reset_input_buffer(self) -> None:
            self.calls.append("input")

        def reset_output_buffer(self) -> None:
            self.calls.append("output")

        def close(self) -> None:
            self.is_open = False

    port = FakeSerial()
    driver = HmpSerialDriver(port_name="COM3", serial_factory=lambda *_args, **_kwargs: port)
    driver.connect()

    driver.reset_io_buffers()

    assert port.calls == ["input", "output"]


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
    assert result.electrically_idle is False
    assert result.busy_channels == (3,)
    assert result.unknown_output_channels == ()
    assert result.idn.startswith("ROHDE&SCHWARZ")
    assert result.channel_readbacks == {
        1: {"output_on": False, "readback": {"voltage_V": 1.0, "current_mA": 10.0}},
        3: {"output_on": True, "readback": {"voltage_V": 3.0, "current_mA": 30.0}},
    }


def test_bench_probe_reports_unknown_output_as_not_idle() -> None:
    class FakeDriver:
        def __init__(self, **_kwargs: object) -> None:
            self.closed = False

        def connect(self) -> None:
            pass

        def identify(self) -> str:
            return "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62"

        def output_state(self, *, channel: int) -> bool | None:
            return None if channel == 4 else False

        def measure(self, *, channel: int) -> dict[str, float]:
            return {"voltage_V": float(channel), "current_mA": 0.0}

        def close(self) -> None:
            self.closed = True

    result = probe_hmp_bench(channels=(1, 4), driver_factory=FakeDriver)

    assert result.available is True
    assert result.electrically_idle is False
    assert result.busy_channels == ()
    assert result.unknown_output_channels == (4,)
