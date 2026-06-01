from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import scripts.hmp_shared_live_smoke as live_smoke


@dataclass(frozen=True)
class _Probe:
    available: bool = True
    electrically_idle: bool = True
    message: str = "ok"
    idn: str = "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62"
    busy_channels: tuple[int, ...] = ()
    unknown_output_channels: tuple[int, ...] = ()
    channel_readbacks: dict[int, dict[str, Any]] | None = None


def test_live_smoke_skips_busy_bench_without_acquiring_lock(
    monkeypatch,
    tmp_path: Path,
) -> None:
    acquired = False

    def _busy_probe(**_kwargs: object) -> _Probe:
        return _Probe(
            electrically_idle=False,
            busy_channels=(3,),
            channel_readbacks={
                1: {"output_on": False, "readback": {"voltage_V": 0.0, "current_mA": 0.0}},
                3: {"output_on": True, "readback": {"voltage_V": 12.0, "current_mA": 230.0}},
                4: {"output_on": False, "readback": {"voltage_V": 0.0, "current_mA": 0.0}},
            },
        )

    @contextmanager
    def _unexpected_lock(**_kwargs: object):
        nonlocal acquired
        acquired = True
        yield

    monkeypatch.setattr(live_smoke, "probe_hmp_bench", _busy_probe)
    monkeypatch.setattr(live_smoke, "wait_for_bench_lock", _unexpected_lock)

    result = live_smoke.run_smoke(
        host="127.0.0.1",
        preferred_port=8765,
        port_name="COM3",
        baudrate=115200,
        owner="test",
        wait_s=0.0,
        poll_s=0.1,
        output_dir=tmp_path,
    )

    assert result["passed"] is False
    assert result["skipped_reason"] == "hardware_not_compatible_before_timeout"
    assert acquired is False
    assert Path(str(result["artifact"])).exists()


def test_live_smoke_uses_existing_broker_and_cleans_outputs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    @contextmanager
    def _lock(**_kwargs: object):
        yield

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            self.outputs = {"1": False, "3": False, "4": False}
            self.leases: dict[int, str] = {}
            self.output_writes: list[tuple[int, bool]] = []
            self.releases: list[tuple[int, str]] = []

        def request(self, action: str, **_payload: object) -> dict[str, object]:
            if action in {"assign_role", "save_profile"}:
                return {"ok": True}
            raise AssertionError(f"unexpected request action {action}")

        def snapshot(self) -> dict[str, object]:
            return {"leases": {}, "readbacks": {}, "scheduler": {}}

        def output_state(self, *, channel: int) -> bool:
            return bool(self.outputs[str(channel)])

        def lease(self, *, channel: int, owner: str, role: str) -> dict[str, object]:
            if channel == 1 and role == live_smoke.ROLE_MINI_DMA_CURRENT:
                raise PermissionError("CH1 is assigned to current_annealing, not mini_dma_current_sweep.")
            lease_id = f"lease-{channel}-{role}-{owner}"
            self.leases[channel] = lease_id
            return {"lease_id": lease_id, "channel": channel, "owner": owner, "role": role}

        def release(self, *, channel: int, lease_id: str) -> None:
            self.releases.append((channel, lease_id))
            self.leases.pop(channel, None)

        def configure_channel(
            self,
            *,
            channel: int,
            lease_id: str,
            voltage_v: float,
            current_a: float,
            output_on: bool,
        ) -> None:
            assert self.leases[channel] == lease_id
            self.outputs[str(channel)] = output_on

        def set_output(self, *, channel: int, lease_id: str, output_on: bool) -> None:
            assert self.leases[channel] == lease_id
            self.outputs[str(channel)] = output_on
            self.output_writes.append((channel, output_on))

        def set_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
            assert self.leases[channel] == lease_id

        def measure_channel(self, *, channel: int) -> dict[str, float | bool]:
            return {
                "voltage_V": 12.0 if channel == 3 and self.outputs[str(channel)] else 0.25,
                "current_mA": 230.0 if channel == 3 and self.outputs[str(channel)] else 1.0,
                "cached": False,
            }

    fake_client = FakeClient()

    monkeypatch.setattr(live_smoke, "probe_hmp_bench", lambda **_kwargs: _Probe())
    monkeypatch.setattr(live_smoke, "wait_for_bench_lock", _lock)
    monkeypatch.setattr(live_smoke, "_broker_alive", lambda **_kwargs: True)
    monkeypatch.setattr(live_smoke, "BrokerJsonClient", lambda **_kwargs: fake_client)
    monkeypatch.setattr(live_smoke.time, "sleep", lambda _seconds: None)

    result = live_smoke.run_smoke(
        host="127.0.0.1",
        preferred_port=8765,
        port_name="COM3",
        baudrate=115200,
        owner="test",
        wait_s=0.0,
        poll_s=0.1,
        output_dir=tmp_path,
    )

    assert result["passed"] is True
    assert result["using_existing_broker"] is True
    assert fake_client.outputs == {"1": False, "3": False, "4": False}
    assert (1, False) in fake_client.output_writes
    assert (3, False) in fake_client.output_writes
    assert (4, False) in fake_client.output_writes
    assert sorted(channel for channel, _lease_id in fake_client.releases) == [1, 3, 4]
    assert Path(str(result["artifact"])).exists()


def test_live_smoke_allows_and_preserves_preexisting_motor_rail(
    monkeypatch,
    tmp_path: Path,
) -> None:
    @contextmanager
    def _lock(**_kwargs: object):
        yield

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            self.outputs = {"1": False, "3": True, "4": False}
            self.leases: dict[int, str] = {}
            self.output_writes: list[tuple[int, bool]] = []
            self.releases: list[tuple[int, str]] = []

        def request(self, action: str, **_payload: object) -> dict[str, object]:
            if action in {"assign_role", "save_profile"}:
                return {"ok": True}
            raise AssertionError(f"unexpected request action {action}")

        def snapshot(self) -> dict[str, object]:
            return {"leases": {}, "readbacks": {}, "scheduler": {}}

        def output_state(self, *, channel: int) -> bool:
            return bool(self.outputs[str(channel)])

        def lease(self, *, channel: int, owner: str, role: str) -> dict[str, object]:
            if channel == 1 and role == live_smoke.ROLE_MINI_DMA_CURRENT:
                raise PermissionError("CH1 is assigned to current_annealing, not mini_dma_current_sweep.")
            lease_id = f"lease-{channel}-{role}-{owner}"
            self.leases[channel] = lease_id
            return {"lease_id": lease_id, "channel": channel, "owner": owner, "role": role}

        def release(self, *, channel: int, lease_id: str) -> None:
            self.releases.append((channel, lease_id))
            self.leases.pop(channel, None)

        def configure_channel(
            self,
            *,
            channel: int,
            lease_id: str,
            voltage_v: float,
            current_a: float,
            output_on: bool,
        ) -> None:
            assert self.leases[channel] == lease_id
            if channel != 3:
                self.outputs[str(channel)] = output_on

        def set_output(self, *, channel: int, lease_id: str, output_on: bool) -> None:
            assert self.leases[channel] == lease_id
            self.outputs[str(channel)] = output_on
            self.output_writes.append((channel, output_on))

        def set_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
            assert self.leases[channel] == lease_id

        def measure_channel(self, *, channel: int) -> dict[str, float | bool]:
            return {
                "voltage_V": 12.0 if channel == 3 else 0.25,
                "current_mA": 230.0 if channel == 3 else 1.0,
                "cached": False,
            }

    fake_client = FakeClient()

    def _motor_on_probe(**_kwargs: object) -> _Probe:
        return _Probe(
            electrically_idle=False,
            busy_channels=(3,),
            channel_readbacks={
                1: {"output_on": False, "readback": {"voltage_V": 0.0, "current_mA": 0.0}},
                3: {"output_on": True, "readback": {"voltage_V": 12.0, "current_mA": 230.0}},
                4: {"output_on": False, "readback": {"voltage_V": 0.0, "current_mA": 0.0}},
            },
        )

    monkeypatch.setattr(live_smoke, "probe_hmp_bench", _motor_on_probe)
    monkeypatch.setattr(live_smoke, "wait_for_bench_lock", _lock)
    monkeypatch.setattr(live_smoke, "_broker_alive", lambda **_kwargs: True)
    monkeypatch.setattr(live_smoke, "BrokerJsonClient", lambda **_kwargs: fake_client)
    monkeypatch.setattr(live_smoke.time, "sleep", lambda _seconds: None)

    result = live_smoke.run_smoke(
        host="127.0.0.1",
        preferred_port=8765,
        port_name="COM3",
        baudrate=115200,
        owner="test",
        wait_s=0.0,
        poll_s=0.1,
        output_dir=tmp_path,
        allow_existing_motor_rail=True,
    )

    assert result["passed"] is True
    assert result["motor_rail_was_on"] is True
    assert fake_client.outputs == {"1": False, "3": True, "4": False}
    assert (3, False) not in fake_client.output_writes
    assert sorted(channel for channel, _lease_id in fake_client.releases) == [1, 3, 4]
    assert Path(str(result["artifact"])).exists()


def test_live_smoke_preserves_existing_ch3_motor_lease(
    monkeypatch,
    tmp_path: Path,
) -> None:
    @contextmanager
    def _lock(**_kwargs: object):
        yield

    existing_lease = {
        "channel": 3,
        "owner": "mini-dma",
        "role": live_smoke.ROLE_MINI_DMA_MOTOR,
        "lease_id": "existing-ch3",
    }

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            self.outputs = {"1": False, "3": True, "4": False}
            self.leases: dict[int, str] = {}
            self.output_writes: list[tuple[int, bool]] = []
            self.releases: list[tuple[int, str]] = []
            self.lease_requests: list[tuple[int, str]] = []

        def request(self, action: str, **_payload: object) -> dict[str, object]:
            if action in {"assign_role", "save_profile"}:
                return {"ok": True}
            raise AssertionError(f"unexpected request action {action}")

        def snapshot(self) -> dict[str, object]:
            return {"leases": {"3": dict(existing_lease)}, "readbacks": {}, "scheduler": {}}

        def output_state(self, *, channel: int) -> bool:
            return bool(self.outputs[str(channel)])

        def lease(self, *, channel: int, owner: str, role: str) -> dict[str, object]:
            self.lease_requests.append((channel, role))
            if channel == 1 and role == live_smoke.ROLE_MINI_DMA_CURRENT:
                raise PermissionError("CH1 is assigned to current_annealing, not mini_dma_current_sweep.")
            if channel == 3:
                raise PermissionError("CH3 is already leased by Mini DMA.")
            lease_id = f"lease-{channel}-{role}-{owner}"
            self.leases[channel] = lease_id
            return {"lease_id": lease_id, "channel": channel, "owner": owner, "role": role}

        def release(self, *, channel: int, lease_id: str) -> None:
            self.releases.append((channel, lease_id))
            self.leases.pop(channel, None)

        def configure_channel(
            self,
            *,
            channel: int,
            lease_id: str,
            voltage_v: float,
            current_a: float,
            output_on: bool,
        ) -> None:
            assert self.leases[channel] == lease_id
            self.outputs[str(channel)] = output_on

        def set_output(self, *, channel: int, lease_id: str, output_on: bool) -> None:
            assert self.leases[channel] == lease_id
            self.outputs[str(channel)] = output_on
            self.output_writes.append((channel, output_on))

        def set_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
            assert self.leases[channel] == lease_id

        def measure_channel(self, *, channel: int) -> dict[str, float | bool]:
            return {"voltage_V": 0.25, "current_mA": 1.0, "cached": False}

        def latest_readback(
            self,
            *,
            channel: int,
            max_age_s: float | None = None,
            fallback_to_measure: bool = True,
        ) -> dict[str, float | bool]:
            assert channel == 3
            return {"voltage_V": 12.0, "current_mA": 230.0, "cached": True}

    fake_client = FakeClient()

    def _motor_on_probe(**_kwargs: object) -> _Probe:
        return _Probe(electrically_idle=False, busy_channels=(3,))

    monkeypatch.setattr(live_smoke, "probe_hmp_bench", _motor_on_probe)
    monkeypatch.setattr(live_smoke, "wait_for_bench_lock", _lock)
    monkeypatch.setattr(live_smoke, "_broker_alive", lambda **_kwargs: True)
    monkeypatch.setattr(live_smoke, "BrokerJsonClient", lambda **_kwargs: fake_client)
    monkeypatch.setattr(live_smoke.time, "sleep", lambda _seconds: None)

    result = live_smoke.run_smoke(
        host="127.0.0.1",
        preferred_port=8765,
        port_name="COM3",
        baudrate=115200,
        owner="test",
        wait_s=0.0,
        poll_s=0.1,
        output_dir=tmp_path,
        allow_existing_motor_rail=True,
    )

    assert result["passed"] is True
    assert result["ch3_existing_lease_preserved"] == existing_lease
    assert (3, live_smoke.ROLE_MINI_DMA_MOTOR) not in fake_client.lease_requests
    assert fake_client.outputs == {"1": False, "3": True, "4": False}
    assert all(channel != 3 for channel, _lease_id in fake_client.releases)
    assert (3, False) not in fake_client.output_writes
