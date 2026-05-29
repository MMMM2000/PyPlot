from __future__ import annotations

import socket
import time

import pytest

from data_logging.shared_power_supply.broker import (
    ROLE_CURRENT_ANNEALING,
    ROLE_MINI_DMA_CURRENT,
    ROLE_MINI_DMA_MOTOR,
    SharedPowerSupplyBroker,
)
from data_logging.shared_power_supply.driver import HmpSerialDriver
from data_logging.shared_power_supply.profiles import HMP4030_PROFILE, HMP4040_PROFILE, detect_hmp_profile
from data_logging.shared_power_supply.protocol import BrokerJsonClient, start_broker_server


class FakeHmpSerial:
    def __init__(self, *_args: object, idn: str = "Rohde&Schwarz,HMP4040,0,0", **_kwargs: object) -> None:
        self.is_open = True
        self.idn = idn
        self.commands: list[str] = []
        self.selected_channel = 1
        self.channels = {
            channel: {"voltage": 0.0, "current": 0.0, "output": False}
            for channel in range(1, 5)
        }
        self._responses: list[bytes] = []

    def write(self, data: bytes) -> int:
        command = data.decode("ascii").strip()
        self.commands.append(command)
        upper = command.upper()
        if upper == "*IDN?":
            self._responses.append((self.idn + "\n").encode("ascii"))
        elif upper.startswith("INST:NSEL"):
            self.selected_channel = int(command.split()[-1])
        elif upper.startswith("VOLT "):
            self.channels[self.selected_channel]["voltage"] = float(command.split()[-1])
        elif upper.startswith("CURR "):
            self.channels[self.selected_channel]["current"] = float(command.split()[-1])
        elif upper == "OUTP ON":
            self.channels[self.selected_channel]["output"] = True
        elif upper == "OUTP OFF":
            self.channels[self.selected_channel]["output"] = False
        elif upper == "OUTP?":
            value = "1" if self.channels[self.selected_channel]["output"] else "0"
            self._responses.append(f"{value}\n".encode("ascii"))
        elif upper == "MEAS:VOLT?":
            self._responses.append(f"{self.channels[self.selected_channel]['voltage']}\n".encode("ascii"))
        elif upper == "MEAS:CURR?":
            self._responses.append(f"{self.channels[self.selected_channel]['current']}\n".encode("ascii"))
        return len(data)

    def readline(self) -> bytes:
        if self._responses:
            return self._responses.pop(0)
        return b"\n"

    def close(self) -> None:
        self.is_open = False


def _driver(profile=HMP4040_PROFILE) -> HmpSerialDriver:
    driver = HmpSerialDriver(
        port_name="COM3",
        profile=profile,
        serial_factory=FakeHmpSerial,
        timeout_s=0.01,
    )
    driver.connect()
    return driver


def test_hmp_idn_detection_maps_supported_models() -> None:
    assert detect_hmp_profile("HAMEG,HMP4030,serial,fw") == HMP4030_PROFILE
    assert detect_hmp_profile("Rohde&Schwarz,HMP4040,serial,fw") == HMP4040_PROFILE
    assert detect_hmp_profile("OWON,SPE6102,serial,fw") is None


def test_broker_rejects_channel_four_for_hmp4030() -> None:
    broker = SharedPowerSupplyBroker(_driver(HMP4030_PROFILE), HMP4030_PROFILE)

    with pytest.raises(ValueError, match="CH4"):
        broker.assign_role(channel=4, role=ROLE_CURRENT_ANNEALING, confirmed=True)


def test_broker_accepts_channel_four_for_hmp4040() -> None:
    broker = SharedPowerSupplyBroker(_driver(HMP4040_PROFILE), HMP4040_PROFILE)

    channel = broker.assign_role(channel=4, role=ROLE_MINI_DMA_CURRENT, confirmed=True)

    assert channel.role == ROLE_MINI_DMA_CURRENT
    assert channel.confirmed is True


def test_broker_prevents_two_owners_from_leasing_same_channel() -> None:
    broker = SharedPowerSupplyBroker(_driver(), HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True)
    broker.confirm_profile()

    broker.lease(channel=1, owner="annealing-a", role=ROLE_CURRENT_ANNEALING)

    with pytest.raises(PermissionError, match="already leased"):
        broker.lease(channel=1, owner="annealing-b", role=ROLE_CURRENT_ANNEALING)


def test_broker_requires_confirmed_role_before_control() -> None:
    broker = SharedPowerSupplyBroker(_driver(), HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=False)

    with pytest.raises(PermissionError, match="confirmed role"):
        broker.lease(channel=1, owner="annealing", role=ROLE_CURRENT_ANNEALING)


def test_broker_serializes_channel_selection_and_commands() -> None:
    driver = _driver()
    broker = SharedPowerSupplyBroker(driver, HMP4040_PROFILE)
    broker.assign_role(
        channel=4,
        role=ROLE_MINI_DMA_CURRENT,
        confirmed=True,
        voltage_limit_v=32.05,
        current_limit_a=1.0,
    )
    broker.confirm_profile()
    lease = broker.lease(channel=4, owner="mini-dma", role=ROLE_MINI_DMA_CURRENT)

    broker.configure_channel(
        channel=4,
        lease_id=lease.lease_id,
        voltage_v=12.0,
        current_a=0.025,
        output_on=True,
    )
    broker.measure_channel(channel=4)

    assert driver.command_log() == [
        "INST:NSEL 4",
        "VOLT 12.000",
        "CURR 0.0250",
        "OUTP ON",
        "INST:NSEL 4",
        "MEAS:VOLT?",
        "MEAS:CURR?",
    ]


def test_broker_allows_current_annealing_and_mini_dma_on_separate_channels() -> None:
    driver = _driver()
    broker = SharedPowerSupplyBroker(driver, HMP4040_PROFILE)
    broker.assign_role(
        channel=1,
        role=ROLE_CURRENT_ANNEALING,
        confirmed=True,
        voltage_limit_v=32.05,
        current_limit_a=0.1,
    )
    broker.assign_role(
        channel=3,
        role=ROLE_MINI_DMA_MOTOR,
        confirmed=True,
        voltage_limit_v=12.0,
        current_limit_a=0.4,
    )
    broker.assign_role(
        channel=4,
        role=ROLE_MINI_DMA_CURRENT,
        confirmed=True,
        voltage_limit_v=32.05,
        current_limit_a=0.06,
    )
    broker.confirm_profile()

    anneal = broker.lease(channel=1, owner="current-annealing", role=ROLE_CURRENT_ANNEALING)
    motor = broker.lease(channel=3, owner="mini-dma", role=ROLE_MINI_DMA_MOTOR)
    current = broker.lease(channel=4, owner="mini-dma", role=ROLE_MINI_DMA_CURRENT)

    broker.configure_channel(channel=1, lease_id=anneal.lease_id, voltage_v=2.0, current_a=0.001, output_on=True)
    broker.configure_channel(channel=3, lease_id=motor.lease_id, voltage_v=12.0, current_a=0.4, output_on=True)
    broker.configure_channel(channel=4, lease_id=current.lease_id, voltage_v=32.05, current_a=0.02, output_on=True)

    snapshot = broker.snapshot()
    assert set(snapshot["leases"]) == {"1", "3", "4"}
    assert driver._serial.channels[1]["current"] == pytest.approx(0.001)  # type: ignore[union-attr]
    assert driver._serial.channels[3]["voltage"] == pytest.approx(12.0)  # type: ignore[union-attr]
    assert driver._serial.channels[4]["current"] == pytest.approx(0.02)  # type: ignore[union-attr]
    with pytest.raises(PermissionError, match="assigned to"):
        broker.lease(channel=1, owner="mini-dma", role=ROLE_MINI_DMA_CURRENT)


def test_broker_reports_channel_output_state() -> None:
    broker = SharedPowerSupplyBroker(_driver(), HMP4040_PROFILE)
    broker.assign_role(channel=3, role=ROLE_MINI_DMA_CURRENT, confirmed=True)
    broker.confirm_profile()
    lease = broker.lease(channel=3, owner="mini-dma", role=ROLE_MINI_DMA_CURRENT)

    broker.configure_channel(channel=3, lease_id=lease.lease_id, voltage_v=12.0, current_a=0.4, output_on=True)

    assert broker.output_state(channel=3) is True


def test_broker_blocks_raw_and_global_reset_style_commands() -> None:
    broker = SharedPowerSupplyBroker(_driver(), HMP4040_PROFILE)

    with pytest.raises(PermissionError, match="guarded"):
        broker.raw_command("*RST")
    with pytest.raises(PermissionError, match="Raw SCPI"):
        broker.raw_command("VOLT 1")


def test_broker_profile_requires_reconfirmation_when_port_changes() -> None:
    broker = SharedPowerSupplyBroker(_driver(), HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True)
    saved = broker.confirm_profile(name="Kosice HMP4040 bench")
    payload = saved.to_dict()
    payload["port_identity"] = "COM9"

    loaded = broker.load_profile(saved.from_dict(payload))

    assert loaded.requires_confirmation is True
    assert loaded.channels[1].confirmed is False


def test_broker_json_protocol_snapshot_round_trip() -> None:
    broker = SharedPowerSupplyBroker(_driver(), HMP4040_PROFILE)
    server, thread = start_broker_server(broker)
    try:
        host, port = server.server_address
        with socket.create_connection((host, port), timeout=2.0) as client:
            client.sendall(b'{"action": "snapshot"}\n')
            raw = client.recv(4096).decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert '"ok": true' in raw
    assert '"channel_count": 4' in raw


def test_scheduler_polls_channels_and_returns_cached_readbacks() -> None:
    driver = _driver()
    broker = SharedPowerSupplyBroker(driver, HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True)
    broker.assign_role(channel=4, role=ROLE_MINI_DMA_CURRENT, confirmed=True)
    broker.confirm_profile()
    anneal = broker.lease(channel=1, owner="anneal", role=ROLE_CURRENT_ANNEALING)
    mini = broker.lease(channel=4, owner="mini", role=ROLE_MINI_DMA_CURRENT)
    broker.configure_channel(channel=1, lease_id=anneal.lease_id, voltage_v=1.0, current_a=0.002, output_on=True)
    broker.configure_channel(channel=4, lease_id=mini.lease_id, voltage_v=1.0, current_a=0.003, output_on=True)

    broker.configure_polling(channel=1, interval_s=1.0)
    broker.configure_polling(channel=4, interval_s=1.0)
    broker.process_scheduler_once(now_s=10.0)
    cached = broker.latest_readback(channel=1, max_age_s=60.0, now_s=10.5)

    assert cached["current_mA"] == pytest.approx(2.0)
    assert cached["cached"] is True
    assert cached["age_s"] == pytest.approx(0.5)
    assert cached["timestamp_s"] == pytest.approx(10.0)
    snapshot = broker.snapshot()
    assert snapshot["scheduler"]["polling"]["1"]["interval_s"] == pytest.approx(1.0)
    assert snapshot["readbacks"]["4"]["current_mA"] == pytest.approx(3.0)


def test_scheduler_coalesces_current_setpoints_until_next_tick() -> None:
    driver = _driver()
    broker = SharedPowerSupplyBroker(driver, HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True, current_limit_a=0.01)
    broker.confirm_profile()
    lease = broker.lease(channel=1, owner="anneal", role=ROLE_CURRENT_ANNEALING)
    broker.configure_channel(channel=1, lease_id=lease.lease_id, voltage_v=1.0, current_a=0.001, output_on=True)

    broker.configure_polling(channel=1, interval_s=1.0)
    broker.schedule_current(channel=1, lease_id=lease.lease_id, current_mA=1.2)
    broker.schedule_current(channel=1, lease_id=lease.lease_id, current_mA=1.4)
    broker.process_scheduler_once(now_s=20.0)

    commands = driver.command_log()
    assert "CURR 0.0012" not in commands
    assert commands.count("CURR 0.0014") == 1
    assert driver._serial.channels[1]["current"] == pytest.approx(0.0014)  # type: ignore[union-attr]
    cached = broker.latest_readback(channel=1, max_age_s=1.0, now_s=20.0)
    assert cached["setpoint_current_mA"] == pytest.approx(1.4)
    assert cached["pending_current_mA"] is None
    assert broker.snapshot()["scheduler"]["metrics"]["coalesced_current_requests"] == 1


def test_scheduler_rate_limited_ramp_sends_small_steps_without_catchup() -> None:
    driver = _driver()
    broker = SharedPowerSupplyBroker(driver, HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True, current_limit_a=0.01)
    broker.confirm_profile()
    lease = broker.lease(channel=1, owner="anneal", role=ROLE_CURRENT_ANNEALING)
    broker.configure_channel(channel=1, lease_id=lease.lease_id, voltage_v=1.0, current_a=0.001, output_on=True)
    broker.configure_polling(channel=1, interval_s=10.0)

    broker.schedule_current_ramp(
        channel=1,
        lease_id=lease.lease_id,
        target_mA=3.0,
        rate_mA_s=1.0,
        max_step_mA=0.2,
        resolution_mA=0.2,
        now_s=0.0,
    )
    broker.process_scheduler_once(now_s=0.1)
    broker.process_scheduler_once(now_s=0.2)
    broker.process_scheduler_once(now_s=1.2)

    commands = driver.command_log()
    assert "CURR 0.0020" not in commands
    assert "CURR 0.0012" in commands
    assert "CURR 0.0014" in commands
    assert driver._serial.channels[1]["current"] == pytest.approx(0.0014)  # type: ignore[union-attr]
    metrics = broker.snapshot()["scheduler"]["metrics"]
    assert metrics["ramp_steps_sent"] == 2
    assert metrics["current_commands_sent"] == 2


def test_scheduler_direct_and_ramp_requests_override_each_other() -> None:
    driver = _driver()
    broker = SharedPowerSupplyBroker(driver, HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True, current_limit_a=0.01)
    broker.confirm_profile()
    lease = broker.lease(channel=1, owner="anneal", role=ROLE_CURRENT_ANNEALING)
    broker.configure_channel(channel=1, lease_id=lease.lease_id, voltage_v=1.0, current_a=0.001, output_on=True)

    broker.schedule_current(channel=1, lease_id=lease.lease_id, current_mA=2.0)
    broker.schedule_current_ramp(
        channel=1,
        lease_id=lease.lease_id,
        target_mA=3.0,
        rate_mA_s=1.0,
        max_step_mA=0.2,
        resolution_mA=0.2,
        now_s=0.0,
    )
    broker.process_scheduler_once(now_s=0.2)

    commands = driver.command_log()
    assert "CURR 0.0020" not in commands
    assert "CURR 0.0012" in commands

    broker.schedule_current(channel=1, lease_id=lease.lease_id, current_mA=1.0)
    broker.process_scheduler_once(now_s=0.4)

    commands = driver.command_log()
    assert commands.count("CURR 0.0010") == 2
    assert "CURR 0.0014" not in commands


def test_release_clears_scheduler_state_for_channel() -> None:
    driver = _driver()
    broker = SharedPowerSupplyBroker(driver, HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True, current_limit_a=0.01)
    broker.confirm_profile()
    lease = broker.lease(channel=1, owner="anneal", role=ROLE_CURRENT_ANNEALING)
    broker.configure_channel(channel=1, lease_id=lease.lease_id, voltage_v=1.0, current_a=0.001, output_on=True)
    broker.configure_polling(channel=1, interval_s=1.0)
    broker.schedule_current_ramp(
        channel=1,
        lease_id=lease.lease_id,
        target_mA=3.0,
        rate_mA_s=1.0,
        max_step_mA=0.2,
        resolution_mA=0.2,
        now_s=0.0,
    )

    assert "1" in broker.snapshot()["scheduler"]["current_ramps"]

    broker.release(channel=1, lease_id=lease.lease_id)

    scheduler = broker.snapshot()["scheduler"]
    assert "1" not in scheduler["current_ramps"]
    assert "1" not in scheduler["pending_currents"]
    assert "1" not in scheduler["polling"]
    assert "1" not in scheduler["setpoint_currents_mA"]


def test_scheduler_skips_ramp_snapshot_if_newer_request_arrives_before_write() -> None:
    driver = _driver()
    broker = SharedPowerSupplyBroker(driver, HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True, current_limit_a=0.01)
    broker.confirm_profile()
    lease = broker.lease(channel=1, owner="anneal", role=ROLE_CURRENT_ANNEALING)
    broker.configure_channel(channel=1, lease_id=lease.lease_id, voltage_v=1.0, current_a=0.001, output_on=True)
    broker.schedule_current_ramp(
        channel=1,
        lease_id=lease.lease_id,
        target_mA=3.0,
        rate_mA_s=1.0,
        max_step_mA=0.2,
        resolution_mA=0.2,
        now_s=0.0,
    )

    _lease_id, ramp = broker._current_ramps[1]  # type: ignore[attr-defined]
    original_next_setpoint = ramp.next_setpoint

    def _next_setpoint_with_direct_override(*, now_s: float) -> float | None:
        next_mA = original_next_setpoint(now_s=now_s)
        broker.schedule_current(channel=1, lease_id=lease.lease_id, current_mA=1.0)
        return next_mA

    ramp.next_setpoint = _next_setpoint_with_direct_override  # type: ignore[method-assign]

    broker.process_scheduler_once(now_s=0.2)

    assert "CURR 0.0012" not in driver.command_log()
    assert broker.snapshot()["scheduler"]["pending_currents"]["1"]["current_mA"] == pytest.approx(1.0)

    broker.process_scheduler_once(now_s=0.4)

    assert driver.command_log().count("CURR 0.0010") == 2
    assert "CURR 0.0012" not in driver.command_log()


def test_scheduler_thread_keeps_cached_readbacks_fresh() -> None:
    broker = SharedPowerSupplyBroker(_driver(), HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True)
    broker.confirm_profile()
    lease = broker.lease(channel=1, owner="anneal", role=ROLE_CURRENT_ANNEALING)
    broker.configure_channel(channel=1, lease_id=lease.lease_id, voltage_v=1.0, current_a=0.001, output_on=True)
    broker.configure_polling(channel=1, interval_s=0.02)

    broker.start_scheduler(tick_s=0.005)
    try:
        deadline = time.time() + 1.0
        readback = None
        while time.time() < deadline:
            readback = broker.latest_readback(channel=1, max_age_s=1.0, fallback_to_measure=False)
            if readback.get("cached"):
                break
            time.sleep(0.01)
    finally:
        broker.stop_scheduler()

    assert readback is not None
    assert readback["cached"] is True
    assert readback["current_mA"] == pytest.approx(1.0)


def test_broker_json_client_exposes_scheduler_cached_readbacks() -> None:
    driver = _driver()
    broker = SharedPowerSupplyBroker(driver, HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True, current_limit_a=0.01)
    broker.confirm_profile()
    server, thread = start_broker_server(broker)
    try:
        host, port = server.server_address
        client = BrokerJsonClient(host=host, port=port)
        lease = client.lease(channel=1, owner="anneal", role=ROLE_CURRENT_ANNEALING)
        client.configure_channel(channel=1, lease_id=lease["lease_id"], voltage_v=1.0, current_a=0.001, output_on=True)

        client.configure_polling(channel=1, interval_s=0.05)
        client.schedule_current(channel=1, lease_id=lease["lease_id"], current_mA=1.2)
        client.schedule_current(channel=1, lease_id=lease["lease_id"], current_mA=1.4)
        client.start_scheduler(tick_s=0.01)
        deadline = time.time() + 1.0
        readback = None
        while time.time() < deadline:
            readback = client.latest_readback(channel=1, max_age_s=1.0, fallback_to_measure=False)
            if readback.get("cached") and readback.get("setpoint_current_mA") == pytest.approx(1.4):
                break
            time.sleep(0.02)
        client.stop_scheduler()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert readback is not None
    assert readback["cached"] is True
    assert readback["current_mA"] == pytest.approx(1.4)
    assert readback["pending_current_mA"] is None
    assert "CURR 0.0012" not in driver.command_log()


def test_broker_json_client_schedules_rate_limited_current_ramps() -> None:
    driver = _driver()
    broker = SharedPowerSupplyBroker(driver, HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True, current_limit_a=0.01)
    broker.confirm_profile()
    server, thread = start_broker_server(broker)
    try:
        host, port = server.server_address
        client = BrokerJsonClient(host=host, port=port)
        lease = client.lease(channel=1, owner="anneal", role=ROLE_CURRENT_ANNEALING)
        client.configure_channel(channel=1, lease_id=lease["lease_id"], voltage_v=1.0, current_a=0.001, output_on=True)

        client.schedule_current_ramp(
            channel=1,
            lease_id=lease["lease_id"],
            target_mA=3.0,
            rate_mA_s=1.0,
            max_step_mA=0.2,
            resolution_mA=0.2,
        )
        broker.process_scheduler_once(now_s=time.monotonic() + 0.2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert "CURR 0.0012" in driver.command_log()
