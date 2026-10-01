from __future__ import annotations

import math
import time

import pytest

from data_logging.current_annealing_logger.keithley import AnnealingKeithley, KeithleyAcquisition


class FakeVisa:
    def __init__(self, *, output=0, invalid=False):
        self.output = output
        self.current = 0.0
        self.invalid = invalid
        self.closed = False
        self.writes = []
        self.locked = False

    def lock_excl(self, **kwargs):
        self.locked = True

    def unlock(self):
        self.locked = False

    def close(self):
        self.closed = True

    def write(self, command):
        self.writes.append(command)
        if ".source.leveli = " in command:
            self.current = float(command.split(" = ")[-1])
        if ".source.output = " in command:
            self.output = int("OUTPUT_ON" in command)

    def query(self, command):
        if command == "*IDN?":
            return "Keithley Instruments Inc., Model 2636B, FAKE, 3.2.1"
        if "OUTPUT_OFF" in command:
            return "0"
        if "source.output" in command:
            return str(self.output)
        if "source.leveli" in command:
            return str(self.current)
        if "measure.iv" in command:
            return "nan nan" if self.invalid else f"{self.current} {self.current * 160}"
        raise AssertionError(command)


class FakeManager:
    def __init__(self, device):
        self.device = device
        self.closed = False

    def open_resource(self, resource):
        return self.device

    def close(self):
        self.closed = True


def adapter_for(device):
    return AnnealingKeithley(resource_name="FAKE", channel="a", remote_sense=False,
                            current_limit_mA=2, resource_manager_factory=lambda: FakeManager(device))


def test_refuse_output_on_without_mutating_device():
    device = FakeVisa(output=1)
    with pytest.raises(RuntimeError, match="already ON"):
        adapter_for(device).open()
    assert device.writes == []
    assert device.closed and not device.locked


def test_current_ceiling_checked_before_configuration():
    device = FakeVisa()
    adapter = adapter_for(device)
    adapter.open()
    with pytest.raises(ValueError, match="ceiling"):
        adapter.configure(voltage_v=1, current_mA=2.1)
    assert device.writes == []
    adapter.close()
    assert adapter.output_off_verified


def test_worker_paired_samples_and_verified_cleanup():
    device = FakeVisa()
    adapter = adapter_for(device)
    adapter.open()
    worker = KeithleyAcquisition(adapter, 1, 1)
    worker.thread.start()
    collected = []
    for _ in range(10):
        worker.set_target(1.5)
        time.sleep(0.02)
        collected.extend(worker.drain())
    worker.stop()
    assert len(collected) >= 10
    assert all(math.isclose(v / (i / 1000), 160) for _, _, i, v in collected)
    assert any(i == pytest.approx(1.5) for _, _, i, _ in collected)
    assert adapter.output_off_verified and device.output == 0
    assert all(float(c.split(" = ")[-1]) <= 0.002 for c in device.writes if "source.leveli =" in c)


@pytest.mark.parametrize("reason", ["invalid", "overflow", "stalled", "contact"])
def test_worker_faults_fail_closed(reason):
    device = FakeVisa(invalid=reason == "invalid")
    if reason == "contact":
        original = device.query
        device.query = lambda c: "0 1" if "measure.iv" in c else original(c)
    adapter = adapter_for(device)
    adapter.open()
    worker = KeithleyAcquisition(adapter, 1, 1)
    if reason == "overflow":
        worker.samples.extend([(0, 1, 1, 0.16)] * 200)
    worker.thread.start()
    deadline = time.monotonic() + 2
    while worker.thread.is_alive() and time.monotonic() < deadline:
        if reason == "contact":
            worker.set_target(1)
        time.sleep(0.02)
    assert not worker.thread.is_alive()
    assert worker.error
    assert adapter.output_off_verified and device.output == 0


def test_nonfinite_setpoint_fails_closed():
    worker = KeithleyAcquisition(adapter_for(FakeVisa()), 1, 1)
    with pytest.raises(ValueError):
        worker.set_target(math.nan)
    assert worker.stop_event.is_set()
