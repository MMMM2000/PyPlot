import json
import threading

import pytest

from experiments import current_program_logger as m
from experiments.current_program_support import ElectricalLimits
from tests.test_current_program_safety import Adapter, config
from tests.test_experiment_current_program_logger import _MemorySettings
from plotting.shared.power_guard import NoopSleepGuard

K = "Keithley 2636B (VISA)"
S = "Siglent SPD1305X (VISA)"
KR = "USB0::0x05E6::0x2636::fake::INSTR"
SR = "USB0::0xF4EC::0x1410::fake::INSTR"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    _MemorySettings.shared_values.clear()
    monkeypatch.setattr(m.QtCore, "QSettings", _MemorySettings)
    monkeypatch.setattr(m, "list_visa_resources", lambda: [KR, SR])
    monkeypatch.setattr(m, "create_experiment_sleep_guard", lambda *a, **kw: NoopSleepGuard())


def select(w, mode, qtbot):
    w.mode_combo.setCurrentText(mode)
    qtbot.waitUntil(lambda: not w._discovery_busy)


def test_profiles_restore_rates_limits_recipe_and_resource(qtbot):
    w = m.CurrentProgramWindow()
    qtbot.addWidget(w)
    select(w, K, qtbot)
    assert w.visa_resource_combo.currentText() == KR
    w.control_rate_spin.setValue(500)
    w.resistance_check_rate_spin.setValue(1000)
    w.voltage_spin.setValue(12)
    w.short_check.setChecked(True)
    w.set_blocks([m.CurrentBlock("Hold", 4, None)])
    select(w, S, qtbot)
    assert w.visa_resource_combo.currentText() == SR
    assert w.control_rate_spin.value() == 10
    assert w.control_rate_spin.maximum() == 10
    assert w.resistance_check_rate_spin.maximum() == 20
    assert w.log_rate_spin.maximum() == 20
    assert w.voltage_spin.value() == 1
    assert not w.short_check.isChecked()
    assert len(w.blocks()) == 3
    assert w.host_edit.isHidden()
    assert w.keithley_channel_combo.isHidden()
    assert not w.table.cellWidget(0, 6).model().item(1).isEnabled()
    w.control_rate_spin.setValue(7)
    select(w, K, qtbot)
    assert w.control_rate_spin.value() == 500
    assert w.resistance_check_rate_spin.value() == 1000
    assert w.voltage_spin.value() == 12
    assert w.short_check.isChecked()
    assert len(w.blocks()) == 1
    assert w.table.cellWidget(0, 6).model().item(1).isEnabled()
    w._save_settings()
    other = m.CurrentProgramWindow()
    qtbot.addWidget(other)
    qtbot.waitUntil(lambda: not other._discovery_busy)
    assert other.control_rate_spin.value() == 500
    select(other, S, qtbot)
    assert other.control_rate_spin.value() == 7


def test_legacy_migration_only_into_last_supply_and_clamps_rates(qtbot):
    _MemorySettings.shared_values.update(connection_mode=S, control_rate_hz=500,
                                        resistance_check_rate_hz=1000, voltage_limit_v=30,
                                        visa_resource=KR)
    w = m.CurrentProgramWindow()
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: not w._discovery_busy)
    assert w.control_rate_spin.value() == 10
    assert w.resistance_check_rate_spin.value() == 20
    assert w.visa_resource_combo.currentText() == SR
    assert "reduced" in w.profile_label.text()
    select(w, K, qtbot)
    assert w.control_rate_spin.value() == 100  # not migrated from the Siglent/flat profile
    assert w.voltage_spin.value() == 1
    assert _MemorySettings.shared_values["voltage_limit_v"] == 30  # legacy keys untouched


def test_late_discovery_cannot_replace_new_supply(qtbot, monkeypatch):
    gate = threading.Event()
    def slow():
        gate.wait(2)
        return [SR]
    monkeypatch.setattr(m, "list_visa_resources", slow)
    w = m.CurrentProgramWindow()
    qtbot.addWidget(w)
    w.mode_combo.setCurrentText(S)
    monkeypatch.setattr(m, "list_visa_resources", lambda: [KR])
    select(w, K, qtbot)
    assert w.visa_resource_combo.currentText() == KR
    gate.set()
    qtbot.waitUntil(lambda: not w._discovery_jobs)
    assert w.visa_resource_combo.currentText() == KR


def test_multiple_resources_require_choice_and_missing_does_not_keep_wrong_vendor(qtbot, monkeypatch):
    monkeypatch.setattr(m, "list_visa_resources", lambda: [KR, SR, SR.replace("fake", "second")])
    w = m.CurrentProgramWindow()
    qtbot.addWidget(w)
    select(w, S, qtbot)
    assert not w.visa_resource_combo.currentText()
    assert "Multiple" in w.visa_status_label.text()
    monkeypatch.setattr(m, "list_visa_resources", lambda: [KR])
    w.visa_resource_combo.setCurrentText(KR)
    w._refresh_visa_resources()
    qtbot.waitUntil(lambda: not w._discovery_busy)
    assert not w.visa_resource_combo.currentText()
    assert "No matching" in w.visa_status_label.text()


def test_startup_retries_zero_readbacks_without_advancing_recipe(tmp_path):
    class Slow(Adapter):
        startup_timeout_s = .1
        startup_poll_s = .005
        calls_at_set = None
        def measure(self):
            self.calls += 1
            if self.calls >= 6:
                w.stop()
            return dict(current_mA=self.target, voltage_V=0 if self.calls <= 2 else self.target*.1)
        def set_current(self, value):
            self.calls_at_set = self.calls
            super().set_current(value)
    a = Slow()
    w = m.ProgramWorker(config(tmp_path, initial_current_mA=1, max_resistance_ohm=190,
                               electrical_limits=ElectricalLimits()), a)
    w.run()
    assert a.calls_at_set >= 4  # two zeros, then two usable samples before ramp
    data = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert len(data["startup_samples"]) == 4
    assert data["status"] == "stopped" and a.closed


@pytest.mark.parametrize("kind", ["zero_voltage", "zero_current", "nan"])
def test_startup_unusable_readings_stop_without_any_recipe_command(tmp_path, kind):
    class Slow(Adapter):
        startup_timeout_s = .02
        startup_poll_s = .005
        def measure(self):
            return dict(current_mA=0 if kind == "zero_current" else (float("nan") if kind == "nan" else 1),
                        voltage_V=1 if kind == "zero_current" else 0)
    a = Slow()
    w = m.ProgramWorker(config(tmp_path, initial_current_mA=1, max_resistance_ohm=190,
                               electrical_limits=ElectricalLimits()), a)
    w.run()
    data = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert data["status"] == "failed"
    assert "I=" in data["error"] and "V=" in data["error"]
    assert "set" not in a.events and a.closed


def test_startup_does_not_mask_enabled_short_detector(tmp_path):
    class Slow(Adapter):
        startup_timeout_s = .1
        startup_poll_s = .005
        def measure(self):
            self.calls += 1
            return dict(current_mA=80, voltage_V=0)
    a = Slow()
    w = m.ProgramWorker(config(tmp_path, initial_current_mA=80), a)
    w.run()
    assert a.calls == 1 and a.closed and "set" not in a.events
    data = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert "short circuit" in data["electrical_fault"]


def test_stop_during_startup_cannot_advance_recipe(tmp_path):
    class Slow(Adapter):
        startup_timeout_s = 1
        startup_poll_s = .1
        def measure(self):
            w.stop()
            return dict(current_mA=0, voltage_V=0)
    a = Slow()
    w = m.ProgramWorker(config(tmp_path, initial_current_mA=1, electrical_limits=ElectricalLimits()), a)
    w.run()
    assert a.closed and "set" not in a.events


def test_hmp_poll_limit_is_two_hz_and_labeled_provisional(qtbot):
    w = m.CurrentProgramWindow()
    qtbot.addWidget(w)
    select(w, "Shared HMP broker", qtbot)
    for spin in (w.measurement_rate_spin, w.resistance_check_rate_spin, w.log_rate_spin):
        assert spin.maximum() == 2
    assert w.measurement_rate_spin.value() == w.resistance_check_rate_spin.value() == 2
    assert "Provisional" in w.profile_label.text()
    assert "not benchmarked" in w.profile_label.text()


def test_worker_rejects_rate_above_profile_before_hardware_open(tmp_path):
    a = Adapter()
    a.profile_mode = S
    w = m.ProgramWorker(config(tmp_path, control_rate_hz=100), a)
    w.run()
    assert not a.events
    data = json.loads(next(tmp_path.glob("*/metadata.json")).read_text())
    assert "exceed" in data["error"]
    with pytest.raises(ValueError, match="not qualified"):
        w.request_sequence([m.CurrentBlock("Hold", 1, 1, resistance_ohm=100)], 1)


def test_discovery_timeout_ignores_late_reply(qtbot, monkeypatch):
    gate = threading.Event()
    def slow():
        gate.wait(2)
        return [SR]
    monkeypatch.setattr(m, "list_visa_resources", slow)
    w = m.CurrentProgramWindow()
    qtbot.addWidget(w)
    w.mode_combo.setCurrentText(S)
    w._visa_discovery_timeout()
    w.visa_resource_combo.setCurrentText("TCPIP0::example::INSTR")
    assert not w._discovery_busy and w.start_button.isEnabled()
    gate.set()
    qtbot.waitUntil(lambda: not w._discovery_jobs)
    assert w.visa_resource_combo.currentText() == "TCPIP0::example::INSTR"


def send_wheel(widget, delta=-120):
    pos = widget.rect().center()
    event = m.QtGui.QWheelEvent(
        m.QtCore.QPointF(pos), m.QtCore.QPointF(widget.mapToGlobal(pos)),
        m.QtCore.QPoint(), m.QtCore.QPoint(0, delta),
        m.QtCore.Qt.MouseButton.NoButton, m.QtCore.Qt.KeyboardModifier.NoModifier,
        m.QtCore.Qt.ScrollPhase.NoScrollPhase, False,
    )
    m.QtWidgets.QApplication.sendEvent(widget, event)


@pytest.mark.parametrize("focused", [False, True])
def test_wheel_never_edits_setup_or_dynamic_recipe_values(qtbot, focused):
    w = m.CurrentProgramWindow()
    qtbot.addWidget(w)
    w.show()
    w.add_block("Ramp", target_mA=3, duration_s=5)
    controls = [w.voltage_spin, w.open_confirm_spin, w.repeat_mode_combo, w.mode_combo,
                w.table.cellWidget(3, 2), w.table.cellWidget(3, 3), w.table.cellWidget(3, 1),
                w.table.cellWidget(3, 6)]
    for control in controls:
        if focused: control.setFocus()
        else: control.clearFocus()
        before = control.currentIndex() if isinstance(control, m.QtWidgets.QComboBox) else control.value()
        send_wheel(control)
        after = control.currentIndex() if isinstance(control, m.QtWidgets.QComboBox) else control.value()
        assert before == after


def test_wheel_over_editor_scrolls_panel_but_keyboard_still_edits(qtbot):
    w = m.CurrentProgramWindow()
    qtbot.addWidget(w)
    w.resize(1360, 600)
    w.show()
    panel = w.voltage_spin.parentWidget()
    while not isinstance(panel, m.QtWidgets.QScrollArea):
        panel = panel.parentWidget()
    panel.verticalScrollBar().setValue(0)
    before = w.voltage_spin.value()
    send_wheel(w.voltage_spin.lineEdit())
    assert panel.verticalScrollBar().value() > 0
    assert w.voltage_spin.value() == before
    qtbot.keyClick(w.voltage_spin, m.QtCore.Qt.Key.Key_Up)
    assert w.voltage_spin.value() > before


def test_wheel_filter_does_not_affect_unrelated_windows(qtbot):
    w = m.CurrentProgramWindow()
    qtbot.addWidget(w)
    other = m.QtWidgets.QSpinBox()
    qtbot.addWidget(other)
    other.setValue(10)
    send_wheel(other)
    assert other.value() != 10
