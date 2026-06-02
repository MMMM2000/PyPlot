from __future__ import annotations

import importlib
import json

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

    assert window._samples_current == []
    assert window._samples_resistance == []
    assert len(window.ax1.lines) == 0
    assert len(window.ax2.lines) == 0


def test_logger_segments_use_current_annealing_cycle_palette(qtbot) -> None:
    window = logger_mod.MainWindow()
    qtbot.addWidget(window)
    window.current_step_mA = 1

    colors = window._segment_colors([1.0, 2.0, 3.0, 2.0, 1.0, 2.0])

    assert colors == [
        "#dc2626",
        "#dc2626",
        "#2563eb",
        "#2563eb",
        "#f97316",
    ]


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


def test_percent_from_hold_handles_zero() -> None:
    assert logger_mod.MainWindow._percent_from_hold(10.0, 0.0) is None


def test_percent_from_hold_nominal() -> None:
    assert logger_mod.MainWindow._percent_from_hold(200.0, 100.0) == pytest.approx(200.0)


def test_percent_from_hold_handles_nan() -> None:
    assert logger_mod.MainWindow._percent_from_hold(float("nan"), 100.0) is None
