"""Software-only TMA full-run controller simulation.

This harness is intentionally deterministic and hardware-free. It models a
first-overheating style run with target acquisition, current rise, endpoint
recovery, optional reverse unwind, mechanical corrections, and slack take-up.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

from .wire_simulator import (
    CurrentSweepConfig,
    MeasurementSample,
    RobustControllerConfig,
    VirtualWireConfig,
    _clamp,
    _finite,
    _median_absolute_deviation,
    decide_robust_center,
    load_g_from_stress_mpa,
    processed_control_signal,
    stress_mpa_from_load_g,
    transformation_fraction,
)


FULL_RUN_SCENARIOS = (
    "baseline_first_overheating",
    "realistic_first_overheating",
    "realistic_run32_first_target",
    "kosice_kern_first_overheating",
    "bad_co6_first_overheating",
    "low_strain_noisy_first_overheating",
    "noisy_centered_first_overheating",
    "transformation_recovery",
    "reverse_unwind_recovery",
    "slack_after_unwind_takeup",
    "thin_wire_delayed_feedback",
    "stress_ladder_50_100_after_unwind",
)

KOSICE_OFFLINE_POLICIES = (
    "overlapping_window",
    "response_sync_d035",
    "response_sync_d050",
    "response_sync_d065",
)


@dataclass(frozen=True)
class ScaleFeedbackConfig:
    """Balance feedback characteristics applied to the raw controller signal."""

    name: str = "prague_gng"
    readability_g: float | None = 0.005

    def validated(self) -> "ScaleFeedbackConfig":
        if not self.name:
            raise ValueError("scale feedback name is required")
        if self.readability_g is not None and self.readability_g <= 0.0:
            raise ValueError("scale readability_g must be positive when configured")
        if self.readability_g is not None:
            _finite(float(self.readability_g))
        return self


@dataclass(frozen=True)
class FullRunConfig:
    name: str = "baseline_first_overheating"
    description: str = "Nominal software-only first-overheating TMA run."
    wire: VirtualWireConfig = field(default_factory=VirtualWireConfig)
    controller: RobustControllerConfig = field(default_factory=RobustControllerConfig)
    sweep: CurrentSweepConfig = field(
        default_factory=lambda: CurrentSweepConfig(start_ma=1.0, end_ma=80.0, rate_ma_s=0.8, sample_hz=4.5)
    )
    reverse_current: bool = True
    target_ramp_start_mpa: float | None = None
    target_stress_sequence_mpa: tuple[float, ...] = ()
    target_ramp_rate_mpa_s: float = 5.0
    target_ramp_max_lead_fraction: float | None = None
    target_ramp_timeout_s: float = 90.0
    endpoint_hold_timeout_s: float = 90.0
    max_ticks: int = 5000
    scale_latency_s: float = 0.2
    current_hold_feedback_wait_s: float = 0.0
    current_hold_correction_feedback_wait_s: float = 0.0
    scale_feedback: ScaleFeedbackConfig = field(default_factory=ScaleFeedbackConfig)
    zero_compression_stress: bool = False
    current_resume_requires_target_crossing: bool = False
    max_correction_strain_pct: float | None = None
    adaptive_correction_cap_max_scale: float = 1.0
    adaptive_correction_cap_growth: float = 1.35
    adaptive_correction_phases: tuple[str, ...] = ("current_hold",)
    reported_strain_motor_scale: float = 1.0
    reported_strain_offset_pct: float = 0.0
    transformation_profile: str = "wire"
    rising_transformation_steps: tuple[tuple[float, float, float], ...] = ()
    falling_transformation_steps: tuple[tuple[float, float, float], ...] = ()
    transformation_kinetic_tau_s: float = 0.0
    mechanical_response_tau_s: float = 0.0
    free_strain_fluctuation_pct: float = 0.0
    free_strain_fluctuation_cycles: float = 0.0
    inter_target_free_length_shift_mm: float = 0.0
    response_synchronized: bool = False
    response_min_fresh_samples: int = 5
    response_initial_wait_s: float = 0.30
    response_timeout_s: float = 3.60
    response_learning_alpha: float = 0.30
    limit_cycle_damping: float = 0.50
    seed: int = 0

    def validated(self) -> "FullRunConfig":
        self.wire.validated()
        self.controller.validated()
        self.sweep.validated()
        self.scale_feedback.validated()
        if self.target_ramp_timeout_s <= 0.0:
            raise ValueError("target_ramp_timeout_s must be positive")
        if self.target_ramp_rate_mpa_s <= 0.0:
            raise ValueError("target_ramp_rate_mpa_s must be positive")
        if self.target_ramp_max_lead_fraction is not None and self.target_ramp_max_lead_fraction <= 0.0:
            raise ValueError("target_ramp_max_lead_fraction must be positive")
        if self.endpoint_hold_timeout_s <= 0.0:
            raise ValueError("endpoint_hold_timeout_s must be positive")
        if self.max_ticks <= 0:
            raise ValueError("max_ticks must be positive")
        if self.scale_latency_s < 0.0:
            raise ValueError("scale_latency_s must be non-negative")
        if self.current_hold_feedback_wait_s < 0.0:
            raise ValueError("current_hold_feedback_wait_s must be non-negative")
        if self.current_hold_correction_feedback_wait_s < 0.0:
            raise ValueError("current_hold_correction_feedback_wait_s must be non-negative")
        if self.max_correction_strain_pct is not None and self.max_correction_strain_pct <= 0.0:
            raise ValueError("max_correction_strain_pct must be positive")
        if self.adaptive_correction_cap_max_scale < 1.0:
            raise ValueError("adaptive_correction_cap_max_scale must be at least 1")
        if self.adaptive_correction_cap_growth < 1.0:
            raise ValueError("adaptive_correction_cap_growth must be at least 1")
        allowed_adaptive_phases = {"current_hold", "target_ramp"}
        if any(phase not in allowed_adaptive_phases for phase in self.adaptive_correction_phases):
            raise ValueError("adaptive_correction_phases may only contain current_hold and target_ramp")
        if self.reported_strain_motor_scale == 0.0:
            raise ValueError("reported_strain_motor_scale cannot be zero")
        if self.transformation_profile not in {"wire", "stepped"}:
            raise ValueError("transformation_profile must be 'wire' or 'stepped'")
        if self.transformation_kinetic_tau_s < 0.0:
            raise ValueError("transformation_kinetic_tau_s must be non-negative")
        if self.mechanical_response_tau_s < 0.0:
            raise ValueError("mechanical_response_tau_s must be non-negative")
        if self.free_strain_fluctuation_pct < 0.0:
            raise ValueError("free_strain_fluctuation_pct must be non-negative")
        if self.free_strain_fluctuation_cycles < 0.0:
            raise ValueError("free_strain_fluctuation_cycles must be non-negative")
        if self.response_min_fresh_samples < 2:
            raise ValueError("response_min_fresh_samples must be at least 2")
        if self.response_initial_wait_s <= 0.0:
            raise ValueError("response_initial_wait_s must be positive")
        if self.response_timeout_s < self.response_initial_wait_s:
            raise ValueError("response_timeout_s must be at least response_initial_wait_s")
        if not 0.0 < self.response_learning_alpha <= 1.0:
            raise ValueError("response_learning_alpha must be in (0, 1]")
        if not 0.0 < self.limit_cycle_damping <= 1.0:
            raise ValueError("limit_cycle_damping must be in (0, 1]")
        if not self.target_stress_sequence_mpa:
            _finite(self.controller.target_stress_mpa)
        for target in self.target_stress_sequence_mpa:
            _finite(target)
        for steps in (self.rising_transformation_steps, self.falling_transformation_steps):
            for center_ma, width_ma, weight in steps:
                if width_ma <= 0.0:
                    raise ValueError("transformation step width must be positive")
                _finite(center_ma)
                _finite(width_ma)
                _finite(weight)
        _finite(self.free_strain_fluctuation_pct)
        _finite(self.free_strain_fluctuation_cycles)
        _finite(self.mechanical_response_tau_s)
        _finite(self.inter_target_free_length_shift_mm)
        _finite(self.current_hold_feedback_wait_s)
        _finite(self.current_hold_correction_feedback_wait_s)
        _finite(self.adaptive_correction_cap_max_scale)
        _finite(self.adaptive_correction_cap_growth)
        _finite(self.response_initial_wait_s)
        _finite(self.response_timeout_s)
        _finite(self.response_learning_alpha)
        _finite(self.limit_cycle_damping)
        if self.target_ramp_max_lead_fraction is not None:
            _finite(self.target_ramp_max_lead_fraction)
        return self


@dataclass(frozen=True)
class FullRunEvent:
    elapsed_s: float
    phase: str
    current_ma: float
    motor_mm: float
    target_stress_mpa: float
    processed_center_mpa: float
    processed_noise_mpa: float
    processed_slope_mpa_s: float
    raw_min_mpa: float
    raw_max_mpa: float
    error_mpa: float
    decision: str
    result: str
    correction_mm: float
    correction_cap_mm: float
    reason: str
    endpoint_recovered: bool
    fresh: bool
    feedback_age_s: float
    total_travel_mm: float
    command_in_flight: bool = False
    learned_response_time_s: float | None = None
    learned_stiffness_mpa_per_mm: float | None = None
    response_damping: float | None = None
    cruise_allowed: bool = False

    def to_row(self) -> dict[str, Any]:
        return {
            "elapsed_s": f"{self.elapsed_s:.6f}",
            "automation_phase": self.phase,
            "automation_basis": "stress_mpa",
            "automation_target_value": f"{self.target_stress_mpa:.6f}",
            "decision": self.decision,
            "result": self.result,
            "current_value": f"{self.processed_center_mpa:.6f}",
            "error_value": f"{self.error_mpa:.6f}",
            "filtered_noise": f"{self.processed_noise_mpa:.6f}",
            "filtered_slope_per_s": f"{self.processed_slope_mpa_s:.6f}",
            "raw_min_mpa": f"{self.raw_min_mpa:.6f}",
            "raw_max_mpa": f"{self.raw_max_mpa:.6f}",
            "tolerance": "",
            "sensitivity_per_mm": "",
            "motor_step_mm": "",
            "correction_mm": f"{self.correction_mm:.9f}",
            "correction_cap_mm": f"{self.correction_cap_mm:.9f}",
            "target_mm": f"{self.motor_mm:.9f}",
            "result_detail": self.reason,
            "endpoint_recovered": str(bool(self.endpoint_recovered)).lower(),
            "processed_fresh": str(bool(self.fresh)).lower(),
            "feedback_age_s": f"{self.feedback_age_s:.6f}",
            "total_travel_mm": f"{self.total_travel_mm:.9f}",
            "command_in_flight": str(bool(self.command_in_flight)).lower(),
            "learned_response_time_s": (
                "" if self.learned_response_time_s is None else f"{self.learned_response_time_s:.6f}"
            ),
            "learned_stiffness_mpa_per_mm": (
                "" if self.learned_stiffness_mpa_per_mm is None else f"{self.learned_stiffness_mpa_per_mm:.6f}"
            ),
            "response_damping": "" if self.response_damping is None else f"{self.response_damping:.6f}",
            "cruise_allowed": str(bool(self.cruise_allowed)).lower(),
        }


@dataclass(frozen=True)
class FullRunTrace:
    config: FullRunConfig
    samples: list[MeasurementSample]
    events: list[FullRunEvent]
    stop_reason: str
    invariants: dict[str, bool]
    warnings: list[str]

    def summary(self) -> dict[str, Any]:
        final = self.events[-1] if self.events else None
        total_time_s = self.samples[-1].elapsed_s if self.samples else 0.0
        hold_events = [event for event in self.events if event.phase == "current_hold"]
        event_durations = _event_durations_s(self.events, 1.0 / self.config.sweep.sample_hz)
        target_ramp_events = [event for event in self.events if event.phase == "target_ramp"]
        targets = _target_sequence(self.config)
        first_target = targets[0] if targets else self.config.controller.target_stress_mpa
        increasing_targets = max(targets, default=first_target) >= first_target
        recovery_band = max(self.config.controller.tolerance_mpa, self.config.controller.min_recovery_mpa)
        later_target_ramp_events = [
            event
            for event in target_ramp_events
            if (
                event.target_stress_mpa > first_target + 1e-12
                if increasing_targets
                else event.target_stress_mpa < first_target - 1e-12
            )
        ]
        scored_later_target_ramp_events = [
            event
            for event in later_target_ramp_events
            if (
                event.processed_center_mpa >= first_target - recovery_band
                if increasing_targets
                else event.processed_center_mpa <= first_target + recovery_band
            )
        ]
        current_events = [
            event
            for event in self.events
            if event.phase in {"current", "current_hold", "current_limit_unwind"}
        ]
        max_abs_error = max((abs(event.error_mpa) for event in self.events), default=0.0)
        max_abs_current_error = max((abs(event.error_mpa) for event in current_events), default=0.0)
        max_abs_target_ramp_error = max((abs(event.error_mpa) for event in target_ramp_events), default=0.0)
        max_abs_later_target_ramp_reacquisition_error = max(
            (abs(event.error_mpa) for event in later_target_ramp_events),
            default=0.0,
        )
        max_abs_later_target_ramp_error = max(
            (abs(event.error_mpa) for event in scored_later_target_ramp_events),
            default=0.0,
        )
        p95_abs_current_error = _percentile(
            [abs(event.error_mpa) for event in current_events],
            0.95,
        )
        p95_abs_later_target_ramp_error = _percentile(
            [abs(event.error_mpa) for event in scored_later_target_ramp_events],
            0.95,
        )
        recovery_times = _recovery_times_s(self.events, recovery_band)
        tracking_errors = _free_strain_tracking_errors_pct(self)
        strain_values = [sample.strain_pct for sample in self.samples]
        free_strain_values = [
            _material_state_for_sample(self.config, sample)["free_transformation_strain_pct"]
            for sample in self.samples
        ]
        current_hold_time_s = sum(
            duration
            for event, duration in zip(self.events, event_durations, strict=False)
            if event.phase == "current_hold"
        )
        free_strain_range_pct = max(free_strain_values, default=0.0) - min(free_strain_values, default=0.0)
        max_abs_tracking_error_pct = max((abs(value) for value in tracking_errors), default=0.0)
        mean_abs_tracking_error_pct = (
            statistics.fmean(abs(value) for value in tracking_errors) if tracking_errors else 0.0
        )
        max_target_mpa = max((abs(target) for target in targets), default=abs(self.config.controller.target_stress_mpa))
        max_target_mpa = max(1.0, max_target_mpa)
        total_time_s = max(0.0, total_time_s)
        quality = _quality_summary(
            stop_reason=self.stop_reason,
            invariant_warnings=self.warnings,
            max_abs_current_sweep_error_mpa=max_abs_current_error,
            max_abs_later_target_ramp_error_mpa=max_abs_later_target_ramp_error,
            p95_abs_current_sweep_error_mpa=p95_abs_current_error,
            p95_abs_later_target_ramp_error_mpa=p95_abs_later_target_ramp_error,
            max_abs_free_strain_tracking_error_pct=max_abs_tracking_error_pct,
            mean_abs_free_strain_tracking_error_pct=mean_abs_tracking_error_pct,
            free_transformation_strain_range_pct=free_strain_range_pct,
            current_hold_time_s=current_hold_time_s,
            total_measurement_time_s=total_time_s,
            max_target_mpa=max_target_mpa,
        )
        correction_events = [event for event in self.events if abs(event.correction_mm) > 0.0]
        command_intervals_s = [
            current.elapsed_s - previous.elapsed_s
            for previous, current in zip(correction_events, correction_events[1:])
        ]
        max_same_direction_stack = 0
        current_stack = 0
        previous_direction = 0
        for event in correction_events:
            direction = 1 if event.correction_mm > 0.0 else -1
            current_stack = current_stack + 1 if direction == previous_direction else 1
            previous_direction = direction
            max_same_direction_stack = max(max_same_direction_stack, current_stack)
        return {
            "scenario": self.config.name,
            "description": self.config.description,
            "stop_reason": self.stop_reason,
            "length_mm": self.config.wire.length_mm,
            "diameter_mm": self.config.wire.diameter_mm,
            "elastic_stiffness_mpa_per_mm": self.config.wire.elastic_stiffness_mpa_per_mm,
            "mechanical_response_tau_s": self.config.mechanical_response_tau_s,
            "configured_transformation_strain_pct": (
                self.config.wire.transformation_contraction_mm / self.config.wire.length_mm * 100.0
            ),
            "configured_free_strain_fluctuation_pct": self.config.free_strain_fluctuation_pct,
            "configured_free_strain_fluctuation_cycles": self.config.free_strain_fluctuation_cycles,
            "target_stress_sequence_mpa": list(_target_sequence(self.config)),
            "target_ramp_max_lead_fraction": self.config.target_ramp_max_lead_fraction,
            "inter_target_free_length_shift_mm": self.config.inter_target_free_length_shift_mm,
            "scale_latency_s": self.config.scale_latency_s,
            "current_hold_feedback_wait_s": self.config.current_hold_feedback_wait_s,
            "current_hold_correction_feedback_wait_s": self.config.current_hold_correction_feedback_wait_s,
            "scale_feedback_name": self.config.scale_feedback.name,
            "scale_readability_g": self.config.scale_feedback.readability_g,
            "current_resume_requires_target_crossing": self.config.current_resume_requires_target_crossing,
            "sample_hz": self.config.sweep.sample_hz,
            "sample_count": len(self.samples),
            "event_count": len(self.events),
            "target_ramp_event_count": len(target_ramp_events),
            "current_phase_event_count": len(current_events),
            "total_measurement_time_s": total_time_s,
            "final_phase": None if final is None else final.phase,
            "final_decision": None if final is None else final.decision,
            "final_result": None if final is None else final.result,
            "final_processed_center_mpa": None if final is None else final.processed_center_mpa,
            "final_error_mpa": None if final is None else final.error_mpa,
            "max_abs_error_mpa": max_abs_error,
            "max_abs_current_sweep_error_mpa": max_abs_current_error,
            "max_abs_target_ramp_error_mpa": max_abs_target_ramp_error,
            "max_abs_later_target_ramp_reacquisition_error_mpa": max_abs_later_target_ramp_reacquisition_error,
            "max_abs_later_target_ramp_error_mpa": max_abs_later_target_ramp_error,
            "p95_abs_current_sweep_error_mpa": p95_abs_current_error,
            "p95_abs_later_target_ramp_error_mpa": p95_abs_later_target_ramp_error,
            "max_abs_current_sweep_error_fraction_of_target": max_abs_current_error / max_target_mpa,
            "max_abs_later_target_ramp_error_fraction_of_target": max_abs_later_target_ramp_error / max_target_mpa,
            "p95_abs_current_sweep_error_fraction_of_target": p95_abs_current_error / max_target_mpa,
            "p95_abs_later_target_ramp_error_fraction_of_target": p95_abs_later_target_ramp_error / max_target_mpa,
            "time_outside_recovery_band_s": sum(
                duration
                for event, duration in zip(self.events, event_durations, strict=False)
                if abs(event.error_mpa) > recovery_band
            ),
            "strain_min_pct": min(strain_values, default=0.0),
            "strain_max_pct": max(strain_values, default=0.0),
            "strain_range_pct": max(strain_values, default=0.0) - min(strain_values, default=0.0),
            "free_transformation_strain_min_pct": min(free_strain_values, default=0.0),
            "free_transformation_strain_max_pct": max(free_strain_values, default=0.0),
            "free_transformation_strain_range_pct": free_strain_range_pct,
            "max_abs_free_strain_tracking_error_pct": max_abs_tracking_error_pct,
            "mean_abs_free_strain_tracking_error_pct": mean_abs_tracking_error_pct,
            "max_abs_free_strain_tracking_error_fraction_of_span": (
                max_abs_tracking_error_pct / max(0.25, free_strain_range_pct)
            ),
            "mean_abs_free_strain_tracking_error_fraction_of_span": (
                mean_abs_tracking_error_pct / max(0.25, free_strain_range_pct)
            ),
            "max_correction_strain_pct": self.config.max_correction_strain_pct,
            "effective_max_correction_mm": _effective_max_correction_mm(self.config),
            "adaptive_correction_cap_max_scale": self.config.adaptive_correction_cap_max_scale,
            "adaptive_correction_phases": list(self.config.adaptive_correction_phases),
            "effective_max_adaptive_correction_mm": _max_allowed_correction_mm(self.config),
            "max_observed_correction_cap_mm": max((event.correction_cap_mm for event in self.events), default=0.0),
            "max_total_travel_mm": max((event.total_travel_mm for event in self.events), default=0.0),
            "max_abs_correction_mm": max((abs(event.correction_mm) for event in self.events), default=0.0),
            "response_synchronized": self.config.response_synchronized,
            "response_command_count": len(correction_events),
            "minimum_command_interval_s": min(command_intervals_s, default=0.0),
            "median_command_interval_s": (
                statistics.median(command_intervals_s) if command_intervals_s else 0.0
            ),
            "max_same_direction_command_stack": max_same_direction_stack,
            "response_crossing_count": sum(
                1
                for previous, current in zip(correction_events, correction_events[1:])
                if previous.correction_mm * current.correction_mm < 0.0
            ),
            "learned_response_time_s": next(
                (
                    event.learned_response_time_s
                    for event in reversed(self.events)
                    if event.learned_response_time_s is not None
                ),
                None,
            ),
            "learned_stiffness_mpa_per_mm": next(
                (
                    event.learned_stiffness_mpa_per_mm
                    for event in reversed(self.events)
                    if event.learned_stiffness_mpa_per_mm is not None
                ),
                None,
            ),
            "final_response_damping": next(
                (event.response_damping for event in reversed(self.events) if event.response_damping is not None),
                None,
            ),
            "current_hold_count": len(hold_events),
            "current_hold_time_s": current_hold_time_s,
            "current_hold_fraction_of_measurement": (
                current_hold_time_s / total_time_s if total_time_s > 0.0 else 0.0
            ),
            "current_hold_periods": _current_hold_periods(
                self.events,
                event_durations,
                1.0 / self.config.sweep.sample_hz,
            ),
            "max_recovery_time_s": max(recovery_times, default=0.0),
            "mean_recovery_time_s": statistics.fmean(recovery_times) if recovery_times else 0.0,
            "endpoint_hold_count": sum(
                1 for event in self.events if event.result == "endpoint_waiting_for_recovery"
            ),
            "invariants": dict(self.invariants),
            "warnings": list(self.warnings),
            "quality_status": quality["status"],
            "quality_flags": quality["flags"],
            "quality_score": quality["score"],
        }


class _FullRunState:
    def __init__(self, config: FullRunConfig) -> None:
        self.config = config
        self.rng = random.Random(config.seed)
        self.elapsed_s = 0.0
        self.sample_index = 0
        self.current_ma = config.sweep.start_ma
        self.motor_mm = config.wire.initial_motor_mm
        self.physical_motor_mm = config.wire.initial_motor_mm
        self.total_travel_mm = 0.0
        self.samples: list[MeasurementSample] = []
        self.events: list[FullRunEvent] = []
        self.previous_error_sign = 0
        self.actual_transformation_fraction = 0.0
        self.permanent_free_length_shift_mm = config.wire.initial_free_length_shift_mm
        initial_target = _target_sequence(config)[0]
        self.ramp_start_stress_mpa = initial_target if config.target_ramp_start_mpa is None else config.target_ramp_start_mpa
        self.ramp_final_stress_mpa = initial_target
        self.ramp_started_elapsed_s = 0.0
        self.ramp_waiting_for_fresh_feedback = False
        self.active_target_stress_mpa = self.ramp_start_stress_mpa
        self.adaptive_correction_scale = 1.0
        self.max_observed_adaptive_correction_scale = 1.0
        self.previous_adaptive_error_mpa: float | None = None
        self.pending_response_command: dict[str, float] | None = None
        self.last_response_command_elapsed_s: float | None = None
        self.learned_response_time_s = config.response_initial_wait_s
        self.learned_stiffness_mpa_per_mm: float | None = None
        self.response_damping = config.limit_cycle_damping
        self.response_crossing_count = 0

    @property
    def dt_s(self) -> float:
        return 1.0 / self.config.sweep.sample_hz

    def feedback_samples(self) -> list[MeasurementSample]:
        if self.config.scale_latency_s <= 0.0:
            return list(self.samples)
        latency_samples = max(1, int(math.ceil(self.config.scale_latency_s / self.dt_s - 1e-12)))
        available_count = len(self.samples) - latency_samples
        if available_count <= 0:
            return []
        return self.samples[:available_count]

    def feedback_age_s(self, feedback_samples: list[MeasurementSample] | None = None) -> float:
        feedback = self.feedback_samples() if feedback_samples is None else feedback_samples
        if not feedback:
            return math.inf
        return max(0.0, self.elapsed_s - feedback[-1].elapsed_s)

    def _post_command_feedback(self) -> list[MeasurementSample]:
        feedback = self.feedback_samples()
        if not self.config.response_synchronized or self.last_response_command_elapsed_s is None:
            return feedback
        return [
            sample
            for sample in feedback
            if sample.elapsed_s > self.last_response_command_elapsed_s + 1e-12
        ]

    def _refresh_response_learning(self, controller: RobustControllerConfig) -> None:
        pending = self.pending_response_command
        if not self.config.response_synchronized or pending is None:
            return
        post = self._post_command_feedback()
        if len(post) < self.config.response_min_fresh_samples:
            return
        age_s = post[-1].elapsed_s - pending["elapsed_s"]
        if age_s + 1e-12 < self.learned_response_time_s and age_s < self.config.response_timeout_s:
            return
        signal = processed_control_signal(post, controller)
        response_mpa = signal.center_mpa - pending["center_mpa"]
        threshold_mpa = max(controller.tolerance_mpa * 0.25, signal.noise_mpa * 0.50, 1e-6)
        directional_response = response_mpa * pending["correction_mm"] > 0.0
        detected = directional_response and abs(response_mpa) >= threshold_mpa
        alpha = self.config.response_learning_alpha
        if detected:
            observed_stiffness = abs(response_mpa / pending["correction_mm"])
            if self.learned_stiffness_mpa_per_mm is None:
                self.learned_stiffness_mpa_per_mm = observed_stiffness
            else:
                self.learned_stiffness_mpa_per_mm += alpha * (
                    observed_stiffness - self.learned_stiffness_mpa_per_mm
                )
            self.learned_response_time_s += alpha * (age_s - self.learned_response_time_s)
            post_error = signal.center_mpa - controller.target_stress_mpa
            pre_error = pending["error_mpa"]
            if pre_error * post_error < 0.0:
                self.response_crossing_count += 1
                self.response_damping = max(0.12, self.response_damping * 0.50)
            elif abs(post_error) < abs(pre_error):
                self.response_damping += alpha * (
                    self.config.limit_cycle_damping - self.response_damping
                )
            else:
                self.response_damping = max(0.12, self.response_damping * 0.70)
        else:
            self.response_damping = max(0.12, self.response_damping * 0.70)
            self.learned_response_time_s = min(
                self.config.response_timeout_s,
                max(self.learned_response_time_s, age_s),
            )
        self.pending_response_command = None

    def control_feedback_samples(self, controller: RobustControllerConfig) -> list[MeasurementSample]:
        self._refresh_response_learning(controller)
        if not self.config.response_synchronized:
            return self.feedback_samples()
        post = self._post_command_feedback()
        if len(post) >= 2:
            return post
        return self.feedback_samples()

    def controller_for_decision(self, *, min_recovery_mpa: float | None = None) -> RobustControllerConfig:
        controller = replace(
            self.config.controller,
            target_stress_mpa=self.active_target_stress_mpa,
            max_correction_mm=_effective_max_correction_mm(self.config),
            previous_error_sign=self.previous_error_sign,
        )
        if min_recovery_mpa is not None:
            controller = replace(controller, min_recovery_mpa=min_recovery_mpa)
        return controller

    def controller_for_correction_phase(self, phase: str, controller: RobustControllerConfig) -> RobustControllerConfig:
        cap_mm = self.correction_cap_for_phase_mm(phase, controller)
        if cap_mm == controller.max_correction_mm:
            return controller
        return replace(controller, max_correction_mm=cap_mm)

    def correction_cap_for_phase_mm(self, phase: str, controller: RobustControllerConfig) -> float:
        base_cap_mm = _effective_max_correction_mm(self.config)
        if phase not in self.config.adaptive_correction_phases or self.config.adaptive_correction_cap_max_scale <= 1.0:
            return base_cap_mm
        feedback = self.feedback_samples()
        if not feedback:
            return base_cap_mm
        signal = processed_control_signal(feedback, controller)
        target_scale_mpa = max(abs(controller.target_stress_mpa), 1.0)
        noise_fraction = signal.noise_mpa / target_scale_mpa
        noise_gate = max(controller.tolerance_mpa / target_scale_mpa, 0.02)
        if noise_fraction > noise_gate * 2.0:
            return base_cap_mm
        scale = self.adaptive_correction_scale
        return base_cap_mm * min(self.config.adaptive_correction_cap_max_scale, scale)

    def update_adaptive_correction_scale(self, event: FullRunEvent) -> None:
        if event.phase not in self.config.adaptive_correction_phases or self.config.adaptive_correction_cap_max_scale <= 1.0:
            self.adaptive_correction_scale = 1.0
            self.previous_adaptive_error_mpa = None
            return
        if event.correction_mm == 0.0 or event.decision in {"no_move", "wait_reversal", "safety_stop"}:
            self.adaptive_correction_scale = max(1.0, self.adaptive_correction_scale / self.config.adaptive_correction_cap_growth)
            self.previous_adaptive_error_mpa = event.error_mpa
            return
        previous_error = self.previous_adaptive_error_mpa
        self.previous_adaptive_error_mpa = event.error_mpa
        if previous_error is None:
            return
        if previous_error * event.error_mpa <= 0.0:
            self.adaptive_correction_scale = 1.0
            return
        improvement = abs(previous_error) - abs(event.error_mpa)
        improvement_floor = max(
            self.config.controller.tolerance_mpa * 0.25,
            event.processed_noise_mpa * 0.5,
            1e-9,
        )
        if improvement > improvement_floor:
            self.adaptive_correction_scale = min(
                self.config.adaptive_correction_cap_max_scale,
                self.adaptive_correction_scale * self.config.adaptive_correction_cap_growth,
            )
        elif improvement < -improvement_floor:
            self.adaptive_correction_scale = max(1.0, self.adaptive_correction_scale / self.config.adaptive_correction_cap_growth)
        self.max_observed_adaptive_correction_scale = max(
            self.max_observed_adaptive_correction_scale,
            self.adaptive_correction_scale,
        )

    def sample(self, phase: str, *, rising: bool) -> MeasurementSample:
        wire = self.config.wire
        fraction = self._next_transformation_fraction(rising=rising)
        free_shift_mm = (
            self.permanent_free_length_shift_mm
            + fraction * wire.transformation_contraction_mm
            + _free_strain_fluctuation_mm(self.config, fraction)
        )
        response_tau_s = self.config.mechanical_response_tau_s
        if response_tau_s <= 0.0:
            self.physical_motor_mm = self.motor_mm
        else:
            response_alpha = 1.0 - math.exp(-self.dt_s / response_tau_s)
            self.physical_motor_mm += (self.motor_mm - self.physical_motor_mm) * response_alpha
        mechanical_mm = self.physical_motor_mm + free_shift_mm
        base_stress = mechanical_mm * wire.elastic_stiffness_mpa_per_mm
        if self.config.zero_compression_stress and base_stress < 0.0:
            base_stress = 0.0
        fluctuation = 0.0
        if 0.0 < fraction < 1.0 and wire.fluctuation_mpa:
            fluctuation = wire.fluctuation_mpa * math.sin(2.0 * math.pi * wire.fluctuation_cycles * fraction)
        noise = self.rng.gauss(0.0, wire.noise_mpa) if wire.noise_mpa else 0.0
        drift = wire.drift_mpa_per_s * self.elapsed_s
        stress = base_stress + fluctuation + noise + drift
        if self.config.zero_compression_stress and stress < 0.0:
            stress = 0.0
        raw_stress = stress
        raw_load_g = load_g_from_stress_mpa(raw_stress, wire.diameter_mm)
        readability_g = self.config.scale_feedback.readability_g
        if readability_g is not None and raw_load_g is not None:
            quantum = abs(float(readability_g))
            if quantum > 0.0:
                raw_load_g = round(float(raw_load_g) / quantum) * quantum
                raw_stress = stress_mpa_from_load_g(raw_load_g, wire.diameter_mm) or raw_stress
        status = "ok"
        safety_reason = ""
        strain_pct = (
            self.config.reported_strain_offset_pct
            + self.config.reported_strain_motor_scale * self.motor_mm / wire.length_mm * 100.0
        )
        if wire.break_stress_mpa is not None and raw_stress >= wire.break_stress_mpa:
            status = "wire_break"
            safety_reason = "break_stress"
        if wire.break_strain_pct is not None and abs(strain_pct) >= wire.break_strain_pct:
            status = "wire_break"
            safety_reason = safety_reason or "break_strain"
        sample = MeasurementSample(
            sample_index=self.sample_index,
            elapsed_s=self.elapsed_s,
            current_ma=self.current_ma,
            motor_mm=self.motor_mm,
            free_length_shift_mm=free_shift_mm,
            transformation_fraction=fraction,
            stress_mpa=stress,
            raw_stress_mpa=raw_stress,
            load_g=load_g_from_stress_mpa(stress, wire.diameter_mm),
            raw_load_g=raw_load_g,
            strain_pct=strain_pct,
            status=status,
            safety_reason=safety_reason,
        )
        self.samples.append(sample)
        self.sample_index += 1
        return sample

    def _next_transformation_fraction(self, *, rising: bool) -> float:
        equilibrium = _equilibrium_transformation_fraction(
            self.current_ma,
            self.config,
            rising=rising,
        )
        tau_s = self.config.transformation_kinetic_tau_s
        if tau_s <= 0.0:
            self.actual_transformation_fraction = equilibrium
        else:
            alpha = 1.0 - math.exp(-self.dt_s / tau_s)
            self.actual_transformation_fraction += (equilibrium - self.actual_transformation_fraction) * alpha
            self.actual_transformation_fraction = _clamp(self.actual_transformation_fraction, 0.0, 1.0)
        return self.actual_transformation_fraction

    def advance_time(self, duration_s: float | None = None) -> None:
        self.elapsed_s += self.dt_s if duration_s is None else max(0.0, float(duration_s))

    def advance_with_passive_samples(self, duration_s: float, phase: str, *, rising: bool) -> float:
        remaining_s = max(0.0, float(duration_s))
        advanced_s = 0.0
        while remaining_s > 1e-12:
            step_s = min(self.dt_s, remaining_s)
            self.advance_time(step_s)
            self.sample(phase, rising=rising)
            advanced_s += step_s
            remaining_s -= step_s
        return advanced_s

    def start_target_ramp(self, start: float, final: float) -> None:
        self.ramp_start_stress_mpa = start
        self.ramp_final_stress_mpa = final
        self.ramp_started_elapsed_s = self.elapsed_s
        self.ramp_waiting_for_fresh_feedback = True
        self.active_target_stress_mpa = start
        self.previous_error_sign = 0

    def apply_inter_target_free_length_shift(self) -> None:
        self.permanent_free_length_shift_mm += self.config.inter_target_free_length_shift_mm

    def update_target_ramp(self) -> None:
        start = self.ramp_start_stress_mpa
        final = self.ramp_final_stress_mpa
        previous_target = self.active_target_stress_mpa
        feedback = self.feedback_samples()
        if self.ramp_waiting_for_fresh_feedback:
            self.active_target_stress_mpa = start
            if not feedback or feedback[-1].elapsed_s + 1e-12 < self.ramp_started_elapsed_s:
                return
            self.ramp_waiting_for_fresh_feedback = False
            self.ramp_started_elapsed_s = self.elapsed_s
            return
        direction = 1.0 if final >= start else -1.0
        elapsed = max(0.0, self.elapsed_s - self.ramp_started_elapsed_s)
        next_target = start + direction * self.config.target_ramp_rate_mpa_s * elapsed
        if direction >= 0.0:
            self.active_target_stress_mpa = min(final, next_target)
        else:
            self.active_target_stress_mpa = max(final, next_target)
        lead_fraction = self.config.target_ramp_max_lead_fraction
        if lead_fraction is None or not feedback:
            return
        signal = processed_control_signal(feedback, self.controller_for_decision())
        lead_mpa = max(abs(final), abs(start), 1.0) * lead_fraction
        if direction >= 0.0:
            planned_target = self.active_target_stress_mpa
            self.active_target_stress_mpa = min(self.active_target_stress_mpa, signal.center_mpa + lead_mpa)
            self.active_target_stress_mpa = max(previous_target, self.active_target_stress_mpa)
            if self.active_target_stress_mpa + 1e-12 < planned_target:
                progressed = max(0.0, self.active_target_stress_mpa - start)
                self.ramp_started_elapsed_s = self.elapsed_s - progressed / self.config.target_ramp_rate_mpa_s
        else:
            planned_target = self.active_target_stress_mpa
            self.active_target_stress_mpa = max(self.active_target_stress_mpa, signal.center_mpa - lead_mpa)
            self.active_target_stress_mpa = min(previous_target, self.active_target_stress_mpa)
            if self.active_target_stress_mpa - 1e-12 > planned_target:
                progressed = max(0.0, start - self.active_target_stress_mpa)
                self.ramp_started_elapsed_s = self.elapsed_s - progressed / self.config.target_ramp_rate_mpa_s

    def target_ramp_complete(self) -> bool:
        return abs(self.active_target_stress_mpa - self.ramp_final_stress_mpa) <= 1e-12

    def record_event(
        self,
        phase: str,
        correction_mm: float,
        reason: str,
        *,
        controller: RobustControllerConfig | None = None,
    ) -> FullRunEvent:
        feedback = self.control_feedback_samples(controller or self.controller_for_decision())
        if not feedback:
            raise ValueError("cannot record a control event without delayed feedback")
        controller = controller or self.controller_for_decision()
        decision = decide_robust_center(feedback, controller)
        signal = processed_control_signal(feedback, controller)
        feedback_age_s = self.feedback_age_s(feedback)
        event = FullRunEvent(
            elapsed_s=self.elapsed_s,
            phase=phase,
            current_ma=self.current_ma,
            motor_mm=self.motor_mm,
            target_stress_mpa=controller.target_stress_mpa,
            processed_center_mpa=signal.center_mpa,
            processed_noise_mpa=signal.noise_mpa,
            processed_slope_mpa_s=signal.slope_mpa_s,
            raw_min_mpa=signal.raw_min_mpa,
            raw_max_mpa=signal.raw_max_mpa,
            error_mpa=signal.center_mpa - controller.target_stress_mpa,
            decision=decision.decision,
            result=decision.result if reason != "endpoint_waiting_for_recovery" else reason,
            correction_mm=correction_mm,
            correction_cap_mm=controller.max_correction_mm,
            reason=reason,
            endpoint_recovered=decision.endpoint_recovered,
            fresh=signal.fresh and feedback_age_s <= self.config.controller.stale_feedback_s,
            feedback_age_s=feedback_age_s,
            total_travel_mm=self.total_travel_mm,
            command_in_flight=self.pending_response_command is not None,
            learned_response_time_s=(
                self.learned_response_time_s if self.config.response_synchronized else None
            ),
            learned_stiffness_mpa_per_mm=self.learned_stiffness_mpa_per_mm,
            response_damping=self.response_damping if self.config.response_synchronized else None,
            cruise_allowed=False,
        )
        self.events.append(event)
        recovery_band = max(controller.tolerance_mpa, controller.min_recovery_mpa)
        if abs(event.error_mpa) > recovery_band and decision.decision != "safety_stop":
            self.previous_error_sign = 1 if event.error_mpa > 0.0 else -1
        self.update_adaptive_correction_scale(event)
        return event

    def correct_toward_target(self, *, controller: RobustControllerConfig | None = None) -> float:
        controller = controller or self.controller_for_decision()
        feedback = self.control_feedback_samples(controller)
        if not feedback or self.feedback_age_s(feedback) > self.config.controller.stale_feedback_s:
            return 0.0
        if self.config.response_synchronized and self.pending_response_command is not None:
            return 0.0
        decision = decide_robust_center(feedback, controller)
        correction = decision.motor_step_mm
        if decision.decision in {"no_move", "wait_reversal", "safety_stop"}:
            correction = 0.0
        if (
            abs(correction) > 0.0
            and self.config.response_synchronized
            and self.learned_stiffness_mpa_per_mm is not None
        ):
            response_step = -decision.error_mpa / max(1e-9, self.learned_stiffness_mpa_per_mm)
            response_step *= self.response_damping
            if abs(response_step) < controller.motor_step_mm:
                response_step = math.copysign(controller.motor_step_mm, response_step)
            correction = response_step
        correction = _clamp(correction, -controller.max_correction_mm, controller.max_correction_mm)
        if abs(correction) <= 0.0:
            return 0.0
        self.motor_mm += correction
        self.total_travel_mm += abs(correction)
        if self.config.response_synchronized:
            signal = processed_control_signal(feedback, controller)
            self.last_response_command_elapsed_s = self.elapsed_s
            self.pending_response_command = {
                "elapsed_s": self.elapsed_s,
                "center_mpa": signal.center_mpa,
                "error_mpa": signal.center_mpa - controller.target_stress_mpa,
                "correction_mm": correction,
            }
        return correction


def _recovered(state: _FullRunState) -> bool:
    controller = state.controller_for_decision()
    feedback = state.control_feedback_samples(controller)
    if state.config.response_synchronized and state.pending_response_command is not None:
        return False
    if not feedback:
        return False
    decision = decide_robust_center(feedback, controller)
    return decision.endpoint_recovered


def _current_sweep_ready_to_advance(state: _FullRunState) -> bool:
    controller = state.controller_for_decision(min_recovery_mpa=state.config.controller.tolerance_mpa)
    feedback = state.control_feedback_samples(controller)
    if state.config.response_synchronized and state.pending_response_command is not None:
        return False
    if not feedback:
        return False
    decision = decide_robust_center(feedback, controller)
    return decision.endpoint_recovered


def _current_sweep_crossed_target_for_resume(state: _FullRunState, *, rising: bool) -> bool:
    if not state.config.current_resume_requires_target_crossing:
        return True
    controller = state.controller_for_decision(min_recovery_mpa=state.config.controller.tolerance_mpa)
    feedback = state.control_feedback_samples(controller)
    if not feedback:
        return False
    signal = processed_control_signal(feedback, controller)
    if signal.raw_min_mpa <= controller.target_stress_mpa <= signal.raw_max_mpa:
        return True
    error = signal.center_mpa - controller.target_stress_mpa
    return error <= 0.0 if rising else error >= 0.0


def _target_ramp_centered_for_move(state: _FullRunState) -> bool:
    controller = state.controller_for_decision()
    feedback = state.control_feedback_samples(controller)
    if state.config.response_synchronized and state.pending_response_command is not None:
        return True
    if not feedback:
        return False
    signal = processed_control_signal(feedback, controller)
    return abs(signal.center_mpa - state.active_target_stress_mpa) <= state.config.controller.tolerance_mpa


def _stop_for_safety(state: _FullRunState) -> str | None:
    if state.samples and state.samples[-1].status != "ok":
        return state.samples[-1].status
    return None


def _target_sequence(config: FullRunConfig) -> tuple[float, ...]:
    return config.target_stress_sequence_mpa or (config.controller.target_stress_mpa,)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = _clamp(fraction, 0.0, 1.0) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _quality_summary(
    *,
    stop_reason: str,
    invariant_warnings: list[str],
    max_abs_current_sweep_error_mpa: float,
    max_abs_later_target_ramp_error_mpa: float,
    p95_abs_current_sweep_error_mpa: float,
    p95_abs_later_target_ramp_error_mpa: float,
    max_abs_free_strain_tracking_error_pct: float,
    mean_abs_free_strain_tracking_error_pct: float,
    free_transformation_strain_range_pct: float,
    current_hold_time_s: float,
    total_measurement_time_s: float,
    max_target_mpa: float,
) -> dict[str, Any]:
    target_scale = max(1.0, abs(float(max_target_mpa)))
    free_span_scale = max(0.25, abs(float(free_transformation_strain_range_pct)))
    current_error_fraction = abs(float(max_abs_current_sweep_error_mpa)) / target_scale
    later_ramp_fraction = abs(float(max_abs_later_target_ramp_error_mpa)) / target_scale
    p95_current_error_fraction = abs(float(p95_abs_current_sweep_error_mpa)) / target_scale
    p95_later_ramp_fraction = abs(float(p95_abs_later_target_ramp_error_mpa)) / target_scale
    max_tracking_fraction = abs(float(max_abs_free_strain_tracking_error_pct)) / free_span_scale
    mean_tracking_fraction = abs(float(mean_abs_free_strain_tracking_error_pct)) / free_span_scale
    hold_fraction = (
        abs(float(current_hold_time_s)) / float(total_measurement_time_s)
        if total_measurement_time_s > 0.0
        else 0.0
    )
    flags: list[str] = []
    if stop_reason != "completed":
        flags.append("incomplete")
    if invariant_warnings:
        flags.append("invariant_warning")
    if later_ramp_fraction > 0.25:
        flags.append("later_target_ramp_error_high")
    if p95_current_error_fraction > 0.35:
        flags.append("current_sweep_error_high")
    if max_tracking_fraction > 0.35:
        flags.append("free_strain_tracking_error_high")
    if mean_tracking_fraction > 0.15:
        flags.append("mean_free_strain_tracking_error_high")
    if hold_fraction > 0.65:
        flags.append("current_hold_fraction_high")
    score = (
        0.35 * current_error_fraction
        + 0.65 * p95_current_error_fraction
        + later_ramp_fraction
        + 0.35 * p95_later_ramp_fraction
        + mean_tracking_fraction
        + 0.25 * hold_fraction
        + (2.0 if stop_reason != "completed" else 0.0)
        + (0.5 if invariant_warnings else 0.0)
    )
    if stop_reason != "completed" or invariant_warnings:
        status = "failed"
    elif flags:
        status = "needs_tuning"
    else:
        status = "ok"
    return {
        "status": status,
        "flags": flags,
        "score": score,
    }


def _effective_max_correction_mm(config: FullRunConfig) -> float:
    if config.max_correction_strain_pct is None:
        return config.controller.max_correction_mm
    strain_cap_mm = config.wire.length_mm * config.max_correction_strain_pct / 100.0
    return max(config.controller.motor_step_mm, strain_cap_mm)


def _max_allowed_correction_mm(config: FullRunConfig) -> float:
    return _effective_max_correction_mm(config) * config.adaptive_correction_cap_max_scale


def _free_strain_fluctuation_mm(config: FullRunConfig, fraction: float) -> float:
    amplitude_pct = config.free_strain_fluctuation_pct
    if amplitude_pct <= 0.0 or fraction <= 0.0 or fraction >= 1.0:
        return 0.0
    cycles = config.free_strain_fluctuation_cycles if config.free_strain_fluctuation_cycles > 0.0 else 1.0
    envelope = math.sin(math.pi * fraction)
    return config.wire.length_mm * amplitude_pct / 100.0 * envelope * math.sin(
        2.0 * math.pi * cycles * fraction
    )


def _material_state_for_sample(config: FullRunConfig, sample: MeasurementSample) -> dict[str, float]:
    length_mm = config.wire.length_mm
    free_contraction_mm = sample.free_length_shift_mm
    free_strain_pct = -free_contraction_mm / length_mm * 100.0
    motor_strain_pct = sample.motor_mm / length_mm * 100.0
    elastic_mismatch_mm = sample.motor_mm + free_contraction_mm
    elastic_mismatch_strain_pct = elastic_mismatch_mm / length_mm * 100.0
    return {
        "free_transformation_contraction_mm": free_contraction_mm,
        "free_transformation_strain_pct": free_strain_pct,
        "motor_strain_pct": motor_strain_pct,
        "reported_motor_strain_pct": sample.strain_pct,
        "elastic_mismatch_mm": elastic_mismatch_mm,
        "elastic_mismatch_strain_pct": elastic_mismatch_strain_pct,
        "elastic_mismatch_stress_mpa": elastic_mismatch_mm * config.wire.elastic_stiffness_mpa_per_mm,
    }


def _current_phase_samples(trace: FullRunTrace) -> list[MeasurementSample]:
    events_by_time = {round(event.elapsed_s, 9): event for event in trace.events}
    return [
        sample
        for sample in trace.samples
        if (event := events_by_time.get(round(sample.elapsed_s, 9))) is not None
        and event.phase in {"current", "current_hold", "current_limit_unwind"}
    ]


def _free_strain_tracking_errors_pct(trace: FullRunTrace) -> list[float]:
    current_samples = _current_phase_samples(trace)
    if not current_samples:
        return []
    first = current_samples[0]
    first_free_strain = _material_state_for_sample(trace.config, first)["free_transformation_strain_pct"]
    first_measured_strain = first.strain_pct
    errors: list[float] = []
    for sample in current_samples:
        state = _material_state_for_sample(trace.config, sample)
        free_delta = state["free_transformation_strain_pct"] - first_free_strain
        measured_delta = sample.strain_pct - first_measured_strain
        errors.append(measured_delta - free_delta)
    return errors


def _equilibrium_transformation_fraction(
    current_ma: float,
    config: FullRunConfig,
    *,
    rising: bool,
) -> float:
    if config.transformation_profile == "wire":
        return transformation_fraction(current_ma, config.wire, rising=rising)
    steps = config.rising_transformation_steps if rising else config.falling_transformation_steps
    if not steps:
        return transformation_fraction(current_ma, config.wire, rising=rising)
    fraction = 0.0
    for center_ma, width_ma, weight in steps:
        exponent = _clamp((center_ma - current_ma) / width_ma, -60.0, 60.0)
        fraction += weight / (1.0 + math.exp(exponent))
    return _clamp(fraction, 0.0, 1.0)


def _recovery_times_s(events: list[FullRunEvent], recovery_band_mpa: float) -> list[float]:
    recovery_times: list[float] = []
    out_of_band_since: float | None = None
    for event in events:
        is_out_of_band = abs(event.error_mpa) > recovery_band_mpa
        if is_out_of_band and out_of_band_since is None:
            out_of_band_since = event.elapsed_s
        elif not is_out_of_band and out_of_band_since is not None:
            recovery_times.append(max(0.0, event.elapsed_s - out_of_band_since))
            out_of_band_since = None
    return recovery_times


def _event_durations_s(events: list[FullRunEvent], default_duration_s: float) -> list[float]:
    durations: list[float] = []
    for index, event in enumerate(events):
        if index + 1 < len(events):
            duration = max(0.0, events[index + 1].elapsed_s - event.elapsed_s)
        else:
            duration = max(0.0, float(default_duration_s))
        durations.append(duration)
    return durations


def _current_hold_periods(
    events: list[FullRunEvent],
    event_durations_s: list[float],
    default_duration_s: float,
) -> list[dict[str, float]]:
    periods: list[dict[str, float]] = []
    active: dict[str, float] | None = None
    for event, duration_s in zip(events, event_durations_s, strict=False):
        duration_s = max(0.0, float(duration_s))
        if event.phase == "current_hold":
            if active is None:
                active = {
                    "start_s": event.elapsed_s,
                    "end_s": event.elapsed_s + duration_s,
                    "current_ma": event.current_ma,
                    "max_abs_error_mpa": abs(event.error_mpa),
                }
            else:
                active["end_s"] = event.elapsed_s + duration_s
                active["max_abs_error_mpa"] = max(active["max_abs_error_mpa"], abs(event.error_mpa))
        elif active is not None:
            active["duration_s"] = max(0.0, active["end_s"] - active["start_s"])
            periods.append(active)
            active = None
    if active is not None:
        if active["end_s"] <= active["start_s"]:
            active["end_s"] = active["start_s"] + max(0.0, float(default_duration_s))
        active["duration_s"] = max(0.0, active["end_s"] - active["start_s"])
        periods.append(active)
    return periods


def run_full_mini_dma_simulation(config: FullRunConfig) -> FullRunTrace:
    config = config.validated()
    state = _FullRunState(config)
    stop_reason = "completed"

    def _run_target_ramp(*, start_mpa: float, final_mpa: float) -> str | None:
        state.start_target_ramp(start_mpa, final_mpa)
        target_deadline = state.elapsed_s + config.target_ramp_timeout_s
        while state.elapsed_s <= target_deadline and len(state.events) < config.max_ticks:
            state.update_target_ramp()
            state.sample("target_ramp", rising=True)
            stop = _stop_for_safety(state)
            if stop is not None:
                return stop
            if not state.feedback_samples():
                state.advance_time()
                continue
            move_controller = state.controller_for_decision(min_recovery_mpa=config.controller.tolerance_mpa)
            correction = (
                0.0
                if _target_ramp_centered_for_move(state)
                else state.correct_toward_target(controller=move_controller)
            )
            state.record_event("target_ramp", correction, "target_acquisition", controller=move_controller)
            stop = _stop_for_safety(state)
            if stop is not None:
                return stop
            if state.target_ramp_complete() and _recovered(state):
                return None
            state.advance_time()
        return "target_ramp_timeout"

    def _run_sweep(*, start_ma: float, end_ma: float, phase_name: str, rising: bool) -> str | None:
        direction = 1.0 if end_ma >= start_ma else -1.0
        state.current_ma = start_ma
        current = start_ma
        endpoint_hold_s = 0.0
        current_hold_s = 0.0
        crossing_required_after_hold = False
        while len(state.events) < config.max_ticks:
            state.current_ma = current
            state.sample(phase_name, rising=rising)
            at_endpoint = abs(current - end_ma) <= 1e-12
            stop = _stop_for_safety(state)
            if stop is not None:
                return stop
            if not state.feedback_samples():
                if not at_endpoint:
                    current += direction * abs(config.sweep.rate_ma_s) * state.dt_s
                    if direction >= 0.0:
                        current = min(end_ma, current)
                    else:
                        current = max(end_ma, current)
                state.advance_time()
                continue
            recovered = _current_sweep_ready_to_advance(state)
            if recovered and crossing_required_after_hold:
                recovered = _current_sweep_crossed_target_for_resume(state, rising=rising)
            phase = phase_name
            reason = "current_tracking"
            if not recovered:
                phase = "current_hold"
                reason = "endpoint_waiting_for_recovery" if at_endpoint else "processed_recovery"
                crossing_required_after_hold = True
            move_controller = state.controller_for_decision(min_recovery_mpa=config.controller.tolerance_mpa)
            move_controller = state.controller_for_correction_phase(phase, move_controller)
            correction = 0.0 if recovered else state.correct_toward_target(controller=move_controller)
            state.record_event(phase, correction, reason, controller=move_controller)
            stop = _stop_for_safety(state)
            if stop is not None:
                return stop
            if not recovered:
                hold_extra_wait_s = config.current_hold_feedback_wait_s
                if abs(correction) > 0.0:
                    hold_extra_wait_s += config.current_hold_correction_feedback_wait_s
                if hold_extra_wait_s > 0.0:
                    hold_extra_wait_s = state.advance_with_passive_samples(
                        hold_extra_wait_s,
                        phase,
                        rising=rising,
                    )
                current_hold_s += state.dt_s + hold_extra_wait_s
                if at_endpoint:
                    endpoint_hold_s += state.dt_s + hold_extra_wait_s
                if current_hold_s >= config.endpoint_hold_timeout_s:
                    return "current_hold_timeout"
                state.advance_time()
                continue
            current_hold_s = 0.0
            crossing_required_after_hold = False
            if at_endpoint:
                if recovered:
                    return None
            else:
                current += direction * abs(config.sweep.rate_ma_s) * state.dt_s
                if direction >= 0.0:
                    current = min(end_ma, current)
                else:
                    current = max(end_ma, current)
            state.advance_time()
        return "tick_limit"

    targets = _target_sequence(config)
    ramp_start = config.target_ramp_start_mpa if config.target_ramp_start_mpa is not None else targets[0]
    for target_index, target_mpa in enumerate(targets):
        if stop_reason != "completed":
            break
        stop = _run_target_ramp(start_mpa=ramp_start, final_mpa=target_mpa)
        if stop is not None:
            stop_reason = stop
            break
        state.advance_time()
        stop = _run_sweep(
            start_ma=config.sweep.start_ma,
            end_ma=config.sweep.end_ma,
            phase_name="current",
            rising=config.sweep.end_ma >= config.sweep.start_ma,
        )
        if stop is not None:
            stop_reason = stop
            break
        if config.reverse_current:
            state.advance_time()
            stop = _run_sweep(
                start_ma=config.sweep.end_ma,
                end_ma=config.sweep.start_ma,
                phase_name="current_limit_unwind",
                rising=False,
            )
            if stop is not None:
                stop_reason = stop
                break
        if target_index < len(targets) - 1:
            if config.inter_target_free_length_shift_mm:
                state.apply_inter_target_free_length_shift()
            state.advance_time()
            ramp_start = target_mpa

    invariants = check_full_run_invariants(FullRunTrace(config, state.samples, state.events, stop_reason, {}, []))
    warnings = [name for name, passed in invariants.items() if not passed]
    return FullRunTrace(config, state.samples, state.events, stop_reason, invariants, warnings)


def check_full_run_invariants(trace: FullRunTrace) -> dict[str, bool]:
    events = trace.events
    endpoint_waits = [event for event in events if event.result == "endpoint_waiting_for_recovery"]
    endpoint_completions = [
        event
        for event in events
        if event.phase in {"current", "current_limit_unwind"}
        and abs(event.current_ma - trace.config.sweep.end_ma) <= 1e-9
    ]
    return {
        "no_load_stress_cruise": all(not event.cruise_allowed for event in events),
        "corrections_bounded": all(
            abs(event.correction_mm) <= _max_allowed_correction_mm(trace.config) + 1e-12
            for event in events
        ),
        "no_accumulated_correction_travel_stop": trace.stop_reason != "travel_limit",
        "endpoint_waits_when_unrecovered": all(not event.endpoint_recovered for event in endpoint_waits),
        "endpoint_completion_recovered": all(event.endpoint_recovered for event in endpoint_completions),
        "completed_run_recovered": trace.stop_reason != "completed"
        or not events
        or events[-1].endpoint_recovered,
        "does_not_stop_for_slack": trace.stop_reason != "slack_no_response",
        "scale_latency_applied": trace.config.scale_latency_s <= 0.0
        or all(event.feedback_age_s + 1e-12 >= trace.config.scale_latency_s for event in events),
    }


def full_run_scenario_by_name(name: str) -> FullRunConfig:
    base = FullRunConfig(
        name="baseline_first_overheating",
        description="Nominal full first-overheating run with endpoint recovery.",
        wire=VirtualWireConfig(
            length_mm=33.0,
            diameter_mm=0.0125,
            initial_motor_mm=0.030,
            elastic_stiffness_mpa_per_mm=650.0,
            transformation_onset_ma=20.0,
            transformation_end_ma=70.0,
            transformation_contraction_mm=0.006,
            noise_mpa=0.25,
        ),
        controller=RobustControllerConfig(target_stress_mpa=20.0, tolerance_mpa=0.5, min_recovery_mpa=1.0, max_correction_mm=0.005),
        sweep=CurrentSweepConfig(start_ma=1.0, end_ma=80.0, rate_ma_s=2.0, sample_hz=4.5),
        seed=101,
    )
    scenarios = {
        base.name: base,
        "realistic_first_overheating": replace(
            base,
            name="realistic_first_overheating",
            description=(
                "Good 12/2-style 50 MPa measurement with stress ramp, stepped transformation-driven "
                "stress/free-length changes, current holds, high strain, and reverse unwind."
            ),
            wire=replace(
                base.wire,
                length_mm=33.623,
                diameter_mm=0.0191,
                initial_motor_mm=0.0,
                elastic_stiffness_mpa_per_mm=100.0,
                transformation_onset_ma=24.0,
                transformation_end_ma=60.0,
                transformation_contraction_mm=3.45,
                transformation_hysteresis_ma=10.0,
                fluctuation_mpa=0.0,
                fluctuation_cycles=5.0,
                noise_mpa=1.2,
            ),
            controller=replace(
                base.controller,
                target_stress_mpa=50.0,
                tolerance_mpa=2.5,
                min_recovery_mpa=5.0,
                motor_step_mm=0.001,
                max_correction_mm=0.0275,
                safety_min_stress_mpa=None,
                stale_feedback_s=1.0,
            ),
            sweep=CurrentSweepConfig(start_ma=1.0, end_ma=80.0, rate_ma_s=0.29, sample_hz=2.0),
            target_ramp_start_mpa=0.0,
            target_ramp_rate_mpa_s=5.0,
            target_ramp_timeout_s=120.0,
            endpoint_hold_timeout_s=360.0,
            max_ticks=7000,
            zero_compression_stress=True,
            max_correction_strain_pct=0.12,
            reported_strain_motor_scale=1.0,
            reported_strain_offset_pct=-0.987508550099039,
            transformation_profile="stepped",
            rising_transformation_steps=(
                (24.0, 2.0, 0.08),
                (28.6, 0.45, 0.54),
                (41.8, 0.35, 0.32),
                (58.0, 2.0, 0.06),
            ),
            falling_transformation_steps=(
                (18.0, 2.0, 0.06),
                (29.0, 0.55, 0.30),
                (41.8, 0.45, 0.54),
                (52.0, 2.0, 0.10),
            ),
            transformation_kinetic_tau_s=3.0,
            seed=184,
        ),
        "bad_co6_first_overheating": replace(
            base,
            name="bad_co6_first_overheating",
            description=(
                "Bad Ni47Fe24Ga23Co6 2/1-style 50 MPa software-only stress case. "
                "It represents the stiff-validation run that broke/contact-lost around 10.6 mA "
                "after a very early transformation stress surge, long current holds, and low usable strain."
            ),
            wire=replace(
                base.wire,
                length_mm=45.869,
                diameter_mm=0.0151,
                initial_motor_mm=0.0,
                elastic_stiffness_mpa_per_mm=115.0,
                transformation_onset_ma=8.5,
                transformation_end_ma=17.0,
                transformation_contraction_mm=7.0,
                transformation_hysteresis_ma=7.0,
                fluctuation_mpa=8.0,
                fluctuation_cycles=7.0,
                noise_mpa=2.4,
                break_stress_mpa=240.0,
            ),
            controller=replace(
                base.controller,
                target_stress_mpa=50.0,
                tolerance_mpa=2.5,
                min_recovery_mpa=5.0,
                motor_step_mm=0.00125,
                max_correction_mm=0.006,
                safety_min_stress_mpa=None,
                safety_max_stress_mpa=260.0,
                stale_feedback_s=1.0,
            ),
            sweep=CurrentSweepConfig(start_ma=1.0, end_ma=40.0, rate_ma_s=0.40, sample_hz=4.0),
            target_ramp_start_mpa=0.0,
            target_ramp_rate_mpa_s=5.0,
            target_ramp_timeout_s=140.0,
            endpoint_hold_timeout_s=1200.0,
            max_ticks=9000,
            zero_compression_stress=True,
            max_correction_strain_pct=0.10,
            reported_strain_motor_scale=1.0,
            reported_strain_offset_pct=-0.95,
            transformation_profile="stepped",
            rising_transformation_steps=(
                (9.2, 0.25, 0.62),
                (10.5, 0.22, 0.26),
                (12.0, 0.40, 0.08),
                (16.0, 1.0, 0.04),
            ),
            falling_transformation_steps=(
                (7.0, 0.8, 0.42),
                (10.8, 0.5, 0.38),
                (15.0, 1.5, 0.20),
            ),
            transformation_kinetic_tau_s=0.45,
            seed=206,
        ),
        "realistic_run32_first_target": replace(
            base,
            name="realistic_run32_first_target",
            description=(
                "Ni50Fe27Ga23 12/2 test_run32 first 50 MPa target segment calibration. "
                "This uses the real 58.328 mm length, 19.1 um diameter, 1 mA/s current ramp, "
                "high hold fraction, hidden free-strain roughness, and a concentrated transformation burst near 30 mA."
            ),
            wire=replace(
                base.wire,
                length_mm=58.328,
                diameter_mm=0.0191,
                initial_motor_mm=0.0,
                elastic_stiffness_mpa_per_mm=58.0,
                transformation_onset_ma=24.0,
                transformation_end_ma=60.0,
                transformation_contraction_mm=58.328 * 10.26 / 100.0,
                transformation_hysteresis_ma=10.0,
                fluctuation_mpa=0.0,
                fluctuation_cycles=5.0,
                noise_mpa=1.8,
            ),
            controller=replace(
                base.controller,
                target_stress_mpa=50.0,
                tolerance_mpa=2.5,
                min_recovery_mpa=5.0,
                motor_step_mm=0.001,
                max_correction_mm=0.0275,
                safety_min_stress_mpa=None,
                stale_feedback_s=1.0,
            ),
            sweep=CurrentSweepConfig(start_ma=1.0, end_ma=80.0, rate_ma_s=1.0, sample_hz=2.0),
            target_ramp_start_mpa=0.0,
            target_ramp_rate_mpa_s=5.0,
            target_ramp_timeout_s=120.0,
            endpoint_hold_timeout_s=360.0,
            max_ticks=6000,
            zero_compression_stress=True,
            max_correction_strain_pct=0.16,
            reported_strain_motor_scale=1.0,
            reported_strain_offset_pct=0.0,
            transformation_profile="stepped",
            rising_transformation_steps=(
                (24.0, 2.0, 0.05),
                (30.5, 0.30, 0.70),
                (42.0, 0.35, 0.19),
                (58.0, 2.0, 0.06),
            ),
            falling_transformation_steps=(
                (18.0, 2.0, 0.06),
                (29.0, 0.55, 0.30),
                (41.8, 0.45, 0.54),
                (52.0, 2.0, 0.10),
            ),
            transformation_kinetic_tau_s=7.0,
            free_strain_fluctuation_pct=0.09,
            free_strain_fluctuation_cycles=10.0,
            seed=9203,
        ),
        "kosice_kern_first_overheating": replace(
            base,
            name="kosice_kern_first_overheating",
            description=(
                "Kosice KERN TEWJ 600-2M/B 50 MPa first-overheating run using today's "
                "Ni49Fe26Ga23Co2 3/6 mounted wire geometry, fast USB/KCP feedback, and "
                "0.01 g quantized scale readback. The app polls every 50 ms, but the "
                "observed full-session raw sample rate is about 16 Hz once request/response "
                "and UI work are included. The KERN profile keeps the base current-hold "
                "correction small and only opens the adaptive cap after response is earned."
            ),
            wire=replace(
                base.wire,
                length_mm=37.0,
                diameter_mm=0.0182,
                initial_motor_mm=0.0,
                elastic_stiffness_mpa_per_mm=95.0,
                transformation_onset_ma=24.0,
                transformation_end_ma=60.0,
                transformation_contraction_mm=37.0 * 6.3 / 100.0,
                transformation_hysteresis_ma=8.0,
                fluctuation_mpa=0.0,
                fluctuation_cycles=7.0,
                noise_mpa=3.0,
            ),
            controller=replace(
                base.controller,
                target_stress_mpa=50.0,
                tolerance_mpa=1.0,
                min_recovery_mpa=2.0,
                motor_step_mm=1.0 / 800.0,
                max_correction_mm=0.050,
                safety_min_stress_mpa=None,
                safety_max_stress_mpa=220.0,
                stale_feedback_s=0.45,
            ),
            sweep=CurrentSweepConfig(start_ma=1.0, end_ma=60.0, rate_ma_s=1.0, sample_hz=16.0),
            target_ramp_start_mpa=0.0,
            target_ramp_rate_mpa_s=8.0,
            target_ramp_timeout_s=80.0,
            endpoint_hold_timeout_s=240.0,
            max_ticks=9000,
            scale_latency_s=1.0 / 16.0,
            current_hold_feedback_wait_s=0.35,
            current_hold_correction_feedback_wait_s=0.20,
            scale_feedback=ScaleFeedbackConfig(name="kosice_kern", readability_g=0.01),
            zero_compression_stress=True,
            max_correction_strain_pct=0.08,
            adaptive_correction_cap_max_scale=1.25,
            reported_strain_motor_scale=1.0,
            reported_strain_offset_pct=0.0,
            transformation_profile="stepped",
            rising_transformation_steps=(
                (24.0, 2.0, 0.08),
                (32.0, 0.45, 0.34),
                (45.0, 0.40, 0.42),
                (58.0, 1.5, 0.16),
            ),
            falling_transformation_steps=(
                (18.0, 2.0, 0.05),
                (29.0, 0.55, 0.32),
                (42.0, 0.45, 0.48),
                (52.0, 1.5, 0.15),
            ),
            transformation_kinetic_tau_s=2.0,
            free_strain_fluctuation_pct=1.20,
            free_strain_fluctuation_cycles=8.0,
            seed=7306,
        ),
        "noisy_centered_first_overheating": replace(
            base,
            name="noisy_centered_first_overheating",
            description="High raw noise centered near target should avoid unnecessary chasing.",
            wire=replace(base.wire, fluctuation_mpa=0.0, noise_mpa=2.0, transformation_contraction_mm=0.0),
            seed=103,
        ),
        "low_strain_noisy_first_overheating": replace(
            base,
            name="low_strain_noisy_first_overheating",
            description=(
                "Low-strain 50 MPa wire where a small hidden transformation strain is mixed with "
                "larger stress noise/fluctuation, so the controller should not invent a high-strain curve."
            ),
            wire=replace(
                base.wire,
                length_mm=33.623,
                diameter_mm=0.0191,
                initial_motor_mm=0.0,
                elastic_stiffness_mpa_per_mm=500.0,
                transformation_onset_ma=24.0,
                transformation_end_ma=65.0,
                transformation_contraction_mm=0.08,
                transformation_hysteresis_ma=9.0,
                fluctuation_mpa=4.0,
                fluctuation_cycles=8.0,
                noise_mpa=1.8,
            ),
            controller=replace(
                base.controller,
                target_stress_mpa=50.0,
                tolerance_mpa=2.5,
                min_recovery_mpa=5.0,
                motor_step_mm=0.001,
                max_correction_mm=0.02,
                safety_min_stress_mpa=None,
                stale_feedback_s=1.0,
            ),
            sweep=CurrentSweepConfig(start_ma=1.0, end_ma=80.0, rate_ma_s=0.5, sample_hz=3.0),
            target_ramp_start_mpa=0.0,
            target_ramp_rate_mpa_s=8.0,
            target_ramp_timeout_s=90.0,
            endpoint_hold_timeout_s=240.0,
            max_ticks=6000,
            zero_compression_stress=True,
            max_correction_strain_pct=0.05,
            reported_strain_motor_scale=1.0,
            reported_strain_offset_pct=-0.30,
            seed=311,
        ),
        "transformation_recovery": replace(
            base,
            name="transformation_recovery",
            description="Transformation contraction forces current-hold mechanical recovery.",
            wire=replace(base.wire, transformation_contraction_mm=0.075, fluctuation_mpa=5.0, noise_mpa=0.5),
            controller=replace(base.controller, safety_min_stress_mpa=None),
            sweep=replace(base.sweep, rate_ma_s=20.0),
            seed=107,
        ),
        "reverse_unwind_recovery": replace(
            base,
            name="reverse_unwind_recovery",
            description="Reverse unwind must recover processed center before completing.",
            wire=replace(base.wire, initial_motor_mm=0.018, transformation_contraction_mm=0.045, noise_mpa=0.45),
            seed=109,
        ),
        "slack_after_unwind_takeup": replace(
            base,
            name="slack_after_unwind_takeup",
            description="Near-zero-load slack case keeps taking up tension until the processed center recovers.",
            wire=replace(base.wire, initial_motor_mm=-0.018, elastic_stiffness_mpa_per_mm=650.0, transformation_contraction_mm=0.0, noise_mpa=0.05),
            controller=replace(base.controller, max_correction_mm=0.002, safety_min_stress_mpa=None),
            zero_compression_stress=True,
            seed=113,
        ),
        "thin_wire_delayed_feedback": replace(
            base,
            name="thin_wire_delayed_feedback",
            description="Very thin wire with low sample cadence remains bounded.",
            wire=replace(base.wire, diameter_mm=0.0083, noise_mpa=0.35, transformation_contraction_mm=0.03),
            sweep=replace(base.sweep, sample_hz=1.2),
            controller=replace(base.controller, max_correction_mm=0.02, stale_feedback_s=1.2),
            seed=127,
        ),
    }
    realistic = scenarios["realistic_first_overheating"]
    scenarios["stress_ladder_50_100_after_unwind"] = replace(
        realistic,
        name="stress_ladder_50_100_after_unwind",
        description=(
            "Good-wire 50 MPa then 100 MPa target ladder with a post-unwind free-length "
            "elongation/slack disturbance before the second target ramp."
        ),
        controller=replace(
            realistic.controller,
            safety_max_stress_mpa=220.0,
        ),
        target_stress_sequence_mpa=(50.0, 100.0),
        target_ramp_start_mpa=0.0,
        target_ramp_rate_mpa_s=5.0,
        target_ramp_max_lead_fraction=0.10,
        target_ramp_timeout_s=220.0,
        endpoint_hold_timeout_s=420.0,
        max_ticks=14000,
        inter_target_free_length_shift_mm=-0.35,
        max_correction_strain_pct=0.30,
        seed=501,
    )
    try:
        return scenarios[name]
    except KeyError as exc:
        known = ", ".join(sorted(scenarios))
        raise ValueError(f"unknown full-run TMA simulator scenario {name!r}; known: {known}") from exc


def run_parameter_sweep() -> list[FullRunTrace]:
    traces: list[FullRunTrace] = []
    base = full_run_scenario_by_name("baseline_first_overheating")
    index = 0
    for diameter in (0.0083, 0.0125, 0.04):
        for stiffness in (250.0, 650.0, 1200.0):
            for noise in (0.2, 1.2):
                index += 1
                config = replace(
                    base,
                    name=f"sweep_d{diameter:g}_k{stiffness:g}_n{noise:g}",
                    description="Parameter sweep of diameter, stiffness, and noise.",
                    wire=replace(base.wire, diameter_mm=diameter, elastic_stiffness_mpa_per_mm=stiffness, noise_mpa=noise),
                    seed=1000 + index,
                )
                traces.append(run_full_mini_dma_simulation(config))
    return traces


def run_free_strain_stress_matrix() -> list[FullRunTrace]:
    """Run a broader real-run-inspired matrix without touching hardware."""

    base_good = full_run_scenario_by_name("realistic_first_overheating")
    base_bad = full_run_scenario_by_name("bad_co6_first_overheating")
    families = (
        {
            "name": "good_12_2_10pct",
            "base": base_good,
            "length_mm": 33.623,
            "diameter_mm": 0.0191,
            "stiffness": 100.0,
            "strain_pct": 10.3,
            "noise": 1.2,
            "end_ma": 80.0,
            "rate": 0.29,
            "cap_pct": 0.12,
            "offset_pct": -0.9875,
            "steps": base_good.rising_transformation_steps,
            "falling": base_good.falling_transformation_steps,
        },
        {
            "name": "early_19_8_9pct",
            "base": base_good,
            "length_mm": 61.0,
            "diameter_mm": 0.0089,
            "stiffness": 24.0,
            "strain_pct": 9.2,
            "noise": 1.8,
            "end_ma": 50.0,
            "rate": 0.24,
            "cap_pct": 0.11,
            "offset_pct": -0.2,
            "steps": ((22.0, 1.4, 0.08), (29.0, 0.50, 0.52), (38.5, 0.45, 0.32), (48.0, 1.5, 0.08)),
            "falling": ((18.0, 1.2, 0.12), (27.0, 0.50, 0.36), (39.0, 0.55, 0.42), (47.0, 1.6, 0.10)),
        },
        {
            "name": "co6_bad_1pct",
            "base": base_bad,
            "length_mm": 45.869,
            "diameter_mm": 0.0151,
            "stiffness": 115.0,
            "strain_pct": 1.0,
            "noise": 2.4,
            "end_ma": 40.0,
            "rate": 0.40,
            "cap_pct": 0.10,
            "offset_pct": -0.95,
            "steps": base_bad.rising_transformation_steps,
            "falling": base_bad.falling_transformation_steps,
        },
        {
            "name": "weak_noisy_0p25pct",
            "base": full_run_scenario_by_name("low_strain_noisy_first_overheating"),
            "length_mm": 33.623,
            "diameter_mm": 0.0191,
            "stiffness": 500.0,
            "strain_pct": 0.25,
            "noise": 2.2,
            "end_ma": 80.0,
            "rate": 0.50,
            "cap_pct": 0.05,
            "offset_pct": -0.30,
            "steps": ((25.0, 3.5, 0.35), (45.0, 5.0, 0.45), (62.0, 4.0, 0.20)),
            "falling": ((21.0, 3.0, 0.25), (42.0, 5.0, 0.50), (58.0, 4.0, 0.25)),
        },
    )
    perturbations = (
        {"name": "nominal", "tau": 3.0, "rough_pct": 0.0, "rough_cycles": 0.0, "latency": 0.2, "sample_hz": 2.0, "stiff_scale": 1.0, "cap_scale": 1.0},
        {"name": "fast_spiky", "tau": 0.45, "rough_pct": 0.08, "rough_cycles": 6.0, "latency": 0.2, "sample_hz": 4.0, "stiff_scale": 1.0, "cap_scale": 1.0},
        {"name": "rough_transform", "tau": 1.2, "rough_pct": 0.18, "rough_cycles": 9.0, "latency": 0.2, "sample_hz": 3.0, "stiff_scale": 1.0, "cap_scale": 1.0},
        {"name": "delayed_feedback", "tau": 3.5, "rough_pct": 0.06, "rough_cycles": 5.0, "latency": 0.45, "sample_hz": 1.5, "stiff_scale": 1.0, "cap_scale": 0.75},
        {"name": "soft_underestimated", "tau": 2.5, "rough_pct": 0.05, "rough_cycles": 4.0, "latency": 0.2, "sample_hz": 2.5, "stiff_scale": 0.45, "cap_scale": 0.8},
        {"name": "stiff_overresponsive", "tau": 1.8, "rough_pct": 0.05, "rough_cycles": 4.0, "latency": 0.2, "sample_hz": 2.5, "stiff_scale": 1.9, "cap_scale": 0.7},
    )
    traces: list[FullRunTrace] = []
    index = 0
    for family in families:
        for perturbation in perturbations:
            index += 1
            base = family["base"]
            length_mm = float(family["length_mm"])
            contraction_mm = length_mm * float(family["strain_pct"]) / 100.0
            cap_pct = float(family["cap_pct"]) * float(perturbation["cap_scale"])
            config = replace(
                base,
                name=f"matrix_{family['name']}_{perturbation['name']}",
                description=(
                    "Free-strain matrix case based on measured TMA wire behavior: "
                    f"{family['name']} with {perturbation['name']} perturbation."
                ),
                wire=replace(
                    base.wire,
                    length_mm=length_mm,
                    diameter_mm=float(family["diameter_mm"]),
                    elastic_stiffness_mpa_per_mm=float(family["stiffness"]) * float(perturbation["stiff_scale"]),
                    transformation_contraction_mm=contraction_mm,
                    noise_mpa=float(family["noise"]),
                    fluctuation_mpa=0.0,
                    break_stress_mpa=None,
                ),
                controller=replace(
                    base.controller,
                    target_stress_mpa=50.0,
                    tolerance_mpa=2.5,
                    min_recovery_mpa=5.0,
                    max_correction_mm=max(0.001, length_mm * cap_pct / 100.0),
                    safety_min_stress_mpa=None,
                    safety_max_stress_mpa=320.0,
                    stale_feedback_s=max(1.0, float(perturbation["latency"]) + 0.6),
                ),
                sweep=CurrentSweepConfig(
                    start_ma=1.0,
                    end_ma=float(family["end_ma"]),
                    rate_ma_s=float(family["rate"]),
                    sample_hz=float(perturbation["sample_hz"]),
                ),
                target_ramp_start_mpa=0.0,
                target_ramp_rate_mpa_s=5.0,
                target_ramp_timeout_s=160.0,
                endpoint_hold_timeout_s=600.0,
                max_ticks=9000,
                zero_compression_stress=True,
                max_correction_strain_pct=cap_pct,
                reported_strain_motor_scale=1.0,
                reported_strain_offset_pct=float(family["offset_pct"]),
                transformation_profile="stepped",
                rising_transformation_steps=family["steps"],
                falling_transformation_steps=family["falling"],
                transformation_kinetic_tau_s=float(perturbation["tau"]),
                free_strain_fluctuation_pct=float(perturbation["rough_pct"]),
                free_strain_fluctuation_cycles=float(perturbation["rough_cycles"]),
                scale_latency_s=float(perturbation["latency"]),
                seed=4000 + index,
            )
            traces.append(run_full_mini_dma_simulation(config))
    return traces


def run_control_policy_matrix() -> list[FullRunTrace]:
    """Compare adaptive percent-cap/recovery policies on representative matrix cases."""

    representative_names = {
        "matrix_good_12_2_10pct_delayed_feedback",
        "matrix_good_12_2_10pct_rough_transform",
        "matrix_good_12_2_10pct_stiff_overresponsive",
        "matrix_early_19_8_9pct_delayed_feedback",
        "matrix_weak_noisy_0p25pct_rough_transform",
    }
    base_configs = [
        trace.config
        for trace in run_free_strain_stress_matrix()
        if trace.config.name in representative_names
    ]
    ladder = full_run_scenario_by_name("stress_ladder_50_100_after_unwind")
    base_configs.append(ladder)
    base_configs.append(
        replace(
            ladder,
            name="stress_ladder_50_100_after_unwind_rough_transform",
            description=(
                "Good-wire 50 -> 100 MPa stress ladder with rough hidden free-strain "
                "fluctuations during transformation."
            ),
            free_strain_fluctuation_pct=0.16,
            free_strain_fluctuation_cycles=8.0,
            scale_latency_s=0.45,
            sweep=replace(ladder.sweep, sample_hz=1.5),
            controller=replace(ladder.controller, stale_feedback_s=1.2),
            seed=1501,
        )
    )
    traces: list[FullRunTrace] = []
    index = 0
    for config in base_configs:
        base_cap_pct = config.max_correction_strain_pct or (
            config.controller.max_correction_mm / config.wire.length_mm * 100.0
        )
        for cap_scale in (0.75, 1.0, 1.4, 2.4):
            for recovery_scale in (0.04, 0.06, 0.10):
                index += 1
                cap_pct = base_cap_pct * cap_scale
                target_scale_mpa = max(abs(target) for target in _target_sequence(config))
                recovery_mpa = max(config.controller.tolerance_mpa, target_scale_mpa * recovery_scale)
                policy_config = replace(
                    config,
                    name=f"policy_{config.name.removeprefix('matrix_')}_cap{cap_scale:g}_rec{recovery_scale:g}",
                    description=(
                        f"Policy comparison for {config.name}: correction cap {cap_scale:g}x "
                        f"the geometry percent cap and recovery band {recovery_scale:g}x target stress."
                    ),
                    controller=replace(
                        config.controller,
                        max_correction_mm=max(config.controller.motor_step_mm, config.wire.length_mm * cap_pct / 100.0),
                        min_recovery_mpa=recovery_mpa,
                    ),
                    max_correction_strain_pct=cap_pct,
                    seed=config.seed + 10000 + index,
                )
                traces.append(run_full_mini_dma_simulation(policy_config))
    return traces


def run_stress_ladder_matrix() -> list[FullRunTrace]:
    """Exercise 0 -> 50 -> 100 MPa target ladders on representative wire families."""

    base_ladder = full_run_scenario_by_name("stress_ladder_50_100_after_unwind")
    selected_matrix_names = {
        "matrix_good_12_2_10pct_stiff_overresponsive",
        "matrix_early_19_8_9pct_delayed_feedback",
        "matrix_co6_bad_1pct_fast_spiky",
        "matrix_weak_noisy_0p25pct_rough_transform",
    }
    traces = [run_full_mini_dma_simulation(base_ladder)]
    for trace in run_free_strain_stress_matrix():
        if trace.config.name not in selected_matrix_names:
            continue
        config = trace.config
        ladder_config = replace(
            config,
            name=f"ladder_{config.name.removeprefix('matrix_')}",
            description=(
                "Stress-ladder regression for post-unwind slack/stiffness behavior: "
                f"{config.description}"
            ),
            target_stress_sequence_mpa=(50.0, 100.0),
            target_ramp_start_mpa=0.0,
            target_ramp_max_lead_fraction=0.10,
            target_ramp_timeout_s=280.0,
            endpoint_hold_timeout_s=900.0,
            max_ticks=18000,
            inter_target_free_length_shift_mm=-max(0.20, config.wire.length_mm * 0.008),
            controller=replace(
                config.controller,
                target_stress_mpa=50.0,
                safety_max_stress_mpa=320.0,
            ),
            seed=config.seed + 70000,
        )
        traces.append(run_full_mini_dma_simulation(ladder_config))
    traces.append(
        run_full_mini_dma_simulation(
            replace(
                base_ladder,
                name="ladder_stiffer_thicker_high_load",
                description="Stiffer/thicker high-load 50 -> 100 MPa stress ladder.",
                wire=replace(
                    base_ladder.wire,
                    length_mm=28.0,
                    diameter_mm=0.040,
                    elastic_stiffness_mpa_per_mm=240.0,
                    transformation_contraction_mm=2.2,
                    noise_mpa=0.9,
                ),
                max_correction_strain_pct=0.18,
                inter_target_free_length_shift_mm=-0.25,
                seed=77001,
            )
        )
    )
    traces.append(
        run_full_mini_dma_simulation(
            replace(
                base_ladder,
                name="ladder_thin_1_2_high_strain_high_hold",
                description=(
                    "Real-run-inspired Ni50Fe26Ga24 1/2 thin-wire ladder: 8.3 um diameter, "
                    "about 15% hidden transformation strain, low maximum current, delayed feedback, "
                    "and high current-hold fraction."
                ),
                wire=replace(
                    base_ladder.wire,
                    length_mm=47.861,
                    diameter_mm=0.0083,
                    elastic_stiffness_mpa_per_mm=42.0,
                    transformation_contraction_mm=47.861 * 14.8 / 100.0,
                    transformation_hysteresis_ma=7.0,
                    noise_mpa=1.8,
                ),
                sweep=CurrentSweepConfig(start_ma=1.0, end_ma=30.0, rate_ma_s=0.18, sample_hz=1.6),
                controller=replace(
                    base_ladder.controller,
                    stale_feedback_s=1.4,
                    safety_max_stress_mpa=280.0,
                ),
                transformation_profile="stepped",
                rising_transformation_steps=(
                    (9.5, 0.9, 0.10),
                    (14.0, 0.45, 0.42),
                    (18.5, 0.55, 0.34),
                    (23.5, 1.3, 0.14),
                ),
                falling_transformation_steps=(
                    (8.0, 1.1, 0.16),
                    (13.0, 0.55, 0.36),
                    (18.0, 0.55, 0.34),
                    (23.0, 1.4, 0.14),
                ),
                transformation_kinetic_tau_s=3.5,
                max_correction_strain_pct=0.12,
                scale_latency_s=0.45,
                inter_target_free_length_shift_mm=-0.45,
                max_ticks=22000,
                seed=77003,
            )
        )
    )
    traces.append(
        run_full_mini_dma_simulation(
            replace(
                base_ladder,
                name="ladder_thin_delayed_tiny_load",
                description="Very thin delayed-feedback 50 -> 100 MPa stress ladder.",
                wire=replace(
                    base_ladder.wire,
                    length_mm=50.0,
                    diameter_mm=0.0083,
                    elastic_stiffness_mpa_per_mm=30.0,
                    transformation_contraction_mm=4.0,
                    noise_mpa=1.3,
                ),
                sweep=replace(base_ladder.sweep, end_ma=55.0, rate_ma_s=0.24, sample_hz=1.4),
                controller=replace(
                    base_ladder.controller,
                    stale_feedback_s=1.3,
                    safety_max_stress_mpa=260.0,
                ),
                max_correction_strain_pct=0.12,
                scale_latency_s=0.45,
                inter_target_free_length_shift_mm=-0.35,
                seed=77002,
            )
        )
    )
    return traces


def run_stress_ladder_candidate_policy_comparison() -> list[FullRunTrace]:
    """Compare baseline ladder cases with a moderate target-lead/cap candidate."""

    traces: list[FullRunTrace] = []
    for baseline in run_stress_ladder_matrix():
        traces.append(baseline)
        config = baseline.config
        base_cap_pct = config.max_correction_strain_pct or (
            config.controller.max_correction_mm / config.wire.length_mm * 100.0
        )
        candidate = replace(
            config,
            name=f"candidate_{config.name}",
            description=(
                f"Candidate moderate-policy variant of {config.name}: target lead 0.07x "
                "and a 1.35x geometry-percent correction cap."
            ),
            target_ramp_max_lead_fraction=0.07,
            max_correction_strain_pct=base_cap_pct * 1.35,
            seed=config.seed + 881,
        )
        traces.append(run_full_mini_dma_simulation(candidate))
    return traces


def run_stress_ladder_combined_policy_grid(
    *,
    lead_fractions: tuple[float, ...] = (0.03, 0.05, 0.07, 0.10),
    cap_scales: tuple[float, ...] = (1.0, 1.35, 1.5),
    adaptive_scales: tuple[float, ...] = (1.0, 2.0, 3.0),
) -> list[FullRunTrace]:
    """Run a broad 0 -> 50 -> 100 MPa ladder policy grid."""

    traces: list[FullRunTrace] = []
    base_configs = [trace.config for trace in run_stress_ladder_matrix()]
    for lead_fraction in lead_fractions:
        for cap_scale in cap_scales:
            for adaptive_scale in adaptive_scales:
                for case_index, config in enumerate(base_configs, start=1):
                    base_cap_pct = config.max_correction_strain_pct or (
                        config.controller.max_correction_mm / config.wire.length_mm * 100.0
                    )
                    stable_seed = (
                        config.seed
                        + 990000
                        + int(round(lead_fraction * 1000.0)) * 10000
                        + int(round(cap_scale * 100.0)) * 100
                        + int(round(adaptive_scale * 10.0)) * 10
                        + case_index
                    )
                    policy_config = replace(
                        config,
                        name=(
                            f"combined_l{lead_fraction:g}_c{cap_scale:g}_a{adaptive_scale:g}_"
                            f"{config.name}"
                        ),
                        description=(
                            f"Combined ladder policy grid for {config.name}: target lead "
                            f"{lead_fraction:g}x, geometry-percent cap {cap_scale:g}x, "
                            f"response-gated adaptive ceiling {adaptive_scale:g}x."
                        ),
                        target_ramp_max_lead_fraction=lead_fraction,
                        max_correction_strain_pct=base_cap_pct * cap_scale,
                        adaptive_correction_cap_max_scale=adaptive_scale,
                        seed=stable_seed,
                    )
                    traces.append(run_full_mini_dma_simulation(policy_config))
    return traces


CONTROL_VALIDATION_POLICIES = (
    "baseline",
    "moderate_response",
    "aggressive_cap",
    "crossing_moderate",
    "kern_fast_quantized",
)


def run_control_validation_suite(
    *,
    policies: tuple[str, ...] = CONTROL_VALIDATION_POLICIES,
) -> list[FullRunTrace]:
    """Compare production-candidate control policies on realistic full-run cases."""

    base_configs = [full_run_scenario_by_name("realistic_run32_first_target")]
    base_configs.append(full_run_scenario_by_name("kosice_kern_first_overheating"))
    base_configs.extend(trace.config for trace in run_stress_ladder_matrix())
    traces: list[FullRunTrace] = []
    for policy_index, policy in enumerate(policies, start=1):
        if policy not in CONTROL_VALIDATION_POLICIES:
            known = ", ".join(CONTROL_VALIDATION_POLICIES)
            raise ValueError(f"unknown TMA validation policy {policy!r}; known: {known}")
        for case_index, config in enumerate(base_configs, start=1):
            traces.append(run_full_mini_dma_simulation(_control_validation_config(config, policy, policy_index, case_index)))
    return traces


def _control_validation_config(
    config: FullRunConfig,
    policy: str,
    policy_index: int,
    case_index: int,
) -> FullRunConfig:
    base_cap_pct = config.max_correction_strain_pct or (
        config.controller.max_correction_mm / config.wire.length_mm * 100.0
    )
    policy_config = replace(
        config,
        name=f"validation_{policy}__{config.name}",
        description=f"Control-validation policy {policy} applied to {config.description}",
        seed=config.seed + 310000 + case_index,
    )
    if policy == "baseline":
        return policy_config

    cap_scale = 1.35
    adaptive_scale = 2.0
    requires_crossing = False
    seed_policy_index = policy_index
    if policy == "aggressive_cap":
        cap_scale = 2.0
    elif policy == "crossing_moderate":
        requires_crossing = True
    elif policy == "kern_fast_quantized":
        if config.scale_feedback.name == "kosice_kern":
            cap_scale = 1.15
            adaptive_scale = 1.25
        else:
            seed_policy_index = CONTROL_VALIDATION_POLICIES.index("moderate_response") + 1

    lead_fraction = config.target_ramp_max_lead_fraction
    if config.target_stress_sequence_mpa:
        lead_fraction = 0.05

    return replace(
        policy_config,
        target_ramp_max_lead_fraction=lead_fraction,
        max_correction_strain_pct=base_cap_pct * cap_scale,
        adaptive_correction_cap_max_scale=adaptive_scale,
        current_resume_requires_target_crossing=requires_crossing,
        seed=config.seed + 310000 + seed_policy_index * 10000 + case_index,
    )


def _kosice_offline_family_configs() -> list[FullRunConfig]:
    """Build geometry/noise/response families from the July 7-8 KERN envelope."""

    base = full_run_scenario_by_name("kosice_kern_first_overheating")
    families = (
        ("thin_long_slow", 62.832, 0.0135, 1050.0, 2.2, 0.45, 35.0),
        ("thin_mid_noisy", 54.630, 0.0139, 760.0, 3.8, 0.60, 50.0),
        ("fine_short_stiff", 51.612, 0.0121, 1320.0, 4.8, 0.28, 70.0),
        ("thicker_compliant", 56.110, 0.0189, 430.0, 1.6, 0.82, 90.0),
    )
    configs: list[FullRunConfig] = []
    for index, (family, length_mm, diameter_mm, stiffness, noise, response_tau_s, target_mpa) in enumerate(
        families,
        start=1,
    ):
        readability_stress_mpa = stress_mpa_from_load_g(0.01, diameter_mm)
        tolerance_mpa = max(1.0, readability_stress_mpa * 1.5, noise * 0.55)
        motor_step_mm = max(0.005, length_mm * 0.00016)
        configs.append(
            replace(
                base,
                name=f"kosice_family_{family}",
                description=(
                    "Cross-sample Košice KERN low-stress family derived from geometry, "
                    "0.01 g readability, measured noise, and delayed mechanical response."
                ),
                wire=replace(
                    base.wire,
                    length_mm=length_mm,
                    diameter_mm=diameter_mm,
                    elastic_stiffness_mpa_per_mm=stiffness,
                    transformation_onset_ma=8.0 + index,
                    transformation_end_ma=22.0 + index,
                    transformation_contraction_mm=length_mm * (0.018 + index * 0.002),
                    fluctuation_mpa=target_mpa * (0.035 + index * 0.008),
                    noise_mpa=noise,
                ),
                controller=replace(
                    base.controller,
                    target_stress_mpa=target_mpa,
                    tolerance_mpa=tolerance_mpa,
                    min_recovery_mpa=max(tolerance_mpa * 1.5, target_mpa * 0.04),
                    motor_step_mm=motor_step_mm,
                    max_correction_mm=max(motor_step_mm, length_mm * 0.0020),
                    safety_max_stress_mpa=target_mpa * 4.0 + 80.0,
                    stale_feedback_s=0.75,
                ),
                sweep=CurrentSweepConfig(start_ma=1.0, end_ma=28.0, rate_ma_s=1.4, sample_hz=20.0),
                target_ramp_start_mpa=0.0,
                target_ramp_rate_mpa_s=max(4.0, target_mpa * 0.10),
                target_ramp_timeout_s=120.0,
                endpoint_hold_timeout_s=180.0,
                max_ticks=12000,
                scale_latency_s=0.10,
                current_hold_feedback_wait_s=0.20,
                current_hold_correction_feedback_wait_s=0.05,
                reverse_current=False,
                max_correction_strain_pct=0.20,
                adaptive_correction_cap_max_scale=1.0,
                transformation_kinetic_tau_s=0.8 + index * 0.25,
                mechanical_response_tau_s=response_tau_s,
                free_strain_fluctuation_pct=0.18 + index * 0.04,
                free_strain_fluctuation_cycles=5.0 + index,
                response_initial_wait_s=max(0.25, response_tau_s * 0.65),
                response_timeout_s=max(2.4, response_tau_s * 4.0),
                seed=87000 + index,
            )
        )
    return configs


def run_kosice_offline_optimization(
    *,
    policies: tuple[str, ...] = KOSICE_OFFLINE_POLICIES,
) -> list[FullRunTrace]:
    """Compare overlapping-window control with response-synchronized candidates."""

    traces: list[FullRunTrace] = []
    for policy_index, policy in enumerate(policies, start=1):
        if policy not in KOSICE_OFFLINE_POLICIES:
            known = ", ".join(KOSICE_OFFLINE_POLICIES)
            raise ValueError(f"unknown Košice offline policy {policy!r}; known: {known}")
        for case_index, config in enumerate(_kosice_offline_family_configs(), start=1):
            synchronized = policy != "overlapping_window"
            damping = config.limit_cycle_damping
            if synchronized:
                damping = float(policy.removeprefix("response_sync_d")) / 100.0
            policy_config = replace(
                config,
                name=f"kosice_cv_{policy}__{config.name.removeprefix('kosice_family_')}",
                description=f"Košice cross-sample policy {policy}: {config.description}",
                response_synchronized=synchronized,
                limit_cycle_damping=damping,
                seed=config.seed + policy_index * 1000 + case_index,
            )
            traces.append(run_full_mini_dma_simulation(policy_config))
    return traces


def run_adaptive_control_policy_matrix() -> list[FullRunTrace]:
    """Compare response-gated adaptive cap ceilings on representative matrix cases."""

    representative_names = {
        "matrix_good_12_2_10pct_rough_transform",
        "matrix_early_19_8_9pct_delayed_feedback",
        "matrix_weak_noisy_0p25pct_rough_transform",
    }
    base_configs = [
        trace.config
        for trace in run_free_strain_stress_matrix()
        if trace.config.name in representative_names
    ]
    ladder = full_run_scenario_by_name("stress_ladder_50_100_after_unwind")
    base_configs.append(ladder)
    base_configs.append(
        replace(
            ladder,
            name="stress_ladder_50_100_after_unwind_rough_transform",
            description=(
                "Good-wire 50 -> 100 MPa stress ladder with rough hidden free-strain "
                "fluctuations during transformation."
            ),
            free_strain_fluctuation_pct=0.16,
            free_strain_fluctuation_cycles=8.0,
            scale_latency_s=0.45,
            sweep=replace(ladder.sweep, sample_hz=1.5),
            controller=replace(ladder.controller, stale_feedback_s=1.2),
            seed=2501,
        )
    )
    traces: list[FullRunTrace] = []
    index = 0
    for config in base_configs:
        for adaptive_scale in (1.0, 1.5, 2.0, 2.5, 3.0):
            index += 1
            policy_config = replace(
                config,
                name=f"adaptive_{config.name.removeprefix('matrix_')}_scale{adaptive_scale:g}",
                description=(
                    f"Adaptive cap comparison for {config.name}: base geometry percent cap "
                    f"with response-gated ceiling {adaptive_scale:g}x."
                ),
                adaptive_correction_cap_max_scale=adaptive_scale,
                seed=config.seed + 20000 + index,
            )
            traces.append(run_full_mini_dma_simulation(policy_config))
    return traces


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _full_measurement_rows(trace: FullRunTrace) -> Iterable[dict[str, Any]]:
    events_by_time = {round(event.elapsed_s, 9): event for event in trace.events}
    current_samples = _current_phase_samples(trace)
    first_current = current_samples[0] if current_samples else None
    first_current_free_strain = (
        _material_state_for_sample(trace.config, first_current)["free_transformation_strain_pct"]
        if first_current is not None
        else 0.0
    )
    first_current_measured_strain = 0.0 if first_current is None else first_current.strain_pct
    for sample in trace.samples:
        row = sample.to_row()
        event = events_by_time.get(round(sample.elapsed_s, 9))
        row_target = _target_stress_for_elapsed(trace, sample.elapsed_s) if event is None else event.target_stress_mpa
        material_state = _material_state_for_sample(trace.config, sample)
        current_phase = event is not None and event.phase in {"current", "current_hold", "current_limit_unwind"}
        tracking_error = ""
        if current_phase:
            free_delta = material_state["free_transformation_strain_pct"] - first_current_free_strain
            measured_delta = sample.strain_pct - first_current_measured_strain
            tracking_error = f"{measured_delta - free_delta:.9f}"
        resistance_ohm = 285.0 + 5.8 * sample.current_ma + 55.0 * sample.transformation_fraction
        voltage_v = sample.current_ma / 1000.0 * resistance_ohm
        row["target_stress_mpa"] = f"{row_target:.6f}"
        for key, value in material_state.items():
            row[key] = f"{value:.9f}"
        row["free_strain_tracking_error_pct"] = tracking_error
        row["recipe_mode"] = "simulated_current_sweep_stress"
        row["current_set_mA"] = f"{sample.current_ma:.6f}"
        row["current_measured_mA"] = f"{sample.current_ma:.6f}"
        row["voltage_V"] = f"{voltage_v:.6f}"
        row["resistance_ohm"] = f"{resistance_ohm:.6f}"
        row["power_W"] = f"{voltage_v * sample.current_ma / 1000.0:.9f}"
        row["scale_sample_rate_hz"] = f"{trace.config.sweep.sample_hz:.6f}"
        row["automation_phase"] = "" if event is None else event.phase
        row["current_hold_active"] = str(bool(event is not None and event.phase == "current_hold")).lower()
        row["decision"] = "" if event is None else event.decision
        row["result"] = "" if event is None else event.result
        row["processed_center_mpa"] = "" if event is None else f"{event.processed_center_mpa:.6f}"
        row["processed_noise_mpa"] = "" if event is None else f"{event.processed_noise_mpa:.6f}"
        row["processed_slope_mpa_s"] = "" if event is None else f"{event.processed_slope_mpa_s:.6f}"
        row["stress_error_mpa"] = "" if event is None else f"{event.error_mpa:.6f}"
        row["correction_mm"] = "" if event is None else f"{event.correction_mm:.9f}"
        row["correction_cap_mm"] = "" if event is None else f"{event.correction_cap_mm:.9f}"
        row["total_correction_travel_mm"] = "" if event is None else f"{event.total_travel_mm:.9f}"
        row["feedback_age_s"] = "" if event is None else f"{event.feedback_age_s:.6f}"
        row["endpoint_recovered"] = "" if event is None else str(bool(event.endpoint_recovered)).lower()
        row["processed_fresh"] = "" if event is None else str(bool(event.fresh)).lower()
        yield row


def _target_stress_at_elapsed(config: FullRunConfig, elapsed_s: float) -> float:
    start = config.target_ramp_start_mpa
    final = config.controller.target_stress_mpa
    if start is None:
        return final
    direction = 1.0 if final >= start else -1.0
    target = start + direction * config.target_ramp_rate_mpa_s * elapsed_s
    if direction >= 0.0:
        return min(final, target)
    return max(final, target)


def _target_stress_for_elapsed(trace: FullRunTrace, elapsed_s: float) -> float:
    if not trace.events:
        return _target_stress_at_elapsed(trace.config, elapsed_s)
    if elapsed_s < trace.events[0].elapsed_s:
        if trace.config.target_ramp_start_mpa is not None:
            return trace.config.target_ramp_start_mpa
        return _target_sequence(trace.config)[0]
    target = trace.events[0].target_stress_mpa
    for event in trace.events:
        if event.elapsed_s - elapsed_s > 1e-9:
            break
        target = event.target_stress_mpa
    return target


def write_full_run_outputs(trace: FullRunTrace, output_dir: Path | str) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    measurement_path = out / "measurement.csv"
    control_path = out / "control_trace.csv"
    summary_path = out / "summary.json"
    config_path = out / "config.json"
    report_path = out / "report.md"
    plot_path = out / "full_run.png"
    _write_csv(measurement_path, _full_measurement_rows(trace))
    _write_csv(control_path, (event.to_row() for event in trace.events))
    summary_path.write_text(json.dumps(trace.summary(), indent=2), encoding="utf-8")
    config_path.write_text(json.dumps(asdict(trace.config), indent=2), encoding="utf-8")
    _write_full_run_report(report_path, trace)
    paths = {
        "measurement": measurement_path,
        "control_trace": control_path,
        "summary": summary_path,
        "config": config_path,
        "report": report_path,
    }
    if _write_full_run_plot(plot_path, trace):
        paths["plot"] = plot_path
    return paths


def write_sweep_outputs(traces: list[FullRunTrace], output_dir: Path | str) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "full_run_sweep_summary.json"
    csv_path = out / "full_run_sweep_summary.csv"
    report_path = out / "full_run_sweep_report.md"
    summary = {"runs": [trace.summary() for trace in traces]}
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_csv(csv_path, summary["runs"])
    lines = [
        "# TMA full-run parameter sweep",
        "",
        "| Scenario | Quality | Stop | Current events | Later ramp error | Sweep error | Hold time | Measured strain span % | Mean tracking error | Adaptive scale | Max cap mm | Invariants |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for trace in traces:
        item = trace.summary()
        invariant_text = "ok" if all(item["invariants"].values()) else ",".join(item["warnings"])
        quality_text = item["quality_status"]
        if item["quality_flags"]:
            quality_text = f"{quality_text}: {','.join(item['quality_flags'])}"
        lines.append(
            f"| {item['scenario']} | {quality_text} | {item['stop_reason']} | {item['current_phase_event_count']} | "
            f"{item['max_abs_later_target_ramp_error_mpa']:.2f} MPa "
            f"max, {item['p95_abs_later_target_ramp_error_mpa']:.2f} p95 "
            f"({item['max_abs_later_target_ramp_error_fraction_of_target']:.2f}x) | "
            f"{item['max_abs_current_sweep_error_mpa']:.2f} MPa "
            f"max, {item['p95_abs_current_sweep_error_mpa']:.2f} p95 "
            f"({item['max_abs_current_sweep_error_fraction_of_target']:.2f}x) | "
            f"{item['current_hold_time_s']:.1f} | {item['strain_range_pct']:.3f} | "
            f"{item['mean_abs_free_strain_tracking_error_pct']:.3f} "
            f"({item['mean_abs_free_strain_tracking_error_fraction_of_span']:.2f}x) | "
            f"{item['adaptive_correction_cap_max_scale']:.2f} | {item['max_observed_correction_cap_mm']:.6f} | "
            f"{invariant_text} |"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    plot_path = out / "full_run_sweep_metrics.png"
    paths = {"summary": summary_path, "summary_csv": csv_path, "report": report_path}
    if _write_sweep_metrics_plot(plot_path, traces):
        paths["plot"] = plot_path
    policy_rank = _combined_policy_rankings(summary["runs"])
    if policy_rank:
        policy_rank_path = out / "stable_seed_policy_grid_ranked.json"
        policy_rank_path.write_text(json.dumps(policy_rank, indent=2), encoding="utf-8")
        paths["policy_rank"] = policy_rank_path
        policy_plot_path = out / "stable_seed_policy_grid_top18.png"
        if _write_combined_policy_rank_plot(policy_plot_path, policy_rank):
            paths["policy_plot"] = policy_plot_path
    validation_rank = _validation_policy_rankings(summary["runs"])
    if validation_rank:
        validation_rank_path = out / "control_validation_ranked.json"
        validation_rank_path.write_text(json.dumps(validation_rank, indent=2), encoding="utf-8")
        paths["validation_rank"] = validation_rank_path
        validation_plot_path = out / "control_validation_top.png"
        if _write_validation_policy_rank_plot(validation_plot_path, validation_rank):
            paths["validation_plot"] = validation_plot_path
    return paths


def _kosice_case_identity(name: str) -> tuple[str, str] | None:
    if not name.startswith("kosice_cv_") or "__" not in name:
        return None
    policy, family = name.removeprefix("kosice_cv_").split("__", 1)
    return policy, family


def _kosice_case_score(item: dict[str, Any]) -> float:
    target = max(abs(float(value)) for value in item["target_stress_sequence_mpa"])
    target = max(1.0, target)
    failure_penalty = 100.0 if item["stop_reason"] != "completed" else 0.0
    return (
        failure_penalty
        + float(item["p95_abs_current_sweep_error_fraction_of_target"]) * 12.0
        + float(item["max_abs_current_sweep_error_fraction_of_target"]) * 3.0
        + float(item["max_abs_target_ramp_error_mpa"]) / target * 2.0
        + float(item["current_hold_fraction_of_measurement"]) * 2.0
        + float(item["response_crossing_count"]) * 0.04
    )


def _kosice_offline_rankings(traces: list[FullRunTrace]) -> dict[str, Any]:
    rows = [trace.summary() for trace in traces]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_policy_family: dict[tuple[str, str], dict[str, Any]] = {}
    for item in rows:
        identity = _kosice_case_identity(str(item["scenario"]))
        if identity is None:
            continue
        policy, family = identity
        enriched = dict(item)
        enriched["offline_score"] = _kosice_case_score(item)
        grouped[policy].append(enriched)
        by_policy_family[(policy, family)] = enriched

    overall = []
    for policy, group in grouped.items():
        overall.append(
            {
                "policy": policy,
                "case_count": len(group),
                "completed": sum(item["stop_reason"] == "completed" for item in group),
                "mean_offline_score": statistics.fmean(item["offline_score"] for item in group),
                "mean_p95_error_fraction": statistics.fmean(
                    float(item["p95_abs_current_sweep_error_fraction_of_target"]) for item in group
                ),
                "mean_hold_fraction": statistics.fmean(
                    float(item["current_hold_fraction_of_measurement"]) for item in group
                ),
                "max_stress_error_mpa": max(float(item["max_abs_error_mpa"]) for item in group),
                "minimum_command_interval_s": min(
                    float(item["minimum_command_interval_s"])
                    for item in group
                    if int(item["response_command_count"]) > 1
                ),
                "mean_learned_stiffness_mpa_per_mm": (
                    statistics.fmean(
                        float(item["learned_stiffness_mpa_per_mm"])
                        for item in group
                        if item["learned_stiffness_mpa_per_mm"] is not None
                    )
                    if any(item["learned_stiffness_mpa_per_mm"] is not None for item in group)
                    else None
                ),
            }
        )
    overall.sort(key=lambda item: (-item["completed"], item["mean_offline_score"]))

    families = sorted({family for _, family in by_policy_family})
    folds = []
    for held_out in families:
        training = []
        for policy, group in grouped.items():
            train_rows = [item for item in group if not item["scenario"].endswith(f"__{held_out}")]
            training.append(
                {
                    "policy": policy,
                    "completed": sum(item["stop_reason"] == "completed" for item in train_rows),
                    "mean_offline_score": statistics.fmean(item["offline_score"] for item in train_rows),
                }
            )
        training.sort(key=lambda item: (-item["completed"], item["mean_offline_score"]))
        selected = training[0]["policy"]
        held = by_policy_family[(selected, held_out)]
        folds.append(
            {
                "held_out_family": held_out,
                "selected_policy": selected,
                "training_ranking": training,
                "held_out_stop_reason": held["stop_reason"],
                "held_out_offline_score": held["offline_score"],
                "held_out_p95_error_fraction": held["p95_abs_current_sweep_error_fraction_of_target"],
                "held_out_max_error_mpa": held["max_abs_error_mpa"],
            }
        )
    return {
        "calibration_evidence": {
            "source": "July 7-8 2026 Košice TMA_Logger runs (read-only)",
            "run_count": 6,
            "scale_feedback_hz_range": [18.98, 20.02],
            "processed_window_s": 1.8,
            "stack_heavy_median_command_interval_s_range": [0.251, 0.300],
            "source_length_mm_range": [51.612, 64.266],
            "source_diameter_mm_range": [0.0121, 0.0189],
            "observed_50_mpa_processed_center_range_mpa": [0.0, 417.9],
        },
        "ranking_method": "Leave one geometry/noise/response family out; lower score is better after completion count.",
        "overall": overall,
        "leave_one_family_out": folds,
        "production_integration_ready": False,
        "production_note": (
            "Offline evidence supports the response-synchronized family, but production integration requires "
            "review against production controller state and staged fake-driver/live safety validation."
        ),
    }


def _write_kosice_offline_rank_plot(path: Path, ranking: dict[str, Any]) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    overall = ranking["overall"]
    if not overall:
        return False
    labels = [item["policy"] for item in overall]
    x = list(range(len(overall)))
    completed_metrics = [item["completed"] == item["case_count"] for item in overall]
    fig, axes = plt.subplots(3, 1, figsize=(10, 9), constrained_layout=True)
    axes[0].bar(x, [item["mean_offline_score"] for item in overall], color="#2563eb")
    axes[0].set_ylabel("mean offline score")
    axes[0].set_title("Košice response-synchronized policy validation")
    axes[1].bar(
        x,
        [
            item["mean_p95_error_fraction"] if complete else math.nan
            for item, complete in zip(overall, completed_metrics)
        ],
        color="#dc2626",
    )
    axes[1].set_ylabel("mean p95 error / target")
    axes[2].bar(
        x,
        [
            item["mean_hold_fraction"] if complete else math.nan
            for item, complete in zip(overall, completed_metrics)
        ],
        color="#f59e0b",
    )
    axes[2].set_ylabel("mean hold fraction")
    axes[2].set_xticks(x)
    axes[2].set_xticklabels(labels, rotation=25, ha="right")
    for axis in axes:
        axis.grid(True, axis="y", alpha=0.25)
        if axis is not axes[2]:
            axis.set_xticks(x)
            axis.set_xticklabels([])
    for index, (item, complete) in enumerate(zip(overall, completed_metrics)):
        if complete:
            continue
        label = f"{item['completed']}/{item['case_count']} completed"
        axes[1].text(index, 0.02, label, rotation=90, ha="center", va="bottom", color="#991b1b")
        axes[2].text(index, 0.02, label, rotation=90, ha="center", va="bottom", color="#991b1b")
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return True


def write_kosice_offline_optimization_outputs(
    traces: list[FullRunTrace],
    output_dir: Path | str,
) -> dict[str, Path]:
    paths = write_sweep_outputs(traces, output_dir)
    out = Path(output_dir)
    ranking = _kosice_offline_rankings(traces)
    ranking_path = out / "kosice_offline_optimization_ranked.json"
    report_path = out / "kosice_offline_optimization_ranked.md"
    plot_path = out / "kosice_offline_optimization_ranked.png"
    ranking_path.write_text(json.dumps(ranking, indent=2), encoding="utf-8")
    lines = [
        "# Košice TMA offline optimization ranking",
        "",
        "The July 7-8 source runs were read only. Lower offline score is better after completed-case count.",
        "",
        "| Rank | Policy | Completed | Mean score | Mean p95 error / target | Mean hold fraction | Min command interval s |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, item in enumerate(ranking["overall"], start=1):
        lines.append(
            f"| {rank} | {item['policy']} | {item['completed']}/{item['case_count']} | "
            f"{item['mean_offline_score']:.4f} | {item['mean_p95_error_fraction']:.4f} | "
            f"{item['mean_hold_fraction']:.4f} | {item['minimum_command_interval_s']:.3f} |"
        )
    lines.extend(["", "## Leave-one-family-out", ""])
    for fold in ranking["leave_one_family_out"]:
        lines.append(
            f"- `{fold['held_out_family']}`: selected `{fold['selected_policy']}`, "
            f"held-out score {fold['held_out_offline_score']:.4f}, stop `{fold['held_out_stop_reason']}`."
        )
    lines.extend(["", "## Production status", "", ranking["production_note"], ""])
    report_path.write_text("\n".join(lines), encoding="utf-8")
    paths.update({"kosice_ranking": ranking_path, "kosice_report": report_path})
    if _write_kosice_offline_rank_plot(plot_path, ranking):
        paths["kosice_plot"] = plot_path
    return paths


def _combined_policy_rankings(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[float, float, float], list[dict[str, Any]]] = defaultdict(list)
    for item in summaries:
        key = _combined_policy_key(str(item.get("scenario", "")))
        if key is not None:
            groups[key].append(item)
    if not groups:
        return []

    rankings: list[dict[str, Any]] = []
    for (lead_fraction, cap_scale, adaptive_scale), group in groups.items():
        statuses = Counter(str(item.get("quality_status", "")) for item in group)
        flags = Counter(flag for item in group for flag in item.get("quality_flags", []))
        rankings.append(
            {
                "lead_fraction": lead_fraction,
                "cap_scale": cap_scale,
                "adaptive_scale": adaptive_scale,
                "case_count": len(group),
                "quality_score_sum": sum(float(item["quality_score"]) for item in group),
                "ok": statuses.get("ok", 0),
                "needs_tuning": statuses.get("needs_tuning", 0),
                "failed": statuses.get("failed", 0),
                "hold_time_sum_s": sum(float(item["current_hold_time_s"]) for item in group),
                "later_error_fraction_sum": sum(
                    float(item["max_abs_later_target_ramp_error_fraction_of_target"]) for item in group
                ),
                "current_error_fraction_sum": sum(
                    float(item["max_abs_current_sweep_error_fraction_of_target"]) for item in group
                ),
                "tracking_fraction_sum": sum(
                    float(item["mean_abs_free_strain_tracking_error_fraction_of_span"]) for item in group
                ),
                "max_total_travel_mm": max(float(item["max_total_travel_mm"]) for item in group),
                "flags": dict(flags),
            }
        )
    rankings.sort(key=lambda item: (item["failed"], -item["ok"], item["quality_score_sum"], item["hold_time_sum_s"]))
    return rankings


def _combined_policy_key(name: str) -> tuple[float, float, float] | None:
    parts = name.split("_", 4)
    if len(parts) < 5 or parts[0] != "combined":
        return None
    try:
        return (float(parts[1][1:]), float(parts[2][1:]), float(parts[3][1:]))
    except (IndexError, ValueError):
        return None


def _validation_policy_rankings(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in summaries:
        policy = _validation_policy_key(str(item.get("scenario", "")))
        if policy is not None:
            groups[policy].append(item)
    if not groups:
        return []

    rankings: list[dict[str, Any]] = []
    for policy, group in groups.items():
        statuses = Counter(str(item.get("quality_status", "")) for item in group)
        flags = Counter(flag for item in group for flag in item.get("quality_flags", []))
        run32 = next((item for item in group if str(item.get("scenario", "")).endswith("realistic_run32_first_target")), None)
        rankings.append(
            {
                "policy": policy,
                "case_count": len(group),
                "quality_score_sum": sum(float(item["quality_score"]) for item in group),
                "ok": statuses.get("ok", 0),
                "needs_tuning": statuses.get("needs_tuning", 0),
                "failed": statuses.get("failed", 0),
                "hold_time_sum_s": sum(float(item["current_hold_time_s"]) for item in group),
                "total_measurement_time_sum_s": sum(float(item["total_measurement_time_s"]) for item in group),
                "later_error_fraction_sum": sum(
                    float(item["max_abs_later_target_ramp_error_fraction_of_target"]) for item in group
                ),
                "current_error_fraction_sum": sum(
                    float(item["max_abs_current_sweep_error_fraction_of_target"]) for item in group
                ),
                "tracking_fraction_sum": sum(
                    float(item["mean_abs_free_strain_tracking_error_fraction_of_span"]) for item in group
                ),
                "max_total_travel_mm": max(float(item["max_total_travel_mm"]) for item in group),
                "run32_p95_current_sweep_error_mpa": (
                    None if run32 is None else float(run32["p95_abs_current_sweep_error_mpa"])
                ),
                "run32_hold_time_s": None if run32 is None else float(run32["current_hold_time_s"]),
                "flags": dict(flags),
            }
        )
    rankings.sort(key=lambda item: (item["failed"], -item["ok"], item["quality_score_sum"], item["hold_time_sum_s"]))
    return rankings


def _validation_policy_key(name: str) -> str | None:
    if not name.startswith("validation_") or "__" not in name:
        return None
    return name.split("__", 1)[0].removeprefix("validation_")


def _write_validation_policy_rank_plot(path: Path, rankings: list[dict[str, Any]]) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    if not rankings:
        return False

    labels = [item["policy"] for item in rankings]
    x = list(range(len(rankings)))
    fig, axes = plt.subplots(4, 1, figsize=(max(9, len(rankings) * 1.6), 11), constrained_layout=True)
    axes[0].bar(x, [item["quality_score_sum"] for item in rankings], color="#1f77b4")
    axes[0].set_ylabel("aggregate score")
    axes[0].set_title("TMA control-validation policy ranking")
    axes[1].bar(x, [item["hold_time_sum_s"] / 60.0 for item in rankings], color="#f59e0b")
    axes[1].set_ylabel("hold time (min)")
    axes[2].bar(x, [item["current_error_fraction_sum"] for item in rankings], color="#dc2626", label="current sweep")
    axes[2].bar(
        x,
        [item["later_error_fraction_sum"] for item in rankings],
        bottom=[item["current_error_fraction_sum"] for item in rankings],
        color="#7c3aed",
        label="later ramp",
    )
    axes[2].set_ylabel("error fraction sum")
    axes[2].legend(fontsize=8, loc="best")
    ok = [item["ok"] for item in rankings]
    needs = [item["needs_tuning"] for item in rankings]
    axes[3].bar(x, ok, color="#2ca02c", label="ok")
    axes[3].bar(x, needs, bottom=ok, color="#f59e0b", label="needs tuning")
    axes[3].bar(
        x,
        [item["failed"] for item in rankings],
        bottom=[ok_count + needs_count for ok_count, needs_count in zip(ok, needs, strict=True)],
        color="#d62728",
        label="failed",
    )
    axes[3].set_ylabel("case count")
    axes[3].legend(fontsize=8, loc="best")
    axes[3].set_xticks(x)
    axes[3].set_xticklabels(labels, rotation=25, ha="right")
    for ax in axes:
        ax.grid(True, axis="y", alpha=0.25)
        if ax is not axes[3]:
            ax.set_xticks(x)
            ax.set_xticklabels([])
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return True


def _write_combined_policy_rank_plot(path: Path, rankings: list[dict[str, Any]]) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    if not rankings:
        return False

    top = rankings[:18]
    labels = [
        f"l{item['lead_fraction']:g}/c{item['cap_scale']:g}/a{item['adaptive_scale']:g}"
        for item in top
    ]
    x = list(range(len(top)))
    fig, axes = plt.subplots(3, 1, figsize=(max(10, len(top) * 0.65), 10), constrained_layout=True)
    axes[0].bar(x, [item["quality_score_sum"] for item in top], color="#1f77b4")
    axes[0].set_ylabel("aggregate quality score")
    axes[0].set_title("TMA stress-ladder policy grid: top candidates")
    axes[1].bar(x, [item["hold_time_sum_s"] / 60.0 for item in top], color="#f59e0b")
    axes[1].set_ylabel("total hold time (min)")
    ok = [item["ok"] for item in top]
    axes[2].bar(x, ok, color="#2ca02c", label="ok cases")
    axes[2].bar(x, [item["failed"] for item in top], bottom=ok, color="#d62728", label="failed cases")
    axes[2].set_ylabel("case count")
    axes[2].legend(fontsize=8, loc="best")
    axes[2].set_xticks(x)
    axes[2].set_xticklabels(labels, rotation=45, ha="right")
    for ax in axes:
        ax.grid(True, axis="y", alpha=0.25)
        if ax is not axes[2]:
            ax.set_xticks(x)
            ax.set_xticklabels([])
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return True


def _write_sweep_metrics_plot(path: Path, traces: list[FullRunTrace]) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    summaries = [trace.summary() for trace in traces]
    if not summaries:
        return False
    labels = [item["scenario"].replace("matrix_", "") for item in summaries]
    max_error = [item["max_abs_current_sweep_error_mpa"] for item in summaries]
    hold_time = [item["current_hold_time_s"] for item in summaries]
    tracking = [item["mean_abs_free_strain_tracking_error_pct"] for item in summaries]
    strain_span = [item["strain_range_pct"] for item in summaries]
    free_span = [item["free_transformation_strain_range_pct"] for item in summaries]
    x = list(range(len(summaries)))
    fig, axes = plt.subplots(3, 1, figsize=(max(10, len(summaries) * 0.42), 9), sharex=True)
    axes[0].bar(x, max_error, color="#dc2626", label="max sweep stress error")
    axes[0].set_ylabel("MPa")
    axes[0].legend(fontsize=8, loc="best")
    axes[1].bar(x, hold_time, color="#f59e0b", label="current-hold time")
    axes[1].set_ylabel("s")
    axes[1].legend(fontsize=8, loc="best")
    axes[2].plot(x, free_span, color="#64748b", lw=1.2, marker="o", label="hidden free strain")
    axes[2].plot(x, strain_span, color="#111827", lw=1.2, marker="o", label="measured strain")
    axes[2].bar(x, tracking, color="#7c3aed", alpha=0.35, label="mean tracking error")
    axes[2].set_ylabel("%")
    axes[2].legend(fontsize=8, loc="best")
    axes[2].set_xticks(x)
    axes[2].set_xticklabels(labels, rotation=65, ha="right", fontsize=7)
    for ax in axes:
        ax.grid(True, axis="y", alpha=0.25)
    fig.suptitle("TMA full-run simulation matrix")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return True


def _write_full_run_report(path: Path, trace: FullRunTrace) -> None:
    item = trace.summary()
    invariants = "\n".join(
        f"- {name}: {'pass' if passed else 'FAIL'}"
        for name, passed in trace.invariants.items()
    )
    quality_flags = ", ".join(item["quality_flags"]) if item["quality_flags"] else "none"
    text = f"""# TMA full-run simulation

Scenario: `{trace.config.name}`

{trace.config.description}

- Stop reason: {trace.stop_reason}
- Quality status: {item["quality_status"]} (score {item["quality_score"]:.4f}; flags: {quality_flags})
- Total measurement time: {item["total_measurement_time_s"]:.3f} s
- Samples: {item["sample_count"]}
- Events: {item["event_count"]}
- Current-hold events: {item["current_hold_count"]}
- Current-hold time: {item["current_hold_time_s"]:.3f} s
- Endpoint hold/recovery events: {item["endpoint_hold_count"]}
- Maximum absolute stress error: {item["max_abs_error_mpa"]:.3f} MPa
- Maximum current-sweep stress error: {item["max_abs_current_sweep_error_mpa"]:.3f} MPa ({item["max_abs_current_sweep_error_fraction_of_target"]:.4f}x target)
- P95 current-sweep stress error: {item["p95_abs_current_sweep_error_mpa"]:.3f} MPa ({item["p95_abs_current_sweep_error_fraction_of_target"]:.4f}x target)
- Maximum later target-ramp reacquisition error: {item["max_abs_later_target_ramp_reacquisition_error_mpa"]:.3f} MPa
- Maximum later target-ramp stress error: {item["max_abs_later_target_ramp_error_mpa"]:.3f} MPa ({item["max_abs_later_target_ramp_error_fraction_of_target"]:.4f}x target)
- P95 later target-ramp stress error: {item["p95_abs_later_target_ramp_error_mpa"]:.3f} MPa ({item["p95_abs_later_target_ramp_error_fraction_of_target"]:.4f}x target)
- Time outside recovery band: {item["time_outside_recovery_band_s"]:.3f} s
- Maximum recovery time: {item["max_recovery_time_s"]:.3f} s
- Mean recovery time: {item["mean_recovery_time_s"]:.3f} s
- Strain range: {item["strain_min_pct"]:.4f}% to {item["strain_max_pct"]:.4f}% ({item["strain_range_pct"]:.4f}% span)
- Free transformation strain range: {item["free_transformation_strain_min_pct"]:.4f}% to {item["free_transformation_strain_max_pct"]:.4f}% ({item["free_transformation_strain_range_pct"]:.4f}% span)
- Maximum free-strain tracking error: {item["max_abs_free_strain_tracking_error_pct"]:.4f}% ({item["max_abs_free_strain_tracking_error_fraction_of_span"]:.4f}x span)
- Mean free-strain tracking error: {item["mean_abs_free_strain_tracking_error_pct"]:.4f}% ({item["mean_abs_free_strain_tracking_error_fraction_of_span"]:.4f}x span)
- Effective correction cap: {item["effective_max_correction_mm"]:.6f} mm
- Adaptive correction cap ceiling: {item["adaptive_correction_cap_max_scale"]:.3f}x ({item["effective_max_adaptive_correction_mm"]:.6f} mm)
- Maximum observed correction cap: {item["max_observed_correction_cap_mm"]:.6f} mm
- Maximum total correction travel: {item["max_total_travel_mm"]:.6f} mm
- Maximum single correction: {item["max_abs_correction_mm"]:.6f} mm

## Invariants

{invariants}
"""
    path.write_text(text, encoding="utf-8")


def _write_full_run_plot(path: Path, trace: FullRunTrace) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    elapsed = [sample.elapsed_s for sample in trace.samples]
    raw = [sample.raw_stress_mpa for sample in trace.samples]
    current = [sample.current_ma for sample in trace.samples]
    motor = [sample.motor_mm for sample in trace.samples]
    strain = [sample.strain_pct for sample in trace.samples]
    target = [_target_stress_for_elapsed(trace, sample.elapsed_s) for sample in trace.samples]
    event_t = [event.elapsed_s for event in trace.events]
    center = [event.processed_center_mpa for event in trace.events]
    corrections = [event.correction_mm for event in trace.events]
    current_phase_samples = _current_phase_samples(trace)
    sweep_current = [sample.current_ma for sample in current_phase_samples]
    sweep_strain = [sample.strain_pct for sample in current_phase_samples]
    if current_phase_samples:
        first_sweep = current_phase_samples[0]
        first_free_strain = _material_state_for_sample(trace.config, first_sweep)["free_transformation_strain_pct"]
        first_measured_strain = first_sweep.strain_pct
        sweep_free_strain = [
            first_measured_strain
            + _material_state_for_sample(trace.config, sample)["free_transformation_strain_pct"]
            - first_free_strain
            for sample in current_phase_samples
        ]
    else:
        sweep_free_strain = []
    hold_event_times = {round(event.elapsed_s, 9) for event in trace.events if event.phase == "current_hold"}
    hold_current = [
        sample.current_ma
        for sample in trace.samples
        if round(sample.elapsed_s, 9) in hold_event_times
    ]
    hold_strain = [
        sample.strain_pct
        for sample in trace.samples
        if round(sample.elapsed_s, 9) in hold_event_times
    ]
    fig, axes = plt.subplots(4, 1, figsize=(11, 10), sharex=False)
    axes[0].plot(elapsed, raw, color="#94a3b8", lw=0.8, label="raw stress")
    axes[0].plot(event_t, center, color="#dc2626", lw=1.2, label="processed center")
    axes[0].plot(elapsed, target, color="#111827", ls="--", lw=0.9, label="target")
    for event in trace.events:
        if event.phase == "current_hold":
            axes[0].axvspan(event.elapsed_s, event.elapsed_s + trace.config.sweep.sample_hz ** -1, color="#f59e0b", alpha=0.08, lw=0)
    axes[0].set_ylabel("MPa")
    axes[0].legend(fontsize=8, loc="best")
    if sweep_free_strain:
        axes[1].plot(
            sweep_current,
            sweep_free_strain,
            color="#64748b",
            lw=0.8,
            ls="--",
            label="free strain reference",
        )
    axes[1].plot(sweep_current, sweep_strain, color="#111827", lw=0.9, label="measured strain")
    if hold_current:
        axes[1].scatter(hold_current, hold_strain, color="#f59e0b", s=8, alpha=0.7, label="current hold")
    axes[1].set_xlabel("Current (mA)")
    axes[1].set_ylabel("Strain (%)")
    axes[1].legend(fontsize=8, loc="best")
    current_line = axes[2].plot(elapsed, current, color="#2563eb", lw=1.0, label="current")
    axes[2].set_ylabel("Current (mA)", color="#2563eb")
    axes[2].tick_params(axis="y", labelcolor="#2563eb")
    motor_axis = axes[2].twinx()
    motor_line = motor_axis.plot(elapsed, motor, color="#059669", lw=1.0, label="motor")
    motor_axis.set_ylabel("Motor position (mm)", color="#059669")
    motor_axis.tick_params(axis="y", labelcolor="#059669")
    axes[2].legend(current_line + motor_line, ["current", "motor"], fontsize=8, loc="best")
    axes[3].bar(event_t, corrections, width=max(0.02, trace.config.sweep.sample_hz ** -1 * 0.6), color="#7c3aed")
    axes[3].set_ylabel("correction mm")
    axes[3].set_xlabel("Elapsed (s)")
    for ax in axes:
        ax.grid(True, alpha=0.25)
    fig.suptitle(f"{trace.config.name} | {trace.stop_reason}")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return True


def _stdout_summary(trace: FullRunTrace) -> dict[str, Any]:
    """Keep CLI output compact; detailed periods remain in artifact JSON/CSV."""

    summary = dict(trace.summary())
    summary.pop("current_hold_periods", None)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run software-only full TMA first-overheating simulations.")
    parser.add_argument("--scenario", action="append", choices=FULL_RUN_SCENARIOS)
    parser.add_argument("--sweep", action="store_true", help="Run the built-in parameter sweep.")
    parser.add_argument("--free-strain-matrix", action="store_true", help="Run the broad free-strain stress-test matrix.")
    parser.add_argument("--policy-matrix", action="store_true", help="Run representative correction-cap/recovery policy comparisons.")
    parser.add_argument("--stress-ladder-matrix", action="store_true", help="Run 0 -> 50 -> 100 MPa ladder cases across representative wires.")
    parser.add_argument("--stress-ladder-candidate-policy", action="store_true", help="Compare baseline stress ladders with the current moderate candidate policy.")
    parser.add_argument("--stress-ladder-policy-grid", action="store_true", help="Run the combined stress-ladder lead/cap/adaptive policy grid.")
    parser.add_argument("--control-validation", action="store_true", help="Rank candidate control policies on run32 and 0 -> 50 -> 100 MPa ladder cases.")
    parser.add_argument("--adaptive-policy-matrix", action="store_true", help="Run response-gated adaptive cap comparisons.")
    parser.add_argument(
        "--kosice-offline-optimization",
        action="store_true",
        help="Run cross-sample Košice response-synchronized policy validation.",
    )
    parser.add_argument("--out", type=Path, default=Path("artifacts/mini-dma-full-run-sim"))
    args = parser.parse_args(argv)

    traces: list[FullRunTrace]
    if args.kosice_offline_optimization:
        traces = run_kosice_offline_optimization()
        write_kosice_offline_optimization_outputs(traces, args.out)
    elif args.adaptive_policy_matrix:
        traces = run_adaptive_control_policy_matrix()
        write_sweep_outputs(traces, args.out)
    elif args.control_validation:
        traces = run_control_validation_suite()
        write_sweep_outputs(traces, args.out)
    elif args.stress_ladder_matrix:
        traces = run_stress_ladder_matrix()
        write_sweep_outputs(traces, args.out)
    elif args.stress_ladder_candidate_policy:
        traces = run_stress_ladder_candidate_policy_comparison()
        write_sweep_outputs(traces, args.out)
    elif args.stress_ladder_policy_grid:
        traces = run_stress_ladder_combined_policy_grid()
        write_sweep_outputs(traces, args.out)
    elif args.policy_matrix:
        traces = run_control_policy_matrix()
        write_sweep_outputs(traces, args.out)
    elif args.free_strain_matrix:
        traces = run_free_strain_stress_matrix()
        write_sweep_outputs(traces, args.out)
    elif args.sweep:
        traces = run_parameter_sweep()
        write_sweep_outputs(traces, args.out)
    else:
        names = args.scenario or list(FULL_RUN_SCENARIOS)
        traces = [run_full_mini_dma_simulation(full_run_scenario_by_name(name)) for name in names]
        for trace in traces:
            scenario_out = args.out / trace.config.name if len(traces) > 1 else args.out
            write_full_run_outputs(trace, scenario_out)
    print(json.dumps({"runs": [_stdout_summary(trace) for trace in traces]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
