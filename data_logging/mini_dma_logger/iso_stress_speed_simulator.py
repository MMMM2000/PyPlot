"""Closed-loop, software-only Mini DMA iso-stress speed-policy simulator.

This module is intentionally independent of Qt, serial, PSU, scale, and Tic
drivers.  It compares hold/resume policy shapes; it is not a digital twin and
must not be used to derive hardware safety limits.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable


POLICY_BASELINE = "baseline"
POLICY_EVIDENCE = "evidence"
POLICY_EVIDENCE_PROBATION = "evidence_probation"
POLICY_PROPOSED = "proposed"
POLICY_CYCLE_CENTER_MOTOR = "cycle_center_motor"
POLICY_CYCLE_CENTER_RESUME = "cycle_center_resume"
POLICY_CYCLE_CENTER_COMBINED = "cycle_center_combined"
POLICIES = (
    POLICY_BASELINE,
    POLICY_EVIDENCE,
    POLICY_EVIDENCE_PROBATION,
    POLICY_PROPOSED,
    POLICY_CYCLE_CENTER_MOTOR,
    POLICY_CYCLE_CENTER_RESUME,
    POLICY_CYCLE_CENTER_COMBINED,
)
CYCLE_MOTOR_POLICIES = {
    POLICY_CYCLE_CENTER_MOTOR,
    POLICY_CYCLE_CENTER_COMBINED,
}
CYCLE_RESUME_POLICIES = {
    POLICY_CYCLE_CENTER_RESUME,
    POLICY_CYCLE_CENTER_COMBINED,
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = _clamp(fraction, 0.0, 1.0) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _median_absolute_deviation(values: list[float], center: float) -> float:
    if not values:
        return 0.0
    return statistics.median(abs(value - center) for value in values)


@dataclass(frozen=True)
class IsoStressPlantConfig:
    """Deterministic physical-ish plant and disturbance configuration."""

    target_stress_mpa: float = 100.0
    stiffness_mpa_per_mm: float = 600.0
    length_mm: float = 55.5
    current_start_ma: float = 1.0
    current_end_ma: float = 50.0
    requested_rate_ma_s: float = 0.4
    transformation_onset_ma: float = 22.0
    transformation_end_ma: float = 46.0
    transformation_hysteresis_ma: float = 4.0
    transformation_contraction_mm: float = 0.080
    physical_fluctuation_mpa: float = 16.0
    physical_fluctuation_hz: float = 0.13
    measurement_noise_mpa: float = 9.0
    noise_ar1: float = 0.90
    outlier_mpa: float = 0.0
    outlier_every_samples: int = 0
    sample_period_s: float = 0.25
    motor_effective_speed_mm_s: float = 0.0045
    motor_correction_interval_s: float = 1.0
    motor_response_tau_s: float = 0.0
    hold_resume_post_move_settle_s: float = 0.0
    motor_gain: float = 0.55
    motor_min_step_mm: float = 0.00125
    motor_max_step_mm: float = 0.006
    max_elapsed_s: float = 3600.0
    safety_max_abs_error_mpa: float = 120.0
    cadence_gap_every_samples: int = 0
    cadence_gap_s: float = 0.0

    def validated(self) -> "IsoStressPlantConfig":
        if self.stiffness_mpa_per_mm <= 0.0:
            raise ValueError("stiffness_mpa_per_mm must be positive")
        if self.length_mm <= 0.0:
            raise ValueError("length_mm must be positive")
        if self.current_end_ma <= self.current_start_ma:
            raise ValueError("current_end_ma must exceed current_start_ma")
        if self.requested_rate_ma_s <= 0.0:
            raise ValueError("requested_rate_ma_s must be positive")
        if self.transformation_end_ma <= self.transformation_onset_ma:
            raise ValueError("transformation_end_ma must exceed onset")
        if self.sample_period_s <= 0.0:
            raise ValueError("sample_period_s must be positive")
        if not 0.0 <= self.noise_ar1 < 1.0:
            raise ValueError("noise_ar1 must be in [0, 1)")
        if self.motor_min_step_mm <= 0.0 or self.motor_max_step_mm < self.motor_min_step_mm:
            raise ValueError("motor step bounds are invalid")
        if self.motor_correction_interval_s <= 0.0:
            raise ValueError("motor_correction_interval_s must be positive")
        if self.motor_response_tau_s < 0.0:
            raise ValueError("motor_response_tau_s must be non-negative")
        if self.hold_resume_post_move_settle_s < 0.0:
            raise ValueError("hold_resume_post_move_settle_s must be non-negative")
        if self.max_elapsed_s <= 0.0:
            raise ValueError("max_elapsed_s must be positive")
        return self


@dataclass(frozen=True)
class IsoStressPolicyConfig:
    """Hold/resume policy parameters shared by baseline and candidates."""

    name: str = POLICY_BASELINE
    fast_window_s: float = 1.8
    tolerance_mpa: float = 0.4335491174
    pause_min_mpa: float = 2.0
    resume_min_mpa: float = 1.0
    pause_factor: float = 3.0
    resume_factor: float = 1.5
    noise_sigma: float = 3.0
    noise_cap_tolerance_factor: float = 3.0
    hold_entry_confirm_s: float = 0.3
    baseline_resume_stable_s: float = 0.5
    correction_deadband_mpa: float = 1.0
    baseline_probation_s: float = 6.0
    baseline_probation_factor: float = 0.6
    slow_window_min_s: float = 3.0
    slow_window_max_s: float = 8.0
    slow_target_samples: int = 16
    evidence_required_s: float = 0.5
    evidence_cap_s: float = 1.5
    evidence_grey_decay: float = 0.5
    coherent_away_slope_mpa_s: float = 15.0
    probation_levels: tuple[float, ...] = (0.75, 0.9, 1.0)
    probation_level_s: float = 0.5
    probation_min_samples: int = 2
    risk_horizon_s: float = 0.75
    cycle_window_s: float = 20.0
    cycle_min_span_s: float = 10.0
    cycle_min_samples: int = 32
    cycle_current_reset_ma: float = 0.05
    cycle_center_band_mpa: float = 5.0
    cycle_drift_ratio_max: float = 0.15
    cycle_slope_max_mpa_s: float = 0.35
    cycle_fast_veto_mpa: float = 35.0
    cycle_evidence_required_s: float = 0.5
    cycle_probe_factor: float = 0.25
    cycle_probe_s: float = 2.0
    cycle_probe_min_samples: int = 8

    def validated(self) -> "IsoStressPolicyConfig":
        if self.name not in POLICIES:
            raise ValueError(f"unknown policy {self.name!r}")
        if self.fast_window_s <= 0.0:
            raise ValueError("fast_window_s must be positive")
        if self.pause_min_mpa <= self.resume_min_mpa:
            raise ValueError("pause_min_mpa must exceed resume_min_mpa")
        if not self.probation_levels or self.probation_levels[-1] != 1.0:
            raise ValueError("probation_levels must end at 1.0")
        if any(level <= 0.0 or level > 1.0 for level in self.probation_levels):
            raise ValueError("probation levels must be in (0, 1]")
        if self.cycle_window_s < self.cycle_min_span_s:
            raise ValueError("cycle window must cover the minimum cycle span")
        if self.cycle_min_samples < 3:
            raise ValueError("cycle_min_samples must be at least 3")
        if self.cycle_current_reset_ma <= 0.0:
            raise ValueError("cycle_current_reset_ma must be positive")
        if self.cycle_center_band_mpa <= 0.0:
            raise ValueError("cycle_center_band_mpa must be positive")
        if not 0.0 < self.cycle_probe_factor <= 1.0:
            raise ValueError("cycle_probe_factor must be in (0, 1]")
        return self


@dataclass(frozen=True)
class IsoStressScenario:
    name: str
    description: str
    plant: IsoStressPlantConfig = field(default_factory=IsoStressPlantConfig)


@dataclass(frozen=True)
class SignalSummary:
    center_mpa: float
    noise_mpa: float
    slope_mpa_s: float
    raw_min_mpa: float
    raw_max_mpa: float
    sample_count: int
    span_s: float


@dataclass(frozen=True)
class IsoStressSimulationRow:
    sample_index: int
    elapsed_s: float
    interval_s: float
    policy: str
    phase: str
    direction: int
    current_ma: float
    requested_rate_ma_s: float
    effective_rate_ma_s: float
    rate_multiplier: float
    target_stress_mpa: float
    true_stress_mpa: float
    measured_stress_mpa: float
    fast_center_mpa: float
    fast_noise_mpa: float
    fast_slope_mpa_s: float
    slow_center_mpa: float
    slow_noise_mpa: float
    slow_slope_mpa_s: float
    cycle_center_mpa: float
    cycle_slope_mpa_s: float
    cycle_span_mpa: float
    cycle_ready: bool
    cycle_stationary: bool
    pause_band_mpa: float
    resume_band_mpa: float
    evidence_s: float
    motor_mm: float
    motor_delta_mm: float
    transformation_fraction: float
    strain_pct: float
    decision: str

    def to_row(self) -> dict[str, Any]:
        result = asdict(self)
        for key, value in list(result.items()):
            if isinstance(value, float):
                result[key] = f"{value:.9g}"
        return result


@dataclass(frozen=True)
class IsoStressSimulationSummary:
    scenario: str
    policy: str
    seed: int
    completed: bool
    stop_reason: str
    elapsed_s: float
    hold_s: float
    hold_fraction: float
    max_continuous_hold_s: float
    median_continuous_hold_s: float
    p95_continuous_hold_s: float
    long_hold_time_fraction: float
    probation_s: float
    cycle_resumes: int
    cycle_motor_suppressions: int
    hold_entries: int
    reholds_within_3s: int
    p95_abs_true_error_mpa: float
    p99_abs_true_error_mpa: float
    max_abs_true_error_mpa: float
    p95_abs_measured_error_mpa: float
    time_outside_pause_s: float
    motor_travel_mm: float
    motor_reversals: int
    max_effective_rate_ma_s: float
    peak_strain_pct: float
    loop_area_abs_ma_pct: float
    samples: int


@dataclass(frozen=True)
class IsoStressSimulationResult:
    scenario: IsoStressScenario
    policy: IsoStressPolicyConfig
    seed: int
    rows: list[IsoStressSimulationRow]
    summary: IsoStressSimulationSummary


def policy_config(name: str) -> IsoStressPolicyConfig:
    return IsoStressPolicyConfig(name=name).validated()


def _scenario_map() -> dict[str, IsoStressScenario]:
    base = IsoStressPlantConfig()
    return {
        "prague_volatile": IsoStressScenario(
            name="prague_volatile",
            description="Prague-derived cadence and volatility with correlated transformation-band fluctuations.",
            plant=base,
        ),
        "prague_stationary_hunting": IsoStressScenario(
            name="prague_stationary_hunting",
            description=(
                "Prague-derived stationary stress oscillation near target with a 10.3 s cycle "
                "and displacement corrections that can hunt its phase."
            ),
            plant=replace(
                base,
                physical_fluctuation_mpa=12.0,
                physical_fluctuation_hz=1.0 / 10.33,
                measurement_noise_mpa=3.0,
                noise_ar1=0.75,
                motor_correction_interval_s=0.75,
                motor_response_tau_s=4.0,
                hold_resume_post_move_settle_s=1.0,
                motor_gain=0.70,
                max_elapsed_s=7200.0,
            ),
        ),
        "calm": IsoStressScenario(
            name="calm",
            description="Low-noise response used as a non-regression hold-out.",
            plant=replace(
                base,
                physical_fluctuation_mpa=0.6,
                measurement_noise_mpa=0.35,
                noise_ar1=0.45,
                motor_effective_speed_mm_s=0.006,
            ),
        ),
        "coherent_transformation": IsoStressScenario(
            name="coherent_transformation",
            description="Strong coherent transformation drift with modest measurement noise.",
            plant=replace(
                base,
                transformation_contraction_mm=0.115,
                physical_fluctuation_mpa=1.0,
                measurement_noise_mpa=0.6,
                noise_ar1=0.65,
            ),
        ),
        "sparse_feedback": IsoStressScenario(
            name="sparse_feedback",
            description="Prague-like signal with periodic cadence gaps approaching the recorded maximum gap.",
            plant=replace(base, cadence_gap_every_samples=17, cadence_gap_s=0.55),
        ),
        "heavy_tail": IsoStressScenario(
            name="heavy_tail",
            description="Correlated noise plus alternating isolated scale outliers.",
            plant=replace(base, outlier_mpa=14.0, outlier_every_samples=37),
        ),
    }


def scenario_by_name(name: str) -> IsoStressScenario:
    try:
        return _scenario_map()[name]
    except KeyError as exc:
        raise ValueError(f"unknown scenario {name!r}") from exc


def scenario_names() -> tuple[str, ...]:
    return tuple(_scenario_map())


def _transformation_fraction(current_ma: float, plant: IsoStressPlantConfig, direction: int) -> float:
    onset = plant.transformation_onset_ma
    end = plant.transformation_end_ma
    if direction < 0:
        onset -= plant.transformation_hysteresis_ma
        end -= plant.transformation_hysteresis_ma
    progress = _clamp((current_ma - onset) / max(1e-9, end - onset), 0.0, 1.0)
    return 0.5 - 0.5 * math.cos(math.pi * progress)


def _signal(samples: list[tuple[float, float]], *, window_s: float) -> SignalSummary:
    if not samples:
        return SignalSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0)
    end_s = samples[-1][0]
    window = [item for item in samples if item[0] >= end_s - max(1e-9, window_s)] or [samples[-1]]
    values = [item[1] for item in window]
    center = statistics.median(values)
    noise = 1.4826 * _median_absolute_deviation(values, center)
    slope = 0.0
    if len(window) >= 3:
        mid = max(1, len(window) // 2)
        first = window[:mid]
        second = window[mid:]
        if second:
            first_center = statistics.median(item[1] for item in first)
            second_center = statistics.median(item[1] for item in second)
            first_t = statistics.median(item[0] for item in first)
            second_t = statistics.median(item[0] for item in second)
            slope = (second_center - first_center) / max(1e-9, second_t - first_t)
    return SignalSummary(
        center_mpa=center,
        noise_mpa=noise,
        slope_mpa_s=slope,
        raw_min_mpa=min(values),
        raw_max_mpa=max(values),
        sample_count=len(window),
        span_s=max(0.0, window[-1][0] - window[0][0]),
    )


def _slow_signal(
    samples: list[tuple[float, float]],
    policy: IsoStressPolicyConfig,
    fast: SignalSummary,
) -> SignalSummary:
    if abs(fast.slope_mpa_s) >= policy.coherent_away_slope_mpa_s:
        window_s = policy.slow_window_min_s
    else:
        recent = [item for item in samples if item[0] >= samples[-1][0] - policy.slow_window_max_s]
        if len(recent) >= policy.slow_target_samples:
            cutoff_index = max(0, len(recent) - policy.slow_target_samples)
            window_s = _clamp(
                recent[-1][0] - recent[cutoff_index][0],
                policy.slow_window_min_s,
                policy.slow_window_max_s,
            )
        else:
            window_s = policy.slow_window_max_s
    return _signal(samples, window_s=window_s)


def _cycle_center_state(
    samples: list[tuple[float, float]],
    policy: IsoStressPolicyConfig,
) -> tuple[SignalSummary, bool, bool]:
    cycle = _signal(samples, window_s=policy.cycle_window_s)
    ready = (
        cycle.sample_count >= policy.cycle_min_samples
        and cycle.span_s >= policy.cycle_min_span_s
    )
    oscillation_span = max(0.0, cycle.raw_max_mpa - cycle.raw_min_mpa)
    endpoint_drift = abs(cycle.slope_mpa_s) * cycle.span_s
    drift_allowance = max(
        policy.cycle_center_band_mpa,
        oscillation_span * policy.cycle_drift_ratio_max,
    )
    stationary = (
        ready
        and abs(cycle.slope_mpa_s) <= policy.cycle_slope_max_mpa_s
        and endpoint_drift <= drift_allowance
    )
    return cycle, ready, stationary


def _bands(fast: SignalSummary, policy: IsoStressPolicyConfig) -> tuple[float, float]:
    bounded_noise = min(
        fast.noise_mpa * policy.noise_sigma,
        policy.tolerance_mpa * policy.noise_cap_tolerance_factor,
    )
    pause = max(
        policy.tolerance_mpa * policy.pause_factor,
        policy.pause_min_mpa,
        bounded_noise,
    )
    spans_target = fast.raw_min_mpa <= 0.0 <= fast.raw_max_mpa
    resume_noise = fast.noise_mpa * policy.noise_sigma if spans_target else 0.0
    resume = max(
        policy.tolerance_mpa * policy.resume_factor,
        policy.resume_min_mpa,
        resume_noise,
    )
    return pause, resume


def _rate_limiter(
    *,
    fast_error: float,
    fast: SignalSummary,
    slow: SignalSummary,
    pause_band: float,
    direction: int,
    policy: IsoStressPolicyConfig,
) -> float:
    away_slope = max(0.0, math.copysign(1.0, fast_error or 1.0) * fast.slope_mpa_s)
    disagreement = abs(fast.center_mpa - slow.center_mpa)
    uncertainty = min(pause_band * 0.30, fast.noise_mpa * 0.25 + disagreement * 0.20)
    risk = abs(fast_error) + policy.risk_horizon_s * away_slope + uncertainty
    margin_fraction = _clamp((pause_band - risk) / max(1e-9, pause_band), 0.0, 1.0)
    if margin_fraction >= 0.70:
        return 1.0
    if margin_fraction >= 0.45:
        return 0.9
    return 0.75


def _hold_episode_durations(rows: list[IsoStressSimulationRow]) -> list[float]:
    durations: list[float] = []
    duration_s = 0.0
    for row in rows:
        if row.phase == "hold":
            duration_s += row.interval_s
        elif duration_s > 0.0:
            durations.append(duration_s)
            duration_s = 0.0
    if duration_s > 0.0:
        durations.append(duration_s)
    return durations


def run_iso_stress_simulation(
    scenario: IsoStressScenario,
    policy: IsoStressPolicyConfig,
    *,
    seed: int,
) -> IsoStressSimulationResult:
    plant = scenario.plant.validated()
    policy = policy.validated()
    rng = random.Random(seed)
    rows: list[IsoStressSimulationRow] = []
    measured_samples: list[tuple[float, float]] = []
    fixed_current_samples: list[tuple[float, float]] = []
    fixed_current_anchor_ma = scenario.plant.current_start_ma
    elapsed_s = 0.0
    current_ma = plant.current_start_ma
    direction = 1
    motor_mm = 0.0
    effective_motor_mm = 0.0
    previous_motor_sign = 0
    motor_reversals = 0
    motor_travel = 0.0
    last_motor_move_s = -math.inf
    noise_state = 0.0
    phase = "ramp"
    hold_entry_candidate_s = 0.0
    resume_stable_s = 0.0
    evidence_s = 0.0
    probation_level = 0
    probation_level_elapsed_s = 0.0
    probation_level_samples = 0
    probe_elapsed_s = 0.0
    probe_samples = 0
    hold_s = 0.0
    hold_elapsed_s = 0.0
    max_continuous_hold_s = 0.0
    probation_s = 0.0
    hold_entries = 0
    cycle_resumes = 0
    cycle_motor_suppressions = 0
    last_resume_s: float | None = None
    reholds_within_3s = 0
    stop_reason = "max_elapsed"
    sample_index = 0

    while elapsed_s <= plant.max_elapsed_s:
        step_dt = plant.sample_period_s
        if (
            plant.cadence_gap_every_samples
            and sample_index
            and sample_index % plant.cadence_gap_every_samples == 0
        ):
            step_dt += plant.cadence_gap_s
        fraction = _transformation_fraction(current_ma, plant, direction)
        free_shift = fraction * plant.transformation_contraction_mm
        if plant.motor_response_tau_s > 0.0:
            response_fraction = 1.0 - math.exp(
                -step_dt / plant.motor_response_tau_s
            )
            effective_motor_mm += (
                motor_mm - effective_motor_mm
            ) * response_fraction
        else:
            effective_motor_mm = motor_mm
        transition_activity = math.sin(math.pi * fraction) ** 2
        physical_fluctuation = (
            plant.physical_fluctuation_mpa
            * transition_activity
            * math.sin(2.0 * math.pi * plant.physical_fluctuation_hz * elapsed_s + seed * 0.17)
        )
        true_stress = (
            plant.target_stress_mpa
            + plant.stiffness_mpa_per_mm * (effective_motor_mm + free_shift)
            + physical_fluctuation
        )
        innovation = rng.gauss(0.0, plant.measurement_noise_mpa)
        noise_state = plant.noise_ar1 * noise_state + math.sqrt(1.0 - plant.noise_ar1**2) * innovation
        outlier = 0.0
        if plant.outlier_every_samples and sample_index and sample_index % plant.outlier_every_samples == 0:
            outlier = plant.outlier_mpa if (sample_index // plant.outlier_every_samples) % 2 else -plant.outlier_mpa
        measured_stress = true_stress + noise_state + outlier
        measured_samples.append((elapsed_s, measured_stress - plant.target_stress_mpa))
        if abs(current_ma - fixed_current_anchor_ma) > policy.cycle_current_reset_ma:
            fixed_current_samples.clear()
            fixed_current_anchor_ma = current_ma
        fixed_current_samples.append(
            (elapsed_s, measured_stress - plant.target_stress_mpa)
        )
        buffer_cutoff_s = elapsed_s - max(
            policy.fast_window_s,
            policy.slow_window_max_s,
            policy.cycle_window_s,
        ) - 1.0
        while len(measured_samples) > 2 and measured_samples[1][0] < buffer_cutoff_s:
            del measured_samples[0]
        while (
            len(fixed_current_samples) > 2
            and fixed_current_samples[1][0] < buffer_cutoff_s
        ):
            del fixed_current_samples[0]
        fast = _signal(measured_samples, window_s=policy.fast_window_s)
        slow = _slow_signal(measured_samples, policy, fast)
        cycle, cycle_ready, cycle_stationary = _cycle_center_state(
            fixed_current_samples,
            policy,
        )
        fast_error = fast.center_mpa
        pause_band, resume_band = _bands(fast, policy)

        decision = "ramp"
        if phase != "hold" and abs(fast_error) > pause_band:
            hold_entry_candidate_s += step_dt
            if hold_entry_candidate_s >= policy.hold_entry_confirm_s:
                if last_resume_s is not None and elapsed_s - last_resume_s <= 3.0:
                    reholds_within_3s += 1
                phase = "hold"
                hold_entries += 1
                hold_elapsed_s = 0.0
                resume_stable_s = 0.0
                evidence_s = 0.0
                probe_elapsed_s = 0.0
                probe_samples = 0
                decision = "enter_hold"
        else:
            hold_entry_candidate_s = 0.0

        if phase == "hold":
            hold_s += step_dt
            hold_elapsed_s += step_dt
            max_continuous_hold_s = max(max_continuous_hold_s, hold_elapsed_s)
            cycle_resume_triggered = False
            slow_away = (
                fast_error * slow.slope_mpa_s > 0.0
                and abs(slow.slope_mpa_s) >= policy.coherent_away_slope_mpa_s
            )
            post_move_feedback_ready = (
                elapsed_s - last_motor_move_s
                >= plant.hold_resume_post_move_settle_s
            )
            if policy.name in {POLICY_BASELINE, POLICY_CYCLE_CENTER_MOTOR}:
                if abs(fast_error) <= resume_band and post_move_feedback_ready:
                    resume_stable_s += step_dt
                else:
                    resume_stable_s = 0.0
                should_resume = resume_stable_s >= policy.baseline_resume_stable_s
            elif policy.name in CYCLE_RESUME_POLICIES:
                if abs(fast_error) <= resume_band and post_move_feedback_ready:
                    resume_stable_s += step_dt
                else:
                    resume_stable_s = 0.0
                cycle_centered = (
                    cycle_stationary
                    and abs(cycle.center_mpa) <= policy.cycle_center_band_mpa
                    and abs(fast_error) <= policy.cycle_fast_veto_mpa
                    and abs(measured_stress - plant.target_stress_mpa)
                    <= policy.cycle_fast_veto_mpa
                    and post_move_feedback_ready
                )
                if cycle_centered:
                    evidence_s = min(
                        policy.evidence_cap_s,
                        evidence_s + step_dt,
                    )
                    decision = "earn_cycle_center_evidence"
                else:
                    evidence_s = 0.0
                cycle_resume_triggered = (
                    evidence_s >= policy.cycle_evidence_required_s
                )
                should_resume = (
                    resume_stable_s >= policy.baseline_resume_stable_s
                    or cycle_resume_triggered
                )
            else:
                if (
                    abs(fast_error) <= resume_band
                    and not slow_away
                    and post_move_feedback_ready
                ):
                    evidence_s = min(policy.evidence_cap_s, evidence_s + step_dt)
                    decision = "earn_resume_evidence"
                elif (
                    abs(fast_error) <= pause_band
                    and not slow_away
                    and post_move_feedback_ready
                ):
                    evidence_s = max(0.0, evidence_s - policy.evidence_grey_decay * step_dt)
                    decision = "decay_resume_evidence"
                else:
                    evidence_s = 0.0
                    decision = "reset_resume_evidence"
                should_resume = evidence_s >= policy.evidence_required_s
            if should_resume:
                last_resume_s = elapsed_s
                if cycle_resume_triggered:
                    cycle_resumes += 1
                if (
                    policy.name == POLICY_CYCLE_CENTER_COMBINED
                    and cycle_resume_triggered
                ):
                    phase = "probe"
                    probe_elapsed_s = 0.0
                    probe_samples = 0
                elif policy.name in {POLICY_EVIDENCE_PROBATION, POLICY_PROPOSED}:
                    phase = "probation"
                    probation_level = 0
                    probation_level_elapsed_s = 0.0
                    probation_level_samples = 0
                else:
                    phase = "ramp"
                resume_stable_s = 0.0
                evidence_s = 0.0
                decision = (
                    "resume_cycle_center"
                    if cycle_resume_triggered
                    else "resume"
                )

        rate_multiplier = 0.0
        if phase == "ramp":
            if (
                policy.name
                in {
                    POLICY_BASELINE,
                    POLICY_CYCLE_CENTER_MOTOR,
                    POLICY_CYCLE_CENTER_RESUME,
                    POLICY_CYCLE_CENTER_COMBINED,
                }
                and last_resume_s is not None
                and direction > 0
            ):
                rate_multiplier = (
                    policy.baseline_probation_factor
                    if elapsed_s - last_resume_s < policy.baseline_probation_s
                    else 1.0
                )
            else:
                rate_multiplier = 1.0
            if policy.name == POLICY_PROPOSED:
                rate_multiplier = min(
                    rate_multiplier,
                    _rate_limiter(
                        fast_error=fast_error,
                        fast=fast,
                        slow=slow,
                        pause_band=pause_band,
                        direction=direction,
                        policy=policy,
                    ),
                )
        elif phase == "probation":
            probation_s += step_dt
            rate_multiplier = policy.probation_levels[probation_level]
            if policy.name == POLICY_PROPOSED:
                rate_multiplier = min(
                    rate_multiplier,
                    _rate_limiter(
                        fast_error=fast_error,
                        fast=fast,
                        slow=slow,
                        pause_band=pause_band,
                        direction=direction,
                        policy=policy,
                    ),
                )
            probation_level_elapsed_s += step_dt
            probation_level_samples += 1
            if (
                probation_level_elapsed_s >= policy.probation_level_s
                and probation_level_samples >= policy.probation_min_samples
            ):
                probation_level += 1
                probation_level_elapsed_s = 0.0
                probation_level_samples = 0
                if probation_level >= len(policy.probation_levels) - 1:
                    phase = "ramp"
                    probation_level = len(policy.probation_levels) - 1
                    decision = "probation_complete"
                else:
                    decision = "probation_graduate"
        elif phase == "probe":
            probation_s += step_dt
            probe_elapsed_s += step_dt
            probe_samples += 1
            rate_multiplier = policy.cycle_probe_factor
            decision = "cycle_center_probe"
            if (
                probe_elapsed_s >= policy.cycle_probe_s
                and probe_samples >= policy.cycle_probe_min_samples
            ):
                phase = "ramp"
                decision = "cycle_center_probe_complete"

        motor_delta = 0.0
        motor_error = fast_error
        suppress_cycle_motor = False
        if (
            policy.name in CYCLE_MOTOR_POLICIES
            and phase == "hold"
            and cycle_stationary
        ):
            motor_error = cycle.center_mpa
            suppress_cycle_motor = (
                abs(cycle.center_mpa) <= policy.cycle_center_band_mpa
            )
        motor_due = (
            elapsed_s - last_motor_move_s >= plant.motor_correction_interval_s
        )
        if (
            suppress_cycle_motor
            and abs(fast_error) > policy.correction_deadband_mpa
            and motor_due
        ):
            cycle_motor_suppressions += 1
            decision = "suppress_cycle_phase_motor"
        elif (
            abs(motor_error) > policy.correction_deadband_mpa
            and motor_due
        ):
            desired = -motor_error / plant.stiffness_mpa_per_mm * plant.motor_gain
            max_by_speed = plant.motor_effective_speed_mm_s * step_dt
            max_step = max(
                plant.motor_min_step_mm,
                min(plant.motor_max_step_mm, max_by_speed),
            )
            magnitude = _clamp(abs(desired), plant.motor_min_step_mm, max_step)
            motor_delta = math.copysign(magnitude, desired)
            motor_mm += motor_delta
            last_motor_move_s = elapsed_s
            motor_travel += abs(motor_delta)
            motor_sign = 1 if motor_delta > 0.0 else -1
            if previous_motor_sign and motor_sign != previous_motor_sign:
                motor_reversals += 1
            previous_motor_sign = motor_sign

        effective_rate = plant.requested_rate_ma_s * rate_multiplier
        rows.append(
            IsoStressSimulationRow(
                sample_index=sample_index,
                elapsed_s=elapsed_s,
                interval_s=step_dt,
                policy=policy.name,
                phase=phase,
                direction=direction,
                current_ma=current_ma,
                requested_rate_ma_s=plant.requested_rate_ma_s,
                effective_rate_ma_s=effective_rate,
                rate_multiplier=rate_multiplier,
                target_stress_mpa=plant.target_stress_mpa,
                true_stress_mpa=true_stress,
                measured_stress_mpa=measured_stress,
                fast_center_mpa=plant.target_stress_mpa + fast.center_mpa,
                fast_noise_mpa=fast.noise_mpa,
                fast_slope_mpa_s=fast.slope_mpa_s,
                slow_center_mpa=plant.target_stress_mpa + slow.center_mpa,
                slow_noise_mpa=slow.noise_mpa,
                slow_slope_mpa_s=slow.slope_mpa_s,
                cycle_center_mpa=plant.target_stress_mpa + cycle.center_mpa,
                cycle_slope_mpa_s=cycle.slope_mpa_s,
                cycle_span_mpa=max(0.0, cycle.raw_max_mpa - cycle.raw_min_mpa),
                cycle_ready=cycle_ready,
                cycle_stationary=cycle_stationary,
                pause_band_mpa=pause_band,
                resume_band_mpa=resume_band,
                evidence_s=evidence_s,
                motor_mm=motor_mm,
                motor_delta_mm=motor_delta,
                transformation_fraction=fraction,
                strain_pct=(motor_mm + free_shift) / plant.length_mm * 100.0,
                decision=decision,
            )
        )

        if abs(true_stress - plant.target_stress_mpa) >= plant.safety_max_abs_error_mpa:
            stop_reason = "stress_safety"
            break
        if phase != "hold":
            current_ma += direction * effective_rate * step_dt
        if direction > 0 and current_ma >= plant.current_end_ma:
            current_ma = plant.current_end_ma
            direction = -1
            measured_samples.clear()
            fixed_current_samples.clear()
            fixed_current_anchor_ma = current_ma
            resume_stable_s = 0.0
            evidence_s = 0.0
            last_resume_s = None
            phase = "ramp"
            probe_elapsed_s = 0.0
            probe_samples = 0
        elif direction < 0 and current_ma <= plant.current_start_ma:
            current_ma = plant.current_start_ma
            stop_reason = "completed"
            elapsed_s += step_dt
            break

        elapsed_s += step_dt
        sample_index += 1

    true_errors = [abs(row.true_stress_mpa - plant.target_stress_mpa) for row in rows]
    measured_errors = [abs(row.measured_stress_mpa - plant.target_stress_mpa) for row in rows]
    time_outside_pause = sum(
        row.interval_s
        for row in rows
        if abs(row.true_stress_mpa - plant.target_stress_mpa) > row.pause_band_mpa
    )
    loop_area = 0.0
    for previous, current in zip(rows[:-1], rows[1:]):
        delta_current = current.current_ma - previous.current_ma
        loop_area += abs(delta_current * (current.strain_pct + previous.strain_pct) * 0.5)
    max_rate = max((row.effective_rate_ma_s for row in rows), default=0.0)
    elapsed_total = elapsed_s
    hold_episode_durations = _hold_episode_durations(rows)
    long_hold_time_s = sum(
        duration_s for duration_s in hold_episode_durations if duration_s >= 30.0
    )
    summary = IsoStressSimulationSummary(
        scenario=scenario.name,
        policy=policy.name,
        seed=seed,
        completed=stop_reason == "completed",
        stop_reason=stop_reason,
        elapsed_s=elapsed_total,
        hold_s=hold_s,
        hold_fraction=hold_s / max(1e-9, elapsed_total),
        max_continuous_hold_s=max_continuous_hold_s,
        median_continuous_hold_s=(
            statistics.median(hold_episode_durations)
            if hold_episode_durations
            else 0.0
        ),
        p95_continuous_hold_s=_percentile(hold_episode_durations, 0.95),
        long_hold_time_fraction=long_hold_time_s / max(1e-9, hold_s),
        probation_s=probation_s,
        cycle_resumes=cycle_resumes,
        cycle_motor_suppressions=cycle_motor_suppressions,
        hold_entries=hold_entries,
        reholds_within_3s=reholds_within_3s,
        p95_abs_true_error_mpa=_percentile(true_errors, 0.95),
        p99_abs_true_error_mpa=_percentile(true_errors, 0.99),
        max_abs_true_error_mpa=max(true_errors, default=0.0),
        p95_abs_measured_error_mpa=_percentile(measured_errors, 0.95),
        time_outside_pause_s=time_outside_pause,
        motor_travel_mm=motor_travel,
        motor_reversals=motor_reversals,
        max_effective_rate_ma_s=max_rate,
        peak_strain_pct=max((abs(row.strain_pct) for row in rows), default=0.0),
        loop_area_abs_ma_pct=loop_area,
        samples=len(rows),
    )
    return IsoStressSimulationResult(
        scenario=scenario,
        policy=policy,
        seed=seed,
        rows=rows,
        summary=summary,
    )


def run_policy_matrix(
    *,
    scenarios: Iterable[str] | None = None,
    policies: Iterable[str] | None = None,
    seeds: Iterable[int] = range(12),
) -> list[IsoStressSimulationResult]:
    scenario_list = list(scenarios or scenario_names())
    policy_list = list(policies or POLICIES)
    return [
        run_iso_stress_simulation(
            scenario_by_name(scenario_name),
            policy_config(policy_name),
            seed=seed,
        )
        for scenario_name in scenario_list
        for policy_name in policy_list
        for seed in seeds
    ]


def aggregate_summaries(results: Iterable[IsoStressSimulationResult]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[IsoStressSimulationSummary]] = {}
    for result in results:
        groups.setdefault((result.summary.scenario, result.summary.policy), []).append(result.summary)
    rows: list[dict[str, Any]] = []
    metrics = (
        "elapsed_s",
        "hold_s",
        "hold_fraction",
        "max_continuous_hold_s",
        "median_continuous_hold_s",
        "p95_continuous_hold_s",
        "long_hold_time_fraction",
        "probation_s",
        "cycle_resumes",
        "cycle_motor_suppressions",
        "hold_entries",
        "reholds_within_3s",
        "p95_abs_true_error_mpa",
        "p99_abs_true_error_mpa",
        "max_abs_true_error_mpa",
        "p95_abs_measured_error_mpa",
        "time_outside_pause_s",
        "motor_travel_mm",
        "motor_reversals",
        "peak_strain_pct",
        "loop_area_abs_ma_pct",
    )
    for (scenario, policy), summaries in sorted(groups.items()):
        row: dict[str, Any] = {
            "scenario": scenario,
            "policy": policy,
            "runs": len(summaries),
            "completed_runs": sum(summary.completed for summary in summaries),
            "safety_stops": sum(summary.stop_reason == "stress_safety" for summary in summaries),
            "max_effective_rate_ma_s": max(summary.max_effective_rate_ma_s for summary in summaries),
        }
        for metric in metrics:
            values = [float(getattr(summary, metric)) for summary in summaries]
            row[f"{metric}_median"] = statistics.median(values)
            row[f"{metric}_p95"] = _percentile(values, 0.95)
        rows.append(row)
    return rows


def comparison_rows(aggregate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = {(row["scenario"], row["policy"]): row for row in aggregate_rows}
    comparisons: list[dict[str, Any]] = []
    for scenario in scenario_names():
        baseline = indexed.get((scenario, POLICY_BASELINE))
        if baseline is None:
            continue
        for policy in POLICIES:
            candidate = indexed.get((scenario, policy))
            if candidate is None:
                continue
            elapsed_base = float(baseline["elapsed_s_median"])
            hold_base = float(baseline["hold_s_median"])
            comparisons.append(
                {
                    "scenario": scenario,
                    "policy": policy,
                    "elapsed_s_median": candidate["elapsed_s_median"],
                    "elapsed_change_pct": 100.0
                    * (float(candidate["elapsed_s_median"]) - elapsed_base)
                    / max(1e-9, elapsed_base),
                    "hold_s_median": candidate["hold_s_median"],
                    "hold_change_pct": 100.0
                    * (float(candidate["hold_s_median"]) - hold_base)
                    / max(1e-9, hold_base),
                    "p95_abs_true_error_mpa_median": candidate["p95_abs_true_error_mpa_median"],
                    "p95_error_change_pct": 100.0
                    * (
                        float(candidate["p95_abs_true_error_mpa_median"])
                        - float(baseline["p95_abs_true_error_mpa_median"])
                    )
                    / max(1e-9, float(baseline["p95_abs_true_error_mpa_median"])),
                    "time_outside_pause_s_median": candidate["time_outside_pause_s_median"],
                    "motor_travel_mm_median": candidate["motor_travel_mm_median"],
                    "reholds_within_3s_median": candidate["reholds_within_3s_median"],
                    "completed_runs": candidate["completed_runs"],
                    "safety_stops": candidate["safety_stops"],
                    "max_effective_rate_ma_s": candidate["max_effective_rate_ma_s"],
                }
            )
    return comparisons


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_policy_matrix_outputs(
    results: list[IsoStressSimulationResult],
    output_dir: Path | str,
) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    detail_rows = [asdict(result.summary) for result in results]
    aggregate_rows = aggregate_summaries(results)
    comparisons = comparison_rows(aggregate_rows)
    detail_path = out / "simulation_detail.csv"
    aggregate_path = out / "simulation_aggregate.csv"
    comparison_path = out / "policy_comparison.csv"
    summary_path = out / "summary.json"
    _write_csv(detail_path, detail_rows)
    _write_csv(aggregate_path, aggregate_rows)
    _write_csv(comparison_path, comparisons)
    summary_path.write_text(
        json.dumps(
            {
                "model_status": "closed-loop policy-shape simulator; not a hardware digital twin",
                "results": detail_rows,
                "aggregate": aggregate_rows,
                "comparison": comparisons,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    representative = next(
        (
            result
            for result in results
            if result.summary.scenario == "prague_stationary_hunting"
            and result.summary.policy == POLICY_CYCLE_CENTER_MOTOR
            and result.summary.seed == 0
        ),
        next(
            (
                result
                for result in results
                if result.summary.scenario == "prague_volatile"
                and result.summary.policy == POLICY_PROPOSED
                and result.summary.seed == 0
            ),
            results[0] if results else None,
        ),
    )
    trace_path = out / "representative_trace.csv"
    _write_csv(trace_path, [] if representative is None else [row.to_row() for row in representative.rows])
    return {
        "detail": detail_path,
        "aggregate": aggregate_path,
        "comparison": comparison_path,
        "summary": summary_path,
        "representative_trace": trace_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare current and proposed Mini DMA iso-stress speed policies offline.")
    parser.add_argument("--scenario", action="append", choices=scenario_names())
    parser.add_argument("--policy", action="append", choices=POLICIES)
    parser.add_argument("--seeds", type=int, default=12)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    results = run_policy_matrix(
        scenarios=args.scenario,
        policies=args.policy,
        seeds=range(max(1, args.seeds)),
    )
    paths = write_policy_matrix_outputs(results, args.out)
    print(json.dumps({"runs": len(results), "outputs": {key: str(value) for key, value in paths.items()}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
