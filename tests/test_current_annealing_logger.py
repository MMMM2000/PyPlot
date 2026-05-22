from __future__ import annotations

import importlib

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="Qt widgets backend is unavailable", exc_type=ImportError)


logger_mod = importlib.import_module("data_logging.current_annealing_logger.current_annealing_logger")


class _FakeBrokerClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.readbacks: list[dict[str, float]] = [
            {"voltage_V": 2.5, "current_mA": 10.0},
        ]

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


def test_shared_broker_profile_is_available() -> None:
    assert "shared_hmp_broker" in logger_mod.SUPPLY_PROFILES


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


def test_shared_broker_setpoint_and_stop_only_affect_leased_channel(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    fake = _FakeBrokerClient()
    window._shared_broker_client = fake
    window._apply_supply_profile("shared_hmp_broker")
    window.channel_select = 2
    window._shared_broker_lease_id = "lease-1"
    window.current_current_set = 0.025

    window._send_current_setpoint()
    window.send_safe_end_commands()

    assert fake.calls == [
        ("set_current", {"channel": 2, "lease_id": "lease-1", "current_mA": 25.0}),
        ("set_output", {"channel": 2, "lease_id": "lease-1", "output_on": False}),
        ("release", {"channel": 2, "lease_id": "lease-1"}),
    ]
    assert window._shared_broker_lease_id is None


def test_percent_from_hold_handles_zero() -> None:
    assert logger_mod.MainWindow._percent_from_hold(10.0, 0.0) is None


def test_percent_from_hold_nominal() -> None:
    assert logger_mod.MainWindow._percent_from_hold(200.0, 100.0) == pytest.approx(200.0)


def test_percent_from_hold_handles_nan() -> None:
    assert logger_mod.MainWindow._percent_from_hold(float("nan"), 100.0) is None
