"""Pure sweep planning and execution helpers for AC susceptibility runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import sys
import time
from typing import Any, Callable, Protocol, Sequence

from .lcr6000 import (
    Lcr6000Reading,
    Lcr6000Settings,
    build_settings_plan,
)

try:  # pragma: no cover - exercised only with pyserial installed
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover - import guard for docs/tests
    serial = None  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]


SWEEP_HEADER_LINE = (
    "# Timestamp UTC\tElapsed (s)\tAC setting index\tAC setting count\t"
    "LCR function\tLCR frequency (Hz)\tLCR level mode\tLCR level\t"
    "Current set (A)\tCurrent actual (A)\tVoltage actual (V)\t"
    "Sweep direction\tRepeat index\tLCR primary\tLCR secondary\t"
    "LCR monitor1\tLCR monitor2\tLCR comparator\tLCR raw\t"
    "PSU backend\tPSU resource\tPSU status\tError"
)

ESTIMATED_LCR_READ_SECONDS = 0.2


POWER_SUPPLY_PROFILES: dict[str, dict[str, Any]] = {
    "hmp4030": {
        "label": "HMP4030",
        "channel_select": 3,
        "reset_on_start": True,
        "voltage_first": False,
        "current_resolution_a": 0.001,
    },
    "owon_spe6102": {
        "label": "OWON SPE6102",
        "channel_select": 0,
        "reset_on_start": False,
        "voltage_first": True,
        "current_resolution_a": 0.0001,
    },
}


@dataclass(frozen=True)
class CurrentLoopPoint:
    current_a: float
    direction: str


@dataclass(frozen=True)
class SweepEstimate:
    total_settings: int
    total_current_points: int
    total_measurements: int
    estimated_seconds: float


@dataclass(frozen=True)
class PowerSupplyMeasurement:
    current_actual_a: float | None = None
    voltage_actual_v: float | None = None
    status: str = ""
    error: str = ""


@dataclass(frozen=True)
class PowerSupplyCandidate:
    label: str
    resource: str
    backend_id: str
    baudrate: int
    idn: str


@dataclass(frozen=True)
class AcSweepConfig:
    lcr_settings: Sequence[Lcr6000Settings]
    current_points: Sequence[CurrentLoopPoint]
    dwell_s: float
    psu_backend: str
    psu_resource: str
    voltage_limit_v: float
    point_duration_s: float = 10.0
    repeats: int = 1
    lcr_read_attempts: int = 3
    lcr_slow_retry_enabled: bool = True
    lcr_slow_retry_min_frequency_hz: float = 1000.0
    lcr_slow_retry_min_rate_hz: float = 20.0
    lcr_slow_retry_check_s: float = 3.0
    lcr_slow_retry_discard_s: float = 3.0
    lcr_slow_retry_max_attempts: int = 2


@dataclass(frozen=True)
class AcSweepRow:
    timestamp_utc: str
    elapsed_s: float
    setting_index: int
    total_settings: int
    setting: Lcr6000Settings
    current_point: CurrentLoopPoint
    repeat_index: int
    lcr_reading: Lcr6000Reading
    psu_measurement: PowerSupplyMeasurement
    psu_backend: str
    psu_resource: str
    error: str = ""


class LcrDevice(Protocol):
    def configure(self, setting: Lcr6000Settings) -> None: ...

    def fetch_impedance(self) -> Lcr6000Reading: ...


class CurrentSource(Protocol):
    backend_id: str
    resource: str

    def connect(self) -> None: ...

    def initialize(self, *, voltage_limit_v: float) -> None: ...

    def set_current(self, current_a: float) -> None: ...

    def measure(self) -> PowerSupplyMeasurement: ...

    def output_off(self) -> None: ...

    def close(self) -> None: ...


def build_ac_settings_plan(
    *,
    models: Sequence[str],
    frequencies_hz: Sequence[float],
    levels: Sequence[float],
    level_mode: str,
    monitor1: str,
    monitor2: str,
    aperture: str,
) -> list[Lcr6000Settings]:
    plan: list[Lcr6000Settings] = []
    normalized_models = [_normalize_model(model) for model in models]
    if not normalized_models:
        raise ValueError("select at least one LCR model")
    for model in normalized_models:
        plan.extend(
            build_settings_plan(
                frequencies_hz,
                levels,
                level_mode=level_mode,
                function=model,
                monitor1=monitor1,
                monitor2=monitor2,
                aperture=aperture,
            )
        )
    return plan


def _normalize_model(value: str) -> str:
    token = value.strip().lower()
    if token in {"ls-rs", "lsrs", "series"}:
        return "Ls-Rs"
    if token in {"lp-rp", "lprp", "parallel"}:
        return "Lp-Rp"
    raise ValueError(f"unsupported AC model: {value!r}")


def build_current_loop_points(
    *,
    start_mA: float,
    stop_mA: float,
    step_mA: float,
    direction_mode: str,
) -> list[CurrentLoopPoint]:
    start = float(start_mA)
    stop = float(stop_mA)
    step = abs(float(step_mA))
    if step <= 0:
        raise ValueError("current step must be positive")
    if start < 0 or stop < 0:
        raise ValueError("current values must be non-negative")
    mode = direction_mode.strip().lower().replace("_", "-")
    if mode not in {"up", "down", "up-down"}:
        raise ValueError("direction_mode must be 'up', 'down', or 'up-down'")

    up = _inclusive_current_values(min(start, stop), max(start, stop), step)
    if mode == "up":
        return [CurrentLoopPoint(value / 1000.0, "up") for value in up]
    down = list(reversed(up))
    if mode == "down":
        return [CurrentLoopPoint(value / 1000.0, "down") for value in down]
    return (
        [CurrentLoopPoint(value / 1000.0, "up") for value in up]
        + [CurrentLoopPoint(value / 1000.0, "down") for value in down[1:]]
    )


def _inclusive_current_values(start_mA: float, stop_mA: float, step_mA: float) -> list[float]:
    values: list[float] = []
    value = start_mA
    while value < stop_mA:
        values.append(round(value, 9))
        value += step_mA
    if not values or not math.isclose(values[-1], stop_mA, rel_tol=1e-9, abs_tol=1e-9):
        values.append(round(stop_mA, 9))
    return values


def estimate_sweep(
    *,
    lcr_settings: Sequence[Lcr6000Settings],
    current_points: Sequence[CurrentLoopPoint],
    point_duration_s: float,
    dwell_s: float,
) -> SweepEstimate:
    total_points = len(lcr_settings) * len(current_points)
    return SweepEstimate(
        total_settings=len(lcr_settings),
        total_current_points=len(current_points),
        total_measurements=0,
        estimated_seconds=(
            total_points * max(0.0, float(dwell_s))
            + total_points * max(0.0, float(point_duration_s))
        ),
    )


class AcSweepTsvWriter:
    def __init__(self, path: str | Path, config: AcSweepConfig) -> None:
        self.path = Path(path)
        self.config = config
        self._fh: Any | None = None

    def write_metadata(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8", newline="")
        self._write("# AC susceptibility sweep")
        self._write(
            "# "
            f"psu_backend={self.config.psu_backend} "
            f"psu_resource={self.config.psu_resource} "
            f"voltage_limit_v={self.config.voltage_limit_v:g} "
            f"dwell_s={self.config.dwell_s:g} "
            f"point_duration_s={self.config.point_duration_s:g} "
            f"repeats={max(1, int(self.config.repeats))} "
            f"lcr_slow_retry_enabled={self.config.lcr_slow_retry_enabled} "
            f"lcr_slow_retry_min_frequency_hz={self.config.lcr_slow_retry_min_frequency_hz:g} "
            f"lcr_slow_retry_min_rate_hz={self.config.lcr_slow_retry_min_rate_hz:g}"
        )
        for index, setting in enumerate(self.config.lcr_settings, start=1):
            self._write(
                "# "
                f"setting {index}: function={setting.function} "
                f"frequency_hz={setting.frequency_hz:g} "
                f"{setting.level_mode}={setting.level_value:g} "
                f"monitor1={setting.monitor1} monitor2={setting.monitor2} "
                f"aperture={setting.aperture}"
            )
        self._write(SWEEP_HEADER_LINE)

    def write_row(self, row: AcSweepRow) -> None:
        if self._fh is None:
            self.write_metadata()
        measurement = row.psu_measurement
        fields = [
            row.timestamp_utc,
            _format_value(row.elapsed_s),
            str(row.setting_index),
            str(row.total_settings),
            row.setting.function,
            _format_value(row.setting.frequency_hz),
            row.setting.level_mode,
            _format_value(row.setting.level_value),
            _format_value(row.current_point.current_a),
            _format_optional_value(measurement.current_actual_a),
            _format_optional_value(measurement.voltage_actual_v),
            row.current_point.direction,
            str(row.repeat_index),
            _format_optional_value(row.lcr_reading.primary),
            _format_optional_value(row.lcr_reading.secondary),
            _format_optional_value(row.lcr_reading.monitor1),
            _format_optional_value(row.lcr_reading.monitor2),
            row.lcr_reading.comparator,
            row.lcr_reading.raw,
            row.psu_backend,
            row.psu_resource,
            measurement.status,
            row.error or measurement.error,
        ]
        self._write("\t".join(fields))

    def _write(self, text: str) -> None:
        if self._fh is None:
            raise RuntimeError("sweep writer is not open")
        self._fh.write(text + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def run_ac_sweep(
    *,
    config: AcSweepConfig,
    lcr: LcrDevice,
    psu: CurrentSource,
    output_path: str | Path,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[AcSweepRow], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    if not config.lcr_settings:
        raise ValueError("sweep has no LCR settings")
    if not config.current_points:
        raise ValueError("sweep has no current points")
    writer = AcSweepTsvWriter(output_path, config)
    started = time.monotonic()
    try:
        writer.write_metadata()
        psu.connect()
        psu.initialize(voltage_limit_v=config.voltage_limit_v)
        for setting_index, setting in enumerate(config.lcr_settings, start=1):
            if stop_requested is not None and stop_requested():
                raise RuntimeError("AC sweep stopped by user")
            lcr.configure(setting)
            for current_point in config.current_points:
                if stop_requested is not None and stop_requested():
                    raise RuntimeError("AC sweep stopped by user")
                psu.set_current(current_point.current_a)
                dwell = max(0.0, float(config.dwell_s))
                if dwell:
                    sleep(dwell)
                _measure_sweep_point_with_retries(
                    config=config,
                    lcr=lcr,
                    psu=psu,
                    writer=writer,
                    started=started,
                    setting_index=setting_index,
                    setting=setting,
                    current_point=current_point,
                    progress=progress,
                    stop_requested=stop_requested,
                )
    finally:
        active_error = sys.exc_info()[1]
        shutdown_error: Exception | None = None
        try:
            psu.output_off()
        except Exception as exc:
            shutdown_error = exc
        finally:
            try:
                psu.close()
            finally:
                writer.close()
        if shutdown_error is not None and active_error is None:
            raise shutdown_error


def _fetch_lcr_reading(lcr: LcrDevice, *, attempts: int) -> Lcr6000Reading:
    last_empty = False
    for attempt in range(1, max(1, int(attempts)) + 1):
        reading = lcr.fetch_impedance()
        if reading.raw.strip():
            return reading
        last_empty = True
        if attempt < attempts:
            time.sleep(0.25)
    if last_empty:
        raise RuntimeError("LCR returned an empty response during AC sweep")
    raise RuntimeError("LCR did not return a measurement")


def _measure_sweep_point_with_retries(
    *,
    config: AcSweepConfig,
    lcr: LcrDevice,
    psu: CurrentSource,
    writer: AcSweepTsvWriter,
    started: float,
    setting_index: int,
    setting: Lcr6000Settings,
    current_point: CurrentLoopPoint,
    progress: Callable[[AcSweepRow], None] | None,
    stop_requested: Callable[[], bool] | None,
) -> None:
    point_duration = max(0.0, float(config.point_duration_s))
    fallback_repeats = max(1, int(config.repeats))
    max_attempts = max(1, int(config.lcr_slow_retry_max_attempts) + 1)
    attempt = 1
    while True:
        try:
            _measure_sweep_point_once(
                config=config,
                lcr=lcr,
                psu=psu,
                writer=writer,
                started=started,
                setting_index=setting_index,
                setting=setting,
                current_point=current_point,
                progress=progress,
                stop_requested=stop_requested,
                point_duration=point_duration,
                fallback_repeats=fallback_repeats,
                attempt=attempt,
                check_cadence=True,
            )
            return
        except SlowLcrCadenceError as exc:
            if stop_requested is not None and stop_requested():
                raise RuntimeError("AC sweep stopped by user") from exc
            if attempt >= max_attempts:
                _write_sweep_warning_row(
                    config=config,
                    psu=psu,
                    writer=writer,
                    started=started,
                    setting_index=setting_index,
                    setting=setting,
                    current_point=current_point,
                    message=f"slow LCR cadence persisted after {attempt} attempts: {exc}",
                    progress=progress,
                )
                _measure_sweep_point_once(
                    config=config,
                    lcr=lcr,
                    psu=psu,
                    writer=writer,
                    started=started,
                    setting_index=setting_index,
                    setting=setting,
                    current_point=current_point,
                    progress=progress,
                    stop_requested=stop_requested,
                    point_duration=point_duration,
                    fallback_repeats=fallback_repeats,
                    attempt=attempt + 1,
                    check_cadence=False,
                )
                return
            _write_sweep_warning_row(
                config=config,
                psu=psu,
                writer=writer,
                started=started,
                setting_index=setting_index,
                setting=setting,
                current_point=current_point,
                message=f"slow LCR cadence attempt {attempt}: {exc}; reconfiguring and retrying",
                progress=progress,
            )
            lcr.configure(setting)
            _discard_lcr_reads(
                lcr,
                duration_s=max(0.0, float(config.lcr_slow_retry_discard_s)),
                attempts=config.lcr_read_attempts,
                stop_requested=stop_requested,
            )
            attempt += 1


def _measure_sweep_point_once(
    *,
    config: AcSweepConfig,
    lcr: LcrDevice,
    psu: CurrentSource,
    writer: AcSweepTsvWriter,
    started: float,
    setting_index: int,
    setting: Lcr6000Settings,
    current_point: CurrentLoopPoint,
    progress: Callable[[AcSweepRow], None] | None,
    stop_requested: Callable[[], bool] | None,
    point_duration: float,
    fallback_repeats: int,
    attempt: int,
    check_cadence: bool,
) -> None:
    repeat_index = 0
    point_started = time.monotonic()
    first_read_monotonic: float | None = None
    cadence_checked = False
    while True:
        if stop_requested is not None and stop_requested():
            raise RuntimeError("AC sweep stopped by user")
        repeat_index += 1
        reading = _fetch_lcr_reading(lcr, attempts=config.lcr_read_attempts)
        read_monotonic = time.monotonic()
        if first_read_monotonic is None:
            first_read_monotonic = read_monotonic
        psu_measurement = psu.measure()
        row = AcSweepRow(
            timestamp_utc=_timestamp_utc(),
            elapsed_s=read_monotonic - started,
            setting_index=setting_index,
            total_settings=len(config.lcr_settings),
            setting=setting,
            current_point=current_point,
            repeat_index=repeat_index,
            lcr_reading=reading,
            psu_measurement=psu_measurement,
            psu_backend=psu.backend_id,
            psu_resource=psu.resource,
            error=f"retry_attempt={attempt}" if attempt > 1 else "",
        )
        writer.write_row(row)
        if progress is not None:
            progress(row)
        if check_cadence and _should_check_lcr_cadence(config, setting) and not cadence_checked:
            check_elapsed = read_monotonic - point_started
            if check_elapsed >= max(0.1, float(config.lcr_slow_retry_check_s)):
                cadence_checked = True
                active_elapsed = max(1e-9, read_monotonic - first_read_monotonic)
                completed_intervals = max(1, repeat_index - 1)
                rate_hz = completed_intervals / active_elapsed
                if rate_hz < float(config.lcr_slow_retry_min_rate_hz):
                    raise SlowLcrCadenceError(rate_hz=rate_hz, reads=repeat_index)
        if point_duration > 0.0:
            if read_monotonic - point_started >= point_duration:
                break
        elif repeat_index >= fallback_repeats:
            break


def _should_check_lcr_cadence(config: AcSweepConfig, setting: Lcr6000Settings) -> bool:
    if not bool(config.lcr_slow_retry_enabled):
        return False
    if setting.aperture.strip().upper() != "FAST":
        return False
    return float(setting.frequency_hz) >= float(config.lcr_slow_retry_min_frequency_hz)


class SlowLcrCadenceError(RuntimeError):
    def __init__(self, *, rate_hz: float, reads: int) -> None:
        super().__init__(f"{rate_hz:.3g} Hz from {reads} reads")
        self.rate_hz = rate_hz
        self.reads = reads


def _discard_lcr_reads(
    lcr: LcrDevice,
    *,
    duration_s: float,
    attempts: int,
    stop_requested: Callable[[], bool] | None,
) -> None:
    deadline = time.monotonic() + max(0.0, float(duration_s))
    while time.monotonic() < deadline:
        if stop_requested is not None and stop_requested():
            raise RuntimeError("AC sweep stopped by user")
        _fetch_lcr_reading(lcr, attempts=attempts)


def _write_sweep_warning_row(
    *,
    config: AcSweepConfig,
    psu: CurrentSource,
    writer: AcSweepTsvWriter,
    started: float,
    setting_index: int,
    setting: Lcr6000Settings,
    current_point: CurrentLoopPoint,
    message: str,
    progress: Callable[[AcSweepRow], None] | None,
) -> None:
    row = AcSweepRow(
        timestamp_utc=_timestamp_utc(),
        elapsed_s=time.monotonic() - started,
        setting_index=setting_index,
        total_settings=len(config.lcr_settings),
        setting=setting,
        current_point=current_point,
        repeat_index=0,
        lcr_reading=Lcr6000Reading(
            timestamp_utc=_timestamp_utc(),
            raw="",
            primary=None,
            secondary=None,
            monitor1=None,
            monitor2=None,
            comparator="",
        ),
        psu_measurement=PowerSupplyMeasurement(status="WARN"),
        psu_backend=psu.backend_id,
        psu_resource=psu.resource,
        error=message,
    )
    writer.write_row(row)
    if progress is not None:
        progress(row)


class SerialScpiCurrentSource:
    def __init__(
        self,
        *,
        backend_id: str,
        resource: str,
        baudrate: int,
        voltage_limit_v: float,
    ) -> None:
        self.backend_id = backend_id if backend_id in POWER_SUPPLY_PROFILES else "hmp4030"
        self.resource = resource.strip()
        self.baudrate = int(baudrate)
        self.voltage_limit_v = float(voltage_limit_v)
        self.profile = dict(POWER_SUPPLY_PROFILES[self.backend_id])
        self._serial: Any | None = None

    def connect(self) -> None:
        if serial is None:
            raise RuntimeError("pyserial is not available")
        if not self.resource:
            raise RuntimeError("select a power-supply serial port")
        if self._serial is not None and getattr(self._serial, "is_open", False):
            return
        self._serial = serial.Serial(self.resource, baudrate=self.baudrate, timeout=0.7, write_timeout=0.7)
        self._serial.rts = False
        self._serial.dtr = False
        time.sleep(0.08)

    def initialize(self, *, voltage_limit_v: float) -> None:
        if self.profile.get("reset_on_start", False):
            self.command("*RST", settle_s=1.2)
        self.select_channel()
        voltage = max(0.0, float(voltage_limit_v))
        if bool(self.profile.get("voltage_first", False)):
            self.command(f"VOLT {voltage:.3f}")
            self.command("CURR 0.0000")
        else:
            self.command("CURR 0.0000")
            self.command(f"VOLT {voltage:.3f}")
        self.command("OUTP ON")

    def selected_channel(self) -> int:
        return int(self.profile.get("channel_select", 0) or 0)

    def select_channel(self) -> None:
        channel = self.selected_channel()
        if channel > 0:
            self.command(f"INST:NSEL {channel}")

    def set_current(self, current_a: float) -> None:
        self.select_channel()
        resolution = max(1e-6, float(self.profile.get("current_resolution_a", 0.001)))
        current = max(0.0, round(float(current_a) / resolution) * resolution)
        self.command(f"CURR {current:.4f}", settle_s=0.03)

    def measure(self) -> PowerSupplyMeasurement:
        self.select_channel()
        voltage = self.query_float("MEAS:VOLT?")
        current = self.query_float("MEAS:CURR?")
        return PowerSupplyMeasurement(current_actual_a=current, voltage_actual_v=voltage, status="OK")

    def output_off(self) -> None:
        try:
            self.select_channel()
        except Exception:
            pass
        self.command("CURR 0.0000", settle_s=0.03)
        self.command("OUTP OFF", settle_s=0.03)

    def close(self) -> None:
        port = self._serial
        self._serial = None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    def command(self, command: str, *, settle_s: float = 0.08) -> None:
        port = self._require_port()
        payload = command.rstrip() + "\n"
        try:
            port.reset_input_buffer()
        except Exception:
            pass
        port.write(payload.encode("ascii", errors="ignore"))
        port.flush()
        if settle_s:
            time.sleep(settle_s)

    def query_float(self, command: str) -> float | None:
        self.command(command)
        return _parse_first_float(self._read_line())

    def _read_line(self, *, timeout_s: float = 0.7) -> str:
        port = self._require_port()
        deadline = time.monotonic() + max(0.1, timeout_s)
        chunks: list[bytes] = []
        while time.monotonic() < deadline:
            line = port.readline()
            if line:
                chunks.append(line)
                if line.endswith(b"\n") or line.endswith(b"\r"):
                    break
        return b"".join(chunks).decode("ascii", errors="ignore").strip()

    def _require_port(self) -> Any:
        if self._serial is None or not bool(getattr(self._serial, "is_open", False)):
            raise RuntimeError("power supply is not connected")
        return self._serial


def available_power_supply_ports() -> list[tuple[str, str]]:
    if list_ports is None:
        return []
    ports: list[tuple[str, str]] = []
    for info in list_ports.comports():
        device = str(info.device)
        description = str(getattr(info, "description", "") or "")
        label = device if not description else f"{device} - {description}"
        ports.append((label, device))
    return ports


def classify_power_supply_idn(idn: str) -> str | None:
    upper = idn.upper()
    if "LCR-" in upper or "LCR6000" in upper or "LCR-6000" in upper:
        return None
    if "HMP4030" in upper or "HAMEG" in upper or "ROHDE" in upper:
        return "hmp4030"
    if "OWON" in upper or "SPE6102" in upper or "SPE" in upper:
        return "owon_spe6102"
    return None


def detect_power_supply_candidates(
    *,
    ports: Sequence[Any] | None = None,
    serial_factory: Any | None = None,
    baudrates: Sequence[int] = (9600, 115200),
) -> list[PowerSupplyCandidate]:
    if list_ports is None and ports is None:
        return []
    if serial is None and serial_factory is None:
        return []
    port_infos = list(ports if ports is not None else list_ports.comports())
    serial_ctor = serial_factory if serial_factory is not None else serial.Serial
    candidates: list[PowerSupplyCandidate] = []
    for info in port_infos:
        device = str(getattr(info, "device", info))
        description = str(getattr(info, "description", "") or "")
        if _description_looks_like_lcr(description):
            continue
        for baudrate in baudrates:
            idn = _query_idn(device, int(baudrate), serial_ctor)
            backend_id = classify_power_supply_idn(idn)
            if backend_id is None:
                continue
            label = device if not description else f"{device} - {description}"
            profile = POWER_SUPPLY_PROFILES.get(backend_id, {})
            profile_label = str(profile.get("label", backend_id))
            candidates.append(
                PowerSupplyCandidate(
                    label=f"{label} [{profile_label}]",
                    resource=device,
                    backend_id=backend_id,
                    baudrate=int(baudrate),
                    idn=idn.strip(),
                )
            )
            break
    return candidates


def _description_looks_like_lcr(description: str) -> bool:
    upper = description.upper()
    return "LCR" in upper and "METER" in upper


def _query_idn(device: str, baudrate: int, serial_ctor: Any) -> str:
    for command in (b"*IDN?\n", b"*IDN?\r\n", b"*IDN?\r"):
        port = None
        try:
            port = serial_ctor(device, baudrate=baudrate, timeout=0.35, write_timeout=0.35)
            try:
                port.rts = False
                port.dtr = False
            except Exception:
                pass
            time.sleep(0.03)
            try:
                port.reset_input_buffer()
            except Exception:
                pass
            port.write(command)
            port.flush()
            reply = port.readline().decode("ascii", errors="ignore").strip()
            if reply:
                return reply
        except Exception:
            pass
        finally:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass
    return ""


def _parse_first_float(text: str) -> float | None:
    for token in text.replace(",", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    return None


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _format_optional_value(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return _format_value(value)


def _format_value(value: float) -> str:
    if not math.isfinite(float(value)):
        return ""
    return f"{float(value):.12g}"
