from __future__ import annotations

import csv
import json
import statistics
import time
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="Qt widgets backend is unavailable", exc_type=ImportError)

from PyQt6 import QtWidgets

from experiments import current_program_logger as module


class _MemorySettings:
    shared_values: dict[str, object] = {}

    def __init__(self, *_args: object) -> None:
        self.values = self.shared_values

    def value(self, key: str, default: object = None, **_kwargs: object) -> object:
        return self.values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _MemorySettings.shared_values.clear()
    monkeypatch.setattr(module.QtCore, "QSettings", _MemorySettings)


def _app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_bundle_estimate_uses_ceiling_and_milliamp_units():
    wires, single, total = module.bundle_estimate(6.575, 10.0, 175.0)
    assert wires == 305
    assert single == pytest.approx(0.0175)
    assert total == pytest.approx(5.3375)
    assert module.bundle_estimate(2000, 0, 100) == (1, 0, 0)
    for load, current, resistance in [(0, 10, 100), (1, -1, 100), (1, 1, float('nan'))]:
        with pytest.raises(ValueError):
            module.bundle_estimate(load, current, resistance)


def test_bundle_panel_updates_and_persists_load(tmp_path):
    app = _app()
    window = module.CurrentProgramWindow()
    window.bundle_load_spin.setValue(6.575)
    window.initial_current_spin.setValue(1)
    window.set_blocks([module.CurrentBlock('Pulse', 10, 0.5)])
    window._refresh_bundle_estimate()
    assert '305 wires' in window.bundle_label.text()
    assert 'awaiting' in window.bundle_label.text()
    window._measured = [1, 10, 10, float('nan')]
    window._resistance = [100, 174, 175, 999]
    window._refresh_bundle_estimate()
    assert '5.34 W total' in window.bundle_label.text()
    window.set_blocks([module.CurrentBlock('Pulse', 20, 0.5)])
    window._refresh_bundle_estimate()
    assert '21.35 W total' in window.bundle_label.text()
    assert 'at 10 mA' in window.bundle_label.text()
    window._save_settings()
    window.show()
    app.processEvents()
    window.grab().save(str(tmp_path / 'bundle-panel.png'))
    window.close()
    restored = module.CurrentProgramWindow()
    assert restored.bundle_load_spin.value() == 6.575
    restored.close()


def test_recipe_engine_ramps_pulses_then_holds_indefinitely() -> None:
    blocks = [
        module.CurrentBlock("Ramp", 20.0, 4.0, "warm up"),
        module.CurrentBlock("Pulse", 30.0, 2.0, "pulse"),
        module.CurrentBlock("Hold", 1.0, None, "readout"),
    ]
    module.validate_recipe(blocks, max_current_mA=50.0)
    engine = module.RecipeEngine(blocks, initial_current_mA=0.0)

    assert engine.state_at(2.0).target_mA == pytest.approx(10.0)
    assert engine.state_at(4.0).block.kind == "Pulse"
    assert engine.state_at(4.0).target_mA == pytest.approx(30.0)
    assert engine.state_at(6.0).block.kind == "Hold"
    assert engine.state_at(600.0).target_mA == pytest.approx(1.0)
    assert not engine.state_at(600.0).sequence_complete


def test_finite_recipe_completes_but_keeps_final_setpoint() -> None:
    engine = module.RecipeEngine(
        [module.CurrentBlock("Pulse", 20.0, 1.0)],
        initial_current_mA=1.0,
    )

    state = engine.state_at(1.1)

    assert state.sequence_complete
    assert state.target_mA == pytest.approx(20.0)


def test_finite_recipe_repeats_requested_number_of_cycles() -> None:
    engine = module.RecipeEngine(
        [
            module.CurrentBlock("Hold", 1.0, 1.0),
            module.CurrentBlock("Ramp", 21.0, 2.0),
            module.CurrentBlock("Hold", 2.0, 1.0),
        ],
        initial_current_mA=0.0,
        repeat_count=3,
    )

    second_cycle_ramp = engine.state_at(5.5)
    complete = engine.state_at(12.1)

    assert second_cycle_ramp.cycle_index == 1
    assert second_cycle_ramp.block.kind == "Ramp"
    assert second_cycle_ramp.target_mA == pytest.approx(6.0)
    assert complete.sequence_complete
    assert complete.cycle_index == 2
    assert complete.target_mA == pytest.approx(2.0)


def test_finite_recipe_can_repeat_forever() -> None:
    engine = module.RecipeEngine(
        [module.CurrentBlock("Pulse", 20.0, 1.0), module.CurrentBlock("Hold", 1.0, 1.0)],
        initial_current_mA=1.0,
        repeat_count=None,
    )

    state = engine.state_at(100.25)

    assert state.cycle_index == 50
    assert state.block.kind == "Pulse"
    assert not state.sequence_complete


@pytest.mark.parametrize(
    ("blocks", "message"),
    [
        ([], "at least one"),
        ([module.CurrentBlock("Pulse", 20.0, None)], "Only a Hold"),
        (
            [module.CurrentBlock("Hold", 1.0, None), module.CurrentBlock("Pulse", 20.0, 1.0)],
            "final block",
        ),
        ([module.CurrentBlock("Pulse", 200.0, 1.0)], "exceeds"),
    ],
)
def test_recipe_validation_rejects_unreachable_or_unsafe_programs(
    blocks: list[module.CurrentBlock], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        module.validate_recipe(blocks, max_current_mA=100.0)


def test_recipe_validation_rejects_indefinite_hold_in_repeated_program() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        module.validate_recipe(
            [module.CurrentBlock("Hold", 1.0, None)],
            max_current_mA=100.0,
            repeat_count=None,
        )


def test_window_starts_with_safe_ramp_and_indefinite_hold() -> None:
    app = _app()
    window = module.CurrentProgramWindow()
    try:
        app.processEvents()
        blocks = window.blocks()
        assert [(block.kind, block.target_mA, block.duration_s) for block in blocks] == [
            ("Hold", 1.0, 3.0),
            ("Ramp", 4.0, 2.0),
            ("Hold", 1.0, None),
        ]
        window.add_block("Ramp", target_mA=12.0, duration_s=3.0)
        assert window.blocks()[-1].kind == "Ramp"
    finally:
        window.close()


def test_keithley_mode_defaults_to_five_milliamp_run_limit() -> None:
    app = _app()
    window = module.CurrentProgramWindow()
    try:
        window.mode_combo.setCurrentText("Keithley 2636B (VISA)")
        app.processEvents()
        assert window.max_current_spin.value() == pytest.approx(5.0)
        assert window.voltage_spin.value() == pytest.approx(1.0)
        assert max(block.target_mA for block in window.blocks()) <= 5.0
        assert window.control_rate_spin.value() == pytest.approx(100.0)
        assert window.measurement_rate_spin.value() == pytest.approx(10.0)
        assert window.ui_rate_spin.value() == pytest.approx(10.0)
    finally:
        window.close()


def test_complete_setup_is_restored_for_future_runs() -> None:
    app = _app()
    window = module.CurrentProgramWindow()
    window.mode_combo.setCurrentText("Keithley 2636B (VISA)")
    window.voltage_spin.setValue(0.8)
    window.max_current_spin.setValue(5.0)
    window.initial_current_spin.setValue(0.2)
    window.resistance_limit_check.setChecked(True)
    window.max_resistance_spin.setValue(190.0)
    window.control_rate_spin.setValue(125.0)
    window.measurement_rate_spin.setValue(8.0)
    window.ui_rate_spin.setValue(1.5)
    window.repeat_mode_combo.setCurrentText("Fixed cycles")
    window.repeat_count_spin.setValue(3)
    window.name_edit.setText("remember-me")
    window.set_blocks(
        [
            module.CurrentBlock("Hold", 10.0, 4.0, "hot"),
            module.CurrentBlock("Ramp", 0.2, 2.0, "cool"),
        ]
    )
    window.close()
    app.processEvents()

    restored = module.CurrentProgramWindow()
    try:
        assert restored.mode_combo.currentText() == "Keithley 2636B (VISA)"
        assert restored.voltage_spin.value() == pytest.approx(0.8)
        assert restored.max_current_spin.value() == pytest.approx(5.0)
        assert restored.initial_current_spin.value() == pytest.approx(0.2)
        assert restored.resistance_limit_check.isChecked()
        assert restored.max_resistance_spin.value() == pytest.approx(190.0)
        assert restored.control_rate_spin.value() == 125.0
        assert restored.measurement_rate_spin.value() == 8.0
        assert restored.ui_rate_spin.value() == 1.5
        assert restored._repeat_count() == 3  # noqa: SLF001 - focused persistence hook
        assert restored.name_edit.text() == "remember-me"
        assert restored.blocks() == [
            module.CurrentBlock("Hold", 10.0, 4.0, "hot"),
            module.CurrentBlock("Ramp", 0.2, 2.0, "cool"),
        ]
    finally:
        restored.close()


def test_live_plot_tracks_resistance_on_secondary_axis() -> None:
    if module.pg is None:
        pytest.skip("pyqtgraph is unavailable")
    _app()
    window = module.CurrentProgramWindow()
    try:
        window._on_sample(  # noqa: SLF001 - focused UI data hook
            {
                "elapsed_s": 1.0,
                "target_current_mA": 2.0,
                "measured_current_mA": 1.999,
                "resistance_ohm": 145.8,
            }
        )
        assert window._resistance == [145.8]  # noqa: SLF001 - focused UI data hook
        x_values, y_values = window.resistance_curve.getData()
        assert list(x_values) == [1.0]
        assert list(y_values) == [145.8]
        current_values, resistance_values = window.resistance_current_curve.getData()
        assert list(current_values) == [1.999]
        assert list(resistance_values) == [145.8]
        assert window._power_mw_at_current(1.999) == pytest.approx(  # noqa: SLF001
            1.999**2 * 145.8 / 1000.0
        )
        assert window.plot.getPlotItem().legend is None
        assert window.resistance_current_plot.getPlotItem().legend is None
        top_axis = window.resistance_current_plot.getPlotItem().getAxis("top")
        assert top_axis.labelText == "Power"
        assert top_axis.labelUnits == "mW"
    finally:
        window.close()


def test_worker_logs_continuously_and_turns_adapter_off(tmp_path: Path) -> None:
    class Adapter:
        description = "test adapter"

        def __init__(self) -> None:
            self.target = 0.0
            self.closed = False

        def open(self) -> None:
            pass

        def configure(self, *, voltage_v: float, current_mA: float) -> None:
            assert voltage_v == 5.0
            self.target = current_mA

        def set_current(self, current_mA: float) -> None:
            self.target = current_mA

        def measure(self) -> dict[str, float]:
            return {"current_mA": self.target, "voltage_V": self.target * 0.1}

        def close(self) -> None:
            self.closed = True

    adapter = Adapter()
    config = module.RunConfig(
        blocks=(module.CurrentBlock("Hold", 1.0, None),),
        output_dir=tmp_path,
        run_name="test",
        control_rate_hz=100.0,
        measurement_rate_hz=100.0,
        ui_rate_hz=50.0,
        initial_current_mA=1.0,
        max_current_mA=20.0,
        voltage_limit_v=5.0,
    )
    worker = module.ProgramWorker(config, adapter)
    worker.start()
    time.sleep(0.06)
    worker.stop()
    assert worker.wait(2000)

    run_dirs = list(tmp_path.glob("test_*"))
    assert len(run_dirs) == 1
    with (run_dirs[0] / "measurement.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    metadata = json.loads((run_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
    assert len(rows) >= 2
    assert rows[-1]["block_type"] == "Hold"
    assert rows[-1]["cycle_index"] == "1"
    assert float(rows[-1]["power_mW"]) == pytest.approx(0.1)
    assert metadata["status"] == "stopped"
    assert metadata["rows"] == len(rows)
    assert metadata["control_rate_hz"] == 100.0
    assert metadata["measurement_rate_hz"] == 100.0
    assert metadata["ui_rate_hz"] == 50.0
    assert adapter.closed


def test_resistance_limit_latches_current_ceiling_for_remainder_of_run(tmp_path: Path) -> None:
    class Adapter:
        description = "resistance-limit adapter"

        def __init__(self) -> None:
            self.target = 0.0
            self.targets: list[float] = []

        def open(self) -> None:
            pass

        def configure(self, *, voltage_v: float, current_mA: float) -> None:
            self.target = current_mA

        def set_current(self, current_mA: float) -> None:
            self.target = current_mA
            self.targets.append(current_mA)

        def measure(self) -> dict[str, float]:
            return {
                "current_mA": self.target,
                "voltage_V": self.target * 200.0 / 1000.0,
            }

        def close(self) -> None:
            pass

    adapter = Adapter()
    config = module.RunConfig(
        blocks=(
            module.CurrentBlock("Hold", 1.0, 0.04),
            module.CurrentBlock("Hold", 4.0, 0.08),
        ),
        output_dir=tmp_path,
        run_name="resistance-limit",
        control_rate_hz=100.0,
        measurement_rate_hz=100.0,
        ui_rate_hz=50.0,
        initial_current_mA=1.0,
        max_current_mA=5.0,
        voltage_limit_v=1.0,
        max_resistance_ohm=190.0,
    )
    worker = module.ProgramWorker(config, adapter)
    worker.start()
    time.sleep(0.14)
    worker.stop()
    assert worker.wait(2000)

    assert adapter.targets
    assert max(adapter.targets) == pytest.approx(1.0)
    run_dir = next(tmp_path.glob("resistance-limit_*"))
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    with (run_dir / "measurement.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert metadata["max_resistance_ohm"] == pytest.approx(190.0)
    assert metadata["resistance_limit_reached"] is True
    assert metadata["resistance_current_ceiling_mA"] == pytest.approx(1.0)
    assert any(row["block_index"] == "2" for row in rows)
    assert max(float(row["target_current_mA"]) for row in rows) == pytest.approx(1.0)
    assert all(row["resistance_limit_reached"] == "True" for row in rows)


@pytest.mark.parametrize("action", ["readout_current", "output_off"])
def test_fast_protection_action_between_log_samples(tmp_path: Path, action: str) -> None:
    class Adapter:
        description = "fake protection adapter"
        def __init__(self) -> None:
            self.target = 0.5
            self.calls = 0
            self.targets = []
            self.closed = False
        def open(self): pass
        def configure(self, **kwargs): self.target = kwargs["current_mA"]
        def set_current(self, value):
            assert not self.closed
            self.target = value
            self.targets.append(value)
        def measure(self):
            self.calls += 1
            return {"current_mA": self.target,
                    "voltage_V": self.target * (200 if self.calls >= 4 else 150) / 1000}
        def close(self): self.closed = True
    adapter = Adapter()
    config = module.RunConfig(
        blocks=(module.CurrentBlock("Hold", 5.0, None),), output_dir=tmp_path,
        run_name="fast", control_rate_hz=100, measurement_rate_hz=1,
        ui_rate_hz=10, initial_current_mA=0.5, max_current_mA=5,
        voltage_limit_v=2, max_resistance_ohm=190,
        resistance_action=action, resistance_check_rate_hz=100,
    )
    worker = module.ProgramWorker(config, adapter)
    worker.start()
    if action == "readout_current":
        time.sleep(0.2)
        worker.stop()
    assert worker.wait(2000)
    assert adapter.closed
    metadata = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert metadata["resistance_limit_reached"]
    assert metadata["protection_checks"] >= 3
    assert metadata["rows"] < metadata["protection_checks"]
    if action == "readout_current":
        assert adapter.targets == [5.0, 0.5]
        assert adapter.calls > 4
    else:
        assert adapter.calls == 4
        assert adapter.targets == [5.0]


def test_protection_settings_persist() -> None:
    app = _app()
    window = module.CurrentProgramWindow()
    window.resistance_action_combo.setCurrentIndex(window.resistance_action_combo.findData("readout_current"))
    window.resistance_check_rate_spin.setValue(250)
    window._save_settings()
    other = module.CurrentProgramWindow()
    assert other.resistance_action_combo.currentData() == "readout_current"
    assert other.resistance_check_rate_spin.value() == 250
    window.close()
    other.close()


@pytest.mark.parametrize("direction,hit,miss", [("above",171,169),("below",169,171)])
def test_resistance_step_confirmation_and_gap_reset(direction,hit,miss):
    engine = module.ConditionalRecipeEngine((module.CurrentBlock("Hold",5,1,"",170,direction),
                                            module.CurrentBlock("Hold",.1,None)),initial_current_mA=.1)
    assert not engine.observe(.001,hit)
    assert not engine.observe(.002,miss)
    assert not engine.observe(.003,hit)
    assert not engine.observe(.004,hit)
    assert not engine.observe(.020,hit)  # gap resets
    assert not engine.observe(.021,float('nan'))
    assert not engine.observe(.022,hit)
    assert not engine.observe(.023,hit)
    assert engine.observe(.024,hit)
    assert engine.state_at(.024).target_mA == .1


def test_resistance_timeout_never_advances_and_final_ramp_does_not_jump():
    e = module.ConditionalRecipeEngine((module.CurrentBlock("Hold",2,.1,"",170),module.CurrentBlock("Hold",5,None)),initial_current_mA=.1)
    e.observe(.099,180)
    assert not e.observe(.100,180)
    assert e.timed_out and e.index == 0
    e = module.ConditionalRecipeEngine((module.CurrentBlock("Ramp",5,1,"",170),),initial_current_mA=1)
    for t in [.1,.101,.102]: e.observe(t,175)
    assert e.complete
    assert e.state_at(.5).target_mA == pytest.approx(1.408)


def test_resistance_fields_ui_roundtrip():
    app = _app()
    w = module.CurrentProgramWindow()
    block = module.CurrentBlock("Hold",.1,25,"cool",150,"below")
    w.set_blocks([block])
    assert w.blocks() == [block]
    w._duplicate_selected()
    assert w.blocks() == [block,block]
    w._save_settings()
    other = module.CurrentProgramWindow()
    assert other.blocks() == [block,block]
    w.close(); other.close()


def test_run_control_buttons_have_matching_height():
    app = _app()
    w = module.CurrentProgramWindow()
    assert w.readout_button.minimumHeight() == w.start_button.minimumHeight() == w.stop_button.minimumHeight() == 42
    layout = w.readout_button.parentWidget().layout()
    # All controls remain in the same horizontal action row.
    assert w.readout_button.parentWidget() is w.start_button.parentWidget()
    w.close()


def test_history_preview_bounded_and_readonly(tmp_path):
    path = tmp_path/'measurement.csv'
    path.write_text('elapsed_s,target_current_mA,measured_current_mA,resistance_ohm\n'+
                    '\n'.join(f'{i},1,1,150' for i in range(100)),encoding='utf-8')
    original = path.read_bytes()
    result = module.read_history_preview(path,limit=10)
    assert result['count'] == 100 and result['sampled']
    assert len(result['points']) <= 10
    assert result['points'][0][0] == 0
    assert result['points'][-1][0] == 99
    assert path.read_bytes() == original


def test_history_preview_ignores_stale_results():
    app = _app()
    preview = module.HistoryPreview()
    preview.token = 2
    preview.pending_path = Path('synthetic/measurement.csv')
    preview.caption.setText('current selection')
    preview._ready(1,None,'old failure')
    assert preview.caption.text() == 'current selection'
    preview._ready(2,dict(points=[(0,1,1,150),(1,2,2,160)],count=2,sampled=False),'')
    assert '2 rows' in preview.caption.text()
    if module.pg is not None:
        assert len(preview.curves) == 4
    preview.close()


def test_worker_resistance_timeout_enters_readout(tmp_path):
    class Adapter:
        description = "timeout fake"
        def open(self): pass
        def configure(self,**kw): self.current=kw['current_mA']; self.commands=[]; self.calls=0
        def set_current(self,v): self.current=v; self.commands.append(v)
        def close(self): self.closed=True
        def measure(self):
            self.calls+=1
            if self.calls>20: worker.stop()
            return dict(current_mA=self.current,voltage_V=self.current*.15)
    a=Adapter()
    config=module.RunConfig(blocks=(module.CurrentBlock("Hold",2,.03,"",170),module.CurrentBlock("Hold",5,None)),
        output_dir=tmp_path,run_name="timeout",control_rate_hz=1000,measurement_rate_hz=100,
        ui_rate_hz=10,initial_current_mA=.1,max_current_mA=5,voltage_limit_v=2,resistance_check_rate_hz=200)
    worker=module.ProgramWorker(config,a)
    worker.run()
    assert a.commands == [2,.1]
    m=json.loads(next(tmp_path.glob('*/metadata.json')).read_text())
    assert m['sequence_events'][0]['event']=='resistance_timeout'


@pytest.mark.parametrize("trip", [False, True])
def test_readout_restart_preserves_connection_and_trip_latch(tmp_path: Path, trip: bool) -> None:
    class Adapter:
        description = "readout fake"
        def __init__(self):
            self.current = .5
            self.opens = 0
            self.closes = 0
            self.calls = 0
            self.commands = []
        def open(self): self.opens += 1
        def configure(self, **kw): self.current = kw["current_mA"]
        def close(self): self.closes += 1
        def set_current(self, value):
            self.current = value
            self.commands.append(value)
        def measure(self):
            self.calls += 1
            if self.calls == 5: worker.request_readout()
            if self.calls == 12: worker.request_sequence((module.CurrentBlock("Hold", 3, None),), 1)
            if self.calls == 20: worker.stop()
            return {"current_mA":self.current, "voltage_V":self.current*(200 if trip and self.calls==4 else 150)/1000}
    adapter = Adapter()
    config = module.RunConfig(blocks=(module.CurrentBlock("Hold", 5, .1),),
        repeat_count=None, output_dir=tmp_path, run_name="readout",control_rate_hz=100,
        measurement_rate_hz=100,ui_rate_hz=100,initial_current_mA=.5,max_current_mA=5,
        voltage_limit_v=2,max_resistance_ohm=190,resistance_action="readout_current")
    worker = module.ProgramWorker(config,adapter)
    worker.run()
    assert adapter.opens == adapter.closes == 1
    assert adapter.commands == ([5,.5,.5] if trip else [5,.5,3])
    rows = list(csv.DictReader(next(tmp_path.glob("*/measurement.csv")).open()))
    assert any(r['operating_mode']=='readout' for r in rows)
    assert max(int(r['sequence_id']) for r in rows) == (1 if trip else 2)
    elapsed = [float(r['elapsed_s']) for r in rows]
    assert elapsed == sorted(elapsed)


def test_measurement_history_discovers_and_loads_saved_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "sample_20260901-120000"
    run_dir.mkdir()
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "started_utc": "2026-09-01T12:00:00.000Z",
                "status": "stopped",
                "rows": 2,
            }
        ),
        encoding="utf-8",
    )
    with (run_dir / "measurement.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=module.CSV_FIELDS)
        writer.writeheader()
        for elapsed, current, resistance in ((0.0, 0.5, 140.0), (1.0, 2.0, 150.0)):
            writer.writerow(
                {
                    "elapsed_s": elapsed,
                    "target_current_mA": current,
                    "measured_current_mA": current,
                    "resistance_ohm": resistance,
                }
            )

    entries = module.find_run_history(tmp_path)
    assert len(entries) == 1
    assert entries[0].rows == 2

    _app()
    window = module.CurrentProgramWindow()
    try:
        window._load_history_csv(run_dir / "measurement.csv")  # noqa: SLF001
        assert window._times == [0.0, 1.0]  # noqa: SLF001
        assert window._measured == [0.5, 2.0]  # noqa: SLF001
        assert window._resistance == [140.0, 150.0]  # noqa: SLF001
        assert "Viewing history" in window.status_label.text()
    finally:
        window.close()


def test_worker_absorbs_adapter_warmup_before_recipe_clock(tmp_path: Path) -> None:
    class Adapter:
        description = "warmup adapter"

        def open(self) -> None:
            pass

        def configure(self, *, voltage_v: float, current_mA: float) -> None:
            pass

        def warm_up_measurement(self) -> None:
            time.sleep(0.05)

        def set_current(self, current_mA: float) -> None:
            pass

        def measure(self) -> dict[str, float]:
            return {"current_mA": 1.0, "voltage_V": 0.1}

        def close(self) -> None:
            pass

    config = module.RunConfig(
        blocks=(module.CurrentBlock("Hold", 1.0, None),),
        output_dir=tmp_path,
        run_name="warmup",
        control_rate_hz=100.0,
        measurement_rate_hz=20.0,
        ui_rate_hz=10.0,
        initial_current_mA=1.0,
        max_current_mA=5.0,
        voltage_limit_v=1.0,
    )
    worker = module.ProgramWorker(config, Adapter())
    worker.start()
    time.sleep(0.12)
    worker.stop()
    assert worker.wait(2000)

    run_dir = next(tmp_path.glob("warmup_*"))
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    with (run_dir / "measurement.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert metadata["warm_up_measurement_s"] >= 0.045
    assert float(rows[0]["elapsed_s"]) < 0.02


def test_worker_controls_faster_than_it_measures(tmp_path: Path) -> None:
    class Adapter:
        description = "rate test adapter"

        def __init__(self) -> None:
            self.targets: list[float] = []
            self.control_times: list[float] = []
            self.measurements = 0

        def open(self) -> None:
            pass

        def configure(self, *, voltage_v: float, current_mA: float) -> None:
            pass

        def set_current(self, current_mA: float) -> None:
            self.targets.append(current_mA)
            self.control_times.append(time.monotonic())

        def measure(self) -> dict[str, float]:
            self.measurements += 1
            return {"current_mA": self.targets[-1], "voltage_V": 0.1}

        def close(self) -> None:
            pass

    adapter = Adapter()
    config = module.RunConfig(
        blocks=(module.CurrentBlock("Ramp", 3.0, 2.0),),
        output_dir=tmp_path,
        run_name="rates",
        control_rate_hz=100.0,
        measurement_rate_hz=10.0,
        ui_rate_hz=2.0,
        initial_current_mA=0.1,
        max_current_mA=5.0,
        voltage_limit_v=1.0,
    )
    worker = module.ProgramWorker(config, adapter)
    worker.start()
    time.sleep(0.24)
    worker.stop()
    assert worker.wait(2000)

    assert len(adapter.targets) >= 10
    assert 2 <= adapter.measurements <= 4
    assert len(adapter.targets) >= adapter.measurements * 3
    assert adapter.targets == sorted(adapter.targets)
    intervals = [
        later - earlier
        for earlier, later in zip(adapter.control_times, adapter.control_times[1:])
    ]
    assert statistics.median(intervals) == pytest.approx(0.01, abs=0.006)


def test_visa_discovery_closes_resource_manager() -> None:
    class Manager:
        closed = False

        def list_resources(self) -> tuple[str, ...]:
            return ("USB0::0x05E6::0x2636::123::INSTR",)

        def close(self) -> None:
            self.closed = True

    manager = Manager()

    assert module.list_visa_resources(lambda: manager) == (
        "USB0::0x05E6::0x2636::123::INSTR",
    )
    assert manager.closed


def test_keithley_2636_adapter_uses_current_source_remote_sense_and_safe_shutdown() -> None:
    class Instrument:
        timeout = 0
        write_termination = ""
        read_termination = ""

        def __init__(self) -> None:
            self.commands: list[str] = []
            self.closed = False

        def write(self, command: str) -> None:
            self.commands.append(command)

        def query(self, command: str) -> str:
            self.commands.append(command)
            if command == "*IDN?":
                return "Keithley Instruments Inc., Model 2636B, 1234567, 3.3.5"
            if "measure.iv" in command:
                return "0.0201\t3.42"
            return "0"

        def close(self) -> None:
            self.closed = True

    class Manager:
        def __init__(self, instrument: Instrument) -> None:
            self.instrument = instrument
            self.opened = ""
            self.closed = False

        def open_resource(self, resource_name: str) -> Instrument:
            self.opened = resource_name
            return self.instrument

        def close(self) -> None:
            self.closed = True

    instrument = Instrument()
    manager = Manager(instrument)
    adapter = module.Keithley2636Adapter(
        resource_name="USB0::0x05E6::0x2636::123::INSTR",
        channel="A",
        remote_sense=True,
        current_limit_mA=5.0,
        resource_manager_factory=lambda: manager,
    )

    adapter.open()
    adapter.configure(voltage_v=20.0, current_mA=1.0)
    adapter.set_current(4.0)
    readback = adapter.measure()
    adapter.close()

    assert manager.opened.endswith("::INSTR")
    assert "smua.source.func = smua.OUTPUT_DCAMPS" in instrument.commands
    assert "smua.source.rangei = 0.01" in instrument.commands
    assert "smua.sense = smua.SENSE_REMOTE" in instrument.commands
    assert "smua.measure.autorangev = smua.AUTORANGE_ON" in instrument.commands
    assert not any("smua.measure.rangev" in command for command in instrument.commands)
    assert "smua.measure.autozero = smua.AUTOZERO_OFF" in instrument.commands
    assert "print(smua.measure.iv())" in instrument.commands
    assert "smua.source.leveli = 0.004" in instrument.commands
    assert readback == {"current_mA": pytest.approx(20.1), "voltage_V": 3.42}
    assert instrument.commands[-2:] == [
        "smua.source.leveli = 0",
        "smua.source.output = smua.OUTPUT_OFF",
    ]
    assert instrument.closed
    assert manager.closed


def test_keithley_adapter_rejects_current_above_run_limit() -> None:
    class Instrument:
        timeout = 0
        write_termination = ""
        read_termination = ""

        def write(self, _command: str) -> None:
            pass

        def query(self, command: str) -> str:
            return "Keithley Instruments Inc., Model 2636B" if command == "*IDN?" else "0"

        def close(self) -> None:
            pass

    class Manager:
        def open_resource(self, _resource_name: str) -> Instrument:
            return Instrument()

        def close(self) -> None:
            pass

    adapter = module.Keithley2636Adapter(
        resource_name="USB::TEST",
        channel="A",
        remote_sense=True,
        current_limit_mA=5.0,
        resource_manager_factory=Manager,
    )
    adapter.open()
    try:
        with pytest.raises(ValueError, match="5 mA run limit"):
            adapter.set_current(5.1)
    finally:
        adapter.close()


def test_experiment_launches_in_isolated_process(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[object] = []
    monkeypatch.setattr(module, "launch_experiment_process", launched.append)

    assert module.launch() is None
    assert len(launched) == 1
    spec = launched[0]
    assert spec.module == "experiments.current_program_logger"
    assert spec.resource_tag == "current_program_logger"
