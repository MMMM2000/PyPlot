from __future__ import annotations

import importlib
import json
import sys
import types

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="Qt widgets backend is unavailable", exc_type=ImportError)


logger_mod = importlib.import_module("data_logging.current_annealing_logger.current_annealing_logger")


def _wheel_event(delta_y: int = -120) -> object:
    return logger_mod.QtGui.QWheelEvent(
        logger_mod.QtCore.QPointF(10.0, 10.0),
        logger_mod.QtCore.QPointF(10.0, 10.0),
        logger_mod.QtCore.QPoint(0, 0),
        logger_mod.QtCore.QPoint(0, delta_y),
        logger_mod.QtCore.Qt.MouseButton.NoButton,
        logger_mod.QtCore.Qt.KeyboardModifier.NoModifier,
        logger_mod.QtCore.Qt.ScrollPhase.NoScrollPhase,
        False,
    )


class _MemorySettings:
    def __init__(self, store: dict[str, object]) -> None:
        self._store = store

    def value(self, key: str, default: object = None, type: type | None = None) -> object:
        value = self._store.get(key, default)
        if type is not None and value is not None:
            return type(value)
        return value

    def setValue(self, key: str, value: object) -> None:
        self._store[key] = value

    def contains(self, key: str) -> bool:
        return key in self._store

    def clear(self) -> None:
        self._store.clear()

    def allKeys(self) -> list[str]:
        return list(self._store)

    def sync(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _isolate_current_annealing_qsettings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Current Annealing tests from writing the operator's real settings."""

    stores: dict[tuple[str, str], dict[str, object]] = {}

    def _settings_factory(organization: str = "", application: str = "") -> _MemorySettings:
        key = (str(organization), str(application))
        return _MemorySettings(stores.setdefault(key, {}))

    monkeypatch.setattr(logger_mod.QtCore, "QSettings", _settings_factory)


class _FakeBrokerClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.readbacks: list[dict[str, float]] = [
            {"voltage_V": 2.5, "current_mA": 10.0},
        ]

    def snapshot(self) -> dict[str, object]:
        self.calls.append(("snapshot", {}))
        return {"profile": {"profile_id": "hmp4040", "channel_count": 4}}

    def lease(self, *, channel: int, owner: str, role: str) -> dict[str, object]:
        self.calls.append(("lease", {"channel": channel, "owner": owner, "role": role}))
        return {"lease_id": "lease-1", "channel": channel, "owner": owner, "role": role}

    def release(self, *, channel: int, lease_id: str) -> None:
        self.calls.append(("release", {"channel": channel, "lease_id": lease_id}))

    def configure_channel(
        self,
        *,
        channel: int,
        lease_id: str,
        voltage_v: float,
        current_a: float,
        output_on: bool,
    ) -> None:
        self.calls.append(
            (
                "configure_channel",
                {
                    "channel": channel,
                    "lease_id": lease_id,
                    "voltage_v": voltage_v,
                    "current_a": current_a,
                    "output_on": output_on,
                },
            )
        )

    def set_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
        self.calls.append(
            (
                "set_current",
                {"channel": channel, "lease_id": lease_id, "current_mA": current_mA},
            )
        )

    def set_output(self, *, channel: int, lease_id: str, output_on: bool) -> None:
        self.calls.append(
            (
                "set_output",
                {"channel": channel, "lease_id": lease_id, "output_on": output_on},
            )
        )

    def measure_channel(self, *, channel: int) -> dict[str, float]:
        self.calls.append(("measure_channel", {"channel": channel}))
        return self.readbacks.pop(0)


class _FakeScheduledBrokerClient(_FakeBrokerClient):
    def configure_polling(self, *, channel: int, interval_s: float) -> None:
        self.calls.append(("configure_polling", {"channel": channel, "interval_s": interval_s}))

    def start_scheduler(self, *, tick_s: float = 0.05) -> None:
        self.calls.append(("start_scheduler", {"tick_s": tick_s}))

    def latest_readback(
        self,
        *,
        channel: int,
        max_age_s: float | None = None,
        fallback_to_measure: bool = True,
    ) -> dict[str, float]:
        self.calls.append(
            (
                "latest_readback",
                {
                    "channel": channel,
                    "max_age_s": max_age_s,
                    "fallback_to_measure": fallback_to_measure,
                },
            )
        )
        return self.readbacks.pop(0)

    def schedule_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
        self.calls.append(
            (
                "schedule_current",
                {"channel": channel, "lease_id": lease_id, "current_mA": current_mA},
            )
        )

    def schedule_current_ramp(
        self,
        *,
        channel: int,
        lease_id: str,
        target_mA: float,
        rate_mA_s: float,
        max_step_mA: float | None = None,
        resolution_mA: float | None = None,
    ) -> None:
        self.calls.append(
            (
                "schedule_current_ramp",
                {
                    "channel": channel,
                    "lease_id": lease_id,
                    "target_mA": target_mA,
                    "rate_mA_s": rate_mA_s,
                    "max_step_mA": max_step_mA,
                    "resolution_mA": resolution_mA,
                },
            )
        )


class _FailingBrokerClient:
    def snapshot(self) -> dict[str, object]:
        raise RuntimeError("timed out")


class _FakeHmpDriver:
    instances: list["_FakeHmpDriver"] = []
    responses: dict[tuple[str, int], tuple[object, str] | Exception] = {}

    def __init__(self, *, port_name: str, baudrate: int, timeout_s: float) -> None:
        self.port_name = port_name
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self.profile = logger_mod.HMP4040_PROFILE
        self.closed = False
        _FakeHmpDriver.instances.append(self)

    def connect(self) -> None:
        result = self.responses.get((self.port_name, self.baudrate))
        if self.responses and result is None:
            raise RuntimeError("not configured")
        if isinstance(result, Exception):
            raise result
        pass

    def identify(self) -> str:
        result = self.responses.get((self.port_name, self.baudrate))
        if self.responses and result is None:
            raise RuntimeError("not configured")
        if isinstance(result, Exception):
            raise result
        if isinstance(result, tuple):
            self.profile = result[0]
            return result[1]
        return "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62"

    def close(self) -> None:
        self.closed = True


class _FakeOwnedBroker:
    def __init__(self, driver: object, profile: object) -> None:
        self.driver = driver
        self.profile = profile
        self.calls: list[tuple[str, dict[str, object]]] = []

    def assign_role(self, **payload: object) -> object:
        self.calls.append(("assign_role", payload))
        return object()

    def confirm_profile(self, **payload: object) -> object:
        self.calls.append(("confirm_profile", payload))
        return object()


def test_shared_broker_profile_is_available() -> None:
    assert "shared_hmp_broker" in logger_mod.SUPPLY_PROFILES


def test_current_annealing_defaults_to_shared_broker_without_channel(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logger_mod.QtCore, "QSettings", lambda *_args: _MemorySettings({}))
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")

    assert window.supply_profile_id == "shared_hmp_broker"
    assert window.channel_select == 0
    assert window.ui.comboBox_channel.currentData() is None
    assert window.ui.comboBox_channel.isEnabled()
    assert not window.ui.comboBox_channel.isHidden()
    assert window.max_voltage == pytest.approx(32.05)
    assert window.ui.spinBox_broker_port.value() == 8765


def test_current_annealing_shared_broker_restores_saved_channel(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict[str, object] = {}

    monkeypatch.setattr(logger_mod.QtCore, "QSettings", lambda *_args: _MemorySettings(saved))

    first = logger_mod.MainWindow()
    qtbot.addWidget(first)
    first._apply_supply_profile("shared_hmp_broker")
    assert first.channel_select == 0
    first.ui.comboBox_channel.setCurrentIndex(first.ui.comboBox_channel.findData(1))
    assert saved["supply_profile/shared_hmp_broker/channel_select"] == 1

    second = logger_mod.MainWindow()
    qtbot.addWidget(second)
    second._apply_supply_profile("shared_hmp_broker")

    assert second.channel_select == 1
    assert second.ui.comboBox_channel.currentData() == 1


def test_current_annealing_shared_broker_hides_advanced_hmp_port_options(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window._apply_supply_profile("shared_hmp_broker")

    assert not window.ui.checkBox_show_hmp_port_options.isHidden()
    assert not window.ui.checkBox_show_hmp_port_options.isChecked()
    assert window.ui.frame_hmp_port_options.isHidden()
    assert not window.ui.label_broker_hint.isHidden()
    assert window.ui.lineEdit_broker_host.isHidden()
    assert window.ui.spinBox_broker_port.isHidden()
    assert window.ui.checkBox_reset_on_start.isHidden()
    assert window.reset_on_start is False
    assert window.ui.pushButton_connect_port.text() == "Connect broker"

    window.ui.checkBox_show_hmp_port_options.setChecked(True)

    assert not window.ui.frame_hmp_port_options.isHidden()
    assert not window.ui.lineEdit_broker_host.isHidden()
    assert not window.ui.spinBox_broker_port.isHidden()
    assert not window.ui.comboBox_port.isHidden()
    assert not window.ui.comboBox_baudrate.isHidden()


def test_current_annealing_direct_hmp_profile_shows_port_options(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window._apply_supply_profile("hmp4040")

    assert window.ui.checkBox_show_hmp_port_options.isHidden()
    assert not window.ui.frame_hmp_port_options.isHidden()
    assert window.ui.lineEdit_broker_host.isHidden()
    assert window.ui.spinBox_broker_port.isHidden()
    assert not window.ui.checkBox_reset_on_start.isHidden()
    assert not window.ui.comboBox_port.isHidden()
    assert not window.ui.comboBox_baudrate.isHidden()
    assert window.ui.pushButton_connect_port.text() == "Connect to port"


def test_current_annealing_settings_wheel_guard_scrolls_without_changing_values(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    scroll = window.ui.left_scroll
    assert isinstance(scroll, logger_mod.QtWidgets.QScrollArea)
    scrollbar = scroll.verticalScrollBar()
    scrollbar.setRange(0, 100)
    scrollbar.setValue(50)

    spin = window.ui.spinBox_max_current
    spin.setValue(10)
    assert window.eventFilter(spin, _wheel_event()) is True
    assert spin.value() == 10
    assert scrollbar.value() > 50

    scrollbar.setValue(50)
    assert window.eventFilter(spin.lineEdit(), _wheel_event()) is True
    assert spin.value() == 10
    assert scrollbar.value() > 50

    combo = window.ui.comboBox_supply
    combo.setCurrentIndex(0)
    assert window.eventFilter(combo, _wheel_event()) is True
    assert combo.currentIndex() == 0


def test_current_annealing_auto_detect_hmp_port_in_shared_mode(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.ui.comboBox_port.clear()
    window.ui.comboBox_port.addItem("COM6 - scale", "COM6")
    window.ui.comboBox_port.addItem("COM3 - unknown", "COM3")
    window.ui.comboBox_baudrate.setCurrentText("9600")
    _FakeHmpDriver.instances = []
    _FakeHmpDriver.responses = {
        ("COM6", 115200): RuntimeError("not an HMP"),
        ("COM6", 9600): RuntimeError("not an HMP"),
        (
            "COM3",
            115200,
        ): (
            logger_mod.HMP4040_PROFILE,
            "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62",
        ),
    }
    monkeypatch.setattr(logger_mod, "HmpSerialDriver", _FakeHmpDriver)

    assert window._auto_detect_hmp_port(show_errors=False) is True

    assert window.supply_profile_id == "shared_hmp_broker"
    assert window.ui.comboBox_port.currentData() == "COM3"
    assert window.ui.comboBox_baudrate.currentText() == "115200"
    assert window.ui.comboBox_channel.count() == 5


def test_current_annealing_auto_detect_blocks_nonpreferred_hmp_baud(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.ui.comboBox_port.clear()
    window.ui.comboBox_port.addItem("COM3 - unknown", "COM3")
    window.ui.comboBox_baudrate.setCurrentText("9600")
    _FakeHmpDriver.instances = []
    _FakeHmpDriver.responses = {
        (
            "COM3",
            9600,
        ): (
            logger_mod.HMP4040_PROFILE,
            "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62",
        ),
    }
    messages: list[str] = []
    monkeypatch.setattr(logger_mod, "HmpSerialDriver", _FakeHmpDriver)
    window._show_status_message = lambda message, timeout_ms=10000: messages.append(message)  # type: ignore[method-assign]

    assert window._auto_detect_hmp_port(show_errors=False) is False

    assert window.ui.comboBox_port.currentData() == "COM3"
    assert window.ui.comboBox_baudrate.currentText() == "9600"
    assert any("115200" in message and "power supply settings" in message for message in messages)


def test_current_annealing_hides_legacy_hold_controls(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    assert not hasattr(window.ui, "pushButton_hold_current")
    assert not hasattr(window.ui, "spinBox_hold_duration")
    assert not hasattr(window.ui, "label_hold_duration")
    assert not hasattr(window.ui, "label_resistance_at_hold_current")
    assert not hasattr(window.ui, "label_resistance_percent_from_hold")
    assert not hasattr(window.ui, "label_hold_resistance_caption")
    assert not hasattr(window.ui, "label_percent_from_hold_caption")
    assert window.ui.comboBox_max_voltage_action.findData("hold") < 0
    assert not hasattr(window, "hold_timer")
    assert not hasattr(logger_mod.MainWindow, "_percent_from_hold")


def test_current_annealing_hides_legacy_command_panel(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    assert window.ui.frame_command_and_response.isHidden()
    assert hasattr(window.ui, "lineEdit_serial_command")
    assert hasattr(window.ui, "pushButton_send_serial_command")
    assert hasattr(window.ui, "label_last_command")
    assert hasattr(window.ui, "label_serial_response")


def test_current_annealing_hides_current_density_without_diameter(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.ui.doubleSpinBox_wire_diameter_um.setValue(0.0)

    window._refresh_current_density_visibility()

    assert window.label_live_set_density.isHidden()
    assert window.label_live_current_density.isHidden()
    assert window.ui.label_max_current_density.isHidden()
    assert window.ui.label_start_current_density.isHidden()
    assert window.ui.label_step_density.isHidden()


def test_current_annealing_process_settings_layout_has_room_for_density(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.resize(1180, 760)
    window.ui.doubleSpinBox_wire_diameter_um.setValue(19.1)
    window.ui.spinBox_max_current.setValue(600)
    window.ui.spinBox_start_current.setValue(100)
    window.ui.spinBox_step_mA.setValue(10.0)
    window._refresh_config_current_density_labels()
    window.show()
    qtbot.wait(50)

    def _window_rect(widget: object) -> object:
        top_left = widget.mapTo(window, logger_mod.QtCore.QPoint(0, 0))
        return logger_mod.QtCore.QRect(top_left, widget.size())

    rows = [
        (window.ui.label_max_current, window.ui.spinBox_max_current, window.ui.label_max_current_density),
        (window.ui.label_step, window.ui.spinBox_step_mA, window.ui.label_step_density),
        (window.ui.label_start_current, window.ui.spinBox_start_current, window.ui.label_start_current_density),
    ]
    for label, spin, density in rows:
        rects = [_window_rect(widget) for widget in (label, spin, density)]
        assert rects[0].right() < rects[1].left()
        assert rects[1].right() < rects[2].left()
        assert not rects[0].intersects(rects[1])
        assert not rects[1].intersects(rects[2])
        assert density.width() >= density.sizeHint().width()


def test_current_annealing_uses_recipe_and_hardware_tabs(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    assert window.ui.left_tabs.tabText(0) == "Recipe"
    assert window.ui.left_tabs.tabText(1) == "Hardware"
    assert window.ui.frame_process_settings.parent() is window.ui.tab_recipe
    assert window.ui.frame_serial_settings.parent() is window.ui.tab_hardware
    assert window.ui.frame_voltage_limit_settings.parent() is window.ui.tab_hardware
    assert window.ui.checkBox_reverse.isHidden()
    assert window.ui.frame_process_settings.isEnabled()
    assert not window._overlay.isVisible()


def test_current_annealing_progress_is_pinned_above_run_buttons(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.resize(1180, 760)
    window.show()
    qtbot.wait(50)

    assert not window.ui.groupBox_process_settings.isAncestorOf(window.ui.progressBar_process)
    progress_top = window.ui.progressBar_process.mapTo(window, logger_mod.QtCore.QPoint(0, 0)).y()
    button_top = window.ui.pushButton_start_process.mapTo(window, logger_mod.QtCore.QPoint(0, 0)).y()
    assert progress_top < button_top


def test_current_annealing_imports_project_diameter_and_autocomplete(tmp_path, qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    project = tmp_path / "microwire_project.pydpj"
    project.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "d (um)": 19.1,
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    window.ui.lineEdit_composition.setText("Ni50Fe27Ga23")
    window.ui.lineEdit_microwire.setText("12_2")
    window.ui.lineEdit_builder_project.setText(str(project))

    assert window._import_builder_project_from_ui() is True

    assert window.ui.doubleSpinBox_wire_diameter_um.value() == pytest.approx(19.1)
    assert window._current_density_a_mm2(60.0) == pytest.approx(209.43, rel=1e-3)
    assert window._metadata_composition_model.stringList() == ["Ni50Fe27Ga23"]
    assert window._metadata_microwire_model.stringList() == ["12/2"]
    assert window._metadata_diameter_imported is True
    assert "16a34a" in window.ui.doubleSpinBox_wire_diameter_um.styleSheet()
    assert window.ui.label_current_density_hint.text() == "Imported d = 19.1 um"
    assert not window.label_live_set_density.isHidden()


def test_current_annealing_imported_diameter_reverts_to_untrusted_on_manual_or_stale_sample(
    tmp_path,
    qtbot,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    project = tmp_path / "microwire_project.pydpj"
    project.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {"Composition": "Ni50Fe27Ga23", "Microwire": "12/2", "d (um)": 19.1},
                            {"Composition": "Ni50Fe27Ga23", "Microwire": "12/3", "d (um)": None},
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    window.ui.lineEdit_composition.setText("Ni50Fe27Ga23")
    window.ui.lineEdit_microwire.setText("12/2")
    window.ui.lineEdit_builder_project.setText(str(project))

    assert window._import_builder_project_from_ui() is True
    assert window._metadata_diameter_imported is True

    window.ui.lineEdit_microwire.setText("12/4")
    assert window._metadata_diameter_imported is False
    assert "dc2626" in window.ui.doubleSpinBox_wire_diameter_um.styleSheet()
    assert window.ui.label_current_density_hint.text() == "Manual/unchecked d = 19.1 um"

    window.ui.lineEdit_microwire.setText("12/2")
    assert window._metadata_diameter_imported is True

    window.ui.doubleSpinBox_wire_diameter_um.setValue(20.0)
    assert window._metadata_diameter_imported is False
    assert "Manual/unchecked d = 20" in window.ui.label_current_density_hint.text()

    window.ui.lineEdit_microwire.setText("12/3")
    assert window._metadata_diameter_imported is False
    assert "no diameter available" in window.ui.label_microwire_metadata_status.text()


def test_current_annealing_fabrication_load_keeps_ui_responsive(
    tmp_path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    root = tmp_path / "fabrication"
    root.mkdir()
    for index in range(40):
        (root / f"fabrication_{index:03d}.xlsx").write_text("placeholder", encoding="utf-8")

    class _FabricationIndex:
        piece_level = {("Ni50Fe27Ga23", 12, 2): {"d (um)": 19.1}}

    worker_thread: list[object] = []

    def _slow_build_fabrication_index(files: list[object]) -> _FabricationIndex:
        assert len(files) == 40
        worker_thread.append(logger_mod.QtCore.QThread.currentThread())
        logger_mod.time.sleep(0.25)
        return _FabricationIndex()

    fake_package = types.ModuleType("microwire_data_builder")
    fake_core = types.ModuleType("microwire_data_builder.core")
    fake_core.build_fabrication_index = _slow_build_fabrication_index
    fake_package.core = fake_core
    monkeypatch.setitem(sys.modules, "microwire_data_builder", fake_package)
    monkeypatch.setitem(sys.modules, "microwire_data_builder.core", fake_core)

    ticks: list[float] = []
    timer = logger_mod.QtCore.QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(logger_mod.time.perf_counter()))
    timer.start()
    window.ui.lineEdit_composition.setText("Ni50Fe27Ga23")
    window.ui.lineEdit_microwire.setText("12_2")
    window.ui.lineEdit_fabrication_folder.setText(str(root))

    assert window._load_fabrication_folder_from_ui() is True
    assert window.ui.pushButton_load_fabrication.text() == "Cancel"

    qtbot.waitUntil(lambda: len(ticks) >= 3, timeout=1000)
    assert window._fabrication_load_active()

    qtbot.waitUntil(lambda: not window._fabrication_load_active(), timeout=5000)
    timer.stop()

    assert worker_thread
    assert worker_thread[0] is not window.thread()
    assert window.ui.pushButton_load_fabrication.text() == "Load"
    assert window.ui.doubleSpinBox_wire_diameter_um.value() == pytest.approx(19.1)
    assert "Loaded 1 microwire suggestion(s) from 40 fabrication workbook(s)." in (
        window.ui.label_microwire_metadata_status.text()
    )


def test_current_annealing_microwire_field_displays_slashes(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window._handle_metadata_microwire_activated("12_2")

    assert window.ui.lineEdit_microwire.text() == "12/2"


def test_current_annealing_top_axis_shows_density_when_diameter_known(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.ui.doubleSpinBox_wire_diameter_um.setValue(20.0)

    window._append_measurement_sample(10.0, 100.0)
    window._append_measurement_sample(20.0, 110.0)

    top_axis = window.pg_plot_resistance_vs_current.getPlotItem().getAxis("top")
    assert top_axis.style["showValues"] is True
    assert top_axis.labelText == "Current density"
    assert top_axis.labelUnits == "A/mm²"


def test_current_annealing_plot_config_can_show_power_top_axis_and_voltage_right_axis(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window.start_current_mA = 1
    window.ui.doubleSpinBox_wire_diameter_um.setValue(20.0)
    window._plot_axis_modes["upper"]["top"] = logger_mod.PLOT_AXIS_POWER_MW
    window._plot_axis_modes["upper"]["right"] = logger_mod.PLOT_AXIS_VOLTAGE
    window._plot_axis_modes["lower"]["bottom"] = logger_mod.PLOT_AXIS_CURRENT_MA
    window._plot_axis_modes["lower"]["left"] = logger_mod.PLOT_AXIS_VOLTAGE
    window._plot_axis_modes["lower"]["top"] = logger_mod.PLOT_AXIS_CURRENT_DENSITY
    window._plot_axis_modes["lower"]["right"] = logger_mod.PLOT_AXIS_RESISTANCE
    window.init_graph_window()
    window._append_measurement_sample(10.0, 100.0, voltage=1.0)
    window._append_measurement_sample(20.0, 110.0, voltage=2.2)

    upper_item = window.pg_plot_resistance_vs_current.getPlotItem()
    lower_item = window.pg_plot_resistance_vs_sample.getPlotItem()
    assert upper_item.getAxis("top").labelText == "Power"
    assert upper_item.getAxis("top").labelUnits == "mW"
    assert upper_item.getAxis("right").labelText == "Voltage"
    assert upper_item.getAxis("right").labelUnits == "V"
    assert lower_item.getAxis("bottom").labelText == "Current"
    assert lower_item.getAxis("left").labelText == "Voltage"
    assert lower_item.getAxis("top").labelText == "Current density"
    assert lower_item.getAxis("right").labelText == "Resistance"
    assert set(window._right_axis_curves) == {"upper", "lower"}


def test_current_annealing_plot_config_dialog_has_superscript_density_unit(qtbot) -> None:
    dialog = logger_mod.CurrentAnnealingPlotConfigDialog(
        None,
        axis_modes={
            "upper": {
                "bottom": logger_mod.PLOT_AXIS_CURRENT_MA,
                "left": logger_mod.PLOT_AXIS_RESISTANCE,
                "top": logger_mod.PLOT_AXIS_CURRENT_DENSITY,
                "right": logger_mod.PLOT_AXIS_NONE,
            },
            "lower": {
                "bottom": logger_mod.PLOT_AXIS_SAMPLE_N,
                "left": logger_mod.PLOT_AXIS_RESISTANCE,
                "top": logger_mod.PLOT_AXIS_NONE,
                "right": logger_mod.PLOT_AXIS_NONE,
            },
        },
    )
    qtbot.addWidget(dialog)

    combo_texts = [
        combo.itemText(index)
        for combo in dialog.findChildren(logger_mod.QtWidgets.QComboBox)
        for index in range(combo.count())
    ]
    assert "Current density (A/mm²)" in combo_texts


def test_current_annealing_density_top_axis_uses_bottom_tick_positions(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.ui.doubleSpinBox_wire_diameter_um.setValue(20.0)
    plot_item = window.pg_plot_resistance_vs_current.getPlotItem()
    bottom_axis = plot_item.getAxis("bottom")
    captured: dict[str, list[tuple[float, str]]] = {}

    def fake_tick_values(_low: float, _high: float, _width: int) -> list[tuple[float, list[float]]]:
        return [(10.0, [0.0, 10.0, 20.0, 30.0])]

    def capture_ticks(levels: list[list[tuple[float, str]]]) -> None:
        captured["ticks"] = levels[0]

    bottom_axis.tickValues = fake_tick_values  # type: ignore[method-assign]
    top_axis = plot_item.getAxis("top")
    top_axis.setTicks = capture_ticks  # type: ignore[method-assign]

    window._refresh_current_density_axis(x_low=0.0, x_high=30.0)

    assert [position for position, _label in captured["ticks"]] == [0.0, 10.0, 20.0, 30.0]


def test_current_annealing_voltage_limit_no_longer_holds_current(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.process_running = True
    window.current_step_A = 0.001
    window.current_increment = 0.001
    window.max_voltage = 32.05

    assert "hold" not in logger_mod.MAX_VOLTAGE_ACTION_LABELS

    window._apply_max_voltage_action("hold")

    assert window.current_increment == pytest.approx(-0.001)
    assert window.direction_ascending is False


def test_current_annealing_planned_time_has_no_hidden_hold_duration(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window.ui.spinBox_start_current.setValue(1)
    window.ui.spinBox_max_current.setValue(3)
    window.ui.spinBox_step_mA.setValue(1.0)
    window.ui.checkBox_reverse.setChecked(True)
    window.ui.spinBox_loops.setValue(1)

    assert window.compute_planned_seconds() == 4


def test_current_annealing_runtime_recipe_fields_stay_editable(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window._set_process_controls_enabled(False)

    assert window.ui.spinBox_max_current.isEnabled()
    assert window.ui.spinBox_step_mA.isEnabled()
    assert window.ui.spinBox_start_current.isEnabled()
    assert window.ui.spinBox_loops.isEnabled()
    assert window.ui.checkBox_infinite_loops.isEnabled()
    assert window.ui.label_max_current.isEnabled()
    assert not window.ui.lineEdit_log_dir.isEnabled()


def test_current_annealing_update_running_recipe_refreshes_live_plan(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.operation_mode = 2
    window.process_running = True
    window.direction_ascending = True
    window.current_current_set = 0.007
    window.current_increment = 0.001
    window.current_step_mA = 1.0
    window.current_step_A = 0.001
    window.loop_idx = 0
    window.ui.spinBox_start_current.setValue(1)
    window.ui.spinBox_max_current.setValue(10)
    window.ui.spinBox_step_mA.setValue(1.0)
    window.ui.spinBox_loops.setValue(2)
    window._init_loop_tracking(window._planned_automatic_loop_steps(), 2, False)

    window.ui.spinBox_max_current.setValue(6)
    window.ui.spinBox_step_mA.setValue(0.2)
    window.ui.spinBox_loops.setValue(3)
    window.handle_update_running_recipe_clicked()

    assert window.max_current_mA == 6
    assert window.current_step_mA == pytest.approx(0.2)
    assert window.current_step_A == pytest.approx(0.0002)
    assert window.current_increment == pytest.approx(-0.0002)
    assert window.direction_ascending is False
    assert window.loop_target == 3
    assert window._planned_loop_steps == 50
    assert window.total_steps >= window.step_idx


def test_current_annealing_reverses_at_max_without_hidden_hold(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeScheduledBrokerClient()
    fake.readbacks = [{"voltage_V": 0.5, "current_mA": 3.0}]
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window._shared_broker_lease_id = "lease-1"
    window.operation_mode = 2
    window.process_running = True
    window.first_sample = False
    window.ui.spinBox_max_current.setValue(3)
    window.ui.spinBox_step_mA.setValue(1.0)
    window.handle_step_changed()
    window.max_current_mA = 3
    window.current_step_mA = 1.0
    window.current_step_A = 0.001
    window.current_current_set = 0.003
    window.current_increment = 0.001
    window.reverse_enabled = True

    window.handle_send_new_command()

    assert not hasattr(window, "hold_timer_running")
    assert window.current_increment == pytest.approx(-0.001)
    assert window.current_current_set == pytest.approx(0.002)
    assert (
        "schedule_current_ramp",
        {
            "channel": 1,
            "lease_id": "lease-1",
            "target_mA": 2.0,
            "rate_mA_s": 1.0,
            "max_step_mA": 0.2,
            "resolution_mA": 0.2,
        },
    ) in fake.calls


def test_current_annealing_shared_broker_clamps_overshoot_to_max_current(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeScheduledBrokerClient()
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window._shared_broker_lease_id = "lease-1"
    window.max_current_mA = 2
    window.current_step_mA = 0.2
    window.current_current_set = 0.0022

    window._send_current_setpoint()

    assert window.current_current_set == pytest.approx(0.002)
    assert (
        "schedule_current_ramp",
        {
            "channel": 1,
            "lease_id": "lease-1",
            "target_mA": 2.0,
            "rate_mA_s": 0.2,
            "max_step_mA": 0.2,
            "resolution_mA": 0.2,
        },
    ) in fake.calls


def test_current_annealing_clamps_to_confirmed_broker_current_limit(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeScheduledBrokerClient()
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window._shared_broker_lease_id = "lease-1"
    window.max_current_mA = 30
    window._shared_broker_current_limit_mA = 2.0
    window.current_step_mA = 0.2
    window.current_current_set = 0.0022

    window._send_current_setpoint()

    assert window.current_current_set == pytest.approx(0.002)
    assert (
        "schedule_current_ramp",
        {
            "channel": 1,
            "lease_id": "lease-1",
            "target_mA": 2.0,
            "rate_mA_s": 0.2,
            "max_step_mA": 0.2,
            "resolution_mA": 0.2,
        },
    ) in fake.calls


def test_current_annealing_preflight_blocks_stale_broker_current_limit(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeScheduledBrokerClient()
    fake.snapshot = lambda: {  # type: ignore[method-assign]
        "profile": {"profile_id": "hmp4040", "channel_count": 4},
        "bench_profile": {
            "channels": {
                "1": {
                    "role": "current_annealing",
                    "confirmed": True,
                    "voltage_limit_v": 32.05,
                    "current_limit_a": 0.03,
                }
            }
        },
    }
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.is_connected = True
    window.channel_select = 1
    window.ui.spinBox_max_current.setValue(35)

    errors = window._start_preflight_errors()

    assert any("35" in error and "30" in error and "shared broker" in error for error in errors)


def test_current_annealing_shared_broker_zero_current_stops_after_startup_grace(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeScheduledBrokerClient()
    fake.readbacks = [{"voltage_V": 0.0, "current_mA": 0.0} for _ in range(6)]
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        logger_mod.QtWidgets.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((str(title), str(message))),
    )
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window._shared_broker_lease_id = "lease-1"
    window.process_running = True
    window.current_current_set = 0.010
    window._process_start_time = 0.0
    monkeypatch.setattr(logger_mod.time, "monotonic", lambda: 10.0)

    for _ in range(6):
        assert window._read_shared_broker_sample() is True

    assert window.process_running is False
    assert warnings
    assert warnings[-1][0] == "Contact lost"
    assert "Measured current is zero" in warnings[-1][1]


def test_current_annealing_broker_limit_clamp_reverses_instead_of_stalling(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeScheduledBrokerClient()
    monkeypatch.setattr(logger_mod.QtWidgets.QMessageBox, "warning", lambda *_args: None)
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window._shared_broker_lease_id = "lease-1"
    window.max_current_mA = 35
    window._shared_broker_current_limit_mA = 30.0
    window.current_step_mA = 1.0
    window.current_step_A = 0.001
    window.current_increment = 0.001
    window.current_current_set = 0.031
    window.reverse_enabled = True
    window.process_running = True
    window.direction_ascending = True

    window._send_current_setpoint()

    assert window.current_current_set == pytest.approx(0.030)
    assert window.current_increment == pytest.approx(-0.001)
    assert window.direction_ascending is False
    ramp_calls = [payload for name, payload in fake.calls if name == "schedule_current_ramp"]
    assert ramp_calls
    assert ramp_calls[-1]["channel"] == 1
    assert ramp_calls[-1]["lease_id"] == "lease-1"
    assert ramp_calls[-1]["target_mA"] == pytest.approx(30.0)
    assert ramp_calls[-1]["rate_mA_s"] == pytest.approx(1.0)


def test_current_annealing_channel_dropdown_tracks_detected_hmp_model(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window._set_detected_hmp_profile(logger_mod.HMP4030_PROFILE, selected=0)
    assert [window.ui.comboBox_channel.itemText(i) for i in range(window.ui.comboBox_channel.count())] == [
        "Select channel...",
        "CH1",
        "CH2",
        "CH3",
    ]
    assert window.ui.comboBox_channel.currentData() is None

    window.ui.comboBox_channel.setCurrentIndex(window.ui.comboBox_channel.findData(2))
    assert window.channel_select == 2

    window._set_detected_hmp_profile(logger_mod.HMP4040_PROFILE)
    assert [window.ui.comboBox_channel.itemText(i) for i in range(window.ui.comboBox_channel.count())] == [
        "Select channel...",
        "CH1",
        "CH2",
        "CH3",
        "CH4",
    ]
    assert window.ui.comboBox_channel.currentData() == 2
    assert window.channel_select == 2

    window._set_detected_hmp_profile(logger_mod.HMP4030_PROFILE, selected=4)
    assert window.ui.comboBox_channel.currentData() is None
    assert window.channel_select == 0


def test_current_annealing_ramp_rate_rounds_to_hmp_resolution(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window._apply_supply_profile("shared_hmp_broker")
    window.ui.spinBox_step_mA.setValue(0.3)
    window.handle_step_changed()

    assert window.current_step_mA == pytest.approx(0.2)
    assert window.ui.spinBox_step_mA.value() == pytest.approx(0.2)


def test_current_annealing_preflight_blocks_shared_broker_without_channel(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logger_mod.QtCore, "QSettings", lambda *_args: _MemorySettings({}))
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    errors = window._start_preflight_errors()

    assert any("channel" in error.lower() for error in errors)


def test_current_annealing_start_auto_connects_selected_shared_broker(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window.operation_mode = 0
    calls: list[str] = []
    errors: list[list[str]] = []

    def _connect() -> None:
        calls.append("connect")
        window.is_connected = True

    monkeypatch.setattr(window, "_connect_shared_broker_mode", _connect)
    monkeypatch.setattr(window, "_start_preflight_errors", lambda **_kwargs: [])
    monkeypatch.setattr(window, "_show_start_preflight_errors", lambda payload: errors.append(payload))

    window.handle_toggle_process_clicked()

    assert calls == ["connect"]
    assert errors == []
    assert window.process_running is True


def test_current_annealing_start_shows_auto_connect_progress(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window.operation_mode = 0
    progress_seen: list[str] = []

    def _connect() -> None:
        progress = window._hardware_auto_connect_progress
        assert progress is not None
        progress_seen.append(progress.labelText())
        window.is_connected = True

    monkeypatch.setattr(window, "_connect_shared_broker_mode", _connect)
    monkeypatch.setattr(window, "_start_preflight_errors", lambda **_kwargs: [])

    window.handle_toggle_process_clicked()

    assert progress_seen == ["Connecting shared HMP broker..."]
    assert window._hardware_auto_connect_progress is None
    assert window.process_running is True


def test_current_annealing_start_reports_auto_connect_failure_detail(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window.operation_mode = 0
    warnings: list[tuple[str, str]] = []
    preflight: list[list[str]] = []

    def _connect() -> None:
        raise RuntimeError("broker is busy")

    monkeypatch.setattr(window, "_connect_shared_broker_mode", _connect)
    monkeypatch.setattr(
        logger_mod.QtWidgets.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((str(title), str(message))),
    )
    monkeypatch.setattr(window, "_show_start_preflight_errors", lambda payload: preflight.append(payload))

    window.handle_toggle_process_clicked()

    assert warnings == [("Hardware auto-connect failed", "Hardware auto-connect failed: broker is busy")]
    assert preflight == [["Hardware auto-connect failed: broker is busy"]]
    assert window.process_running is False


def test_shared_broker_connect_verifies_broker_before_marking_connected(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeBrokerClient()
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")

    window._connect_shared_broker_mode()

    assert window.is_connected is True
    assert fake.calls == [("snapshot", {})]


def test_shared_broker_connect_preserves_confirmed_channel(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeBrokerClient()
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.ui.comboBox_channel.setCurrentIndex(window.ui.comboBox_channel.findData(1))

    window._connect_shared_broker_mode()

    assert window.channel_select == 1
    assert window.ui.comboBox_channel.currentData() == 1


def test_shared_broker_connect_falls_back_to_standard_port(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.ui.spinBox_broker_port.setValue(49685)
    created_ports: list[int] = []

    def _client_factory(*, host: str, port: int) -> object:
        created_ports.append(port)
        if port == 49685:
            return _FailingBrokerClient()
        return _FakeBrokerClient()

    monkeypatch.setattr(logger_mod, "BrokerJsonClient", _client_factory)

    window._connect_shared_broker_mode()

    assert created_ports == [49685, 8765]
    assert window.is_connected is True
    assert window.ui.spinBox_broker_port.value() == 8765


def test_shared_broker_connect_starts_owned_broker_when_no_existing_broker(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.ui.comboBox_channel.setCurrentIndex(window.ui.comboBox_channel.findData(1))
    window.ui.spinBox_max_current.setValue(30)
    window.ui.comboBox_port.clear()
    window.ui.comboBox_port.addItem("COM3 - HMP4040", "COM3")
    window.ui.comboBox_baudrate.setCurrentText("115200")
    started: list[tuple[object, str, int]] = []

    def _client_factory(*, host: str, port: int) -> object:
        if not started:
            return _FailingBrokerClient()
        return _FakeBrokerClient()

    def _start_server(broker: object, *, host: str, port: int) -> tuple[object, object]:
        started.append((broker, host, port))
        return object(), object()

    _FakeHmpDriver.instances = []
    _FakeHmpDriver.responses = {}
    monkeypatch.setattr(logger_mod, "BrokerJsonClient", _client_factory)
    monkeypatch.setattr(logger_mod, "HmpSerialDriver", _FakeHmpDriver)
    monkeypatch.setattr(logger_mod, "SharedPowerSupplyBroker", _FakeOwnedBroker)
    monkeypatch.setattr(logger_mod, "start_broker_server", _start_server)

    window._connect_shared_broker_mode()

    assert window.is_connected is True
    assert len(started) == 1
    assert _FakeHmpDriver.instances[0].port_name == "COM3"
    owned_broker = started[0][0]
    assert isinstance(owned_broker, _FakeOwnedBroker)
    assert owned_broker.calls == [
        (
            "assign_role",
            {
                "channel": 1,
                "role": "current_annealing",
                "confirmed": True,
                "voltage_limit_v": pytest.approx(32.05),
                "current_limit_a": pytest.approx(0.03),
            },
        ),
        ("confirm_profile", {"name": "Current Annealing auto-started shared HMP broker"}),
    ]


def test_shared_broker_connect_auto_detects_hmp_before_owned_start(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.ui.comboBox_channel.setCurrentIndex(window.ui.comboBox_channel.findData(1))
    window.ui.spinBox_max_current.setValue(30)
    window.ui.comboBox_port.clear()
    window.ui.comboBox_port.addItem("COM6 - scale", "COM6")
    window.ui.comboBox_port.addItem("COM9 - LCR", "COM9")
    window.ui.comboBox_port.addItem("COM3 - HMP4040", "COM3")
    window.ui.comboBox_baudrate.setCurrentText("9600")
    started: list[tuple[object, str, int]] = []
    _FakeHmpDriver.instances = []
    _FakeHmpDriver.responses = {
        ("COM6", 115200): RuntimeError("not an HMP"),
        ("COM6", 9600): RuntimeError("not an HMP"),
        ("COM9", 115200): (None, "LCR-6200,REV E8.13,GEZ883931,Good Will Instrument Co., Ltd."),
        ("COM9", 9600): (None, "LCR-6200,REV E8.13,GEZ883931,Good Will Instrument Co., Ltd."),
        (
            "COM3",
            115200,
        ): (
            logger_mod.HMP4040_PROFILE,
            "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62",
        ),
    }

    def _client_factory(*, host: str, port: int) -> object:
        if not started:
            return _FailingBrokerClient()
        return _FakeBrokerClient()

    def _start_server(broker: object, *, host: str, port: int) -> tuple[object, object]:
        started.append((broker, host, port))
        return object(), object()

    monkeypatch.setattr(logger_mod, "BrokerJsonClient", _client_factory)
    monkeypatch.setattr(logger_mod, "HmpSerialDriver", _FakeHmpDriver)
    monkeypatch.setattr(logger_mod, "SharedPowerSupplyBroker", _FakeOwnedBroker)
    monkeypatch.setattr(logger_mod, "start_broker_server", _start_server)

    window._connect_shared_broker_mode()

    assert window.is_connected is True
    assert window.ui.comboBox_port.currentData() == "COM3"
    assert window.ui.comboBox_baudrate.currentText() == "115200"
    assert [driver.port_name for driver in _FakeHmpDriver.instances if not driver.closed] == ["COM3"]
    assert len(started) == 1


def test_shared_broker_connect_refreshes_stale_port_list_before_owned_start(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.ui.comboBox_channel.setCurrentIndex(window.ui.comboBox_channel.findData(1))
    window.ui.spinBox_max_current.setValue(30)
    window.ui.comboBox_port.clear()
    window.ui.comboBox_port.addItem("COM6 - scale", "COM6")
    window.ui.comboBox_baudrate.setCurrentText("9600")
    started: list[tuple[object, str, int]] = []
    _FakeHmpDriver.instances = []
    _FakeHmpDriver.responses = {
        ("COM6", 115200): RuntimeError("not an HMP"),
        ("COM6", 9600): RuntimeError("not an HMP"),
        ("COM9", 115200): (None, "LCR-6200,REV E8.13,GEZ883931,Good Will Instrument Co., Ltd."),
        ("COM9", 9600): (None, "LCR-6200,REV E8.13,GEZ883931,Good Will Instrument Co., Ltd."),
        (
            "COM3",
            115200,
        ): (
            logger_mod.HMP4040_PROFILE,
            "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62",
        ),
    }

    def _populate_ports() -> None:
        window.ui.comboBox_port.clear()
        window.ui.comboBox_port.addItem("COM6 - scale", "COM6")
        window.ui.comboBox_port.addItem("COM9 - LCR", "COM9")
        window.ui.comboBox_port.addItem("COM3 - HMP4040", "COM3")

    def _client_factory(*, host: str, port: int) -> object:
        if not started:
            return _FailingBrokerClient()
        return _FakeBrokerClient()

    def _start_server(broker: object, *, host: str, port: int) -> tuple[object, object]:
        started.append((broker, host, port))
        return object(), object()

    monkeypatch.setattr(window, "populate_ports", _populate_ports)
    monkeypatch.setattr(logger_mod, "BrokerJsonClient", _client_factory)
    monkeypatch.setattr(logger_mod, "HmpSerialDriver", _FakeHmpDriver)
    monkeypatch.setattr(logger_mod, "SharedPowerSupplyBroker", _FakeOwnedBroker)
    monkeypatch.setattr(logger_mod, "start_broker_server", _start_server)

    window._connect_shared_broker_mode()

    assert window.is_connected is True
    assert window.ui.comboBox_port.currentData() == "COM3"
    assert window.ui.comboBox_baudrate.currentText() == "115200"
    assert [driver.port_name for driver in _FakeHmpDriver.instances if not driver.closed] == ["COM3"]
    assert len(started) == 1


def test_shared_broker_connect_does_not_start_owned_broker_on_unverified_port(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.ui.comboBox_channel.setCurrentIndex(window.ui.comboBox_channel.findData(1))
    window.ui.comboBox_port.clear()
    window.ui.comboBox_port.addItem("COM6 - scale", "COM6")
    started: list[tuple[object, str, int]] = []
    _FakeHmpDriver.instances = []
    _FakeHmpDriver.responses = {
        ("COM6", 115200): RuntimeError("not an HMP"),
        ("COM6", 9600): RuntimeError("not an HMP"),
        ("COM6", 57600): RuntimeError("not an HMP"),
        ("COM6", 38400): RuntimeError("not an HMP"),
        ("COM6", 19200): RuntimeError("not an HMP"),
    }

    monkeypatch.setattr(window, "populate_ports", lambda: None)
    monkeypatch.setattr(logger_mod, "BrokerJsonClient", lambda *, host, port: _FailingBrokerClient())
    monkeypatch.setattr(logger_mod, "HmpSerialDriver", _FakeHmpDriver)
    monkeypatch.setattr(logger_mod, "start_broker_server", lambda broker, *, host, port: started.append((broker, host, port)))

    with pytest.raises(RuntimeError, match="automatic HMP discovery"):
        window._connect_shared_broker_mode()

    assert started == []
    assert window.is_connected is False
    assert all(driver.closed for driver in _FakeHmpDriver.instances)


def test_shared_broker_owned_start_uses_selected_hmp_port_only(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.ui.comboBox_channel.setCurrentIndex(window.ui.comboBox_channel.findData(1))
    window.ui.comboBox_port.clear()
    window.ui.comboBox_port.addItem("COM3 - HMP4040", "COM3")
    window.ui.comboBox_port.addItem("COM9 - LCR", "COM9")
    window.ui.comboBox_port.setCurrentIndex(0)
    window.ui.comboBox_baudrate.setCurrentText("115200")
    started: list[tuple[object, str, int]] = []
    _FakeHmpDriver.instances = []
    _FakeHmpDriver.responses = {}

    def _start_server(broker: object, *, host: str, port: int) -> tuple[object, object]:
        started.append((broker, host, port))
        return object(), object()

    monkeypatch.setattr(logger_mod, "HmpSerialDriver", _FakeHmpDriver)
    monkeypatch.setattr(logger_mod, "SharedPowerSupplyBroker", _FakeOwnedBroker)
    monkeypatch.setattr(logger_mod, "start_broker_server", _start_server)

    window._start_owned_shared_broker()

    assert [driver.port_name for driver in _FakeHmpDriver.instances] == ["COM3"]
    assert len(started) == 1
    assert window._candidate_hmp_ports_for_broker(include_all=True) == ["COM3", "COM9"]


def test_shared_broker_disconnect_clears_connected_state(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("shared_hmp_broker")
    window.is_connected = True
    window._shared_broker_client = object()
    window._shared_broker_lease_id = None
    monkeypatch.setattr(window, "send_safe_end_commands", lambda: None)
    monkeypatch.setattr(window, "_stop_owned_shared_broker", lambda: None)

    window._disconnect_shared_broker_mode()

    assert window.is_connected is False
    assert window._shared_broker_client is None
    assert window.ui.pushButton_connect_port.text() == "Connect broker"


def test_current_annealing_prepare_output_file_creates_metadata_sidecar(tmp_path, qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.ui.lineEdit_log_dir.setText(str(tmp_path))
    window.ui.lineEdit_log_file.setText("sample_run01")
    window.ui.spinBox_loops.setValue(1)
    window._apply_supply_profile("shared_hmp_broker")
    window.ui.comboBox_channel.setCurrentIndex(window.ui.comboBox_channel.findData(1))

    assert window.prepare_output_file() is True

    data_path = logger_mod.Path(window.f_name)
    assert data_path.exists()
    metadata_path = data_path.parent / "metadata" / data_path.stem / "metadata.json"
    assert metadata_path.exists()
    payload = logger_mod.json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["data_file"] == data_path.name
    assert payload["supply"]["profile_id"] == "shared_hmp_broker"
    assert payload["supply"]["channel"] == 1
    assert "hold_duration_s" not in payload


def test_current_annealing_replace_moves_previous_output_and_metadata_to_trash(
    tmp_path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.ui.lineEdit_log_dir.setText(str(tmp_path))
    window.ui.lineEdit_log_file.setText("replace_me")
    data_path = logger_mod.Path(window.build_log_path())
    data_path.write_text("old data\n", encoding="utf-8")
    metadata_dir = data_path.parent / "metadata" / data_path.stem
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "metadata.json").write_text('{"old": true}\n', encoding="utf-8")
    moved: list[logger_mod.Path] = []

    class _FakeMessageBox:
        Icon = logger_mod.QtWidgets.QMessageBox.Icon
        ButtonRole = logger_mod.QtWidgets.QMessageBox.ButtonRole

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self._clicked: object | None = None

        def setWindowTitle(self, _title: str) -> None:
            pass

        def setIcon(self, _icon: object) -> None:
            pass

        def setText(self, _text: str) -> None:
            pass

        def setInformativeText(self, _text: str) -> None:
            pass

        def addButton(self, text: str, _role: object) -> object:
            button = object()
            if text == "Replace":
                self._clicked = button
            return button

        def exec(self) -> int:
            return 0

        def clickedButton(self) -> object | None:
            return self._clicked

        @staticmethod
        def critical(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("critical message was not expected")

    def _fake_trash(path: logger_mod.Path) -> logger_mod.Path:
        moved.append(path)
        if path.is_dir():
            destination = tmp_path / "trash" / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            logger_mod.shutil.move(str(path), str(destination))
            return destination
        destination = tmp_path / "trash" / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.replace(destination)
        return destination

    monkeypatch.setattr(logger_mod.QtWidgets, "QMessageBox", _FakeMessageBox)
    monkeypatch.setattr(logger_mod, "_move_path_to_trash", _fake_trash)

    assert window.prepare_output_file() is True

    assert moved == [data_path, metadata_dir]
    assert data_path.read_text(encoding="utf-8").startswith("# Current (mA)")
    assert (metadata_dir / "metadata.json").exists()
    assert (tmp_path / "trash" / data_path.name).read_text(encoding="utf-8") == "old data\n"


def test_current_annealing_metadata_records_hardware_backend(tmp_path, qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.ui.lineEdit_log_dir.setText(str(tmp_path))
    window.ui.lineEdit_log_file.setText("sample_run02")
    window.ui.checkBox_reverse.setChecked(True)
    window.ui.spinBox_loops.setValue(2)
    window._apply_supply_profile("shared_hmp_broker")
    window._set_detected_hmp_profile(logger_mod.HMP4040_PROFILE)
    window.ui.comboBox_channel.setCurrentIndex(window.ui.comboBox_channel.findData(1))
    window.ui.comboBox_port.clear()
    window.ui.comboBox_port.addItem("COM3 - HMP4040", "COM3")
    window.ui.comboBox_port.setCurrentIndex(0)
    window.ui.comboBox_baudrate.setCurrentText("115200")
    window._owned_shared_broker_server = object()

    assert window.prepare_output_file() is True

    data_path = logger_mod.Path(window.f_name)
    metadata_path = data_path.parent / "metadata" / data_path.stem / "metadata.json"
    payload = logger_mod.json.loads(metadata_path.read_text(encoding="utf-8"))
    supply = payload["supply"]
    assert supply["detected_model"] == "hmp4040"
    assert supply["port"] == "COM3"
    assert supply["baud"] == 115200
    assert supply["current_resolution_mA"] == pytest.approx(0.2)
    assert supply["min_positive_current_mA"] == pytest.approx(1.0)
    assert supply["broker_owned_by_app"] is True
    assert supply["broker_source"] == "owned"
    assert payload["recipe"]["reverse_enabled"] is True
    assert payload["recipe"]["loops"] == 2


def test_current_annealing_metadata_preserves_decimal_ramp_rate(tmp_path, qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.ui.lineEdit_log_dir.setText(str(tmp_path))
    window.ui.lineEdit_log_file.setText("decimal_ramp")
    window._apply_supply_profile("shared_hmp_broker")
    window.ui.comboBox_channel.setCurrentIndex(window.ui.comboBox_channel.findData(1))
    window.ui.spinBox_step_mA.setValue(0.2)
    window.handle_step_changed()

    assert window.prepare_output_file() is True

    data_path = logger_mod.Path(window.f_name)
    metadata_path = data_path.parent / "metadata" / data_path.stem / "metadata.json"
    payload = logger_mod.json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["step_mA"] == pytest.approx(0.2)
    assert payload["recipe"]["current_ramp_rate_mA_s"] == pytest.approx(0.2)


def test_current_annealing_metadata_records_source_control_snapshot(
    tmp_path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.ui.lineEdit_log_dir.setText(str(tmp_path))
    window.ui.lineEdit_log_file.setText("metadata_git")

    replies = {
        ("branch", "--show-current"): "codex/current-annealing-pyqtgraph\n",
        ("rev-parse", "HEAD"): "abc123\n",
        ("status", "--short"): " M data_logging/current_annealing_logger/current_annealing_logger.py\n",
        ("config", "--get", "remote.origin.url"): "https://example.test/repo.git\n",
    }

    def _fake_run(args: list[str], **_kwargs: object) -> object:
        class Result:
            returncode = 0
            stdout = replies[tuple(args[3:])]

        return Result()

    monkeypatch.setattr(logger_mod.subprocess, "run", _fake_run)

    assert window.prepare_output_file() is True

    data_path = logger_mod.Path(window.f_name)
    metadata_path = data_path.parent / "metadata" / data_path.stem / "metadata.json"
    payload = logger_mod.json.loads(metadata_path.read_text(encoding="utf-8"))
    source_control = payload["source_control"]
    assert source_control["branch"] == "codex/current-annealing-pyqtgraph"
    assert source_control["commit"] == "abc123"
    assert source_control["is_dirty"] is True
    assert source_control["remote_url"] == "https://example.test/repo.git"


def test_live_dashboard_uses_pyqtgraph_backend(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    assert window._plot_backend == "pyqtgraph"
    assert window.canvas is None
    assert window.pg_plot_resistance_vs_current is not None
    assert window.pg_plot_resistance_vs_sample is not None


def test_stopping_measurement_keeps_last_graph_data(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._reset_sample_buffers()
    window._append_measurement_sample(5.0, 100.0)
    window._append_measurement_sample(6.0, 101.0)
    line_count = len(window._segment_lines_ax1)
    window.process_running = True
    monkeypatch.setattr(window, "send_safe_end_commands", lambda: None)

    window.stop_annealing("Stopped by user.", show_dialog=False)

    assert window._samples_current == [5.0, 6.0]
    assert window._samples_resistance == [100.0, 101.0]
    assert len(window._segment_lines_ax1) == line_count
    assert window.process_running is False


def test_live_dashboard_pyqtgraph_axes_are_visible_without_gridlines(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    assert window._plot_backend == "pyqtgraph"
    for plot in (
        window.pg_plot_resistance_vs_current,
        window.pg_plot_resistance_vs_sample,
    ):
        plot_item = plot.getPlotItem()
        bottom_axis = plot_item.getAxis("bottom")
        left_axis = plot_item.getAxis("left")
        top_axis = plot_item.getAxis("top")
        right_axis = plot_item.getAxis("right")

        assert top_axis.isVisible()
        assert right_axis.isVisible()
        assert bottom_axis.grid is False
        assert left_axis.grid is False
        assert plot_item.ctrl.xGridCheck.isChecked() is False
        assert plot_item.ctrl.yGridCheck.isChecked() is False
        assert top_axis.labelText == ""
        assert right_axis.labelText == ""
        assert top_axis.style["showValues"] is False
        assert right_axis.style["showValues"] is False
        assert top_axis.style["tickLength"] == 0
        assert right_axis.style["tickLength"] == 0


def test_live_dashboard_pyqtgraph_ranges_leave_right_padding(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window._append_measurement_sample(10.0, 100.0)
    window._append_measurement_sample(20.0, 110.0)

    current_range = window.pg_plot_resistance_vs_current.getPlotItem().viewRange()[0]
    sample_range = window.pg_plot_resistance_vs_sample.getPlotItem().viewRange()[0]
    assert current_range[1] > 20.0
    assert sample_range[1] > 2.0


def test_live_dashboard_draws_pyqtgraph_segments(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window.current_step_mA = 1.0
    window._append_measurement_sample(5.0, 100.0)
    window._append_measurement_sample(6.0, 105.0)
    window._append_measurement_sample(5.0, 103.0)
    window._append_measurement_sample(4.0, 101.0)

    assert window._plot_backend == "pyqtgraph"
    assert len(window._segment_lines_ax1) == 2
    assert len(window._segment_lines_ax2) == 2
    assert all(hasattr(item, "setData") for item in window._segment_lines_ax1)


def test_live_dashboard_keeps_turning_point_in_both_direction_runs(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.current_step_mA = 1.0
    window._reset_sample_buffers()

    for current, resistance in [
        (1.0, 100.0),
        (2.0, 102.0),
        (3.0, 104.0),
        (2.0, 103.0),
        (1.0, 101.0),
    ]:
        window._append_measurement_sample(current, resistance)

    assert window._segment_runs(window._samples_current) == [
        ("#dc2626", 0, 2),
        ("#2563eb", 2, 4),
    ]
    red_item, blue_item = window._segment_lines_ax1
    red_x, red_y = red_item.getData()
    blue_x, blue_y = blue_item.getData()
    assert list(red_x) == [1.0, 2.0, 3.0]
    assert list(red_y) == [100.0, 102.0, 104.0]
    assert list(blue_x) == [3.0, 2.0, 1.0]
    assert list(blue_y) == [104.0, 103.0, 101.0]


def test_live_dashboard_keeps_real_start_current_point(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.start_current_mA = 1
    window._reset_sample_buffers()

    window._append_measurement_sample(1.0, 98.0)
    window._append_measurement_sample(2.0, 100.0)

    assert window._samples_current == [1.0, 2.0]
    assert window._samples_resistance == [98.0, 100.0]


def test_live_dashboard_skips_sub_start_current_points(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.start_current_mA = 1
    window._reset_sample_buffers()

    window._append_measurement_sample(0.2, 0.0)
    window._append_measurement_sample(1.0, 98.0)

    assert window._samples_current == [1.0]
    assert window._samples_resistance == [98.0]


def test_live_dashboard_skips_zero_resistance_points_at_start_current(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.start_current_mA = 1
    window._reset_sample_buffers()

    window._append_measurement_sample(1.0, 0.0)
    window._append_measurement_sample(1.0, 98.0)
    window._append_measurement_sample(2.0, 100.0)

    assert window._samples_current == [1.0, 2.0]
    assert window._samples_resistance == [98.0, 100.0]
    curve_colors = [
        item.opts["pen"].color().name()
        for item in window._segment_lines_ax1
        if hasattr(item, "opts")
    ]
    assert curve_colors == ["#dc2626"]


def test_live_dashboard_groups_pyqtgraph_segments_by_direction(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window.current_step_mA = 1.0
    for offset in range(20):
        current = 5.0 + (offset % 4)
        window._append_measurement_sample(current, 100.0 + offset)

    assert window._plot_backend == "pyqtgraph"
    assert len(window._segment_lines_ax1) == len(window._segment_runs(window._samples_current))
    assert len(window._segment_lines_ax2) == len(window._segment_runs(window._samples_current))
    for item in window._segment_lines_ax1 + window._segment_lines_ax2:
        x_values, y_values = item.getData()
        assert not any(logger_mod.math.isnan(float(value)) for value in x_values)
        assert not any(logger_mod.math.isnan(float(value)) for value in y_values)


def test_live_dashboard_pyqtgraph_uses_cycle_palette(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window.current_step_mA = 1.0
    for current, resistance in [
        (5.0, 100.0),
        (6.0, 101.0),
        (7.0, 102.0),
        (6.0, 103.0),
        (5.0, 104.0),
        (6.0, 105.0),
        (7.0, 106.0),
    ]:
        window._append_measurement_sample(current, resistance)

    assert window._plot_backend == "pyqtgraph"
    curve_colors = {
        item.opts["pen"].color().name()
        for item in window._segment_lines_ax1
        if hasattr(item, "opts")
    }
    assert {"#dc2626", "#2563eb", "#f97316"}.issubset(curve_colors)


def test_live_dashboard_ignores_initial_zero_current_placeholder(qtbot) -> None:
    pytest.importorskip("pyqtgraph")
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)

    window._record_zero_placeholder()

    assert window._samples_current == []
    assert window._samples_resistance == []
    assert window._segment_lines_ax1 == []
    assert window._segment_lines_ax2 == []
    assert window._zero_placeholder_count == 0


def test_record_acquired_sample_writes_each_non_initial_sample_once(tmp_path, qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.f_name = str(tmp_path / "annealing.tsv")
    window.first_sample = True
    window.current_current_read = 0.002
    window.current_voltage = 0.5
    window.current_resistance = 250.0
    window.curr_value_x = 2.0
    window.curr_value_y = 250.0
    window._reset_sample_buffers()

    window._record_acquired_sample()
    window.current_current_read = 0.003
    window.current_voltage = 0.6
    window.current_resistance = 200.0
    window.curr_value_x = 3.0
    window.curr_value_y = 200.0
    window._record_acquired_sample()

    assert (tmp_path / "annealing.tsv").read_text(encoding="utf-8").splitlines() == [
        "3\t0.6\t200"
    ]
    assert window._samples_current == [2.0, 3.0]
    assert window._samples_resistance == [250.0, 200.0]


def test_invalid_zero_resistance_readback_does_not_consume_first_sample(tmp_path, qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.f_name = str(tmp_path / "annealing.tsv")
    window.first_sample = True
    window.start_current_mA = 1
    window._reset_sample_buffers()

    window.curr_value_x = 1.0
    window.curr_value_y = 0.0
    window.current_current_read = 0.001
    window.current_voltage = 0.0
    window.current_resistance = 0.0
    window._record_acquired_sample()

    window.curr_value_x = 1.0
    window.curr_value_y = 98.0
    window.current_current_read = 0.001
    window.current_voltage = 0.098
    window.current_resistance = 98.0
    window._record_acquired_sample()

    assert window.first_sample is False
    assert window._samples_current == [1.0]
    assert window._samples_resistance == [98.0]
    assert not (tmp_path / "annealing.tsv").exists()


def test_shared_broker_init_leases_and_configures_current_annealing_channel(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeBrokerClient()
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window.max_voltage = 30.0
    window.current_current_set = 0.010
    window.process_running = True

    window.send_init_commands()

    assert fake.calls[:2] == [
        ("lease", {"channel": 1, "owner": "current_annealing_logger", "role": "current_annealing"}),
        (
            "configure_channel",
            {
                "channel": 1,
                "lease_id": "lease-1",
                "voltage_v": 30.0,
                "current_a": 0.01,
                "output_on": True,
            },
        ),
    ]


def test_shared_broker_init_enables_cached_polling_when_available(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeScheduledBrokerClient()
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window.max_voltage = 30.0
    window.current_current_set = 0.010
    window.process_running = True

    window.send_init_commands()

    assert ("configure_polling", {"channel": 1, "interval_s": 1.0}) in fake.calls
    assert ("start_scheduler", {"tick_s": 0.05}) in fake.calls


def test_shared_broker_measurement_updates_live_values_without_raw_serial(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeBrokerClient()
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window._shared_broker_lease_id = "lease-1"

    assert window._read_shared_broker_sample() is True

    assert window.current_voltage == pytest.approx(2.5)
    assert window.current_current_read == pytest.approx(0.010)
    assert window.current_resistance == pytest.approx(250.0)
    assert fake.calls == [("measure_channel", {"channel": 1})]


def test_shared_broker_measurement_prefers_cached_scheduler_readback(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeScheduledBrokerClient()
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window._shared_broker_lease_id = "lease-1"

    assert window._read_shared_broker_sample() is True

    assert window.current_voltage == pytest.approx(2.5)
    assert window.current_current_read == pytest.approx(0.010)
    assert fake.calls == [
            (
                "latest_readback",
                {"channel": 1, "max_age_s": 2.5, "fallback_to_measure": True},
            )
        ]


def test_shared_broker_measurement_retries_transient_missing_readback(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeScheduledBrokerClient()
    fake.readbacks = [
        {"voltage_V": None, "current_mA": 0.0},
        {"voltage_V": 2.0, "current_mA": 8.0},
    ]
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window._shared_broker_lease_id = "lease-1"

    assert window._read_shared_broker_sample() is True

    assert window.current_voltage == pytest.approx(2.0)
    assert window.current_current_read == pytest.approx(0.008)
    latest_calls = [call for call in fake.calls if call[0] == "latest_readback"]
    assert len(latest_calls) == 2


def test_shared_broker_measurement_skips_sub_start_current_readback(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeScheduledBrokerClient()
    fake.readbacks = [{"voltage_V": 0.05, "current_mA": 0.2}]
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.start_current_mA = 1
    window.channel_select = 1
    window._shared_broker_lease_id = "lease-1"
    window.current_resistance = 0.0

    assert window._read_shared_broker_sample() is True

    assert window._skip_current_sample is True
    assert window.current_current_read == pytest.approx(0.0002)
    assert window.current_resistance == pytest.approx(0.0)


def test_shared_broker_setpoint_and_stop_only_affect_leased_channel(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeBrokerClient()
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 2
    window._shared_broker_lease_id = "lease-1"
    window.ui.spinBox_max_current.setValue(30)
    window.current_current_set = 0.025

    window._send_current_setpoint()
    window.send_safe_end_commands()

    assert fake.calls == [
        ("set_current", {"channel": 2, "lease_id": "lease-1", "current_mA": 25.0}),
        (
            "configure_channel",
            {
                "channel": 2,
                "lease_id": "lease-1",
                "voltage_v": 0.0,
                "current_a": 0.0,
                "output_on": False,
            },
        ),
        ("release", {"channel": 2, "lease_id": "lease-1"}),
    ]
    assert window._shared_broker_lease_id is None


def test_direct_hmp_safe_end_resets_selected_channel(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._apply_supply_profile("hmp4040")
    window.channel_select = 3
    window.start_current_mA = 20
    window._refresh_command_profiles()

    assert window.commands_safe_end[:4] == [
        "INST:NSEL 3\n",
        "CURR 0.0000\n",
        "VOLT 0.000\n",
        "OUTP OFF\n",
    ]
    assert f"CURR {window._start_current_A():.4f}\n" not in window.commands_safe_end


def test_shared_broker_setpoint_uses_rate_limited_ramp_when_available(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeScheduledBrokerClient()
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 2
    window._shared_broker_lease_id = "lease-1"
    window.ui.spinBox_max_current.setValue(30)
    window.current_current_set = 0.025
    window.current_step_mA = 1.0

    window._send_current_setpoint()

    assert fake.calls == [
        (
            "schedule_current_ramp",
            {
                "channel": 2,
                "lease_id": "lease-1",
                "target_mA": 25.0,
                "rate_mA_s": 1.0,
                "max_step_mA": 0.2,
                "resolution_mA": 0.2,
            },
        ),
    ]


def test_shared_broker_run_writes_measurements_to_log(tmp_path, qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeBrokerClient()
    fake.readbacks = [
        {"voltage_V": 0.5, "current_mA": 2.0},
        {"voltage_V": 0.6, "current_mA": 3.0},
    ]
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 1
    window._shared_broker_lease_id = "lease-1"
    window.operation_mode = 2
    window.process_running = True
    window.first_sample = True
    window.current_increment = 0.0
    window.current_current_set = 0.002
    window.f_name = str(tmp_path / "annealing.tsv")
    window._reset_sample_buffers()

    window.handle_send_new_command()
    window.handle_send_new_command()

    lines = (tmp_path / "annealing.tsv").read_text(encoding="utf-8").splitlines()
    assert lines == ["3\t0.6\t200"]


def test_accepting_direct_serial_sample_writes_one_row(tmp_path, qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.f_name = str(tmp_path / "annealing.tsv")
    window.first_sample = False
    window.current_current_read = 0.061
    window.current_voltage = 5.063
    window.current_resistance = window.current_voltage / window.current_current_read
    window.curr_value_x = window.current_current_read * 1000.0
    window.curr_value_y = window.current_resistance
    window._reset_sample_buffers()

    window._accept_measurement_sample()

    lines = (tmp_path / "annealing.tsv").read_text(encoding="utf-8").splitlines()
    assert lines == ["61\t5.063\t83"]


def test_zero_current_sample_does_not_add_plot_point(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window._reset_sample_buffers()
    window.ax1 = logger_mod.Figure().add_subplot(111)
    window.ax2 = logger_mod.Figure().add_subplot(111)

    window._record_zero_placeholder()
    window._append_measurement_sample(0.0, 123.0)
    window._append_measurement_sample(1.0, 0.0)

    assert window._samples_current == []
    assert window._samples_resistance == []
    assert len(window.ax1.lines) == 0
    assert len(window.ax2.lines) == 0


def test_logger_segments_use_current_annealing_cycle_palette(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.current_step_mA = 1

    colors = window._segment_colors([1.0, 2.0, 3.0, 2.0, 1.0, 2.0, 3.0])

    assert colors[0] in logger_mod.INCREASING_CYCLE_COLORS
    assert colors[1] in logger_mod.INCREASING_CYCLE_COLORS
    assert colors[2] in logger_mod.DECREASING_CYCLE_COLORS
    assert colors[3] in logger_mod.DECREASING_CYCLE_COLORS
    assert colors[4] in logger_mod.INCREASING_CYCLE_COLORS
    assert colors[5] in logger_mod.INCREASING_CYCLE_COLORS
    assert colors == [
        "#dc2626",
        "#dc2626",
        "#2563eb",
        "#2563eb",
        "#f97316",
        "#f97316",
    ]


def test_logger_segments_ignore_single_point_cooling_jitter(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.current_step_mA = 1

    colors = window._segment_colors([6.0, 5.0, 4.0, 4.8, 3.8, 2.8])

    assert colors == ["#2563eb"] * 5
    assert window._segment_runs([6.0, 5.0, 4.0, 4.8, 3.8, 2.8]) == [("#2563eb", 0, 5)]


def test_measurement_history_dialog_uses_scroll_area_instead_of_tabs(qtbot) -> None:
    entries = [
        {
            "title": f"Run {idx}",
            "currents": [1.0, 2.0, 3.0, 2.0, 1.0],
            "resistances": [40.0 + idx, 41.0 + idx, 42.0 + idx, 41.5 + idx, 41.0 + idx],
            "timestamp": "2026-06-18 14:00:00",
            "source": f"run-{idx}.txt",
        }
        for idx in range(5)
    ]
    dialog = logger_mod.MeasurementHistoryDialog(None, entries)
    qtbot.addWidget(dialog)

    assert dialog.findChild(logger_mod.QtWidgets.QScrollArea) is not None
    assert dialog.findChild(logger_mod.QtWidgets.QTabWidget) is None
    assert len(dialog.findChildren(logger_mod.QtWidgets.QFrame)) >= 5


def test_prepare_output_file_writes_current_ui_metadata(tmp_path, qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.ui.lineEdit_log_dir.setText(str(tmp_path))
    window.ui.lineEdit_log_file.setText("Ni50Fe27Ga23 12_2 100mA test")
    window.ui.lineEdit_composition.setText("Ni50Fe27Ga23")
    window.ui.lineEdit_microwire.setText("12_2")
    window.ui.lineEdit_sample.setText("s1")
    window.ui.spinBox_max_current.setValue(100)
    window.ui.spinBox_step_mA.setValue(1)
    window.ui.checkBox_reverse.setChecked(True)
    window.ui.spinBox_loops.setValue(2)

    assert window.prepare_output_file() is True

    output = logger_mod.Path(window.f_name)
    metadata_path = tmp_path / "metadata" / output.stem / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["output_file"] == str(output)
    assert payload["composition"] == "Ni50Fe27Ga23"
    assert payload["microwire"] == "12_2"
    assert payload["max_current_mA"] == 100
    assert payload["reverse_enabled"] is True
    assert payload["loops"] == 2


def test_annealing_run_holds_sleep_guard_until_safe_end(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    calls: list[str] = []

    class _FakeSleepGuard:
        def acquire(self) -> None:
            calls.append("acquire")

        def release(self) -> None:
            calls.append("release")

    monkeypatch.setattr(logger_mod, "create_experiment_sleep_guard", lambda _reason: _FakeSleepGuard())
    window._apply_supply_profile("shared_hmp_broker")
    window._shared_broker_client = _FakeBrokerClient()
    window.channel_select = 1
    window.max_voltage = 30.0
    window.current_current_set = 0.010
    window.process_running = True

    window.send_init_commands()
    assert calls == ["acquire"]

    window.send_safe_end_commands()
    assert calls == ["acquire", "release"]
