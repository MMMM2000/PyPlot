"""GW Instek LCR-6000 series remote-control helpers.

The LCR-6200 appears on Windows as a virtual COM port once the vendor
driver is installed. Commands are line-feed terminated SCPI-like ASCII
commands documented in the LCR-6000 series manual.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
import time
from typing import Iterable, Sequence

try:  # pragma: no cover - exercised only when pyserial is installed
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover - import guard for docs/tests
    serial = None  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]


LCR6000_VID = 0x2184
LCR6000_PID = 0x005F
DEFAULT_BAUDRATE = 115200
DEFAULT_TIMEOUT_S = 1.5
LCR_MODEL_FREQUENCY_LIMITS_HZ = {
    "LCR-6300": (10.0, 300_000.0),
    "LCR-6200": (10.0, 200_000.0),
    "LCR-6100": (10.0, 100_000.0),
    "LCR-6020": (10.0, 20_000.0),
    "LCR-6002": (10.0, 2_000.0),
}
LCR_VOLTAGE_LEVEL_RANGE_V = (0.01, 2.0)
LCR_CURRENT_LEVEL_RANGE_A = (100e-6, 20e-3)
LCR_FRONT_PANEL_VOLTAGE_PRESETS_V = (0.01, 0.1, 0.3, 0.5, 1.0, 1.5, 2.0)
LCR_FRONT_PANEL_CURRENT_PRESETS_A = (100e-6, 500e-6, 1e-3, 5e-3, 10e-3, 20e-3)

SUPPORTED_FUNCTIONS = (
    "Ls-Q",
    "Ls-Rs",
    "Lp-Q",
    "Lp-Rp",
    "R-X",
    "Z-Q",
    "Z-D",
    "Z-THD",
    "Z-THR",
    "Cp-D",
    "Cp-Rp",
    "Cs-D",
    "Cs-Rs",
)

SUPPORTED_MONITORS = (
    "OFF",
    "Z",
    "D",
    "Q",
    "VAC",
    "IAC",
    "R",
    "X",
    "Y",
    "G",
    "B",
    "ABS",
    "PER",
    "THR",
    "THD",
)


@dataclass(frozen=True)
class SerialPortCandidate:
    """A serial port option that may be an LCR-6000 series device."""

    device: str
    description: str = ""
    hwid: str = ""
    vid: int | None = None
    pid: int | None = None

    @property
    def is_lcr6000(self) -> bool:
        return self.vid == LCR6000_VID and self.pid == LCR6000_PID

    @property
    def label(self) -> str:
        label = self.device
        if self.description:
            label += f" - {self.description}"
        if self.is_lcr6000:
            label += " [GW Instek LCR-6000]"
        return label


@dataclass(frozen=True)
class Lcr6000Settings:
    """Measurement settings for one AC susceptibility current sweep."""

    frequency_hz: float
    level_value: float
    level_mode: str = "voltage"
    function: str = "Ls-Rs"
    monitor1: str = "Z"
    monitor2: str = "IAC"
    aperture: str = "FAST"


@dataclass(frozen=True)
class Lcr6000Reading:
    """Parsed result from ``FETC:IMP?``."""

    timestamp_utc: str
    raw: str
    primary: float | None
    secondary: float | None
    monitor1: float | None
    monitor2: float | None
    comparator: str


def available_serial_ports() -> list[SerialPortCandidate]:
    """Return serial ports, sorted with LCR-6000 candidates first."""

    if list_ports is None:
        return []
    ports: list[SerialPortCandidate] = []
    for info in list_ports.comports():
        ports.append(
            SerialPortCandidate(
                device=str(info.device),
                description=str(getattr(info, "description", "") or ""),
                hwid=str(getattr(info, "hwid", "") or ""),
                vid=getattr(info, "vid", None),
                pid=getattr(info, "pid", None),
            )
        )
    return sorted(ports, key=lambda item: (not item.is_lcr6000, item.device))


_VALUE_RE = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)\s*([a-zA-Z]*)\s*$")
_MULTIPLIERS = {
    "": 1.0,
    "hz": 1.0,
    "v": 1.0,
    "a": 1.0,
    "k": 1e3,
    "khz": 1e3,
    "ma": 1e6,
    "mhz": 1e6,
    "g": 1e9,
    "ghz": 1e9,
    "m": 1e-3,
    "mv": 1e-3,
    "ma_current": 1e-3,
    "u": 1e-6,
    "ua": 1e-6,
    "uv": 1e-6,
}


def parse_numeric_token(token: str, *, quantity: str = "generic") -> float:
    """Parse UI-entered numbers with lightweight SI suffixes.

    ``quantity="current"`` treats ``mA`` as milliampere while bare ``M`` or
    ``MHz`` still means mega for frequency-like fields.
    """

    text = token.strip()
    if not text:
        raise ValueError("empty value")
    match = _VALUE_RE.match(text)
    if match is None:
        raise ValueError(f"invalid numeric value: {token!r}")
    value = float(match.group(1))
    suffix_raw = match.group(2)
    suffix = suffix_raw.lower()
    if quantity == "current" and suffix == "ma":
        multiplier = _MULTIPLIERS["ma_current"]
    elif suffix_raw == "M":
        multiplier = 1e6
    else:
        multiplier = _MULTIPLIERS.get(suffix)
    if multiplier is None:
        raise ValueError(f"unsupported suffix in {token!r}")
    return value * multiplier


def parse_numeric_list(text: str, *, quantity: str = "generic") -> list[float]:
    """Parse comma/semicolon/whitespace separated numeric values."""

    tokens = [part for part in re.split(r"[,;\s]+", text.strip()) if part]
    values = [parse_numeric_token(part, quantity=quantity) for part in tokens]
    if not values:
        raise ValueError("enter at least one value")
    return values


def build_settings_plan(
    frequencies_hz: Sequence[float],
    levels: Sequence[float],
    *,
    level_mode: str,
    function: str,
    monitor1: str,
    monitor2: str,
    aperture: str,
) -> list[Lcr6000Settings]:
    """Create the frequency x level measurement matrix."""

    mode = level_mode.strip().lower()
    if mode not in {"voltage", "current"}:
        raise ValueError("level_mode must be 'voltage' or 'current'")
    normalized_function = normalize_function(function)
    normalized_monitor1 = normalize_monitor(monitor1)
    normalized_monitor2 = normalize_monitor(monitor2)
    normalized_aperture = normalize_aperture(aperture)
    plan: list[Lcr6000Settings] = []
    for frequency in frequencies_hz:
        if frequency <= 0:
            raise ValueError("frequency must be positive")
        for level in levels:
            if level <= 0:
                raise ValueError("level must be positive")
            setting = Lcr6000Settings(
                frequency_hz=float(frequency),
                level_value=float(level),
                level_mode=mode,
                function=normalized_function,
                monitor1=normalized_monitor1,
                monitor2=normalized_monitor2,
                aperture=normalized_aperture,
            )
            validate_settings(setting)
            plan.append(setting)
    return plan


def validate_settings(settings: Lcr6000Settings, *, model: str = "LCR-6200") -> None:
    """Validate settings against the LCR-6000 manual ranges.

    The LCR-6000 frequency setting is continuous inside each model's range. The
    front panel has convenient increment presets, but remote commands may use
    arbitrary in-range values with the instrument's resolution.
    """

    model_key = model.strip().upper()
    min_hz, max_hz = LCR_MODEL_FREQUENCY_LIMITS_HZ.get(model_key, LCR_MODEL_FREQUENCY_LIMITS_HZ["LCR-6200"])
    frequency = float(settings.frequency_hz)
    if not (min_hz <= frequency <= max_hz):
        if model_key == "LCR-6200":
            raise ValueError("LCR-6200 frequency must be in the manual range 10 Hz to 200 kHz")
        raise ValueError(f"{model_key} frequency must be in the manual range {min_hz:g} Hz to {max_hz:g} Hz")
    level = float(settings.level_value)
    if settings.level_mode == "voltage":
        min_v, max_v = LCR_VOLTAGE_LEVEL_RANGE_V
        if not (min_v <= level <= max_v):
            raise ValueError("LCR voltage level must be in the manual range 10 mV to 2 V")
    elif settings.level_mode == "current":
        min_a, max_a = LCR_CURRENT_LEVEL_RANGE_A
        if not (min_a <= level <= max_a):
            raise ValueError("LCR current level must be in the manual range 100 uA to 20 mA")
    else:
        raise ValueError("level_mode must be 'voltage' or 'current'")


def normalize_function(value: str) -> str:
    token = value.strip()
    for known in SUPPORTED_FUNCTIONS:
        if token.lower() == known.lower():
            return known
    raise ValueError(f"unsupported LCR function: {value!r}")


def normalize_monitor(value: str) -> str:
    token = value.strip()
    for known in SUPPORTED_MONITORS:
        if token.lower() == known.lower():
            return known
    raise ValueError(f"unsupported monitor parameter: {value!r}")


def normalize_aperture(value: str) -> str:
    token = value.strip().upper()
    if token not in {"FAST", "MED", "SLOW"}:
        raise ValueError(f"unsupported aperture: {value!r}")
    return token


def _parse_float_token(token: str) -> float | None:
    stripped = token.strip()
    if not stripped or stripped.upper() in {"OUT", "NAN"}:
        return None
    try:
        value = float(stripped)
    except ValueError:
        return None
    return value


def parse_fetch_impedance(response: str) -> Lcr6000Reading:
    """Parse the comma-separated ``FETC:IMP?`` response."""

    raw = response.strip()
    parts = [part.strip() for part in raw.split(",")]
    values = [_parse_float_token(part) for part in parts[:4]]
    while len(values) < 4:
        values.append(None)
    comparator = ",".join(parts[4:]).strip() if len(parts) > 4 else ""
    return Lcr6000Reading(
        timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        raw=raw,
        primary=values[0],
        secondary=values[1],
        monitor1=values[2],
        monitor2=values[3],
        comparator=comparator,
    )


def commands_for_settings(settings: Lcr6000Settings) -> list[str]:
    """Return line-terminated commands needed to apply one setting."""

    commands = [
        "DISP:PAGE MEAS\n",
        f"FUNC {settings.function}\n",
        "FUNC:RANG:AUTO AUTO\n",
        f"FUNC:MON1 {settings.monitor1}\n",
        f"FUNC:MON2 {settings.monitor2}\n",
        f"FREQ {settings.frequency_hz:.12g}\n",
    ]
    if settings.level_mode == "current":
        commands.append(f"LEV:CURR {settings.level_value:.12g}\n")
    else:
        commands.append(f"LEV:VOLT {settings.level_value:.12g}\n")
    commands.append(f"APER {settings.aperture}\n")
    return commands


class Lcr6000Serial:
    """Small pyserial wrapper for the LCR-6000 virtual COM protocol."""

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if serial is None:
            raise RuntimeError("pyserial is not available")
        self.port = port
        self.baudrate = int(baudrate)
        self.timeout = float(timeout)
        self._serial = serial.Serial(
            port=port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            write_timeout=self.timeout,
        )

    @property
    def is_open(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    def close(self) -> None:
        self._serial.close()

    def write(self, command: str) -> None:
        if not command.endswith("\n"):
            command += "\n"
        self._serial.write(command.encode("ascii"))
        self._serial.flush()

    def query(self, command: str, *, attempts: int = 2) -> str:
        for attempt in range(max(1, int(attempts))):
            try:
                self._serial.reset_input_buffer()
            except Exception:
                pass
            self.write(command)
            data = self._serial.readline()
            text = data.decode("ascii", errors="replace").strip()
            if text:
                return text
            if attempt + 1 < attempts:
                time.sleep(0.2)
        return ""

    def identify(self) -> str:
        return self.query("*IDN?")

    def _wait_for_measurement_page(self, *, timeout_s: float = 2.0, poll_s: float = 0.1) -> None:
        attempts = max(1, int(math.ceil(timeout_s / poll_s)))
        for attempt in range(attempts):
            page = self.query("DISP:PAGE?", attempts=1).strip().upper()
            if page.startswith("MEAS"):
                return
            if attempt + 1 < attempts:
                time.sleep(poll_s)

    def configure(self, settings: Lcr6000Settings) -> None:
        for command in commands_for_settings(settings):
            self.write(command)
            if command.strip().upper() == "DISP:PAGE MEAS":
                self._wait_for_measurement_page()
            else:
                time.sleep(0.15)

    def fetch_impedance(self) -> Lcr6000Reading:
        return parse_fetch_impedance(self.query("FETC:IMP?"))


def first_lcr_port(candidates: Iterable[SerialPortCandidate] | None = None) -> SerialPortCandidate | None:
    """Return the first likely LCR port from a candidate list."""

    ports = list(candidates if candidates is not None else available_serial_ports())
    for port in ports:
        if port.is_lcr6000:
            return port
    return ports[0] if ports else None
