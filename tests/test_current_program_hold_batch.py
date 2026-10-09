"""Isolated fake-hardware preparation tests; no live VISA or persistent app data."""
import csv
import json
from dataclasses import replace

import pytest

from experiments import current_program_logger as logger
from experiments.current_program_support import ColdResetLimits, ColdResetMonitor
from plotting.shared.power_guard import NoopSleepGuard
from scripts.current_program_hold_batch import hold_config, run_screen, SyncBoard
from scripts import current_program_hold_batch as batch


def test_preview_is_nonmutating(tmp_path):
    result = run_screen(resource="USB0::0x05E6::0x2636::FAKE::INSTR",
                        output_dir=tmp_path, run_name="preview")
    assert result["status"] == "dry_run"
    assert result["preview"]["hold_levels_mA"] == (3, 2.5, 2, 3)
    assert not list(tmp_path.iterdir())


def test_protocol_and_sampling(tmp_path):
    config = hold_config(tmp_path, "test", 2.5)
    assert [(b.target_mA, b.duration_s) for b in config.blocks] == [(.1, 3), (10, .5), (2.5, 10), (.1, 20)]
    assert config.blocks[1].resistance_ohm == 185
    assert (config.max_resistance_ohm, config.emergency_resistance_ohm) == (190, 195)
    assert config.resistance_action == "readout_current"
    assert config.cold_reset.timeout_s == 90
    for age, expected in [(0, 1000), (1.99, 1000), (2, 100)]:
        state = logger.ProgramState(0, 3, config.blocks[-1], .1, age)
        assert config.log_rate_for(state) == expected
    assert len(hold_config(tmp_path, "reference", None).blocks) == 3
    with pytest.raises(ValueError):
        hold_config(tmp_path, "bad", 4)


def test_cold_reset_preserves_reference_and_requires_window():
    monitor = ColdResetMonitor(ColdResetLimits(window_s=.1, tolerance_ohm=2))
    for stamp in (0, .03, .08):
        monitor.observe(stamp, 160)
    assert not monitor.stable()
    monitor.observe(.11, 160)
    assert monitor.stable() and monitor.reference == 160
    monitor.clear()
    for stamp in (1, 1.05, 1.11):
        monitor.observe(stamp, 165)
    assert not monitor.stable() and monitor.reference == 160
    with pytest.raises(RuntimeError, match="Invalid"):
        monitor.observe(2, float("nan"))


class FakeAdapter:
    description = "fake Keithley"
    def __init__(self, heat_r=180, warm_tail=False, broken=False):
        self.heat_r, self.warm_tail, self.broken = heat_r, warm_tail, broken
        self.heated = False
        self.commands = []
        self.output_off_verified = False
    def open(self): self.closed = False
    def configure(self, **kwargs): self.current = kwargs["current_mA"]
    def set_current(self, current):
        self.commands.append(current)
        self.current = current
        self.heated |= current >= 10
    def measure(self):
        resistance = self.heat_r if self.current >= 10 else (170 if self.heated and self.warm_tail else 160)
        current = 0 if self.broken and self.heated else self.current
        return dict(current_mA=current, voltage_V=resistance*current/1000)
    def close(self): self.closed = self.output_off_verified = True


def shortened_config(tmp_path):
    config = hold_config(tmp_path, "fake", 3)
    blocks = tuple(replace(block, duration_s=value)
                   for block, value in zip(config.blocks, (.06, .05, .04, .06)))
    return replace(config, blocks=blocks, cold_reset=ColdResetLimits(window_s=.02, timeout_s=.15),
                   electrical_limits=replace(config.electrical_limits, open_confirm_s=.005),
                   log_rate_schedule=((3, .02, 100),))


@pytest.mark.parametrize("heat_r,warm_tail,broken,expected", [
    (180, False, False, "stopped"),
    (186, False, False, "stopped"),
    (191, False, False, "stopped"),
    (196, False, False, "failed"),
    (180, True, False, "failed"),
    (180, False, True, "failed"),
])
def test_fake_transitions_and_shutdown(tmp_path, monkeypatch, heat_r, warm_tail, broken, expected):
    monkeypatch.setattr(logger, "create_experiment_sleep_guard", lambda *a, **k: NoopSleepGuard())
    adapter = FakeAdapter(heat_r, warm_tail, broken)
    worker = logger.ProgramWorker(shortened_config(tmp_path), adapter)
    worker.start()
    assert worker.wait(2000)
    data = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert data["status"] == expected, data.get("error")
    assert adapter.closed and data["output_off_verified"]
    if heat_r == 191:
        assert 3 not in adapter.commands  # Guard must skip the hold, not advance into it.
        assert any(event["event"] == "guard_to_cooling" for event in data["sequence_events"])
    if expected == "stopped":
        assert data["cold_reference_ohm"] == 160 and data["cold_reset_verified"]
        cooling = next(command["elapsed_s"] for command in data["current_commands"]
                       if command["block_index"] == 3)
        assert data["cold_reset_elapsed_s"] - cooling >= .055
    if warm_tail:
        assert "Cold reset timed out" in data["error"]
    if broken:
        assert "current collapsed" in data["error"]


def test_warm_start_refuses_heating(tmp_path, monkeypatch):
    monkeypatch.setattr(logger, "create_experiment_sleep_guard", lambda *a, **k: NoopSleepGuard())
    config = shortened_config(tmp_path)
    config = replace(config, cold_reset=replace(config.cold_reset, reference_ohm=150))
    adapter = FakeAdapter()
    worker = logger.ProgramWorker(config, adapter)
    worker.start()
    assert worker.wait(2000)
    assert 10 not in adapter.commands and adapter.output_off_verified


def test_full_rate_saves_every_acquired_raw_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(logger, "create_experiment_sleep_guard", lambda *a, **k: NoopSleepGuard())
    config = replace(shortened_config(tmp_path), log_rate_schedule=())
    worker = logger.ProgramWorker(config, FakeAdapter())
    worker.start()
    assert worker.wait(2000)
    data = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert data["status"] == "stopped"
    assert data["rows"] == data["protection_checks"]


def test_cancellation_is_not_reported_as_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(logger, "create_experiment_sleep_guard", lambda *a, **k: NoopSleepGuard())
    adapter = FakeAdapter()
    worker = logger.ProgramWorker(shortened_config(tmp_path), adapter)
    original = adapter.measure
    def measure():
        if adapter.current == 10:
            worker.stop()
        return original()
    adapter.measure = measure
    worker.start()
    assert worker.wait(2000)
    data = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert adapter.output_off_verified and not data.get("cold_reset_verified")


def test_sync_cues_use_monotonic_clock_and_closing_requests_stop(tmp_path, qtbot):
    board = SyncBoard(tmp_path/"sync.csv")
    qtbot.addWidget(board)
    board.show()
    qtbot.waitUntil(lambda: board.last_code is not None)
    board.close()
    assert board.stop_requested
    board.writer.close()
    rows = list(csv.DictReader((tmp_path/"sync.csv").open()))
    assert rows and float(rows[0]["monotonic_s"]) >= board.origin


def test_entire_screen_with_fake_hardware_and_isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(logger, "create_experiment_sleep_guard", lambda *a, **k: NoopSleepGuard())
    monkeypatch.setattr(batch, "running_controllers", lambda: [])
    adapters = []
    def adapter_factory(**kwargs):
        adapter = FakeAdapter()
        adapters.append(adapter)
        return adapter
    monkeypatch.setattr(batch, "OwnedKeithley2636Adapter", adapter_factory)
    def config_factory(output_dir, run_name, hold_mA, *, reference=None):
        config = shortened_config(output_dir)
        return replace(config, run_name=run_name,
                       blocks=tuple(replace(block, target_mA=hold_mA) if index == 2 else block
                                    for index, block in enumerate(config.blocks)),
                       cold_reset=replace(config.cold_reset, reference_ohm=reference))
    monkeypatch.setattr(batch, "hold_config", config_factory)
    result = batch.run_screen(resource="USB0::0x05E6::0x2636::FAKE::INSTR",
        output_dir=tmp_path, run_name="screen", live=True, manifest_dir=tmp_path/"manifests")
    assert result["status"] == "complete" and len(result["trials"]) == 4
    assert all(adapter.output_off_verified for adapter in adapters)
    assert all(trial["output_off_verified"] and trial["pulse_command_interval_s"] < .1
               for trial in result["trials"])


def test_hold_screen_does_not_continue_after_failed_trial(tmp_path, monkeypatch):
    monkeypatch.setattr(logger, "create_experiment_sleep_guard", lambda *a, **k: NoopSleepGuard())
    monkeypatch.setattr(batch, "running_controllers", lambda: [])
    adapters = []
    def factory(**kwargs):
        adapter = FakeAdapter(heat_r=196)
        adapters.append(adapter)
        return adapter
    monkeypatch.setattr(batch, "OwnedKeithley2636Adapter", factory)
    monkeypatch.setattr(batch, "hold_config", lambda directory, name, level, **kwargs:
                        replace(shortened_config(directory), run_name=name))
    with pytest.raises(RuntimeError, match="Trial failed"):
        batch.run_screen(resource="USB0::0x05E6::0x2636::FAKE::INSTR", output_dir=tmp_path,
                         run_name="fail", live=True, manifest_dir=tmp_path/"manifests")
    assert len(adapters) == 1 and adapters[0].output_off_verified
    data = json.loads(next((tmp_path/"manifests").glob("*.json")).read_text())
    assert data["status"] == "stopped_on_error"
