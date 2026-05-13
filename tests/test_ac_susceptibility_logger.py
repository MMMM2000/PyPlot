from __future__ import annotations

from pathlib import Path

import pytest

from data_logging.ac_susceptibility_logger import lcr6000
from data_logging.ac_susceptibility_logger import sweep

ac_logger = pytest.importorskip(
    "data_logging.ac_susceptibility_logger.ac_susceptibility_logger",
    reason="Qt widgets backend is unavailable",
    exc_type=ImportError,
)


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


def test_estimate_sweep_totals_counts_repeats_and_dwell() -> None:
    estimate = sweep.estimate_sweep(
        lcr_settings=[lcr6000.Lcr6000Settings(1000.0, 0.1), lcr6000.Lcr6000Settings(10000.0, 0.1)],
        current_points=[
            sweep.CurrentLoopPoint(0.02, "up"),
            sweep.CurrentLoopPoint(0.04, "up"),
            sweep.CurrentLoopPoint(0.02, "down"),
        ],
        repeats=3,
        dwell_s=2.0,
    )

    assert estimate.total_measurements == 18
    assert estimate.estimated_seconds == pytest.approx(36.0)


def test_write_sweep_metadata_and_row_flushes_incrementally(tmp_path: Path) -> None:
    path = tmp_path / "overnight.tsv"
    config = sweep.AcSweepConfig(
        lcr_settings=[lcr6000.Lcr6000Settings(1000.0, 0.1, function="Ls-Rs")],
        current_points=[sweep.CurrentLoopPoint(0.02, "up")],
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

    rows = window._collect_baseline_rows(plan, repeats=2)

    assert meter.configured == plan
    assert meter.fetch_count == 4
    assert [row[1:3] for row in rows] == [["1", "1"], ["1", "2"], ["2", "1"], ["2", "2"]]


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
