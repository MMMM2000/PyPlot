"""Finite trial behavior with fake adapters; never touches a real supply."""
import json
from dataclasses import replace

import pytest

from experiments import current_program_logger as logger
from scripts.current_program_batch import OwnedKeithley2636Adapter, run_batch, trial_config


class FakeVisa:
    def __init__(self, output=0.0):
        self.output = output
        self.level = 0.0
        self.locked = False
        self.closed = False
        self.commands = []

    def lock_excl(self, **kwargs): self.locked = True
    def unlock(self): self.locked = False
    def close(self): self.closed = True
    def write(self, command):
        self.commands.append(command)
        if command == "smua.source.output = smua.OUTPUT_OFF": self.output = 0.0
        if command == "smua.source.leveli = 0": self.level = 0.0
    def query(self, command):
        if command == "*IDN?": return "Keithley Instruments Inc., Model 2636B, TEST"
        if command == "print(smua.source.output)": return str(self.output)
        if command == "print(smua.OUTPUT_OFF)": return "0"
        if command == "print(smua.source.leveli)": return str(self.level)
        raise AssertionError(command)


def make_adapter(device):
    class Manager:
        def open_resource(self, resource): return device
        def close(self): pass
    return OwnedKeithley2636Adapter(resource_name="FAKE", channel="A", remote_sense=False,
                                    current_limit_mA=10, resource_manager_factory=Manager)


def test_exclusive_ownership_and_verified_off_cleanup():
    device = FakeVisa()
    adapter = make_adapter(device)
    adapter.open()
    assert device.locked
    device.output, device.level = 1.0, .002
    adapter.close()
    assert not device.locked and device.closed
    assert device.output == device.level == 0


def test_active_output_refuses_takeover_without_commands():
    device = FakeVisa(output=1.0)
    adapter = make_adapter(device)
    with pytest.raises(RuntimeError, match="already on"):
        adapter.open()
    assert not device.commands and not device.locked and device.closed


def test_batch_timeout_advances_to_cooling_and_then_completes():
    blocks = trial_config(None, "trial", 2).blocks
    engine = logger.ConditionalRecipeEngine(blocks, initial_current_mA=.1,
                                             advance_on_timeout=True)
    assert engine.state_at(599.9).target_mA == 2
    cooling = engine.state_at(600.0)
    assert cooling.block_index == 1 and cooling.target_mA == .1
    assert engine.timeout_events == [(0, 0, 600.0)]
    assert engine.state_at(629.9).sequence_complete is False
    assert engine.state_at(630.0).sequence_complete is True


def test_batch_threshold_advances_early():
    blocks = trial_config(None, "trial", 2).blocks
    engine = logger.ConditionalRecipeEngine(blocks, initial_current_mA=.1,
                                             advance_on_timeout=True)
    assert not engine.observe(.001, 191)
    assert not engine.observe(.002, 191)
    assert engine.observe(.003, 191)
    assert engine.state_at(.004).target_mA == .1
    assert engine.state_at(30.003).sequence_complete
    assert not engine.timeout_events


def test_pilot_limits_and_phase_log_schedule(tmp_path):
    config = trial_config(tmp_path, "pilot", 5)
    assert config.blocks[0].resistance_ohm == 190
    assert config.max_resistance_ohm == 195
    assert config.max_resistance_active_above_mA == 1
    assert config.resistance_action == "output_off"
    def state(block, age):
        return logger.ProgramState(0, block, config.blocks[block],
                                   config.blocks[block].target_mA, age)
    assert config.log_rate_for(state(0, 0)) == 1000
    assert config.log_rate_for(state(0, 30)) == 100
    assert config.log_rate_for(state(1, 0)) == 1000
    assert config.log_rate_for(state(1, 2)) == 20


@pytest.mark.parametrize("heat_resistance, expected_target, cutoff", [
    (191, True, False), (196, False, True),
])
def test_pilot_cooling_transition_and_emergency_cutoff(
        tmp_path, heat_resistance, expected_target, cutoff):
    class Adapter:
        description = "fake Keithley"
        def open(self): self.closed = False
        def configure(self, **kwargs): self.current = kwargs["current_mA"]
        def set_current(self, value): self.current = value
        def measure(self):
            resistance = heat_resistance if self.current >= 1 else 210
            return {"current_mA": self.current,
                    "voltage_V": self.current * resistance / 1000}
        def close(self): self.closed = True

    config = replace(trial_config(tmp_path, "pilot", 5), blocks=(
        logger.CurrentBlock("Hold", 5, .2, resistance_ohm=190),
        logger.CurrentBlock("Hold", .1, .02)))
    adapter = Adapter()
    worker = logger.ProgramWorker(config, adapter)
    worker.start()
    assert worker.wait(3000)
    assert adapter.closed
    metadata = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    events = [event["event"] for event in metadata["sequence_events"]]
    assert ("resistance_target" in events) is expected_target
    assert metadata["resistance_limit_reached"] is cutoff
    assert metadata["status"] == "stopped"
    assert metadata["current_commands"][0]["target_current_mA"] == 5
    assert any(command["target_current_mA"] == .1
               for command in metadata["current_commands"]) is expected_target


def test_worker_automatically_stops_after_timed_out_trial(tmp_path):
    class Adapter:
        description = "fake Keithley"
        def open(self): self.closed = False
        def configure(self, **kwargs): self.current = kwargs["current_mA"]
        def set_current(self, value): self.current = value
        def measure(self): return {"current_mA": self.current, "voltage_V": self.current * .16}
        def close(self): self.closed = True

    blocks = (logger.CurrentBlock("Hold", 2, .03, resistance_ohm=180),
              logger.CurrentBlock("Hold", .1, .02))
    config = replace(trial_config(tmp_path, "short", 2), blocks=blocks,
                     control_rate_hz=500, measurement_rate_hz=100,
                     resistance_check_rate_hz=500, log_rate_hz=100)
    adapter = Adapter()
    worker = logger.ProgramWorker(config, adapter)
    worker.start()
    assert worker.wait(3000)
    assert adapter.closed
    metadata = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert metadata["status"] == "stopped"
    assert [event["event"] for event in metadata["sequence_events"]] == [
        "resistance_timeout_continue", "sequence_complete_stop"]


def test_preview_and_bounds_without_hardware(tmp_path):
    resource = "USB0::0x05E6::0x2636::4093243::INSTR"
    result = run_batch(resource=resource, output_dir=tmp_path, run_name="wire", first=2, last=10)
    assert result["status"] == "dry_run"
    assert result["preview"]["levels_mA"] == list(range(2, 11))
    assert result["preview"]["max_total_s"] == 9 * 630
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError):
        run_batch(resource=resource, output_dir=tmp_path, run_name="wire", first=2, last=11)
