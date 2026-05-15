from __future__ import annotations

from pathlib import Path

import pytest

from PyQt6 import QtCore, QtGui, QtWidgets

from data_logging.ac_susceptibility_logger import lcr6000
from data_logging.ac_susceptibility_logger import sweep

ac_logger = pytest.importorskip(
    "data_logging.ac_susceptibility_logger.ac_susceptibility_logger",
    reason="Qt widgets backend is unavailable",
    exc_type=ImportError,
)


def _isolate_ac_qsettings(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    original = QtCore.QSettings
    ini_format = original.Format.IniFormat
    user_scope = original.Scope.UserScope

    def factory(_organization: str, _application: str) -> QtCore.QSettings:
        settings = original(
            ini_format,
            user_scope,
            "microwire_tests",
            name,
        )
        settings.clear()
        return settings

    monkeypatch.setattr(ac_logger.QtCore, "QSettings", factory)


def test_parse_numeric_list_accepts_frequency_suffixes() -> None:
    assert lcr6000.parse_numeric_list("100, 1k, 2.5MHz", quantity="frequency") == pytest.approx(
        [100.0, 1000.0, 2_500_000.0]
    )


def test_parse_numeric_list_accepts_current_suffixes() -> None:
    assert lcr6000.parse_numeric_list("100uA, 1mA", quantity="current") == pytest.approx(
        [100e-6, 1e-3]
    )


def test_build_settings_plan_crosses_frequency_and_level() -> None:
    plan = lcr6000.build_settings_plan(
        [100.0, 1000.0],
        [0.1, 0.3],
        level_mode="voltage",
        function="ls-q",
        monitor1="z",
        monitor2="iac",
        aperture="fast",
    )

    assert len(plan) == 4
    assert [item.frequency_hz for item in plan] == [100.0, 100.0, 1000.0, 1000.0]
    assert [item.level_value for item in plan] == [0.1, 0.3, 0.1, 0.3]
    assert all(item.function == "Ls-Q" for item in plan)
    assert all(item.monitor2 == "IAC" for item in plan)


def test_lcr_monitor_off_uses_uppercase_scpi_token() -> None:
    setting = lcr6000.Lcr6000Settings(
        frequency_hz=1000.0,
        level_value=0.1,
        level_mode="voltage",
        function="Ls-Rs",
        monitor1=lcr6000.normalize_monitor("off"),
        monitor2=lcr6000.normalize_monitor("OFF"),
        aperture="FAST",
    )

    assert setting.monitor1 == "OFF"
    assert setting.monitor2 == "OFF"
    assert "FUNC:MON1 OFF\n" in lcr6000.commands_for_settings(setting)
    assert "FUNC:MON2 OFF\n" in lcr6000.commands_for_settings(setting)


def test_lcr6200_limits_accept_manual_frequency_and_voltage_ranges() -> None:
    lcr6000.validate_settings(
        lcr6000.Lcr6000Settings(
            frequency_hz=200000.0,
            level_value=2.0,
            level_mode="voltage",
            function="Ls-Rs",
        ),
        model="LCR-6200",
    )

    with pytest.raises(ValueError, match="10 Hz to 200 kHz"):
        lcr6000.validate_settings(
            lcr6000.Lcr6000Settings(
                frequency_hz=200001.0,
                level_value=0.1,
                level_mode="voltage",
                function="Ls-Rs",
            ),
            model="LCR-6200",
        )

    with pytest.raises(ValueError, match="10 mV to 2 V"):
        lcr6000.validate_settings(
            lcr6000.Lcr6000Settings(
                frequency_hz=1000.0,
                level_value=0.001,
                level_mode="voltage",
                function="Ls-Rs",
            ),
            model="LCR-6200",
        )


def test_build_ac_settings_plan_crosses_models_frequency_and_level() -> None:
    plan = sweep.build_ac_settings_plan(
        models=["ls-rs", "Lp-Rp"],
        frequencies_hz=[1000.0, 10000.0],
        levels=[0.1, 0.3],
        level_mode="voltage",
        monitor1="z",
        monitor2="iac",
        aperture="fast",
    )

    assert len(plan) == 8
    assert [(item.function, item.frequency_hz, item.level_value) for item in plan[:4]] == [
        ("Ls-Rs", 1000.0, 0.1),
        ("Ls-Rs", 1000.0, 0.3),
        ("Ls-Rs", 10000.0, 0.1),
        ("Ls-Rs", 10000.0, 0.3),
    ]
    assert all(item.function == "Lp-Rp" for item in plan[4:])


def test_build_current_loop_points_supports_up_down_without_duplicate_peak() -> None:
    points = sweep.build_current_loop_points(
        start_mA=20.0,
        stop_mA=80.0,
        step_mA=30.0,
        direction_mode="up-down",
    )

    assert [(point.current_a, point.direction) for point in points] == [
        (0.02, "up"),
        (0.05, "up"),
        (0.08, "up"),
        (0.05, "down"),
        (0.02, "down"),
    ]


def test_estimate_sweep_totals_counts_time_per_point_and_dwell() -> None:
    estimate = sweep.estimate_sweep(
        lcr_settings=[lcr6000.Lcr6000Settings(1000.0, 0.1), lcr6000.Lcr6000Settings(10000.0, 0.1)],
        current_points=[
            sweep.CurrentLoopPoint(0.02, "up"),
            sweep.CurrentLoopPoint(0.04, "up"),
            sweep.CurrentLoopPoint(0.02, "down"),
        ],
        point_duration_s=10.0,
        dwell_s=2.0,
    )

    assert estimate.total_measurements == 0
    assert estimate.estimated_seconds == pytest.approx(72.0)


def test_power_supply_idn_classification_detects_supported_backends() -> None:
    assert sweep.classify_power_supply_idn("HAMEG,HMP4030,022982747,HW50020001/SW2.50") == "hmp4030"
    assert sweep.classify_power_supply_idn("OWON,SPE6102,123456,V1.0") == "owon_spe6102"
    assert sweep.classify_power_supply_idn("LCR-6200,REV E8.13,GEZ883931") is None


def test_detect_power_supply_candidates_queries_ports_without_selecting_lcr() -> None:
    class FakeInfo:
        def __init__(self, device: str, description: str) -> None:
            self.device = device
            self.description = description

    class FakeSerial:
        replies = {
            ("COM7", 9600): "OWON,SPE6102,123456,V1.0\n",
            ("COM9", 115200): "LCR-6200,REV E8.13,GEZ883931\n",
        }

        def __init__(self, port: str, baudrate: int, timeout: float, write_timeout: float) -> None:
            self.port = port
            self.baudrate = baudrate
            self.is_open = True
            self.written: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.written.append(data)

        def flush(self) -> None:
            return None

        def reset_input_buffer(self) -> None:
            return None

        def readline(self) -> bytes:
            return self.replies.get((self.port, self.baudrate), "").encode("ascii")

        def close(self) -> None:
            self.is_open = False

    candidates = sweep.detect_power_supply_candidates(
        ports=[
            FakeInfo("COM7", "USB Serial Port"),
            FakeInfo("COM9", "LCR Meter Virtual COM Port"),
        ],
        serial_factory=FakeSerial,
        baudrates=[9600, 115200],
    )

    assert [(item.resource, item.backend_id, item.baudrate) for item in candidates] == [
        ("COM7", "owon_spe6102", 9600)
    ]


def test_write_sweep_metadata_and_row_flushes_incrementally(tmp_path: Path) -> None:
    path = tmp_path / "overnight.tsv"
    config = sweep.AcSweepConfig(
        lcr_settings=[lcr6000.Lcr6000Settings(1000.0, 0.1, function="Ls-Rs")],
        current_points=[sweep.CurrentLoopPoint(0.02, "up")],
        point_duration_s=0.0,
        repeats=1,
        dwell_s=0.0,
        psu_backend="owon_spe6102",
        psu_resource="COM7",
        voltage_limit_v=5.0,
    )
    reading = lcr6000.Lcr6000Reading(
        timestamp_utc="2026-05-13T10:00:00.000+00:00",
        raw="+1.0,+2.0,+0.0,+0.0,OK",
        primary=1.0,
        secondary=2.0,
        monitor1=0.0,
        monitor2=0.0,
        comparator="OK",
    )
    writer = sweep.AcSweepTsvWriter(path, config)

    writer.write_metadata()
    writer.write_row(
        sweep.AcSweepRow(
            timestamp_utc="2026-05-13T10:00:01.000+00:00",
            elapsed_s=1.5,
            setting_index=1,
            total_settings=1,
            setting=config.lcr_settings[0],
            current_point=config.current_points[0],
            repeat_index=1,
            lcr_reading=reading,
            psu_measurement=sweep.PowerSupplyMeasurement(current_actual_a=0.0198, voltage_actual_v=1.2, status="OK"),
            psu_backend="owon_spe6102",
            psu_resource="COM7",
        )
    )
    writer.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# AC susceptibility sweep"
    assert "psu_backend=owon_spe6102" in lines[1]
    assert lines[-2] == sweep.SWEEP_HEADER_LINE
    assert lines[-1].split("\t") == [
        "2026-05-13T10:00:01.000+00:00",
        "1.5",
        "1",
        "1",
        "Ls-Rs",
        "1000",
        "voltage",
        "0.1",
        "0.02",
        "0.0198",
        "1.2",
        "up",
        "1",
        "1",
        "2",
        "0",
        "0",
        "OK",
        "+1.0,+2.0,+0.0,+0.0,OK",
        "owon_spe6102",
        "COM7",
        "OK",
        "",
    ]


def test_run_ac_sweep_uses_owon_backend_and_safe_shutdown_on_lcr_failure(tmp_path: Path) -> None:
    class FakeLcr:
        def __init__(self) -> None:
            self.configured: list[lcr6000.Lcr6000Settings] = []

        def configure(self, setting: lcr6000.Lcr6000Settings) -> None:
            self.configured.append(setting)

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            raise RuntimeError("empty LCR response")

    class FakePsu:
        backend_id = "owon_spe6102"
        resource = "COM7"

        def __init__(self) -> None:
            self.events: list[object] = []

        def connect(self) -> None:
            self.events.append("connect")

        def initialize(self, *, voltage_limit_v: float) -> None:
            self.events.append(("initialize", voltage_limit_v))

        def set_current(self, current_a: float) -> None:
            self.events.append(("current", current_a))

        def measure(self) -> sweep.PowerSupplyMeasurement:
            self.events.append("measure")
            return sweep.PowerSupplyMeasurement(current_actual_a=0.02, voltage_actual_v=1.0, status="OK")

        def output_off(self) -> None:
            self.events.append("off")

        def close(self) -> None:
            self.events.append("close")

    psu = FakePsu()
    config = sweep.AcSweepConfig(
        lcr_settings=[lcr6000.Lcr6000Settings(1000.0, 0.1, function="Ls-Rs")],
        current_points=[sweep.CurrentLoopPoint(0.02, "up")],
        point_duration_s=0.0,
        repeats=1,
        dwell_s=0.0,
        psu_backend="owon_spe6102",
        psu_resource="COM7",
        voltage_limit_v=5.0,
    )

    with pytest.raises(RuntimeError, match="empty LCR response"):
        sweep.run_ac_sweep(
            config=config,
            lcr=FakeLcr(),
            psu=psu,
            output_path=tmp_path / "failed.tsv",
            sleep=lambda _seconds: None,
        )

    assert psu.events == [
        "connect",
        ("initialize", 5.0),
        ("current", 0.02),
        "off",
        "close",
    ]


def test_run_ac_sweep_measures_each_current_point_for_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        def advance(self, seconds: float) -> None:
            self.now += seconds

    class FakeLcr:
        def __init__(self, clock: FakeClock) -> None:
            self.clock = clock
            self.fetch_count = 0

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            return None

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            self.fetch_count += 1
            self.clock.advance(0.4)
            return lcr6000.Lcr6000Reading(
                timestamp_utc=f"2026-05-13T10:00:{self.fetch_count:02d}.000+00:00",
                raw="+1.0,+2.0,+0.0,+0.0,OK",
                primary=1.0,
                secondary=2.0,
                monitor1=0.0,
                monitor2=0.0,
                comparator="OK",
            )

    class FakePsu:
        backend_id = "owon_spe6102"
        resource = "COM7"

        def connect(self) -> None:
            return None

        def initialize(self, *, voltage_limit_v: float) -> None:
            return None

        def set_current(self, _current_a: float) -> None:
            return None

        def measure(self) -> sweep.PowerSupplyMeasurement:
            return sweep.PowerSupplyMeasurement(status="OK")

        def output_off(self) -> None:
            return None

        def close(self) -> None:
            return None

    clock = FakeClock()
    monkeypatch.setattr(sweep.time, "monotonic", clock.monotonic)
    lcr = FakeLcr(clock)
    progress_rows: list[sweep.AcSweepRow] = []
    config = sweep.AcSweepConfig(
        lcr_settings=[lcr6000.Lcr6000Settings(1000.0, 0.1, function="Ls-Rs")],
        current_points=[sweep.CurrentLoopPoint(0.02, "up")],
        point_duration_s=1.0,
        dwell_s=0.0,
        psu_backend="owon_spe6102",
        psu_resource="COM7",
        voltage_limit_v=5.0,
    )

    sweep.run_ac_sweep(
        config=config,
        lcr=lcr,
        psu=FakePsu(),
        output_path=tmp_path / "duration.tsv",
        progress=progress_rows.append,
    )

    assert lcr.fetch_count == 3
    assert [row.repeat_index for row in progress_rows] == [1, 2, 3]


def _wheel_event(delta_y: int = -120) -> QtGui.QWheelEvent:
    return QtGui.QWheelEvent(
        QtCore.QPointF(10.0, 10.0),
        QtCore.QPointF(10.0, 10.0),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, delta_y),
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_ac_logger_wheel_guard_scrolls_parent_without_changing_spinbox() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    scroll = QtWidgets.QScrollArea()
    content = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(content)
    spin = QtWidgets.QSpinBox(content)
    spin.setRange(0, 10)
    spin.setValue(5)
    filler = QtWidgets.QWidget(content)
    filler.setMinimumHeight(800)
    layout.addWidget(spin)
    layout.addWidget(filler)
    scroll.setWidget(content)
    scroll.resize(200, 120)
    scroll.show()
    app.processEvents()

    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window._ac_lcr_scroll_area = scroll
    spin.setProperty("_ac_wheel_guard", True)
    start_scroll = scroll.verticalScrollBar().value()

    assert window.eventFilter(spin, _wheel_event()) is True
    assert spin.value() == 5
    assert scroll.verticalScrollBar().value() > start_scroll


def test_lcr_preset_lists_match_simplified_ac_workflow() -> None:
    assert ac_logger.PRACTICAL_FREQUENCY_PRESETS_HZ == [
        10.0,
        20.0,
        50.0,
        100.0,
        200.0,
        500.0,
        1000.0,
        2000.0,
        5000.0,
        10000.0,
        20000.0,
        50000.0,
        100000.0,
        200000.0,
    ]
    assert ac_logger.LCR_FRONT_PANEL_VOLTAGE_PRESETS_V == [
        0.01,
        0.1,
        0.3,
        0.5,
        1.0,
        1.5,
        2.0,
    ]


def test_ac_logger_uses_shared_owon_supply_defaults_for_sweep_config() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window.ac_settings = QtCore.QSettings("microwire", "ac_susceptibility_logger_test_shared_owon")
    window.ac_settings.clear()
    window._lcr_plan = []
    window.ui = type("Ui", (), {})()
    window.ui.comboBox_supply = QtWidgets.QComboBox()
    window.ui.comboBox_supply.addItem("Owon SPE6102", "owon_spe6102")
    window.ui.comboBox_port = QtWidgets.QComboBox()
    window.ui.comboBox_port.addItem("COM7", "COM7")
    window.ui.comboBox_baudrate = QtWidgets.QComboBox()
    window.ui.comboBox_baudrate.addItem("9600")
    window.spinBox_ac_voltage_limit = QtWidgets.QDoubleSpinBox()
    window.spinBox_ac_voltage_limit.setRange(0.1, 120.0)
    window.spinBox_ac_voltage_limit.setValue(5.0)
    window.spinBox_ac_current_start = QtWidgets.QDoubleSpinBox()
    window.spinBox_ac_current_start.setValue(20.0)
    window.spinBox_ac_current_stop = QtWidgets.QDoubleSpinBox()
    window.spinBox_ac_current_stop.setValue(80.0)
    window.spinBox_ac_current_step = QtWidgets.QDoubleSpinBox()
    window.spinBox_ac_current_step.setValue(20.0)
    window.comboBox_ac_direction = QtWidgets.QComboBox()
    window.comboBox_ac_direction.addItem("Up and down", "up-down")
    window.spinBox_ac_dwell = QtWidgets.QDoubleSpinBox()
    window.spinBox_ac_dwell.setValue(0.5)
    window.spinBox_ac_point_duration = QtWidgets.QDoubleSpinBox()
    window.spinBox_ac_point_duration.setValue(10.0)
    window._prepare_lcr_plan = lambda: [lcr6000.Lcr6000Settings(1000.0, 0.1, function="Ls-Rs")]

    window._sync_ac_psu_from_shared_controls()
    config = window._build_ac_sweep_config()

    assert config.psu_backend == "owon_spe6102"
    assert config.psu_resource == "COM7"
    assert window._selected_ac_psu_baudrate() == 9600
    assert config.voltage_limit_v == pytest.approx(62.0)
    app.processEvents()


def test_ac_logger_upgrades_older_owon_voltage_limit_defaults() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window.ui = type("Ui", (), {})()
    window.ui.comboBox_supply = QtWidgets.QComboBox()
    window.ui.comboBox_supply.addItem("Owon SPE6102", "owon_spe6102")
    window.ui.comboBox_port = QtWidgets.QComboBox()
    window.ui.comboBox_port.addItem("COM7", "COM7")
    window.ui.comboBox_baudrate = QtWidgets.QComboBox()
    window.ui.comboBox_baudrate.addItem("9600")
    window.label_ac_psu_status = QtWidgets.QLabel()
    window.spinBox_ac_voltage_limit = QtWidgets.QDoubleSpinBox()
    window.spinBox_ac_voltage_limit.setRange(0.1, 120.0)

    for old_default in (5.0, 60.0):
        window.spinBox_ac_voltage_limit.setValue(old_default)
        window._sync_ac_psu_from_shared_controls()
        assert window.spinBox_ac_voltage_limit.value() == pytest.approx(62.0)

    window.spinBox_ac_voltage_limit.setValue(48.0)
    window._sync_ac_psu_from_shared_controls()
    assert window.spinBox_ac_voltage_limit.value() == pytest.approx(48.0)
    app.processEvents()


def test_ac_logger_simplified_window_hides_duplicate_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "simplified_window")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        assert window.ui.frame_process_settings.isHidden()
        assert window.frame_ac_plan_actions.isHidden()
        assert window.comboBox_lcr_function.isHidden()
        assert window.checkBox_lcr_model_lsrs.isHidden()
        assert window.checkBox_lcr_model_lprp.text() == "Also measure Lp-Rp"
        assert window.checkBox_lcr_model_lprp.isChecked() is False
        assert window.pushButton_measure_lcr_baseline.text() == "Measure empty-coil baseline"
        assert window.pushButton_run_ac_sweep.text() == "Run microwire current sweep"
        assert window.pushButton_lcr_default_presets.text() == "Default full scan"
        sticky_texts = {
            window.ui.pushButton_start_process.text(),
            window.ui.pushButton_show_history.text(),
            window.ui.pushButton_reverse_now.text(),
        }
        assert sticky_texts == {"Measure empty-coil baseline", "Run microwire current sweep", "Stop"}
        assert all("anneal" not in text.lower() for text in sticky_texts)
        assert all("history" not in text.lower() for text in sticky_texts)
        assert all("reverse" not in text.lower() for text in sticky_texts)
        assert window.pushButton_auto_setup.text() == "Auto-detect instruments"
        assert window.pushButton_identify_lcr.isHidden()
        assert window.ui.label_log_file.text() == "Microwire sweep base:"
        assert window.ui.label_extension.text() == ".tsv"
        assert window.label_ac_current_task.text() == "Current task: idle"
        assert "ac_susc_empty_coil_baseline" in window.label_ac_baseline_file.text()
        assert not hasattr(window, "comboBox_ac_psu_backend")
        assert not hasattr(window, "comboBox_ac_psu_port")
        assert not hasattr(window, "comboBox_ac_psu_baud")
        assert "Instrument setup" in window.groupBox_lcr_settings.title()
        assert "Experiment plan" in window.groupBox_ac_plan.title()
        assert not hasattr(window, "label_ac_read_interval")
        assert not hasattr(window, "spinBox_ac_read_interval")
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_numeric_fields_and_acquisition_labels_are_concise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "numeric_fields")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        window.ui.comboBox_supply.setCurrentIndex(window.ui.comboBox_supply.findData("owon_spe6102"))
        window.spinBox_ac_voltage_limit.setValue(5.0)
        window._sync_ac_psu_from_shared_controls()
        window.spinBox_ac_current_start.setValue(20.0)
        window.spinBox_ac_current_step.setValue(5.0)
        window.spinBox_ac_dwell.setValue(1.0)
        assert window.spinBox_ac_voltage_limit.text() == "62 V"
        assert window.spinBox_ac_current_start.text() == "20 mA"
        assert window.spinBox_ac_current_step.text() == "5 mA"
        assert window.spinBox_ac_dwell.text() == "1 s"
        assert window.spinBox_ac_point_duration.text() == "10 s"
        window.spinBox_ac_current_step.setValue(2.5)
        window.spinBox_ac_dwell.setValue(0.5)
        window.spinBox_ac_point_duration.setValue(2.5)
        assert window.spinBox_ac_current_step.text() == "2.5 mA"
        assert window.spinBox_ac_dwell.text() == "0.5 s"
        assert window.spinBox_ac_point_duration.text() == "2.5 s"
        assert window.label_ac_settle_time.text() == "Settle time:"
        assert window.label_ac_point_duration.text() == "Measure time/point:"
        assert not hasattr(window, "label_ac_baseline_readings")
        assert not hasattr(window, "spinBox_lcr_baseline_repeats")
        assert "Baseline:" in window.label_ac_sweep_estimate.text()
        before = window.label_ac_sweep_estimate.text()
        window.spinBox_ac_point_duration.setValue(window.spinBox_ac_point_duration.value() + 1)
        window._update_ac_sweep_estimate()
        assert window.label_ac_sweep_estimate.text() != before
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_default_output_names_are_ac_specific(tmp_path: Path) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window.ui = type("Ui", (), {})()
    window.ui.lineEdit_log_dir = QtWidgets.QLineEdit(str(tmp_path))
    window.ui.lineEdit_log_file = QtWidgets.QLineEdit("Ni50Fe27Ga23_20um")
    window.ui.lineEdit_log_file_full = QtWidgets.QLineEdit()

    baseline_name = window._baseline_output_path().name
    assert baseline_name.startswith("ac_susc_empty_coil_baseline_")
    assert "Ni50Fe27Ga23" not in baseline_name
    assert baseline_name.endswith(".tsv")

    window.ui.lineEdit_log_file.setText("")
    window._set_default_log_name()
    assert window.ui.lineEdit_log_file.text() == "ac_susc_current_sweep"
    sweep_name = window._sweep_output_path().name
    assert sweep_name.startswith("ac_susc_current_sweep_")
    assert sweep_name.endswith(".tsv")
    app.processEvents()


def test_ac_logger_output_settings_are_separate_from_current_annealing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    original = QtCore.QSettings
    stores: dict[str, QtCore.QSettings] = {}
    for name in ("current_annealing", "ac_susceptibility_logger", "naming_history", "current_annealing_history"):
        store = original(
            original.Format.IniFormat,
            original.Scope.UserScope,
            "microwire_tests",
            f"separate_ac_output_{name}",
        )
        store.clear()
        stores[name] = store
    anneal_dir = str(tmp_path / "current annealing")
    stores["current_annealing"].setValue("log_dir", anneal_dir)
    stores["current_annealing"].setValue("log_file", "Ni48Fe25Ga27_2_3_30mA_with_glass")

    def factory(_organization: str, application: str) -> QtCore.QSettings:
        return stores.setdefault(
            application,
            original(
                original.Format.IniFormat,
                original.Scope.UserScope,
                "microwire_tests",
                f"separate_ac_output_{application}",
            ),
        )

    monkeypatch.setattr(ac_logger.QtCore, "QSettings", factory)
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        assert window.ui.lineEdit_log_dir.text() == str(ac_logger.AC_DEFAULT_LOG_DIR)
        assert window.ui.lineEdit_log_file.text() == ac_logger.AC_DEFAULT_SWEEP_BASE
        assert window.ui.lineEdit_log_dir.text() != anneal_dir

        ac_dir = str(tmp_path / "ac susceptibility")
        window.ui.lineEdit_log_dir.setText(ac_dir)
        window.ui.lineEdit_log_file.setText("ac_susc_wire_test")
        window.sync_full_log_path()
        assert stores["ac_susceptibility_logger"].value("log_dir", type=str) == ac_dir
        assert stores["ac_susceptibility_logger"].value("log_file", type=str) == "ac_susc_wire_test"
        assert stores["current_annealing"].value("log_dir", type=str) == anneal_dir
        assert stores["current_annealing"].value("log_file", type=str) == "Ni48Fe25Ga27_2_3_30mA_with_glass"
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_graph_defaults_are_ac_susceptibility_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "graph_defaults")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        assert window.button_plot_setup.text() == "Configure plots"
        assert len(window._plot_tiles) == 4
        assert [tile.visible.isChecked() for tile in window._plot_tiles] == [True, True, False, False]
        assert window._plot_tiles[0].x_combo.currentData() == "current_mA"
        assert window._plot_tiles[0].y_left_combo.currentData() == "rs_ohm"
        assert window._plot_tiles[1].x_combo.currentData() == "current_mA"
        assert window._plot_tiles[1].y_left_combo.currentData() == "ls_h"
        assert len(window.figure.axes) == 2
        assert window.figure.axes[0].get_title() == "Rs vs DC current"
        assert window.figure.axes[0].get_ylabel() == "Rs [Ohm]"
        assert window.figure.axes[0].get_xlabel() == "DC current [mA]"
        assert window.figure.axes[1].get_title() == "Ls vs DC current"
        assert window.figure.axes[1].get_ylabel() == "Ls [H]"
        assert window.figure.axes[1].get_xlabel() == "DC current [mA]"
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_frequency_plot_uses_log_scatter_and_legend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "graph_frequency_scatter")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        for tile in window._plot_tiles:
            tile.visible.setChecked(False)
        tile = window._plot_tiles[0]
        tile.visible.setChecked(True)
        window._set_combo_data(tile.x_combo, "frequency_hz")
        window._set_combo_data(tile.y_left_combo, "rs_ohm")
        window._set_combo_data(tile.y_right_combo, "ls_h")
        window._ac_plot_points = [
            ac_logger.AcPlotPoint(0.0, "Ls-Rs", 10.0, 0.1, 0.0, 1e-5, 14.3),
            ac_logger.AcPlotPoint(1.0, "Ls-Rs", 100.0, 0.1, 0.0, 2e-5, 14.4),
            ac_logger.AcPlotPoint(2.0, "Ls-Rs", 1000.0, 0.1, 0.0, 3e-5, 14.5),
        ]

        window._refresh_ac_plots(force=True)

        axis = window.figure.axes[0]
        twin = window.figure.axes[1]
        assert axis.get_xscale() == "log"
        assert not axis.lines
        assert not twin.lines
        assert len(axis.collections) == 1
        assert len(twin.collections) == 1
        legend = axis.get_legend()
        assert legend is not None
        assert {text.get_text() for text in legend.get_texts()} == {"Rs [Ohm]", "Ls [H]"}
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_amplitude_plot_uses_scatter_without_log_x(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "graph_amplitude_scatter")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        for tile in window._plot_tiles:
            tile.visible.setChecked(False)
        tile = window._plot_tiles[0]
        tile.visible.setChecked(True)
        window._set_combo_data(tile.x_combo, "amplitude_v")
        window._set_combo_data(tile.y_left_combo, "rs_ohm")
        window._ac_plot_points = [
            ac_logger.AcPlotPoint(0.0, "Ls-Rs", 1000.0, 0.1, 0.0, 1e-5, 14.3),
            ac_logger.AcPlotPoint(1.0, "Ls-Rs", 1000.0, 1.0, 0.0, 2e-5, 14.4),
        ]

        window._refresh_ac_plots(force=True)

        axis = window.figure.axes[0]
        assert axis.get_xscale() == "linear"
        assert not axis.lines
        assert len(axis.collections) == 1
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_does_not_auto_detect_psu_during_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "startup_no_auto_detect")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    calls = {"detect": 0}

    def _detect(*_args: object, **_kwargs: object) -> list[sweep.PowerSupplyCandidate]:
        calls["detect"] += 1
        return []

    monkeypatch.setattr(sweep, "detect_power_supply_candidates", _detect)

    window = ac_logger.MainWindow()
    try:
        assert calls["detect"] == 0
        assert "from shared PSU controls" in window.label_ac_psu_status.text()
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_auto_setup_keeps_manual_psu_when_id_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "auto_setup_manual_psu_fallback")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [("COM6 - USB", "COM6")])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        window.ui.comboBox_port.clear()
        window.ui.comboBox_port.addItem("COM6 - USB", "COM6")
        window._set_combo_data(window.ui.comboBox_supply, "owon_spe6102")
        window.handle_auto_setup_clicked()
        assert window._selected_ac_psu_backend() == "owon_spe6102"
        assert window._selected_ac_psu_resource() == "COM6"
        assert "kept the manually selected" in window.label_lcr_status.text()
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_auto_setup_trusts_connected_shared_psu_without_id_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "auto_setup_connected_shared_psu")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [("COM6 - USB", "COM6")])
    calls = {"detect": 0}

    def _detect(*_args: object, **_kwargs: object) -> list[sweep.PowerSupplyCandidate]:
        calls["detect"] += 1
        return []

    monkeypatch.setattr(sweep, "detect_power_supply_candidates", _detect)

    window = ac_logger.MainWindow()
    try:
        window.ui.comboBox_port.clear()
        window.ui.comboBox_port.addItem("COM6 - USB", "COM6")
        window._set_combo_data(window.ui.comboBox_supply, "owon_spe6102")
        window.is_connected = True
        window.handle_auto_setup_clicked()
        assert calls["detect"] == 0
        assert window._selected_ac_psu_backend() == "owon_spe6102"
        assert "Using connected shared OWON SPE6102" in window.label_lcr_status.text()
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_uses_shared_point_duration_and_sticky_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "shared_readings_progress")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        assert window.spinBox_ac_point_duration.value() == pytest.approx(10.0)
        assert window.lineEdit_lcr_frequencies.text() == window._format_numeric_list(ac_logger.PRACTICAL_FREQUENCY_PRESETS_HZ)
        assert window.lineEdit_lcr_levels.text() == window._format_numeric_list(ac_logger.LCR_FRONT_PANEL_VOLTAGE_PRESETS_V)
        assert window.progress_ac_run.format() == "AC progress: idle"
        sticky_frame = window.ui.pushButton_start_process.parentWidget()
        sticky_parent_layout = sticky_frame.parentWidget().layout()
        progress_index = sticky_parent_layout.indexOf(window.progress_ac_run)
        sticky_index = sticky_parent_layout.indexOf(sticky_frame)
        assert progress_index >= 0
        assert sticky_index >= 0
        assert progress_index < sticky_index
        window.lineEdit_lcr_frequencies.setText("100, 1k")
        window.lineEdit_lcr_levels.setText("0.1, 1")
        window.spinBox_ac_point_duration.setValue(10.0)
        window.spinBox_ac_current_start.setValue(0.0)
        window.spinBox_ac_current_stop.setValue(10.0)
        window.spinBox_ac_current_step.setValue(10.0)
        window._update_ac_sweep_estimate()
        estimate = window.label_ac_sweep_estimate.text()
        assert "Baseline: about 44s" in estimate
        assert "Microwire sweep: about 2m 12s" in estimate
        monkeypatch.setattr(ac_logger.time, "monotonic", lambda: 100.0)
        window._reset_ac_progress("Empty-coil baseline", 100)
        monkeypatch.setattr(ac_logger.time, "monotonic", lambda: 110.0)
        window._ac_progress_value = 49
        window._advance_ac_progress("Empty-coil baseline")
        progress_text = window.progress_ac_run.format()
        assert "ETA" in progress_text
        assert "50/100" in progress_text
        window._set_ac_current_task("Current task: empty-coil baseline - 100 Hz, 0.1 voltage, read 1")
        assert "100 Hz" in window.label_ac_current_task.text()
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_writes_optional_diagnostics(tmp_path: Path) -> None:
    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    path = tmp_path / "ac_diag.jsonl"
    window._ac_diagnostics_enabled = True
    window._ac_diagnostics_path = path
    window._write_ac_diagnostic("plot_refresh", duration_s=0.1, points=3)
    text = path.read_text(encoding="utf-8")
    assert '"event": "plot_refresh"' in text
    assert '"points": 3' in text


def test_ac_logger_migrates_old_short_frequency_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    original = QtCore.QSettings
    store = original(
        original.Format.IniFormat,
        original.Scope.UserScope,
        "microwire_tests",
        "old_short_frequency_defaults",
    )
    store.clear()
    store.setValue("frequencies", "100, 1k, 10k, 100k")
    store.setValue("levels", "0.1, 0.3, 1.0")

    def factory(_organization: str, _application: str) -> QtCore.QSettings:
        return store

    monkeypatch.setattr(ac_logger.QtCore, "QSettings", factory)
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])
    window = ac_logger.MainWindow()
    try:
        assert window.lineEdit_lcr_frequencies.text() == window._format_numeric_list(ac_logger.PRACTICAL_FREQUENCY_PRESETS_HZ)
        assert window.lineEdit_lcr_levels.text() == window._format_numeric_list(ac_logger.LCR_FRONT_PANEL_VOLTAGE_PRESETS_V)
    finally:
        window.close()
        app.processEvents()


def test_commands_for_settings_use_lcr6000_scpi_spellings() -> None:
    settings = lcr6000.Lcr6000Settings(
        frequency_hz=1000.0,
        level_value=0.1,
        level_mode="voltage",
        function="Ls-Q",
        monitor1="Z",
        monitor2="IAC",
        aperture="FAST",
    )

    assert lcr6000.commands_for_settings(settings) == [
        "DISP:PAGE MEAS\n",
        "FUNC Ls-Q\n",
        "FUNC:RANG:AUTO AUTO\n",
        "FUNC:MON1 Z\n",
        "FUNC:MON2 IAC\n",
        "FREQ 1000\n",
        "LEV:VOLT 0.1\n",
        "APER FAST\n",
    ]


def test_parse_fetch_impedance_keeps_raw_and_numeric_values() -> None:
    reading = lcr6000.parse_fetch_impedance(
        "+2.61788e-11,+5.45442e-01,+3.88651e+05,+0.00000e+00,BIN1,AUX-OK,OK"
    )

    assert reading.primary == pytest.approx(2.61788e-11)
    assert reading.secondary == pytest.approx(0.545442)
    assert reading.monitor1 == pytest.approx(388651.0)
    assert reading.monitor2 == pytest.approx(0.0)
    assert reading.comparator == "BIN1,AUX-OK,OK"
    assert reading.raw.startswith("+2.61788e-11")


def test_ac_logger_formats_lcr_columns_with_plan_index() -> None:
    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window._lcr_plan_index = 1
    setting = lcr6000.Lcr6000Settings(
        frequency_hz=1000.0,
        level_value=0.1,
        level_mode="voltage",
        function="Ls-Q",
        monitor1="Z",
        monitor2="IAC",
        aperture="FAST",
    )
    reading = lcr6000.Lcr6000Reading(
        timestamp_utc="2026-05-04T12:00:00.000+00:00",
        raw="+1.0,+2.0,+3.0,+4.0,BIN1,AUX-OK,OK",
        primary=1.0,
        secondary=2.0,
        monitor1=3.0,
        monitor2=4.0,
        comparator="BIN1,AUX-OK,OK",
    )

    assert window._format_lcr_columns(setting, reading) == [
        "2",
        "1000",
        "voltage",
        "0.1",
        "Ls-Q",
        "1",
        "2",
        "3",
        "4",
        "BIN1,AUX-OK,OK",
        "+1.0,+2.0,+3.0,+4.0,BIN1,AUX-OK,OK",
    ]


def test_ac_logger_ensure_header_replaces_current_annealing_header(tmp_path: Path) -> None:
    path = tmp_path / "existing-log.txt"
    path.write_text("# Current (mA)\tVoltage (V)\tResistance (Ohm)\n1\t2\t3\n", encoding="utf-8")
    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)

    window._ensure_log_header(str(path))

    assert path.read_text(encoding="utf-8").splitlines() == [
        ac_logger.HEADER_LINE,
        "1\t2\t3",
    ]


def test_ac_logger_configure_reports_meter_failure() -> None:
    class FakeStatus:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, text: str) -> None:  # noqa: N802 - Qt-style test double
            self.text = text

    class FailingMeter:
        is_open = True

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            raise RuntimeError("meter did not accept setting")

    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window._lcr_plan = [
        lcr6000.Lcr6000Settings(
            frequency_hz=1000.0,
            level_value=0.1,
            level_mode="voltage",
            function="Ls-Q",
            monitor1="Z",
            monitor2="IAC",
            aperture="FAST",
        )
    ]
    window._lcr_plan_index = 0
    window._lcr_last_error = ""
    window.lcr_meter = FailingMeter()
    window.label_lcr_status = FakeStatus()

    assert window._configure_lcr_for_current_index() is False
    assert window._lcr_last_error == "meter did not accept setting"
    assert window.label_lcr_status.text == "LCR configure failed: meter did not accept setting"


def test_ac_logger_formats_baseline_row_without_current_columns() -> None:
    setting = lcr6000.Lcr6000Settings(
        frequency_hz=1000.0,
        level_value=0.1,
        level_mode="voltage",
        function="Ls-Q",
        monitor1="Z",
        monitor2="IAC",
        aperture="FAST",
    )
    reading = lcr6000.Lcr6000Reading(
        timestamp_utc="2026-05-04T12:00:00.000+00:00",
        raw="+1.0,+2.0,+3.0,+4.0,BIN1,AUX-OK,OK",
        primary=1.0,
        secondary=2.0,
        monitor1=3.0,
        monitor2=4.0,
        comparator="BIN1,AUX-OK,OK",
    )

    assert ac_logger.MainWindow._format_baseline_row(
        setting_index=2,
        repeat_index=3,
        setting=setting,
        reading=reading,
    ) == [
        "2026-05-04T12:00:00.000+00:00",
        "2",
        "3",
        "1000",
        "voltage",
        "0.1",
        "Ls-Q",
        "1",
        "2",
        "3",
        "4",
        "BIN1,AUX-OK,OK",
        "+1.0,+2.0,+3.0,+4.0,BIN1,AUX-OK,OK",
    ]


def test_ac_logger_collects_baseline_rows_without_power_supply() -> None:
    class FakeMeter:
        is_open = True

        def __init__(self) -> None:
            self.configured: list[lcr6000.Lcr6000Settings] = []
            self.fetch_count = 0

        def configure(self, setting: lcr6000.Lcr6000Settings) -> None:
            self.configured.append(setting)

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            self.fetch_count += 1
            return lcr6000.Lcr6000Reading(
                timestamp_utc=f"2026-05-04T12:00:0{self.fetch_count}.000+00:00",
                raw=f"+{self.fetch_count}.0,+0.0,+0.0,+0.0,OK",
                primary=float(self.fetch_count),
                secondary=0.0,
                monitor1=0.0,
                monitor2=0.0,
                comparator="OK",
            )

    plan = [
        lcr6000.Lcr6000Settings(frequency_hz=100.0, level_value=0.1),
        lcr6000.Lcr6000Settings(frequency_hz=1000.0, level_value=0.1),
    ]
    meter = FakeMeter()
    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window.lcr_meter = meter
    window._lcr_last_error = ""
    window._ac_plot_points = []
    window._refresh_ac_plots = lambda: None  # type: ignore[method-assign]

    rows = window._collect_baseline_rows(plan, repeats=2)

    assert meter.configured == plan
    assert meter.fetch_count == 4
    assert [row[1:3] for row in rows] == [["1", "1"], ["1", "2"], ["2", "1"], ["2", "2"]]
    assert len(window._ac_plot_points) == 4
    assert {point.current_mA for point in window._ac_plot_points} == {0.0}
    assert window._ac_plot_points[-1].frequency_hz == 1000.0


def test_ac_logger_baseline_collection_does_not_redraw_plots_inline() -> None:
    class FakeMeter:
        is_open = True

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            pass

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            return lcr6000.Lcr6000Reading(
                timestamp_utc="2026-05-15T00:00:00.000+00:00",
                primary=1e-5,
                secondary=14.0,
                monitor1=0.0,
                monitor2=0.0,
                comparator="OK",
                raw="+1.0E-05,+1.4E+01,+0,+0,OK",
            )

    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window.lcr_meter = FakeMeter()
    window._ac_sweep_stop_requested = False
    window._lcr_last_error = ""
    window._ac_plot_points = []
    redraws = {"count": 0}
    window._refresh_ac_plots = lambda: redraws.__setitem__("count", redraws["count"] + 1)  # type: ignore[method-assign]
    window._set_ac_current_task = lambda _text: None  # type: ignore[method-assign]
    window._advance_ac_progress = lambda _label: None  # type: ignore[method-assign]

    rows = window._collect_baseline_rows(
        [lcr6000.Lcr6000Settings(frequency_hz=100.0, level_value=0.1)],
        repeats=5,
    )

    assert len(rows) == 5
    assert len(window._ac_plot_points) == 5
    assert redraws["count"] == 0


def test_ac_baseline_worker_writes_rows_and_emits_plot_points(tmp_path: Path) -> None:
    class FakeMeter:
        is_open = True

        def __init__(self) -> None:
            self.fetch_count = 0

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            return None

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            self.fetch_count += 1
            return lcr6000.Lcr6000Reading(
                timestamp_utc=f"2026-05-15T00:00:0{self.fetch_count}.000+00:00",
                primary=float(self.fetch_count) * 1e-6,
                secondary=14.0,
                monitor1=0.0,
                monitor2=0.0,
                comparator="OK",
                raw=f"+{self.fetch_count}.0E-06,+1.4E+01,+0,+0,OK",
            )

    worker = ac_logger.AcBaselineWorker(
        meter=FakeMeter(),
        plan=[lcr6000.Lcr6000Settings(frequency_hz=1000.0, level_value=0.1)],
        output_path=tmp_path / "baseline.tsv",
        point_duration_s=0.0,
        settle_s=0.0,
        total_planned_s=1.0,
    )
    points: list[ac_logger.AcPlotPoint] = []
    finished: list[tuple[str, bool]] = []
    worker.plot_point_ready.connect(points.append)
    worker.finished.connect(lambda path, stopped: finished.append((path, stopped)))

    worker.run()

    assert finished == [(str(tmp_path / "baseline.tsv"), False)]
    assert len(points) == 1
    assert points[0].frequency_hz == 1000.0
    lines = (tmp_path / "baseline.tsv").read_text(encoding="utf-8").splitlines()
    assert lines[-1].split("\t")[1:4] == ["1", "1", "1000"]


def test_ac_logger_collects_baseline_rows_with_incremental_callback() -> None:
    class FakeMeter:
        is_open = True

        def __init__(self) -> None:
            self.fetch_count = 0

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            return None

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            self.fetch_count += 1
            return lcr6000.Lcr6000Reading(
                timestamp_utc=f"2026-05-04T12:00:0{self.fetch_count}.000+00:00",
                raw=f"+{self.fetch_count}.0,+0.0,+0.0,+0.0,OK",
                primary=float(self.fetch_count),
                secondary=0.0,
                monitor1=0.0,
                monitor2=0.0,
                comparator="OK",
            )

    written: list[list[str]] = []
    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window.lcr_meter = FakeMeter()
    window._lcr_last_error = ""
    window._ac_plot_points = []
    window._refresh_ac_plots = lambda: None  # type: ignore[method-assign]

    rows = window._collect_baseline_rows(
        [lcr6000.Lcr6000Settings(frequency_hz=100.0, level_value=0.1)],
        repeats=3,
        row_callback=written.append,
    )

    assert rows == written
    assert [row[2] for row in written] == ["1", "2", "3"]


def test_ac_logger_baseline_collection_honors_stop_request() -> None:
    class FakeMeter:
        is_open = True

        def __init__(self) -> None:
            self.fetch_count = 0

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            return None

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            self.fetch_count += 1
            return lcr6000.Lcr6000Reading(
                timestamp_utc=f"2026-05-04T12:00:{self.fetch_count:02d}.000+00:00",
                raw=f"+{self.fetch_count}.0,+0.0,+0.0,+0.0,OK",
                primary=float(self.fetch_count),
                secondary=0.0,
                monitor1=0.0,
                monitor2=0.0,
                comparator="OK",
            )

    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window.lcr_meter = FakeMeter()
    window._lcr_last_error = ""
    window._ac_sweep_stop_requested = False
    window._ac_plot_points = []
    window._refresh_ac_plots = lambda: None  # type: ignore[method-assign]
    window._advance_ac_progress = lambda _label: setattr(window, "_ac_sweep_stop_requested", True)  # type: ignore[method-assign]
    window._set_ac_current_task = lambda _text: None  # type: ignore[method-assign]

    rows = window._collect_baseline_rows(
        [lcr6000.Lcr6000Settings(frequency_hz=100.0, level_value=0.1)],
        repeats=10,
    )

    assert len(rows) == 1
    assert window._ac_sweep_stop_requested is True


def test_ac_logger_settle_sleep_processes_stop_request(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window._ac_sweep_stop_requested = False
    calls = {"events": 0, "sleeps": 0}

    def _process_events(*_args: object) -> None:
        calls["events"] += 1
        window._ac_sweep_stop_requested = True

    def _sleep(_seconds: float) -> None:
        calls["sleeps"] += 1

    monkeypatch.setattr(ac_logger.QtWidgets.QApplication, "processEvents", _process_events)
    monkeypatch.setattr(ac_logger.time, "sleep", _sleep)

    assert window._sleep_with_stop_processing(2.0, quantum_s=0.25) is False
    assert calls["events"] == 1
    assert calls["sleeps"] == 0
    app.processEvents()


def test_ac_logger_baseline_retries_empty_lcr_response() -> None:
    class SlowMeter:
        is_open = True

        def __init__(self) -> None:
            self.fetch_count = 0

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            return None

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            self.fetch_count += 1
            if self.fetch_count == 1:
                return lcr6000.Lcr6000Reading(
                    timestamp_utc="2026-05-04T12:00:00.000+00:00",
                    raw="",
                    primary=None,
                    secondary=None,
                    monitor1=None,
                    monitor2=None,
                    comparator="",
                )
            return lcr6000.Lcr6000Reading(
                timestamp_utc="2026-05-04T12:00:01.000+00:00",
                raw="+9.0,+0.0,+0.0,+0.0,OK",
                primary=9.0,
                secondary=0.0,
                monitor1=0.0,
                monitor2=0.0,
                comparator="OK",
            )

    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window.lcr_meter = SlowMeter()
    window._lcr_last_error = ""

    rows = window._collect_baseline_rows(
        [lcr6000.Lcr6000Settings(frequency_hz=1000.0, level_value=0.1)],
        repeats=1,
    )

    assert window.lcr_meter.fetch_count == 2
    assert rows[0][-1] == "+9.0,+0.0,+0.0,+0.0,OK"


def test_ac_logger_writes_baseline_file_with_lcr_only_header(tmp_path: Path) -> None:
    path = tmp_path / "empty_fixture_baseline.tsv"
    setting = lcr6000.Lcr6000Settings(frequency_hz=1000.0, level_value=0.1)
    reading = lcr6000.Lcr6000Reading(
        timestamp_utc="2026-05-04T12:00:00.000+00:00",
        raw="+1.0,+2.0,+3.0,+4.0,OK",
        primary=1.0,
        secondary=2.0,
        monitor1=3.0,
        monitor2=4.0,
        comparator="OK",
    )
    row = ac_logger.MainWindow._format_baseline_row(
        setting_index=1,
        repeat_index=1,
        setting=setting,
        reading=reading,
    )

    ac_logger.MainWindow._write_baseline_file(path, [setting], [row])

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# AC susceptibility baseline generated from LCR-6200 settings"
    assert lines[2] == ac_logger.BASELINE_HEADER_LINE
    assert "Current (mA)" not in lines[2]
    assert lines[3].split("\t") == row
