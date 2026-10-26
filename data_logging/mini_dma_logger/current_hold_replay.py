"""Offline adapter for the application's real scale filter and ramp-hold methods.

No MainWindow, QApplication, settings, worker, or hardware driver is constructed.
Only the explicit method allowlist below is bound, with an instance-local clock.
This tests the hold subsystem, not the entire recipe runner or safety watchdog.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from threading import RLock
from types import FunctionType, MethodType

from . import mini_dma_logger as app


PRODUCTION_METHODS = (
    "_scale_control_signal_for_basis", "_current_sweep_hold_filter_window_s",
    "_current_sweep_hold_noise_sigma", "_current_sweep_hold_min_pause_stress_mpa",
    "_current_sweep_hold_min_resume_stress_mpa", "_scale_readability_g",
    "_force_control_profile", "_using_kern_kcp_scale", "_scale_quantization_band_for_basis",
    "_current_sweep_hold_setting", "_current_sweep_hold_pause_factor",
    "_current_sweep_hold_resume_factor", "_current_sweep_hold_resume_stable_s",
    "_auto_requested_tolerance_for_basis", "_automation_tolerance_for_step",
    "_current_distribution_value", "_current_sweep_hold_filtered_value_and_noise",
    "_current_sweep_target_error_and_tolerance", "_seek_error_key",
    "_seek_target_acceptance_tolerance", "_basis_sensitivity_per_mm", "_basis_noise_floor",
    "_uses_current_sweep_force_control", "_is_current_sweep_mode", "_is_elastocaloric_mode",
    "_control_interval_ms", "_current_sweep_hold_noise_independent_pause_band",
    "_current_sweep_bounded_noise_band", "_current_sweep_hold_noise_cap_for_basis",
    "_current_sweep_hold_entry_band_for_basis", "_current_sweep_hold_entry_confirmed",
    "_reset_current_sweep_ramp_hold_candidate", "_current_sweep_hold_min_band_for_basis",
    "_current_sweep_hold_min_slope_for_basis", "_current_sweep_hold_earned_resume_band",
    "_kosice_current_sweep_error_bands", "_update_kosice_current_sweep_ramp_hold",
    "_update_current_sweep_ramp_hold", "_current_sweep_hold_cycle_center_state",
    "_maybe_resume_current_sweep_ramp_from_cycle_center", "_clear_current_sweep_ramp_hold",
    "_mark_current_sweep_ramp_hold_scale_start", "_resume_current_sweep_ramp_from_hold",
    "_apply_current_sweep_post_hold_ramp_throttle", "_has_fresh_scale_reading",
    "_scale_reading_age_s", "_motion_feedback_ready_after_s",
    "_motion_feedback_ready_after_monotonic_s", "_latest_scale_sample_time_s",
    "_log_waiting_for_feedback",
)


@dataclass(frozen=True)
class ReplayConfig:
    diameter_mm: float = 0.0191
    initial_length_mm: float = 50.0
    force_control_profile: app.ForceControlProfile = app.ForceControlProfile.PRAGUE_LEGACY
    scale_readability_g: float = 0.005
    scale_interval_ms: int = 250
    control_interval_ms: int = 50
    current_sweep_hold_filter_window_s: float = 1.8
    current_sweep_hold_noise_sigma: float = 3.0
    current_sweep_hold_min_pause_stress_mpa: float = 2.0
    current_sweep_hold_min_resume_stress_mpa: float = 1.0
    current_sweep_hold_pause_factor: float = 3.0
    current_sweep_hold_resume_factor: float = 1.5
    current_sweep_hold_resume_stable_s: float = 0.5

    def __post_init__(self):
        positive = (self.diameter_mm, self.initial_length_mm, self.scale_interval_ms,
                    self.control_interval_ms, self.current_sweep_hold_filter_window_s)
        nonnegative = (self.scale_readability_g, self.current_sweep_hold_noise_sigma,
                       self.current_sweep_hold_min_pause_stress_mpa,
                       self.current_sweep_hold_min_resume_stress_mpa,
                       self.current_sweep_hold_pause_factor, self.current_sweep_hold_resume_factor,
                       self.current_sweep_hold_resume_stable_s)
        if any(not math.isfinite(x) or x <= 0 for x in positive):
            raise ValueError("geometry, intervals and filter window must be finite and positive")
        if any(not math.isfinite(x) or x < 0 for x in nonnegative):
            raise ValueError("hold settings must be finite and nonnegative")


@dataclass
class VirtualClock:
    now_s: float = 0.0

    def advance_to(self, value: float):
        if not math.isfinite(value) or value < self.now_s:
            raise ValueError(f"replay clock must be finite and nondecreasing: {self.now_s} -> {value}")
        self.now_s = value

    def time(self):
        return self.now_s

    def monotonic(self):
        return self.now_s


@dataclass
class FakeSupply:
    current_mA: float = 1.0
    commands: list[tuple[float, float]] = field(default_factory=list)

    def set_current(self, now_s: float, current_mA: float):
        if not math.isfinite(current_mA) or current_mA < 0:
            raise ValueError("invalid simulated current")
        self.current_mA = current_mA
        self.commands.append((now_s, current_mA))


class CurrentHoldReplay:
    """Plain Python host; missing production dependencies fail closed.

    Function code objects/defaults are shared with MainWindow. A copied globals
    mapping changes ONLY `time`, avoiding a process-global clock monkeypatch.
    Calibrated applied loads are inputs: tare/sign conversion is not replayed.
    """

    def __init__(self, config: ReplayConfig = ReplayConfig()):
        self.config = config
        self.clock = VirtualClock()
        self.supply = FakeSupply()
        self.logs = []
        self.trace_events = []
        globals_map = dict(vars(app), time=self.clock)
        for name in PRODUCTION_METHODS:
            original = getattr(app.MainWindow, name)
            function = FunctionType(original.__code__, globals_map, name,
                                    original.__defaults__, original.__closure__)
            function.__kwdefaults__ = original.__kwdefaults__
            setattr(self, name, MethodType(function, self))
        self._scale_signal_buffer = app.ScaleSignalBuffer(window_s=max(
            60.0, config.current_sweep_hold_filter_window_s))
        self._scale_state_lock = RLock()
        self._latest_scale_timestamp = None
        self._latest_scale_arrival_monotonic_s = None
        self._seek_live_stiffness_by_key = {}
        self._seek_live_stiffness_g_per_mm = None
        self._calibrated_load_noise_g = None  # Explicit unknown, not estimated from future rows.
        self._automation_name = app.CURRENT_SWEEP_STRESS
        self._automation_phase = "current"
        self._automation_step_note = ""
        self._automation_plateau_index = None
        self._current_hold_bypass_active = False
        self._current_sweep_endpoint_seek_accepted_step_index = None
        self._current_sweep_cycle_center_motor_suppression_enabled = True
        self._current_sweep_cycle_center_resume_enabled = True
        self._pending_motion_command = None
        self._kosice_active_motion_target_steps = None
        self._last_motion_command_time_s = None
        self._last_motion_expected_complete_time_s = None
        self._last_motion_command_monotonic_s = None
        self._last_motion_expected_complete_monotonic_s = None
        self._last_feedback_wait_log_s = -math.inf
        self._clear_current_sweep_ramp_hold()
        self.start_step(target_mpa=50.0, now_s=0.0)

    def _control_config(self):
        return self.config

    def _is_ui_thread(self):
        return False

    def _stored_calibration_stiffness_g_per_mm(self):
        return None

    def _current_effective_load_g(self):
        sample = self._scale_signal_buffer.latest()
        return 0.0 if sample is None else sample.applied_load_g

    def _log(self, message):
        self.logs.append(message)

    def _write_control_trace(self, **event):
        self.trace_events.append(dict(now_s=self.clock.now_s, **event))

    def start_step(self, *, target_mpa: float, now_s: float, start_mA=1.0,
                   end_mA=80.0, rate_mA_s=1.0, hold_enabled=True):
        if not all(math.isfinite(x) for x in (target_mpa, start_mA, end_mA, rate_mA_s)):
            raise ValueError("step parameters must be finite")
        if min(start_mA, end_mA) < 0 or rate_mA_s <= 0:
            raise ValueError("invalid current ramp")
        self.clock.advance_to(now_s)
        self.step = app.AutomationStep(
            action="sweep_current", basis=app.HSW_BASIS_STRESS_MPA,
            target_value=target_mpa, current_start_mA=start_mA, current_end_mA=end_mA,
            current_ramp_rate_mA_s=rate_mA_s, current_hold_enabled=hold_enabled)
        self._clear_current_sweep_ramp_hold()
        self._active_current_sweep_started_s = now_s
        self._active_current_sweep_last_schedule_update_s = now_s
        self._active_current_sweep_last_setpoint_mA = start_mA
        self._active_current_sweep_display_direction = 1.0 if end_mA >= start_mA else -1.0
        self._current_sweep_post_hold_throttle_until_s = 0.0
        self._current_sweep_voltage_limit_step_index = None
        self._current_sweep_voltage_limit_started_s = None
        self.supply.set_current(now_s, start_mA)

    def deliver_scale(self, *, sample_s: float, arrival_s: float, applied_load_g: float):
        if (not math.isfinite(sample_s) or sample_s < 0 or sample_s > arrival_s
                or not math.isfinite(applied_load_g)):
            raise ValueError("invalid sample or noncausal arrival")
        if self._latest_scale_timestamp is not None and sample_s <= self._latest_scale_timestamp:
            raise ValueError("duplicate or out-of-order scale sample")
        self.clock.advance_to(arrival_s)
        self._scale_signal_buffer.add_sample(timestamp_s=sample_s, raw_g=applied_load_g,
                                            applied_load_g=applied_load_g, raw_text="offline")
        self._latest_scale_timestamp = sample_s
        self._latest_scale_arrival_monotonic_s = arrival_s

    def simulate_motor_move(self, *, now_s: float, duration_s: float):
        if not math.isfinite(duration_s) or duration_s < 0:
            raise ValueError("invalid simulated motion duration")
        self.clock.advance_to(now_s)
        self._last_motion_command_time_s = now_s
        self._last_motion_command_monotonic_s = now_s
        self._last_motion_expected_complete_time_s = now_s + duration_s
        self._last_motion_expected_complete_monotonic_s = now_s + duration_s

    def tick(self, now_s: float, *, drive_supply: bool = False):
        self.clock.advance_to(now_s)
        holding, _ = self._update_current_sweep_ramp_hold(self.step, 0, now_s=now_s)
        signal = self._scale_control_signal_for_basis(app.HSW_BASIS_STRESS_MPA, trend_aware=True)
        fresh = self._has_fresh_scale_reading()
        if drive_supply and not holding and fresh:
            self._apply_current_sweep_post_hold_ramp_throttle(now_s=now_s)
            step = self.step
            amount = max(0.0, now_s - self._active_current_sweep_started_s) * step.current_ramp_rate_mA_s
            amount = min(amount, abs(step.current_end_mA - step.current_start_mA))
            current = step.current_start_mA + self._active_current_sweep_display_direction * amount
            self.supply.set_current(now_s, current)
            self._active_current_sweep_last_setpoint_mA = current
        self._automation_phase = "current_hold" if holding else "current"
        return {"time_s": now_s, "holding": holding, "fresh": fresh,
                "filtered_mpa": None if signal is None else signal.value,
                "noise_mpa": None if signal is None else signal.noise,
                "slope_mpa_s": None if signal is None else signal.slope_per_s,
                "sample_count": 0 if signal is None else signal.sample_count,
                "latest_sample_s": self._latest_scale_timestamp,
                "current_mA": self.supply.current_mA,
                "classification": self._current_hold_fluctuation_classification}
