"""Software-only fault, buffering and sleep ownership regressions."""
import csv
import json
import threading
import time
from dataclasses import replace

import pytest

from experiments import current_program_logger as m
from experiments.current_program_support import BufferedCsv, ElectricalLimits, ElectricalMonitor
from plotting.shared.power_guard import NoopSleepGuard


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    from tests.test_experiment_current_program_logger import _MemorySettings
    _MemorySettings.shared_values.clear()
    monkeypatch.setattr(m.QtCore, "QSettings", _MemorySettings)
    monkeypatch.setattr(m, "list_visa_resources", lambda: [])
    monkeypatch.setattr(m, "create_experiment_sleep_guard", lambda *a, **k: NoopSleepGuard())


def config(tmp_path, **changes):
    c = m.RunConfig(blocks=(m.CurrentBlock("Hold", 80, None),), output_dir=tmp_path,
                    run_name="safety", control_rate_hz=100, measurement_rate_hz=1,
                    ui_rate_hz=1, initial_current_mA=0, max_current_mA=100, voltage_limit_v=10,
                    log_rate_hz=1, resistance_check_rate_hz=100,
                    electrical_limits=ElectricalLimits(10, .25, 10, .03))
    return replace(c, **changes)


class Adapter:
    description = "synthetic only"

    def __init__(self, kind="normal"):
        self.kind, self.target, self.calls = kind, 0, 0
        self.closed = False
        self.events = []

    def open(self):
        self.events.append("open")

    def configure(self, **kw):
        self.target = kw["current_mA"]
        self.events.append("configure")

    def set_current(self, value):
        assert not self.closed
        self.events.append("set")
        self.target = value

    def measure(self):
        self.calls += 1
        if self.calls >= 3:
            if self.kind == "short":
                return dict(current_mA=self.target, voltage_V=0)
            if self.kind == "open":
                return dict(current_mA=0, voltage_V=10)
            if self.kind == "invalid":
                return dict(current_mA=float("nan"), voltage_V=1)
        return dict(current_mA=self.target, voltage_V=self.target*.1)

    def close(self):
        self.events.append("close")
        self.closed = True


@pytest.mark.parametrize("kind", ["short", "open", "invalid"])
@pytest.mark.parametrize("record", [True, False])
def test_fault_shutdown_independent_of_csv_ui_and_legacy_hold(tmp_path, kind, record):
    a = Adapter(kind)
    a.resistance_quality = lambda: "settling"  # Qualification never mutes faults.
    # Retain legacy high-R protection too: zero-I detection must not divide by zero.
    w = m.ProgramWorker(config(tmp_path, record_csv=record, max_resistance_ohm=190), a)
    w.coalesce_ui = True  # GUI never acknowledges: acquisition must continue anyway.
    w.start()
    assert w.wait(3000)
    assert a.closed and a.events[-1] == "close"
    data = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert data["status"] == "failed"
    assert data["electrical_fault"]
    assert data["fault_sample"]["elapsed_s"] < 1  # between 1-Hz CSV/UI updates
    assert data["protection_checks"] >= 2
    paths = list(tmp_path.glob("*/measurement.csv"))
    assert bool(paths) == record
    if record:
        with paths[0].open(newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[-1]["electrical_fault"]
        assert len(rows) == data["rows"]
    else:
        assert data["rows"] == 0


def test_zero_steps_and_transient_contact_loss_reset_confirmation():
    monitor = ElectricalMonitor(ElectricalLimits(10, .25, 10, .2))
    assert monitor.check(0, 80, 0, 10) is None
    assert monitor.check(.1, 80, 80, 8) is None
    assert monitor.check(.3, 80, 0, 10) is None
    assert monitor.check(.4, 0, 0, 0) is None
    assert monitor.check(2, 80, 0, 10) is None
    assert monitor.check(2.1, 80, 0, 10) is None
    assert "contact loss" in monitor.check(2.21, 80, 0, 10)
    assert monitor.check(3, 80, 80, 8)  # remains latched even after recovery


def test_settling_keeps_raw_csv_but_qualifies_separately(tmp_path):
    a = Adapter()
    a.resistance_quality = lambda: "settling" if a.calls < 4 else "settled_interval"
    w = m.ProgramWorker(config(tmp_path, initial_current_mA=10,
        electrical_limits=ElectricalLimits(), measurement_rate_hz=100, log_rate_hz=100), a)
    original = a.measure
    def measure():
        row = original()
        if a.calls >= 8:
            w.stop()
        return row
    a.measure = measure
    w.start()
    assert w.wait(3000)
    assert a.closed
    with next(tmp_path.glob('*/measurement.csv')).open(newline='') as f:
        rows = list(csv.DictReader(f))
    assert any(r['resistance_quality'] == 'settling' for r in rows)
    assert any(r['resistance_quality'] == 'settled_interval' for r in rows)
    for r in rows:
        assert float(r['resistance_ohm']) == pytest.approx(100)
        if r['resistance_quality'] == 'settling':
            assert r['qualified_resistance_ohm'] == ''
        else:
            assert float(r['qualified_resistance_ohm']) == pytest.approx(100)


def test_short_threshold_and_detection_floor():
    mon = ElectricalMonitor(ElectricalLimits(short_resistance_ohm=10))
    assert mon.check(0, 0, 0, 0) is None
    assert mon.check(1, 5, 5, 0) is None
    assert mon.check(2, 80, 80, .801) is None
    assert "short circuit" in mon.check(3, 80, 80, .8)


@pytest.mark.parametrize("kwargs", [dict(short_resistance_ohm=0), dict(open_current_fraction=1),
                                     dict(active_above_mA=0), dict(open_confirm_s=float("nan"))])
def test_limits_reject_invalid(kwargs):
    with pytest.raises(ValueError):
        ElectricalLimits(**kwargs)


def test_sleep_guard_thread_ownership_and_shutdown_order(tmp_path, monkeypatch):
    a = Adapter("short")
    threads = []
    class Guard:
        def acquire(self):
            threads.append(threading.get_ident())
            a.events.append("awake")
        def release(self):
            threads.append(threading.get_ident())
            assert a.closed
            a.events.append("release")
    def factory(*args, **kwargs):
        assert kwargs == {"keep_display_awake": False}
        return Guard()
    monkeypatch.setattr(m, "create_experiment_sleep_guard", factory)
    w = m.ProgramWorker(config(tmp_path), a)
    w.start()
    assert w.wait(3000)
    assert a.events[0] == "awake"
    assert a.events[-2:] == ["close", "release"]
    assert threads[0] == threads[1] != threading.get_ident()


def test_sleep_failure_never_opens_hardware(tmp_path, monkeypatch):
    class Guard(NoopSleepGuard):
        def acquire(self):
            raise OSError("sleep prevention unavailable")
    monkeypatch.setattr(m, "create_experiment_sleep_guard", lambda *a, **k: Guard())
    a = Adapter()
    w = m.ProgramWorker(config(tmp_path), a)
    w.run()
    assert a.events == []
    assert "sleep prevention" in json.loads(next(tmp_path.glob("*/metadata.json")).read_text())["error"]


def test_fault_shutdown_precedes_fault_log_submission(tmp_path, monkeypatch):
    a = Adapter("short")
    real_submit = BufferedCsv.submit
    def submit(self, row, **kw):
        if row.get("electrical_fault"):
            assert a.closed
        real_submit(self, row, **kw)
    monkeypatch.setattr(BufferedCsv, "submit", submit)
    w = m.ProgramWorker(config(tmp_path), a)
    w.run()
    assert a.closed


def test_bounded_csv_queue_and_writer_error(tmp_path):
    writer = BufferedCsv(tmp_path/"data.csv", ["n"], capacity=2)
    writer.submit(dict(n=1))
    writer.submit(dict(n=2))
    with pytest.raises(RuntimeError, match="queue full"):
        writer.submit(dict(n=3))
    assert writer.queue.qsize() == 2
    writer.start()
    writer.close()
    assert writer.rows == 2
    broken = BufferedCsv(tmp_path/"missing"/"data.csv", ["n"])
    with pytest.raises(RuntimeError, match="CSV writer failed"):
        broken.start()
    with pytest.raises(RuntimeError, match="CSV writer failed"):
        broken.close()


def test_ui_bound_latch_reset_and_settings(qtbot, monkeypatch):
    window = m.CurrentProgramWindow()
    qtbot.addWidget(window)
    window.live_points_spin.setValue(100)
    monkeypatch.setattr(window, "_refresh_plot_data", lambda: None)
    for n in range(500):
        window._on_sample(dict(elapsed_s=n, target_current_mA=80, measured_current_mA=80, resistance_ohm=100))
    for values in (window._times, window._targets, window._measured, window._resistance):
        assert len(values) == 100
    assert window._times[0] == 400
    monkeypatch.setattr(m.QtWidgets.QMessageBox, "critical", lambda *a: None)
    monkeypatch.setattr(m.QtWidgets.QMessageBox, "question", lambda *a: m.QtWidgets.QMessageBox.StandardButton.Yes)
    window._on_failure("short")
    window._on_finished()
    assert not window.start_button.isEnabled()
    window.start_run()
    assert window.worker is None
    window._reset_fault()
    assert window.start_button.isEnabled() and window.worker is None
    window.record_csv_check.setChecked(False)
    window.short_check.setChecked(True)
    window.open_check.setChecked(True)
    window.log_rate_spin.setValue(.5)
    window._save_settings()
    other = m.CurrentProgramWindow()
    qtbot.addWidget(other)
    assert other.short_check.isChecked() and other.open_check.isChecked()
    assert not other.record_csv_check.isChecked()
    assert other.live_points_spin.value() == 100
    assert other.log_rate_spin.value() == .5


def test_refresh_replaces_stale_keithley_address(qtbot, monkeypatch):
    window = m.CurrentProgramWindow()
    qtbot.addWidget(window)
    window.mode_combo.setCurrentText("Siglent SPD1305X (VISA)")
    qtbot.waitUntil(lambda: not window._discovery_busy)
    window.visa_resource_combo.setCurrentText("USB0::0x05E6::0x2636::old::INSTR")
    warnings = []
    monkeypatch.setattr(m.QtWidgets.QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    window.start_run()
    assert window.worker is None and "not Siglent" in warnings[0]
    resource = "USB0::0xF4EC::0x1410::fake::INSTR"
    monkeypatch.setattr(m, "list_visa_resources", lambda: [resource])
    window._refresh_visa_resources()
    qtbot.waitUntil(lambda: not window._discovery_busy)
    assert window.visa_resource_combo.currentText() == resource


def test_writer_failure_shuts_hardware_before_join(tmp_path, monkeypatch):
    a = Adapter()
    class FailedWriter:
        rows = 0
        checks = 0
        def __init__(self, *args): pass
        def start(self): pass
        def submit(self, *args, **kwargs): pass
        def check(self):
            self.checks += 1
            if self.checks == 5:
                raise RuntimeError("simulated disk failure")
        def close(self):
            assert a.closed
    monkeypatch.setattr(m, "BufferedCsv", FailedWriter)
    w = m.ProgramWorker(config(tmp_path), a)
    w.run()
    data = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert "disk failure" in data["error"]
    assert a.closed


def test_sparse_logging_does_not_reduce_acquisition(tmp_path):
    a = Adapter()
    w = m.ProgramWorker(config(tmp_path), a)
    w.coalesce_ui = True
    w.start()
    time.sleep(.15)
    w.stop()
    assert w.wait(3000)
    data = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert data["status"] == "stopped"
    assert data["rows"] == 1
    assert data["protection_checks"] >= 5
    assert a.closed
