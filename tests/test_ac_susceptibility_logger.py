from __future__ import annotations

import json
from datetime import datetime
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


def test_build_current_loop_points_can_include_zero_reference_before_sweep() -> None:
    points = sweep.build_current_loop_points(
        start_mA=20.0,
        stop_mA=80.0,
        step_mA=20.0,
        direction_mode="up-down",
        include_zero=True,
    )

    assert [(round(point.current_a * 1000.0), point.direction) for point in points] == [
        (0, "zero"),
        (20, "up"),
        (40, "up"),
        (60, "up"),
        (80, "up"),
        (60, "down"),
        (40, "down"),
        (20, "down"),
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


def test_power_supply_serial_resource_is_normalized_for_windows_paths() -> None:
    assert sweep.normalize_serial_resource("COM6 - Prolific USB serial") == "COM6"
    assert sweep.normalize_serial_resource(r"\\.\COM6") == "COM6"
    assert sweep.normalize_serial_resource(r"\\\\COM6") == "COM6"

    psu = sweep.SerialScpiCurrentSource(
        backend_id="owon_spe6102",
        resource=r"\\\\COM6",
        baudrate=115200,
        voltage_limit_v=62.0,
    )

    assert psu.resource == "COM6"


def test_owon_voltage_limit_is_clamped_to_bench_scpi_maximum() -> None:
    assert sweep.effective_power_supply_voltage_limit("owon_spe6102", 62.0) == pytest.approx(61.0)
    assert sweep.effective_power_supply_voltage_limit("owon_spe6102", 48.0) == pytest.approx(48.0)
    assert sweep.effective_power_supply_voltage_limit("hmp4030", 62.0) == pytest.approx(62.0)


def test_serial_scpi_current_source_requires_supported_id_before_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSerialPort:
        instances: list["FakeSerialPort"] = []

        def __init__(self, port: str, baudrate: int, timeout: float, write_timeout: float) -> None:
            self.port = port
            self.baudrate = baudrate
            self.is_open = True
            self.written: list[bytes] = []
            self.closed = False
            FakeSerialPort.instances.append(self)

        def reset_input_buffer(self) -> None:
            return None

        def write(self, data: bytes) -> None:
            self.written.append(data)

        def flush(self) -> None:
            return None

        def readline(self) -> bytes:
            return b""

        def close(self) -> None:
            self.closed = True
            self.is_open = False

    fake_serial_module = type("SerialModule", (), {"Serial": FakeSerialPort})
    monkeypatch.setattr(sweep, "serial", fake_serial_module)

    psu = sweep.SerialScpiCurrentSource(
        backend_id="owon_spe6102",
        resource="COM6",
        baudrate=115200,
        voltage_limit_v=62.0,
    )

    with pytest.raises(RuntimeError, match="not a supported HMP/OWON power supply"):
        psu.connect()

    assert FakeSerialPort.instances
    assert FakeSerialPort.instances[0].written == [b"*IDN?\n"]
    assert FakeSerialPort.instances[0].closed


def test_serial_scpi_current_source_accepts_matching_supported_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSerialPort:
        def __init__(self, port: str, baudrate: int, timeout: float, write_timeout: float) -> None:
            self.port = port
            self.baudrate = baudrate
            self.is_open = True
            self.written: list[bytes] = []
            self.rts = False
            self.dtr = False

        def reset_input_buffer(self) -> None:
            return None

        def write(self, data: bytes) -> None:
            self.written.append(data)

        def flush(self) -> None:
            return None

        def readline(self) -> bytes:
            if self.written and self.written[-1] == b"*IDN?\n":
                return b"OWON,SPE6102,123456,V1.0\n"
            return b""

        def close(self) -> None:
            self.is_open = False

    fake_serial_module = type("SerialModule", (), {"Serial": FakeSerialPort})
    monkeypatch.setattr(sweep, "serial", fake_serial_module)

    psu = sweep.SerialScpiCurrentSource(
        backend_id="owon_spe6102",
        resource="COM7",
        baudrate=115200,
        voltage_limit_v=62.0,
    )

    psu.connect()

    assert psu._serial is not None
    assert psu._serial.written == [b"*IDN?\n"]


def test_serial_scpi_current_source_clamps_owon_voltage_before_output(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSerialPort:
        def __init__(self, port: str, baudrate: int, timeout: float, write_timeout: float) -> None:
            self.port = port
            self.baudrate = baudrate
            self.is_open = True
            self.written: list[bytes] = []
            self.rts = False
            self.dtr = False

        def reset_input_buffer(self) -> None:
            return None

        def write(self, data: bytes) -> None:
            self.written.append(data)

        def flush(self) -> None:
            return None

        def readline(self) -> bytes:
            if self.written and self.written[-1] == b"*IDN?\n":
                return b"OWON,SPE6102,123456,V1.0\n"
            return b""

        def close(self) -> None:
            self.is_open = False

    fake_serial_module = type("SerialModule", (), {"Serial": FakeSerialPort})
    monkeypatch.setattr(sweep, "serial", fake_serial_module)

    psu = sweep.SerialScpiCurrentSource(
        backend_id="owon_spe6102",
        resource="COM7",
        baudrate=115200,
        voltage_limit_v=62.0,
    )

    psu.connect()
    psu.initialize(voltage_limit_v=62.0)

    assert b"VOLT 61.000\n" in psu._serial.written
    assert b"VOLT 62.000\n" not in psu._serial.written


def test_serial_scpi_current_source_shutdown_zeroes_current_and_voltage(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSerialPort:
        def __init__(self, port: str, baudrate: int, timeout: float, write_timeout: float) -> None:
            self.port = port
            self.baudrate = baudrate
            self.is_open = True
            self.written: list[bytes] = []

        def reset_input_buffer(self) -> None:
            return None

        def write(self, data: bytes) -> None:
            self.written.append(data)

        def flush(self) -> None:
            return None

        def readline(self) -> bytes:
            if self.written and self.written[-1] == b"*IDN?\n":
                return b"OWON,SPE6102,123456,V1.0\n"
            return b""

        def close(self) -> None:
            self.is_open = False

    fake_serial_module = type("SerialModule", (), {"Serial": FakeSerialPort})
    monkeypatch.setattr(sweep, "serial", fake_serial_module)

    psu = sweep.SerialScpiCurrentSource(
        backend_id="owon_spe6102",
        resource="COM7",
        baudrate=115200,
        voltage_limit_v=62.0,
    )

    psu.connect()
    psu.output_off()

    assert psu._serial is not None
    assert psu._serial.written[-3:] == [b"CURR 0.0000\n", b"VOLT 0.000\n", b"OUTP OFF\n"]


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
    assert "psu_backend=owon_spe6102" in lines[2]
    assert any(line.startswith("# config_json=") for line in lines)
    assert lines[-2] == sweep.SWEEP_HEADER_LINE
    assert "PSU resistance (Ohm)" in lines[-2]
    assert "PSU power (W)" in lines[-2]
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
        "60.6060606061",
        "0.02376",
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
        "measure",
        "off",
        "close",
    ]


def test_sweep_metadata_snapshot_includes_full_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings_snapshot.tsv"
    config = sweep.AcSweepConfig(
        lcr_settings=[
            lcr6000.Lcr6000Settings(1000.0, 0.3, function="Ls-Rs"),
            lcr6000.Lcr6000Settings(2000.0, 0.5, function="Lp-Rp"),
        ],
        current_points=[
            sweep.CurrentLoopPoint(0.0, "zero"),
            sweep.CurrentLoopPoint(0.02, "up"),
            sweep.CurrentLoopPoint(0.04, "up"),
        ],
        point_duration_s=10.0,
        repeats=1,
        dwell_s=1.0,
        psu_backend="owon_spe6102",
        psu_resource="COM11",
        voltage_limit_v=61.0,
    )

    writer = sweep.AcSweepTsvWriter(path, config)
    writer.write_metadata()
    writer.close()

    line = next(line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("# config_json="))
    snapshot = json.loads(line.removeprefix("# config_json="))
    assert snapshot["psu"]["backend"] == "owon_spe6102"
    assert snapshot["psu"]["resource"] == "COM11"
    assert snapshot["acquisition"]["point_duration_s"] == 10.0
    assert snapshot["current_loop"]["points_mA"] == [0.0, 20.0, 40.0]
    assert snapshot["lcr_settings"][1]["function"] == "Lp-Rp"


def test_run_ac_sweep_aborts_when_psu_actual_current_is_missing(tmp_path: Path) -> None:
    class FakeLcr:
        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            return None

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            return lcr6000.Lcr6000Reading(
                timestamp_utc="2026-05-18T00:00:00Z",
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

        def __init__(self) -> None:
            self.events: list[str] = []

        def connect(self) -> None:
            self.events.append("connect")

        def initialize(self, *, voltage_limit_v: float) -> None:
            self.events.append(f"initialize:{voltage_limit_v:g}")

        def set_current(self, current_a: float) -> None:
            self.events.append(f"current:{current_a:g}")

        def measure(self) -> sweep.PowerSupplyMeasurement:
            self.events.append("measure")
            return sweep.PowerSupplyMeasurement(status="OK")

        def output_off(self) -> None:
            self.events.append("off")

        def close(self) -> None:
            self.events.append("close")

    output = tmp_path / "missing-psu-readback.tsv"
    psu = FakePsu()
    config = sweep.AcSweepConfig(
        lcr_settings=[lcr6000.Lcr6000Settings(1000.0, 0.1, function="Ls-Rs")],
        current_points=[sweep.CurrentLoopPoint(0.02, "up")],
        point_duration_s=0.0,
        repeats=1,
        dwell_s=0.0,
        psu_backend="owon_spe6102",
        psu_resource="COM7",
        voltage_limit_v=62.0,
        psu_current_ready_timeout_s=0.05,
        psu_current_ready_poll_s=0.01,
        psu_measure_attempts=1,
    )

    with pytest.raises(sweep.PsuOutputVerificationError, match="actual-current readback"):
        sweep.run_ac_sweep(
            config=config,
            lcr=FakeLcr(),
            psu=psu,
            output_path=output,
            sleep=lambda _seconds: None,
        )

    text = output.read_text(encoding="utf-8")
    assert "PSU did not reach requested current" in text
    assert "\tFAIL\t" in text
    assert psu.events[0:3] == ["connect", "initialize:62", "current:0.02"]
    assert psu.events[-2:] == ["off", "close"]
    assert "measure" in psu.events


def test_run_ac_sweep_aborts_when_psu_actual_current_is_too_low(tmp_path: Path) -> None:
    class FakeLcr:
        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            return None

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            return lcr6000.Lcr6000Reading(
                timestamp_utc="2026-05-18T00:00:00Z",
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

        def set_current(self, current_a: float) -> None:
            return None

        def measure(self) -> sweep.PowerSupplyMeasurement:
            return sweep.PowerSupplyMeasurement(current_actual_a=0.0, voltage_actual_v=0.0, status="OK")

        def output_off(self) -> None:
            return None

        def close(self) -> None:
            return None

    config = sweep.AcSweepConfig(
        lcr_settings=[lcr6000.Lcr6000Settings(1000.0, 0.1, function="Ls-Rs")],
        current_points=[sweep.CurrentLoopPoint(0.02, "up")],
        point_duration_s=0.0,
        repeats=1,
        dwell_s=0.0,
        psu_backend="owon_spe6102",
        psu_resource="COM7",
        voltage_limit_v=62.0,
        psu_current_ready_timeout_s=0.05,
        psu_current_ready_poll_s=0.01,
        psu_measure_attempts=1,
    )

    with pytest.raises(sweep.PsuOutputVerificationError, match="did not reach requested current"):
        sweep.run_ac_sweep(
            config=config,
            lcr=FakeLcr(),
            psu=FakePsu(),
            output_path=tmp_path / "low-current.tsv",
            sleep=lambda _seconds: None,
        )


def test_run_ac_sweep_warns_for_missing_zero_current_readback(tmp_path: Path) -> None:
    class FakeLcr:
        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            return None

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            return lcr6000.Lcr6000Reading(
                timestamp_utc="2026-05-18T00:00:00Z",
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

        def __init__(self) -> None:
            self.events: list[str] = []

        def connect(self) -> None:
            self.events.append("connect")

        def initialize(self, *, voltage_limit_v: float) -> None:
            self.events.append(f"initialize:{voltage_limit_v:g}")

        def set_current(self, current_a: float) -> None:
            self.events.append(f"current:{current_a:g}")

        def measure(self) -> sweep.PowerSupplyMeasurement:
            self.events.append("measure")
            return sweep.PowerSupplyMeasurement(status="FAIL", error="missing actual-current readback")

        def output_off(self) -> None:
            self.events.append("off")

        def close(self) -> None:
            self.events.append("close")

    output = tmp_path / "zero-current-missing-readback.tsv"
    psu = FakePsu()
    config = sweep.AcSweepConfig(
        lcr_settings=[lcr6000.Lcr6000Settings(1000.0, 0.1, function="Ls-Rs")],
        current_points=[sweep.CurrentLoopPoint(0.0, "zero")],
        point_duration_s=0.0,
        repeats=1,
        dwell_s=0.0,
        psu_backend="owon_spe6102",
        psu_resource="COM7",
        voltage_limit_v=62.0,
        psu_measure_attempts=1,
    )

    sweep.run_ac_sweep(
        config=config,
        lcr=FakeLcr(),
        psu=psu,
        output_path=output,
        sleep=lambda _seconds: None,
    )

    text = output.read_text(encoding="utf-8")
    assert "\tWARN\t" in text
    assert "\tFAIL\t" not in text
    assert "missing actual-current readback at zero-current reference; continuing" in text
    assert psu.events[-2:] == ["off", "close"]


def test_run_ac_sweep_tolerates_transient_missing_psu_readback_after_current_ready(tmp_path: Path) -> None:
    class FakeLcr:
        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            return None

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            return lcr6000.Lcr6000Reading(
                timestamp_utc="2026-05-18T00:00:00Z",
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

        def __init__(self) -> None:
            self.measurements = [
                sweep.PowerSupplyMeasurement(current_actual_a=0.02, voltage_actual_v=1.0, status="OK"),
                sweep.PowerSupplyMeasurement(status="FAIL", error="missing actual-current readback"),
                sweep.PowerSupplyMeasurement(current_actual_a=0.02, voltage_actual_v=1.0, status="OK"),
            ]

        def connect(self) -> None:
            return None

        def initialize(self, *, voltage_limit_v: float) -> None:
            return None

        def set_current(self, current_a: float) -> None:
            return None

        def measure(self) -> sweep.PowerSupplyMeasurement:
            return self.measurements.pop(0)

        def output_off(self) -> None:
            return None

        def close(self) -> None:
            return None

    output = tmp_path / "transient-missing-readback.tsv"
    config = sweep.AcSweepConfig(
        lcr_settings=[lcr6000.Lcr6000Settings(1000.0, 0.1, function="Ls-Rs")],
        current_points=[sweep.CurrentLoopPoint(0.02, "up")],
        point_duration_s=0.0,
        repeats=2,
        dwell_s=0.0,
        psu_backend="owon_spe6102",
        psu_resource="COM7",
        voltage_limit_v=62.0,
        psu_measure_attempts=1,
    )

    sweep.run_ac_sweep(
        config=config,
        lcr=FakeLcr(),
        psu=FakePsu(),
        output_path=output,
        sleep=lambda _seconds: None,
    )

    text = output.read_text(encoding="utf-8")
    assert "\tWARN\t" in text
    assert "\tFAIL\t" not in text
    assert "missing actual-current readback during active point; continuing" in text
    assert text.count("\n") >= 4


def test_psu_output_verification_allows_zero_reference_and_catches_wire_break() -> None:
    sweep._validate_psu_output(
        sweep.CurrentLoopPoint(0.0, "zero"),
        sweep.PowerSupplyMeasurement(status="FAIL", error="missing actual-current readback"),
    )

    with pytest.raises(sweep.PsuOutputVerificationError, match="far below requested"):
        sweep._validate_psu_output(
            sweep.CurrentLoopPoint(0.02, "up"),
            sweep.PowerSupplyMeasurement(current_actual_a=0.0, voltage_actual_v=0.0, status="OK"),
        )

    with pytest.raises(sweep.PsuOutputVerificationError, match="far below requested"):
        sweep._validate_psu_output(
            sweep.CurrentLoopPoint(0.08, "up"),
            sweep.PowerSupplyMeasurement(current_actual_a=0.015, voltage_actual_v=1.0, status="OK"),
        )


def test_run_ac_sweep_waits_for_psu_current_before_lcr_reads(tmp_path: Path) -> None:
    class FakeLcr:
        def __init__(self) -> None:
            self.fetch_count = 0

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            return None

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            self.fetch_count += 1
            return lcr6000.Lcr6000Reading(
                timestamp_utc="2026-05-18T00:00:00Z",
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

        def __init__(self) -> None:
            self.measurements = [
                sweep.PowerSupplyMeasurement(current_actual_a=0.0, voltage_actual_v=0.0, status="OK"),
                sweep.PowerSupplyMeasurement(current_actual_a=0.02, voltage_actual_v=1.0, status="OK"),
                sweep.PowerSupplyMeasurement(current_actual_a=0.02, voltage_actual_v=1.0, status="OK"),
            ]

        def connect(self) -> None:
            return None

        def initialize(self, *, voltage_limit_v: float) -> None:
            return None

        def set_current(self, current_a: float) -> None:
            return None

        def measure(self) -> sweep.PowerSupplyMeasurement:
            return self.measurements.pop(0)

        def output_off(self) -> None:
            return None

        def close(self) -> None:
            return None

    sleeps: list[float] = []
    lcr = FakeLcr()
    config = sweep.AcSweepConfig(
        lcr_settings=[lcr6000.Lcr6000Settings(1000.0, 0.1, function="Ls-Rs")],
        current_points=[sweep.CurrentLoopPoint(0.02, "up")],
        point_duration_s=0.0,
        repeats=1,
        dwell_s=0.0,
        psu_backend="owon_spe6102",
        psu_resource="COM7",
        voltage_limit_v=62.0,
        psu_current_ready_timeout_s=1.0,
        psu_current_ready_poll_s=0.01,
        psu_measure_attempts=1,
    )

    sweep.run_ac_sweep(
        config=config,
        lcr=lcr,
        psu=FakePsu(),
        output_path=tmp_path / "waits-for-current.tsv",
        sleep=sleeps.append,
    )

    assert lcr.fetch_count == 1
    assert sleeps == [0.02]


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
            return sweep.PowerSupplyMeasurement(current_actual_a=0.02, voltage_actual_v=1.0, status="OK")

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


def test_run_ac_sweep_reconfigures_and_retries_slow_lcr_cadence(
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
            self.configure_count = 0
            self.fetch_count = 0

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            self.configure_count += 1

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            self.fetch_count += 1
            self.clock.advance(0.2 if self.configure_count == 1 else 0.025)
            return lcr6000.Lcr6000Reading(
                timestamp_utc=f"2026-05-15T10:00:{self.fetch_count:02d}.000+00:00",
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
            return sweep.PowerSupplyMeasurement(current_actual_a=0.02, voltage_actual_v=1.0, status="OK")

        def output_off(self) -> None:
            return None

        def close(self) -> None:
            return None

    clock = FakeClock()
    monkeypatch.setattr(sweep.time, "monotonic", clock.monotonic)
    lcr = FakeLcr(clock)
    progress_rows: list[sweep.AcSweepRow] = []
    config = sweep.AcSweepConfig(
        lcr_settings=[lcr6000.Lcr6000Settings(200000.0, 0.3, function="Ls-Rs", aperture="FAST")],
        current_points=[sweep.CurrentLoopPoint(0.02, "up")],
        point_duration_s=0.7,
        dwell_s=0.0,
        psu_backend="owon_spe6102",
        psu_resource="COM7",
        voltage_limit_v=62.0,
        lcr_slow_retry_check_s=0.6,
        lcr_slow_retry_discard_s=0.0,
        lcr_slow_retry_max_attempts=1,
    )

    output_path = tmp_path / "slow-retry.tsv"
    sweep.run_ac_sweep(
        config=config,
        lcr=lcr,
        psu=FakePsu(),
        output_path=output_path,
        progress=progress_rows.append,
    )

    assert lcr.configure_count == 2
    assert any("slow LCR cadence attempt 1" in row.error for row in progress_rows)
    assert any(row.error == "retry_attempt=2" for row in progress_rows)
    assert "slow LCR cadence attempt 1" in output_path.read_text(encoding="utf-8")


def test_run_ac_sweep_continues_full_point_after_slow_lcr_retries_are_exhausted(
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
            self.configure_count = 0
            self.fetch_count = 0

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            self.configure_count += 1

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            self.fetch_count += 1
            self.clock.advance(0.2)
            return lcr6000.Lcr6000Reading(
                timestamp_utc=f"2026-05-15T10:01:{self.fetch_count:02d}.000+00:00",
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
            return sweep.PowerSupplyMeasurement(current_actual_a=0.02, voltage_actual_v=1.0, status="OK")

        def output_off(self) -> None:
            return None

        def close(self) -> None:
            return None

    clock = FakeClock()
    monkeypatch.setattr(sweep.time, "monotonic", clock.monotonic)
    progress_rows: list[sweep.AcSweepRow] = []
    config = sweep.AcSweepConfig(
        lcr_settings=[lcr6000.Lcr6000Settings(100000.0, 2.0, function="Ls-Rs", aperture="FAST")],
        current_points=[sweep.CurrentLoopPoint(0.02, "up")],
        point_duration_s=1.0,
        dwell_s=0.0,
        psu_backend="owon_spe6102",
        psu_resource="COM7",
        voltage_limit_v=62.0,
        lcr_slow_retry_check_s=0.6,
        lcr_slow_retry_discard_s=0.0,
        lcr_slow_retry_max_attempts=1,
    )

    sweep.run_ac_sweep(
        config=config,
        lcr=FakeLcr(clock),
        psu=FakePsu(),
        output_path=tmp_path / "slow-persist.tsv",
        progress=progress_rows.append,
    )

    assert any("slow LCR cadence persisted" in row.error for row in progress_rows)
    assert any(row.error == "retry_attempt=3" for row in progress_rows)
    assert clock.now >= 2.2


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


def test_ac_logger_uses_ac_specific_owon_supply_defaults_for_sweep_config() -> None:
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
    window._ac_psu_backend = "owon_spe6102"
    window._ac_psu_resource = "COM7"
    window._ac_psu_baudrate = 9600
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
    assert config.voltage_limit_v == pytest.approx(61.0)
    app.processEvents()


def test_ac_logger_psu_settings_are_separate_from_current_annealing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    original = QtCore.QSettings
    ini_format = original.Format.IniFormat
    user_scope = original.Scope.UserScope
    stores: dict[str, QtCore.QSettings] = {}

    def factory(_organization: str, application: str) -> QtCore.QSettings:
        settings = stores.get(application)
        if settings is None:
            settings = original(ini_format, user_scope, "microwire_tests", f"separate_{application}")
            settings.clear()
            stores[application] = settings
        return settings

    monkeypatch.setattr(ac_logger.QtCore, "QSettings", factory)
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    factory("microwire", "current_annealing").setValue("supply_profile", "hmp4030")
    ac_store = factory("microwire", "ac_susceptibility_logger")
    ac_store.setValue("psu_backend", "owon_spe6102")
    ac_store.setValue("psu_port", "COM6")
    ac_store.setValue("psu_baud", "115200")

    window = ac_logger.MainWindow()
    try:
        assert window._selected_ac_psu_backend() == "owon_spe6102"
        assert window._selected_ac_psu_resource().endswith("COM6")
        assert "AC current supply" in window.label_ac_psu_status.text()
        window._set_combo_data(window.ui.comboBox_supply, "hmp4030")
        window._handle_ac_psu_controls_changed()
        assert ac_store.value("psu_backend", type=str) == "hmp4030"
        assert factory("microwire", "current_annealing").value("supply_profile", type=str) == "hmp4030"
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_remembers_psu_hardware_settings_per_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "psu_profile_memory")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        port_combo = window.ui.comboBox_port
        port_combo.clear()
        port_combo.addItem("COM11 - OWON", "COM11")
        port_combo.addItem("COM3 - HMP", "COM3")
        window.ui.comboBox_baudrate.clear()
        window.ui.comboBox_baudrate.addItems(["9600", "115200"])

        window._set_combo_data(window.ui.comboBox_supply, "owon_spe6102")
        window._set_combo_data(port_combo, "COM11")
        window._set_combo_text(window.ui.comboBox_baudrate, "115200")
        window.spinBox_ac_voltage_limit.setValue(61.0)
        window._handle_ac_psu_controls_changed()

        window._set_combo_data(window.ui.comboBox_supply, "hmp4030")
        window._handle_ac_psu_controls_changed()
        window._set_combo_data(port_combo, "COM3")
        window._set_combo_text(window.ui.comboBox_baudrate, "9600")
        window.spinBox_ac_voltage_limit.setValue(30.0)
        window._handle_ac_psu_controls_changed()

        window._set_combo_data(window.ui.comboBox_supply, "owon_spe6102")
        window._handle_ac_psu_controls_changed()
        assert window._selected_ac_psu_backend() == "owon_spe6102"
        assert window._selected_ac_psu_resource() == "COM11"
        assert window._selected_ac_psu_baudrate() == 115200
        assert window.spinBox_ac_voltage_limit.value() == pytest.approx(61.0)

        window._set_combo_data(window.ui.comboBox_supply, "hmp4030")
        window._handle_ac_psu_controls_changed()
        assert window._selected_ac_psu_backend() == "hmp4030"
        assert window._selected_ac_psu_resource() == "COM3"
        assert window._selected_ac_psu_baudrate() == 9600
        assert window.spinBox_ac_voltage_limit.value() == pytest.approx(30.0)
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_reapplies_saved_psu_port_after_port_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    original = QtCore.QSettings
    store = original(
        original.Format.IniFormat,
        original.Scope.UserScope,
        "microwire_tests",
        "psu_refresh_keeps_com11",
    )
    store.clear()
    store.setValue("psu_backend", "owon_spe6102")
    store.setValue("psu_profiles/owon_spe6102/port", "COM11")
    store.setValue("psu_profiles/owon_spe6102/baud", "115200")
    store.setValue("psu_profiles/owon_spe6102/voltage_limit_v", 61.0)

    def factory(_organization: str, _application: str) -> QtCore.QSettings:
        return store

    calls = {"ports": 0}

    def _ports() -> list[tuple[str, str]]:
        calls["ports"] += 1
        if calls["ports"] == 1:
            return [("COM6 - Scale", "COM6")]
        return [("COM6 - Scale", "COM6"), ("COM11 - OWON", "COM11")]

    monkeypatch.setattr(ac_logger.QtCore, "QSettings", factory)
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", _ports)
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        assert window._selected_ac_psu_resource() == "COM11"
        window.populate_ac_psu_ports()
        assert window._selected_ac_psu_resource() == "COM11"
        assert store.value("psu_profiles/owon_spe6102/port", type=str) == "COM11"
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_releases_inherited_connected_psu_before_ac_run() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)

    class FakeSignal:
        def __init__(self) -> None:
            self.disconnected = False

        def disconnect(self, *_args: object) -> None:
            self.disconnected = True

    class FakeSerial:
        def __init__(self) -> None:
            self.readyRead = FakeSignal()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_serial = FakeSerial()
    button = QtWidgets.QPushButton("Disconnect")
    window.ui = type("Ui", (), {"pushButton_connect_port": button})()
    window.is_connected = True
    window.process_running = False
    window.port_name = r"\\.\COM6"
    window.ser_mcu = fake_serial
    window.handle_ser_mcu_readyRead = lambda: None
    safe_end_calls: list[str] = []
    window.send_safe_end_commands = lambda: safe_end_calls.append("safe")
    window._set_port_controls_enabled = lambda _enabled: None
    window._update_mode_action_state = lambda: None

    window._release_inherited_psu_port_for_ac("COM6")

    assert safe_end_calls == ["safe"]
    assert fake_serial.closed
    assert fake_serial.readyRead.disconnected
    assert window.is_connected is False
    assert button.text() == "Connect"
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

    for old_default in (5.0, 60.0, 62.0):
        window.spinBox_ac_voltage_limit.setValue(old_default)
        window._sync_ac_psu_from_shared_controls()
        assert window.spinBox_ac_voltage_limit.value() == pytest.approx(61.0)

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
        assert window.spinBox_ac_voltage_limit.text() == "61 V"
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
        assert [tile.visible.isChecked() for tile in window._plot_tiles] == [True, True, True, True]
        assert window._plot_tiles[0].x_combo.currentData() == "elapsed_s"
        assert window._plot_tiles[0].y_left_combo.currentData() == "rs_ohm"
        assert window._plot_tiles[0].y_right_combo.currentData() == "ls_h"
        assert window._plot_tiles[1].x_combo.currentData() == "current_actual_mA"
        assert window._plot_tiles[1].y_left_combo.currentData() == "rs_ohm"
        assert window._plot_tiles[1].y_right_combo.currentData() == "ls_h"
        assert window._plot_tiles[1].y_extra_combo.currentData() == "wire_resistance_ohm"
        assert window._plot_tiles[2].x_combo.currentData() == "frequency_hz"
        assert window._plot_tiles[3].x_combo.currentData() == "amplitude_v"
        assert len(window.figure.axes) >= 4
        assert window.figure.axes[0].get_title() == "Rs + Ls vs Elapsed time"
        assert window.figure.axes[0].get_ylabel() == "Rs [Ohm]"
        assert window.figure.axes[0].get_xlabel() == "Elapsed time [s]"
        assert window.figure.axes[2].get_title() == "Rs + Ls + Wire R vs Current measured"
        assert window.figure.axes[2].get_xlabel() == "Current measured [mA]"
        assert window.comboBox_ac_plot_spread.currentData() == "small"
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_frequency_plot_uses_log_scatter_and_colored_axes_without_legend(
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
        assert axis.get_legend() is None
        assert axis.yaxis.label.get_color() == window._plot_channel("rs_ohm").color
        assert twin.yaxis.label.get_color() == window._plot_channel("ls_h").color
        assert axis.yaxis.get_offset_text().get_color() == window._plot_channel("rs_ohm").color
        assert twin.yaxis.get_offset_text().get_color() == window._plot_channel("ls_h").color
        assert not any(line.get_visible() for line in twin.get_ygridlines())
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


def test_ac_logger_elapsed_plot_defaults_to_small_scatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "graph_elapsed_scatter")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        for tile in window._plot_tiles:
            tile.visible.setChecked(False)
        tile = window._plot_tiles[0]
        tile.visible.setChecked(True)
        window._set_combo_data(tile.x_combo, "elapsed_s")
        window._set_combo_data(tile.y_left_combo, "rs_ohm")
        window._ac_plot_points = [
            ac_logger.AcPlotPoint(float(index), "Ls-Rs", 1000.0, 0.1, 0.0, 1e-5, 14.3)
            for index in range(20)
        ]

        window._refresh_ac_plots(force=True)

        axis = window.figure.axes[0]
        assert not axis.lines
        assert len(axis.collections) == 1
        assert max(axis.collections[0].get_sizes()) <= 8
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_frequency_plot_keeps_each_condition_when_display_thinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "graph_frequency_condition_thinning")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(ac_logger, "AC_PLOT_MAX_POINTS_PER_CONDITION", 8)

    window = ac_logger.MainWindow()
    try:
        for tile in window._plot_tiles:
            tile.visible.setChecked(False)
        tile = window._plot_tiles[0]
        tile.visible.setChecked(True)
        window._set_combo_data(tile.x_combo, "frequency_hz")
        window._set_combo_data(tile.y_left_combo, "rs_ohm")
        window._ac_plot_points = [
            ac_logger.AcPlotPoint(float(index), "Ls-Rs", 100.0, 0.1, 0.0, 1e-5, 14.0 + index * 1e-4)
            for index in range(100)
        ] + [
            ac_logger.AcPlotPoint(100.0 + float(index), "Ls-Rs", 200000.0, 2.0, 0.0, 2e-5, 15.0 + index * 1e-4)
            for index in range(100)
        ]

        window._refresh_ac_plots(force=True)

        offsets = window.figure.axes[0].collections[0].get_offsets()
        x_values = {float(point[0]) for point in offsets}
        assert len(x_values) > 2
        assert min(x_values) < 100.0 < max(x_values)
        assert any(90.0 <= value <= 110.0 for value in x_values)
        assert any(180000.0 <= value <= 220000.0 for value in x_values)
        assert len(offsets) <= 16
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_repeated_frequency_points_are_spread_unless_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "graph_frequency_spread")
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
        window._ac_plot_points = [
            ac_logger.AcPlotPoint(float(index), "Ls-Rs", 1000.0, 0.3, 0.0, 2e-5, 14.0 + index * 0.01)
            for index in range(5)
        ]

        window._set_combo_data(window.comboBox_ac_plot_spread, "small")
        window._refresh_ac_plots(force=True)
        spread_x = [float(point[0]) for point in window.figure.axes[0].collections[0].get_offsets()]
        assert len({round(value, 6) for value in spread_x}) > 1

        window._set_combo_data(window.comboBox_ac_plot_spread, "off")
        window._refresh_ac_plots(force=True)
        stacked_x = [float(point[0]) for point in window.figure.axes[0].collections[0].get_offsets()]
        assert {round(value, 6) for value in stacked_x} == {1000.0}
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_current_plot_uses_measured_current_and_wire_resistance_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "graph_actual_current_wire_r")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        for tile in window._plot_tiles:
            tile.visible.setChecked(False)
        tile = window._plot_tiles[0]
        tile.visible.setChecked(True)
        window._set_combo_data(tile.x_combo, "current_actual_mA")
        window._set_combo_data(tile.y_left_combo, "rs_ohm")
        window._set_combo_data(tile.y_right_combo, "ls_h")
        window._set_combo_data(tile.y_extra_combo, "wire_resistance_ohm")
        window._ac_plot_points = [
            ac_logger.AcPlotPoint(0.0, "Ls-Rs", 1000.0, 0.3, 20.0, 2e-5, 14.4, 19.6, 400.0, 0.16),
            ac_logger.AcPlotPoint(1.0, "Ls-Rs", 1000.0, 0.3, 20.0, 2.1e-5, 14.5, 19.8, 420.0, 0.17),
            ac_logger.AcPlotPoint(2.0, "Ls-Rs", 1000.0, 0.3, 40.0, 2.2e-5, 14.6, 39.2, 430.0, 0.66),
            ac_logger.AcPlotPoint(3.0, "Ls-Rs", 1000.0, 0.3, 40.0, 2.3e-5, 14.7, 39.4, 470.0, 0.67),
        ]

        window._refresh_ac_plots(force=True)

        axes = window.figure.axes
        assert axes[0].get_xlabel() == "Current measured [mA]"
        assert axes[0].get_title() == "Rs + Ls + Wire R vs Current measured"
        assert axes[0].collections
        assert not axes[0].lines
        assert axes[1].collections
        assert not axes[1].lines
        assert axes[2].get_ylabel() == "Wire R [Ohm]"
        assert axes[2].yaxis.label.get_color() == window._plot_channel("wire_resistance_ohm").color
        assert axes[0].get_legend() is None
        assert axes[2].lines
        assert not axes[2].collections
        x_values = list(axes[2].lines[0].get_xdata())
        y_values = list(axes[2].lines[0].get_ydata())
        assert x_values == pytest.approx([19.7, 39.3])
        assert y_values == pytest.approx([410.0, 450.0])
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
        assert "AC current supply" in window.label_ac_psu_status.text()
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
        assert "Using connected AC OWON SPE6102" in window.label_lcr_status.text()
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
        window._reset_ac_progress("Empty-coil baseline", 100_000, units="time")
        window._complete_ac_progress("Empty-coil baseline")
        assert "1m 40s" in window.progress_ac_run.format()
        assert "100000/100000" not in window.progress_ac_run.format()
        window._set_ac_current_task("Current task: empty-coil baseline - 100 Hz, 0.1 voltage, read 1")
        assert "100 Hz" in window.label_ac_current_task.text()
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_formats_expected_finish_times() -> None:
    now = datetime(2026, 5, 19, 9, 15)
    assert ac_logger.MainWindow._format_expected_finish(45 * 60, now=now) == "today 10:00"
    assert ac_logger.MainWindow._format_expected_finish(24 * 3600 + 45 * 60, now=now) == "tomorrow 10:00"
    assert ac_logger.MainWindow._format_expected_finish(3 * 24 * 3600, now=now) == "2026-05-22 09:15"


def test_ac_logger_estimate_and_progress_show_expected_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "finish_estimate")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        monkeypatch.setattr(
            ac_logger.MainWindow,
            "_format_expected_finish",
            staticmethod(lambda seconds, now=None: f"finish-{int(round(seconds))}s"),
        )
        window.lineEdit_lcr_frequencies.setText("100")
        window.lineEdit_lcr_levels.setText("0.1")
        window.spinBox_ac_point_duration.setValue(10.0)
        window.spinBox_ac_dwell.setValue(1.0)
        window.spinBox_ac_current_start.setValue(0.0)
        window.spinBox_ac_current_stop.setValue(10.0)
        window.spinBox_ac_current_step.setValue(10.0)
        window._update_ac_sweep_estimate()
        estimate = window.label_ac_sweep_estimate.text()
        assert "finish finish-11s if started now" in estimate
        assert "finish finish-33s if started now" in estimate

        window._set_ac_elapsed_progress("Empty-coil baseline", 4.0, 10.0)
        assert "finish finish-6s" in window.progress_ac_run.format()

        window._set_ac_planned_progress("Microwire sweep", 5.0, 20.0, wall_elapsed_s=10.0)
        assert "finish finish-30s" in window.progress_ac_run.format()
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_builds_current_loop_with_optional_zero_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "zero_reference_loop")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [("COM11 - OWON", "COM11")])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        window.lineEdit_lcr_frequencies.setText("1000")
        window.lineEdit_lcr_levels.setText("0.3")
        window._set_combo_data(window.ui.comboBox_supply, "owon_spe6102")
        window._set_combo_data(window.ui.comboBox_port, "COM11")
        window.spinBox_ac_current_start.setValue(20.0)
        window.spinBox_ac_current_stop.setValue(80.0)
        window.spinBox_ac_current_step.setValue(20.0)
        window.checkBox_ac_include_zero_current.setChecked(True)

        config = window._build_ac_sweep_config()

        assert [(round(point.current_a * 1000.0), point.direction) for point in config.current_points] == [
            (0, "zero"),
            (20, "up"),
            (40, "up"),
            (60, "up"),
            (80, "up"),
            (60, "down"),
            (40, "down"),
            (20, "down"),
        ]
    finally:
        window.close()
        app.processEvents()


def test_ac_logger_microwire_eta_uses_planned_sweep_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "microwire_eta_position")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])

    window = ac_logger.MainWindow()
    try:
        config = sweep.AcSweepConfig(
            lcr_settings=[
                lcr6000.Lcr6000Settings(1000.0, 0.3, function="Ls-Rs"),
                lcr6000.Lcr6000Settings(1000.0, 0.5, function="Ls-Rs"),
            ],
            current_points=sweep.build_current_loop_points(
                start_mA=20.0,
                stop_mA=80.0,
                step_mA=20.0,
                direction_mode="up-down",
            ),
            dwell_s=1.0,
            psu_backend="owon_spe6102",
            psu_resource="COM11",
            voltage_limit_v=61.0,
            point_duration_s=10.0,
        )
        window._ac_active_sweep_config = config
        window._reset_ac_progress("Microwire sweep", window._sweep_total_reads(config), units="time")
        monkeypatch.setattr(ac_logger.time, "monotonic", lambda: 180.0)
        row = sweep.AcSweepRow(
            timestamp_utc="2026-05-18T15:00:00.000+00:00",
            elapsed_s=180.0,
            setting_index=2,
            total_settings=2,
            setting=config.lcr_settings[1],
            current_point=config.current_points[5],
            repeat_index=33,
            lcr_reading=lcr6000.Lcr6000Reading(
                timestamp_utc="2026-05-18T15:00:00.000+00:00",
                raw="+1.0,+2.0,+0.0,+0.0,OK",
                primary=1.0,
                secondary=2.0,
                monitor1=0.0,
                monitor2=0.0,
                comparator="OK",
            ),
            psu_measurement=sweep.PowerSupplyMeasurement(current_actual_a=0.04, voltage_actual_v=16.0, status="OK"),
            psu_backend="owon_spe6102",
            psu_resource="COM11",
            current_point_index=6,
            total_current_points=7,
            point_elapsed_s=3.0,
        )
        window._handle_ac_sweep_progress(row)
        text = window.progress_ac_run.format()
        assert "100%" not in text
        assert "ETA 0s" not in text
        assert "ETA" in text
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


def test_ac_logger_diagnostics_records_ui_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _isolate_ac_qsettings(monkeypatch, "ui_telemetry_diag")
    monkeypatch.setattr(ac_logger, "available_serial_ports", lambda: [])
    monkeypatch.setattr(sweep, "available_power_supply_ports", lambda: [])
    monkeypatch.setattr(sweep, "detect_power_supply_candidates", lambda *args, **kwargs: [])
    window = ac_logger.MainWindow()
    try:
        timer = getattr(window, "_ac_plot_refresh_timer", None)
        if isinstance(timer, QtCore.QTimer):
            timer.stop()
        path = tmp_path / "ac_diag.jsonl"
        window._ac_diagnostics_enabled = True
        window._ac_diagnostics_path = path
        window._ac_ui_telemetry_last_s = 0.0
        window._ac_ui_telemetry_ticks = 0
        window._ac_ui_telemetry_sum_s = 0.0
        window._ac_ui_telemetry_max_s = 0.0
        clock_values = [1.0] + [1.0 + 0.016 * index for index in range(1, 62)]
        times = iter(clock_values)

        def fake_perf_counter() -> float:
            return next(times, clock_values[-1] + 1.0)

        monkeypatch.setattr(ac_logger.time, "perf_counter", fake_perf_counter)

        for _ in range(62):
            window._record_ac_ui_telemetry_tick()

        text = path.read_text(encoding="utf-8")
        assert '"event": "ui_telemetry"' in text
        assert '"ticks": 60' in text
        assert '"fps_estimate"' in text
    finally:
        window.close()
        app.processEvents()


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


def test_ac_baseline_worker_reconfigures_and_retries_slow_lcr_cadence(
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

    class FakeMeter:
        is_open = True

        def __init__(self, clock: FakeClock) -> None:
            self.clock = clock
            self.configure_count = 0
            self.fetch_count = 0

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            self.configure_count += 1

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            self.fetch_count += 1
            self.clock.advance(0.2 if self.configure_count == 1 else 0.025)
            return lcr6000.Lcr6000Reading(
                timestamp_utc=f"2026-05-15T00:00:{self.fetch_count:02d}.000+00:00",
                primary=float(self.fetch_count) * 1e-6,
                secondary=14.0,
                monitor1=0.0,
                monitor2=0.0,
                comparator="OK",
                raw=f"+{self.fetch_count}.0E-06,+1.4E+01,+0,+0,OK",
            )

    clock = FakeClock()
    monkeypatch.setattr(ac_logger.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(ac_logger, "AC_LCR_SLOW_RETRY_CHECK_S", 0.6)
    monkeypatch.setattr(ac_logger, "AC_LCR_SLOW_RETRY_DISCARD_S", 0.0)
    monkeypatch.setattr(ac_logger, "AC_LCR_SLOW_RETRY_MAX_ATTEMPTS", 1)
    meter = FakeMeter(clock)
    worker = ac_logger.AcBaselineWorker(
        meter=meter,
        plan=[lcr6000.Lcr6000Settings(frequency_hz=200000.0, level_value=0.3, aperture="FAST")],
        output_path=tmp_path / "baseline.tsv",
        point_duration_s=0.7,
        settle_s=0.0,
        total_planned_s=1.0,
    )
    finished: list[tuple[str, bool]] = []
    worker.finished.connect(lambda path, stopped: finished.append((path, stopped)))

    worker.run()

    assert finished == [(str(tmp_path / "baseline.tsv"), False)]
    assert meter.configure_count == 2
    text = (tmp_path / "baseline.tsv").read_text(encoding="utf-8")
    assert "# WARN" in text
    assert "slow LCR cadence attempt 1" in text


def test_ac_baseline_worker_continues_full_point_after_slow_lcr_retries_are_exhausted(
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

    class FakeMeter:
        is_open = True

        def __init__(self, clock: FakeClock) -> None:
            self.clock = clock
            self.configure_count = 0
            self.fetch_count = 0

        def configure(self, _setting: lcr6000.Lcr6000Settings) -> None:
            self.configure_count += 1

        def fetch_impedance(self) -> lcr6000.Lcr6000Reading:
            self.fetch_count += 1
            self.clock.advance(0.2)
            return lcr6000.Lcr6000Reading(
                timestamp_utc=f"2026-05-15T00:01:{self.fetch_count:02d}.000+00:00",
                primary=float(self.fetch_count) * 1e-6,
                secondary=14.0,
                monitor1=0.0,
                monitor2=0.0,
                comparator="OK",
                raw=f"+{self.fetch_count}.0E-06,+1.4E+01,+0,+0,OK",
            )

    clock = FakeClock()
    monkeypatch.setattr(ac_logger.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(ac_logger, "AC_LCR_SLOW_RETRY_CHECK_S", 0.6)
    monkeypatch.setattr(ac_logger, "AC_LCR_SLOW_RETRY_DISCARD_S", 0.0)
    monkeypatch.setattr(ac_logger, "AC_LCR_SLOW_RETRY_MAX_ATTEMPTS", 1)
    worker = ac_logger.AcBaselineWorker(
        meter=FakeMeter(clock),
        plan=[lcr6000.Lcr6000Settings(frequency_hz=100000.0, level_value=2.0, aperture="FAST")],
        output_path=tmp_path / "baseline.tsv",
        point_duration_s=1.0,
        settle_s=0.0,
        total_planned_s=1.0,
    )
    worker.run()

    text = (tmp_path / "baseline.tsv").read_text(encoding="utf-8")
    assert "slow LCR cadence persisted" in text
    assert clock.now >= 2.2
    assert text.count("\n2026-") >= 10


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


def test_ac_logger_reset_live_plots_clears_previous_run_points() -> None:
    window = ac_logger.MainWindow.__new__(ac_logger.MainWindow)
    window._ac_plot_points = [
        ac_logger.AcPlotPoint(0.0, "Ls-Rs", 1000.0, 0.3, 20.0, 2e-5, 14.4),
        ac_logger.AcPlotPoint(1.0, "Ls-Rs", 1000.0, 0.3, 40.0, 2.1e-5, 14.5),
    ]
    window._ac_plot_dirty = True
    diagnostics: list[tuple[str, dict[str, object]]] = []
    redraws = {"count": 0}
    window._write_ac_diagnostic = lambda event, **payload: diagnostics.append((event, payload))  # type: ignore[method-assign]
    window._refresh_ac_plots = lambda **_kwargs: redraws.__setitem__("count", redraws["count"] + 1)  # type: ignore[method-assign]

    window._reset_ac_live_plots("microwire_sweep_start")

    assert window._ac_plot_points == []
    assert window._ac_plot_dirty is False
    assert diagnostics == [("plot_reset", {"reason": "microwire_sweep_start"})]
    assert redraws["count"] == 1


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
    assert lines[1].startswith("# config_json=")
    snapshot = json.loads(lines[1].removeprefix("# config_json="))
    assert snapshot["run_type"] == "empty_coil_baseline"
    assert snapshot["lcr_settings"][0]["frequency_hz"] == 1000.0
    assert lines[3] == ac_logger.BASELINE_HEADER_LINE
    assert "Current (mA)" not in lines[3]
    assert lines[4].split("\t") == row
