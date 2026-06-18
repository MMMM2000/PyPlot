from __future__ import annotations

import socket

import pytest

from data_logging.shared_power_supply.broker import (
    ROLE_AC_SUSCEPTIBILITY,
    ROLE_CURRENT_ANNEALING,
    ROLE_MINI_DMA_CURRENT,
    SharedPowerSupplyBroker,
    VALID_ROLES,
)
from data_logging.shared_power_supply.driver import HmpSerialDriver
from data_logging.shared_power_supply.profiles import HMP4030_PROFILE, HMP4040_PROFILE, detect_hmp_profile
from data_logging.shared_power_supply.protocol import (
    BrokerConnectionError,
    BrokerJsonClient,
    BrokerRequestError,
    broker_failure_diagnostic,
    start_broker_server,
)


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


def test_broker_accepts_ac_susceptibility_role() -> None:
    assert ROLE_AC_SUSCEPTIBILITY in VALID_ROLES


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


def test_broker_reuses_same_owner_lease_without_replacing_id() -> None:
    broker = SharedPowerSupplyBroker(_driver(), HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True)
    broker.confirm_profile()

    first = broker.lease(channel=1, owner="annealing", role=ROLE_CURRENT_ANNEALING)
    second = broker.lease(channel=1, owner="annealing", role=ROLE_CURRENT_ANNEALING)

    assert second.lease_id == first.lease_id


def test_broker_blocks_profile_changes_while_channel_is_leased() -> None:
    broker = SharedPowerSupplyBroker(_driver(), HMP4040_PROFILE)
    broker.assign_role(channel=1, role=ROLE_CURRENT_ANNEALING, confirmed=True)
    saved = broker.confirm_profile()
    broker.lease(channel=1, owner="annealing", role=ROLE_CURRENT_ANNEALING)

    with pytest.raises(PermissionError, match="leased"):
        broker.assign_role(channel=1, role=ROLE_MINI_DMA_CURRENT, confirmed=True)
    with pytest.raises(PermissionError, match="leased"):
        broker.confirm_profile(name="new name")
    with pytest.raises(PermissionError, match="leased"):
        broker.load_profile(saved)


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


def test_broker_json_client_uses_configurable_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class _Socket:
        def __enter__(self) -> "_Socket":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def sendall(self, data: bytes) -> None:
            seen["request"] = data

        def recv(self, _size: int) -> bytes:
            if seen.get("responded"):
                return b""
            seen["responded"] = True
            return b'{"ok": true, "snapshot": {}}\n'

    def _create_connection(address: tuple[str, int], timeout: float) -> _Socket:
        seen["address"] = address
        seen["timeout"] = timeout
        return _Socket()

    monkeypatch.setattr("data_logging.shared_power_supply.protocol.socket.create_connection", _create_connection)

    client = BrokerJsonClient(host="127.0.0.1", port=8765, timeout_s=9.5)
    assert client.request("snapshot")["snapshot"] == {}
    assert seen["address"] == ("127.0.0.1", 8765)
    assert seen["timeout"] == pytest.approx(9.5)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            BrokerConnectionError(
                host="127.0.0.1",
                port=8765,
                action="snapshot",
                cause=TimeoutError("timed out"),
            ),
            "broker missing or not reachable at 127.0.0.1:8765",
        ),
        (
            PermissionError(13, "Access is denied", "COM3"),
            "direct HMP serial access was denied",
        ),
        (
            BrokerRequestError(action="lease", message="channel already leased by ac_logger"),
            "channel lease refused",
        ),
        (
            BrokerRequestError(action="set_current", message="valid lease required for CH4"),
            "stale channel lease detected",
        ),
        (
            BrokerRequestError(action="configure_channel", message="requested current exceeds CH4 limit"),
            "channel limit is stale or too low",
        ),
    ],
)
def test_broker_failure_diagnostic_classifies_common_operator_failures(
    exc: Exception,
    expected: str,
) -> None:
    diagnostic = broker_failure_diagnostic(exc, context="Test broker")

    assert diagnostic.startswith("Test broker:")
    assert expected in diagnostic
