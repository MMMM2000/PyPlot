"""Deterministic spawned-process HMP fake used by lifecycle tests."""

from __future__ import annotations

import json
from pathlib import Path

from .profiles import HMP4040_PROFILE


class FakeHmpDriver:
    def __init__(self, *, port_name: str, audit_path: str = "") -> None:
        self.port_name = port_name
        self.profile = HMP4040_PROFILE
        self._audit_path = Path(audit_path) if audit_path else None
        self._connected = False
        self._outputs = {channel: False for channel in range(1, 5)}
        self._currents_mA = {channel: 0.0 for channel in range(1, 5)}
        self._voltages_v = {channel: 0.0 for channel in range(1, 5)}

    def connect(self) -> None:
        self._connected = True
        self._audit("connect")

    def close(self) -> None:
        self._connected = False
        self._audit("close")

    def is_connected(self) -> bool:
        return self._connected

    def identify(self) -> str:
        return "Rohde&Schwarz,HMP4040,FAKE,0.0"

    def configure_channel(
        self,
        *,
        channel: int,
        voltage_v: float,
        current_a: float,
        output_on: bool,
    ) -> None:
        self._voltages_v[channel] = float(voltage_v)
        self._currents_mA[channel] = float(current_a) * 1000.0
        self._outputs[channel] = bool(output_on)
        self._audit(f"configure:{channel}:output={int(bool(output_on))}")

    def set_current_mA(self, *, channel: int, current_mA: float) -> None:
        self._currents_mA[channel] = float(current_mA)
        self._audit(f"current:{channel}:{float(current_mA):.6f}")

    def set_output(self, *, channel: int, output_on: bool) -> None:
        self._outputs[channel] = bool(output_on)
        self._audit(f"output:{channel}:{'on' if output_on else 'off'}")

    def output_state(self, *, channel: int) -> bool:
        return self._outputs[channel]

    def measure_current_mA(self, *, channel: int) -> float:
        return self._currents_mA[channel] if self._outputs[channel] else 0.0

    def measure_voltage_v(self, *, channel: int) -> float:
        return self._voltages_v[channel] if self._outputs[channel] else 0.0

    def command_log(self) -> list[str]:
        return []

    def _audit(self, line: str) -> None:
        path = self._audit_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")


def create_fake_hmp_driver(config: object) -> FakeHmpDriver:
    raw_options = str(getattr(config, "driver_factory_options_json", "{}") or "{}")
    options = json.loads(raw_options)
    return FakeHmpDriver(
        port_name=str(getattr(config, "port_name", "FAKE-HMP")),
        audit_path=str(options.get("audit_path") or ""),
    )


__all__ = ["FakeHmpDriver", "create_fake_hmp_driver"]
