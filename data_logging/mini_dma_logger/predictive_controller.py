"""Replay-first predictive controller primitives for Mini DMA current sweeps.

The classes in this module are intentionally independent from the Qt logger.
They model controller decisions from time-series snapshots so experiments can
be tested against saved runs before any live hardware path is wired in.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import math
import statistics
from typing import Iterable, Sequence


class PredictivePhase(StrEnum):
    STABLE_ELASTIC = "stable_elastic"
    TRANSFORMATION = "transformation"
    RECOVERY = "recovery"
    CURRENT_LIMITED = "current_limited"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PredictiveControllerTuning:
    target_tolerance_mpa: float = 0.25
    hold_pause_factor: float = 3.0
    hold_resume_factor: float = 1.5
    prediction_horizon_s: float = 2.5
    transformation_strain_per_mA: float = 0.015
    transformation_stress_slope_mpa_s: float = 0.8
    recovery_stress_slope_mpa_s: float = 0.35
    large_error_mpa: float = 18.0
    current_limit_voltage_ratio: float = 0.985
    minimum_ramp_scale: float = 0.35
    transformation_ramp_scale: float = 0.65
    recovery_ramp_scale: float = 0.85
    cautious_ramp_scale: float = 0.75
    motor_step_mm: float = 0.00125
    motor_gain: float = 0.72
    max_correction_mm: float = 0.05
    max_correction_stress_mpa: float = 10.0
    transformation_correction_factor: float = 0.55
    overshoot_penalty: float = 3.0
    reversal_penalty_mpa: float = 1.2
    motion_penalty_mpa_per_mm: float = 8.0


@dataclass(frozen=True)
class PredictiveSnapshot:
    elapsed_s: float
    stress_mpa: float
    target_mpa: float
    current_mA: float | None = None
    strain_pct: float | None = None
    voltage_V: float | None = None
    voltage_limit_V: float | None = None
    automation_phase: str | None = None


@dataclass(frozen=True)
class PredictiveFeatures:
    elapsed_s: float
    stress_mpa: float
    target_mpa: float
    stress_error_mpa: float
    stress_slope_mpa_s: float
    current_mA: float | None
    current_slope_mA_s: float
    strain_pct: float | None
    strain_per_mA: float
    stress_noise_mpa: float
    sample_count: int
    voltage_ratio: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        return asdict(self)


@dataclass(frozen=True)
class PredictiveAdvice:
    phase: PredictivePhase
    confidence: float
    ramp_scale: float
    hold_current: bool
    hold_reason: str
    predicted_error_mpa: float
    moving_away: bool
    recovery_priority: float

    def to_dict(self) -> dict[str, float | bool | str]:
        row = asdict(self)
        row["phase"] = self.phase.value
        return row


@dataclass(frozen=True)
class PredictiveMotorStep:
    correction_mm: float
    target_space_direction: float
    predicted_error_mpa: float
    cost: float
    reason: str

    def to_dict(self) -> dict[str, float | str]:
        return asdict(self)


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _linear_slope(x_values: Sequence[float], y_values: Sequence[float]) -> float:
    pairs = [
        (float(x), float(y))
        for x, y in zip(x_values, y_values, strict=False)
        if math.isfinite(float(x)) and math.isfinite(float(y))
    ]
    if len(pairs) < 2:
        return 0.0
    mean_x = sum(x for x, _ in pairs) / len(pairs)
    mean_y = sum(y for _, y in pairs) / len(pairs)
    denominator = sum((x - mean_x) ** 2 for x, _ in pairs)
    if denominator <= 0.0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in pairs) / denominator


def _mad(values: Sequence[float]) -> float:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return 0.0
    median = statistics.median(finite)
    return statistics.median(abs(value - median) for value in finite)


def estimate_predictive_features(
    samples: Iterable[PredictiveSnapshot],
    *,
    window_s: float = 2.5,
) -> PredictiveFeatures | None:
    ordered = sorted(samples, key=lambda item: float(item.elapsed_s))
    if not ordered:
        return None
    latest = ordered[-1]
    window_start = float(latest.elapsed_s) - max(0.05, float(window_s))
    recent = [item for item in ordered if float(item.elapsed_s) >= window_start]
    if len(recent) < 2:
        recent = ordered[-min(len(ordered), 2) :]
    times = [float(item.elapsed_s) for item in recent]
    stress = [float(item.stress_mpa) for item in recent]
    stress_slope = _linear_slope(times, stress)
    current_pairs = [
        (float(item.elapsed_s), float(item.current_mA))
        for item in recent
        if _finite(item.current_mA)
    ]
    current_slope = _linear_slope(
        [item[0] for item in current_pairs],
        [item[1] for item in current_pairs],
    )
    strain_current_pairs = [
        (float(item.current_mA), float(item.strain_pct))
        for item in recent
        if _finite(item.current_mA) and _finite(item.strain_pct)
    ]
    strain_per_mA = _linear_slope(
        [item[0] for item in strain_current_pairs],
        [item[1] for item in strain_current_pairs],
    )
    voltage_ratio = None
    if _finite(latest.voltage_V) and _finite(latest.voltage_limit_V) and float(latest.voltage_limit_V) > 0.0:
        voltage_ratio = float(latest.voltage_V) / float(latest.voltage_limit_V)
    return PredictiveFeatures(
        elapsed_s=float(latest.elapsed_s),
        stress_mpa=float(latest.stress_mpa),
        target_mpa=float(latest.target_mpa),
        stress_error_mpa=float(latest.stress_mpa) - float(latest.target_mpa),
        stress_slope_mpa_s=stress_slope,
        current_mA=None if latest.current_mA is None else float(latest.current_mA),
        current_slope_mA_s=current_slope,
        strain_pct=None if latest.strain_pct is None else float(latest.strain_pct),
        strain_per_mA=strain_per_mA,
        stress_noise_mpa=1.4826 * _mad(stress),
        sample_count=len(recent),
        voltage_ratio=voltage_ratio,
    )


def classify_predictive_phase(
    features: PredictiveFeatures,
    tuning: PredictiveControllerTuning | None = None,
) -> tuple[PredictivePhase, float]:
    tuning = tuning or PredictiveControllerTuning()
    error_abs = abs(float(features.stress_error_mpa))
    slope_abs = abs(float(features.stress_slope_mpa_s))
    strain_score = (
        abs(float(features.strain_per_mA))
        / max(1e-12, float(tuning.transformation_strain_per_mA))
    )
    slope_score = slope_abs / max(1e-12, float(tuning.transformation_stress_slope_mpa_s))
    error_score = error_abs / max(1e-12, float(tuning.target_tolerance_mpa) * tuning.hold_pause_factor)
    moving_away = features.stress_error_mpa * features.stress_slope_mpa_s > 0.0
    moving_toward = features.stress_error_mpa * features.stress_slope_mpa_s < 0.0
    if (
        features.voltage_ratio is not None
        and features.voltage_ratio >= tuning.current_limit_voltage_ratio
    ):
        return PredictivePhase.CURRENT_LIMITED, _clamp(features.voltage_ratio, 0.0, 1.0)
    if strain_score >= 1.0 or (moving_away and slope_score >= 1.0 and error_score >= 0.75):
        confidence = _clamp(max(strain_score, slope_score * 0.8, error_score * 0.6), 0.0, 1.0)
        return PredictivePhase.TRANSFORMATION, confidence
    if moving_toward and error_abs > tuning.target_tolerance_mpa * tuning.hold_resume_factor:
        confidence = _clamp(
            max(
                slope_abs / max(1e-12, tuning.recovery_stress_slope_mpa_s),
                error_score * 0.5,
            ),
            0.0,
            1.0,
        )
        return PredictivePhase.RECOVERY, confidence
    if features.sample_count < 2:
        return PredictivePhase.UNKNOWN, 0.0
    return PredictivePhase.STABLE_ELASTIC, _clamp(1.0 - max(strain_score, slope_score) * 0.5, 0.0, 1.0)


def advise_predictive_control(
    features: PredictiveFeatures,
    tuning: PredictiveControllerTuning | None = None,
) -> PredictiveAdvice:
    tuning = tuning or PredictiveControllerTuning()
    phase, confidence = classify_predictive_phase(features, tuning)
    predicted_error = (
        float(features.stress_error_mpa)
        + float(features.stress_slope_mpa_s) * max(0.0, float(tuning.prediction_horizon_s))
    )
    pause_band = max(
        tuning.target_tolerance_mpa * tuning.hold_pause_factor,
        features.stress_noise_mpa * 2.0,
    )
    moving_away = features.stress_error_mpa * features.stress_slope_mpa_s > 0.0
    hold_current = False
    hold_reason = ""
    ramp_scale = 1.0
    projection_hold_band = max(
        tuning.large_error_mpa * 0.75,
        pause_band * 4.0,
    )
    if phase == PredictivePhase.CURRENT_LIMITED:
        hold_current = True
        hold_reason = "voltage_ratio_near_limit"
        ramp_scale = tuning.minimum_ramp_scale
    elif phase == PredictivePhase.TRANSFORMATION:
        ramp_scale = _clamp(
            1.0 - (1.0 - tuning.transformation_ramp_scale) * confidence,
            tuning.minimum_ramp_scale,
            1.0,
        )
        if moving_away and (
            abs(predicted_error) > projection_hold_band
            or abs(features.stress_error_mpa) > tuning.large_error_mpa
        ):
            hold_current = True
            hold_reason = "transformation_error_projected_away"
            ramp_scale = tuning.minimum_ramp_scale
    elif phase == PredictivePhase.RECOVERY:
        ramp_scale = _clamp(
            1.0 - (1.0 - tuning.recovery_ramp_scale) * confidence,
            tuning.minimum_ramp_scale,
            1.0,
        )
    elif abs(predicted_error) > tuning.large_error_mpa and moving_away:
        hold_current = True
        hold_reason = "large_projected_error"
        ramp_scale = tuning.cautious_ramp_scale
    if not hold_current and abs(features.stress_error_mpa) > tuning.large_error_mpa and moving_away:
        hold_current = True
        hold_reason = "large_observed_error"
        ramp_scale = min(ramp_scale, tuning.cautious_ramp_scale)
    recovery_priority = _clamp(
        abs(predicted_error) / max(1e-12, tuning.large_error_mpa),
        0.0,
        1.0,
    )
    return PredictiveAdvice(
        phase=phase,
        confidence=confidence,
        ramp_scale=_clamp(ramp_scale, tuning.minimum_ramp_scale, 1.0),
        hold_current=hold_current,
        hold_reason=hold_reason,
        predicted_error_mpa=predicted_error,
        moving_away=moving_away,
        recovery_priority=recovery_priority,
    )


def select_predictive_motor_step(
    *,
    stress_error_mpa: float,
    sensitivity_mpa_per_mm: float,
    phase: PredictivePhase,
    previous_target_space_direction: float = 0.0,
    tuning: PredictiveControllerTuning | None = None,
) -> PredictiveMotorStep:
    tuning = tuning or PredictiveControllerTuning()
    error = float(stress_error_mpa)
    sensitivity = abs(float(sensitivity_mpa_per_mm))
    if not math.isfinite(error) or not math.isfinite(sensitivity) or sensitivity <= 0.0:
        return PredictiveMotorStep(0.0, 0.0, error, abs(error), "invalid_sensitivity")
    if abs(error) <= tuning.target_tolerance_mpa:
        return PredictiveMotorStep(0.0, 0.0, error, abs(error), "inside_tolerance")
    target_space_direction = -math.copysign(1.0, error)
    cap_by_stress = max(tuning.motor_step_mm, tuning.max_correction_stress_mpa / sensitivity)
    max_step = min(tuning.max_correction_mm, cap_by_stress)
    if phase == PredictivePhase.TRANSFORMATION:
        max_step = max(
            tuning.motor_step_mm,
            max_step * _clamp(tuning.transformation_correction_factor, 0.05, 1.0),
        )
    ideal_step = min(max_step, abs(error) / sensitivity * tuning.motor_gain)
    candidates = {0.0, max(tuning.motor_step_mm, ideal_step), max_step}
    step = tuning.motor_step_mm
    while step < max_step:
        candidates.add(step)
        step *= 2.0
    best: PredictiveMotorStep | None = None
    for candidate in sorted(candidates):
        correction = min(max_step, max(0.0, float(candidate)))
        predicted = error + target_space_direction * sensitivity * correction
        overshot = predicted * error < 0.0 and abs(predicted) > tuning.target_tolerance_mpa
        reversal = (
            previous_target_space_direction != 0.0
            and math.copysign(1.0, previous_target_space_direction) != target_space_direction
        )
        cost = abs(predicted)
        cost += tuning.motion_penalty_mpa_per_mm * correction
        if overshot:
            cost += abs(predicted) * tuning.overshoot_penalty
        if reversal:
            cost += tuning.reversal_penalty_mpa
        if phase == PredictivePhase.TRANSFORMATION:
            cost += correction * tuning.motion_penalty_mpa_per_mm
        reason = "candidate"
        if correction <= 0.0:
            reason = "wait"
        elif overshot:
            reason = "overshoot_penalized"
        elif reversal:
            reason = "reversal_penalized"
        step_advice = PredictiveMotorStep(
            correction_mm=correction,
            target_space_direction=target_space_direction if correction > 0.0 else 0.0,
            predicted_error_mpa=predicted,
            cost=cost,
            reason=reason,
        )
        if best is None or step_advice.cost < best.cost:
            best = step_advice
    assert best is not None
    if best.correction_mm <= 0.0 and abs(error) > tuning.target_tolerance_mpa:
        return PredictiveMotorStep(
            correction_mm=tuning.motor_step_mm,
            target_space_direction=target_space_direction,
            predicted_error_mpa=error + target_space_direction * sensitivity * tuning.motor_step_mm,
            cost=best.cost,
            reason="minimum_step_floor",
        )
    return best
