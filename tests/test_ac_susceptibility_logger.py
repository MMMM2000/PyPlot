from __future__ import annotations

import pytest

from data_logging.ac_susceptibility_logger import lcr6000


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
