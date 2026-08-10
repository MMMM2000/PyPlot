from __future__ import annotations

from typing import Any

from scripts import hmp_shared_live_validation as live_validation


def test_mini_dma_validation_restores_motor_output_state(monkeypatch) -> None:
    class _Client:
        output_states = {3: False, 4: False}
        output_calls: list[tuple[int, bool]] = []

        def __init__(self, *, host: str, port: int) -> None:
            assert host == "127.0.0.1"
            assert port == 8765

        def lease(self, *, channel: int, owner: str, role: str) -> dict[str, Any]:
            return {"channel": channel, "lease_id": f"lease-{channel}", "owner": owner, "role": role}

        def output_state(self, *, channel: int) -> bool:
            return self.output_states[channel]

        def measure_channel(self, *, channel: int) -> dict[str, float]:
            return {"current_mA": 1.0, "voltage_V": 0.2}

        def configure_channel(
            self,
            *,
            channel: int,
            lease_id: str,
            voltage_v: float,
            current_a: float,
            output_on: bool,
        ) -> None:
            self.output_states[channel] = output_on
            self.output_calls.append((channel, output_on))

        def set_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
            pass

        def set_output(self, *, channel: int, lease_id: str, output_on: bool) -> None:
            self.output_states[channel] = output_on
            self.output_calls.append((channel, output_on))

        def release(self, *, channel: int, lease_id: str) -> None:
            pass

    monkeypatch.setattr(live_validation, "BrokerJsonClient", _Client)
    monkeypatch.setattr(live_validation.time, "sleep", lambda _seconds: None)
    rows: list[dict[str, Any]] = []
    result: dict[str, Any] = {}

    live_validation._run_mini_dma_client(
        host="127.0.0.1",
        port=8765,
        current_channel=4,
        motor_channel=3,
        voltage_limit_v=32.05,
        current_limit_mA=3.0,
        max_current_mA=3.0,
        ramp_rate_mA_s=1.0,
        rows=rows,
        result=result,
    )

    assert _Client.output_states[3] is False
    assert _Client.output_states[4] is False
    assert _Client.output_calls[0] == (3, True)
    assert _Client.output_calls[-1] == (3, False)
    assert result["motor_after"]["output_on"] is False
