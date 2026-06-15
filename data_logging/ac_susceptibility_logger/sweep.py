"""Pure sweep planning and execution helpers for AC susceptibility runs."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import traceback
from typing import Any, Callable, Protocol, Sequence

from data_logging.shared_power_supply.broker import ROLE_AC_SUSCEPTIBILITY
from data_logging.shared_power_supply.protocol import BrokerJsonClient

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
    "PSU resistance (Ohm)\tPSU power (W)\t"
    "Sweep direction\tRepeat index\tLCR primary\tLCR secondary\t"
    "LCR monitor1\tLCR monitor2\tLCR comparator\tLCR raw\t"
    "PSU backend\tPSU resource\tPSU status\tError"
)

ESTIMATED_LCR_READ_SECONDS = 0.2


def normalize_serial_resource(resource: str) -> str:
    """Return a pyserial-friendly Windows serial resource name."""
    text = str(resource or "").strip().strip("'\"")
    match = re.search(r"COM\d+", text, flags=re.IGNORECASE)
    if match:
        return match.group(0).upper()
    return text


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
        "max_voltage_v": 61.0,
    },
    "shared_hmp_broker": {
        "label": "Shared HMP broker",
        "channel_select": 1,
        "reset_on_start": False,
        "voltage_first": False,
        "current_resolution_a": 0.001,
        "shared_broker": True,
    },
}


def effective_power_supply_voltage_limit(backend_id: str, voltage_limit_v: float) -> float:
    """Return the voltage setpoint that will actually be sent to the PSU."""
    voltage = max(0.0, float(voltage_limit_v))
    profile = POWER_SUPPLY_PROFILES.get(backend_id, {})
    max_voltage = profile.get("max_voltage_v")
    if max_voltage is not None:
        voltage = min(voltage, float(max_voltage))
    return voltage


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

    @property
    def resistance_ohm(self) -> float | None:
        current = self.current_actual_a
        voltage = self.voltage_actual_v
        if current is None or voltage is None:
            return None
        if not math.isfinite(current) or not math.isfinite(voltage) or abs(current) < 1e-12:
            return None
        return voltage / current

    @property
    def power_w(self) -> float | None:
        current = self.current_actual_a
        voltage = self.voltage_actual_v
        if current is None or voltage is None:
            return None
        if not math.isfinite(current) or not math.isfinite(voltage):
            return None
        return voltage * current


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
    psu_current_ready_timeout_s: float = 3.0
    psu_current_ready_poll_s: float = 0.2
    psu_current_ready_tolerance_a: float = 0.0015
    psu_current_feedback_enabled: bool = True
    psu_current_feedback_resistance_ohm: float = 390.0
    psu_measure_attempts: int = 3
    psu_missing_readback_warn: bool = True
    shared_broker_host: str = "127.0.0.1"
    shared_broker_port: int = 8765
    shared_broker_channel: int | None = None
    lcr_continuous_log_enabled: bool = False
    lcr_continuous_log_path: str | None = None
    lcr_continuous_log_cadence_s: float = 1.0
    lcr_continuous_log_aggregate_s: float = 1.0
    lcr_continuous_log_max_rows_per_point: int = 120
    lcr_correction: dict[str, Any] | None = None

    @property
    def uses_shared_broker(self) -> bool:
        return self.psu_backend == "shared_hmp_broker"


@dataclass(frozen=True)
class AcResumePlan:
    config: AcSweepConfig
    completed_setting_indices: list[int]
    partial_setting_indices: list[int]
    summary: str
    run_statuses: list["AcRunStatusSummary"] = field(default_factory=list)


@dataclass(frozen=True)
class AcRunStatusSummary:
    output_path: Path
    status_path: Path
    status: str
    phase: str
    updated_utc: str
    stop_reason: str
    rows_written: int
    is_unclean: bool
    stale: bool
    heartbeat_age_s: float | None
    lcr_debug_path: Path | None = None
    lcr_debug_closed: bool | None = None
    resume_note: str = ""


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
    current_point_index: int = 1
    total_current_points: int = 1
    point_elapsed_s: float = 0.0
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

    def set_voltage_limit(self, voltage_v: float) -> None: ...

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
    include_zero: bool = False,
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
        points = [CurrentLoopPoint(value / 1000.0, "up") for value in up]
        return _with_optional_zero_reference(points, include_zero=include_zero)
    down = list(reversed(up))
    if mode == "down":
        points = [CurrentLoopPoint(value / 1000.0, "down") for value in down]
        return _with_optional_zero_reference(points, include_zero=include_zero)
    points = (
        [CurrentLoopPoint(value / 1000.0, "up") for value in up]
        + [CurrentLoopPoint(value / 1000.0, "down") for value in down[1:]]
    )
    return _with_optional_zero_reference(points, include_zero=include_zero)


def _with_optional_zero_reference(
    points: Sequence[CurrentLoopPoint],
    *,
    include_zero: bool,
) -> list[CurrentLoopPoint]:
    values = list(points)
    if not include_zero:
        return values
    if values and math.isclose(values[0].current_a, 0.0, rel_tol=0.0, abs_tol=1e-12):
        return values
    return [CurrentLoopPoint(0.0, "zero")] + values


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


def build_resume_plan(config: AcSweepConfig, completed_paths: Sequence[str | Path]) -> AcResumePlan:
    """Return a config that skips settings already completed in previous TSVs.

    Resume is intentionally setting-granular: a partial setting is measured
    again from its first current point so each setting remains internally
    complete in one output file.
    """
    planned_settings = list(config.lcr_settings)
    planned_current_points = list(config.current_points)
    required_points = {_current_point_key(point) for point in planned_current_points}
    if not required_points:
        raise ValueError("resume requires at least one planned current point")
    planned_keys = {_lcr_setting_key(setting) for setting in planned_settings}
    observed_by_setting: dict[tuple[Any, ...], set[tuple[float, str]]] = {}
    run_statuses = [classify_ac_run_status(path) for path in completed_paths]
    for path in completed_paths:
        snapshot_settings, observed = _read_completed_sweep_observations(Path(path))
        for setting_index, points in observed.items():
            if setting_index < 1 or setting_index > len(snapshot_settings):
                continue
            setting_key = _lcr_setting_key(snapshot_settings[setting_index - 1])
            if setting_key not in planned_keys:
                continue
            observed_by_setting.setdefault(setting_key, set()).update(points)

    completed_indices: list[int] = []
    partial_indices: list[int] = []
    remaining_settings: list[Lcr6000Settings] = []
    for index, setting in enumerate(planned_settings, start=1):
        setting_key = _lcr_setting_key(setting)
        seen_points = observed_by_setting.get(setting_key, set())
        if required_points.issubset(seen_points):
            completed_indices.append(index)
            continue
        if seen_points:
            partial_indices.append(index)
        remaining_settings.append(setting)

    summary = (
        f"{len(completed_indices)} complete setting(s) skipped; "
        f"{len(partial_indices)} partial setting(s) will be redone; "
        f"{len(remaining_settings)} setting(s) remain."
    )
    unclean = [item for item in run_statuses if item.is_unclean]
    if unclean:
        summary += f" {len(unclean)} previous run(s) ended uncleanly; partial settings will not be skipped."
    return AcResumePlan(
        config=replace(config, lcr_settings=remaining_settings),
        completed_setting_indices=completed_indices,
        partial_setting_indices=partial_indices,
        summary=summary,
        run_statuses=run_statuses,
    )


def _read_completed_sweep_observations(path: Path) -> tuple[list[Lcr6000Settings], dict[int, set[tuple[float, str]]]]:
    config_settings: list[Lcr6000Settings] | None = None
    header: list[str] | None = None
    observed: dict[int, set[tuple[float, str]]] = {}
    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if line.startswith("# config_json="):
                payload = json.loads(line.split("=", 1)[1])
                config_settings = [
                    _lcr_setting_from_snapshot(item)
                    for item in payload.get("lcr_settings", [])
                    if isinstance(item, dict)
                ]
                continue
            if line.startswith("# Timestamp UTC\t"):
                header = line[2:].split("\t")
                continue
            if not line or line.startswith("#") or header is None:
                continue
            cells = line.split("\t")
            row = _row_mapping(header, cells)
            try:
                setting_index = int(row["AC setting index"])
                current_a = float(row["Current set (A)"])
                direction = row["Sweep direction"].strip().lower()
                int(row["Repeat index"])
            except (KeyError, TypeError, ValueError):
                continue
            if direction:
                observed.setdefault(setting_index, set()).add((round(current_a, 9), direction))
    if config_settings is None:
        raise ValueError(f"{path} does not contain AC sweep metadata")
    return config_settings, observed


def _row_mapping(header: Sequence[str], cells: Sequence[str]) -> dict[str, str]:
    return {name: cells[index] if index < len(cells) else "" for index, name in enumerate(header)}


def _lcr_setting_from_snapshot(item: dict[str, Any]) -> Lcr6000Settings:
    return Lcr6000Settings(
        frequency_hz=float(item.get("frequency_hz", 0.0)),
        level_value=float(item.get("level_value", 0.0)),
        level_mode=str(item.get("level_mode", "voltage")),
        function=str(item.get("function", "Ls-Rs")),
        monitor1=str(item.get("monitor1", "Z")),
        monitor2=str(item.get("monitor2", "IAC")),
        aperture=str(item.get("aperture", "FAST")),
        source_resistance_ohm=int(item.get("source_resistance_ohm", 30)),
        auto_lcz_enabled=bool(item.get("auto_lcz_enabled", False)),
        alc_enabled=bool(item.get("alc_enabled", True)),
        comparator_enabled=bool(item.get("comparator_enabled", False)),
    )


def _lcr_setting_key(setting: Lcr6000Settings) -> tuple[Any, ...]:
    return (
        str(setting.function),
        round(float(setting.frequency_hz), 9),
        str(setting.level_mode),
        round(float(setting.level_value), 12),
        str(setting.monitor1),
        str(setting.monitor2),
        str(setting.aperture),
        int(setting.source_resistance_ohm),
        bool(setting.auto_lcz_enabled),
        bool(setting.alc_enabled),
        bool(setting.comparator_enabled),
    )


def _current_point_key(point: CurrentLoopPoint) -> tuple[float, str]:
    return (round(float(point.current_a), 9), str(point.direction).strip().lower())


RUN_STATUS_SCHEMA = "ac_susceptibility_run_status_v1"


def ac_run_status_path_for_output(output_path: str | Path) -> Path:
    path = Path(output_path)
    return path.with_name(f"{path.stem}_run_status.json")


def ac_lcr_debug_path_for_output(output_path: str | Path) -> Path:
    path = Path(output_path)
    return path.with_name(f"{path.stem}_lcr_debug.jsonl")


def classify_ac_run_status(
    output_path: str | Path,
    *,
    stale_after_s: float = 300.0,
    now_utc: datetime | None = None,
) -> AcRunStatusSummary:
    """Classify whether a previous AC sweep ended cleanly enough to resume from.

    The resume policy remains setting-granular. This helper only surfaces the
    previous run state so the UI/automation can warn when the prior process
    disappeared before writing a final checkpoint.
    """
    output = Path(output_path)
    status_path = ac_run_status_path_for_output(output)
    debug_path = ac_lcr_debug_path_for_output(output)
    payload: dict[str, Any] = {}
    if status_path.exists():
        try:
            loaded = json.loads(status_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}
    updated_utc = str(payload.get("updated_utc") or payload.get("created_utc") or "")
    heartbeat_age_s = _heartbeat_age_s(updated_utc, now_utc=now_utc)
    status = str(payload.get("status") or "unknown").strip().lower() or "unknown"
    stale = status == "running" and heartbeat_age_s is not None and heartbeat_age_s > max(1.0, float(stale_after_s))
    lcr_closed = _lcr_debug_has_closed_event(debug_path) if debug_path.exists() else None
    is_unclean = False
    resume_note = ""
    if stale:
        status = "unclean_stale_heartbeat"
        is_unclean = True
        resume_note = "previous run heartbeat is stale; any partial setting will be repeated"
    elif status in {"failed", "stopped", "completed"}:
        is_unclean = False
    elif status == "running":
        is_unclean = True
        resume_note = "previous run still appears running; verify before continuing"
    elif lcr_closed is False:
        status = "unclean_missing_lcr_closed_marker"
        is_unclean = True
        resume_note = "LCR debug stream has no closed marker; any partial setting will be repeated"
    elif not payload:
        status = "unknown_no_run_status"
        is_unclean = lcr_closed is False
        if is_unclean:
            resume_note = "no run status file and LCR debug stream has no closed marker"
    return AcRunStatusSummary(
        output_path=output,
        status_path=status_path,
        status=status,
        phase=str(payload.get("phase") or ""),
        updated_utc=updated_utc,
        stop_reason=str(payload.get("stop_reason") or ""),
        rows_written=int(payload.get("rows_written") or 0),
        is_unclean=is_unclean,
        stale=stale,
        heartbeat_age_s=heartbeat_age_s,
        lcr_debug_path=debug_path if debug_path.exists() else None,
        lcr_debug_closed=lcr_closed,
        resume_note=resume_note,
    )


def record_ac_run_exception(
    output_path: str | Path,
    exc: BaseException,
    *,
    phase: str = "worker_exception",
) -> None:
    """Patch the status sidecar with a late worker/UI exception."""
    path = ac_run_status_path_for_output(output_path)
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}
    if not payload:
        payload = {
            "schema": RUN_STATUS_SCHEMA,
            "created_utc": _timestamp_utc(),
            "output_path": str(Path(output_path)),
            "pid": os.getpid(),
        }
    payload.update(
        {
            "status": "failed",
            "phase": phase,
            "updated_utc": _timestamp_utc(),
            "stop_reason": str(exc),
            "exception": _exception_payload(exc),
        }
    )
    _write_json_atomic(path, payload)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _heartbeat_age_s(updated_utc: str, *, now_utc: datetime | None) -> float | None:
    if not updated_utc:
        return None
    try:
        updated = datetime.fromisoformat(updated_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now.astimezone(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds())


def _lcr_debug_has_closed_event(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("event") == "closed":
                    return True
    except OSError:
        return False
    return False


def _lcr_setting_snapshot(setting: Lcr6000Settings) -> dict[str, Any]:
    return {
        "function": setting.function,
        "frequency_hz": float(setting.frequency_hz),
        "level_mode": setting.level_mode,
        "level_value": float(setting.level_value),
        "monitor1": setting.monitor1,
        "monitor2": setting.monitor2,
        "aperture": setting.aperture,
    }


def _psu_runtime_snapshot(psu: CurrentSource) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "backend": getattr(psu, "backend_id", ""),
        "resource": getattr(psu, "resource", ""),
    }
    if getattr(psu, "backend_id", "") == "shared_hmp_broker":
        snapshot["shared_broker"] = {
            "enabled": True,
            "host": getattr(psu, "host", ""),
            "port": getattr(psu, "port", None),
            "channel": getattr(psu, "channel", None),
            "lease_id": getattr(psu, "lease_id", None),
            "owner": getattr(psu, "owner", ""),
        }
    return snapshot


def _exception_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }


def _status_for_exception(exc: BaseException | None) -> str:
    if exc is None:
        return "completed"
    if str(exc).strip() == "AC sweep stopped by user":
        return "stopped"
    return "failed"


class AcRunStatusWriter:
    """Durable heartbeat/checkpoint sidecar for long AC sweeps."""

    def __init__(
        self,
        *,
        output_path: str | Path,
        config: AcSweepConfig,
        continuous_log_path: str | Path | None,
    ) -> None:
        self.output_path = Path(output_path)
        self.path = ac_run_status_path_for_output(self.output_path)
        self.config = config
        self.continuous_log_path = None if continuous_log_path is None else Path(continuous_log_path)
        self.created_utc = _timestamp_utc()
        self.rows_written = 0
        self._payload: dict[str, Any] = self._base_payload(status="running", phase="starting")

    def open(self, *, psu: CurrentSource | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.update(phase="starting", psu=psu)

    def update(
        self,
        *,
        phase: str,
        setting_index: int | None = None,
        total_settings: int | None = None,
        setting: Lcr6000Settings | None = None,
        current_point: CurrentLoopPoint | None = None,
        current_point_index: int | None = None,
        total_current_points: int | None = None,
        psu: CurrentSource | None = None,
        status: str = "running",
        stop_reason: str = "",
    ) -> None:
        self._payload.update(
            {
                "status": status,
                "phase": phase,
                "updated_utc": _timestamp_utc(),
                "rows_written": self.rows_written,
                "stop_reason": stop_reason,
            }
        )
        if setting_index is not None:
            self._payload["setting_index"] = int(setting_index)
        if total_settings is not None:
            self._payload["setting_count"] = int(total_settings)
        if current_point_index is not None:
            self._payload["current_point_index"] = int(current_point_index)
        if total_current_points is not None:
            self._payload["current_point_count"] = int(total_current_points)
        if setting is not None:
            self._payload["lcr_setting"] = _lcr_setting_snapshot(setting)
        if current_point is not None:
            self._payload["current_point"] = {
                "current_set_a": float(current_point.current_a),
                "current_set_mA": float(current_point.current_a) * 1000.0,
                "direction": str(current_point.direction),
            }
        if psu is not None:
            self._payload["psu_runtime"] = _psu_runtime_snapshot(psu)
        _write_json_atomic(self.path, self._payload)

    def record_row(self, row: AcSweepRow) -> None:
        self.rows_written += 1
        self.update(
            phase="measure",
            setting_index=row.setting_index,
            total_settings=row.total_settings,
            setting=row.setting,
            current_point=row.current_point,
            current_point_index=row.current_point_index,
            total_current_points=row.total_current_points,
            status="running",
        )
        self._payload["last_row_timestamp_utc"] = row.timestamp_utc
        self._payload["last_row_error"] = row.error or row.psu_measurement.error
        _write_json_atomic(self.path, self._payload)

    def record_exception(self, exc: BaseException, *, phase: str = "exception") -> None:
        self._payload["exception"] = _exception_payload(exc)
        self.update(phase=phase, status=_status_for_exception(exc), stop_reason=str(exc))

    def close(
        self,
        *,
        status: str,
        stop_reason: str,
        psu: CurrentSource | None = None,
        cleanup_errors: Sequence[str] = (),
    ) -> None:
        self._payload["closed_utc"] = _timestamp_utc()
        if cleanup_errors:
            self._payload["cleanup_errors"] = list(cleanup_errors)
        self.update(phase="closed", status=status, stop_reason=stop_reason, psu=psu)
        self._payload["closed_utc"] = self._payload["updated_utc"]
        _write_json_atomic(self.path, self._payload)

    def _base_payload(self, *, status: str, phase: str) -> dict[str, Any]:
        return {
            "schema": RUN_STATUS_SCHEMA,
            "created_utc": self.created_utc,
            "updated_utc": self.created_utc,
            "pid": os.getpid(),
            "status": status,
            "phase": phase,
            "output_path": str(self.output_path),
            "lcr_continuous_log_path": None if self.continuous_log_path is None else str(self.continuous_log_path),
            "rows_written": 0,
            "setting_index": None,
            "setting_count": len(self.config.lcr_settings),
            "current_point_index": None,
            "current_point_count": len(self.config.current_points),
            "stop_reason": "",
            "psu": {
                "backend": self.config.psu_backend,
                "resource": self.config.psu_resource,
                "voltage_limit_v": float(self.config.voltage_limit_v),
                "shared_broker": {
                    "enabled": self.config.uses_shared_broker,
                    "host": str(self.config.shared_broker_host or "127.0.0.1"),
                    "port": int(self.config.shared_broker_port),
                    "channel": self.config.shared_broker_channel,
                },
            },
            "lcr_continuous_log": {
                "enabled": bool(self.config.lcr_continuous_log_enabled),
                "cadence_s": float(self.config.lcr_continuous_log_cadence_s),
                "aggregate_s": float(self.config.lcr_continuous_log_aggregate_s),
                "max_rows_per_point": int(self.config.lcr_continuous_log_max_rows_per_point),
            },
        }


class AcSweepTsvWriter:
    def __init__(self, path: str | Path, config: AcSweepConfig) -> None:
        self.path = Path(path)
        self.config = config
        self._fh: Any | None = None

    def write_metadata(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8", newline="")
        self._write("# AC susceptibility sweep")
        self._write(f"# config_json={self._settings_snapshot_json()}")
        self._write(
            "# "
            f"psu_backend={self.config.psu_backend} "
            f"psu_resource={self.config.psu_resource} "
            f"voltage_limit_v={self.config.voltage_limit_v:g} "
            f"shared_broker_used={self.config.uses_shared_broker} "
            f"shared_broker_channel={self.config.shared_broker_channel or ''} "
            f"dwell_s={self.config.dwell_s:g} "
            f"point_duration_s={self.config.point_duration_s:g} "
            f"repeats={max(1, int(self.config.repeats))} "
            f"psu_current_feedback_enabled={self.config.psu_current_feedback_enabled} "
            f"psu_current_ready_tolerance_a={self.config.psu_current_ready_tolerance_a:g} "
            f"psu_current_feedback_resistance_ohm={self.config.psu_current_feedback_resistance_ohm:g} "
            f"lcr_slow_retry_enabled={self.config.lcr_slow_retry_enabled} "
            f"lcr_slow_retry_min_frequency_hz={self.config.lcr_slow_retry_min_frequency_hz:g} "
            f"lcr_slow_retry_min_rate_hz={self.config.lcr_slow_retry_min_rate_hz:g} "
            f"lcr_continuous_log_enabled={self.config.lcr_continuous_log_enabled} "
            f"lcr_continuous_log_path={self.config.lcr_continuous_log_path or ''}"
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

    def _settings_snapshot_json(self) -> str:
        snapshot = {
            "run_type": "microwire_current_sweep",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "psu": {
                "backend": self.config.psu_backend,
                "resource": self.config.psu_resource,
                "voltage_limit_v": float(self.config.voltage_limit_v),
                "shared_broker": {
                    "enabled": self.config.uses_shared_broker,
                    "host": str(self.config.shared_broker_host or "127.0.0.1"),
                    "port": int(self.config.shared_broker_port),
                    "channel": self.config.shared_broker_channel,
                },
            },
            "acquisition": {
                "dwell_s": float(self.config.dwell_s),
                "point_duration_s": float(self.config.point_duration_s),
                "repeats": max(1, int(self.config.repeats)),
                "lcr_read_attempts": int(self.config.lcr_read_attempts),
                "psu_measure_attempts": int(self.config.psu_measure_attempts),
                "psu_current_ready_timeout_s": float(self.config.psu_current_ready_timeout_s),
                "psu_current_ready_poll_s": float(self.config.psu_current_ready_poll_s),
                "psu_current_ready_tolerance_a": float(self.config.psu_current_ready_tolerance_a),
                "psu_current_feedback_enabled": bool(self.config.psu_current_feedback_enabled),
                "psu_current_feedback_resistance_ohm": float(self.config.psu_current_feedback_resistance_ohm),
                "psu_missing_readback_warn": bool(self.config.psu_missing_readback_warn),
            },
            "lcr_continuous_log": {
                "enabled": bool(self.config.lcr_continuous_log_enabled),
                "path": self.config.lcr_continuous_log_path,
                "cadence_s": float(self.config.lcr_continuous_log_cadence_s),
                "aggregate_s": float(self.config.lcr_continuous_log_aggregate_s),
                "max_rows_per_point": int(self.config.lcr_continuous_log_max_rows_per_point),
            },
            "lcr_correction": self.config.lcr_correction or {
                "open_enabled": None,
                "short_enabled": None,
                "checked_utc": "",
                "source": "not_checked",
            },
            "lcr_slow_retry": {
                "enabled": bool(self.config.lcr_slow_retry_enabled),
                "min_frequency_hz": float(self.config.lcr_slow_retry_min_frequency_hz),
                "min_rate_hz": float(self.config.lcr_slow_retry_min_rate_hz),
                "check_s": float(self.config.lcr_slow_retry_check_s),
                "discard_s": float(self.config.lcr_slow_retry_discard_s),
                "max_attempts": int(self.config.lcr_slow_retry_max_attempts),
            },
            "current_loop": {
                "points_mA": [round(point.current_a * 1000.0, 9) for point in self.config.current_points],
                "directions": [point.direction for point in self.config.current_points],
            },
            "lcr_settings": [
                {
                    "function": setting.function,
                    "frequency_hz": float(setting.frequency_hz),
                    "level_mode": setting.level_mode,
                    "level_value": float(setting.level_value),
                    "monitor1": setting.monitor1,
                    "monitor2": setting.monitor2,
                    "aperture": setting.aperture,
                }
                for setting in self.config.lcr_settings
            ],
        }
        return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

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
            _format_optional_value(measurement.resistance_ohm),
            _format_optional_value(measurement.power_w),
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


class LcrContinuousJsonlWriter:
    """Bounded sidecar stream for transition-oriented LCR diagnostics."""

    def __init__(self, path: str | Path, config: AcSweepConfig) -> None:
        self.path = Path(path)
        self.config = config
        self._fh: Any | None = None
        self._rows_written = 0
        self._point_counts: dict[tuple[object, ...], int] = {}
        self._point_last_elapsed: dict[tuple[object, ...], float] = {}

    def open(self) -> None:
        if self._fh is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")
        self._write(
            {
                "event": "metadata",
                "created_utc": _timestamp_utc(),
                "main_output_path": None,
                "cadence_s": max(0.1, float(self.config.lcr_continuous_log_cadence_s)),
                "aggregate_s": max(0.1, float(self.config.lcr_continuous_log_aggregate_s)),
                "max_rows_per_point": max(1, int(self.config.lcr_continuous_log_max_rows_per_point)),
                "psu": {
                    "backend": self.config.psu_backend,
                    "resource": self.config.psu_resource,
                    "shared_broker": {
                        "enabled": self.config.uses_shared_broker,
                        "host": str(self.config.shared_broker_host or "127.0.0.1"),
                        "port": int(self.config.shared_broker_port),
                        "channel": self.config.shared_broker_channel,
                    },
                },
            }
        )

    def write_sample(
        self,
        *,
        phase: str,
        started: float,
        point_started: float,
        setting_index: int,
        total_settings: int,
        setting: Lcr6000Settings,
        current_point: CurrentLoopPoint,
        current_point_index: int,
        total_current_points: int,
        repeat_index: int,
        reading: Lcr6000Reading,
        psu_measurement: PowerSupplyMeasurement | None = None,
    ) -> None:
        if self._fh is None:
            self.open()
        read_monotonic = time.monotonic()
        point_key = (
            phase,
            setting_index,
            current_point_index,
            current_point.direction,
            round(float(current_point.current_a), 12),
        )
        point_elapsed_s = read_monotonic - point_started
        if not self._sample_allowed(point_key, point_elapsed_s):
            return
        payload: dict[str, object] = {
            "event": "lcr_sample",
            "timestamp_utc": reading.timestamp_utc or _timestamp_utc(),
            "elapsed_s": read_monotonic - started,
            "point_elapsed_s": point_elapsed_s,
            "phase": phase,
            "setting_index": setting_index,
            "total_settings": total_settings,
            "current_point_index": current_point_index,
            "total_current_points": total_current_points,
            "repeat_index": repeat_index,
            "lcr": {
                "function": setting.function,
                "frequency_hz": float(setting.frequency_hz),
                "level_mode": setting.level_mode,
                "level_value": float(setting.level_value),
                "primary": reading.primary,
                "secondary": reading.secondary,
                "monitor1": reading.monitor1,
                "monitor2": reading.monitor2,
                "comparator": reading.comparator,
                "raw": reading.raw,
            },
            "current_loop": {
                "current_set_a": float(current_point.current_a),
                "direction": current_point.direction,
            },
            "aggregation": {
                "mode": "cadence_sample",
                "window_s": max(0.1, float(self.config.lcr_continuous_log_aggregate_s)),
                "sample_count": 1,
            },
        }
        if psu_measurement is not None:
            payload["psu_readback"] = {
                "current_actual_a": psu_measurement.current_actual_a,
                "voltage_actual_v": psu_measurement.voltage_actual_v,
                "resistance_ohm": psu_measurement.resistance_ohm,
                "power_w": psu_measurement.power_w,
                "status": psu_measurement.status,
                "error": psu_measurement.error,
            }
        self._write(payload)

    def _sample_allowed(self, point_key: tuple[object, ...], point_elapsed_s: float) -> bool:
        max_rows = max(1, int(self.config.lcr_continuous_log_max_rows_per_point))
        count = int(self._point_counts.get(point_key, 0))
        if count >= max_rows:
            return False
        last = self._point_last_elapsed.get(point_key)
        cadence = max(0.1, float(self.config.lcr_continuous_log_cadence_s))
        if last is not None and point_elapsed_s - last < cadence:
            return False
        self._point_counts[point_key] = count + 1
        self._point_last_elapsed[point_key] = point_elapsed_s
        return True

    def _write(self, payload: dict[str, object]) -> None:
        if self._fh is None:
            raise RuntimeError("continuous LCR writer is not open")
        self._fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        self._fh.flush()
        if payload.get("event") == "lcr_sample":
            self._rows_written += 1

    def close(self, *, stop_reason: str = "", status: str = "") -> None:
        if self._fh is None:
            return
        try:
            self._write(
                {
                    "event": "closed",
                    "timestamp_utc": _timestamp_utc(),
                    "rows_written": self._rows_written,
                    "status": status,
                    "stop_reason": stop_reason,
                }
            )
        finally:
            self._fh.close()
            self._fh = None


def _continuous_writer_for_config(config: AcSweepConfig) -> LcrContinuousJsonlWriter | None:
    if not bool(config.lcr_continuous_log_enabled):
        return None
    path_text = str(config.lcr_continuous_log_path or "").strip()
    if not path_text:
        return None
    return LcrContinuousJsonlWriter(path_text, config)


def _status_wrapped_progress(
    status_writer: AcRunStatusWriter,
    progress: Callable[[AcSweepRow], None] | None,
) -> Callable[[AcSweepRow], None]:
    def _wrapped(row: AcSweepRow) -> None:
        status_writer.record_row(row)
        if progress is not None:
            progress(row)

    return _wrapped


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
    continuous_writer = _continuous_writer_for_config(config)
    status_writer = AcRunStatusWriter(
        output_path=output_path,
        config=config,
        continuous_log_path=None if continuous_writer is None else continuous_writer.path,
    )
    started = time.monotonic()
    stop_reason = ""
    progress_callback = _status_wrapped_progress(status_writer, progress)
    try:
        status_writer.open(psu=psu)
        writer.write_metadata()
        status_writer.update(phase="metadata_written", psu=psu)
        if continuous_writer is not None:
            continuous_writer.open()
            status_writer.update(phase="lcr_debug_opened", psu=psu)
        psu.connect()
        status_writer.update(phase="psu_connected", psu=psu)
        psu.initialize(voltage_limit_v=config.voltage_limit_v)
        status_writer.update(phase="psu_initialized", psu=psu)
        resistance_estimate_ohm = max(1.0, float(config.psu_current_feedback_resistance_ohm))
        for setting_index, setting in enumerate(config.lcr_settings, start=1):
            if stop_requested is not None and stop_requested():
                raise RuntimeError("AC sweep stopped by user")
            status_writer.update(
                phase="configure_lcr",
                setting_index=setting_index,
                total_settings=len(config.lcr_settings),
                setting=setting,
                psu=psu,
            )
            lcr.configure(setting)
            total_current_points = len(config.current_points)
            for current_point_index, current_point in enumerate(config.current_points, start=1):
                if stop_requested is not None and stop_requested():
                    raise RuntimeError("AC sweep stopped by user")
                status_writer.update(
                    phase="set_current",
                    setting_index=setting_index,
                    total_settings=len(config.lcr_settings),
                    setting=setting,
                    current_point=current_point,
                    current_point_index=current_point_index,
                    total_current_points=total_current_points,
                    psu=psu,
                )
                _set_initial_psu_voltage_for_current(
                    config=config,
                    psu=psu,
                    current_point=current_point,
                    resistance_estimate_ohm=resistance_estimate_ohm,
                )
                psu.set_current(current_point.current_a)
                try:
                    ready_measurement = _wait_for_psu_current(
                        config=config,
                        psu=psu,
                        current_point=current_point,
                        resistance_estimate_ohm=resistance_estimate_ohm,
                        stop_requested=stop_requested,
                        sleep=sleep,
                    )
                except MissingPsuReadbackError as exc:
                    _write_sweep_failure_row(
                        config=config,
                        psu=psu,
                        writer=writer,
                        started=started,
                        setting_index=setting_index,
                        setting=setting,
                        current_point=current_point,
                        current_point_index=current_point_index,
                        total_current_points=total_current_points,
                        message=str(exc),
                        progress=progress_callback,
                    )
                    raise
                if ready_measurement.resistance_ohm is not None:
                    resistance_estimate_ohm = _smoothed_resistance_estimate(
                        previous_ohm=resistance_estimate_ohm,
                        measurement_ohm=ready_measurement.resistance_ohm,
                    )
                if ready_measurement.status.upper() == "WARN" or ready_measurement.error.strip():
                    _write_sweep_warning_row(
                        config=config,
                        psu=psu,
                        writer=writer,
                        started=started,
                        setting_index=setting_index,
                        setting=setting,
                        current_point=current_point,
                        current_point_index=current_point_index,
                        total_current_points=total_current_points,
                        message=ready_measurement.error or ready_measurement.status,
                        progress=progress_callback,
                    )
                dwell = max(0.0, float(config.dwell_s))
                if dwell:
                    status_writer.update(
                        phase="settle",
                        setting_index=setting_index,
                        total_settings=len(config.lcr_settings),
                        setting=setting,
                        current_point=current_point,
                        current_point_index=current_point_index,
                        total_current_points=total_current_points,
                        psu=psu,
                    )
                    _log_lcr_continuous_window(
                        config=config,
                        lcr=lcr,
                        psu=psu,
                        writer=continuous_writer,
                        started=started,
                        setting_index=setting_index,
                        setting=setting,
                        current_point=current_point,
                        current_point_index=current_point_index,
                        total_current_points=total_current_points,
                        phase="settle",
                        duration_s=dwell,
                        sleep=sleep,
                        stop_requested=stop_requested,
                    )
                status_writer.update(
                    phase="measure",
                    setting_index=setting_index,
                    total_settings=len(config.lcr_settings),
                    setting=setting,
                    current_point=current_point,
                    current_point_index=current_point_index,
                    total_current_points=total_current_points,
                    psu=psu,
                )
                _measure_sweep_point_with_retries(
                    config=config,
                    lcr=lcr,
                    psu=psu,
                    writer=writer,
                    continuous_writer=continuous_writer,
                    started=started,
                    setting_index=setting_index,
                    setting=setting,
                    current_point=current_point,
                    current_point_index=current_point_index,
                    total_current_points=total_current_points,
                    progress=progress_callback,
                    stop_requested=stop_requested,
                )
        stop_reason = "completed"
    finally:
        active_error = sys.exc_info()[1]
        if active_error is not None:
            stop_reason = str(active_error)
            status_writer.record_exception(active_error)
        shutdown_errors: list[Exception] = []
        cleanup_error_messages: list[str] = []
        try:
            status_writer.update(phase="cleanup_output_off", psu=psu, status=_status_for_exception(active_error), stop_reason=stop_reason)
            psu.output_off()
        except Exception as exc:
            shutdown_errors.append(exc)
            cleanup_error_messages.append(f"psu.output_off: {exc}")
        try:
            status_writer.update(phase="cleanup_psu_close", psu=psu, status=_status_for_exception(active_error), stop_reason=stop_reason)
            psu.close()
        except Exception as exc:
            shutdown_errors.append(exc)
            cleanup_error_messages.append(f"psu.close: {exc}")
        final_status = "failed" if shutdown_errors and active_error is None else _status_for_exception(active_error)
        try:
            if continuous_writer is not None:
                continuous_writer.close(stop_reason=stop_reason or final_status, status=final_status)
        except Exception as exc:
            shutdown_errors.append(exc)
            cleanup_error_messages.append(f"lcr_debug.close: {exc}")
        try:
            writer.close()
        except Exception as exc:
            shutdown_errors.append(exc)
            cleanup_error_messages.append(f"tsv.close: {exc}")
        if shutdown_errors and active_error is None:
            final_status = "failed"
            stop_reason = str(shutdown_errors[0])
        status_writer.close(
            status=final_status,
            stop_reason=stop_reason or final_status,
            psu=None,
            cleanup_errors=cleanup_error_messages,
        )
        if shutdown_errors and active_error is None:
            raise shutdown_errors[0]


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


def _write_continuous_sample_if_due(
    *,
    config: AcSweepConfig,
    writer: LcrContinuousJsonlWriter | None,
    started: float,
    point_started: float,
    setting_index: int,
    total_settings: int,
    setting: Lcr6000Settings,
    current_point: CurrentLoopPoint,
    current_point_index: int,
    total_current_points: int,
    repeat_index: int,
    reading: Lcr6000Reading,
    psu_measurement: PowerSupplyMeasurement | None,
    phase: str,
) -> None:
    if writer is None or not bool(config.lcr_continuous_log_enabled):
        return
    writer.write_sample(
        phase=phase,
        started=started,
        point_started=point_started,
        setting_index=setting_index,
        total_settings=total_settings,
        setting=setting,
        current_point=current_point,
        current_point_index=current_point_index,
        total_current_points=total_current_points,
        repeat_index=repeat_index,
        reading=reading,
        psu_measurement=psu_measurement,
    )


def _log_lcr_continuous_window(
    *,
    config: AcSweepConfig,
    lcr: LcrDevice,
    psu: CurrentSource,
    writer: LcrContinuousJsonlWriter | None,
    started: float,
    setting_index: int,
    setting: Lcr6000Settings,
    current_point: CurrentLoopPoint,
    current_point_index: int,
    total_current_points: int,
    phase: str,
    duration_s: float,
    sleep: Callable[[float], None],
    stop_requested: Callable[[], bool] | None,
) -> None:
    duration = max(0.0, float(duration_s))
    if duration <= 0.0:
        return
    if writer is None or not bool(config.lcr_continuous_log_enabled):
        sleep(duration)
        return
    point_started = time.monotonic()
    deadline = point_started + duration
    repeat_index = 0
    cadence_s = max(0.1, float(config.lcr_continuous_log_cadence_s))
    while True:
        if stop_requested is not None and stop_requested():
            raise RuntimeError("AC sweep stopped by user")
        now = time.monotonic()
        if now >= deadline:
            break
        repeat_index += 1
        reading = _fetch_lcr_reading(lcr, attempts=config.lcr_read_attempts)
        measurement = _measure_psu_with_retries(psu, attempts=config.psu_measure_attempts)
        _write_continuous_sample_if_due(
            config=config,
            writer=writer,
            started=started,
            point_started=point_started,
            setting_index=setting_index,
            total_settings=len(config.lcr_settings),
            setting=setting,
            current_point=current_point,
            current_point_index=current_point_index,
            total_current_points=total_current_points,
            repeat_index=repeat_index,
            reading=reading,
            psu_measurement=measurement,
            phase=phase,
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            break
        sleep(min(cadence_s, remaining))


def _measure_sweep_point_with_retries(
    *,
    config: AcSweepConfig,
    lcr: LcrDevice,
    psu: CurrentSource,
    writer: AcSweepTsvWriter,
    continuous_writer: LcrContinuousJsonlWriter | None,
    started: float,
    setting_index: int,
    setting: Lcr6000Settings,
    current_point: CurrentLoopPoint,
    current_point_index: int,
    total_current_points: int,
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
                continuous_writer=continuous_writer,
                started=started,
                setting_index=setting_index,
                setting=setting,
                current_point=current_point,
                current_point_index=current_point_index,
                total_current_points=total_current_points,
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
                    current_point_index=current_point_index,
                    total_current_points=total_current_points,
                    message=f"slow LCR cadence persisted after {attempt} attempts: {exc}",
                    progress=progress,
                )
                _measure_sweep_point_once(
                    config=config,
                    lcr=lcr,
                    psu=psu,
                    writer=writer,
                    continuous_writer=continuous_writer,
                    started=started,
                    setting_index=setting_index,
                    setting=setting,
                    current_point=current_point,
                    current_point_index=current_point_index,
                    total_current_points=total_current_points,
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
                current_point_index=current_point_index,
                total_current_points=total_current_points,
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
    continuous_writer: LcrContinuousJsonlWriter | None,
    started: float,
    setting_index: int,
    setting: Lcr6000Settings,
    current_point: CurrentLoopPoint,
    current_point_index: int,
    total_current_points: int,
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
        point_elapsed_s = max(0.0, read_monotonic - point_started)
        psu_measurement = _measure_psu_with_retries(psu, attempts=config.psu_measure_attempts)
        _write_continuous_sample_if_due(
            config=config,
            writer=continuous_writer,
            started=started,
            point_started=point_started,
            setting_index=setting_index,
            total_settings=len(config.lcr_settings),
            setting=setting,
            current_point=current_point,
            current_point_index=current_point_index,
            total_current_points=total_current_points,
            repeat_index=repeat_index,
            reading=reading,
            psu_measurement=psu_measurement,
            phase="measure",
        )
        error = f"retry_attempt={attempt}" if attempt > 1 else ""
        current_set = max(0.0, float(current_point.current_a))
        if (
            current_set <= 1e-9
            and psu_measurement.current_actual_a is None
            and bool(config.psu_missing_readback_warn)
        ):
            message = "missing actual-current readback at zero-current reference; continuing"
            psu_measurement = PowerSupplyMeasurement(
                current_actual_a=psu_measurement.current_actual_a,
                voltage_actual_v=psu_measurement.voltage_actual_v,
                status="WARN",
                error=_combine_messages(psu_measurement.error, message),
            )
            error = _combine_messages(error, message)
        try:
            _validate_psu_output(
                current_point,
                psu_measurement,
                tolerance_a=config.psu_current_ready_tolerance_a,
            )
        except MissingPsuReadbackError as exc:
            if not bool(config.psu_missing_readback_warn):
                raise
            if current_set <= 1e-9:
                message = "missing actual-current readback at zero-current reference; continuing"
            else:
                message = "missing actual-current readback during active point; continuing"
            row = AcSweepRow(
                timestamp_utc=_timestamp_utc(),
                elapsed_s=read_monotonic - started,
                setting_index=setting_index,
                total_settings=len(config.lcr_settings),
                setting=setting,
                current_point=current_point,
                repeat_index=repeat_index,
                lcr_reading=reading,
                psu_measurement=PowerSupplyMeasurement(
                    current_actual_a=psu_measurement.current_actual_a,
                    voltage_actual_v=psu_measurement.voltage_actual_v,
                    status="WARN",
                    error=_combine_messages(psu_measurement.error, message),
                ),
                psu_backend=psu.backend_id,
                psu_resource=psu.resource,
                current_point_index=current_point_index,
                total_current_points=total_current_points,
                point_elapsed_s=point_elapsed_s,
                error=_combine_messages(error, str(exc), message),
            )
            writer.write_row(row)
            if progress is not None:
                progress(row)
            if _sweep_point_complete(
                point_duration=point_duration,
                fallback_repeats=fallback_repeats,
                repeat_index=repeat_index,
                point_started=point_started,
                read_monotonic=read_monotonic,
            ):
                break
            continue
        except PsuOutputVerificationError as exc:
            message = f"PSU current not at target during active point; continuing: {exc}"
            row = AcSweepRow(
                timestamp_utc=_timestamp_utc(),
                elapsed_s=read_monotonic - started,
                setting_index=setting_index,
                total_settings=len(config.lcr_settings),
                setting=setting,
                current_point=current_point,
                repeat_index=repeat_index,
                lcr_reading=reading,
                psu_measurement=PowerSupplyMeasurement(
                    current_actual_a=psu_measurement.current_actual_a,
                    voltage_actual_v=psu_measurement.voltage_actual_v,
                    status="WARN",
                    error=_combine_messages(psu_measurement.error, message),
                ),
                psu_backend=psu.backend_id,
                psu_resource=psu.resource,
                current_point_index=current_point_index,
                total_current_points=total_current_points,
                point_elapsed_s=point_elapsed_s,
                error=_combine_messages(error, message),
            )
            writer.write_row(row)
            if progress is not None:
                progress(row)
            if _sweep_point_complete(
                point_duration=point_duration,
                fallback_repeats=fallback_repeats,
                repeat_index=repeat_index,
                point_started=point_started,
                read_monotonic=read_monotonic,
            ):
                break
            continue
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
            current_point_index=current_point_index,
            total_current_points=total_current_points,
            point_elapsed_s=point_elapsed_s,
            error=error,
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
        if _sweep_point_complete(
            point_duration=point_duration,
            fallback_repeats=fallback_repeats,
            repeat_index=repeat_index,
            point_started=point_started,
            read_monotonic=read_monotonic,
        ):
            break


def _sweep_point_complete(
    *,
    point_duration: float,
    fallback_repeats: int,
    repeat_index: int,
    point_started: float,
    read_monotonic: float,
) -> bool:
    if point_duration > 0.0:
        return read_monotonic - point_started >= point_duration
    return repeat_index >= fallback_repeats


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


class PsuOutputVerificationError(RuntimeError):
    """Raised when PSU readback does not prove the requested current is active."""


class MissingPsuReadbackError(PsuOutputVerificationError):
    """Raised when PSU actual-current readback is missing."""


def _measure_psu_with_retries(psu: CurrentSource, *, attempts: int) -> PowerSupplyMeasurement:
    last_error = ""
    for attempt in range(1, max(1, int(attempts)) + 1):
        try:
            measurement = psu.measure()
        except Exception as exc:
            last_error = str(exc)
        else:
            if measurement.current_actual_a is not None:
                return measurement
            last_error = "missing actual-current readback"
        if attempt < attempts:
            time.sleep(0.1)
    return PowerSupplyMeasurement(status="FAIL", error=last_error)


def _set_initial_psu_voltage_for_current(
    *,
    config: AcSweepConfig,
    psu: CurrentSource,
    current_point: CurrentLoopPoint,
    resistance_estimate_ohm: float,
) -> None:
    if not bool(config.psu_current_feedback_enabled):
        return
    if not _supports_voltage_control(psu):
        return
    if _uses_fixed_hmp_voltage_limit(config):
        return
    current_a = max(0.0, float(current_point.current_a))
    if current_a <= 1e-9:
        _set_psu_voltage_limit(psu, 0.0)
        return
    voltage_limit = effective_power_supply_voltage_limit(config.psu_backend, config.voltage_limit_v)
    estimated_voltage = current_a * max(1.0, float(resistance_estimate_ohm))
    initial_voltage = min(voltage_limit, max(1.0, estimated_voltage * 0.9))
    _set_psu_voltage_limit(psu, initial_voltage)


def _wait_for_psu_current(
    *,
    config: AcSweepConfig,
    psu: CurrentSource,
    current_point: CurrentLoopPoint,
    resistance_estimate_ohm: float,
    stop_requested: Callable[[], bool] | None,
    sleep: Callable[[float], None],
) -> PowerSupplyMeasurement:
    if float(current_point.current_a) <= 1e-9:
        return _measure_psu_with_retries(psu, attempts=config.psu_measure_attempts)
    deadline = time.monotonic() + max(0.1, float(config.psu_current_ready_timeout_s))
    last_error = "PSU current readback was not checked"
    last_measurement = PowerSupplyMeasurement(status="FAIL", error=last_error)
    while True:
        if stop_requested is not None and stop_requested():
            raise RuntimeError("AC sweep stopped by user")
        measurement = _measure_psu_with_retries(psu, attempts=config.psu_measure_attempts)
        last_measurement = measurement
        try:
            _validate_psu_output(
                current_point,
                measurement,
                tolerance_a=config.psu_current_ready_tolerance_a,
            )
        except PsuOutputVerificationError as exc:
            last_error = str(exc)
        else:
            return measurement
        if _can_adjust_psu_voltage(config=config, psu=psu, measurement=measurement):
            _adjust_psu_voltage_toward_current(
                config=config,
                psu=psu,
                current_point=current_point,
                measurement=measurement,
                resistance_estimate_ohm=resistance_estimate_ohm,
            )
        if time.monotonic() >= deadline:
            if last_measurement.current_actual_a is None:
                raise MissingPsuReadbackError(
                    f"PSU did not reach requested current within "
                    f"{float(config.psu_current_ready_timeout_s):g} s: "
                    "PSU did not return an actual-current readback."
                )
            return PowerSupplyMeasurement(
                current_actual_a=last_measurement.current_actual_a,
                voltage_actual_v=last_measurement.voltage_actual_v,
                status="WARN",
                error=(
                    f"PSU did not reach requested current within "
                    f"{float(config.psu_current_ready_timeout_s):g} s: {last_error}"
                ),
            )
        sleep(max(0.02, float(config.psu_current_ready_poll_s)))


def _validate_psu_output(
    current_point: CurrentLoopPoint,
    measurement: PowerSupplyMeasurement,
    *,
    tolerance_a: float = 0.0015,
) -> None:
    current_set = max(0.0, float(current_point.current_a))
    if current_set <= 1e-9:
        return
    actual = measurement.current_actual_a
    if actual is None or not math.isfinite(float(actual)):
        raise MissingPsuReadbackError("PSU did not return an actual-current readback.")
    tolerance_a = max(0.0, float(tolerance_a))
    if abs(float(actual) - current_set) > tolerance_a + 1e-12:
        raise PsuOutputVerificationError(
            f"PSU actual current {float(actual) * 1000:g} mA is not within "
            f"{tolerance_a * 1000:g} mA of requested {current_set * 1000:g} mA."
        )


def _can_adjust_psu_voltage(
    *,
    config: AcSweepConfig,
    psu: CurrentSource,
    measurement: PowerSupplyMeasurement,
) -> bool:
    if not bool(config.psu_current_feedback_enabled):
        return False
    if _uses_fixed_hmp_voltage_limit(config):
        return False
    if not _supports_voltage_control(psu):
        return False
    actual = measurement.current_actual_a
    return actual is not None and math.isfinite(float(actual))


def _uses_fixed_hmp_voltage_limit(config: AcSweepConfig) -> bool:
    return str(config.psu_backend or "").strip().lower() in {"hmp4030", "shared_hmp_broker"}


def _adjust_psu_voltage_toward_current(
    *,
    config: AcSweepConfig,
    psu: CurrentSource,
    current_point: CurrentLoopPoint,
    measurement: PowerSupplyMeasurement,
    resistance_estimate_ohm: float,
) -> None:
    actual_current = measurement.current_actual_a
    if actual_current is None or not math.isfinite(float(actual_current)):
        return
    target_current = max(0.0, float(current_point.current_a))
    error_a = target_current - float(actual_current)
    if abs(error_a) <= max(0.0001, float(config.psu_current_ready_tolerance_a) * 0.5):
        return
    measured_voltage = measurement.voltage_actual_v
    if measured_voltage is None or not math.isfinite(float(measured_voltage)):
        measured_voltage = target_current * max(1.0, float(resistance_estimate_ohm))
    correction_v = error_a * max(1.0, float(resistance_estimate_ohm)) * 0.75
    correction_v = max(-3.5, min(3.5, correction_v))
    if abs(correction_v) < 0.15:
        correction_v = 0.15 if correction_v > 0 else -0.15
    voltage_limit = effective_power_supply_voltage_limit(config.psu_backend, config.voltage_limit_v)
    next_voltage = max(0.0, min(voltage_limit, float(measured_voltage) + correction_v))
    _set_psu_voltage_limit(psu, next_voltage)


def _supports_voltage_control(psu: CurrentSource) -> bool:
    return callable(getattr(psu, "set_voltage_limit", None))


def _set_psu_voltage_limit(psu: CurrentSource, voltage_v: float) -> None:
    setter = getattr(psu, "set_voltage_limit", None)
    if callable(setter):
        setter(float(voltage_v))


def _smoothed_resistance_estimate(*, previous_ohm: float, measurement_ohm: float) -> float:
    if not math.isfinite(float(measurement_ohm)) or float(measurement_ohm) <= 0.0:
        return previous_ohm
    if not math.isfinite(float(previous_ohm)) or float(previous_ohm) <= 0.0:
        return float(measurement_ohm)
    return 0.8 * float(previous_ohm) + 0.2 * float(measurement_ohm)


def _combine_messages(*messages: str) -> str:
    parts = [str(message).strip() for message in messages if str(message or "").strip()]
    return "; ".join(dict.fromkeys(parts))


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
    current_point_index: int,
    total_current_points: int,
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
        current_point_index=current_point_index,
        total_current_points=total_current_points,
        error=message,
    )
    writer.write_row(row)
    if progress is not None:
        progress(row)


def _write_sweep_failure_row(
    *,
    config: AcSweepConfig,
    psu: CurrentSource,
    writer: AcSweepTsvWriter,
    started: float,
    setting_index: int,
    setting: Lcr6000Settings,
    current_point: CurrentLoopPoint,
    current_point_index: int,
    total_current_points: int,
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
        psu_measurement=PowerSupplyMeasurement(status="FAIL"),
        psu_backend=psu.backend_id,
        psu_resource=psu.resource,
        current_point_index=current_point_index,
        total_current_points=total_current_points,
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
        self.resource = normalize_serial_resource(resource)
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
        self._open_serial_port()
        time.sleep(0.08)
        self._verify_identity()

    def _open_serial_port(self) -> None:
        if serial is None:
            raise RuntimeError("pyserial is not available")
        self._serial = serial.Serial(self.resource, baudrate=self.baudrate, timeout=0.7, write_timeout=0.7)
        self._serial.rts = False
        self._serial.dtr = False

    def _verify_identity(self) -> None:
        idn = self.identify()
        backend = classify_power_supply_idn(idn)
        if backend is None:
            self.close()
            detail = "no SCPI ID response" if not idn else f"unexpected ID response {idn!r}"
            raise RuntimeError(f"{self.resource} is not a supported HMP/OWON power supply ({detail})")
        if backend != self.backend_id:
            self.close()
            expected = POWER_SUPPLY_PROFILES[self.backend_id]["label"]
            actual = POWER_SUPPLY_PROFILES[backend]["label"]
            raise RuntimeError(f"{self.resource} identified as {actual}, not selected {expected}")

    def identify(self) -> str:
        self.command("*IDN?", settle_s=0.08)
        return self._read_line()

    def initialize(self, *, voltage_limit_v: float) -> None:
        if self.profile.get("reset_on_start", False):
            self.command("*RST", settle_s=1.2)
        self.select_channel()
        voltage = effective_power_supply_voltage_limit(self.backend_id, voltage_limit_v)
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

    def set_voltage_limit(self, voltage_v: float) -> None:
        self.select_channel()
        voltage = effective_power_supply_voltage_limit(self.backend_id, voltage_v)
        self.command(f"VOLT {voltage:.3f}", settle_s=0.03)

    def measure(self) -> PowerSupplyMeasurement:
        self.select_channel()
        voltage = self.query_float("MEAS:VOLT?")
        current = self.query_float("MEAS:CURR?")
        return PowerSupplyMeasurement(current_actual_a=current, voltage_actual_v=voltage, status="OK")

    def output_off(self) -> None:
        try:
            self._send_output_off_commands()
        except Exception as first_error:
            self.close()
            try:
                self._open_serial_port()
                time.sleep(0.08)
                self._send_output_off_commands()
            except Exception as second_error:
                raise RuntimeError(
                    f"failed to turn power-supply output off after reconnect: {second_error}"
                ) from first_error

    def _send_output_off_commands(self) -> None:
        try:
            self.select_channel()
        except Exception:
            pass
        self.command("CURR 0.0000", settle_s=0.03)
        self.command("VOLT 0.000", settle_s=0.03)
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


class SharedBrokerCurrentSource:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        channel: int,
        voltage_limit_v: float,
        owner: str = "ac_susceptibility_logger",
        timeout_s: float = 8.0,
        client_factory: Any = BrokerJsonClient,
    ) -> None:
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = int(port)
        self.channel = int(channel)
        self.voltage_limit_v = float(voltage_limit_v)
        self.owner = str(owner or "ac_susceptibility_logger")
        self.timeout_s = float(timeout_s)
        self.backend_id = "shared_hmp_broker"
        self.resource = f"{self.host}:{self.port}/CH{self.channel}"
        self.profile = dict(POWER_SUPPLY_PROFILES[self.backend_id])
        self._client_factory = client_factory
        self._client: Any | None = None
        self._lease_id: str | None = None

    @property
    def lease_id(self) -> str | None:
        return self._lease_id

    def connect(self) -> None:
        if self.channel <= 0:
            raise RuntimeError("select a shared HMP broker channel")
        if self._client is None:
            self._client = self._client_factory(host=self.host, port=self.port, timeout_s=self.timeout_s)
        self._client.request("snapshot")

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("shared HMP broker is not connected")
        return self._client

    def _ensure_lease(self) -> str:
        if self._lease_id:
            return self._lease_id
        lease = self._require_client().lease(
            channel=self.channel,
            owner=self.owner,
            role=ROLE_AC_SUSCEPTIBILITY,
        )
        lease_id = str(lease.get("lease_id") or "")
        if not lease_id:
            raise RuntimeError("Shared HMP broker did not return a lease id.")
        self._lease_id = lease_id
        return lease_id

    def initialize(self, *, voltage_limit_v: float) -> None:
        self.voltage_limit_v = effective_power_supply_voltage_limit(self.backend_id, voltage_limit_v)
        self._require_client().configure_channel(
            channel=self.channel,
            lease_id=self._ensure_lease(),
            voltage_v=self.voltage_limit_v,
            current_a=0.0,
            output_on=True,
        )

    def set_current(self, current_a: float) -> None:
        resolution = max(1e-6, float(self.profile.get("current_resolution_a", 0.001)))
        current = max(0.0, round(float(current_a) / resolution) * resolution)
        self._require_client().configure_channel(
            channel=self.channel,
            lease_id=self._ensure_lease(),
            voltage_v=self.voltage_limit_v,
            current_a=current,
            output_on=True,
        )

    def set_voltage_limit(self, voltage_v: float) -> None:
        self.voltage_limit_v = effective_power_supply_voltage_limit(self.backend_id, voltage_v)
        measurement = self.measure()
        current_a = 0.0 if measurement.current_actual_a is None else max(0.0, float(measurement.current_actual_a))
        self._require_client().configure_channel(
            channel=self.channel,
            lease_id=self._ensure_lease(),
            voltage_v=self.voltage_limit_v,
            current_a=current_a,
            output_on=True,
        )

    def measure(self) -> PowerSupplyMeasurement:
        readback = self._require_client().measure_channel(channel=self.channel)
        current_mA = readback.get("current_mA")
        voltage_v = readback.get("voltage_V")
        return PowerSupplyMeasurement(
            current_actual_a=None if current_mA is None else float(current_mA) / 1000.0,
            voltage_actual_v=None if voltage_v is None else float(voltage_v),
            status="OK",
        )

    def output_off(self) -> None:
        if not self._lease_id:
            return
        self._require_client().set_output(
            channel=self.channel,
            lease_id=self._lease_id,
            output_on=False,
        )

    def close(self) -> None:
        client = self._client
        lease_id = self._lease_id
        self._lease_id = None
        self._client = None
        if client is not None and lease_id:
            try:
                client.release(channel=self.channel, lease_id=lease_id)
            except Exception:
                pass


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
