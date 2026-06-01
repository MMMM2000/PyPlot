from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Condition, Event, RLock, Thread, current_thread, get_ident
from time import perf_counter
from typing import Any, Callable, Iterable, Mapping, Sequence

from PyQt6 import QtCore, QtGui, QtWidgets
try:
    import pyqtgraph as pg
    pg.setConfigOptions(antialias=False)
except Exception:  # pragma: no cover - import guard
    pg = None  # type: ignore[assignment]

from plotting.shared.logfiles import append_text_with_rotation
from plotting.shared.power_guard import create_experiment_sleep_guard
from plotting.shared.utils import ensure_app_theme, install_standard_menu
from data_logging.shared_power_supply.broker import SharedPowerSupplyBroker, ROLE_MINI_DMA_CURRENT, ROLE_MINI_DMA_MOTOR
from data_logging.shared_power_supply.driver import HmpSerialDriver
from data_logging.shared_power_supply.protocol import BrokerJsonClient, start_broker_server

try:
    import serial
    from serial import SerialException
    from serial.tools import list_ports
except Exception:  # pragma: no cover - import guard
    serial = None  # type: ignore[assignment]
    SerialException = Exception  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]


APP_NAME = "Mini DMA Logger"
DEFAULT_LOG_BASENAME = "mini_dma"
DEFAULT_RUN_LOG_MIRROR_PATH = Path("logs") / "mini_dma_run_log.txt"
DEFAULT_ZERO_LOAD_SCALE_G = 21.2
SESSION_MEASUREMENT_TX = "measurement.txt"
SESSION_MEASUREMENT_CSV = "measurement.csv"
SESSION_METADATA_JSON = "metadata.json"
SESSION_RAW_SCALE_CSV = "scale_raw.csv"
SESSION_CONTROL_TRACE_CSV = "control_trace.csv"
SESSION_SETUP_TX = "setup.txt"
SESSION_SETUP_CSV = "setup.csv"
SESSION_UI_TELEMETRY_CSV = "ui_telemetry.csv"
CONTROL_LOGIC_NAME = "mini_dma_control"
CONTROL_LOGIC_VERSION = "2026-05-29.2"
CONTROL_LOGIC_PROFILE = "filtered-current-hold-setup-ui"
CONTROL_LOGIC_FEATURES = [
    "mandatory_setup_length_refreeze",
    "setup_slack_stress_cap",
    "setup_zero_plateau_accept_current_position",
    "current_hold_filtered_scale_signal",
    "current_hold_filtered_signal_change_gate",
    "current_hold_persistent_error_gate",
    "current_hold_automatic_entry_gate",
    "current_hold_recovery_tolerance_band",
    "current_hold_retry_after_filter_window",
    "current_hold_bounded_saved_cap",
    "current_hold_noise_band_resume",
    "adaptive_current_hold_response_stiffness",
    "current_hold_waits_for_natural_target_return",
    "current_hold_response_requires_directional_motor_response",
    "current_hold_large_error_bypasses_persistence",
    "current_hold_moving_away_bypasses_persistence",
    "current_hold_moving_away_preserves_predictive_step",
    "current_hold_large_error_not_masked_by_noise",
    "separate_setup_preload_and_zero_settle",
    "stable_setup_phase_progress",
    "dashboard_plot_gap_breaks",
    "zero_load_reference_sidecar",
    "voltage_limit_unwind_uses_measured_current_fallback",
    "voltage_limit_defers_to_current_hold",
    "voltage_limit_unwind_keeps_shortened_return_leg",
    "voltage_limit_preserves_rate_limited_nominal_return",
    "wire_break_recovery_prompt_ui_thread",
    "current_sweep_mechanical_load_loss_guard",
    "fault_stop_metadata_preserved_on_app_close",
    "control_trace_row_local_task_text",
    "single_prompt_length_setup",
]
CONTROL_TRACE_FIELDNAMES = [
    "elapsed_s",
    "timestamp_utc",
    "recipe_mode",
    "task_text",
    "automation_phase",
    "automation_basis",
    "automation_target_value",
    "plateau_index",
    "decision",
    "current_value",
    "error_value",
    "tolerance",
    "sensitivity_per_mm",
    "motor_step_mm",
    "correction_mm",
    "backlash_mm",
    "command_speed_mm_s",
    "required_fresh_samples",
    "post_move_sample_count",
    "target_mm",
    "effective_target_mm",
    "result",
    "reason",
]
UI_TELEMETRY_FIELDNAMES = [
    "elapsed_s",
    "timestamp_utc",
    "target_interval_ms",
    "actual_interval_ms",
    "ui_fps",
    "ui_heartbeat_interval_ms",
    "ui_heartbeat_fps",
    "handler_duration_ms",
    "graph_refresh_interval_ms",
    "task_text",
    "automation_active",
    "session_active",
    "session_logging_enabled",
    "length_setup_dialog_visible",
    "recovery_dialog_visible",
    "scale_sample_changed",
    "dialog_sample_recorded",
    "live_plot_sample_recorded",
    "dashboard_plot_refreshed",
    "latest_scale_age_s",
    "session_points",
    "live_plot_points",
]
GRAVITY_MS2 = 9.80665
LONG_NAMES = ("Displacement", "Load", "Strain", "Stress")
UNITS = ("mm", "g", "%", "MPa")
MEASUREMENT_CSV_FIELDNAMES = [
    "elapsed_s",
    "timestamp_utc",
    "recipe_mode",
    "automation_phase",
    "automation_basis",
    "automation_target_value",
    "plateau_index",
    "plateau_label",
    "raw_position_mm",
    "position_mm",
    "raw_load_g",
    "load_g",
    "load_raw_last_g",
    "load_mean_g",
    "load_std_g",
    "load_min_g",
    "load_max_g",
    "load_sample_count",
    "scale_sample_rate_hz",
    "preload_state",
    "strain_pct",
    "stress_mpa",
    "current_zero_position_mm",
    "current_l0_mm",
    "current_relative_position_mm",
    "current_relative_strain_pct",
    "current_set_mA",
    "current_measured_mA",
    "voltage_V",
    "resistance_ohm",
    "power_W",
]
FLOAT_PATTERN = re.compile(r"[-+]?(?:(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][-+]?\d+)?)")
RUN_SUFFIX_PATTERN = re.compile(r"(?:_run\d{2,})+$")
WINDOWS: list[QtWidgets.QWidget] = []
GNG_SUPPORTED_BAUDS = (600, 1200, 2400, 4800, 9600)
SCALE_NO_DATA_HINT_DELAY_MS = 3500
STALE_SCALE_AFTER_S = 2.0
TIC_MOTOR_POWER_MIN_V = 4.5
MANUAL_JOG_TIC_STATUS_FRESH_S = 2.0
TIC_USB_VENDOR_ID = 0x1FFB
TIC_USB_TRANSPORT_NATIVE = "native-usb"
TIC_USB_TRANSPORT_ALIASES = {TIC_USB_TRANSPORT_NATIVE, "usb", "pyusb"}
TIC_USB_REQUEST_OUT = 0x40
TIC_USB_REQUEST_IN = 0xC0
TIC_CMD_EXIT_SAFE_START = 0x83
TIC_CMD_ENERGIZE = 0x85
TIC_CMD_HALT_AND_HOLD = 0x89
TIC_CMD_RESET_COMMAND_TIMEOUT = 0x8C
TIC_CMD_SET_CURRENT_LIMIT = 0x91
TIC_CMD_GET_VARIABLES = 0xA1
TIC_CMD_SET_TARGET_POSITION = 0xE0
TIC_CMD_SET_TARGET_VELOCITY = 0xE3
TIC_CMD_SET_MAX_SPEED = 0xE6
TIC_CMD_HALT_AND_SET_POSITION = 0xEC
TIC_KEEPALIVE_INTERVAL_MS = 500
DEFAULT_TIC_STATUS_INTERVAL_MS = 1000
DEFAULT_SUPPLY_READ_INTERVAL_MS = 750
DEFAULT_CONTROL_INTERVAL_MS = 50
DEFAULT_LOG_INTERVAL_MS = 500
DEFAULT_UI_REFRESH_INTERVAL_MS = 200
DEFAULT_UI_HEARTBEAT_INTERVAL_MS = 16
DEFAULT_GRAPH_REFRESH_INTERVAL_MS = 500
DEFAULT_SCALE_REQUEST_INTERVAL_MS = 250
LIVE_PLOT_MAX_POINTS = 3000
DISPLAY_PLOT_MAX_POINTS = 1500
DISPLAY_PLOT_RECENT_POINTS = 600
DISPLAY_PLOT_BRIDGE_POINTS = 200
DISPLAY_PLOT_BASE_BUCKET_S = 1.0
DISPLAY_PLOT_OLD_CACHE_GRANULARITY = 1000
DISPLAY_PLOT_BREAK_GAP_S = 30.0
SCALE_REQUEST_TIMEOUT_MIN_S = 0.30
SETUP_ZERO_FALLBACK_MIN_POINTS = 4
SETUP_ZERO_FALLBACK_MIN_TIME_S = 0.8
SETUP_ZERO_FALLBACK_MIN_STRAIN_PCT = 0.05
SETUP_ZERO_FALLBACK_MIN_MOTOR_STEPS = 4.0
SETUP_ZERO_FALLBACK_RAW_SPAN_G = 0.012
SETUP_ZERO_FALLBACK_MIN_RESIDUAL_G = 0.02
SETUP_ZERO_FALLBACK_MAX_RESIDUAL_G = 0.10
SETUP_PRELOAD_TAKEUP_LOAD_G = 0.03
CURRENT_SWEEP_MECHANICAL_LOAD_LOSS_MIN_STRAIN_PCT = 0.5
CURRENT_SWEEP_MECHANICAL_LOAD_LOSS_MIN_MOTOR_STEPS = 20.0
SETUP_PRELOAD_MAX_SLACK_STEP_STRESS_MPA = 50.0
SETUP_RETURN_MIN_SPEED_STRAIN_PCT = 0.10
SETUP_UNLOAD_BASELINE_MIN_POINTS = 5
SETUP_UNLOAD_BASELINE_MIN_FRACTION = 0.15
SETUP_UNLOAD_BASELINE_MIN_STRESS_MPA = 1.0
SETUP_UNLOAD_SLACK_RECENT_POINTS = 5
SETUP_UNLOAD_SLACK_MAX_STRESS_MPA = 4.0
SETUP_UNLOAD_SLACK_MAX_STRESS_FRACTION = 0.30
SETUP_UNLOAD_SLACK_SLOPE_FRACTION = 0.35
SETUP_PRELOAD_DEFAULT_DURATION_S = 10.0
SETUP_RETURN_DEFAULT_DURATION_S = 5.0
SETUP_SLACK_DEFAULT_STRAIN_RATE_PCT_S = 1.0
MIN_RESISTANCE_CURRENT_MA = 0.05
SUPPLY_READ_MIN_INTERVAL_S = DEFAULT_SUPPLY_READ_INTERVAL_MS / 1000.0
RECOVERY_POSITION = "recovery_position"
RECOVERY_LOAD = "recovery_load"
PROJECT_EXTENSION = ".pydpj"
PRELOAD_PENDING = "pending"
PRELOAD_ACTIVE = "active"
PRELOAD_DISABLED = "disabled"
SUPPLY_PROFILES: dict[str, dict[str, Any]] = {
    "hmp4030": {
        "label": "HMP4030 (original)",
        "start_current_mA": 1.0,
        "min_start_current_mA": 1.0,
        "max_voltage": 32.05,
        "channel_select": 3,
        "motor_supply_channel": 2,
        "channel_count": 3,
        "baudrate": 115200,
        "reset_on_start": True,
        "voltage_first": False,
        "current_resolution_mA": 0.2,
    },
    "hmp4040": {
        "label": "HMP4040 (4-channel)",
        "start_current_mA": 1.0,
        "min_start_current_mA": 1.0,
        "max_voltage": 32.05,
        "channel_select": 4,
        "motor_supply_channel": 3,
        "channel_count": 4,
        "baudrate": 115200,
        "reset_on_start": True,
        "voltage_first": False,
        "current_resolution_mA": 0.2,
    },
    "owon_spe6102": {
        "label": "Owon SPE6102",
        "start_current_mA": 10.0,
        "min_start_current_mA": 10.0,
        "max_voltage": 62.0,
        "channel_select": 0,
        "motor_supply_channel": 0,
        "channel_count": 1,
        "baudrate": 115200,
        "reset_on_start": False,
        "voltage_first": True,
        "current_resolution_mA": 1.0,
    },
    "shared_hmp_broker": {
        "label": "Shared HMP broker",
        "start_current_mA": 1.0,
        "min_start_current_mA": 1.0,
        "max_voltage": 32.05,
        "channel_select": 0,
        "motor_supply_channel": 0,
        "channel_count": 4,
        "baudrate": 0,
        "reset_on_start": False,
        "voltage_first": False,
        "current_resolution_mA": 0.2,
        "shared_broker": True,
    },
}
HEATING_MODE_OFF = "off"
OUTPUT_COLLISION_NEXT = "next"
OUTPUT_COLLISION_REPLACE = "replace"
OUTPUT_COLLISION_CANCEL = "cancel"
HSW_BASIS_LOAD_G = "load_g"
HSW_BASIS_STRESS_MPA = "stress_mpa"
HSW_BASIS_STRAIN_PCT = "strain_pct"
MECHANICAL_STEP_DISPLACEMENT_MM = "displacement_mm"
HSW_BASIS_LABELS = {
    HSW_BASIS_LOAD_G: "Load (g)",
    HSW_BASIS_STRESS_MPA: "Stress (MPa)",
    HSW_BASIS_STRAIN_PCT: "Strain (%)",
}
CURRENT_SWEEP_LOAD = "current_sweep_load"
CURRENT_SWEEP_STRESS = "current_sweep_stress"
CURRENT_SWEEP_STRAIN = "current_sweep_strain"
CONSTANT_CURRENT_STRAIN_SWEEP = "constant_current_strain_sweep"
LEGACY_CURRENT_SWEEP = "current_sweep"
CALIBRATION = "calibration"
CALIBRATION_COPPER = "calibration_copper"
CALIBRATION_BASELINE = "calibration_baseline"
CALIBRATION_PRELOAD = "calibration_preload"
CALIBRATION_FORWARD = "calibration_forward"
CALIBRATION_REVERSE = "calibration_reverse"
CALIBRATION_DEFAULTS_VERSION = 4
MOTOR_DEFAULTS_VERSION = 3
DEFAULT_FULL_STEPS_PER_MM = 100.0
DEFAULT_TIC_STEP_MODE = "8"
DEFAULT_STEPS_PER_MM = 800.0
DEFAULT_TIC_CURRENT_LIMIT_MA = 343
DEFAULT_MOTOR_SUPPLY_CURRENT_LIMIT_A = 0.5
TIC_CURRENT_LIMIT_STEP_MA = 1
TIC_T500_CURRENT_LIMITS_MA: tuple[int, ...] = (
    0,
    1,
    174,
    343,
    495,
    634,
    762,
    880,
    990,
    1092,
    1189,
    1281,
    1368,
    1452,
    1532,
    1611,
    1687,
    1762,
    1835,
    1909,
    1982,
    2056,
    2131,
    2207,
    2285,
    2366,
    2451,
    2540,
    2634,
    2734,
    2843,
    2962,
    3093,
)
TIC_STEP_MODE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Full step", "full"),
    ("1/2 step", "2"),
    ("1/4 step", "4"),
    ("1/8 step", "8"),
    ("1/16 step", "16"),
    ("1/32 step", "32"),
)
TIC_STEP_MODE_FACTORS = {
    "full": 1,
    "1": 1,
    "half": 2,
    "2": 2,
    "2_100p": 2,
    "4": 4,
    "8": 8,
    "16": 16,
    "32": 32,
}
MOTOR_STEP_CALIBRATION_DEFAULT_INCREMENT_STEPS = 800
MOTOR_STEP_CALIBRATION_DEFAULT_MOVES = 5
MOTOR_STEP_CALIBRATION_DEFAULT_SPEED_MM_S = 0.01
MOTOR_STEP_CALIBRATION_CSV_FIELDNAMES = [
    "point_index",
    "timestamp_utc",
    "tic_position_steps",
    "relative_tic_steps",
    "entered_displacement_mm",
    "relative_displacement_mm",
    "move_command_steps",
    "move_speed_steps_per_s",
    "estimated_steps_per_mm_from_baseline",
]
SERVO_CORRECTION_GAIN = 0.75
SERVO_LIVE_STIFFNESS_ALPHA = 0.35
SERVO_NOISE_SIGMA = 3.0
SERVO_CURRENT_SWEEP_ERROR_GAIN_PER_S = 1.5
SERVO_CURRENT_SWEEP_RATE_GAIN = 1.2
SERVO_CURRENT_SWEEP_DEFAULTS_VERSION = 5
SERVO_CURRENT_SWEEP_MAX_CORRECTION_STRAIN_PCT = 5.0
SERVO_CURRENT_SWEEP_MAX_CORRECTION_RATE_PCT_S = 15.0
SERVO_CURRENT_SWEEP_MAX_STAGE_SPEED_MM_S = 5.0
SERVO_CURRENT_SWEEP_MAX_CORRECTION_STRESS_MPA = 10.0
SERVO_CURRENT_SWEEP_HOLD_MAX_CORRECTION_STRESS_MPA = 30.0
SERVO_CURRENT_SWEEP_MID_CORRECTION_STRESS_MPA = 5.0
SERVO_CURRENT_SWEEP_NEAR_CORRECTION_STRESS_MPA = 1.0
SERVO_CURRENT_SWEEP_REVERSAL_HOLD_STRESS_MPA = 1.0
SERVO_CURRENT_SWEEP_MIN_COMMAND_SPEED_MM_S = 0.05
SERVO_CURRENT_SWEEP_DYNAMIC_MIN_FRACTION = 0.20
SERVO_CURRENT_SWEEP_DYNAMIC_MAX_FRACTION = 0.60
SERVO_CURRENT_SWEEP_DYNAMIC_SCALE_MPA = 25.0
SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MIN_FRACTION = 0.50
SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MAX_FRACTION = 0.80
SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_LARGE_ERROR_MPA = 10.0
SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MAX_COMMAND_STRAIN_PCT = 0.35
SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MIN_SAMPLES = 3
SERVO_CURRENT_SWEEP_HOLD_CORRECTION_CONFIRM_S = 1.0
SERVO_CURRENT_SWEEP_HOLD_FILTER_WINDOW_S = 1.8
SERVO_CURRENT_SWEEP_HOLD_MIN_PAUSE_STRESS_MPA = 2.0
SERVO_CURRENT_SWEEP_HOLD_MIN_RESUME_STRESS_MPA = 1.0
SERVO_CURRENT_SWEEP_HOLD_NOISE_SIGMA = 3.0
SERVO_CURRENT_SWEEP_HOLD_NOISE_CAP_TOLERANCE_FACTOR = 20.0
SERVO_CURRENT_SWEEP_HOLD_ENTRY_TOLERANCE_FACTOR = 20.0
SERVO_CURRENT_SWEEP_HOLD_LARGE_ERROR_FACTOR = 10.0
SERVO_CURRENT_SWEEP_HOLD_NOISY_LARGE_ERROR_FACTOR = 2.0
SERVO_CURRENT_SWEEP_HOLD_ENTRY_CONFIRM_S = 0.3
SERVO_CURRENT_SWEEP_HOLD_MIN_AWAY_SLOPE_MPA_S = 1.0
SERVO_CURRENT_SWEEP_POST_HOLD_THROTTLE_S = 6.0
SERVO_CURRENT_SWEEP_POST_HOLD_THROTTLE_FACTOR = 0.6
CURRENT_SWEEP_HOLD_PAUSE_TOLERANCE_FACTOR = 3.0
CURRENT_SWEEP_HOLD_RESUME_TOLERANCE_FACTOR = 1.5
CURRENT_SWEEP_HOLD_RESUME_STABLE_S = 0.5
CURRENT_SWEEP_HOLD_ESTIMATE_MIN_S = 10.0
CURRENT_SWEEP_HOLD_ESTIMATE_FRACTION = 0.25
CURRENT_SWEEP_HOLD_ESTIMATE_MAX_S = 60.0
CURRENT_SWEEP_ETA_MEASURED_WEIGHT_START = 0.05
CURRENT_SWEEP_ETA_MEASURED_WEIGHT_FULL = 0.25
SERVO_FULL_SPEED_ERROR_RATIO = 8.0
SERVO_CRUISE_FEEDBACK_SAFETY_FACTOR = 1.25
SERVO_MOTION_SETTLE_AFTER_MOVE_S = 0.05
SERVO_AUTO_TOLERANCE_LOAD_G = 0.005
CALIBRATION_MAX_AUTO_ACCEPTANCE_LOAD_G = 0.05
WIRE_BREAK_MIN_SETPOINT_MA = 5.0
WIRE_BREAK_MAX_MEASURED_MA = 0.5
WIRE_BREAK_VOLTAGE_LIMIT_FRACTION = 0.95
CONTINUITY_CURRENT_DEFAULT_MA = 1.0
MIN_RECIPE_CURRENT_MA = 1.0
RAW_SCALE_DISPLAY_LIMIT_DEFAULT_G = 30.0
SETUP_PRELOAD_OVERLOAD_FACTOR = 2.0
CURRENT_SWEEP_BASIS_BY_MODE = {
    CURRENT_SWEEP_LOAD: HSW_BASIS_LOAD_G,
    CURRENT_SWEEP_STRESS: HSW_BASIS_STRESS_MPA,
    CURRENT_SWEEP_STRAIN: HSW_BASIS_STRAIN_PCT,
}
CURRENT_SWEEP_TARGET_DEFAULTS_BY_MODE = {
    CURRENT_SWEEP_LOAD: (0.0, 9.0, 3.0, 0.1),
    CURRENT_SWEEP_STRESS: (50.0, 1000.0, 50.0, 5.0),
    CURRENT_SWEEP_STRAIN: (0.0, 0.5, 0.1, 0.05),
}
CURRENT_SWEEP_MODES = frozenset(CURRENT_SWEEP_BASIS_BY_MODE) | {LEGACY_CURRENT_SWEEP}
CURRENT_TARGET_VALUE_MODES = frozenset(CURRENT_SWEEP_TARGET_DEFAULTS_BY_MODE)
RECIPE_FILENAME_TOKENS = {
    CURRENT_SWEEP_LOAD: "iso-load",
    CURRENT_SWEEP_STRESS: "iso-stress",
    CURRENT_SWEEP_STRAIN: "iso-strain",
    CONSTANT_CURRENT_STRAIN_SWEEP: "iso-current",
}
CALIBRATION_MODES = frozenset({CALIBRATION, CALIBRATION_COPPER})
PROJECT_ROW_DIAMETER_KEYS = ("d (µm)", "d (um)", "d", "Diameter", "diameter_um")
PROJECT_ROW_CURRENT_KEYS = (
    "Stress/strain current (mA)",
    "Current (mA)",
    "Fracture stress/strain current (mA)",
)
PROJECT_ROW_MICROWIRE_KEYS = ("Microwire", "Wire")
PROJECT_ROW_SPECIMEN_KEYS = ("Specimen", "Sample", "Piece", "Sample name")


def _default_download_dir() -> str:
    home = Path.home()
    candidates = [
        Path(os.environ.get("USERPROFILE", "")) / "Downloads",
        home / "Downloads",
        home / "downloads",
    ]
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_dir():
                return str(candidate)
        except Exception:
            continue
    fallback = home / "Downloads"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(fallback)


def _mini_dma_settings() -> QtCore.QSettings:
    ini_dir = os.environ.get("MINI_DMA_QSETTINGS_INI_DIR", "").strip()
    if ini_dir:
        settings_path = Path(ini_dir) / "mini_dma_logger.ini"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        return QtCore.QSettings(str(settings_path), QtCore.QSettings.Format.IniFormat)
    return QtCore.QSettings("microwire", "mini_dma_logger")


def _session_paths_for_basename(directory: Path, basename: str) -> tuple[Path, Path, Path, Path]:
    run_dir = directory / ((basename or "").strip() or DEFAULT_LOG_BASENAME)
    return (
        run_dir / SESSION_MEASUREMENT_TX,
        run_dir / SESSION_MEASUREMENT_CSV,
        run_dir / SESSION_METADATA_JSON,
        run_dir / SESSION_RAW_SCALE_CSV,
    )


def _session_paths_exist(paths: Sequence[Path]) -> bool:
    if any(path.exists() for path in paths) or (bool(paths) and paths[0].parent.exists()):
        return True
    if not paths:
        return False
    run_dir = paths[0].parent
    base_dir = run_dir.parent
    basename = run_dir.name
    legacy_paths = (
        base_dir / f"{basename}.txt",
        base_dir / f"{basename}.csv",
        base_dir / f"{basename}.json",
        base_dir / f"{basename}.scale_raw.csv",
    )
    if any(path.exists() for path in legacy_paths):
        return True
    run_prefix = f"{basename}_run"
    return any(
        sibling.name.startswith(run_prefix) and RUN_SUFFIX_PATTERN.search(sibling.name)
        for sibling in base_dir.glob(f"{basename}_run*")
    )


def _session_setup_paths_for_measurement(txt_path: Path) -> tuple[Path, Path]:
    return txt_path.parent / SESSION_SETUP_TX, txt_path.parent / SESSION_SETUP_CSV


def _clean_session_basename(basename: str) -> str:
    clean_basename = (basename or "").strip() or DEFAULT_LOG_BASENAME
    return RUN_SUFFIX_PATTERN.sub("", clean_basename).strip() or DEFAULT_LOG_BASENAME


def _next_run_session_paths(directory: Path, basename: str) -> tuple[str, tuple[Path, Path, Path, Path]]:
    clean_basename = _clean_session_basename(basename)
    for run_number in range(2, 10000):
        candidate = f"{clean_basename}_run{run_number:02d}"
        candidate_paths = _session_paths_for_basename(directory, candidate)
        if not _session_paths_exist(candidate_paths):
            return candidate, candidate_paths
    raise RuntimeError(f"Could not find a free run filename for {clean_basename!r}.")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _utc_timestamp_from_epoch(timestamp_s: float) -> str:
    return datetime.fromtimestamp(timestamp_s, timezone.utc).isoformat(timespec="milliseconds")


def _utc_filename_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _find_ticcmd() -> str:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    candidates = [
        shutil.which("ticcmd"),
        str(Path(local_app_data) / "Programs" / "Pololu" / "Tic" / "bin" / "ticcmd.exe")
        if local_app_data
        else None,
        r"C:\Program Files (x86)\Pololu\Tic\bin\ticcmd.exe",
        r"C:\Program Files\Pololu\Tic\bin\ticcmd.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if Path(candidate).exists():
            return str(candidate)
    return "ticcmd"


def _hidden_subprocess_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def _decode_escape_text(text: str) -> bytes:
    if not text:
        return b""
    normalized = text.encode("utf-8").decode("unicode_escape")
    return normalized.encode("utf-8")


def _format_duration(seconds: float) -> str:
    if seconds < 60.0:
        return f"{seconds:.1f} s"
    minutes = seconds / 60.0
    if minutes < 60.0:
        return f"{minutes:.1f} min"
    hours = int(minutes // 60)
    remaining_minutes = minutes - (hours * 60)
    return f"{hours:d} h {remaining_minutes:.0f} min"


def _format_compact_number(value: float, *, decimals: int = 4) -> str:
    text = f"{float(value):.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _format_compact_unit(value: float, unit: str, *, decimals: int = 4) -> str:
    return f"{_format_compact_number(value, decimals=decimals)} {unit}"


def _parse_first_float(text: str) -> float | None:
    match = FLOAT_PATTERN.search(text)
    if not match:
        return None
    token = match.group(0).replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None


def _supply_profile_id_from_idn(idn_text: str) -> str | None:
    upper_raw = str(idn_text or "").upper()
    if "HMP4040" in upper_raw:
        return "hmp4040"
    if "HMP4030" in upper_raw or "HAMEG" in upper_raw:
        return "hmp4030"
    if "OWON" in upper_raw or "SPE6102" in upper_raw:
        return "owon_spe6102"
    return None


def _safe_float(value: Any) -> float | None:
    try:
        numeric = float(str(value).replace(",", "."))
    except Exception:
        return None
    return numeric if math.isfinite(numeric) else None


def _normalized_token(text: Any) -> str:
    token = str(text or "").strip().lower()
    token = token.replace("_", "").replace("-", "").replace(" ", "")
    return token


def _normalized_microwire_token(text: Any) -> str:
    token = str(text or "").strip().lower()
    token = token.replace("_", "/").replace("-", "/")
    token = re.sub(r"\s+", "", token)
    return token


def _normalized_column_key(text: Any) -> str:
    token = str(text or "").strip().lower()
    token = token.replace("µ", "u").replace("μ", "u").replace("?", "u")
    return re.sub(r"[^a-z0-9]+", "", token)


def _project_row_value(row: Mapping[str, Any], aliases: Iterable[str]) -> Any:
    alias_map = {_normalized_column_key(alias): alias for alias in aliases}
    for key, value in row.items():
        if _normalized_column_key(key) in alias_map:
            return value
    for alias in aliases:
        if alias in row:
            return row.get(alias)
    return None


def _extract_status_value(text: str, label: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


def _extract_status_float(text: str, label: str) -> float | None:
    value = _extract_status_value(text, label)
    if value is None:
        return None
    return _parse_first_float(value)


def _extract_first_int(text: str) -> int | None:
    match = re.search(r"[-+]?\d+", text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _extract_tic_current_limit_mA(status_text: str | None) -> int | None:
    value = _extract_status_value(status_text or "", "Current limit")
    if value is None:
        return None
    return _extract_first_int(value)


def normalize_tic_step_mode(step_mode: object) -> str | None:
    text = str(step_mode or "").strip().lower()
    if not text:
        return None
    text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    if "full" in text:
        return "full"
    if "half" in text:
        return "2"
    fraction_match = re.search(r"1\s*/\s*(2|4|8|16|32)\b", text)
    if fraction_match:
        return fraction_match.group(1)
    compact = text.replace(" ", "_")
    if compact == "2_100p":
        return "2_100p"
    token_match = re.fullmatch(r"(1|2|4|8|16|32)", text)
    if token_match:
        token = token_match.group(1)
        return "full" if token == "1" else token
    return None


def tic_step_mode_factor(step_mode: object) -> int | None:
    normalized = normalize_tic_step_mode(step_mode)
    if normalized is None:
        return None
    return TIC_STEP_MODE_FACTORS.get(normalized)


def tic_units_per_mm(full_steps_per_mm: float, step_mode: object) -> float:
    factor = tic_step_mode_factor(step_mode)
    if factor is None:
        raise ValueError(f"Unsupported Tic step mode: {step_mode!r}")
    return float(full_steps_per_mm) * float(factor)


def _tic_step_mode_label(step_mode: object) -> str:
    normalized = normalize_tic_step_mode(step_mode)
    if normalized is None:
        return str(step_mode or "unknown")
    for label, value in TIC_STEP_MODE_OPTIONS:
        if value == normalized:
            return label
    return str(step_mode)


def safe_tic_current_limit_mA(target_mA: float) -> int:
    target = max(0.0, float(target_mA))
    safe_value = 0
    for candidate in TIC_T500_CURRENT_LIMITS_MA:
        if candidate <= target:
            safe_value = candidate
        else:
            break
    return safe_value


def tic_t500_current_limit_code(target_mA: float) -> int:
    safe_value = safe_tic_current_limit_mA(target_mA)
    return TIC_T500_CURRENT_LIMITS_MA.index(safe_value)


def apply_tic_current_limit_mA(controller: object, target_mA: float) -> int:
    safe_value = safe_tic_current_limit_mA(target_mA)
    run = getattr(controller, "run", None)
    if not callable(run):
        raise RuntimeError("Tic current limit requires ticcmd transport.")
    run("--current", str(safe_value))
    return safe_value


def _parse_tic_list_output(text: str) -> list[tuple[str, str]]:
    devices: list[tuple[str, str]] = []
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate or "," not in candidate:
            continue
        serial_token, _, name_token = candidate.partition(",")
        serial_number = serial_token.strip()
        name = name_token.strip()
        if serial_number:
            devices.append((serial_number, name))
    return devices


def _read_serial_bytes(
    port_name: str,
    *,
    baudrate: int,
    payload: bytes,
    timeout_s: float = 0.35,
    total_wait_s: float = 1.0,
) -> bytes:
    if serial is None:
        raise RuntimeError("pyserial is not available.")

    with serial.Serial(port_name, baudrate=baudrate, timeout=timeout_s, write_timeout=timeout_s) as port:
        port.reset_input_buffer()
        port.reset_output_buffer()
        port.rts = False
        port.dtr = False
        time.sleep(0.08)
        if payload:
            port.write(payload)
            port.flush()

        chunks: list[bytes] = []
        deadline = time.time() + max(total_wait_s, timeout_s)
        while time.time() < deadline:
            waiting = port.in_waiting
            chunk = port.read(waiting or 1)
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)


def strain_percent(
    displacement_mm: float,
    initial_length_mm: float,
    reference_mm: float,
) -> float | None:
    if initial_length_mm <= 0.0:
        return None
    return ((displacement_mm - reference_mm) / initial_length_mm) * 100.0


def stress_mpa_from_load_g(load_g: float, diameter_mm: float) -> float | None:
    if diameter_mm <= 0.0:
        return None
    area_mm2 = (math.pi * diameter_mm * diameter_mm) / 4.0
    if area_mm2 <= 0.0:
        return None
    force_n = load_g * GRAVITY_MS2 / 1000.0
    return force_n / area_mm2


def load_g_from_stress_mpa(stress_mpa: float, diameter_mm: float) -> float | None:
    if diameter_mm <= 0.0:
        return None
    area_mm2 = (math.pi * diameter_mm * diameter_mm) / 4.0
    if area_mm2 <= 0.0:
        return None
    force_n = stress_mpa * area_mm2
    return (force_n * 1000.0) / GRAVITY_MS2


@dataclass(frozen=True)
class ScaleSample:
    timestamp_s: float
    raw_g: float
    applied_load_g: float
    raw_text: str


@dataclass(frozen=True)
class ScaleIntervalSummary:
    raw_last_g: float | None
    applied_last_g: float | None
    load_mean_g: float | None
    load_std_g: float | None
    load_min_g: float | None
    load_max_g: float | None
    sample_count: int
    sample_rate_hz: float | None


@dataclass(frozen=True)
class ScaleControlSignal:
    value: float
    latest_value: float
    noise: float
    slope_per_s: float
    sample_count: int
    timestamp_s: float


class ScaleSignalBuffer:
    def __init__(self, *, window_s: float = 10.0) -> None:
        self.window_s = max(1.0, float(window_s))
        self._samples: deque[ScaleSample] = deque()

    def clear(self) -> None:
        self._samples.clear()

    def add_sample(
        self,
        *,
        timestamp_s: float,
        raw_g: float,
        applied_load_g: float,
        raw_text: str,
    ) -> ScaleSample:
        sample = ScaleSample(
            timestamp_s=float(timestamp_s),
            raw_g=float(raw_g),
            applied_load_g=float(applied_load_g),
            raw_text=str(raw_text),
        )
        self._samples.append(sample)
        self._trim(timestamp_s=float(timestamp_s))
        return sample

    def latest(self) -> ScaleSample | None:
        return self._samples[-1] if self._samples else None

    def interval_summary(
        self,
        *,
        since_s: float | None,
        until_s: float | None,
    ) -> ScaleIntervalSummary:
        selected = self._select_samples(since_s=since_s, until_s=until_s)
        return self._summarize(selected)

    def recent_summary(self, *, now_s: float | None = None, window_s: float = 1.0) -> ScaleIntervalSummary:
        if now_s is None:
            latest = self.latest()
            now_s = latest.timestamp_s if latest is not None else time.time()
        return self.interval_summary(since_s=now_s - max(0.001, float(window_s)), until_s=now_s)

    def recent_samples(self, *, now_s: float | None = None, window_s: float = 1.0) -> list[ScaleSample]:
        if now_s is None:
            latest = self.latest()
            now_s = latest.timestamp_s if latest is not None else time.time()
        return self._select_samples(since_s=now_s - max(0.001, float(window_s)), until_s=now_s)

    def sample_rate_hz(self, *, now_s: float | None = None, window_s: float = 2.0) -> float | None:
        return self.recent_summary(now_s=now_s, window_s=window_s).sample_rate_hz

    def _trim(self, *, timestamp_s: float) -> None:
        cutoff_s = timestamp_s - self.window_s
        while self._samples and self._samples[0].timestamp_s < cutoff_s:
            self._samples.popleft()

    def _select_samples(
        self,
        *,
        since_s: float | None,
        until_s: float | None,
    ) -> list[ScaleSample]:
        selected: list[ScaleSample] = []
        for sample in self._samples:
            if since_s is not None and sample.timestamp_s < since_s:
                continue
            if until_s is not None and sample.timestamp_s > until_s:
                continue
            selected.append(sample)
        return selected

    def _summarize(self, samples: Sequence[ScaleSample]) -> ScaleIntervalSummary:
        if not samples:
            return ScaleIntervalSummary(
                raw_last_g=None,
                applied_last_g=None,
                load_mean_g=None,
                load_std_g=None,
                load_min_g=None,
                load_max_g=None,
                sample_count=0,
                sample_rate_hz=None,
            )
        loads = [sample.applied_load_g for sample in samples]
        count = len(loads)
        mean_load = sum(loads) / count
        if count > 1:
            variance = sum((value - mean_load) ** 2 for value in loads) / (count - 1)
            std_load = math.sqrt(max(0.0, variance))
            duration_s = max(0.0, samples[-1].timestamp_s - samples[0].timestamp_s)
            sample_rate_hz = (count - 1) / duration_s if duration_s > 0.0 else None
        else:
            std_load = 0.0
            sample_rate_hz = None
        return ScaleIntervalSummary(
            raw_last_g=samples[-1].raw_g,
            applied_last_g=samples[-1].applied_load_g,
            load_mean_g=mean_load,
            load_std_g=std_load,
            load_min_g=min(loads),
            load_max_g=max(loads),
            sample_count=count,
            sample_rate_hz=sample_rate_hz,
        )


@dataclass
class MeasurementPoint:
    elapsed_s: float
    timestamp_utc: str
    raw_position_mm: float
    position_mm: float
    raw_load_g: float
    load_g: float
    preload_state: str
    strain_pct: float | None
    stress_mpa: float | None
    current_set_mA: float | None
    current_measured_mA: float | None
    voltage_V: float | None
    resistance_ohm: float | None
    power_W: float | None
    automation_phase: str
    automation_basis: str | None
    automation_target_value: float | None
    plateau_index: int | None
    plateau_label: str | None
    load_raw_last_g: float | None = None
    load_mean_g: float | None = None
    load_std_g: float | None = None
    load_min_g: float | None = None
    load_max_g: float | None = None
    load_sample_count: int = 0
    scale_sample_rate_hz: float | None = None
    current_zero_position_mm: float | None = None
    current_l0_mm: float | None = None
    current_relative_position_mm: float | None = None
    current_relative_strain_pct: float | None = None
    plot_gap_before: bool = False


@dataclass
class AutomationStep:
    action: str
    target_mm: float | None = None
    relative_mm: float | None = None
    target_value: float | None = None
    target_start_value: float | None = None
    target_end_value: float | None = None
    target_ramp_rate_value_s: float | None = None
    basis: str | None = None
    current_mA: float | None = None
    current_start_mA: float | None = None
    current_end_mA: float | None = None
    current_ramp_rate_mA_s: float | None = None
    current_hold_enabled: bool = False
    current_hold_pause_tolerance_factor: float | None = None
    current_hold_resume_tolerance_factor: float | None = None
    current_hold_resume_stable_s: float | None = None
    mechanical_step_basis: str | None = None
    mechanical_step_value: float | None = None
    mechanical_step_speed_mm_s: float | None = None
    mechanical_step_limit: int | None = None
    duration_s: float | None = None
    note: str = ""


@dataclass
class MotorStepCalibrationPoint:
    point_index: int
    timestamp_utc: str
    tic_position_steps: int
    entered_displacement_mm: float
    move_command_steps: int = 0
    move_speed_steps_per_s: float = 0.0


@dataclass
class AutomationResumeState:
    steps: list[AutomationStep]
    index: int
    interval_ms: int
    total_steps: int
    name: str
    origin_mm: float
    summary: str
    current_setpoint_mA: float | None = None


@dataclass(frozen=True)
class SetupUnloadBaselineFit:
    zero_position_mm: float
    slope_mpa_per_mm: float
    intercept_mpa: float
    r_squared: float
    fit_point_count: int
    max_stress_mpa: float
    stress_floor_mpa: float


def _numeric_values(values: Iterable[float | None]) -> list[float]:
    return [float(value) for value in values if value is not None and math.isfinite(float(value))]


def _summary_stats(values: Sequence[float]) -> dict[str, float | int | None]:
    count = len(values)
    if count == 0:
        return {
            "sample_count": 0,
            "load_mean_g": None,
            "load_std_g": None,
            "load_min_g": None,
            "load_max_g": None,
        }
    mean_value = sum(values) / count
    if count > 1:
        variance = sum((value - mean_value) ** 2 for value in values) / (count - 1)
        std_value = math.sqrt(max(0.0, variance))
    else:
        std_value = 0.0
    return {
        "sample_count": count,
        "load_mean_g": mean_value,
        "load_std_g": std_value,
        "load_min_g": min(values),
        "load_max_g": max(values),
    }


def _linear_load_fit(points: Sequence[MeasurementPoint]) -> dict[str, float | int | None]:
    pairs = [
        (float(point.raw_position_mm), float(point.load_g))
        for point in points
        if math.isfinite(float(point.raw_position_mm)) and math.isfinite(float(point.load_g))
    ]
    count = len(pairs)
    if count < 2:
        return {
            "sample_count": count,
            "stiffness_g_per_mm": None,
            "intercept_g": None,
            "position_start_mm": pairs[0][0] if pairs else None,
            "position_end_mm": pairs[-1][0] if pairs else None,
            "load_start_g": pairs[0][1] if pairs else None,
            "load_end_g": pairs[-1][1] if pairs else None,
        }
    x_values = [pair[0] for pair in pairs]
    y_values = [pair[1] for pair in pairs]
    x_mean = sum(x_values) / count
    y_mean = sum(y_values) / count
    denominator = sum((x_value - x_mean) ** 2 for x_value in x_values)
    if denominator <= 0.0:
        slope = None
        intercept = None
    else:
        slope = sum((x_value - x_mean) * (y_value - y_mean) for x_value, y_value in pairs) / denominator
        intercept = y_mean - slope * x_mean
    return {
        "sample_count": count,
        "stiffness_g_per_mm": None if slope is None else abs(slope),
        "signed_stiffness_g_per_mm": slope,
        "intercept_g": intercept,
        "position_start_mm": x_values[0],
        "position_end_mm": x_values[-1],
        "load_start_g": y_values[0],
        "load_end_g": y_values[-1],
        "load_min_g": min(y_values),
        "load_max_g": max(y_values),
    }


def _linear_stress_strain_fit(points: Sequence[MeasurementPoint]) -> dict[str, float | int | None]:
    pairs: list[tuple[float, float]] = []
    for point in points:
        if point.strain_pct is None or point.stress_mpa is None:
            continue
        strain_pct = float(point.strain_pct)
        stress_mpa = float(point.stress_mpa)
        if math.isfinite(strain_pct) and math.isfinite(stress_mpa):
            pairs.append((strain_pct / 100.0, stress_mpa))
    count = len(pairs)
    if count < 2:
        return {
            "sample_count": count,
            "modulus_mpa": None,
            "modulus_gpa": None,
            "signed_modulus_mpa": None,
            "intercept_mpa": None,
            "strain_start_pct": pairs[0][0] * 100.0 if pairs else None,
            "strain_end_pct": pairs[-1][0] * 100.0 if pairs else None,
            "stress_start_mpa": pairs[0][1] if pairs else None,
            "stress_end_mpa": pairs[-1][1] if pairs else None,
        }
    x_values = [pair[0] for pair in pairs]
    y_values = [pair[1] for pair in pairs]
    x_mean = sum(x_values) / count
    y_mean = sum(y_values) / count
    denominator = sum((x_value - x_mean) ** 2 for x_value in x_values)
    if denominator <= 0.0:
        slope = None
        intercept = None
    else:
        slope = sum((x_value - x_mean) * (y_value - y_mean) for x_value, y_value in pairs) / denominator
        intercept = y_mean - slope * x_mean
    modulus_mpa = None if slope is None else abs(slope)
    return {
        "sample_count": count,
        "modulus_mpa": modulus_mpa,
        "modulus_gpa": None if modulus_mpa is None else modulus_mpa / 1000.0,
        "signed_modulus_mpa": slope,
        "intercept_mpa": intercept,
        "strain_start_pct": x_values[0] * 100.0,
        "strain_end_pct": x_values[-1] * 100.0,
        "strain_min_pct": min(x_values) * 100.0,
        "strain_max_pct": max(x_values) * 100.0,
        "stress_start_mpa": y_values[0],
        "stress_end_mpa": y_values[-1],
        "stress_min_mpa": min(y_values),
        "stress_max_mpa": max(y_values),
    }


def _fit_x_at_load(fit: Mapping[str, float | int | None], load_g: float) -> float | None:
    slope = fit.get("signed_stiffness_g_per_mm")
    intercept = fit.get("intercept_g")
    if slope is None or intercept is None:
        return None
    slope_value = float(slope)
    if abs(slope_value) <= 1e-12:
        return None
    return (float(load_g) - float(intercept)) / slope_value


def _backlash_from_fits(
    forward_fit: Mapping[str, float | int | None],
    reverse_fit: Mapping[str, float | int | None],
) -> float | None:
    forward_min = forward_fit.get("load_min_g")
    forward_max = forward_fit.get("load_max_g")
    reverse_min = reverse_fit.get("load_min_g")
    reverse_max = reverse_fit.get("load_max_g")
    if None in {forward_min, forward_max, reverse_min, reverse_max}:
        return None
    low = max(float(forward_min), float(reverse_min))
    high = min(float(forward_max), float(reverse_max))
    if high < low:
        return None
    probe_load = (low + high) / 2.0
    forward_x = _fit_x_at_load(forward_fit, probe_load)
    reverse_x = _fit_x_at_load(reverse_fit, probe_load)
    if forward_x is None or reverse_x is None:
        return None
    return abs(forward_x - reverse_x)


def calibration_report_from_points(points: Sequence[MeasurementPoint]) -> dict[str, Any]:
    baseline = [point for point in points if point.automation_phase == CALIBRATION_BASELINE]
    forward = [point for point in points if point.automation_phase == CALIBRATION_FORWARD]
    reverse = [point for point in points if point.automation_phase == CALIBRATION_REVERSE]
    baseline_stats = _summary_stats(_numeric_values(point.load_g for point in baseline))
    forward_fit = _linear_load_fit(forward)
    reverse_fit = _linear_load_fit(reverse)
    forward_stress_strain = _linear_stress_strain_fit(forward)
    reverse_stress_strain = _linear_stress_strain_fit(reverse)
    stiffness_values = _numeric_values(
        [
            forward_fit.get("stiffness_g_per_mm"),
            reverse_fit.get("stiffness_g_per_mm"),
        ]
    )
    average_stiffness = sum(stiffness_values) / len(stiffness_values) if stiffness_values else None
    modulus_values = _numeric_values(
        [
            forward_stress_strain.get("modulus_mpa"),
            reverse_stress_strain.get("modulus_mpa"),
        ]
    )
    average_modulus_mpa = sum(modulus_values) / len(modulus_values) if modulus_values else None
    backlash_mm = _backlash_from_fits(forward_fit, reverse_fit)
    status = "ok" if average_stiffness is not None and backlash_mm is not None else "insufficient_data"
    return {
        "status": status,
        "baseline": baseline_stats,
        "forward": forward_fit,
        "reverse": reverse_fit,
        "average_stiffness_g_per_mm": average_stiffness,
        "backlash_mm": backlash_mm,
        "stress_strain": {
            "forward": forward_stress_strain,
            "reverse": reverse_stress_strain,
            "average_modulus_mpa": average_modulus_mpa,
            "average_modulus_gpa": None if average_modulus_mpa is None else average_modulus_mpa / 1000.0,
        },
        "sample_counts": {
            "baseline": len(baseline),
            "forward": len(forward),
            "reverse": len(reverse),
        },
    }


def motor_step_calibration_report_from_points(
    points: Sequence[MotorStepCalibrationPoint],
) -> dict[str, Any]:
    if len(points) < 2:
        return {
            "status": "insufficient_data",
            "reason": "at_least_two_points_required",
            "sample_count": len(points),
        }

    baseline = points[0]
    relative_pairs = [
        (
            int(point.tic_position_steps) - int(baseline.tic_position_steps),
            float(point.entered_displacement_mm) - float(baseline.entered_displacement_mm),
        )
        for point in points
    ]
    x_values = [float(pair[0]) for pair in relative_pairs]
    y_values = [float(pair[1]) for pair in relative_pairs]
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    denominator = sum((x_value - x_mean) ** 2 for x_value in x_values)
    if denominator <= 0.0:
        return {
            "status": "insufficient_data",
            "reason": "tic_positions_do_not_change",
            "sample_count": len(points),
        }
    slope_mm_per_step = sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x_values, y_values, strict=False)
    ) / denominator
    if abs(slope_mm_per_step) <= 1e-12:
        return {
            "status": "insufficient_data",
            "reason": "external_displacement_does_not_change",
            "sample_count": len(points),
            "signed_mm_per_step": slope_mm_per_step,
        }

    intercept_mm = y_mean - slope_mm_per_step * x_mean
    fitted_values = [intercept_mm + slope_mm_per_step * x_value for x_value in x_values]
    residuals = [observed - fitted for observed, fitted in zip(y_values, fitted_values, strict=False)]
    ss_res = sum(residual**2 for residual in residuals)
    ss_tot = sum((y_value - y_mean) ** 2 for y_value in y_values)
    r2 = 1.0 if ss_tot <= 1e-18 and ss_res <= 1e-18 else 1.0 - (ss_res / ss_tot if ss_tot > 0.0 else 0.0)
    signed_steps_per_mm = 1.0 / slope_mm_per_step
    point_estimates: list[dict[str, float | int | None]] = []
    for point, (relative_steps, relative_mm) in zip(points[1:], relative_pairs[1:], strict=False):
        if abs(relative_mm) <= 1e-12:
            signed_estimate = None
            estimate = None
        else:
            signed_estimate = float(relative_steps) / float(relative_mm)
            estimate = abs(signed_estimate)
        point_estimates.append(
            {
                "point_index": point.point_index,
                "relative_tic_steps": relative_steps,
                "relative_displacement_mm": relative_mm,
                "signed_steps_per_mm_from_baseline": signed_estimate,
                "steps_per_mm_from_baseline": estimate,
            }
        )

    return {
        "status": "ok",
        "sample_count": len(points),
        "baseline_tic_position_steps": int(baseline.tic_position_steps),
        "baseline_displacement_mm": float(baseline.entered_displacement_mm),
        "signed_mm_per_step": slope_mm_per_step,
        "mm_per_step": abs(slope_mm_per_step),
        "signed_steps_per_mm": signed_steps_per_mm,
        "recommended_steps_per_mm": abs(signed_steps_per_mm),
        "fit_intercept_mm": intercept_mm,
        "r2": max(0.0, min(1.0, r2)),
        "max_residual_mm": max(abs(residual) for residual in residuals),
        "movement_direction": (
            "external_reading_increases_with_positive_tic_steps"
            if slope_mm_per_step > 0.0
            else "external_reading_decreases_with_positive_tic_steps"
        ),
        "point_estimates": point_estimates,
    }


@dataclass
class PlotChannel:
    key: str
    label: str
    color: str
    getter: Callable[[MeasurementPoint], float | None]


@dataclass
class PlotTileWidgets:
    visible: QtWidgets.QCheckBox
    x_combo: QtWidgets.QComboBox
    y_left_combo: QtWidgets.QComboBox
    y_right_combo: QtWidgets.QComboBox


@dataclass
class PyqtGraphPlotBundle:
    widget: Any
    plot_item: Any
    left_curve: Any
    right_view: Any | None = None
    right_curve: Any | None = None
    sync_right_view: Callable[[], None] | None = None


@dataclass
class ProjectImportResult:
    path: Path
    section: str
    diameter_mm: float | None
    current_mA: float | None
    matched_row: dict[str, Any]


class MicrowireLineEdit(QtWidgets.QLineEdit):
    """Microwire entry with slash display and filename-safe token conversion."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._normalizing = False
        self.setPlaceholderText("e.g. 156/2")
        self.textEdited.connect(self._normalize_on_edit)

    @staticmethod
    def _split_parts(value: object) -> tuple[str, str]:
        text = str(value or "").strip().lower()
        if not text:
            return "", ""
        text = text.replace("\\", "/").replace("_", "/")
        text = re.sub(r"\s+", "", text)
        if "/" in text:
            left, right = text.split("/", 1)
        else:
            tokens = re.findall(r"\d+", text)
            if len(tokens) >= 2:
                left, right = tokens[0], tokens[1]
            elif len(tokens) == 1:
                left, right = tokens[0], ""
            else:
                left, right = "", ""
        return re.sub(r"\D", "", left), re.sub(r"\D", "", right)

    @classmethod
    def to_display_text(cls, value: object) -> str:
        left, right = cls._split_parts(value)
        return f"{left}/{right}" if (left or right) else ""

    @classmethod
    def to_filename_token(cls, value: object) -> str:
        left, right = cls._split_parts(value)
        if left and right:
            return f"{left}_{right}"
        if left:
            return left
        if right:
            return right
        return ""

    def _normalize_on_edit(self, _text: str) -> None:
        if self._normalizing:
            return
        raw_text = self.text()
        raw_cursor = self.cursorPosition()
        compact = re.sub(r"\s+", "", raw_text).replace("\\", "/").replace("_", "/")
        if "/" not in compact:
            digits = re.sub(r"\D", "", compact)
            if len(digits) <= 3:
                normalized = digits
                cursor = min(raw_cursor, len(normalized))
            else:
                normalized = f"{digits[:3]}/{digits[3:]}"
                cursor = len(normalized)
        else:
            normalized = self.to_display_text(raw_text)
            cursor = min(len(normalized), raw_cursor)
            slash_index = normalized.find("/")
            if slash_index >= 0 and raw_cursor > slash_index:
                cursor = len(normalized)
        self._normalizing = True
        self.setText(normalized)
        self.setCursorPosition(cursor)
        self._normalizing = False


class CompactDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """Double spin box that avoids padded zero-only decimals in the editor text."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setKeyboardTracking(False)
        self.setCorrectionMode(QtWidgets.QAbstractSpinBox.CorrectionMode.CorrectToNearestValue)
        self.setMinimumWidth(130)

    def textFromValue(self, value: float) -> str:  # type: ignore[override]
        return _format_compact_number(value, decimals=self.decimals())

    def valueFromText(self, text: str) -> float:  # type: ignore[override]
        suffix = self.suffix().strip()
        cleaned = text.strip()
        if suffix and cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
        parsed = _parse_first_float(cleaned)
        return self.value() if parsed is None else parsed

    def validate(self, text: str, pos: int) -> tuple[QtGui.QValidator.State, str, int]:  # type: ignore[override]
        cleaned = text.strip()
        suffix = self.suffix().strip()
        if suffix and cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
        if cleaned in {"", "+", "-", ".", ",", "+.", "-.", "+,", "-,"}:
            return (QtGui.QValidator.State.Intermediate, text, pos)
        parsed = _parse_first_float(cleaned)
        if parsed is None:
            return (QtGui.QValidator.State.Invalid, text, pos)
        if self.minimum() <= parsed <= self.maximum():
            return (QtGui.QValidator.State.Acceptable, text, pos)
        return (QtGui.QValidator.State.Intermediate, text, pos)

    def fixup(self, text: str) -> str:  # type: ignore[override]
        suffix = self.suffix().strip()
        cleaned = text.strip()
        if suffix and cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
        parsed = _parse_first_float(cleaned)
        if parsed is None:
            parsed = self.value()
        clamped = min(max(parsed, self.minimum()), self.maximum())
        return _format_compact_number(clamped, decimals=self.decimals())


class CurrentPageStackedWidget(QtWidgets.QStackedWidget):
    """Stacked widget that does not reserve space for hidden recipe pages."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(lambda _index: self.updateGeometry())

    def sizeHint(self) -> QtCore.QSize:  # type: ignore[override]
        widget = self.currentWidget()
        return widget.sizeHint() if widget is not None else super().sizeHint()

    def minimumSizeHint(self) -> QtCore.QSize:  # type: ignore[override]
        widget = self.currentWidget()
        return widget.minimumSizeHint() if widget is not None else super().minimumSizeHint()

    def hasHeightForWidth(self) -> bool:  # type: ignore[override]
        return False

    def heightForWidth(self, _width: int) -> int:  # type: ignore[override]
        return self.sizeHint().height()


class PlotConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure plot dashboard")
        self.setModal(False)
        self.resize(860, 320)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.body_layout = QtWidgets.QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.body_layout, stretch=1)
        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        close_button = QtWidgets.QPushButton("Close", self)
        close_button.clicked.connect(self.close)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)


class CollapsibleSection(QtWidgets.QFrame):
    def __init__(
        self,
        title: str,
        *,
        expanded: bool = True,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame { border: 1px solid palette(mid); border-radius: 8px; }")
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        self.toggle_button = QtWidgets.QToolButton(self)
        self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setText(title)
        self.toggle_button.clicked.connect(self._handle_toggled)
        root.addWidget(self.toggle_button)
        self.content = QtWidgets.QWidget(self)
        self.content_layout = QtWidgets.QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(4, 0, 4, 2)
        self.content_layout.setSpacing(8)
        root.addWidget(self.content)
        self.set_expanded(expanded)

    def _handle_toggled(self, checked: bool) -> None:
        self.set_expanded(checked)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if expanded else QtCore.Qt.ArrowType.RightArrow
        )
        self.content.setVisible(expanded)

    def is_expanded(self) -> bool:
        return self.toggle_button.isChecked()


class ScaleWorker(QtCore.QObject):
    measurement_received = QtCore.pyqtSignal(float, str, float)
    status_changed = QtCore.pyqtSignal(str)
    error_occurred = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        port_name: str,
        baudrate: int,
        poll_interval_ms: int,
        request_command: str,
        request_terminator: str,
    ) -> None:
        super().__init__()
        self.port_name = port_name
        self.baudrate = baudrate
        self.poll_interval_ms = max(50, int(poll_interval_ms))
        self.request_command = request_command
        self.request_terminator = request_terminator
        self._stop_event = Event()

    def _read_timeout_s(self) -> float:
        timeout_s = max(0.05, self.poll_interval_ms / 1000.0)
        if self.request_command.strip():
            return max(SCALE_REQUEST_TIMEOUT_MIN_S, timeout_s)
        return timeout_s

    def _request_poll_delay_s(self, *, started_s: float, finished_s: float) -> float:
        elapsed_s = max(0.0, finished_s - started_s)
        return max(0.0, (self.poll_interval_ms / 1000.0) - elapsed_s)

    @QtCore.pyqtSlot()
    def run(self) -> None:
        if serial is None:
            self.error_occurred.emit("pyserial is not available.")
            self.finished.emit()
            return

        port: Any = None
        try:
            timeout_s = self._read_timeout_s()
            request_payload = _decode_escape_text(self.request_command)
            terminator_payload = _decode_escape_text(self.request_terminator)
            port = serial.Serial(
                self.port_name,
                self.baudrate,
                timeout=timeout_s,
                write_timeout=0.2,
            )
            self.status_changed.emit(
                f"Scale connected on {self.port_name} at {self.baudrate} baud."
            )
            while not self._stop_event.is_set():
                request_started_s = time.monotonic()
                if request_payload:
                    try:
                        port.write(request_payload + terminator_payload)
                    except Exception as exc:
                        self.error_occurred.emit(f"Scale write failed: {exc}")
                        break

                raw_bytes = port.readline()
                if raw_bytes:
                    raw_text = raw_bytes.decode("utf-8", errors="ignore").strip()
                    if raw_text:
                        value = _parse_first_float(raw_text)
                        if value is None:
                            self.status_changed.emit(f"Scale raw: {raw_text}")
                        else:
                            self.measurement_received.emit(value, raw_text, time.time())
                if request_payload:
                    delay_s = self._request_poll_delay_s(
                        started_s=request_started_s,
                        finished_s=time.monotonic(),
                    )
                    if delay_s > 0.0:
                        self._stop_event.wait(delay_s)
        except SerialException as exc:
            self.error_occurred.emit(f"Scale connection failed: {exc}")
        except Exception as exc:
            self.error_occurred.emit(f"Scale worker failed: {exc}")
        finally:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass
            self.finished.emit()

    @QtCore.pyqtSlot()
    def stop(self) -> None:
        self._stop_event.set()


class AutomationControlLoop:
    """Run recipe-control ticks on a plain thread, independent of Qt repaint timing."""

    def __init__(
        self,
        tick_callback: Callable[[], None],
        *,
        error_callback: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._tick_callback = tick_callback
        self._error_callback = error_callback
        self._condition = Condition()
        self._thread: Thread | None = None
        self._running = False
        self._paused = False
        self._interval_s = DEFAULT_CONTROL_INTERVAL_MS / 1000.0

    def start(self, interval_ms: int) -> None:
        interval_s = max(0.001, float(interval_ms) / 1000.0)
        with self._condition:
            self._interval_s = interval_s
            if self._running:
                self._paused = False
                self._condition.notify_all()
                return
            self._running = True
            self._paused = False
            self._thread = Thread(target=self._run, name="MiniDMAAutomationControlLoop", daemon=True)
            self._thread.start()
            self._condition.notify_all()

    def pause(self) -> None:
        with self._condition:
            if self._running:
                self._paused = True
                self._condition.notify_all()

    def resume(self) -> None:
        with self._condition:
            if self._running:
                self._paused = False
                self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._running = False
            self._paused = False
            thread = self._thread
            self._condition.notify_all()
        if thread is not None and thread is not current_thread():
            thread.join(timeout=1.0)
        with self._condition:
            if self._thread is thread:
                self._thread = None

    def is_running(self) -> bool:
        with self._condition:
            return self._running

    def is_paused(self) -> bool:
        with self._condition:
            return self._paused

    def _run(self) -> None:
        next_tick_s = time.monotonic()
        while True:
            with self._condition:
                while self._running and self._paused:
                    self._condition.wait()
                    next_tick_s = time.monotonic()
                if not self._running:
                    return
                interval_s = self._interval_s
                delay_s = max(0.0, next_tick_s - time.monotonic())
                if delay_s > 0.0:
                    self._condition.wait(timeout=delay_s)
                    continue
            try:
                self._tick_callback()
            except BaseException as exc:
                with self._condition:
                    self._running = False
                    self._paused = False
                if self._error_callback is not None:
                    self._error_callback(exc)
                return
            next_tick_s = max(next_tick_s + interval_s, time.monotonic())


@dataclass(slots=True)
class MiniDmaControlConfig:
    diameter_mm: float
    initial_length_mm: float
    steps_per_mm: float
    tension_decreases_scale_reading: bool
    positive_motion_is_tension: bool
    zero_on_preload: bool
    preload_threshold_g: float
    backlash_mm: float
    scale_interval_ms: int
    control_interval_ms: int
    log_interval_ms: int
    soft_limits_enabled: bool
    soft_min_mm: float
    soft_max_mm: float
    max_load_enabled: bool
    max_load_g: float
    raw_scale_limit_g: float | None
    jog_mm: float
    motion_speed_mm_s: float
    ramp_speed_mm_s: float
    cycle_speed_mm_s: float
    hold_speed_mm_s: float
    distribution_seek_speed_mm_s: float
    distribution_nudge_mm: float
    calibration_preload_nudge_mm: float
    calibration_preload_speed_mm_s: float
    calibration_speed_mm_s: float
    setup_preload_stress_mpa: float
    setup_preload_duration_s: float
    setup_return_duration_s: float
    setup_slack_speed_strain_pct_s: float
    setup_slack_step_cap_stress_mpa: float
    setup_preload_tolerance_mpa: float
    setup_preload_stable_s: float
    setup_zero_stable_s: float
    current_sweep_target_ramp_rate_value_s: float
    current_sweep_target_speed_mm_s: float
    current_sweep_correction_rate_pct_s: float
    current_sweep_max_correction_strain_pct: float
    current_sweep_max_correction_stress_mpa: float
    current_sweep_hold_correction_stress_mpa: float
    current_sweep_mid_correction_stress_mpa: float
    current_sweep_near_correction_stress_mpa: float
    current_sweep_hold_pause_factor: float
    current_sweep_hold_resume_factor: float
    current_sweep_hold_resume_stable_s: float
    current_sweep_hold_filter_window_s: float
    current_sweep_hold_noise_sigma: float
    current_sweep_hold_min_pause_stress_mpa: float
    current_sweep_hold_min_resume_stress_mpa: float
    current_sweep_tolerance: float
    current_sweep_nudge_mm: float
    current_sweep_balance_speed_mm_s: float
    current_sweep_max_seek_mm: float
    supply_profile_id: str
    supply_current_resolution_mA: float
    motor_supply_enabled: bool
    return_to_origin: bool


def _find_libusb_wheel_library(candidate: str) -> str | None:
    try:
        import libusb._platform as libusb_platform  # type: ignore[import-not-found]
    except Exception:
        return None
    dll_path = Path(str(libusb_platform.DLL_PATH))
    if not dll_path.exists():
        return None
    stem = dll_path.name.lower()
    normalized_candidate = candidate.lower().replace(".dll", "")
    if normalized_candidate in stem:
        return str(dll_path)
    return None


def _load_pyusb_backend() -> tuple[Any, Any, Any | None]:
    try:
        import usb.core as usb_core  # type: ignore[import-not-found]
        import usb.util as usb_util  # type: ignore[import-not-found]
        import usb.backend.libusb1 as usb_libusb1  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - optional dependency guard
        raise RuntimeError("PyUSB is not installed; install pyusb and libusb.") from exc

    backend_errors: list[str] = []
    backend = usb_libusb1.get_backend(find_library=_find_libusb_wheel_library)
    if backend is not None:
        return usb_core, usb_util, backend

    try:
        import libusb_package  # type: ignore[import-not-found]

        backend = usb_libusb1.get_backend(find_library=libusb_package.find_library)
    except Exception as exc:
        backend_errors.append(str(exc))
        backend = usb_libusb1.get_backend()
    if backend is None:
        reason = "; ".join(message for message in backend_errors if message)
        suffix = f" ({reason})" if reason else ""
        raise RuntimeError(
            "No libusb backend is available for PyUSB. Install the 64-bit libusb wheel "
            "or make a matching libusb-1.0.dll available."
            f"{suffix}"
        )
    return usb_core, usb_util, backend


def _tic_usb_command_argument(value: int) -> tuple[int, int]:
    unsigned = int(value) & 0xFFFFFFFF
    return unsigned & 0xFFFF, (unsigned >> 16) & 0xFFFF


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


class NativeTicUsbController:
    def __init__(self, *, device_serial: str = "", timeout_ms: int = 1000) -> None:
        self.device_serial = device_serial.strip()
        self.timeout_ms = max(100, int(timeout_ms))
        self._usb_core, self._usb_util, self._usb_backend = _load_pyusb_backend()
        self._device = self._find_device()

    def _device_string(self, device: Any, index: int | None) -> str:
        if not index:
            return ""
        try:
            return str(self._usb_util.get_string(device, index) or "")
        except Exception:
            return ""

    def _find_device(self) -> Any:
        try:
            raw_devices = list(
                self._usb_core.find(
                    find_all=True,
                    idVendor=TIC_USB_VENDOR_ID,
                    backend=self._usb_backend,
                )
                or []
            )
        except Exception as exc:
            raise RuntimeError(f"Native Tic USB scan failed: {exc}") from exc
        tic_devices: list[tuple[Any, str, str]] = []
        serial_unreadable_devices: list[Any] = []
        for device in raw_devices:
            product = self._device_string(device, getattr(device, "iProduct", None))
            serial = self._device_string(device, getattr(device, "iSerialNumber", None))
            if product and "tic" not in product.lower():
                continue
            tic_devices.append((device, product, serial))
            if self.device_serial and not serial:
                serial_unreadable_devices.append(device)
        for device, _product, serial in tic_devices:
            if self.device_serial and serial and serial != self.device_serial:
                continue
            if self.device_serial and not serial:
                continue
            return device
        if self.device_serial and len(tic_devices) == 1 and len(serial_unreadable_devices) == 1:
            return serial_unreadable_devices[0]
        serial_text = f" with serial {self.device_serial}" if self.device_serial else ""
        raise RuntimeError(f"No Pololu Tic USB device{serial_text} was found.")

    def _quick_command(self, command: int) -> None:
        self._device.ctrl_transfer(
            TIC_USB_REQUEST_OUT,
            command,
            0,
            0,
            None,
            timeout=self.timeout_ms,
        )

    def _command_7bit(self, command: int, value: int) -> None:
        self._device.ctrl_transfer(
            TIC_USB_REQUEST_OUT,
            command,
            int(value) & 0x7F,
            0,
            None,
            timeout=self.timeout_ms,
        )

    def _command_u32(self, command: int, value: int) -> None:
        value_low, value_high = _tic_usb_command_argument(value)
        self._device.ctrl_transfer(
            TIC_USB_REQUEST_OUT,
            command,
            value_low,
            value_high,
            None,
            timeout=self.timeout_ms,
        )

    def _block_read(self, offset: int, length: int) -> bytes:
        response = self._device.ctrl_transfer(
            TIC_USB_REQUEST_IN,
            TIC_CMD_GET_VARIABLES,
            0,
            int(offset),
            int(length),
            timeout=self.timeout_ms,
        )
        return bytes(response)

    def halt_and_hold(self) -> None:
        self._quick_command(TIC_CMD_HALT_AND_HOLD)

    def reset_command_timeout(self) -> None:
        self._quick_command(TIC_CMD_RESET_COMMAND_TIMEOUT)

    def set_current_position(self, position_steps: int) -> None:
        self._command_u32(TIC_CMD_HALT_AND_SET_POSITION, int(position_steps))

    def set_current_limit_mA(self, target_mA: float) -> int:
        safe_value = safe_tic_current_limit_mA(target_mA)
        self._command_7bit(TIC_CMD_SET_CURRENT_LIMIT, tic_t500_current_limit_code(target_mA))
        return safe_value

    def set_target_velocity(self, velocity_steps_per_10k_s: int) -> None:
        self._quick_command(TIC_CMD_ENERGIZE)
        self._quick_command(TIC_CMD_RESET_COMMAND_TIMEOUT)
        self._quick_command(TIC_CMD_EXIT_SAFE_START)
        self._command_u32(TIC_CMD_SET_TARGET_VELOCITY, int(velocity_steps_per_10k_s))

    def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
        self._quick_command(TIC_CMD_ENERGIZE)
        self._quick_command(TIC_CMD_RESET_COMMAND_TIMEOUT)
        self._quick_command(TIC_CMD_EXIT_SAFE_START)
        if max_speed is not None and max_speed > 0:
            self._command_u32(TIC_CMD_SET_MAX_SPEED, int(max_speed))
        self._command_u32(TIC_CMD_SET_TARGET_POSITION, int(position_steps))

    def get_status(self) -> str:
        variables = self._block_read(0x00, 0x35)
        operation_state = variables[0x00] if len(variables) > 0x00 else 0
        errors = int.from_bytes(variables[0x02:0x04], "little") if len(variables) >= 0x04 else 0
        current_position = (
            int.from_bytes(variables[0x22:0x26], "little", signed=True)
            if len(variables) >= 0x26
            else 0
        )
        vin_mv = int.from_bytes(variables[0x33:0x35], "little") if len(variables) >= 0x35 else 0
        operation_text = {
            0: "Reset",
            2: "De-energized",
            4: "Soft error",
            6: "Waiting for ERR line",
            8: "Starting up",
            10: "Normal",
        }.get(operation_state, f"Unknown ({operation_state})")
        error_text = "None" if errors == 0 else f"0x{errors:04X}"
        return "\n".join(
            [
                f"VIN voltage: {vin_mv / 1000.0:.2f} V",
                f"Operation state: {operation_text}",
                f"Current position: {current_position}",
                f"Errors currently stopping the motor: {error_text}",
                "Transport: native USB",
            ]
        )


class TicController:
    def __init__(
        self,
        command_path: str = "ticcmd",
        device_serial: str = "",
        *,
        prefer_native_usb: bool = False,
        allow_ticcmd_fallback: bool = True,
        transport_logger: Callable[[str], None] | None = None,
    ) -> None:
        self.command_path = command_path.strip() or "ticcmd"
        self.device_serial = device_serial.strip()
        self.prefer_native_usb = bool(prefer_native_usb)
        self.allow_ticcmd_fallback = bool(allow_ticcmd_fallback)
        self.transport_logger = transport_logger
        self._native_backend: NativeTicUsbController | None = None
        self._native_attempted = False
        self._native_error: Exception | None = None
        self._native_success_logged = False
        self._ticcmd_fallback_messages: set[str] = set()
        self._transport_lock = RLock()

    def _native_only(self) -> bool:
        return self.command_path.strip().lower() in TIC_USB_TRANSPORT_ALIASES

    def _native_allowed(self) -> bool:
        return self.prefer_native_usb or self._native_only() or self.command_path.strip().lower() == "auto"

    def _fallback_allowed(self) -> bool:
        return self.allow_ticcmd_fallback and not self._native_only()

    def _native_controller(self) -> NativeTicUsbController | None:
        if not self._native_allowed():
            return None
        if self._native_attempted:
            return self._native_backend
        self._native_attempted = True
        try:
            self._native_backend = NativeTicUsbController(device_serial=self.device_serial)
        except Exception as exc:
            self._native_error = exc
            if self._native_only() or not self._fallback_allowed():
                raise RuntimeError(f"Native Tic USB transport is unavailable: {exc}") from exc
            self._log_ticcmd_fallback(f"native USB setup failed: {exc}")
            self._native_backend = None
        return self._native_backend

    def _log_native_success_once(self) -> None:
        if self._native_success_logged:
            return
        self._native_success_logged = True
        if self.transport_logger is not None:
            self.transport_logger("Tic transport: native USB active.")

    def _log_ticcmd_fallback(self, reason: str) -> None:
        message = f"Tic transport fallback: using ticcmd because {reason}"
        if message in self._ticcmd_fallback_messages:
            return
        self._ticcmd_fallback_messages.add(message)
        if self.transport_logger is not None:
            self.transport_logger(message)

    def _reopen_native_controller(self) -> NativeTicUsbController | None:
        if not self._native_allowed():
            return None
        self._native_backend = None
        self._native_attempted = True
        try:
            self._native_backend = NativeTicUsbController(device_serial=self.device_serial)
        except Exception as exc:
            self._native_error = exc
            self._native_backend = None
        return self._native_backend

    def _native_retry_after_failure(self, initial_error: Exception) -> NativeTicUsbController | None:
        self._native_error = initial_error
        return self._reopen_native_controller()

    def executable(self) -> str | None:
        if self._native_only():
            return None
        if os.path.sep in self.command_path or "/" in self.command_path:
            return self.command_path if Path(self.command_path).exists() else None
        return shutil.which(self.command_path)

    def _base_args(self) -> list[str]:
        exe = self.executable()
        if not exe:
            raise FileNotFoundError(
                "ticcmd was not found. Install Pololu Tic software and make ticcmd available on PATH."
            )
        args = [exe]
        if self.device_serial:
            args.extend(["-d", self.device_serial])
        return args

    def run(self, *extra_args: str, timeout_s: float = 5.0) -> str:
        with self._transport_lock:
            if self._native_only():
                native = self._native_controller()
                if native is not None:
                    raise RuntimeError("Raw ticcmd arguments are unavailable for the native USB transport.")
            args = self._base_args()
            args.extend(extra_args)
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                **_hidden_subprocess_kwargs(),
            )
            stdout = completed.stdout.strip()
            stderr = completed.stderr.strip()
            if completed.returncode != 0:
                detail = stderr or stdout or f"ticcmd exited with code {completed.returncode}"
                raise RuntimeError(detail)
            return stdout

    def get_status(self) -> str:
        with self._transport_lock:
            native = self._native_controller()
            if native is not None:
                try:
                    status_text = native.get_status()
                    self._log_native_success_once()
                    return status_text
                except Exception as exc:
                    retry_native = self._native_retry_after_failure(exc)
                    if retry_native is not None:
                        try:
                            status_text = retry_native.get_status()
                            self._log_native_success_once()
                            return status_text
                        except Exception as retry_exc:
                            self._native_error = retry_exc
                            exc = retry_exc
                    if self._native_only() or not self._fallback_allowed():
                        raise
                    self._log_ticcmd_fallback(f"native status failed: {exc}")
            last_error: Exception | None = None
            for args in (("--status", "--full"), ("--status",), ("--full",)):
                try:
                    status_text = self.run(*args)
                    if _extract_status_float(status_text, "VIN voltage") is not None:
                        return status_text
                    last_error = RuntimeError(
                        "ticcmd status output did not include VIN voltage; motor power cannot be verified."
                    )
                except Exception as exc:
                    last_error = exc
                    continue
            if last_error is not None:
                raise last_error
            raise RuntimeError("ticcmd status did not return motor status.")

    def halt_and_hold(self) -> None:
        with self._transport_lock:
            native = self._native_controller()
            if native is not None:
                try:
                    native.halt_and_hold()
                    self._log_native_success_once()
                    return
                except Exception as exc:
                    retry_native = self._native_retry_after_failure(exc)
                    if retry_native is not None:
                        try:
                            retry_native.halt_and_hold()
                            self._log_native_success_once()
                            return
                        except Exception as retry_exc:
                            self._native_error = retry_exc
                            exc = retry_exc
                    if self._native_only() or not self._fallback_allowed():
                        raise
                    self._log_ticcmd_fallback(f"native halt failed: {exc}")
            self.run("--halt-and-hold")

    def reset_command_timeout(self) -> None:
        with self._transport_lock:
            native = self._native_controller()
            if native is not None:
                try:
                    native.reset_command_timeout()
                    self._log_native_success_once()
                    return
                except Exception as exc:
                    retry_native = self._native_retry_after_failure(exc)
                    if retry_native is not None:
                        try:
                            retry_native.reset_command_timeout()
                            self._log_native_success_once()
                            return
                        except Exception as retry_exc:
                            self._native_error = retry_exc
                            exc = retry_exc
                    if self._native_only() or not self._fallback_allowed():
                        raise
                    self._log_ticcmd_fallback(f"native keepalive failed: {exc}")
            self.run("--reset-command-timeout", timeout_s=2.0)

    def set_current_position(self, position_steps: int) -> None:
        with self._transport_lock:
            native = self._native_controller()
            if native is not None:
                try:
                    native.set_current_position(position_steps)
                    self._log_native_success_once()
                    return
                except Exception as exc:
                    retry_native = self._native_retry_after_failure(exc)
                    if retry_native is not None:
                        try:
                            retry_native.set_current_position(position_steps)
                            self._log_native_success_once()
                            return
                        except Exception as retry_exc:
                            self._native_error = retry_exc
                            exc = retry_exc
                    if self._native_only() or not self._fallback_allowed():
                        raise
                    self._log_ticcmd_fallback(f"native zero-position failed: {exc}")
            self.run("--halt-and-set-position", str(int(position_steps)))

    def set_step_mode(self, step_mode: str) -> None:
        normalized = normalize_tic_step_mode(step_mode)
        if normalized is None:
            raise ValueError(f"Unsupported Tic step mode: {step_mode!r}")
        self.run("--step-mode", normalized)

    def set_current_limit_mA(self, target_mA: float) -> int:
        with self._transport_lock:
            native = self._native_controller()
            if native is not None:
                try:
                    applied = native.set_current_limit_mA(target_mA)
                    self._log_native_success_once()
                    return applied
                except Exception as exc:
                    retry_native = self._native_retry_after_failure(exc)
                    if retry_native is not None:
                        try:
                            applied = retry_native.set_current_limit_mA(target_mA)
                            self._log_native_success_once()
                            return applied
                        except Exception as retry_exc:
                            self._native_error = retry_exc
                            exc = retry_exc
                    if self._native_only() or not self._fallback_allowed():
                        raise
                    self._log_ticcmd_fallback(f"native current-limit command failed: {exc}")
            return apply_tic_current_limit_mA(self, target_mA)

    def set_target_velocity(self, velocity_steps_per_10k_s: int) -> None:
        with self._transport_lock:
            native = self._native_controller()
            if native is not None:
                try:
                    native.set_target_velocity(velocity_steps_per_10k_s)
                    self._log_native_success_once()
                    return
                except Exception as exc:
                    retry_native = self._native_retry_after_failure(exc)
                    if retry_native is not None:
                        try:
                            retry_native.set_target_velocity(velocity_steps_per_10k_s)
                            self._log_native_success_once()
                            return
                        except Exception as retry_exc:
                            self._native_error = retry_exc
                            exc = retry_exc
                    if self._native_only() or not self._fallback_allowed():
                        raise
                    self._log_ticcmd_fallback(f"native velocity command failed: {exc}")
            self.run(
                "--energize",
                "--reset-command-timeout",
                "--exit-safe-start",
                "--velocity",
                str(int(velocity_steps_per_10k_s)),
            )

    def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
        with self._transport_lock:
            native = self._native_controller()
            if native is not None:
                try:
                    native.set_target_position(position_steps, max_speed=max_speed)
                    self._log_native_success_once()
                    return
                except Exception as exc:
                    retry_native = self._native_retry_after_failure(exc)
                    if retry_native is not None:
                        try:
                            retry_native.set_target_position(position_steps, max_speed=max_speed)
                            self._log_native_success_once()
                            return
                        except Exception as retry_exc:
                            self._native_error = retry_exc
                            exc = retry_exc
                    if self._native_only() or not self._fallback_allowed():
                        raise
                    self._log_ticcmd_fallback(f"native position command failed: {exc}")
            args = [
                "--energize",
                "--reset-command-timeout",
                "--exit-safe-start",
            ]
            if max_speed is not None and max_speed > 0:
                args.extend(["--max-speed", str(int(max_speed))])
            args.extend(["--position", str(int(position_steps))])
            self.run(
                *args,
            )


def benchmark_tic_transport_latency(
    *,
    command_path: str,
    device_serial: str = "",
    iterations: int = 5,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    transports = {
        "native_usb": TicController(
            command_path=TIC_USB_TRANSPORT_NATIVE,
            device_serial=device_serial,
        ),
        "ticcmd": TicController(
            command_path=command_path,
            device_serial=device_serial,
            prefer_native_usb=False,
        ),
    }
    for label, controller in transports.items():
        reset_times: list[float] = []
        status_times: list[float] = []
        error: str | None = None
        try:
            controller.get_status()
            for _ in range(max(1, int(iterations))):
                started = time.perf_counter()
                controller.reset_command_timeout()
                reset_times.append(time.perf_counter() - started)
                started = time.perf_counter()
                controller.get_status()
                status_times.append(time.perf_counter() - started)
        except Exception as exc:
            error = str(exc)
        results[label] = {
            "reset_median_ms": None if error else (_median(reset_times) or 0.0) * 1000.0,
            "status_median_ms": None if error else (_median(status_times) or 0.0) * 1000.0,
            "iterations": len(reset_times),
            "error": error,
        }
    return results


@dataclass
class TicCommand:
    action: str
    position_steps: int | None = None
    max_speed: int | None = None
    sequence: int = 0


class TicCommandDispatcher:
    def __init__(
        self,
        controller_factory: Callable[[], TicController],
        *,
        autostart: bool = True,
    ) -> None:
        self._controller_factory = controller_factory
        self._condition = Condition()
        self._pending_target: TicCommand | None = None
        self._pending_commands: list[TicCommand] = []
        self._stop_requested = False
        self._busy = False
        self._sequence = 0
        self._last_error: Exception | None = None
        self._thread: Thread | None = None
        if autostart:
            self.start()

    def start(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_requested = False
            self._thread = Thread(target=self._run, name="MiniDMATicCommandDispatcher", daemon=True)
            self._thread.start()
            self._condition.notify_all()

    def stop(self, *, timeout_s: float = 2.0) -> None:
        thread: Thread | None
        with self._condition:
            self._stop_requested = True
            self._pending_target = None
            self._pending_commands.clear()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout_s)))

    def set_target_position(self, position_steps: int, max_speed: int | None = None) -> int:
        with self._condition:
            self._sequence += 1
            self._pending_target = TicCommand(
                action="target",
                position_steps=int(position_steps),
                max_speed=max_speed,
                sequence=self._sequence,
            )
            self._condition.notify_all()
            return self._sequence

    def reset_command_timeout(self) -> None:
        self._enqueue_priority(TicCommand(action="keepalive"))

    def halt_and_hold(self) -> None:
        self._enqueue_priority(TicCommand(action="halt"))

    def set_current_position(self, position_steps: int) -> None:
        self._enqueue_priority(TicCommand(action="zero", position_steps=int(position_steps)))

    def wait_until_target_dispatched(self, sequence: int, *, timeout_s: float = 2.0) -> bool:
        deadline_s = time.monotonic() + max(0.0, float(timeout_s))
        with self._condition:
            while (
                self._busy
                or (
                    self._pending_target is not None
                    and self._pending_target.sequence <= sequence
                )
                or any(command.sequence <= sequence for command in self._pending_commands)
            ):
                remaining_s = deadline_s - time.monotonic()
                if remaining_s <= 0.0:
                    return False
                self._condition.wait(remaining_s)
            return True

    def _enqueue_priority(self, command: TicCommand) -> None:
        with self._condition:
            self._sequence += 1
            command.sequence = self._sequence
            if command.action == "halt":
                self._pending_target = None
                self._pending_commands.insert(0, command)
            else:
                self._pending_commands.append(command)
            self._condition.notify_all()

    def wait_until_idle(self, *, timeout_s: float = 2.0) -> bool:
        deadline_s = time.monotonic() + max(0.0, float(timeout_s))
        with self._condition:
            while self._busy or self._pending_target is not None or self._pending_commands:
                remaining_s = deadline_s - time.monotonic()
                if remaining_s <= 0.0:
                    return False
                self._condition.wait(remaining_s)
            return True

    def last_error(self) -> Exception | None:
        with self._condition:
            return self._last_error

    def _next_command(self) -> TicCommand | None:
        if self._pending_commands:
            return self._pending_commands.pop(0)
        command = self._pending_target
        self._pending_target = None
        return command

    def _run(self) -> None:
        while True:
            with self._condition:
                while (
                    not self._stop_requested
                    and self._pending_target is None
                    and not self._pending_commands
                ):
                    self._condition.wait()
                if self._stop_requested:
                    self._busy = False
                    self._condition.notify_all()
                    return
                command = self._next_command()
                self._busy = command is not None
            if command is not None:
                try:
                    with self._condition:
                        self._last_error = None
                    controller = self._controller_factory()
                    if command.action == "target" and command.position_steps is not None:
                        controller.set_target_position(command.position_steps, max_speed=command.max_speed)
                    elif command.action == "keepalive":
                        controller.reset_command_timeout()
                    elif command.action == "halt":
                        controller.halt_and_hold()
                    elif command.action == "zero" and command.position_steps is not None:
                        controller.set_current_position(command.position_steps)
                except Exception as exc:
                    with self._condition:
                        self._last_error = exc
                finally:
                    with self._condition:
                        self._busy = False
                        self._condition.notify_all()


class PowerSupplyController:
    def __init__(
        self,
        *,
        port_name: str,
        baudrate: int,
        profile_id: str,
        max_voltage_v: float,
        channel_select: int | None = None,
        device_serial: str = "",
    ) -> None:
        self.port_name = port_name.strip()
        self.baudrate = int(baudrate)
        self.profile_id = profile_id if profile_id in SUPPLY_PROFILES else "hmp4030"
        self.profile = dict(SUPPLY_PROFILES[self.profile_id])
        if channel_select is not None:
            self.profile["channel_select"] = int(channel_select)
        self.max_voltage_v = float(max_voltage_v)
        self.device_serial = device_serial.strip()
        self._serial: Any = None
        self._io_lock = RLock()

    def connect(self) -> None:
        if serial is None:
            raise RuntimeError("pyserial is not available.")
        if not self.port_name:
            raise RuntimeError("Select a power-supply serial port first.")
        if self._serial is not None and getattr(self._serial, "is_open", False):
            return
        self._serial = serial.Serial(
            self.port_name,
            baudrate=self.baudrate,
            timeout=0.5,
            write_timeout=0.5,
        )
        self._serial.rts = False
        self._serial.dtr = False
        time.sleep(0.08)

    def disconnect(self) -> None:
        port = self._serial
        self._serial = None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    def is_connected(self) -> bool:
        return self._serial is not None and bool(getattr(self._serial, "is_open", False))

    def _require_port(self) -> Any:
        if not self.is_connected():
            raise RuntimeError("Power supply is not connected.")
        return self._serial

    def _write_command(self, command: str, *, settle_s: float = 0.08) -> None:
        port = self._require_port()
        payload = command.rstrip() + "\n"
        port.reset_input_buffer()
        port.write(payload.encode("ascii", errors="ignore"))
        port.flush()
        if settle_s > 0:
            time.sleep(settle_s)

    def _read_line(self, *, timeout_s: float = 0.7) -> str:
        port = self._require_port()
        deadline = time.time() + max(0.1, timeout_s)
        chunks: list[bytes] = []
        while time.time() < deadline:
            line = port.readline()
            if line:
                chunks.append(line)
                if line.endswith(b"\n") or line.endswith(b"\r"):
                    break
        return b"".join(chunks).decode("ascii", errors="ignore").strip()

    def command(self, command: str, *, settle_s: float = 0.08) -> None:
        with self._io_lock:
            self._write_command(command, settle_s=settle_s)

    def query_float(self, command: str, *, settle_s: float = 0.08, timeout_s: float = 0.7) -> float | None:
        with self._io_lock:
            self._write_command(command, settle_s=settle_s)
            return _parse_first_float(self._read_line(timeout_s=timeout_s))

    def selected_channel(self) -> int:
        return int(self.profile.get("channel_select", 0) or 0)

    def select_channel(self, channel: int | None = None) -> None:
        target_channel = self.selected_channel() if channel is None else int(channel)
        if target_channel <= 0:
            raise RuntimeError("Select a power-supply channel before controlling the output.")
        self.command(f"INST:NSEL {target_channel}")

    def current_resolution_mA(self) -> float:
        return max(0.001, float(self.profile.get("current_resolution_mA", 1.0)))

    def quantize_current_mA(self, current_mA: float) -> float:
        resolution_mA = self.current_resolution_mA()
        return max(0.0, round(float(current_mA) / resolution_mA) * resolution_mA)

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
            self.command(f"VOLT {max(0.0, float(voltage_v)):.3f}")
            self.command(f"CURR {max(0.0, float(current_a)):.3f}")
            self.command("OUTP ON" if output_on else "OUTP OFF")

    def initialize_output(
        self,
        *,
        current_mA: float,
        reset_on_start: bool,
        force_voltage_first: bool | None = None,
    ) -> None:
        with self._io_lock:
            if reset_on_start:
                self.command("*RST", settle_s=1.2)
            self.select_channel()
            limit_v = max(0.0, float(self.max_voltage_v))
            current_a = self.quantize_current_mA(current_mA) / 1000.0
            voltage_first = bool(self.profile.get("voltage_first", False)) if force_voltage_first is None else bool(force_voltage_first)
            if voltage_first:
                self.command(f"VOLT {limit_v:.1f}")
                self.command(f"CURR {current_a:.4f}")
            else:
                self.command(f"CURR {current_a:.4f}")
                self.command(f"VOLT {limit_v:.1f}")
            self.command("OUTP ON")

    def set_current_mA(self, current_mA: float) -> None:
        with self._io_lock:
            self.select_channel()
            self.command(f"CURR {self.quantize_current_mA(current_mA) / 1000.0:.4f}", settle_s=0.03)

    def output_on(self) -> None:
        with self._io_lock:
            self.select_channel()
            self.command("OUTP ON")

    def output_off(self) -> None:
        with self._io_lock:
            self.select_channel()
            self.command("OUTP OFF")

    def output_state(self, channel: int | None = None) -> bool | None:
        target_channel = self.selected_channel() if channel is None else int(channel)
        with self._io_lock:
            self.select_channel(target_channel)
            state = self.query_float("OUTP?", settle_s=0.03, timeout_s=0.4)
        return None if state is None else bool(int(round(float(state))))

    def shutdown_output(self, *, reset_voltage_v: float = 1.0, reset_current_mA: float = 1.0) -> None:
        with self._io_lock:
            self.select_channel()
            self.command("OUTP OFF")
            self.command(f"VOLT {max(0.0, float(reset_voltage_v)):.3f}")
            self.command(f"CURR {max(0.0, float(reset_current_mA)) / 1000.0:.4f}")
            self.command("OUTP OFF")

    def measure(self) -> dict[str, float | None]:
        with self._io_lock:
            self.select_channel()
            voltage_v = self.query_float("MEAS:VOLT?")
            current_a = self.query_float("MEAS:CURR?")
        current_mA = None if current_a is None else current_a * 1000.0
        resistance_ohm = None
        power_w = None
        if voltage_v is not None and current_a is not None:
            if abs(current_a) >= MIN_RESISTANCE_CURRENT_MA / 1000.0:
                resistance_ohm = voltage_v / current_a
            power_w = voltage_v * current_a
        return {
            "voltage_V": voltage_v,
            "current_mA": current_mA,
            "resistance_ohm": resistance_ohm,
            "power_W": power_w,
        }


class SharedBrokerSupplyController:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        max_voltage_v: float,
        current_channel: int | None,
        motor_channel: int | None = None,
        owner: str = "mini_dma_logger",
    ) -> None:
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = int(port)
        self.port_name = f"{self.host}:{self.port}"
        self.baudrate = 0
        self.profile_id = "shared_hmp_broker"
        self.profile = dict(SUPPLY_PROFILES[self.profile_id])
        self.max_voltage_v = float(max_voltage_v)
        self.current_channel = None if current_channel is None else int(current_channel)
        self.motor_channel = None if motor_channel is None else int(motor_channel)
        self.owner = owner
        self._client: Any = None
        self._leases: dict[int, str] = {}
        self._connected = False
        self._io_lock = RLock()

    def connect(self) -> None:
        if self._client is None:
            self._client = BrokerJsonClient(host=self.host, port=self.port)
        self._client.request("snapshot")
        self._connected = True

    def disconnect(self) -> None:
        with self._io_lock:
            client = self._client
            channels = list(self._leases)
            if self.current_channel in channels:
                channels.remove(self.current_channel)
                channels.insert(0, self.current_channel)
            for channel in channels:
                lease_id = self._leases[channel]
                try:
                    client.release(channel=channel, lease_id=lease_id)
                except Exception:
                    pass
            self._leases.clear()
            self._client = None
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    def _require_client(self) -> Any:
        if not self.is_connected():
            raise RuntimeError("Shared HMP broker is not connected.")
        return self._client

    def selected_channel(self) -> int:
        return int(self.current_channel or 0)

    def select_channel(self, channel: int | None = None) -> None:
        return None

    def _role_for_channel(self, channel: int) -> str:
        if self.motor_channel is not None and int(channel) == self.motor_channel:
            return ROLE_MINI_DMA_MOTOR
        return ROLE_MINI_DMA_CURRENT

    def _lease_channel(self, channel: int) -> str:
        channel = int(channel)
        lease_id = self._leases.get(channel)
        if lease_id:
            return lease_id
        lease = self._require_client().lease(
            channel=channel,
            owner=self.owner,
            role=self._role_for_channel(channel),
        )
        lease_id = str(lease.get("lease_id") or "")
        if not lease_id:
            raise RuntimeError("Shared HMP broker did not return a lease id.")
        self._leases[channel] = lease_id
        return lease_id

    def current_resolution_mA(self) -> float:
        return max(0.001, float(self.profile.get("current_resolution_mA", 1.0)))

    def quantize_current_mA(self, current_mA: float) -> float:
        resolution_mA = self.current_resolution_mA()
        return max(0.0, round(float(current_mA) / resolution_mA) * resolution_mA)

    def configure_channel(
        self,
        *,
        channel: int,
        voltage_v: float,
        current_a: float,
        output_on: bool,
    ) -> None:
        with self._io_lock:
            lease_id = self._lease_channel(channel)
            self._require_client().configure_channel(
                channel=int(channel),
                lease_id=lease_id,
                voltage_v=max(0.0, float(voltage_v)),
                current_a=max(0.0, float(current_a)),
                output_on=bool(output_on),
            )

    def initialize_output(
        self,
        *,
        current_mA: float,
        reset_on_start: bool,
        force_voltage_first: bool | None = None,
    ) -> None:
        channel = self.selected_channel()
        if channel <= 0:
            raise RuntimeError("Select a shared HMP broker current-sweep channel first.")
        self.configure_channel(
            channel=channel,
            voltage_v=max(0.0, float(self.max_voltage_v)),
            current_a=self.quantize_current_mA(current_mA) / 1000.0,
            output_on=True,
        )

    def set_current_mA(self, current_mA: float) -> None:
        channel = self.selected_channel()
        if channel <= 0:
            raise RuntimeError("Select a shared HMP broker current-sweep channel first.")
        with self._io_lock:
            self._require_client().set_current(
                channel=channel,
                lease_id=self._lease_channel(channel),
                current_mA=self.quantize_current_mA(current_mA),
            )

    def output_on(self) -> None:
        channel = self.selected_channel()
        if channel <= 0:
            raise RuntimeError("Select a shared HMP broker current-sweep channel first.")
        with self._io_lock:
            self._require_client().set_output(
                channel=channel,
                lease_id=self._lease_channel(channel),
                output_on=True,
            )

    def output_off(self) -> None:
        channel = self.selected_channel()
        if channel <= 0:
            return
        lease_id = self._leases.get(channel)
        if not lease_id:
            return
        with self._io_lock:
            self._require_client().set_output(channel=channel, lease_id=lease_id, output_on=False)

    def output_state(self, channel: int | None = None) -> bool | None:
        target_channel = self.selected_channel() if channel is None else int(channel)
        if target_channel <= 0:
            return None
        return self._require_client().output_state(channel=target_channel)

    def shutdown_output(self, *, reset_voltage_v: float = 1.0, reset_current_mA: float = 1.0) -> None:
        channel = self.selected_channel()
        if channel <= 0:
            return
        with self._io_lock:
            lease_id = self._lease_channel(channel)
            client = self._require_client()
            client.set_output(channel=channel, lease_id=lease_id, output_on=False)
            client.configure_channel(
                channel=channel,
                lease_id=lease_id,
                voltage_v=max(0.0, float(reset_voltage_v)),
                current_a=max(0.0, float(reset_current_mA)) / 1000.0,
                output_on=False,
            )

    def measure(self) -> dict[str, float | None]:
        channel = self.selected_channel()
        if channel <= 0:
            return {
                "voltage_V": None,
                "current_mA": None,
                "resistance_ohm": None,
                "power_W": None,
            }
        readback = dict(self._require_client().measure_channel(channel=channel))
        voltage_v = readback.get("voltage_V")
        current_mA = readback.get("current_mA")
        current_a = None if current_mA is None else float(current_mA) / 1000.0
        resistance_ohm = readback.get("resistance_ohm")
        power_w = readback.get("power_W")
        if resistance_ohm is None and voltage_v is not None and current_a is not None:
            if abs(current_a) >= MIN_RESISTANCE_CURRENT_MA / 1000.0:
                resistance_ohm = float(voltage_v) / current_a
        if power_w is None and voltage_v is not None and current_a is not None:
            power_w = float(voltage_v) * current_a
        return {
            "voltage_V": None if voltage_v is None else float(voltage_v),
            "current_mA": None if current_mA is None else float(current_mA),
            "resistance_ohm": None if resistance_ohm is None else float(resistance_ohm),
            "power_W": None if power_w is None else float(power_w),
        }


class MainWindow(QtWidgets.QMainWindow):
    _control_ui_event = QtCore.pyqtSignal(object)

    def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
        super().__init__()
        self._ui_thread_id = get_ident()
        self._control_worker_thread_id: int | None = None
        self._control_ui_event.connect(self._apply_control_ui_event, QtCore.Qt.ConnectionType.QueuedConnection)
        self.setWindowTitle(APP_NAME)
        self.settings = _mini_dma_settings()
        self._persist_settings = persist_settings
        self._settings_restore_in_progress = False
        self._settings_persistence_ready = False
        self._provided_log_dir = log_dir
        self._restored_log_dir = ""
        self._scale_thread: QtCore.QThread | None = None
        self._scale_worker: ScaleWorker | None = None
        self._tic_controller: TicController | None = None
        self._tic_controller_key: tuple[str, str, bool] | None = None
        self._tic_command_dispatcher: TicCommandDispatcher | None = None
        self._tic_command_dispatcher_key: tuple[str, str, bool] | None = None
        self._tic_status_text = ""
        self._latest_scale_value_g = 0.0
        self._latest_scale_text = ""
        self._latest_scale_timestamp: float | None = None
        self._scale_state_lock = RLock()
        self._cached_tension_decreases_scale_reading = True
        self._cached_zero_load_scale_g = DEFAULT_ZERO_LOAD_SCALE_G
        self._scale_connected_at_s: float | None = None
        self._scale_no_data_hint_emitted = False
        self._scale_signal_buffer = ScaleSignalBuffer()
        self._current_position_steps = 0
        self._current_position_mm = 0.0
        self._last_move_target_mm = 0.0
        self._effective_position_mm = 0.0
        self._last_effective_move_target_mm = 0.0
        self._last_commanded_position_steps: int | None = 0
        self._last_tic_vin_v: float | None = None
        self._last_tic_status_error: str | None = None
        self._tic_motor_power_ok: bool | None = None
        self._tic_motor_power_warning_active = False
        self._tic_keepalive_warning_active = False
        self._manual_jog_uses_last_target = False
        self._last_auto_sample_name = ""
        self._last_auto_log_name = ""
        self._last_move_direction = 0.0
        self._seek_last_error_by_key: dict[tuple[str, int, float], float] = {}
        self._seek_last_value_by_key: dict[tuple[str, int, float], float] = {}
        self._seek_last_time_by_key: dict[tuple[str, int, float], float] = {}
        self._seek_last_filtered_value_by_key: dict[tuple[str, int, float], float] = {}
        self._seek_out_of_band_since_by_key: dict[tuple[str, int, float], float] = {}
        self._seek_out_of_band_sign_by_key: dict[tuple[str, int, float], float] = {}
        self._seek_last_scale_timestamp_by_key: dict[tuple[str, int, float], float] = {}
        self._seek_last_scale_timestamp_by_clock: dict[tuple[str, int], float] = {}
        self._seek_post_move_sample_count_by_key: dict[tuple[str, int, float], int] = {}
        self._seek_last_effective_position_by_key: dict[tuple[str, int, float], float] = {}
        self._seek_live_stiffness_by_key: dict[tuple[str, int, float], float] = {}
        self._seek_no_response_count_by_key: dict[tuple[str, int, float], int] = {}
        self._seek_travel_by_key: dict[tuple[str, int, float], float] = {}
        self._seek_pending_reversal_by_key: dict[tuple[str, int, float], tuple[float, float | None]] = {}
        self._current_sweep_hold_response_stiffness_by_key: dict[tuple[str, int, float], float] = {}
        self._current_sweep_hold_response_count_by_key: dict[tuple[str, int, float], int] = {}
        self._setup_preload_engaged_seek_keys: set[tuple[str, int, float]] = set()
        self._seek_live_stiffness_g_per_mm: float | None = None
        self._seek_last_stiffness_value_by_basis: dict[str, float] = {}
        self._seek_last_stiffness_position_by_basis: dict[str, float] = {}
        self._session_points: list[MeasurementPoint] = []
        self._live_plot_points: list[MeasurementPoint] = []
        self._last_live_plot_scale_timestamp: float | None = None
        self._display_plot_old_cache_key: tuple[object, ...] | None = None
        self._display_plot_old_cache: list[MeasurementPoint] = []
        self._session_active = False
        self._sleep_guard: Any = None
        self._session_start_monotonic = 0.0
        self._session_created_utc: str | None = None
        self._session_txt_handle: Any = None
        self._session_csv_handle: Any = None
        self._session_csv_writer: csv.DictWriter[str] | None = None
        self._session_raw_scale_handle: Any = None
        self._session_raw_scale_writer: csv.DictWriter[str] | None = None
        self._session_control_trace_handle: Any = None
        self._session_control_trace_writer: csv.DictWriter[str] | None = None
        self._session_ui_telemetry_handle: Any = None
        self._session_ui_telemetry_writer: csv.DictWriter[str] | None = None
        self._session_setup_txt_handle: Any = None
        self._session_setup_csv_handle: Any = None
        self._session_setup_csv_writer: csv.DictWriter[str] | None = None
        self._session_base_path: Path | None = None
        self._session_csv_path: Path | None = None
        self._session_json_path: Path | None = None
        self._session_raw_scale_path: Path | None = None
        self._session_control_trace_path: Path | None = None
        self._session_ui_telemetry_path: Path | None = None
        self._session_setup_txt_path: Path | None = None
        self._session_setup_csv_path: Path | None = None
        self._session_recovery_path: Path | None = None
        self._session_start_wall_s = 0.0
        self._session_raw_scale_start_wall_s = 0.0
        self._last_session_log_timestamp_s: float | None = None
        self._session_stop_reason: str | None = None
        self._session_stop_detail: str | None = None
        self._session_stop_recorded_utc: str | None = None
        self._session_raw_scale_count = 0
        self._session_ui_telemetry_count = 0
        self._ui_refresh_last_monotonic_s: float | None = None
        self._session_logging_enabled = True
        self._load_offset_g = 0.0
        self._position_reference_mm = 0.0
        self._preload_reference_armed = False
        self._preload_trigger_elapsed_s: float | None = None
        self._builder_project_path: Path | None = None
        self._builder_project_match: ProjectImportResult | None = None
        self._supply_controller: PowerSupplyController | None = None
        self._supply_snapshot: dict[str, float | None] = {
            "voltage_V": None,
            "current_mA": None,
            "resistance_ohm": None,
            "power_W": None,
        }
        self._supply_snapshot_monotonic = 0.0
        self._supply_output_enabled = False
        self._supply_last_setpoint_mA: float | None = None
        self._heating_program_current_mA: float | None = None
        self._heating_program_direction = 1.0
        self._automation_active = False
        self._automation_steps: list[AutomationStep] = []
        self._automation_index = 0
        self._automation_interval_ms = DEFAULT_CONTROL_INTERVAL_MS
        self._automation_total_steps = 0
        self._automation_completed_ticks = 0
        self._automation_progress_started_s = 0.0
        self._automation_progress_last_format_update_s = 0.0
        self._automation_name = ""
        self._automation_phase = "idle"
        self._automation_step_note: str | None = None
        self._automation_paused = False
        self._automation_basis: str | None = None
        self._automation_target_value: float | None = None
        self._automation_plateau_index: int | None = None
        self._automation_plateau_label: str | None = None
        self._resume_recipe_state: AutomationResumeState | None = None
        self._last_recipe_summary = ""
        self._loaded_recipe_path: Path | None = None
        self._saved_recipe_signature: str | None = None
        self._paused_current_setpoint_mA: float | None = None
        self._recipe_origin_mm = 0.0
        self._recipe_estimated_points = 0
        self._automation_estimated_total_s = 0.0
        self._current_sweep_duration_overheads_s: list[float] = []
        self._active_current_sweep_step_index: int | None = None
        self._active_current_sweep_started_s = 0.0
        self._active_current_sweep_wall_started_s = 0.0
        self._active_current_sweep_last_schedule_update_s = 0.0
        self._current_sweep_post_hold_throttle_until_s = 0.0
        self._active_current_sweep_last_setpoint_mA: float | None = None
        self._active_current_sweep_display_target_mA: float | None = None
        self._active_current_sweep_display_direction = 0.0
        self._current_sweep_ramp_hold_step_index: int | None = None
        self._current_sweep_ramp_hold_started_s = 0.0
        self._current_sweep_ramp_hold_in_band_since_s: float | None = None
        self._current_sweep_ramp_hold_seek_accepted_since_s: float | None = None
        self._current_sweep_ramp_hold_candidate_step_index: int | None = None
        self._current_sweep_ramp_hold_candidate_sign = 0.0
        self._current_sweep_ramp_hold_candidate_since_s: float | None = None
        self._current_sweep_voltage_limit_step_index: int | None = None
        self._current_sweep_voltage_limit_started_s: float | None = None
        self._current_sweep_voltage_limit_start_mA = 0.0
        self._current_sweep_voltage_limited_return_steps: set[int] = set()
        self._supply_voltage_limit_logged = False
        self._wire_break_stop_in_progress = False
        self._active_target_ramp_step_index: int | None = None
        self._active_target_ramp_started_s = 0.0
        self._active_target_ramp_start_value: float | None = None
        self._active_target_ramp_rate_value_s: float | None = None
        self._active_timed_step_index: int | None = None
        self._active_timed_step_started_s = 0.0
        self._active_timed_move_sent = False
        self._setup_measured_length_mm: float | None = None
        self._setup_starting_length_mm: float | None = None
        self._setup_preload_position_mm: float | None = None
        self._setup_preload_ramp_skipped = False
        self._setup_zero_position_mm: float | None = None
        self._setup_return_zero_start_point_index = 0
        self._setup_return_zero_speed_mm_s_value: float | None = None
        self._setup_zero_fallback_return_position_mm: float | None = None
        self._setup_zero_fallback_raw_g: float | None = None
        self._setup_zero_fallback_reason = ""
        self._end_zero_fallback_armed = False
        self._end_zero_fallback_start_point_index = 0
        self._end_zero_fallback_return_position_mm: float | None = None
        self._end_zero_fallback_raw_g: float | None = None
        self._run_zero_load_scale_g: float | None = None
        self._length_setup_dialog: QtWidgets.QDialog | None = None
        self._length_setup_status_label: QtWidgets.QLabel | None = None
        self._length_setup_progress: QtWidgets.QProgressBar | None = None
        self._button_length_setup_pause: QtWidgets.QPushButton | None = None
        self._button_length_setup_stop: QtWidgets.QPushButton | None = None
        self._length_setup_stress_plot: PyqtGraphPlotBundle | None = None
        self._length_setup_displacement_plot: PyqtGraphPlotBundle | None = None
        self._length_setup_stress_plot_widget: Any | None = None
        self._length_setup_displacement_plot_widget: Any | None = None
        self._length_setup_stress_curve: Any | None = None
        self._length_setup_load_curve: Any | None = None
        self._length_setup_displacement_curve: Any | None = None
        self._length_setup_start_monotonic = 0.0
        self._length_setup_last_record_scale_timestamp: float | None = None
        self._last_length_setup_plot_refresh_s: float | None = None
        self._length_setup_points: list[MeasurementPoint] = []
        self._length_setup_progress_phase_key: tuple[object, ...] | None = None
        self._length_setup_progress_fraction_floor = 0.0
        self._automated_setup_starting_length_mm: float | None = None
        self._automated_setup_preload_length_mm: float | None = None
        self._motor_step_calibration_dialog: QtWidgets.QDialog | None = None
        self._motor_step_calibration_status_label: QtWidgets.QLabel | None = None
        self._motor_step_calibration_detail_label: QtWidgets.QLabel | None = None
        self._motor_step_calibration_progress: QtWidgets.QProgressBar | None = None
        self._motor_step_calibration_points_view: QtWidgets.QPlainTextEdit | None = None
        self._motor_step_calibration_active = False
        self._motor_step_calibration_stop_requested = False
        self._calibration_report: dict[str, Any] | None = None
        self._calibrated_stiffness_g_per_mm: float | None = None
        self._calibrated_stiffness_length_mm: float | None = None
        self._calibrated_load_noise_g: float | None = None
        self._run_log_mirror_enabled = False
        self._run_log_mirror_path = DEFAULT_RUN_LOG_MIRROR_PATH
        self._owned_shared_broker_server: Any | None = None
        self._owned_shared_broker_thread: Thread | None = None
        self._owned_shared_broker_driver: HmpSerialDriver | None = None
        self._diameter_imported = False
        self._builder_import_in_progress = False
        self._plot_tiles: list[PlotTileWidgets] = []
        self._dashboard_plot_bundles: list[PyqtGraphPlotBundle] = []
        self._dashboard_plot_widgets: list[Any] = []
        self._dashboard_left_curves: list[Any] = []
        self._dashboard_right_curves: list[Any] = []
        self._dashboard_plot_settings_by_mode: dict[str, list[dict[str, object]]] = {}
        self._plot_settings_restore_in_progress = False
        self._dashboard_value_labels: dict[str, QtWidgets.QLabel] = {}
        self._current_sweep_target_values_by_mode: dict[str, tuple[float, float, float, float]] = {}
        self._constant_current_step_base_position_by_note: dict[str, float] = {}
        self._constant_current_step_base_strain_by_note: dict[str, float] = {}
        self._active_constant_current_zero_position_mm: float | None = None
        self._active_constant_current_zero_current_mA: float | None = None
        self._active_mechanical_scan_step_index: int | None = None
        self._active_mechanical_scan_started_s = 0.0
        self._active_mechanical_scan_move_count = 0
        self._active_mechanical_scan_hold_started_s: float | None = None
        self._active_mechanical_scan_move_pending = False
        self._active_mechanical_scan_direction: float | None = None
        self._active_mechanical_scan_origin_position_mm: float | None = None
        self._last_recipe_mode = "ramp"
        self._control_scroll_area: QtWidgets.QScrollArea | None = None
        self._manual_jog_direction = 0.0
        self._manual_jog_last_tick_s: float | None = None
        self._manual_jog_pending_mm = 0.0
        self._manual_jog_timer_moves = 0
        self._manual_jog_click_suppressed = False
        self._manual_auto_connect_progress: QtWidgets.QProgressDialog | None = None
        self._last_motion_command_time_s: float | None = None
        self._last_motion_expected_complete_time_s: float | None = None
        self._last_commanded_speed_mm_s = 0.0
        self._last_tic_status_time_s: float | None = None
        self._last_feedback_wait_log_s = 0.0
        self._raw_scale_display_limit_active = False
        self._automation_control_lock = RLock()
        self._automation_control_loop: AutomationControlLoop | None = None
        self._automation_control_error: str | None = None
        self._active_control_config: MiniDmaControlConfig | None = None
        self._bench_allow_mechanical_slack_takeup = False
        self._bench_mechanical_slack_max_seek_mm: float | None = None
        self._bench_mechanical_slack_takeup_logged_keys: set[tuple[str, int | None, float]] = set()
        self._recovery_plot_dialog: QtWidgets.QDialog | None = None
        self._recovery_plot: PyqtGraphPlotBundle | None = None
        self._recovery_plot_widget: Any | None = None
        self._recovery_left_curve: Any | None = None
        self._recovery_right_curve: Any | None = None
        self._recovery_start_elapsed_s: float | None = None
        self._recovery_start_monotonic = 0.0
        self._recovery_last_record_scale_timestamp: float | None = None
        self._last_recovery_plot_refresh_s: float | None = None
        self._pending_recovery_return_duration_s: float | None = None
        self._recovery_points: list[MeasurementPoint] = []
        self._last_dashboard_plot_refresh_s: float | None = None
        self._ui_heartbeat_last_s: float | None = None
        self._ui_heartbeat_interval_ms: float | None = None
        self._ui_heartbeat_fps: float | None = None
        self._window_closing = False
        self.action_timing_settings: QtGui.QAction | None = None
        self.action_show_recipe_file_controls: QtGui.QAction | None = None
        self.action_mirror_run_log: QtGui.QAction | None = None
        self._manual_jog_timer = QtCore.QTimer(self)
        self._manual_jog_timer.setInterval(50)
        self._manual_jog_timer.timeout.connect(self._handle_manual_jog_timer)
        self._tic_keepalive_timer = QtCore.QTimer(self)
        self._tic_keepalive_timer.setInterval(TIC_KEEPALIVE_INTERVAL_MS)
        self._tic_keepalive_timer.timeout.connect(self._handle_tic_keepalive_timer)
        self._build_ui(log_dir or _default_download_dir())
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._handle_status_timer)
        self._ui_refresh_timer = QtCore.QTimer(self)
        self._ui_refresh_timer.setInterval(DEFAULT_UI_REFRESH_INTERVAL_MS)
        self._ui_refresh_timer.timeout.connect(self._handle_ui_refresh_timer)
        self._ui_heartbeat_timer = QtCore.QTimer(self)
        self._ui_heartbeat_timer.setInterval(DEFAULT_UI_HEARTBEAT_INTERVAL_MS)
        self._ui_heartbeat_timer.timeout.connect(self._handle_ui_heartbeat_timer)
        self._ui_heartbeat_timer.start()
        self._auto_ramp_timer = QtCore.QTimer(self)
        self._auto_ramp_timer.timeout.connect(self._handle_auto_ramp_tick)
        self._scale_hint_timer = QtCore.QTimer(self)
        self._scale_hint_timer.setSingleShot(True)
        self._scale_hint_timer.timeout.connect(self._warn_if_scale_is_silent)
        self._restore_settings()
        self._refresh_scale_ports()
        self._refresh_live_labels()

    def _menu_by_text(self, text: str) -> QtWidgets.QMenu | None:
        menu_bar = self.menuBar()
        wanted = text.replace("&", "").lower()
        for action in menu_bar.actions():
            candidate = action.menu()
            if candidate is None:
                continue
            candidate_text = action.text().replace("&", "").lower()
            if candidate_text == wanted:
                return candidate
        return None

    def _install_mini_dma_settings_menu(self) -> None:
        menu_bar = self.menuBar()
        settings_menu = self._menu_by_text("Settings")
        if settings_menu is None:
            settings_menu = menu_bar.addMenu("&Settings")
            if settings_menu is None:
                return
            settings_menu.setObjectName("mw_mini_dma_settings")
        self.action_timing_settings = settings_menu.addAction("Timing...")
        if self.action_timing_settings is not None:
            self.action_timing_settings.triggered.connect(self._show_timing_settings_dialog)
        self.action_show_recipe_file_controls = settings_menu.addAction("Show recipe save/load")
        if self.action_show_recipe_file_controls is not None:
            self.action_show_recipe_file_controls.setCheckable(True)
            self.action_show_recipe_file_controls.setChecked(False)
            self.action_show_recipe_file_controls.toggled.connect(self._set_recipe_file_controls_visible)

    def _install_mini_dma_developer_menu(self) -> None:
        menu_bar = self.menuBar()
        developer_menu: QtWidgets.QMenu | None = None
        for action in menu_bar.actions():
            candidate = action.menu()
            if candidate is not None and candidate.objectName() == "mw_shared_developer":
                developer_menu = candidate
                break
        if developer_menu is None:
            developer_menu = menu_bar.addMenu("&Developer")
            if developer_menu is None:
                return
            developer_menu.setObjectName("mw_shared_developer")
        developer_menu.addSeparator()
        self.action_mirror_run_log = developer_menu.addAction("Mirror Mini DMA Run Log to File")
        if self.action_mirror_run_log is not None:
            self.action_mirror_run_log.setCheckable(True)
            self.action_mirror_run_log.toggled.connect(self._set_run_log_mirror_enabled)
        choose_action = developer_menu.addAction("Choose Mini DMA Run Log File...")
        if choose_action is not None:
            choose_action.triggered.connect(self._choose_run_log_mirror_file)
        benchmark_action = developer_menu.addAction("Benchmark Tic Transports")
        if benchmark_action is not None:
            benchmark_action.triggered.connect(self._benchmark_tic_transports)

    def _is_ui_thread(self) -> bool:
        return get_ident() == self._ui_thread_id

    def _run_on_ui_thread(self, callback: Callable[[], None]) -> None:
        if self._is_ui_thread():
            callback()
            return
        self._control_ui_event.emit(callback)

    def _restore_main_window_focus_soon(self) -> None:
        if self._window_closing:
            return
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._restore_main_window_focus_soon)
            return
        QtCore.QTimer.singleShot(0, self._restore_main_window_focus)

    def _restore_main_window_focus(self) -> None:
        if self._window_closing:
            return
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _call_on_ui_thread_sync(self, callback: Callable[[], Any]) -> Any:
        if self._is_ui_thread():
            return callback()
        done = Event()
        result: dict[str, Any] = {}

        def _invoke() -> None:
            try:
                result["value"] = callback()
            except BaseException as exc:  # pragma: no cover - re-raised on worker thread
                result["error"] = exc
            finally:
                done.set()

        self._control_ui_event.emit(_invoke)
        done.wait()
        if "error" in result:
            raise result["error"]
        return result.get("value")

    @QtCore.pyqtSlot(object)
    def _apply_control_ui_event(self, callback: object) -> None:
        if callable(callback):
            callback()

    def _freeze_control_config(self) -> MiniDmaControlConfig:
        raw_limit = self._raw_scale_display_limit_g(live_widget=True)
        if self._supply_controller is not None:
            supply_resolution = self._supply_controller.current_resolution_mA()
        else:
            profile = SUPPLY_PROFILES.get(str(self.combo_supply_profile.currentData() or "hmp4030"), {})
            supply_resolution = max(0.001, float(profile.get("current_resolution_mA", 1.0)))
        return MiniDmaControlConfig(
            diameter_mm=float(self.spin_diameter.value()),
            initial_length_mm=float(self.spin_initial_length.value()),
            steps_per_mm=float(self.spin_steps_per_mm.value()),
            tension_decreases_scale_reading=self.check_tension_load_positive.isChecked(),
            positive_motion_is_tension=self.check_positive_motion_is_tension.isChecked(),
            zero_on_preload=self.check_zero_on_preload.isChecked(),
            preload_threshold_g=float(self.spin_preload_threshold_g.value()),
            backlash_mm=float(self.spin_backlash_mm.value()),
            scale_interval_ms=int(self.spin_scale_interval.value()),
            control_interval_ms=self._control_interval_ms(),
            log_interval_ms=self._log_interval_ms(),
            soft_limits_enabled=self.check_soft_limits.isChecked(),
            soft_min_mm=float(self.spin_soft_min_mm.value()),
            soft_max_mm=float(self.spin_soft_max_mm.value()),
            max_load_enabled=self.check_max_load.isChecked(),
            max_load_g=float(self.spin_max_load_g.value()),
            raw_scale_limit_g=raw_limit,
            jog_mm=float(self.spin_jog_mm.value()),
            motion_speed_mm_s=float(self.spin_motion_speed_mm_s.value()),
            ramp_speed_mm_s=float(self.spin_ramp_speed_mm_s.value()),
            cycle_speed_mm_s=float(self.spin_cycle_speed_mm_s.value()),
            hold_speed_mm_s=float(self.spin_hold_speed_mm_s.value()),
            distribution_seek_speed_mm_s=float(self.spin_distribution_seek_speed_mm_s.value()),
            distribution_nudge_mm=float(self.spin_distribution_nudge_mm.value()),
            calibration_preload_nudge_mm=float(self.spin_calibration_preload_nudge_mm.value()),
            calibration_preload_speed_mm_s=float(self.spin_calibration_preload_speed_mm_s.value()),
            calibration_speed_mm_s=float(self.spin_calibration_speed_mm_s.value()),
            setup_preload_stress_mpa=float(self.spin_setup_preload_stress_mpa.value()),
            setup_preload_duration_s=float(self.spin_setup_preload_duration_s.value()),
            setup_return_duration_s=float(self.spin_setup_return_duration_s.value()),
            setup_slack_speed_strain_pct_s=float(self.spin_setup_slack_speed_strain_pct_s.value()),
            setup_slack_step_cap_stress_mpa=float(self.spin_setup_slack_step_cap_stress_mpa.value()),
            setup_preload_tolerance_mpa=float(self.spin_setup_preload_tolerance_mpa.value()),
            setup_preload_stable_s=float(self.spin_setup_preload_stable_s.value()),
            setup_zero_stable_s=float(self.spin_setup_zero_stable_s.value()),
            current_sweep_target_ramp_rate_value_s=float(self.spin_current_sweep_target_ramp_rate.value()),
            current_sweep_target_speed_mm_s=float(self.spin_current_sweep_target_speed_mm_s.value()),
            current_sweep_correction_rate_pct_s=float(self.spin_current_sweep_correction_rate_pct_s.value()),
            current_sweep_max_correction_strain_pct=float(self.spin_current_sweep_max_correction_strain_pct.value()),
            current_sweep_max_correction_stress_mpa=float(self.spin_current_sweep_max_correction_stress_mpa.value()),
            current_sweep_hold_correction_stress_mpa=float(self.spin_current_sweep_hold_correction_stress_mpa.value()),
            current_sweep_mid_correction_stress_mpa=float(self.spin_current_sweep_mid_correction_stress_mpa.value()),
            current_sweep_near_correction_stress_mpa=float(self.spin_current_sweep_near_correction_stress_mpa.value()),
            current_sweep_hold_pause_factor=float(self.spin_current_sweep_hold_pause_factor.value()),
            current_sweep_hold_resume_factor=float(self.spin_current_sweep_hold_resume_factor.value()),
            current_sweep_hold_resume_stable_s=float(self.spin_current_sweep_hold_resume_stable_s.value()),
            current_sweep_hold_filter_window_s=float(self.spin_current_sweep_hold_filter_window_s.value()),
            current_sweep_hold_noise_sigma=float(self.spin_current_sweep_hold_noise_sigma.value()),
            current_sweep_hold_min_pause_stress_mpa=float(self.spin_current_sweep_hold_min_pause_stress_mpa.value()),
            current_sweep_hold_min_resume_stress_mpa=float(self.spin_current_sweep_hold_min_resume_stress_mpa.value()),
            current_sweep_tolerance=float(self.spin_current_sweep_tolerance.value()),
            current_sweep_nudge_mm=float(self.spin_current_sweep_nudge_mm.value()),
            current_sweep_balance_speed_mm_s=float(self.spin_current_sweep_balance_speed_mm_s.value()),
            current_sweep_max_seek_mm=self._current_sweep_config_max_seek_mm(),
            supply_profile_id=str(self.combo_supply_profile.currentData() or "hmp4030"),
            supply_current_resolution_mA=supply_resolution,
            motor_supply_enabled=self.check_motor_supply_power.isChecked(),
            return_to_origin=self.check_return_to_origin.isChecked(),
        )

    def _control_config(self) -> MiniDmaControlConfig | None:
        return self._active_control_config

    def _current_sweep_config_max_seek_mm(self) -> float:
        value = float(self.spin_current_sweep_max_seek_mm.value())
        override = getattr(self, "_bench_mechanical_slack_max_seek_mm", None)
        if bool(getattr(self, "_bench_allow_mechanical_slack_takeup", False)) and override is not None:
            value = max(value, float(override))
        return value

    def set_bench_mechanical_slack_takeup(
        self,
        *,
        allow: bool,
        max_seek_mm: float | None = None,
    ) -> None:
        self._bench_allow_mechanical_slack_takeup = bool(allow)
        self._bench_mechanical_slack_max_seek_mm = None if max_seek_mm is None else max(
            self._motor_step_mm(),
            float(max_seek_mm),
        )
        self._bench_mechanical_slack_takeup_logged_keys.clear()

    def _show_timing_settings_dialog(self) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Mini DMA Timing")
        layout = QtWidgets.QVBoxLayout(dialog)
        form = QtWidgets.QFormLayout()
        control_interval = QtWidgets.QSpinBox(dialog)
        control_interval.setRange(10, 5000)
        control_interval.setValue(self._control_interval_ms())
        control_interval.setSuffix(" ms")
        form.addRow("Control interval", control_interval)
        log_interval = QtWidgets.QSpinBox(dialog)
        log_interval.setRange(50, 60000)
        log_interval.setValue(self._log_interval_ms())
        log_interval.setSuffix(" ms")
        form.addRow("Data log interval", log_interval)
        ui_interval = QtWidgets.QSpinBox(dialog)
        ui_interval.setRange(50, 5000)
        ui_interval.setValue(self._ui_refresh_interval_ms())
        ui_interval.setSuffix(" ms")
        form.addRow("Live label/telemetry interval", ui_interval)
        graph_interval = QtWidgets.QSpinBox(dialog)
        graph_interval.setRange(100, 60000)
        graph_interval.setValue(self._graph_refresh_interval_ms())
        graph_interval.setSuffix(" ms")
        form.addRow("Dashboard graph interval", graph_interval)
        scale_interval = QtWidgets.QSpinBox(dialog)
        scale_interval.setRange(50, 60000)
        scale_interval.setValue(int(self.spin_scale_interval.value()))
        scale_interval.setSuffix(" ms")
        form.addRow("Scale acquisition interval", scale_interval)
        tic_status_interval = QtWidgets.QSpinBox(dialog)
        tic_status_interval.setRange(100, 60000)
        tic_status_interval.setValue(self._tic_status_interval_ms())
        tic_status_interval.setSuffix(" ms")
        form.addRow("Tic status interval", tic_status_interval)
        tic_keepalive_interval = QtWidgets.QSpinBox(dialog)
        tic_keepalive_interval.setRange(100, 5000)
        tic_keepalive_interval.setValue(self._tic_keepalive_interval_ms())
        tic_keepalive_interval.setSuffix(" ms")
        form.addRow("Tic keepalive interval", tic_keepalive_interval)
        supply_read_interval = QtWidgets.QSpinBox(dialog)
        supply_read_interval.setRange(100, 60000)
        supply_read_interval.setValue(self._supply_read_interval_ms())
        supply_read_interval.setSuffix(" ms")
        form.addRow("Supply readback interval", supply_read_interval)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self.spin_control_interval.setValue(control_interval.value())
        self.spin_log_interval.setValue(log_interval.value())
        self.spin_ui_interval.setValue(ui_interval.value())
        self.spin_graph_interval.setValue(graph_interval.value())
        self.spin_scale_interval.setValue(scale_interval.value())
        self.spin_tic_status_interval.setValue(tic_status_interval.value())
        self.spin_tic_keepalive_interval.setValue(tic_keepalive_interval.value())
        self.spin_supply_read_interval.setValue(supply_read_interval.value())
        self._apply_ui_refresh_interval()
        self._apply_hardware_timer_intervals()
        self._update_recipe_mode_ui()

    def _set_run_log_mirror_enabled(self, enabled: bool) -> None:
        self._run_log_mirror_enabled = bool(enabled)
        if hasattr(self, "action_mirror_run_log") and self.action_mirror_run_log is not None:
            self.action_mirror_run_log.blockSignals(True)
            self.action_mirror_run_log.setChecked(self._run_log_mirror_enabled)
            self.action_mirror_run_log.blockSignals(False)
        state = "enabled" if self._run_log_mirror_enabled else "disabled"
        self._log(f"Run-log file mirror {state}: {self._run_log_mirror_path}")

    def _choose_run_log_mirror_file(self) -> None:
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select Mini DMA run-log mirror file",
            str(self._run_log_mirror_path),
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        self._run_log_mirror_path = Path(path)
        self._set_run_log_mirror_enabled(True)

    def _spin_with_equivalent_label(
        self,
        parent: QtWidgets.QWidget,
        spinbox: QtWidgets.QWidget,
    ) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
        row = QtWidgets.QWidget(parent)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        spinbox.setMinimumWidth(max(spinbox.minimumWidth(), 130))
        layout.addWidget(spinbox, stretch=1)
        label = QtWidgets.QLabel("-", row)
        label.setMinimumWidth(88)
        label.setStyleSheet("color: palette(text);")
        layout.addWidget(label)
        return row, label

    def _hide_form_row(self, form: QtWidgets.QFormLayout, field: QtWidgets.QWidget) -> None:
        field.setVisible(False)
        label = form.labelForField(field)
        if label is not None:
            label.setVisible(False)

    def _set_form_row_visible(
        self,
        form: QtWidgets.QFormLayout,
        field: QtWidgets.QWidget,
        visible: bool,
    ) -> None:
        field.setVisible(visible)
        label = form.labelForField(field)
        if label is not None:
            label.setVisible(visible)

    def _build_dashboard_value_cell(
        self,
        parent: QtWidgets.QWidget,
        key: str,
        title: str,
        *,
        min_width: int = 96,
        fixed_height: int | None = None,
    ) -> QtWidgets.QFrame:
        cell = QtWidgets.QFrame(parent)
        cell.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        cell.setMinimumWidth(min_width)
        if fixed_height is not None:
            cell.setFixedHeight(fixed_height)
        cell.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.MinimumExpanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        layout = QtWidgets.QHBoxLayout(cell)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(5)
        title_label = QtWidgets.QLabel(title, cell)
        title_font = title_label.font()
        title_font.setPointSize(max(8, title_font.pointSize() - 1))
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: palette(light);")
        value_label = QtWidgets.QLabel("-", cell)
        value_font = value_label.font()
        value_font.setBold(True)
        value_label.setFont(value_font)
        value_label.setMinimumWidth(max(54, min_width - 42))
        value_label.setWordWrap(False)
        value_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        if fixed_height is not None:
            value_label.setFixedHeight(max(16, fixed_height - 4))
        layout.addWidget(title_label)
        layout.addWidget(value_label, stretch=1)
        self._dashboard_value_labels[key] = value_label
        return cell

    def _set_dashboard_value(self, key: str, text: str) -> None:
        label = self._dashboard_value_labels.get(key)
        if label is not None:
            label.setText(text)
            label.setToolTip(text)

    def _build_ui(self, log_dir: str) -> None:
        install_standard_menu(self, open_folder=self._choose_log_dir)
        self._install_mini_dma_settings_menu()
        self._install_mini_dma_developer_menu()

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, central)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        control_column = QtWidgets.QWidget(splitter)
        control_column.setMinimumWidth(560)
        control_column.setMaximumWidth(720)
        control_column_layout = QtWidgets.QVBoxLayout(control_column)
        control_column_layout.setContentsMargins(0, 0, 0, 0)
        control_column_layout.setSpacing(6)

        control_scroll = QtWidgets.QScrollArea(control_column)
        self._control_scroll_area = control_scroll
        control_scroll.setWidgetResizable(True)
        control_scroll.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        control_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        control_scroll.horizontalScrollBar().setFixedHeight(0)
        control_scroll.setMinimumWidth(560)
        control_scroll.setMaximumWidth(720)
        control_column_layout.addWidget(control_scroll, stretch=1)

        self.recipe_action_footer = QtWidgets.QFrame(control_column)
        self.recipe_action_footer.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.recipe_action_footer_layout = QtWidgets.QVBoxLayout(self.recipe_action_footer)
        self.recipe_action_footer_layout.setContentsMargins(8, 8, 8, 8)
        self.recipe_action_footer_layout.setSpacing(6)
        control_column_layout.addWidget(self.recipe_action_footer)

        control_panel = QtWidgets.QWidget(control_scroll)
        control_panel.setMinimumWidth(0)
        control_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        control_scroll.setWidget(control_panel)
        controls = QtWidgets.QVBoxLayout(control_panel)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(10)

        self.label_session_status = QtWidgets.QLabel("Session idle", control_panel)
        self.label_live_summary = QtWidgets.QLabel("Live strain: - | Live stress: -", control_panel)
        self.label_live_speed = QtWidgets.QLabel("Command speed: -", control_panel)
        self.label_card_session = QtWidgets.QLabel("Idle", control_panel)
        self.label_card_scale = QtWidgets.QLabel("Disconnected", control_panel)
        self.label_card_motion = QtWidgets.QLabel("Unknown", control_panel)
        self.label_card_recipe = QtWidgets.QLabel("Manual", control_panel)
        for hidden_status_label in (
            self.label_session_status,
            self.label_live_summary,
            self.label_live_speed,
            self.label_card_session,
            self.label_card_scale,
            self.label_card_motion,
            self.label_card_recipe,
        ):
            hidden_status_label.setVisible(False)

        tabs = QtWidgets.QTabWidget(control_panel)
        tabs.setMinimumWidth(0)
        tabs.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        controls.addWidget(tabs)

        hardware_tab = QtWidgets.QWidget(tabs)
        hardware_layout = QtWidgets.QVBoxLayout(hardware_tab)
        hardware_layout.setContentsMargins(0, 0, 0, 0)
        hardware_layout.setSpacing(10)

        scale_box = self._group_box("Scale")
        scale_box.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        scale_form = QtWidgets.QFormLayout(scale_box)
        scale_action_row = QtWidgets.QHBoxLayout()
        detect_scale_button = QtWidgets.QPushButton("Auto-detect scale", scale_box)
        detect_scale_button.clicked.connect(self._auto_detect_scale_port)
        scale_action_row.addWidget(detect_scale_button)
        self.button_scale_connect = QtWidgets.QPushButton("Connect scale", scale_box)
        self.button_scale_connect.clicked.connect(self._toggle_scale_connection)
        scale_action_row.addWidget(self.button_scale_connect)
        scale_form.addRow("", scale_action_row)
        scale_zero_row = QtWidgets.QHBoxLayout()
        self.button_scale_tare = QtWidgets.QPushButton("Capture zero-load", scale_box)
        self.button_scale_tare.setToolTip("Use the current real scale reading as the 0 g applied-load reference.")
        self.button_scale_tare.clicked.connect(self._capture_zero_load_scale_reference)
        scale_zero_row.addWidget(self.button_scale_tare)
        self.button_scale_hardware_tare = QtWidgets.QPushButton("Tare scale", scale_box)
        self.button_scale_hardware_tare.setToolTip(
            "Occasional use. Sends the physical tare command to the balance. "
            "Use Capture zero-load to change Mini DMA's zero-load reference."
        )
        self.button_scale_hardware_tare.clicked.connect(self._tare_scale_hardware)
        scale_zero_row.addWidget(self.button_scale_hardware_tare)
        scale_form.addRow("", scale_zero_row)

        self.label_scale_value = QtWidgets.QLabel("Latest load: 0.000 g", scale_box)
        self.label_scale_value.setWordWrap(True)
        scale_form.addRow("", self.label_scale_value)
        scale_help = QtWidgets.QLabel(
            "Use Auto-detect after reconnecting USB devices. Usually leave the balance showing real grams and use Capture zero-load; use Tare scale only when the physical balance needs to be re-zeroed.",
            scale_box,
        )
        scale_help.setWordWrap(True)
        scale_help.setStyleSheet("color: #a3a3a3;")
        scale_form.addRow("", scale_help)
        hardware_layout.addWidget(scale_box)

        motion_box = self._group_box("Motion")
        motion_box.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        motion_form = QtWidgets.QFormLayout(motion_box)
        motion_buttons = QtWidgets.QHBoxLayout()
        refresh_tic_button = QtWidgets.QPushButton("Check motor", motion_box)
        refresh_tic_button.clicked.connect(self._refresh_tic_status)
        motion_buttons.addWidget(refresh_tic_button)
        detect_tic_button = QtWidgets.QPushButton("Auto-detect motor", motion_box)
        detect_tic_button.clicked.connect(self._auto_detect_tic)
        motion_buttons.addWidget(detect_tic_button)
        zero_tic_button = QtWidgets.QPushButton("Set position = 0", motion_box)
        zero_tic_button.clicked.connect(self._zero_tic_position)
        motion_buttons.addWidget(zero_tic_button)
        halt_tic_button = QtWidgets.QPushButton("Halt motor", motion_box)
        halt_tic_button.clicked.connect(self._halt_tic)
        motion_buttons.addWidget(halt_tic_button)
        motion_form.addRow("", motion_buttons)

        self.spin_jog_mm = CompactDoubleSpinBox(motion_box)
        self.spin_jog_mm.setDecimals(4)
        self.spin_jog_mm.setRange(0.0001, 10.0)
        self.spin_jog_mm.setValue(0.1)
        self.spin_jog_mm.setToolTip("Single-click jog distance. Holding the manual arrows uses Manual move speed instead.")
        motion_form.addRow("Jog step", self.spin_jog_mm)

        self.spin_motion_speed_mm_s = CompactDoubleSpinBox(motion_box)
        self.spin_motion_speed_mm_s.setDecimals(3)
        self.spin_motion_speed_mm_s.setRange(0.0001, 50.0)
        self.spin_motion_speed_mm_s.setValue(1.0)
        self.spin_motion_speed_mm_s.setSuffix(" mm/s")
        self.spin_motion_speed_mm_s.setToolTip("Linear stage speed for held manual movement.")

        jog_buttons = QtWidgets.QHBoxLayout()
        jog_negative = QtWidgets.QPushButton("▲ Move up / increase tension", motion_box)
        jog_negative.setObjectName("hardware_jog_tension_button")
        self._configure_manual_jog_button(jog_negative, lambda: self._tension_motion_sign())
        jog_buttons.addWidget(jog_negative)
        jog_positive = QtWidgets.QPushButton("▼ Move down / relax", motion_box)
        jog_positive.setObjectName("hardware_jog_relax_button")
        self._configure_manual_jog_button(jog_positive, lambda: -self._tension_motion_sign())
        jog_buttons.addWidget(jog_positive)
        motion_form.addRow("", jog_buttons)

        self.label_tic_position = QtWidgets.QLabel("Position: 0.0000 mm", motion_box)
        self.label_tic_summary = QtWidgets.QLabel("Motor status not queried yet.", motion_box)
        self.label_tic_summary.setWordWrap(True)
        motion_form.addRow("", self.label_tic_position)
        motion_form.addRow("", self.label_tic_summary)
        hardware_layout.addWidget(motion_box)

        advanced_toggle = QtWidgets.QToolButton(hardware_tab)
        advanced_toggle.setText("Advanced hardware settings")
        advanced_toggle.setCheckable(True)
        advanced_toggle.setChecked(False)
        advanced_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        advanced_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        hardware_layout.addWidget(advanced_toggle)

        self.advanced_hardware_panel = QtWidgets.QWidget(hardware_tab)
        advanced_layout = QtWidgets.QVBoxLayout(self.advanced_hardware_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(10)
        self.advanced_hardware_panel.setVisible(False)

        def _toggle_advanced_hardware(checked: bool) -> None:
            self.advanced_hardware_panel.setVisible(checked)
            advanced_toggle.setArrowType(
                QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
            )

        advanced_toggle.toggled.connect(_toggle_advanced_hardware)

        scale_advanced_box = self._group_box("Scale Driver Details")
        scale_advanced_form = QtWidgets.QFormLayout(scale_advanced_box)
        self.combo_scale_port = QtWidgets.QComboBox(scale_advanced_box)
        refresh_ports_button = QtWidgets.QPushButton("Refresh ports", scale_advanced_box)
        refresh_ports_button.clicked.connect(self._refresh_scale_ports)
        port_row = QtWidgets.QHBoxLayout()
        port_row.addWidget(self.combo_scale_port, stretch=1)
        port_row.addWidget(refresh_ports_button)
        scale_advanced_form.addRow("Port", port_row)

        self.combo_scale_baud = QtWidgets.QComboBox(scale_advanced_box)
        for baud in ("600", "1200", "2400", "4800", "9600", "19200", "38400", "115200"):
            self.combo_scale_baud.addItem(baud)
        self.combo_scale_baud.setCurrentText("600")
        scale_advanced_form.addRow("Baud", self.combo_scale_baud)

        self.spin_scale_interval = QtWidgets.QSpinBox(scale_advanced_box)
        self.spin_scale_interval.setRange(50, 5000)
        self.spin_scale_interval.setSuffix(" ms")
        self.spin_scale_interval.setValue(DEFAULT_SCALE_REQUEST_INTERVAL_MS)
        scale_advanced_form.addRow("Poll interval", self.spin_scale_interval)
        self.spin_scale_interval.setVisible(False)
        scale_interval_label = scale_advanced_form.labelForField(self.spin_scale_interval)
        if scale_interval_label is not None:
            scale_interval_label.setVisible(False)

        self.edit_scale_request = QtWidgets.QLineEdit(scale_advanced_box)
        self.edit_scale_request.setPlaceholderText("leave blank if the scale streams continuously")
        scale_advanced_form.addRow("Request command", self.edit_scale_request)

        self.edit_scale_terminator = QtWidgets.QLineEdit(scale_advanced_box)
        self.edit_scale_terminator.setText("")
        scale_advanced_form.addRow("Line ending", self.edit_scale_terminator)

        self.label_scale_raw = QtWidgets.QLabel("Raw line: -", scale_advanced_box)
        self.label_scale_raw.setWordWrap(True)
        self.label_scale_hint = QtWidgets.QLabel(
            "G&G RS232 note: these balances often need a DB9 null modem crossover between the "
            "USB-serial adapter and the scale.",
            scale_advanced_box,
        )
        self.label_scale_hint.setWordWrap(True)
        gng_button = QtWidgets.QPushButton("Apply G&G E-series preset", scale_advanced_box)
        gng_button.clicked.connect(self._apply_gng_scale_preset)
        probe_button = QtWidgets.QPushButton("Probe scale", scale_advanced_box)
        probe_button.clicked.connect(self._probe_scale_port)
        remote_tare_button = QtWidgets.QPushButton("Diagnostic remote tare scale", scale_advanced_box)
        remote_tare_button.setToolTip(
            "Advanced only. Sends the physical scale tare command without changing Mini DMA's zero-load reference."
        )
        remote_tare_button.clicked.connect(self._tare_scale_hardware)
        self.button_advanced_software_tare = QtWidgets.QPushButton(
            "Diagnostic software tare (app only)",
            scale_advanced_box,
        )
        self.button_advanced_software_tare.setToolTip(
            "Advanced fallback for diagnostics only: offsets Mini DMA without changing the physical scale display."
        )
        self.button_advanced_software_tare.clicked.connect(self._tare_scale)
        scale_advanced_form.addRow("", self.label_scale_raw)
        scale_advanced_form.addRow("", self.label_scale_hint)
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(gng_button)
        preset_row.addWidget(probe_button)
        scale_advanced_form.addRow("", preset_row)
        scale_advanced_form.addRow("", remote_tare_button)
        scale_advanced_form.addRow("", self.button_advanced_software_tare)
        advanced_layout.addWidget(scale_advanced_box)

        motion_advanced_box = self._group_box("Motor Driver Details")
        motion_advanced_form = QtWidgets.QFormLayout(motion_advanced_box)

        self.edit_ticcmd_path = QtWidgets.QLineEdit(motion_advanced_box)
        self.edit_ticcmd_path.setText(_find_ticcmd())
        motion_advanced_form.addRow("ticcmd path", self.edit_ticcmd_path)

        self.check_tic_native_usb = QtWidgets.QCheckBox(
            "Prefer native USB commands when available",
            motion_advanced_box,
        )
        self.check_tic_native_usb.setToolTip(
            "Uses PyUSB/libusb for Tic commands and falls back to ticcmd if native USB is unavailable."
        )
        self.check_tic_native_usb.setChecked(True)
        motion_advanced_form.addRow("", self.check_tic_native_usb)

        self.edit_tic_serial = QtWidgets.QLineEdit(motion_advanced_box)
        self.edit_tic_serial.setPlaceholderText("optional when only one Tic is connected")
        motion_advanced_form.addRow("Device serial", self.edit_tic_serial)

        self.spin_tic_status_interval = QtWidgets.QSpinBox(motion_advanced_box)
        self.spin_tic_status_interval.setRange(100, 60000)
        self.spin_tic_status_interval.setSuffix(" ms")
        self.spin_tic_status_interval.setValue(DEFAULT_TIC_STATUS_INTERVAL_MS)
        motion_advanced_form.addRow("Status interval", self.spin_tic_status_interval)
        self.spin_tic_status_interval.setVisible(False)
        tic_status_label = motion_advanced_form.labelForField(self.spin_tic_status_interval)
        if tic_status_label is not None:
            tic_status_label.setVisible(False)

        self.spin_tic_keepalive_interval = QtWidgets.QSpinBox(motion_advanced_box)
        self.spin_tic_keepalive_interval.setRange(100, 5000)
        self.spin_tic_keepalive_interval.setSuffix(" ms")
        self.spin_tic_keepalive_interval.setValue(TIC_KEEPALIVE_INTERVAL_MS)
        motion_advanced_form.addRow("Keepalive interval", self.spin_tic_keepalive_interval)
        self.spin_tic_keepalive_interval.setVisible(False)
        tic_keepalive_label = motion_advanced_form.labelForField(self.spin_tic_keepalive_interval)
        if tic_keepalive_label is not None:
            tic_keepalive_label.setVisible(False)

        self.spin_full_steps_per_mm = CompactDoubleSpinBox(motion_advanced_box)
        self.spin_full_steps_per_mm.setDecimals(4)
        self.spin_full_steps_per_mm.setRange(0.001, 100000.0)
        self.spin_full_steps_per_mm.setValue(DEFAULT_FULL_STEPS_PER_MM)
        self.spin_full_steps_per_mm.setToolTip(
            "Mechanical full motor steps per mm before Tic microstepping. "
            "The current external-gauge calibration confirms about 100 full steps/mm."
        )
        motion_advanced_form.addRow("Full steps/mm", self.spin_full_steps_per_mm)

        step_mode_row = QtWidgets.QHBoxLayout()
        self.combo_tic_step_mode = QtWidgets.QComboBox(motion_advanced_box)
        for label, value in TIC_STEP_MODE_OPTIONS:
            self.combo_tic_step_mode.addItem(label, value)
        default_step_mode_index = self.combo_tic_step_mode.findData(DEFAULT_TIC_STEP_MODE)
        if default_step_mode_index >= 0:
            self.combo_tic_step_mode.setCurrentIndex(default_step_mode_index)
        self.combo_tic_step_mode.setToolTip(
            "Tic microstep mode. Applying this changes the controller step mode and rescales the Tic "
            "position register so the physical mm position stays continuous."
        )
        self.button_apply_tic_step_mode = QtWidgets.QPushButton("Apply", motion_advanced_box)
        self.button_apply_tic_step_mode.setToolTip(
            "Apply the selected Tic step mode, then rewrite the current Tic position to preserve physical mm."
        )
        self.button_apply_tic_step_mode.clicked.connect(self._apply_tic_step_mode)
        step_mode_row.addWidget(self.combo_tic_step_mode, stretch=1)
        step_mode_row.addWidget(self.button_apply_tic_step_mode)
        motion_advanced_form.addRow("Tic step mode", step_mode_row)

        self.spin_tic_current_limit_mA = QtWidgets.QSpinBox(motion_advanced_box)
        self.spin_tic_current_limit_mA.setRange(0, 3000)
        self.spin_tic_current_limit_mA.setSingleStep(TIC_CURRENT_LIMIT_STEP_MA)
        self.spin_tic_current_limit_mA.setValue(DEFAULT_TIC_CURRENT_LIMIT_MA)
        self.spin_tic_current_limit_mA.setSuffix(" mA")
        self.spin_tic_current_limit_mA.setToolTip(
            "Tic motor winding current limit. This is separate from the HMP motor-supply rail current limit."
        )
        motion_advanced_form.addRow("Tic motor current limit", self.spin_tic_current_limit_mA)

        self.spin_steps_per_mm = CompactDoubleSpinBox(motion_advanced_box)
        self.spin_steps_per_mm.setDecimals(3)
        self.spin_steps_per_mm.setRange(1.0, 100000.0)
        self.spin_steps_per_mm.setValue(DEFAULT_STEPS_PER_MM)
        self.spin_steps_per_mm.setReadOnly(True)
        self.spin_steps_per_mm.setToolTip(
            "Tic units/mm, not full motor steps/mm. The current 800 Tic units/mm default "
            "matches 100 full motor steps/mm with the Tic set to 1/8 step."
        )
        motion_advanced_form.addRow("Tic units/mm", self.spin_steps_per_mm)

        self.label_tic_settings_summary = QtWidgets.QLabel("Live Tic settings: not queried yet.", motion_advanced_box)
        self.label_tic_settings_summary.setWordWrap(True)
        motion_advanced_form.addRow("", self.label_tic_settings_summary)

        self.spin_motor_step_calibration_increment_steps = QtWidgets.QSpinBox(motion_advanced_box)
        self.spin_motor_step_calibration_increment_steps.setRange(1, 1000000)
        self.spin_motor_step_calibration_increment_steps.setValue(MOTOR_STEP_CALIBRATION_DEFAULT_INCREMENT_STEPS)
        self.spin_motor_step_calibration_increment_steps.setSuffix(" Tic units")
        self.spin_motor_step_calibration_increment_steps.setToolTip(
            "Raw Tic position units per calibration move. This does not use the current Tic units/mm value."
        )
        motion_advanced_form.addRow("Calibration move", self.spin_motor_step_calibration_increment_steps)

        self.spin_motor_step_calibration_moves = QtWidgets.QSpinBox(motion_advanced_box)
        self.spin_motor_step_calibration_moves.setRange(1, 20)
        self.spin_motor_step_calibration_moves.setValue(MOTOR_STEP_CALIBRATION_DEFAULT_MOVES)
        motion_advanced_form.addRow("Calibration points", self.spin_motor_step_calibration_moves)

        self.spin_motor_step_calibration_speed_mm_s = CompactDoubleSpinBox(motion_advanced_box)
        self.spin_motor_step_calibration_speed_mm_s.setDecimals(4)
        self.spin_motor_step_calibration_speed_mm_s.setRange(0.0001, 10.0)
        self.spin_motor_step_calibration_speed_mm_s.setValue(MOTOR_STEP_CALIBRATION_DEFAULT_SPEED_MM_S)
        self.spin_motor_step_calibration_speed_mm_s.setSuffix(" mm/s")
        self.spin_motor_step_calibration_speed_mm_s.setToolTip(
            "Expected linear calibration speed. Mini DMA converts this to raw Tic units/s using the calibration move size."
        )
        motion_advanced_form.addRow("Calibration speed", self.spin_motor_step_calibration_speed_mm_s)

        self.button_motor_step_calibration = QtWidgets.QPushButton("Run motor step calibration", motion_advanced_box)
        self.button_motor_step_calibration.setObjectName("motor_step_calibration_button")
        self.button_motor_step_calibration.setToolTip(
            "Prompt for an external-gauge baseline, move down by raw Tic units, prompt after each move, "
            "and save CSV/JSON logs without applying the result by default."
        )
        self.button_motor_step_calibration.clicked.connect(self._run_motor_step_calibration)
        motion_advanced_form.addRow("", self.button_motor_step_calibration)
        advanced_layout.addWidget(motion_advanced_box)
        hardware_layout.addWidget(self.advanced_hardware_panel)

        safety_box = self._group_box("Reference & Safety")
        safety_form = QtWidgets.QFormLayout(safety_box)
        self.button_set_reference_now = QtWidgets.QPushButton("Use current position as zero", safety_box)
        self.button_set_reference_now.clicked.connect(self._set_position_reference_now)
        safety_form.addRow("", self.button_set_reference_now)

        self.check_soft_limits = QtWidgets.QCheckBox("Enable position soft limits", safety_box)
        safety_form.addRow("", self.check_soft_limits)
        soft_limit_row = QtWidgets.QHBoxLayout()
        self.spin_soft_min_mm = CompactDoubleSpinBox(safety_box)
        self.spin_soft_min_mm.setDecimals(4)
        self.spin_soft_min_mm.setRange(-100.0, 100.0)
        self.spin_soft_min_mm.setValue(-5.0)
        self.spin_soft_min_mm.setSuffix(" mm")
        self.spin_soft_max_mm = CompactDoubleSpinBox(safety_box)
        self.spin_soft_max_mm.setDecimals(4)
        self.spin_soft_max_mm.setRange(-100.0, 100.0)
        self.spin_soft_max_mm.setValue(5.0)
        self.spin_soft_max_mm.setSuffix(" mm")
        soft_limit_row.addWidget(QtWidgets.QLabel("Min", safety_box))
        soft_limit_row.addWidget(self.spin_soft_min_mm)
        soft_limit_row.addWidget(QtWidgets.QLabel("Max", safety_box))
        soft_limit_row.addWidget(self.spin_soft_max_mm)
        safety_form.addRow("Soft limits", soft_limit_row)

        self.check_max_load = QtWidgets.QCheckBox("Use lower applied-load limit", safety_box)
        self.check_max_load.setToolTip(
            "The hanging-weight zero-load reading is always used as the physical load ceiling. "
            "Enable this only to stop below that weight."
        )
        safety_form.addRow("", self.check_max_load)
        self.spin_max_load_g = CompactDoubleSpinBox(safety_box)
        self.spin_max_load_g.setDecimals(3)
        self.spin_max_load_g.setRange(0.001, 1000.0)
        self.spin_max_load_g.setValue(DEFAULT_ZERO_LOAD_SCALE_G)
        self.spin_max_load_g.setSuffix(" g")
        safety_form.addRow("Lower load limit", self.spin_max_load_g)
        zero_load_row = QtWidgets.QHBoxLayout()
        self.spin_zero_load_scale_g = CompactDoubleSpinBox(safety_box)
        self.spin_zero_load_scale_g.setDecimals(4)
        self.spin_zero_load_scale_g.setRange(-100000.0, 100000.0)
        self.spin_zero_load_scale_g.setValue(DEFAULT_ZERO_LOAD_SCALE_G)
        self.spin_zero_load_scale_g.setSuffix(" g")
        self.spin_zero_load_scale_g.setToolTip(
            "Real scale reading when the hanging weight applies 0 g to the wire. "
            "For the current 21.200 g weight, a raw scale reading of 18.200 g means 3.000 g applied load."
        )
        self.spin_zero_load_scale_g.valueChanged.connect(self._handle_zero_load_scale_changed)
        zero_load_row.addWidget(self.spin_zero_load_scale_g, stretch=1)
        capture_zero_button = QtWidgets.QPushButton("Use live", safety_box)
        capture_zero_button.setToolTip("Set the zero-load reference from the current raw scale reading.")
        capture_zero_button.clicked.connect(self._capture_zero_load_scale_reference)
        zero_load_row.addWidget(capture_zero_button)
        safety_form.addRow("Zero-load scale reading", zero_load_row)
        self.spin_raw_scale_limit_g = CompactDoubleSpinBox(safety_box)
        self.spin_raw_scale_limit_g.setDecimals(3)
        self.spin_raw_scale_limit_g.setRange(0.001, 100000.0)
        self.spin_raw_scale_limit_g.setValue(RAW_SCALE_DISPLAY_LIMIT_DEFAULT_G)
        self.spin_raw_scale_limit_g.setSuffix(" g")
        self.spin_raw_scale_limit_g.setToolTip(
            "Maximum allowed raw balance display. If the balance reaches this value, Mini DMA halts automation "
            "and blocks ordinary motor moves until the display is below the limit again."
        )
        safety_form.addRow("Raw scale display limit", self.spin_raw_scale_limit_g)
        self.check_tension_load_positive = QtWidgets.QCheckBox(
            "Tension makes the scale reading decrease",
            safety_box,
        )
        self.check_tension_load_positive.setChecked(True)
        self.check_tension_load_positive.setToolTip(
            "Leave checked for the hanging-weight setup: pulling up unloads the balance, "
            "so applied wire load is zero-load reading minus current scale reading."
        )
        self.check_tension_load_positive.toggled.connect(self._handle_scale_reference_setting_changed)
        safety_form.addRow("", self.check_tension_load_positive)
        self.check_positive_motion_is_tension = QtWidgets.QCheckBox(
            "Positive raw Tic motion pulls the wire",
            safety_box,
        )
        self.check_positive_motion_is_tension.setChecked(False)
        self.check_positive_motion_is_tension.setToolTip(
            "Leave unchecked for the current Mini DMA rig: pulling up makes the raw Tic position negative, "
            "while Mini DMA displays and logs that tensile displacement as positive."
        )
        self.check_positive_motion_is_tension.toggled.connect(lambda _checked: self._refresh_live_labels())
        safety_form.addRow("", self.check_positive_motion_is_tension)

        self.spin_backlash_mm = CompactDoubleSpinBox(safety_box)
        self.spin_backlash_mm.setDecimals(4)
        self.spin_backlash_mm.setRange(0.0, 5.0)
        self.spin_backlash_mm.setValue(0.02)
        self.spin_backlash_mm.setSuffix(" mm")
        self.spin_backlash_mm.setToolTip(
            "Optional measured linear backlash. When the controller reverses direction while seeking a target, "
            "this extra take-up distance is added once before the normal correction step."
        )
        safety_form.addRow("Backlash take-up", self.spin_backlash_mm)

        self.label_reference_status = QtWidgets.QLabel("Reference position: 0.0000 mm")
        self.label_reference_status.setWordWrap(True)
        safety_form.addRow("", self.label_reference_status)
        hardware_layout.addWidget(safety_box)

        heating_tab = QtWidgets.QWidget(tabs)
        heating_layout = QtWidgets.QVBoxLayout(heating_tab)
        heating_layout.setContentsMargins(0, 0, 0, 0)
        heating_layout.setSpacing(10)

        supply_box = self._group_box("Current Annealing")
        supply_form = QtWidgets.QFormLayout(supply_box)
        self.combo_supply_port = QtWidgets.QComboBox(supply_box)
        refresh_supply_button = QtWidgets.QPushButton("Refresh ports", supply_box)
        refresh_supply_button.clicked.connect(self._refresh_supply_ports)
        detect_supply_button = QtWidgets.QPushButton("Auto-detect", supply_box)
        detect_supply_button.clicked.connect(self._auto_detect_supply_port)
        supply_port_row = QtWidgets.QHBoxLayout()
        supply_port_row.addWidget(self.combo_supply_port, stretch=1)
        supply_port_row.addWidget(refresh_supply_button)
        supply_port_row.addWidget(detect_supply_button)
        supply_form.addRow("Port", supply_port_row)

        self.combo_supply_baud = QtWidgets.QComboBox(supply_box)
        for baud in ("9600", "19200", "38400", "57600", "115200"):
            self.combo_supply_baud.addItem(baud)
        self.combo_supply_baud.setCurrentText("9600")
        supply_form.addRow("Baud", self.combo_supply_baud)

        broker_row = QtWidgets.QHBoxLayout()
        self.edit_shared_broker_host = QtWidgets.QLineEdit("127.0.0.1", supply_box)
        self.edit_shared_broker_host.setMaximumWidth(120)
        self.edit_shared_broker_host.setToolTip("Shared HMP broker host.")
        broker_row.addWidget(self.edit_shared_broker_host)
        self.spin_shared_broker_port = QtWidgets.QSpinBox(supply_box)
        self.spin_shared_broker_port.setRange(1, 65535)
        self.spin_shared_broker_port.setValue(8765)
        self.spin_shared_broker_port.setMaximumWidth(90)
        self.spin_shared_broker_port.setToolTip("Shared HMP broker port.")
        broker_row.addWidget(self.spin_shared_broker_port)
        supply_form.addRow("Broker", broker_row)

        self.combo_supply_profile = QtWidgets.QComboBox(supply_box)
        for profile_id, profile in SUPPLY_PROFILES.items():
            self.combo_supply_profile.addItem(str(profile.get("label", profile_id)), profile_id)
        self.combo_supply_profile.currentIndexChanged.connect(self._apply_supply_profile_defaults)
        supply_form.addRow("Profile", self.combo_supply_profile)

        self.combo_current_sweep_supply_channel = QtWidgets.QComboBox(supply_box)
        self.combo_current_sweep_supply_channel.addItem("Select channel...", 0)
        for channel in range(1, 5):
            self.combo_current_sweep_supply_channel.addItem(f"CH{channel}", channel)
        supply_form.addRow("Current-sweep channel", self.combo_current_sweep_supply_channel)

        self.spin_supply_voltage_limit = CompactDoubleSpinBox(supply_box)
        self.spin_supply_voltage_limit.setDecimals(2)
        self.spin_supply_voltage_limit.setRange(0.0, 1000.0)
        self.spin_supply_voltage_limit.setValue(float(SUPPLY_PROFILES["hmp4030"]["max_voltage"]))
        self.spin_supply_voltage_limit.setSuffix(" V")
        supply_form.addRow("Voltage limit", self.spin_supply_voltage_limit)

        self.spin_supply_manual_current = CompactDoubleSpinBox(supply_box)
        self.spin_supply_manual_current.setDecimals(2)
        self.spin_supply_manual_current.setRange(0.0, 5000.0)
        self.spin_supply_manual_current.setValue(1.0)
        self.spin_supply_manual_current.setSuffix(" mA")
        supply_form.addRow("Manual set current", self.spin_supply_manual_current)

        self.check_continuity_monitor = QtWidgets.QCheckBox(
            "Run continuity current during measurements",
            supply_box,
        )
        self.check_continuity_monitor.setChecked(True)
        self.check_continuity_monitor.setToolTip(
            "Applies a small current during automated measurements so an open circuit can stop the run."
        )
        supply_form.addRow("", self.check_continuity_monitor)
        self.spin_continuity_current_mA = CompactDoubleSpinBox(supply_box)
        self.spin_continuity_current_mA.setDecimals(2)
        self.spin_continuity_current_mA.setRange(0.0, 100.0)
        self.spin_continuity_current_mA.setValue(CONTINUITY_CURRENT_DEFAULT_MA)
        self.spin_continuity_current_mA.setSuffix(" mA")
        supply_form.addRow("Continuity current", self.spin_continuity_current_mA)

        connect_supply_row = QtWidgets.QHBoxLayout()
        self.button_supply_connect = QtWidgets.QPushButton("Connect supply", supply_box)
        self.button_supply_connect.clicked.connect(self._connect_supply)
        connect_supply_row.addWidget(self.button_supply_connect)
        disconnect_supply_button = QtWidgets.QPushButton("Disconnect supply", supply_box)
        disconnect_supply_button.clicked.connect(self._disconnect_supply)
        connect_supply_row.addWidget(disconnect_supply_button)
        supply_form.addRow("", connect_supply_row)

        manual_supply_row = QtWidgets.QHBoxLayout()
        apply_current_button = QtWidgets.QPushButton("Apply current", supply_box)
        apply_current_button.clicked.connect(self._apply_manual_supply_current)
        manual_supply_row.addWidget(apply_current_button)
        output_on_button = QtWidgets.QPushButton("Output on", supply_box)
        output_on_button.clicked.connect(self._enable_supply_output)
        manual_supply_row.addWidget(output_on_button)
        output_off_button = QtWidgets.QPushButton("Output off", supply_box)
        output_off_button.clicked.connect(self._disable_supply_output)
        manual_supply_row.addWidget(output_off_button)
        supply_form.addRow("", manual_supply_row)

        read_supply_button = QtWidgets.QPushButton("Read supply now", supply_box)
        read_supply_button.clicked.connect(lambda _checked=False: self._refresh_supply_snapshot(force=True))
        supply_form.addRow("", read_supply_button)

        self.spin_supply_read_interval = QtWidgets.QSpinBox(supply_box)
        self.spin_supply_read_interval.setRange(100, 60000)
        self.spin_supply_read_interval.setSuffix(" ms")
        self.spin_supply_read_interval.setValue(DEFAULT_SUPPLY_READ_INTERVAL_MS)
        supply_form.addRow("Readback interval", self.spin_supply_read_interval)
        self.spin_supply_read_interval.setVisible(False)
        supply_read_label = supply_form.labelForField(self.spin_supply_read_interval)
        if supply_read_label is not None:
            supply_read_label.setVisible(False)

        self.label_supply_status = QtWidgets.QLabel("Supply disconnected.")
        self.label_supply_status.setWordWrap(True)
        supply_form.addRow("", self.label_supply_status)
        self.label_supply_live = QtWidgets.QLabel("Set - | Current - | Voltage - | Resistance - | Power -")
        self.label_supply_live.setWordWrap(True)
        supply_form.addRow("", self.label_supply_live)

        self.check_motor_supply_power = QtWidgets.QCheckBox(
            "Use this HMP supply to power the motor channel",
            supply_box,
        )
        supply_form.addRow("", self.check_motor_supply_power)
        motor_supply_row = QtWidgets.QHBoxLayout()
        self.combo_motor_supply_channel = QtWidgets.QComboBox(supply_box)
        self.combo_motor_supply_channel.addItem("Select channel...", 0)
        for channel in range(1, 5):
            self.combo_motor_supply_channel.addItem(f"CH{channel}", channel)
        self.spin_motor_supply_voltage = CompactDoubleSpinBox(supply_box)
        self.spin_motor_supply_voltage.setDecimals(2)
        self.spin_motor_supply_voltage.setRange(0.0, 32.05)
        self.spin_motor_supply_voltage.setValue(12.0)
        self.spin_motor_supply_voltage.setSuffix(" V")
        self.spin_motor_supply_current_limit = CompactDoubleSpinBox(supply_box)
        self.spin_motor_supply_current_limit.setDecimals(3)
        self.spin_motor_supply_current_limit.setRange(0.01, 10.0)
        self.spin_motor_supply_current_limit.setValue(DEFAULT_MOTOR_SUPPLY_CURRENT_LIMIT_A)
        self.spin_motor_supply_current_limit.setSuffix(" A")
        motor_supply_row.addWidget(self.combo_motor_supply_channel)
        motor_supply_row.addWidget(self.spin_motor_supply_voltage)
        motor_supply_row.addWidget(self.spin_motor_supply_current_limit)
        supply_form.addRow("Motor supply", motor_supply_row)
        motor_supply_buttons = QtWidgets.QHBoxLayout()
        motor_supply_on_button = QtWidgets.QPushButton("Motor power on", supply_box)
        motor_supply_on_button.clicked.connect(self._enable_motor_supply_output)
        motor_supply_buttons.addWidget(motor_supply_on_button)
        motor_supply_off_button = QtWidgets.QPushButton("Motor power off", supply_box)
        motor_supply_off_button.clicked.connect(self._disable_motor_supply_output)
        motor_supply_buttons.addWidget(motor_supply_off_button)
        supply_form.addRow("", motor_supply_buttons)
        self.button_provision_bench = QtWidgets.QPushButton("Provision bench hardware", supply_box)
        self.button_provision_bench.clicked.connect(self._provision_bench_hardware)
        supply_form.addRow("", self.button_provision_bench)
        self.label_hardware_provisioning_status = QtWidgets.QLabel("Bench provisioning has not run yet.", supply_box)
        self.label_hardware_provisioning_status.setWordWrap(True)
        supply_form.addRow("", self.label_hardware_provisioning_status)
        hardware_layout.addWidget(supply_box)

        hardware_layout.addStretch(1)

        specimen_tab = QtWidgets.QWidget(tabs)
        specimen_layout = QtWidgets.QVBoxLayout(specimen_tab)
        specimen_layout.setContentsMargins(0, 0, 0, 0)
        specimen_layout.setSpacing(10)

        naming_box = self._group_box("Naming")
        naming_form = QtWidgets.QFormLayout(naming_box)
        self.edit_name_composition = QtWidgets.QLineEdit(naming_box)
        self.edit_name_composition.setPlaceholderText("e.g. Ni51Fe26Ga21")
        naming_form.addRow("Composition", self.edit_name_composition)
        self.edit_name_wire = MicrowireLineEdit(naming_box)
        naming_form.addRow("Microwire", self.edit_name_wire)
        self.edit_name_specimen = QtWidgets.QLineEdit(naming_box)
        self.edit_name_specimen.setPlaceholderText("e.g. s1")
        naming_form.addRow("Sample ID", self.edit_name_specimen)
        self.edit_name_condition = QtWidgets.QLineEdit(naming_box)
        self.edit_name_condition.setPlaceholderText("e.g. preload test")
        naming_form.addRow("Condition / notes", self.edit_name_condition)
        specimen_layout.addWidget(naming_box)

        sample_box = self._group_box("Sample")
        sample_form = QtWidgets.QFormLayout(sample_box)
        self.spin_initial_length = CompactDoubleSpinBox(sample_box)
        self.spin_initial_length.setDecimals(3)
        self.spin_initial_length.setRange(0.0, 1000.0)
        self.spin_initial_length.setValue(30.0)
        self.spin_initial_length.setSuffix(" mm")
        self.spin_initial_length.setVisible(False)

        self.spin_diameter = CompactDoubleSpinBox(sample_box)
        self.spin_diameter.setDecimals(5)
        self.spin_diameter.setRange(0.0, 10.0)
        self.spin_diameter.setValue(0.03)
        self.spin_diameter.setSuffix(" mm")
        sample_form.addRow("Wire diameter", self.spin_diameter)

        self.check_zero_on_preload = QtWidgets.QCheckBox(
            "Zero strain/stress only after preload is reached",
            sample_box,
        )
        self.check_zero_on_preload.setChecked(False)
        self.check_zero_on_preload.setVisible(False)
        self.spin_preload_threshold_g = CompactDoubleSpinBox(sample_box)
        self.spin_preload_threshold_g.setDecimals(4)
        self.spin_preload_threshold_g.setRange(0.0, 1000.0)
        self.spin_preload_threshold_g.setValue(0.02)
        self.spin_preload_threshold_g.setSuffix(" g")
        self.spin_preload_threshold_g.setVisible(False)

        self.edit_sample_name = QtWidgets.QLineEdit(sample_box)
        sample_form.addRow("Sample name", self.edit_sample_name)
        self.edit_run_notes = QtWidgets.QPlainTextEdit(sample_box)
        self.edit_run_notes.setPlaceholderText(
            "Optional notes saved into the session metadata, for example gauge length, fixture state, or operator notes."
        )
        self.edit_run_notes.setMaximumBlockCount(200)
        self.edit_run_notes.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.edit_run_notes.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit_run_notes.setFixedHeight(80)
        sample_form.addRow("Run notes", self.edit_run_notes)
        specimen_layout.addWidget(sample_box)

        project_box = self._group_box("Builder Project")
        project_form = QtWidgets.QFormLayout(project_box)
        self.edit_project_path = QtWidgets.QLineEdit(project_box)
        project_path_row = QtWidgets.QHBoxLayout()
        project_path_row.addWidget(self.edit_project_path, stretch=1)
        browse_project_button = QtWidgets.QPushButton("Browse", project_box)
        browse_project_button.clicked.connect(self._choose_builder_project)
        project_path_row.addWidget(browse_project_button)
        import_project_button = QtWidgets.QPushButton("Import sample info", project_box)
        import_project_button.clicked.connect(self._import_builder_project)
        project_path_row.addWidget(import_project_button)
        project_form.addRow("Project (.pydpj)", project_path_row)
        self.label_project_status = QtWidgets.QLabel(
            "Load a Microwire Data Builder project to auto-fill diameter and sample metadata."
        )
        self.label_project_status.setWordWrap(True)
        project_form.addRow("", self.label_project_status)
        specimen_layout.addWidget(project_box)

        logging_box = self._group_box("Session")
        logging_form = QtWidgets.QFormLayout(logging_box)
        self.edit_log_dir = QtWidgets.QLineEdit(logging_box)
        self.edit_log_dir.setText(log_dir)
        log_dir_buttons = QtWidgets.QHBoxLayout()
        log_dir_buttons.addWidget(self.edit_log_dir, stretch=1)
        browse_button = QtWidgets.QPushButton("Browse", logging_box)
        browse_button.clicked.connect(self._choose_log_dir)
        log_dir_buttons.addWidget(browse_button)
        open_button = QtWidgets.QPushButton("Open", logging_box)
        open_button.clicked.connect(self._open_log_dir)
        log_dir_buttons.addWidget(open_button)
        logging_form.addRow("Output folder", log_dir_buttons)

        self.edit_log_name = QtWidgets.QLineEdit(logging_box)
        self.edit_log_name.setText(DEFAULT_LOG_BASENAME)
        logging_form.addRow("Base filename", self.edit_log_name)

        self.check_zero_position_on_start = QtWidgets.QCheckBox(
            "Set current Tic position to 0 when the session starts",
            logging_box,
        )
        self.check_zero_position_on_start.setChecked(False)
        self.check_zero_position_on_start.setVisible(False)

        self.check_tare_on_start = QtWidgets.QCheckBox(
            "Diagnostic: software tare the latest scale value when the session starts",
            logging_box,
        )
        self.check_tare_on_start.setChecked(False)
        self.check_tare_on_start.setVisible(False)

        self.button_start_session = QtWidgets.QPushButton("Start session", logging_box)
        self.button_start_session.clicked.connect(self._start_session)
        self.button_start_session.setVisible(False)
        self.button_stop_session = QtWidgets.QPushButton("Stop session", logging_box)
        self.button_stop_session.clicked.connect(self._stop_session)
        self.button_stop_session.setEnabled(False)
        self.button_stop_session.setVisible(False)
        specimen_layout.addWidget(logging_box)
        specimen_layout.addStretch(1)
        experiment_tab = QtWidgets.QWidget(tabs)
        experiment_layout = QtWidgets.QVBoxLayout(experiment_tab)
        experiment_layout.setContentsMargins(0, 0, 0, 0)
        experiment_layout.setSpacing(10)

        automation_box = self._group_box("Experiment Recipe")
        automation_box.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        automation_form = QtWidgets.QFormLayout(automation_box)
        self.label_recipe_sample = QtWidgets.QLabel("Sample: (unnamed sample)", automation_box)
        self.label_recipe_sample.setWordWrap(True)
        sample_font = self.label_recipe_sample.font()
        sample_font.setBold(True)
        self.label_recipe_sample.setFont(sample_font)
        automation_form.addRow("", self.label_recipe_sample)
        self.spin_control_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_control_interval.setRange(10, 5000)
        self.spin_control_interval.setValue(DEFAULT_CONTROL_INTERVAL_MS)
        self.spin_control_interval.setSuffix(" ms")
        self.spin_control_interval.setToolTip(
            "Fast internal control-loop cadence for recipe decisions and closed-loop corrections."
        )
        automation_form.addRow("Control interval", self.spin_control_interval)
        self.spin_control_interval.setVisible(False)
        control_interval_label = automation_form.labelForField(self.spin_control_interval)
        if control_interval_label is not None:
            control_interval_label.setVisible(False)
        self.spin_log_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_log_interval.setRange(50, 60000)
        self.spin_log_interval.setValue(DEFAULT_LOG_INTERVAL_MS)
        self.spin_log_interval.setSuffix(" ms")
        self.spin_log_interval.setToolTip(
            "Main TXT/CSV logging cadence. Raw scale samples are still written at the scale acquisition rate."
        )
        automation_form.addRow("Log interval", self.spin_log_interval)
        self.spin_log_interval.setVisible(False)
        log_interval_label = automation_form.labelForField(self.spin_log_interval)
        if log_interval_label is not None:
            log_interval_label.setVisible(False)
        self.spin_ui_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_ui_interval.setRange(50, 5000)
        self.spin_ui_interval.setValue(DEFAULT_UI_REFRESH_INTERVAL_MS)
        self.spin_ui_interval.setSuffix(" ms")
        self.spin_ui_interval.setToolTip("Live label refresh cadence; hardware polling has separate limits.")
        automation_form.addRow("Live label/telemetry interval", self.spin_ui_interval)
        self.spin_ui_interval.setVisible(False)
        ui_interval_label = automation_form.labelForField(self.spin_ui_interval)
        if ui_interval_label is not None:
            ui_interval_label.setVisible(False)
        self.spin_graph_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_graph_interval.setRange(100, 60000)
        self.spin_graph_interval.setValue(DEFAULT_GRAPH_REFRESH_INTERVAL_MS)
        self.spin_graph_interval.setSuffix(" ms")
        self.spin_graph_interval.setToolTip(
            "Dashboard Matplotlib redraw cadence. Data acquisition and control continue independently."
        )
        automation_form.addRow("Dashboard graph interval", self.spin_graph_interval)
        self.spin_graph_interval.setVisible(False)
        graph_interval_label = automation_form.labelForField(self.spin_graph_interval)
        if graph_interval_label is not None:
            graph_interval_label.setVisible(False)
        self.combo_recipe_mode = QtWidgets.QComboBox(automation_box)
        self.combo_recipe_mode.addItem("Displacement ramp", "ramp")
        self.combo_recipe_mode.addItem("Cyclic displacement", "cycle")
        self.combo_recipe_mode.addItem("Displacement hold", "hold")
        self.combo_recipe_mode.addItem("Hsw plateau scan", "distribution")
        self.combo_recipe_mode.addItem("Calibration", CALIBRATION)
        self.combo_recipe_mode.addItem("Iso-load current sweep", CURRENT_SWEEP_LOAD)
        self.combo_recipe_mode.addItem("Iso-stress current sweep", CURRENT_SWEEP_STRESS)
        self.combo_recipe_mode.addItem("Iso-strain current sweep", CURRENT_SWEEP_STRAIN)
        self.combo_recipe_mode.addItem("Constant-current stress-strain", CONSTANT_CURRENT_STRAIN_SWEEP)
        self.combo_recipe_mode.currentIndexChanged.connect(self._handle_recipe_mode_changed)
        automation_form.addRow("Recipe type", self.combo_recipe_mode)
        self.recipe_file_controls_widget = QtWidgets.QWidget(automation_box)
        recipe_file_row = QtWidgets.QHBoxLayout(self.recipe_file_controls_widget)
        recipe_file_row.setContentsMargins(0, 0, 0, 0)
        recipe_file_row.setSpacing(8)
        self.button_save_recipe = QtWidgets.QPushButton("Save recipe", self.recipe_file_controls_widget)
        self.button_save_recipe.clicked.connect(self._save_recipe_dialog)
        self.button_save_recipe.setMinimumWidth(120)
        recipe_file_row.addWidget(self.button_save_recipe, stretch=1)
        self.button_load_recipe = QtWidgets.QPushButton("Load recipe", self.recipe_file_controls_widget)
        self.button_load_recipe.clicked.connect(self._load_recipe_dialog)
        self.button_load_recipe.setMinimumWidth(120)
        recipe_file_row.addWidget(self.button_load_recipe, stretch=1)
        automation_form.addRow("Recipe file", self.recipe_file_controls_widget)
        self.label_recipe_file_row = automation_form.labelForField(self.recipe_file_controls_widget)
        self.label_recipe_file_status = QtWidgets.QLabel("Unsaved recipe", automation_box)
        self.label_recipe_file_status.setWordWrap(True)
        automation_form.addRow("", self.label_recipe_file_status)
        self._set_recipe_file_controls_visible(
            bool(
                self.action_show_recipe_file_controls is not None
                and self.action_show_recipe_file_controls.isChecked()
            )
        )

        self.strain_setup_box = self._group_box("Zero-load and length setup")
        strain_setup_layout = QtWidgets.QVBoxLayout(self.strain_setup_box)
        strain_setup_layout.setContentsMargins(8, 8, 8, 8)
        strain_setup_layout.setSpacing(6)
        setup_header = QtWidgets.QWidget(self.strain_setup_box)
        setup_header_layout = QtWidgets.QHBoxLayout(setup_header)
        setup_header_layout.setContentsMargins(0, 0, 0, 0)
        setup_header_layout.setSpacing(8)
        self.button_setup_details = QtWidgets.QToolButton(setup_header)
        self.button_setup_details.setText("Setup details")
        self.button_setup_details.setCheckable(True)
        self.button_setup_details.setChecked(False)
        self.button_setup_details.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.button_setup_details.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        setup_header_layout.addWidget(self.button_setup_details)
        self.label_setup_summary = QtWidgets.QLabel("Setup on", setup_header)
        self.label_setup_summary.setWordWrap(True)
        self.label_setup_summary.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        setup_header_layout.addWidget(self.label_setup_summary, stretch=1)
        strain_setup_layout.addWidget(setup_header)
        self.setup_details_panel = QtWidgets.QWidget(self.strain_setup_box)
        strain_setup_form = QtWidgets.QFormLayout(self.setup_details_panel)
        strain_setup_form.setContentsMargins(0, 4, 0, 0)
        strain_setup_layout.addWidget(self.setup_details_panel)
        self.setup_details_panel.setVisible(False)
        self.button_setup_details.toggled.connect(self._toggle_setup_details)
        self.check_pre_measurement_setup_enabled = QtWidgets.QCheckBox("Use setup", self.setup_details_panel)
        self.check_pre_measurement_setup_enabled.setChecked(True)
        self.check_pre_measurement_setup_enabled.setToolTip(
            "Run the mounted-length/preload/return setup before the recipe. "
            "Disable only for controlled automation tests or special diagnostics."
        )
        self.spin_setup_preload_stress_mpa = CompactDoubleSpinBox(self.setup_details_panel)
        self.spin_setup_preload_stress_mpa.setDecimals(3)
        self.spin_setup_preload_stress_mpa.setRange(0.001, 10000.0)
        self.spin_setup_preload_stress_mpa.setValue(10.0)
        self.spin_setup_preload_stress_mpa.setSuffix(" MPa")
        setup_stress_row, self.label_setup_preload_stress_equiv = self._spin_with_equivalent_label(
            self.setup_details_panel,
            self.spin_setup_preload_stress_mpa,
        )
        strain_setup_form.addRow("Setup preload stress", setup_stress_row)
        self.spin_setup_preload_duration_s = CompactDoubleSpinBox(self.setup_details_panel)
        self.spin_setup_preload_duration_s.setDecimals(2)
        self.spin_setup_preload_duration_s.setRange(0.1, 3600.0)
        self.spin_setup_preload_duration_s.setValue(SETUP_PRELOAD_DEFAULT_DURATION_S)
        self.spin_setup_preload_duration_s.setSuffix(" s")
        self.spin_setup_preload_duration_s.setToolTip(
            "Desired time for the setup preload ramp after the wire is engaged; Mini DMA derives the MPa/s rate."
        )
        setup_ramp_row, self.label_setup_preload_ramp_equiv = self._spin_with_equivalent_label(
            self.setup_details_panel,
            self.spin_setup_preload_duration_s,
        )
        strain_setup_form.addRow("Setup preload time", setup_ramp_row)
        self.spin_setup_slack_speed_strain_pct_s = CompactDoubleSpinBox(self.setup_details_panel)
        self.spin_setup_slack_speed_strain_pct_s.setDecimals(3)
        self.spin_setup_slack_speed_strain_pct_s.setRange(0.001, 100.0)
        self.spin_setup_slack_speed_strain_pct_s.setValue(SETUP_SLACK_DEFAULT_STRAIN_RATE_PCT_S)
        self.spin_setup_slack_speed_strain_pct_s.setSuffix(" %/s")
        self.spin_setup_slack_speed_strain_pct_s.setToolTip(
            "Mechanical take-up speed while the wire is slack and load/stress feedback is not yet meaningful."
        )
        strain_setup_form.addRow("Slack take-up speed", self.spin_setup_slack_speed_strain_pct_s)
        self.spin_setup_slack_step_cap_stress_mpa = CompactDoubleSpinBox(self.setup_details_panel)
        self.spin_setup_slack_step_cap_stress_mpa.setDecimals(2)
        self.spin_setup_slack_step_cap_stress_mpa.setRange(0.001, 10000.0)
        self.spin_setup_slack_step_cap_stress_mpa.setValue(SETUP_PRELOAD_MAX_SLACK_STEP_STRESS_MPA)
        self.spin_setup_slack_step_cap_stress_mpa.setSuffix(" MPa")
        self.spin_setup_slack_step_cap_stress_mpa.setToolTip(
            "Maximum stiffness-prior-equivalent setup slack take-up step before real load response is detected."
        )
        strain_setup_form.addRow("Slack step cap", self.spin_setup_slack_step_cap_stress_mpa)
        self.spin_setup_return_duration_s = CompactDoubleSpinBox(self.setup_details_panel)
        self.spin_setup_return_duration_s.setDecimals(2)
        self.spin_setup_return_duration_s.setRange(0.1, 3600.0)
        self.spin_setup_return_duration_s.setValue(SETUP_RETURN_DEFAULT_DURATION_S)
        self.spin_setup_return_duration_s.setSuffix(" s")
        self.spin_setup_return_duration_s.setToolTip(
            "Desired time for return-to-zero/start recovery; setup and recipe-finish recovery use this target."
        )
        self.spin_setup_preload_tolerance_mpa = CompactDoubleSpinBox(self.setup_details_panel)
        self.spin_setup_preload_tolerance_mpa.setDecimals(4)
        self.spin_setup_preload_tolerance_mpa.setRange(0.0001, 10000.0)
        self.spin_setup_preload_tolerance_mpa.setValue(0.25)
        self.spin_setup_preload_tolerance_mpa.setSuffix(" MPa")
        setup_tolerance_row, self.label_setup_preload_tolerance_equiv = self._spin_with_equivalent_label(
            self.setup_details_panel,
            self.spin_setup_preload_tolerance_mpa,
        )
        strain_setup_form.addRow("Setup preload tolerance", setup_tolerance_row)
        self._hide_form_row(strain_setup_form, setup_tolerance_row)
        self.spin_setup_zero_tolerance_g = CompactDoubleSpinBox(self.setup_details_panel)
        self.spin_setup_zero_tolerance_g.setDecimals(4)
        self.spin_setup_zero_tolerance_g.setRange(0.0001, 1000.0)
        self.spin_setup_zero_tolerance_g.setValue(SERVO_AUTO_TOLERANCE_LOAD_G)
        self.spin_setup_zero_tolerance_g.setSuffix(" g")
        setup_zero_tolerance_row, self.label_setup_zero_tolerance_equiv = self._spin_with_equivalent_label(
            self.setup_details_panel,
            self.spin_setup_zero_tolerance_g,
        )
        strain_setup_form.addRow("Setup zero-load tolerance", setup_zero_tolerance_row)
        self._hide_form_row(strain_setup_form, setup_zero_tolerance_row)
        self.spin_setup_zero_tolerance_g.hide()
        self.label_setup_zero_tolerance_equiv.hide()
        self.spin_setup_preload_stable_s = CompactDoubleSpinBox(self.setup_details_panel)
        self.spin_setup_preload_stable_s.setDecimals(2)
        self.spin_setup_preload_stable_s.setRange(0.0, 60.0)
        self.spin_setup_preload_stable_s.setValue(1.0)
        self.spin_setup_preload_stable_s.setSuffix(" s")
        self.spin_setup_preload_stable_s.setToolTip(
            "Time to hold the setup preload target before asking for the measured loaded length."
        )
        strain_setup_form.addRow("Preload settle time", self.spin_setup_preload_stable_s)
        self.spin_setup_zero_stable_s = CompactDoubleSpinBox(self.setup_details_panel)
        self.spin_setup_zero_stable_s.setDecimals(2)
        self.spin_setup_zero_stable_s.setRange(0.0, 60.0)
        self.spin_setup_zero_stable_s.setValue(1.0)
        self.spin_setup_zero_stable_s.setSuffix(" s")
        self.spin_setup_zero_stable_s.setToolTip(
            "Legacy setting retained for old recipe files; setup now applies l0 as soon as the zero-load return is accepted."
        )
        strain_setup_form.addRow("Zero-load settle time", self.spin_setup_zero_stable_s)
        self._hide_form_row(strain_setup_form, self.spin_setup_zero_stable_s)
        self.button_restore_setup_defaults = QtWidgets.QPushButton("Restore setup defaults", self.setup_details_panel)
        self.button_restore_setup_defaults.clicked.connect(self._restore_setup_defaults)
        strain_setup_form.addRow("", self.button_restore_setup_defaults)
        strain_setup_form.addRow("", self.check_pre_measurement_setup_enabled)
        automation_form.addRow("", self.strain_setup_box)

        self.recipe_stack = CurrentPageStackedWidget(automation_box)
        self.recipe_stack.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        ramp_page = QtWidgets.QWidget(self.recipe_stack)
        ramp_form = QtWidgets.QFormLayout(ramp_page)
        self.spin_ramp_distance = CompactDoubleSpinBox(automation_box)
        self.spin_ramp_distance.setDecimals(4)
        self.spin_ramp_distance.setRange(-50.0, 50.0)
        self.spin_ramp_distance.setValue(1.0)
        self.spin_ramp_distance.setSuffix(" mm")
        ramp_form.addRow("Total distance", self.spin_ramp_distance)

        self.spin_ramp_step = CompactDoubleSpinBox(automation_box)
        self.spin_ramp_step.setDecimals(4)
        self.spin_ramp_step.setRange(0.0001, 10.0)
        self.spin_ramp_step.setValue(0.1)
        self.spin_ramp_step.setSuffix(" mm")
        ramp_form.addRow("Step size", self.spin_ramp_step)

        self.spin_ramp_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_ramp_interval.setRange(100, 60000)
        self.spin_ramp_interval.setValue(1000)
        self.spin_ramp_interval.setSuffix(" ms")
        ramp_form.addRow("Settle interval", self.spin_ramp_interval)
        self.spin_ramp_interval.setVisible(False)
        ramp_interval_label = ramp_form.labelForField(self.spin_ramp_interval)
        if ramp_interval_label is not None:
            ramp_interval_label.setVisible(False)
        self.spin_ramp_speed_mm_s = CompactDoubleSpinBox(automation_box)
        self.spin_ramp_speed_mm_s.setDecimals(3)
        self.spin_ramp_speed_mm_s.setRange(0.001, 50.0)
        self.spin_ramp_speed_mm_s.setValue(1.0)
        self.spin_ramp_speed_mm_s.setSuffix(" mm/s")
        ramp_form.addRow("Ramp speed", self.spin_ramp_speed_mm_s)
        self.recipe_stack.addWidget(ramp_page)

        cycle_page = QtWidgets.QWidget(self.recipe_stack)
        cycle_form = QtWidgets.QFormLayout(cycle_page)
        self.spin_cycle_amplitude = CompactDoubleSpinBox(automation_box)
        self.spin_cycle_amplitude.setDecimals(4)
        self.spin_cycle_amplitude.setRange(-50.0, 50.0)
        self.spin_cycle_amplitude.setValue(1.0)
        self.spin_cycle_amplitude.setSuffix(" mm")
        cycle_form.addRow("Amplitude", self.spin_cycle_amplitude)
        self.spin_cycle_step = CompactDoubleSpinBox(automation_box)
        self.spin_cycle_step.setDecimals(4)
        self.spin_cycle_step.setRange(0.0001, 10.0)
        self.spin_cycle_step.setValue(0.1)
        self.spin_cycle_step.setSuffix(" mm")
        cycle_form.addRow("Step size", self.spin_cycle_step)
        self.spin_cycle_count = QtWidgets.QSpinBox(automation_box)
        self.spin_cycle_count.setRange(1, 1000)
        self.spin_cycle_count.setValue(3)
        cycle_form.addRow("Cycles", self.spin_cycle_count)
        self.spin_cycle_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_cycle_interval.setRange(100, 60000)
        self.spin_cycle_interval.setValue(1000)
        self.spin_cycle_interval.setSuffix(" ms")
        cycle_form.addRow("Settle interval", self.spin_cycle_interval)
        self.spin_cycle_interval.setVisible(False)
        cycle_interval_label = cycle_form.labelForField(self.spin_cycle_interval)
        if cycle_interval_label is not None:
            cycle_interval_label.setVisible(False)
        self.spin_cycle_speed_mm_s = CompactDoubleSpinBox(automation_box)
        self.spin_cycle_speed_mm_s.setDecimals(3)
        self.spin_cycle_speed_mm_s.setRange(0.001, 50.0)
        self.spin_cycle_speed_mm_s.setValue(1.0)
        self.spin_cycle_speed_mm_s.setSuffix(" mm/s")
        cycle_form.addRow("Move speed", self.spin_cycle_speed_mm_s)
        self.recipe_stack.addWidget(cycle_page)

        hold_page = QtWidgets.QWidget(self.recipe_stack)
        hold_form = QtWidgets.QFormLayout(hold_page)
        self.spin_hold_target = CompactDoubleSpinBox(automation_box)
        self.spin_hold_target.setDecimals(4)
        self.spin_hold_target.setRange(-50.0, 50.0)
        self.spin_hold_target.setValue(0.5)
        self.spin_hold_target.setSuffix(" mm")
        hold_form.addRow("Target offset", self.spin_hold_target)
        self.spin_hold_duration_s = CompactDoubleSpinBox(automation_box)
        self.spin_hold_duration_s.setDecimals(1)
        self.spin_hold_duration_s.setRange(0.1, 86400.0)
        self.spin_hold_duration_s.setValue(10.0)
        self.spin_hold_duration_s.setSuffix(" s")
        hold_form.addRow("Hold duration", self.spin_hold_duration_s)
        self.spin_hold_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_hold_interval.setRange(100, 60000)
        self.spin_hold_interval.setValue(1000)
        self.spin_hold_interval.setSuffix(" ms")
        hold_form.addRow("Record interval", self.spin_hold_interval)
        self.spin_hold_interval.setVisible(False)
        hold_interval_label = hold_form.labelForField(self.spin_hold_interval)
        if hold_interval_label is not None:
            hold_interval_label.setVisible(False)
        self.spin_hold_speed_mm_s = CompactDoubleSpinBox(automation_box)
        self.spin_hold_speed_mm_s.setDecimals(3)
        self.spin_hold_speed_mm_s.setRange(0.001, 50.0)
        self.spin_hold_speed_mm_s.setValue(1.0)
        self.spin_hold_speed_mm_s.setSuffix(" mm/s")
        hold_form.addRow("Move speed", self.spin_hold_speed_mm_s)
        self.recipe_stack.addWidget(hold_page)

        distribution_page = QtWidgets.QWidget(self.recipe_stack)
        distribution_form = QtWidgets.QFormLayout(distribution_page)
        self.combo_distribution_basis = QtWidgets.QComboBox(automation_box)
        for basis_key, label in HSW_BASIS_LABELS.items():
            self.combo_distribution_basis.addItem(label, basis_key)
        distribution_form.addRow("Control basis", self.combo_distribution_basis)
        self.spin_distribution_start = CompactDoubleSpinBox(automation_box)
        self.spin_distribution_start.setDecimals(3)
        self.spin_distribution_start.setRange(-100000.0, 100000.0)
        self.spin_distribution_start.setValue(10.0)
        distribution_start_row, self.label_distribution_start_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_distribution_start,
        )
        distribution_form.addRow("Start", distribution_start_row)
        self.spin_distribution_end = CompactDoubleSpinBox(automation_box)
        self.spin_distribution_end.setDecimals(3)
        self.spin_distribution_end.setRange(-100000.0, 100000.0)
        self.spin_distribution_end.setValue(100.0)
        distribution_end_row, self.label_distribution_end_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_distribution_end,
        )
        distribution_form.addRow("End", distribution_end_row)
        self.spin_distribution_step = CompactDoubleSpinBox(automation_box)
        self.spin_distribution_step.setDecimals(3)
        self.spin_distribution_step.setRange(0.001, 100000.0)
        self.spin_distribution_step.setValue(10.0)
        distribution_step_row, self.label_distribution_step_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_distribution_step,
        )
        distribution_form.addRow("Step", distribution_step_row)
        self.spin_distribution_tolerance = CompactDoubleSpinBox(automation_box)
        self.spin_distribution_tolerance.setDecimals(4)
        self.spin_distribution_tolerance.setRange(0.0001, 100000.0)
        self.spin_distribution_tolerance.setValue(0.5)
        distribution_tolerance_row, self.label_distribution_tolerance_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_distribution_tolerance,
        )
        distribution_form.addRow("Target tolerance", distribution_tolerance_row)
        self._hide_form_row(distribution_form, distribution_tolerance_row)
        self.spin_distribution_nudge_mm = CompactDoubleSpinBox(automation_box)
        self.spin_distribution_nudge_mm.setDecimals(4)
        self.spin_distribution_nudge_mm.setRange(0.0001, 10.0)
        self.spin_distribution_nudge_mm.setValue(0.01)
        self.spin_distribution_nudge_mm.setSuffix(" mm")
        self.spin_distribution_nudge_mm.setToolTip(
            "Linear stage correction step used while settling a load, stress, or strain target."
        )
        distribution_form.addRow("Seek correction step", self.spin_distribution_nudge_mm)
        self.spin_distribution_seek_speed_mm_s = CompactDoubleSpinBox(automation_box)
        self.spin_distribution_seek_speed_mm_s.setDecimals(3)
        self.spin_distribution_seek_speed_mm_s.setRange(0.001, 50.0)
        self.spin_distribution_seek_speed_mm_s.setValue(0.1)
        self.spin_distribution_seek_speed_mm_s.setSuffix(" mm/s")
        distribution_form.addRow("Balancing speed", self.spin_distribution_seek_speed_mm_s)
        self.spin_distribution_points = QtWidgets.QSpinBox(automation_box)
        self.spin_distribution_points.setRange(1, 1000000)
        self.spin_distribution_points.setValue(10000)
        distribution_form.addRow("Points per plateau", self.spin_distribution_points)
        self.spin_distribution_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_distribution_interval.setRange(10, 60000)
        self.spin_distribution_interval.setValue(100)
        self.spin_distribution_interval.setSuffix(" ms")
        distribution_form.addRow("Record interval", self.spin_distribution_interval)
        self.spin_distribution_interval.setVisible(False)
        distribution_interval_label = distribution_form.labelForField(self.spin_distribution_interval)
        if distribution_interval_label is not None:
            distribution_interval_label.setVisible(False)
        self.spin_distribution_settle_s = CompactDoubleSpinBox(automation_box)
        self.spin_distribution_settle_s.setDecimals(2)
        self.spin_distribution_settle_s.setRange(0.0, 3600.0)
        self.spin_distribution_settle_s.setValue(1.0)
        self.spin_distribution_settle_s.setSuffix(" s")
        distribution_form.addRow("Plateau settle", self.spin_distribution_settle_s)
        self.check_distribution_return_sweep = QtWidgets.QCheckBox(
            "Sweep back to the start target after the forward pass",
            automation_box,
        )
        self.check_distribution_return_sweep.setChecked(True)
        distribution_form.addRow("", self.check_distribution_return_sweep)
        distribution_hint = QtWidgets.QLabel(
            "Closed-loop in Mini DMA terms: the stage corrects until load, stress, or strain is within tolerance, "
            "then records the requested point count before moving to the next plateau.",
            distribution_page,
        )
        distribution_hint.setWordWrap(True)
        distribution_hint.setStyleSheet("color: palette(mid);")
        distribution_form.addRow("", distribution_hint)
        self.recipe_stack.addWidget(distribution_page)

        calibration_page = QtWidgets.QWidget(self.recipe_stack)
        calibration_form = QtWidgets.QFormLayout(calibration_page)
        self.spin_calibration_baseline_s = CompactDoubleSpinBox(automation_box)
        self.spin_calibration_baseline_s.setDecimals(1)
        self.spin_calibration_baseline_s.setRange(0.1, 3600.0)
        self.spin_calibration_baseline_s.setValue(3.0)
        self.spin_calibration_baseline_s.setSuffix(" s")
        calibration_form.addRow("Baseline noise", self.spin_calibration_baseline_s)
        self.spin_calibration_start_load_g = CompactDoubleSpinBox(automation_box)
        self.spin_calibration_start_load_g.setDecimals(3)
        self.spin_calibration_start_load_g.setRange(0.001, 150.0)
        self.spin_calibration_start_load_g.setValue(0.25)
        self.spin_calibration_start_load_g.setSuffix(" g")
        calibration_start_row, self.label_calibration_start_load_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_calibration_start_load_g,
        )
        calibration_form.addRow("Start preload", calibration_start_row)
        self.spin_calibration_end_load_g = CompactDoubleSpinBox(automation_box)
        self.spin_calibration_end_load_g.setDecimals(3)
        self.spin_calibration_end_load_g.setRange(0.001, 150.0)
        self.spin_calibration_end_load_g.setValue(1.0)
        self.spin_calibration_end_load_g.setSuffix(" g")
        calibration_end_row, self.label_calibration_end_load_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_calibration_end_load_g,
        )
        calibration_form.addRow("End preload", calibration_end_row)
        self.spin_calibration_load_step_g = CompactDoubleSpinBox(automation_box)
        self.spin_calibration_load_step_g.setDecimals(3)
        self.spin_calibration_load_step_g.setRange(0.001, 150.0)
        self.spin_calibration_load_step_g.setValue(0.25)
        self.spin_calibration_load_step_g.setSuffix(" g")
        calibration_step_row, self.label_calibration_load_step_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_calibration_load_step_g,
        )
        calibration_form.addRow("Preload step", calibration_step_row)
        self.spin_calibration_tolerance_g = CompactDoubleSpinBox(automation_box)
        self.spin_calibration_tolerance_g.setDecimals(4)
        self.spin_calibration_tolerance_g.setRange(0.0001, 10.0)
        self.spin_calibration_tolerance_g.setValue(SERVO_AUTO_TOLERANCE_LOAD_G)
        self.spin_calibration_tolerance_g.setSuffix(" g")
        calibration_tolerance_row, self.label_calibration_tolerance_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_calibration_tolerance_g,
        )
        calibration_form.addRow("Load tolerance", calibration_tolerance_row)
        self._hide_form_row(calibration_form, calibration_tolerance_row)
        self.spin_calibration_settle_s = CompactDoubleSpinBox(automation_box)
        self.spin_calibration_settle_s.setDecimals(2)
        self.spin_calibration_settle_s.setRange(0.0, 60.0)
        self.spin_calibration_settle_s.setValue(0.25)
        self.spin_calibration_settle_s.setSuffix(" s")
        calibration_form.addRow("Settle at preload", self.spin_calibration_settle_s)
        self.spin_calibration_preload_nudge_mm = CompactDoubleSpinBox(automation_box)
        self.spin_calibration_preload_nudge_mm.setDecimals(4)
        self.spin_calibration_preload_nudge_mm.setRange(0.0001, 10.0)
        self.spin_calibration_preload_nudge_mm.setValue(0.01)
        self.spin_calibration_preload_nudge_mm.setSuffix(" mm")
        calibration_form.addRow("Preload correction step", self.spin_calibration_preload_nudge_mm)
        self.spin_calibration_preload_speed_mm_s = CompactDoubleSpinBox(automation_box)
        self.spin_calibration_preload_speed_mm_s.setDecimals(3)
        self.spin_calibration_preload_speed_mm_s.setRange(0.001, 50.0)
        self.spin_calibration_preload_speed_mm_s.setValue(0.2)
        self.spin_calibration_preload_speed_mm_s.setSuffix(" mm/s")
        calibration_form.addRow("Preload seek speed", self.spin_calibration_preload_speed_mm_s)
        self.spin_calibration_move_step_mm = CompactDoubleSpinBox(automation_box)
        self.spin_calibration_move_step_mm.setDecimals(4)
        self.spin_calibration_move_step_mm.setRange(0.0001, 1.0)
        self.spin_calibration_move_step_mm.setValue(0.01)
        self.spin_calibration_move_step_mm.setSuffix(" mm")
        calibration_form.addRow("Micro-move step", self.spin_calibration_move_step_mm)
        self.spin_calibration_steps_per_direction = QtWidgets.QSpinBox(automation_box)
        self.spin_calibration_steps_per_direction.setRange(1, 1000)
        self.spin_calibration_steps_per_direction.setValue(5)
        calibration_form.addRow("Steps per direction", self.spin_calibration_steps_per_direction)
        self.spin_calibration_speed_mm_s = CompactDoubleSpinBox(automation_box)
        self.spin_calibration_speed_mm_s.setDecimals(3)
        self.spin_calibration_speed_mm_s.setRange(0.001, 50.0)
        self.spin_calibration_speed_mm_s.setValue(0.05)
        self.spin_calibration_speed_mm_s.setSuffix(" mm/s")
        calibration_form.addRow("Micro-move speed", self.spin_calibration_speed_mm_s)
        self.spin_calibration_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_calibration_interval.setRange(50, 60000)
        self.spin_calibration_interval.setValue(250)
        self.spin_calibration_interval.setSuffix(" ms")
        calibration_form.addRow("Control interval", self.spin_calibration_interval)
        self.spin_calibration_interval.setVisible(False)
        calibration_interval_label = calibration_form.labelForField(self.spin_calibration_interval)
        if calibration_interval_label is not None:
            calibration_interval_label.setVisible(False)
        self.recipe_stack.addWidget(calibration_page)

        current_sweep_page = QtWidgets.QWidget(self.recipe_stack)
        current_sweep_form = QtWidgets.QFormLayout(current_sweep_page)
        current_sweep_form.setProperty("_mini_dma_keep_rows_unwrapped", True)
        current_sweep_form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.DontWrapRows)
        self.combo_current_sweep_basis = QtWidgets.QComboBox(automation_box)
        for basis_key in (HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA, HSW_BASIS_STRAIN_PCT):
            self.combo_current_sweep_basis.addItem(HSW_BASIS_LABELS[basis_key], basis_key)
        current_sweep_form.addRow("Hold basis", self.combo_current_sweep_basis)
        basis_label = current_sweep_form.labelForField(self.combo_current_sweep_basis)
        if basis_label is not None:
            basis_label.setVisible(False)
        self.combo_current_sweep_basis.setVisible(False)

        self.label_current_sweep_first_overheating_section = QtWidgets.QLabel(
            "First overheating",
            automation_box,
        )
        first_overheating_font = self.label_current_sweep_first_overheating_section.font()
        first_overheating_font.setBold(True)
        self.label_current_sweep_first_overheating_section.setFont(first_overheating_font)
        current_sweep_form.addRow("", self.label_current_sweep_first_overheating_section)
        self.check_current_sweep_first_overheating = QtWidgets.QCheckBox(
            "Enable first-overheating sweep",
            automation_box,
        )
        self.check_current_sweep_first_overheating.setChecked(False)
        self.check_current_sweep_first_overheating.setToolTip(
            "Before the normal target sequence, run one current sweep at this fixed stress target. "
            "Use this when the first heating has a higher transformation temperature than later cycles."
        )
        current_sweep_form.addRow("", self.check_current_sweep_first_overheating)
        self.spin_current_sweep_first_overheating_target_mpa = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_first_overheating_target_mpa.setDecimals(3)
        self.spin_current_sweep_first_overheating_target_mpa.setRange(0.001, 100000.0)
        self.spin_current_sweep_first_overheating_target_mpa.setValue(20.0)
        self.spin_current_sweep_first_overheating_target_mpa.setSuffix(" MPa")
        self.spin_current_sweep_first_overheating_target_mpa.setToolTip(
            "Stress target used only for the optional first-overheating preheat sweep."
        )
        (
            current_preheat_row,
            self.label_current_first_overheating_target_equiv,
        ) = self._spin_with_equivalent_label(
            automation_box,
            self.spin_current_sweep_first_overheating_target_mpa,
        )
        current_sweep_form.addRow("First-overheating stress", current_preheat_row)

        self.label_current_sweep_targets_section = QtWidgets.QLabel("Targets", automation_box)
        targets_font = self.label_current_sweep_targets_section.font()
        targets_font.setBold(True)
        self.label_current_sweep_targets_section.setFont(targets_font)
        current_sweep_form.addRow("", self.label_current_sweep_targets_section)
        self.spin_current_sweep_target_start = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_target_start.setDecimals(3)
        self.spin_current_sweep_target_start.setRange(-100000.0, 100000.0)
        self.spin_current_sweep_target_start.setValue(0.0)
        current_start_row, self.label_current_target_start_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_current_sweep_target_start,
        )
        current_sweep_form.addRow("Start", current_start_row)
        self.spin_current_sweep_target_end = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_target_end.setDecimals(3)
        self.spin_current_sweep_target_end.setRange(-100000.0, 100000.0)
        self.spin_current_sweep_target_end.setValue(9.0)
        current_end_row, self.label_current_target_end_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_current_sweep_target_end,
        )
        current_sweep_form.addRow("End", current_end_row)
        self.spin_current_sweep_target_step = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_target_step.setDecimals(3)
        self.spin_current_sweep_target_step.setRange(0.001, 100000.0)
        self.spin_current_sweep_target_step.setValue(3.0)
        current_step_row, self.label_current_target_step_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_current_sweep_target_step,
        )
        current_sweep_form.addRow("Step", current_step_row)
        self.spin_current_sweep_target_ramp_rate = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_target_ramp_rate.setDecimals(4)
        self.spin_current_sweep_target_ramp_rate.setRange(0.0001, 100000.0)
        self.spin_current_sweep_target_ramp_rate.setValue(0.1)
        self.spin_current_sweep_target_ramp_rate.setToolTip(
            "Target loading rate. For iso-load this is g/s; for iso-stress it is MPa/s; "
            "for iso-strain it is %/s."
        )
        current_ramp_row, self.label_current_target_ramp_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_current_sweep_target_ramp_rate,
        )
        current_sweep_form.addRow("Ramp rate", current_ramp_row)
        self.button_current_sweep_advanced_controls = QtWidgets.QToolButton(automation_box)
        self.button_current_sweep_advanced_controls.setText("Advanced speeds/caps")
        self.button_current_sweep_advanced_controls.setToolTip(
            "Show or hide advanced current-sweep speed, correction-cap, and hold-band settings."
        )
        self.button_current_sweep_advanced_controls.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.button_current_sweep_advanced_controls.setCheckable(True)
        self.button_current_sweep_advanced_controls.setChecked(False)
        self.button_current_sweep_advanced_controls.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.button_current_sweep_advanced_controls.setMinimumWidth(240)
        self.button_current_sweep_advanced_controls.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        current_sweep_form.addRow(self.button_current_sweep_advanced_controls)
        self.current_sweep_advanced_panel = QtWidgets.QWidget(automation_box)
        current_sweep_advanced_form = QtWidgets.QFormLayout(self.current_sweep_advanced_panel)
        current_sweep_advanced_form.setContentsMargins(8, 2, 0, 2)
        current_sweep_advanced_form.setHorizontalSpacing(8)
        current_sweep_advanced_form.setVerticalSpacing(4)
        current_sweep_advanced_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        current_sweep_form.addRow(self.current_sweep_advanced_panel)
        self.spin_current_sweep_target_speed_mm_s = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_target_speed_mm_s.setDecimals(3)
        self.spin_current_sweep_target_speed_mm_s.setRange(0.001, 50.0)
        self.spin_current_sweep_target_speed_mm_s.setValue(SERVO_CURRENT_SWEEP_MAX_STAGE_SPEED_MM_S)
        self.spin_current_sweep_target_speed_mm_s.setSuffix(" mm/s")
        self.spin_current_sweep_target_speed_mm_s.setToolTip(
            "Absolute motor speed ceiling for target ramps and dynamic iso-load/iso-stress/iso-strain balancing."
        )
        current_sweep_advanced_form.addRow("Stage speed cap", self.spin_current_sweep_target_speed_mm_s)
        self.spin_current_sweep_max_correction_strain_pct = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_max_correction_strain_pct.setDecimals(3)
        self.spin_current_sweep_max_correction_strain_pct.setRange(0.001, 100.0)
        self.spin_current_sweep_max_correction_strain_pct.setValue(
            SERVO_CURRENT_SWEEP_MAX_CORRECTION_STRAIN_PCT
        )
        self.spin_current_sweep_max_correction_strain_pct.setSuffix(" %")
        self.spin_current_sweep_max_correction_strain_pct.setToolTip(
            "Maximum specimen-strain change allowed in one predictive servo correction."
        )
        current_sweep_advanced_form.addRow("Corr. strain", self.spin_current_sweep_max_correction_strain_pct)
        self.spin_current_sweep_correction_rate_pct_s = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_correction_rate_pct_s.setDecimals(3)
        self.spin_current_sweep_correction_rate_pct_s.setRange(0.001, 1000.0)
        self.spin_current_sweep_correction_rate_pct_s.setValue(
            SERVO_CURRENT_SWEEP_MAX_CORRECTION_RATE_PCT_S
        )
        self.spin_current_sweep_correction_rate_pct_s.setSuffix(" %/s")
        self.spin_current_sweep_correction_rate_pct_s.setToolTip(
            "Specimen-strain-rate ceiling for dynamic servo corrections; still limited by the stage speed cap."
        )
        current_sweep_advanced_form.addRow("Corr. rate", self.spin_current_sweep_correction_rate_pct_s)
        self.spin_current_sweep_max_correction_stress_mpa = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_max_correction_stress_mpa.setDecimals(2)
        self.spin_current_sweep_max_correction_stress_mpa.setRange(0.001, 100000.0)
        self.spin_current_sweep_max_correction_stress_mpa.setValue(SERVO_CURRENT_SWEEP_MAX_CORRECTION_STRESS_MPA)
        self.spin_current_sweep_max_correction_stress_mpa.setSuffix(" MPa")
        self.spin_current_sweep_max_correction_stress_mpa.setToolTip(
            "Absolute stress-equivalent safety rail for one current-sweep servo correction while current is moving."
        )
        current_sweep_advanced_form.addRow("Sweep cap", self.spin_current_sweep_max_correction_stress_mpa)
        self.spin_current_sweep_hold_correction_stress_mpa = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_hold_correction_stress_mpa.setDecimals(2)
        self.spin_current_sweep_hold_correction_stress_mpa.setRange(0.001, 100000.0)
        self.spin_current_sweep_hold_correction_stress_mpa.setValue(
            SERVO_CURRENT_SWEEP_HOLD_MAX_CORRECTION_STRESS_MPA
        )
        self.spin_current_sweep_hold_correction_stress_mpa.setSuffix(" MPa")
        self.spin_current_sweep_hold_correction_stress_mpa.setToolTip(
            "Absolute stress-equivalent safety rail for one servo correction while current is paused for target recovery."
        )
        current_sweep_advanced_form.addRow("Hold cap", self.spin_current_sweep_hold_correction_stress_mpa)
        self.spin_current_sweep_mid_correction_stress_mpa = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_mid_correction_stress_mpa.setDecimals(2)
        self.spin_current_sweep_mid_correction_stress_mpa.setRange(0.001, 100000.0)
        self.spin_current_sweep_mid_correction_stress_mpa.setValue(SERVO_CURRENT_SWEEP_MID_CORRECTION_STRESS_MPA)
        self.spin_current_sweep_mid_correction_stress_mpa.setSuffix(" MPa")
        self.spin_current_sweep_mid_correction_stress_mpa.setToolTip(
            "Legacy medium-error correction cap kept for older saved settings."
        )
        current_sweep_advanced_form.addRow("Mid correction cap", self.spin_current_sweep_mid_correction_stress_mpa)
        self._hide_form_row(current_sweep_advanced_form, self.spin_current_sweep_mid_correction_stress_mpa)
        self.spin_current_sweep_near_correction_stress_mpa = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_near_correction_stress_mpa.setDecimals(2)
        self.spin_current_sweep_near_correction_stress_mpa.setRange(0.001, 100000.0)
        self.spin_current_sweep_near_correction_stress_mpa.setValue(SERVO_CURRENT_SWEEP_NEAR_CORRECTION_STRESS_MPA)
        self.spin_current_sweep_near_correction_stress_mpa.setSuffix(" MPa")
        self.spin_current_sweep_near_correction_stress_mpa.setToolTip(
            "Stress-equivalent near-target band. Inside this band, the controller only sends one motor step."
        )
        current_sweep_advanced_form.addRow("Near band", self.spin_current_sweep_near_correction_stress_mpa)
        self.label_current_sweep_current_section = QtWidgets.QLabel("Current sweep", automation_box)
        current_section_font = self.label_current_sweep_current_section.font()
        current_section_font.setBold(True)
        self.label_current_sweep_current_section.setFont(current_section_font)
        current_sweep_form.addRow("", self.label_current_sweep_current_section)
        self.check_current_sweep_return_target = QtWidgets.QCheckBox("Return to start target at the end", automation_box)
        self.check_current_sweep_return_target.setChecked(
            bool(self.settings.value("current_sweep_return_target", True, type=bool))
        )
        self.check_current_sweep_return_target.setVisible(False)
        self.spin_current_sweep_start_mA = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_start_mA.setDecimals(2)
        self.spin_current_sweep_start_mA.setRange(0.0, 5000.0)
        self.spin_current_sweep_start_mA.setValue(1.0)
        self.spin_current_sweep_start_mA.setSuffix(" mA")
        current_start_mA_row, self.label_current_start_density = self._spin_with_equivalent_label(
            automation_box,
            self.spin_current_sweep_start_mA,
        )
        self.label_current_start_density.setTextFormat(QtCore.Qt.TextFormat.RichText)
        current_sweep_form.addRow("Start", current_start_mA_row)
        self.spin_current_sweep_end_mA = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_end_mA.setDecimals(2)
        self.spin_current_sweep_end_mA.setRange(0.0, 5000.0)
        self.spin_current_sweep_end_mA.setValue(3.0)
        self.spin_current_sweep_end_mA.setSuffix(" mA")
        current_end_mA_row, self.label_current_end_density = self._spin_with_equivalent_label(
            automation_box,
            self.spin_current_sweep_end_mA,
        )
        self.label_current_end_density.setTextFormat(QtCore.Qt.TextFormat.RichText)
        current_sweep_form.addRow("End", current_end_mA_row)
        self.spin_current_sweep_step_mA = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_step_mA.setDecimals(2)
        self.spin_current_sweep_step_mA.setRange(0.01, 5000.0)
        self.spin_current_sweep_step_mA.setValue(1.0)
        self.spin_current_sweep_step_mA.setSuffix(" mA/s")
        self.spin_current_sweep_step_mA.setToolTip(
            "Current ramp rate. Mini DMA converts this to smaller setpoint updates using the control interval."
        )
        current_sweep_form.addRow("Ramp rate", self.spin_current_sweep_step_mA)
        self.check_current_sweep_hold_on_error = QtWidgets.QCheckBox(
            "Pause while target recovers",
            automation_box,
        )
        self.check_current_sweep_hold_on_error.setToolTip(
            "Hold the current setpoint when absolute load/stress/strain error is too far from the requested target, "
            "while the displacement servo keeps correcting."
        )
        current_sweep_form.addRow("", self.check_current_sweep_hold_on_error)
        self.spin_current_sweep_hold_pause_factor = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_hold_pause_factor.setDecimals(2)
        self.spin_current_sweep_hold_pause_factor.setRange(1.0, 1000.0)
        self.spin_current_sweep_hold_pause_factor.setValue(CURRENT_SWEEP_HOLD_PAUSE_TOLERANCE_FACTOR)
        self.spin_current_sweep_hold_pause_factor.setSuffix(" x")
        self.spin_current_sweep_hold_pause_factor.setToolTip(
            "Pause the current ramp when target error exceeds this multiple of the hold tolerance."
        )
        current_sweep_advanced_form.addRow("Pause band", self.spin_current_sweep_hold_pause_factor)
        self.spin_current_sweep_hold_resume_factor = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_hold_resume_factor.setDecimals(2)
        self.spin_current_sweep_hold_resume_factor.setRange(0.1, 1000.0)
        self.spin_current_sweep_hold_resume_factor.setValue(CURRENT_SWEEP_HOLD_RESUME_TOLERANCE_FACTOR)
        self.spin_current_sweep_hold_resume_factor.setSuffix(" x")
        self.spin_current_sweep_hold_resume_factor.setToolTip(
            "Resume the current ramp once target error is inside this multiple of the hold tolerance."
        )
        current_sweep_advanced_form.addRow("Resume band", self.spin_current_sweep_hold_resume_factor)
        self.spin_current_sweep_hold_resume_stable_s = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_hold_resume_stable_s.setDecimals(2)
        self.spin_current_sweep_hold_resume_stable_s.setRange(0.0, 600.0)
        self.spin_current_sweep_hold_resume_stable_s.setValue(CURRENT_SWEEP_HOLD_RESUME_STABLE_S)
        self.spin_current_sweep_hold_resume_stable_s.setSuffix(" s")
        self.spin_current_sweep_hold_resume_stable_s.setToolTip(
            "Require the target error to stay inside the resume band for this long before current ramping resumes."
        )
        current_sweep_advanced_form.addRow("Resume time", self.spin_current_sweep_hold_resume_stable_s)
        self.spin_current_sweep_hold_filter_window_s = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_hold_filter_window_s.setDecimals(2)
        self.spin_current_sweep_hold_filter_window_s.setRange(0.1, 60.0)
        self.spin_current_sweep_hold_filter_window_s.setValue(SERVO_CURRENT_SWEEP_HOLD_FILTER_WINDOW_S)
        self.spin_current_sweep_hold_filter_window_s.setSuffix(" s")
        self.spin_current_sweep_hold_filter_window_s.setToolTip(
            "Scale averaging window used for current-hold pause/resume decisions."
        )
        current_sweep_advanced_form.addRow("Filter window", self.spin_current_sweep_hold_filter_window_s)
        self.spin_current_sweep_hold_noise_sigma = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_hold_noise_sigma.setDecimals(2)
        self.spin_current_sweep_hold_noise_sigma.setRange(0.0, 100.0)
        self.spin_current_sweep_hold_noise_sigma.setValue(SERVO_CURRENT_SWEEP_HOLD_NOISE_SIGMA)
        self.spin_current_sweep_hold_noise_sigma.setSuffix(" x")
        self.spin_current_sweep_hold_noise_sigma.setToolTip(
            "Recent scale-noise multiplier added to the current-hold pause/resume bands."
        )
        current_sweep_advanced_form.addRow("Noise band", self.spin_current_sweep_hold_noise_sigma)
        self.spin_current_sweep_hold_min_pause_stress_mpa = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_hold_min_pause_stress_mpa.setDecimals(2)
        self.spin_current_sweep_hold_min_pause_stress_mpa.setRange(0.0, 100000.0)
        self.spin_current_sweep_hold_min_pause_stress_mpa.setValue(SERVO_CURRENT_SWEEP_HOLD_MIN_PAUSE_STRESS_MPA)
        self.spin_current_sweep_hold_min_pause_stress_mpa.setSuffix(" MPa")
        self.spin_current_sweep_hold_min_pause_stress_mpa.setToolTip(
            "Minimum MPa-equivalent error required before current hold can start."
        )
        current_sweep_advanced_form.addRow("Min pause", self.spin_current_sweep_hold_min_pause_stress_mpa)
        self.spin_current_sweep_hold_min_resume_stress_mpa = CompactDoubleSpinBox(self.current_sweep_advanced_panel)
        self.spin_current_sweep_hold_min_resume_stress_mpa.setDecimals(2)
        self.spin_current_sweep_hold_min_resume_stress_mpa.setRange(0.0, 100000.0)
        self.spin_current_sweep_hold_min_resume_stress_mpa.setValue(SERVO_CURRENT_SWEEP_HOLD_MIN_RESUME_STRESS_MPA)
        self.spin_current_sweep_hold_min_resume_stress_mpa.setSuffix(" MPa")
        self.spin_current_sweep_hold_min_resume_stress_mpa.setToolTip(
            "Minimum MPa-equivalent band used before current hold can resume the current ramp."
        )
        current_sweep_advanced_form.addRow("Min resume", self.spin_current_sweep_hold_min_resume_stress_mpa)
        self.button_restore_current_sweep_advanced_defaults = QtWidgets.QPushButton(
            "Restore advanced defaults",
            self.current_sweep_advanced_panel,
        )
        self.button_restore_current_sweep_advanced_defaults.clicked.connect(
            self._restore_current_sweep_advanced_defaults
        )
        current_sweep_advanced_form.addRow("", self.button_restore_current_sweep_advanced_defaults)
        self._current_sweep_advanced_control_widgets = [
            self.spin_current_sweep_target_speed_mm_s,
            self.spin_current_sweep_max_correction_strain_pct,
            self.spin_current_sweep_correction_rate_pct_s,
            self.spin_current_sweep_max_correction_stress_mpa,
            self.spin_current_sweep_hold_correction_stress_mpa,
            self.spin_current_sweep_near_correction_stress_mpa,
            self.spin_current_sweep_hold_pause_factor,
            self.spin_current_sweep_hold_resume_factor,
            self.spin_current_sweep_hold_resume_stable_s,
            self.spin_current_sweep_hold_filter_window_s,
            self.spin_current_sweep_hold_noise_sigma,
            self.spin_current_sweep_hold_min_pause_stress_mpa,
            self.spin_current_sweep_hold_min_resume_stress_mpa,
            self.button_restore_current_sweep_advanced_defaults,
        ]

        def _toggle_current_sweep_advanced_controls(checked: bool) -> None:
            self.button_current_sweep_advanced_controls.setArrowType(
                QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
            )
            self.button_current_sweep_advanced_controls.setText(
                "Hide advanced speeds/caps" if checked else "Advanced speeds/caps"
            )
            for advanced_widget in self._current_sweep_advanced_control_widgets:
                self._set_form_row_visible(current_sweep_advanced_form, advanced_widget, checked)
            self.current_sweep_advanced_panel.setVisible(checked)
            if hasattr(self, "recipe_stack"):
                self.recipe_stack.setFixedHeight(self.recipe_stack.sizeHint().height())
            if hasattr(self, "_control_scroll_area") and self._control_scroll_area.widget() is not None:
                self._control_scroll_area.widget().adjustSize()

        self.button_current_sweep_advanced_controls.toggled.connect(
            _toggle_current_sweep_advanced_controls
        )
        _toggle_current_sweep_advanced_controls(False)
        self.check_current_sweep_reverse_current = QtWidgets.QCheckBox("Sweep current back to start at each target", automation_box)
        self.check_current_sweep_reverse_current.setChecked(True)
        self.check_current_sweep_reverse_current.setVisible(False)
        current_sweep_form.addRow("", self.check_current_sweep_reverse_current)
        self.spin_current_sweep_tolerance = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_tolerance.setDecimals(4)
        self.spin_current_sweep_tolerance.setRange(0.0001, 100000.0)
        self.spin_current_sweep_tolerance.setValue(0.25)
        current_tolerance_row, self.label_current_tolerance_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_current_sweep_tolerance,
        )
        current_sweep_form.addRow("Hold tolerance", current_tolerance_row)
        self._hide_form_row(current_sweep_form, current_tolerance_row)
        self.spin_current_sweep_nudge_mm = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_nudge_mm.setDecimals(4)
        self.spin_current_sweep_nudge_mm.setRange(0.0001, 10.0)
        self.spin_current_sweep_nudge_mm.setValue(0.1)
        self.spin_current_sweep_nudge_mm.setSuffix(" mm")
        self.spin_current_sweep_nudge_mm.setToolTip(
            "Legacy internal safety clamp retained for older settings. Dynamic balancing uses the target ramp stage speed."
        )
        current_sweep_form.addRow("Legacy correction step", self.spin_current_sweep_nudge_mm)
        current_nudge_label = current_sweep_form.labelForField(self.spin_current_sweep_nudge_mm)
        self.spin_current_sweep_nudge_mm.setVisible(False)
        if current_nudge_label is not None:
            current_nudge_label.setVisible(False)
        self.spin_current_sweep_balance_speed_mm_s = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_balance_speed_mm_s.setDecimals(3)
        self.spin_current_sweep_balance_speed_mm_s.setRange(0.001, 50.0)
        self.spin_current_sweep_balance_speed_mm_s.setValue(0.05)
        self.spin_current_sweep_balance_speed_mm_s.setSuffix(" mm/s")
        self.spin_current_sweep_balance_speed_mm_s.setToolTip(
            "Legacy hidden correction-speed setting. Dynamic balancing is capped by Target ramp stage speed."
        )
        current_sweep_form.addRow("Legacy correction speed", self.spin_current_sweep_balance_speed_mm_s)
        current_balance_label = current_sweep_form.labelForField(self.spin_current_sweep_balance_speed_mm_s)
        self.spin_current_sweep_balance_speed_mm_s.setVisible(False)
        if current_balance_label is not None:
            current_balance_label.setVisible(False)
        self.spin_current_sweep_max_seek_mm = CompactDoubleSpinBox(automation_box)
        self.spin_current_sweep_max_seek_mm.setDecimals(3)
        self.spin_current_sweep_max_seek_mm.setRange(0.01, 100.0)
        self.spin_current_sweep_max_seek_mm.setValue(3.0)
        self.spin_current_sweep_max_seek_mm.setSuffix(" mm")
        self.spin_current_sweep_max_seek_mm.setToolTip(
            "Maximum tensile-stage travel allowed while seeking one target before stopping as no-response."
        )
        self.spin_current_sweep_max_seek_mm.setVisible(False)
        current_max_seek_label = current_sweep_form.labelForField(self.spin_current_sweep_max_seek_mm)
        if current_max_seek_label is not None:
            current_max_seek_label.setVisible(False)
        self.spin_current_sweep_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_current_sweep_interval.setRange(50, 60000)
        self.spin_current_sweep_interval.setValue(250)
        self.spin_current_sweep_interval.setSuffix(" ms")
        current_sweep_form.addRow("Control interval", self.spin_current_sweep_interval)
        self.spin_current_sweep_interval.setVisible(False)
        current_interval_label = current_sweep_form.labelForField(self.spin_current_sweep_interval)
        if current_interval_label is not None:
            current_interval_label.setVisible(False)
        self.spin_current_sweep_log_interval = QtWidgets.QSpinBox(automation_box)
        self.spin_current_sweep_log_interval.setRange(50, 60000)
        self.spin_current_sweep_log_interval.setValue(500)
        self.spin_current_sweep_log_interval.setSuffix(" ms")
        current_sweep_form.addRow("Log interval", self.spin_current_sweep_log_interval)
        self.spin_current_sweep_log_interval.setVisible(False)
        current_log_interval_label = current_sweep_form.labelForField(self.spin_current_sweep_log_interval)
        if current_log_interval_label is not None:
            current_log_interval_label.setVisible(False)
        self.recipe_stack.addWidget(current_sweep_page)

        constant_current_page = QtWidgets.QWidget(self.recipe_stack)
        constant_current_form = QtWidgets.QFormLayout(constant_current_page)
        self.combo_constant_current_start_basis = QtWidgets.QComboBox(automation_box)
        for basis_key in (HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA, HSW_BASIS_STRAIN_PCT):
            self.combo_constant_current_start_basis.addItem(HSW_BASIS_LABELS[basis_key], basis_key)
        constant_current_form.addRow("Start target", self.combo_constant_current_start_basis)
        self.spin_constant_current_start_target = CompactDoubleSpinBox(automation_box)
        self.spin_constant_current_start_target.setDecimals(3)
        self.spin_constant_current_start_target.setRange(-100000.0, 100000.0)
        self.spin_constant_current_start_target.setValue(0.0)
        constant_current_start_row, self.label_constant_current_start_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_constant_current_start_target,
        )
        constant_current_form.addRow("Target start", constant_current_start_row)
        self.spin_constant_current_end_target = CompactDoubleSpinBox(automation_box)
        self.spin_constant_current_end_target.setDecimals(3)
        self.spin_constant_current_end_target.setRange(-100000.0, 100000.0)
        self.spin_constant_current_end_target.setValue(500.0)
        constant_current_end_row, self.label_constant_current_end_equiv = self._spin_with_equivalent_label(
            automation_box,
            self.spin_constant_current_end_target,
        )
        constant_current_form.addRow("Target end", constant_current_end_row)
        self.combo_constant_current_step_basis = QtWidgets.QComboBox(automation_box)
        self.combo_constant_current_step_basis.addItem("Displacement (mm)", MECHANICAL_STEP_DISPLACEMENT_MM)
        self.combo_constant_current_step_basis.addItem("Strain (%)", HSW_BASIS_STRAIN_PCT)
        constant_current_form.addRow("Step basis", self.combo_constant_current_step_basis)
        self.spin_constant_current_step_size = CompactDoubleSpinBox(automation_box)
        self.spin_constant_current_step_size.setDecimals(4)
        self.spin_constant_current_step_size.setRange(-100000.0, 100000.0)
        self.spin_constant_current_step_size.setValue(0.01)
        self.spin_constant_current_step_size.setSuffix(" mm")
        constant_current_form.addRow("Step size", self.spin_constant_current_step_size)
        self.spin_constant_current_hold_s = CompactDoubleSpinBox(automation_box)
        self.spin_constant_current_hold_s.setDecimals(2)
        self.spin_constant_current_hold_s.setRange(0.0, 3600.0)
        self.spin_constant_current_hold_s.setValue(1.0)
        self.spin_constant_current_hold_s.setSuffix(" s")
        constant_current_form.addRow("Hold per step", self.spin_constant_current_hold_s)
        self.spin_constant_current_move_speed_mm_s = CompactDoubleSpinBox(automation_box)
        self.spin_constant_current_move_speed_mm_s.setDecimals(3)
        self.spin_constant_current_move_speed_mm_s.setRange(0.001, 50.0)
        self.spin_constant_current_move_speed_mm_s.setValue(0.05)
        self.spin_constant_current_move_speed_mm_s.setSuffix(" mm/s")
        constant_current_form.addRow("Step speed", self.spin_constant_current_move_speed_mm_s)
        self.spin_constant_current_start_mA = CompactDoubleSpinBox(automation_box)
        self.spin_constant_current_start_mA.setDecimals(2)
        self.spin_constant_current_start_mA.setRange(0.0, 5000.0)
        self.spin_constant_current_start_mA.setValue(1.0)
        self.spin_constant_current_start_mA.setSuffix(" mA")
        constant_current_form.addRow("Current start", self.spin_constant_current_start_mA)
        self.spin_constant_current_end_mA = CompactDoubleSpinBox(automation_box)
        self.spin_constant_current_end_mA.setDecimals(2)
        self.spin_constant_current_end_mA.setRange(0.0, 5000.0)
        self.spin_constant_current_end_mA.setValue(80.0)
        self.spin_constant_current_end_mA.setSuffix(" mA")
        constant_current_form.addRow("Current end", self.spin_constant_current_end_mA)
        self.spin_constant_current_step_mA = CompactDoubleSpinBox(automation_box)
        self.spin_constant_current_step_mA.setDecimals(2)
        self.spin_constant_current_step_mA.setRange(0.01, 5000.0)
        self.spin_constant_current_step_mA.setValue(10.0)
        self.spin_constant_current_step_mA.setSuffix(" mA")
        constant_current_form.addRow("Current step", self.spin_constant_current_step_mA)
        self.check_constant_current_return_to_start = QtWidgets.QCheckBox(
            "Step back to the start target after each current",
            automation_box,
        )
        self.check_constant_current_return_to_start.setChecked(True)
        constant_current_form.addRow("", self.check_constant_current_return_to_start)
        self.recipe_stack.addWidget(constant_current_page)

        automation_form.addRow("", self.recipe_stack)
        self.check_return_to_origin = QtWidgets.QCheckBox(
            "Return to the recipe start position when finished",
            automation_box,
        )
        self.check_return_to_origin.setChecked(True)
        self.check_return_to_origin.setVisible(False)
        self.label_recipe_summary = QtWidgets.QLabel("")
        self.label_recipe_summary.setVisible(False)
        self.label_recipe_estimate = QtWidgets.QLabel("Estimated points: - | Estimated duration: -")
        self.label_recipe_estimate.setWordWrap(True)
        self.label_recipe_estimate.setVisible(False)
        self.recipe_progress = QtWidgets.QProgressBar(self.recipe_action_footer)
        self.recipe_progress.setRange(0, 100)
        self.recipe_progress.setValue(0)
        self.recipe_progress.setTextVisible(True)
        self.recipe_progress.setFormat("Recipe progress: idle")
        self.label_current_task = QtWidgets.QLabel("Current task: idle", self.recipe_action_footer)
        self.label_current_task.setWordWrap(True)
        task_font = self.label_current_task.font()
        task_font.setBold(True)
        self.label_current_task.setFont(task_font)
        self.label_current_task.setStyleSheet("color: palette(text);")
        self.label_current_task.setVisible(False)

        self.recipe_action_footer_layout.addWidget(self.recipe_progress)
        ramp_buttons = QtWidgets.QHBoxLayout()
        ramp_buttons.setSpacing(6)
        self.button_start_recipe = QtWidgets.QPushButton("Start recipe", self.recipe_action_footer)
        self.button_start_recipe.clicked.connect(self._start_auto_ramp)
        self.button_start_recipe.setMinimumWidth(110)
        ramp_buttons.addWidget(self.button_start_recipe, stretch=1)
        self.button_pause_recipe = QtWidgets.QPushButton("Pause", self.recipe_action_footer)
        self.button_pause_recipe.clicked.connect(self._toggle_recipe_pause)
        self.button_pause_recipe.setEnabled(False)
        self.button_pause_recipe.setMinimumWidth(82)
        ramp_buttons.addWidget(self.button_pause_recipe, stretch=1)
        self.button_stop_recipe = QtWidgets.QPushButton("Stop", self.recipe_action_footer)
        self.button_stop_recipe.clicked.connect(self._stop_recipe_from_button)
        self.button_stop_recipe.setMinimumWidth(82)
        ramp_buttons.addWidget(self.button_stop_recipe, stretch=1)
        self.recipe_action_footer_layout.addLayout(ramp_buttons)
        experiment_layout.addWidget(automation_box)

        manual_box = self._group_box("Manual Actions")
        manual_layout = QtWidgets.QVBoxLayout(manual_box)
        manual_hint = QtWidgets.QLabel(
            "Use manual controls for setup, preloading, or quick checks before launching a recipe."
        )
        manual_hint.setWordWrap(True)
        manual_layout.addWidget(manual_hint)
        manual_motion_row = QtWidgets.QVBoxLayout()
        manual_motion_row.setSpacing(6)
        self.button_manual_auto_connect = QtWidgets.QPushButton("Auto-connect hardware", manual_box)
        self.button_manual_auto_connect.setObjectName("manual_auto_connect_button")
        self.button_manual_auto_connect.setToolTip("Auto-detect/connect the motor and scale for manual setup.")
        self.button_manual_auto_connect.clicked.connect(self._auto_connect_manual_hardware)
        manual_motion_row.addWidget(self.button_manual_auto_connect)
        manual_up = QtWidgets.QPushButton("▲ Move up", manual_box)
        manual_up.setObjectName("manual_jog_tension_button")
        manual_up.setToolTip("Move the stage in the tension-increasing direction by the jog step.")
        manual_up.setMinimumHeight(42)
        self._configure_manual_jog_button(manual_up, lambda: self._tension_motion_sign())
        manual_motion_row.addWidget(manual_up)
        manual_down = QtWidgets.QPushButton("▼ Move down", manual_box)
        manual_down.setObjectName("manual_jog_relax_button")
        manual_down.setToolTip("Move the stage in the relaxing direction by the jog step.")
        manual_down.setMinimumHeight(42)
        self._configure_manual_jog_button(manual_down, lambda: -self._tension_motion_sign())
        manual_motion_row.addWidget(manual_down)
        recovery_buttons = QtWidgets.QHBoxLayout()
        manual_zero_displacement = QtWidgets.QPushButton("Move displacement to 0", manual_box)
        manual_zero_displacement.clicked.connect(self._start_recovery_displacement_zero)
        recovery_buttons.addWidget(manual_zero_displacement)
        manual_zero_load = QtWidgets.QPushButton("Move load to 0", manual_box)
        manual_zero_load.clicked.connect(self._start_recovery_load_zero)
        recovery_buttons.addWidget(manual_zero_load)
        manual_motion_row.addLayout(recovery_buttons)
        manual_halt = QtWidgets.QPushButton("Halt motor", manual_box)
        manual_halt.clicked.connect(self._halt_tic)
        manual_motion_row.addWidget(manual_halt)
        manual_layout.addLayout(manual_motion_row)
        manual_record = QtWidgets.QPushButton("Record point now", manual_box)
        manual_record.clicked.connect(self._record_current_point)
        manual_layout.addWidget(manual_record)
        manual_hardware_tare = QtWidgets.QPushButton("Capture zero-load", manual_box)
        manual_hardware_tare.setToolTip("Use the current real scale reading as the 0 g applied-load reference.")
        manual_hardware_tare.clicked.connect(self._capture_zero_load_scale_reference)
        manual_layout.addWidget(manual_hardware_tare)
        manual_refresh = QtWidgets.QPushButton("Refresh Tic status", manual_box)
        manual_refresh.clicked.connect(self._refresh_tic_status)
        manual_layout.addWidget(manual_refresh)
        self.button_manual_action_settings = QtWidgets.QToolButton(manual_box)
        self.button_manual_action_settings.setText("Manual action settings")
        self.button_manual_action_settings.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button_manual_action_settings.setCheckable(True)
        self.button_manual_action_settings.setChecked(False)
        self.button_manual_action_settings.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        manual_layout.addWidget(self.button_manual_action_settings)
        self.manual_action_settings_panel = QtWidgets.QWidget(manual_box)
        manual_form = QtWidgets.QFormLayout(self.manual_action_settings_panel)
        manual_form.setContentsMargins(0, 4, 0, 0)
        manual_form.addRow("Manual move speed", self.spin_motion_speed_mm_s)
        manual_form.addRow("Single-click step", self.spin_jog_mm)
        manual_form.addRow("Return-to-zero time", self.spin_setup_return_duration_s)
        self.button_restore_manual_action_defaults = QtWidgets.QPushButton(
            "Restore manual defaults",
            self.manual_action_settings_panel,
        )
        self.button_restore_manual_action_defaults.clicked.connect(self._restore_manual_action_defaults)
        manual_form.addRow("", self.button_restore_manual_action_defaults)
        manual_layout.addWidget(self.manual_action_settings_panel)
        self.manual_action_settings_panel.setVisible(False)
        self.button_manual_action_settings.toggled.connect(self._toggle_manual_action_settings)
        experiment_layout.addWidget(manual_box)
        experiment_layout.addStretch(1)
        tabs.addTab(experiment_tab, "Recipe")
        tabs.addTab(specimen_tab, "Sample")
        tabs.addTab(hardware_tab, "Hardware")
        tabs.setCurrentWidget(experiment_tab)

        controls.addStretch(1)
        splitter.addWidget(control_column)

        plot_panel = QtWidgets.QWidget(splitter)
        plot_layout = QtWidgets.QVBoxLayout(plot_panel)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(6)

        hero_box = QtWidgets.QFrame(plot_panel)
        self.dashboard_header = hero_box
        hero_box.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        hero_layout = QtWidgets.QHBoxLayout(hero_box)
        hero_layout.setContentsMargins(12, 10, 12, 10)
        hero_layout.setSpacing(18)
        hero_title = QtWidgets.QLabel("Mini DMA Dashboard", hero_box)
        hero_font = hero_title.font()
        hero_font.setPointSize(max(hero_font.pointSize(), 13))
        hero_font.setBold(True)
        hero_title.setFont(hero_font)
        hero_layout.addWidget(hero_title)
        self.button_emergency_stop = QtWidgets.QPushButton("EMERGENCY STOP", hero_box)
        self.button_emergency_stop.setObjectName("emergencyStopButton")
        self.button_emergency_stop.setMinimumHeight(42)
        self.button_emergency_stop.setMinimumWidth(160)
        self.button_emergency_stop.setToolTip(
            "Immediately stop the active recipe/session, halt the Tic motor, and turn the power-supply output off."
        )
        self.button_emergency_stop.setStyleSheet(
            "QPushButton#emergencyStopButton {"
            "background-color: #b91c1c;"
            "color: white;"
            "border: 2px solid #7f1d1d;"
            "border-radius: 8px;"
            "font-weight: 800;"
            "letter-spacing: 1px;"
            "padding: 8px 16px;"
            "}"
            "QPushButton#emergencyStopButton:hover { background-color: #dc2626; }"
            "QPushButton#emergencyStopButton:pressed { background-color: #7f1d1d; }"
        )
        self.button_emergency_stop.clicked.connect(self._emergency_stop)
        hero_layout.addWidget(self.button_emergency_stop)
        self.button_plot_setup = QtWidgets.QPushButton("Configure plots", hero_box)
        self.button_plot_setup.clicked.connect(self._show_plot_config_dialog)
        hero_layout.addWidget(self.button_plot_setup)

        self.dashboard_status_box = QtWidgets.QFrame(hero_box)
        status_layout = QtWidgets.QGridLayout(self.dashboard_status_box)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setHorizontalSpacing(6)
        status_layout.setVerticalSpacing(1)
        status_cells = (
            ("load_g", "Load", 86),
            ("stress_mpa", "Stress", 92),
            ("strain_pct", "Strain", 88),
            ("speed_mm_s", "Speed", 96),
            ("motor", "Motor", 108),
            ("supply", "Supply", 112),
        )
        for index, (key, title, min_width) in enumerate(status_cells):
            row = index // 3
            column = index % 3
            status_layout.addWidget(
                self._build_dashboard_value_cell(
                    self.dashboard_status_box,
                    key,
                    title,
                    min_width=min_width,
                ),
                row,
                column,
            )
            status_layout.setColumnStretch(column, 1)
        status_layout.addWidget(
            self._build_dashboard_value_cell(
                self.dashboard_status_box,
                "task",
                "Task",
                min_width=340,
                fixed_height=24,
            ),
            2,
            0,
            1,
            3,
        )
        status_layout.setRowMinimumHeight(2, 26)
        hero_layout.addWidget(self.dashboard_status_box, stretch=1)
        self.label_recipe_banner = QtWidgets.QLabel("Manual mode", hero_box)
        self.label_recipe_banner.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.label_recipe_banner.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.label_recipe_banner.setVisible(False)
        hero_layout.addWidget(self.label_recipe_banner)
        plot_layout.addWidget(hero_box, stretch=0)

        self.plot_config_dialog = PlotConfigDialog(self)
        plot_config_box = self._group_box("Plot Dashboard")
        plot_config_layout = QtWidgets.QGridLayout(plot_config_box)
        plot_config_layout.setContentsMargins(8, 8, 8, 8)
        plot_config_layout.setHorizontalSpacing(10)
        plot_config_layout.setVerticalSpacing(6)
        preset_row = QtWidgets.QHBoxLayout()
        dma_preset_button = QtWidgets.QPushButton("DMA preset", plot_config_box)
        dma_preset_button.clicked.connect(lambda: self._apply_plot_preset("dma"))
        preset_row.addWidget(dma_preset_button)
        heating_preset_button = QtWidgets.QPushButton("Heating preset", plot_config_box)
        heating_preset_button.clicked.connect(lambda: self._apply_plot_preset("heating"))
        preset_row.addWidget(heating_preset_button)
        mechanical_preset_button = QtWidgets.QPushButton("Mechanical preset", plot_config_box)
        mechanical_preset_button.clicked.connect(lambda: self._apply_plot_preset("mechanical"))
        preset_row.addWidget(mechanical_preset_button)
        preset_row.addStretch(1)
        plot_config_layout.addWidget(QtWidgets.QLabel("Presets", plot_config_box), 0, 0)
        plot_config_layout.addLayout(preset_row, 0, 1, 1, 5)

        header_labels = ("Tile", "Show", "Bottom X", "Left Y", "Right Y")
        for column, label in enumerate(header_labels):
            plot_config_layout.addWidget(QtWidgets.QLabel(label, plot_config_box), 1, column)

        self._plot_tiles = []
        for tile_index in range(4):
            visible = QtWidgets.QCheckBox(plot_config_box)
            visible.setChecked(True)
            x_combo = QtWidgets.QComboBox(plot_config_box)
            y_left_combo = QtWidgets.QComboBox(plot_config_box)
            y_right_combo = QtWidgets.QComboBox(plot_config_box)
            for combo in (x_combo, y_left_combo):
                for channel in self._plot_channels():
                    combo.addItem(channel.label, channel.key)
            y_right_combo.addItem("(none)", "")
            for channel in self._plot_channels():
                y_right_combo.addItem(channel.label, channel.key)
            for widget in (visible, x_combo, y_left_combo, y_right_combo):
                signal = (
                    widget.toggled
                    if isinstance(widget, QtWidgets.QCheckBox)
                    else widget.currentIndexChanged
                )
                signal.connect(self._handle_plot_config_changed)
            plot_config_layout.addWidget(QtWidgets.QLabel(f"Plot {tile_index + 1}", plot_config_box), tile_index + 2, 0)
            plot_config_layout.addWidget(visible, tile_index + 2, 1)
            plot_config_layout.addWidget(x_combo, tile_index + 2, 2)
            plot_config_layout.addWidget(y_left_combo, tile_index + 2, 3)
            plot_config_layout.addWidget(y_right_combo, tile_index + 2, 4)
            self._plot_tiles.append(
                PlotTileWidgets(
                    visible=visible,
                    x_combo=x_combo,
                    y_left_combo=y_left_combo,
                    y_right_combo=y_right_combo,
                )
            )
        self.plot_config_dialog.body_layout.addWidget(plot_config_box)

        plot_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical, plot_panel)
        plot_splitter.setChildrenCollapsible(False)
        self._dashboard_plot_splitter = plot_splitter
        plot_layout.addWidget(plot_splitter, stretch=1)

        plot_canvas_container = QtWidgets.QWidget(plot_splitter)
        self._dashboard_plot_canvas_container = plot_canvas_container
        plot_canvas_container.setMinimumHeight(0)
        plot_canvas_layout = QtWidgets.QVBoxLayout(plot_canvas_container)
        self._dashboard_plot_canvas_layout = plot_canvas_layout
        plot_canvas_layout.setContentsMargins(10, 10, 10, 16)
        plot_canvas_layout.setSpacing(10)
        self._dashboard_plot_grid = QtWidgets.QGridLayout()
        self._dashboard_plot_grid.setContentsMargins(0, 0, 0, 0)
        self._dashboard_plot_grid.setHorizontalSpacing(18)
        self._dashboard_plot_grid.setVerticalSpacing(18)
        plot_canvas_layout.addLayout(self._dashboard_plot_grid, stretch=1)
        self._dashboard_plot_bundles = []
        self._dashboard_plot_widgets = []
        self._dashboard_left_curves = []
        self._dashboard_right_curves = []
        if pg is not None:
            for plot_index in range(4):
                bundle = self._create_pyqtgraph_plot(
                    parent=plot_canvas_container,
                    title=f"Plot {plot_index + 1}",
                    x_label="Time (s)",
                    left_label="Applied tensile load (g)",
                    right_label="Right Y",
                )
                bundle.widget.setMinimumSize(320, 230)
                row, column = divmod(plot_index, 2)
                self._dashboard_plot_grid.addWidget(bundle.widget, row, column)
                self._dashboard_plot_grid.setRowStretch(row, 1)
                self._dashboard_plot_grid.setColumnStretch(column, 1)
                self._dashboard_plot_bundles.append(bundle)
                self._dashboard_plot_widgets.append(bundle.widget)
                self._dashboard_left_curves.append(bundle.left_curve)
                self._dashboard_right_curves.append(bundle.right_curve)
        else:
            plot_canvas_layout.addWidget(QtWidgets.QLabel("pyqtgraph is not available; live plots are disabled.", plot_canvas_container))

        log_container = QtWidgets.QWidget(plot_splitter)
        self._dashboard_log_container = log_container
        log_container.setMaximumHeight(118)
        log_layout = QtWidgets.QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(4)
        log_label = QtWidgets.QLabel("Run log", log_container)
        log_font = log_label.font()
        log_font.setBold(True)
        log_label.setFont(log_font)
        log_layout.addWidget(log_label)
        self.log_output = QtWidgets.QPlainTextEdit(log_container)
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(96)
        self.log_output.setMaximumBlockCount(1000)
        self.log_output.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log_output.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.log_output.setPlaceholderText("Mini DMA log output")
        log_layout.addWidget(self.log_output, stretch=1)
        self.statusBar().hide()
        plot_splitter.setStretchFactor(0, 8)
        plot_splitter.setStretchFactor(1, 1)
        plot_splitter.setSizes([960, 96])
        splitter.addWidget(plot_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 1380])

        for widget in (
            self.edit_name_composition,
            self.edit_name_wire,
            self.edit_name_specimen,
            self.edit_name_condition,
        ):
            widget.textChanged.connect(self._sync_auto_name_fields)
        self.edit_sample_name.textChanged.connect(self._refresh_recipe_sample_label)
        self.edit_sample_name.textChanged.connect(lambda *_args: self._persist_settings_if_enabled())
        self.edit_log_name.textChanged.connect(lambda *_args: self._persist_settings_if_enabled())
        self.edit_log_dir.textChanged.connect(lambda *_args: self._persist_settings_if_enabled())
        self.edit_project_path.editingFinished.connect(
            lambda: self._auto_import_builder_project_if_possible(update_identity=False, quiet=True)
        )
        self.edit_project_path.textChanged.connect(lambda *_args: self._persist_settings_if_enabled())
        self.spin_diameter.valueChanged.connect(self._refresh_recipe_sample_label)
        self.spin_diameter.valueChanged.connect(self._refresh_equivalent_labels)
        self.spin_diameter.valueChanged.connect(self._refresh_diameter_import_state)
        self.spin_diameter.valueChanged.connect(lambda *_args: self._persist_settings_if_enabled())
        for widget in (
            self.spin_control_interval,
            self.spin_log_interval,
            self.spin_ramp_distance,
            self.spin_ramp_speed_mm_s,
            self.spin_cycle_amplitude,
            self.spin_cycle_count,
            self.spin_cycle_speed_mm_s,
            self.spin_hold_target,
            self.spin_hold_duration_s,
            self.spin_hold_speed_mm_s,
            self.spin_distribution_start,
            self.spin_distribution_end,
            self.spin_distribution_step,
            self.spin_distribution_tolerance,
            self.spin_distribution_nudge_mm,
            self.spin_distribution_seek_speed_mm_s,
            self.spin_distribution_points,
            self.spin_distribution_settle_s,
            self.spin_calibration_start_load_g,
            self.spin_calibration_end_load_g,
            self.spin_calibration_load_step_g,
            self.spin_calibration_tolerance_g,
        ):
            widget.valueChanged.connect(self._update_recipe_mode_ui)
        for widget in (
            self.spin_ramp_step,
            self.spin_ramp_interval,
            self.spin_cycle_step,
            self.spin_cycle_interval,
            self.spin_hold_interval,
            self.spin_distribution_interval,
            self.spin_setup_preload_stress_mpa,
            self.spin_setup_preload_duration_s,
            self.spin_setup_return_duration_s,
            self.spin_setup_slack_speed_strain_pct_s,
            self.spin_setup_slack_step_cap_stress_mpa,
            self.spin_setup_preload_tolerance_mpa,
            self.spin_setup_zero_tolerance_g,
            self.spin_setup_preload_stable_s,
            self.spin_setup_zero_stable_s,
            self.spin_current_sweep_target_start,
            self.spin_current_sweep_target_end,
            self.spin_current_sweep_target_step,
            self.spin_current_sweep_target_ramp_rate,
            self.spin_current_sweep_target_speed_mm_s,
            self.spin_current_sweep_max_correction_strain_pct,
            self.spin_current_sweep_correction_rate_pct_s,
            self.spin_current_sweep_max_correction_stress_mpa,
            self.spin_current_sweep_hold_correction_stress_mpa,
            self.spin_current_sweep_mid_correction_stress_mpa,
            self.spin_current_sweep_near_correction_stress_mpa,
            self.spin_current_sweep_start_mA,
            self.spin_current_sweep_end_mA,
            self.spin_current_sweep_step_mA,
            self.spin_current_sweep_hold_pause_factor,
            self.spin_current_sweep_hold_resume_factor,
            self.spin_current_sweep_hold_resume_stable_s,
            self.spin_current_sweep_hold_filter_window_s,
            self.spin_current_sweep_hold_noise_sigma,
            self.spin_current_sweep_hold_min_pause_stress_mpa,
            self.spin_current_sweep_hold_min_resume_stress_mpa,
            self.spin_current_sweep_tolerance,
            self.spin_current_sweep_nudge_mm,
            self.spin_current_sweep_balance_speed_mm_s,
            self.spin_current_sweep_max_seek_mm,
            self.spin_current_sweep_first_overheating_target_mpa,
            self.spin_current_sweep_interval,
            self.spin_current_sweep_log_interval,
            self.spin_constant_current_start_target,
            self.spin_constant_current_end_target,
            self.spin_constant_current_step_size,
            self.spin_constant_current_hold_s,
            self.spin_constant_current_move_speed_mm_s,
            self.spin_constant_current_start_mA,
            self.spin_constant_current_end_mA,
            self.spin_constant_current_step_mA,
        ):
            widget.valueChanged.connect(self._update_recipe_mode_ui)
        self.spin_ui_interval.valueChanged.connect(self._apply_ui_refresh_interval)
        self.spin_graph_interval.valueChanged.connect(self._apply_ui_refresh_interval)
        self.spin_tic_status_interval.valueChanged.connect(self._apply_hardware_timer_intervals)
        self.spin_tic_keepalive_interval.valueChanged.connect(self._apply_hardware_timer_intervals)
        self.spin_full_steps_per_mm.valueChanged.connect(self._sync_tic_units_per_mm_from_full_steps)
        self.check_return_to_origin.toggled.connect(self._update_recipe_mode_ui)
        self.check_distribution_return_sweep.toggled.connect(self._update_recipe_mode_ui)
        self.check_pre_measurement_setup_enabled.toggled.connect(self._update_recipe_mode_ui)
        self.check_current_sweep_return_target.toggled.connect(self._update_recipe_mode_ui)
        self.check_current_sweep_hold_on_error.toggled.connect(self._update_recipe_mode_ui)
        self.check_current_sweep_first_overheating.toggled.connect(self._update_recipe_mode_ui)
        self.check_current_sweep_reverse_current.toggled.connect(self._update_recipe_mode_ui)
        self.check_zero_on_preload.toggled.connect(self._refresh_live_labels)
        self.spin_preload_threshold_g.valueChanged.connect(self._refresh_live_labels)
        self.combo_distribution_basis.currentIndexChanged.connect(self._update_distribution_basis_ui)
        self.combo_distribution_basis.currentIndexChanged.connect(self._update_recipe_mode_ui)
        self.combo_current_sweep_basis.currentIndexChanged.connect(self._update_current_sweep_basis_ui)
        self.combo_current_sweep_basis.currentIndexChanged.connect(self._update_recipe_mode_ui)
        self.combo_constant_current_start_basis.currentIndexChanged.connect(self._update_constant_current_basis_ui)
        self.combo_constant_current_start_basis.currentIndexChanged.connect(self._update_recipe_mode_ui)
        self.combo_constant_current_step_basis.currentIndexChanged.connect(self._update_constant_current_basis_ui)
        self.combo_constant_current_step_basis.currentIndexChanged.connect(self._update_recipe_mode_ui)
        self.check_constant_current_return_to_start.toggled.connect(self._update_recipe_mode_ui)
        self.spin_steps_per_mm.valueChanged.connect(self._clamp_motion_resolution_controls)
        self.spin_steps_per_mm.valueChanged.connect(self._update_recipe_mode_ui)

        self.statusBar().showMessage("Ready")
        self._refresh_supply_ports()
        self._apply_supply_profile_defaults()
        self._update_distribution_basis_ui()
        self._update_current_sweep_basis_ui()
        self._update_constant_current_basis_ui()
        self._clamp_motion_resolution_controls()
        self._apply_plot_preset("dma")
        self._update_recipe_mode_ui()
        self._refresh_recipe_sample_label()
        self._update_recipe_buttons()
        self._refresh_plots()
        self._make_settings_panel_width_friendly()
        self._install_settings_wheel_guard()

    def _group_box(self, title: str) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox(title, self)
        box.setMinimumWidth(0)
        box.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        return box

    def _make_settings_panel_width_friendly(self) -> None:
        root = self._control_scroll_area.widget() if self._control_scroll_area is not None else None
        if root is None:
            return
        self._control_scroll_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._control_scroll_area.horizontalScrollBar().setFixedHeight(0)
        root.setMinimumWidth(0)
        root.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self._make_layout_width_friendly(root.layout())
        for widget in root.findChildren(QtWidgets.QWidget):
            if not isinstance(
                widget,
                (
                    QtWidgets.QAbstractSpinBox,
                    QtWidgets.QPushButton,
                    QtWidgets.QToolButton,
                ),
            ):
                widget.setMinimumWidth(0)
            policy = widget.sizePolicy()
            if isinstance(widget, QtWidgets.QLabel):
                widget.setWordWrap(True)
            if isinstance(widget, QtWidgets.QAbstractSpinBox):
                widget.setMinimumWidth(max(widget.minimumWidth(), 130))
                widget.lineEdit().setMinimumWidth(96)
            if isinstance(widget, QtWidgets.QToolButton):
                widget.setMinimumWidth(max(widget.minimumWidth(), 220))
                widget.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Expanding,
                    policy.verticalPolicy(),
                )
            if isinstance(widget, QtWidgets.QComboBox):
                widget.setMinimumContentsLength(0)
                widget.setSizeAdjustPolicy(
                    QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
                )
            if isinstance(
                widget,
                (
                    QtWidgets.QAbstractSpinBox,
                    QtWidgets.QComboBox,
                    QtWidgets.QLineEdit,
                    QtWidgets.QPlainTextEdit,
                    QtWidgets.QTextEdit,
                ),
            ):
                widget.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Expanding,
                    policy.verticalPolicy(),
                )
            if isinstance(widget, QtWidgets.QAbstractScrollArea):
                widget.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _make_layout_width_friendly(self, layout: QtWidgets.QLayout | None) -> None:
        if layout is None:
            return
        if isinstance(layout, QtWidgets.QFormLayout):
            layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            if bool(layout.property("_mini_dma_keep_rows_unwrapped")):
                layout.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.DontWrapRows)
            else:
                layout.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item is None:
                continue
            self._make_layout_width_friendly(item.layout())
            widget = item.widget()
            if widget is not None:
                self._make_layout_width_friendly(widget.layout())

    def _install_settings_wheel_guard(self) -> None:
        control_root = self._control_scroll_area.widget() if self._control_scroll_area is not None else None
        if control_root is None:
            return
        for widget in control_root.findChildren((QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)):
            widget.setProperty("_mini_dma_wheel_guard", True)
            widget.installEventFilter(self)
            if isinstance(widget, QtWidgets.QAbstractSpinBox):
                editor = widget.lineEdit()
                editor.setProperty("_mini_dma_wheel_guard", True)
                editor.installEventFilter(self)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if (
            event.type() == QtCore.QEvent.Type.Wheel
            and isinstance(watched, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox, QtWidgets.QLineEdit))
            and watched.property("_mini_dma_wheel_guard")
        ):
            if isinstance(watched, QtWidgets.QComboBox) and watched.view().isVisible():
                return super().eventFilter(watched, event)
            self._scroll_control_panel_from_wheel(event)
            return True
        return super().eventFilter(watched, event)

    def _scroll_control_panel_from_wheel(self, event: QtCore.QEvent) -> None:
        if not isinstance(event, QtGui.QWheelEvent):
            event.ignore()
            return
        scroll_area = self._control_scroll_area
        if scroll_area is None:
            event.ignore()
            return
        scrollbar = scroll_area.verticalScrollBar()
        delta = event.pixelDelta().y()
        if delta == 0:
            delta = int(event.angleDelta().y() / 120 * scrollbar.singleStep() * 3)
        if delta != 0:
            scrollbar.setValue(scrollbar.value() - delta)
        event.accept()

    def _build_status_card(
        self,
        title: str,
        value: str,
        detail: str,
        accent_color: str,
    ) -> tuple[QtWidgets.QFrame, QtWidgets.QLabel]:
        card = QtWidgets.QFrame(self)
        card.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        card.setStyleSheet(
            "QFrame { border: 1px solid palette(mid); border-radius: 8px; }"
            f"QLabel#statusValue {{ color: {accent_color}; font-weight: 700; }}"
        )
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        title_label = QtWidgets.QLabel(title, card)
        value_label = QtWidgets.QLabel(value, card)
        value_label.setObjectName("statusValue")
        detail_label = QtWidgets.QLabel(detail, card)
        detail_label.setWordWrap(True)
        detail_label.setStyleSheet("color: palette(mid);")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(detail_label)
        return card, value_label

    def _plot_channels(self) -> list[PlotChannel]:
        return [
            PlotChannel("elapsed_s", "Time (s)", "#ef4444", lambda point: point.elapsed_s),
            PlotChannel("raw_position_mm", "Raw Tic position (mm)", "#93c5fd", lambda point: point.raw_position_mm),
            PlotChannel("position_mm", "Tensile displacement (mm)", "#60a5fa", lambda point: point.position_mm),
            PlotChannel("raw_load_g", "Raw scale signed (g)", "#f59e0b", lambda point: point.raw_load_g),
            PlotChannel("load_g", "Applied tensile load (g)", "#fbbf24", lambda point: point.load_g),
            PlotChannel(
                "strain_pct",
                "Strain (%)",
                "#22c55e",
                lambda point: point.strain_pct,
            ),
            PlotChannel(
                "stress_mpa",
                "Stress (MPa)",
                "#a78bfa",
                lambda point: point.stress_mpa,
            ),
            PlotChannel(
                "current_set_mA",
                "Set current (mA)",
                "#f97316",
                lambda point: self._plot_nonzero_current_mA(point.current_set_mA),
            ),
            PlotChannel(
                "current_measured_mA",
                "Measured current (mA)",
                "#fb7185",
                lambda point: self._plot_nonzero_current_mA(point.current_measured_mA),
            ),
            PlotChannel("voltage_V", "Voltage (V)", "#facc15", lambda point: point.voltage_V),
            PlotChannel(
                "resistance_ohm",
                "Resistance (Ohm)",
                "#14b8a6",
                self._plot_resistance_ohm,
            ),
            PlotChannel("power_W", "Power (W)", "#c084fc", lambda point: point.power_W),
        ]

    def _plot_channel_color(self, key: str, fallback: str = "#38bdf8") -> str:
        channel = self._plot_channel(key)
        return fallback if channel is None else channel.color

    def _plot_nonzero_current_mA(self, value_mA: float | None) -> float | None:
        if value_mA is None:
            return None
        value = float(value_mA)
        if abs(value) < MIN_RESISTANCE_CURRENT_MA:
            return None
        return value

    def _plot_resistance_ohm(self, point: MeasurementPoint) -> float | None:
        if point.resistance_ohm is None:
            return None
        if (
            self._plot_nonzero_current_mA(point.current_set_mA) is None
            or self._plot_nonzero_current_mA(point.current_measured_mA) is None
        ):
            return None
        return point.resistance_ohm

    def _plot_channel(self, key: str) -> PlotChannel | None:
        for channel in self._plot_channels():
            if channel.key == key:
                return channel
        return None

    def _compact_plot_label(self, label: str) -> str:
        compact = re.sub(r"\s*\([^)]*\)", "", label).strip()
        compact = compact.replace("Effective load", "Load")
        compact = compact.replace("Measured current", "Current")
        compact = compact.replace("Displacement", "Disp.")
        compact = compact.replace("Resistance", "Res.")
        return compact

    def _plot_title(
        self,
        x_channel: PlotChannel,
        y_left_channel: PlotChannel,
        y_right_channel: PlotChannel | None,
    ) -> str:
        x_label = self._compact_plot_label(x_channel.label)
        left_label = self._compact_plot_label(y_left_channel.label)
        if y_right_channel is None:
            return f"{left_label} vs {x_label}"
        right_label = self._compact_plot_label(y_right_channel.label)
        return f"{left_label} + {right_label} vs {x_label}"

    def _persist_settings_if_enabled(self) -> None:
        if (
            self._persist_settings
            and self._settings_persistence_ready
            and not self._settings_restore_in_progress
        ):
            self._save_settings()

    def _handle_plot_config_changed(self, *_args: object) -> None:
        if not self._plot_settings_restore_in_progress:
            self._store_dashboard_plot_settings()
        self._refresh_plots()
        self._persist_settings_if_enabled()

    def _dashboard_plot_settings_mode_key(self, mode: str | None = None) -> str:
        mode_text = str(mode if mode is not None else self.combo_recipe_mode.currentData() or "ramp")
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", mode_text).strip("_")
        return safe or "recipe"

    def _dashboard_plot_settings_prefix(self, index: int, mode: str | None = None) -> str:
        if mode is None:
            return f"plot_tile_{index}"
        return f"plot_tile_recipe_{self._dashboard_plot_settings_mode_key(mode)}_{index}"

    def _default_dashboard_plot_settings(self, _index: int) -> dict[str, object]:
        return {
            "visible": True,
            "x": "elapsed_s",
            "y_left": "load_g",
            "y_right": "",
        }

    def _capture_dashboard_plot_settings(self) -> list[dict[str, object]]:
        settings: list[dict[str, object]] = []
        for index, tile in enumerate(self._plot_tiles):
            fallback = self._default_dashboard_plot_settings(index)
            settings.append(
                {
                    "visible": tile.visible.isChecked(),
                    "x": tile.x_combo.currentData() or fallback["x"],
                    "y_left": tile.y_left_combo.currentData() or fallback["y_left"],
                    "y_right": tile.y_right_combo.currentData() or fallback["y_right"],
                }
            )
        return settings

    def _settings_have_dashboard_plot_values(self, mode: str | None = None) -> bool:
        if mode is not None and self._dashboard_plot_settings_mode_key(mode) in self._dashboard_plot_settings_by_mode:
            return True
        if not self._plot_tiles:
            return False
        prefix = self._dashboard_plot_settings_prefix(0, mode)
        return self.settings.contains(f"{prefix}_x") or self.settings.contains(f"{prefix}_y_left")

    def _read_dashboard_plot_settings(self, mode: str | None = None) -> list[dict[str, object]]:
        if mode is not None:
            mode_key = self._dashboard_plot_settings_mode_key(mode)
            cached = self._dashboard_plot_settings_by_mode.get(mode_key)
            if cached is not None:
                return [dict(values) for values in cached]
            if self._settings_have_dashboard_plot_values(mode):
                settings = [
                    self._read_dashboard_plot_tile_settings(index, mode)
                    for index in range(len(self._plot_tiles))
                ]
                self._dashboard_plot_settings_by_mode[mode_key] = [dict(values) for values in settings]
                return settings
        if self._settings_have_dashboard_plot_values(None):
            return [
                self._read_dashboard_plot_tile_settings(index, None)
                for index in range(len(self._plot_tiles))
            ]
        return [self._default_dashboard_plot_settings(index) for index in range(len(self._plot_tiles))]

    def _read_dashboard_plot_tile_settings(self, index: int, mode: str | None = None) -> dict[str, object]:
        defaults = self._default_dashboard_plot_settings(index)
        prefix = self._dashboard_plot_settings_prefix(index, mode)
        return {
            "visible": bool(self.settings.value(f"{prefix}_visible", defaults["visible"], type=bool)),
            "x": self.settings.value(f"{prefix}_x", defaults["x"], type=str),
            "y_left": self.settings.value(f"{prefix}_y_left", defaults["y_left"], type=str),
            "y_right": self.settings.value(f"{prefix}_y_right", defaults["y_right"], type=str),
        }

    def _write_dashboard_plot_settings(
        self,
        values_by_tile: Sequence[Mapping[str, object]],
        mode: str | None = None,
    ) -> None:
        for index, values in enumerate(values_by_tile):
            prefix = self._dashboard_plot_settings_prefix(index, mode)
            defaults = self._default_dashboard_plot_settings(index)
            self.settings.setValue(f"{prefix}_visible", bool(values.get("visible", defaults["visible"])))
            self.settings.setValue(f"{prefix}_x", str(values.get("x", defaults["x"])))
            self.settings.setValue(f"{prefix}_y_left", str(values.get("y_left", defaults["y_left"])))
            self.settings.setValue(f"{prefix}_y_right", str(values.get("y_right", defaults["y_right"])))

    def _store_default_dashboard_plot_settings_if_missing(self) -> None:
        if self._settings_have_dashboard_plot_values(None):
            return
        defaults = [self._default_dashboard_plot_settings(index) for index in range(len(self._plot_tiles))]
        self._write_dashboard_plot_settings(defaults, None)

    def _store_dashboard_plot_settings(
        self,
        mode: str | None = None,
        *,
        write_settings: bool = False,
    ) -> None:
        if not self._plot_tiles:
            return
        mode_text = str(mode if mode is not None else self.combo_recipe_mode.currentData() or "ramp")
        values = self._capture_dashboard_plot_settings()
        self._dashboard_plot_settings_by_mode[self._dashboard_plot_settings_mode_key(mode_text)] = [
            dict(tile_values) for tile_values in values
        ]
        if write_settings:
            self._write_dashboard_plot_settings(values, mode_text)

    def _apply_dashboard_plot_settings(self, mode: str | None = None) -> None:
        if not self._plot_tiles:
            return
        mode_text = str(mode if mode is not None else self.combo_recipe_mode.currentData() or "ramp")
        settings = self._read_dashboard_plot_settings(mode_text)
        self._plot_settings_restore_in_progress = True
        try:
            for index, tile in enumerate(self._plot_tiles):
                values = settings[index] if index < len(settings) else self._default_dashboard_plot_settings(index)
                blockers = [
                    QtCore.QSignalBlocker(tile.visible),
                    QtCore.QSignalBlocker(tile.x_combo),
                    QtCore.QSignalBlocker(tile.y_left_combo),
                    QtCore.QSignalBlocker(tile.y_right_combo),
                ]
                try:
                    tile.visible.setChecked(bool(values.get("visible", True)))
                    for combo, key_name in (
                        (tile.x_combo, "x"),
                        (tile.y_left_combo, "y_left"),
                        (tile.y_right_combo, "y_right"),
                    ):
                        combo_index = combo.findData(str(values.get(key_name, "")))
                        if combo_index >= 0:
                            combo.setCurrentIndex(combo_index)
                finally:
                    del blockers
        finally:
            self._plot_settings_restore_in_progress = False
        self._refresh_plots()

    def _apply_plot_preset(self, preset: str) -> None:
        presets = {
            "dma": [
                ("elapsed_s", "load_g", ""),
                ("elapsed_s", "position_mm", ""),
                ("elapsed_s", "current_measured_mA", ""),
                ("elapsed_s", "resistance_ohm", ""),
            ],
            "heating": [
                ("elapsed_s", "current_measured_mA", "voltage_V"),
                ("elapsed_s", "resistance_ohm", "power_W"),
                ("elapsed_s", "load_g", "position_mm"),
                ("strain_pct", "stress_mpa", "current_measured_mA"),
            ],
            "mechanical": [
                ("position_mm", "load_g", ""),
                ("strain_pct", "stress_mpa", ""),
                ("elapsed_s", "load_g", ""),
                ("elapsed_s", "position_mm", "strain_pct"),
            ],
        }
        config = presets.get(preset, presets["dma"])
        for index, tile in enumerate(self._plot_tiles):
            x_key, y_left, y_right = config[index]
            tile.visible.setChecked(True)
            x_index = tile.x_combo.findData(x_key)
            if x_index >= 0:
                tile.x_combo.setCurrentIndex(x_index)
            y_left_index = tile.y_left_combo.findData(y_left)
            if y_left_index >= 0:
                tile.y_left_combo.setCurrentIndex(y_left_index)
            y_right_index = tile.y_right_combo.findData(y_right)
            if y_right_index >= 0:
                tile.y_right_combo.setCurrentIndex(y_right_index)
        self._refresh_plots()
        self._persist_settings_if_enabled()

    def _plot_theme(self) -> dict[str, Any]:
        palette = self.palette()
        app = QtWidgets.QApplication.instance()
        style_hints = app.styleHints() if isinstance(app, QtWidgets.QApplication) else None
        color_scheme = style_hints.colorScheme() if style_hints is not None else QtCore.Qt.ColorScheme.Light
        window = palette.color(QtGui.QPalette.ColorRole.Window)
        base = palette.color(QtGui.QPalette.ColorRole.Base)
        text = palette.color(QtGui.QPalette.ColorRole.Text)
        mid = palette.color(QtGui.QPalette.ColorRole.Mid)
        grid = QtGui.QColor(mid)
        grid.setAlpha(160 if color_scheme == QtCore.Qt.ColorScheme.Dark else 120)
        return {
            "dark": color_scheme == QtCore.Qt.ColorScheme.Dark,
            "figure_rgb": window.getRgbF()[:3],
            "axes_rgb": base.getRgbF()[:3],
            "text_rgb": text.getRgbF()[:3],
            "grid_rgba": grid.getRgbF(),
        }

    def _show_plot_config_dialog(self) -> None:
        if self.plot_config_dialog.isHidden():
            self.plot_config_dialog.show()
        self.plot_config_dialog.raise_()
        self.plot_config_dialog.activateWindow()

    def _probe_supply_candidate(self, port_name: str) -> dict[str, Any] | None:
        if serial is None:
            return None
        trials = (
            (115200, b"*IDN?\r\n"),
            (115200, b"*IDN?\n"),
            (9600, b"*IDN?\r\n"),
            (9600, b"*IDN?\n"),
        )
        for baudrate, payload in trials:
            try:
                with serial.Serial(port_name, baudrate=baudrate, timeout=0.5, write_timeout=0.5) as port:
                    port.reset_input_buffer()
                    port.reset_output_buffer()
                    port.rts = False
                    port.dtr = False
                    time.sleep(0.08)
                    port.write(payload)
                    port.flush()
                    time.sleep(0.12)
                    raw = port.readline().decode("ascii", errors="ignore").strip()
            except Exception:
                continue
            if not raw:
                continue
            profile_id = _supply_profile_id_from_idn(raw)
            if profile_id:
                return {
                    "port": port_name,
                    "baudrate": baudrate,
                    "profile_id": profile_id,
                    "idn_text": raw,
                }
        return None

    def _auto_detect_supply_port(self) -> bool:
        if list_ports is None:
            self._log("Supply auto-detect unavailable because pyserial is missing.")
            return False
        for port in list_ports.comports():
            match = self._probe_supply_candidate(port.device)
            if match is None:
                continue
            index = self.combo_supply_port.findData(match["port"])
            if index >= 0:
                self.combo_supply_port.setCurrentIndex(index)
            if self.combo_supply_baud.findText(str(match["baudrate"])) >= 0:
                self.combo_supply_baud.setCurrentText(str(match["baudrate"]))
            profile_index = self.combo_supply_profile.findData(str(match["profile_id"]))
            if profile_index >= 0:
                self.combo_supply_profile.setCurrentIndex(profile_index)
            self._log(
                f"Auto-detected supply on {match['port']} at {match['baudrate']} baud "
                f"({match['idn_text']})."
            )
            return True
        self._log("Automatic supply detection did not find a supported serial power supply.")
        return False

    def _refresh_supply_ports(self) -> None:
        current = self.combo_supply_port.currentData() or self.settings.value("supply_port", "", type=str)
        self.combo_supply_port.clear()
        if list_ports is None:
            self.combo_supply_port.addItem("pyserial unavailable", "")
            return
        for port in list_ports.comports():
            label = f"{port.device} - {port.description}"
            self.combo_supply_port.addItem(label, port.device)
        if current:
            index = self.combo_supply_port.findData(current)
            if index >= 0:
                self.combo_supply_port.setCurrentIndex(index)

    def _log(self, message: str) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(lambda message=message: self._log(message))
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.log_output.appendPlainText(line)
        if self._run_log_mirror_enabled:
            try:
                append_text_with_rotation(self._run_log_mirror_path, line + "\n")
            except Exception:
                self._run_log_mirror_enabled = False
                if hasattr(self, "action_mirror_run_log") and self.action_mirror_run_log is not None:
                    self.action_mirror_run_log.blockSignals(True)
                    self.action_mirror_run_log.setChecked(False)
                    self.action_mirror_run_log.blockSignals(False)
                self.log_output.appendPlainText(
                    f"[{timestamp}] Run-log file mirror disabled because writing {self._run_log_mirror_path} failed."
                )

    def _set_run_status(self, message: str) -> None:
        self.label_recipe_banner.setText(message)

    def _probe_scale_candidate(self, port_name: str) -> dict[str, Any] | None:
        trials = (
            (9600, "\\x1bp", ""),
            (9600, "\\x1bp", "\\r\\n"),
            (600, "\\x1bp", ""),
            (600, "\\x1bp", "\\r\\n"),
        )
        for baudrate, request_command, terminator in trials:
            try:
                raw = _read_serial_bytes(
                    port_name,
                    baudrate=baudrate,
                    payload=_decode_escape_text(request_command) + _decode_escape_text(terminator),
                    total_wait_s=0.8,
                )
            except Exception:
                continue
            raw_text = raw.decode("utf-8", errors="ignore").strip()
            if _parse_first_float(raw_text) is None:
                continue
            return {
                "port": port_name,
                "baudrate": baudrate,
                "request_command": request_command,
                "terminator": terminator,
                "raw_text": raw_text,
            }
        return None

    def _auto_detect_scale_port(self) -> bool:
        if list_ports is None:
            self._log("Scale auto-detect unavailable because pyserial is missing.")
            return False
        for port in list_ports.comports():
            match = self._probe_scale_candidate(port.device)
            if match is None:
                continue
            index = self.combo_scale_port.findData(match["port"])
            if index >= 0:
                self.combo_scale_port.setCurrentIndex(index)
            if self.combo_scale_baud.findText(str(match["baudrate"])) >= 0:
                self.combo_scale_baud.setCurrentText(str(match["baudrate"]))
            self.edit_scale_request.setText(str(match["request_command"]))
            self.edit_scale_terminator.setText(str(match["terminator"]))
            self._log(
                f"Auto-detected scale on {match['port']} at {match['baudrate']} baud "
                f"(sample reply: {match['raw_text']})."
            )
            return True
        self._log("Automatic scale detection did not find a responding serial balance.")
        return False

    def _refresh_scale_ports(self) -> None:
        current = self.combo_scale_port.currentData() or self.settings.value("scale_port", "", type=str)
        self.combo_scale_port.clear()
        if list_ports is None:
            self.combo_scale_port.addItem("pyserial unavailable", "")
            return
        seen = False
        preferred_index = -1
        for port in list_ports.comports():
            label = f"{port.device} - {port.description}"
            self.combo_scale_port.addItem(label, port.device)
            if current and port.device == current:
                seen = True
            description = port.description.lower()
            if "prolific" in description or "pl2303" in description:
                preferred_index = self.combo_scale_port.count() - 1
            elif preferred_index < 0 and port.device.upper() == "COM4":
                preferred_index = self.combo_scale_port.count() - 1
        if current and seen:
            index = self.combo_scale_port.findData(current)
            if index >= 0:
                self.combo_scale_port.setCurrentIndex(index)
        elif preferred_index >= 0:
            self.combo_scale_port.setCurrentIndex(preferred_index)
        elif self.combo_scale_port.count():
            self.combo_scale_port.setCurrentIndex(0)

    def _auto_detect_tic(self) -> bool:
        candidates: list[str] = []
        saved = self.edit_ticcmd_path.text().strip()
        discovered = _find_ticcmd()
        for candidate in (saved, discovered):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        for candidate in candidates:
            controller = TicController(command_path=candidate)
            try:
                devices = _parse_tic_list_output(controller.run("--list"))
            except Exception:
                continue
            if not devices:
                continue
            self.edit_ticcmd_path.setText(candidate)
            if len(devices) == 1 or not self.edit_tic_serial.text().strip():
                self.edit_tic_serial.setText(devices[0][0])
            if len(devices) == 1:
                self._log(f"Auto-detected Tic controller {devices[0][0]} using {candidate}.")
            else:
                self._log(
                    f"Detected {len(devices)} Tic controllers using {candidate}; "
                    f"defaulting to {self.edit_tic_serial.text().strip() or devices[0][0]}."
                )
            return True
        self._log("Automatic Tic detection did not find a reachable controller.")
        return False

    def _benchmark_tic_transports(self) -> None:
        command_path = self.edit_ticcmd_path.text().strip() or _find_ticcmd()
        if command_path.strip().lower() in TIC_USB_TRANSPORT_ALIASES:
            command_path = _find_ticcmd()
        serial = self.edit_tic_serial.text().strip()
        self._log("Benchmarking Tic transports with status reads and command-timeout resets...")
        try:
            results = benchmark_tic_transport_latency(
                command_path=command_path,
                device_serial=serial,
                iterations=5,
            )
        except Exception as exc:
            self._log(f"Tic transport benchmark failed: {exc}")
            return
        for label, result in results.items():
            error = result.get("error")
            if error:
                self._log(f"Tic transport {label}: unavailable ({error}).")
                continue
            self._log(
                f"Tic transport {label}: reset median {result['reset_median_ms']:.1f} ms, "
                f"status median {result['status_median_ms']:.1f} ms over {result['iterations']} trials."
            )

    def _build_tic_controller(self) -> TicController:
        key = (
            self.edit_ticcmd_path.text().strip(),
            self.edit_tic_serial.text().strip(),
            bool(self.check_tic_native_usb.isChecked()),
        )
        if self._tic_controller is None or self._tic_controller_key != key:
            self._tic_controller = TicController(
                command_path=key[0],
                device_serial=key[1],
                prefer_native_usb=key[2],
                allow_ticcmd_fallback=not key[2],
                transport_logger=self._log,
            )
            self._tic_controller_key = key
        return self._tic_controller

    def _build_tic_dispatcher(self) -> TicCommandDispatcher:
        key = (
            self.edit_ticcmd_path.text().strip(),
            self.edit_tic_serial.text().strip(),
            bool(self.check_tic_native_usb.isChecked()),
        )
        if self._tic_command_dispatcher is not None and self._tic_command_dispatcher_key != key:
            self._tic_command_dispatcher.stop()
            self._tic_command_dispatcher = None
        if self._tic_command_dispatcher is None:
            self._tic_command_dispatcher = TicCommandDispatcher(self._build_tic_controller)
            self._tic_command_dispatcher_key = key
        return self._tic_command_dispatcher

    def _stop_tic_dispatcher(self) -> None:
        dispatcher = self._tic_command_dispatcher
        self._tic_command_dispatcher = None
        self._tic_command_dispatcher_key = None
        if dispatcher is not None and hasattr(dispatcher, "stop"):
            dispatcher.stop()

    def _wait_for_tic_dispatcher(
        self,
        dispatcher: object,
        action: str,
        *,
        timeout_s: float = 2.0,
    ) -> bool:
        wait_until_idle = getattr(dispatcher, "wait_until_idle", None)
        if callable(wait_until_idle) and not wait_until_idle(timeout_s=timeout_s):
            self._log(f"Tic {action} command is still pending after {timeout_s:.1f} s.")
            return False
        last_error = getattr(dispatcher, "last_error", None)
        if callable(last_error):
            error = last_error()
            if error is not None:
                self._log(f"Tic {action} command failed: {error}")
                return False
        return True

    def _build_supply_controller(self) -> PowerSupplyController:
        profile_id = str(self.combo_supply_profile.currentData() or "hmp4030")
        if self._using_shared_broker_supply():
            return SharedBrokerSupplyController(  # type: ignore[return-value]
                host=self.edit_shared_broker_host.text().strip(),
                port=int(self.spin_shared_broker_port.value()),
                max_voltage_v=float(self.spin_supply_voltage_limit.value()),
                current_channel=self._current_sweep_supply_channel(),
                motor_channel=self._motor_supply_channel(),
            )
        return PowerSupplyController(
            port_name=str(self.combo_supply_port.currentData() or "").strip(),
            baudrate=int(self.combo_supply_baud.currentText()),
            profile_id=profile_id,
            max_voltage_v=float(self.spin_supply_voltage_limit.value()),
            channel_select=self._current_sweep_supply_channel() or 0,
        )

    def _using_shared_broker_supply(self) -> bool:
        profile_id = str(self.combo_supply_profile.currentData() or "hmp4030")
        return bool(SUPPLY_PROFILES.get(profile_id, {}).get("shared_broker", False))

    def _apply_supply_profile_defaults(self) -> None:
        profile_id = str(self.combo_supply_profile.currentData() or "hmp4030")
        profile = SUPPLY_PROFILES.get(profile_id, SUPPLY_PROFILES["hmp4030"])
        baudrate = int(profile.get("baudrate", 0) or 0)
        if baudrate > 0:
            self.combo_supply_baud.setCurrentText(str(baudrate))
        self.spin_supply_voltage_limit.setValue(float(profile.get("max_voltage", 32.05)))
        self.spin_supply_manual_current.setValue(float(profile.get("start_current_mA", 1.0)))
        if not getattr(self, "_settings_restore_in_progress", False):
            current_channel_index = self.combo_current_sweep_supply_channel.findData(0)
            if current_channel_index >= 0:
                self.combo_current_sweep_supply_channel.setCurrentIndex(current_channel_index)
            motor_channel_index = self.combo_motor_supply_channel.findData(0)
            if motor_channel_index >= 0:
                self.combo_motor_supply_channel.setCurrentIndex(motor_channel_index)

    def _connect_supply(self, checked: bool = False, *, show_errors: bool = True) -> bool:
        self._disconnect_supply()
        controller = self._build_supply_controller()
        try:
            controller.connect()
        except Exception as exc:
            if self._using_shared_broker_supply():
                try:
                    self._start_owned_shared_broker()
                    controller = self._build_supply_controller()
                    controller.connect()
                except Exception as broker_exc:
                    if show_errors:
                        QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to connect power supply: {broker_exc}")
                    else:
                        self._log(f"Failed to connect power supply: {broker_exc}")
                    return False
            else:
                if show_errors:
                    QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to connect power supply: {exc}")
                else:
                    self._log(f"Failed to connect power supply: {exc}")
                return False
        self._supply_controller = controller
        if isinstance(controller, SharedBrokerSupplyController):
            self.label_supply_status.setText(
                f"Supply connected through shared HMP broker at {controller.port_name}."
            )
        else:
            self.label_supply_status.setText(
                f"Supply connected on {controller.port_name} at {controller.baudrate} baud ({controller.profile['label']})."
            )
        self._log(self.label_supply_status.text())
        self._refresh_supply_snapshot(force=True)
        return True

    def _start_owned_shared_broker(self) -> None:
        if self._owned_shared_broker_server is not None:
            return
        port_name = str(self.combo_supply_port.currentData() or "").strip()
        if not port_name:
            raise RuntimeError("Select the HMP COM port before starting the shared HMP broker.")
        current_channel = self._current_sweep_supply_channel()
        if current_channel is None:
            raise RuntimeError("Select a current-sweep supply channel before starting the shared HMP broker.")
        motor_channel = self._motor_supply_channel() if self._motor_supply_enabled() else None
        if self._motor_supply_enabled() and motor_channel is None:
            raise RuntimeError("Select a motor supply channel before starting the shared HMP broker.")

        driver = HmpSerialDriver(
            port_name=port_name,
            baudrate=int(self.combo_supply_baud.currentText()),
            timeout_s=0.7,
        )
        try:
            driver.connect()
            idn_text = driver.identify()
            if driver.profile is None:
                raise RuntimeError(f"Unsupported shared HMP response: {idn_text}")
            broker = SharedPowerSupplyBroker(driver, driver.profile)
            current_limit_mA = max(
                float(self.spin_supply_manual_current.value()),
                float(self.spin_current_sweep_start_mA.value()),
                float(self.spin_current_sweep_end_mA.value()),
                float(self.spin_continuity_current_mA.value()) if self._continuity_monitor_enabled() else 0.0,
                1.0,
            )
            broker.assign_role(
                channel=current_channel,
                role=ROLE_MINI_DMA_CURRENT,
                confirmed=True,
                voltage_limit_v=float(self.spin_supply_voltage_limit.value()),
                current_limit_a=current_limit_mA / 1000.0,
            )
            if motor_channel is not None:
                broker.assign_role(
                    channel=motor_channel,
                    role=ROLE_MINI_DMA_MOTOR,
                    confirmed=True,
                    voltage_limit_v=float(self.spin_motor_supply_voltage.value()),
                    current_limit_a=float(self.spin_motor_supply_current_limit.value()),
                )
            broker.confirm_profile(name="Mini DMA auto-started shared HMP broker")
            server, thread = start_broker_server(
                broker,
                host=self.edit_shared_broker_host.text().strip() or "127.0.0.1",
                port=int(self.spin_shared_broker_port.value()),
            )
        except Exception:
            driver.close()
            raise

        self._owned_shared_broker_server = server
        self._owned_shared_broker_thread = thread
        self._owned_shared_broker_driver = driver
        self._log(f"Started shared HMP broker on {self.edit_shared_broker_host.text().strip() or '127.0.0.1'}:{self.spin_shared_broker_port.value()} for {port_name}.")

    def _stop_owned_shared_broker(self) -> None:
        server = self._owned_shared_broker_server
        thread = self._owned_shared_broker_thread
        driver = self._owned_shared_broker_driver
        self._owned_shared_broker_server = None
        self._owned_shared_broker_thread = None
        self._owned_shared_broker_driver = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread is not None:
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass

    def _disconnect_supply(self) -> None:
        if self._supply_controller is not None:
            self._supply_controller.disconnect()
        self._supply_controller = None
        self._supply_output_enabled = False
        self.label_supply_status.setText("Supply disconnected.")
        self._refresh_supply_live_label()

    def _refresh_supply_live_label(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._refresh_supply_live_label)
            return
        setpoint_text = "-" if self._supply_last_setpoint_mA is None else f"{self._supply_last_setpoint_mA:.2f} mA"
        current_text = "-" if self._supply_snapshot["current_mA"] is None else f"{self._supply_snapshot['current_mA']:.2f} mA"
        voltage_text = "-" if self._supply_snapshot["voltage_V"] is None else f"{self._supply_snapshot['voltage_V']:.3f} V"
        resistance_text = "-" if self._supply_snapshot["resistance_ohm"] is None else f"{self._supply_snapshot['resistance_ohm']:.3f} Ohm"
        power_text = "-" if self._supply_snapshot["power_W"] is None else f"{self._supply_snapshot['power_W']:.4f} W"
        self.label_supply_live.setText(
            f"Set {setpoint_text} | Current {current_text} | Voltage {voltage_text} | "
            f"Resistance {resistance_text} | Power {power_text}"
        )

    def _motor_supply_enabled(self) -> bool:
        config = self._control_config()
        if config is not None:
            return config.motor_supply_enabled
        return self.check_motor_supply_power.isChecked()

    def _motor_supply_channel(self) -> int | None:
        configured = int(self.combo_motor_supply_channel.currentData() or 0)
        return configured if configured > 0 else None

    def _current_sweep_supply_channel(self) -> int | None:
        configured = int(self.combo_current_sweep_supply_channel.currentData() or 0)
        if configured > 0:
            return configured
        return None

    def _enable_motor_supply_output(self) -> bool:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            QtWidgets.QMessageBox.information(self, APP_NAME, "Connect the HMP power supply first.")
            return False
        try:
            channel = self._motor_supply_channel()
            if channel is None:
                raise RuntimeError("Select a motor supply channel before enabling motor power.")
            self._supply_controller.configure_channel(
                channel=channel,
                voltage_v=float(self.spin_motor_supply_voltage.value()),
                current_a=float(self.spin_motor_supply_current_limit.value()),
                output_on=True,
            )
            current_channel = self._current_sweep_supply_channel()
            if current_channel is not None:
                self._supply_controller.select_channel(current_channel)
            output_state = self._supply_channel_output_state(channel)
            if output_state is False:
                raise RuntimeError(f"CH{channel} output did not report ON after motor power enable.")
        except Exception as exc:
            self._log(f"Motor supply enable failed: {exc}")
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to enable motor supply channel: {exc}")
            return False
        self._log(
            f"Motor supply CH{channel} enabled at "
            f"{_format_compact_unit(self.spin_motor_supply_voltage.value(), 'V', decimals=2)} "
            f"with {_format_compact_unit(self.spin_motor_supply_current_limit.value(), 'A', decimals=3)} limit."
        )
        return True

    def _supply_channel_output_state(self, channel: int) -> bool | None:
        if self._supply_controller is None:
            return None
        method = getattr(self._supply_controller, "output_state", None)
        if not callable(method):
            return None
        try:
            return method(channel)
        except Exception as exc:
            self._log(f"Supply CH{channel} output-state readback failed: {exc}")
            return None

    def _disable_motor_supply_output(self) -> bool:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            return False
        try:
            channel = self._motor_supply_channel()
            if channel is None:
                self._log("Select a motor supply channel before disabling motor power.")
                return False
            self._supply_controller.configure_channel(
                channel=channel,
                voltage_v=float(self.spin_motor_supply_voltage.value()),
                current_a=float(self.spin_motor_supply_current_limit.value()),
                output_on=False,
            )
            current_channel = self._current_sweep_supply_channel()
            if current_channel is not None:
                self._supply_controller.select_channel(current_channel)
        except Exception as exc:
            self._log(f"Motor supply disable failed: {exc}")
            return False
        self._log(f"Motor supply CH{channel} disabled.")
        return True

    def _prepare_current_sweep_supply_channel(self) -> bool:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            return False
        channel = self._current_sweep_supply_channel()
        if channel is None:
            self._log("Select a current-sweep supply channel before preparing the output.")
            return False
        current_mA = float(self.spin_supply_manual_current.value())
        try:
            self._supply_controller.configure_channel(
                channel=channel,
                voltage_v=float(self.spin_supply_voltage_limit.value()),
                current_a=max(0.0, current_mA) / 1000.0,
                output_on=False,
            )
            self._supply_controller.select_channel()
        except Exception as exc:
            self._log(f"Current-sweep channel setup failed: {exc}")
            return False
        self._supply_output_enabled = False
        self._supply_last_setpoint_mA = current_mA
        self._heating_program_current_mA = current_mA
        self._log(
            f"Current-sweep CH{channel} prepared at "
            f"{_format_compact_unit(self.spin_supply_voltage_limit.value(), 'V', decimals=2)} / "
            f"{_format_compact_unit(current_mA, 'mA', decimals=2)} with output off."
        )
        return True

    def _refresh_supply_snapshot(self, force: bool = False) -> dict[str, float | None]:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            self._refresh_supply_live_label()
            return dict(self._supply_snapshot)
        now_s = time.monotonic()
        if (
            not force
            and self._supply_snapshot_monotonic > 0.0
            and now_s - self._supply_snapshot_monotonic < (self._supply_read_interval_ms() / 1000.0)
        ):
            self._refresh_supply_live_label()
            self._handle_supply_limit_condition()
            return dict(self._supply_snapshot)
        try:
            self._supply_snapshot = dict(self._supply_controller.measure())
            self._supply_snapshot_monotonic = now_s
        except Exception as exc:
            self._log(f"Supply read failed: {exc}")
        self._refresh_supply_live_label()
        self._handle_supply_limit_condition()
        return dict(self._supply_snapshot)

    def _apply_manual_supply_current(self) -> None:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            QtWidgets.QMessageBox.information(self, APP_NAME, "Connect the power supply first.")
            return
        try:
            controller = self._supply_controller
            assert controller is not None
            controller.initialize_output(
                current_mA=float(self.spin_supply_manual_current.value()),
                reset_on_start=(
                    bool(controller.profile.get("reset_on_start", False))
                    and not self._motor_supply_enabled()
                ),
            )
            self._supply_output_enabled = True
            self._supply_last_setpoint_mA = float(self.spin_supply_manual_current.value())
            self._heating_program_current_mA = self._supply_last_setpoint_mA
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to apply current: {exc}")
            return
        self.label_supply_status.setText("Supply output enabled from the manual current control.")
        self._refresh_supply_snapshot(force=True)

    def _enable_supply_output(self) -> None:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            QtWidgets.QMessageBox.information(self, APP_NAME, "Connect the power supply first.")
            return
        try:
            self._supply_controller.output_on()
            self._supply_output_enabled = True
            self.label_supply_status.setText("Supply output enabled.")
            self._refresh_supply_snapshot(force=True)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to enable output: {exc}")

    def _disable_supply_output(self) -> None:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            self._supply_output_enabled = False
            self._supply_last_setpoint_mA = 0.0
            return
        try:
            shutdown = getattr(self._supply_controller, "shutdown_output", None)
            if callable(shutdown):
                shutdown(reset_voltage_v=1.0, reset_current_mA=1.0)
            else:
                self._supply_controller.output_off()
        except Exception as exc:
            self._log(f"Failed to disable/reset supply output: {exc}")
        self._supply_output_enabled = False
        self._supply_last_setpoint_mA = 1.0
        if self._is_ui_thread():
            self.label_supply_status.setText("Supply output disabled; current channel reset to 1 V / 1 mA.")
        else:
            self._run_on_ui_thread(
                lambda: self.label_supply_status.setText(
                    "Supply output disabled; current channel reset to 1 V / 1 mA."
                )
            )
        self._refresh_supply_snapshot(force=True)

    def _emergency_stop(self) -> None:
        messages: list[str] = []

        if self._automation_active:
            self._stop_auto_ramp(
                log_completion=False,
                stop_reason="emergency_stop",
                stop_detail="Emergency stop button was pressed.",
            )
            messages.append("recipe stopped")

        try:
            motor_off = self._disable_motor_supply_output()
            if motor_off:
                messages.append("motor supply off")
            else:
                messages.append("motor supply already off/unavailable")
        except Exception as exc:
            messages.append(f"motor-supply-off failed: {exc}")
            self._log(f"Emergency stop could not disable motor supply output: {exc}")

        try:
            self._disable_supply_output()
            messages.append("current off")
        except Exception as exc:
            messages.append(f"current-off failed: {exc}")
            self._log(f"Emergency stop could not disable supply output: {exc}")
            self._supply_output_enabled = False
            self._supply_last_setpoint_mA = 0.0

        try:
            dispatcher = self._build_tic_dispatcher()
            dispatcher.halt_and_hold()
            tic_halted = self._wait_for_tic_dispatcher(dispatcher, "halt", timeout_s=2.0)
            self._stop_tic_keepalive()
            messages.append("Tic halted" if tic_halted else "Tic halt pending/failed")
        except Exception as exc:
            messages.append(f"Tic halt failed: {exc}")
            self._log(f"Emergency stop could not halt Tic: {exc}")

        if self._session_active:
            self._stop_session(
                reason="emergency_stop",
                detail="Emergency stop button was pressed.",
            )
            messages.append("session saved/stopped")

        self._refresh_live_labels()
        self._refresh_plots()
        summary = "EMERGENCY STOP: " + ", ".join(messages or ["no active hardware/session state"])
        self._log(summary)
        self.statusBar().showMessage(summary, 10000)

    def _supply_current_resolution_mA(self) -> float:
        config = self._control_config()
        if config is not None:
            return config.supply_current_resolution_mA
        if self._supply_controller is not None:
            return self._supply_controller.current_resolution_mA()
        profile = SUPPLY_PROFILES.get(str(self.combo_supply_profile.currentData() or "hmp4030"), {})
        return max(0.001, float(profile.get("current_resolution_mA", 1.0)))

    def _quantize_supply_current_mA(self, current_mA: float) -> float:
        resolution_mA = self._supply_current_resolution_mA()
        return max(0.0, round(float(current_mA) / resolution_mA) * resolution_mA)

    def _minimum_recipe_current_mA(self) -> float:
        return MIN_RECIPE_CURRENT_MA

    def _recipe_current_setpoint_mA(self, current_mA: float) -> float:
        return max(self._minimum_recipe_current_mA(), self._quantize_supply_current_mA(current_mA))

    def _quantize_ramp_current_mA(self, current_mA: float, direction: float, end_mA: float) -> float:
        resolution_mA = self._supply_current_resolution_mA()
        if direction >= 0.0:
            quantized = math.floor((float(current_mA) + 1e-9) / resolution_mA) * resolution_mA
            return min(float(end_mA), max(0.0, quantized))
        quantized = math.ceil((float(current_mA) - 1e-9) / resolution_mA) * resolution_mA
        return max(float(end_mA), max(0.0, quantized))

    def _set_recipe_current_mA(self, current_mA: float, *, measure_after: bool = False) -> bool:
        if self._supply_controller is None or not self._supply_controller.is_connected():
            self._log("Recipe stopped because the power supply is not connected.")
            return False
        current_mA = self._quantize_supply_current_mA(current_mA)
        if self._automation_active or self._session_active:
            current_mA = max(self._minimum_recipe_current_mA(), current_mA)
        try:
            if not self._supply_output_enabled:
                self._supply_controller.initialize_output(
                    current_mA=current_mA,
                    reset_on_start=(
                        bool(self._supply_controller.profile.get("reset_on_start", False))
                        and not self._motor_supply_enabled()
                    ),
                )
                self._supply_output_enabled = True
            else:
                self._supply_controller.set_current_mA(current_mA)
            self._supply_last_setpoint_mA = current_mA
            self._heating_program_current_mA = current_mA
            if measure_after:
                self._refresh_supply_snapshot(force=True)
        except Exception as exc:
            self._log(f"Recipe current update failed: {exc}")
            return False
        return True

    def _continuity_monitor_enabled(self) -> bool:
        return hasattr(self, "check_continuity_monitor") and self.check_continuity_monitor.isChecked()

    def _continuity_current_mA(self) -> float:
        if hasattr(self, "spin_continuity_current_mA"):
            return max(0.0, float(self.spin_continuity_current_mA.value()))
        return CONTINUITY_CURRENT_DEFAULT_MA

    def _recipe_uses_explicit_current(self, steps: Sequence[AutomationStep]) -> bool:
        if any(step.action in {"set_current", "sweep_current"} for step in steps):
            return True
        return self._is_current_sweep_mode(str(self.combo_recipe_mode.currentData() or self._automation_name))

    def _steps_begin_with_length_setup(self, steps: Sequence[AutomationStep]) -> bool:
        return bool(steps) and steps[0].note in {"setup_start_length", "setup_preload"}

    def _prepare_continuity_current_for_recipe(self, steps: Sequence[AutomationStep]) -> bool:
        if not self._continuity_monitor_enabled():
            return True
        if self._recipe_uses_explicit_current(steps) and not self._steps_begin_with_length_setup(steps):
            return True
        current_mA = self._continuity_current_mA()
        if current_mA <= 0.0:
            return True
        if not self._set_recipe_current_mA(current_mA, measure_after=True):
            self._log("Recipe stopped because continuity-current setup failed.")
            return False
        self._log(
            f"Continuity monitor enabled at {_format_compact_unit(current_mA, 'mA', decimals=3)}."
        )
        return True

    def _set_reference_from_current_position(self) -> None:
        self._position_reference_mm = self._effective_position_mm
        self._preload_reference_armed = False
        self._preload_trigger_elapsed_s = 0.0 if self._session_active else None
        self._refresh_live_labels()
        self._log(f"Gauge zero moved to the current position ({self._effective_position_mm:.4f} mm).")

    def _heating_mode(self) -> str:
        return HEATING_MODE_OFF

    def _prepare_heating_for_session(self) -> None:
        return

    def _advance_heating_after_record(self) -> None:
        return

    def _handle_supply_limit_condition(self) -> None:
        if self._wire_break_detected():
            self._stop_for_wire_break()
            return
        limit_v = float(self.spin_supply_voltage_limit.value())
        measured_v = self._supply_snapshot.get("voltage_V")
        if measured_v is None or limit_v <= 0:
            self._supply_voltage_limit_logged = False
            return
        if measured_v < limit_v * 0.995:
            self._supply_voltage_limit_logged = False
            return

        if self._automation_active and self._is_current_sweep_mode(self._automation_name):
            if (
                self._active_current_sweep_step_index is not None
                and self._current_sweep_ramp_hold_step_index == self._active_current_sweep_step_index
            ):
                if not self._supply_voltage_limit_logged:
                    self._log(
                        f"Supply voltage reached the configured limit ({measured_v:.3f} V / {limit_v:.3f} V) "
                        "while the current ramp is held for target recovery; keeping the held current."
                    )
                    self._supply_voltage_limit_logged = True
                return
            if self._active_current_sweep_step_is_nominal_return():
                if self._current_sweep_voltage_limit_step_index == self._active_current_sweep_step_index:
                    self._clear_current_sweep_voltage_limit()
                if not self._supply_voltage_limit_logged:
                    self._log(
                        f"Supply voltage remains near the configured limit ({measured_v:.3f} V / {limit_v:.3f} V) "
                        "during the reverse current sweep; continuing the configured rate-limited return."
                    )
                    self._supply_voltage_limit_logged = True
                return
            self._mark_current_sweep_voltage_limit(
                measured_v=measured_v,
                limit_v=limit_v,
                started_s=self._supply_snapshot_monotonic,
            )
            return

        if not self._supply_voltage_limit_logged:
            target_text = (
                f"{self._minimum_recipe_current_mA():.3g} mA"
                if self._automation_active or self._session_active
                else "0 mA"
            )
            self._log(
                f"Supply voltage reached the configured limit ({measured_v:.3f} V / {limit_v:.3f} V); "
                f"setting current to {target_text}."
            )
            self._supply_voltage_limit_logged = True
        if self._supply_output_enabled and (self._supply_last_setpoint_mA or 0.0) > 0.0:
            self._set_recipe_current_mA(0.0, measure_after=False)

    def _wire_break_detected(self) -> bool:
        if self._wire_break_stop_in_progress:
            return False
        if not self._automation_active:
            return False
        if not self._supply_output_enabled:
            return False
        setpoint_mA = self._active_current_sweep_last_setpoint_mA
        if setpoint_mA is None:
            setpoint_mA = self._supply_last_setpoint_mA
        if setpoint_mA is None:
            return False
        min_setpoint_mA = (
            min(WIRE_BREAK_MIN_SETPOINT_MA, max(self._supply_current_resolution_mA(), self._continuity_current_mA()))
            if self._continuity_monitor_enabled()
            else WIRE_BREAK_MIN_SETPOINT_MA
        )
        if abs(float(setpoint_mA)) < min_setpoint_mA:
            return False
        measured_current_mA = self._supply_snapshot.get("current_mA")
        measured_voltage_v = self._supply_snapshot.get("voltage_V")
        limit_v = float(self.spin_supply_voltage_limit.value())
        if measured_current_mA is None or measured_voltage_v is None or limit_v <= 0.0:
            return False
        current_threshold_mA = max(WIRE_BREAK_MAX_MEASURED_MA, self._supply_current_resolution_mA())
        if abs(float(measured_current_mA)) > current_threshold_mA:
            return False
        return float(measured_voltage_v) >= limit_v * WIRE_BREAK_VOLTAGE_LIMIT_FRACTION

    def _current_sweep_wire_break_detected(self) -> bool:
        return self._wire_break_detected()

    def _wire_break_stop_message(self) -> str:
        setpoint_mA = self._active_current_sweep_last_setpoint_mA
        if setpoint_mA is None:
            setpoint_mA = self._supply_last_setpoint_mA
        measured_current_mA = self._supply_snapshot.get("current_mA")
        measured_voltage_v = self._supply_snapshot.get("voltage_V")
        setpoint_text = "-" if setpoint_mA is None else _format_compact_unit(float(setpoint_mA), "mA", decimals=3)
        current_text = (
            "-"
            if measured_current_mA is None
            else _format_compact_unit(float(measured_current_mA), "mA", decimals=3)
        )
        voltage_text = (
            "-"
            if measured_voltage_v is None
            else _format_compact_unit(float(measured_voltage_v), "V", decimals=3)
        )
        return (
            "Wire break detected: "
            f"set current {setpoint_text}, measured current {current_text}, voltage {voltage_text}. "
            "Current output was disabled and the measurement was stopped."
        )

    def _stop_for_wire_break(self) -> None:
        if not self._is_ui_thread():
            if self._wire_break_stop_in_progress:
                return
            self._wire_break_stop_in_progress = True
            self._run_on_ui_thread(self._finish_wire_break_stop_on_ui_thread)
            return
        self._finish_wire_break_stop_on_ui_thread()

    def _finish_wire_break_stop_on_ui_thread(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._finish_wire_break_stop_on_ui_thread)
            return
        if not self._wire_break_stop_in_progress:
            self._wire_break_stop_in_progress = True
        try:
            message = self._wire_break_stop_message()
            self._log(message)
            self._clear_current_sweep_voltage_limit()
            self._stop_auto_ramp(
                log_completion=False,
                offer_recovery=False,
                stop_reason="wire_break_or_contact_loss",
                stop_detail=message,
            )
            if self._session_active:
                self._stop_session(reason="wire_break_or_contact_loss", detail=message)
            self.statusBar().showMessage(message, 15000)
            self._ask_wire_break_recovery_after_stop(message)
        finally:
            self._wire_break_stop_in_progress = False

    def _ask_wire_break_recovery_after_stop(self, message: str) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(lambda message=message: self._ask_wire_break_recovery_after_stop(message))
            return
        if self._tic_motor_power_ok is False:
            QtWidgets.QMessageBox.warning(self, APP_NAME, message)
            return
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setText("Wire break detected.")
        box.setInformativeText(
            f"{message}\n\nDo you want to move the tensile displacement back to 0 now?"
        )
        return_position_button = box.addButton("Move displacement to 0", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        leave_button = box.addButton("Leave as is", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(leave_button)
        box.exec()
        if box.clickedButton() == return_position_button:
            self._start_recovery_displacement_zero()

    def _mark_current_sweep_voltage_limit(
        self,
        *,
        measured_v: float,
        limit_v: float,
        started_s: float | None = None,
    ) -> None:
        step_index = self._active_current_sweep_step_index
        if step_index is None:
            return
        if self._current_sweep_voltage_limit_step_index == step_index:
            return
        start_mA = self._active_current_sweep_last_setpoint_mA
        if start_mA is None:
            start_mA = self._supply_last_setpoint_mA
        measured_current_mA = self._supply_snapshot.get("current_mA")
        if (
            start_mA is None
            and measured_current_mA is not None
            and math.isfinite(float(measured_current_mA))
            and float(measured_current_mA) > 0.0
        ):
            start_mA = float(measured_current_mA)
        self._current_sweep_voltage_limit_step_index = step_index
        self._current_sweep_voltage_limit_started_s = started_s if started_s is not None else time.monotonic()
        self._current_sweep_voltage_limit_start_mA = self._quantize_supply_current_mA(start_mA or 0.0)
        self._log(
            f"Supply voltage reached the configured limit ({measured_v:.3f} V / {limit_v:.3f} V); "
            "reversing recipe current back to the sweep start current and continuing."
        )

    def _current_sweep_step_is_nominal_return(self, step: AutomationStep | None) -> bool:
        if step is None or step.action != "sweep_current":
            return False
        if step.current_start_mA is None or step.current_end_mA is None:
            return False
        return float(step.current_end_mA) < float(step.current_start_mA)

    def _active_current_sweep_step_is_nominal_return(self) -> bool:
        step_index = self._active_current_sweep_step_index
        if step_index is None or step_index < 0 or step_index >= len(self._automation_steps):
            return False
        return self._current_sweep_step_is_nominal_return(self._automation_steps[step_index])

    def _ignore_voltage_limit_on_nominal_current_return(self, step: AutomationStep, step_index: int) -> bool:
        if self._current_sweep_voltage_limit_step_index != step_index:
            return False
        if not self._current_sweep_step_is_nominal_return(step):
            return False
        self._clear_current_sweep_voltage_limit()
        self._write_control_trace(
            decision="voltage_limit_return_continue",
            basis=step.basis,
            target_value=step.target_value,
            result="continued",
            reason="already_rate_limited_return",
            task_text=self._current_sweep_step_task_summary(step),
        )
        self._log(
            "Supply voltage limit is still active during the nominal reverse current sweep; "
            "continuing the rate-limited return instead of skipping the step or changing current abruptly."
        )
        return True

    def _clear_current_sweep_voltage_limit(self) -> None:
        self._current_sweep_voltage_limit_step_index = None
        self._current_sweep_voltage_limit_started_s = None
        self._current_sweep_voltage_limit_start_mA = 0.0

    def _is_voltage_limited_return_pair(self, step_index: int, step: AutomationStep) -> bool:
        previous_index = int(step_index) - 1
        if previous_index < 0 or previous_index >= len(self._automation_steps):
            return False
        previous = self._automation_steps[previous_index]
        if previous.action != "sweep_current" or step.action != "sweep_current":
            return False
        if previous.basis != step.basis or previous.target_value != step.target_value:
            return False
        if previous.note != step.note:
            return False
        if (
            previous.current_start_mA is None
            or previous.current_end_mA is None
            or step.current_start_mA is None
            or step.current_end_mA is None
        ):
            return False
        resolution = max(self._supply_current_resolution_mA(), 1e-9)
        return (
            abs(float(previous.current_start_mA) - float(step.current_end_mA)) <= resolution
            and abs(float(previous.current_end_mA) - float(step.current_start_mA)) <= resolution
        )

    def _mark_voltage_limited_return_step(self, step_index: int, step: AutomationStep) -> None:
        return_index = int(step_index) + 1
        if return_index >= len(self._automation_steps):
            return
        return_step = self._automation_steps[return_index]
        if not self._is_voltage_limited_return_pair(return_index, return_step):
            return
        self._current_sweep_voltage_limited_return_steps.add(return_index)
        start_text = self._automation_current_target_text(return_step.current_start_mA)
        end_text = self._automation_current_target_text(return_step.current_end_mA)
        self._log(
            "Voltage limit reversed the current before the requested maximum was reached; "
            f"keeping the unwind as the return leg and skipping the paired nominal {start_text} -> {end_text} sweep "
            "to avoid a high-current restart."
        )

    def _complete_voltage_limited_return_step(self, step: AutomationStep, step_index: int) -> bool:
        if step_index not in self._current_sweep_voltage_limited_return_steps:
            return False
        self._current_sweep_voltage_limited_return_steps.discard(step_index)
        plateau_index = int(step.note) if step.note.isdigit() else None
        self._set_automation_context(
            phase="current_limit_return_skipped",
            basis=step.basis,
            target_value=step.target_value,
            plateau_index=plateau_index,
            note=step.note,
        )
        self._write_control_trace(
            decision="skip_voltage_limited_return",
            basis=step.basis,
            target_value=step.target_value,
            result="completed",
            reason="paired_return_already_recorded_by_voltage_limit_unwind",
        )
        self._log(
            "Skipped paired nominal reverse current sweep because the voltage-limit unwind already returned "
            "current to the sweep start."
        )
        return True

    def _choose_builder_project(self) -> None:
        start_dir = str(self._builder_project_path.parent) if self._builder_project_path is not None else self.edit_log_dir.text().strip()
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Microwire Builder project",
            start_dir,
            f"Microwire Project (*{PROJECT_EXTENSION});;All files (*)",
        )
        if path_str:
            self.edit_project_path.setText(path_str)
            self._builder_project_path = Path(path_str)
            self._auto_import_builder_project_if_possible(update_identity=False, quiet=True)

    def _refresh_diameter_import_state(self) -> None:
        self._mark_diameter_imported(self._diameter_imported)

    def _mark_diameter_imported(self, imported: bool) -> None:
        self._diameter_imported = bool(imported)
        if not hasattr(self, "spin_diameter"):
            return
        if self._diameter_imported:
            self.spin_diameter.setStyleSheet("")
            self.spin_diameter.setToolTip("Wire diameter imported from the Microwire Data Builder project; manual edits are allowed.")
        else:
            self.spin_diameter.setStyleSheet(
                "QDoubleSpinBox { border: 1px solid #dc2626; background-color: rgba(220, 38, 38, 0.10); }"
            )
            self.spin_diameter.setToolTip("Wire diameter has not been imported from the Builder project; manual edits are allowed.")

    def _read_builder_project_payload(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _project_match_score(self, row: Mapping[str, Any]) -> int:
        score = 0
        composition = _normalized_token(self.edit_name_composition.text())
        microwire = _normalized_microwire_token(self.edit_name_wire.text())
        specimen = _normalized_token(self.edit_name_specimen.text())
        row_composition = _normalized_token(row.get("Composition"))
        row_microwire = _normalized_microwire_token(_project_row_value(row, PROJECT_ROW_MICROWIRE_KEYS))
        row_specimen = _normalized_token(_project_row_value(row, PROJECT_ROW_SPECIMEN_KEYS))
        if composition and row_composition == composition:
            score += 5
        if microwire and row_microwire == microwire:
            score += 5
        if specimen and row_specimen == specimen:
            score += 3
        diameter = _safe_float(_project_row_value(row, PROJECT_ROW_DIAMETER_KEYS))
        if diameter and diameter > 0:
            score += 2
        return score

    def _project_row_matches_current_sample(self, row: Mapping[str, Any]) -> bool:
        composition = _normalized_token(self.edit_name_composition.text())
        microwire = _normalized_microwire_token(self.edit_name_wire.text())
        specimen = _normalized_token(self.edit_name_specimen.text())
        row_composition = _normalized_token(row.get("Composition"))
        row_microwire = _normalized_microwire_token(_project_row_value(row, PROJECT_ROW_MICROWIRE_KEYS))
        row_specimen = _normalized_token(_project_row_value(row, PROJECT_ROW_SPECIMEN_KEYS))
        if microwire:
            return row_microwire == microwire and (not composition or row_composition == composition)
        if composition and specimen:
            return row_composition == composition and row_specimen == specimen
        return False

    def _find_project_sample(
        self,
        payload: Any,
        path: Path,
        *,
        require_current_sample_match: bool = False,
    ) -> ProjectImportResult | None:
        rows_by_section: list[tuple[str, list[Any]]] = []
        if isinstance(payload, Mapping):
            sections = payload.get("sections", {})
            if isinstance(sections, Mapping):
                preferred_sections = ("microscope", "assemble", "shape_memory_stress_strain")
                for section_name in preferred_sections:
                    section_payload = sections.get(section_name)
                    if not isinstance(section_payload, Mapping):
                        continue
                    rows = section_payload.get("rows")
                    if isinstance(rows, list):
                        rows_by_section.append((section_name, rows))
            top_level_rows = payload.get("rows")
            if isinstance(top_level_rows, list):
                rows_by_section.append(("rows", top_level_rows))
        elif isinstance(payload, list):
            rows_by_section.append(("rows", payload))
        if not rows_by_section:
            return None
        best_score = -1
        best_match: ProjectImportResult | None = None
        for section_name, rows in rows_by_section:
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                if require_current_sample_match and not self._project_row_matches_current_sample(row):
                    continue
                score = self._project_match_score(row)
                if score < 0:
                    continue
                diameter_um = _safe_float(_project_row_value(row, PROJECT_ROW_DIAMETER_KEYS))
                current_mA = _safe_float(_project_row_value(row, PROJECT_ROW_CURRENT_KEYS))
                if score > best_score:
                    best_score = score
                    best_match = ProjectImportResult(
                        path=path,
                        section=section_name,
                        diameter_mm=None if diameter_um is None else diameter_um / 1000.0,
                        current_mA=current_mA,
                        matched_row=dict(row),
                    )
        return best_match if best_score >= 0 else None

    def _apply_project_match(
        self,
        match: ProjectImportResult,
        *,
        update_identity: bool,
        quiet: bool = False,
    ) -> None:
        self._builder_project_path = match.path
        self._builder_project_match = match
        row = match.matched_row
        self._builder_import_in_progress = True
        try:
            if update_identity and row.get("Composition"):
                self.edit_name_composition.setText(str(row.get("Composition")))
            microwire_value = _project_row_value(row, PROJECT_ROW_MICROWIRE_KEYS)
            if update_identity and microwire_value:
                self.edit_name_wire.setText(MicrowireLineEdit.to_display_text(microwire_value) or str(microwire_value))
            specimen_value = _project_row_value(row, PROJECT_ROW_SPECIMEN_KEYS)
            if update_identity and specimen_value:
                self.edit_name_specimen.setText(str(specimen_value))
            if match.diameter_mm is not None:
                self.spin_diameter.setValue(match.diameter_mm)
                self._mark_diameter_imported(True)
            else:
                self._mark_diameter_imported(False)
            if match.current_mA is not None:
                self.spin_current_sweep_end_mA.setValue(match.current_mA)
        finally:
            self._builder_import_in_progress = False
        self.label_project_status.setText(
            f"Imported {match.path.name} -> section {match.section}, diameter "
            f"{'-' if match.diameter_mm is None else _format_compact_unit(match.diameter_mm * 1000.0, 'um', decimals=3)}"
            f"{'' if match.current_mA is None else f', current {match.current_mA:.2f} mA'}."
        )
        if not quiet:
            self._sync_auto_name_fields()
        else:
            self._refresh_recipe_sample_label()
            self._refresh_equivalent_labels()

    def _auto_import_builder_project_if_possible(self, *, update_identity: bool = False, quiet: bool = True) -> bool:
        if self._builder_import_in_progress:
            return False
        path_text = self.edit_project_path.text().strip()
        if not path_text:
            self._mark_diameter_imported(False)
            return False
        path = Path(path_text)
        self._builder_project_path = path
        if not path.exists():
            self._mark_diameter_imported(False)
            self.label_project_status.setText("Builder project path is saved, but the file was not found.")
            return False
        try:
            payload = self._read_builder_project_payload(path)
        except Exception as exc:
            self._mark_diameter_imported(False)
            self.label_project_status.setText(f"Failed to read saved project file: {exc}")
            return False
        match = self._find_project_sample(payload, path, require_current_sample_match=True)
        if match is None:
            self._mark_diameter_imported(False)
            self.label_project_status.setText(
                "Project loaded, but no matching sample row was found from the current naming fields."
            )
            return False
        self._apply_project_match(match, update_identity=update_identity, quiet=quiet)
        return True

    def _import_builder_project(self) -> None:
        path = Path(self.edit_project_path.text().strip())
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Choose a valid .pydpj file first.")
            return
        try:
            payload = self._read_builder_project_payload(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to read project file: {exc}")
            return
        match = self._find_project_sample(payload, path)
        if match is None:
            self.label_project_status.setText(
                "Project loaded, but no matching sample row was found from the current naming fields."
            )
            self._mark_diameter_imported(False)
            return
        self._apply_project_match(match, update_identity=True)

    def _toggle_scale_connection(self) -> None:
        if self._scale_thread is not None:
            self._disconnect_scale()
        else:
            self._connect_scale()

    def _connect_scale(self, checked: bool = False, *, show_errors: bool = True) -> bool:
        port_name = str(self.combo_scale_port.currentData() or "").strip()
        if not port_name:
            if show_errors:
                QtWidgets.QMessageBox.warning(self, APP_NAME, "Select a scale serial port first.")
            return False
        baudrate = int(self.combo_scale_baud.currentText())
        worker = ScaleWorker(
            port_name=port_name,
            baudrate=baudrate,
            poll_interval_ms=int(self.spin_scale_interval.value()),
            request_command=self.edit_scale_request.text(),
            request_terminator=self.edit_scale_terminator.text(),
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.measurement_received.connect(
            self._handle_scale_measurement,
            QtCore.Qt.ConnectionType.DirectConnection,
        )
        worker.status_changed.connect(self._handle_scale_status)
        worker.error_occurred.connect(self._handle_scale_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._handle_scale_thread_finished)
        self._scale_worker = worker
        self._scale_thread = thread
        self._scale_connected_at_s = time.time()
        self._scale_no_data_hint_emitted = False
        thread.start()
        self.button_scale_connect.setText("Disconnect scale")
        self._scale_hint_timer.start(SCALE_NO_DATA_HINT_DELAY_MS)
        return True

    def _disconnect_scale(self) -> None:
        worker = self._scale_worker
        thread = self._scale_thread
        self._scale_worker = None
        self._scale_thread = None
        self._scale_connected_at_s = None
        self._scale_no_data_hint_emitted = False
        self._scale_hint_timer.stop()
        if worker is not None:
            worker.stop()
        if thread is not None:
            thread.quit()
            thread.wait(1500)
        self.button_scale_connect.setText("Connect scale")

    def _handle_scale_thread_finished(self) -> None:
        self.button_scale_connect.setText("Connect scale")
        self._scale_hint_timer.stop()
        self._refresh_live_labels()

    def _handle_scale_measurement(self, value_g: float, raw_text: str, timestamp_s: float) -> None:
        with self._scale_state_lock:
            self._latest_scale_value_g = value_g
            self._latest_scale_text = raw_text
            self._latest_scale_timestamp = timestamp_s
            self._restore_default_zero_load_reference_if_real_grams(value_g)
            sample = self._scale_signal_buffer.add_sample(
                timestamp_s=timestamp_s,
                raw_g=value_g,
                applied_load_g=self._effective_load_from_raw_g(value_g),
                raw_text=raw_text,
            )
            self._write_raw_scale_sample(sample)
            self._scale_no_data_hint_emitted = True
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._handle_scale_measurement_ui_update)
            return
        self._handle_scale_measurement_ui_update()

    def _handle_scale_measurement_ui_update(self) -> None:
        if self._scale_hint_timer.isActive():
            self._scale_hint_timer.stop()
        self._refresh_live_labels()

    def _handle_scale_status(self, message: str) -> None:
        self._log(message)
        self._refresh_live_labels()

    def _handle_scale_error(self, message: str) -> None:
        self._log(message)
        self.label_scale_raw.setText(f"Raw line: {message}")
        self._refresh_live_labels()

    def _query_scale_now(
        self,
        *,
        port_name: str,
        baudrate: int,
        request_command: str | None = None,
        terminator: str | None = None,
    ) -> tuple[float | None, str]:
        if serial is None:
            raise RuntimeError("pyserial is not available.")
        request_text = self.edit_scale_request.text() if request_command is None else request_command
        terminator_text = self.edit_scale_terminator.text() if terminator is None else terminator
        payload = _decode_escape_text(request_text) + _decode_escape_text(terminator_text)
        with serial.Serial(port_name, baudrate=baudrate, timeout=0.4, write_timeout=0.4) as port:
            port.reset_input_buffer()
            port.reset_output_buffer()
            port.rts = False
            port.dtr = False
            time.sleep(0.08)
            if payload:
                port.write(payload)
                port.flush()
            raw_text = port.readline().decode("utf-8", errors="ignore").strip()
        return _parse_first_float(raw_text), raw_text

    def _zero_load_scale_reference_g(self) -> float:
        run_reference_g = getattr(self, "_run_zero_load_scale_g", None)
        if run_reference_g is not None:
            return float(run_reference_g)
        if not self._is_ui_thread():
            return float(self._cached_zero_load_scale_g)
        if hasattr(self, "spin_zero_load_scale_g"):
            return float(self.spin_zero_load_scale_g.value())
        return DEFAULT_ZERO_LOAD_SCALE_G

    def _configured_zero_load_scale_reference_g(self) -> float:
        if hasattr(self, "spin_zero_load_scale_g"):
            return float(self.spin_zero_load_scale_g.value())
        return DEFAULT_ZERO_LOAD_SCALE_G

    def _set_run_zero_load_scale_reference(self, value_g: float, *, reason: str) -> None:
        self._run_zero_load_scale_g = float(value_g)
        self._load_offset_g = 0.0
        self._refresh_live_labels()
        if reason:
            self._log(f"Run zero-load scale reference set to {float(value_g):.5f} g ({reason}).")

    def _clear_run_zero_load_scale_reference(self) -> None:
        self._run_zero_load_scale_g = None
        self._refresh_scale_reference_cache()
        self._refresh_live_labels()

    def _refresh_scale_reference_cache(self) -> None:
        if hasattr(self, "check_tension_load_positive"):
            self._cached_tension_decreases_scale_reading = self.check_tension_load_positive.isChecked()
        if hasattr(self, "spin_zero_load_scale_g"):
            self._cached_zero_load_scale_g = float(self.spin_zero_load_scale_g.value())

    def _handle_scale_reference_setting_changed(self, _value: object | None = None) -> None:
        self._refresh_scale_reference_cache()
        self._refresh_live_labels()

    def _handle_zero_load_scale_changed(self, _value: float) -> None:
        self._run_zero_load_scale_g = None
        self._refresh_scale_reference_cache()
        self._refresh_live_labels()

    def _restore_default_zero_load_reference_if_real_grams(self, raw_g: float) -> bool:
        if not self._is_ui_thread():
            return False
        if not hasattr(self, "spin_zero_load_scale_g") or not hasattr(self, "check_tension_load_positive"):
            return False
        if not self.check_tension_load_positive.isChecked():
            return False
        if self._run_zero_load_scale_g is not None:
            return False
        current_reference_g = float(self.spin_zero_load_scale_g.value())
        if abs(current_reference_g) > 0.01:
            return False
        if float(raw_g) < DEFAULT_ZERO_LOAD_SCALE_G * 0.5:
            return False
        self.spin_zero_load_scale_g.setValue(DEFAULT_ZERO_LOAD_SCALE_G)
        self._load_offset_g = 0.0
        self._refresh_scale_reference_cache()
        self._log(
            "Zero-load scale reference restored to "
            f"{DEFAULT_ZERO_LOAD_SCALE_G:.5f} g because the balance is reporting real grams "
            f"({float(raw_g):.5f} g) while the saved reference was 0 g."
        )
        return True

    def _capture_zero_load_scale_reference(self) -> bool:
        if not self._has_fresh_scale_reading():
            QtWidgets.QMessageBox.warning(
                self,
                APP_NAME,
                "No fresh scale reading is available for the zero-load reference.",
            )
            self._log("Zero-load reference capture failed because scale feedback is stale.")
            return False
        self.spin_zero_load_scale_g.setValue(float(self._latest_scale_value_g))
        self._load_offset_g = 0.0
        self._refresh_live_labels()
        self._log(
            f"Zero-load scale reference set to {self.spin_zero_load_scale_g.value():.5f} g "
            "from the current raw scale reading."
        )
        return True

    def _tare_scale(self) -> None:
        signed_load = self._load_sign() * (self._latest_scale_value_g - self._zero_load_scale_reference_g())
        self._load_offset_g = -signed_load
        self._refresh_live_labels()
        self._log(f"Diagnostic software load offset set to {self._load_offset_g:+.5f} g.")

    def _tare_scale_hardware(self) -> bool:
        port_name = str(self.combo_scale_port.currentData() or "").strip()
        if not port_name:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Select a scale serial port first.")
            return False
        if serial is None:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "pyserial is not available.")
            return False
        was_connected = self._scale_thread is not None
        baudrate = int(self.combo_scale_baud.currentText())
        if was_connected:
            self._disconnect_scale()
        try:
            with serial.Serial(port_name, baudrate=baudrate, timeout=0.4, write_timeout=0.4) as port:
                port.reset_input_buffer()
                port.reset_output_buffer()
                port.rts = False
                port.dtr = False
                time.sleep(0.08)
                port.write(b"\x1bt")
                port.flush()
                time.sleep(0.25)
            value_g, raw_text = self._query_scale_now(port_name=port_name, baudrate=baudrate)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Hardware tare failed: {exc}")
            return False
        finally:
            if was_connected:
                self._connect_scale()
        self._load_offset_g = 0.0
        if value_g is not None:
            self._latest_scale_value_g = value_g
        self._latest_scale_text = raw_text or "tare command sent"
        self._latest_scale_timestamp = time.time()
        self._refresh_live_labels()
        self._log(
            "Diagnostic hardware tare command sent to the scale; zero-load reference was left unchanged."
            + (f" Current raw reading: {raw_text}." if raw_text else "")
        )
        return True

    def _apply_gng_scale_preset(self) -> None:
        if self.combo_scale_baud.findText("600") >= 0:
            self.combo_scale_baud.setCurrentText("600")
        self.edit_scale_request.setText("\\x1bp")
        self.edit_scale_terminator.setText("")
        self._log("Applied G&G E-series scale preset: 600 baud, ESC+p request, no extra terminator.")

    def _build_sample_name(self) -> str:
        wire_display = MicrowireLineEdit.to_display_text(self.edit_name_wire.text()) or self.edit_name_wire.text().strip()
        parts = [
            self.edit_name_composition.text().strip(),
            wire_display,
            self.edit_name_specimen.text().strip(),
            " ".join(self.edit_name_condition.text().split()),
        ]
        return " ".join(part for part in parts if part)

    def _build_log_name_label(self, sample_name: str) -> str:
        condition = " ".join(self.edit_name_condition.text().split())
        parts = [
            self.edit_name_composition.text().strip(),
            MicrowireLineEdit.to_filename_token(self.edit_name_wire.text()) or self.edit_name_wire.text().strip(),
            self.edit_name_specimen.text().strip(),
            condition,
        ]
        log_label = " ".join(part for part in parts if part) or sample_name
        if str(self.combo_recipe_mode.currentData() or "") == "distribution":
            log_label = f"{log_label} {self._distribution_log_suffix()}".strip()
        return log_label

    def _recipe_filename_token(self) -> str:
        mode = str(self.combo_recipe_mode.currentData() or "")
        return RECIPE_FILENAME_TOKENS.get(mode, "")

    def _log_name_with_recipe_token(self, log_label: str) -> str:
        token = self._recipe_filename_token()
        if not token:
            return log_label
        label = _clean_session_basename(log_label)
        token_label = _clean_session_basename(token)
        if not label or token_label in label.split():
            return label
        return f"{label} {token_label}".strip()

    def _distribution_log_suffix(self) -> str:
        basis = self._distribution_basis()
        basis_label = {
            HSW_BASIS_LOAD_G: "load",
            HSW_BASIS_STRESS_MPA: "stress",
            HSW_BASIS_STRAIN_PCT: "strain",
        }.get(basis, "distribution")
        start_token = f"{self.spin_distribution_start.value():.3f}".rstrip("0").rstrip(".")
        end_token = f"{self.spin_distribution_end.value():.3f}".rstrip("0").rstrip(".")
        step_token = f"{self.spin_distribution_step.value():.3f}".rstrip("0").rstrip(".")
        return f"hsw-{basis_label}-{start_token}-{end_token}-step{step_token}"

    def _apply_name_fields(self) -> None:
        built = self._build_sample_name()
        if built:
            self.edit_sample_name.setText(built)
            log_label = self._log_name_with_recipe_token(self._build_log_name_label(built))
            safe_name = re.sub(r'[<>:"/\\\\|?*]+', "_", log_label).strip(" .")
            self.edit_log_name.setText(safe_name or DEFAULT_LOG_BASENAME)
            self._log(f"Applied naming fields: {built}")

    def _sync_auto_name_fields(self) -> None:
        built = self._build_sample_name()
        if built:
            log_label = self._log_name_with_recipe_token(self._build_log_name_label(built))
            safe_name = re.sub(r'[<>:"/\\\\|?*]+', "_", log_label).strip(" .")
            safe_name = safe_name or DEFAULT_LOG_BASENAME
            current_sample_name = self.edit_sample_name.text().strip()
            current_log_name = self.edit_log_name.text().strip()
            if not current_sample_name or current_sample_name == self._last_auto_sample_name:
                self.edit_sample_name.setText(built)
                current_sample_name = built
            if (
                not current_log_name
                or current_log_name == DEFAULT_LOG_BASENAME
                or current_log_name == self._last_auto_log_name
            ):
                self.edit_log_name.setText(safe_name)
                current_log_name = safe_name
            self._last_auto_sample_name = built
            self._last_auto_log_name = safe_name
        self._refresh_recipe_sample_label()
        self._auto_import_builder_project_if_possible(update_identity=False, quiet=True)
        self._persist_settings_if_enabled()

    def _sync_stale_log_name_from_sample(self) -> None:
        sample_name = self.edit_sample_name.text().strip()
        if not sample_name:
            return
        desired = _clean_session_basename(self._log_name_with_recipe_token(self._build_log_name_label(sample_name)))
        current = _clean_session_basename(self.edit_log_name.text())
        if not desired or current == desired:
            return
        stale_values = {
            "",
            DEFAULT_LOG_BASENAME,
            _clean_session_basename(self._last_auto_log_name),
        }
        if current in stale_values:
            self.edit_log_name.setText(desired)
            self._last_auto_log_name = desired
            self._log(f"Output base filename synced to current sample: {desired}")
            return
        sample_tokens = [
            _clean_session_basename(self.edit_name_composition.text()),
            _clean_session_basename(MicrowireLineEdit.to_filename_token(self.edit_name_wire.text())),
            _clean_session_basename(self.edit_name_specimen.text()),
            _clean_session_basename(" ".join(self.edit_name_condition.text().split())),
        ]
        meaningful_tokens = [token for token in sample_tokens if len(token) >= 2]
        matched_tokens = [token for token in meaningful_tokens if token and token in current]
        required_matches = len(meaningful_tokens)
        if meaningful_tokens and len(matched_tokens) < required_matches:
            self.edit_log_name.setText(desired)
            self._last_auto_log_name = desired
            self._log(f"Stale output base filename replaced with current sample: {desired}")

    def _refresh_recipe_sample_label(self) -> None:
        if not hasattr(self, "label_recipe_sample"):
            return
        sample_name = self.edit_sample_name.text().strip()
        if not sample_name:
            sample_name = "(unnamed sample)"
        diameter_mm = float(self.spin_diameter.value()) if hasattr(self, "spin_diameter") else 0.0
        diameter_text = (
            f" | diameter {_format_compact_unit(diameter_mm * 1000.0, 'um', decimals=3)}"
            if diameter_mm > 0.0
            else " | diameter -"
        )
        self.label_recipe_sample.setText(f"Sample: {sample_name}{diameter_text}")

    def _load_equivalent_text(self, value_mpa: float, *, per_second: bool = False) -> str:
        load_g = load_g_from_stress_mpa(float(value_mpa), float(self.spin_diameter.value()))
        if load_g is None:
            return "-"
        unit = "g/s" if per_second else "g"
        return _format_compact_unit(load_g, unit, decimals=3)

    def _stress_equivalent_text(self, value_g: float, *, per_second: bool = False) -> str:
        stress_mpa = stress_mpa_from_load_g(float(value_g), float(self.spin_diameter.value()))
        if stress_mpa is None:
            return "-"
        unit = "MPa/s" if per_second else "MPa"
        return _format_compact_unit(stress_mpa, unit, decimals=4)

    def _current_density_text(self, current_mA: float, *, per_second: bool = False) -> str:
        diameter_mm = float(self.spin_diameter.value())
        if diameter_mm <= 0.0:
            return "-"
        area_mm2 = math.pi * (diameter_mm / 2.0) ** 2
        if area_mm2 <= 0.0:
            return "-"
        current_density_a_mm2 = (float(current_mA) / 1000.0) / area_mm2
        unit = "A/mm<sup>2</sup>/s" if per_second else "A/mm<sup>2</sup>"
        return _format_compact_unit(current_density_a_mm2, unit, decimals=3)

    def _target_equivalent_text(self, basis: str, value: float, *, per_second: bool = False) -> str:
        if basis == HSW_BASIS_STRESS_MPA:
            return self._load_equivalent_text(value, per_second=per_second)
        if basis == HSW_BASIS_LOAD_G:
            return self._stress_equivalent_text(value, per_second=per_second)
        return "-"

    def _setup_preload_ramp_rate_mpa_s(self) -> float:
        config = self._control_config()
        preload_stress_mpa = max(
            0.001,
            config.setup_preload_stress_mpa if config is not None else float(self.spin_setup_preload_stress_mpa.value()),
        )
        preload_duration_s = max(
            0.1,
            config.setup_preload_duration_s if config is not None else float(self.spin_setup_preload_duration_s.value()),
        )
        return preload_stress_mpa / preload_duration_s

    def _setup_slack_speed_mm_s(self) -> float:
        config = self._control_config()
        length_mm = max(0.001, config.initial_length_mm if config is not None else float(self.spin_initial_length.value()))
        strain_rate_pct_s = max(
            0.001,
            config.setup_slack_speed_strain_pct_s if config is not None else float(self.spin_setup_slack_speed_strain_pct_s.value()),
        )
        return max(self._minimum_held_speed_mm_s(), length_mm * strain_rate_pct_s / 100.0)

    def _setup_motion_speed_cap_mm_s(self) -> float:
        config = self._control_config()
        speed_mm_s = config.motion_speed_mm_s if config is not None else float(self.spin_motion_speed_mm_s.value())
        return max(self._minimum_held_speed_mm_s(), speed_mm_s)

    def _setup_return_duration_s(self) -> float:
        config = self._control_config()
        duration_s = config.setup_return_duration_s if config is not None else float(self.spin_setup_return_duration_s.value())
        return max(0.1, duration_s)

    def _setup_return_speed_for_distance_mm_s(
        self,
        distance_mm: float,
        *,
        duration_s: float | None = None,
    ) -> float:
        duration = self._setup_return_duration_s() if duration_s is None else max(0.1, float(duration_s))
        speed = abs(float(distance_mm)) / duration
        return max(self._minimum_held_speed_mm_s(), min(self._setup_motion_speed_cap_mm_s(), speed))

    def _setup_return_zero_speed_mm_s(self, basis: str | None, current_value: float | None) -> float:
        if self._setup_return_zero_speed_mm_s_value is not None:
            return self._setup_return_zero_speed_mm_s_value
        if current_value is None or basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return self._setup_motion_speed_cap_mm_s()
        current_load_g = self._basis_value_as_load_g(basis, current_value)
        stiffness = self._basis_sensitivity_per_mm(HSW_BASIS_LOAD_G)
        if (
            current_load_g is None
            or stiffness is None
            or not math.isfinite(float(stiffness))
            or abs(float(stiffness)) <= 0.0
        ):
            return self._setup_motion_speed_cap_mm_s()
        distance_mm = abs(float(current_load_g)) / abs(float(stiffness))
        config = self._control_config()
        length_mm = max(0.001, config.initial_length_mm if config is not None else float(self.spin_initial_length.value()))
        distance_floor_mm = length_mm * SETUP_RETURN_MIN_SPEED_STRAIN_PCT / 100.0
        distance_mm = max(distance_mm, distance_floor_mm)
        speed_mm_s = self._setup_return_speed_for_distance_mm_s(distance_mm)
        self._setup_return_zero_speed_mm_s_value = speed_mm_s
        return speed_mm_s

    def _refresh_equivalent_labels(self) -> None:
        if not hasattr(self, "label_setup_preload_stress_equiv"):
            return
        self.label_setup_preload_stress_equiv.setText(
            self._load_equivalent_text(float(self.spin_setup_preload_stress_mpa.value()))
        )
        setup_ramp_mpa_s = self._setup_preload_ramp_rate_mpa_s()
        self.label_setup_preload_ramp_equiv.setText(
            f"{_format_compact_unit(setup_ramp_mpa_s, 'MPa/s', decimals=4)}; "
            f"{self._load_equivalent_text(setup_ramp_mpa_s, per_second=True)}"
        )
        self.label_setup_preload_tolerance_equiv.setText(
            self._load_equivalent_text(float(self.spin_setup_preload_tolerance_mpa.value()))
        )
        self.label_setup_zero_tolerance_equiv.setText(
            self._stress_equivalent_text(float(self.spin_setup_zero_tolerance_g.value()))
        )
        for label, spinbox in (
            (self.label_calibration_start_load_equiv, self.spin_calibration_start_load_g),
            (self.label_calibration_end_load_equiv, self.spin_calibration_end_load_g),
            (self.label_calibration_load_step_equiv, self.spin_calibration_load_step_g),
            (self.label_calibration_tolerance_equiv, self.spin_calibration_tolerance_g),
        ):
            label.setText(self._stress_equivalent_text(float(spinbox.value())))

        distribution_basis = self._distribution_basis()
        for label, spinbox in (
            (self.label_distribution_start_equiv, self.spin_distribution_start),
            (self.label_distribution_end_equiv, self.spin_distribution_end),
            (self.label_distribution_step_equiv, self.spin_distribution_step),
            (self.label_distribution_tolerance_equiv, self.spin_distribution_tolerance),
        ):
            label.setText(self._target_equivalent_text(distribution_basis, float(spinbox.value())))

        current_basis = self._current_sweep_basis()
        self.label_current_first_overheating_target_equiv.setText(
            self._load_equivalent_text(float(self.spin_current_sweep_first_overheating_target_mpa.value()))
        )
        for label, spinbox in (
            (self.label_current_target_start_equiv, self.spin_current_sweep_target_start),
            (self.label_current_target_end_equiv, self.spin_current_sweep_target_end),
            (self.label_current_target_step_equiv, self.spin_current_sweep_target_step),
            (self.label_current_tolerance_equiv, self.spin_current_sweep_tolerance),
        ):
            label.setText(self._target_equivalent_text(current_basis, float(spinbox.value())))
        self.label_current_target_ramp_equiv.setText(
            self._target_equivalent_text(
                current_basis,
                float(self.spin_current_sweep_target_ramp_rate.value()),
                per_second=True,
            )
        )
        self.label_current_start_density.setText(
            self._current_density_text(float(self.spin_current_sweep_start_mA.value()))
        )
        self.label_current_end_density.setText(
            self._current_density_text(float(self.spin_current_sweep_end_mA.value()))
        )
        constant_basis = self._constant_current_start_basis()
        self.label_constant_current_start_equiv.setText(
            self._target_equivalent_text(constant_basis, float(self.spin_constant_current_start_target.value()))
        )
        self.label_constant_current_end_equiv.setText(
            self._target_equivalent_text(constant_basis, float(self.spin_constant_current_end_target.value()))
        )

    def _set_position_reference_now(self) -> None:
        self._position_reference_mm = self._effective_position_mm
        self._refresh_live_labels()
        self._log(f"Reference position set to the current specimen position ({self._position_reference_mm:.4f} mm).")

    def _selected_tic_step_mode(self) -> str:
        value = self.combo_tic_step_mode.currentData()
        normalized = normalize_tic_step_mode(value)
        if normalized is None:
            normalized = DEFAULT_TIC_STEP_MODE
        return normalized

    def _set_tic_step_mode_combo(self, step_mode: object) -> bool:
        normalized = normalize_tic_step_mode(step_mode)
        if normalized is None:
            return False
        index = self.combo_tic_step_mode.findData(normalized)
        if index < 0:
            return False
        blocker = QtCore.QSignalBlocker(self.combo_tic_step_mode)
        self.combo_tic_step_mode.setCurrentIndex(index)
        del blocker
        return True

    def _set_tic_units_per_mm(self, units_per_mm: float) -> None:
        units_per_mm = max(1.0, float(units_per_mm))
        blocker = QtCore.QSignalBlocker(self.spin_steps_per_mm)
        self.spin_steps_per_mm.setValue(units_per_mm)
        del blocker
        self._clamp_motion_resolution_controls()
        self._update_recipe_mode_ui()

    def _sync_tic_units_per_mm_from_full_steps(self, *_args: object, persist: bool = True) -> None:
        try:
            units_per_mm = tic_units_per_mm(
                float(self.spin_full_steps_per_mm.value()),
                self._selected_tic_step_mode(),
            )
        except Exception:
            return
        self._set_tic_units_per_mm(units_per_mm)
        self._refresh_tic_settings_summary()
        if persist:
            self._persist_settings_if_enabled()

    def _refresh_tic_settings_summary(self) -> None:
        if not hasattr(self, "label_tic_settings_summary"):
            return
        status_text = self._tic_status_text
        step_mode = _extract_status_value(status_text, "Step mode") if status_text else None
        if step_mode is None:
            step_mode = _tic_step_mode_label(self._selected_tic_step_mode())
        current_limit = _extract_status_value(status_text, "Current limit") if status_text else None
        max_speed = _extract_status_value(status_text, "Max speed") if status_text else None
        max_accel = _extract_status_value(status_text, "Max acceleration") if status_text else None
        max_decel = _extract_status_value(status_text, "Max deceleration") if status_text else None
        units_per_mm = max(1.0, float(self.spin_steps_per_mm.value()))
        speed_detail = ""
        if max_speed:
            max_speed_units = _extract_first_int(max_speed)
            if max_speed_units is not None:
                speed_detail = f" ({max_speed_units / 10000.0 / units_per_mm:.4g} mm/s)"
        parts = [
            f"step mode {_tic_step_mode_label(step_mode)}",
            f"{float(self.spin_full_steps_per_mm.value()):.4g} full steps/mm",
            f"{units_per_mm:.4g} Tic units/mm",
        ]
        if max_speed:
            parts.append(f"max speed {max_speed}{speed_detail}")
        if max_accel:
            parts.append(f"max accel {max_accel}")
        if max_decel:
            parts.append(f"max decel {max_decel}")
        if current_limit:
            parts.append(f"current limit {current_limit}")
        self.label_tic_settings_summary.setText("Live Tic settings: " + " | ".join(parts))

    def _apply_tic_step_mode(self, _checked: bool = False, *, confirm: bool = True) -> bool:
        if self._automation_active or self._session_active or self._manual_jog_timer.isActive():
            QtWidgets.QMessageBox.warning(
                self,
                APP_NAME,
                "Stop the active session or manual jog before changing the Tic step mode.",
            )
            return False
        if self._motor_step_calibration_active:
            QtWidgets.QMessageBox.warning(
                self,
                APP_NAME,
                "Stop motor step calibration before changing the Tic step mode.",
            )
            return False
        requested_step_mode = self._selected_tic_step_mode()
        if confirm:
            self._refresh_tic_status()
            self._set_tic_step_mode_combo(requested_step_mode)
        old_units_per_mm = max(1.0, float(self.spin_steps_per_mm.value()))
        new_step_mode = requested_step_mode
        try:
            new_units_per_mm = tic_units_per_mm(float(self.spin_full_steps_per_mm.value()), new_step_mode)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, str(exc))
            return False
        current_steps = int(self._commanded_position_steps())
        physical_position_mm = current_steps / old_units_per_mm
        new_position_steps = int(round(physical_position_mm * new_units_per_mm))
        quantized_position_mm = new_position_steps / new_units_per_mm
        if confirm:
            reply = QtWidgets.QMessageBox.question(
                self,
                APP_NAME,
                (
                    f"Apply Tic step mode {_tic_step_mode_label(new_step_mode)}?\n\n"
                    f"Mini DMA will halt the motor, set the controller step mode, and rewrite the Tic "
                    f"current-position register from {current_steps} to {new_position_steps} so the "
                    "physical mm position remains continuous. This does not command a move."
                ),
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                return False
        try:
            dispatcher = self._build_tic_dispatcher()
            dispatcher.halt_and_hold()
            if not self._wait_for_tic_dispatcher(dispatcher, "halt", timeout_s=2.0):
                QtWidgets.QMessageBox.warning(self, APP_NAME, "Tic halt command did not finish cleanly.")
                return False
            self._build_tic_controller().set_step_mode(new_step_mode)
            dispatcher.set_current_position(new_position_steps)
            if not self._wait_for_tic_dispatcher(dispatcher, "step-mode-position", timeout_s=2.0):
                QtWidgets.QMessageBox.warning(
                    self,
                    APP_NAME,
                    "Tic current-position rewrite did not finish cleanly after changing step mode.",
                )
                return False
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to apply Tic step mode: {exc}")
            return False
        self._set_tic_units_per_mm(new_units_per_mm)
        self._current_position_steps = new_position_steps
        self._current_position_mm = quantized_position_mm
        self._effective_position_mm = quantized_position_mm
        self._last_effective_move_target_mm = quantized_position_mm
        self._last_move_target_mm = quantized_position_mm
        self._last_commanded_position_steps = new_position_steps
        self._manual_jog_uses_last_target = False
        self._last_move_direction = 0.0
        self._refresh_tic_settings_summary()
        self._refresh_live_labels()
        self._persist_settings_if_enabled()
        self._log(
            f"Applied Tic step mode {_tic_step_mode_label(new_step_mode)}: "
            f"{float(self.spin_full_steps_per_mm.value()):.4g} full steps/mm -> "
            f"{new_units_per_mm:.3f} Tic units/mm; position register {current_steps} -> {new_position_steps}."
        )
        if confirm:
            self._refresh_tic_status()
        return True

    def _motor_step_calibration_down_sign(self) -> int:
        return -1 if self._tension_motion_sign() > 0.0 else 1

    def _motor_step_calibration_speed_steps_per_s(self) -> float:
        increment_steps = max(1, abs(int(self.spin_motor_step_calibration_increment_steps.value())))
        return max(
            1.0,
            float(self.spin_motor_step_calibration_speed_mm_s.value()) * increment_steps,
        )

    def _motor_step_calibration_point(
        self,
        *,
        point_index: int,
        entered_displacement_mm: float,
        move_command_steps: int = 0,
        move_speed_steps_per_s: float = 0.0,
    ) -> MotorStepCalibrationPoint:
        return MotorStepCalibrationPoint(
            point_index=int(point_index),
            timestamp_utc=_utc_timestamp(),
            tic_position_steps=int(self._commanded_position_steps()),
            entered_displacement_mm=float(entered_displacement_mm),
            move_command_steps=int(move_command_steps),
            move_speed_steps_per_s=float(move_speed_steps_per_s),
        )

    def _next_motor_step_calibration_paths(self) -> tuple[Path, Path]:
        directory = Path(self.edit_log_dir.text().strip() or _default_download_dir()).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"motor_step_calibration_{_utc_filename_timestamp()}"
        for suffix in ("", *(f"_{index:02d}" for index in range(2, 10000))):
            csv_path = directory / f"{stem}{suffix}.csv"
            json_path = directory / f"{stem}{suffix}.json"
            if not csv_path.exists() and not json_path.exists():
                return csv_path, json_path
        raise RuntimeError("Could not find a free motor-step calibration filename.")

    def _write_motor_step_calibration_log(
        self,
        points: Sequence[MotorStepCalibrationPoint],
        report: Mapping[str, Any],
        *,
        move_increment_steps: int,
        move_speed_steps_per_s: float,
        applied_to_settings: bool,
    ) -> tuple[Path, Path]:
        if not points:
            raise ValueError("Cannot write a motor-step calibration log without points.")
        csv_path, json_path = self._next_motor_step_calibration_paths()
        baseline = points[0]
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MOTOR_STEP_CALIBRATION_CSV_FIELDNAMES)
            writer.writeheader()
            for point in points:
                relative_steps = int(point.tic_position_steps) - int(baseline.tic_position_steps)
                relative_mm = float(point.entered_displacement_mm) - float(baseline.entered_displacement_mm)
                if point.point_index == 0 or abs(relative_mm) <= 1e-12:
                    estimate_text = ""
                else:
                    estimate_text = f"{abs(relative_steps / relative_mm):.6f}"
                writer.writerow(
                    {
                        "point_index": point.point_index,
                        "timestamp_utc": point.timestamp_utc,
                        "tic_position_steps": point.tic_position_steps,
                        "relative_tic_steps": relative_steps,
                        "entered_displacement_mm": f"{point.entered_displacement_mm:.6f}",
                        "relative_displacement_mm": f"{relative_mm:.6f}",
                        "move_command_steps": point.move_command_steps,
                        "move_speed_steps_per_s": f"{point.move_speed_steps_per_s:.6f}",
                        "estimated_steps_per_mm_from_baseline": estimate_text,
                    }
                )

        step_mode = _extract_status_value(self._tic_status_text, "Step mode") if self._tic_status_text else None
        payload = {
            "created_utc": _utc_timestamp(),
            "tic_serial": self.edit_tic_serial.text().strip() or None,
            "tic_step_mode": step_mode,
            "start_position_steps": int(baseline.tic_position_steps),
            "move_increment_steps": int(move_increment_steps),
            "move_direction": "down",
            "move_speed_steps_per_s": float(move_speed_steps_per_s),
            "steps_per_mm_before": float(self.spin_steps_per_mm.value()),
            "applied_to_settings": bool(applied_to_settings),
            "points": [dict(point.__dict__) for point in points],
            "report": dict(report),
        }
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return csv_path, json_path

    def _update_motor_step_calibration_applied_flag(self, json_path: Path, applied: bool) -> None:
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload["applied_to_settings"] = bool(applied)
                payload["applied_utc"] = _utc_timestamp() if applied else None
                json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            self._log(f"Could not update calibration metadata apply flag: {exc}")

    def _apply_motor_step_calibration_report(self, report: Mapping[str, Any]) -> bool:
        value = report.get("recommended_steps_per_mm")
        if value is None:
            return False
        steps_per_mm = float(value)
        if not math.isfinite(steps_per_mm) or steps_per_mm <= 0.0:
            return False
        factor = tic_step_mode_factor(self._selected_tic_step_mode())
        if factor is not None and factor > 0:
            self.spin_full_steps_per_mm.setValue(steps_per_mm / float(factor))
        self._set_tic_units_per_mm(steps_per_mm)
        self._persist_settings_if_enabled()
        self._log(f"Applied motor step calibration: {steps_per_mm:.3f} Tic units/mm.")
        return True

    def _discard_motor_step_calibration_log(self, paths: Sequence[Path]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                self._log(f"Could not remove calibration log {path}: {exc}")

    def _request_stop_motor_step_calibration(self) -> None:
        self._motor_step_calibration_stop_requested = True
        if self._motor_step_calibration_status_label is not None:
            self._motor_step_calibration_status_label.setText(
                "Stop requested. Mini DMA will stop after the current prompt or move."
            )
        self._log("Motor step calibration stop requested.")

    def _show_motor_step_calibration_dialog(
        self,
        *,
        total_moves: int,
        signed_increment_steps: int,
        speed_steps_per_s: float,
    ) -> None:
        dialog = self._motor_step_calibration_dialog
        if dialog is None or dialog.isHidden():
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("Mini DMA Motor Step Calibration")
            dialog.resize(620, 420)
            layout = QtWidgets.QVBoxLayout(dialog)
            status_label = QtWidgets.QLabel("Preparing motor step calibration baseline...", dialog)
            status_label.setWordWrap(True)
            layout.addWidget(status_label)
            progress = QtWidgets.QProgressBar(dialog)
            progress.setRange(0, max(1, int(total_moves) * 100))
            progress.setValue(0)
            progress.setTextVisible(True)
            progress.setFormat("Motor calibration: baseline")
            layout.addWidget(progress)
            detail_label = QtWidgets.QLabel(dialog)
            detail_label.setWordWrap(True)
            layout.addWidget(detail_label)
            points_view = QtWidgets.QPlainTextEdit(dialog)
            points_view.setReadOnly(True)
            points_view.setMaximumBlockCount(200)
            points_view.setPlaceholderText("Accepted gauge readings will appear here.")
            layout.addWidget(points_view, stretch=1)
            note = QtWidgets.QLabel(
                "Keep this window visible while the external-gauge prompts are open. "
                "The calibration result is saved to CSV/JSON before anything is applied.",
                dialog,
            )
            note.setWordWrap(True)
            note.setStyleSheet("color: palette(mid);")
            layout.addWidget(note)
            button_row = QtWidgets.QHBoxLayout()
            button_row.addStretch(1)
            stop_button = QtWidgets.QPushButton("Stop after current step", dialog)
            stop_button.clicked.connect(self._request_stop_motor_step_calibration)
            button_row.addWidget(stop_button)
            layout.addLayout(button_row)
            self._motor_step_calibration_dialog = dialog
            self._motor_step_calibration_status_label = status_label
            self._motor_step_calibration_detail_label = detail_label
            self._motor_step_calibration_progress = progress
            self._motor_step_calibration_points_view = points_view
        self._motor_step_calibration_stop_requested = False
        self._update_motor_step_calibration_dialog(
            "Enter the external-gauge baseline reading.",
            completed_moves=0,
            total_moves=total_moves,
            detail=(
                f"Plan: {total_moves} move(s), {signed_increment_steps:+d} raw Tic steps per move, "
                f"{float(speed_steps_per_s):.3f} steps/s."
            ),
        )
        if self._motor_step_calibration_points_view is not None:
            self._motor_step_calibration_points_view.clear()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        QtWidgets.QApplication.processEvents()

    def _update_motor_step_calibration_dialog(
        self,
        message: str,
        *,
        completed_moves: int,
        total_moves: int,
        active_move_fraction: float = 0.0,
        detail: str | None = None,
    ) -> None:
        if self._motor_step_calibration_dialog is None:
            return
        total = max(1, int(total_moves))
        completed = max(0, min(total, int(completed_moves)))
        active_fraction = max(0.0, min(1.0, float(active_move_fraction)))
        progress_value = int(round((completed + active_fraction) * 100.0))
        progress_max = total * 100
        if self._motor_step_calibration_status_label is not None:
            self._motor_step_calibration_status_label.setText(message)
        if self._motor_step_calibration_detail_label is not None and detail is not None:
            self._motor_step_calibration_detail_label.setText(detail)
        if self._motor_step_calibration_progress is not None:
            self._motor_step_calibration_progress.setRange(0, progress_max)
            self._motor_step_calibration_progress.setValue(max(0, min(progress_value, progress_max)))
            self._motor_step_calibration_progress.setFormat(
                f"Motor calibration: {completed}/{total} move(s)"
            )
        QtWidgets.QApplication.processEvents()

    def _append_motor_step_calibration_dialog_point(self, point: MotorStepCalibrationPoint) -> None:
        if self._motor_step_calibration_points_view is None:
            return
        move_text = ""
        if int(point.move_command_steps) != 0:
            move_text = f", move {int(point.move_command_steps):+d} steps at {point.move_speed_steps_per_s:.3f} steps/s"
        self._motor_step_calibration_points_view.appendPlainText(
            f"Point {point.point_index}: {point.tic_position_steps} steps, "
            f"reading {point.entered_displacement_mm:.6f} mm{move_text}"
        )

    def _close_motor_step_calibration_dialog(self) -> None:
        if self._motor_step_calibration_dialog is not None:
            self._motor_step_calibration_dialog.close()
        self._motor_step_calibration_dialog = None
        self._motor_step_calibration_status_label = None
        self._motor_step_calibration_detail_label = None
        self._motor_step_calibration_progress = None
        self._motor_step_calibration_points_view = None
        self._motor_step_calibration_active = False
        self._motor_step_calibration_stop_requested = False

    def _offer_motor_step_calibration_result(
        self,
        report: Mapping[str, Any],
        *,
        csv_path: Path,
        json_path: Path,
    ) -> None:
        if report.get("status") != "ok":
            QtWidgets.QMessageBox.information(
                self,
                APP_NAME,
                "Motor step calibration log saved, but there was not enough displacement data to compute a fit.\n\n"
                f"CSV: {csv_path}\nJSON: {json_path}",
            )
            return

        recommended = float(report["recommended_steps_per_mm"])
        r2 = float(report.get("r2", 0.0))
        residual = float(report.get("max_residual_mm", 0.0))
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setText(f"Recommended motor calibration: {recommended:.3f} Tic units/mm")
        box.setInformativeText(
            f"Linearity R2: {r2:.5f}\n"
            f"Max residual: {residual:.6f} mm\n\n"
            f"CSV: {csv_path}\n"
            f"JSON: {json_path}\n\n"
            "The default choice keeps the log and does not change Mini DMA settings."
        )
        apply_button = box.addButton("Apply result", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        save_only_button = box.addButton("Save only", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        discard_button = box.addButton("Discard log", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(save_only_button)  # type: ignore[arg-type]
        box.exec()
        clicked = box.clickedButton()
        if clicked is apply_button and self._apply_motor_step_calibration_report(report):
            self._update_motor_step_calibration_applied_flag(json_path, True)
            return
        if clicked is discard_button:
            self._discard_motor_step_calibration_log((csv_path, json_path))
            self._log("Motor step calibration log discarded.")
            return
        self._log(
            f"Motor step calibration saved only: {recommended:.3f} Tic units/mm recommendation "
            f"({csv_path.name}, {json_path.name})."
        )

    def _wait_for_motor_step_calibration_move(
        self,
        expected_duration_s: float,
        *,
        point_index: int,
        total_moves: int,
        target_position_steps: int | None,
    ) -> bool:
        dispatcher = self._build_tic_dispatcher()
        if not self._wait_for_tic_dispatcher(
            dispatcher,
            "motor-step calibration move",
            timeout_s=max(2.0, min(30.0, float(expected_duration_s) + 2.0)),
        ):
            return False
        move_started_s = time.time()
        move_duration_s = max(0.0, float(expected_duration_s))
        deadline_s = move_started_s + move_duration_s + SERVO_MOTION_SETTLE_AFTER_MOVE_S
        while time.time() < deadline_s:
            elapsed_s = max(0.0, time.time() - move_started_s)
            move_fraction = 1.0 if move_duration_s <= 1e-9 else min(1.0, elapsed_s / move_duration_s)
            remaining_s = max(0.0, deadline_s - time.time())
            target_text = "-" if target_position_steps is None else str(target_position_steps)
            self._update_motor_step_calibration_dialog(
                f"Moving calibration point {point_index}/{total_moves}.",
                completed_moves=max(0, point_index - 1),
                total_moves=total_moves,
                active_move_fraction=move_fraction,
                detail=(
                    f"Confirmed position {self._current_position_steps} steps; "
                    f"commanded target {target_text} steps. "
                    f"Elapsed {elapsed_s:.1f} s, remaining about {remaining_s:.1f} s."
                ),
            )
            QtWidgets.QApplication.processEvents()
            time.sleep(min(0.05, max(0.0, deadline_s - time.time())))
        try:
            self._refresh_tic_status()
        except Exception as exc:
            self._log(f"Tic status refresh after calibration move failed: {exc}")
        self._update_motor_step_calibration_dialog(
            f"Move {point_index}/{total_moves} finished. Enter the external-gauge reading.",
            completed_moves=point_index,
            total_moves=total_moves,
            detail=f"Move complete at {self._commanded_position_steps()} commanded steps.",
        )
        return True

    def _run_motor_step_calibration(self) -> None:
        if self._automation_active or self._session_active:
            QtWidgets.QMessageBox.information(
                self,
                APP_NAME,
                "Stop the running recipe/session before starting motor step calibration.",
            )
            return
        self._stop_manual_jog()
        if not self._ensure_tic_ready_for_recipe():
            QtWidgets.QMessageBox.warning(
                self,
                APP_NAME,
                "Motor step calibration needs a reachable Tic controller with motor power ready.",
            )
            return

        increment_steps = max(1, abs(int(self.spin_motor_step_calibration_increment_steps.value())))
        move_count = max(1, int(self.spin_motor_step_calibration_moves.value()))
        speed_steps_per_s = self._motor_step_calibration_speed_steps_per_s()
        signed_increment_steps = self._motor_step_calibration_down_sign() * increment_steps
        self._log(
            f"Motor step calibration started: baseline plus {move_count} down move(s), "
            f"{signed_increment_steps:+d} raw Tic steps per move at {speed_steps_per_s:.3f} steps/s."
        )
        self._motor_step_calibration_active = True
        self._show_motor_step_calibration_dialog(
            total_moves=move_count,
            signed_increment_steps=signed_increment_steps,
            speed_steps_per_s=speed_steps_per_s,
        )

        baseline_mm, accepted = QtWidgets.QInputDialog.getDouble(
            self,
            APP_NAME,
            "External gauge baseline reading (mm):",
            0.0,
            -100000.0,
            100000.0,
            6,
        )
        if not accepted:
            self._log("Motor step calibration cancelled before baseline entry.")
            self._close_motor_step_calibration_dialog()
            self._motor_step_calibration_active = False
            if not self._automation_active and not self._manual_jog_timer.isActive():
                self._stop_tic_keepalive()
            return

        points = [
            self._motor_step_calibration_point(
                point_index=0,
                entered_displacement_mm=float(baseline_mm),
            )
        ]
        self._append_motor_step_calibration_dialog_point(points[0])
        previous_reading = float(baseline_mm)
        completed_all_moves = True
        for point_index in range(1, move_count + 1):
            if self._motor_step_calibration_stop_requested:
                completed_all_moves = False
                break
            self._log(f"Motor step calibration move {point_index}/{move_count}: moving down.")
            start_steps = self._commanded_position_steps()
            target_steps = start_steps + signed_increment_steps
            self._update_motor_step_calibration_dialog(
                f"Starting calibration move {point_index}/{move_count}.",
                completed_moves=point_index - 1,
                total_moves=move_count,
                detail=(
                    f"Commanding {signed_increment_steps:+d} raw Tic steps: "
                    f"{start_steps} -> {target_steps} steps at {speed_steps_per_s:.3f} steps/s."
                ),
            )
            if not self._move_relative_raw_tic_steps(
                signed_increment_steps,
                speed_steps_per_s=speed_steps_per_s,
            ):
                completed_all_moves = False
                break
            expected_duration_s = increment_steps / max(speed_steps_per_s, 1e-9)
            if not self._wait_for_motor_step_calibration_move(
                expected_duration_s,
                point_index=point_index,
                total_moves=move_count,
                target_position_steps=self._last_commanded_position_steps,
            ):
                completed_all_moves = False
                break
            reading_mm, accepted = QtWidgets.QInputDialog.getDouble(
                self,
                APP_NAME,
                f"External gauge reading after down move {point_index}/{move_count} (mm):",
                previous_reading,
                -100000.0,
                100000.0,
                6,
            )
            if not accepted:
                self._log("Motor step calibration stopped because gauge reading entry was cancelled.")
                completed_all_moves = False
                break
            previous_reading = float(reading_mm)
            points.append(
                self._motor_step_calibration_point(
                    point_index=point_index,
                    entered_displacement_mm=previous_reading,
                    move_command_steps=signed_increment_steps,
                    move_speed_steps_per_s=speed_steps_per_s,
                )
            )
            self._append_motor_step_calibration_dialog_point(points[-1])

        report = motor_step_calibration_report_from_points(points)
        csv_path, json_path = self._write_motor_step_calibration_log(
            points,
            report,
            move_increment_steps=signed_increment_steps,
            move_speed_steps_per_s=speed_steps_per_s,
            applied_to_settings=False,
        )
        if completed_all_moves and report.get("status") == "ok":
            self._log(
                "Motor step calibration fit ready: "
                f"{float(report['recommended_steps_per_mm']):.3f} Tic units/mm, "
                f"R2 {float(report.get('r2', 0.0)):.5f}, "
                f"max residual {float(report.get('max_residual_mm', 0.0)):.6f} mm."
            )
            self._update_motor_step_calibration_dialog(
                "Motor step calibration fit is ready.",
                completed_moves=move_count,
                total_moves=move_count,
                detail=(
                    f"Recommended {float(report['recommended_steps_per_mm']):.3f} Tic units/mm; "
                    f"R2 {float(report.get('r2', 0.0)):.5f}; "
                    f"max residual {float(report.get('max_residual_mm', 0.0)):.6f} mm. "
                    f"Saved CSV/JSON: {csv_path.name}, {json_path.name}."
                ),
            )
        else:
            self._log("Motor step calibration saved as partial/insufficient data for inspection.")
            self._update_motor_step_calibration_dialog(
                "Motor step calibration saved as partial/insufficient data.",
                completed_moves=max(0, len(points) - 1),
                total_moves=move_count,
                detail=f"Saved CSV/JSON: {csv_path.name}, {json_path.name}.",
            )
        self._offer_motor_step_calibration_result(report, csv_path=csv_path, json_path=json_path)
        self._close_motor_step_calibration_dialog()
        self._motor_step_calibration_active = False
        if not self._automation_active and not self._manual_jog_timer.isActive():
            self._stop_tic_keepalive()

    def _apply_preload_length_result(
        self,
        *,
        measured_length_mm: float,
        preload_position_mm: float,
        zero_position_mm: float,
    ) -> float:
        tensile_preload_mm = self._tensile_position_mm(preload_position_mm)
        tensile_zero_mm = self._tensile_position_mm(zero_position_mm)
        preload_extension_mm = tensile_preload_mm - tensile_zero_mm
        l0_mm = float(measured_length_mm) - preload_extension_mm
        if l0_mm <= 0.0:
            raise ValueError(
                "Computed l0 is not positive. Check the measured length and motion direction convention."
            )
        self.spin_initial_length.setValue(l0_mm)
        self._effective_position_mm = float(zero_position_mm)
        self._last_effective_move_target_mm = self._effective_position_mm
        self._position_reference_mm = float(zero_position_mm)
        self._preload_reference_armed = False
        self._preload_trigger_elapsed_s = None
        self._refresh_live_labels()
        self._log(
            f"Computed l0 = {_format_compact_unit(l0_mm, 'mm', decimals=4)} from "
            f"measured mounted length {_format_compact_unit(measured_length_mm, 'mm', decimals=4)} "
            f"and stage return {_format_compact_unit(preload_extension_mm, 'mm', decimals=4)}."
        )
        return l0_mm

    def _setup_unload_candidate_points(self) -> list[tuple[float, float]]:
        start_index = max(0, int(self._setup_return_zero_start_point_index))
        return_points = self._length_setup_points[start_index:]
        candidates: list[tuple[float, float]] = []
        for point in return_points:
            stress_mpa = point.stress_mpa
            if stress_mpa is None:
                stress_mpa = stress_mpa_from_load_g(point.load_g, float(self.spin_diameter.value()))
            if stress_mpa is None or not math.isfinite(float(stress_mpa)):
                continue
            stress_value = abs(float(stress_mpa))
            candidates.append((float(point.raw_position_mm), stress_value))
        return candidates

    def _setup_unload_baseline_fit(self) -> SetupUnloadBaselineFit | None:
        candidates = self._setup_unload_candidate_points()
        if len(candidates) < SETUP_UNLOAD_BASELINE_MIN_POINTS:
            return None
        max_stress = max(stress for _position, stress in candidates)
        stress_floor = max(
            SETUP_UNLOAD_BASELINE_MIN_STRESS_MPA,
            max_stress * SETUP_UNLOAD_BASELINE_MIN_FRACTION,
        )
        fit_points = [(position, stress) for position, stress in candidates if stress >= stress_floor]
        if len(fit_points) < SETUP_UNLOAD_BASELINE_MIN_POINTS:
            fit_points = candidates[:SETUP_UNLOAD_BASELINE_MIN_POINTS]
        if len(fit_points) < SETUP_UNLOAD_BASELINE_MIN_POINTS:
            return None
        n = float(len(fit_points))
        sum_x = sum(position for position, _stress in fit_points)
        sum_y = sum(stress for _position, stress in fit_points)
        sum_xx = sum(position * position for position, _stress in fit_points)
        sum_xy = sum(position * stress for position, stress in fit_points)
        denominator = n * sum_xx - sum_x * sum_x
        if abs(denominator) <= 1e-12:
            return None
        slope = (n * sum_xy - sum_x * sum_y) / denominator
        if not math.isfinite(slope) or abs(slope) <= 1e-12:
            return None
        intercept = (sum_y - slope * sum_x) / n
        zero_position_mm = -intercept / slope
        if not math.isfinite(zero_position_mm):
            return None
        min_position = min(position for position, _stress in candidates)
        max_position = max(position for position, _stress in candidates)
        margin_mm = max(self._motor_step_mm() * 4.0, abs(max_position - min_position) * 0.25)
        if zero_position_mm < min_position - margin_mm or zero_position_mm > max_position + margin_mm:
            return None
        mean_y = sum_y / n
        residual_sum = sum((stress - (slope * position + intercept)) ** 2 for position, stress in fit_points)
        total_sum = sum((stress - mean_y) ** 2 for _position, stress in fit_points)
        r_squared = 1.0 if total_sum <= 1e-12 else 1.0 - residual_sum / total_sum
        return SetupUnloadBaselineFit(
            zero_position_mm=float(zero_position_mm),
            slope_mpa_per_mm=float(slope),
            intercept_mpa=float(intercept),
            r_squared=float(r_squared),
            fit_point_count=len(fit_points),
            max_stress_mpa=float(max_stress),
            stress_floor_mpa=float(stress_floor),
        )

    def _fit_setup_unload_zero_position_mm(self) -> float | None:
        fit = self._setup_unload_baseline_fit()
        if fit is None:
            return None
        self._log(
            "Computed setup l0 zero position from linear unload fit: "
            f"{_format_compact_unit(fit.zero_position_mm, 'mm')} "
            f"using {fit.fit_point_count} points."
        )
        return fit.zero_position_mm

    def _setup_unload_recent_slope_mpa_per_mm(
        self,
        candidates: Sequence[tuple[float, float]],
    ) -> float | None:
        if len(candidates) < SETUP_UNLOAD_SLACK_RECENT_POINTS:
            return None
        recent = list(candidates[-SETUP_UNLOAD_SLACK_RECENT_POINTS:])
        n = float(len(recent))
        sum_x = sum(position for position, _stress in recent)
        sum_y = sum(stress for _position, stress in recent)
        sum_xx = sum(position * position for position, _stress in recent)
        sum_xy = sum(position * stress for position, stress in recent)
        denominator = n * sum_xx - sum_x * sum_x
        if abs(denominator) <= 1e-12:
            return None
        slope = (n * sum_xy - sum_x * sum_y) / denominator
        if not math.isfinite(slope):
            return None
        return float(slope)

    def _maybe_start_setup_unload_baseline_fallback(self) -> bool:
        if self._automation_step_note != "setup_return_zero":
            return False
        if self._setup_zero_fallback_return_position_mm is not None:
            return True
        if self._setup_zero_position_mm is not None:
            return False
        fit = self._setup_unload_baseline_fit()
        if fit is None or fit.r_squared < 0.90:
            return False
        candidates = self._setup_unload_candidate_points()
        if len(candidates) < SETUP_UNLOAD_BASELINE_MIN_POINTS + SETUP_UNLOAD_SLACK_RECENT_POINTS:
            return False
        recent = candidates[-SETUP_UNLOAD_SLACK_RECENT_POINTS:]
        recent_max_stress = max(stress for _position, stress in recent)
        slack_stress_limit = max(
            SETUP_UNLOAD_SLACK_MAX_STRESS_MPA,
            fit.max_stress_mpa * SETUP_UNLOAD_SLACK_MAX_STRESS_FRACTION,
        )
        if recent_max_stress > slack_stress_limit:
            return False
        recent_slope = self._setup_unload_recent_slope_mpa_per_mm(candidates)
        if recent_slope is None:
            return False
        if abs(recent_slope) > abs(fit.slope_mpa_per_mm) * SETUP_UNLOAD_SLACK_SLOPE_FRACTION:
            return False

        current_position_mm = float(self._current_position_mm)
        zero_position_mm = fit.zero_position_mm
        min_position = min(position for position, _stress in candidates)
        max_position = max(position for position, _stress in candidates)
        margin_mm = max(self._motor_step_mm() * 4.0, abs(max_position - min_position) * 0.25)
        if zero_position_mm < min_position - margin_mm or zero_position_mm > max_position + margin_mm:
            return False

        self._setup_zero_position_mm = zero_position_mm
        self._setup_zero_fallback_return_position_mm = zero_position_mm
        self._setup_zero_fallback_reason = "linear_unload_slack"
        self._log(
            "Detected setup unload slack onset: recent load/stress slope collapsed "
            f"to {abs(recent_slope):.4g} MPa/mm from the linear-fit "
            f"{abs(fit.slope_mpa_per_mm):.4g} MPa/mm stiffness; using "
            f"{_format_compact_unit(zero_position_mm, 'mm')} as the zero-stress l0 position "
            "and returning there instead of driving farther into slack."
        )
        if not self._move_to_position_mm(
            zero_position_mm,
            speed_mm_s=self._setup_return_speed_for_distance_mm_s(abs(current_position_mm - zero_position_mm)),
        ):
            if abs(current_position_mm - zero_position_mm) <= self._motor_step_mm():
                self._setup_zero_fallback_return_position_mm = None
                return True
            return False
        return True

    def _tension_motion_sign(self) -> float:
        config = self._control_config()
        positive_motion_is_tension = (
            config.positive_motion_is_tension
            if config is not None
            else self.check_positive_motion_is_tension.isChecked()
        )
        return 1.0 if positive_motion_is_tension else -1.0

    def _tensile_position_mm(self, raw_position_mm: float) -> float:
        return self._tension_motion_sign() * float(raw_position_mm)

    def _tensile_displacement_mm(self, raw_position_mm: float) -> float:
        return self._tension_motion_sign() * (float(raw_position_mm) - self._position_reference_mm)

    def _strain_percent_for_position(self, raw_position_mm: float) -> float | None:
        config = self._control_config()
        return strain_percent(
            self._tensile_position_mm(raw_position_mm),
            config.initial_length_mm if config is not None else float(self.spin_initial_length.value()),
            self._tensile_position_mm(self._position_reference_mm),
        )

    def _current_relative_position_and_strain(
        self,
        specimen_position_mm: float,
    ) -> tuple[float | None, float | None, float | None, float | None]:
        zero_position_mm = self._active_constant_current_zero_position_mm
        if zero_position_mm is None:
            return None, None, None, None
        relative_position_mm = self._tension_motion_sign() * (float(specimen_position_mm) - float(zero_position_mm))
        config = self._control_config()
        length_mm = config.initial_length_mm if config is not None else float(self.spin_initial_length.value())
        current_l0_mm = float(length_mm) + self._tension_motion_sign() * (
            float(zero_position_mm) - float(self._position_reference_mm)
        )
        if not math.isfinite(float(current_l0_mm)) or float(current_l0_mm) <= 0.0:
            return float(zero_position_mm), None, relative_position_mm, None
        return (
            float(zero_position_mm),
            current_l0_mm,
            relative_position_mm,
            100.0 * relative_position_mm / current_l0_mm,
        )

    def _motor_step_mm(self) -> float:
        config = self._control_config()
        steps_per_mm = config.steps_per_mm if config is not None else float(self.spin_steps_per_mm.value())
        return 1.0 / max(1.0, steps_per_mm)

    def _quantize_backlash_mm(self, backlash_mm: float) -> float:
        step_mm = self._motor_step_mm()
        if step_mm <= 0.0:
            return max(0.0, float(backlash_mm))
        return max(0.0, round(float(backlash_mm) / step_mm) * step_mm)

    def _current_effective_tensile_position_mm(self) -> float:
        return self._tensile_position_mm(self._measurement_effective_position_mm())

    def _minimum_held_speed_mm_s(self) -> float:
        return self._motor_step_mm()

    def _strain_pct_to_stage_mm(self, strain_pct: float) -> float:
        config = self._control_config()
        length_mm = max(0.001, config.initial_length_mm if config is not None else float(self.spin_initial_length.value()))
        return abs(float(strain_pct)) * length_mm / 100.0

    def _current_sweep_max_correction_mm(self) -> float:
        config = self._control_config()
        strain_pct = (
            config.current_sweep_max_correction_strain_pct
            if config is not None
            else SERVO_CURRENT_SWEEP_MAX_CORRECTION_STRAIN_PCT
        )
        if config is None and hasattr(self, "spin_current_sweep_max_correction_strain_pct"):
            strain_pct = float(self.spin_current_sweep_max_correction_strain_pct.value())
        return max(self._motor_step_mm(), self._strain_pct_to_stage_mm(strain_pct))

    def _current_sweep_max_correction_stress_mpa(self) -> float:
        config = self._control_config()
        value = (
            config.current_sweep_max_correction_stress_mpa
            if config is not None
            else SERVO_CURRENT_SWEEP_MAX_CORRECTION_STRESS_MPA
        )
        if config is None and hasattr(self, "spin_current_sweep_max_correction_stress_mpa"):
            value = float(self.spin_current_sweep_max_correction_stress_mpa.value())
        return max(0.001, abs(float(value)))

    def _current_sweep_hold_correction_stress_mpa(self) -> float:
        config = self._control_config()
        value = (
            config.current_sweep_hold_correction_stress_mpa
            if config is not None
            else SERVO_CURRENT_SWEEP_HOLD_MAX_CORRECTION_STRESS_MPA
        )
        if config is None and hasattr(self, "spin_current_sweep_hold_correction_stress_mpa"):
            value = float(self.spin_current_sweep_hold_correction_stress_mpa.value())
        return max(0.001, abs(float(value)))

    def _current_sweep_mid_correction_stress_mpa(self) -> float:
        config = self._control_config()
        value = (
            config.current_sweep_mid_correction_stress_mpa
            if config is not None
            else SERVO_CURRENT_SWEEP_MID_CORRECTION_STRESS_MPA
        )
        if config is None and hasattr(self, "spin_current_sweep_mid_correction_stress_mpa"):
            value = float(self.spin_current_sweep_mid_correction_stress_mpa.value())
        return max(0.001, abs(float(value)))

    def _current_sweep_near_correction_stress_mpa(self) -> float:
        config = self._control_config()
        value = (
            config.current_sweep_near_correction_stress_mpa
            if config is not None
            else SERVO_CURRENT_SWEEP_NEAR_CORRECTION_STRESS_MPA
        )
        if config is None and hasattr(self, "spin_current_sweep_near_correction_stress_mpa"):
            value = float(self.spin_current_sweep_near_correction_stress_mpa.value())
        return max(0.001, abs(float(value)))

    def _current_sweep_hold_filter_window_s(self) -> float:
        config = self._control_config()
        value = (
            config.current_sweep_hold_filter_window_s
            if config is not None
            else SERVO_CURRENT_SWEEP_HOLD_FILTER_WINDOW_S
        )
        if config is None and hasattr(self, "spin_current_sweep_hold_filter_window_s"):
            value = float(self.spin_current_sweep_hold_filter_window_s.value())
        return max(0.1, abs(float(value)))

    def _current_sweep_hold_noise_sigma(self) -> float:
        config = self._control_config()
        value = (
            config.current_sweep_hold_noise_sigma
            if config is not None
            else SERVO_CURRENT_SWEEP_HOLD_NOISE_SIGMA
        )
        if config is None and hasattr(self, "spin_current_sweep_hold_noise_sigma"):
            value = float(self.spin_current_sweep_hold_noise_sigma.value())
        return max(0.0, abs(float(value)))

    def _current_sweep_hold_min_pause_stress_mpa(self) -> float:
        config = self._control_config()
        value = (
            config.current_sweep_hold_min_pause_stress_mpa
            if config is not None
            else SERVO_CURRENT_SWEEP_HOLD_MIN_PAUSE_STRESS_MPA
        )
        if config is None and hasattr(self, "spin_current_sweep_hold_min_pause_stress_mpa"):
            value = float(self.spin_current_sweep_hold_min_pause_stress_mpa.value())
        return max(0.0, abs(float(value)))

    def _current_sweep_hold_min_resume_stress_mpa(self) -> float:
        config = self._control_config()
        value = (
            config.current_sweep_hold_min_resume_stress_mpa
            if config is not None
            else SERVO_CURRENT_SWEEP_HOLD_MIN_RESUME_STRESS_MPA
        )
        if config is None and hasattr(self, "spin_current_sweep_hold_min_resume_stress_mpa"):
            value = float(self.spin_current_sweep_hold_min_resume_stress_mpa.value())
        return max(0.0, abs(float(value)))

    def _current_sweep_max_stress_correction_mm(
        self,
        basis: str,
        sensitivity_per_mm: float,
        *,
        error_value: float | None = None,
        seek_key: tuple[str, int, float] | None = None,
    ) -> float | None:
        sensitivity = abs(float(sensitivity_per_mm))
        if not math.isfinite(sensitivity) or sensitivity <= 0.0:
            return None
        def _basis_cap_from_stress(cap_mpa: float) -> float | None:
            if basis == HSW_BASIS_STRESS_MPA:
                return abs(float(cap_mpa))
            if basis == HSW_BASIS_LOAD_G:
                config = self._control_config()
                diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
                load_cap_g = load_g_from_stress_mpa(cap_mpa, diameter_mm)
                return None if load_cap_g is None else abs(float(load_cap_g))
            return None

        cap_mpa = self._current_sweep_max_correction_stress_mpa()
        if self._automation_phase == "current_hold":
            cap_mpa = self._current_sweep_hold_correction_stress_mpa()
        if error_value is not None and math.isfinite(float(error_value)):
            error_abs = abs(float(error_value))
            near_mpa = self._current_sweep_near_correction_stress_mpa()
            near_cap = _basis_cap_from_stress(near_mpa)
            max_cap = _basis_cap_from_stress(cap_mpa)
            near_threshold = 0.0 if near_cap is None else near_cap
            adaptive_sensitivity = self._current_sweep_hold_response_sensitivity_per_mm(
                basis,
                seek_key=seek_key,
            )
            if (
                adaptive_sensitivity is not None
                and math.isfinite(float(adaptive_sensitivity))
                and float(adaptive_sensitivity) > 0.0
                and max_cap is not None
                and max_cap > 0.0
                and error_abs > near_threshold
            ):
                large_error = max(
                    0.0,
                    error_abs - max(near_threshold, SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_LARGE_ERROR_MPA),
                )
                fraction = SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MIN_FRACTION + (
                    SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MAX_FRACTION
                    - SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MIN_FRACTION
                ) * (1.0 - math.exp(-large_error / SERVO_CURRENT_SWEEP_DYNAMIC_SCALE_MPA))
                cap_value = min(max_cap, max(near_threshold, error_abs * fraction))
                adaptive_mm = cap_value / abs(float(adaptive_sensitivity))
                return max(
                    self._motor_step_mm(),
                    min(self._current_sweep_hold_adaptive_command_cap_mm(), adaptive_mm),
                )
            if near_threshold > 0.0 and error_abs <= near_threshold:
                return self._motor_step_mm()
            if max_cap is not None and max_cap > 0.0:
                error_over_near = max(0.0, error_abs - near_threshold)
                scale = max(1e-9, SERVO_CURRENT_SWEEP_DYNAMIC_SCALE_MPA)
                fraction = SERVO_CURRENT_SWEEP_DYNAMIC_MIN_FRACTION + (
                    SERVO_CURRENT_SWEEP_DYNAMIC_MAX_FRACTION - SERVO_CURRENT_SWEEP_DYNAMIC_MIN_FRACTION
                ) * (1.0 - math.exp(-error_over_near / scale))
                cap_value = min(max_cap, max(near_threshold, error_abs * fraction))
                return max(self._motor_step_mm(), cap_value / sensitivity)
        elif self._current_sweep_freezes_live_stiffness() and self._automation_phase != "target_ramp":
            cap_mpa = self._current_sweep_near_correction_stress_mpa()
        cap_value = _basis_cap_from_stress(cap_mpa)
        if cap_value is None:
            return None
        return max(self._motor_step_mm(), cap_value / sensitivity)

    def _current_sweep_hold_adaptive_command_cap_mm(self) -> float:
        strain_cap_mm = self._strain_pct_to_stage_mm(
            SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MAX_COMMAND_STRAIN_PCT
        )
        return max(
            self._motor_step_mm(),
            min(strain_cap_mm, self._current_sweep_max_correction_mm()),
        )

    def _current_sweep_hold_response_sensitivity_per_mm(
        self,
        basis: str,
        *,
        seek_key: tuple[str, int, float] | None,
    ) -> float | None:
        if self._automation_phase != "current_hold" or seek_key is None:
            return None
        count = self._current_sweep_hold_response_count_by_key.get(seek_key, 0)
        if count < SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MIN_SAMPLES:
            return None
        load_stiffness = self._current_sweep_hold_response_stiffness_by_key.get(seek_key)
        if load_stiffness is None or not math.isfinite(float(load_stiffness)) or float(load_stiffness) <= 0.0:
            return None
        if basis == HSW_BASIS_LOAD_G:
            return float(load_stiffness)
        if basis == HSW_BASIS_STRESS_MPA:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            return stress_mpa_from_load_g(float(load_stiffness), diameter_mm)
        return None

    def _current_sweep_basis_value_from_stress_cap(self, basis: str, stress_mpa: float) -> float | None:
        if basis == HSW_BASIS_STRESS_MPA:
            return abs(float(stress_mpa))
        if basis == HSW_BASIS_LOAD_G:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            load_cap_g = load_g_from_stress_mpa(abs(float(stress_mpa)), diameter_mm)
            return None if load_cap_g is None else abs(float(load_cap_g))
        return None

    def _current_sweep_hold_fast_recovery_needed(self, basis: str | None, error_value: float) -> bool:
        if self._automation_phase != "current_hold" or basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return False
        threshold_mpa = min(
            self._current_sweep_hold_correction_stress_mpa(),
            SERVO_CURRENT_SWEEP_HOLD_MAX_CORRECTION_STRESS_MPA,
        )
        fast_recovery_threshold = self._current_sweep_basis_value_from_stress_cap(
            basis,
            threshold_mpa,
        )
        return fast_recovery_threshold is not None and abs(float(error_value)) >= fast_recovery_threshold

    def _current_sweep_stage_speed_cap_mm_s(self) -> float:
        config = self._control_config()
        speed_mm_s = (
            config.current_sweep_target_speed_mm_s
            if config is not None
            else SERVO_CURRENT_SWEEP_MAX_STAGE_SPEED_MM_S
        )
        if config is None and hasattr(self, "spin_current_sweep_target_speed_mm_s"):
            speed_mm_s = float(self.spin_current_sweep_target_speed_mm_s.value())
        return max(self._minimum_held_speed_mm_s(), speed_mm_s)

    def _current_sweep_strain_rate_speed_cap_mm_s(self) -> float:
        config = self._control_config()
        strain_rate_pct_s = (
            config.current_sweep_correction_rate_pct_s
            if config is not None
            else SERVO_CURRENT_SWEEP_MAX_CORRECTION_RATE_PCT_S
        )
        if config is None and hasattr(self, "spin_current_sweep_correction_rate_pct_s"):
            strain_rate_pct_s = float(self.spin_current_sweep_correction_rate_pct_s.value())
        return max(self._minimum_held_speed_mm_s(), self._strain_pct_to_stage_mm(strain_rate_pct_s))

    def _current_sweep_dynamic_speed_cap_mm_s(self) -> float:
        return max(
            self._minimum_held_speed_mm_s(),
            min(
                self._current_sweep_stage_speed_cap_mm_s(),
                self._current_sweep_strain_rate_speed_cap_mm_s(),
            ),
        )

    def _current_sweep_min_command_speed_mm_s(self) -> float:
        return min(
            self._current_sweep_stage_speed_cap_mm_s(),
            max(self._minimum_held_speed_mm_s(), SERVO_CURRENT_SWEEP_MIN_COMMAND_SPEED_MM_S),
        )

    def _zero_return_acceptance_tolerance_g(self) -> float:
        load_noise_g = self._calibrated_load_noise_g
        noise_floor_g = 0.0
        if load_noise_g is not None and math.isfinite(float(load_noise_g)) and float(load_noise_g) > 0.0:
            noise_floor_g = abs(float(load_noise_g)) * SERVO_NOISE_SIGMA
        return max(SERVO_AUTO_TOLERANCE_LOAD_G, noise_floor_g)

    def _zero_return_requires_true_zero(
        self,
        basis: str,
        target_value: float,
    ) -> bool:
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return False
        target_load_g = self._basis_value_as_load_g(basis, target_value)
        if target_load_g is None:
            return False
        if target_load_g > self._zero_return_acceptance_tolerance_g():
            return False
        if (
            self._automation_step_note == "setup_return_zero"
            and self._setup_zero_position_mm is not None
            and self._setup_zero_fallback_return_position_mm is None
        ):
            return False
        return (
            self._automation_step_note == "setup_return_zero"
            or self._automation_name == RECOVERY_LOAD
            or self._end_zero_fallback_armed
        )

    def _zero_return_current_load_g(self, basis: str, current_value: float) -> float:
        current_load_g = self._basis_value_as_load_g(basis, current_value)
        if current_load_g is None:
            current_load_g = self._current_effective_load_g()
        return abs(float(current_load_g))

    def _current_sweep_mechanical_load_loss_min_travel_mm(self) -> float:
        config = self._control_config()
        length_mm = max(
            0.001,
            config.initial_length_mm if config is not None else float(self.spin_initial_length.value()),
        )
        strain_travel_mm = length_mm * (CURRENT_SWEEP_MECHANICAL_LOAD_LOSS_MIN_STRAIN_PCT / 100.0)
        motor_travel_mm = self._motor_step_mm() * CURRENT_SWEEP_MECHANICAL_LOAD_LOSS_MIN_MOTOR_STEPS
        return max(strain_travel_mm, motor_travel_mm)

    def _current_sweep_mechanical_load_loss_detected(
        self,
        basis: str,
        target_value: float,
        current_value: float,
        tolerance: float,
    ) -> bool:
        if (
            not self._is_current_sweep_mode(self._automation_name)
            or self._automation_phase != "target_ramp"
            or self._automation_step_note in {"setup_preload", "setup_return_zero"}
            or basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
        ):
            return False
        target_load_g = self._basis_value_as_load_g(basis, target_value)
        tolerance_load_g = self._basis_value_as_load_g(basis, tolerance)
        current_load_g = self._basis_value_as_load_g(basis, current_value)
        if target_load_g is None or current_load_g is None:
            return False
        required_target_g = max(
            self._zero_return_acceptance_tolerance_g(),
            SETUP_ZERO_FALLBACK_MAX_RESIDUAL_G,
            0.0 if tolerance_load_g is None else abs(float(tolerance_load_g)),
        )
        if abs(float(target_load_g)) <= required_target_g:
            return False
        if abs(float(current_load_g)) > self._zero_return_acceptance_tolerance_g():
            return False
        travel_mm = abs(self._tensile_displacement_mm(self._measurement_effective_position_mm()))
        return travel_mm >= self._current_sweep_mechanical_load_loss_min_travel_mm()

    def _stop_for_current_sweep_mechanical_load_loss(
        self,
        basis: str,
        target_value: float,
        current_value: float,
    ) -> None:
        travel_mm = abs(self._tensile_displacement_mm(self._measurement_effective_position_mm()))
        message = (
            "Current-sweep mechanical load loss detected: "
            f"target {_format_compact_unit(float(target_value), self._distribution_units(basis)[0])}, "
            f"measured {_format_compact_unit(float(current_value), self._distribution_units(basis)[0])}, "
            f"tensile travel {_format_compact_unit(travel_mm, 'mm')} with near-zero load. "
            "Electrical continuity is not inferred from this guard; current may still be flowing. "
            "Current output was disabled and the measurement was stopped."
        )
        self._log(message)
        self._stop_auto_ramp(
            log_completion=False,
            offer_recovery=False,
            stop_reason="mechanical_load_loss",
            stop_detail=message,
        )

    def _current_sweep_mechanical_slack_takeup_allowed(self) -> bool:
        return bool(getattr(self, "_bench_allow_mechanical_slack_takeup", False))

    def _note_current_sweep_mechanical_slack_takeup(
        self,
        basis: str,
        target_value: float,
        current_value: float,
    ) -> None:
        key = (basis, self._automation_plateau_index, round(float(target_value), 6))
        logged_keys = getattr(self, "_bench_mechanical_slack_takeup_logged_keys", None)
        if logged_keys is None:
            logged_keys = set()
            self._bench_mechanical_slack_takeup_logged_keys = logged_keys
        if key in logged_keys:
            return
        logged_keys.add(key)
        travel_mm = abs(self._tensile_displacement_mm(self._measurement_effective_position_mm()))
        self._log(
            "Bench automation detected mechanical slack/load loss during current sweep: "
            f"target {_format_compact_unit(float(target_value), self._distribution_units(basis)[0])}, "
            f"measured {_format_compact_unit(float(current_value), self._distribution_units(basis)[0])}, "
            f"tensile travel {_format_compact_unit(travel_mm, 'mm')}. "
            "Continuing tensile take-up because the bench plan explicitly allows it."
        )

    def _clamp_motion_resolution_controls(self) -> None:
        step_mm = self._motor_step_mm()
        min_speed = self._minimum_held_speed_mm_s()
        controls = (
            self.spin_jog_mm,
            self.spin_distribution_nudge_mm,
            self.spin_current_sweep_nudge_mm,
            self.spin_ramp_step,
            self.spin_cycle_step,
        )
        for control in controls:
            control.blockSignals(True)
            control.setMinimum(step_mm)
            control.setSingleStep(step_mm)
            if control.value() < step_mm:
                control.setValue(step_mm)
            control.blockSignals(False)
        for control in (
            self.spin_motion_speed_mm_s,
            self.spin_ramp_speed_mm_s,
            self.spin_cycle_speed_mm_s,
            self.spin_hold_speed_mm_s,
            self.spin_distribution_seek_speed_mm_s,
            self.spin_calibration_preload_speed_mm_s,
            self.spin_calibration_speed_mm_s,
            self.spin_current_sweep_target_speed_mm_s,
            self.spin_current_sweep_balance_speed_mm_s,
            self.spin_constant_current_move_speed_mm_s,
        ):
            control.blockSignals(True)
            control.setMinimum(min_speed)
            control.setSingleStep(min_speed)
            if control.value() < min_speed:
                control.setValue(min_speed)
            control.blockSignals(False)

    def _distribution_basis(self) -> str:
        return str(self.combo_distribution_basis.currentData() or HSW_BASIS_STRESS_MPA)

    def _is_current_sweep_mode(self, mode: str | None = None) -> bool:
        return str(mode if mode is not None else self.combo_recipe_mode.currentData() or "") in CURRENT_SWEEP_MODES

    def _is_constant_current_strain_sweep_mode(self, mode: str | None = None) -> bool:
        default_mode = self.combo_recipe_mode.currentData() if hasattr(self, "combo_recipe_mode") else self._automation_name
        return str(mode if mode is not None else default_mode or "") == CONSTANT_CURRENT_STRAIN_SWEEP

    def _is_calibration_mode(self, mode: str | None = None) -> bool:
        default_mode = self.combo_recipe_mode.currentData() if hasattr(self, "combo_recipe_mode") else self._automation_name
        return str(mode if mode is not None else default_mode or "") in CALIBRATION_MODES

    def _is_recovery_mode(self, mode: str | None = None) -> bool:
        return str(mode if mode is not None else self._automation_name) in {RECOVERY_POSITION, RECOVERY_LOAD}

    def _current_sweep_basis(self) -> str:
        mode = str(self.combo_recipe_mode.currentData() or "")
        if mode in CURRENT_SWEEP_BASIS_BY_MODE:
            return CURRENT_SWEEP_BASIS_BY_MODE[mode]
        return str(self.combo_current_sweep_basis.currentData() or HSW_BASIS_LOAD_G)

    def _current_sweep_mode_for_basis(self, basis: str) -> str:
        for mode, mode_basis in CURRENT_SWEEP_BASIS_BY_MODE.items():
            if basis == mode_basis:
                return mode
        return CURRENT_SWEEP_LOAD

    def _current_sweep_target_settings_prefix(self, mode: str | None = None) -> str:
        mode = str(mode or self.combo_recipe_mode.currentData() or CURRENT_SWEEP_LOAD)
        basis = CURRENT_SWEEP_BASIS_BY_MODE.get(mode, HSW_BASIS_LOAD_G)
        return f"current_sweep_{basis}"

    def _current_sweep_target_defaults(self, mode: str | None = None) -> tuple[float, float, float, float]:
        return CURRENT_SWEEP_TARGET_DEFAULTS_BY_MODE.get(
            str(mode or self.combo_recipe_mode.currentData() or CURRENT_SWEEP_LOAD),
            CURRENT_SWEEP_TARGET_DEFAULTS_BY_MODE[CURRENT_SWEEP_LOAD],
        )

    def _current_sweep_target_values(self) -> tuple[float, float, float, float]:
        return (
            float(self.spin_current_sweep_target_start.value()),
            float(self.spin_current_sweep_target_end.value()),
            float(self.spin_current_sweep_target_step.value()),
            float(self.spin_current_sweep_target_ramp_rate.value()),
        )

    def _store_current_sweep_target_values(self, mode: str | None = None) -> None:
        mode = str(mode or self.combo_recipe_mode.currentData() or "")
        if mode in CURRENT_TARGET_VALUE_MODES:
            self._current_sweep_target_values_by_mode[mode] = self._current_sweep_target_values()

    def _apply_current_sweep_target_values(
        self,
        mode: str | None = None,
        *,
        allow_legacy_settings: bool = False,
    ) -> None:
        mode = str(mode or self.combo_recipe_mode.currentData() or CURRENT_SWEEP_LOAD)
        defaults = self._current_sweep_target_defaults(mode)
        values = self._current_sweep_target_values_by_mode.get(mode)
        if values is None:
            prefix = self._current_sweep_target_settings_prefix(mode)
            if self.settings.contains(f"{prefix}_target_start"):
                values = (
                    float(self.settings.value(f"{prefix}_target_start", defaults[0])),
                    float(self.settings.value(f"{prefix}_target_end", defaults[1])),
                    float(self.settings.value(f"{prefix}_target_step", defaults[2])),
                    max(0.0001, float(self.settings.value(f"{prefix}_target_ramp_rate", defaults[3]))),
                )
            elif allow_legacy_settings:
                values = (
                    float(self.settings.value("current_sweep_target_start", defaults[0])),
                    float(self.settings.value("current_sweep_target_end", defaults[1])),
                    float(self.settings.value("current_sweep_target_step", defaults[2])),
                    max(0.0001, float(self.settings.value("current_sweep_target_ramp_rate", defaults[3]))),
                )
            else:
                values = defaults
        start_value, end_value, step_value, ramp_rate = values
        for widget, value in (
            (self.spin_current_sweep_target_start, start_value),
            (self.spin_current_sweep_target_end, end_value),
            (self.spin_current_sweep_target_step, step_value),
            (self.spin_current_sweep_target_ramp_rate, max(0.0001, ramp_rate)),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

    def _handle_recipe_mode_changed(self, _index: int | None = None) -> None:
        previous_mode = self._last_recipe_mode
        current_mode = str(self.combo_recipe_mode.currentData() or "ramp")
        if not self._settings_restore_in_progress:
            self._store_dashboard_plot_settings(previous_mode)
            self._store_current_sweep_target_values(previous_mode)
            if current_mode in CURRENT_TARGET_VALUE_MODES and current_mode != previous_mode:
                self._apply_current_sweep_target_values(current_mode)
        self._last_recipe_mode = current_mode
        if not self._settings_restore_in_progress:
            self._apply_dashboard_plot_settings(current_mode)
        self._update_recipe_mode_ui()

    def _toggle_setup_details(self, checked: bool) -> None:
        if hasattr(self, "setup_details_panel"):
            self.setup_details_panel.setVisible(bool(checked))
        if hasattr(self, "button_setup_details"):
            self.button_setup_details.setArrowType(
                QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
            )

    def _toggle_manual_action_settings(self, checked: bool) -> None:
        if hasattr(self, "manual_action_settings_panel"):
            self.manual_action_settings_panel.setVisible(bool(checked))
        if hasattr(self, "button_manual_action_settings"):
            self.button_manual_action_settings.setArrowType(
                QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
            )

    def _set_recipe_file_controls_visible(self, visible: bool) -> None:
        for widget_name in ("recipe_file_controls_widget", "label_recipe_file_status"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setVisible(bool(visible))
        label = getattr(self, "label_recipe_file_row", None)
        if label is not None:
            label.setVisible(bool(visible))
        action = getattr(self, "action_show_recipe_file_controls", None)
        if action is not None and action.isChecked() != bool(visible):
            action.blockSignals(True)
            action.setChecked(bool(visible))
            action.blockSignals(False)

    def _restore_setup_defaults(self) -> None:
        self.check_pre_measurement_setup_enabled.setChecked(True)
        self.spin_setup_preload_stress_mpa.setValue(20.0)
        self.spin_setup_preload_duration_s.setValue(SETUP_PRELOAD_DEFAULT_DURATION_S)
        self.spin_setup_slack_speed_strain_pct_s.setValue(SETUP_SLACK_DEFAULT_STRAIN_RATE_PCT_S)
        self.spin_setup_slack_step_cap_stress_mpa.setValue(SETUP_PRELOAD_MAX_SLACK_STEP_STRESS_MPA)
        self.spin_setup_preload_tolerance_mpa.setValue(0.25)
        self.spin_setup_zero_tolerance_g.setValue(SERVO_AUTO_TOLERANCE_LOAD_G)
        self.spin_setup_preload_stable_s.setValue(3.0)
        self.spin_setup_zero_stable_s.setValue(1.0)
        self._update_recipe_mode_ui()

    def _restore_current_sweep_advanced_defaults(self) -> None:
        self.spin_current_sweep_target_speed_mm_s.setValue(SERVO_CURRENT_SWEEP_MAX_STAGE_SPEED_MM_S)
        self.spin_current_sweep_max_correction_strain_pct.setValue(
            SERVO_CURRENT_SWEEP_MAX_CORRECTION_STRAIN_PCT
        )
        self.spin_current_sweep_correction_rate_pct_s.setValue(SERVO_CURRENT_SWEEP_MAX_CORRECTION_RATE_PCT_S)
        self.spin_current_sweep_max_correction_stress_mpa.setValue(SERVO_CURRENT_SWEEP_MAX_CORRECTION_STRESS_MPA)
        self.spin_current_sweep_hold_correction_stress_mpa.setValue(
            SERVO_CURRENT_SWEEP_HOLD_MAX_CORRECTION_STRESS_MPA
        )
        self.spin_current_sweep_mid_correction_stress_mpa.setValue(SERVO_CURRENT_SWEEP_MID_CORRECTION_STRESS_MPA)
        self.spin_current_sweep_near_correction_stress_mpa.setValue(SERVO_CURRENT_SWEEP_NEAR_CORRECTION_STRESS_MPA)
        self.spin_current_sweep_hold_pause_factor.setValue(CURRENT_SWEEP_HOLD_PAUSE_TOLERANCE_FACTOR)
        self.spin_current_sweep_hold_resume_factor.setValue(CURRENT_SWEEP_HOLD_RESUME_TOLERANCE_FACTOR)
        self.spin_current_sweep_hold_resume_stable_s.setValue(CURRENT_SWEEP_HOLD_RESUME_STABLE_S)
        self.spin_current_sweep_hold_filter_window_s.setValue(SERVO_CURRENT_SWEEP_HOLD_FILTER_WINDOW_S)
        self.spin_current_sweep_hold_noise_sigma.setValue(SERVO_CURRENT_SWEEP_HOLD_NOISE_SIGMA)
        self.spin_current_sweep_hold_min_pause_stress_mpa.setValue(SERVO_CURRENT_SWEEP_HOLD_MIN_PAUSE_STRESS_MPA)
        self.spin_current_sweep_hold_min_resume_stress_mpa.setValue(SERVO_CURRENT_SWEEP_HOLD_MIN_RESUME_STRESS_MPA)
        self._update_recipe_mode_ui()

    def _restore_manual_action_defaults(self) -> None:
        self.spin_motion_speed_mm_s.setValue(1.0)
        self.spin_jog_mm.setValue(0.1)
        self.spin_setup_return_duration_s.setValue(SETUP_RETURN_DEFAULT_DURATION_S)
        self._clamp_motion_resolution_controls()
        self._update_recipe_mode_ui()

    def _pre_measurement_setup_enabled(self, mode: str | None = None) -> bool:
        _ = mode
        checkbox = getattr(self, "check_pre_measurement_setup_enabled", None)
        return True if checkbox is None else bool(checkbox.isChecked())

    def _update_setup_summary(self) -> None:
        if not hasattr(self, "label_setup_summary"):
            return
        if not self._pre_measurement_setup_enabled():
            self.label_setup_summary.setText("Off for this recipe")
            self.label_setup_summary.setStyleSheet("color: #dc2626;")
            return
        preload_text = _format_compact_unit(float(self.spin_setup_preload_stress_mpa.value()), "MPa", decimals=3)
        duration_text = _format_compact_unit(float(self.spin_setup_preload_duration_s.value()), "s", decimals=2)
        settle_text = _format_compact_unit(float(self.spin_setup_preload_stable_s.value()), "s", decimals=2)
        self.label_setup_summary.setText(
            f"On: {preload_text}, {duration_text} ramp, {settle_text} settle"
        )
        self.label_setup_summary.setStyleSheet("color: palette(text);")

    def _distribution_units(self, basis: str | None = None) -> tuple[str, int]:
        basis = basis or self._distribution_basis()
        if basis == HSW_BASIS_LOAD_G:
            return " g", 4
        if basis == HSW_BASIS_STRAIN_PCT:
            return " %", 4
        return " MPa", 3

    def _constant_current_start_basis(self) -> str:
        return str(self.combo_constant_current_start_basis.currentData() or HSW_BASIS_STRAIN_PCT)

    def _constant_current_step_basis(self) -> str:
        return str(self.combo_constant_current_step_basis.currentData() or MECHANICAL_STEP_DISPLACEMENT_MM)

    def _update_constant_current_basis_ui(self) -> None:
        suffix, decimals = self._distribution_units(self._constant_current_start_basis())
        for widget in (self.spin_constant_current_start_target, self.spin_constant_current_end_target):
            widget.blockSignals(True)
            widget.setDecimals(decimals)
            widget.setSuffix(suffix)
            widget.blockSignals(False)
        step_basis = self._constant_current_step_basis()
        self.spin_constant_current_step_size.blockSignals(True)
        if step_basis == HSW_BASIS_STRAIN_PCT:
            self.spin_constant_current_step_size.setDecimals(4)
            self.spin_constant_current_step_size.setSuffix(" %")
        else:
            self.spin_constant_current_step_size.setDecimals(4)
            self.spin_constant_current_step_size.setSuffix(" mm")
        self.spin_constant_current_step_size.blockSignals(False)

    def _update_distribution_basis_ui(self) -> None:
        suffix, decimals = self._distribution_units()
        for widget in (
            self.spin_distribution_start,
            self.spin_distribution_end,
            self.spin_distribution_step,
            self.spin_distribution_tolerance,
        ):
            widget.blockSignals(True)
            widget.setDecimals(decimals)
            widget.setSuffix(suffix)
            widget.blockSignals(False)

    def _update_current_sweep_basis_ui(self) -> None:
        basis = self._current_sweep_basis()
        suffix, decimals = self._distribution_units(basis)
        if hasattr(self, "label_current_sweep_targets_section"):
            if basis == HSW_BASIS_LOAD_G:
                self.label_current_sweep_targets_section.setText("Load targets")
            elif basis == HSW_BASIS_STRAIN_PCT:
                self.label_current_sweep_targets_section.setText("Strain targets")
            else:
                self.label_current_sweep_targets_section.setText("Stress targets")
        for widget in (
            self.spin_current_sweep_target_start,
            self.spin_current_sweep_target_end,
            self.spin_current_sweep_target_step,
            self.spin_current_sweep_tolerance,
        ):
            widget.blockSignals(True)
            widget.setDecimals(decimals)
            widget.setSuffix(suffix)
            widget.blockSignals(False)
        self.spin_current_sweep_target_ramp_rate.blockSignals(True)
        self.spin_current_sweep_target_ramp_rate.setDecimals(decimals)
        self.spin_current_sweep_target_ramp_rate.setSuffix(f"{suffix}/s")
        self.spin_current_sweep_target_ramp_rate.blockSignals(False)

    def _build_distribution_targets(
        self,
        start_value: float,
        end_value: float,
        step_value: float,
        *,
        include_return: bool,
    ) -> list[float]:
        if step_value <= 0.0:
            raise ValueError("Distribution step must be greater than zero.")
        delta_value = end_value - start_value
        if delta_value == 0.0:
            targets = [start_value]
        else:
            sign = 1.0 if delta_value >= 0.0 else -1.0
            count = max(1, int(math.ceil(abs(delta_value) / step_value)))
            targets = [
                start_value + sign * min(index * step_value, abs(delta_value))
                for index in range(0, count + 1)
            ]
        if include_return and len(targets) > 1:
            targets.extend(reversed(targets[:-1]))
        return targets

    def _build_numeric_targets(self, start_value: float, end_value: float, step_value: float) -> list[float]:
        if step_value <= 0.0:
            raise ValueError("Step size must be greater than zero.")
        delta_value = end_value - start_value
        if abs(delta_value) < 1e-12:
            return [start_value]
        sign = 1.0 if delta_value >= 0.0 else -1.0
        count = max(1, int(math.ceil(abs(delta_value) / step_value)))
        return [
            start_value + sign * min(index * step_value, abs(delta_value))
            for index in range(0, count + 1)
        ]

    def _current_distribution_value(self, basis: str, *, require_after_last_move: bool = False) -> float | None:
        after_s = self._motion_feedback_ready_after_s() if require_after_last_move else None
        if basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA} and not self._has_fresh_scale_reading(after_s=after_s):
            return None
        effective_load = self._current_effective_load_g()
        if basis == HSW_BASIS_LOAD_G:
            return effective_load
        if basis == HSW_BASIS_STRESS_MPA:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            return stress_mpa_from_load_g(effective_load, diameter_mm)
        preload_state = self._current_preload_state(effective_load)
        if preload_state == PRELOAD_PENDING:
            return None
        return self._strain_percent_for_position(self._measurement_effective_position_mm())

    def _basis_value_as_load_g(self, basis: str, value: float) -> float | None:
        if basis == HSW_BASIS_LOAD_G:
            return abs(float(value))
        if basis == HSW_BASIS_STRESS_MPA:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            return load_g_from_stress_mpa(abs(float(value)), diameter_mm)
        return None

    def _motion_feedback_ready_after_s(self) -> float | None:
        if self._last_motion_command_time_s is None:
            return None
        ready_after_s = float(self._last_motion_command_time_s)
        if self._last_motion_expected_complete_time_s is not None:
            ready_after_s = max(ready_after_s, float(self._last_motion_expected_complete_time_s))
        return ready_after_s

    def _servo_landing_factor(self, error_value: float, tolerance: float) -> float:
        error_ratio = abs(float(error_value)) / max(abs(float(tolerance)), 1e-12)
        if error_ratio <= 1.0:
            return 0.0
        if error_ratio >= SERVO_FULL_SPEED_ERROR_RATIO:
            return 1.0
        scaled = (error_ratio - 1.0) / max(1e-9, SERVO_FULL_SPEED_ERROR_RATIO - 1.0)
        scaled = max(0.0, min(1.0, scaled))
        return scaled * scaled * (3.0 - 2.0 * scaled)

    def _live_speed_values(self) -> dict[str, float | None]:
        speed_mm_s = abs(float(self._last_commanded_speed_mm_s))
        if speed_mm_s <= 0.0:
            return {
                "speed_mm_s": None,
                "load_rate_g_s": None,
                "stress_rate_mpa_s": None,
                "strain_rate_pct_s": None,
            }
        stiffness = self._seek_live_stiffness_g_per_mm
        if stiffness is None or not math.isfinite(float(stiffness)) or float(stiffness) <= 0.0:
            stiffness = self._stored_calibration_stiffness_g_per_mm()
        load_rate_g_s = None if stiffness is None else abs(speed_mm_s * float(stiffness))
        stress_rate_mpa_s = (
            None
            if load_rate_g_s is None
            else stress_mpa_from_load_g(load_rate_g_s, float(self.spin_diameter.value()))
        )
        length_mm = max(0.001, float(self.spin_initial_length.value()))
        strain_rate_pct_s = abs(speed_mm_s * 100.0 / length_mm)
        return {
            "speed_mm_s": speed_mm_s,
            "load_rate_g_s": load_rate_g_s,
            "stress_rate_mpa_s": stress_rate_mpa_s,
            "strain_rate_pct_s": strain_rate_pct_s,
        }

    def _live_speed_summary_text(self) -> str:
        speed_values = self._live_speed_values()
        speed_mm_s = speed_values["speed_mm_s"]
        if speed_mm_s is None:
            return "Command speed: -"

        def _rate_text(value: float | None, unit: str) -> str:
            if value is None or not math.isfinite(float(value)):
                return f"- {unit}"
            return f"{_format_compact_number(float(value))} {unit}"

        return (
            f"Command speed: {_format_compact_number(speed_mm_s)} mm/s | "
            f"{_rate_text(speed_values['load_rate_g_s'], 'g/s')} | "
            f"{_rate_text(speed_values['stress_rate_mpa_s'], 'MPa/s')} | "
            f"{_rate_text(speed_values['strain_rate_pct_s'], '%/s')}"
        )

    def _auto_requested_tolerance_for_basis(self, basis: str | None) -> float:
        if basis == HSW_BASIS_LOAD_G:
            return SERVO_AUTO_TOLERANCE_LOAD_G
        if basis == HSW_BASIS_STRESS_MPA:
            stress_tolerance = stress_mpa_from_load_g(
                SERVO_AUTO_TOLERANCE_LOAD_G,
                self._control_config().diameter_mm if self._control_config() is not None else float(self.spin_diameter.value()),
            )
            return 0.0 if stress_tolerance is None else abs(float(stress_tolerance))
        if basis == HSW_BASIS_STRAIN_PCT:
            return 0.0
        return SERVO_AUTO_TOLERANCE_LOAD_G

    def _auto_tolerance_summary_text(self, basis: str | None) -> str:
        tolerance = self._auto_requested_tolerance_for_basis(basis)
        suffix, decimals = self._distribution_units(basis)
        if basis == HSW_BASIS_LOAD_G:
            return f"{_format_compact_number(tolerance)} g minimum"
        if basis == HSW_BASIS_STRESS_MPA:
            return (
                f"{_format_compact_number(tolerance, decimals=decimals)}{suffix} "
                f"from {_format_compact_number(SERVO_AUTO_TOLERANCE_LOAD_G)} g minimum"
            )
        if basis == HSW_BASIS_STRAIN_PCT:
            return "motor-step/noise floor"
        return f"{_format_compact_number(SERVO_AUTO_TOLERANCE_LOAD_G)} g minimum"

    def _distribution_target_reached(self, basis: str, target_value: float, tolerance: float) -> bool:
        current_value = self._current_distribution_value(basis)
        if current_value is None:
            return False
        return abs(target_value - current_value) <= tolerance

    def _seek_nudge_mm(self) -> float:
        config = self._control_config()
        if self._automation_name == RECOVERY_LOAD:
            return abs(float(config.jog_mm if config is not None else self.spin_jog_mm.value()))
        if self._is_calibration_mode(self._automation_name):
            value = (
                config.calibration_preload_nudge_mm
                if config is not None
                else float(self.spin_calibration_preload_nudge_mm.value())
            )
            return abs(float(value))
        if self._is_current_sweep_mode(self._automation_name):
            return self._seek_speed_limited_step_mm(
                self._automation_basis,
                self._motion_speed_for_current_context(manual_jog=False),
            )
        value = config.distribution_nudge_mm if config is not None else float(self.spin_distribution_nudge_mm.value())
        return abs(float(value))

    def _seek_decision_interval_s(self, basis: str | None = None) -> float:
        interval_ms = max(1, int(self._automation_interval_ms))
        config = self._control_config()
        if basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            if config is not None:
                interval_ms = max(interval_ms, int(config.scale_interval_ms))
            elif hasattr(self, "spin_scale_interval"):
                interval_ms = max(interval_ms, int(self.spin_scale_interval.value()))
        return max(0.001, interval_ms / 1000.0)

    def _seek_speed_limited_step_mm(self, basis: str | None, speed_mm_s: float) -> float:
        return max(
            self._motor_step_mm(),
            self._seek_travel_during_interval_mm(speed_mm_s, basis),
        )

    def _seek_travel_during_interval_mm(self, speed_mm_s: float, basis: str | None) -> float:
        interval_s = self._seek_decision_interval_s(basis)
        speed = max(self._minimum_held_speed_mm_s(), abs(float(speed_mm_s)))
        profile_travel = self._motion_profile_travel_mm(speed, interval_s)
        if profile_travel is not None:
            return max(0.0, profile_travel)
        return speed * interval_s

    def _seek_max_travel_mm(self) -> float:
        config = self._control_config()
        if self._is_current_sweep_mode(self._automation_name):
            max_seek_mm = (
                config.current_sweep_max_seek_mm
                if config is not None
                else float(self.spin_current_sweep_max_seek_mm.value())
            )
            return max(self._motor_step_mm(), max_seek_mm)
        if self._is_calibration_mode(self._automation_name):
            return max(self._motor_step_mm(), self._seek_nudge_mm() * 100.0)
        return max(self._motor_step_mm(), self._seek_nudge_mm() * 30.0)

    def _stored_calibration_stiffness_g_per_mm(self) -> float | None:
        stiffness = self._calibrated_stiffness_g_per_mm
        if stiffness is None or not math.isfinite(float(stiffness)) or float(stiffness) <= 0.0:
            return None
        calibrated_length = self._calibrated_stiffness_length_mm
        if (
            calibrated_length is None
            or not math.isfinite(float(calibrated_length))
            or float(calibrated_length) <= 0.0
        ):
            return float(stiffness)
        config = self._control_config()
        current_length = max(0.001, config.initial_length_mm if config is not None else float(self.spin_initial_length.value()))
        return float(stiffness) * (float(calibrated_length) / current_length)

    def _load_stiffness_from_basis_sensitivity(self, basis: str, sensitivity_per_mm: float) -> float | None:
        sensitivity = abs(float(sensitivity_per_mm))
        if not math.isfinite(sensitivity) or sensitivity <= 0.0:
            return None
        if basis == HSW_BASIS_LOAD_G:
            return sensitivity
        if basis == HSW_BASIS_STRESS_MPA:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            load_per_mm = load_g_from_stress_mpa(sensitivity, diameter_mm)
            return None if load_per_mm is None else abs(float(load_per_mm))
        return None

    def _current_sweep_freezes_live_stiffness(self) -> bool:
        return (
            self._is_current_sweep_mode(self._automation_name)
            and self._automation_step_note not in {"setup_preload", "setup_return_zero"}
        )

    def _basis_sensitivity_per_mm(
        self,
        basis: str,
        *,
        seek_key: tuple[str, int, float] | None = None,
    ) -> float | None:
        if basis == HSW_BASIS_STRAIN_PCT:
            config = self._control_config()
            length_mm = config.initial_length_mm if config is not None else float(self.spin_initial_length.value())
            return None if length_mm <= 0.0 else 100.0 / length_mm
        stiffness_candidates: list[float | None] = []
        if seek_key is not None:
            stiffness_candidates.append(self._seek_live_stiffness_by_key.get(seek_key))
        stiffness_candidates.extend(
            (
                self._seek_live_stiffness_g_per_mm,
                self._stored_calibration_stiffness_g_per_mm(),
            )
        )
        valid_stiffness = [
            float(stiffness)
            for stiffness in stiffness_candidates
            if stiffness is not None and math.isfinite(float(stiffness)) and float(stiffness) > 0.0
        ]
        if not valid_stiffness:
            return None
        if (
            self._is_current_sweep_mode(self._automation_name)
            and basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
        ):
            stiffness = max(valid_stiffness)
        else:
            stiffness = valid_stiffness[0]
        if stiffness is None or not math.isfinite(float(stiffness)) or float(stiffness) <= 0.0:
            return None
        if basis == HSW_BASIS_LOAD_G:
            return float(stiffness)
        if basis == HSW_BASIS_STRESS_MPA:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            return stress_mpa_from_load_g(float(stiffness), diameter_mm)
        return None

    def _setup_preload_takeup_active(
        self,
        basis: str,
        current_value: float,
        delta_value: float,
        effective_tolerance: float,
        *,
        seek_key: tuple[str, int, float] | None = None,
    ) -> bool:
        if (
            self._automation_phase != "target_ramp"
            or self._automation_step_note != "setup_preload"
            or basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            or delta_value <= 0.0
        ):
            return False
        if seek_key is not None and seek_key in self._setup_preload_engaged_seek_keys:
            return False
        current_load_g = self._basis_value_as_load_g(basis, current_value)
        if current_load_g is not None:
            if abs(float(current_load_g)) <= self._setup_preload_contact_threshold_g():
                return True
        return abs(float(current_value)) <= abs(float(effective_tolerance))

    def _setup_preload_contact_threshold_g(self) -> float:
        load_noise_g = (
            0.0
            if self._calibrated_load_noise_g is None
            or not math.isfinite(float(self._calibrated_load_noise_g))
            else abs(float(self._calibrated_load_noise_g))
        )
        return max(
            SETUP_PRELOAD_TAKEUP_LOAD_G,
            SERVO_AUTO_TOLERANCE_LOAD_G * 4.0,
            load_noise_g * SERVO_NOISE_SIGMA,
        )

    def _setup_preload_max_slack_takeup_step_mm(
        self,
        basis: str,
        *,
        seek_key: tuple[str, int, float] | None = None,
    ) -> float | None:
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return None
        sensitivity = self._basis_sensitivity_per_mm(basis, seek_key=seek_key)
        if sensitivity is None or not math.isfinite(float(sensitivity)) or abs(float(sensitivity)) <= 0.0:
            return None
        config = self._control_config()
        cap_stress_mpa = (
            config.setup_slack_step_cap_stress_mpa
            if config is not None
            else float(self.spin_setup_slack_step_cap_stress_mpa.value())
        )
        cap_value = self._current_sweep_basis_value_from_stress_cap(
            basis,
            max(0.001, float(cap_stress_mpa)),
        )
        if cap_value is None:
            return None
        return max(self._motor_step_mm(), cap_value / abs(float(sensitivity)))

    def _update_setup_preload_engagement(
        self,
        seek_key: tuple[str, int, float],
        basis: str,
        current_value: float,
    ) -> None:
        if (
            self._automation_phase != "target_ramp"
            or self._automation_step_note != "setup_preload"
            or basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
        ):
            return
        current_load_g = self._basis_value_as_load_g(basis, current_value)
        if current_load_g is None:
            return
        if abs(float(current_load_g)) > self._setup_preload_contact_threshold_g():
            self._setup_preload_engaged_seek_keys.add(seek_key)

    def _setup_preload_first_contact_transition(
        self,
        seek_key: tuple[str, int, float],
        basis: str,
        current_value: float,
    ) -> bool:
        if (
            self._automation_phase != "target_ramp"
            or self._automation_step_note != "setup_preload"
            or basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            or seek_key in self._setup_preload_engaged_seek_keys
        ):
            return False
        current_load_g = self._basis_value_as_load_g(basis, current_value)
        if current_load_g is None:
            return False
        return abs(float(current_load_g)) > self._setup_preload_contact_threshold_g()

    def _setup_preload_relaxation_active(
        self,
        basis: str,
        current_value: float,
        target_value: float,
        tolerance: float,
    ) -> bool:
        if (
            self._automation_phase != "target_ramp"
            or self._automation_step_note != "setup_preload"
            or basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
        ):
            return False
        current_load_g = self._basis_value_as_load_g(basis, current_value)
        target_load_g = self._basis_value_as_load_g(basis, target_value)
        tolerance_load_g = self._basis_value_as_load_g(basis, tolerance) or 0.0
        if current_load_g is None or target_load_g is None:
            return False
        return float(current_load_g) > float(target_load_g) + abs(float(tolerance_load_g))

    def _basis_noise_floor(
        self,
        basis: str,
        *,
        sensitivity_per_mm: float | None,
    ) -> float:
        load_noise_g = self._calibrated_load_noise_g
        if load_noise_g is None or not math.isfinite(float(load_noise_g)) or float(load_noise_g) <= 0.0:
            return 0.0
        noise_g = abs(float(load_noise_g)) * SERVO_NOISE_SIGMA
        if basis == HSW_BASIS_LOAD_G:
            return noise_g
        if basis == HSW_BASIS_STRESS_MPA:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            stress_noise = stress_mpa_from_load_g(noise_g, diameter_mm)
            return 0.0 if stress_noise is None else abs(float(stress_noise))
        if basis == HSW_BASIS_STRAIN_PCT and sensitivity_per_mm is not None:
            return 0.0
        return 0.0

    def _remember_live_stiffness(self, load_stiffness_g_per_mm: float) -> None:
        if not math.isfinite(float(load_stiffness_g_per_mm)) or float(load_stiffness_g_per_mm) <= 0.0:
            return
        old = self._seek_live_stiffness_g_per_mm
        if old is None or old <= 0.0 or not math.isfinite(float(old)):
            self._seek_live_stiffness_g_per_mm = float(load_stiffness_g_per_mm)
            return
        self._seek_live_stiffness_g_per_mm = (
            (1.0 - SERVO_LIVE_STIFFNESS_ALPHA) * float(old)
            + SERVO_LIVE_STIFFNESS_ALPHA * float(load_stiffness_g_per_mm)
        )

    def _seek_effective_tolerance(
        self,
        basis: str,
        requested_tolerance: float,
        *,
        seek_key: tuple[str, int, float] | None = None,
    ) -> float:
        tolerance = abs(float(requested_tolerance))
        sensitivity = self._basis_sensitivity_per_mm(basis, seek_key=seek_key)
        if sensitivity is None or sensitivity <= 0.0:
            return tolerance
        step_floor = abs(float(sensitivity)) * self._motor_step_mm()
        noise_floor = self._basis_noise_floor(basis, sensitivity_per_mm=sensitivity)
        effective_tolerance = max(tolerance, step_floor, noise_floor)
        calibration_cap = self._calibration_acceptance_cap_for_basis(basis, tolerance)
        if calibration_cap is not None:
            effective_tolerance = min(effective_tolerance, calibration_cap)
        setup_preload_cap = self._setup_preload_acceptance_cap_for_basis(basis, tolerance)
        if setup_preload_cap is not None:
            effective_tolerance = min(effective_tolerance, setup_preload_cap)
        return effective_tolerance

    def _seek_target_acceptance_tolerance(
        self,
        basis: str,
        requested_tolerance: float,
        *,
        seek_key: tuple[str, int, float] | None = None,
    ) -> float:
        if not (
            self._is_current_sweep_mode(self._automation_name)
            and self._automation_phase in {"target_ramp", "current", "current_hold", "current_limit_unwind"}
            and self._automation_step_note not in {"setup_preload", "setup_return_zero"}
            and basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
        ):
            return self._seek_effective_tolerance(
                basis,
                requested_tolerance,
                seek_key=seek_key,
            )
        tolerance = abs(float(requested_tolerance))
        sensitivity = self._basis_sensitivity_per_mm(basis, seek_key=seek_key)
        noise_floor = self._basis_noise_floor(basis, sensitivity_per_mm=sensitivity)
        return max(tolerance, noise_floor)

    def _setup_preload_acceptance_cap_for_basis(
        self,
        basis: str,
        requested_tolerance: float,
    ) -> float | None:
        if self._automation_step_note != "setup_preload":
            return None
        requested = abs(float(requested_tolerance))
        contact_load_g = self._setup_preload_contact_threshold_g()
        if basis == HSW_BASIS_LOAD_G:
            return max(requested, contact_load_g)
        if basis == HSW_BASIS_STRESS_MPA:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            contact_stress = stress_mpa_from_load_g(contact_load_g, diameter_mm)
            return None if contact_stress is None else max(requested, abs(float(contact_stress)))
        return None

    def _calibration_acceptance_cap_for_basis(self, basis: str, requested_tolerance: float) -> float | None:
        if not self._is_calibration_mode(self._automation_name):
            return None
        requested = abs(float(requested_tolerance))
        cap_load_g = max(requested, CALIBRATION_MAX_AUTO_ACCEPTANCE_LOAD_G)
        if basis == HSW_BASIS_LOAD_G:
            return cap_load_g
        if basis == HSW_BASIS_STRESS_MPA:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            cap_stress = stress_mpa_from_load_g(cap_load_g, diameter_mm)
            return None if cap_stress is None else max(requested, abs(float(cap_stress)))
        return None

    def _update_live_seek_stiffness(
        self,
        seek_key: tuple[str, int, float],
        basis: str,
        current_value: float,
    ) -> None:
        if self._current_sweep_freezes_live_stiffness():
            self._update_current_sweep_hold_response_stiffness(
                seek_key,
                basis,
                current_value,
            )
            return
        current_position = self._current_effective_tensile_position_mm()
        if self._setup_preload_first_contact_transition(seek_key, basis, current_value):
            self._seek_last_stiffness_value_by_basis[basis] = float(current_value)
            self._seek_last_stiffness_position_by_basis[basis] = float(current_position)
            self._seek_last_value_by_key[seek_key] = float(current_value)
            self._seek_last_effective_position_by_key[seek_key] = float(current_position)
            return
        previous_basis_value = self._seek_last_stiffness_value_by_basis.get(basis)
        previous_basis_position = self._seek_last_stiffness_position_by_basis.get(basis)
        self._seek_last_stiffness_value_by_basis[basis] = float(current_value)
        self._seek_last_stiffness_position_by_basis[basis] = float(current_position)
        if previous_basis_value is not None and previous_basis_position is not None:
            delta_position = abs(current_position - previous_basis_position)
            delta_value = abs(float(current_value) - float(previous_basis_value))
            if delta_position >= self._motor_step_mm() * 0.5 and delta_value > 0.0 and math.isfinite(delta_value):
                load_stiffness = self._load_stiffness_from_basis_sensitivity(
                    basis,
                    delta_value / delta_position,
                )
                if load_stiffness is not None:
                    self._remember_live_stiffness(load_stiffness)

        previous_value = self._seek_last_value_by_key.get(seek_key)
        previous_position = self._seek_last_effective_position_by_key.get(seek_key)
        if previous_value is None or previous_position is None:
            return
        signed_delta_position = float(current_position) - float(previous_position)
        delta_position = abs(signed_delta_position)
        if delta_position < self._motor_step_mm() * 0.5:
            return
        signed_delta_value = float(current_value) - float(previous_value)
        delta_value = abs(signed_delta_value)
        if delta_value <= 0.0 or not math.isfinite(delta_value):
            return
        if signed_delta_position * signed_delta_value <= 0.0:
            return
        load_stiffness = self._load_stiffness_from_basis_sensitivity(basis, delta_value / delta_position)
        if load_stiffness is None:
            return
        old = self._seek_live_stiffness_by_key.get(seek_key)
        if old is None or old <= 0.0 or not math.isfinite(float(old)):
            self._seek_live_stiffness_by_key[seek_key] = load_stiffness
            self._remember_live_stiffness(load_stiffness)
            return
        self._seek_live_stiffness_by_key[seek_key] = (
            (1.0 - SERVO_LIVE_STIFFNESS_ALPHA) * float(old)
            + SERVO_LIVE_STIFFNESS_ALPHA * load_stiffness
        )
        self._remember_live_stiffness(load_stiffness)

    def _update_current_sweep_hold_response_stiffness(
        self,
        seek_key: tuple[str, int, float],
        basis: str,
        current_value: float,
    ) -> None:
        if self._current_sweep_freezes_live_stiffness():
            if self._automation_phase != "current_hold" or basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
                return
        else:
            return
        current_position = self._current_effective_tensile_position_mm()
        previous_value = self._seek_last_value_by_key.get(seek_key)
        previous_position = self._seek_last_effective_position_by_key.get(seek_key)
        if previous_value is None or previous_position is None:
            return
        signed_delta_position = float(current_position) - float(previous_position)
        delta_position = abs(signed_delta_position)
        if delta_position < self._motor_step_mm() * 0.5:
            return
        signed_delta_value = float(current_value) - float(previous_value)
        delta_value = abs(signed_delta_value)
        if delta_value <= 0.0 or not math.isfinite(delta_value):
            return
        if signed_delta_position * signed_delta_value <= 0.0:
            return
        load_stiffness = self._load_stiffness_from_basis_sensitivity(basis, delta_value / delta_position)
        if load_stiffness is None:
            return
        old = self._current_sweep_hold_response_stiffness_by_key.get(seek_key)
        old_count = self._current_sweep_hold_response_count_by_key.get(seek_key, 0)
        if old is None or old <= 0.0 or not math.isfinite(float(old)):
            self._current_sweep_hold_response_stiffness_by_key[seek_key] = load_stiffness
            self._current_sweep_hold_response_count_by_key[seek_key] = 1
            return
        self._current_sweep_hold_response_stiffness_by_key[seek_key] = (
            (1.0 - SERVO_LIVE_STIFFNESS_ALPHA) * float(old)
            + SERVO_LIVE_STIFFNESS_ALPHA * load_stiffness
        )
        self._current_sweep_hold_response_count_by_key[seek_key] = old_count + 1

    def _predictive_seek_step_mm(
        self,
        basis: str,
        error_value: float,
        tolerance: float,
        *,
        seek_key: tuple[str, int, float],
    ) -> float:
        sensitivity = self._basis_sensitivity_per_mm(basis, seek_key=seek_key)
        if sensitivity is None or sensitivity <= 0.0:
            if self._is_current_sweep_mode(self._automation_name):
                return self._seek_speed_limited_step_mm(
                    basis,
                    self._motion_speed_for_current_context(manual_jog=False),
                )
            return self._seek_step_mm(error_value, tolerance, basis=basis)
        predicted_mm = (abs(float(error_value)) / abs(float(sensitivity))) * SERVO_CORRECTION_GAIN
        max_step_mm = self._seek_nudge_mm()
        if self._automation_step_note == "setup_preload" and basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            correction_caps = [
                self._seek_speed_limited_step_mm(
                    basis,
                    self._motion_speed_for_current_context(manual_jog=False),
                )
            ]
            stress_cap_mm = self._current_sweep_max_stress_correction_mm(
                basis,
                sensitivity,
                error_value=error_value,
                seek_key=seek_key,
            )
            if stress_cap_mm is not None:
                correction_caps.append(stress_cap_mm)
            max_step_mm = max(self._motor_step_mm(), min(correction_caps))
        elif self._automation_step_note in {"setup_preload", "setup_return_zero"}:
            max_step_mm = self._seek_speed_limited_step_mm(
                basis,
                self._motion_speed_for_current_context(manual_jog=False),
            )
        elif self._is_current_sweep_mode(self._automation_name):
            correction_caps = [self._current_sweep_max_correction_mm()]
            stress_cap_mm = self._current_sweep_max_stress_correction_mm(
                basis,
                sensitivity,
                error_value=error_value,
                seek_key=seek_key,
            )
            if stress_cap_mm is not None:
                correction_caps.append(stress_cap_mm)
            max_step_mm = max(self._motor_step_mm(), min(correction_caps))
        return max(self._motor_step_mm(), min(max_step_mm, predicted_mm))

    def _reverse_correction_is_worthwhile(
        self,
        basis: str,
        error_value: float,
        tolerance: float,
        backlash_takeup_mm: float,
        *,
        seek_key: tuple[str, int, float],
    ) -> bool:
        if backlash_takeup_mm <= 0.0:
            return True
        if self._automation_step_note == "setup_preload":
            return True
        if self._current_sweep_freezes_live_stiffness():
            return True
        if self._stored_calibration_stiffness_g_per_mm() is None:
            return True
        sensitivity = self._basis_sensitivity_per_mm(basis, seek_key=seek_key)
        if sensitivity is None or sensitivity <= 0.0:
            return True
        reversal_cost = abs(float(sensitivity)) * abs(float(backlash_takeup_mm))
        return abs(float(error_value)) > abs(float(tolerance)) + reversal_cost

    def _use_backlash_compensation_for_current_recipe(self) -> bool:
        if self._is_calibration_mode(self._automation_name):
            return False
        if self._is_current_sweep_mode(self._automation_name):
            return False
        return True

    def _reversal_acceptance_tolerance(
        self,
        basis: str,
        tolerance: float,
        *,
        seek_key: tuple[str, int, float],
    ) -> float:
        base = abs(float(tolerance))
        if self._automation_step_note == "setup_preload":
            base *= 4.0
        else:
            base *= 2.0
        sensitivity = self._basis_sensitivity_per_mm(basis, seek_key=seek_key)
        if sensitivity is not None and math.isfinite(float(sensitivity)) and abs(float(sensitivity)) > 0.0:
            step_value = abs(float(sensitivity)) * self._motor_step_mm()
            config = self._control_config()
            backlash_mm = (
                max(0.0, float(config.backlash_mm if config is not None else self.spin_backlash_mm.value()))
                if self._use_backlash_compensation_for_current_recipe()
                else 0.0
            )
            backlash_value = abs(float(sensitivity)) * backlash_mm
            base = max(base, step_value * 1.5, backlash_value)
        calibration_cap = self._calibration_acceptance_cap_for_basis(basis, tolerance)
        if calibration_cap is not None:
            base = min(base, calibration_cap)
        return base

    def _target_reversal_is_practical_hold(
        self,
        basis: str,
        error_value: float,
        tolerance: float,
        *,
        seek_key: tuple[str, int, float],
    ) -> bool:
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA, HSW_BASIS_STRAIN_PCT}:
            return False
        if self._automation_step_note == "setup_preload":
            return abs(float(error_value)) <= abs(float(tolerance))
        if self._current_sweep_freezes_live_stiffness():
            return abs(float(error_value)) <= abs(float(tolerance))
        return abs(float(error_value)) <= self._reversal_acceptance_tolerance(
            basis,
            tolerance,
            seek_key=seek_key,
        )

    def _current_sweep_protective_step_needed(
        self,
        previous_error: float | None,
        error_value: float,
        tolerance: float,
    ) -> bool:
        if previous_error is None or not self._current_sweep_freezes_live_stiffness():
            return False
        previous_abs = abs(float(previous_error))
        current_abs = abs(float(error_value))
        growth_margin = max(abs(float(tolerance)) * 0.2, 1e-9)
        if float(previous_error) * float(error_value) >= 0.0:
            return current_abs > previous_abs + growth_margin
        violent_reversal_margin = max(abs(float(tolerance)) * 3.0, previous_abs * 0.5)
        return current_abs > previous_abs + violent_reversal_margin

    def _current_sweep_travel_limit_exceeded(
        self,
        seek_key: tuple[str, int, float],
        next_travel_mm: float,
    ) -> bool:
        if not self._current_sweep_freezes_live_stiffness():
            return False
        if self._automation_phase == "current_hold":
            return False
        limit_mm = self._seek_max_travel_mm()
        current_travel_mm = self._seek_travel_by_key.get(seek_key, 0.0)
        return current_travel_mm + abs(float(next_travel_mm)) > limit_mm

    def _stop_for_current_sweep_travel_limit(
        self,
        seek_key: tuple[str, int, float],
        next_travel_mm: float,
    ) -> None:
        limit_mm = self._seek_max_travel_mm()
        current_travel_mm = self._seek_travel_by_key.get(seek_key, 0.0)
        self._log(
            "Recipe stopped because closed-loop load/stress correction exceeded the "
            f"correction travel limit ({_format_compact_unit(current_travel_mm + abs(float(next_travel_mm)), 'mm')} "
            f"> {_format_compact_unit(limit_mm, 'mm')})."
        )
        self._stop_auto_ramp(log_completion=False, offer_recovery=True)

    def _clear_seek_state(self, seek_key: tuple[str, int, float]) -> None:
        self._seek_last_error_by_key.pop(seek_key, None)
        self._seek_last_value_by_key.pop(seek_key, None)
        self._seek_last_time_by_key.pop(seek_key, None)
        self._seek_last_filtered_value_by_key.pop(seek_key, None)
        self._seek_out_of_band_since_by_key.pop(seek_key, None)
        self._seek_out_of_band_sign_by_key.pop(seek_key, None)
        self._seek_last_scale_timestamp_by_key.pop(seek_key, None)
        self._seek_post_move_sample_count_by_key.pop(seek_key, None)
        self._seek_last_effective_position_by_key.pop(seek_key, None)
        self._seek_no_response_count_by_key.pop(seek_key, None)
        self._seek_travel_by_key.pop(seek_key, None)
        self._seek_pending_reversal_by_key.pop(seek_key, None)

    def _filtered_signal_changed_after_last_correction(
        self,
        seek_key: tuple[str, int, float],
        filtered_signal: ScaleControlSignal | None,
        effective_tolerance: float,
    ) -> bool:
        if filtered_signal is None or seek_key not in self._seek_last_filtered_value_by_key:
            return True
        previous_value = self._seek_last_filtered_value_by_key[seek_key]
        change = abs(float(filtered_signal.value) - float(previous_value))
        required_change = max(
            abs(float(effective_tolerance)) * 0.25,
            abs(float(filtered_signal.noise)) * 0.25,
            1e-9,
        )
        if change > required_change:
            return True
        latest_s = self._latest_scale_sample_time_s()
        clock_key = seek_key[0], seek_key[1]
        last_s = self._seek_last_scale_timestamp_by_clock.get(clock_key)
        if latest_s is None or last_s is None:
            return False
        return latest_s - float(last_s) >= self._current_sweep_hold_filter_window_s()

    def _current_hold_error_is_persistent(
        self,
        seek_key: tuple[str, int, float],
        basis: str,
        error_value: float,
        effective_tolerance: float,
        filtered_signal: ScaleControlSignal | None,
    ) -> bool:
        if (
            not self._is_current_sweep_mode(self._automation_name)
            or self._automation_phase != "current_hold"
            or basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
        ):
            return True
        if filtered_signal is None:
            return False
        out_of_band_floor = self._current_sweep_hold_min_band_for_basis(
            basis,
            self._current_sweep_hold_min_pause_stress_mpa(),
        )
        if abs(float(error_value)) <= max(abs(float(effective_tolerance)), out_of_band_floor):
            self._seek_out_of_band_since_by_key.pop(seek_key, None)
            self._seek_out_of_band_sign_by_key.pop(seek_key, None)
            return True
        slope = float(filtered_signal.slope_per_s)
        recovery_band = max(
            abs(float(effective_tolerance)),
            self._current_sweep_hold_min_band_for_basis(
                basis,
                self._current_sweep_hold_min_resume_stress_mpa(),
            ),
            abs(float(filtered_signal.noise)) * self._current_sweep_hold_noise_sigma(),
        )
        remaining_error = abs(float(error_value)) - recovery_band
        moving_toward_target = float(error_value) * slope > 0.0
        if moving_toward_target and remaining_error > 0.0:
            min_slope = max(
                self._current_sweep_hold_min_slope_for_basis(basis),
                remaining_error / max(self._current_sweep_hold_filter_window_s() * 2.0, 1e-9),
            )
            if abs(slope) >= min_slope:
                return False
        moving_away_from_target = float(error_value) * slope < 0.0
        if moving_away_from_target and remaining_error > 0.0:
            min_slope = self._current_sweep_hold_min_slope_for_basis(basis)
            if abs(slope) >= min_slope:
                self._seek_out_of_band_since_by_key.pop(seek_key, None)
                self._seek_out_of_band_sign_by_key.pop(seek_key, None)
                return True
        large_error_band = max(
            out_of_band_floor,
            self._current_sweep_hold_entry_band_for_basis(effective_tolerance),
        ) * SERVO_CURRENT_SWEEP_HOLD_NOISY_LARGE_ERROR_FACTOR
        if abs(float(error_value)) > large_error_band:
            self._seek_out_of_band_since_by_key.pop(seek_key, None)
            self._seek_out_of_band_sign_by_key.pop(seek_key, None)
            return True
        sign = math.copysign(1.0, float(error_value))
        previous_sign = self._seek_out_of_band_sign_by_key.get(seek_key)
        timestamp_s = float(filtered_signal.timestamp_s)
        if previous_sign != sign:
            self._seek_out_of_band_sign_by_key[seek_key] = sign
            self._seek_out_of_band_since_by_key[seek_key] = timestamp_s
            return False
        since_s = self._seek_out_of_band_since_by_key.get(seek_key)
        if since_s is None:
            self._seek_out_of_band_since_by_key[seek_key] = timestamp_s
            return False
        return timestamp_s - float(since_s) >= SERVO_CURRENT_SWEEP_HOLD_CORRECTION_CONFIRM_S

    def _scale_control_signal_for_basis(self, basis: str, *, window_s: float | None = None) -> ScaleControlSignal | None:
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return None
        latest = self._scale_signal_buffer.latest()
        if latest is None:
            return None
        window = (
            self._current_sweep_hold_filter_window_s()
            if window_s is None
            else max(0.001, float(window_s))
        )
        samples = self._scale_signal_buffer.recent_samples(
            now_s=latest.timestamp_s,
            window_s=window,
        )
        if len(samples) < 3:
            return None
        loads = [float(sample.applied_load_g) for sample in samples]
        median_load = statistics.median(loads)
        deviations = [abs(value - median_load) for value in loads]
        mad_load = statistics.median(deviations) if deviations else 0.0
        robust_noise_load = 1.4826 * mad_load
        mean_time = sum(sample.timestamp_s for sample in samples) / len(samples)
        mean_load = sum(loads) / len(loads)
        denominator = sum((sample.timestamp_s - mean_time) ** 2 for sample in samples)
        slope_load_s = 0.0
        if denominator > 0.0:
            slope_load_s = sum(
                (sample.timestamp_s - mean_time) * (load - mean_load)
                for sample, load in zip(samples, loads, strict=False)
            ) / denominator
        if basis == HSW_BASIS_LOAD_G:
            return ScaleControlSignal(
                value=float(median_load),
                latest_value=float(loads[-1]),
                noise=max(0.0, float(robust_noise_load)),
                slope_per_s=float(slope_load_s),
                sample_count=len(samples),
                timestamp_s=float(latest.timestamp_s),
            )
        config = self._control_config()
        diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
        median_stress = stress_mpa_from_load_g(float(median_load), diameter_mm)
        latest_stress = stress_mpa_from_load_g(float(loads[-1]), diameter_mm)
        noise_stress = stress_mpa_from_load_g(max(0.0, float(robust_noise_load)), diameter_mm)
        slope_stress = stress_mpa_from_load_g(float(slope_load_s), diameter_mm)
        if median_stress is None or latest_stress is None:
            return None
        return ScaleControlSignal(
            value=float(median_stress),
            latest_value=float(latest_stress),
            noise=0.0 if noise_stress is None else abs(float(noise_stress)),
            slope_per_s=0.0 if slope_stress is None else float(slope_stress),
            sample_count=len(samples),
            timestamp_s=float(latest.timestamp_s),
        )

    def _seek_filtered_control_signal(self, basis: str) -> ScaleControlSignal | None:
        if not self._is_current_sweep_mode(self._automation_name):
            return None
        if self._automation_phase not in {"current", "current_hold"}:
            return None
        return self._scale_control_signal_for_basis(basis)

    def _current_sweep_filtered_window_spans_target(
        self,
        basis: str,
        target_value: float,
        tolerance: float,
    ) -> bool:
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return True
        latest = self._scale_signal_buffer.latest()
        if latest is None:
            return False
        samples = self._scale_signal_buffer.recent_samples(
            now_s=latest.timestamp_s,
            window_s=self._current_sweep_hold_filter_window_s(),
        )
        if len(samples) < 3:
            return False
        values: list[float] = []
        if basis == HSW_BASIS_LOAD_G:
            values = [float(sample.applied_load_g) for sample in samples]
        else:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            for sample in samples:
                stress = stress_mpa_from_load_g(float(sample.applied_load_g), diameter_mm)
                if stress is not None and math.isfinite(float(stress)):
                    values.append(float(stress))
        if len(values) < 3:
            return False
        padding = max(0.0, abs(float(tolerance)))
        target = float(target_value)
        return min(values) <= target + padding and max(values) >= target - padding

    def _seek_step_mm(self, error_value: float, tolerance: float, *, basis: str | None = None) -> float:
        if self._automation_name == RECOVERY_LOAD:
            interval_s = self._seek_decision_interval_s(basis)
            config = self._control_config()
            speed_mm_s = config.motion_speed_mm_s if config is not None else float(self.spin_motion_speed_mm_s.value())
            max_step_mm = float(speed_mm_s) * interval_s
            return max(self._motor_step_mm(), max_step_mm * self._servo_landing_factor(error_value, tolerance))
        if self._automation_step_note in {"setup_preload", "setup_return_zero"}:
            interval_s = self._seek_decision_interval_s(basis)
            max_step_mm = self._setup_motion_speed_cap_mm_s() * interval_s
            return max(self._motor_step_mm(), max_step_mm * self._servo_landing_factor(error_value, tolerance))
        max_step_mm = max(self._motor_step_mm(), self._seek_nudge_mm())
        factor = self._servo_landing_factor(error_value, tolerance)
        return max(self._motor_step_mm(), max_step_mm * factor)

    def _seek_speed_mm_s(
        self,
        error_value: float,
        tolerance: float,
        *,
        basis: str | None = None,
        seek_key: tuple[str, int, float] | None = None,
        current_value: float | None = None,
    ) -> float:
        base_speed = self._motion_speed_for_current_context(manual_jog=False)
        if self._automation_name == RECOVERY_LOAD:
            return max(self._minimum_held_speed_mm_s(), base_speed)
        if self._automation_step_note == "setup_return_zero":
            return self._setup_return_zero_speed_mm_s(basis, current_value)
        if (
            current_value is not None
            and basis is not None
            and self._setup_preload_takeup_active(
                basis,
                current_value,
                error_value,
                tolerance,
                seek_key=seek_key,
            )
        ):
            return min(base_speed, self._setup_slack_speed_mm_s())
        if (
            self._is_current_sweep_mode(self._automation_name)
            and basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA, HSW_BASIS_STRAIN_PCT}
        ):
            if self._current_sweep_hold_fast_recovery_needed(basis, error_value):
                return self._current_sweep_dynamic_speed_cap_mm_s()
            sensitivity = self._basis_sensitivity_per_mm(basis, seek_key=seek_key)
            if sensitivity is not None and abs(float(sensitivity)) > 0.0:
                away_rate = 0.0
                if seek_key is not None and current_value is not None and abs(float(error_value)) > 0.0:
                    previous_value = self._seek_last_value_by_key.get(seek_key)
                    previous_time_s = self._seek_last_time_by_key.get(seek_key)
                    if previous_value is not None and previous_time_s is not None:
                        dt_s = max(1e-6, time.monotonic() - float(previous_time_s))
                        rate_value_s = (float(current_value) - float(previous_value)) / dt_s
                        error_sign = math.copysign(1.0, float(error_value))
                        away_rate = max(0.0, -error_sign * rate_value_s)
                requested_value_rate_s = (
                    SERVO_CURRENT_SWEEP_ERROR_GAIN_PER_S * abs(float(error_value))
                    + SERVO_CURRENT_SWEEP_RATE_GAIN * away_rate
                )
                speed_mm_s = requested_value_rate_s / abs(float(sensitivity))
                landing_cap_mm_s = max(
                    self._minimum_held_speed_mm_s(),
                    base_speed * self._servo_landing_factor(error_value, tolerance),
                )
                return max(
                    self._current_sweep_min_command_speed_mm_s(),
                    min(base_speed, landing_cap_mm_s, speed_mm_s),
                )
        factor = self._servo_landing_factor(error_value, tolerance)
        return max(self._minimum_held_speed_mm_s(), base_speed * factor)

    def _setup_preload_ramp_rate_for_current_value(
        self,
        basis: str,
        current_value: float | None,
        target_value: float | None,
    ) -> float:
        config = self._control_config()
        duration_s = max(
            0.1,
            config.setup_preload_duration_s if config is not None else float(self.spin_setup_preload_duration_s.value()),
        )
        if current_value is None or target_value is None:
            return self._setup_preload_ramp_rate_mpa_s()
        if basis == HSW_BASIS_STRESS_MPA:
            delta_mpa = abs(float(target_value) - float(current_value))
        elif basis == HSW_BASIS_LOAD_G:
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            current_mpa = stress_mpa_from_load_g(float(current_value), diameter_mm)
            target_mpa = stress_mpa_from_load_g(float(target_value), diameter_mm)
            if current_mpa is None or target_mpa is None:
                return self._setup_preload_ramp_rate_mpa_s()
            delta_mpa = abs(float(target_mpa) - float(current_mpa))
        else:
            return self._setup_preload_ramp_rate_mpa_s()
        return max(self._setup_preload_ramp_rate_mpa_s(), delta_mpa / duration_s)

    def _target_ramp_rate_value_s_for_context(
        self,
        basis: str,
        *,
        current_value: float | None = None,
        target_value: float | None = None,
    ) -> float | None:
        if self._automation_phase != "target_ramp":
            return None
        if self._automation_step_note == "setup_preload" and basis == HSW_BASIS_STRESS_MPA:
            return self._setup_preload_ramp_rate_for_current_value(basis, current_value, target_value)
        if self._active_target_ramp_rate_value_s is not None:
            return abs(float(self._active_target_ramp_rate_value_s))
        if self._is_current_sweep_mode(self._automation_name) and basis in {
            HSW_BASIS_LOAD_G,
            HSW_BASIS_STRESS_MPA,
            HSW_BASIS_STRAIN_PCT,
        }:
            config = self._control_config()
            value = (
                config.current_sweep_target_ramp_rate_value_s
                if config is not None
                else float(self.spin_current_sweep_target_ramp_rate.value())
            )
            return abs(float(value))
        return None

    def _target_ramp_speed_cap_mm_s(
        self,
        basis: str,
        *,
        seek_key: tuple[str, int, float],
        current_value: float | None = None,
        target_value: float | None = None,
    ) -> float | None:
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA, HSW_BASIS_STRAIN_PCT}:
            return None
        ramp_rate = self._target_ramp_rate_value_s_for_context(
            basis,
            current_value=current_value,
            target_value=target_value,
        )
        if ramp_rate is None or ramp_rate <= 0.0:
            return None
        sensitivity = self._basis_sensitivity_per_mm(basis, seek_key=seek_key)
        if sensitivity is None or not math.isfinite(float(sensitivity)) or abs(float(sensitivity)) <= 0.0:
            return None
        if (
            self._is_current_sweep_mode(self._automation_name)
            and self._automation_step_note != "setup_preload"
            and current_value is not None
            and target_value is not None
        ):
            near_cap = self._current_sweep_basis_value_from_stress_cap(
                basis,
                SERVO_CURRENT_SWEEP_NEAR_CORRECTION_STRESS_MPA,
            )
            error_value = abs(float(target_value) - float(current_value))
            ramp_gate = abs(float(ramp_rate)) * self._seek_decision_interval_s(basis) * 2.0
            if near_cap is not None and error_value > max(near_cap, ramp_gate):
                return None
        return max(self._minimum_held_speed_mm_s(), abs(float(ramp_rate)) / abs(float(sensitivity)))

    def _seek_feedback_dead_time_s(self, basis: str | None) -> float:
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return 0.0
        return SERVO_MOTION_SETTLE_AFTER_MOVE_S + self._seek_decision_interval_s(basis)

    def _seek_feedback_compensated_speed_mm_s(
        self,
        desired_average_speed_mm_s: float,
        move_distance_mm: float,
        *,
        basis: str | None,
        cruise_mode: bool,
    ) -> float:
        desired_speed = max(
            self._minimum_held_speed_mm_s(),
            abs(float(desired_average_speed_mm_s)),
        )
        hard_cap = max(
            self._minimum_held_speed_mm_s(),
            self._motion_speed_for_current_context(manual_jog=False),
        )
        desired_speed = min(desired_speed, hard_cap)
        if cruise_mode:
            return desired_speed
        dead_time_s = self._seek_feedback_dead_time_s(basis)
        move_distance = abs(float(move_distance_mm))
        if dead_time_s <= 0.0 or move_distance <= 0.0:
            return desired_speed
        desired_cycle_s = move_distance / max(desired_speed, 1e-9)
        moving_time_s = desired_cycle_s - dead_time_s
        if moving_time_s <= 1e-9:
            return hard_cap
        compensated_speed = move_distance / moving_time_s
        return max(
            self._minimum_held_speed_mm_s(),
            min(hard_cap, max(desired_speed, compensated_speed)),
        )

    def _seek_command_step_mm(
        self,
        nudge_mm: float,
        speed_mm_s: float,
        *,
        basis: str | None = None,
        cruise_mode: bool = False,
    ) -> float:
        if cruise_mode:
            return max(self._motor_step_mm(), abs(float(nudge_mm)))
        if (
            self._is_current_sweep_mode(self._automation_name)
            and self._automation_step_note not in {"setup_preload", "setup_return_zero"}
        ):
            return max(
                self._motor_step_mm(),
                min(abs(nudge_mm), self._current_sweep_max_correction_mm()),
            )
        if self._automation_step_note == "setup_preload" and basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return max(self._motor_step_mm(), abs(float(nudge_mm)))
        speed_limited_step = self._seek_speed_limited_step_mm(basis, speed_mm_s)
        return max(self._motor_step_mm(), min(abs(nudge_mm), speed_limited_step))

    def _seek_error_key(self, basis: str, target_value: float) -> tuple[str, int, float]:
        plateau = -1 if self._automation_plateau_index is None else int(self._automation_plateau_index)
        return basis, plateau, round(float(target_value), 9)

    def _automation_tolerance_for_step(self, step: AutomationStep) -> float:
        return self._auto_requested_tolerance_for_basis(step.basis)

    def _log_waiting_for_feedback(self, message: str) -> None:
        now_s = time.monotonic()
        if now_s - self._last_feedback_wait_log_s >= 2.0:
            self._last_feedback_wait_log_s = now_s
            self._log(message)

    def _latest_scale_sample_time_s(self) -> float | None:
        timestamp_s = self._latest_scale_timestamp
        if timestamp_s is None:
            return None
        try:
            return float(timestamp_s)
        except (TypeError, ValueError):
            return None

    def _seek_has_unused_scale_sample(self, seek_key: tuple[str, int, float]) -> bool:
        latest_s = self._latest_scale_sample_time_s()
        if latest_s is None:
            return True
        clock_key = seek_key[0], seek_key[1]
        last_s = self._seek_last_scale_timestamp_by_clock.get(clock_key)
        return last_s is None or latest_s > float(last_s) + 1e-9

    def _seek_supports_cruise_feedback(self, basis: str) -> bool:
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return False
        if self._automation_step_note == "setup_preload":
            return False
        if self._automation_step_note == "setup_return_zero" or self._is_recovery_mode():
            return False
        if self._end_zero_fallback_armed:
            return False
        if self._is_current_sweep_mode(self._automation_name) and self._automation_phase != "current":
            return False
        if self._is_calibration_mode(self._automation_name) and self._automation_step_note != "setup_preload":
            return False
        return True

    def _seek_cruise_feedback_allowed(
        self,
        basis: str,
        error_value: float,
        tolerance: float,
        *,
        speed_mm_s: float,
        seek_key: tuple[str, int, float],
        previous_error: float | None,
        setup_preload_relaxation: bool = False,
    ) -> bool:
        if self._automation_step_note == "setup_preload":
            return False
        if not self._seek_supports_cruise_feedback(basis):
            return False
        if abs(float(error_value)) <= abs(float(tolerance)):
            return False
        if previous_error is not None:
            if float(previous_error) * float(error_value) < 0.0:
                return False
            if abs(float(error_value)) > abs(float(previous_error)) + max(abs(float(tolerance)) * 0.2, 1e-9):
                return False
        sensitivity = self._basis_sensitivity_per_mm(basis, seek_key=seek_key)
        if sensitivity is None or not math.isfinite(float(sensitivity)) or abs(float(sensitivity)) <= 0.0:
            return False
        sensitivity = abs(float(sensitivity))
        remaining_mm = abs(float(error_value)) / sensitivity
        tolerance_mm = abs(float(tolerance)) / sensitivity
        feedback_travel_mm = (
            self._seek_travel_during_interval_mm(speed_mm_s, basis)
            * SERVO_CRUISE_FEEDBACK_SAFETY_FACTOR
        )
        safety_margin_mm = max(
            self._motor_step_mm() * 2.0,
            max(
                0.0,
                float(
                    self._control_config().backlash_mm
                    if self._control_config() is not None
                    else self.spin_backlash_mm.value()
                ),
            ),
            tolerance_mm,
        )
        return remaining_mm > feedback_travel_mm + safety_margin_mm + tolerance_mm

    def _seek_backlash_takeup_mm(self, movement_direction: float) -> float:
        if not self._use_backlash_compensation_for_current_recipe():
            return 0.0
        config = self._control_config()
        backlash_mm = max(0.0, float(config.backlash_mm if config is not None else self.spin_backlash_mm.value()))
        if backlash_mm <= 0.0:
            return 0.0
        if self._last_move_direction == 0.0 or math.copysign(1.0, movement_direction) == math.copysign(1.0, self._last_move_direction):
            return 0.0
        return backlash_mm

    def _seek_requires_fresh_after_last_move(
        self,
        basis: str,
        *,
        setup_preload_relaxation: bool = False,
    ) -> bool:
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return False
        return not self._seek_supports_cruise_feedback(basis)

    def _seek_required_post_move_samples(
        self,
        basis: str,
        error_value: float,
        tolerance: float,
        *,
        seek_key: tuple[str, int, float],
    ) -> int:
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return 0
        if self._seek_supports_cruise_feedback(basis):
            return 0
        sensitivity = self._basis_sensitivity_per_mm(basis, seek_key=seek_key)
        if sensitivity is None or not math.isfinite(float(sensitivity)) or abs(float(sensitivity)) <= 0.0:
            return 1
        step_value = abs(float(sensitivity)) * self._motor_step_mm()
        very_near_value = max(abs(float(tolerance)) * 3.0, step_value * 2.0)
        near_cap = self._current_sweep_basis_value_from_stress_cap(
            basis,
            SERVO_CURRENT_SWEEP_NEAR_CORRECTION_STRESS_MPA,
        )
        if near_cap is not None:
            very_near_value = max(very_near_value, abs(float(near_cap)))
        if abs(float(error_value)) <= very_near_value:
            return 2
        return 1

    def _seek_wait_for_required_post_move_samples(
        self,
        seek_key: tuple[str, int, float],
        required_samples: int,
    ) -> bool:
        if required_samples <= 1:
            return False
        latest_s = self._latest_scale_sample_time_s()
        if latest_s is None:
            return True
        count = self._seek_post_move_sample_count_by_key.get(seek_key, 0)
        was_short = count < required_samples
        clock_key = seek_key[0], seek_key[1]
        last_s = self._seek_last_scale_timestamp_by_clock.get(clock_key)
        if last_s is None or latest_s > float(last_s) + 1e-9:
            count = min(required_samples, count + 1)
            self._seek_post_move_sample_count_by_key[seek_key] = count
            self._seek_last_scale_timestamp_by_key[seek_key] = latest_s
            self._seek_last_scale_timestamp_by_clock[clock_key] = latest_s
        return was_short

    def _setup_preload_overload_exceeded(
        self,
        basis: str,
        target_value: float,
        current_value: float,
        effective_tolerance: float,
    ) -> bool:
        if (
            self._automation_step_note != "setup_preload"
            or basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
        ):
            return False
        target_load_g = self._basis_value_as_load_g(basis, target_value)
        current_load_g = self._basis_value_as_load_g(basis, current_value)
        tolerance_load_g = self._basis_value_as_load_g(basis, effective_tolerance) or 0.0
        if target_load_g is None or current_load_g is None:
            return False
        if float(current_load_g) > float(target_load_g) + abs(float(tolerance_load_g)):
            return False
        allowed_load_g = max(
            float(target_load_g) + abs(float(tolerance_load_g)) * 4.0,
            abs(float(target_load_g)) * SETUP_PRELOAD_OVERLOAD_FACTOR,
            SETUP_PRELOAD_TAKEUP_LOAD_G * 10.0,
        )
        return abs(float(current_load_g)) > allowed_load_g

    def _stop_for_setup_preload_overload(self, basis: str, target_value: float, current_value: float) -> None:
        suffix, _ = self._distribution_units(basis)
        message = (
            "Setup preload stopped for overload: "
            f"live {HSW_BASIS_LABELS.get(basis, basis)} {_format_compact_number(current_value)}{suffix} "
            f"exceeded the setup target {_format_compact_number(target_value)}{suffix}."
        )
        self._log(message)
        self._stop_auto_ramp(log_completion=False, offer_recovery=False)

    def _seek_uses_planned_motion_base(self, basis: str) -> bool:
        if self._automation_phase != "target_ramp":
            return False
        return basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}

    def _seek_motion_base_mm(self, basis: str) -> float:
        if self._seek_uses_planned_motion_base(basis):
            return self._last_move_target_mm
        if basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA} and self._last_motion_command_time_s is not None:
            return self._last_move_target_mm
        return self._current_position_mm

    def _setup_zero_fallback_is_pending(self) -> bool:
        return (
            self._automation_step_note == "setup_return_zero"
            and self._setup_zero_fallback_return_position_mm is not None
        )

    def _setup_zero_stable_duration_s(self) -> float:
        return SETUP_ZERO_FALLBACK_MIN_TIME_S

    def _accept_pending_linear_zero_plateau_if_stable(self) -> bool:
        if self._setup_zero_fallback_reason != "linear_unload_slack":
            return False
        return self._accept_stable_setup_zero_plateau()

    def _accept_stable_setup_zero_plateau(self) -> bool:
        return_points = self._length_setup_points[max(0, int(self._setup_return_zero_start_point_index)) :]
        if len(return_points) < SETUP_ZERO_FALLBACK_MIN_POINTS:
            return False
        stable_window_s = self._setup_zero_stable_duration_s()
        final_raw_g = float(return_points[-1].raw_load_g)
        recent_points: list[MeasurementPoint] = []
        for point in reversed(return_points):
            if abs(float(point.raw_load_g) - final_raw_g) > SETUP_ZERO_FALLBACK_RAW_SPAN_G:
                break
            recent_points.append(point)
        recent_points.reverse()
        if len(recent_points) < SETUP_ZERO_FALLBACK_MIN_POINTS:
            return False
        elapsed_values = [float(point.elapsed_s) for point in recent_points]
        if max(elapsed_values) - min(elapsed_values) < stable_window_s:
            return False
        recent_raw_values = [float(point.raw_load_g) for point in recent_points]
        raw_span_g = max(recent_raw_values) - min(recent_raw_values)
        if raw_span_g > SETUP_ZERO_FALLBACK_RAW_SPAN_G:
            return False
        plateau_raw_g = 0.5 * (min(recent_raw_values) + max(recent_raw_values))
        residual_load_g = abs(self._effective_load_from_raw_g(plateau_raw_g))
        if residual_load_g > SETUP_ZERO_FALLBACK_MAX_RESIDUAL_G:
            return False
        zero_position_mm = float(self._current_position_mm)
        self._set_run_zero_load_scale_reference(float(plateau_raw_g), reason="setup linear-unload near-zero plateau")
        self._setup_zero_position_mm = zero_position_mm
        self._setup_zero_fallback_raw_g = float(plateau_raw_g)
        self._setup_zero_fallback_return_position_mm = None
        self._log(
            "Accepted stable near-zero load plateau during setup return: "
            f"{_format_compact_unit(abs(residual_load_g), 'g')} residual over "
            f"{_format_duration(max(elapsed_values) - min(elapsed_values))}; "
            f"using current position {_format_compact_unit(zero_position_mm, 'mm')} for l0."
        )
        return True

    def _maybe_accept_setup_zero_plateau_without_more_travel(
        self,
        basis: str,
        target_value: float,
    ) -> bool:
        if self._automation_step_note != "setup_return_zero":
            return False
        if self._setup_zero_position_mm is not None or self._setup_zero_fallback_return_position_mm is not None:
            return False
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return False
        target_load_g = self._basis_value_as_load_g(basis, target_value)
        if target_load_g is None or target_load_g > self._zero_return_acceptance_tolerance_g():
            return False
        current_load_g = abs(self._current_effective_load_g())
        if current_load_g <= self._zero_return_acceptance_tolerance_g():
            return False
        if current_load_g > SETUP_ZERO_FALLBACK_MAX_RESIDUAL_G:
            return False
        return self._accept_stable_setup_zero_plateau()

    def _handle_pending_setup_zero_fallback(self) -> bool:
        target_mm = self._setup_zero_fallback_return_position_mm
        if target_mm is None:
            return True
        self._record_length_setup_point()
        try:
            self._refresh_tic_status()
        except Exception:
            pass
        if abs(float(self._current_position_mm) - float(target_mm)) <= self._motor_step_mm():
            self._setup_zero_fallback_return_position_mm = None
            if self._setup_zero_fallback_reason == "linear_unload_slack":
                self._log("Returned to the linear-unload zero-stress position for l0.")
            else:
                self._log("Returned to zero-load plateau position for l0.")
            return True
        if self._accept_pending_linear_zero_plateau_if_stable():
            return True
        if self._setup_zero_fallback_reason == "linear_unload_slack":
            self._log_waiting_for_feedback("Returning to the linear-unload zero-stress position before computing l0.")
        else:
            self._log_waiting_for_feedback("Returning to zero-load plateau position before computing l0.")
        return False

    def _zero_fallback_min_travel_mm(self) -> float:
        config = self._control_config()
        length_mm = max(0.001, config.initial_length_mm if config is not None else float(self.spin_initial_length.value()))
        strain_travel_mm = length_mm * (SETUP_ZERO_FALLBACK_MIN_STRAIN_PCT / 100.0)
        motor_travel_mm = self._motor_step_mm() * SETUP_ZERO_FALLBACK_MIN_MOTOR_STEPS
        return max(strain_travel_mm, motor_travel_mm)

    def _zero_fallback_plateau_motion_ready(
        self,
        plateau_points: Sequence[MeasurementPoint],
    ) -> tuple[bool, float, float, float]:
        if not plateau_points:
            return False, 0.0, 0.0, self._zero_fallback_min_travel_mm()
        plateau_positions = [point.raw_position_mm for point in plateau_points]
        travel_mm = max(plateau_positions) - min(plateau_positions)
        elapsed_values = [point.elapsed_s for point in plateau_points]
        elapsed_s = max(elapsed_values) - min(elapsed_values)
        min_travel_mm = self._zero_fallback_min_travel_mm()
        ready = (
            abs(travel_mm) >= min_travel_mm
            and elapsed_s >= SETUP_ZERO_FALLBACK_MIN_TIME_S
        )
        return ready, abs(travel_mm), elapsed_s, min_travel_mm

    def _maybe_start_setup_zero_plateau_fallback(
        self,
        basis: str,
        current_value: float,
        tolerance: float,
    ) -> bool:
        if self._automation_step_note != "setup_return_zero" or basis != HSW_BASIS_LOAD_G:
            return False
        if self._setup_zero_fallback_return_position_mm is not None:
            return True
        residual_load_g = abs(float(current_value))
        min_plateau_residual_g = SERVO_AUTO_TOLERANCE_LOAD_G
        if residual_load_g < min_plateau_residual_g:
            return False
        if residual_load_g > SETUP_ZERO_FALLBACK_MAX_RESIDUAL_G:
            return False
        return_points = self._length_setup_points[self._setup_return_zero_start_point_index :]
        if len(return_points) < SETUP_ZERO_FALLBACK_MIN_POINTS:
            return False
        recent_points = return_points[-SETUP_ZERO_FALLBACK_MIN_POINTS:]
        recent_raw_values = [point.raw_load_g for point in recent_points]
        raw_span_g = max(recent_raw_values) - min(recent_raw_values)
        raw_tolerance_g = max(SETUP_ZERO_FALLBACK_RAW_SPAN_G, float(tolerance) * 0.25)
        if raw_span_g > raw_tolerance_g:
            return False
        plateau_raw_g = 0.5 * (min(recent_raw_values) + max(recent_raw_values))
        plateau_residual_g = abs(self._effective_load_from_raw_g(plateau_raw_g))
        if plateau_residual_g < min_plateau_residual_g:
            return False
        plateau_points = [
            point for point in return_points if abs(point.raw_load_g - plateau_raw_g) <= raw_tolerance_g
        ]
        if len(plateau_points) < SETUP_ZERO_FALLBACK_MIN_POINTS:
            return False
        plateau_ready, travel_mm, elapsed_s, min_travel_mm = self._zero_fallback_plateau_motion_ready(
            plateau_points
        )
        if not plateau_ready:
            return False
        plateau_first_position_mm = float(plateau_points[0].raw_position_mm)
        if plateau_first_position_mm is None:
            return False

        self._set_run_zero_load_scale_reference(float(plateau_raw_g), reason="setup zero-load plateau")
        self._setup_zero_position_mm = float(self._current_position_mm)
        self._setup_zero_fallback_raw_g = float(plateau_raw_g)
        self._setup_zero_fallback_return_position_mm = None
        self._setup_zero_fallback_reason = "zero_load_plateau"
        self._log(
            "Detected zero-load plateau at "
            f"{float(plateau_raw_g):.5f} g after "
            f"{_format_compact_unit(abs(travel_mm), 'mm')} of return travel "
            f"over {_format_duration(elapsed_s)} "
            f"(threshold {_format_compact_unit(min_travel_mm, 'mm')} and "
            f"{_format_duration(SETUP_ZERO_FALLBACK_MIN_TIME_S)}); "
            "using it as this run's zero-load reference and accepting the current "
            f"{_format_compact_unit(float(self._current_position_mm), 'mm')} position for l0."
        )
        return True

    def _end_zero_fallback_is_pending(self) -> bool:
        return self._end_zero_fallback_return_position_mm is not None

    def _handle_pending_end_zero_fallback(self) -> bool:
        target_mm = self._end_zero_fallback_return_position_mm
        if target_mm is None:
            return True
        if self._automation_name == RECOVERY_LOAD:
            self._record_recovery_point()
        try:
            self._refresh_tic_status()
        except Exception:
            pass
        if abs(float(self._current_position_mm) - float(target_mm)) <= self._motor_step_mm():
            self._end_zero_fallback_return_position_mm = None
            self._end_zero_fallback_armed = False
            self._log("Returned to zero-load plateau position; zero-load return accepted.")
            return True
        self._log_waiting_for_feedback("Returning to zero-load plateau position before finishing zero-load recovery.")
        return False

    def _end_zero_fallback_points(self) -> list[MeasurementPoint]:
        start_index = max(0, int(self._end_zero_fallback_start_point_index))
        if self._automation_name == RECOVERY_LOAD:
            return self._recovery_points[start_index:]
        return self._session_points[start_index:]

    def _maybe_start_end_zero_plateau_fallback(
        self,
        basis: str,
        target_value: float,
        tolerance: float,
    ) -> bool:
        if not self._end_zero_fallback_armed or basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return False
        if self._end_zero_fallback_return_position_mm is not None:
            return True
        target_load_g = self._basis_value_as_load_g(basis, target_value)
        tolerance_load_g = self._basis_value_as_load_g(basis, tolerance)
        if target_load_g is None or tolerance_load_g is None:
            return False
        if target_load_g > max(tolerance_load_g, SETUP_ZERO_FALLBACK_MIN_RESIDUAL_G):
            return False
        residual_load_g = abs(self._current_effective_load_g())
        min_plateau_residual_g = SERVO_AUTO_TOLERANCE_LOAD_G
        if residual_load_g < min_plateau_residual_g:
            return False
        if residual_load_g > SETUP_ZERO_FALLBACK_MAX_RESIDUAL_G:
            return False
        return_points = self._end_zero_fallback_points()
        if len(return_points) < SETUP_ZERO_FALLBACK_MIN_POINTS:
            return False
        recent_points = return_points[-SETUP_ZERO_FALLBACK_MIN_POINTS:]
        recent_raw_values = [point.raw_load_g for point in recent_points]
        raw_span_g = max(recent_raw_values) - min(recent_raw_values)
        raw_tolerance_g = max(SETUP_ZERO_FALLBACK_RAW_SPAN_G, tolerance_load_g * 0.25)
        if raw_span_g > raw_tolerance_g:
            return False
        plateau_raw_g = 0.5 * (min(recent_raw_values) + max(recent_raw_values))
        plateau_residual_g = abs(self._effective_load_from_raw_g(plateau_raw_g))
        if plateau_residual_g < min_plateau_residual_g:
            return False
        plateau_points = [
            point for point in return_points if abs(point.raw_load_g - plateau_raw_g) <= raw_tolerance_g
        ]
        if len(plateau_points) < SETUP_ZERO_FALLBACK_MIN_POINTS:
            return False
        plateau_ready, travel_mm, elapsed_s, min_travel_mm = self._zero_fallback_plateau_motion_ready(
            plateau_points
        )
        if not plateau_ready:
            return False
        plateau_first_position_mm = float(plateau_points[0].raw_position_mm)
        if plateau_first_position_mm is None:
            return False

        self._set_run_zero_load_scale_reference(float(plateau_raw_g), reason="zero-load plateau return")
        self._end_zero_fallback_raw_g = float(plateau_raw_g)
        self._end_zero_fallback_return_position_mm = None
        self._end_zero_fallback_armed = False
        self._log(
            "Detected zero-load plateau at "
            f"{float(plateau_raw_g):.5f} g after "
            f"{_format_compact_unit(abs(travel_mm), 'mm')} of return travel "
            f"over {_format_duration(elapsed_s)} "
            f"(threshold {_format_compact_unit(min_travel_mm, 'mm')} and "
            f"{_format_duration(SETUP_ZERO_FALLBACK_MIN_TIME_S)}); "
            "using it as the corrected zero-load reference and accepting the current position."
        )
        return True

    def _seek_distribution_target(self, basis: str, target_value: float, tolerance: float) -> bool:
        if basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA} and not self._has_fresh_scale_reading():
            raise RuntimeError(
                "Scale feedback is stale; fix the scale connection before closed-loop load/stress control."
            )
        seek_key = self._seek_error_key(basis, target_value)
        if self._setup_zero_fallback_is_pending():
            return self._handle_pending_setup_zero_fallback()
        if self._end_zero_fallback_is_pending():
            return self._handle_pending_end_zero_fallback()
        current_value = self._current_distribution_value(basis, require_after_last_move=False)
        if current_value is None:
            if basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
                self._log_waiting_for_feedback("Waiting for a fresh scale reading before the next load/stress correction.")
                self._write_control_trace(
                    decision="wait",
                    basis=basis,
                    target_value=target_value,
                    tolerance=tolerance,
                    result="waiting",
                    reason="fresh_scale_reading",
                )
                return False
            current_value = 0.0
        filtered_signal = self._seek_filtered_control_signal(basis)
        if filtered_signal is not None:
            current_value = filtered_signal.value
        setup_preload_relaxation = self._setup_preload_relaxation_active(
            basis,
            current_value,
            target_value,
            tolerance,
        )
        delta_value = target_value - current_value
        require_after_last_move = self._seek_requires_fresh_after_last_move(
            basis,
            setup_preload_relaxation=setup_preload_relaxation,
        )
        if (
            require_after_last_move
            and basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            and not self._has_fresh_scale_reading(after_s=self._motion_feedback_ready_after_s())
        ):
            self._log_waiting_for_feedback("Waiting for post-move scale feedback before the next load/stress correction.")
            self._write_control_trace(
                decision="wait",
                basis=basis,
                target_value=target_value,
                current_value=current_value,
                error_value=target_value - current_value,
                tolerance=tolerance,
                result="waiting",
                reason="post_move_feedback",
            )
            return False
        early_recorded_seek_point = False
        if self._is_recovery_mode():
            self._record_recovery_point()
            early_recorded_seek_point = True
        elif self._automation_step_note == "setup_return_zero":
            self._record_length_setup_point()
            early_recorded_seek_point = True
        seek_sample_time_s = time.monotonic()
        current_effective_tensile_position_mm = self._current_effective_tensile_position_mm()
        self._update_live_seek_stiffness(seek_key, basis, current_value)
        effective_tolerance = self._seek_effective_tolerance(
            basis,
            tolerance,
            seek_key=seek_key,
        )
        acceptance_tolerance = self._seek_target_acceptance_tolerance(
            basis,
            tolerance,
            seek_key=seek_key,
        )
        self._update_setup_preload_engagement(seek_key, basis, current_value)
        if self._setup_preload_overload_exceeded(basis, target_value, current_value, effective_tolerance):
            self._stop_for_setup_preload_overload(basis, target_value, current_value)
            return False
        if self._current_sweep_mechanical_load_loss_detected(
            basis,
            target_value,
            current_value,
            effective_tolerance,
        ):
            if self._current_sweep_mechanical_slack_takeup_allowed():
                self._note_current_sweep_mechanical_slack_takeup(basis, target_value, current_value)
            else:
                self._stop_for_current_sweep_mechanical_load_loss(basis, target_value, current_value)
                return False
        if self._maybe_start_setup_unload_baseline_fallback():
            return False
        if self._maybe_start_setup_zero_plateau_fallback(basis, current_value, effective_tolerance):
            return False
        if self._maybe_start_end_zero_plateau_fallback(basis, target_value, effective_tolerance):
            return False
        self._maybe_accept_setup_zero_plateau_without_more_travel(basis, target_value)
        if (
            self._automation_step_note == "setup_return_zero"
            and self._setup_zero_position_mm is not None
            and self._setup_zero_fallback_return_position_mm is None
        ):
            if self._latest_scale_value_g is not None:
                self._set_run_zero_load_scale_reference(
                    float(self._latest_scale_value_g),
                    reason="setup linear-unload baseline committed",
                )
            self._clear_seek_state(seek_key)
            self._write_control_trace(
                decision="accept",
                basis=basis,
                target_value=target_value,
                current_value=current_value,
                error_value=delta_value,
                tolerance=acceptance_tolerance,
                sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                result="reached",
                reason="setup_l0_baseline_committed",
            )
            return True
        if abs(delta_value) <= acceptance_tolerance:
            if self._zero_return_requires_true_zero(basis, target_value):
                current_load_g = self._zero_return_current_load_g(basis, current_value)
                if current_load_g > self._zero_return_acceptance_tolerance_g():
                    self._log_waiting_for_feedback(
                        "Zero-load return is inside the inflated servo band, but load is not truly near zero yet."
                    )
                else:
                    self._clear_seek_state(seek_key)
                    self._write_control_trace(
                        decision="accept",
                        basis=basis,
                        target_value=target_value,
                        current_value=current_value,
                        error_value=delta_value,
                        tolerance=acceptance_tolerance,
                        sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                        result="reached",
                    )
                    return True
            else:
                self._clear_seek_state(seek_key)
                self._write_control_trace(
                    decision="accept",
                    basis=basis,
                    target_value=target_value,
                    current_value=current_value,
                    error_value=delta_value,
                    tolerance=acceptance_tolerance,
                    sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                    result="reached",
                )
                return True
        if filtered_signal is not None:
            noise_component = filtered_signal.noise * self._current_sweep_hold_noise_sigma()
            if self._is_current_sweep_mode(self._automation_name):
                noise_component = self._current_sweep_bounded_noise_band(
                    basis,
                    filtered_signal.noise,
                    acceptance_tolerance,
                )
            noise_band = max(
                acceptance_tolerance,
                noise_component,
                self._current_sweep_hold_min_band_for_basis(
                    basis,
                    self._current_sweep_hold_min_resume_stress_mpa(),
                ),
            )
            if self._automation_phase == "current_hold":
                noise_band = max(
                    noise_band,
                    self._current_sweep_hold_entry_band_for_basis(acceptance_tolerance),
                )
            if (
                abs(delta_value) <= noise_band
                and self._current_sweep_filtered_window_spans_target(
                    basis,
                    target_value,
                    acceptance_tolerance,
                )
            ):
                self._clear_seek_state(seek_key)
                self._write_control_trace(
                    decision="accept",
                    basis=basis,
                    target_value=target_value,
                    current_value=current_value,
                    error_value=delta_value,
                    tolerance=max(acceptance_tolerance, noise_band),
                    sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                    result="filtered_noise_band",
                    reason="filtered_control_signal",
                )
                return True
        if abs(delta_value) <= acceptance_tolerance and self._zero_return_requires_true_zero(basis, target_value):
            pass
        elif abs(delta_value) <= acceptance_tolerance:
            self._clear_seek_state(seek_key)
            self._write_control_trace(
                decision="accept",
                basis=basis,
                target_value=target_value,
                current_value=current_value,
                error_value=delta_value,
                tolerance=acceptance_tolerance,
                sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                result="reached",
            )
            return True
        if basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA} and not self._seek_has_unused_scale_sample(seek_key):
            self._log_waiting_for_feedback("Waiting for a new scale sample before the next load/stress correction.")
            self._write_control_trace(
                decision="wait",
                basis=basis,
                target_value=target_value,
                current_value=current_value,
                error_value=delta_value,
                tolerance=acceptance_tolerance,
                sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                result="waiting",
                reason="new_scale_sample",
            )
            return False
        if not self._current_hold_error_is_persistent(
            seek_key,
            basis,
            delta_value,
            acceptance_tolerance,
            filtered_signal,
        ):
            self._log_waiting_for_feedback(
                "Waiting for the current-hold load/stress error to persist before correcting."
            )
            self._write_control_trace(
                decision="wait",
                basis=basis,
                target_value=target_value,
                current_value=current_value,
                error_value=delta_value,
                tolerance=acceptance_tolerance,
                sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                result="waiting",
                reason="hold_error_not_persistent",
            )
            return False
        if (
            require_after_last_move
            and basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            and self._last_motion_command_time_s is not None
        ):
            if (
                not self._current_sweep_hold_fast_recovery_needed(basis, delta_value)
                and not self._filtered_signal_changed_after_last_correction(
                seek_key,
                filtered_signal,
                acceptance_tolerance,
                )
            ):
                self._log_waiting_for_feedback(
                    "Waiting for the filtered control signal to update before repeating the load/stress correction."
                )
                self._write_control_trace(
                    decision="wait",
                    basis=basis,
                    target_value=target_value,
                    current_value=current_value,
                    error_value=delta_value,
                    tolerance=acceptance_tolerance,
                    sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                    result="waiting",
                    reason="filtered_signal_unchanged",
                )
                return False
            required_samples = self._seek_required_post_move_samples(
                basis,
                delta_value,
                effective_tolerance,
                seek_key=seek_key,
            )
            if self._seek_wait_for_required_post_move_samples(seek_key, required_samples):
                self._log_waiting_for_feedback(
                    f"Waiting for {required_samples} fresh scale samples before the next fine correction."
                )
                self._write_control_trace(
                    decision="wait",
                    basis=basis,
                    target_value=target_value,
                    current_value=current_value,
                    error_value=delta_value,
                    tolerance=effective_tolerance,
                    sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                    required_fresh_samples=required_samples,
                    post_move_sample_count=self._seek_post_move_sample_count_by_key.get(seek_key, 0),
                    result="waiting",
                    reason=f"{required_samples}_fresh_scale_samples",
                )
                return False
        setup_preload_takeup = self._setup_preload_takeup_active(
            basis,
            current_value,
            delta_value,
            effective_tolerance,
            seek_key=seek_key,
        )
        if setup_preload_takeup:
            nudge_mm = self._seek_speed_limited_step_mm(
                basis,
                min(
                    self._motion_speed_for_current_context(manual_jog=False),
                    self._setup_slack_speed_mm_s(),
                ),
            )
            slack_cap_mm = self._setup_preload_max_slack_takeup_step_mm(basis, seek_key=seek_key)
            if slack_cap_mm is not None:
                nudge_mm = min(nudge_mm, slack_cap_mm)
        else:
            nudge_mm = self._predictive_seek_step_mm(
                basis,
                delta_value,
                effective_tolerance,
                seek_key=seek_key,
            )
        if nudge_mm <= 0.0:
            raise ValueError("Set a non-zero correction step.")
        zero_return_needs_more_motion = (
            self._zero_return_requires_true_zero(basis, target_value)
            and self._zero_return_current_load_g(basis, current_value)
            > self._zero_return_acceptance_tolerance_g()
        )
        previous_error = self._seek_last_error_by_key.get(seek_key)
        preliminary_speed_mm_s = self._seek_speed_mm_s(
            delta_value,
            effective_tolerance,
            basis=basis,
            seek_key=seek_key,
            current_value=current_value,
        )
        preliminary_ramp_speed_cap_mm_s = None
        if not setup_preload_takeup:
            preliminary_ramp_speed_cap_mm_s = self._target_ramp_speed_cap_mm_s(
                basis,
                seek_key=seek_key,
                current_value=current_value,
                target_value=target_value,
            )
        if preliminary_ramp_speed_cap_mm_s is not None:
            preliminary_speed_mm_s = min(preliminary_speed_mm_s, preliminary_ramp_speed_cap_mm_s)
        cruise_mode = self._seek_cruise_feedback_allowed(
            basis,
            delta_value,
            effective_tolerance,
            speed_mm_s=preliminary_speed_mm_s,
            seek_key=seek_key,
            previous_error=previous_error,
            setup_preload_relaxation=setup_preload_relaxation,
        )
        if (
            basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            and not cruise_mode
            and not self._has_fresh_scale_reading(after_s=self._motion_feedback_ready_after_s())
        ):
            self._log_waiting_for_feedback("Waiting for post-move scale feedback before near-target correction.")
            self._write_control_trace(
                decision="wait",
                basis=basis,
                target_value=target_value,
                current_value=current_value,
                error_value=delta_value,
                tolerance=effective_tolerance,
                sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                result="waiting",
                reason="near_target_post_move_feedback",
            )
            return False
        if early_recorded_seek_point:
            pass
        elif self._is_recovery_mode():
            self._record_recovery_point()
        elif self._automation_step_note in {"setup_preload", "setup_return_zero"}:
            self._record_length_setup_point()
        elif self._session_active and not self._maybe_record_scheduled_point(
            quiet=True,
            advance_heating=False,
            require_fresh_after_move=not cruise_mode and basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA},
        ):
            return False
        overshot_target = previous_error is not None and previous_error * delta_value < 0.0
        protective_single_step = self._current_sweep_protective_step_needed(
            previous_error,
            delta_value,
            effective_tolerance,
        )
        if overshot_target:
            if (
                filtered_signal is not None
                and self._is_current_sweep_mode(self._automation_name)
                and self._automation_phase in {"current", "current_hold"}
            ):
                reversal_sign = math.copysign(1.0, delta_value)
                pending_sign, pending_timestamp_s = self._seek_pending_reversal_by_key.get(
                    seek_key,
                    (0.0, None),
                )
                confirmed = (
                    pending_sign == reversal_sign
                    and pending_timestamp_s is not None
                    and filtered_signal.timestamp_s - float(pending_timestamp_s) >= 0.5
                )
                if not confirmed:
                    self._seek_pending_reversal_by_key[seek_key] = (
                        reversal_sign,
                        filtered_signal.timestamp_s,
                    )
                    self._seek_last_error_by_key[seek_key] = delta_value
                    self._seek_last_value_by_key[seek_key] = current_value
                    self._seek_last_time_by_key[seek_key] = seek_sample_time_s
                    self._seek_last_filtered_value_by_key[seek_key] = filtered_signal.value
                    latest_scale_sample_time_s = self._latest_scale_sample_time_s()
                    if latest_scale_sample_time_s is not None:
                        self._seek_last_scale_timestamp_by_key[seek_key] = latest_scale_sample_time_s
                        self._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = latest_scale_sample_time_s
                    self._log_waiting_for_feedback(
                        "Confirming filtered reversal before sending the opposite load/stress correction."
                    )
                    self._write_control_trace(
                        decision="wait",
                        basis=basis,
                        target_value=target_value,
                        current_value=current_value,
                        error_value=delta_value,
                        tolerance=effective_tolerance,
                        sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                        result="waiting",
                        reason="filtered_reversal_confirmation",
                    )
                    return False
                self._seek_pending_reversal_by_key.pop(seek_key, None)
            if (
                not zero_return_needs_more_motion
                and self._target_reversal_is_practical_hold(
                basis,
                delta_value,
                acceptance_tolerance,
                seek_key=seek_key,
                )
            ):
                self._clear_seek_state(seek_key)
                self._log(
                    "Load/stress target accepted after crossing target; "
                    "reverse correction skipped inside the physical reversal band."
                )
                self._write_control_trace(
                    decision="accept",
                    basis=basis,
                    target_value=target_value,
                    current_value=current_value,
                    error_value=delta_value,
                    tolerance=acceptance_tolerance,
                    sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                    result="reversal_hold",
                )
                return True
            if self._current_sweep_freezes_live_stiffness() or (
                self._automation_step_note == "setup_preload"
                and basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            ):
                sensitivity = self._basis_sensitivity_per_mm(basis, seek_key=seek_key)
                stress_cap_mm = (
                    None
                    if sensitivity is None
                    else self._current_sweep_max_stress_correction_mm(
                        basis,
                        sensitivity,
                        error_value=delta_value,
                        seek_key=seek_key,
                    )
                )
                if stress_cap_mm is not None:
                    nudge_mm = max(self._motor_step_mm(), min(nudge_mm, stress_cap_mm))
            else:
                nudge_mm = max(self._motor_step_mm(), self._seek_nudge_mm() * 0.25)
            self._seek_no_response_count_by_key[seek_key] = 0
            self._log(
                f"Overshoot detected at target {_format_compact_number(target_value)}"
                f"{self._distribution_units(basis)[0]}; switching to fine correction steps."
            )
        elif previous_error is not None:
            error_worsened = abs(delta_value) > abs(previous_error) + max(effective_tolerance * 0.2, 1e-9)
            if error_worsened:
                count = self._seek_no_response_count_by_key.get(seek_key, 0) + 1
                self._seek_no_response_count_by_key[seek_key] = count
                travel_mm = self._seek_travel_by_key.get(seek_key, 0.0)
                self._log(
                    f"Closed-loop feedback warning: {HSW_BASIS_LABELS.get(basis, basis)} moved away "
                    f"from target ({count}; correction travel {_format_compact_unit(travel_mm, 'mm')})."
                )
            else:
                self._seek_no_response_count_by_key[seek_key] = 0
        current_hold_moving_away_fast = (
            self._automation_phase == "current_hold"
            and basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            and filtered_signal is not None
            and float(delta_value) * float(filtered_signal.slope_per_s) < 0.0
            and abs(float(filtered_signal.slope_per_s)) >= self._current_sweep_hold_min_slope_for_basis(basis)
        )
        if (
            protective_single_step
            and not self._current_sweep_hold_fast_recovery_needed(basis, delta_value)
            and not current_hold_moving_away_fast
        ):
            nudge_mm = min(nudge_mm, self._motor_step_mm())
            self._log(
                "Closed-loop response worsened after the previous correction; "
                "using a protective single-step correction."
            )
        seek_direction = delta_value
        if basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA, HSW_BASIS_STRAIN_PCT}:
            seek_direction *= self._tension_motion_sign()
        movement_direction = math.copysign(1.0, seek_direction)
        speed_mm_s = preliminary_speed_mm_s
        nudge_mm = self._seek_command_step_mm(nudge_mm, speed_mm_s, basis=basis, cruise_mode=cruise_mode)
        backlash_takeup_mm = self._seek_backlash_takeup_mm(movement_direction)
        if (
            backlash_takeup_mm > 0.0
            and basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            and nudge_mm <= self._motor_step_mm() + 1e-12
        ):
            backlash_takeup_mm = 0.0
        if self._current_sweep_travel_limit_exceeded(seek_key, nudge_mm + backlash_takeup_mm):
            self._stop_for_current_sweep_travel_limit(seek_key, nudge_mm + backlash_takeup_mm)
            self._write_control_trace(
                decision="wait",
                basis=basis,
                target_value=target_value,
                current_value=current_value,
                error_value=delta_value,
                tolerance=effective_tolerance,
                sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                correction_mm=nudge_mm,
                backlash_mm=backlash_takeup_mm,
                result="stopped",
                reason="correction_travel_limit",
            )
            return False
        if not zero_return_needs_more_motion and not self._reverse_correction_is_worthwhile(
            basis,
            delta_value,
            effective_tolerance,
            backlash_takeup_mm,
            seek_key=seek_key,
        ):
            self._clear_seek_state(seek_key)
            self._log(
                "Load/stress target accepted within backlash-limited tolerance; "
                "reversal skipped because take-up would be larger than the predicted improvement."
            )
            self._write_control_trace(
                decision="accept",
                basis=basis,
                target_value=target_value,
                current_value=current_value,
                error_value=delta_value,
                tolerance=effective_tolerance,
                sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                backlash_mm=backlash_takeup_mm,
                result="backlash_limited",
            )
            return True
        chain_from_last_target = self._seek_uses_planned_motion_base(basis) or cruise_mode
        base_position_mm = self._seek_motion_base_mm(basis)
        effective_base_position_mm = self._measurement_effective_position_mm()
        if backlash_takeup_mm > 0.0 and self._current_sweep_freezes_live_stiffness():
            target_mm = base_position_mm + movement_direction * backlash_takeup_mm
            command_speed_mm_s = max(
                self._current_sweep_min_command_speed_mm_s(),
                min(
                    self._motion_speed_for_current_context(manual_jog=False),
                    self._current_sweep_dynamic_speed_cap_mm_s(),
                ),
            )
            self._log(
                f"Direction reversal: taking up {_format_compact_unit(backlash_takeup_mm, 'mm')} "
                "backlash take-up before the next load/stress correction."
            )
            if not self._move_to_position_mm(
                target_mm,
                chain_from_last_target=chain_from_last_target,
                effective_position_mm=effective_base_position_mm,
                speed_mm_s=command_speed_mm_s,
            ):
                return False
            self._seek_last_error_by_key[seek_key] = delta_value
            self._seek_last_value_by_key[seek_key] = current_value
            self._seek_last_time_by_key[seek_key] = seek_sample_time_s
            if filtered_signal is not None:
                self._seek_last_filtered_value_by_key[seek_key] = filtered_signal.value
            latest_scale_sample_time_s = self._latest_scale_sample_time_s()
            if latest_scale_sample_time_s is not None:
                self._seek_last_scale_timestamp_by_key[seek_key] = latest_scale_sample_time_s
                self._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = latest_scale_sample_time_s
            self._seek_post_move_sample_count_by_key[seek_key] = 0
            self._seek_last_effective_position_by_key[seek_key] = current_effective_tensile_position_mm
            self._seek_travel_by_key[seek_key] = (
                self._seek_travel_by_key.get(seek_key, 0.0) + abs(backlash_takeup_mm)
            )
            self._write_control_trace(
                decision="backlash_takeup",
                basis=basis,
                target_value=target_value,
                current_value=current_value,
                error_value=delta_value,
                tolerance=effective_tolerance,
                sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                correction_mm=0.0,
                backlash_mm=backlash_takeup_mm,
                command_speed_mm_s=command_speed_mm_s,
                required_fresh_samples=self._seek_required_post_move_samples(
                    basis,
                    delta_value,
                    effective_tolerance,
                    seek_key=seek_key,
                ),
                post_move_sample_count=0,
                target_mm=target_mm,
                effective_target_mm=effective_base_position_mm,
                result="move_sent",
            )
            return False
        target_mm = base_position_mm + movement_direction * (nudge_mm + backlash_takeup_mm)
        effective_target_mm = effective_base_position_mm + movement_direction * nudge_mm
        if backlash_takeup_mm > 0.0:
            self._log(
                f"Direction reversal: adding {_format_compact_unit(backlash_takeup_mm, 'mm')} backlash take-up."
            )
        if setup_preload_takeup or self._automation_step_note == "setup_preload":
            command_speed_mm_s = speed_mm_s
        else:
            command_speed_mm_s = self._seek_feedback_compensated_speed_mm_s(
                speed_mm_s,
                nudge_mm + backlash_takeup_mm,
                basis=basis,
                cruise_mode=cruise_mode,
            )
        if not self._move_to_position_mm(
            target_mm,
            chain_from_last_target=chain_from_last_target,
            effective_position_mm=effective_target_mm,
            speed_mm_s=command_speed_mm_s,
        ):
            self._write_control_trace(
                decision="correction",
                basis=basis,
                target_value=target_value,
                current_value=current_value,
                error_value=delta_value,
                tolerance=effective_tolerance,
                sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
                correction_mm=nudge_mm,
                backlash_mm=backlash_takeup_mm,
                command_speed_mm_s=command_speed_mm_s,
                required_fresh_samples=self._seek_required_post_move_samples(
                    basis,
                    delta_value,
                    effective_tolerance,
                    seek_key=seek_key,
                ),
                post_move_sample_count=0,
                target_mm=target_mm,
                effective_target_mm=effective_target_mm,
                result="move_blocked",
            )
            return False
        self._write_control_trace(
            decision="correction",
            basis=basis,
            target_value=target_value,
            current_value=current_value,
            error_value=delta_value,
            tolerance=effective_tolerance,
            sensitivity_per_mm=self._basis_sensitivity_per_mm(basis, seek_key=seek_key),
            correction_mm=nudge_mm,
            backlash_mm=backlash_takeup_mm,
            command_speed_mm_s=command_speed_mm_s,
            required_fresh_samples=self._seek_required_post_move_samples(
                basis,
                delta_value,
                effective_tolerance,
                seek_key=seek_key,
            ),
            post_move_sample_count=0,
            target_mm=target_mm,
            effective_target_mm=effective_target_mm,
            result="move_sent",
            reason="cruise" if cruise_mode else "gated",
        )
        self._seek_last_error_by_key[seek_key] = delta_value
        self._seek_last_value_by_key[seek_key] = current_value
        self._seek_last_time_by_key[seek_key] = seek_sample_time_s
        if filtered_signal is not None:
            self._seek_last_filtered_value_by_key[seek_key] = filtered_signal.value
        latest_scale_sample_time_s = self._latest_scale_sample_time_s()
        if latest_scale_sample_time_s is not None:
            self._seek_last_scale_timestamp_by_key[seek_key] = latest_scale_sample_time_s
            self._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = latest_scale_sample_time_s
        self._seek_post_move_sample_count_by_key[seek_key] = 0
        self._seek_last_effective_position_by_key[seek_key] = current_effective_tensile_position_mm
        self._seek_travel_by_key[seek_key] = (
            self._seek_travel_by_key.get(seek_key, 0.0) + abs(nudge_mm + backlash_takeup_mm)
        )
        return False

    def _update_recipe_mode_ui(self) -> None:
        mode = str(self.combo_recipe_mode.currentData() or "ramp")
        page_index = 5 if self._is_current_sweep_mode(mode) else {
            "ramp": 0,
            "cycle": 1,
            "hold": 2,
            "distribution": 3,
            CALIBRATION: 4,
            CALIBRATION_COPPER: 4,
            CONSTANT_CURRENT_STRAIN_SWEEP: 6,
        }.get(mode, 0)
        self.recipe_stack.setCurrentIndex(page_index)
        self.recipe_stack.setFixedHeight(self.recipe_stack.sizeHint().height())
        self.strain_setup_box.setVisible(True)
        self._refresh_equivalent_labels()
        self._update_setup_summary()
        if mode == "cycle":
            summary = (
                f"Plan: cyclic displacement, {self.spin_cycle_count.value()} cycle(s), "
                f"with ±{abs(self.spin_cycle_amplitude.value()):.4f} mm amplitude."
            )
            banner = "Cyclic displacement"
            summary = (
                f"Plan: cyclic displacement, {self.spin_cycle_count.value()} cycle(s), "
                f"+/-{_format_compact_unit(abs(self.spin_cycle_amplitude.value()), 'mm')} amplitude."
            )
        elif mode == "hold":
            summary = (
                f"Plan: displacement hold at {_format_compact_unit(self.spin_hold_target.value(), 'mm')} for "
                f"{_format_compact_unit(self.spin_hold_duration_s.value(), 's', decimals=1)}."
            )
            banner = "Displacement hold"
        elif mode == "distribution":
            basis = self._distribution_basis()
            suffix, _ = self._distribution_units(basis)
            summary = (
                f"Plan: Hsw plateau scan, {HSW_BASIS_LABELS.get(basis, basis)} {_format_compact_number(self.spin_distribution_start.value())}{suffix} "
                f"to {_format_compact_number(self.spin_distribution_end.value())}{suffix} in "
                f"{_format_compact_number(self.spin_distribution_step.value())}{suffix} steps; "
                f"{self.spin_distribution_points.value()} point(s)/plateau."
            )
            if self.check_distribution_return_sweep.isChecked():
                summary += " Includes a reverse sweep."
            summary += (
                f" Automatic tolerance {self._auto_tolerance_summary_text(basis)} with "
                f"{_format_compact_unit(self.spin_distribution_settle_s.value(), 's', decimals=2)} settling."
            )
            banner = "Hsw plateau scan"
            summary = (
                f"Plan: Hsw plateau scan, {HSW_BASIS_LABELS.get(basis, basis)} "
                f"{_format_compact_number(self.spin_distribution_start.value())}{suffix} to "
                f"{_format_compact_number(self.spin_distribution_end.value())}{suffix}; "
                f"{self.spin_distribution_points.value()} point(s)/plateau."
            )
        elif self._is_calibration_mode(mode):
            summary = (
                "Plan: calibration, load "
                f"{_format_compact_unit(self.spin_calibration_start_load_g.value(), 'g', decimals=3)} to "
                f"{_format_compact_unit(self.spin_calibration_end_load_g.value(), 'g', decimals=3)}; "
                f"seek with {_format_compact_unit(self.spin_calibration_preload_nudge_mm.value(), 'mm')} "
                f"steps at {_format_compact_unit(self.spin_calibration_preload_speed_mm_s.value(), 'mm/s', decimals=3)}, "
                f"then {self.spin_calibration_steps_per_direction.value()} forward/reverse micro-step(s) per preload."
            )
            if self._pre_measurement_setup_enabled(mode):
                setup_load_g = load_g_from_stress_mpa(
                    float(self.spin_setup_preload_stress_mpa.value()),
                    float(self.spin_diameter.value()),
                )
                load_text = "" if setup_load_g is None else f" ({_format_compact_unit(setup_load_g, 'g', decimals=4)})"
                summary += (
                    " Setup: 0 g load, "
                    f"{_format_compact_unit(self.spin_setup_preload_stress_mpa.value(), 'MPa', decimals=3)}"
                    f"{load_text} preload for length entry, then back to 0 g."
                )
            banner = "Calibration"
        elif self._is_current_sweep_mode(mode):
            basis = self._current_sweep_basis()
            self._update_current_sweep_basis_ui()
            suffix, _ = self._distribution_units(basis)
            summary = (
                f"Plan: {HSW_BASIS_LABELS.get(basis, basis)} "
                f"{_format_compact_number(self.spin_current_sweep_target_start.value())}{suffix} to "
                f"{_format_compact_number(self.spin_current_sweep_target_end.value())}{suffix} in "
                f"{_format_compact_number(self.spin_current_sweep_target_step.value())}{suffix} steps; current "
                f"{_format_compact_number(self.spin_current_sweep_start_mA.value(), decimals=2)} to "
                f"{_format_compact_unit(self.spin_current_sweep_end_mA.value(), 'mA', decimals=2)}."
            )
            summary += " Current returns at each plateau."
            if self.check_current_sweep_return_target.isChecked():
                summary += " Target returns to start."
            summary += f" Automatic hold tolerance {self._auto_tolerance_summary_text(basis)}."
            if basis == HSW_BASIS_LOAD_G:
                banner = "Iso-load current sweep"
            elif basis == HSW_BASIS_STRESS_MPA:
                banner = "Iso-stress current sweep"
            else:
                banner = "Iso-strain current sweep"
            summary = (
                f"Plan: {banner}, {HSW_BASIS_LABELS.get(basis, basis)} "
                f"{_format_compact_number(self.spin_current_sweep_target_start.value())}{suffix} to "
                f"{_format_compact_number(self.spin_current_sweep_target_end.value())}{suffix} at "
                f"{_format_compact_number(self.spin_current_sweep_target_ramp_rate.value())}{suffix}/s; current "
                f"{_format_compact_number(self.spin_current_sweep_start_mA.value(), decimals=2)} to "
                f"{_format_compact_unit(self.spin_current_sweep_end_mA.value(), 'mA', decimals=2)} at "
                f"{_format_compact_unit(self.spin_current_sweep_step_mA.value(), 'mA/s', decimals=2)}."
            )
            if self._pre_measurement_setup_enabled(mode):
                setup_load_g = load_g_from_stress_mpa(
                    float(self.spin_setup_preload_stress_mpa.value()),
                    float(self.spin_diameter.value()),
                )
                load_text = "" if setup_load_g is None else f" ({_format_compact_unit(setup_load_g, 'g', decimals=4)})"
                summary += (
                    " Setup: 0 g load, "
                    f"{_format_compact_unit(self.spin_setup_preload_stress_mpa.value(), 'MPa', decimals=3)}"
                    f"{load_text} preload for length entry, then back to 0 g."
                )
        elif self._is_constant_current_strain_sweep_mode(mode):
            self._update_constant_current_basis_ui()
            basis = self._constant_current_start_basis()
            suffix, _ = self._distribution_units(basis)
            step_suffix = "%" if self._constant_current_step_basis() == HSW_BASIS_STRAIN_PCT else "mm"
            summary = (
                f"Plan: constant-current stress-strain, current "
                f"{_format_compact_number(self.spin_constant_current_start_mA.value(), decimals=2)} to "
                f"{_format_compact_unit(self.spin_constant_current_end_mA.value(), 'mA', decimals=2)} in "
                f"{_format_compact_unit(self.spin_constant_current_step_mA.value(), 'mA', decimals=2)} steps; "
                f"seek {HSW_BASIS_LABELS.get(basis, basis)} "
                f"{_format_compact_number(self.spin_constant_current_start_target.value())}{suffix}, "
                f"then fixed {_format_compact_unit(abs(self.spin_constant_current_step_size.value()), step_suffix, decimals=4)} "
                f"mechanical steps until {_format_compact_number(self.spin_constant_current_end_target.value())}{suffix}."
            )
            if self.check_constant_current_return_to_start.isChecked():
                summary += " Steps back to the start target after each current."
            summary += (
                f" Holds/logs {_format_compact_unit(self.spin_constant_current_hold_s.value(), 's', decimals=2)} "
                "after each displacement step. No closed-loop corrections are applied during the linear steps."
            )
            if self._pre_measurement_setup_enabled(mode):
                setup_load_g = load_g_from_stress_mpa(
                    float(self.spin_setup_preload_stress_mpa.value()),
                    float(self.spin_diameter.value()),
                )
                load_text = "" if setup_load_g is None else f" ({_format_compact_unit(setup_load_g, 'g', decimals=4)})"
                summary += (
                    " Setup: 0 g load, "
                    f"{_format_compact_unit(self.spin_setup_preload_stress_mpa.value(), 'MPa', decimals=3)}"
                    f"{load_text} preload for length entry, then back to 0 g."
                )
            banner = "Constant-current stress-strain"
        else:
            summary = (
                f"Plan: displacement ramp of {_format_compact_unit(self.spin_ramp_distance.value(), 'mm')} "
                f"from the current position."
            )
            banner = "Displacement ramp"
        if self._is_current_sweep_mode(mode) or self._is_calibration_mode(mode) or self._is_constant_current_strain_sweep_mode(mode):
            if self._is_current_sweep_mode(mode):
                summary += " Recipe controls current."
            elif self._is_constant_current_strain_sweep_mode(mode):
                summary += " Recipe controls current and open-loop displacement steps."
            else:
                summary += " Recipe owns the hardware sequence."
        preload_text = (
            f" Strain zero waits for {_format_compact_unit(self.spin_preload_threshold_g.value(), 'g')} preload."
            if self.check_zero_on_preload.isChecked() and self.spin_preload_threshold_g.value() > 0
            else " Strain zero follows the current reference immediately."
        )
        if not self._is_current_sweep_mode(mode) and not self._is_calibration_mode(mode):
            summary += preload_text
        self.label_recipe_summary.setText(summary)
        self.label_recipe_banner.setText(banner)
        try:
            steps, _, interval_ms = self._build_automation_recipe()
            record_points, tick_count = self._estimate_recipe_points_and_ticks(steps, interval_ms)
            duration_s = (tick_count * interval_ms) / 1000.0
            self._recipe_estimated_points = record_points
            self.label_recipe_estimate.setText(
                f"Estimated points: {record_points} | Estimated duration: {_format_duration(duration_s)}"
            )
            self._recipe_idle_progress_text = (
                f"Estimated: {record_points} pts | {_format_duration(duration_s)}"
            )
            if not self._automation_active:
                self._automation_total_steps = tick_count
                self.recipe_progress.setRange(0, max(1, tick_count))
                self.recipe_progress.setValue(0)
                self.recipe_progress.setFormat(self._recipe_idle_progress_text)
        except Exception:
            self._recipe_estimated_points = 0
            self.label_recipe_estimate.setText("Estimated points: - | Estimated duration: -")
            self._recipe_idle_progress_text = "Recipe estimate unavailable"
            if not self._automation_active:
                self.recipe_progress.setRange(0, 100)
                self.recipe_progress.setValue(0)
                self.recipe_progress.setFormat(self._recipe_idle_progress_text)
        self._update_recipe_file_status()

    def _scheduled_log_point_count(self, *, duration_s: float, control_interval_s: float) -> int:
        effective_log_interval_s = max(control_interval_s, self._current_sweep_log_interval_ms() / 1000.0)
        return max(1, int(math.ceil(max(0.0, duration_s) / effective_log_interval_s)))

    def _tic_accel_decel_mm_s2(self) -> tuple[float, float] | None:
        status_text = self._tic_status_text
        if not status_text:
            return None
        accel_units = _extract_status_float(status_text, "Max acceleration")
        decel_units = _extract_status_float(status_text, "Max deceleration")
        if accel_units is None or not math.isfinite(float(accel_units)) or float(accel_units) <= 0.0:
            return None
        if decel_units is None or not math.isfinite(float(decel_units)) or float(decel_units) <= 0.0:
            decel_units = accel_units
        steps_per_mm = max(1e-9, float(self.spin_steps_per_mm.value()))
        accel_mm_s2 = float(accel_units) / 100.0 / steps_per_mm
        decel_mm_s2 = float(decel_units) / 100.0 / steps_per_mm
        if accel_mm_s2 <= 0.0 or decel_mm_s2 <= 0.0:
            return None
        return accel_mm_s2, decel_mm_s2

    def _motion_profile_duration_s(self, distance_mm: float, speed_mm_s: float) -> float | None:
        accel_decel = self._tic_accel_decel_mm_s2()
        if accel_decel is None:
            return None
        distance = abs(float(distance_mm))
        if distance <= 0.0:
            return 0.0
        speed = max(self._minimum_held_speed_mm_s(), abs(float(speed_mm_s)))
        accel_mm_s2, decel_mm_s2 = accel_decel
        accel_distance = (speed * speed) / (2.0 * accel_mm_s2)
        decel_distance = (speed * speed) / (2.0 * decel_mm_s2)
        if distance >= accel_distance + decel_distance:
            cruise_distance = distance - accel_distance - decel_distance
            return (speed / accel_mm_s2) + (cruise_distance / speed) + (speed / decel_mm_s2)
        peak_speed = math.sqrt(
            (2.0 * distance * accel_mm_s2 * decel_mm_s2) / (accel_mm_s2 + decel_mm_s2)
        )
        return (peak_speed / accel_mm_s2) + (peak_speed / decel_mm_s2)

    def _motion_profile_travel_mm(self, speed_mm_s: float, duration_s: float) -> float | None:
        accel_decel = self._tic_accel_decel_mm_s2()
        if accel_decel is None:
            return None
        duration = max(0.0, float(duration_s))
        if duration <= 0.0:
            return 0.0
        speed = max(self._minimum_held_speed_mm_s(), abs(float(speed_mm_s)))
        accel_mm_s2, _decel_mm_s2 = accel_decel
        accel_time = speed / accel_mm_s2
        accel_distance = (speed * speed) / (2.0 * accel_mm_s2)
        if duration <= accel_time:
            return 0.5 * accel_mm_s2 * duration * duration
        return accel_distance + (speed * (duration - accel_time))

    def _move_duration_s(self, distance_mm: float, speed_mm_s: float) -> float:
        speed = max(self._minimum_held_speed_mm_s(), abs(float(speed_mm_s)))
        profile_duration = self._motion_profile_duration_s(distance_mm, speed)
        if profile_duration is not None:
            return max(0.0, profile_duration)
        return max(0.0, abs(float(distance_mm)) / max(speed, 1e-9))

    def _control_summary_text(self) -> str:
        return f"control every {self._control_interval_ms()} ms, log every {self._log_interval_ms()} ms"

    def _reset_timed_step_state(self) -> None:
        self._active_timed_step_index = None
        self._active_timed_step_started_s = 0.0
        self._active_timed_move_sent = False

    def _timed_step_elapsed_s(self, step_index: int) -> float:
        now_s = time.monotonic()
        if self._active_timed_step_index != step_index:
            self._active_timed_step_index = step_index
            self._active_timed_step_started_s = now_s
            self._active_timed_move_sent = False
            return 0.0
        return max(0.0, now_s - self._active_timed_step_started_s)

    def _timed_step_finished(self, step: AutomationStep, step_index: int) -> bool:
        duration_s = max(0.0, float(step.duration_s or 0.0))
        if duration_s <= 0.0:
            self._reset_timed_step_state()
            return True
        if self._timed_step_elapsed_s(step_index) >= duration_s:
            self._reset_timed_step_state()
            return True
        return False

    def _record_scheduled_recipe_point(self, step: AutomationStep) -> bool:
        if self._is_recovery_mode() or self._is_calibration_mode(self._automation_name):
            return True
        return self._maybe_record_scheduled_point(
            quiet=True,
            advance_heating=False,
            require_fresh_after_move=step.basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA},
        )

    def _estimate_recipe_points_and_ticks(self, steps: Sequence[AutomationStep], interval_ms: int) -> tuple[int, int]:
        points = 0
        ticks = 0
        interval_s = max(0.001, float(interval_ms) / 1000.0)
        logging_enabled = not any(step.action == "start_session" for step in steps)
        is_calibration_recipe = any(step.action == "calibration_record" for step in steps)
        for step in steps:
            if step.action == "start_session":
                ticks += 1
                logging_enabled = True
                continue
            if step.action == "ramp_target":
                start_value = float(
                    step.target_start_value
                    if step.target_start_value is not None
                    else step.target_end_value
                    if step.target_end_value is not None
                    else step.target_value
                    if step.target_value is not None
                    else 0.0
                )
                end_value = float(
                    step.target_end_value
                    if step.target_end_value is not None
                    else step.target_value
                    if step.target_value is not None
                    else start_value
                )
                ramp_rate = max(
                    1e-9,
                    abs(float(step.target_ramp_rate_value_s or self.spin_current_sweep_target_ramp_rate.value())),
                )
                ramp_ticks = max(1, int(math.ceil((abs(end_value - start_value) / ramp_rate) / interval_s)))
                ticks += ramp_ticks
                if logging_enabled:
                    points += self._scheduled_log_point_count(
                        duration_s=abs(end_value - start_value) / ramp_rate,
                        control_interval_s=interval_s,
                    )
                continue
            if step.action == "sweep_current" and step.current_start_mA is not None and step.current_end_mA is not None:
                duration_s = self._current_sweep_nominal_duration_s(step)
                estimated_duration_s = duration_s + self._current_sweep_hold_estimate_s(step)
                sweep_ticks = max(1, int(math.ceil(estimated_duration_s / interval_s)))
                ticks += sweep_ticks
                if logging_enabled:
                    points += self._scheduled_log_point_count(
                        duration_s=estimated_duration_s,
                        control_interval_s=interval_s,
                    )
                continue
            if step.action == "mechanical_scan":
                max_steps = self._estimate_mechanical_scan_step_count(step)
                per_step_s = max(interval_s, float(step.duration_s or 0.0))
                ticks += max_steps * max(1, int(math.ceil(per_step_s / interval_s)))
                if logging_enabled:
                    points += max_steps * self._scheduled_log_point_count(
                        duration_s=per_step_s,
                        control_interval_s=interval_s,
                    )
                continue
            duration_s = max(0.0, float(step.duration_s or 0.0))
            if duration_s > 0.0:
                ticks += max(1, int(math.ceil(duration_s / interval_s)))
            else:
                ticks += 1
            if not logging_enabled:
                continue
            if step.action in {"record", "set_current", "calibration_record"}:
                points += 1
            elif (
                duration_s > 0.0
                and step.action in {"move", "settle", "calibration_move"}
                and not is_calibration_recipe
            ):
                points += self._scheduled_log_point_count(
                    duration_s=duration_s,
                    control_interval_s=interval_s,
                )
            elif step.action == "seek_target":
                points += 1
        return points, ticks

    def _estimate_mechanical_scan_step_count(self, step: AutomationStep) -> int:
        if step.mechanical_step_limit is not None:
            return max(1, int(step.mechanical_step_limit))
        if step.mechanical_step_basis == HSW_BASIS_STRAIN_PCT and step.basis == HSW_BASIS_STRAIN_PCT:
            try:
                step_value = abs(float(step.mechanical_step_value or 0.0))
                target_value = abs(float(step.target_value or 0.0))
            except (TypeError, ValueError):
                step_value = 0.0
                target_value = 0.0
            if step_value > 0.0:
                return max(1, int(math.ceil(target_value / step_value)))
        return 100

    def _current_sweep_nominal_duration_s(self, step: AutomationStep) -> float:
        if step.current_start_mA is None or step.current_end_mA is None:
            return 0.0
        ramp_rate = max(
            1e-9,
            abs(float(step.current_ramp_rate_mA_s or self.spin_current_sweep_step_mA.value())),
        )
        return abs(
            self._recipe_current_setpoint_mA(float(step.current_end_mA))
            - self._recipe_current_setpoint_mA(float(step.current_start_mA))
        ) / ramp_rate

    def _current_sweep_hold_estimate_s(self, step: AutomationStep) -> float:
        if not step.current_hold_enabled:
            return 0.0
        nominal_s = self._current_sweep_nominal_duration_s(step)
        config = self._control_config()
        stable_s = self._current_sweep_hold_setting(
            step.current_hold_resume_stable_s,
            config.current_sweep_hold_resume_stable_s
            if config is not None
            else float(self.spin_current_sweep_hold_resume_stable_s.value()),
            CURRENT_SWEEP_HOLD_RESUME_STABLE_S,
        )
        return min(
            CURRENT_SWEEP_HOLD_ESTIMATE_MAX_S,
            max(
                CURRENT_SWEEP_HOLD_ESTIMATE_MIN_S,
                nominal_s * CURRENT_SWEEP_HOLD_ESTIMATE_FRACTION,
                stable_s * 2.0,
            ),
        )

    def _remaining_current_sweep_steps(self) -> list[AutomationStep]:
        if not self._automation_steps:
            return []
        start_index = min(max(0, self._automation_index), len(self._automation_steps))
        return [
            step
            for step in self._automation_steps[start_index:]
            if step.action == "sweep_current" and step.current_start_mA is not None and step.current_end_mA is not None
        ]

    def _learned_current_sweep_extra_remaining_s(self) -> float:
        if not self._current_sweep_duration_overheads_s:
            return 0.0
        remaining_steps = self._remaining_current_sweep_steps()
        if not remaining_steps:
            return 0.0
        recent_overheads = self._current_sweep_duration_overheads_s[-4:]
        learned_overhead_s = statistics.median(recent_overheads)
        extra_s = 0.0
        for step in remaining_steps:
            extra_s += max(0.0, learned_overhead_s - self._current_sweep_hold_estimate_s(step))
        return extra_s

    def _record_current_sweep_duration(self, step: AutomationStep, *, finished_s: float) -> None:
        if self._active_current_sweep_wall_started_s <= 0.0:
            return
        actual_s = max(0.0, finished_s - self._active_current_sweep_wall_started_s)
        overhead_s = max(0.0, actual_s - self._current_sweep_nominal_duration_s(step))
        self._current_sweep_duration_overheads_s.append(overhead_s)
        if len(self._current_sweep_duration_overheads_s) > 12:
            self._current_sweep_duration_overheads_s = self._current_sweep_duration_overheads_s[-12:]

    def _estimated_recipe_remaining_s(self, *, value: int, total: int, elapsed_s: float) -> float | None:
        if total <= 0 or value >= total:
            return 0.0
        measured_remaining_s = None
        if value > 0 and elapsed_s > 0.0:
            measured_remaining_s = ((total - value) * elapsed_s) / max(1, value)
        if (
            self._automation_estimated_total_s <= 0.0
            and not self._current_sweep_duration_overheads_s
            and not self._remaining_current_sweep_steps()
        ):
            return measured_remaining_s
        interval_s = max(0.001, float(self._automation_interval_ms) / 1000.0)
        scheduled_remaining_s = max(0.0, (total - value) * interval_s)
        if self._automation_estimated_total_s > 0.0:
            scheduled_remaining_s = min(
                scheduled_remaining_s,
                max(0.0, self._automation_estimated_total_s - elapsed_s),
            )
        scheduled_remaining_s += self._learned_current_sweep_extra_remaining_s()
        if value <= 0 or elapsed_s <= 0.0:
            return scheduled_remaining_s

        progress_fraction = max(0.0, min(1.0, value / max(1, total)))
        if progress_fraction <= CURRENT_SWEEP_ETA_MEASURED_WEIGHT_START:
            measured_weight = 0.0
        elif progress_fraction >= CURRENT_SWEEP_ETA_MEASURED_WEIGHT_FULL:
            measured_weight = 1.0
        else:
            measured_weight = (
                (progress_fraction - CURRENT_SWEEP_ETA_MEASURED_WEIGHT_START)
                / (CURRENT_SWEEP_ETA_MEASURED_WEIGHT_FULL - CURRENT_SWEEP_ETA_MEASURED_WEIGHT_START)
            )
        return scheduled_remaining_s * (1.0 - measured_weight) + measured_remaining_s * measured_weight

    def _warn_if_scale_is_silent(self) -> None:
        if self._scale_thread is None or self._scale_no_data_hint_emitted:
            return
        connected_at_s = self._scale_connected_at_s
        if connected_at_s is None:
            return
        if self._latest_scale_timestamp is not None and self._latest_scale_timestamp >= connected_at_s:
            return
        self._scale_no_data_hint_emitted = True
        self._log(
            "Scale connected but no serial data arrived. G&G documentation says these balances need a "
            "DB9 null modem crossover, so a straight-through adapter/cable chain will stay silent."
        )

    def _probe_scale_port(self) -> None:
        if self._scale_thread is not None:
            QtWidgets.QMessageBox.information(
                self,
                APP_NAME,
                "Disconnect the live scale connection first, then run Probe scale.",
            )
            return

        port_name = str(self.combo_scale_port.currentData() or "").strip()
        if not port_name:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Select a scale serial port first.")
            return

        trials = [
            ("Passive listen", 600, b""),
            ("G&G request", 600, b"\x1bp"),
            ("G&G request", 9600, b"\x1bp"),
            ("G&G request+CRLF", 9600, b"\x1bp\r\n"),
        ]
        findings: list[str] = []
        errors: list[str] = []

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            for label, baudrate, payload in trials:
                try:
                    raw = _read_serial_bytes(
                        port_name,
                        baudrate=baudrate,
                        payload=payload,
                        total_wait_s=1.1,
                    )
                except Exception as exc:
                    errors.append(f"{label} @ {baudrate} baud failed: {exc}")
                    continue
                if raw:
                    findings.append(f"{label} @ {baudrate} baud returned {raw!r}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        if findings:
            for line in findings:
                self._log(f"Scale probe: {line}")
            return

        for line in errors:
            self._log(f"Scale probe: {line}")
        supported = ", ".join(str(value) for value in GNG_SUPPORTED_BAUDS)
        self._log(
            "Scale probe found no serial response on the selected port. Tested passive listen plus ESC+p "
            f"requests at 600 and 9600 baud. G&G docs list supported rates {supported} and warn that the "
            "balance needs a null modem crossover instead of a straight-through DB9 link."
        )

    def _append_return_to_origin(self, steps: list[AutomationStep]) -> list[AutomationStep]:
        return steps

    def _tic_motor_power_warning(self, vin_v: float | None) -> str | None:
        if vin_v is None:
            return "Tic VIN voltage is unknown; motor power could not be verified."
        if vin_v < TIC_MOTOR_POWER_MIN_V:
            return (
                f"Motor power appears off or too low: Tic VIN is {vin_v:.2f} V "
                f"(expected at least {TIC_MOTOR_POWER_MIN_V:.1f} V)."
            )
        return None

    def _refresh_tic_status(self) -> bool:
        controller = self._build_tic_controller()
        try:
            status_text = controller.get_status()
        except Exception as exc:
            self._last_tic_status_error = str(exc)
            self._log(f"Tic status failed: {exc}")
            self.label_tic_summary.setText(str(exc))
            self.label_card_motion.setText("Tic unavailable")
            self._status_timer.stop()
            self._last_tic_vin_v = None
            self._tic_motor_power_ok = False
            return False
        self._last_tic_status_error = None
        self._tic_status_text = status_text
        step_mode_text = _extract_status_value(status_text, "Step mode")
        if step_mode_text is not None and self._set_tic_step_mode_combo(step_mode_text):
            self._sync_tic_units_per_mm_from_full_steps(persist=False)
        vin_v = _extract_status_float(status_text, "VIN voltage")
        self._last_tic_vin_v = vin_v
        power_warning = self._tic_motor_power_warning(vin_v)
        self._tic_motor_power_ok = power_warning is None
        if power_warning and not self._tic_motor_power_warning_active:
            self._log(power_warning)
            self._tic_motor_power_warning_active = True
        elif power_warning is None:
            self._tic_motor_power_warning_active = False
        current_position_text = _extract_status_value(status_text, "Current position")
        if current_position_text is not None:
            current_position = _extract_first_int(current_position_text)
            if current_position is not None:
                previous_commanded_steps = self._last_commanded_position_steps
                self._current_position_steps = current_position
                self._current_position_mm = current_position / float(self.spin_steps_per_mm.value())
                self._last_tic_status_time_s = time.time()
                if previous_commanded_steps is not None and current_position == previous_commanded_steps:
                    self._effective_position_mm = self._last_effective_move_target_mm
                elif not self._has_unconfirmed_motion_command():
                    self._effective_position_mm = self._current_position_mm
                self._last_commanded_position_steps = current_position
        operation_state = _extract_status_value(status_text, "Operation state") or "unknown"
        errors = _extract_status_value(status_text, "Errors currently stopping the motor") or "none"
        vin_text = "-" if vin_v is None else f"{vin_v:.2f} V"
        summary = f"Operation state: {operation_state}\nVIN: {vin_text}\nErrors: {errors}"
        if power_warning:
            summary += f"\nWarning: {power_warning}"
            self.label_card_motion.setText(f"Motor power low/off | {vin_text}")
        else:
            self.label_card_motion.setText(
                f"{operation_state} | {self._tensile_displacement_mm(self._effective_position_mm):.4f} mm tensile | VIN {vin_text}"
            )
        self.label_tic_summary.setText(summary)
        self._refresh_tic_settings_summary()
        self._refresh_live_labels()
        self._status_timer.start(self._tic_status_interval_ms())
        return True

    def _zero_tic_position(self) -> None:
        try:
            dispatcher = self._build_tic_dispatcher()
            dispatcher.set_current_position(0)
            if not self._wait_for_tic_dispatcher(dispatcher, "zero-position", timeout_s=2.0):
                QtWidgets.QMessageBox.warning(self, APP_NAME, "Tic zero-position command did not finish cleanly.")
                return
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to set Tic position: {exc}")
            return
        self._current_position_steps = 0
        self._current_position_mm = 0.0
        self._last_commanded_position_steps = 0
        self._effective_position_mm = 0.0
        self._last_effective_move_target_mm = 0.0
        self._position_reference_mm = 0.0
        self._last_move_target_mm = 0.0
        self._manual_jog_uses_last_target = False
        self._last_move_direction = 0.0
        self._refresh_live_labels()
        self._log("Tic current position was set to 0.")
        self._refresh_tic_status()

    def _halt_tic(self) -> None:
        self._stop_manual_jog()
        self._stop_tic_keepalive()
        try:
            dispatcher = self._build_tic_dispatcher()
            dispatcher.halt_and_hold()
            if not self._wait_for_tic_dispatcher(dispatcher, "halt", timeout_s=2.0):
                QtWidgets.QMessageBox.warning(self, APP_NAME, "Tic halt command did not finish cleanly.")
                return
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to halt Tic: {exc}")
            return
        self._manual_jog_uses_last_target = False
        self._last_move_direction = 0.0
        self._log("Sent halt-and-hold to Tic.")
        self._refresh_tic_status()

    def _configure_manual_jog_button(
        self,
        button: QtWidgets.QPushButton,
        direction_getter: Callable[[], float],
    ) -> None:
        button.setAutoRepeat(False)
        button.pressed.connect(lambda: self._start_manual_jog(direction_getter()))
        button.released.connect(lambda: self._stop_manual_jog())
        button.clicked.connect(lambda: self._handle_manual_jog_button_clicked(direction_getter()))

    def _prepare_manual_jog_press(self) -> None:
        previous_motor_power_ok = self._tic_motor_power_ok
        refreshed = False
        recent_ok_status = (
            self._tic_motor_power_ok is True
            and self._last_tic_status_time_s is not None
            and time.time() - self._last_tic_status_time_s <= MANUAL_JOG_TIC_STATUS_FRESH_S
        )
        if recent_ok_status:
            refreshed = True
        else:
            try:
                refreshed = self._refresh_tic_status()
            except Exception:
                refreshed = False
        if not refreshed and previous_motor_power_ok is None:
            self._tic_motor_power_ok = None
        if self._has_unconfirmed_motion_command():
            return
        self._manual_jog_uses_last_target = False
        self._last_move_target_mm = self._current_position_mm
        self._effective_position_mm = self._current_position_mm
        self._last_effective_move_target_mm = self._effective_position_mm

    def _jog_relative(self, direction: float, *, force_step: bool = False) -> bool:
        direction = -1.0 if direction < 0.0 else 1.0
        now_s = time.monotonic()
        elapsed_s = None if self._manual_jog_last_tick_s is None else now_s - self._manual_jog_last_tick_s
        same_direction = self._manual_jog_direction == direction
        continuous_hold = self._manual_jog_timer.isActive()
        if (
            not force_step
            and elapsed_s is not None
            and same_direction
            and 0.0 < elapsed_s
            and (continuous_hold or elapsed_s < 0.5)
        ):
            self._manual_jog_pending_mm += abs(float(self.spin_motion_speed_mm_s.value()) * elapsed_s)
            min_step_mm = 1.0 / max(1.0, float(self.spin_steps_per_mm.value()))
            if self._manual_jog_pending_mm < min_step_mm:
                self._manual_jog_last_tick_s = now_s
                return False
            whole_steps = max(1, int(math.floor(self._manual_jog_pending_mm / min_step_mm + 1e-9)))
            distance_mm = whole_steps * min_step_mm
            self._manual_jog_pending_mm -= distance_mm
        else:
            distance_mm = abs(float(self.spin_jog_mm.value()))
            self._manual_jog_pending_mm = 0.0
        self._manual_jog_direction = direction
        self._manual_jog_last_tick_s = now_s
        distance_mm *= direction
        base_mm = self._relative_motion_base_mm()
        return self._move_to_position_mm(base_mm + distance_mm, manual_jog=True)

    def _start_manual_jog(self, direction: float) -> None:
        self._prepare_manual_jog_press()
        self._manual_jog_direction = -1.0 if direction < 0.0 else 1.0
        self._manual_jog_last_tick_s = time.monotonic()
        self._manual_jog_pending_mm = 0.0
        self._manual_jog_timer_moves = 0
        self._manual_jog_click_suppressed = False
        self._start_tic_keepalive()
        self._manual_jog_timer.start()

    def _stop_manual_jog(self) -> None:
        self._manual_jog_timer.stop()
        if self._manual_jog_timer_moves > 0:
            self._manual_jog_click_suppressed = True
        self._manual_jog_last_tick_s = None
        self._manual_jog_direction = 0.0
        self._manual_jog_pending_mm = 0.0
        self._manual_jog_timer_moves = 0
        if not self._automation_active:
            self._stop_tic_keepalive()

    def _handle_manual_jog_timer(self) -> None:
        if self._manual_jog_direction == 0.0:
            return
        if self._jog_relative(self._manual_jog_direction):
            self._manual_jog_timer_moves += 1

    def _handle_manual_jog_button_clicked(self, direction: float) -> None:
        if self._manual_jog_click_suppressed:
            self._manual_jog_click_suppressed = False
            return
        self._jog_relative(direction, force_step=True)

    def _show_manual_auto_connect_progress(self) -> None:
        progress = QtWidgets.QProgressDialog("Connecting hardware...", "", 0, 0, self)
        progress.setWindowTitle("Auto-connect hardware")
        progress.setCancelButton(None)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.setValue(0)
        progress.show()
        self._manual_auto_connect_progress = progress
        QtWidgets.QApplication.processEvents()

    def _set_manual_auto_connect_progress(self, label: str, value: int, maximum: int) -> None:
        progress = self._manual_auto_connect_progress
        if progress is None:
            return
        progress.setRange(0, max(0, int(maximum)))
        progress.setValue(max(0, min(int(value), max(0, int(maximum)))))
        progress.setLabelText(label)
        QtWidgets.QApplication.processEvents()

    def _close_manual_auto_connect_progress(self) -> None:
        progress = self._manual_auto_connect_progress
        self._manual_auto_connect_progress = None
        if progress is not None:
            progress.close()
            progress.deleteLater()

    def _auto_connect_manual_hardware(self) -> bool:
        if getattr(self, "_manual_auto_connect_active", False):
            return False
        self._manual_auto_connect_active = True
        if hasattr(self, "button_manual_auto_connect"):
            self.button_manual_auto_connect.setEnabled(False)
            self.button_manual_auto_connect.setText("Auto-connecting...")
        self._show_manual_auto_connect_progress()
        self._log("Manual hardware auto-connect started.")
        QtCore.QTimer.singleShot(0, self._run_manual_auto_connect_hardware)
        return True

    def _run_manual_auto_connect_hardware(self) -> None:
        connected = True
        steps = 3 + (1 if self._motor_supply_enabled() else 0)
        completed_steps = 0
        try:
            self._set_manual_auto_connect_progress("Connecting scale...", completed_steps, steps)
            if self._scale_thread is None:
                connected = self._ensure_scale_ready_for_recipe() and connected
            completed_steps += 1
            self._set_manual_auto_connect_progress("Preparing current-sweep supply channel...", completed_steps, steps)
            if not self._ensure_supply_ready_for_recipe():
                connected = False
            elif not self._prepare_current_sweep_supply_channel():
                connected = False
            completed_steps += 1
            if self._motor_supply_enabled():
                self._set_manual_auto_connect_progress("Preparing motor power supply...", completed_steps, steps)
                if not self._ensure_supply_ready_for_recipe():
                    connected = False
                elif not self._enable_motor_supply_output():
                    connected = False
                completed_steps += 1
            self._set_manual_auto_connect_progress("Connecting motor controller...", completed_steps, steps)
            if not self._ensure_tic_ready_for_recipe():
                connected = False
            completed_steps += 1
            if connected:
                self._set_manual_auto_connect_progress("Hardware auto-connect completed.", steps, steps)
                self._log("Manual hardware auto-connect completed.")
            else:
                self._set_manual_auto_connect_progress("Hardware auto-connect needs attention.", completed_steps, steps)
                self._log("Manual hardware auto-connect did not complete; check the hardware status cards.")
            self._refresh_live_labels()
        finally:
            self._close_manual_auto_connect_progress()
            self._manual_auto_connect_active = False
            if hasattr(self, "button_manual_auto_connect"):
                self.button_manual_auto_connect.setEnabled(True)
                self.button_manual_auto_connect.setText("Auto-connect hardware")

    def _start_tic_keepalive(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._start_tic_keepalive)
            return
        self._tic_keepalive_warning_active = False
        self._tic_keepalive_timer.setInterval(self._tic_keepalive_interval_ms())
        if not self._tic_keepalive_timer.isActive():
            self._tic_keepalive_timer.start()

    def _stop_tic_keepalive(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._stop_tic_keepalive)
            return
        self._tic_keepalive_timer.stop()
        self._tic_keepalive_warning_active = False

    def _handle_tic_keepalive_timer(self) -> None:
        if (
            not self._automation_active
            and not self._manual_jog_timer.isActive()
            and not self._motor_step_calibration_active
        ):
            self._stop_tic_keepalive()
            return
        if self._tic_motor_power_ok is False:
            return
        try:
            self._build_tic_dispatcher().reset_command_timeout()
            self._tic_keepalive_warning_active = False
        except Exception as exc:
            if not self._tic_keepalive_warning_active:
                self._log(f"Tic command-timeout keepalive failed: {exc}")
                self._tic_keepalive_warning_active = True

    def _has_unconfirmed_motion_command(self) -> bool:
        return (
            self._last_motion_command_time_s is not None
            and (
                self._last_tic_status_time_s is None
                or self._last_tic_status_time_s < self._last_motion_command_time_s
            )
        )

    def _commanded_motion_base_mm(self) -> float:
        if self._has_unconfirmed_motion_command():
            return self._last_move_target_mm
        return self._current_position_mm

    def _commanded_position_steps(self) -> int:
        if self._has_unconfirmed_motion_command() and self._last_commanded_position_steps is not None:
            return self._last_commanded_position_steps
        return self._current_position_steps

    def _measurement_position_mm(self) -> float:
        return self._commanded_motion_base_mm()

    def _measurement_effective_position_mm(self) -> float:
        if self._has_unconfirmed_motion_command():
            return self._last_effective_move_target_mm
        if abs(self._last_effective_move_target_mm - self._last_move_target_mm) <= 1e-12:
            self._effective_position_mm = self._current_position_mm
            self._last_effective_move_target_mm = self._effective_position_mm
        return self._effective_position_mm

    def _relative_motion_base_mm(self) -> float:
        return self._last_move_target_mm if self._manual_jog_uses_last_target else self._commanded_motion_base_mm()

    def _effective_max_load_limit_g(self) -> float | None:
        zero_load_limit_g = abs(self._zero_load_scale_reference_g())
        config = self._control_config()
        custom_limit_g = float(config.max_load_g if config is not None else self.spin_max_load_g.value())
        max_load_enabled = config.max_load_enabled if config is not None else self.check_max_load.isChecked()
        if max_load_enabled:
            return min(custom_limit_g, zero_load_limit_g) if zero_load_limit_g > 0.0 else custom_limit_g
        if zero_load_limit_g > 0.0:
            return zero_load_limit_g
        return None

    def _is_max_load_exceeded(self) -> bool:
        limit_g = self._effective_max_load_limit_g()
        if limit_g is None:
            return False
        return abs(self._current_effective_load_g()) > limit_g

    def _raw_scale_display_limit_g(self, *, live_widget: bool = False) -> float | None:
        config = None if live_widget else self._control_config()
        if config is not None:
            return config.raw_scale_limit_g
        if not hasattr(self, "spin_raw_scale_limit_g"):
            return RAW_SCALE_DISPLAY_LIMIT_DEFAULT_G
        limit_g = float(self.spin_raw_scale_limit_g.value())
        if not math.isfinite(limit_g) or limit_g <= 0.0:
            return None
        return limit_g

    def _is_raw_scale_display_limit_reached(self) -> bool:
        if self._latest_scale_timestamp is None:
            return False
        limit_g = self._raw_scale_display_limit_g()
        if limit_g is None:
            return False
        return float(self._latest_scale_value_g) >= limit_g

    def _raw_scale_limit_blocks_move(self, _position_mm: float) -> bool:
        return self._is_raw_scale_display_limit_reached()

    def _last_motion_increases_tension(self) -> bool:
        return self._last_move_direction * self._tension_motion_sign() > 0.0

    def _clear_motion_tracking_after_safety_halt(self) -> None:
        self._manual_jog_uses_last_target = False
        self._manual_jog_direction = 0.0
        self._manual_jog_last_tick_s = None
        self._manual_jog_pending_mm = 0.0
        self._last_move_direction = 0.0
        self._last_motion_command_time_s = None
        self._last_motion_expected_complete_time_s = None
        self._last_commanded_speed_mm_s = 0.0
        self._last_commanded_position_steps = self._current_position_steps
        self._last_move_target_mm = self._current_position_mm
        self._effective_position_mm = self._current_position_mm
        self._last_effective_move_target_mm = self._effective_position_mm

    def _halt_motion_for_safety(self, reason: str) -> bool:
        self._stop_manual_jog()
        try:
            dispatcher = self._build_tic_dispatcher()
            dispatcher.halt_and_hold()
            halted = self._wait_for_tic_dispatcher(dispatcher, "safety halt", timeout_s=2.0)
        except Exception as exc:
            self._log(f"{reason}; Tic safety halt failed: {exc}")
            return False
        self._clear_motion_tracking_after_safety_halt()
        if halted:
            self._log(reason)
        else:
            self._log(f"{reason}; Tic halt command is still pending.")
        return halted

    def _handle_raw_scale_display_limit_status(self) -> bool:
        if not self._is_raw_scale_display_limit_reached():
            self._raw_scale_display_limit_active = False
            return False
        limit_g = self._raw_scale_display_limit_g()
        reason = (
            f"Raw scale display safety stop: live display {self._latest_scale_value_g:.5f} g "
            f"is at or above the configured limit of {0.0 if limit_g is None else limit_g:.5f} g."
        )
        if not self._raw_scale_display_limit_active or self._last_move_direction != 0.0:
            self._halt_motion_for_safety(reason)
        self._raw_scale_display_limit_active = True
        if self._automation_active:
            self._stop_auto_ramp(log_completion=False, offer_recovery=False)
        return True

    def _handle_applied_load_limit_status(self) -> bool:
        if not self._is_max_load_exceeded():
            return False
        if not self._last_motion_increases_tension():
            return True
        limit_g = self._effective_max_load_limit_g()
        self._halt_motion_for_safety(
            f"Applied-load limit reached: live applied load {self._current_effective_load_g():.5f} g "
            f"is above the configured limit of {0.0 if limit_g is None else limit_g:.5f} g. "
            "The tension-increasing move was halted; relaxing moves remain allowed."
        )
        return True

    def _motion_speed_for_current_context(self, *, manual_jog: bool) -> float:
        config = self._control_config()
        if manual_jog:
            speed_mm_s = config.motion_speed_mm_s if config is not None else float(self.spin_motion_speed_mm_s.value())
            return max(self._minimum_held_speed_mm_s(), speed_mm_s)
        if self._automation_active:
            if self._is_recovery_mode(self._automation_name):
                speed_mm_s = config.motion_speed_mm_s if config is not None else float(self.spin_motion_speed_mm_s.value())
                return max(self._minimum_held_speed_mm_s(), speed_mm_s)
            if self._automation_step_note in {"setup_preload", "setup_return_zero"} or (
                self._automation_phase == "target_ramp"
                and self._is_calibration_mode(self._automation_name)
                and self._automation_basis == HSW_BASIS_STRESS_MPA
            ):
                return self._setup_motion_speed_cap_mm_s()
            if self._is_current_sweep_mode(self._automation_name):
                return max(
                    self._minimum_held_speed_mm_s(),
                    self._current_sweep_dynamic_speed_cap_mm_s(),
                )
            if self._is_constant_current_strain_sweep_mode(self._automation_name):
                speed_mm_s = (
                    config.distribution_seek_speed_mm_s
                    if config is not None
                    else float(self.spin_distribution_seek_speed_mm_s.value())
                )
                return max(self._minimum_held_speed_mm_s(), speed_mm_s)
            if self._is_calibration_mode(self._automation_name):
                if self._automation_phase == "seek":
                    speed_mm_s = (
                        config.calibration_preload_speed_mm_s
                        if config is not None
                        else float(self.spin_calibration_preload_speed_mm_s.value())
                    )
                    return max(
                        self._minimum_held_speed_mm_s(),
                        speed_mm_s,
                    )
                speed_mm_s = (
                    config.calibration_speed_mm_s
                    if config is not None
                    else float(self.spin_calibration_speed_mm_s.value())
                )
                return max(
                    self._minimum_held_speed_mm_s(),
                    speed_mm_s,
                )
            if self._automation_name == "distribution":
                speed_mm_s = (
                    config.distribution_seek_speed_mm_s
                    if config is not None
                    else float(self.spin_distribution_seek_speed_mm_s.value())
                )
                return max(
                    self._minimum_held_speed_mm_s(),
                    speed_mm_s,
                )
            if self._automation_name == "cycle":
                speed_mm_s = config.cycle_speed_mm_s if config is not None else float(self.spin_cycle_speed_mm_s.value())
                return max(self._minimum_held_speed_mm_s(), speed_mm_s)
            if self._automation_name == "hold":
                speed_mm_s = config.hold_speed_mm_s if config is not None else float(self.spin_hold_speed_mm_s.value())
                return max(self._minimum_held_speed_mm_s(), speed_mm_s)
            if self._automation_name == "ramp":
                speed_mm_s = config.ramp_speed_mm_s if config is not None else float(self.spin_ramp_speed_mm_s.value())
                return max(self._minimum_held_speed_mm_s(), speed_mm_s)
        mode = str(self.combo_recipe_mode.currentData() or "ramp")
        if self._is_current_sweep_mode(mode):
            return self._current_sweep_dynamic_speed_cap_mm_s()
        if self._is_constant_current_strain_sweep_mode(mode):
            return max(self._minimum_held_speed_mm_s(), float(self.spin_constant_current_move_speed_mm_s.value()))
        if self._is_calibration_mode(mode):
            return max(
                self._minimum_held_speed_mm_s(),
                float(self.spin_calibration_preload_speed_mm_s.value()),
            )
        if mode == "distribution":
            return max(
                self._minimum_held_speed_mm_s(),
                float(self.spin_distribution_seek_speed_mm_s.value()),
            )
        if mode == "cycle":
            return max(self._minimum_held_speed_mm_s(), float(self.spin_cycle_speed_mm_s.value()))
        if mode == "hold":
            return max(self._minimum_held_speed_mm_s(), float(self.spin_hold_speed_mm_s.value()))
        return max(self._minimum_held_speed_mm_s(), float(self.spin_ramp_speed_mm_s.value()))

    def _move_increases_tension(self, position_mm: float) -> bool:
        delta_mm = position_mm - self._relative_motion_base_mm()
        if abs(delta_mm) < 1e-12:
            return False
        return delta_mm * self._tension_motion_sign() > 0.0

    def _move_relative_raw_tic_steps(self, delta_steps: int, *, speed_steps_per_s: float) -> bool:
        if self._tic_motor_power_ok is False:
            vin_text = "-" if self._last_tic_vin_v is None else f"{self._last_tic_vin_v:.2f} V"
            self._log(
                "Raw-step move cancelled because Tic motor power is not ready "
                f"(VIN {vin_text}; expected at least {TIC_MOTOR_POWER_MIN_V:.1f} V)."
            )
            return False
        delta_steps = int(delta_steps)
        if delta_steps == 0:
            self._log("Raw-step move skipped because the requested step delta is zero.")
            return False
        steps_per_mm = max(1.0, float(self.spin_steps_per_mm.value()))
        start_steps = int(self._commanded_position_steps())
        target_steps = start_steps + delta_steps
        if target_steps == start_steps:
            return False
        target_mm = target_steps / steps_per_mm
        if self._raw_scale_limit_blocks_move(target_mm):
            limit_g = self._raw_scale_display_limit_g()
            self._log(
                f"Raw-step move cancelled because the raw scale display is "
                f"{self._latest_scale_value_g:.5f} g, at or above the safety limit of "
                f"{0.0 if limit_g is None else limit_g:.5f} g. Standard motion is blocked until "
                "the scale display is back below the limit."
            )
            return False
        if self._is_max_load_exceeded() and self._move_increases_tension(target_mm):
            load_g = abs(self._current_effective_load_g())
            limit_g = self._effective_max_load_limit_g()
            self._log(
                f"Raw-step move cancelled because it would increase applied load "
                f"{load_g:.5f} g beyond the safety limit of "
                f"{0.0 if limit_g is None else limit_g:.5f} g."
            )
            return False
        if self.check_soft_limits.isChecked():
            min_mm = min(float(self.spin_soft_min_mm.value()), float(self.spin_soft_max_mm.value()))
            max_mm = max(float(self.spin_soft_min_mm.value()), float(self.spin_soft_max_mm.value()))
            if target_mm < min_mm or target_mm > max_mm:
                self._log(
                    f"Raw-step move cancelled because the provisional target {target_mm:.4f} mm is outside "
                    f"soft limits [{min_mm:.4f}, {max_mm:.4f}] mm."
                )
                return False

        selected_speed_steps_per_s = max(1.0, abs(float(speed_steps_per_s)))
        max_speed_units = max(1, int(round(selected_speed_steps_per_s * 10000.0)))
        try:
            self._build_tic_dispatcher().set_target_position(target_steps, max_speed=max_speed_units)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to move Tic: {exc}")
            return False

        selected_speed_mm_s = selected_speed_steps_per_s / steps_per_mm
        expected_duration_s = self._move_duration_s(delta_steps / steps_per_mm, selected_speed_mm_s)
        self._log(
            f"Raw-step move command sent to {target_steps} steps "
            f"({delta_steps:+d} steps) at {selected_speed_steps_per_s:.3f} steps/s."
        )
        command_time_s = time.time()
        self._last_motion_command_time_s = command_time_s
        self._last_motion_expected_complete_time_s = (
            command_time_s + expected_duration_s + SERVO_MOTION_SETTLE_AFTER_MOVE_S
        )
        self._last_commanded_speed_mm_s = selected_speed_mm_s
        self._last_commanded_position_steps = target_steps
        self._last_effective_move_target_mm = target_mm
        self._last_move_target_mm = target_mm
        self._manual_jog_uses_last_target = True
        self._last_move_direction = math.copysign(1.0, delta_steps)
        self._start_tic_keepalive()
        self._refresh_live_labels()
        return True

    def _move_to_position_mm(
        self,
        position_mm: float,
        *,
        manual_jog: bool = False,
        chain_from_last_target: bool = False,
        effective_position_mm: float | None = None,
        speed_mm_s: float | None = None,
    ) -> bool:
        if self._tic_motor_power_ok is False:
            vin_text = "-" if self._last_tic_vin_v is None else f"{self._last_tic_vin_v:.2f} V"
            self._log(
                "Move cancelled because Tic motor power is not ready "
                f"(VIN {vin_text}; expected at least {TIC_MOTOR_POWER_MIN_V:.1f} V)."
            )
            return False
        if self._is_max_load_exceeded() and self._move_increases_tension(position_mm):
            load_g = abs(self._current_effective_load_g())
            limit_g = self._effective_max_load_limit_g()
            self._log(
                f"Move cancelled because it would increase applied load "
                f"{load_g:.5f} g beyond the safety limit of "
                f"{0.0 if limit_g is None else limit_g:.5f} g. Relaxing moves are still allowed."
            )
            return False
        if self._raw_scale_limit_blocks_move(position_mm):
            limit_g = self._raw_scale_display_limit_g()
            self._log(
                f"Move cancelled because the raw scale display is {self._latest_scale_value_g:.5f} g, "
                f"at or above the safety limit of {0.0 if limit_g is None else limit_g:.5f} g. "
                "Standard motion is blocked until the scale display is back below the limit."
            )
            if self._automation_active and not chain_from_last_target:
                self._stop_auto_ramp(log_completion=False, offer_recovery=False)
            return False
        config = self._control_config()
        soft_limits_enabled = (
            config.soft_limits_enabled
            if config is not None
            else self.check_soft_limits.isChecked()
        )
        if soft_limits_enabled:
            if config is not None:
                min_mm = min(config.soft_min_mm, config.soft_max_mm)
                max_mm = max(config.soft_min_mm, config.soft_max_mm)
            else:
                min_mm = min(float(self.spin_soft_min_mm.value()), float(self.spin_soft_max_mm.value()))
                max_mm = max(float(self.spin_soft_min_mm.value()), float(self.spin_soft_max_mm.value()))
            if position_mm < min_mm or position_mm > max_mm:
                self._log(
                    f"Move cancelled because {position_mm:.4f} mm is outside soft limits "
                    f"[{min_mm:.4f}, {max_mm:.4f}] mm."
                )
                if self._automation_active:
                    self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return False
        steps_per_mm = config.steps_per_mm if config is not None else float(self.spin_steps_per_mm.value())
        target_steps = int(round(position_mm * steps_per_mm))
        if target_steps == self._commanded_position_steps():
            min_step_mm = 1.0 / max(1.0, steps_per_mm)
            self._log(
                "Move skipped because the requested displacement rounds to the current motor step. "
                f"Use at least {_format_compact_unit(min_step_mm, 'mm')} with the current calibration."
            )
            return False
        selected_speed_mm_s = (
            self._motion_speed_for_current_context(manual_jog=manual_jog)
            if speed_mm_s is None
            else max(self._minimum_held_speed_mm_s(), float(speed_mm_s))
        )
        command_base_mm = self._relative_motion_base_mm()
        expected_duration_s = self._move_duration_s(position_mm - command_base_mm, selected_speed_mm_s)
        max_speed_units = max(1, int(round(selected_speed_mm_s * steps_per_mm * 10000.0)))
        try:
            self._build_tic_dispatcher().set_target_position(target_steps, max_speed=max_speed_units)
        except Exception as exc:
            if self._is_ui_thread():
                QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to move Tic: {exc}")
            else:
                self._log(f"Failed to move Tic: {exc}")
            return False
        self._log(
            f"Move command sent to {_format_compact_unit(position_mm, 'mm')} "
            f"({target_steps} steps) at {_format_compact_unit(selected_speed_mm_s, 'mm/s', decimals=3)}."
        )
        command_time_s = time.time()
        self._last_motion_command_time_s = command_time_s
        self._last_motion_expected_complete_time_s = (
            command_time_s + expected_duration_s + SERVO_MOTION_SETTLE_AFTER_MOVE_S
        )
        self._last_commanded_speed_mm_s = selected_speed_mm_s
        self._last_commanded_position_steps = target_steps
        self._start_tic_keepalive()
        self._last_effective_move_target_mm = (
            float(position_mm) if effective_position_mm is None else float(effective_position_mm)
        )
        delta_mm = position_mm - self._relative_motion_base_mm()
        if abs(delta_mm) >= 1e-12:
            self._last_move_direction = math.copysign(1.0, delta_mm)
        self._last_move_target_mm = position_mm
        if manual_jog or chain_from_last_target:
            self._manual_jog_uses_last_target = True
        else:
            self._manual_jog_uses_last_target = False
        self._refresh_live_labels()
        return True

    def _session_base_paths(self) -> tuple[Path, Path, Path, Path]:
        directory = Path(self.edit_log_dir.text().strip() or _default_download_dir())
        directory.mkdir(parents=True, exist_ok=True)
        basename = _clean_session_basename(self.edit_log_name.text())
        if basename != self.edit_log_name.text().strip():
            self.edit_log_name.setText(basename)
        return _session_paths_for_basename(directory, basename)

    def _current_session_identity_text(self, paths: Sequence[Path] | None = None) -> str:
        sample_name = self.edit_sample_name.text().strip() or "(unnamed sample)"
        log_name = _clean_session_basename(self.edit_log_name.text())
        output_folder = paths[0].parent if paths else Path(self.edit_log_dir.text().strip() or _default_download_dir()) / log_name
        return (
            f"Sample: {sample_name}\n"
            f"Base filename: {log_name or DEFAULT_LOG_BASENAME}\n"
            f"Output folder: {output_folder}"
        )

    def _ask_existing_output_action(self, paths: Sequence[Path]) -> str:
        existing_names = ", ".join(path.name for path in paths if path.exists())
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setText("An output folder or files already exist for this base filename.")
        box.setInformativeText(
            f"{self._current_session_identity_text(paths)}\n\n"
            f"Existing file(s): {existing_names or paths[0].name}\n\n"
            "Save as next run keeps the existing data and creates a new _run02, _run03, and so on folder."
        )
        next_button = box.addButton("Save as next run", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        replace_button = box.addButton("Replace existing", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(next_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is next_button:
            return OUTPUT_COLLISION_NEXT
        if clicked is replace_button:
            return OUTPUT_COLLISION_REPLACE
        if clicked is cancel_button:
            return OUTPUT_COLLISION_CANCEL
        return OUTPUT_COLLISION_CANCEL

    def _resolve_session_base_paths(self) -> tuple[Path, Path, Path, Path]:
        paths = self._session_base_paths()
        if not _session_paths_exist(paths):
            return paths
        action = self._ask_existing_output_action(paths)
        if action == OUTPUT_COLLISION_REPLACE:
            return paths
        if action == OUTPUT_COLLISION_NEXT:
            directory = paths[0].parent.parent
            basename = _clean_session_basename(self.edit_log_name.text())
            next_basename, next_paths = _next_run_session_paths(directory, basename)
            self.edit_log_name.setText(next_basename)
            self._log(f"Existing output preserved; using next run filename {next_basename}.")
            return next_paths
        raise RuntimeError("Session start cancelled because output files already exist.")

    def _prepare_session_files(
        self,
        *,
        created_utc: str,
    ) -> tuple[
        Any,
        Any,
        csv.DictWriter[str],
        Any,
        csv.DictWriter[str],
        Any,
        csv.DictWriter[str],
        Any,
        csv.DictWriter[str],
        Any,
        Any,
        csv.DictWriter[str],
        Path,
        Path,
        Path,
        Path,
        Path,
        Path,
        Path,
        Path,
    ]:
        txt_path, csv_path, json_path, raw_scale_path = self._resolve_session_base_paths()
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        setup_txt_path, setup_csv_path = _session_setup_paths_for_measurement(txt_path)

        txt_handle = txt_path.open("w", encoding="utf-8", newline="")
        csv_handle = csv_path.open("w", encoding="utf-8", newline="")
        raw_scale_handle = raw_scale_path.open("w", encoding="utf-8", newline="")
        setup_txt_handle = setup_txt_path.open("w", encoding="utf-8", newline="")
        setup_csv_handle = setup_csv_path.open("w", encoding="utf-8", newline="")
        control_trace_path = txt_path.parent / SESSION_CONTROL_TRACE_CSV
        control_trace_handle = control_trace_path.open("w", encoding="utf-8", newline="")
        ui_telemetry_path = txt_path.parent / SESSION_UI_TELEMETRY_CSV
        ui_telemetry_handle = ui_telemetry_path.open("w", encoding="utf-8", newline="")
        txt_handle.write("\t".join(LONG_NAMES) + "\n")
        txt_handle.write("\t".join(UNITS) + "\n")
        txt_handle.write(f"# Created UTC\t{created_utc}\n")
        txt_handle.write(f"# Sample\t{self.edit_sample_name.text().strip()}\n")
        txt_handle.write(f"# Notes\t{self.edit_run_notes.toPlainText().strip()}\n")
        txt_handle.write(f"# Initial length mm\t{self.spin_initial_length.value():.6f}\n")
        txt_handle.write(f"# Wire diameter mm\t{self.spin_diameter.value():.6f}\n")
        txt_handle.write(f"# Zero-load scale reading g\t{self._zero_load_scale_reference_g():.6f}\n")
        txt_handle.write(f"# Diagnostic software load offset g\t{self._load_offset_g:.6f}\n")
        txt_handle.write("# Mandatory length setup\tTrue\n")
        txt_handle.write(f"# Setup preload stress MPa\t{self.spin_setup_preload_stress_mpa.value():.6f}\n")
        txt_handle.write(f"# Recipe mode\t{self.combo_recipe_mode.currentText()}\n")
        txt_handle.flush()

        setup_txt_handle.write("\t".join(LONG_NAMES) + "\n")
        setup_txt_handle.write("\t".join(UNITS) + "\n")
        setup_txt_handle.write(f"# Created UTC\t{created_utc}\n")
        setup_txt_handle.write(f"# Sample\t{self.edit_sample_name.text().strip()}\n")
        setup_txt_handle.write(f"# Notes\t{self.edit_run_notes.toPlainText().strip()}\n")
        setup_txt_handle.write(f"# Initial length setting before setup mm\t{self.spin_initial_length.value():.6f}\n")
        setup_txt_handle.write(f"# Wire diameter mm\t{self.spin_diameter.value():.6f}\n")
        setup_txt_handle.write(f"# Zero-load scale reading g\t{self._zero_load_scale_reference_g():.6f}\n")
        setup_txt_handle.write(f"# Setup preload stress MPa\t{self.spin_setup_preload_stress_mpa.value():.6f}\n")
        setup_txt_handle.write(f"# Setup preload duration s\t{self.spin_setup_preload_duration_s.value():.6f}\n")
        setup_txt_handle.write(f"# Setup preload ramp rate MPa/s\t{self._setup_preload_ramp_rate_mpa_s():.6f}\n")
        setup_txt_handle.write(f"# Setup preload settle s\t{self.spin_setup_preload_stable_s.value():.6f}\n")
        setup_txt_handle.write(f"# Setup return duration s\t{self.spin_setup_return_duration_s.value():.6f}\n")
        setup_txt_handle.write(f"# Setup slack speed pct/s\t{self.spin_setup_slack_speed_strain_pct_s.value():.6f}\n")
        setup_txt_handle.write(f"# Setup slack step cap MPa\t{self.spin_setup_slack_step_cap_stress_mpa.value():.6f}\n")
        setup_txt_handle.write(f"# Setup stage max speed mm/s\t{self._setup_motion_speed_cap_mm_s():.6f}\n")
        setup_txt_handle.flush()

        writer = csv.DictWriter(
            csv_handle,
            fieldnames=MEASUREMENT_CSV_FIELDNAMES,
        )
        writer.writeheader()
        csv_handle.flush()
        setup_writer = csv.DictWriter(setup_csv_handle, fieldnames=MEASUREMENT_CSV_FIELDNAMES)
        setup_writer.writeheader()
        setup_csv_handle.flush()
        raw_scale_writer = csv.DictWriter(
            raw_scale_handle,
            fieldnames=[
                "elapsed_s",
                "timestamp_utc",
                "raw_load_g",
                "applied_load_g",
                "raw_text",
            ],
        )
        raw_scale_writer.writeheader()
        raw_scale_handle.flush()
        control_trace_writer = csv.DictWriter(control_trace_handle, fieldnames=CONTROL_TRACE_FIELDNAMES)
        control_trace_writer.writeheader()
        control_trace_handle.flush()
        ui_telemetry_writer = csv.DictWriter(ui_telemetry_handle, fieldnames=UI_TELEMETRY_FIELDNAMES)
        ui_telemetry_writer.writeheader()
        ui_telemetry_handle.flush()
        return (
            txt_handle,
            csv_handle,
            writer,
            raw_scale_handle,
            raw_scale_writer,
            control_trace_handle,
            control_trace_writer,
            ui_telemetry_handle,
            ui_telemetry_writer,
            setup_txt_handle,
            setup_csv_handle,
            setup_writer,
            txt_path,
            csv_path,
            json_path,
            raw_scale_path,
            control_trace_path,
            ui_telemetry_path,
            setup_txt_path,
            setup_csv_path,
        )

    def _control_interval_ms(self) -> int:
        if hasattr(self, "spin_control_interval"):
            return int(self.spin_control_interval.value())
        return DEFAULT_CONTROL_INTERVAL_MS

    def _log_interval_ms(self) -> int:
        if hasattr(self, "spin_log_interval"):
            return int(self.spin_log_interval.value())
        if hasattr(self, "spin_current_sweep_log_interval"):
            return int(self.spin_current_sweep_log_interval.value())
        return DEFAULT_LOG_INTERVAL_MS

    def _ui_refresh_interval_ms(self) -> int:
        if hasattr(self, "spin_ui_interval"):
            return int(self.spin_ui_interval.value())
        return DEFAULT_UI_REFRESH_INTERVAL_MS

    def _graph_refresh_interval_ms(self) -> int:
        if hasattr(self, "spin_graph_interval"):
            return int(self.spin_graph_interval.value())
        return DEFAULT_GRAPH_REFRESH_INTERVAL_MS

    def _tic_status_interval_ms(self) -> int:
        if hasattr(self, "spin_tic_status_interval"):
            return int(self.spin_tic_status_interval.value())
        return DEFAULT_TIC_STATUS_INTERVAL_MS

    def _tic_keepalive_interval_ms(self) -> int:
        if hasattr(self, "spin_tic_keepalive_interval"):
            return int(self.spin_tic_keepalive_interval.value())
        return TIC_KEEPALIVE_INTERVAL_MS

    def _supply_read_interval_ms(self) -> int:
        if hasattr(self, "spin_supply_read_interval"):
            return int(self.spin_supply_read_interval.value())
        return DEFAULT_SUPPLY_READ_INTERVAL_MS

    def _current_sweep_log_interval_ms(self) -> int:
        return self._log_interval_ms()

    def _apply_ui_refresh_interval(self) -> None:
        if hasattr(self, "_ui_refresh_timer"):
            self._ui_refresh_timer.setInterval(self._ui_refresh_interval_ms())

    def _dashboard_graph_refresh_due(self, *, now_s: float) -> bool:
        interval_s = max(0.0, self._graph_refresh_interval_ms() / 1000.0)
        return (
            self._last_dashboard_plot_refresh_s is None
            or now_s - self._last_dashboard_plot_refresh_s >= interval_s
        )

    def _apply_hardware_timer_intervals(self) -> None:
        if hasattr(self, "_status_timer"):
            self._status_timer.setInterval(self._tic_status_interval_ms())
        if hasattr(self, "_tic_keepalive_timer"):
            self._tic_keepalive_timer.setInterval(self._tic_keepalive_interval_ms())

    def _session_raw_scale_rate_hz(self) -> float | None:
        started_s = self._session_raw_scale_start_wall_s or self._session_start_wall_s
        if started_s <= 0.0:
            return None
        elapsed_s = max(0.0, time.time() - started_s)
        if elapsed_s <= 0.0 or self._session_raw_scale_count <= 0:
            return None
        return self._session_raw_scale_count / elapsed_s

    def _source_control_metadata(self) -> dict[str, Any]:
        repo_root = Path(__file__).resolve().parents[2]

        def _git_text(*args: str) -> str | None:
            try:
                completed = subprocess.run(
                    ["git", "-C", str(repo_root), *args],
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                    check=False,
                    **_hidden_subprocess_kwargs(),
                )
            except Exception:
                return None
            if completed.returncode != 0:
                return None
            text = completed.stdout.strip()
            return text or None

        status = _git_text("status", "--short")
        return {
            "repo_root": str(repo_root),
            "branch": _git_text("branch", "--show-current"),
            "commit": _git_text("rev-parse", "HEAD"),
            "is_dirty": bool(status),
            "status_short": status or "",
            "remote_url": _git_text("config", "--get", "remote.origin.url"),
        }

    def _control_logic_fingerprint_payload(self) -> dict[str, Any]:
        return {
            "name": CONTROL_LOGIC_NAME,
            "version": CONTROL_LOGIC_VERSION,
            "profile": CONTROL_LOGIC_PROFILE,
            "features": list(CONTROL_LOGIC_FEATURES),
            "constants": {
                "servo_current_sweep_defaults_version": SERVO_CURRENT_SWEEP_DEFAULTS_VERSION,
                "setup_zero_fallback_min_points": SETUP_ZERO_FALLBACK_MIN_POINTS,
                "setup_zero_fallback_min_time_s": SETUP_ZERO_FALLBACK_MIN_TIME_S,
                "setup_zero_fallback_min_strain_pct": SETUP_ZERO_FALLBACK_MIN_STRAIN_PCT,
                "setup_zero_fallback_raw_span_g": SETUP_ZERO_FALLBACK_RAW_SPAN_G,
                "setup_zero_fallback_min_residual_g": SETUP_ZERO_FALLBACK_MIN_RESIDUAL_G,
                "setup_zero_fallback_max_residual_g": SETUP_ZERO_FALLBACK_MAX_RESIDUAL_G,
                "current_sweep_error_gain_per_s": SERVO_CURRENT_SWEEP_ERROR_GAIN_PER_S,
                "current_sweep_rate_gain": SERVO_CURRENT_SWEEP_RATE_GAIN,
                "current_sweep_dynamic_min_fraction": SERVO_CURRENT_SWEEP_DYNAMIC_MIN_FRACTION,
                "current_sweep_dynamic_max_fraction": SERVO_CURRENT_SWEEP_DYNAMIC_MAX_FRACTION,
                "current_sweep_dynamic_scale_mpa": SERVO_CURRENT_SWEEP_DYNAMIC_SCALE_MPA,
                "current_hold_adaptive_min_fraction": SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MIN_FRACTION,
                "current_hold_adaptive_max_fraction": SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MAX_FRACTION,
                "current_hold_adaptive_large_error_mpa": SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_LARGE_ERROR_MPA,
                "current_hold_adaptive_max_command_strain_pct": (
                    SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MAX_COMMAND_STRAIN_PCT
                ),
                "current_hold_adaptive_min_samples": SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_MIN_SAMPLES,
                "current_hold_correction_confirm_s": SERVO_CURRENT_SWEEP_HOLD_CORRECTION_CONFIRM_S,
                "current_hold_noise_cap_tolerance_factor": (
                    SERVO_CURRENT_SWEEP_HOLD_NOISE_CAP_TOLERANCE_FACTOR
                ),
                "current_hold_entry_tolerance_factor": (
                    SERVO_CURRENT_SWEEP_HOLD_ENTRY_TOLERANCE_FACTOR
                ),
                "current_hold_large_error_factor": SERVO_CURRENT_SWEEP_HOLD_LARGE_ERROR_FACTOR,
                "current_hold_noisy_large_error_factor": (
                    SERVO_CURRENT_SWEEP_HOLD_NOISY_LARGE_ERROR_FACTOR
                ),
                "current_hold_entry_confirm_s": SERVO_CURRENT_SWEEP_HOLD_ENTRY_CONFIRM_S,
                "current_hold_min_away_slope_mpa_s": (
                    SERVO_CURRENT_SWEEP_HOLD_MIN_AWAY_SLOPE_MPA_S
                ),
            },
            "settings": {
                "control_interval_ms": self._control_interval_ms(),
                "setup_preload_stress_mpa": float(self.spin_setup_preload_stress_mpa.value()),
                "setup_preload_duration_s": float(self.spin_setup_preload_duration_s.value()),
                "setup_return_duration_s": float(self.spin_setup_return_duration_s.value()),
                "setup_slack_speed_strain_pct_s": float(self.spin_setup_slack_speed_strain_pct_s.value()),
                "setup_slack_step_cap_stress_mpa": float(self.spin_setup_slack_step_cap_stress_mpa.value()),
                "setup_zero_tolerance_g": float(self._auto_requested_tolerance_for_basis(HSW_BASIS_LOAD_G)),
                "setup_preload_stable_s": float(self.spin_setup_preload_stable_s.value()),
                "setup_zero_stable_s": float(self.spin_setup_zero_stable_s.value()),
                "target_ramp_rate_value_s": float(self.spin_current_sweep_target_ramp_rate.value()),
                "target_ramp_stage_speed_mm_s": float(self.spin_current_sweep_target_speed_mm_s.value()),
                "correction_max_strain_pct": float(self.spin_current_sweep_max_correction_strain_pct.value()),
                "correction_max_strain_rate_pct_s": float(self.spin_current_sweep_correction_rate_pct_s.value()),
                "correction_max_stress_mpa": self._current_sweep_max_correction_stress_mpa(),
                "correction_hold_max_stress_mpa": self._current_sweep_hold_correction_stress_mpa(),
                "correction_mid_stress_mpa": self._current_sweep_mid_correction_stress_mpa(),
                "correction_near_stress_mpa": self._current_sweep_near_correction_stress_mpa(),
                "current_ramp_hold_on_error": self.check_current_sweep_hold_on_error.isChecked(),
                "current_ramp_hold_pause_factor": float(self.spin_current_sweep_hold_pause_factor.value()),
                "current_ramp_hold_resume_factor": float(self.spin_current_sweep_hold_resume_factor.value()),
                "current_ramp_hold_resume_stable_s": float(self.spin_current_sweep_hold_resume_stable_s.value()),
                "current_hold_filter_window_s": self._current_sweep_hold_filter_window_s(),
                "current_hold_noise_sigma": self._current_sweep_hold_noise_sigma(),
                "current_hold_min_pause_stress_mpa": self._current_sweep_hold_min_pause_stress_mpa(),
                "current_hold_min_resume_stress_mpa": self._current_sweep_hold_min_resume_stress_mpa(),
                "max_correction_travel_mm": float(self.spin_current_sweep_max_seek_mm.value()),
            },
        }

    def _control_logic_metadata(self) -> dict[str, Any]:
        payload = self._control_logic_fingerprint_payload()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fingerprint = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
        return {
            "name": CONTROL_LOGIC_NAME,
            "version": CONTROL_LOGIC_VERSION,
            "profile": CONTROL_LOGIC_PROFILE,
            "features": list(CONTROL_LOGIC_FEATURES),
            "fingerprint": fingerprint,
            "fingerprint_algorithm": "sha256-json-v1",
            "fingerprint_fields": [
                "control_logic_name",
                "control_logic_version",
                "control_logic_profile",
                "features",
                "control_constants",
                "setup_control_settings",
                "current_sweep_correction_settings",
                "current_hold_filter_window_s",
                "current_hold_noise_sigma",
                "current_hold_persistent_error_gate",
            ],
        }

    def _session_stop_label(self, reason: str | None) -> tuple[str, str]:
        labels = {
            "recipe_completed": ("normal", "Recipe completed normally"),
            "manual_recipe_stop": ("operator", "Manual recipe stop"),
            "manual_session_stop": ("operator", "Manual session stop"),
            "emergency_stop": ("operator", "Emergency stop"),
            "wire_break_or_contact_loss": ("fault", "Wire break or contact loss"),
            "mechanical_load_loss": ("fault", "Mechanical load loss or slack"),
            "automation_timeout": ("fault", "Bench automation timeout"),
            "recipe_control_stop": ("fault", "Recipe stopped by control/error condition"),
            "app_closed": ("operator", "Application closed while session was active"),
        }
        return labels.get(str(reason or ""), ("unknown", "Unknown stop reason"))

    def _session_stop_metadata(self) -> dict[str, Any]:
        category, label = self._session_stop_label(self._session_stop_reason)
        return {
            "reason": self._session_stop_reason,
            "category": category,
            "label": label,
            "detail": self._session_stop_detail,
            "recorded_utc": self._session_stop_recorded_utc,
        }

    def _mark_session_stop_reason(
        self,
        reason: str,
        *,
        detail: str | None = None,
        force: bool = False,
    ) -> None:
        if self._session_stop_reason is not None and not force:
            return
        self._session_stop_reason = str(reason)
        self._session_stop_detail = detail
        self._session_stop_recorded_utc = _utc_timestamp()

    def _session_metadata(self) -> dict[str, Any]:
        calibration_metadata = {
            "baseline_s": float(self.spin_calibration_baseline_s.value()),
            "start_load_g": float(self.spin_calibration_start_load_g.value()),
            "end_load_g": float(self.spin_calibration_end_load_g.value()),
            "load_step_g": float(self.spin_calibration_load_step_g.value()),
            "tolerance_g": self._auto_requested_tolerance_for_basis(HSW_BASIS_LOAD_G),
            "tolerance_mode": "automatic",
            "settle_s": float(self.spin_calibration_settle_s.value()),
            "preload_nudge_mm": float(self.spin_calibration_preload_nudge_mm.value()),
            "preload_speed_mm_s": float(self.spin_calibration_preload_speed_mm_s.value()),
            "move_step_mm": float(self.spin_calibration_move_step_mm.value()),
            "steps_per_direction": int(self.spin_calibration_steps_per_direction.value()),
            "move_speed_mm_s": float(self.spin_calibration_speed_mm_s.value()),
            "legacy_interval_ms": int(self.spin_calibration_interval.value()),
            "control_interval_ms": self._control_interval_ms(),
            "pre_measurement_setup_enabled": self._pre_measurement_setup_enabled(CALIBRATION),
            "report": self._calibration_report,
        }
        return {
            "created_utc": self._session_created_utc or _utc_timestamp(),
            "sample_name": self.edit_sample_name.text().strip(),
            "name_fields": {
                "composition": self.edit_name_composition.text().strip(),
                "microwire": self.edit_name_wire.text().strip(),
                "specimen": self.edit_name_specimen.text().strip(),
                "condition": self.edit_name_condition.text().strip(),
            },
            "notes": self.edit_run_notes.toPlainText().strip(),
            "initial_length_mm": float(self.spin_initial_length.value()),
            "wire_diameter_mm": float(self.spin_diameter.value()),
            "mandatory_length_setup": True,
            "steps_per_mm": float(self.spin_steps_per_mm.value()),
            "position_reference_mm": float(self._position_reference_mm),
            "preload_reference_armed": self._preload_reference_armed,
            "preload_trigger_elapsed_s": self._preload_trigger_elapsed_s,
            "soft_limits_enabled": self.check_soft_limits.isChecked(),
            "soft_limit_min_mm": float(self.spin_soft_min_mm.value()),
            "soft_limit_max_mm": float(self.spin_soft_max_mm.value()),
            "max_load_limit_enabled": self.check_max_load.isChecked(),
            "max_load_limit_g": self._effective_max_load_limit_g(),
            "custom_max_load_limit_g": float(self.spin_max_load_g.value()),
            "raw_scale_display_limit_g": self._raw_scale_display_limit_g(),
            "zero_load_scale_g": self._zero_load_scale_reference_g(),
            "configured_zero_load_scale_g": self._configured_zero_load_scale_reference_g(),
            "run_zero_load_scale_g": self._run_zero_load_scale_g,
            "diagnostic_load_offset_g": float(self._load_offset_g),
            "tension_decreases_scale_reading": self.check_tension_load_positive.isChecked(),
            "positive_motion_is_tension": self.check_positive_motion_is_tension.isChecked(),
            "backlash_mm": float(self.spin_backlash_mm.value()),
            "return_to_origin": self.check_return_to_origin.isChecked(),
            "constant_current_zero": {
                "active_position_mm": self._active_constant_current_zero_position_mm,
                "active_current_mA": self._active_constant_current_zero_current_mA,
                "positions_by_leg": dict(self._constant_current_step_base_position_by_note),
            },
            "scale": {
                "port": str(self.combo_scale_port.currentData() or ""),
                "baud": int(self.combo_scale_baud.currentText()),
                "poll_interval_ms": int(self.spin_scale_interval.value()),
                "request_command": self.edit_scale_request.text(),
                "line_ending": self.edit_scale_terminator.text(),
                "recent_sample_rate_hz": self._scale_signal_buffer.sample_rate_hz(now_s=time.time()),
            },
            "logging": {
                "output_folder": None if self._session_base_path is None else self._session_base_path.parent.name,
                "measurement_txt": None if self._session_base_path is None else self._session_base_path.name,
                "measurement_csv": None if self._session_csv_path is None else self._session_csv_path.name,
                "metadata_json": None if self._session_json_path is None else self._session_json_path.name,
                "log_interval_ms": self._log_interval_ms(),
                "raw_scale_sidecar": None
                if self._session_raw_scale_path is None
                else self._session_raw_scale_path.name,
                "control_trace_csv": None
                if self._session_control_trace_path is None
                else self._session_control_trace_path.name,
                "ui_telemetry_csv": None
                if self._session_ui_telemetry_path is None
                else self._session_ui_telemetry_path.name,
                "setup_txt": None if self._session_setup_txt_path is None else self._session_setup_txt_path.name,
                "setup_csv": None if self._session_setup_csv_path is None else self._session_setup_csv_path.name,
                "raw_scale_sample_count": int(self._session_raw_scale_count),
                "raw_scale_session_rate_hz": self._session_raw_scale_rate_hz(),
                "ui_telemetry_sample_count": int(self._session_ui_telemetry_count),
            },
            "control": {
                "control_interval_ms": self._control_interval_ms(),
                "live_label_interval_ms": self._ui_refresh_interval_ms(),
                "ui_refresh_interval_ms": self._ui_refresh_interval_ms(),
                "ui_heartbeat_interval_ms": DEFAULT_UI_HEARTBEAT_INTERVAL_MS,
                "graph_refresh_interval_ms": self._graph_refresh_interval_ms(),
                "tic_keepalive_interval_ms": self._tic_keepalive_interval_ms(),
                "tic_status_interval_ms": self._tic_status_interval_ms(),
                "supply_read_interval_ms": self._supply_read_interval_ms(),
            },
            "heating": {
                "port": str(self.combo_supply_port.currentData() or ""),
                "baud": int(self.combo_supply_baud.currentText()),
                "profile": str(self.combo_supply_profile.currentData() or "hmp4030"),
                "current_sweep_channel": self._current_sweep_supply_channel(),
                "voltage_limit_v": float(self.spin_supply_voltage_limit.value()),
                "mode": HEATING_MODE_OFF,
                "voltage_limit_behavior": "current_sweeps_unwind_to_start_current",
                "continuity_monitor_enabled": self._continuity_monitor_enabled(),
                "continuity_current_mA": self._continuity_current_mA(),
                "output_off_on_stop": True,
                "motor_supply_enabled": self.check_motor_supply_power.isChecked(),
                "motor_supply_channel": self._motor_supply_channel(),
                "motor_supply_voltage_v": float(self.spin_motor_supply_voltage.value()),
                "motor_supply_current_limit_a": float(self.spin_motor_supply_current_limit.value()),
            },
            "recipe_mode": str(self.combo_recipe_mode.currentData() or "ramp"),
            "recipe_summary": self._last_recipe_summary,
            "recipe_estimated_points": int(self._recipe_estimated_points),
            "stop": self._session_stop_metadata(),
            "source_control": self._source_control_metadata(),
            "control_logic": self._control_logic_metadata(),
            "hsw_distribution": {
                "basis": self._distribution_basis(),
                "start": float(self.spin_distribution_start.value()),
                "end": float(self.spin_distribution_end.value()),
                "step": float(self.spin_distribution_step.value()),
                "tolerance": self._auto_requested_tolerance_for_basis(self._distribution_basis()),
                "tolerance_mode": "automatic",
                "seek_nudge_mm": float(self.spin_distribution_nudge_mm.value()),
                "settle_s": float(self.spin_distribution_settle_s.value()),
                "points_per_plateau": int(self.spin_distribution_points.value()),
                "legacy_interval_ms": int(self.spin_distribution_interval.value()),
                "return_sweep": self.check_distribution_return_sweep.isChecked(),
            },
            "calibration": calibration_metadata,
            "copper_calibration": dict(calibration_metadata, legacy_name="copper_calibration"),
            "controlled_current_sweep": {
                "mode": str(self.combo_recipe_mode.currentData() or ""),
                "basis": self._current_sweep_basis(),
                "pre_measurement_setup_enabled": self._pre_measurement_setup_enabled(),
                "setup_preload_stress_mpa": float(self.spin_setup_preload_stress_mpa.value()),
                "setup_preload_duration_s": float(self.spin_setup_preload_duration_s.value()),
                "setup_preload_ramp_rate_mpa_s": self._setup_preload_ramp_rate_mpa_s(),
                "setup_return_duration_s": float(self.spin_setup_return_duration_s.value()),
                "setup_slack_speed_strain_pct_s": float(self.spin_setup_slack_speed_strain_pct_s.value()),
                "setup_slack_step_cap_stress_mpa": float(self.spin_setup_slack_step_cap_stress_mpa.value()),
                "setup_stage_max_speed_mm_s": self._setup_motion_speed_cap_mm_s(),
                "setup_preload_tolerance_mpa": self._auto_requested_tolerance_for_basis(HSW_BASIS_STRESS_MPA),
                "setup_preload_tolerance_mode": "automatic",
                "setup_zero_tolerance_g": float(self._auto_requested_tolerance_for_basis(HSW_BASIS_LOAD_G)),
                "setup_zero_tolerance_mode": "automatic",
                "setup_preload_stable_s": float(self.spin_setup_preload_stable_s.value()),
                "setup_zero_stable_s": float(self.spin_setup_zero_stable_s.value()),
                "setup_starting_length_mm": self._setup_starting_length_mm,
                "setup_measured_length_mm": self._setup_measured_length_mm,
                "setup_preload_position_mm": self._setup_preload_position_mm,
                "setup_length_reference_position_mm": self._setup_preload_position_mm,
                "setup_preload_ramp_skipped": self._setup_preload_ramp_skipped,
                "target_start": float(self.spin_current_sweep_target_start.value()),
                "target_end": float(self.spin_current_sweep_target_end.value()),
                "target_step": float(self.spin_current_sweep_target_step.value()),
                "target_ramp_rate_value_s": float(self.spin_current_sweep_target_ramp_rate.value()),
                "target_ramp_stage_speed_mm_s": float(self.spin_current_sweep_target_speed_mm_s.value()),
                "correction_max_strain_pct": float(self.spin_current_sweep_max_correction_strain_pct.value()),
                "correction_max_strain_rate_pct_s": float(self.spin_current_sweep_correction_rate_pct_s.value()),
                "correction_max_stress_mpa": self._current_sweep_max_correction_stress_mpa(),
                "correction_hold_max_stress_mpa": self._current_sweep_hold_correction_stress_mpa(),
                "correction_mid_stress_mpa": self._current_sweep_mid_correction_stress_mpa(),
                "correction_near_stress_mpa": self._current_sweep_near_correction_stress_mpa(),
                "return_target": bool(self.check_current_sweep_return_target.isChecked()),
                "current_start_mA": float(self.spin_current_sweep_start_mA.value()),
                "current_end_mA": float(self.spin_current_sweep_end_mA.value()),
                "current_ramp_rate_mA_s": float(self.spin_current_sweep_step_mA.value()),
                "current_ramp_hold_on_error": self.check_current_sweep_hold_on_error.isChecked(),
                "current_ramp_hold_pause_factor": float(self.spin_current_sweep_hold_pause_factor.value()),
                "current_ramp_hold_resume_factor": float(self.spin_current_sweep_hold_resume_factor.value()),
                "current_ramp_hold_resume_stable_s": float(self.spin_current_sweep_hold_resume_stable_s.value()),
                "current_ramp_hold_filter_window_s": self._current_sweep_hold_filter_window_s(),
                "current_ramp_hold_noise_sigma": self._current_sweep_hold_noise_sigma(),
                "current_ramp_hold_min_pause_stress_mpa": self._current_sweep_hold_min_pause_stress_mpa(),
                "current_ramp_hold_min_resume_stress_mpa": self._current_sweep_hold_min_resume_stress_mpa(),
                "first_overheating": self.check_current_sweep_first_overheating.isChecked(),
                "first_overheating_target_mpa": float(
                    self.spin_current_sweep_first_overheating_target_mpa.value()
                ),
                "reverse_current": True,
                "tolerance": self._auto_requested_tolerance_for_basis(self._current_sweep_basis()),
                "tolerance_mode": "automatic",
                "dynamic_balance_max_speed_mm_s": float(self.spin_current_sweep_target_speed_mm_s.value()),
                "dynamic_balance_effective_speed_cap_mm_s": self._current_sweep_dynamic_speed_cap_mm_s(),
                "dynamic_balance_max_correction_mm": self._current_sweep_max_correction_mm(),
                "dynamic_balance_error_gain_per_s": SERVO_CURRENT_SWEEP_ERROR_GAIN_PER_S,
                "dynamic_balance_rate_gain": SERVO_CURRENT_SWEEP_RATE_GAIN,
                "legacy_balancing_nudge_mm": float(self.spin_current_sweep_nudge_mm.value()),
                "legacy_balancing_speed_mm_s": float(self.spin_current_sweep_balance_speed_mm_s.value()),
                "max_correction_travel_mm": float(self.spin_current_sweep_max_seek_mm.value()),
                "legacy_interval_ms": int(self.spin_current_sweep_interval.value()),
                "control_interval_ms": self._control_interval_ms(),
                "log_interval_ms": self._log_interval_ms(),
            },
            "builder_project": None if self._builder_project_path is None else str(self._builder_project_path),
        }

    def _write_session_metadata(self, *, finished_utc: str | None = None) -> None:
        if self._session_json_path is None:
            return
        payload = self._session_metadata()
        payload["point_count"] = len(self._session_points)
        if self._session_active:
            payload["session_state"] = "running"
            payload["elapsed_s"] = time.monotonic() - self._session_start_monotonic
        else:
            payload["session_state"] = "finished"
        if finished_utc:
            payload["finished_utc"] = finished_utc
        try:
            self._session_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            self._write_emergency_session_snapshot(
                payload,
                reason="metadata_write_failed",
                error=exc,
            )

    def _session_recovery_root(self) -> Path:
        return Path(_default_download_dir()) / "MiniDMA_recovered_sessions"

    def _write_emergency_session_snapshot(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
        error: BaseException,
    ) -> None:
        try:
            if self._session_recovery_path is None:
                base_name = "session"
                if self._session_base_path is not None:
                    base_name = self._session_base_path.parent.name or self._session_base_path.stem
                safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", base_name).strip("._") or "session"
                self._session_recovery_path = (
                    self._session_recovery_root()
                    / f"MiniDMA_recovered_{safe_name}_{_utc_filename_timestamp()}"
                )
            self._session_recovery_path.mkdir(parents=True, exist_ok=True)
            recovery_payload = dict(payload)
            recovery_payload["recovery"] = {
                "reason": reason,
                "error": str(error),
                "original_metadata_path": None
                if self._session_json_path is None
                else str(self._session_json_path),
                "saved_utc": _utc_timestamp(),
            }
            metadata_path = self._session_recovery_path / SESSION_METADATA_JSON
            metadata_path.write_text(json.dumps(recovery_payload, indent=2), encoding="utf-8")

            txt_path = self._session_recovery_path / SESSION_MEASUREMENT_TX
            csv_path = self._session_recovery_path / SESSION_MEASUREMENT_CSV
            with (
                txt_path.open("w", encoding="utf-8", newline="") as txt_handle,
                csv_path.open("w", encoding="utf-8", newline="") as csv_handle,
            ):
                txt_handle.write("\t".join(LONG_NAMES) + "\n")
                txt_handle.write("\t".join(UNITS) + "\n")
                txt_handle.write("# Emergency recovery copy\n")
                writer = csv.DictWriter(csv_handle, fieldnames=MEASUREMENT_CSV_FIELDNAMES)
                writer.writeheader()
                for point in self._session_points:
                    self._write_point_to_handles(
                        point,
                        txt_handle=txt_handle,
                        csv_writer=writer,
                        csv_handle=None,
                    )
            self._log(f"Emergency session recovery saved to {self._session_recovery_path}.")
        except Exception as recovery_exc:
            self._log(
                "Emergency session recovery failed after metadata write error: "
                f"{recovery_exc}; original error: {error}"
            )

    def _set_automation_context(
        self,
        *,
        phase: str,
        basis: str | None = None,
        target_value: float | None = None,
        plateau_index: int | None = None,
        note: str | None = None,
    ) -> None:
        self._automation_phase = phase
        self._automation_step_note = note
        self._automation_basis = basis
        self._automation_target_value = target_value
        self._automation_plateau_index = plateau_index
        if basis and target_value is not None:
            label = HSW_BASIS_LABELS.get(basis, basis)
            suffix, _ = self._distribution_units(basis)
            self._automation_plateau_label = f"{label} {target_value:.4f}{suffix}"
        else:
            self._automation_plateau_label = None

    def _start_session(self, *, enable_logging: bool = True, record_initial_point: bool = True) -> None:
        if self._session_active:
            return
        self._persist_settings_if_enabled()
        self._clear_run_zero_load_scale_reference()
        created_utc = _utc_timestamp()
        try:
            (
                txt_handle,
                csv_handle,
                csv_writer,
                raw_scale_handle,
                raw_scale_writer,
                control_trace_handle,
                control_trace_writer,
                ui_telemetry_handle,
                ui_telemetry_writer,
                setup_txt_handle,
                setup_csv_handle,
                setup_csv_writer,
                txt_path,
                csv_path,
                json_path,
                raw_scale_path,
                control_trace_path,
                ui_telemetry_path,
                setup_txt_path,
                setup_csv_path,
            ) = self._prepare_session_files(created_utc=created_utc)
        except Exception as exc:
            if str(exc):
                self._log(str(exc))
            return

        self._session_created_utc = created_utc
        if self.check_zero_position_on_start.isChecked():
            self._zero_tic_position()
        try:
            if self.check_tare_on_start.isChecked():
                signed_load = self._load_sign() * (
                    self._latest_scale_value_g - self._zero_load_scale_reference_g()
                )
                self._load_offset_g = -signed_load
        except Exception as exc:
            for handle in (
                txt_handle,
                csv_handle,
                raw_scale_handle,
                control_trace_handle,
                ui_telemetry_handle,
                setup_txt_handle,
                setup_csv_handle,
            ):
                try:
                    handle.close()
                except Exception:
                    pass
            for path in (
                txt_path,
                csv_path,
                json_path,
                raw_scale_path,
                control_trace_path,
                ui_telemetry_path,
                setup_txt_path,
                setup_csv_path,
            ):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
            try:
                txt_path.parent.rmdir()
            except Exception:
                pass
            self._session_created_utc = None
            self._log(str(exc))
            self._refresh_live_labels()
            return
        self._effective_position_mm = self._current_position_mm
        self._last_effective_move_target_mm = self._effective_position_mm
        self._position_reference_mm = self._effective_position_mm
        self._preload_reference_armed = (
            self.check_zero_on_preload.isChecked() and self.spin_preload_threshold_g.value() > 0
        )
        self._preload_trigger_elapsed_s = None
        self._session_points = []
        self._live_plot_points = []
        self._last_live_plot_scale_timestamp = None
        self._last_dashboard_plot_refresh_s = None
        self._session_active = True
        self._session_logging_enabled = bool(enable_logging)
        self._session_start_monotonic = time.monotonic()
        self._session_start_wall_s = time.time()
        self._session_raw_scale_start_wall_s = self._session_start_wall_s
        self._last_session_log_timestamp_s = self._session_start_wall_s
        self._session_stop_reason = None
        self._session_stop_detail = None
        self._session_stop_recorded_utc = None
        self._session_recovery_path = None
        self._session_raw_scale_count = 0
        self._session_ui_telemetry_count = 0
        self._ui_refresh_last_monotonic_s = None
        self._acquire_experiment_sleep_guard()
        self._session_txt_handle = txt_handle
        self._session_csv_handle = csv_handle
        self._session_csv_writer = csv_writer
        self._session_raw_scale_handle = raw_scale_handle
        self._session_raw_scale_writer = raw_scale_writer
        self._session_control_trace_handle = control_trace_handle
        self._session_control_trace_writer = control_trace_writer
        self._session_ui_telemetry_handle = ui_telemetry_handle
        self._session_ui_telemetry_writer = ui_telemetry_writer
        self._session_setup_txt_handle = setup_txt_handle
        self._session_setup_csv_handle = setup_csv_handle
        self._session_setup_csv_writer = setup_csv_writer
        self._session_base_path = txt_path
        self._session_csv_path = csv_path
        self._session_json_path = json_path
        self._session_raw_scale_path = raw_scale_path
        self._session_control_trace_path = control_trace_path
        self._session_ui_telemetry_path = ui_telemetry_path
        self._session_setup_txt_path = setup_txt_path
        self._session_setup_csv_path = setup_csv_path
        self.button_start_session.setEnabled(False)
        self.button_stop_session.setEnabled(True)
        self.label_session_status.setText(f"Session running -> {txt_path.parent.name}")
        self._log(f"Session started: {txt_path.parent}")
        self._prepare_heating_for_session()
        self._apply_ui_refresh_interval()
        self._ui_refresh_timer.start()
        self._write_session_metadata()
        self._refresh_live_labels()
        if self._session_logging_enabled and record_initial_point:
            self._record_current_point()

    def _begin_recipe_logging(self) -> None:
        if not self._session_active:
            self._start_session(enable_logging=True, record_initial_point=False)
            if not self._session_active:
                return
        self._recipe_origin_mm = self._current_position_mm
        self._effective_position_mm = self._current_position_mm
        self._last_effective_move_target_mm = self._effective_position_mm
        self._position_reference_mm = self._effective_position_mm
        self._preload_reference_armed = False
        self._preload_trigger_elapsed_s = None
        self._session_points = []
        self._live_plot_points = []
        self._last_live_plot_scale_timestamp = None
        self._last_dashboard_plot_refresh_s = None
        self._session_logging_enabled = True
        self._session_start_monotonic = time.monotonic()
        self._session_start_wall_s = time.time()
        self._last_session_log_timestamp_s = self._session_start_wall_s
        self._ui_refresh_last_monotonic_s = None
        self._set_automation_context(phase="start")
        self._write_session_metadata()
        self._refresh_plots()
        self._refresh_live_labels()
        self._record_current_point()

    def _stop_session(
        self,
        *_args: object,
        reason: str | None = None,
        detail: str | None = None,
    ) -> None:
        if not self._is_ui_thread():
            self._call_on_ui_thread_sync(lambda: self._stop_session(reason=reason, detail=detail))
            return
        if not self._session_active:
            return
        self._finalize_calibration_report_if_needed()
        self._stop_auto_ramp(
            log_completion=False,
            stop_reason=reason,
            stop_detail=detail,
        )
        if reason is not None:
            self._mark_session_stop_reason(reason, detail=detail, force=reason != "app_closed")
        elif self._session_stop_reason is None:
            self._mark_session_stop_reason(
                "manual_session_stop",
                detail="Session stopped without completing an active recipe.",
            )
        self._session_active = False
        self._session_logging_enabled = False
        if self._session_txt_handle is not None:
            self._session_txt_handle.close()
            self._session_txt_handle = None
        if self._session_csv_handle is not None:
            self._session_csv_handle.close()
            self._session_csv_handle = None
        self._session_csv_writer = None
        if self._session_raw_scale_handle is not None:
            self._session_raw_scale_handle.close()
            self._session_raw_scale_handle = None
        self._session_raw_scale_writer = None
        if self._session_control_trace_handle is not None:
            self._session_control_trace_handle.close()
            self._session_control_trace_handle = None
        self._session_control_trace_writer = None
        if self._session_ui_telemetry_handle is not None:
            self._session_ui_telemetry_handle.close()
            self._session_ui_telemetry_handle = None
        self._session_ui_telemetry_writer = None
        if self._session_setup_txt_handle is not None:
            self._session_setup_txt_handle.close()
            self._session_setup_txt_handle = None
        if self._session_setup_csv_handle is not None:
            self._session_setup_csv_handle.close()
            self._session_setup_csv_handle = None
        self._session_setup_csv_writer = None
        self.button_start_session.setEnabled(True)
        self.button_stop_session.setEnabled(False)
        point_count = len(self._session_points)
        _stop_category, stop_label = self._session_stop_label(self._session_stop_reason)
        self.label_session_status.setText(f"Session saved ({point_count} point(s)); {stop_label}")
        if self._session_base_path is not None:
            self._log(
                f"Session stopped ({stop_label}). "
                f"Saved {point_count} point(s) to {self._session_base_path}."
            )
        if self._supply_output_enabled:
            self._disable_supply_output()
        self._ui_refresh_timer.stop()
        if self._session_json_path is not None:
            self._write_session_metadata(finished_utc=_utc_timestamp())
        self._clear_run_zero_load_scale_reference()
        self._release_experiment_sleep_guard()
        self._live_plot_points = []
        self._last_live_plot_scale_timestamp = None
        self._refresh_live_labels()

    def _acquire_experiment_sleep_guard(self) -> None:
        try:
            if self._sleep_guard is not None:
                return
            self._sleep_guard = create_experiment_sleep_guard("Mini DMA experiment")
            self._sleep_guard.acquire()
            self._log("Sleep prevention active while the Mini DMA session is running.")
        except Exception as exc:
            self._sleep_guard = None
            self._log(f"Could not enable sleep prevention: {exc}")

    def _release_experiment_sleep_guard(self) -> None:
        guard = self._sleep_guard
        self._sleep_guard = None
        if guard is None:
            return
        try:
            guard.release()
            self._log("Sleep prevention released.")
        except Exception as exc:
            self._log(f"Could not release sleep prevention: {exc}")

    def _write_control_trace(
        self,
        *,
        decision: str,
        basis: str | None = None,
        target_value: float | None = None,
        current_value: float | None = None,
        error_value: float | None = None,
        tolerance: float | None = None,
        sensitivity_per_mm: float | None = None,
        correction_mm: float | None = None,
        backlash_mm: float | None = None,
        command_speed_mm_s: float | None = None,
        required_fresh_samples: int | None = None,
        post_move_sample_count: int | None = None,
        target_mm: float | None = None,
        effective_target_mm: float | None = None,
        result: str = "",
        reason: str = "",
        task_text: str | None = None,
    ) -> None:
        if (
            not self._session_active
            or self._session_control_trace_writer is None
            or self._session_control_trace_handle is None
        ):
            return

        def _number(value: float | None) -> str:
            if value is None:
                return ""
            try:
                value = float(value)
            except (TypeError, ValueError):
                return ""
            if not math.isfinite(value):
                return ""
            return f"{value:.9g}"

        elapsed_s = max(0.0, time.monotonic() - self._session_start_monotonic)
        self._session_control_trace_writer.writerow(
            {
                "elapsed_s": f"{elapsed_s:.6f}",
                "timestamp_utc": _utc_timestamp(),
                "recipe_mode": str(self.combo_recipe_mode.currentData() or "ramp"),
                "task_text": self._current_task_summary() if task_text is None else task_text,
                "automation_phase": self._automation_phase,
                "automation_basis": "" if basis is None else basis,
                "automation_target_value": _number(target_value),
                "plateau_index": "" if self._automation_plateau_index is None else self._automation_plateau_index,
                "decision": decision,
                "current_value": _number(current_value),
                "error_value": _number(error_value),
                "tolerance": _number(tolerance),
                "sensitivity_per_mm": _number(sensitivity_per_mm),
                "motor_step_mm": _number(self._motor_step_mm()),
                "correction_mm": _number(correction_mm),
                "backlash_mm": _number(backlash_mm),
                "command_speed_mm_s": _number(command_speed_mm_s),
                "required_fresh_samples": "" if required_fresh_samples is None else int(required_fresh_samples),
                "post_move_sample_count": "" if post_move_sample_count is None else int(post_move_sample_count),
                "target_mm": _number(target_mm),
                "effective_target_mm": _number(effective_target_mm),
                "result": result,
                "reason": reason,
            }
        )
        self._session_control_trace_handle.flush()

    def _write_raw_scale_sample(self, sample: ScaleSample) -> None:
        if (
            not self._session_active
            or self._session_raw_scale_writer is None
            or self._session_raw_scale_handle is None
            or (self._session_raw_scale_start_wall_s or self._session_start_wall_s) <= 0.0
        ):
            return
        started_s = self._session_raw_scale_start_wall_s or self._session_start_wall_s
        elapsed_s = max(0.0, sample.timestamp_s - started_s)
        self._session_raw_scale_writer.writerow(
            {
                "elapsed_s": f"{elapsed_s:.6f}",
                "timestamp_utc": _utc_timestamp_from_epoch(sample.timestamp_s),
                "raw_load_g": f"{sample.raw_g:.6f}",
                "applied_load_g": f"{sample.applied_load_g:.6f}",
                "raw_text": sample.raw_text,
            }
        )
        self._session_raw_scale_count += 1
        self._session_raw_scale_handle.flush()

    def _scale_reading_age_s(self) -> float | None:
        with self._scale_state_lock:
            timestamp_s = self._latest_scale_timestamp
        if timestamp_s is None:
            return None
        return max(0.0, time.time() - timestamp_s)

    def _has_fresh_scale_reading(self, *, after_s: float | None = None) -> bool:
        age_s = self._scale_reading_age_s()
        if age_s is None or age_s > STALE_SCALE_AFTER_S:
            return False
        if after_s is not None:
            with self._scale_state_lock:
                timestamp_s = self._latest_scale_timestamp
            if timestamp_s is None or timestamp_s < after_s:
                return False
        return True

    def _load_sign(self) -> float:
        config = self._control_config()
        tension_decreases_scale_reading = (
            config.tension_decreases_scale_reading
            if config is not None
            else (
                self._cached_tension_decreases_scale_reading
                if not self._is_ui_thread()
                else self.check_tension_load_positive.isChecked()
            )
        )
        return -1.0 if tension_decreases_scale_reading else 1.0

    def _effective_load_from_raw_g(self, raw_g: float) -> float:
        signed_load_g = self._load_sign() * (float(raw_g) - self._zero_load_scale_reference_g())
        return max(0.0, signed_load_g + self._load_offset_g)

    def _current_effective_load_g(self) -> float:
        if self._latest_scale_timestamp is None:
            return 0.0
        return self._effective_load_from_raw_g(self._latest_scale_value_g)

    def _current_preload_state(self, load_g: float) -> str:
        config = self._control_config()
        zero_on_preload = config.zero_on_preload if config is not None else self.check_zero_on_preload.isChecked()
        threshold_g = config.preload_threshold_g if config is not None else float(self.spin_preload_threshold_g.value())
        if not zero_on_preload or threshold_g <= 0:
            return PRELOAD_DISABLED
        if self._preload_reference_armed:
            return PRELOAD_PENDING
        if self._preload_trigger_elapsed_s is not None:
            return PRELOAD_ACTIVE
        if abs(load_g) >= float(threshold_g):
            return PRELOAD_ACTIVE
        return PRELOAD_PENDING

    def _capture_measurement_point(
        self,
        *,
        elapsed_s: float,
        position_mm: float,
        effective_position_mm: float | None = None,
        raw_load_g: float,
        load_g: float,
        load_summary: ScaleIntervalSummary | None = None,
    ) -> MeasurementPoint:
        specimen_position_mm = float(position_mm) if effective_position_mm is None else float(effective_position_mm)
        preload_state = self._current_preload_state(load_g)
        config = self._control_config()
        preload_threshold_g = (
            config.preload_threshold_g
            if config is not None
            else float(self.spin_preload_threshold_g.value())
        )
        if preload_state == PRELOAD_PENDING and abs(load_g) >= float(preload_threshold_g):
            self._position_reference_mm = specimen_position_mm
            self._preload_reference_armed = False
            self._preload_trigger_elapsed_s = elapsed_s
            preload_state = PRELOAD_ACTIVE
            self._log(
                f"Preload reached at {load_g:.5f} g. Gauge zero moved to {specimen_position_mm:.4f} mm."
            )
        strain = None
        stress = None
        if preload_state != PRELOAD_PENDING:
            strain = self._strain_percent_for_position(specimen_position_mm)
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            stress = stress_mpa_from_load_g(load_g, diameter_mm)
        (
            current_zero_position_mm,
            current_l0_mm,
            current_relative_position_mm,
            current_relative_strain_pct,
        ) = (
            self._current_relative_position_and_strain(specimen_position_mm)
        )
        tensile_displacement_mm = self._tensile_displacement_mm(specimen_position_mm)
        snapshot = self._refresh_supply_snapshot()
        current_set_mA = self._supply_last_setpoint_mA
        current_measured_mA = snapshot.get("current_mA")
        resistance_ohm = snapshot.get("resistance_ohm")
        if (
            current_set_mA is None
            or abs(current_set_mA) < MIN_RESISTANCE_CURRENT_MA
            or current_measured_mA is None
            or abs(current_measured_mA) < MIN_RESISTANCE_CURRENT_MA
        ):
            resistance_ohm = None
        return MeasurementPoint(
            elapsed_s=elapsed_s,
            timestamp_utc=_utc_timestamp(),
            raw_position_mm=position_mm,
            position_mm=tensile_displacement_mm,
            raw_load_g=raw_load_g,
            load_g=load_g,
            preload_state=preload_state,
            strain_pct=strain,
            stress_mpa=stress,
            current_zero_position_mm=current_zero_position_mm,
            current_l0_mm=current_l0_mm,
            current_relative_position_mm=current_relative_position_mm,
            current_relative_strain_pct=current_relative_strain_pct,
            current_set_mA=current_set_mA,
            current_measured_mA=current_measured_mA,
            voltage_V=snapshot.get("voltage_V"),
            resistance_ohm=resistance_ohm,
            power_W=snapshot.get("power_W"),
            automation_phase=self._automation_phase,
            automation_basis=self._automation_basis,
            automation_target_value=self._automation_target_value,
            plateau_index=self._automation_plateau_index,
            plateau_label=self._automation_plateau_label,
            load_raw_last_g=None if load_summary is None else load_summary.raw_last_g,
            load_mean_g=None if load_summary is None else load_summary.load_mean_g,
            load_std_g=None if load_summary is None else load_summary.load_std_g,
            load_min_g=None if load_summary is None else load_summary.load_min_g,
            load_max_g=None if load_summary is None else load_summary.load_max_g,
            load_sample_count=0 if load_summary is None else load_summary.sample_count,
            scale_sample_rate_hz=None if load_summary is None else load_summary.sample_rate_hz,
        )

    def _capture_live_plot_point(self) -> MeasurementPoint | None:
        if self._latest_scale_timestamp is None:
            return None
        elapsed_s = max(0.0, time.monotonic() - self._session_start_monotonic)
        raw_load_g = float(self._latest_scale_value_g)
        load_g = self._current_effective_load_g()
        position_mm = self._measurement_position_mm()
        effective_position_mm = self._measurement_effective_position_mm()
        specimen_position_mm = effective_position_mm
        preload_state = self._current_preload_state(load_g)
        strain = None
        stress = None
        if preload_state != PRELOAD_PENDING:
            strain = self._strain_percent_for_position(specimen_position_mm)
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            stress = stress_mpa_from_load_g(load_g, diameter_mm)
        (
            current_zero_position_mm,
            current_l0_mm,
            current_relative_position_mm,
            current_relative_strain_pct,
        ) = (
            self._current_relative_position_and_strain(specimen_position_mm)
        )
        tensile_displacement_mm = self._tensile_displacement_mm(specimen_position_mm)
        current_set_mA = self._supply_last_setpoint_mA
        current_measured_mA = self._supply_snapshot.get("current_mA")
        resistance_ohm = self._supply_snapshot.get("resistance_ohm")
        if (
            current_set_mA is None
            or abs(current_set_mA) < MIN_RESISTANCE_CURRENT_MA
            or current_measured_mA is None
            or abs(current_measured_mA) < MIN_RESISTANCE_CURRENT_MA
        ):
            resistance_ohm = None
        return MeasurementPoint(
            elapsed_s=elapsed_s,
            timestamp_utc=_utc_timestamp(),
            raw_position_mm=position_mm,
            position_mm=tensile_displacement_mm,
            raw_load_g=raw_load_g,
            load_g=load_g,
            preload_state=preload_state,
            strain_pct=strain,
            stress_mpa=stress,
            current_zero_position_mm=current_zero_position_mm,
            current_l0_mm=current_l0_mm,
            current_relative_position_mm=current_relative_position_mm,
            current_relative_strain_pct=current_relative_strain_pct,
            current_set_mA=current_set_mA,
            current_measured_mA=current_measured_mA,
            voltage_V=self._supply_snapshot.get("voltage_V"),
            resistance_ohm=resistance_ohm,
            power_W=self._supply_snapshot.get("power_W"),
            automation_phase=self._automation_phase,
            automation_basis=self._automation_basis,
            automation_target_value=self._automation_target_value,
            plateau_index=self._automation_plateau_index,
            plateau_label=self._automation_plateau_label,
            load_raw_last_g=raw_load_g,
            load_mean_g=load_g,
            load_std_g=None,
            load_min_g=load_g,
            load_max_g=load_g,
            load_sample_count=1,
            scale_sample_rate_hz=self._scale_signal_buffer.sample_rate_hz(now_s=time.time()),
        )

    def _scale_summary_for_record(self, *, now_s: float) -> ScaleIntervalSummary:
        since_s = self._last_session_log_timestamp_s
        if since_s is None and self._session_start_wall_s > 0.0:
            since_s = self._session_start_wall_s
        summary = self._scale_signal_buffer.interval_summary(since_s=since_s, until_s=now_s)
        if summary.sample_count > 0:
            return summary
        current_load = self._current_effective_load_g()
        return ScaleIntervalSummary(
            raw_last_g=self._latest_scale_value_g if self._latest_scale_timestamp is not None else None,
            applied_last_g=current_load if self._latest_scale_timestamp is not None else None,
            load_mean_g=current_load,
            load_std_g=0.0,
            load_min_g=current_load,
            load_max_g=current_load,
            sample_count=0,
            sample_rate_hz=self._scale_signal_buffer.sample_rate_hz(now_s=now_s),
        )

    def _record_current_point(
        self,
        *,
        quiet: bool = False,
        advance_heating: bool = True,
        require_fresh_after_move: bool | None = None,
    ) -> bool:
        if not self._session_active:
            if not quiet:
                if self._is_ui_thread():
                    QtWidgets.QMessageBox.information(self, APP_NAME, "Start a session before recording points.")
                else:
                    self._log("Point not recorded because no session is active.")
            return False
        if not self._session_logging_enabled:
            if not quiet and not self._automation_active:
                if self._is_ui_thread():
                    QtWidgets.QMessageBox.information(self, APP_NAME, "Recipe setup is running; normal logging has not started.")
                else:
                    self._log("Point not recorded because recipe setup is running; normal logging has not started.")
            return True
        if self._handle_raw_scale_display_limit_status():
            return False
        if require_fresh_after_move is None:
            require_fresh_after_move = self._automation_basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
        after_s = self._motion_feedback_ready_after_s() if require_fresh_after_move else None
        if self._automation_basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA} and not self._has_fresh_scale_reading(after_s=after_s):
            self._log("Point not recorded because load/stress feedback is stale after the last move.")
            if self._automation_active:
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return False
        elapsed_s = time.monotonic() - self._session_start_monotonic
        record_wall_s = time.time()
        load_summary = self._scale_summary_for_record(now_s=record_wall_s)
        position_mm = self._measurement_position_mm()
        raw_load_g = (
            self._latest_scale_value_g
            if load_summary.raw_last_g is None
            else load_summary.raw_last_g
        )
        load_g = (
            self._current_effective_load_g()
            if load_summary.load_mean_g is None
            else load_summary.load_mean_g
        )
        effective_position_mm = self._measurement_effective_position_mm()
        point = self._capture_measurement_point(
            elapsed_s=elapsed_s,
            position_mm=position_mm,
            effective_position_mm=effective_position_mm,
            raw_load_g=raw_load_g,
            load_g=load_g,
            load_summary=load_summary,
        )
        if not self._session_active:
            return False
        self._session_points.append(point)
        self._live_plot_points = [
            live_point
            for live_point in self._live_plot_points
            if live_point.elapsed_s < point.elapsed_s
            and not math.isclose(live_point.elapsed_s, point.elapsed_s, rel_tol=0.0, abs_tol=1e-6)
        ]
        self._write_point(point)
        self._last_session_log_timestamp_s = record_wall_s
        self._write_session_metadata()
        self._refresh_plots()
        self._refresh_live_labels()
        if not quiet:
            self._log(
                f"Recorded point #{len(self._session_points)} at "
                f"{point.position_mm:.4f} mm tensile displacement, "
                f"{load_g:.5f} g."
            )
        if advance_heating:
            self._advance_heating_after_record()
        return True

    def _maybe_record_scheduled_point(
        self,
        *,
        quiet: bool = True,
        advance_heating: bool = False,
        require_fresh_after_move: bool | None = None,
        force: bool = False,
    ) -> bool:
        if not self._session_active:
            return True
        if not self._session_logging_enabled:
            return True
        now_s = time.time()
        last_s = self._last_session_log_timestamp_s
        interval_s = self._current_sweep_log_interval_ms() / 1000.0
        if not force and last_s is not None and now_s - last_s < interval_s:
            return True
        if (
            not force
            and require_fresh_after_move
            and self._automation_basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            and not self._has_fresh_scale_reading(after_s=self._motion_feedback_ready_after_s())
        ):
            self._log_waiting_for_feedback("Waiting for a fresh scale reading before logging the next load/stress point.")
            return True
        return self._record_current_point(
            quiet=quiet,
            advance_heating=advance_heating,
            require_fresh_after_move=require_fresh_after_move,
        )

    def _record_recovery_point(self) -> bool:
        try:
            self._refresh_tic_status()
        except Exception:
            pass
        elapsed_s = 0.0
        if self._recovery_start_monotonic > 0.0:
            elapsed_s = time.monotonic() - self._recovery_start_monotonic
        point = self._capture_measurement_point(
            elapsed_s=elapsed_s,
            position_mm=self._current_position_mm,
            effective_position_mm=self._effective_position_mm,
            raw_load_g=self._latest_scale_value_g,
            load_g=self._current_effective_load_g(),
        )
        self._recovery_points.append(point)
        self._recovery_last_record_scale_timestamp = self._latest_scale_timestamp
        now_s = time.monotonic()
        if self._dialog_plot_refresh_due(self._last_recovery_plot_refresh_s, now_s=now_s):
            self._last_recovery_plot_refresh_s = now_s
            self._refresh_recovery_plot()
        self._refresh_live_labels()
        return True

    def _write_point_to_handles(
        self,
        point: MeasurementPoint,
        *,
        txt_handle: Any,
        csv_writer: csv.DictWriter[str],
        csv_handle: Any,
    ) -> None:
        if txt_handle is None or csv_writer is None:
            return
        txt_values = (
            f"{point.position_mm:.6f}",
            f"{point.load_g:.6f}",
            "" if point.strain_pct is None else f"{point.strain_pct:.6f}",
            "" if point.stress_mpa is None else f"{point.stress_mpa:.6f}",
        )
        txt_handle.write("\t".join(txt_values) + "\n")
        txt_handle.flush()

        csv_writer.writerow(
            {
                "elapsed_s": f"{point.elapsed_s:.6f}",
                "timestamp_utc": point.timestamp_utc,
                "recipe_mode": str(self.combo_recipe_mode.currentData() or "ramp"),
                "automation_phase": point.automation_phase,
                "automation_basis": "" if point.automation_basis is None else point.automation_basis,
                "automation_target_value": ""
                if point.automation_target_value is None
                else f"{point.automation_target_value:.6f}",
                "plateau_index": "" if point.plateau_index is None else point.plateau_index,
                "plateau_label": "" if point.plateau_label is None else point.plateau_label,
                "raw_position_mm": f"{point.raw_position_mm:.6f}",
                "position_mm": f"{point.position_mm:.6f}",
                "raw_load_g": f"{point.raw_load_g:.6f}",
                "load_g": f"{point.load_g:.6f}",
                "load_raw_last_g": "" if point.load_raw_last_g is None else f"{point.load_raw_last_g:.6f}",
                "load_mean_g": "" if point.load_mean_g is None else f"{point.load_mean_g:.6f}",
                "load_std_g": "" if point.load_std_g is None else f"{point.load_std_g:.6f}",
                "load_min_g": "" if point.load_min_g is None else f"{point.load_min_g:.6f}",
                "load_max_g": "" if point.load_max_g is None else f"{point.load_max_g:.6f}",
                "load_sample_count": point.load_sample_count,
                "scale_sample_rate_hz": ""
                if point.scale_sample_rate_hz is None
                else f"{point.scale_sample_rate_hz:.6f}",
                "preload_state": point.preload_state,
                "strain_pct": "" if point.strain_pct is None else f"{point.strain_pct:.6f}",
                "stress_mpa": "" if point.stress_mpa is None else f"{point.stress_mpa:.6f}",
                "current_zero_position_mm": ""
                if point.current_zero_position_mm is None
                else f"{point.current_zero_position_mm:.6f}",
                "current_l0_mm": "" if point.current_l0_mm is None else f"{point.current_l0_mm:.6f}",
                "current_relative_position_mm": ""
                if point.current_relative_position_mm is None
                else f"{point.current_relative_position_mm:.6f}",
                "current_relative_strain_pct": ""
                if point.current_relative_strain_pct is None
                else f"{point.current_relative_strain_pct:.6f}",
                "current_set_mA": "" if point.current_set_mA is None else f"{point.current_set_mA:.6f}",
                "current_measured_mA": "" if point.current_measured_mA is None else f"{point.current_measured_mA:.6f}",
                "voltage_V": "" if point.voltage_V is None else f"{point.voltage_V:.6f}",
                "resistance_ohm": "" if point.resistance_ohm is None else f"{point.resistance_ohm:.6f}",
                "power_W": "" if point.power_W is None else f"{point.power_W:.6f}",
            }
        )
        if csv_handle is not None:
            csv_handle.flush()

    def _write_point(self, point: MeasurementPoint) -> None:
        if self._session_txt_handle is None or self._session_csv_writer is None:
            return
        self._write_point_to_handles(
            point,
            txt_handle=self._session_txt_handle,
            csv_writer=self._session_csv_writer,
            csv_handle=self._session_csv_handle,
        )

    def _write_setup_point(self, point: MeasurementPoint) -> None:
        if self._session_setup_txt_handle is None or self._session_setup_csv_writer is None:
            return
        self._write_point_to_handles(
            point,
            txt_handle=self._session_setup_txt_handle,
            csv_writer=self._session_setup_csv_writer,
            csv_handle=self._session_setup_csv_handle,
        )

    def _recipe_requires_tic(self, steps: Sequence[AutomationStep]) -> bool:
        return any(step.action in {"move", "seek_target", "ramp_target", "calibration_move", "mechanical_scan"} for step in steps)

    def _recipe_requires_scale(self, steps: Sequence[AutomationStep]) -> bool:
        if any(step.action in {"calibration_record", "calibration_move"} for step in steps):
            return True
        return any(
            step.action in {"seek_target", "ramp_target", "mechanical_scan"}
            and step.basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            for step in steps
        )

    def _recipe_requires_supply(self, steps: Sequence[AutomationStep]) -> bool:
        if self._motor_supply_enabled():
            return True
        if any(step.action == "set_current" for step in steps):
            return True
        if self._continuity_monitor_enabled() and self._continuity_current_mA() > 0.0:
            return True
        mode = str(self.combo_recipe_mode.currentData() or "")
        if self._is_calibration_mode(mode) or self._is_calibration_mode(self._automation_name):
            return False
        return not self._is_current_sweep_mode() and self._heating_mode() != HEATING_MODE_OFF

    def _ensure_scale_ready_for_recipe(self) -> bool:
        if self._scale_thread is not None:
            return True
        self._log("Preflight: scale is not connected, trying auto-detect/connect.")
        if not str(self.combo_scale_port.currentData() or "").strip():
            self._refresh_scale_ports()
        self._auto_detect_scale_port()
        return self._connect_scale(show_errors=False)

    def _ensure_supply_ready_for_recipe(self) -> bool:
        if self._supply_controller is not None and self._supply_controller.is_connected():
            return True
        self._log("Preflight: power supply is not connected, trying auto-detect/connect.")
        if not self._using_shared_broker_supply() and not str(self.combo_supply_port.currentData() or "").strip():
            self._refresh_supply_ports()
        if not self._using_shared_broker_supply():
            self._auto_detect_supply_port()
        return self._connect_supply(show_errors=False)

    def _ensure_tic_ready_for_recipe(self) -> bool:
        if not self.edit_tic_serial.text().strip():
            self._log("Preflight: Tic controller is not selected, trying auto-detect.")
            self._auto_detect_tic()
        if not self._refresh_tic_status():
            return False
        return self._tic_motor_power_ok is not False

    def _recipe_preflight_needs_progress(self, steps: Sequence[AutomationStep]) -> bool:
        if self._manual_auto_connect_progress is not None:
            return False
        supply_ready = self._supply_controller is not None and self._supply_controller.is_connected()
        return (
            (self._recipe_requires_supply(steps) and not supply_ready)
            or (self._recipe_requires_scale(steps) and self._scale_thread is None)
            or (self._recipe_requires_tic(steps) and self._tic_motor_power_ok is not True)
        )

    def _preflight_recipe_hardware(self, steps: Sequence[AutomationStep], *, show_progress: bool = False) -> bool:
        started_progress = False
        preflight_steps = 4
        if show_progress and self._recipe_preflight_needs_progress(steps):
            started_progress = True
            self._show_manual_auto_connect_progress()
            self._set_manual_auto_connect_progress("Checking recipe hardware...", 0, preflight_steps)
            self._log("Recipe hardware auto-connect started.")
        issues: list[str] = []
        try:
            self._set_manual_auto_connect_progress("Checking power supply...", 0, preflight_steps)
            if self._recipe_requires_supply(steps) and not self._ensure_supply_ready_for_recipe():
                issues.append("Power supply is not connected. Use Auto-detect/connect supply and check the supply is powered on.")
            self._set_manual_auto_connect_progress("Checking motor supply...", 1, preflight_steps)
            if not issues and self._motor_supply_enabled() and not self._enable_motor_supply_output():
                issues.append("Motor supply channel could not be enabled. Check the HMP channel wiring/settings.")
            self._set_manual_auto_connect_progress("Checking motor controller...", 2, preflight_steps)
            if self._recipe_requires_tic(steps) and not self.check_tic_native_usb.isChecked():
                issues.append(
                    "Mini DMA recipes require native USB Tic control. "
                    "Enable 'Prefer native USB commands when available' and run Check motor again. "
                    "ticcmd remains available for diagnostics only."
                )
            if not issues and self._recipe_requires_tic(steps) and not self._ensure_tic_ready_for_recipe():
                vin_text = "-" if self._last_tic_vin_v is None else f"{self._last_tic_vin_v:.2f} V"
                if self._last_tic_status_error:
                    issues.append(
                        "Motor controller status could not be read "
                        f"({self._last_tic_status_error}). "
                        "Close other Mini DMA/test processes that may be using the Tic USB device, "
                        "then run Check motor again."
                    )
                else:
                    issues.append(
                        "Motor controller is reachable, but motor power is not ready "
                        f"(VIN {vin_text}; expected at least {TIC_MOTOR_POWER_MIN_V:.1f} V). "
                        "Turn on the motor supply, or enable the HMP motor-supply channel option and run Check motor again."
                    )
            if not issues and self._recipe_requires_tic(steps):
                tic_limit_ok, tic_limit_message = self._apply_tic_current_limit()
                self._log(f"Recipe preflight: {tic_limit_message}")
                if not tic_limit_ok:
                    issues.append(tic_limit_message.replace("FAIL: ", "", 1))
            self._set_manual_auto_connect_progress("Checking scale...", 3, preflight_steps)
            if self._recipe_requires_scale(steps) and not self._ensure_scale_ready_for_recipe():
                issues.append(
                    "Scale is not connected. Use Auto-detect scale, then verify the zero-load reference, "
                    "and fix the serial link if it still fails."
                )
            if not issues and self._recipe_requires_scale(steps):
                with self._scale_state_lock:
                    latest_raw_g = self._latest_scale_value_g
                self._restore_default_zero_load_reference_if_real_grams(float(latest_raw_g))
            if not issues:
                self._set_manual_auto_connect_progress("Recipe hardware ready.", preflight_steps, preflight_steps)
                return True
            message = "Recipe preflight failed:\n\n" + "\n".join(f"- {issue}" for issue in issues)
            self._log(message.replace("\n", " "))
            QtWidgets.QMessageBox.warning(self, APP_NAME, message)
            return False
        finally:
            if started_progress:
                self._close_manual_auto_connect_progress()

    def _apply_tic_current_limit(self) -> tuple[bool, str]:
        target_mA = float(self.spin_tic_current_limit_mA.value())
        safe_mA = safe_tic_current_limit_mA(target_mA)
        try:
            controller = self._build_tic_controller()
            method = getattr(controller, "set_current_limit_mA", None)
            if callable(method):
                applied_mA = int(method(target_mA))
            else:
                applied_mA = apply_tic_current_limit_mA(controller, target_mA)
        except Exception as exc:
            reported_mA = _extract_tic_current_limit_mA(self._tic_status_text)
            if reported_mA == safe_mA:
                return (
                    True,
                    f"PASS: Tic current limit already {safe_mA} mA "
                    f"(write skipped because the controller handle was busy: {exc}).",
                )
            return False, f"FAIL: Tic current limit could not be set ({exc})."
        if applied_mA != safe_mA:
            return False, f"FAIL: Tic current limit returned {applied_mA} mA, expected {safe_mA} mA."
        return True, f"PASS: Tic current limit {applied_mA} mA."

    def _provision_bench_hardware(self, _checked: bool = False) -> bool:
        statuses: list[str] = []
        ok = True

        if not self._ensure_supply_ready_for_recipe():
            statuses.append("FAIL: HMP supply is not connected.")
            ok = False
        else:
            try:
                if self._supply_controller is None:
                    raise RuntimeError("supply controller is missing after connect")
                channel = self._motor_supply_channel()
                if channel is None:
                    raise RuntimeError("select a motor supply channel first")
                voltage = float(self.spin_motor_supply_voltage.value())
                current_limit = float(self.spin_motor_supply_current_limit.value())
                self._supply_controller.configure_channel(
                    channel=channel,
                    voltage_v=voltage,
                    current_a=current_limit,
                    output_on=True,
                )
                current_channel = self._current_sweep_supply_channel()
                if current_channel is not None:
                    self._supply_controller.select_channel(current_channel)
                statuses.append(
                    f"PASS: Motor supply CH{channel} set to "
                    f"{_format_compact_number(voltage, decimals=2)} V / "
                    f"{_format_compact_number(current_limit, decimals=3)} A."
                )
            except Exception as exc:
                statuses.append(f"FAIL: Motor supply channel setup failed ({exc}).")
                ok = False
            channel = self._current_sweep_supply_channel()
            if channel is None:
                statuses.append("FAIL: Select a current-sweep supply channel.")
                ok = False
            else:
                statuses.append(f"PASS: Current-sweep supply channel CH{channel} selected.")

        if not self._ensure_scale_ready_for_recipe():
            statuses.append("FAIL: Scale did not connect/respond with the selected preset.")
            ok = False
        else:
            statuses.append("PASS: Scale serial preset connected/responding.")

        tic_ok, tic_message = self._apply_tic_current_limit()
        statuses.append(tic_message)
        ok = ok and tic_ok
        if not self._ensure_tic_ready_for_recipe():
            statuses.append("FAIL: Tic status/VIN check failed.")
            ok = False
        else:
            statuses.append("PASS: Tic status/VIN check passed.")

        status_text = "\n".join(statuses)
        self.label_hardware_provisioning_status.setText(status_text)
        for line in statuses:
            self._log(f"Bench provisioning: {line}")
        return ok

    def _recipe_number_token(self, value: float, *, decimals: int = 3) -> str:
        text = _format_compact_number(float(value), decimals=decimals)
        return text.replace(".", "p").replace("-", "m")

    def _suggest_recipe_filename(self) -> str:
        mode = str(self.combo_recipe_mode.currentData() or "recipe")
        if mode == CURRENT_SWEEP_STRESS:
            prefix = "iso-stress"
            target_unit = "MPa"
        elif mode == CURRENT_SWEEP_LOAD:
            prefix = "iso-load"
            target_unit = "g"
        elif mode == CURRENT_SWEEP_STRAIN:
            prefix = "iso-strain"
            target_unit = "pct"
        elif mode == CONSTANT_CURRENT_STRAIN_SWEEP:
            setup = self._recipe_number_token(self.spin_setup_preload_stress_mpa.value())
            target_start = self._recipe_number_token(self.spin_constant_current_start_target.value())
            target_end = self._recipe_number_token(self.spin_constant_current_end_target.value())
            step = self._recipe_number_token(abs(self.spin_constant_current_step_size.value()))
            current_start = self._recipe_number_token(self.spin_constant_current_start_mA.value())
            current_end = self._recipe_number_token(self.spin_constant_current_end_mA.value())
            current_step = self._recipe_number_token(self.spin_constant_current_step_mA.value())
            step_unit = "pct" if self._constant_current_step_basis() == HSW_BASIS_STRAIN_PCT else "mm"
            basis_token = {
                HSW_BASIS_LOAD_G: "load",
                HSW_BASIS_STRESS_MPA: "stress",
                HSW_BASIS_STRAIN_PCT: "strain",
            }.get(self._constant_current_start_basis(), "target")
            return (
                f"constant-current-stress-strain_setup{setup}MPa_{basis_token}{target_start}-{target_end}_"
                f"step{step}{step_unit}_current{current_start}-{current_end}x{current_step}mA.recipe.json"
            )
        else:
            prefix = re.sub(r"[^a-z0-9]+", "-", mode.lower()).strip("-") or "recipe"
            return f"{prefix}.recipe.json"
        setup = self._recipe_number_token(self.spin_setup_preload_stress_mpa.value())
        target_start = self._recipe_number_token(self.spin_current_sweep_target_start.value())
        target_end = self._recipe_number_token(self.spin_current_sweep_target_end.value())
        target_step = self._recipe_number_token(self.spin_current_sweep_target_step.value())
        current_start = self._recipe_number_token(self.spin_current_sweep_start_mA.value())
        current_end = self._recipe_number_token(self.spin_current_sweep_end_mA.value())
        current_rate = self._recipe_number_token(self.spin_current_sweep_step_mA.value())
        flags: list[str] = []
        if self.check_current_sweep_hold_on_error.isChecked():
            flags.append("hold")
        if self.check_current_sweep_first_overheating.isChecked():
            preheat = self._recipe_number_token(self.spin_current_sweep_first_overheating_target_mpa.value())
            flags.append(f"firstheat{preheat}MPa")
        flag_text = "" if not flags else "_" + "_".join(flags)
        return (
            f"{prefix}_setup{setup}MPa_target{target_start}-{target_end}x{target_step}{target_unit}_"
            f"current{current_start}-{current_end}mA_{current_rate}mAps{flag_text}.recipe.json"
        )

    def _current_recipe_payload(self) -> dict[str, Any]:
        mode = str(self.combo_recipe_mode.currentData() or "ramp")
        payload: dict[str, Any] = {
            "schema_version": 1,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "app": APP_NAME,
            "recipe": {
                "mode": mode,
                "setup": {
                    "enabled": self._pre_measurement_setup_enabled(mode),
                    "preload_stress_mpa": float(self.spin_setup_preload_stress_mpa.value()),
                    "preload_duration_s": float(self.spin_setup_preload_duration_s.value()),
                    "return_duration_s": float(self.spin_setup_return_duration_s.value()),
                    "slack_speed_strain_pct_s": float(self.spin_setup_slack_speed_strain_pct_s.value()),
                    "slack_step_cap_stress_mpa": float(self.spin_setup_slack_step_cap_stress_mpa.value()),
                    "preload_tolerance_mpa": float(self.spin_setup_preload_tolerance_mpa.value()),
                    "zero_tolerance_g": float(self.spin_setup_zero_tolerance_g.value()),
                    "preload_stable_s": float(self.spin_setup_preload_stable_s.value()),
                    "zero_stable_s": float(self.spin_setup_zero_stable_s.value()),
                },
                "timing": {
                    "control_interval_ms": int(self._control_interval_ms()),
                    "log_interval_ms": int(self._log_interval_ms()),
                },
            },
        }
        if self._is_current_sweep_mode(mode):
            payload["recipe"]["current_sweep"] = {
                "basis": self._current_sweep_basis(),
                "target_start": float(self.spin_current_sweep_target_start.value()),
                "target_end": float(self.spin_current_sweep_target_end.value()),
                "target_step": float(self.spin_current_sweep_target_step.value()),
                "target_ramp_rate": float(self.spin_current_sweep_target_ramp_rate.value()),
                "target_speed_mm_s": float(self.spin_current_sweep_target_speed_mm_s.value()),
                "current_start_mA": float(self.spin_current_sweep_start_mA.value()),
                "current_end_mA": float(self.spin_current_sweep_end_mA.value()),
                "current_ramp_rate_mA_s": float(self.spin_current_sweep_step_mA.value()),
                "hold_on_error": bool(self.check_current_sweep_hold_on_error.isChecked()),
                "hold_pause_factor": float(self.spin_current_sweep_hold_pause_factor.value()),
                "hold_resume_factor": float(self.spin_current_sweep_hold_resume_factor.value()),
                "hold_resume_stable_s": float(self.spin_current_sweep_hold_resume_stable_s.value()),
                "hold_filter_window_s": float(self.spin_current_sweep_hold_filter_window_s.value()),
                "hold_noise_sigma": float(self.spin_current_sweep_hold_noise_sigma.value()),
                "hold_min_pause_stress_mpa": float(self.spin_current_sweep_hold_min_pause_stress_mpa.value()),
                "hold_min_resume_stress_mpa": float(self.spin_current_sweep_hold_min_resume_stress_mpa.value()),
                "max_correction_strain_pct": float(self.spin_current_sweep_max_correction_strain_pct.value()),
                "correction_rate_pct_s": float(self.spin_current_sweep_correction_rate_pct_s.value()),
                "max_correction_stress_mpa": float(self.spin_current_sweep_max_correction_stress_mpa.value()),
                "hold_correction_stress_mpa": float(self.spin_current_sweep_hold_correction_stress_mpa.value()),
                "mid_correction_stress_mpa": float(self.spin_current_sweep_mid_correction_stress_mpa.value()),
                "near_correction_stress_mpa": float(self.spin_current_sweep_near_correction_stress_mpa.value()),
                "return_target": bool(self.check_current_sweep_return_target.isChecked()),
                "first_overheating": bool(self.check_current_sweep_first_overheating.isChecked()),
                "first_overheating_target_mpa": float(
                    self.spin_current_sweep_first_overheating_target_mpa.value()
                ),
                "reverse_current": bool(self.check_current_sweep_reverse_current.isChecked()),
                "tolerance": float(self.spin_current_sweep_tolerance.value()),
                "nudge_mm": float(self.spin_current_sweep_nudge_mm.value()),
                "balance_speed_mm_s": float(self.spin_current_sweep_balance_speed_mm_s.value()),
                "max_seek_mm": float(self.spin_current_sweep_max_seek_mm.value()),
            }
        if self._is_constant_current_strain_sweep_mode(mode):
            payload["recipe"]["constant_current_stress_strain"] = {
                "start_basis": self._constant_current_start_basis(),
                "target_start": float(self.spin_constant_current_start_target.value()),
                "target_end": float(self.spin_constant_current_end_target.value()),
                "step_basis": self._constant_current_step_basis(),
                "step_size": float(self.spin_constant_current_step_size.value()),
                "hold_s": float(self.spin_constant_current_hold_s.value()),
                "move_speed_mm_s": float(self.spin_constant_current_move_speed_mm_s.value()),
                "current_start_mA": float(self.spin_constant_current_start_mA.value()),
                "current_end_mA": float(self.spin_constant_current_end_mA.value()),
                "current_step_mA": float(self.spin_constant_current_step_mA.value()),
                "return_to_start": bool(self.check_constant_current_return_to_start.isChecked()),
            }
        return payload

    def _recipe_signature_from_payload(self, payload: Mapping[str, Any]) -> str:
        recipe = payload.get("recipe")
        if not isinstance(recipe, Mapping):
            recipe = {}
        return json.dumps(recipe, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _current_recipe_signature(self) -> str:
        return self._recipe_signature_from_payload(self._current_recipe_payload())

    def _update_recipe_file_status(self) -> None:
        if not hasattr(self, "label_recipe_file_status"):
            return
        if self._saved_recipe_signature is None or self._loaded_recipe_path is None:
            self.label_recipe_file_status.setText("Unsaved recipe")
            self.label_recipe_file_status.setStyleSheet("color: #dc2626; font-weight: 600;")
            return
        name = self._loaded_recipe_path.name
        if self._current_recipe_signature() == self._saved_recipe_signature:
            self.label_recipe_file_status.setText(f"Saved: {name}")
            self.label_recipe_file_status.setStyleSheet("color: #16a34a; font-weight: 600;")
        else:
            self.label_recipe_file_status.setText(f"Unsaved changes: {name}")
            self.label_recipe_file_status.setStyleSheet("color: #dc2626; font-weight: 600;")

    def _apply_recipe_payload(self, payload: Mapping[str, Any]) -> None:
        recipe = payload.get("recipe")
        if not isinstance(recipe, Mapping):
            raise ValueError("Recipe file is missing the recipe object.")
        mode = str(recipe.get("mode", ""))
        mode_index = self.combo_recipe_mode.findData(mode)
        if mode_index < 0:
            raise ValueError(f"Unsupported recipe mode: {mode or '<missing>'}")
        self.combo_recipe_mode.setCurrentIndex(mode_index)
        setup = recipe.get("setup")
        if isinstance(setup, Mapping):
            self.check_pre_measurement_setup_enabled.setChecked(bool(setup.get("enabled", True)))
            self.spin_setup_preload_stress_mpa.setValue(float(setup.get("preload_stress_mpa", self.spin_setup_preload_stress_mpa.value())))
            self.spin_setup_preload_duration_s.setValue(float(setup.get("preload_duration_s", self.spin_setup_preload_duration_s.value())))
            self.spin_setup_return_duration_s.setValue(float(setup.get("return_duration_s", self.spin_setup_return_duration_s.value())))
            self.spin_setup_slack_speed_strain_pct_s.setValue(float(setup.get("slack_speed_strain_pct_s", self.spin_setup_slack_speed_strain_pct_s.value())))
            self.spin_setup_slack_step_cap_stress_mpa.setValue(float(setup.get("slack_step_cap_stress_mpa", self.spin_setup_slack_step_cap_stress_mpa.value())))
            self.spin_setup_preload_tolerance_mpa.setValue(float(setup.get("preload_tolerance_mpa", self.spin_setup_preload_tolerance_mpa.value())))
            self.spin_setup_zero_tolerance_g.setValue(float(setup.get("zero_tolerance_g", self.spin_setup_zero_tolerance_g.value())))
            self.spin_setup_preload_stable_s.setValue(float(setup.get("preload_stable_s", self.spin_setup_preload_stable_s.value())))
            self.spin_setup_zero_stable_s.setValue(float(setup.get("zero_stable_s", self.spin_setup_zero_stable_s.value())))
        timing = recipe.get("timing")
        if isinstance(timing, Mapping):
            self.spin_control_interval.setValue(int(timing.get("control_interval_ms", self.spin_control_interval.value())))
            self.spin_log_interval.setValue(int(timing.get("log_interval_ms", self.spin_log_interval.value())))
        current_sweep = recipe.get("current_sweep")
        if isinstance(current_sweep, Mapping):
            basis = str(current_sweep.get("basis", self._current_sweep_basis()))
            basis_mode = self._current_sweep_mode_for_basis(basis)
            basis_index = self.combo_recipe_mode.findData(basis_mode)
            if basis_index >= 0:
                self.combo_recipe_mode.setCurrentIndex(basis_index)
            self.spin_current_sweep_target_start.setValue(float(current_sweep.get("target_start", self.spin_current_sweep_target_start.value())))
            self.spin_current_sweep_target_end.setValue(float(current_sweep.get("target_end", self.spin_current_sweep_target_end.value())))
            self.spin_current_sweep_target_step.setValue(float(current_sweep.get("target_step", self.spin_current_sweep_target_step.value())))
            self.spin_current_sweep_target_ramp_rate.setValue(float(current_sweep.get("target_ramp_rate", self.spin_current_sweep_target_ramp_rate.value())))
            self.spin_current_sweep_target_speed_mm_s.setValue(float(current_sweep.get("target_speed_mm_s", self.spin_current_sweep_target_speed_mm_s.value())))
            self.spin_current_sweep_start_mA.setValue(float(current_sweep.get("current_start_mA", self.spin_current_sweep_start_mA.value())))
            self.spin_current_sweep_end_mA.setValue(float(current_sweep.get("current_end_mA", self.spin_current_sweep_end_mA.value())))
            self.spin_current_sweep_step_mA.setValue(float(current_sweep.get("current_ramp_rate_mA_s", self.spin_current_sweep_step_mA.value())))
            self.check_current_sweep_hold_on_error.setChecked(bool(current_sweep.get("hold_on_error", self.check_current_sweep_hold_on_error.isChecked())))
            self.spin_current_sweep_hold_pause_factor.setValue(float(current_sweep.get("hold_pause_factor", self.spin_current_sweep_hold_pause_factor.value())))
            self.spin_current_sweep_hold_resume_factor.setValue(float(current_sweep.get("hold_resume_factor", self.spin_current_sweep_hold_resume_factor.value())))
            self.spin_current_sweep_hold_resume_stable_s.setValue(float(current_sweep.get("hold_resume_stable_s", self.spin_current_sweep_hold_resume_stable_s.value())))
            self.spin_current_sweep_hold_filter_window_s.setValue(float(current_sweep.get("hold_filter_window_s", self.spin_current_sweep_hold_filter_window_s.value())))
            self.spin_current_sweep_hold_noise_sigma.setValue(float(current_sweep.get("hold_noise_sigma", self.spin_current_sweep_hold_noise_sigma.value())))
            self.spin_current_sweep_hold_min_pause_stress_mpa.setValue(float(current_sweep.get("hold_min_pause_stress_mpa", self.spin_current_sweep_hold_min_pause_stress_mpa.value())))
            self.spin_current_sweep_hold_min_resume_stress_mpa.setValue(float(current_sweep.get("hold_min_resume_stress_mpa", self.spin_current_sweep_hold_min_resume_stress_mpa.value())))
            self.spin_current_sweep_max_correction_strain_pct.setValue(float(current_sweep.get("max_correction_strain_pct", self.spin_current_sweep_max_correction_strain_pct.value())))
            self.spin_current_sweep_correction_rate_pct_s.setValue(float(current_sweep.get("correction_rate_pct_s", self.spin_current_sweep_correction_rate_pct_s.value())))
            self.spin_current_sweep_max_correction_stress_mpa.setValue(float(current_sweep.get("max_correction_stress_mpa", self.spin_current_sweep_max_correction_stress_mpa.value())))
            self.spin_current_sweep_hold_correction_stress_mpa.setValue(float(current_sweep.get("hold_correction_stress_mpa", self.spin_current_sweep_hold_correction_stress_mpa.value())))
            self.spin_current_sweep_mid_correction_stress_mpa.setValue(float(current_sweep.get("mid_correction_stress_mpa", self.spin_current_sweep_mid_correction_stress_mpa.value())))
            self.spin_current_sweep_near_correction_stress_mpa.setValue(float(current_sweep.get("near_correction_stress_mpa", self.spin_current_sweep_near_correction_stress_mpa.value())))
            self.check_current_sweep_return_target.setChecked(
                bool(current_sweep.get("return_target", self.check_current_sweep_return_target.isChecked()))
            )
            first_overheating_enabled = current_sweep.get(
                "first_overheating",
                current_sweep.get(
                    "first_overheating_repeat",
                    self.check_current_sweep_first_overheating.isChecked(),
                ),
            )
            self.check_current_sweep_first_overheating.setChecked(bool(first_overheating_enabled))
            self.spin_current_sweep_first_overheating_target_mpa.setValue(
                float(
                    current_sweep.get(
                        "first_overheating_target_mpa",
                        current_sweep.get("target_start", self.spin_current_sweep_first_overheating_target_mpa.value()),
                    )
                )
            )
            self.check_current_sweep_reverse_current.setChecked(bool(current_sweep.get("reverse_current", self.check_current_sweep_reverse_current.isChecked())))
            self.spin_current_sweep_tolerance.setValue(float(current_sweep.get("tolerance", self.spin_current_sweep_tolerance.value())))
            self.spin_current_sweep_nudge_mm.setValue(float(current_sweep.get("nudge_mm", self.spin_current_sweep_nudge_mm.value())))
            self.spin_current_sweep_balance_speed_mm_s.setValue(float(current_sweep.get("balance_speed_mm_s", self.spin_current_sweep_balance_speed_mm_s.value())))
            self.spin_current_sweep_max_seek_mm.setValue(float(current_sweep.get("max_seek_mm", self.spin_current_sweep_max_seek_mm.value())))
        constant_current = recipe.get("constant_current_stress_strain")
        if isinstance(constant_current, Mapping):
            basis = str(constant_current.get("start_basis", self._constant_current_start_basis()))
            basis_index = self.combo_constant_current_start_basis.findData(basis)
            if basis_index >= 0:
                self.combo_constant_current_start_basis.setCurrentIndex(basis_index)
            step_basis = str(constant_current.get("step_basis", self._constant_current_step_basis()))
            step_basis_index = self.combo_constant_current_step_basis.findData(step_basis)
            if step_basis_index >= 0:
                self.combo_constant_current_step_basis.setCurrentIndex(step_basis_index)
            self.spin_constant_current_start_target.setValue(float(constant_current.get("target_start", self.spin_constant_current_start_target.value())))
            self.spin_constant_current_end_target.setValue(float(constant_current.get("target_end", self.spin_constant_current_end_target.value())))
            self.spin_constant_current_step_size.setValue(float(constant_current.get("step_size", self.spin_constant_current_step_size.value())))
            self.spin_constant_current_hold_s.setValue(float(constant_current.get("hold_s", self.spin_constant_current_hold_s.value())))
            self.spin_constant_current_move_speed_mm_s.setValue(float(constant_current.get("move_speed_mm_s", self.spin_constant_current_move_speed_mm_s.value())))
            self.spin_constant_current_start_mA.setValue(float(constant_current.get("current_start_mA", self.spin_constant_current_start_mA.value())))
            self.spin_constant_current_end_mA.setValue(float(constant_current.get("current_end_mA", self.spin_constant_current_end_mA.value())))
            self.spin_constant_current_step_mA.setValue(float(constant_current.get("current_step_mA", self.spin_constant_current_step_mA.value())))
            self.check_constant_current_return_to_start.setChecked(bool(constant_current.get("return_to_start", self.check_constant_current_return_to_start.isChecked())))
        self._update_recipe_mode_ui()

    def _save_recipe_to_path(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self._current_recipe_payload()
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        self._loaded_recipe_path = target
        self._saved_recipe_signature = self._recipe_signature_from_payload(payload)
        self._update_recipe_file_status()
        self._log(f"Saved recipe to {target}.")

    def _load_recipe_from_path(self, path: str | Path) -> None:
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("Recipe file must contain a JSON object.")
        self._apply_recipe_payload(payload)
        self._loaded_recipe_path = source
        self._saved_recipe_signature = self._current_recipe_signature()
        self._update_recipe_file_status()
        self._log(f"Loaded recipe from {source}: {self._suggest_recipe_filename()}.")

    def _save_recipe_dialog(self) -> None:
        start_dir = Path(self.edit_log_dir.text().strip() or _default_download_dir())
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Mini DMA recipe",
            str(start_dir / self._suggest_recipe_filename()),
            "Mini DMA recipe (*.recipe.json);;JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            self._save_recipe_to_path(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to save recipe: {exc}")

    def _load_recipe_dialog(self) -> None:
        start_dir = self.edit_log_dir.text().strip() or _default_download_dir()
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load Mini DMA recipe",
            start_dir,
            "Mini DMA recipe (*.recipe.json);;JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            self._load_recipe_from_path(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Failed to load recipe: {exc}")

    def _automation_target_text(self, basis: str | None, target_value: float | None) -> str:
        if basis is None or target_value is None:
            return "target"
        suffix, _ = self._distribution_units(basis)
        return f"{_format_compact_number(float(target_value))}{suffix}"

    def _automation_current_target_text(self, current_mA: float | None) -> str:
        if current_mA is None:
            return "current"
        return f"{_format_compact_number(float(current_mA), decimals=3)} mA"

    def _current_sweep_step_task_summary(self, step: AutomationStep) -> str:
        target_text = self._automation_target_text(step.basis, step.target_value)
        start_mA = step.current_start_mA
        end_mA = step.current_end_mA
        if start_mA is None or end_mA is None:
            return f"Current sweep at {target_text}"
        direction = "increasing" if float(end_mA) >= float(start_mA) else "decreasing"
        target_current = self._automation_current_target_text(end_mA)
        return f"At {target_text}: {direction} current to {target_current}"

    def _previous_current_sweep_step_for_settle(self, step_index: int, step: AutomationStep) -> AutomationStep | None:
        for previous in reversed(self._automation_steps[: max(0, step_index)]):
            if previous.action == "sweep_current":
                if previous.basis == step.basis and previous.target_value == step.target_value:
                    return previous
            if previous.action not in {"settle", "sweep_current"}:
                break
        return None

    def _current_task_summary(self) -> str:
        if not self._automation_active:
            return "Manual mode"
        if not self._automation_steps:
            return "Starting recipe"
        step_index = min(max(0, self._automation_index), len(self._automation_steps) - 1)
        step = self._automation_steps[step_index]
        target_text = self._automation_target_text(step.basis, step.target_value)

        if step.note == "setup_start_length":
            return "Setup: enter starting length"
        if step.note == "setup_preload":
            return f"Setup: ramp to preload {target_text}"
        if step.note == "setup_return_zero":
            return "Setup: return load to zero"
        if step.note == "setup_measure_length":
            return "Setup: measure loaded length"
        if step.action == "apply_length_setup":
            return "Setup: apply l0 baseline"
        if step.action == "start_session":
            return "Starting measurement log"

        if (
            self._is_current_sweep_mode(self._automation_name)
            and self._automation_phase in {"current", "current_hold", "current_limit_unwind"}
        ):
            context_target_text = self._automation_target_text(self._automation_basis, self._automation_target_value)
            if self._automation_phase == "current_hold":
                held = self._automation_current_target_text(self._active_current_sweep_last_setpoint_mA)
                return f"At {context_target_text}: holding {held}, recovering target"
            target_current = self._automation_current_target_text(self._active_current_sweep_display_target_mA)
            direction_value = self._active_current_sweep_display_direction
            if abs(direction_value) <= 1e-12 and self._active_current_sweep_display_target_mA is not None:
                start_for_display = self._active_current_sweep_last_setpoint_mA
                if start_for_display is not None:
                    direction_value = float(self._active_current_sweep_display_target_mA) - float(start_for_display)
            direction = "increasing" if direction_value >= 0.0 else "decreasing"
            return f"At {context_target_text}: {direction} current to {target_current}"

        if step.action == "ramp_target":
            end_value = step.target_end_value if step.target_end_value is not None else step.target_value
            end_text = self._automation_target_text(step.basis, end_value)
            if (
                self._is_current_sweep_mode(self._automation_name)
                and step.target_start_value is not None
                and step.target_end_value is not None
                and abs(float(step.target_end_value) - float(step.target_start_value)) > 1e-12
            ):
                direction = "Ramp up" if float(step.target_end_value) > float(step.target_start_value) else "Ramp down"
                return f"{direction} to {end_text}"
            return f"Ramp to {end_text}"

        if step.action == "sweep_current":
            return self._current_sweep_step_task_summary(step)

        if step.action == "mechanical_scan":
            current_text = self._automation_current_target_text(step.current_mA)
            return f"At {current_text}: fixed displacement steps toward {target_text}"

        if step.action == "settle":
            if self._is_current_sweep_mode(self._automation_name):
                previous_sweep = self._previous_current_sweep_step_for_settle(step_index, step)
                if previous_sweep is not None:
                    return self._current_sweep_step_task_summary(previous_sweep)
            return f"Settling at {target_text}"
        if step.action == "set_current":
            return f"Setting current to {self._automation_current_target_text(step.current_mA)}"
        if step.action == "record":
            return f"Recording {target_text}"
        if step.action == "move":
            return "Moving stage"
        if self._automation_phase not in {"idle", "start"}:
            return self._automation_phase.replace("_", " ").capitalize()
        return str(self.combo_recipe_mode.currentText())

    def _update_current_task_display(self) -> None:
        task_text = self._current_task_summary()
        if hasattr(self, "label_current_task"):
            self.label_current_task.setText(f"Current task: {task_text}")
        if hasattr(self, "label_recipe_banner"):
            self.label_recipe_banner.setText(task_text)
            self.label_recipe_banner.setVisible(self._automation_active)
        self._set_dashboard_value("task", task_text)

    def _update_recipe_progress(self, *, complete: bool = False) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(lambda complete=complete: self._update_recipe_progress(complete=complete))
            return
        total = max(1, self._automation_total_steps or len(self._automation_steps))
        if self._automation_active and not complete and self._automation_completed_ticks >= total:
            total = self._automation_completed_ticks + 1
            self._automation_total_steps = total
        value = total if complete else min(self._automation_completed_ticks, max(0, total - 1))
        self.recipe_progress.setRange(0, total)
        self.recipe_progress.setValue(value)
        percent = int(round((value / total) * 100.0))
        if complete:
            self.recipe_progress.setFormat(f"Recipe progress: complete ({total}/{total})")
        elif self._automation_active:
            now_s = time.monotonic()
            if self._automation_progress_started_s <= 0.0:
                self._automation_progress_started_s = now_s
            should_update_format = (
                self._automation_progress_last_format_update_s <= 0.0
                or now_s - self._automation_progress_last_format_update_s >= 1.0
            )
            if should_update_format:
                self._automation_progress_last_format_update_s = now_s
                progress_text = f"Recipe progress: {percent}% ({value}/{total})"
                elapsed_s = max(0.0, now_s - self._automation_progress_started_s)
                if value < total:
                    remaining_s = self._estimated_recipe_remaining_s(
                        value=value,
                        total=total,
                        elapsed_s=elapsed_s,
                    )
                else:
                    remaining_s = None
                if remaining_s is not None and remaining_s > 0.0:
                    progress_text += f", {_format_duration(remaining_s)} remaining"
                self.recipe_progress.setFormat(progress_text)
        else:
            self._automation_progress_started_s = 0.0
            self._automation_progress_last_format_update_s = 0.0
            self.recipe_progress.setFormat(getattr(self, "_recipe_idle_progress_text", "Recipe progress: idle"))
        self._update_current_task_display()
        self._update_length_setup_progress(value=value, total=total, complete=complete, percent=percent)

    def _timed_step_elapsed_for_progress_s(self, step_index: int) -> float:
        if self._active_timed_step_index != step_index:
            return 0.0
        return max(0.0, time.monotonic() - float(self._active_timed_step_started_s))

    def _monotonic_length_setup_phase_fraction(
        self,
        phase_key: tuple[object, ...],
        phase_fraction: float,
    ) -> float:
        clamped = max(0.0, min(1.0, float(phase_fraction)))
        if self._length_setup_progress_phase_key != phase_key:
            self._length_setup_progress_phase_key = phase_key
            self._length_setup_progress_fraction_floor = clamped
            return clamped
        self._length_setup_progress_fraction_floor = max(
            self._length_setup_progress_fraction_floor,
            clamped,
        )
        return self._length_setup_progress_fraction_floor

    def _update_length_setup_progress(
        self,
        *,
        value: int | None = None,
        total: int | None = None,
        complete: bool = False,
        percent: int | None = None,
    ) -> None:
        if self._length_setup_progress is None:
            return
        sample_count = len(self._length_setup_points)
        current_step = (
            self._automation_steps[self._automation_index]
            if 0 <= self._automation_index < len(self._automation_steps)
            else None
        )
        if complete:
            self._length_setup_progress_phase_key = None
            self._length_setup_progress_fraction_floor = 0.0
            self._length_setup_progress.setRange(0, 1000)
            self._length_setup_progress.setValue(1000)
            self._length_setup_progress.setFormat(f"Setup progress: complete ({sample_count} samples)")
            return
        if self._automation_active and current_step is not None:
            phase_text = ""
            phase_fraction: float | None = None
            phase_key: tuple[object, ...] | None = None
            if current_step.action == "ramp_target" and current_step.note == "setup_preload":
                phase_text = "Slack take-up"
                phase_key = (self._automation_index, current_step.action, current_step.note, "slack")
                slack_takeup = False
                target_value = (
                    current_step.target_end_value
                    if current_step.target_end_value is not None
                    else current_step.target_value
                )
                current_value = (
                    self._current_distribution_value(current_step.basis)
                    if current_step.basis
                    else None
                )
                if (
                    current_step.basis
                    and target_value is not None
                    and current_value is not None
                    and self._setup_preload_takeup_active(
                        current_step.basis,
                        float(current_value),
                        float(target_value) - float(current_value),
                        self._automation_tolerance_for_step(current_step),
                        seek_key=self._seek_error_key(current_step.basis, float(target_value)),
                    )
                ):
                    slack_takeup = True
                else:
                    phase_text = "Preload ramp"
                start_value = self._active_target_ramp_start_value
                rate_value_s = self._active_target_ramp_rate_value_s
                if (
                    not slack_takeup
                    and start_value is not None
                    and target_value is not None
                    and current_value is not None
                    and abs(float(start_value) - float(target_value)) > 1e-12
                ):
                    total_error = abs(float(start_value) - float(target_value))
                    remaining_error = abs(float(current_value) - float(target_value))
                    phase_fraction = 1.0 - min(1.0, remaining_error / total_error)
                    phase_key = (self._automation_index, current_step.action, current_step.note, "ramp")
                if (
                    not slack_takeup
                    and phase_fraction is None
                    and start_value is not None
                    and rate_value_s is not None
                    and target_value is not None
                    and abs(float(rate_value_s)) > 0.0
                ):
                    duration_s = abs(float(start_value) - float(target_value)) / abs(float(rate_value_s))
                    if duration_s > 0.0:
                        phase_fraction = (time.monotonic() - self._active_target_ramp_started_s) / duration_s
                        phase_key = (self._automation_index, current_step.action, current_step.note, "ramp")
            elif current_step.action == "settle" and current_step.note == "setup_preload":
                phase_text = "Preload settle"
                phase_key = (self._automation_index, current_step.action, current_step.note)
                duration_s = max(0.001, float(current_step.duration_s or 0.0))
                phase_fraction = self._timed_step_elapsed_for_progress_s(self._automation_index) / duration_s
            elif current_step.action == "mark_setup_return_zero":
                phase_text = "Length reference"
                phase_key = (self._automation_index, current_step.action, current_step.note)
                phase_fraction = 0.0
            elif current_step.action == "measure_length_prompt":
                phase_text = "Waiting for length"
                phase_key = (self._automation_index, current_step.action, current_step.note)
                phase_fraction = 0.0
            elif current_step.action == "seek_target" and current_step.note == "setup_return_zero":
                phase_text = "Return load to zero"
                phase_fraction = None
            elif current_step.action == "settle" and current_step.note == "setup_return_zero":
                phase_text = "Zero-load settle"
                phase_key = (self._automation_index, current_step.action, current_step.note)
                duration_s = max(0.001, float(current_step.duration_s or 0.0))
                phase_fraction = self._timed_step_elapsed_for_progress_s(self._automation_index) / duration_s
            elif current_step.action == "apply_length_setup":
                phase_text = "Apply length"
                phase_key = (self._automation_index, current_step.action, current_step.note)
                phase_fraction = 0.0
            if phase_text:
                if phase_fraction is None:
                    self._length_setup_progress_phase_key = None
                    self._length_setup_progress_fraction_floor = 0.0
                    self._length_setup_progress.setRange(0, 0)
                    self._length_setup_progress.setFormat(
                        f"Setup progress: {phase_text} ({sample_count} samples)"
                    )
                else:
                    if phase_key is not None:
                        phase_fraction = self._monotonic_length_setup_phase_fraction(phase_key, phase_fraction)
                    phase_percent = int(round(max(0.0, min(1.0, phase_fraction)) * 100.0))
                    self._length_setup_progress.setRange(0, 1000)
                    self._length_setup_progress.setValue(max(0, min(1000, phase_percent * 10)))
                    self._length_setup_progress.setFormat(
                        f"Setup progress: {phase_text} {phase_percent}% ({sample_count} samples)"
                    )
                return
        setup_total = 0
        for step in self._automation_steps:
            if step.action == "start_session":
                break
            setup_total += 1
        setup_total = max(1, setup_total)
        setup_value = setup_total if complete else min(max(0, self._automation_index), max(0, setup_total - 1))
        if self._automation_index >= setup_total:
            setup_value = setup_total
        setup_percent = int(round((setup_value / max(1, setup_total)) * 100.0))
        self._length_setup_progress.setRange(0, setup_total)
        self._length_setup_progress.setValue(max(0, min(setup_value, setup_total)))
        if self._automation_active:
            self._length_setup_progress.setFormat(
                f"Setup progress: {setup_percent}% ({setup_value}/{setup_total} steps, {sample_count} samples)"
            )
        else:
            self._length_setup_progress.setFormat("Setup progress: idle")

    def _update_recipe_buttons(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._update_recipe_buttons)
            return
        self.button_start_recipe.setEnabled(not self._automation_active or self._automation_paused)
        self.button_pause_recipe.setEnabled(self._automation_active)
        self.button_pause_recipe.setText("Resume recipe" if self._automation_paused else "Pause recipe")
        self.button_stop_recipe.setEnabled(self._automation_active)
        self._update_length_setup_controls()

    def _update_length_setup_controls(self) -> None:
        if self._button_length_setup_pause is not None:
            self._button_length_setup_pause.setEnabled(self._automation_active)
            self._button_length_setup_pause.setText("Resume setup" if self._automation_paused else "Pause setup")
        if self._button_length_setup_stop is not None:
            self._button_length_setup_stop.setEnabled(self._automation_active)

    def _store_resume_state(self, *, summary: str | None = None) -> None:
        if not self._automation_steps:
            return
        self._resume_recipe_state = AutomationResumeState(
            steps=list(self._automation_steps),
            index=int(self._automation_index),
            interval_ms=int(self._automation_interval_ms),
            total_steps=int(self._automation_total_steps),
            name=str(self._automation_name),
            origin_mm=float(self._recipe_origin_mm),
            summary=summary or self._last_recipe_summary,
            current_setpoint_mA=self._supply_last_setpoint_mA,
        )

    def _ask_resume_stopped_recipe(self) -> str:
        state = self._resume_recipe_state
        if state is None:
            return "start"
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setText("A recipe was stopped before it finished.")
        box.setInformativeText(
            f"Resume from saved recipe row {state.index + 1}, or start the recipe from the beginning?"
        )
        resume_button = box.addButton("Resume", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        start_button = box.addButton("Start over", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(resume_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked == resume_button:
            return "resume"
        if clicked == start_button:
            return "start"
        if clicked == cancel_button:
            return "cancel"
        return "cancel"

    def _resume_stopped_recipe(self, state: AutomationResumeState) -> None:
        if not self._preflight_recipe_hardware(state.steps):
            return
        if not self._session_active:
            self._log("Cannot resume because the previous session is no longer active. Start over instead.")
            self._resume_recipe_state = None
            return
        self._automation_steps = list(state.steps)
        self._automation_index = min(max(0, int(state.index)), len(self._automation_steps))
        self._automation_total_steps = int(state.total_steps)
        self._automation_completed_ticks = min(self._automation_index, self._automation_total_steps)
        self._automation_progress_started_s = time.monotonic()
        self._automation_progress_last_format_update_s = 0.0
        self._automation_estimated_total_s = max(
            0.0,
            float(self._automation_total_steps) * max(0.001, float(state.interval_ms) / 1000.0),
        )
        self._automation_active = True
        self._automation_paused = False
        self._automation_interval_ms = int(state.interval_ms)
        self._active_control_config = self._freeze_control_config()
        self._automation_name = str(state.name)
        self._recipe_origin_mm = float(state.origin_mm)
        self._last_recipe_summary = state.summary
        self._resume_recipe_state = None
        self._set_automation_context(phase="resume")
        if state.current_setpoint_mA is not None and self._is_current_sweep_mode(self._automation_name):
            self._set_recipe_current_mA(float(state.current_setpoint_mA))
        self._active_current_sweep_step_index = None
        self._active_current_sweep_started_s = 0.0
        self._active_current_sweep_wall_started_s = 0.0
        self._active_current_sweep_last_schedule_update_s = 0.0
        self._current_sweep_post_hold_throttle_until_s = 0.0
        self._active_current_sweep_last_setpoint_mA = None
        self._current_sweep_voltage_limited_return_steps.clear()
        self._clear_current_sweep_ramp_hold()
        self._active_mechanical_scan_step_index = None
        self._active_mechanical_scan_started_s = 0.0
        self._active_mechanical_scan_move_count = 0
        self._active_mechanical_scan_hold_started_s = None
        self._active_mechanical_scan_move_pending = False
        self._active_mechanical_scan_direction = None
        self._active_mechanical_scan_origin_position_mm = None
        self._constant_current_step_base_position_by_note.clear()
        self._constant_current_step_base_strain_by_note.clear()
        self._active_constant_current_zero_position_mm = None
        self._active_constant_current_zero_current_mA = None
        self._active_target_ramp_step_index = None
        self._active_target_ramp_started_s = 0.0
        self._active_target_ramp_start_value = None
        self._active_target_ramp_rate_value_s = None
        self._setup_zero_fallback_return_position_mm = None
        self._end_zero_fallback_armed = False
        self._end_zero_fallback_start_point_index = 0
        self._end_zero_fallback_return_position_mm = None
        self._end_zero_fallback_raw_g = None
        self._reset_timed_step_state()
        self._start_automation_control_loop(self._automation_interval_ms)
        self._log(f"Recipe resumed at saved recipe row {self._automation_index + 1}.")
        self._update_recipe_progress()
        self._update_recipe_buttons()
        self._refresh_live_labels()

    def _run_automation_control_tick(self) -> None:
        self._control_worker_thread_id = get_ident()
        with self._automation_control_lock:
            self._handle_auto_ramp_tick()

    def _handle_automation_control_loop_error(self, exc: BaseException) -> None:
        self._automation_control_error = str(exc) or exc.__class__.__name__
        self._run_on_ui_thread(lambda: self._log(f"Recipe control worker stopped: {self._automation_control_error}"))

    def _start_automation_control_loop(self, interval_ms: int) -> None:
        self._auto_ramp_timer.stop()
        if self._automation_control_loop is None:
            self._automation_control_loop = AutomationControlLoop(
                self._run_automation_control_tick,
                error_callback=self._handle_automation_control_loop_error,
            )
        self._automation_control_error = None
        self._automation_control_loop.start(interval_ms)

    def _pause_automation_control_loop(self) -> None:
        if self._automation_control_loop is not None:
            self._automation_control_loop.pause()
        self._auto_ramp_timer.stop()

    def _resume_automation_control_loop(self) -> None:
        if self._automation_control_loop is None:
            self._start_automation_control_loop(self._automation_interval_ms)
            return
        self._auto_ramp_timer.stop()
        self._automation_control_loop.resume()

    def _stop_automation_control_loop(self) -> None:
        if self._automation_control_loop is not None:
            self._automation_control_loop.stop()
        self._auto_ramp_timer.stop()

    def _start_auto_ramp(self) -> None:
        if self._automation_paused:
            self._resume_paused_recipe()
            return
        if self._automation_active:
            return
        try:
            steps, summary, interval_ms = self._build_automation_recipe()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, str(exc))
            return
        if self._resume_recipe_state is not None and self._session_active:
            if self._resume_recipe_state.summary == summary:
                resume_choice = self._ask_resume_stopped_recipe()
                if resume_choice == "cancel":
                    return
                if resume_choice == "resume":
                    self._resume_stopped_recipe(self._resume_recipe_state)
                    return
            else:
                self._log(
                    "Discarded stopped-recipe resume state because the visible recipe controls changed."
                )
            self._resume_recipe_state = None
        self._sync_stale_log_name_from_sample()
        if not self._preflight_recipe_hardware(steps, show_progress=True):
            return
        if not self._prepare_continuity_current_for_recipe(steps):
            return
        self._manual_jog_uses_last_target = False
        self._clear_run_zero_load_scale_reference()
        self._last_move_target_mm = self._current_position_mm
        self._effective_position_mm = self._current_position_mm
        self._last_effective_move_target_mm = self._effective_position_mm
        self._last_move_direction = 0.0
        self._last_motion_command_time_s = None
        self._last_motion_expected_complete_time_s = None
        self._last_commanded_speed_mm_s = 0.0
        self._active_current_sweep_step_index = None
        self._active_current_sweep_started_s = 0.0
        self._active_current_sweep_wall_started_s = 0.0
        self._active_current_sweep_last_schedule_update_s = 0.0
        self._current_sweep_post_hold_throttle_until_s = 0.0
        self._active_current_sweep_last_setpoint_mA = None
        self._current_sweep_voltage_limited_return_steps.clear()
        self._clear_current_sweep_ramp_hold()
        self._active_mechanical_scan_step_index = None
        self._active_mechanical_scan_started_s = 0.0
        self._active_mechanical_scan_move_count = 0
        self._active_mechanical_scan_hold_started_s = None
        self._active_mechanical_scan_move_pending = False
        self._active_mechanical_scan_direction = None
        self._active_mechanical_scan_origin_position_mm = None
        self._active_target_ramp_step_index = None
        self._active_target_ramp_started_s = 0.0
        self._active_target_ramp_start_value = None
        self._active_target_ramp_rate_value_s = None
        self._reset_timed_step_state()
        self._setup_measured_length_mm = None
        self._setup_starting_length_mm = None
        self._setup_preload_position_mm = None
        self._setup_preload_ramp_skipped = False
        self._setup_zero_position_mm = None
        self._setup_return_zero_start_point_index = 0
        self._setup_return_zero_speed_mm_s_value = None
        self._setup_zero_fallback_return_position_mm = None
        self._setup_zero_fallback_raw_g = None
        self._setup_zero_fallback_reason = ""
        self._end_zero_fallback_armed = False
        self._end_zero_fallback_start_point_index = 0
        self._end_zero_fallback_return_position_mm = None
        self._end_zero_fallback_raw_g = None
        self._calibration_report = None
        self._seek_last_error_by_key.clear()
        self._seek_last_value_by_key.clear()
        self._seek_last_time_by_key.clear()
        self._seek_last_filtered_value_by_key.clear()
        self._seek_out_of_band_since_by_key.clear()
        self._seek_out_of_band_sign_by_key.clear()
        self._seek_last_scale_timestamp_by_key.clear()
        self._seek_last_scale_timestamp_by_clock.clear()
        self._seek_last_effective_position_by_key.clear()
        self._seek_live_stiffness_by_key.clear()
        self._seek_live_stiffness_g_per_mm = None
        self._seek_last_stiffness_value_by_basis.clear()
        self._seek_last_stiffness_position_by_basis.clear()
        self._current_sweep_hold_response_stiffness_by_key.clear()
        self._current_sweep_hold_response_count_by_key.clear()
        self._seek_no_response_count_by_key.clear()
        self._seek_travel_by_key.clear()
        self._setup_preload_engaged_seek_keys.clear()
        if not self._session_active:
            self._start_session(enable_logging=False, record_initial_point=False)
            if not self._session_active:
                return
        self._automation_steps = steps
        self._automation_index = 0
        self._recipe_estimated_points, self._automation_total_steps = self._estimate_recipe_points_and_ticks(
            steps,
            interval_ms,
        )
        self._automation_completed_ticks = 0
        self._automation_estimated_total_s = max(
            0.0,
            float(self._automation_total_steps) * max(0.001, float(interval_ms) / 1000.0),
        )
        self._current_sweep_duration_overheads_s = []
        self._automation_active = True
        self._automation_paused = False
        self._automation_interval_ms = interval_ms
        self._active_control_config = self._freeze_control_config()
        self._recipe_origin_mm = self._current_position_mm
        self._automation_name = str(self.combo_recipe_mode.currentData() or "ramp")
        self._last_recipe_summary = summary
        self._set_automation_context(phase="start")
        if steps and steps[0].note in {"setup_start_length", "setup_preload"}:
            self._show_length_setup_dialog()
        self._start_automation_control_loop(interval_ms)
        self._log(summary)
        self._update_recipe_progress()
        self._update_recipe_buttons()
        self._refresh_live_labels()

    def _pause_recipe(self) -> None:
        if not self._automation_active or self._automation_paused:
            return
        self._automation_paused = True
        self._paused_current_setpoint_mA = self._supply_last_setpoint_mA
        self._pause_automation_control_loop()
        self._stop_tic_keepalive()
        try:
            dispatcher = self._build_tic_dispatcher()
            dispatcher.halt_and_hold()
            if not self._wait_for_tic_dispatcher(dispatcher, "halt", timeout_s=2.0):
                self._log("Pause requested a Tic halt, but the command did not finish cleanly.")
        except Exception as exc:
            self._log(f"Pause could not halt Tic: {exc}")
        self._disable_supply_output()
        self._set_automation_context(phase="paused")
        self._log("Recipe paused. Current annealing output is off.")
        self._update_recipe_buttons()
        self._refresh_live_labels()

    def _resume_paused_recipe(self) -> None:
        if not self._automation_active or not self._automation_paused:
            return
        if self._paused_current_setpoint_mA is not None and self._is_current_sweep_mode(self._automation_name):
            if not self._set_recipe_current_mA(float(self._paused_current_setpoint_mA)):
                return
        self._automation_paused = False
        self._resume_automation_control_loop()
        self._set_automation_context(phase="resume")
        self._log("Recipe resumed.")
        self._update_recipe_buttons()
        self._refresh_live_labels()

    def _toggle_recipe_pause(self) -> None:
        if self._automation_paused:
            self._resume_paused_recipe()
        else:
            self._pause_recipe()

    def _stop_recipe_from_button(self) -> None:
        self._stop_auto_ramp(log_completion=True, user_initiated=True)

    def _sync_manual_motion_base_from_current_position(self) -> None:
        try:
            self._refresh_tic_status()
        except Exception:
            pass
        self._manual_jog_uses_last_target = False
        self._manual_jog_direction = 0.0
        self._manual_jog_last_tick_s = None
        self._manual_jog_pending_mm = 0.0
        self._last_move_target_mm = self._current_position_mm
        self._effective_position_mm = self._current_position_mm
        self._last_effective_move_target_mm = self._effective_position_mm

    def _ask_recovery_after_stop(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._ask_recovery_after_stop)
            return
        if self._window_closing:
            return
        if self._tic_motor_power_ok is False:
            return
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setText("Recipe stopped.")
        box.setInformativeText("Do you want to relax the rig now?")
        return_position_button = box.addButton("Move displacement to 0", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        zero_load_button = box.addButton("Return load to 0", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        leave_button = box.addButton("Leave as is", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(leave_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked == return_position_button:
            self._start_recovery_displacement_zero()
        elif clicked == zero_load_button:
            self._start_recovery_load_zero()

    def _show_recovery_plot_dialog(self, title: str) -> None:
        if pg is None:
            return
        if self._window_closing:
            return
        self._recovery_points = []
        self._recovery_start_monotonic = time.monotonic()
        self._recovery_start_elapsed_s = 0.0
        self._recovery_last_record_scale_timestamp = None
        self._last_recovery_plot_refresh_s = None
        dialog = self._recovery_plot_dialog
        if dialog is None or dialog.isHidden():
            dialog = QtWidgets.QDialog(self)
            dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.setWindowTitle(title)
            dialog.resize(820, 520)
            layout = QtWidgets.QVBoxLayout(dialog)
            self._recovery_plot = self._create_pyqtgraph_plot(
                parent=dialog,
                title="Recovery load + displacement vs time",
                x_label="Recovery time (s)",
                left_label="Applied tensile load (g)",
                right_label="Tensile displacement (mm)",
                left_color=self._plot_channel_color("load_g"),
                right_color=self._plot_channel_color("position_mm"),
                symbols=True,
            )
            self._recovery_plot_widget = self._recovery_plot.widget
            self._recovery_left_curve = self._recovery_plot.left_curve
            self._recovery_right_curve = self._recovery_plot.right_curve
            layout.addWidget(self._recovery_plot_widget)
            self._recovery_plot_dialog = dialog
            dialog.destroyed.connect(lambda _obj=None: self._forget_recovery_plot_dialog())
        else:
            dialog.setWindowTitle(title)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._refresh_recovery_plot()

    def _forget_recovery_plot_dialog(self) -> None:
        self._recovery_plot_dialog = None
        self._recovery_plot = None
        self._recovery_plot_widget = None
        self._recovery_left_curve = None
        self._recovery_right_curve = None
        self._restore_main_window_focus_soon()

    def _show_length_setup_dialog(self) -> None:
        if self._window_closing:
            return
        dialog = self._length_setup_dialog
        title_sample = self.edit_sample_name.text().strip() or self.edit_log_name.text().strip() or "unnamed sample"
        title = f"Mini DMA Length Setup - {title_sample}"
        if dialog is None or dialog.isHidden():
            dialog = QtWidgets.QDialog(self)
            dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.setWindowTitle(title)
            dialog.resize(760, 520)
            layout = QtWidgets.QVBoxLayout(dialog)
            label = QtWidgets.QLabel("Preparing mandatory zero-load and length setup...", dialog)
            label.setWordWrap(True)
            layout.addWidget(label)
            progress = QtWidgets.QProgressBar(dialog)
            progress.setRange(0, 100)
            progress.setValue(0)
            progress.setTextVisible(True)
            progress.setFormat("Setup progress: idle")
            layout.addWidget(progress)
            if pg is not None:
                self._length_setup_stress_plot = self._create_pyqtgraph_plot(
                    parent=dialog,
                    title="Length setup load and stress",
                    x_label="Setup time (s)",
                    left_label="Stress (MPa)",
                    right_label="Load (g)",
                    left_color=self._plot_channel_color("stress_mpa"),
                    right_color=self._plot_channel_color("load_g"),
                    symbols=True,
                )
                self._length_setup_displacement_plot = self._create_pyqtgraph_plot(
                    parent=dialog,
                    title="Length setup displacement",
                    x_label="Setup time (s)",
                    left_label="Displacement (mm)",
                    right_label=None,
                    left_color=self._plot_channel_color("position_mm"),
                    symbols=True,
                )
                self._length_setup_stress_plot_widget = self._length_setup_stress_plot.widget
                self._length_setup_displacement_plot_widget = self._length_setup_displacement_plot.widget
                self._length_setup_stress_curve = self._length_setup_stress_plot.left_curve
                self._length_setup_load_curve = self._length_setup_stress_plot.right_curve
                self._length_setup_displacement_curve = self._length_setup_displacement_plot.left_curve
                layout.addWidget(self._length_setup_stress_plot_widget, stretch=1)
                layout.addWidget(self._length_setup_displacement_plot_widget, stretch=1)
            control_row = QtWidgets.QHBoxLayout()
            pause_button = QtWidgets.QPushButton("Pause setup", dialog)
            pause_button.clicked.connect(self._toggle_recipe_pause)
            pause_button.setEnabled(False)
            control_row.addWidget(pause_button)
            stop_button = QtWidgets.QPushButton("Stop setup", dialog)
            stop_button.clicked.connect(self._stop_recipe_from_button)
            stop_button.setEnabled(False)
            control_row.addWidget(stop_button)
            layout.addLayout(control_row)
            close_note = QtWidgets.QLabel("This window closes automatically when the recipe start point is ready.", dialog)
            close_note.setWordWrap(True)
            close_note.setStyleSheet("color: palette(mid);")
            layout.addWidget(close_note)
            self._length_setup_status_label = label
            self._length_setup_progress = progress
            self._button_length_setup_pause = pause_button
            self._button_length_setup_stop = stop_button
            self._length_setup_dialog = dialog
            dialog.destroyed.connect(lambda _obj=None: self._forget_length_setup_dialog())
        else:
            dialog.setWindowTitle(title)
        self._length_setup_points.clear()
        self._length_setup_last_record_scale_timestamp = None
        self._last_length_setup_plot_refresh_s = None
        self._setup_return_zero_start_point_index = 0
        self._setup_return_zero_speed_mm_s_value = None
        self._setup_zero_position_mm = None
        self._setup_zero_fallback_return_position_mm = None
        self._setup_zero_fallback_raw_g = None
        self._setup_zero_fallback_reason = ""
        self._length_setup_start_monotonic = time.monotonic()
        self._update_length_setup_dialog("Enter the measured mounted wire length before setup.")
        self._update_length_setup_progress()
        self._update_length_setup_controls()
        self._refresh_length_setup_plot()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _update_length_setup_dialog(self, message: str) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(lambda message=message: self._update_length_setup_dialog(message))
            return
        if self._length_setup_status_label is not None:
            self._length_setup_status_label.setText(message)

    def set_length_setup_automation_values(
        self,
        *,
        starting_length_mm: float | None,
        preload_length_mm: float | None,
    ) -> None:
        self._automated_setup_starting_length_mm = None if starting_length_mm is None else float(starting_length_mm)
        # Kept for older bench-plan JSON and old resume code. New setup recipes use only
        # the starting length prompt, which is now the measured mounted wire length.
        self._automated_setup_preload_length_mm = None if preload_length_mm is None else float(preload_length_mm)

    def _close_length_setup_dialog(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._close_length_setup_dialog)
            return
        dialog = self._length_setup_dialog
        self._forget_length_setup_dialog()
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()

    def _forget_length_setup_dialog(self) -> None:
        self._length_setup_dialog = None
        self._length_setup_status_label = None
        self._length_setup_progress = None
        self._button_length_setup_pause = None
        self._button_length_setup_stop = None
        self._length_setup_stress_plot = None
        self._length_setup_displacement_plot = None
        self._length_setup_stress_plot_widget = None
        self._length_setup_displacement_plot_widget = None
        self._length_setup_stress_curve = None
        self._length_setup_load_curve = None
        self._length_setup_displacement_curve = None
        self._length_setup_progress_phase_key = None
        self._length_setup_progress_fraction_floor = 0.0
        self._restore_main_window_focus_soon()

    def _close_recovery_plot_dialog(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._close_recovery_plot_dialog)
            return
        dialog = self._recovery_plot_dialog
        self._forget_recovery_plot_dialog()
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()

    def _close_transient_child_windows(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._close_transient_child_windows)
            return
        app = QtWidgets.QApplication.instance()
        active_modal = app.activeModalWidget() if app is not None else None
        if active_modal is not None and active_modal is not self:
            active_modal.close()
        self._close_length_setup_dialog()
        self._close_recovery_plot_dialog()
        for dialog_attr in ("plot_config_dialog", "_motor_step_calibration_dialog"):
            dialog = getattr(self, dialog_attr, None)
            if isinstance(dialog, QtWidgets.QDialog):
                dialog.close()

    def _record_length_setup_point(self) -> bool:
        if self._length_setup_start_monotonic <= 0.0:
            self._length_setup_start_monotonic = time.monotonic()
        elapsed_s = time.monotonic() - self._length_setup_start_monotonic
        load_g = self._current_effective_load_g()
        point = self._capture_measurement_point(
            elapsed_s=elapsed_s,
            position_mm=self._current_position_mm,
            effective_position_mm=self._effective_position_mm,
            raw_load_g=self._latest_scale_value_g,
            load_g=load_g,
        )
        self._length_setup_points.append(point)
        self._length_setup_last_record_scale_timestamp = self._latest_scale_timestamp
        self._write_setup_point(point)
        if len(self._length_setup_points) > 1000:
            self._length_setup_points = self._length_setup_points[-1000:]
        now_s = time.monotonic()
        if self._dialog_plot_refresh_due(self._last_length_setup_plot_refresh_s, now_s=now_s):
            self._last_length_setup_plot_refresh_s = now_s
            self._refresh_length_setup_plot()
        return True

    def _dialog_plot_refresh_due(self, last_refresh_s: float | None, *, now_s: float) -> bool:
        if last_refresh_s is None:
            return True
        interval_s = max(0.1, float(self._graph_refresh_interval_ms()) / 1000.0)
        return now_s - float(last_refresh_s) >= interval_s

    def _qcolor_from_rgb(self, rgb: Sequence[float]) -> QtGui.QColor:
        red, green, blue = rgb[:3]
        return QtGui.QColor.fromRgbF(float(red), float(green), float(blue))

    def _create_pyqtgraph_plot(
        self,
        *,
        parent: QtWidgets.QWidget,
        title: str,
        x_label: str,
        left_label: str,
        right_label: str | None,
        left_color: str = "#fbbf24",
        right_color: str = "#60a5fa",
        symbols: bool = True,
    ) -> PyqtGraphPlotBundle:
        if pg is None:  # pragma: no cover - guarded by caller in normal app use
            raise RuntimeError("pyqtgraph is not available")
        widget = pg.PlotWidget(parent=parent)
        widget.setMinimumWidth(0)
        widget.setMinimumHeight(0)
        widget.setMaximumHeight(16777215)
        widget.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)
        widget.setMouseEnabled(x=True, y=True)
        plot_item = widget.getPlotItem()
        plot_item.showGrid(x=True, y=True, alpha=0.28)
        plot_item.setClipToView(True)
        plot_item.vb.setDefaultPadding(0.05)
        left_curve_kwargs: dict[str, Any] = {"pen": pg.mkPen(left_color, width=0.8)}
        if symbols:
            left_curve_kwargs.update(
                {
                    "symbol": "o",
                    "symbolSize": 4,
                    "symbolBrush": left_color,
                    "symbolPen": left_color,
                }
            )
        left_curve = plot_item.plot([], [], **left_curve_kwargs)
        bundle = PyqtGraphPlotBundle(widget=widget, plot_item=plot_item, left_curve=left_curve)
        if right_label is not None:
            right_view = pg.ViewBox()
            right_view.setDefaultPadding(0.05)
            right_curve_kwargs: dict[str, Any] = {"pen": pg.mkPen(right_color, width=0.75)}
            if symbols:
                right_curve_kwargs.update(
                    {
                        "symbol": "s",
                        "symbolSize": 4,
                        "symbolBrush": right_color,
                        "symbolPen": right_color,
                    }
                )
            right_curve = pg.PlotDataItem([], [], **right_curve_kwargs)
            plot_item.showAxis("right")
            plot_item.scene().addItem(right_view)
            plot_item.getAxis("right").linkToView(right_view)
            right_view.setXLink(plot_item)
            right_view.addItem(right_curve)

            def _sync_right_view() -> None:
                right_view.setGeometry(plot_item.vb.sceneBoundingRect())
                right_view.linkedViewChanged(plot_item.vb, right_view.XAxis)

            plot_item.vb.sigResized.connect(_sync_right_view)
            _sync_right_view()
            bundle.right_view = right_view
            bundle.right_curve = right_curve
            bundle.sync_right_view = _sync_right_view
        self._style_pyqtgraph_plot(
            bundle,
            title=title,
            x_label=x_label,
            left_label=left_label,
            right_label=right_label,
            left_color=left_color,
            right_color=right_color,
        )
        return bundle

    def _style_pyqtgraph_plot(
        self,
        bundle: PyqtGraphPlotBundle,
        *,
        title: str,
        x_label: str,
        left_label: str,
        right_label: str | None,
        left_color: str = "#fbbf24",
        right_color: str = "#60a5fa",
    ) -> None:
        if pg is None:
            return
        theme = self._plot_theme()
        text_color = self._qcolor_from_rgb(theme["text_rgb"])
        background_color = self._qcolor_from_rgb(theme["axes_rgb"])
        bundle.widget.setBackground(background_color)
        bundle.plot_item.setTitle(title, color=text_color.name(), size="9pt")
        bundle.plot_item.setLabel("bottom", x_label, color=text_color.name())
        bundle.plot_item.setLabel("left", left_label, color=left_color)
        bottom_axis = bundle.plot_item.getAxis("bottom")
        bottom_axis.setPen(pg.mkPen(text_color, width=0.8))
        bottom_axis.setTextPen(pg.mkPen(text_color))
        bottom_axis.setTickPen(pg.mkPen(text_color, width=0.6))
        bottom_axis.setGrid(False)
        bottom_axis.setStyle(maxTickLevel=0, maxTextLevel=0)
        self._disable_pyqtgraph_axis_scaling(bottom_axis)
        top_axis = bundle.plot_item.getAxis("top")
        top_axis.setPen(pg.mkPen(text_color, width=0.8))
        top_axis.setTextPen(pg.mkPen(text_color))
        top_axis.setTickPen(pg.mkPen(text_color, width=0.0))
        top_axis.setGrid(False)
        top_axis.setLabel("")
        top_axis.setStyle(showValues=False, tickLength=0, maxTickLevel=0, maxTextLevel=0)
        self._disable_pyqtgraph_axis_scaling(top_axis)
        left_axis = bundle.plot_item.getAxis("left")
        left_axis.setPen(pg.mkPen(left_color, width=0.9))
        left_axis.setTextPen(pg.mkPen(left_color))
        left_axis.setTickPen(pg.mkPen(left_color, width=0.7))
        left_axis.setGrid(False)
        left_axis.setStyle(maxTickLevel=0, maxTextLevel=0)
        self._disable_pyqtgraph_axis_scaling(left_axis)
        right_axis = bundle.plot_item.getAxis("right")
        right_axis.setPen(pg.mkPen(right_color, width=0.9))
        right_axis.setTextPen(pg.mkPen(right_color))
        right_axis.setTickPen(pg.mkPen(right_color, width=0.7))
        right_axis.setGrid(False)
        right_axis.setStyle(maxTickLevel=0, maxTextLevel=0)
        self._disable_pyqtgraph_axis_scaling(right_axis)
        bundle.plot_item.getAxis("bottom").setStyle(tickTextOffset=4)
        bundle.plot_item.getAxis("left").setStyle(tickTextOffset=4)
        bundle.plot_item.showAxis("top")
        bundle.plot_item.showGrid(x=False, y=False)
        if right_label is None:
            bundle.plot_item.showAxis("right")
            right_axis.setPen(pg.mkPen(text_color, width=0.8))
            right_axis.setTextPen(pg.mkPen(text_color))
            right_axis.setTickPen(pg.mkPen(text_color, width=0.0))
            right_axis.setLabel("")
            right_axis.setStyle(showValues=False, tickLength=0, maxTickLevel=0, maxTextLevel=0)
        else:
            bundle.plot_item.showAxis("right")
            bundle.plot_item.setLabel("right", right_label, color=right_color)
            right_axis.setStyle(showValues=True, tickLength=-5, maxTickLevel=0, maxTextLevel=0)

    def _set_pyqtgraph_curve_data(
        self,
        curve: Any | None,
        x_values: Sequence[float],
        y_values: Sequence[float],
    ) -> None:
        if curve is not None:
            curve.setData(list(x_values), list(y_values), connect="finite")

    def _disable_pyqtgraph_axis_scaling(self, axis: Any) -> None:
        try:
            axis.enableAutoSIPrefix(False)
        except Exception:
            pass
        try:
            axis.setScale(1.0)
        except Exception:
            pass
        if hasattr(axis, "autoSIPrefix"):
            axis.autoSIPrefix = False
        if hasattr(axis, "autoSIPrefixScale"):
            axis.autoSIPrefixScale = 1.0
        if hasattr(axis, "labelUnitPrefix"):
            axis.labelUnitPrefix = ""

    def _set_pyqtgraph_curve_style(
        self,
        curve: Any | None,
        color: str,
        *,
        width: float,
        symbol: str | None,
    ) -> None:
        if curve is None or pg is None:
            return
        curve.setPen(pg.mkPen(color, width=width))
        curve.setSymbol(symbol)
        if symbol is not None:
            curve.setSymbolSize(4)
            curve.setSymbolBrush(color)
            curve.setSymbolPen(color)

    def _plot_xy_values(
        self,
        points: Sequence[MeasurementPoint],
        x_channel: PlotChannel,
        y_channel: PlotChannel,
    ) -> tuple[list[float], list[float]]:
        x_values: list[float] = []
        y_values: list[float] = []
        previous_elapsed_s: float | None = None
        for point in points:
            x_value = x_channel.getter(point)
            y_value = y_channel.getter(point)
            if x_value is None or y_value is None:
                continue
            elapsed_s = float(point.elapsed_s)
            if (
                point.plot_gap_before
                or
                previous_elapsed_s is not None
                and elapsed_s - previous_elapsed_s > DISPLAY_PLOT_BREAK_GAP_S
            ):
                x_values.append(float("nan"))
                y_values.append(float("nan"))
            x_values.append(float(x_value))
            y_values.append(float(y_value))
            previous_elapsed_s = elapsed_s
        return x_values, y_values

    def _refresh_length_setup_plot(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._refresh_length_setup_plot)
            return
        if self._length_setup_stress_plot is None or self._length_setup_displacement_plot is None:
            return
        points = tuple(self._length_setup_points)
        self._style_pyqtgraph_plot(
            self._length_setup_stress_plot,
            title="Length setup load and stress",
            x_label="Setup time (s)",
            left_label="Stress (MPa)",
            right_label="Load (g)",
            left_color=self._plot_channel_color("stress_mpa"),
            right_color=self._plot_channel_color("load_g"),
        )
        self._style_pyqtgraph_plot(
            self._length_setup_displacement_plot,
            title="Length setup displacement",
            x_label="Setup time (s)",
            left_label="Displacement (mm)",
            right_label=None,
            left_color=self._plot_channel_color("position_mm"),
        )
        x_values = [point.elapsed_s for point in points]
        stress_values = [
            float("nan") if point.stress_mpa is None else float(point.stress_mpa)
            for point in points
        ]
        load_values = [float(point.load_g) for point in points]
        displacement_values = [float(point.position_mm) for point in points]
        self._set_pyqtgraph_curve_data(self._length_setup_stress_curve, x_values, stress_values)
        self._set_pyqtgraph_curve_data(self._length_setup_load_curve, x_values, load_values)
        self._set_pyqtgraph_curve_data(
            self._length_setup_displacement_curve,
            x_values,
            displacement_values,
        )
        if self._length_setup_stress_plot.sync_right_view is not None:
            self._length_setup_stress_plot.sync_right_view()
        if self._length_setup_stress_plot.right_view is not None:
            self._length_setup_stress_plot.right_view.enableAutoRange()

    def _refresh_recovery_plot(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._refresh_recovery_plot)
            return
        if self._recovery_plot_dialog is None or self._recovery_plot_dialog.isHidden():
            return
        if self._recovery_plot is None:
            return
        self._style_pyqtgraph_plot(
            self._recovery_plot,
            title="Recovery load + displacement vs time",
            x_label="Recovery time (s)",
            left_label="Applied tensile load (g)",
            right_label="Tensile displacement (mm)",
            left_color=self._plot_channel_color("load_g"),
            right_color=self._plot_channel_color("position_mm"),
        )
        points = self._recovery_points
        x_values = [point.elapsed_s for point in points]
        self._set_pyqtgraph_curve_data(
            self._recovery_left_curve,
            x_values,
            [float(point.load_g) for point in points],
        )
        self._set_pyqtgraph_curve_data(
            self._recovery_right_curve,
            x_values,
            [float(point.position_mm) for point in points],
        )
        if self._recovery_plot.sync_right_view is not None:
            self._recovery_plot.sync_right_view()
        if self._recovery_plot.right_view is not None:
            self._recovery_plot.right_view.enableAutoRange()

    def _start_recovery_position_target(self, target_mm: float, label: str) -> None:
        distance_mm = abs(target_mm - self._current_position_mm)
        return_duration_s = self._pending_recovery_return_duration_s or self._setup_return_duration_s()
        self._pending_recovery_return_duration_s = None
        speed_mm_s = self._setup_return_speed_for_distance_mm_s(
            distance_mm,
            duration_s=return_duration_s,
        )
        interval_ms = self._control_interval_ms()
        move_duration_s = self._move_duration_s(distance_mm, speed_mm_s)
        steps = [AutomationStep("move", target_mm=target_mm, duration_s=move_duration_s, note=label)]
        steps.append(AutomationStep("settle", duration_s=max(0.1, interval_ms / 500.0), note=label))
        steps.append(AutomationStep("record", note=label))
        if not self._preflight_recipe_hardware(steps):
            return
        self._show_recovery_plot_dialog(f"Mini DMA Recovery: {label}")
        self._automation_steps = steps
        self._automation_index = 0
        _, tick_count = self._estimate_recipe_points_and_ticks(steps, interval_ms)
        self._automation_total_steps = tick_count
        self._automation_completed_ticks = 0
        self._automation_progress_started_s = time.monotonic()
        self._automation_progress_last_format_update_s = 0.0
        self._automation_active = True
        self._automation_paused = False
        self._automation_interval_ms = interval_ms
        self._active_control_config = self._freeze_control_config()
        self._automation_name = RECOVERY_POSITION
        self._set_automation_context(phase="recover")
        self._start_automation_control_loop(self._automation_interval_ms)
        self._apply_ui_refresh_interval()
        self._ui_refresh_timer.start()
        self._log(f"Started displacement recovery: {label}.")
        self._update_recipe_buttons()
        self._update_recipe_progress()
        self._refresh_live_labels()

    def _start_recovery_position_origin(self) -> None:
        self._start_recovery_position_target(self._recipe_origin_mm, "displacement to recipe start")

    def _start_recovery_displacement_zero(self) -> None:
        self._start_recovery_position_target(self._position_reference_mm, "displacement to 0")

    def _start_recovery_load_zero(self) -> None:
        self._sync_manual_motion_base_from_current_position()
        steps = [
            AutomationStep(
                "seek_target",
                target_value=0.0,
                basis=HSW_BASIS_LOAD_G,
                note="0",
            ),
            AutomationStep("record", target_value=0.0, basis=HSW_BASIS_LOAD_G, note="0"),
        ]
        if not self._preflight_recipe_hardware(steps):
            return
        self._show_recovery_plot_dialog("Mini DMA Recovery: load to zero")
        self._automation_steps = steps
        self._automation_index = 0
        self._automation_interval_ms = self._control_interval_ms()
        _, tick_count = self._estimate_recipe_points_and_ticks(steps, self._automation_interval_ms)
        self._automation_total_steps = tick_count
        self._automation_completed_ticks = 0
        self._automation_progress_started_s = time.monotonic()
        self._automation_progress_last_format_update_s = 0.0
        self._automation_active = True
        self._automation_paused = False
        self._active_control_config = self._freeze_control_config()
        self._automation_name = RECOVERY_LOAD
        self._end_zero_fallback_armed = True
        self._end_zero_fallback_start_point_index = len(self._recovery_points)
        self._end_zero_fallback_return_position_mm = None
        self._end_zero_fallback_raw_g = None
        self._set_automation_context(phase="recover", basis=HSW_BASIS_LOAD_G, target_value=0.0, plateau_index=0)
        self._start_automation_control_loop(self._automation_interval_ms)
        self._apply_ui_refresh_interval()
        self._ui_refresh_timer.start()
        self._log("Started load-zero recovery.")
        self._update_recipe_buttons()
        self._update_recipe_progress()
        self._refresh_live_labels()

    def _bench_latest_stress_mpa(self) -> float | None:
        for points in (self._live_plot_points, self._session_points):
            for point in reversed(points):
                if point.stress_mpa is not None:
                    return float(point.stress_mpa)
        effective_load = self._current_effective_load_g()
        return stress_mpa_from_load_g(effective_load, float(self.spin_diameter.value()))

    def start_bench_stress_recovery(self, target_stress_mpa: float, *, reason: str) -> bool:
        self._sync_manual_motion_base_from_current_position()
        target = max(0.0, float(target_stress_mpa))
        steps = [
            AutomationStep(
                "seek_target",
                target_value=target,
                basis=HSW_BASIS_STRESS_MPA,
                note="bench_guard_recovery",
            ),
            AutomationStep(
                "record",
                target_value=target,
                basis=HSW_BASIS_STRESS_MPA,
                note="bench_guard_recovery",
            ),
        ]
        if not self._preflight_recipe_hardware(steps):
            return False
        self._show_recovery_plot_dialog("Mini DMA Recovery: bench stress guard")
        self._automation_steps = steps
        self._automation_index = 0
        self._automation_interval_ms = self._control_interval_ms()
        _, tick_count = self._estimate_recipe_points_and_ticks(steps, self._automation_interval_ms)
        self._automation_total_steps = tick_count
        self._automation_completed_ticks = 0
        self._automation_progress_started_s = time.monotonic()
        self._automation_progress_last_format_update_s = 0.0
        self._automation_active = True
        self._automation_paused = False
        self._active_control_config = self._freeze_control_config()
        self._automation_name = RECOVERY_LOAD
        self._set_automation_context(
            phase="recover",
            basis=HSW_BASIS_STRESS_MPA,
            target_value=target,
            plateau_index=0,
        )
        self._start_automation_control_loop(self._automation_interval_ms)
        self._apply_ui_refresh_interval()
        self._ui_refresh_timer.start()
        self._log(
            f"Bench high-stress guard triggered ({reason}); current output disabled and "
            f"stress recovery toward {_format_compact_unit(target, 'MPa')} started."
        )
        self._update_recipe_buttons()
        self._update_recipe_progress()
        self._refresh_live_labels()
        return True

    def _stop_auto_ramp(
        self,
        *,
        log_completion: bool = True,
        keep_progress: bool = False,
        user_initiated: bool = False,
        offer_recovery: bool = False,
        stop_reason: str | None = None,
        stop_detail: str | None = None,
    ) -> None:
        if not self._automation_active:
            return
        if stop_reason is not None:
            self._mark_session_stop_reason(stop_reason, detail=stop_detail, force=stop_reason != "app_closed")
        elif user_initiated:
            self._mark_session_stop_reason(
                "manual_recipe_stop",
                detail="Recipe stop was requested by the operator.",
            )
        elif offer_recovery:
            self._mark_session_stop_reason(
                "recipe_control_stop",
                detail="Recipe stopped before completion and recovery was offered.",
            )
        should_store_resume = user_initiated and self._automation_index < len(self._automation_steps)
        if should_store_resume:
            self._store_resume_state()
        self._automation_active = False
        self._automation_paused = False
        self._active_control_config = None
        self._automation_steps = []
        self._automation_index = 0
        if not keep_progress:
            self._automation_completed_ticks = 0
            self._automation_progress_started_s = 0.0
            self._automation_progress_last_format_update_s = 0.0
        self._seek_last_error_by_key.clear()
        self._seek_last_value_by_key.clear()
        self._seek_last_time_by_key.clear()
        self._seek_last_filtered_value_by_key.clear()
        self._seek_out_of_band_since_by_key.clear()
        self._seek_out_of_band_sign_by_key.clear()
        self._seek_last_scale_timestamp_by_key.clear()
        self._seek_last_scale_timestamp_by_clock.clear()
        self._seek_last_effective_position_by_key.clear()
        self._seek_live_stiffness_by_key.clear()
        self._seek_last_stiffness_value_by_basis.clear()
        self._seek_last_stiffness_position_by_basis.clear()
        self._current_sweep_hold_response_stiffness_by_key.clear()
        self._current_sweep_hold_response_count_by_key.clear()
        self._seek_no_response_count_by_key.clear()
        self._seek_travel_by_key.clear()
        self._setup_preload_engaged_seek_keys.clear()
        self._setup_preload_ramp_skipped = False
        self._active_current_sweep_step_index = None
        self._active_current_sweep_started_s = 0.0
        self._active_current_sweep_wall_started_s = 0.0
        self._active_current_sweep_last_schedule_update_s = 0.0
        self._current_sweep_post_hold_throttle_until_s = 0.0
        self._active_current_sweep_last_setpoint_mA = None
        self._current_sweep_voltage_limited_return_steps.clear()
        self._clear_current_sweep_ramp_hold()
        self._active_target_ramp_step_index = None
        self._active_target_ramp_started_s = 0.0
        self._active_target_ramp_start_value = None
        self._active_target_ramp_rate_value_s = None
        self._active_mechanical_scan_step_index = None
        self._active_mechanical_scan_started_s = 0.0
        self._active_mechanical_scan_move_count = 0
        self._active_mechanical_scan_hold_started_s = None
        self._active_mechanical_scan_move_pending = False
        self._active_mechanical_scan_direction = None
        self._active_mechanical_scan_origin_position_mm = None
        self._constant_current_step_base_position_by_note.clear()
        self._constant_current_step_base_strain_by_note.clear()
        self._active_constant_current_zero_position_mm = None
        self._active_constant_current_zero_current_mA = None
        self._reset_timed_step_state()
        self._stop_automation_control_loop()
        self._stop_tic_keepalive()
        if self._is_ui_thread():
            self._sync_manual_motion_base_from_current_position()
        else:
            self._run_on_ui_thread(self._sync_manual_motion_base_from_current_position)
        self._set_automation_context(phase="idle")
        if self._supply_output_enabled:
            self._disable_supply_output()
        if log_completion:
            self._log("Recipe stopped.")
        self._close_length_setup_dialog()
        if user_initiated and self._session_active:
            self._stop_session()
        if not keep_progress:
            self._update_recipe_progress()
        self._update_recipe_buttons()
        self._refresh_live_labels()
        if user_initiated or offer_recovery:
            self._ask_recovery_after_stop()

    def _build_segment_targets(
        self,
        start_offset_mm: float,
        end_offset_mm: float,
        step_mm: float,
    ) -> list[float]:
        if step_mm <= 0.0:
            raise ValueError("Step size must be greater than zero.")
        delta_mm = end_offset_mm - start_offset_mm
        if delta_mm == 0.0:
            return []
        sign = 1.0 if delta_mm >= 0.0 else -1.0
        count = max(1, int(math.ceil(abs(delta_mm) / step_mm)))
        return [
            self._recipe_origin_mm
            + start_offset_mm
            + sign * min(index * step_mm, abs(delta_mm))
            for index in range(1, count + 1)
        ]

    def _build_segment_offsets(
        self,
        start_offset_mm: float,
        end_offset_mm: float,
        step_mm: float,
    ) -> list[float]:
        if step_mm <= 0.0:
            raise ValueError("Step size must be greater than zero.")
        delta_mm = end_offset_mm - start_offset_mm
        if delta_mm == 0.0:
            return []
        sign = 1.0 if delta_mm >= 0.0 else -1.0
        count = max(1, int(math.ceil(abs(delta_mm) / step_mm)))
        return [
            start_offset_mm + sign * min(index * step_mm, abs(delta_mm))
            for index in range(1, count + 1)
        ]

    def _build_pre_measurement_setup_steps(self) -> list[AutomationStep]:
        preload_stress_mpa = float(self.spin_setup_preload_stress_mpa.value())
        preload_duration_s = float(self.spin_setup_preload_duration_s.value())
        preload_ramp_rate_mpa_s = self._setup_preload_ramp_rate_mpa_s()
        if preload_stress_mpa <= 0.0:
            raise ValueError("Set a positive setup preload stress.")
        if preload_duration_s <= 0.0:
            raise ValueError("Set a positive setup preload duration.")
        preload_load_g = load_g_from_stress_mpa(preload_stress_mpa, float(self.spin_diameter.value()))
        if preload_load_g is None:
            raise ValueError("Set a positive wire diameter before using preload length setup.")
        preload_stable_s = max(0.0, float(self.spin_setup_preload_stable_s.value()))
        steps: list[AutomationStep] = [
            AutomationStep("starting_length_prompt", note="setup_start_length"),
            AutomationStep(
                "ramp_target",
                target_value=preload_stress_mpa,
                target_start_value=None,
                target_end_value=preload_stress_mpa,
                target_ramp_rate_value_s=preload_ramp_rate_mpa_s,
                basis=HSW_BASIS_STRESS_MPA,
                note="setup_preload",
            )
        ]
        steps.append(
            AutomationStep(
                "settle",
                target_value=preload_stress_mpa,
                basis=HSW_BASIS_STRESS_MPA,
                duration_s=preload_stable_s,
                note="setup_preload",
            )
        )
        steps.append(
            AutomationStep(
                "mark_setup_return_zero",
                target_value=preload_stress_mpa,
                basis=HSW_BASIS_STRESS_MPA,
                note="setup_return_zero_start",
            )
        )
        steps.append(
            AutomationStep(
                "seek_target",
                target_value=0.0,
                basis=HSW_BASIS_LOAD_G,
                note="setup_return_zero",
            )
        )
        steps.append(AutomationStep("apply_length_setup", note="setup_apply_l0"))
        steps.append(AutomationStep("start_session", note="recipe_start"))
        return steps

    def _prepend_length_setup_steps(
        self,
        steps: list[AutomationStep],
    ) -> list[AutomationStep]:
        if not self._pre_measurement_setup_enabled():
            return list(steps)
        return self._build_pre_measurement_setup_steps() + list(steps)

    def _recipe_setup_summary_sentence(self) -> str:
        if not self._pre_measurement_setup_enabled():
            return " Setup disabled."
        setup_load_g = load_g_from_stress_mpa(
            float(self.spin_setup_preload_stress_mpa.value()),
            float(self.spin_diameter.value()),
        )
        load_text = "-" if setup_load_g is None else f" (~{_format_compact_unit(setup_load_g, 'g', decimals=3)})"
        return (
            " Includes length setup: "
            f"{_format_compact_unit(self.spin_setup_preload_stress_mpa.value(), 'MPa', decimals=3)}"
            f"{load_text} -> 0 g."
        )

    def _build_automation_recipe(self) -> tuple[list[AutomationStep], str, int]:
        mode = str(self.combo_recipe_mode.currentData() or "ramp")
        self._recipe_origin_mm = self._current_position_mm
        control_interval_ms = self._control_interval_ms()
        log_interval_ms = self._log_interval_ms()
        record_spacing_s = max(control_interval_ms, log_interval_ms) / 1000.0
        clock_summary = self._control_summary_text()

        if mode == "cycle":
            amplitude = float(self.spin_cycle_amplitude.value())
            step_mm = abs(float(self.spin_cycle_step.value()))
            cycles = int(self.spin_cycle_count.value())
            speed_mm_s = float(self.spin_cycle_speed_mm_s.value())
            if amplitude == 0.0:
                raise ValueError("Set a non-zero cycle amplitude.")
            up_targets = self._build_segment_offsets(0.0, amplitude, step_mm)
            down_targets = self._build_segment_offsets(amplitude, 0.0, step_mm)
            steps: list[AutomationStep] = []
            for _ in range(cycles):
                previous = 0.0
                for target in up_targets:
                    steps.append(
                        AutomationStep(
                            "move",
                            relative_mm=target,
                            duration_s=self._move_duration_s(target - previous, speed_mm_s),
                        )
                    )
                    previous = target
                for target in down_targets:
                    steps.append(
                        AutomationStep(
                            "move",
                            relative_mm=target,
                            duration_s=self._move_duration_s(target - previous, speed_mm_s),
                        )
                    )
                    previous = target
            steps = self._append_return_to_origin(steps)
            steps = self._prepend_length_setup_steps(steps)
            summary = (
                f"Started cyclic displacement recipe: {cycles} cycle(s), amplitude {amplitude:.4f} mm, "
                f"step {step_mm:.4f} mm at {speed_mm_s:.4f} mm/s; {clock_summary}."
                f"{self._recipe_setup_summary_sentence()}"
            )
            return steps, summary, control_interval_ms

        if mode == "hold":
            target_offset = float(self.spin_hold_target.value())
            duration_s = float(self.spin_hold_duration_s.value())
            speed_mm_s = float(self.spin_hold_speed_mm_s.value())
            if duration_s <= 0.0:
                raise ValueError("Hold duration must be greater than zero.")
            steps = [
                AutomationStep(
                    "move",
                    relative_mm=target_offset,
                    duration_s=self._move_duration_s(target_offset, speed_mm_s),
                ),
                AutomationStep("settle", duration_s=duration_s, note="hold"),
            ]
            steps = self._append_return_to_origin(steps)
            steps = self._prepend_length_setup_steps(steps)
            summary = (
                f"Started displacement-hold recipe: target offset {target_offset:.4f} mm for "
                f"{duration_s:.1f} s at {speed_mm_s:.4f} mm/s; {clock_summary}."
                f"{self._recipe_setup_summary_sentence()}"
            )
            return steps, summary, control_interval_ms

        if mode == "distribution":
            basis = self._distribution_basis()
            start_value = float(self.spin_distribution_start.value())
            end_value = float(self.spin_distribution_end.value())
            step_value = abs(float(self.spin_distribution_step.value()))
            points_per_plateau = int(self.spin_distribution_points.value())
            settle_s = float(self.spin_distribution_settle_s.value())
            if points_per_plateau <= 0:
                raise ValueError("Set at least one point per Hsw plateau.")
            targets = self._build_distribution_targets(
                start_value,
                end_value,
                step_value,
                include_return=self.check_distribution_return_sweep.isChecked(),
            )
            steps = []
            for plateau_index, target in enumerate(targets, start=1):
                steps.append(
                    AutomationStep(
                        "seek_target",
                        target_value=target,
                        basis=basis,
                        note=str(plateau_index),
                    )
                )
                steps.append(
                    AutomationStep(
                        "settle",
                        target_value=target,
                        basis=basis,
                        duration_s=settle_s,
                        note=str(plateau_index),
                    )
                )
                steps.extend(
                    AutomationStep(
                        "record",
                        target_value=target,
                        basis=basis,
                        duration_s=record_spacing_s,
                        note=str(plateau_index),
                    )
                    for _ in range(points_per_plateau)
                )
            steps = self._append_return_to_origin(steps)
            steps = self._prepend_length_setup_steps(steps)
            suffix, _ = self._distribution_units(basis)
            summary = (
                f"Started Hsw plateau scan: {start_value:.4f}{suffix} to {end_value:.4f}{suffix}, "
                f"step {step_value:.4f}{suffix}, {points_per_plateau} point(s) per plateau, "
                f"settle {settle_s:.2f} s; {clock_summary}.{self._recipe_setup_summary_sentence()}"
            )
            return steps, summary, control_interval_ms

        if self._is_calibration_mode(mode):
            start_load_g = float(self.spin_calibration_start_load_g.value())
            end_load_g = float(self.spin_calibration_end_load_g.value())
            load_step_g = abs(float(self.spin_calibration_load_step_g.value()))
            move_step_mm = abs(float(self.spin_calibration_move_step_mm.value()))
            steps_per_direction = int(self.spin_calibration_steps_per_direction.value())
            baseline_s = float(self.spin_calibration_baseline_s.value())
            settle_s = float(self.spin_calibration_settle_s.value())
            calibration_speed_mm_s = float(self.spin_calibration_speed_mm_s.value())
            if start_load_g <= 0.0 or end_load_g <= 0.0:
                raise ValueError("Calibration preload targets must be greater than zero.")
            if load_step_g <= 0.0:
                raise ValueError("Set a non-zero calibration preload step.")
            if move_step_mm <= 0.0:
                raise ValueError("Set a non-zero calibration move step.")
            if steps_per_direction <= 0:
                raise ValueError("Set at least one calibration step per direction.")
            preload_targets = self._build_numeric_targets(start_load_g, end_load_g, load_step_g)
            baseline_count = max(1, int(math.ceil(baseline_s / max(record_spacing_s, 1e-9))))
            steps = self._build_pre_measurement_setup_steps() if self._pre_measurement_setup_enabled(mode) else []
            steps.extend(
                AutomationStep(
                    "calibration_record",
                    basis=HSW_BASIS_LOAD_G,
                    target_value=0.0,
                    duration_s=record_spacing_s,
                    note=CALIBRATION_BASELINE,
                )
                for _ in range(baseline_count)
            )
            for plateau_index, preload_g in enumerate(preload_targets, start=1):
                steps.append(
                    AutomationStep(
                        "seek_target",
                        target_value=preload_g,
                        basis=HSW_BASIS_LOAD_G,
                        note=str(plateau_index),
                    )
                )
                steps.append(
                    AutomationStep(
                        "settle",
                        target_value=preload_g,
                        basis=HSW_BASIS_LOAD_G,
                        duration_s=settle_s,
                        note=str(plateau_index),
                    )
                )
                steps.append(
                    AutomationStep(
                        "calibration_record",
                        target_value=preload_g,
                        basis=HSW_BASIS_LOAD_G,
                        note=CALIBRATION_PRELOAD,
                    )
                )
                for _ in range(steps_per_direction):
                    steps.append(
                        AutomationStep(
                            "calibration_move",
                            relative_mm=move_step_mm * self._tension_motion_sign(),
                            target_value=preload_g,
                            basis=HSW_BASIS_LOAD_G,
                            duration_s=self._move_duration_s(move_step_mm, calibration_speed_mm_s),
                            note=CALIBRATION_FORWARD,
                        )
                    )
                    steps.append(
                        AutomationStep(
                            "calibration_record",
                            target_value=preload_g,
                            basis=HSW_BASIS_LOAD_G,
                            note=CALIBRATION_FORWARD,
                        )
                    )
                for _ in range(steps_per_direction):
                    steps.append(
                        AutomationStep(
                            "calibration_move",
                            relative_mm=-move_step_mm * self._tension_motion_sign(),
                            target_value=preload_g,
                            basis=HSW_BASIS_LOAD_G,
                            duration_s=self._move_duration_s(move_step_mm, calibration_speed_mm_s),
                            note=CALIBRATION_REVERSE,
                        )
                    )
                    steps.append(
                        AutomationStep(
                            "calibration_record",
                            target_value=preload_g,
                            basis=HSW_BASIS_LOAD_G,
                            note=CALIBRATION_REVERSE,
                        )
                    )
            steps = self._append_return_to_origin(steps)
            summary = (
                "Started calibration: "
                f"baseline {baseline_s:.1f} s, preload {start_load_g:.4f} to {end_load_g:.4f} g "
                f"in {load_step_g:.4f} g steps, {steps_per_direction} forward/reverse "
                f"{move_step_mm:.4f} mm move(s) per preload; {clock_summary}."
            )
            summary += self._recipe_setup_summary_sentence()
            return steps, summary, control_interval_ms

        if self._is_constant_current_strain_sweep_mode(mode):
            basis = self._constant_current_start_basis()
            start_target = float(self.spin_constant_current_start_target.value())
            end_target = float(self.spin_constant_current_end_target.value())
            mechanical_step_basis = self._constant_current_step_basis()
            mechanical_step_value = abs(float(self.spin_constant_current_step_size.value()))
            mechanical_step_speed_mm_s = float(self.spin_constant_current_move_speed_mm_s.value())
            hold_s = float(self.spin_constant_current_hold_s.value())
            current_start = float(self.spin_constant_current_start_mA.value())
            current_end = float(self.spin_constant_current_end_mA.value())
            current_step = abs(float(self.spin_constant_current_step_mA.value()))
            if mechanical_step_value <= 0.0:
                raise ValueError("Set a non-zero mechanical step size.")
            if current_step <= 0.0:
                raise ValueError("Set a non-zero current step.")
            current_targets = []
            for current_target in self._build_numeric_targets(current_start, current_end, current_step):
                clamped_target = self._recipe_current_setpoint_mA(current_target)
                if not current_targets or abs(clamped_target - current_targets[-1]) > 1e-12:
                    current_targets.append(clamped_target)
            steps = self._build_pre_measurement_setup_steps() if self._pre_measurement_setup_enabled(mode) else []
            for current_index, current_mA in enumerate(current_targets, start=1):
                note_prefix = f"{current_index}"
                steps.append(
                    AutomationStep(
                        "set_current",
                        target_value=start_target,
                        basis=basis,
                        current_mA=current_mA,
                        note=f"{note_prefix}:current",
                    )
                )
                steps.append(
                    AutomationStep(
                        "seek_target",
                        target_value=start_target,
                        basis=basis,
                        current_mA=current_mA,
                        note=f"{note_prefix}:start",
                    )
                )
                steps.append(
                    AutomationStep(
                        "mark_current_zero",
                        target_value=start_target,
                        basis=basis,
                        current_mA=current_mA,
                        note=f"{note_prefix}:zero",
                    )
                )
                steps.append(
                    AutomationStep(
                        "mechanical_scan",
                        target_value=end_target,
                        basis=basis,
                        current_mA=current_mA,
                        mechanical_step_basis=mechanical_step_basis,
                        mechanical_step_value=mechanical_step_value,
                        mechanical_step_speed_mm_s=mechanical_step_speed_mm_s,
                        duration_s=hold_s,
                        note=f"{note_prefix}:up",
                    )
                )
                if self.check_constant_current_return_to_start.isChecked():
                    steps.append(
                        AutomationStep(
                            "mechanical_scan",
                            target_value=start_target,
                            basis=basis,
                            current_mA=current_mA,
                            mechanical_step_basis=mechanical_step_basis,
                            mechanical_step_value=mechanical_step_value,
                            mechanical_step_speed_mm_s=mechanical_step_speed_mm_s,
                            duration_s=hold_s,
                            note=f"{note_prefix}:down",
                        )
                    )
            steps = self._append_return_to_origin(steps)
            suffix, _ = self._distribution_units(basis)
            step_unit = "%" if mechanical_step_basis == HSW_BASIS_STRAIN_PCT else "mm"
            summary = (
                "Started constant-current stress-strain recipe: "
                f"current {current_start:.2f} to {current_end:.2f} mA in {current_step:.2f} mA steps, "
                f"{HSW_BASIS_LABELS.get(basis, basis)} {start_target:.4f}{suffix} to {end_target:.4f}{suffix}, "
                f"fixed mechanical step {mechanical_step_value:.4f} {step_unit}, "
                f"hold/log {hold_s:.2f} s after each step; {clock_summary}. "
                "No closed-loop corrections are applied during the linear displacement legs."
            )
            if self.check_constant_current_return_to_start.isChecked():
                summary += " Each current leg steps back to the start target."
            summary += self._recipe_setup_summary_sentence()
            return steps, summary, control_interval_ms

        if self._is_current_sweep_mode(mode):
            basis = self._current_sweep_basis()
            target_start = float(self.spin_current_sweep_target_start.value())
            target_end = float(self.spin_current_sweep_target_end.value())
            target_step = abs(float(self.spin_current_sweep_target_step.value()))
            target_ramp_rate = abs(float(self.spin_current_sweep_target_ramp_rate.value()))
            current_start = self._recipe_current_setpoint_mA(float(self.spin_current_sweep_start_mA.value()))
            current_end = self._recipe_current_setpoint_mA(float(self.spin_current_sweep_end_mA.value()))
            current_ramp_rate = abs(float(self.spin_current_sweep_step_mA.value()))
            current_hold_enabled = self.check_current_sweep_hold_on_error.isChecked()
            first_overheating_enabled = self.check_current_sweep_first_overheating.isChecked()
            first_overheating_target_mpa = float(self.spin_current_sweep_first_overheating_target_mpa.value())
            current_hold_pause_factor = float(self.spin_current_sweep_hold_pause_factor.value())
            current_hold_resume_factor = min(
                current_hold_pause_factor,
                float(self.spin_current_sweep_hold_resume_factor.value()),
            )
            current_hold_resume_stable_s = float(self.spin_current_sweep_hold_resume_stable_s.value())
            if target_ramp_rate <= 0.0:
                raise ValueError("Set a non-zero target ramp rate.")
            targets = self._build_numeric_targets(target_start, target_end, target_step)
            steps = self._build_pre_measurement_setup_steps() if self._pre_measurement_setup_enabled(mode) else []
            previous_target: float | None = 0.0

            def _append_current_sweep_plateau(*, target: float, plateau_basis: str, note: str) -> None:
                sweep_ranges = [(current_start, current_end)]
                if abs(current_end - current_start) > 1e-12:
                    sweep_ranges.append((current_end, current_start))
                for sweep_start_mA, sweep_end_mA in sweep_ranges:
                    steps.append(
                        AutomationStep(
                            "sweep_current",
                            target_value=target,
                            basis=plateau_basis,
                            current_start_mA=sweep_start_mA,
                            current_end_mA=sweep_end_mA,
                            current_ramp_rate_mA_s=current_ramp_rate,
                            current_hold_enabled=current_hold_enabled,
                            current_hold_pause_tolerance_factor=current_hold_pause_factor,
                            current_hold_resume_tolerance_factor=current_hold_resume_factor,
                            current_hold_resume_stable_s=current_hold_resume_stable_s,
                            note=note,
                        )
                    )

            if first_overheating_enabled:
                steps.append(
                    AutomationStep(
                        "set_current",
                        target_value=first_overheating_target_mpa,
                        basis=HSW_BASIS_STRESS_MPA,
                        current_mA=current_start,
                        note="first_overheating",
                    )
                )
                steps.append(
                    AutomationStep(
                        "ramp_target",
                        target_value=first_overheating_target_mpa,
                        target_start_value=previous_target,
                        target_end_value=first_overheating_target_mpa,
                        target_ramp_rate_value_s=target_ramp_rate,
                        basis=HSW_BASIS_STRESS_MPA,
                        note="first_overheating",
                    )
                )
                _append_current_sweep_plateau(
                    target=first_overheating_target_mpa,
                    plateau_basis=HSW_BASIS_STRESS_MPA,
                    note="first_overheating",
                )
                if basis == HSW_BASIS_STRESS_MPA:
                    previous_target = first_overheating_target_mpa

            for plateau_index, target in enumerate(targets, start=1):
                steps.append(
                    AutomationStep(
                        "set_current",
                        target_value=target,
                        basis=basis,
                        current_mA=current_start,
                        note=str(plateau_index),
                    )
                )
                steps.append(
                    AutomationStep(
                        "ramp_target",
                        target_value=target,
                        target_start_value=previous_target,
                        target_end_value=target,
                        target_ramp_rate_value_s=target_ramp_rate,
                        basis=basis,
                        note=str(plateau_index),
                    )
                )
                previous_target = target
                _append_current_sweep_plateau(target=target, plateau_basis=basis, note=str(plateau_index))
            if targets and self.check_current_sweep_return_target.isChecked():
                steps.append(
                    AutomationStep(
                        "set_current",
                        target_value=targets[0],
                        basis=basis,
                        current_mA=current_start,
                        note=str(len(targets) + 1),
                    )
                )
                steps.append(
                    AutomationStep(
                        "ramp_target",
                        target_value=targets[0],
                        target_start_value=previous_target,
                        target_end_value=targets[0],
                        target_ramp_rate_value_s=target_ramp_rate,
                        basis=basis,
                        current_mA=current_start,
                        note=str(len(targets) + 1),
                    )
                )
            suffix, _ = self._distribution_units(basis)
            if basis == HSW_BASIS_LOAD_G:
                recipe_name = "iso-load current sweep"
            elif basis == HSW_BASIS_STRESS_MPA:
                recipe_name = "iso-stress current sweep"
            else:
                recipe_name = "iso-strain current sweep"
            summary = (
                f"Started {recipe_name}: {target_start:.4f}{suffix} to {target_end:.4f}{suffix}, "
                f"target step {target_step:.4f}{suffix} at {target_ramp_rate:.4f}{suffix}/s, "
                f"current {current_start:.2f} to {current_end:.2f} mA "
                f"at {current_ramp_rate:.2f} mA/s; {clock_summary}."
            )
            if current_hold_enabled:
                summary += (
                    f" Current ramp hold enabled: pause on absolute target error above "
                    f"{current_hold_pause_factor:.2f}x tolerance, "
                    f"resume inside {current_hold_resume_factor:.2f}x for "
                    f"{current_hold_resume_stable_s:.2f} s."
                )
            if first_overheating_enabled:
                summary += (
                    " First overheating enabled: "
                    f"{first_overheating_target_mpa:.4f} MPa preheat target before the normal sequence."
                )
            summary += self._recipe_setup_summary_sentence()
            return steps, summary, control_interval_ms

        total_distance_mm = float(self.spin_ramp_distance.value())
        step_mm = abs(float(self.spin_ramp_step.value()))
        speed_mm_s = float(self.spin_ramp_speed_mm_s.value())
        if total_distance_mm == 0.0:
            raise ValueError("Set a non-zero ramp distance.")
        targets = self._build_segment_offsets(0.0, total_distance_mm, step_mm)
        steps = []
        previous = 0.0
        for target in targets:
            steps.append(
                AutomationStep(
                    "move",
                    relative_mm=target,
                    duration_s=self._move_duration_s(target - previous, speed_mm_s),
                )
            )
            previous = target
        steps = self._append_return_to_origin(steps)
        steps = self._prepend_length_setup_steps(steps)
        summary = (
            f"Started displacement-ramp recipe: distance {total_distance_mm:.4f} mm, "
            f"step {step_mm:.4f} mm at {speed_mm_s:.4f} mm/s; {clock_summary}."
            f"{self._recipe_setup_summary_sentence()}"
        )
        return steps, summary, control_interval_ms

    def _clear_current_sweep_ramp_hold(self) -> None:
        self._current_sweep_ramp_hold_step_index = None
        self._current_sweep_ramp_hold_started_s = 0.0
        self._current_sweep_ramp_hold_in_band_since_s = None
        self._current_sweep_ramp_hold_seek_accepted_since_s = None
        self._current_sweep_ramp_hold_candidate_step_index = None
        self._current_sweep_ramp_hold_candidate_sign = 0.0
        self._current_sweep_ramp_hold_candidate_since_s = None

    def _resume_current_sweep_ramp_from_hold(self, *, now_s: float, reason: str) -> None:
        held_s = max(0.0, float(now_s) - self._current_sweep_ramp_hold_started_s)
        self._active_current_sweep_started_s += held_s
        self._active_current_sweep_last_schedule_update_s = float(now_s)
        if self._active_current_sweep_display_direction > 0.0:
            self._current_sweep_post_hold_throttle_until_s = float(now_s) + max(
                0.0,
                SERVO_CURRENT_SWEEP_POST_HOLD_THROTTLE_S,
            )
        else:
            self._current_sweep_post_hold_throttle_until_s = 0.0
        self._clear_current_sweep_ramp_hold()
        self._log(f"Resumed current ramp after holding for {held_s:.2f} s; {reason}.")

    def _apply_current_sweep_post_hold_ramp_throttle(self, *, now_s: float) -> None:
        if self._current_sweep_post_hold_throttle_until_s <= 0.0:
            self._active_current_sweep_last_schedule_update_s = float(now_s)
            return
        last_s = self._active_current_sweep_last_schedule_update_s
        if last_s <= 0.0:
            self._active_current_sweep_last_schedule_update_s = float(now_s)
            return
        active_until_s = min(float(now_s), self._current_sweep_post_hold_throttle_until_s)
        active_dt_s = max(0.0, active_until_s - float(last_s))
        factor = min(1.0, max(0.0, SERVO_CURRENT_SWEEP_POST_HOLD_THROTTLE_FACTOR))
        self._active_current_sweep_started_s += active_dt_s * (1.0 - factor)
        if float(now_s) >= self._current_sweep_post_hold_throttle_until_s:
            self._current_sweep_post_hold_throttle_until_s = 0.0
        self._active_current_sweep_last_schedule_update_s = float(now_s)

    def _current_sweep_hold_setting(
        self,
        step_value: float | None,
        widget_value: float | None,
        default: float,
    ) -> float:
        value = step_value
        if value is None:
            value = widget_value if widget_value is not None else default
        value = float(value)
        if not math.isfinite(value):
            return default
        return value

    def _current_sweep_hold_resume_factor(self, step: AutomationStep) -> float:
        config = self._control_config()
        pause_factor = self._current_sweep_hold_setting(
            step.current_hold_pause_tolerance_factor,
            config.current_sweep_hold_pause_factor
            if config is not None
            else float(self.spin_current_sweep_hold_pause_factor.value()),
            CURRENT_SWEEP_HOLD_PAUSE_TOLERANCE_FACTOR,
        )
        resume_factor = self._current_sweep_hold_setting(
            step.current_hold_resume_tolerance_factor,
            config.current_sweep_hold_resume_factor
            if config is not None
            else float(self.spin_current_sweep_hold_resume_factor.value()),
            CURRENT_SWEEP_HOLD_RESUME_TOLERANCE_FACTOR,
        )
        return max(0.0, min(pause_factor, resume_factor))

    def _current_sweep_hold_resume_stable_s(self, step: AutomationStep) -> float:
        config = self._control_config()
        return max(
            0.0,
            self._current_sweep_hold_setting(
                step.current_hold_resume_stable_s,
                config.current_sweep_hold_resume_stable_s
                if config is not None
                else float(self.spin_current_sweep_hold_resume_stable_s.value()),
                CURRENT_SWEEP_HOLD_RESUME_STABLE_S,
            ),
        )

    def _current_sweep_hold_min_band_for_basis(self, basis: str, stress_mpa: float) -> float:
        if basis == HSW_BASIS_STRESS_MPA:
            return abs(float(stress_mpa))
        if basis == HSW_BASIS_LOAD_G:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            load_g = load_g_from_stress_mpa(abs(float(stress_mpa)), diameter_mm)
            return 0.0 if load_g is None else abs(float(load_g))
        return 0.0

    def _current_sweep_hold_noise_cap_for_basis(self, tolerance: float) -> float:
        tolerance = abs(float(tolerance))
        if not math.isfinite(tolerance) or tolerance <= 0.0:
            return 0.0
        return tolerance * SERVO_CURRENT_SWEEP_HOLD_NOISE_CAP_TOLERANCE_FACTOR

    def _current_sweep_hold_entry_band_for_basis(self, tolerance: float) -> float:
        tolerance = abs(float(tolerance))
        if not math.isfinite(tolerance) or tolerance <= 0.0:
            return 0.0
        return tolerance * SERVO_CURRENT_SWEEP_HOLD_ENTRY_TOLERANCE_FACTOR

    def _current_sweep_bounded_noise_band(
        self,
        basis: str,
        noise_value: float,
        tolerance: float,
    ) -> float:
        noise_band = max(0.0, float(noise_value)) * self._current_sweep_hold_noise_sigma()
        if not self._is_current_sweep_mode(self._automation_name):
            return noise_band
        cap = self._current_sweep_hold_noise_cap_for_basis(tolerance)
        if cap <= 0.0:
            return noise_band
        return min(noise_band, cap)

    def _current_sweep_hold_min_slope_for_basis(self, basis: str) -> float:
        if basis == HSW_BASIS_STRESS_MPA:
            return SERVO_CURRENT_SWEEP_HOLD_MIN_AWAY_SLOPE_MPA_S
        if basis == HSW_BASIS_LOAD_G:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            load_slope = load_g_from_stress_mpa(
                SERVO_CURRENT_SWEEP_HOLD_MIN_AWAY_SLOPE_MPA_S,
                diameter_mm,
            )
            return 0.0 if load_slope is None else abs(float(load_slope))
        return 0.0

    def _reset_current_sweep_ramp_hold_candidate(self) -> None:
        self._current_sweep_ramp_hold_candidate_step_index = None
        self._current_sweep_ramp_hold_candidate_sign = 0.0
        self._current_sweep_ramp_hold_candidate_since_s = None

    def _current_sweep_hold_filtered_value_and_noise(self, basis: str) -> tuple[float, float] | None:
        current_value = self._current_distribution_value(basis, require_after_last_move=False)
        if current_value is None:
            return None
        if basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return float(current_value), 0.0
        signal = self._scale_control_signal_for_basis(basis)
        if signal is None:
            return float(current_value), 0.0
        return signal.value, signal.noise

    def _current_sweep_target_error_and_tolerance(
        self,
        step: AutomationStep,
        *,
        filtered: bool = False,
    ) -> tuple[float, float, float, float] | None:
        if step.target_value is None or not step.basis:
            return None
        if filtered:
            state = self._current_sweep_hold_filtered_value_and_noise(step.basis)
            if state is None:
                return None
            current_value, noise_value = state
        else:
            current_value = self._current_distribution_value(step.basis, require_after_last_move=False)
            if current_value is None:
                return None
            noise_value = 0.0
        current_value = float(current_value)
        if not math.isfinite(current_value):
            return None
        tolerance = self._automation_tolerance_for_step(step)
        seek_key = self._seek_error_key(step.basis, step.target_value)
        acceptance_tolerance = self._seek_target_acceptance_tolerance(
            step.basis,
            tolerance,
            seek_key=seek_key,
        )
        signed_error = current_value - float(step.target_value)
        return signed_error, abs(signed_error), max(1e-12, abs(float(acceptance_tolerance))), max(0.0, noise_value)

    def _current_sweep_hold_entry_confirmed(
        self,
        step: AutomationStep,
        step_index: int,
        signed_error: float,
        pause_band: float,
        tolerance: float,
        noise_value: float,
        filtered_signal: ScaleControlSignal | None,
    ) -> bool:
        if step.basis not in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            return True
        if filtered_signal is None:
            self._reset_current_sweep_ramp_hold_candidate()
            return False
        entry_band = max(
            abs(float(pause_band)),
            self._current_sweep_hold_entry_band_for_basis(tolerance),
        )
        raw_noise_band = max(0.0, float(noise_value)) * self._current_sweep_hold_noise_sigma()
        if raw_noise_band > 0.0:
            fast_band = max(
                abs(float(pause_band)),
                self._current_sweep_hold_noise_cap_for_basis(tolerance),
            )
            if (
                fast_band > 0.0
                and raw_noise_band > fast_band
                and abs(float(signed_error))
                > fast_band * SERVO_CURRENT_SWEEP_HOLD_NOISY_LARGE_ERROR_FACTOR
            ):
                self._reset_current_sweep_ramp_hold_candidate()
                return True
        if (
            abs(float(signed_error))
            > entry_band * SERVO_CURRENT_SWEEP_HOLD_LARGE_ERROR_FACTOR
        ):
            self._reset_current_sweep_ramp_hold_candidate()
            return True
        if abs(float(signed_error)) <= entry_band:
            self._reset_current_sweep_ramp_hold_candidate()
            return False
        slope = float(filtered_signal.slope_per_s)
        if step.basis == HSW_BASIS_LOAD_G:
            config = self._control_config()
            diameter_mm = config.diameter_mm if config is not None else float(self.spin_diameter.value())
            slope_mpa = stress_mpa_from_load_g(slope, diameter_mm)
            slope = 0.0 if slope_mpa is None else float(slope_mpa)
        away_slope_floor = max(
            SERVO_CURRENT_SWEEP_HOLD_MIN_AWAY_SLOPE_MPA_S,
            entry_band / max(self._current_sweep_hold_filter_window_s(), 1e-9),
        )
        moving_away = float(signed_error) * slope > 0.0 and abs(slope) >= away_slope_floor
        current_ramping_up = (
            step.current_start_mA is not None
            and step.current_end_mA is not None
            and float(step.current_end_mA) >= float(step.current_start_mA)
        )
        if (
            current_ramping_up
            and float(signed_error) < -entry_band * 2.0
            and slope < -away_slope_floor * 2.0
        ):
            self._reset_current_sweep_ramp_hold_candidate()
            return True
        if (
            not moving_away
            and abs(float(signed_error))
            <= entry_band * SERVO_CURRENT_SWEEP_HOLD_LARGE_ERROR_FACTOR
        ):
            self._reset_current_sweep_ramp_hold_candidate()
            return False
        sign = math.copysign(1.0, float(signed_error))
        timestamp_s = float(filtered_signal.timestamp_s)
        if (
            self._current_sweep_ramp_hold_candidate_step_index != step_index
            or self._current_sweep_ramp_hold_candidate_sign != sign
        ):
            self._current_sweep_ramp_hold_candidate_step_index = step_index
            self._current_sweep_ramp_hold_candidate_sign = sign
            self._current_sweep_ramp_hold_candidate_since_s = timestamp_s
            return False
        since_s = self._current_sweep_ramp_hold_candidate_since_s
        if since_s is None:
            self._current_sweep_ramp_hold_candidate_since_s = timestamp_s
            return False
        return timestamp_s - float(since_s) >= SERVO_CURRENT_SWEEP_HOLD_ENTRY_CONFIRM_S

    def _update_current_sweep_ramp_hold(
        self,
        step: AutomationStep,
        step_index: int,
        *,
        now_s: float,
    ) -> tuple[bool, bool]:
        if not step.current_hold_enabled:
            if self._current_sweep_ramp_hold_step_index == step_index:
                self._clear_current_sweep_ramp_hold()
            return False, False
        error_state = self._current_sweep_target_error_and_tolerance(step, filtered=True)
        if error_state is None:
            return self._current_sweep_ramp_hold_step_index == step_index, False

        signed_error, error_value, tolerance, noise_value = error_state
        filtered_signal = (
            self._scale_control_signal_for_basis(step.basis)
            if step.basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            else None
        )
        pause_factor = max(
            1e-12,
            self._current_sweep_hold_setting(
                step.current_hold_pause_tolerance_factor,
                self._control_config().current_sweep_hold_pause_factor
                if self._control_config() is not None
                else float(self.spin_current_sweep_hold_pause_factor.value()),
                CURRENT_SWEEP_HOLD_PAUSE_TOLERANCE_FACTOR,
            ),
        )
        resume_factor = self._current_sweep_hold_resume_factor(step)
        pause_band = max(
            tolerance * pause_factor,
            self._current_sweep_bounded_noise_band(step.basis, noise_value, tolerance),
            self._current_sweep_hold_min_band_for_basis(
                step.basis,
                self._current_sweep_hold_min_pause_stress_mpa(),
            ),
        )
        resume_noise_band = self._current_sweep_bounded_noise_band(step.basis, noise_value, tolerance)
        if not self._current_sweep_filtered_window_spans_target(
            step.basis,
            float(step.target_value),
            tolerance,
        ):
            resume_noise_band = 0.0
        resume_band = max(
            tolerance * resume_factor,
            resume_noise_band,
            self._current_sweep_hold_min_band_for_basis(
                step.basis,
                self._current_sweep_hold_min_resume_stress_mpa(),
            ),
        )
        holding = self._current_sweep_ramp_hold_step_index == step_index
        pause_error = error_value
        resume_error = error_value
        error_label = "filtered absolute target error"

        if not holding and pause_error > pause_band:
            if not self._current_sweep_hold_entry_confirmed(
                step,
                step_index,
                signed_error,
                pause_band,
                tolerance,
                noise_value,
                filtered_signal,
            ):
                return False, False
            self._current_sweep_ramp_hold_step_index = step_index
            self._current_sweep_ramp_hold_started_s = now_s
            self._current_sweep_ramp_hold_in_band_since_s = None
            self._reset_current_sweep_ramp_hold_candidate()
            setpoint = self._active_current_sweep_last_setpoint_mA
            self._log(
                "Holding current ramp"
                f"{'' if setpoint is None else f' at {setpoint:.3f} mA'}; "
                f"{error_label} {_format_compact_number(pause_error)} exceeds pause band "
                f"{_format_compact_number(pause_band)}."
            )
            return True, False

        if not holding:
            self._reset_current_sweep_ramp_hold_candidate()
            return False, False

        held_s = max(0.0, now_s - self._current_sweep_ramp_hold_started_s)
        if resume_error <= resume_band:
            if self._current_sweep_ramp_hold_in_band_since_s is None:
                self._current_sweep_ramp_hold_in_band_since_s = now_s
            stable_s = self._current_sweep_hold_resume_stable_s(step)
            if now_s - self._current_sweep_ramp_hold_in_band_since_s >= stable_s:
                self._resume_current_sweep_ramp_from_hold(
                    now_s=now_s,
                    reason=(
                        f"filtered target error {_format_compact_number(resume_error)} "
                        f"is inside resume band {_format_compact_number(resume_band)}"
                    ),
                )
                return False, False
        else:
            self._current_sweep_ramp_hold_in_band_since_s = None
        return True, False

    def _handle_current_sweep_voltage_unwind(
        self,
        step: AutomationStep,
        step_index: int,
        ramp_rate_mA_s: float,
        target_mA: float,
    ) -> bool:
        target_mA = self._recipe_current_setpoint_mA(target_mA)
        start_mA = max(target_mA, self._current_sweep_voltage_limit_start_mA)
        if self._current_sweep_voltage_limit_started_s is None:
            self._current_sweep_voltage_limit_started_s = time.monotonic()
        elapsed_s = max(0.0, time.monotonic() - self._current_sweep_voltage_limit_started_s)
        desired_mA = max(target_mA, start_mA - ramp_rate_mA_s * elapsed_s)
        setpoint_mA = self._quantize_ramp_current_mA(desired_mA, -1.0, target_mA)
        if desired_mA <= target_mA:
            setpoint_mA = target_mA
        if (
            self._active_current_sweep_last_setpoint_mA is None
            or abs(setpoint_mA - self._active_current_sweep_last_setpoint_mA) >= self._supply_current_resolution_mA() * 0.5
        ):
            if not self._set_recipe_current_mA(setpoint_mA, measure_after=False):
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return True
            self._active_current_sweep_last_setpoint_mA = setpoint_mA

        plateau_index = int(step.note) if step.note.isdigit() else None
        self._set_automation_context(
            phase="current_limit_unwind",
            basis=step.basis,
            target_value=step.target_value,
            plateau_index=plateau_index,
        )
        tolerance = self._automation_tolerance_for_step(step)
        try:
            self._seek_distribution_target(step.basis, step.target_value, tolerance)
        except Exception as exc:
            self._log(f"Recipe stopped: {exc}")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True

        if setpoint_mA <= target_mA + 1e-12:
            self._record_current_sweep_duration(step, finished_s=time.monotonic())
            self._write_control_trace(
                decision="voltage_limit_unwind_complete",
                basis=step.basis,
                target_value=step.target_value,
                current_value=self._current_distribution_value(step.basis, require_after_last_move=False),
                tolerance=tolerance,
                result="completed",
                reason="current_returned_to_sweep_start",
            )
            self._mark_voltage_limited_return_step(step_index, step)
            self._clear_current_sweep_voltage_limit()
            self._active_current_sweep_step_index = None
            self._active_current_sweep_started_s = 0.0
            self._active_current_sweep_wall_started_s = 0.0
            self._active_current_sweep_last_schedule_update_s = 0.0
            self._current_sweep_post_hold_throttle_until_s = 0.0
            self._active_current_sweep_last_setpoint_mA = None
            self._active_current_sweep_display_target_mA = None
            self._active_current_sweep_display_direction = 0.0
            self._clear_current_sweep_ramp_hold()
            return True
        return False

    def _mechanical_step_mm_for_step(self, step: AutomationStep) -> float:
        step_value = abs(float(step.mechanical_step_value or 0.0))
        if step_value <= 0.0:
            return 0.0
        if step.mechanical_step_basis == HSW_BASIS_STRAIN_PCT:
            config = self._control_config()
            length_mm = max(
                0.001,
                config.initial_length_mm if config is not None else float(self.spin_initial_length.value()),
            )
            return (step_value / 100.0) * length_mm
        return step_value

    def _handle_mechanical_scan_step(self, step: AutomationStep, step_index: int) -> bool:
        if step.target_value is None or not step.basis:
            self._log("Recipe stopped because the mechanical scan step is incomplete.")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        note_text = str(step.note or "")
        note_group = note_text.split(":", 1)[0] if note_text else ""
        if self._active_mechanical_scan_step_index != step_index:
            self._active_mechanical_scan_step_index = step_index
            self._active_mechanical_scan_started_s = time.monotonic()
            self._active_mechanical_scan_move_count = 0
            self._active_mechanical_scan_hold_started_s = None
            self._active_mechanical_scan_move_pending = False
            self._active_mechanical_scan_direction = None
            if note_text.endswith(":up") and note_group:
                self._constant_current_step_base_position_by_note.setdefault(
                    note_group,
                    self._relative_motion_base_mm(),
                )
                if note_group not in self._constant_current_step_base_strain_by_note:
                    base_strain = self._strain_percent_for_position(self._measurement_effective_position_mm())
                    if base_strain is not None:
                        self._constant_current_step_base_strain_by_note[note_group] = base_strain
            if note_text.endswith(":down") and note_group:
                self._active_mechanical_scan_origin_position_mm = (
                    self._constant_current_step_base_position_by_note.get(note_group)
                )
            else:
                self._active_mechanical_scan_origin_position_mm = None

        target_value = float(step.target_value)
        basis = str(step.basis)
        self._set_automation_context(phase="mechanical_scan", basis=basis, target_value=target_value, note=step.note)

        hold_s = max(0.0, float(step.duration_s or 0.0))
        if self._active_mechanical_scan_hold_started_s is not None:
            if not self._record_scheduled_recipe_point(step):
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return True
            if time.monotonic() - self._active_mechanical_scan_hold_started_s < hold_s:
                return False
            self._active_mechanical_scan_hold_started_s = None
            self._active_mechanical_scan_move_pending = False

        current_value = self._current_distribution_value(
            basis,
            require_after_last_move=self._active_mechanical_scan_move_pending,
        )
        if current_value is None:
            self._log_waiting_for_feedback("Waiting for fresh feedback after the fixed displacement step.")
            return False

        if self._active_mechanical_scan_direction is None:
            self._active_mechanical_scan_direction = 1.0 if target_value >= float(current_value) else -1.0
        direction = self._active_mechanical_scan_direction
        if (direction >= 0.0 and float(current_value) >= target_value) or (
            direction < 0.0 and float(current_value) <= target_value
        ):
            self._active_mechanical_scan_step_index = None
            self._active_mechanical_scan_started_s = 0.0
            self._active_mechanical_scan_move_count = 0
            self._active_mechanical_scan_hold_started_s = None
            self._active_mechanical_scan_move_pending = False
            self._active_mechanical_scan_direction = None
            self._active_mechanical_scan_origin_position_mm = None
            return True

        step_mm = self._mechanical_step_mm_for_step(step)
        if step_mm <= 0.0:
            self._log("Recipe stopped because the mechanical scan step size is zero.")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        tension_sign = self._tension_motion_sign()
        base_mm = self._relative_motion_base_mm()
        target_mm = base_mm + tension_sign * direction * step_mm
        origin_mm = self._active_mechanical_scan_origin_position_mm
        if direction < 0.0 and origin_mm is not None:
            current_offset_mm = (base_mm - origin_mm) * tension_sign
            if current_offset_mm <= max(self._motor_step_mm() * 0.5, 1e-9):
                self._active_mechanical_scan_step_index = None
                self._active_mechanical_scan_started_s = 0.0
                self._active_mechanical_scan_move_count = 0
                self._active_mechanical_scan_hold_started_s = None
                self._active_mechanical_scan_move_pending = False
                self._active_mechanical_scan_direction = None
                self._active_mechanical_scan_origin_position_mm = None
                return True
            target_offset_mm = max(0.0, current_offset_mm - step_mm)
            target_mm = origin_mm + tension_sign * target_offset_mm
        speed_mm_s = max(self._minimum_held_speed_mm_s(), float(step.mechanical_step_speed_mm_s or 0.05))
        if not self._move_to_position_mm(target_mm, chain_from_last_target=True, speed_mm_s=speed_mm_s):
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        self._active_mechanical_scan_move_count += 1
        self._active_mechanical_scan_move_pending = True
        self._active_mechanical_scan_hold_started_s = time.monotonic() if hold_s > 0.0 else None
        if hold_s <= 0.0 and not self._record_scheduled_recipe_point(step):
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        return False

    def _handle_current_zero_mark_step(self, step: AutomationStep) -> bool:
        note_text = str(step.note or "")
        note_group = note_text.split(":", 1)[0] if note_text else ""
        self._set_automation_context(
            phase="current_zero",
            basis=step.basis,
            target_value=step.target_value,
            plateau_index=int(note_group) if note_group.isdigit() else None,
            note=step.note,
        )
        zero_position_mm = self._relative_motion_base_mm()
        if note_group:
            self._constant_current_step_base_position_by_note[note_group] = self._relative_motion_base_mm()
            base_strain = self._strain_percent_for_position(zero_position_mm)
            if base_strain is not None:
                self._constant_current_step_base_strain_by_note[note_group] = base_strain
        self._active_constant_current_zero_position_mm = zero_position_mm
        self._active_constant_current_zero_current_mA = (
            None if step.current_mA is None else self._recipe_current_setpoint_mA(float(step.current_mA))
        )
        label = HSW_BASIS_LABELS.get(str(step.basis), str(step.basis or "start"))
        target_text = "-" if step.target_value is None else f"{float(step.target_value):.4f}"
        current_text = (
            "-"
            if self._active_constant_current_zero_current_mA is None
            else f"{self._active_constant_current_zero_current_mA:.3f} mA"
        )
        self._log(
            f"Marked current-specific zero at {zero_position_mm:.6f} mm "
            f"for {current_text} after reaching {label} {target_text}."
        )
        return self._record_scheduled_recipe_point(step)

    def _handle_current_sweep_step(self, step: AutomationStep, step_index: int) -> bool:
        if step.current_start_mA is None or step.current_end_mA is None or step.current_ramp_rate_mA_s is None:
            self._log("Recipe stopped because the current ramp step is incomplete.")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        if step.target_value is None or not step.basis:
            self._log("Recipe stopped because the current ramp has no control target.")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        if self._complete_voltage_limited_return_step(step, step_index):
            return True

        start_mA = self._recipe_current_setpoint_mA(float(step.current_start_mA))
        end_mA = self._recipe_current_setpoint_mA(float(step.current_end_mA))
        ramp_rate_mA_s = max(1e-9, abs(float(step.current_ramp_rate_mA_s)))
        direction = 1.0 if end_mA >= start_mA else -1.0
        self._active_current_sweep_display_target_mA = end_mA
        self._active_current_sweep_display_direction = direction
        self._ignore_voltage_limit_on_nominal_current_return(step, step_index)

        if self._active_current_sweep_step_index != step_index:
            now_s = time.monotonic()
            self._active_current_sweep_step_index = step_index
            self._active_current_sweep_started_s = now_s
            self._active_current_sweep_wall_started_s = now_s
            self._active_current_sweep_last_schedule_update_s = now_s
            self._current_sweep_post_hold_throttle_until_s = 0.0
            self._active_current_sweep_last_setpoint_mA = None
            self._clear_current_sweep_ramp_hold()
            if not self._set_recipe_current_mA(start_mA, measure_after=False):
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return True
            self._active_current_sweep_last_setpoint_mA = start_mA

        if self._current_sweep_voltage_limit_step_index == step_index:
            return self._handle_current_sweep_voltage_unwind(
                step,
                step_index,
                ramp_rate_mA_s,
                target_mA=start_mA,
            )

        plateau_index = int(step.note) if step.note.isdigit() else None
        self._set_automation_context(
            phase="current",
            basis=step.basis,
            target_value=step.target_value,
            plateau_index=plateau_index,
        )
        now_s = time.monotonic()
        holding_current, stopped_for_hold = self._update_current_sweep_ramp_hold(
            step,
            step_index,
            now_s=now_s,
        )
        if stopped_for_hold:
            return True
        tolerance = self._automation_tolerance_for_step(step)
        if holding_current:
            self._set_automation_context(
                phase="current_hold",
                basis=step.basis,
                target_value=step.target_value,
                plateau_index=plateau_index,
            )
            point_count_before_seek = len(self._session_points)
            try:
                target_recovered = self._seek_distribution_target(step.basis, step.target_value, tolerance)
            except Exception as exc:
                self._log(f"Recipe stopped: {exc}")
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return True
            if target_recovered:
                recovered_s = time.monotonic()
                if self._current_sweep_ramp_hold_seek_accepted_since_s is None:
                    self._current_sweep_ramp_hold_seek_accepted_since_s = recovered_s
                stable_s = self._current_sweep_hold_resume_stable_s(step)
                if recovered_s - self._current_sweep_ramp_hold_seek_accepted_since_s >= stable_s:
                    self._resume_current_sweep_ramp_from_hold(
                        now_s=recovered_s,
                        reason=(
                            "held-current recovery seek stayed accepted for "
                            f"{stable_s:.2f} s"
                        ),
                    )
                else:
                    self._log_waiting_for_feedback(
                        "Held-current recovery reached the target; confirming stable recovery before resuming current."
                    )
            else:
                self._current_sweep_ramp_hold_seek_accepted_since_s = None
            if len(self._session_points) == point_count_before_seek:
                self._maybe_record_scheduled_point(
                    quiet=True,
                    advance_heating=False,
                    require_fresh_after_move=step.basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA},
                )
            return False

        elapsed_s = max(0.0, now_s - self._active_current_sweep_started_s)
        self._apply_current_sweep_post_hold_ramp_throttle(now_s=now_s)
        elapsed_s = max(0.0, now_s - self._active_current_sweep_started_s)
        desired_mA = start_mA + direction * ramp_rate_mA_s * elapsed_s
        if direction >= 0.0:
            desired_mA = min(end_mA, desired_mA)
        else:
            desired_mA = max(end_mA, desired_mA)
        setpoint_mA = self._quantize_ramp_current_mA(desired_mA, direction, end_mA)
        if (
            self._active_current_sweep_last_setpoint_mA is None
            or abs(setpoint_mA - self._active_current_sweep_last_setpoint_mA) >= self._supply_current_resolution_mA() * 0.5
        ):
            if not self._set_recipe_current_mA(setpoint_mA, measure_after=False):
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return True
            self._active_current_sweep_last_setpoint_mA = setpoint_mA

        point_count_before_seek = len(self._session_points)
        try:
            self._seek_distribution_target(step.basis, step.target_value, tolerance)
        except Exception as exc:
            self._log(f"Recipe stopped: {exc}")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        if len(self._session_points) == point_count_before_seek:
            self._maybe_record_scheduled_point(
                quiet=True,
                advance_heating=False,
                require_fresh_after_move=step.basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA},
            )

        duration_s = abs(end_mA - start_mA) / ramp_rate_mA_s
        finished = elapsed_s >= duration_s and abs((self._active_current_sweep_last_setpoint_mA or setpoint_mA) - end_mA) < 1e-9
        if finished:
            self._record_current_sweep_duration(step, finished_s=now_s)
            self._active_current_sweep_step_index = None
            self._active_current_sweep_started_s = 0.0
            self._active_current_sweep_wall_started_s = 0.0
            self._active_current_sweep_last_schedule_update_s = 0.0
            self._current_sweep_post_hold_throttle_until_s = 0.0
            self._active_current_sweep_last_setpoint_mA = None
            self._active_current_sweep_display_target_mA = None
            self._active_current_sweep_display_direction = 0.0
            self._clear_current_sweep_ramp_hold()
            return True
        return False

    def _handle_target_ramp_step(self, step: AutomationStep, step_index: int) -> bool:
        if step.target_value is None or not step.basis:
            self._log("Recipe stopped because the target ramp step is incomplete.")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        end_value = float(step.target_end_value if step.target_end_value is not None else step.target_value)
        config = self._control_config()
        configured_ramp_rate = max(
            1e-9,
            abs(
                float(
                    step.target_ramp_rate_value_s
                    or (
                        config.current_sweep_target_ramp_rate_value_s
                        if config is not None
                        else self.spin_current_sweep_target_ramp_rate.value()
                    )
                )
            ),
        )
        ramp_rate = configured_ramp_rate
        if (
            step.note == "setup_preload"
            and step.basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            and self._active_target_ramp_step_index != step_index
        ):
            current_value = self._current_distribution_value(step.basis)
            if current_value is not None and float(current_value) >= end_value:
                self._setup_preload_ramp_skipped = True
                self._set_automation_context(
                    phase="target_ramp",
                    basis=step.basis,
                    target_value=end_value,
                    note=step.note,
                )
                self._record_length_setup_point()
                self._log(
                    "Mounted wire is already above setup preload; "
                    "skipping preload ramp and returning load to 0."
                )
                return True
            self._setup_preload_ramp_skipped = False
        if self._active_target_ramp_step_index != step_index:
            self._active_target_ramp_step_index = step_index
            self._active_target_ramp_started_s = time.monotonic()
            self._active_target_ramp_rate_value_s = configured_ramp_rate
            start_value = step.target_start_value
            if start_value is None:
                start_value = self._current_distribution_value(step.basis)
            self._active_target_ramp_start_value = float(end_value if start_value is None else start_value)
            if (
                step.target_start_value is None
                and step.note == "setup_preload"
                and step.basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}
            ):
                duration_s = max(
                    0.1,
                    config.setup_preload_duration_s
                    if config is not None
                    else float(self.spin_setup_preload_duration_s.value()),
                )
                live_delta = abs(float(self._active_target_ramp_start_value) - end_value)
                self._active_target_ramp_rate_value_s = max(1e-9, live_delta / duration_s)
            self._end_zero_fallback_armed = False
            self._end_zero_fallback_return_position_mm = None
            self._end_zero_fallback_raw_g = None
            if self._is_current_sweep_mode(self._automation_name) and step.basis in {
                HSW_BASIS_LOAD_G,
                HSW_BASIS_STRESS_MPA,
            }:
                ramp_start_load_g = self._basis_value_as_load_g(
                    step.basis,
                    self._active_target_ramp_start_value,
                )
                ramp_end_load_g = self._basis_value_as_load_g(step.basis, end_value)
                if (
                    ramp_start_load_g is not None
                    and ramp_end_load_g is not None
                    and ramp_end_load_g <= SETUP_ZERO_FALLBACK_MIN_RESIDUAL_G
                    and ramp_start_load_g > ramp_end_load_g + SETUP_ZERO_FALLBACK_MIN_RESIDUAL_G
                ):
                    self._end_zero_fallback_armed = True
                    self._end_zero_fallback_start_point_index = len(self._session_points)
        elif self._active_target_ramp_rate_value_s is not None:
            ramp_rate = max(1e-9, abs(float(self._active_target_ramp_rate_value_s)))

        if step.note == "setup_preload" and step.basis in {HSW_BASIS_LOAD_G, HSW_BASIS_STRESS_MPA}:
            current_value = self._current_distribution_value(step.basis)
            tolerance = self._automation_tolerance_for_step(step)
            current_load_g = None if current_value is None else self._basis_value_as_load_g(step.basis, current_value)
            end_load_g = self._basis_value_as_load_g(step.basis, end_value)
            tolerance_load_g = self._basis_value_as_load_g(step.basis, tolerance) or 0.0
            if (
                current_load_g is not None
                and end_load_g is not None
                and float(current_load_g) > float(end_load_g) + abs(float(tolerance_load_g))
            ):
                active_start = float(
                    end_value if self._active_target_ramp_start_value is None else self._active_target_ramp_start_value
                )
                if active_start < end_value or float(current_value) > active_start + abs(float(tolerance)):
                    self._active_target_ramp_start_value = float(current_value)
                    self._active_target_ramp_started_s = time.monotonic()
                    setup_duration_s = max(
                        0.1,
                        config.setup_preload_duration_s
                        if config is not None
                        else float(self.spin_setup_preload_duration_s.value()),
                    )
                    ramp_rate = max(1e-9, abs(float(current_value) - end_value) / setup_duration_s)
                    self._active_target_ramp_rate_value_s = ramp_rate

        start_value = float(
            end_value if self._active_target_ramp_start_value is None else self._active_target_ramp_start_value
        )
        direction = 1.0 if end_value >= start_value else -1.0
        elapsed_s = max(0.0, time.monotonic() - self._active_target_ramp_started_s)
        duration_s = abs(end_value - start_value) / ramp_rate
        desired_value = start_value + direction * ramp_rate * elapsed_s
        if direction >= 0.0:
            desired_value = min(end_value, desired_value)
        else:
            desired_value = max(end_value, desired_value)

        plateau_index = int(step.note) if step.note.isdigit() else None
        self._set_automation_context(
            phase="target_ramp",
            basis=step.basis,
            target_value=desired_value,
            plateau_index=plateau_index,
            note=step.note,
        )
        tolerance = self._automation_tolerance_for_step(step)
        try:
            reached = self._seek_distribution_target(step.basis, desired_value, tolerance)
        except Exception as exc:
            self._log(f"Recipe stopped: {exc}")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True

        if elapsed_s >= duration_s and reached:
            self._active_target_ramp_step_index = None
            self._active_target_ramp_started_s = 0.0
            self._active_target_ramp_start_value = None
            self._active_target_ramp_rate_value_s = None
            return True
        return False

    def _handle_starting_length_prompt_step(self) -> bool:
        if not self._is_ui_thread():
            return bool(self._call_on_ui_thread_sync(self._handle_starting_length_prompt_step))
        config = self._control_config()
        default_length_mm = max(0.001, config.initial_length_mm if config is not None else float(self.spin_initial_length.value()))
        self._update_length_setup_dialog("Enter the measured mounted wire length before setup.")
        if self._automated_setup_starting_length_mm is not None:
            starting_length_mm, accepted = self._automated_setup_starting_length_mm, True
        else:
            starting_length_mm, accepted = QtWidgets.QInputDialog.getDouble(
                self,
                APP_NAME,
                "Measured mounted wire length now (mm):",
                default_length_mm,
                0.001,
                100000.0,
                4,
            )
        if not accepted:
            self._log("Recipe stopped because starting length entry was cancelled.")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        self._setup_starting_length_mm = float(starting_length_mm)
        self._setup_measured_length_mm = float(starting_length_mm)
        self._setup_preload_position_mm = float(self._current_position_mm)
        self.spin_initial_length.setValue(float(starting_length_mm))
        self._active_control_config = self._freeze_control_config()
        if self._session_setup_txt_handle is not None:
            self._session_setup_txt_handle.write(
                f"# Measured mounted length mm\t{self._setup_measured_length_mm:.6f}\n"
                f"# Length measurement position mm\t{self._setup_preload_position_mm:.6f}\n"
            )
            self._session_setup_txt_handle.flush()
        self._refresh_equivalent_labels()
        self._update_length_setup_dialog("Moving to setup preload.")
        self._log(
            "Mounted length accepted: "
            f"{_format_compact_unit(self._setup_measured_length_mm, 'mm', decimals=4)} "
            f"at stage position {_format_compact_unit(self._setup_preload_position_mm, 'mm', decimals=4)}. "
            "The stiffness prior was rescaled before setup."
        )
        return True

    def _handle_measure_length_prompt_step(self) -> bool:
        if not self._is_ui_thread():
            return bool(self._call_on_ui_thread_sync(self._handle_measure_length_prompt_step))
        try:
            self._refresh_tic_status()
        except Exception:
            pass
        self._setup_preload_position_mm = float(self._current_position_mm)
        self._record_length_setup_point()
        self._update_length_setup_dialog("Setup preload reached. Enter the measured wire length at preload.")
        config = self._control_config()
        default_length_mm = max(0.001, config.initial_length_mm if config is not None else float(self.spin_initial_length.value()))
        if self._automated_setup_preload_length_mm is not None:
            measured_length_mm, accepted = self._automated_setup_preload_length_mm, True
        else:
            measured_length_mm, accepted = QtWidgets.QInputDialog.getDouble(
                self,
                APP_NAME,
                "Measured wire length at preload (mm):",
                default_length_mm,
                0.001,
                100000.0,
                4,
            )
        if not accepted:
            self._log("Recipe stopped because preload length entry was cancelled.")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        self._setup_measured_length_mm = float(measured_length_mm)
        if self._session_setup_txt_handle is not None:
            self._session_setup_txt_handle.write(
                f"# Measured preload length mm\t{self._setup_measured_length_mm:.6f}\n"
            )
            self._session_setup_txt_handle.flush()
        self._setup_return_zero_start_point_index = len(self._length_setup_points)
        self._setup_return_zero_speed_mm_s_value = None
        self._setup_zero_position_mm = None
        self._setup_zero_fallback_return_position_mm = None
        self._setup_zero_fallback_raw_g = None
        self._setup_zero_fallback_reason = ""
        self._update_length_setup_dialog("Measured length saved. Returning load to 0 g to compute l0.")
        self._log(
            "Measured preload length accepted: "
            f"{_format_compact_unit(self._setup_measured_length_mm, 'mm', decimals=4)}."
        )
        return True

    def _handle_mark_setup_return_zero_step(self) -> bool:
        if not self._is_ui_thread():
            return bool(self._call_on_ui_thread_sync(self._handle_mark_setup_return_zero_step))
        self._record_length_setup_point()
        self._setup_return_zero_start_point_index = len(self._length_setup_points)
        self._setup_return_zero_speed_mm_s_value = None
        self._setup_zero_position_mm = None
        self._setup_zero_fallback_return_position_mm = None
        self._setup_zero_fallback_raw_g = None
        self._setup_zero_fallback_reason = ""
        self._update_length_setup_dialog("Returning load to 0 g to compute l0.")
        self._log("Length reference already captured; returning load to 0 g to compute l0.")
        return True

    def _handle_apply_length_setup_step(self) -> bool:
        if not self._is_ui_thread():
            return bool(self._call_on_ui_thread_sync(self._handle_apply_length_setup_step))
        if self._setup_measured_length_mm is None or self._setup_preload_position_mm is None:
            self._log("Recipe stopped because length setup is missing the measured length or reference position.")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        try:
            self._refresh_tic_status()
            if self._setup_zero_position_mm is not None:
                zero_position_mm = float(self._setup_zero_position_mm)
            else:
                fitted_zero_position_mm = self._fit_setup_unload_zero_position_mm()
                if fitted_zero_position_mm is not None:
                    zero_position_mm = fitted_zero_position_mm
                    self._setup_zero_position_mm = zero_position_mm
                else:
                    zero_position_mm = float(self._current_position_mm)
                    self._setup_zero_position_mm = zero_position_mm
            l0_mm = self._apply_preload_length_result(
                measured_length_mm=self._setup_measured_length_mm,
                preload_position_mm=self._setup_preload_position_mm,
                zero_position_mm=zero_position_mm,
            )
            self._active_control_config = self._freeze_control_config()
            if self._session_setup_txt_handle is not None:
                self._session_setup_txt_handle.write(f"# Computed l0 mm\t{l0_mm:.6f}\n")
                self._session_setup_txt_handle.write(f"# Zero-load position mm\t{zero_position_mm:.6f}\n")
                self._session_setup_txt_handle.flush()
            self._record_length_setup_point()
            self._update_length_setup_dialog("Length setup finished. Starting the recipe log.")
        except Exception as exc:
            self._log(f"Recipe stopped: {exc}")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        return True

    def _handle_move_step(self, step: AutomationStep, step_index: int) -> bool:
        self._set_automation_context(phase="move")
        target_mm = step.target_mm
        if target_mm is None and step.relative_mm is not None:
            target_mm = self._recipe_origin_mm + float(step.relative_mm)
        if target_mm is None:
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        self._timed_step_elapsed_s(step_index)
        if not self._active_timed_move_sent:
            if not self._move_to_position_mm(target_mm):
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return True
            self._active_timed_move_sent = True
        if not self._record_scheduled_recipe_point(step):
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        return self._timed_step_finished(step, step_index)

    def _handle_calibration_move_step(self, step: AutomationStep, step_index: int) -> bool:
        if step.relative_mm is None:
            self._log("Recipe stopped because the calibration move step is missing its distance.")
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return True
        self._set_automation_context(
            phase=step.note or CALIBRATION_FORWARD,
            basis=step.basis or HSW_BASIS_LOAD_G,
            target_value=step.target_value,
        )
        self._timed_step_elapsed_s(step_index)
        if not self._active_timed_move_sent:
            target_mm = self._commanded_motion_base_mm() + float(step.relative_mm)
            config = self._control_config()
            calibration_speed_mm_s = (
                config.calibration_speed_mm_s
                if config is not None
                else float(self.spin_calibration_speed_mm_s.value())
            )
            if not self._move_to_position_mm(
                target_mm,
                chain_from_last_target=True,
                speed_mm_s=max(self._minimum_held_speed_mm_s(), calibration_speed_mm_s),
            ):
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return True
            self._active_timed_move_sent = True
        return self._timed_step_finished(step, step_index)

    def _record_calibration_point(self, step: AutomationStep) -> bool:
        self._set_automation_context(
            phase=step.note or CALIBRATION_PRELOAD,
            basis=step.basis or HSW_BASIS_LOAD_G,
            target_value=step.target_value,
        )
        requires_post_move_feedback = step.note in {CALIBRATION_FORWARD, CALIBRATION_REVERSE}
        if (
            requires_post_move_feedback
            and not self._has_fresh_scale_reading(after_s=self._motion_feedback_ready_after_s())
        ):
            self._log_waiting_for_feedback("Waiting for a fresh scale reading before recording calibration point.")
            return False
        if not self._record_current_point(
            quiet=True,
            advance_heating=False,
            require_fresh_after_move=requires_post_move_feedback,
        ):
            self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            return False
        return True

    def _handle_timed_record_step(self, step: AutomationStep, step_index: int, *, calibration: bool = False) -> bool:
        self._timed_step_elapsed_s(step_index)
        if not self._active_timed_move_sent:
            if calibration:
                if not self._record_calibration_point(step):
                    return False
            else:
                plateau_index = int(step.note) if step.note.isdigit() else None
                self._set_automation_context(
                    phase="record",
                    basis=step.basis,
                    target_value=step.target_value,
                    plateau_index=plateau_index,
                )
                if self._is_recovery_mode():
                    self._record_recovery_point()
                elif not self._record_current_point():
                    self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                    return True
            self._active_timed_move_sent = True
        return self._timed_step_finished(step, step_index)

    def _finalize_calibration_report(self) -> None:
        report = calibration_report_from_points(self._session_points)
        self._calibration_report = report
        if report.get("status") == "ok":
            stiffness = report.get("average_stiffness_g_per_mm")
            backlash = report.get("backlash_mm")
            baseline = report.get("baseline")
            if stiffness is not None and math.isfinite(float(stiffness)) and float(stiffness) > 0.0:
                self._calibrated_stiffness_g_per_mm = float(stiffness)
                self._calibrated_stiffness_length_mm = max(0.001, float(self.spin_initial_length.value()))
                if self._persist_settings:
                    self.settings.setValue("calibration_stiffness_g_per_mm", self._calibrated_stiffness_g_per_mm)
                    self.settings.setValue("calibration_stiffness_length_mm", self._calibrated_stiffness_length_mm)
            if isinstance(baseline, Mapping):
                load_std = baseline.get("load_std_g")
                if load_std is not None and math.isfinite(float(load_std)) and float(load_std) >= 0.0:
                    self._calibrated_load_noise_g = float(load_std)
                    if self._persist_settings:
                        self.settings.setValue("calibration_load_noise_g", self._calibrated_load_noise_g)
            if backlash is not None and math.isfinite(float(backlash)) and float(backlash) >= 0.0:
                backlash = self._quantize_backlash_mm(float(backlash))
                self.spin_backlash_mm.setValue(float(backlash))
                if self._persist_settings:
                    self.settings.setValue("backlash_mm", float(backlash))
            if self._persist_settings:
                self.settings.sync()
            self._log(
                "Calibration report ready: "
                f"stiffness {_format_compact_unit(float(stiffness), 'g/mm', decimals=4) if stiffness is not None else '-'}, "
                f"backlash {_format_compact_unit(float(backlash), 'mm', decimals=4) if backlash is not None else '-'}."
            )
        else:
            self._log("Calibration report has insufficient data; inspect the session CSV and raw scale sidecar.")
        self._write_session_metadata()

    def _has_calibration_points(self) -> bool:
        calibration_phases = {
            CALIBRATION_BASELINE,
            CALIBRATION_PRELOAD,
            CALIBRATION_FORWARD,
            CALIBRATION_REVERSE,
        }
        return any(point.automation_phase in calibration_phases for point in self._session_points)

    def _finalize_calibration_report_if_needed(self) -> None:
        if self._calibration_report is None and self._has_calibration_points():
            self._finalize_calibration_report()

    def _handle_auto_ramp_tick(self) -> None:
        if not self._automation_active or self._automation_paused:
            return
        if self._automation_index >= len(self._automation_steps):
            if not self._is_ui_thread():
                self._call_on_ui_thread_sync(self._handle_auto_ramp_tick)
                return
            is_recovery = self._is_recovery_mode()
            is_calibration = self._is_calibration_mode(self._automation_name)
            if is_calibration and self._session_active:
                self._finalize_calibration_report()
            recovery_return_duration_s = self._setup_return_duration_s() if is_calibration else None
            config = self._control_config()
            return_to_origin = config.return_to_origin if config is not None else self.check_return_to_origin.isChecked()
            if self._is_constant_current_strain_sweep_mode(self._automation_name):
                return_to_origin = False
            self._update_recipe_progress(complete=True)
            self._stop_auto_ramp(log_completion=False, keep_progress=True)
            self._log("Recovery completed." if is_recovery else "Recipe completed.")
            if not is_recovery and self._session_active:
                self._stop_session(reason="recipe_completed", detail="Recipe completed.")
            if not is_recovery and return_to_origin:
                self._pending_recovery_return_duration_s = recovery_return_duration_s
                self._start_recovery_position_origin()
                return
            self._restore_main_window_focus_soon()
            return
        step_index = self._automation_index
        step = self._automation_steps[step_index]
        self._automation_index += 1
        if step.action == "move":
            finished = self._handle_move_step(step, step_index)
            if not finished and self._automation_active:
                self._automation_index -= 1
        elif step.action == "ramp_target":
            finished = self._handle_target_ramp_step(step, step_index)
            if not finished and self._automation_active:
                self._automation_index -= 1
        elif step.action == "seek_target":
            if step.target_value is None or not step.basis:
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return
            if step.note == "setup_return_zero" and self._automation_step_note != "setup_return_zero":
                self._setup_return_zero_speed_mm_s_value = None
            plateau_index = int(step.note) if step.note.isdigit() else None
            self._set_automation_context(
                phase="seek",
                basis=step.basis,
                target_value=step.target_value,
                plateau_index=plateau_index,
                note=step.note,
            )
            tolerance = self._automation_tolerance_for_step(step)
            if tolerance <= 0.0:
                self._log("Recipe stopped because the target tolerance is zero.")
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return
            try:
                reached = self._seek_distribution_target(step.basis, step.target_value, tolerance)
            except Exception as exc:
                self._log(f"Recipe stopped: {exc}")
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return
            if reached:
                current_value = self._current_distribution_value(step.basis)
                if current_value is None:
                    current_value = 0.0
                label = HSW_BASIS_LABELS.get(step.basis, step.basis)
                self._log(
                    f"Reached {label} plateau {step.target_value:.4f} "
                    f"(live {current_value:.4f})."
                )
            else:
                self._automation_index -= 1
        elif step.action == "set_current":
            plateau_index = int(step.note) if step.note.isdigit() else None
            self._set_automation_context(
                phase="current",
                basis=step.basis,
                target_value=step.target_value,
                plateau_index=plateau_index,
            )
            if step.current_mA is None or not self._set_recipe_current_mA(float(step.current_mA), measure_after=False):
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
            elif not self._record_scheduled_recipe_point(step):
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
        elif step.action == "mark_current_zero":
            if not self._handle_current_zero_mark_step(step):
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
        elif step.action == "sweep_current":
            finished = self._handle_current_sweep_step(step, step_index)
            if not finished and self._automation_active:
                self._automation_index -= 1
        elif step.action == "mechanical_scan":
            finished = self._handle_mechanical_scan_step(step, step_index)
            if not finished and self._automation_active:
                self._automation_index -= 1
        elif step.action == "starting_length_prompt":
            self._set_automation_context(phase="starting_length", note=step.note)
            self._handle_starting_length_prompt_step()
        elif step.action == "measure_length_prompt":
            self._set_automation_context(
                phase="length_prompt",
                basis=step.basis,
                target_value=step.target_value,
                note=step.note,
            )
            self._handle_measure_length_prompt_step()
        elif step.action == "mark_setup_return_zero":
            self._set_automation_context(
                phase="return_zero_start",
                basis=step.basis,
                target_value=step.target_value,
                note=step.note,
            )
            self._handle_mark_setup_return_zero_step()
        elif step.action == "apply_length_setup":
            self._set_automation_context(phase="apply_l0", note=step.note)
            self._handle_apply_length_setup_step()
        elif step.action == "start_session":
            self._begin_recipe_logging()
            self._close_length_setup_dialog()
        elif step.action == "calibration_move":
            finished = self._handle_calibration_move_step(step, step_index)
            if not finished and self._automation_active:
                self._automation_index -= 1
        elif step.action == "calibration_record":
            finished = self._handle_timed_record_step(step, step_index, calibration=True)
            if not finished and self._automation_active:
                self._automation_index -= 1
        elif step.action == "settle":
            if step.note == "setup_preload" and self._setup_preload_ramp_skipped:
                self._set_automation_context(
                    phase="settle",
                    basis=step.basis,
                    target_value=step.target_value,
                    plateau_index=int(step.note) if step.note.isdigit() else None,
                    note=step.note,
                )
                self._record_length_setup_point()
                self._log("Skipping setup preload settle because the run started above preload.")
                if self._automation_active:
                    self._automation_completed_ticks = min(
                        max(1, self._automation_total_steps or len(self._automation_steps)),
                        self._automation_completed_ticks + 1,
                    )
                self._update_recipe_progress()
                self._refresh_live_labels()
                return
            self._timed_step_elapsed_s(step_index)
            plateau_index = int(step.note) if step.note.isdigit() else None
            setup_settle_phase = step.note in {"setup_preload", "setup_return_zero"}
            self._set_automation_context(
                phase="settle",
                basis=step.basis,
                target_value=step.target_value,
                plateau_index=plateau_index,
                note=step.note,
            )
            settle_target_reached = True
            setup_points_before = len(self._length_setup_points) if setup_settle_phase else None
            if (
                step.basis
                and step.target_value is not None
                and not self._is_recovery_mode()
            ):
                tolerance = self._automation_tolerance_for_step(step)
                try:
                    settle_target_reached = self._seek_distribution_target(step.basis, step.target_value, tolerance)
                except Exception as exc:
                    self._log(f"Recipe stopped: {exc}")
                    self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                    return
            elif self._is_recovery_mode():
                self._record_recovery_point()
            elif not self._record_scheduled_recipe_point(step):
                self._stop_auto_ramp(log_completion=False, offer_recovery=True)
                return
            if setup_settle_phase and setup_points_before == len(self._length_setup_points):
                self._record_length_setup_point()
            current_sweep_timed_settle = self._is_current_sweep_mode(self._automation_name)
            if setup_settle_phase:
                current_sweep_timed_settle = False
            if not settle_target_reached and not current_sweep_timed_settle:
                self._reset_timed_step_state()
            if (
                (
                    (not settle_target_reached and not current_sweep_timed_settle)
                    or not self._timed_step_finished(step, step_index)
                )
                and self._automation_active
            ):
                self._automation_index -= 1
        elif step.action == "record":
            finished = self._handle_timed_record_step(step, step_index)
            if not finished and self._automation_active:
                self._automation_index -= 1
        if self._automation_active:
            self._automation_completed_ticks = min(
                max(1, self._automation_total_steps or len(self._automation_steps)),
                self._automation_completed_ticks + 1,
            )
        self._update_recipe_progress()
        self._refresh_live_labels()

    def _handle_status_timer(self) -> None:
        if self._automation_active or self._session_active:
            self._refresh_tic_status()
        if self._supply_controller is not None and self._supply_controller.is_connected():
            self._refresh_supply_snapshot()
        if self._handle_raw_scale_display_limit_status():
            return
        self._handle_applied_load_limit_status()

    def _handle_ui_refresh_timer(self) -> None:
        if not self._automation_active and not self._session_active:
            self._ui_refresh_timer.stop()
            return
        started_s = time.monotonic()
        previous_ui_s = self._ui_refresh_last_monotonic_s
        previous_scale_timestamp = self._latest_scale_timestamp
        self._ui_refresh_last_monotonic_s = started_s
        dialog_sample_recorded = self._record_live_dialog_samples_from_ui_refresh()
        live_plot_sample_recorded, dashboard_plot_refreshed = self._record_live_plot_sample_from_ui_refresh()
        self._refresh_live_labels()
        finished_s = time.monotonic()
        self._write_ui_telemetry_sample(
            started_s=started_s,
            finished_s=finished_s,
            previous_ui_s=previous_ui_s,
            scale_sample_changed=(
                previous_scale_timestamp is not None
                and self._latest_scale_timestamp is not None
                and self._latest_scale_timestamp != previous_scale_timestamp
            ),
            dialog_sample_recorded=dialog_sample_recorded,
            live_plot_sample_recorded=live_plot_sample_recorded,
            dashboard_plot_refreshed=dashboard_plot_refreshed,
        )

    def _handle_ui_heartbeat_timer(self) -> None:
        now_s = perf_counter()
        previous_s = self._ui_heartbeat_last_s
        self._ui_heartbeat_last_s = now_s
        if previous_s is None:
            return
        interval_ms = max(0.0, (now_s - previous_s) * 1000.0)
        self._ui_heartbeat_interval_ms = interval_ms
        self._ui_heartbeat_fps = None if interval_ms <= 0.0 else 1000.0 / interval_ms

    def _setup_dialog_visible(self) -> bool:
        return self._length_setup_dialog is not None and not self._length_setup_dialog.isHidden()

    def _recovery_dialog_visible(self) -> bool:
        return self._recovery_plot_dialog is not None and not self._recovery_plot_dialog.isHidden()

    def _write_ui_telemetry_sample(
        self,
        *,
        started_s: float,
        finished_s: float,
        previous_ui_s: float | None,
        scale_sample_changed: bool,
        dialog_sample_recorded: bool,
        live_plot_sample_recorded: bool,
        dashboard_plot_refreshed: bool,
    ) -> None:
        if (
            not self._session_active
            or self._session_ui_telemetry_writer is None
            or self._session_ui_telemetry_handle is None
        ):
            return
        actual_interval_ms = None if previous_ui_s is None else max(0.0, (started_s - previous_ui_s) * 1000.0)
        ui_fps = None if not actual_interval_ms or actual_interval_ms <= 0.0 else 1000.0 / actual_interval_ms
        scale_age_s = self._scale_reading_age_s()

        def _number(value: float | None, *, decimals: int = 6) -> str:
            if value is None or not math.isfinite(float(value)):
                return ""
            return f"{float(value):.{decimals}f}"

        self._session_ui_telemetry_writer.writerow(
            {
                "elapsed_s": f"{max(0.0, started_s - self._session_start_monotonic):.6f}",
                "timestamp_utc": _utc_timestamp(),
                "target_interval_ms": int(self._ui_refresh_interval_ms()),
                "actual_interval_ms": _number(actual_interval_ms, decimals=3),
                "ui_fps": _number(ui_fps, decimals=3),
                "ui_heartbeat_interval_ms": _number(self._ui_heartbeat_interval_ms, decimals=3),
                "ui_heartbeat_fps": _number(self._ui_heartbeat_fps, decimals=3),
                "handler_duration_ms": _number(max(0.0, (finished_s - started_s) * 1000.0), decimals=3),
                "graph_refresh_interval_ms": int(self._graph_refresh_interval_ms()),
                "task_text": self._current_task_summary(),
                "automation_active": int(bool(self._automation_active)),
                "session_active": int(bool(self._session_active)),
                "session_logging_enabled": int(bool(self._session_logging_enabled)),
                "length_setup_dialog_visible": int(self._setup_dialog_visible()),
                "recovery_dialog_visible": int(self._recovery_dialog_visible()),
                "scale_sample_changed": int(bool(scale_sample_changed)),
                "dialog_sample_recorded": int(bool(dialog_sample_recorded)),
                "live_plot_sample_recorded": int(bool(live_plot_sample_recorded)),
                "dashboard_plot_refreshed": int(bool(dashboard_plot_refreshed)),
                "latest_scale_age_s": _number(scale_age_s, decimals=3),
                "session_points": len(self._session_points),
                "live_plot_points": len(self._live_plot_points),
            }
        )
        self._session_ui_telemetry_count += 1
        if self._session_ui_telemetry_count % 10 == 0:
            self._session_ui_telemetry_handle.flush()

    def _record_live_plot_sample_from_ui_refresh(self) -> tuple[bool, bool]:
        if (
            not self._session_active
            or self._latest_scale_timestamp is None
            or self._last_live_plot_scale_timestamp == self._latest_scale_timestamp
            or self._setup_dialog_visible()
        ):
            return False, False
        point = self._capture_live_plot_point()
        if point is None:
            return False, False
        self._live_plot_points.append(point)
        if len(self._live_plot_points) > LIVE_PLOT_MAX_POINTS:
            self._live_plot_points = self._live_plot_points[-LIVE_PLOT_MAX_POINTS:]
        self._last_live_plot_scale_timestamp = self._latest_scale_timestamp
        now_s = time.monotonic()
        if self._dashboard_graph_refresh_due(now_s=now_s):
            self._refresh_plots()
            self._last_dashboard_plot_refresh_s = now_s
            return True, True
        return True, False

    def _record_live_dialog_samples_from_ui_refresh(self) -> bool:
        recorded = False
        if self._latest_scale_timestamp is None:
            return False
        if (
            self._setup_dialog_visible()
            and self._automation_active
            and self._length_setup_last_record_scale_timestamp != self._latest_scale_timestamp
        ):
            self._record_length_setup_point()
            recorded = True
        if (
            self._recovery_dialog_visible()
            and self._automation_active
            and self._is_recovery_mode()
            and self._recovery_last_record_scale_timestamp != self._latest_scale_timestamp
        ):
            self._record_recovery_point()
            recorded = True
        return recorded

    def _refresh_live_labels(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._refresh_live_labels)
            return
        effective_load = self._current_effective_load_g()
        if self._latest_scale_timestamp is None:
            self.label_scale_value.setText("Raw scale: no readings yet | Applied tensile load: -")
        else:
            now_s = time.time()
            recent_scale = self._scale_signal_buffer.recent_summary(now_s=now_s, window_s=1.0)
            rate_text = (
                "-"
                if recent_scale.sample_rate_hz is None
                else f"{recent_scale.sample_rate_hz:.1f} Hz"
            )
            noise_text = (
                "-"
                if recent_scale.load_std_g is None
                else f"{recent_scale.load_std_g:.5f} g std"
            )
            self.label_scale_value.setText(
                f"Applied tensile load: {effective_load:.5f} g | "
                f"Raw scale: {self._latest_scale_value_g:.5f} g | "
                f"Scale: {rate_text}, {noise_text}"
            )
        self.label_scale_raw.setText(f"Raw line: {self._latest_scale_text or '-'}")
        self.label_tic_position.setText(
            f"Raw position: {self._current_position_mm:.4f} mm ({self._current_position_steps} steps) | "
            f"Tensile displacement: {self._tensile_displacement_mm(self._effective_position_mm):.4f} mm"
        )
        self.label_reference_status.setText(
            f"Reference position: {self._position_reference_mm:.4f} mm | "
            f"Last target: {self._last_move_target_mm:.4f} mm"
            f"{' | waiting for preload' if self._preload_reference_armed else ''}"
        )

        preload_state = self._current_preload_state(effective_load)
        if preload_state == PRELOAD_PENDING:
            strain = None
            stress = None
        else:
            strain = self._strain_percent_for_position(self._effective_position_mm)
            stress = stress_mpa_from_load_g(effective_load, float(self.spin_diameter.value()))
        self.label_live_summary.setText(
            f"Live strain: {'-' if strain is None else f'{strain:.4f} %'} | "
            f"Live stress: {'-' if stress is None else f'{stress:.4f} MPa'}"
            f" | Heating: {'off' if not self._supply_output_enabled else f'{self._supply_last_setpoint_mA or 0.0:.2f} mA'}"
        )
        live_speed_text = self._live_speed_summary_text()
        self.label_live_speed.setText(live_speed_text)
        session_value = "Running" if self._session_active else "Idle"
        self._set_dashboard_value("load_g", f"{effective_load:.3f} g")
        self._set_dashboard_value(
            "stress_mpa",
            "-" if stress is None else f"{stress:.1f} MPa",
        )
        self._set_dashboard_value(
            "strain_pct",
            "-" if strain is None else f"{strain:.3f} %",
        )
        speed_values = self._live_speed_values()

        def _dashboard_rate_text(value: float | None, unit: str) -> str:
            if value is None or not math.isfinite(float(value)):
                return "-"
            return f"{float(value):.3g} {unit}"

        self._set_dashboard_value("speed_mm_s", _dashboard_rate_text(speed_values["speed_mm_s"], "mm/s"))
        self.label_card_session.setText(
            f"{session_value} | {len(self._session_points)} point(s)"
        )
        if self._latest_scale_timestamp is None:
            scale_value = "No readings yet"
        else:
            age_s = self._scale_reading_age_s() or 0.0
            freshness = "stale" if age_s > STALE_SCALE_AFTER_S else "live"
            recent_rate = self._scale_signal_buffer.sample_rate_hz(now_s=time.time())
            rate_suffix = "" if recent_rate is None else f" | {recent_rate:.1f} Hz"
            scale_value = f"{effective_load:.4f} g | {freshness} {age_s:.1f} s{rate_suffix}"
        self.label_card_scale.setText(scale_value)
        vin_text = "-" if self._last_tic_vin_v is None else f"{self._last_tic_vin_v:.2f} V"
        if self._tic_motor_power_ok is False:
            motion_state = f"Motor power low/off | VIN {vin_text}"
        else:
            motion_state = f"{self._tensile_displacement_mm(self._effective_position_mm):.4f} mm tensile"
            if self._last_tic_vin_v is not None:
                motion_state += f" | VIN {vin_text}"
        if self.check_soft_limits.isChecked():
            motion_state += (
                f" | limits {min(self.spin_soft_min_mm.value(), self.spin_soft_max_mm.value()):.2f}"
                f" to {max(self.spin_soft_min_mm.value(), self.spin_soft_max_mm.value()):.2f}"
            )
        if preload_state == PRELOAD_PENDING:
            motion_state += f" | preload < {self.spin_preload_threshold_g.value():.4f} g"
        self.label_card_motion.setText(motion_state)
        self._set_dashboard_value("motor", f"{self._tensile_displacement_mm(self._effective_position_mm):.4f} mm")
        if self._automation_active:
            recipe_state = (
                f"{self._automation_name} | done {self._automation_index}"
                f"/{max(1, len(self._automation_steps))}"
            )
            if self._automation_plateau_label:
                recipe_state += f" | {self._automation_phase} {self._automation_plateau_label}"
            elif self._automation_phase not in {"idle", "start"}:
                recipe_state += f" | {self._automation_phase}"
        else:
            recipe_state = str(self.combo_recipe_mode.currentText())
        self.label_card_recipe.setText(recipe_state)
        self._update_current_task_display()
        supply_current = self._supply_snapshot.get("current_mA")
        supply_voltage = self._supply_snapshot.get("voltage_V")
        current_text = "-" if supply_current is None else f"{supply_current:.2f}mA"
        voltage_text = "-" if supply_voltage is None else f"{supply_voltage:.2f}V"
        self._set_dashboard_value("supply", f"{current_text} {voltage_text}")
        self._refresh_supply_live_label()

    def _refresh_plots(self) -> None:
        if not self._is_ui_thread():
            self._run_on_ui_thread(self._refresh_plots)
            return
        if self._setup_dialog_visible():
            return
        if not self._dashboard_plot_bundles:
            self._refresh_recovery_plot()
            return
        display_points = self._display_plot_points()
        active_tiles = [tile for tile in self._plot_tiles if tile.visible.isChecked()]
        if not active_tiles:
            active_tiles = list(self._plot_tiles[:1])
        for tile_index, tile in enumerate(active_tiles[:4]):
            bundle = self._dashboard_plot_bundles[tile_index]
            bundle.widget.show()
            x_channel = self._plot_channel(str(tile.x_combo.currentData() or "elapsed_s"))
            y_left_channel = self._plot_channel(str(tile.y_left_combo.currentData() or "load_g"))
            y_right_channel = self._plot_channel(str(tile.y_right_combo.currentData() or ""))
            if x_channel is None or y_left_channel is None:
                self._set_pyqtgraph_curve_data(bundle.left_curve, [], [])
                self._set_pyqtgraph_curve_data(bundle.right_curve, [], [])
                continue

            self._style_pyqtgraph_plot(
                bundle,
                title=self._plot_title(x_channel, y_left_channel, y_right_channel),
                x_label=x_channel.label,
                left_label=y_left_channel.label,
                right_label=y_right_channel.label if y_right_channel is not None else None,
                left_color=y_left_channel.color,
                right_color=y_right_channel.color if y_right_channel is not None else "#f59e0b",
            )
            self._set_pyqtgraph_curve_style(
                bundle.left_curve,
                y_left_channel.color,
                width=0.8,
                symbol="o",
            )
            left_x, left_y = self._plot_xy_values(display_points, x_channel, y_left_channel)
            self._set_pyqtgraph_curve_data(bundle.left_curve, left_x, left_y)
            if y_right_channel is not None:
                self._set_pyqtgraph_curve_style(
                    bundle.right_curve,
                    y_right_channel.color,
                    width=0.75,
                    symbol="s",
                )
                right_x, right_y = self._plot_xy_values(display_points, x_channel, y_right_channel)
                self._set_pyqtgraph_curve_data(bundle.right_curve, right_x, right_y)
                if bundle.sync_right_view is not None:
                    bundle.sync_right_view()
                if bundle.right_view is not None:
                    bundle.right_view.enableAutoRange()
            else:
                self._set_pyqtgraph_curve_data(bundle.right_curve, [], [])
        for bundle in self._dashboard_plot_bundles[len(active_tiles[:4]):]:
            bundle.widget.hide()
            self._set_pyqtgraph_curve_data(bundle.left_curve, [], [])
            self._set_pyqtgraph_curve_data(bundle.right_curve, [], [])
        self._refresh_recovery_plot()

    def _display_plot_points(self) -> list[MeasurementPoint]:
        if not self._live_plot_points:
            return self._downsample_display_plot_points(list(self._session_points))
        points = list(self._session_points) + list(self._live_plot_points)
        points.sort(key=lambda point: point.elapsed_s)
        return self._downsample_display_plot_points(points)

    def _downsample_display_plot_points(self, points: list[MeasurementPoint]) -> list[MeasurementPoint]:
        if len(points) <= DISPLAY_PLOT_MAX_POINTS:
            self._display_plot_old_cache_key = None
            self._display_plot_old_cache = []
            return points
        recent_count = min(DISPLAY_PLOT_RECENT_POINTS, DISPLAY_PLOT_MAX_POINTS, len(points))
        old_budget = max(0, DISPLAY_PLOT_MAX_POINTS - recent_count)
        if old_budget <= 0:
            self._display_plot_old_cache_key = None
            self._display_plot_old_cache = []
            return points[-DISPLAY_PLOT_MAX_POINTS:]
        older_points = points[:-recent_count]
        recent_points = points[-recent_count:]
        if len(older_points) <= old_budget:
            self._display_plot_old_cache_key = None
            self._display_plot_old_cache = []
            return older_points + recent_points
        bridge_budget = min(DISPLAY_PLOT_BRIDGE_POINTS, max(0, old_budget // 3))
        history_budget = max(1, old_budget - bridge_budget)
        sampled_older = self._cached_stable_downsample_older_plot_points(older_points, history_budget)
        if bridge_budget > 0 and sampled_older:
            latest_sampled_elapsed_s = float(sampled_older[-1].elapsed_s)
            bridge_source = [
                point for point in older_points if float(point.elapsed_s) > latest_sampled_elapsed_s
            ]
            if bridge_source:
                sampled_older.extend(self._stable_downsample_older_plot_points(bridge_source, bridge_budget))
        if len(sampled_older) > old_budget:
            sampled_older = sampled_older[:history_budget] + sampled_older[-bridge_budget:]
        return sampled_older + recent_points

    def _cached_stable_downsample_older_plot_points(
        self,
        points: list[MeasurementPoint],
        budget: int,
    ) -> list[MeasurementPoint]:
        if not points:
            return []
        first_elapsed_s = float(points[0].elapsed_s)
        granularity = max(1, DISPLAY_PLOT_OLD_CACHE_GRANULARITY)
        bucket = len(points) // granularity
        boundary_index = min(len(points) - 1, max(0, (bucket * granularity) - 1))
        boundary_point = points[boundary_index]
        cache_key = (
            budget,
            bucket,
            id(points[0]),
            first_elapsed_s,
            id(boundary_point),
            float(boundary_point.elapsed_s),
        )
        if self._display_plot_old_cache_key == cache_key:
            return list(self._display_plot_old_cache)
        sampled = self._stable_downsample_older_plot_points(points, budget)
        self._display_plot_old_cache_key = cache_key
        self._display_plot_old_cache = list(sampled)
        return sampled

    def _stable_downsample_older_plot_points(
        self,
        points: list[MeasurementPoint],
        budget: int,
    ) -> list[MeasurementPoint]:
        if budget <= 0:
            return []
        if len(points) <= budget:
            return points
        if budget == 1:
            return [points[0]]
        first_elapsed_s = float(points[0].elapsed_s)
        last_elapsed_s = float(points[-1].elapsed_s)
        span_s = max(0.0, last_elapsed_s - first_elapsed_s)
        bucket_s = DISPLAY_PLOT_BASE_BUCKET_S
        target_bucket_s = span_s / max(1, budget - 1)
        while bucket_s < target_bucket_s:
            bucket_s *= 2.0
        sampled: list[MeasurementPoint] = []
        seen_buckets: set[int] = set()
        for point in points:
            bucket = int(math.floor(max(0.0, float(point.elapsed_s)) / bucket_s))
            if bucket in seen_buckets:
                continue
            seen_buckets.add(bucket)
            sampled.append(point)
        if sampled[-1] is not points[-1]:
            sampled.append(points[-1])
        if len(sampled) <= budget:
            return sampled
        step = (len(sampled) - 1) / float(budget - 1)
        return [sampled[round(index * step)] for index in range(budget)]

    def _choose_log_dir(self) -> None:
        start_dir = self.edit_log_dir.text().strip() or _default_download_dir()
        new_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select output folder",
            start_dir,
        )
        if new_dir:
            self.edit_log_dir.setText(new_dir)

    def _open_log_dir(self) -> None:
        directory = Path(self.edit_log_dir.text().strip() or _default_download_dir()).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "Output folder",
                f"Could not create output folder:\n{directory}\n\n{exc}",
            )
            return
        if not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(directory))):
            QtWidgets.QMessageBox.warning(
                self,
                "Output folder",
                f"Could not open output folder:\n{directory}",
            )

    def _save_settings(self) -> None:
        self.settings.setValue("scale_port", self.combo_scale_port.currentData() or "")
        self.settings.setValue("scale_baud", self.combo_scale_baud.currentText())
        self.settings.setValue("scale_interval_ms", self.spin_scale_interval.value())
        self.settings.setValue("scale_request", self.edit_scale_request.text())
        self.settings.setValue("scale_terminator", self.edit_scale_terminator.text())
        self.settings.setValue("supply_port", self.combo_supply_port.currentData() or "")
        self.settings.setValue("supply_baud", self.combo_supply_baud.currentText())
        self.settings.setValue("supply_profile", self.combo_supply_profile.currentData() or "hmp4030")
        self.settings.setValue("shared_broker_host", self.edit_shared_broker_host.text().strip())
        self.settings.setValue("shared_broker_port", self.spin_shared_broker_port.value())
        self.settings.setValue("current_sweep_supply_channel", self.combo_current_sweep_supply_channel.currentData() or 0)
        self.settings.setValue("supply_voltage_limit_v", self.spin_supply_voltage_limit.value())
        self.settings.setValue("supply_manual_current_mA", self.spin_supply_manual_current.value())
        self.settings.setValue("continuity_monitor_enabled", self.check_continuity_monitor.isChecked())
        self.settings.setValue("continuity_current_mA", self.spin_continuity_current_mA.value())
        self.settings.setValue("motor_supply_enabled", self.check_motor_supply_power.isChecked())
        self.settings.setValue("motor_supply_channel", self.combo_motor_supply_channel.currentData() or 0)
        self.settings.setValue("motor_supply_voltage_v", self.spin_motor_supply_voltage.value())
        self.settings.setValue("motor_supply_current_limit_a", self.spin_motor_supply_current_limit.value())
        self.settings.setValue("ticcmd_path", self.edit_ticcmd_path.text())
        self.settings.setValue("tic_native_usb_preferred", self.check_tic_native_usb.isChecked())
        self.settings.setValue("tic_serial", self.edit_tic_serial.text())
        self.settings.setValue("tic_current_limit_mA", self.spin_tic_current_limit_mA.value())
        self.settings.setValue("tic_status_interval_ms", self._tic_status_interval_ms())
        self.settings.setValue("tic_keepalive_interval_ms", self._tic_keepalive_interval_ms())
        self.settings.setValue("full_steps_per_mm", self.spin_full_steps_per_mm.value())
        self.settings.setValue("tic_step_mode", self._selected_tic_step_mode())
        self.settings.setValue("steps_per_mm", self.spin_steps_per_mm.value())
        self.settings.setValue("motor_defaults_version", MOTOR_DEFAULTS_VERSION)
        self.settings.setValue(
            "motor_step_calibration_increment_steps",
            self.spin_motor_step_calibration_increment_steps.value(),
        )
        self.settings.setValue("motor_step_calibration_moves", self.spin_motor_step_calibration_moves.value())
        self.settings.setValue(
            "motor_step_calibration_speed_mm_s",
            self.spin_motor_step_calibration_speed_mm_s.value(),
        )
        self.settings.setValue("jog_mm", self.spin_jog_mm.value())
        self.settings.setValue("manual_motion_speed_mm_s", self.spin_motion_speed_mm_s.value())
        self.settings.setValue("ramp_speed_mm_s", self.spin_ramp_speed_mm_s.value())
        self.settings.setValue("cycle_speed_mm_s", self.spin_cycle_speed_mm_s.value())
        self.settings.setValue("hold_speed_mm_s", self.spin_hold_speed_mm_s.value())
        self.settings.setValue("distribution_seek_speed_mm_s", self.spin_distribution_seek_speed_mm_s.value())
        self.settings.setValue("current_sweep_target_speed_mm_s", self.spin_current_sweep_target_speed_mm_s.value())
        self.settings.setValue("soft_limits_enabled", self.check_soft_limits.isChecked())
        self.settings.setValue("soft_limit_min_mm", self.spin_soft_min_mm.value())
        self.settings.setValue("soft_limit_max_mm", self.spin_soft_max_mm.value())
        self.settings.setValue("max_load_enabled", self.check_max_load.isChecked())
        self.settings.setValue("max_load_g", self.spin_max_load_g.value())
        self.settings.setValue("raw_scale_display_limit_g", self.spin_raw_scale_limit_g.value())
        self.settings.setValue("zero_load_scale_g", self.spin_zero_load_scale_g.value())
        self.settings.setValue("negative_scale_is_tension", self.check_tension_load_positive.isChecked())
        self.settings.setValue("positive_motion_is_tension", self.check_positive_motion_is_tension.isChecked())
        self.settings.setValue("backlash_mm", self.spin_backlash_mm.value())
        self.settings.setValue("initial_length_mm", self.spin_initial_length.value())
        if self._calibrated_stiffness_g_per_mm is not None:
            self.settings.setValue("calibration_stiffness_g_per_mm", self._calibrated_stiffness_g_per_mm)
        if self._calibrated_stiffness_length_mm is not None:
            self.settings.setValue("calibration_stiffness_length_mm", self._calibrated_stiffness_length_mm)
        if self._calibrated_load_noise_g is not None:
            self.settings.setValue("calibration_load_noise_g", self._calibrated_load_noise_g)
        self.settings.setValue("diameter_mm", self.spin_diameter.value())
        self.settings.setValue("name_composition", self.edit_name_composition.text())
        self.settings.setValue("name_wire", self.edit_name_wire.text())
        self.settings.setValue("name_specimen", self.edit_name_specimen.text())
        self.settings.setValue("name_condition", self.edit_name_condition.text())
        self.settings.setValue("sample_name", self.edit_sample_name.text())
        self.settings.setValue("run_notes", self.edit_run_notes.toPlainText())
        self.settings.setValue("builder_project_path", self.edit_project_path.text())
        log_dir_to_save = self.edit_log_dir.text()
        if self._provided_log_dir and log_dir_to_save == self._provided_log_dir:
            log_dir_to_save = self._restored_log_dir or log_dir_to_save
        self.settings.setValue("log_dir", log_dir_to_save)
        self.settings.setValue("log_name", _clean_session_basename(self.edit_log_name.text()))
        self.settings.setValue("tare_on_start", self.check_tare_on_start.isChecked())
        self.settings.setValue("developer_run_log_mirror_enabled", self._run_log_mirror_enabled)
        self.settings.setValue("developer_run_log_mirror_path", str(self._run_log_mirror_path))
        self.settings.setValue(
            "show_recipe_file_controls",
            bool(
                self.action_show_recipe_file_controls is not None
                and self.action_show_recipe_file_controls.isChecked()
            ),
        )
        self.settings.setValue("recipe_mode", self.combo_recipe_mode.currentData())
        self.settings.setValue("return_to_origin", self.check_return_to_origin.isChecked())
        self.settings.setValue("control_interval_ms", self._control_interval_ms())
        self.settings.setValue("log_interval_ms", self._log_interval_ms())
        self.settings.setValue("ui_refresh_interval_ms", self._ui_refresh_interval_ms())
        self.settings.setValue("graph_refresh_interval_ms", self._graph_refresh_interval_ms())
        self.settings.setValue("supply_read_interval_ms", self._supply_read_interval_ms())
        self.settings.setValue("ramp_distance_mm", self.spin_ramp_distance.value())
        self.settings.setValue("ramp_step_mm", self.spin_ramp_step.value())
        self.settings.setValue("ramp_interval_ms", self.spin_ramp_interval.value())
        self.settings.setValue("cycle_amplitude_mm", self.spin_cycle_amplitude.value())
        self.settings.setValue("cycle_step_mm", self.spin_cycle_step.value())
        self.settings.setValue("cycle_count", self.spin_cycle_count.value())
        self.settings.setValue("cycle_interval_ms", self.spin_cycle_interval.value())
        self.settings.setValue("hold_target_mm", self.spin_hold_target.value())
        self.settings.setValue("hold_duration_s", self.spin_hold_duration_s.value())
        self.settings.setValue("hold_interval_ms", self.spin_hold_interval.value())
        self.settings.setValue("distribution_basis", self.combo_distribution_basis.currentData() or HSW_BASIS_STRESS_MPA)
        self.settings.setValue("distribution_start", self.spin_distribution_start.value())
        self.settings.setValue("distribution_end", self.spin_distribution_end.value())
        self.settings.setValue("distribution_step", self.spin_distribution_step.value())
        self.settings.setValue("distribution_tolerance", self.spin_distribution_tolerance.value())
        self.settings.setValue("distribution_nudge_mm", self.spin_distribution_nudge_mm.value())
        self.settings.setValue("distribution_settle_s", self.spin_distribution_settle_s.value())
        self.settings.setValue("distribution_points", self.spin_distribution_points.value())
        self.settings.setValue("distribution_interval_ms", self.spin_distribution_interval.value())
        self.settings.setValue("distribution_return_sweep", self.check_distribution_return_sweep.isChecked())
        self.settings.setValue("calibration_baseline_s", self.spin_calibration_baseline_s.value())
        self.settings.setValue("calibration_start_load_g", self.spin_calibration_start_load_g.value())
        self.settings.setValue("calibration_end_load_g", self.spin_calibration_end_load_g.value())
        self.settings.setValue("calibration_load_step_g", self.spin_calibration_load_step_g.value())
        self.settings.setValue("calibration_tolerance_g", self.spin_calibration_tolerance_g.value())
        self.settings.setValue("calibration_settle_s", self.spin_calibration_settle_s.value())
        self.settings.setValue("calibration_preload_nudge_mm", self.spin_calibration_preload_nudge_mm.value())
        self.settings.setValue("calibration_preload_speed_mm_s", self.spin_calibration_preload_speed_mm_s.value())
        self.settings.setValue("calibration_move_step_mm", self.spin_calibration_move_step_mm.value())
        self.settings.setValue("calibration_steps_per_direction", self.spin_calibration_steps_per_direction.value())
        self.settings.setValue("calibration_speed_mm_s", self.spin_calibration_speed_mm_s.value())
        self.settings.setValue("calibration_interval_ms", self.spin_calibration_interval.value())
        self.settings.setValue("calibration_defaults_version", CALIBRATION_DEFAULTS_VERSION)
        self.settings.setValue("current_sweep_basis", self._current_sweep_basis())
        self.settings.setValue("setup_preload_stress_mpa", self.spin_setup_preload_stress_mpa.value())
        self.settings.setValue("setup_preload_duration_s", self.spin_setup_preload_duration_s.value())
        self.settings.setValue("setup_preload_ramp_rate_mpa_s", self._setup_preload_ramp_rate_mpa_s())
        self.settings.setValue("setup_return_duration_s", self.spin_setup_return_duration_s.value())
        self.settings.setValue(
            "setup_slack_speed_strain_pct_s",
            self.spin_setup_slack_speed_strain_pct_s.value(),
        )
        self.settings.setValue(
            "setup_slack_step_cap_stress_mpa",
            self.spin_setup_slack_step_cap_stress_mpa.value(),
        )
        self.settings.setValue("setup_preload_tolerance_mpa", self.spin_setup_preload_tolerance_mpa.value())
        self.settings.setValue("setup_zero_tolerance_g", SERVO_AUTO_TOLERANCE_LOAD_G)
        self.settings.setValue("setup_preload_stable_s", self.spin_setup_preload_stable_s.value())
        self.settings.setValue("setup_zero_stable_s", self.spin_setup_zero_stable_s.value())
        self.settings.setValue("current_sweep_target_start", self.spin_current_sweep_target_start.value())
        self.settings.setValue("current_sweep_target_end", self.spin_current_sweep_target_end.value())
        self.settings.setValue("current_sweep_target_step", self.spin_current_sweep_target_step.value())
        self.settings.setValue("current_sweep_target_ramp_rate", self.spin_current_sweep_target_ramp_rate.value())
        self._store_current_sweep_target_values()
        for mode, values in self._current_sweep_target_values_by_mode.items():
            prefix = self._current_sweep_target_settings_prefix(mode)
            self.settings.setValue(f"{prefix}_target_start", values[0])
            self.settings.setValue(f"{prefix}_target_end", values[1])
            self.settings.setValue(f"{prefix}_target_step", values[2])
            self.settings.setValue(f"{prefix}_target_ramp_rate", values[3])
        self.settings.setValue("current_sweep_target_speed_mm_s", self.spin_current_sweep_target_speed_mm_s.value())
        self.settings.setValue(
            "current_sweep_max_correction_strain_pct",
            self.spin_current_sweep_max_correction_strain_pct.value(),
        )
        self.settings.setValue(
            "current_sweep_correction_rate_pct_s",
            self.spin_current_sweep_correction_rate_pct_s.value(),
        )
        self.settings.setValue(
            "current_sweep_max_correction_stress_mpa",
            self.spin_current_sweep_max_correction_stress_mpa.value(),
        )
        self.settings.setValue(
            "current_sweep_hold_correction_stress_mpa",
            self.spin_current_sweep_hold_correction_stress_mpa.value(),
        )
        self.settings.setValue(
            "current_sweep_mid_correction_stress_mpa",
            self.spin_current_sweep_mid_correction_stress_mpa.value(),
        )
        self.settings.setValue(
            "current_sweep_near_correction_stress_mpa",
            self.spin_current_sweep_near_correction_stress_mpa.value(),
        )
        self.settings.setValue("current_sweep_servo_defaults_version", SERVO_CURRENT_SWEEP_DEFAULTS_VERSION)
        self.settings.setValue(
            "current_sweep_return_target",
            self.check_current_sweep_return_target.isChecked(),
        )
        self.settings.setValue("current_sweep_start_mA", self.spin_current_sweep_start_mA.value())
        self.settings.setValue("current_sweep_end_mA", self.spin_current_sweep_end_mA.value())
        self.settings.setValue("current_sweep_step_mA", self.spin_current_sweep_step_mA.value())
        self.settings.setValue("current_sweep_ramp_rate_mA_s", self.spin_current_sweep_step_mA.value())
        self.settings.setValue("current_sweep_hold_on_error", self.check_current_sweep_hold_on_error.isChecked())
        self.settings.setValue("current_sweep_hold_pause_factor", self.spin_current_sweep_hold_pause_factor.value())
        self.settings.setValue("current_sweep_hold_resume_factor", self.spin_current_sweep_hold_resume_factor.value())
        self.settings.setValue(
            "current_sweep_hold_resume_stable_s",
            self.spin_current_sweep_hold_resume_stable_s.value(),
        )
        self.settings.setValue(
            "current_sweep_hold_filter_window_s",
            self.spin_current_sweep_hold_filter_window_s.value(),
        )
        self.settings.setValue(
            "current_sweep_hold_noise_sigma",
            self.spin_current_sweep_hold_noise_sigma.value(),
        )
        self.settings.setValue(
            "current_sweep_hold_min_pause_stress_mpa",
            self.spin_current_sweep_hold_min_pause_stress_mpa.value(),
        )
        self.settings.setValue(
            "current_sweep_hold_min_resume_stress_mpa",
            self.spin_current_sweep_hold_min_resume_stress_mpa.value(),
        )
        self.settings.setValue(
            "current_sweep_first_overheating",
            self.check_current_sweep_first_overheating.isChecked(),
        )
        self.settings.setValue(
            "current_sweep_first_overheating_target_mpa",
            self.spin_current_sweep_first_overheating_target_mpa.value(),
        )
        self.settings.setValue("current_sweep_reverse_current", self.check_current_sweep_reverse_current.isChecked())
        self.settings.setValue("current_sweep_tolerance", self.spin_current_sweep_tolerance.value())
        self.settings.setValue("current_sweep_nudge_mm", self.spin_current_sweep_nudge_mm.value())
        self.settings.setValue("current_sweep_balance_speed_mm_s", self.spin_current_sweep_balance_speed_mm_s.value())
        self.settings.setValue("current_sweep_max_seek_mm", self.spin_current_sweep_max_seek_mm.value())
        self.settings.setValue("current_sweep_interval_ms", self._control_interval_ms())
        self.settings.setValue("current_sweep_log_interval_ms", self._log_interval_ms())
        self.settings.setValue("constant_current_start_basis", self._constant_current_start_basis())
        self.settings.setValue("constant_current_target_start", self.spin_constant_current_start_target.value())
        self.settings.setValue("constant_current_target_end", self.spin_constant_current_end_target.value())
        self.settings.setValue("constant_current_step_basis", self._constant_current_step_basis())
        self.settings.setValue("constant_current_step_size", self.spin_constant_current_step_size.value())
        self.settings.setValue("constant_current_hold_s", self.spin_constant_current_hold_s.value())
        self.settings.setValue("constant_current_move_speed_mm_s", self.spin_constant_current_move_speed_mm_s.value())
        self.settings.setValue("constant_current_start_mA", self.spin_constant_current_start_mA.value())
        self.settings.setValue("constant_current_end_mA", self.spin_constant_current_end_mA.value())
        self.settings.setValue("constant_current_step_mA", self.spin_constant_current_step_mA.value())
        self.settings.setValue("constant_current_return_to_start", self.check_constant_current_return_to_start.isChecked())
        self._store_default_dashboard_plot_settings_if_missing()
        self._store_dashboard_plot_settings(write_settings=True)
        self.settings.sync()

    def _restore_settings(self) -> None:
        self._settings_restore_in_progress = True
        baud = self.settings.value("scale_baud", "600", type=str)
        if self.combo_scale_baud.findText(baud) >= 0:
            self.combo_scale_baud.setCurrentText(baud)
        saved_scale_interval_ms = int(self.settings.value("scale_interval_ms", DEFAULT_SCALE_REQUEST_INTERVAL_MS))
        scale_request = self.settings.value("scale_request", "\\x1bp", type=str)
        scale_terminator = self.settings.value("scale_terminator", "", type=str)
        if baud == "9600" and (not scale_request) and scale_terminator == "\\r\\n":
            baud = "600"
            self.combo_scale_baud.setCurrentText(baud)
            scale_request = "\\x1bp"
            scale_terminator = ""
        if scale_request.strip() == "\\x1bp" and saved_scale_interval_ms < DEFAULT_SCALE_REQUEST_INTERVAL_MS:
            saved_scale_interval_ms = DEFAULT_SCALE_REQUEST_INTERVAL_MS
        self.spin_scale_interval.setValue(saved_scale_interval_ms)
        self.edit_scale_request.setText(scale_request)
        self.edit_scale_terminator.setText(scale_terminator)
        supply_profile = self.settings.value("supply_profile", "hmp4030", type=str)
        supply_profile_index = self.combo_supply_profile.findData(supply_profile)
        if supply_profile_index >= 0:
            self.combo_supply_profile.setCurrentIndex(supply_profile_index)
        supply_profile_defaults = SUPPLY_PROFILES.get(str(self.combo_supply_profile.currentData() or supply_profile), SUPPLY_PROFILES["hmp4030"])
        supply_baud = self.settings.value(
            "supply_baud",
            str(int(supply_profile_defaults.get("baudrate", 9600) or 9600)),
            type=str,
        )
        if self.combo_supply_baud.findText(supply_baud) >= 0:
            self.combo_supply_baud.setCurrentText(supply_baud)
        self.edit_shared_broker_host.setText(
            self.settings.value("shared_broker_host", "127.0.0.1", type=str)
        )
        self.spin_shared_broker_port.setValue(
            int(self.settings.value("shared_broker_port", 8765, type=int))
        )
        current_sweep_channel = int(
            self.settings.value(
                "current_sweep_supply_channel",
                0,
            )
        )
        current_sweep_channel_index = self.combo_current_sweep_supply_channel.findData(current_sweep_channel)
        if current_sweep_channel_index >= 0:
            self.combo_current_sweep_supply_channel.setCurrentIndex(current_sweep_channel_index)
        self.spin_supply_voltage_limit.setValue(
            float(self.settings.value("supply_voltage_limit_v", supply_profile_defaults.get("max_voltage", SUPPLY_PROFILES["hmp4030"]["max_voltage"])))
        )
        self.spin_supply_manual_current.setValue(
            float(self.settings.value("supply_manual_current_mA", supply_profile_defaults.get("start_current_mA", 1.0)))
        )
        self.check_continuity_monitor.setChecked(
            bool(self.settings.value("continuity_monitor_enabled", True, type=bool))
        )
        self.spin_continuity_current_mA.setValue(
            float(self.settings.value("continuity_current_mA", CONTINUITY_CURRENT_DEFAULT_MA))
        )
        self.check_motor_supply_power.setChecked(bool(self.settings.value("motor_supply_enabled", False, type=bool)))
        motor_channel = int(
            self.settings.value(
                "motor_supply_channel",
                0,
            )
        )
        motor_channel_index = self.combo_motor_supply_channel.findData(motor_channel)
        if motor_channel_index >= 0:
            self.combo_motor_supply_channel.setCurrentIndex(motor_channel_index)
        self.spin_motor_supply_voltage.setValue(float(self.settings.value("motor_supply_voltage_v", 12.0)))
        self.spin_motor_supply_current_limit.setValue(
            float(self.settings.value("motor_supply_current_limit_a", DEFAULT_MOTOR_SUPPLY_CURRENT_LIMIT_A))
        )
        saved_ticcmd = self.settings.value("ticcmd_path", "ticcmd", type=str)
        discovered_ticcmd = _find_ticcmd()
        saved_ticcmd_text = saved_ticcmd.strip()
        saved_ticcmd_native = (
            saved_ticcmd_text.lower() in TIC_USB_TRANSPORT_ALIASES
            or saved_ticcmd_text.lower() == "auto"
        )
        saved_ticcmd_missing = (
            saved_ticcmd_text
            and saved_ticcmd_text.lower() != "ticcmd"
            and not saved_ticcmd_native
            and not Path(saved_ticcmd_text).exists()
        )
        if (saved_ticcmd_text.lower() == "ticcmd" or saved_ticcmd_missing) and discovered_ticcmd != "ticcmd":
            saved_ticcmd = discovered_ticcmd
        self.edit_ticcmd_path.setText(saved_ticcmd)
        self.check_tic_native_usb.setChecked(
            bool(self.settings.value("tic_native_usb_preferred", True, type=bool))
        )
        self.edit_tic_serial.setText(self.settings.value("tic_serial", "", type=str))
        self.spin_tic_current_limit_mA.setValue(
            int(float(self.settings.value("tic_current_limit_mA", DEFAULT_TIC_CURRENT_LIMIT_MA)))
        )
        self.spin_tic_status_interval.setValue(
            int(self.settings.value("tic_status_interval_ms", DEFAULT_TIC_STATUS_INTERVAL_MS))
        )
        self.spin_tic_keepalive_interval.setValue(
            int(self.settings.value("tic_keepalive_interval_ms", TIC_KEEPALIVE_INTERVAL_MS))
        )
        self._apply_hardware_timer_intervals()
        motor_defaults_version = int(self.settings.value("motor_defaults_version", 0))
        saved_step_mode = self.settings.value("tic_step_mode", DEFAULT_TIC_STEP_MODE, type=str)
        if not self._set_tic_step_mode_combo(saved_step_mode):
            self._set_tic_step_mode_combo(DEFAULT_TIC_STEP_MODE)
        saved_steps_per_mm = float(self.settings.value("steps_per_mm", DEFAULT_STEPS_PER_MM))
        if (
            motor_defaults_version < MOTOR_DEFAULTS_VERSION
            and math.isclose(saved_steps_per_mm, 100.0, rel_tol=1e-9, abs_tol=1e-9)
        ):
            saved_steps_per_mm = DEFAULT_STEPS_PER_MM
        saved_full_steps_value = self.settings.value("full_steps_per_mm", None)
        if saved_full_steps_value is None:
            factor = tic_step_mode_factor(self._selected_tic_step_mode()) or tic_step_mode_factor(DEFAULT_TIC_STEP_MODE) or 1
            saved_full_steps_per_mm = saved_steps_per_mm / float(factor)
        else:
            saved_full_steps_per_mm = float(saved_full_steps_value)
        self.spin_full_steps_per_mm.setValue(max(0.001, saved_full_steps_per_mm))
        self._sync_tic_units_per_mm_from_full_steps(persist=False)
        self.spin_motor_step_calibration_increment_steps.setValue(
            max(
                1,
                int(
                    self.settings.value(
                        "motor_step_calibration_increment_steps",
                        MOTOR_STEP_CALIBRATION_DEFAULT_INCREMENT_STEPS,
                    )
                ),
            )
        )
        self.spin_motor_step_calibration_moves.setValue(
            max(
                1,
                int(
                    self.settings.value(
                        "motor_step_calibration_moves",
                        MOTOR_STEP_CALIBRATION_DEFAULT_MOVES,
                    )
                ),
            )
        )
        self.spin_motor_step_calibration_speed_mm_s.setValue(
            max(
                0.0001,
                float(
                    self.settings.value(
                        "motor_step_calibration_speed_mm_s",
                        MOTOR_STEP_CALIBRATION_DEFAULT_SPEED_MM_S,
                    )
                ),
            )
        )
        self.spin_jog_mm.setValue(max(0.01, float(self.settings.value("jog_mm", 0.1))))
        self.spin_motion_speed_mm_s.setValue(
            max(
                0.001,
                float(
                    self.settings.value(
                        "manual_motion_speed_mm_s",
                        self.settings.value("motion_speed_mm_s", 1.0),
                    )
                ),
            )
        )
        self.spin_ramp_speed_mm_s.setValue(max(0.001, float(self.settings.value("ramp_speed_mm_s", 1.0))))
        self.spin_cycle_speed_mm_s.setValue(max(0.001, float(self.settings.value("cycle_speed_mm_s", 1.0))))
        self.spin_hold_speed_mm_s.setValue(max(0.001, float(self.settings.value("hold_speed_mm_s", 1.0))))
        self.spin_distribution_seek_speed_mm_s.setValue(
            max(0.001, float(self.settings.value("distribution_seek_speed_mm_s", 0.1)))
        )
        self.check_soft_limits.setChecked(bool(self.settings.value("soft_limits_enabled", False, type=bool)))
        self.spin_soft_min_mm.setValue(float(self.settings.value("soft_limit_min_mm", -5.0)))
        self.spin_soft_max_mm.setValue(float(self.settings.value("soft_limit_max_mm", 5.0)))
        self.check_max_load.setChecked(bool(self.settings.value("max_load_enabled", False, type=bool)))
        self.spin_max_load_g.setValue(float(self.settings.value("max_load_g", DEFAULT_ZERO_LOAD_SCALE_G)))
        self.spin_raw_scale_limit_g.setValue(
            float(self.settings.value("raw_scale_display_limit_g", RAW_SCALE_DISPLAY_LIMIT_DEFAULT_G))
        )
        self.spin_zero_load_scale_g.setValue(
            float(self.settings.value("zero_load_scale_g", DEFAULT_ZERO_LOAD_SCALE_G))
        )
        self.check_tension_load_positive.setChecked(
            bool(self.settings.value("negative_scale_is_tension", True, type=bool))
        )
        self._refresh_scale_reference_cache()
        if not bool(self.settings.value("negative_tic_motion_default_applied", False, type=bool)):
            motion_positive_is_tension = False
            self.settings.setValue("positive_motion_is_tension", False)
            self.settings.setValue("negative_tic_motion_default_applied", True)
        else:
            motion_positive_is_tension = bool(
                self.settings.value("positive_motion_is_tension", False, type=bool)
            )
        self.check_positive_motion_is_tension.setChecked(motion_positive_is_tension)
        self.spin_backlash_mm.setValue(float(self.settings.value("backlash_mm", 0.02)))
        self.spin_initial_length.setValue(float(self.settings.value("initial_length_mm", 30.0)))
        self._calibrated_stiffness_g_per_mm = _safe_float(
            self.settings.value("calibration_stiffness_g_per_mm", None)
        )
        self._calibrated_stiffness_length_mm = _safe_float(
            self.settings.value("calibration_stiffness_length_mm", None)
        )
        self._calibrated_load_noise_g = _safe_float(
            self.settings.value("calibration_load_noise_g", None)
        )
        self.spin_diameter.setValue(float(self.settings.value("diameter_mm", 0.03)))
        self._mark_diameter_imported(False)
        self.check_zero_on_preload.setChecked(False)
        self.spin_preload_threshold_g.setValue(0.0)
        self.edit_name_composition.setText(self.settings.value("name_composition", "", type=str))
        saved_wire = self.settings.value("name_wire", "", type=str)
        self.edit_name_wire.setText(MicrowireLineEdit.to_display_text(saved_wire) or saved_wire)
        self.edit_name_specimen.setText(self.settings.value("name_specimen", "", type=str))
        self.edit_name_condition.setText(self.settings.value("name_condition", "", type=str))
        self.edit_sample_name.setText(self.settings.value("sample_name", "", type=str))
        self.edit_run_notes.setPlainText(self.settings.value("run_notes", "", type=str))
        builder_project_path = self.settings.value("builder_project_path", "", type=str)
        self.edit_project_path.setText(builder_project_path)
        self._builder_project_path = Path(builder_project_path) if builder_project_path else None
        restored_log_dir = self.settings.value("log_dir", self.edit_log_dir.text(), type=str)
        self._restored_log_dir = restored_log_dir
        if self._provided_log_dir:
            self.edit_log_dir.setText(self._provided_log_dir)
        else:
            self.edit_log_dir.setText(restored_log_dir)
        saved_log_name = self.settings.value("log_name", DEFAULT_LOG_BASENAME, type=str)
        cleaned_log_name = _clean_session_basename(saved_log_name)
        self.edit_log_name.setText(cleaned_log_name)
        if cleaned_log_name != (saved_log_name or "").strip():
            self.settings.setValue("log_name", cleaned_log_name)
        self._auto_import_builder_project_if_possible(update_identity=False, quiet=True)
        self.check_zero_position_on_start.setChecked(False)
        self.check_tare_on_start.setChecked(
            bool(self.settings.value("tare_on_start", False, type=bool))
        )
        recipe_mode = self.settings.value("recipe_mode", "ramp", type=str)
        if recipe_mode == LEGACY_CURRENT_SWEEP:
            saved_basis = self.settings.value("current_sweep_basis", HSW_BASIS_LOAD_G, type=str)
            recipe_mode = self._current_sweep_mode_for_basis(saved_basis)
        elif recipe_mode == CALIBRATION_COPPER:
            recipe_mode = CALIBRATION
        recipe_index = self.combo_recipe_mode.findData(recipe_mode)
        if recipe_index >= 0:
            self.combo_recipe_mode.setCurrentIndex(recipe_index)
        self.check_return_to_origin.setChecked(
            bool(self.settings.value("return_to_origin", True, type=bool))
        )
        self.spin_control_interval.setValue(
            int(
                self.settings.value(
                    "control_interval_ms",
                    self.settings.value("current_sweep_interval_ms", DEFAULT_CONTROL_INTERVAL_MS),
                )
            )
        )
        self.spin_log_interval.setValue(
            int(
                self.settings.value(
                    "log_interval_ms",
                    self.settings.value("current_sweep_log_interval_ms", DEFAULT_LOG_INTERVAL_MS),
                )
            )
        )
        self.spin_ui_interval.setValue(int(self.settings.value("ui_refresh_interval_ms", DEFAULT_UI_REFRESH_INTERVAL_MS)))
        saved_graph_interval_ms = int(
            self.settings.value("graph_refresh_interval_ms", DEFAULT_GRAPH_REFRESH_INTERVAL_MS)
        )
        if saved_graph_interval_ms == 1000:
            saved_graph_interval_ms = DEFAULT_GRAPH_REFRESH_INTERVAL_MS
        self.spin_graph_interval.setValue(saved_graph_interval_ms)
        self._apply_ui_refresh_interval()
        self.spin_supply_read_interval.setValue(
            int(self.settings.value("supply_read_interval_ms", DEFAULT_SUPPLY_READ_INTERVAL_MS))
        )
        self._apply_hardware_timer_intervals()
        self._run_log_mirror_path = Path(
            self.settings.value("developer_run_log_mirror_path", str(DEFAULT_RUN_LOG_MIRROR_PATH), type=str)
        )
        self._run_log_mirror_enabled = bool(
            self.settings.value("developer_run_log_mirror_enabled", False, type=bool)
        )
        if hasattr(self, "action_mirror_run_log") and self.action_mirror_run_log is not None:
            self.action_mirror_run_log.blockSignals(True)
            self.action_mirror_run_log.setChecked(self._run_log_mirror_enabled)
            self.action_mirror_run_log.blockSignals(False)
        self.spin_ramp_distance.setValue(float(self.settings.value("ramp_distance_mm", 1.0)))
        self.spin_ramp_step.setValue(float(self.settings.value("ramp_step_mm", 0.1)))
        self.spin_ramp_interval.setValue(int(self.settings.value("ramp_interval_ms", 1000)))
        self.spin_cycle_amplitude.setValue(float(self.settings.value("cycle_amplitude_mm", 1.0)))
        self.spin_cycle_step.setValue(float(self.settings.value("cycle_step_mm", 0.1)))
        self.spin_cycle_count.setValue(int(self.settings.value("cycle_count", 3)))
        self.spin_cycle_interval.setValue(int(self.settings.value("cycle_interval_ms", 1000)))
        self.spin_hold_target.setValue(float(self.settings.value("hold_target_mm", 0.5)))
        self.spin_hold_duration_s.setValue(float(self.settings.value("hold_duration_s", 10.0)))
        self.spin_hold_interval.setValue(int(self.settings.value("hold_interval_ms", 1000)))
        distribution_basis = self.settings.value("distribution_basis", HSW_BASIS_STRESS_MPA, type=str)
        distribution_basis_index = self.combo_distribution_basis.findData(distribution_basis)
        if distribution_basis_index >= 0:
            self.combo_distribution_basis.setCurrentIndex(distribution_basis_index)
        self.spin_distribution_start.setValue(float(self.settings.value("distribution_start", 10.0)))
        self.spin_distribution_end.setValue(float(self.settings.value("distribution_end", 100.0)))
        self.spin_distribution_step.setValue(float(self.settings.value("distribution_step", 10.0)))
        self.spin_distribution_tolerance.setValue(float(self.settings.value("distribution_tolerance", 0.5)))
        self.spin_distribution_nudge_mm.setValue(float(self.settings.value("distribution_nudge_mm", 0.01)))
        self.spin_distribution_settle_s.setValue(float(self.settings.value("distribution_settle_s", 1.0)))
        self.spin_distribution_points.setValue(int(self.settings.value("distribution_points", 10000)))
        self.spin_distribution_interval.setValue(int(self.settings.value("distribution_interval_ms", 100)))
        self.check_distribution_return_sweep.setChecked(
            bool(self.settings.value("distribution_return_sweep", True, type=bool))
        )
        calibration_defaults_version = int(self.settings.value("calibration_defaults_version", 0) or 0)

        def _calibration_setting_float(
            key: str,
            *,
            old_default: float | Sequence[float],
            new_default: float,
        ) -> float:
            raw = self.settings.value(key, None)
            if raw is None:
                return new_default
            value = float(raw)
            old_defaults = (old_default,) if isinstance(old_default, (int, float)) else tuple(old_default)
            if calibration_defaults_version < CALIBRATION_DEFAULTS_VERSION and any(
                math.isclose(value, float(default), rel_tol=1e-9, abs_tol=1e-9) for default in old_defaults
            ):
                return new_default
            return value

        self.spin_calibration_baseline_s.setValue(
            max(0.1, _calibration_setting_float("calibration_baseline_s", old_default=10.0, new_default=3.0))
        )
        self.spin_calibration_start_load_g.setValue(
            max(0.001, _calibration_setting_float("calibration_start_load_g", old_default=(1.0, 5.0), new_default=0.25))
        )
        self.spin_calibration_end_load_g.setValue(
            max(0.001, _calibration_setting_float("calibration_end_load_g", old_default=(5.0, 10.0), new_default=1.0))
        )
        self.spin_calibration_load_step_g.setValue(
            max(0.001, _calibration_setting_float("calibration_load_step_g", old_default=1.0, new_default=0.25))
        )
        self.spin_calibration_tolerance_g.setValue(
            max(
                0.0001,
                _calibration_setting_float(
                    "calibration_tolerance_g",
                    old_default=(0.1, 0.02),
                    new_default=SERVO_AUTO_TOLERANCE_LOAD_G,
                ),
            )
        )
        self.spin_calibration_settle_s.setValue(
            max(0.0, _calibration_setting_float("calibration_settle_s", old_default=0.5, new_default=0.25))
        )
        self.spin_calibration_preload_nudge_mm.setValue(
            max(
                0.0001,
                _calibration_setting_float(
                    "calibration_preload_nudge_mm",
                    old_default=0.1,
                    new_default=0.01,
                ),
            )
        )
        self.spin_calibration_preload_speed_mm_s.setValue(
            max(
                0.001,
                _calibration_setting_float(
                    "calibration_preload_speed_mm_s",
                    old_default=1.0,
                    new_default=0.2,
                ),
            )
        )
        self.spin_calibration_move_step_mm.setValue(
            max(0.0001, float(self.settings.value("calibration_move_step_mm", 0.01)))
        )
        self.spin_calibration_steps_per_direction.setValue(
            max(1, int(self.settings.value("calibration_steps_per_direction", 5)))
        )
        self.spin_calibration_speed_mm_s.setValue(
            max(0.001, _calibration_setting_float("calibration_speed_mm_s", old_default=0.2, new_default=0.05))
        )
        self.spin_calibration_interval.setValue(int(self.settings.value("calibration_interval_ms", 250)))
        current_sweep_basis = self.settings.value("current_sweep_basis", HSW_BASIS_LOAD_G, type=str)
        current_sweep_basis_index = self.combo_current_sweep_basis.findData(current_sweep_basis)
        if current_sweep_basis_index >= 0:
            self.combo_current_sweep_basis.setCurrentIndex(current_sweep_basis_index)
        self.spin_setup_preload_stress_mpa.setValue(
            max(0.001, float(self.settings.value("setup_preload_stress_mpa", 10.0)))
        )
        saved_setup_duration = self.settings.value("setup_preload_duration_s", None)
        if saved_setup_duration is None:
            saved_setup_rate = float(self.settings.value("setup_preload_ramp_rate_mpa_s", 1.0))
            saved_setup_duration = float(self.spin_setup_preload_stress_mpa.value()) / max(0.001, saved_setup_rate)
        self.spin_setup_preload_duration_s.setValue(
            max(0.1, float(saved_setup_duration))
        )
        self.spin_setup_return_duration_s.setValue(
            max(
                0.1,
                float(self.settings.value("setup_return_duration_s", SETUP_RETURN_DEFAULT_DURATION_S)),
            )
        )
        self.spin_setup_slack_speed_strain_pct_s.setValue(
            max(
                0.001,
                float(
                    self.settings.value(
                        "setup_slack_speed_strain_pct_s",
                        SETUP_SLACK_DEFAULT_STRAIN_RATE_PCT_S,
                    )
                ),
            )
        )
        self.spin_setup_slack_step_cap_stress_mpa.setValue(
            max(
                0.001,
                float(
                    self.settings.value(
                        "setup_slack_step_cap_stress_mpa",
                        SETUP_PRELOAD_MAX_SLACK_STEP_STRESS_MPA,
                    )
                ),
            )
        )
        self.spin_setup_preload_tolerance_mpa.setValue(
            max(0.0001, float(self.settings.value("setup_preload_tolerance_mpa", 0.25)))
        )
        self.spin_setup_zero_tolerance_g.setValue(SERVO_AUTO_TOLERANCE_LOAD_G)
        saved_setup_zero_stable_s = max(0.0, float(self.settings.value("setup_zero_stable_s", 1.0)))
        self.spin_setup_preload_stable_s.setValue(
            max(0.0, float(self.settings.value("setup_preload_stable_s", saved_setup_zero_stable_s)))
        )
        self.spin_setup_zero_stable_s.setValue(
            saved_setup_zero_stable_s
        )
        self._apply_current_sweep_target_values(recipe_mode, allow_legacy_settings=True)
        self._last_recipe_mode = recipe_mode
        current_sweep_servo_defaults_version = int(
            self.settings.value("current_sweep_servo_defaults_version", 0)
        )
        saved_current_sweep_target_speed = float(
            self.settings.value(
                "current_sweep_target_speed_mm_s",
                SERVO_CURRENT_SWEEP_MAX_STAGE_SPEED_MM_S,
            )
        )
        if (
            current_sweep_servo_defaults_version < SERVO_CURRENT_SWEEP_DEFAULTS_VERSION
            and math.isclose(saved_current_sweep_target_speed, 1.0, rel_tol=1e-9, abs_tol=1e-9)
        ):
            saved_current_sweep_target_speed = SERVO_CURRENT_SWEEP_MAX_STAGE_SPEED_MM_S
        self.spin_current_sweep_target_speed_mm_s.setValue(
            max(0.001, saved_current_sweep_target_speed)
        )
        self.spin_current_sweep_max_correction_strain_pct.setValue(
            max(
                0.001,
                float(
                    self.settings.value(
                        "current_sweep_max_correction_strain_pct",
                        SERVO_CURRENT_SWEEP_MAX_CORRECTION_STRAIN_PCT,
                    )
                ),
            )
        )
        self.spin_current_sweep_correction_rate_pct_s.setValue(
            max(
                0.001,
                float(
                    self.settings.value(
                        "current_sweep_correction_rate_pct_s",
                        SERVO_CURRENT_SWEEP_MAX_CORRECTION_RATE_PCT_S,
                    )
                ),
            )
        )
        self.spin_current_sweep_max_correction_stress_mpa.setValue(
            max(
                0.001,
                float(
                    self.settings.value(
                        "current_sweep_max_correction_stress_mpa",
                        SERVO_CURRENT_SWEEP_MAX_CORRECTION_STRESS_MPA,
                    )
                ),
            )
        )
        saved_current_sweep_hold_cap = float(
            self.settings.value(
                "current_sweep_hold_correction_stress_mpa",
                SERVO_CURRENT_SWEEP_HOLD_MAX_CORRECTION_STRESS_MPA,
            )
        )
        if (
            current_sweep_servo_defaults_version < SERVO_CURRENT_SWEEP_DEFAULTS_VERSION
            and (
                math.isclose(saved_current_sweep_hold_cap, 20.0, rel_tol=1e-9, abs_tol=1e-9)
                or saved_current_sweep_hold_cap > SERVO_CURRENT_SWEEP_HOLD_MAX_CORRECTION_STRESS_MPA
            )
        ):
            saved_current_sweep_hold_cap = SERVO_CURRENT_SWEEP_HOLD_MAX_CORRECTION_STRESS_MPA
        self.spin_current_sweep_hold_correction_stress_mpa.setValue(
            max(0.001, saved_current_sweep_hold_cap)
        )
        self.spin_current_sweep_mid_correction_stress_mpa.setValue(
            max(
                0.001,
                float(
                    self.settings.value(
                        "current_sweep_mid_correction_stress_mpa",
                        SERVO_CURRENT_SWEEP_MID_CORRECTION_STRESS_MPA,
                    )
                ),
            )
        )
        self.spin_current_sweep_near_correction_stress_mpa.setValue(
            max(
                0.001,
                float(
                    self.settings.value(
                        "current_sweep_near_correction_stress_mpa",
                        SERVO_CURRENT_SWEEP_NEAR_CORRECTION_STRESS_MPA,
                    )
                ),
            )
        )
        self.check_current_sweep_return_target.setChecked(
            bool(self.settings.value("current_sweep_return_target", True, type=bool))
        )
        saved_current_start_mA = float(self.settings.value("current_sweep_start_mA", 1.0))
        self.spin_current_sweep_start_mA.setValue(saved_current_start_mA)
        self.spin_current_sweep_end_mA.setValue(float(self.settings.value("current_sweep_end_mA", 3.0)))
        self.spin_current_sweep_step_mA.setValue(
            float(
                self.settings.value(
                    "current_sweep_ramp_rate_mA_s",
                    self.settings.value("current_sweep_step_mA", 1.0),
                )
            )
        )
        self.check_current_sweep_hold_on_error.setChecked(
            bool(self.settings.value("current_sweep_hold_on_error", False, type=bool))
        )
        self.spin_current_sweep_hold_pause_factor.setValue(
            max(
                1.0,
                float(
                    self.settings.value(
                        "current_sweep_hold_pause_factor",
                        CURRENT_SWEEP_HOLD_PAUSE_TOLERANCE_FACTOR,
                    )
                ),
            )
        )
        self.spin_current_sweep_hold_resume_factor.setValue(
            max(
                0.1,
                float(
                    self.settings.value(
                        "current_sweep_hold_resume_factor",
                        CURRENT_SWEEP_HOLD_RESUME_TOLERANCE_FACTOR,
                    )
                ),
            )
        )
        self.spin_current_sweep_hold_resume_stable_s.setValue(
            max(
                0.0,
                float(
                    self.settings.value(
                        "current_sweep_hold_resume_stable_s",
                        CURRENT_SWEEP_HOLD_RESUME_STABLE_S,
                    )
                ),
            )
        )
        self.spin_current_sweep_hold_filter_window_s.setValue(
            max(
                0.1,
                float(
                    self.settings.value(
                        "current_sweep_hold_filter_window_s",
                        SERVO_CURRENT_SWEEP_HOLD_FILTER_WINDOW_S,
                    )
                ),
            )
        )
        self.spin_current_sweep_hold_noise_sigma.setValue(
            max(
                0.0,
                float(
                    self.settings.value(
                        "current_sweep_hold_noise_sigma",
                        SERVO_CURRENT_SWEEP_HOLD_NOISE_SIGMA,
                    )
                ),
            )
        )
        self.spin_current_sweep_hold_min_pause_stress_mpa.setValue(
            max(
                0.0,
                float(
                    self.settings.value(
                        "current_sweep_hold_min_pause_stress_mpa",
                        SERVO_CURRENT_SWEEP_HOLD_MIN_PAUSE_STRESS_MPA,
                    )
                ),
            )
        )
        self.spin_current_sweep_hold_min_resume_stress_mpa.setValue(
            max(
                0.0,
                float(
                    self.settings.value(
                        "current_sweep_hold_min_resume_stress_mpa",
                        SERVO_CURRENT_SWEEP_HOLD_MIN_RESUME_STRESS_MPA,
                    )
                ),
            )
        )
        self.check_current_sweep_first_overheating.setChecked(
            bool(self.settings.value("current_sweep_first_overheating", False, type=bool))
        )
        self.spin_current_sweep_first_overheating_target_mpa.setValue(
            max(
                0.001,
                float(self.settings.value("current_sweep_first_overheating_target_mpa", 20.0)),
            )
        )
        self.check_current_sweep_reverse_current.setChecked(
            bool(self.settings.value("current_sweep_reverse_current", True, type=bool))
        )
        self.spin_current_sweep_tolerance.setValue(float(self.settings.value("current_sweep_tolerance", 0.25)))
        self.spin_current_sweep_nudge_mm.setValue(float(self.settings.value("current_sweep_nudge_mm", 0.1)))
        self.spin_current_sweep_balance_speed_mm_s.setValue(
            max(0.001, float(self.settings.value("current_sweep_balance_speed_mm_s", 0.05)))
        )
        self.spin_current_sweep_max_seek_mm.setValue(
            max(0.01, float(self.settings.value("current_sweep_max_seek_mm", 3.0)))
        )
        self.spin_current_sweep_interval.setValue(int(self.settings.value("current_sweep_interval_ms", 250)))
        self.spin_current_sweep_log_interval.setValue(int(self.settings.value("current_sweep_log_interval_ms", 500)))
        self._update_current_sweep_basis_ui()
        constant_basis = self.settings.value("constant_current_start_basis", HSW_BASIS_STRESS_MPA, type=str)
        constant_basis_index = self.combo_constant_current_start_basis.findData(constant_basis)
        if constant_basis_index >= 0:
            self.combo_constant_current_start_basis.setCurrentIndex(constant_basis_index)
        constant_step_basis = self.settings.value("constant_current_step_basis", MECHANICAL_STEP_DISPLACEMENT_MM, type=str)
        constant_step_basis_index = self.combo_constant_current_step_basis.findData(constant_step_basis)
        if constant_step_basis_index >= 0:
            self.combo_constant_current_step_basis.setCurrentIndex(constant_step_basis_index)
        self.spin_constant_current_start_target.setValue(float(self.settings.value("constant_current_target_start", 0.0)))
        self.spin_constant_current_end_target.setValue(float(self.settings.value("constant_current_target_end", 500.0)))
        self.spin_constant_current_step_size.setValue(float(self.settings.value("constant_current_step_size", 0.01)))
        self.spin_constant_current_hold_s.setValue(float(self.settings.value("constant_current_hold_s", 1.0)))
        self.spin_constant_current_move_speed_mm_s.setValue(
            max(0.001, float(self.settings.value("constant_current_move_speed_mm_s", 0.05)))
        )
        self.spin_constant_current_start_mA.setValue(float(self.settings.value("constant_current_start_mA", 0.0)))
        self.spin_constant_current_end_mA.setValue(float(self.settings.value("constant_current_end_mA", 100.0)))
        self.spin_constant_current_step_mA.setValue(
            max(0.01, float(self.settings.value("constant_current_step_mA", 10.0)))
        )
        self.check_constant_current_return_to_start.setChecked(
            bool(self.settings.value("constant_current_return_to_start", True, type=bool))
        )
        self._update_constant_current_basis_ui()
        self._apply_dashboard_plot_settings(str(self.combo_recipe_mode.currentData() or "ramp"))
        show_recipe_controls = bool(self.settings.value("show_recipe_file_controls", False, type=bool))
        self._set_recipe_file_controls_visible(show_recipe_controls)
        self._sync_auto_name_fields()
        self._update_recipe_mode_ui()
        self._settings_restore_in_progress = False
        self._settings_persistence_ready = True

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self._window_closing = True
        self._close_transient_child_windows()
        if self._persist_settings:
            self._save_settings()
        self._stop_tic_keepalive()
        self._stop_auto_ramp(
            log_completion=False,
            stop_reason="app_closed",
            stop_detail="Application window closed while automation was active.",
        )
        self._stop_tic_dispatcher()
        self._disconnect_scale()
        self._stop_session(reason="app_closed", detail="Application window closed while session was active.")
        self._release_experiment_sleep_guard()
        self._disconnect_supply()
        self._stop_owned_shared_broker()
        super().closeEvent(event)


def main(log_dir: str | None = None, *, persist_settings: bool = True) -> QtWidgets.QWidget:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if not isinstance(app, QtWidgets.QApplication):
        app = QtWidgets.QApplication(sys.argv)
        owns_app = True

    ensure_app_theme(app)
    window = MainWindow(log_dir, persist_settings=persist_settings)
    window.showMaximized()
    WINDOWS.append(window)

    if owns_app:
        sys.exit(app.exec())
    return window


if __name__ == "__main__":
    main()
