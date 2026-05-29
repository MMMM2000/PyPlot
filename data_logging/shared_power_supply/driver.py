from __future__ import annotations

import time
from threading import RLock
from typing import Any, Callable, Protocol

from .profiles import SupplyProfile, detect_hmp_profile

try:
    import serial
except Exception:  # pragma: no cover - optional dependency guard
    serial = None  # type: ignore[assignment]


class SerialLike(Protocol):
    is_open: bool

    def write(self, data: bytes) -> int | None: ...

    def readline(self) -> bytes: ...

    def close(self) -> None: ...


SerialFactory = Callable[..., SerialLike]


def _parse_first_float(text: str) -> float | None:
    for token in str(text or "").replace(",", ".").split():
        try:
            return float(token)
        except ValueError:
            continue
    try:
        return float(str(text or "").replace(",", ".").strip())
    except ValueError:
        return None


class HmpSerialDriver:
    """Serialized SCPI driver for HMP4030/HMP4040 supplies."""

    def __init__(
        self,
        *,
        port_name: str,
        baudrate: int = 115200,
        profile: SupplyProfile | None = None,
        serial_factory: SerialFactory | None = None,
        timeout_s: float = 0.5,
    ) -> None:
        self.port_name = port_name.strip()
        self.baudrate = int(baudrate)
        self.profile = profile
        self.timeout_s = float(timeout_s)
        self._serial_factory = serial_factory
        self._serial: SerialLike | None = None
        self._io_lock = RLock()

    def connect(self) -> None:
        if self._serial is not None and getattr(self._serial, "is_open", False):
            return
        if self._serial_factory is None:
            if serial is None:
                raise RuntimeError("pyserial is not available.")
            self._serial_factory = serial.Serial
        if not self.port_name:
            raise RuntimeError("Select a power-supply serial port first.")
        self._serial = self._serial_factory(
            self.port_name,
            baudrate=self.baudrate,
            timeout=self.timeout_s,
            write_timeout=self.timeout_s,
        )

    def close(self) -> None:
        with self._io_lock:
            port = self._serial
            self._serial = None
            if port is not None:
                port.close()

    def is_connected(self) -> bool:
        return self._serial is not None and bool(getattr(self._serial, "is_open", False))

    def _port(self) -> SerialLike:
        if self._serial is None or not getattr(self._serial, "is_open", False):
            raise RuntimeError("Power supply is not connected.")
        return self._serial

    def command_log(self) -> list[str]:
        port = self._serial
        commands = getattr(port, "commands", None)
        if isinstance(commands, list):
            return [str(command) for command in commands]
        return []

    def _write_command(self, command: str, *, settle_s: float = 0.03) -> None:
        wire = command.strip()
        if not wire:
            return
        self._port().write((wire + "\n").encode("ascii"))
        if settle_s > 0:
            time.sleep(settle_s)

    def _read_line(self) -> str:
        return self._port().readline().decode("ascii", errors="ignore").strip()

    def query(self, command: str, *, settle_s: float = 0.03) -> str:
        with self._io_lock:
            self._write_command(command, settle_s=settle_s)
            return self._read_line()

    def command(self, command: str, *, settle_s: float = 0.03) -> None:
        with self._io_lock:
            self._write_command(command, settle_s=settle_s)

    def identify(self) -> str:
        idn = self.query("*IDN?")
        detected = detect_hmp_profile(idn)
        if detected is not None:
            self.profile = detected
        return idn

    def select_channel(self, channel: int) -> None:
        if self.profile is None:
            raise RuntimeError("Detect the HMP model before selecting channels.")
        channel = self.profile.validate_channel(channel)
        self._write_command(f"INST:NSEL {channel}")

    def configure_channel(
        self,
        *,
        channel: int,
        voltage_v: float,
        current_a: float,
        output_on: bool,
    ) -> None:
        with self._io_lock:
            self.select_channel(channel)
            current_mA = max(0.0, float(current_a) * 1000.0)
            if self.profile is not None:
                current_mA = self.profile.normalize_current_mA(current_mA)
            self._write_command(f"VOLT {max(0.0, float(voltage_v)):.3f}")
            self._write_command(f"CURR {current_mA / 1000.0:.4f}")
            self._write_command("OUTP ON" if output_on else "OUTP OFF")

    def set_current_mA(self, *, channel: int, current_mA: float) -> None:
        if self.profile is None:
            raise RuntimeError("Detect the HMP model before setting current.")
        quantized = self.profile.normalize_current_mA(current_mA)
        with self._io_lock:
            self.select_channel(channel)
            self._write_command(f"CURR {quantized / 1000.0:.4f}")

    def set_output(self, *, channel: int, output_on: bool) -> None:
        with self._io_lock:
            self.select_channel(channel)
            self._write_command("OUTP ON" if output_on else "OUTP OFF")

    def output_state(self, *, channel: int) -> bool | None:
        with self._io_lock:
            self.select_channel(channel)
            self._write_command("OUTP?")
            response = self._read_line().strip()
        if not response:
            return None
        return response[:1] == "1" or response.upper().startswith("ON")

    def measure(self, *, channel: int) -> dict[str, float | None]:
        with self._io_lock:
            self.select_channel(channel)
            self._write_command("MEAS:VOLT?")
            voltage_v = _parse_first_float(self._read_line())
            self._write_command("MEAS:CURR?")
            current_a = _parse_first_float(self._read_line())
        return {
            "voltage_V": voltage_v,
            "current_mA": None if current_a is None else current_a * 1000.0,
        }
