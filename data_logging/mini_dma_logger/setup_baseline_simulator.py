"""Deterministic simulator and trace replay for TMA setup zero-load detection.

This module is deliberately offline.  It defines a detector contract that can be
validated before any equivalent policy is wired into the live hardware controller.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


DEFAULT_SETUP_BASELINE_SCENARIOS = (
    "clean_piecewise",
    "delayed_feedback",
    "curved_taut_branch",
    "false_knee_then_plateau",
    "ambiguous_shallow_plateau",
    "no_plateau",
)


def _finite(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _median_absolute_deviation(values: Sequence[float], center: float) -> float:
    if not values:
        return 0.0
    return float(statistics.median(abs(float(value) - center) for value in values))


def _linear_fit(points: Sequence[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if len(points) < 2:
        return None
    n = float(len(points))
    sum_x = sum(point[0] for point in points)
    sum_y = sum(point[1] for point in points)
    sum_xx = sum(point[0] * point[0] for point in points)
    sum_xy = sum(point[0] * point[1] for point in points)
    denominator = n * sum_xx - sum_x * sum_x
    if abs(denominator) <= 1e-15:
        return None
    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n
    fitted = [slope * point[0] + intercept for point in points]
    residuals = [point[1] - prediction for point, prediction in zip(points, fitted, strict=True)]
    mean_y = sum_y / n
    total = sum((point[1] - mean_y) ** 2 for point in points)
    residual_sum = sum(residual * residual for residual in residuals)
    r_squared = 1.0 if total <= 1e-15 else 1.0 - residual_sum / total
    rmse = math.sqrt(residual_sum / n)
    return float(slope), float(intercept), float(r_squared), float(rmse)


def _robust_line_fit(points: Sequence[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if len(points) < 2:
        return None
    pair_slopes = [
        (right_y - left_y) / (right_x - left_x)
        for left_index, (left_x, left_y) in enumerate(points)
        for right_x, right_y in points[left_index + 1 :]
        if abs(right_x - left_x) > 1e-15
    ]
    if not pair_slopes:
        return None
    robust_slope = float(statistics.median(pair_slopes))
    robust_intercept = float(
        statistics.median(point_y - robust_slope * point_x for point_x, point_y in points)
    )
    residuals = [
        point_y - (robust_slope * point_x + robust_intercept)
        for point_x, point_y in points
    ]
    residual_center = float(statistics.median(residuals))
    residual_mad = _median_absolute_deviation(residuals, residual_center)
    residual_limit = max(0.003, residual_mad * 4.5)
    inliers = [
        point
        for point, residual in zip(points, residuals, strict=True)
        if abs(residual - residual_center) <= residual_limit
    ]
    return _linear_fit(inliers if len(inliers) >= max(3, len(points) // 2) else points)


@dataclass(frozen=True)
class SetupBaselineDetectorConfig:
    motor_step_mm: float = 0.00125
    relaxation_position_sign: int = 1
    relaxation_raw_sign: int = 1
    fresh_samples_per_position: int = 3
    minimum_taut_positions: int = 8
    candidate_slope_fraction: float = 0.50
    confirmation_probe_positions: int = 3
    plateau_slope_fraction: float = 0.20
    plateau_max_span_g: float = 0.045
    minimum_taut_r_squared: float = 0.95
    maximum_zero_uncertainty_mm: float = 0.0025
    intersection_bracket_steps: float = 1.5

    def validated(self) -> "SetupBaselineDetectorConfig":
        if self.motor_step_mm <= 0.0:
            raise ValueError("motor_step_mm must be positive")
        if self.relaxation_position_sign not in (-1, 1):
            raise ValueError("relaxation_position_sign must be -1 or 1")
        if self.relaxation_raw_sign not in (-1, 1):
            raise ValueError("relaxation_raw_sign must be -1 or 1")
        if self.fresh_samples_per_position < 1:
            raise ValueError("fresh_samples_per_position must be positive")
        if self.minimum_taut_positions < 4:
            raise ValueError("minimum_taut_positions must be at least 4")
        if self.confirmation_probe_positions < 1:
            raise ValueError("confirmation_probe_positions must be positive")
        for name in (
            "candidate_slope_fraction",
            "plateau_slope_fraction",
            "plateau_max_span_g",
            "minimum_taut_r_squared",
            "maximum_zero_uncertainty_mm",
            "intersection_bracket_steps",
        ):
            if _finite(getattr(self, name), name) < 0.0:
                raise ValueError(f"{name} must be non-negative")
        return self


@dataclass(frozen=True)
class SetupBaselineObservation:
    elapsed_s: float
    position_mm: float
    raw_load_g: float
    fresh_after_move: bool = True
    move_index: int = 0
    sample_index: int = 0
    source: str = "simulation"

    def to_row(self) -> dict[str, Any]:
        return {
            "elapsed_s": f"{self.elapsed_s:.6f}",
            "position_mm": f"{self.position_mm:.9f}",
            "raw_load_g": f"{self.raw_load_g:.9f}",
            "fresh_after_move": self.fresh_after_move,
            "move_index": self.move_index,
            "sample_index": self.sample_index,
            "source": self.source,
        }


@dataclass(frozen=True)
class SettledSetupPosition:
    move_index: int
    position_mm: float
    relaxation_mm: float
    raw_center_g: float
    raw_span_g: float
    sample_count: int

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SetupBaselineCandidate:
    settled_index: int
    move_index: int
    position_mm: float
    relaxation_mm: float
    incremental_slope_g_per_mm: float
    taut_slope_g_per_mm: float
    outcome: str
    reason: str

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SetupBaselineEstimate:
    status: str
    reason: str
    zero_position_mm: float | None = None
    zero_raw_load_g: float | None = None
    zero_uncertainty_mm: float | None = None
    taut_slope_g_per_mm: float | None = None
    taut_r_squared: float | None = None
    plateau_slope_g_per_mm: float | None = None
    plateau_span_g: float | None = None
    candidate_position_mm: float | None = None
    confirmation_position_mm: float | None = None
    additional_probe_positions_required: int = 0
    settled_positions: tuple[SettledSetupPosition, ...] = ()
    candidates: tuple[SetupBaselineCandidate, ...] = ()

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"

    def summary(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confirmed"] = self.confirmed
        payload["settled_position_count"] = len(self.settled_positions)
        payload["candidate_count"] = len(self.candidates)
        payload.pop("settled_positions", None)
        payload.pop("candidates", None)
        return payload


def _settled_positions(
    observations: Sequence[SetupBaselineObservation],
    config: SetupBaselineDetectorConfig,
) -> tuple[SettledSetupPosition, ...]:
    if not observations:
        return ()
    visits: list[list[SetupBaselineObservation]] = []
    current: list[SetupBaselineObservation] = []
    current_move: int | None = None
    previous_relaxation: float | None = None
    start_position = float(observations[0].position_mm)
    for observation in observations:
        relaxation = config.relaxation_position_sign * (
            float(observation.position_mm) - start_position
        )
        if previous_relaxation is not None and relaxation < previous_relaxation - config.motor_step_mm * 0.25:
            break
        previous_relaxation = relaxation
        move_key = int(observation.move_index)
        if current and move_key != current_move:
            visits.append(current)
            current = []
        current_move = move_key
        current.append(observation)
    if current:
        visits.append(current)

    settled: list[SettledSetupPosition] = []
    for visit in visits:
        fresh = [observation for observation in visit if observation.fresh_after_move]
        if not fresh:
            continue
        retained = fresh[-config.fresh_samples_per_position :]
        raw_values = [config.relaxation_raw_sign * observation.raw_load_g for observation in retained]
        position = float(retained[-1].position_mm)
        relaxation = config.relaxation_position_sign * (position - start_position)
        if settled and abs(relaxation - settled[-1].relaxation_mm) < config.motor_step_mm * 0.25:
            combined_values = [settled[-1].raw_center_g, *raw_values]
            settled[-1] = SettledSetupPosition(
                move_index=int(retained[-1].move_index),
                position_mm=position,
                relaxation_mm=relaxation,
                raw_center_g=float(statistics.median(combined_values)),
                raw_span_g=float(max(combined_values) - min(combined_values)),
                sample_count=settled[-1].sample_count + len(retained),
            )
            continue
        settled.append(
            SettledSetupPosition(
                move_index=int(retained[-1].move_index),
                position_mm=position,
                relaxation_mm=relaxation,
                raw_center_g=float(statistics.median(raw_values)),
                raw_span_g=float(max(raw_values) - min(raw_values)),
                sample_count=len(retained),
            )
        )
    return tuple(settled)


def detect_setup_baseline(
    observations: Sequence[SetupBaselineObservation],
    config: SetupBaselineDetectorConfig | None = None,
) -> SetupBaselineEstimate:
    detector = (config or SetupBaselineDetectorConfig()).validated()
    settled = _settled_positions(observations, detector)
    if len(settled) < detector.minimum_taut_positions + 1:
        return SetupBaselineEstimate(
            status="insufficient_taut_data",
            reason=(
                f"Need at least {detector.minimum_taut_positions + 1} distinct settled positions; "
                f"received {len(settled)}."
            ),
            settled_positions=settled,
        )

    seed_points = [
        (point.relaxation_mm, point.raw_center_g)
        for point in settled[: detector.minimum_taut_positions]
    ]
    seed_fit = _robust_line_fit(seed_points)
    if seed_fit is None or seed_fit[0] <= 0.0:
        return SetupBaselineEstimate(
            status="invalid_taut_fit",
            reason="The initial settled positions do not establish a positive unloading stiffness.",
            settled_positions=settled,
        )
    seed_slope = float(seed_fit[0])
    candidates: list[SetupBaselineCandidate] = []
    pending_candidate: SetupBaselineCandidate | None = None

    index = detector.minimum_taut_positions
    while index < len(settled):
        previous = settled[index - 1]
        current = settled[index]
        delta_position = current.relaxation_mm - previous.relaxation_mm
        if delta_position <= detector.motor_step_mm * 0.25:
            index += 1
            continue
        incremental_slope = (current.raw_center_g - previous.raw_center_g) / delta_position
        if incremental_slope >= seed_slope * detector.candidate_slope_fraction:
            index += 1
            continue

        provisional = SetupBaselineCandidate(
            settled_index=index,
            move_index=current.move_index,
            position_mm=current.position_mm,
            relaxation_mm=current.relaxation_mm,
            incremental_slope_g_per_mm=float(incremental_slope),
            taut_slope_g_per_mm=seed_slope,
            outcome="pending",
            reason="Incremental unloading slope collapsed below the candidate threshold.",
        )
        confirmation_index = index + detector.confirmation_probe_positions
        if confirmation_index >= len(settled):
            pending_candidate = provisional
            candidates.append(provisional)
            break

        taut_points = [
            (point.relaxation_mm, point.raw_center_g)
            for point in settled[:index]
        ]
        taut_fit = _robust_line_fit(taut_points)
        plateau_points = [
            (point.relaxation_mm, point.raw_center_g)
            for point in settled[index : confirmation_index + 1]
        ]
        plateau_fit = _linear_fit(plateau_points)
        if taut_fit is None or plateau_fit is None:
            candidates.append(
                SetupBaselineCandidate(**{**asdict(provisional), "outcome": "rejected", "reason": "Fit failed."})
            )
            index += 1
            continue
        taut_slope, taut_intercept, taut_r_squared, taut_rmse = taut_fit
        plateau_slope, _plateau_intercept, _plateau_r_squared, _plateau_rmse = plateau_fit
        plateau_values = [point[1] for point in plateau_points]
        plateau_span = max(plateau_values) - min(plateau_values)
        if taut_slope <= 0.0 or taut_r_squared < detector.minimum_taut_r_squared:
            candidates.append(
                SetupBaselineCandidate(
                    **{
                        **asdict(provisional),
                        "outcome": "rejected",
                        "reason": f"Taut fit confidence was insufficient (R^2={taut_r_squared:.4f}).",
                    }
                )
            )
            index += 1
            continue
        if (
            abs(plateau_slope) > abs(taut_slope) * detector.plateau_slope_fraction
            or plateau_span > detector.plateau_max_span_g
        ):
            candidates.append(
                SetupBaselineCandidate(
                    **{
                        **asdict(provisional),
                        "outcome": "rejected",
                        "reason": (
                            "Additional relaxation probes did not establish a sufficiently flat spatial plateau."
                        ),
                    }
                )
            )
            index += 1
            continue

        plateau_center = float(statistics.median(plateau_values))
        zero_relaxation = (plateau_center - taut_intercept) / taut_slope
        zero_position = float(observations[0].position_mm) + (
            zero_relaxation / detector.relaxation_position_sign
        )
        plateau_noise = _median_absolute_deviation(plateau_values, plateau_center) * 1.4826
        motor_quantization_uncertainty = detector.motor_step_mm / math.sqrt(12.0)
        zero_uncertainty = math.sqrt(
            ((taut_rmse + plateau_noise) / abs(taut_slope)) ** 2
            + motor_quantization_uncertainty**2
        )
        lower_bracket = previous.relaxation_mm - detector.motor_step_mm * detector.intersection_bracket_steps
        upper_bracket = (
            settled[confirmation_index].relaxation_mm
            + detector.motor_step_mm * detector.intersection_bracket_steps
        )
        if not (lower_bracket <= zero_relaxation <= upper_bracket):
            candidates.append(
                SetupBaselineCandidate(
                    **{
                        **asdict(provisional),
                        "outcome": "rejected",
                        "reason": "The fit intersection fell outside the taut-to-plateau bracket.",
                    }
                )
            )
            index += 1
            continue
        if zero_uncertainty > detector.maximum_zero_uncertainty_mm:
            candidates.append(
                SetupBaselineCandidate(
                    **{
                        **asdict(provisional),
                        "outcome": "rejected",
                        "reason": (
                            f"Zero-position uncertainty {zero_uncertainty * 1000.0:.3f} um exceeds "
                            f"the {detector.maximum_zero_uncertainty_mm * 1000.0:.3f} um limit."
                        ),
                    }
                )
            )
            index += 1
            continue

        accepted = SetupBaselineCandidate(
            **{
                **asdict(provisional),
                "outcome": "confirmed",
                "reason": "Bounded additional probes established a spatial plateau.",
            }
        )
        candidates.append(accepted)
        return SetupBaselineEstimate(
            status="confirmed",
            reason=(
                "Confirmed the zero-load boundary from the intersection of the robust taut fit "
                "and a bounded spatial plateau."
            ),
            zero_position_mm=zero_position,
            zero_raw_load_g=detector.relaxation_raw_sign * plateau_center,
            zero_uncertainty_mm=float(zero_uncertainty),
            taut_slope_g_per_mm=float(taut_slope),
            taut_r_squared=float(taut_r_squared),
            plateau_slope_g_per_mm=float(plateau_slope),
            plateau_span_g=float(plateau_span),
            candidate_position_mm=current.position_mm,
            confirmation_position_mm=settled[confirmation_index].position_mm,
            settled_positions=settled,
            candidates=tuple(candidates),
        )

    if pending_candidate is not None:
        available = len(settled) - pending_candidate.settled_index - 1
        required = max(0, detector.confirmation_probe_positions - available)
        pending_taut_fit = _robust_line_fit(
            [
                (point.relaxation_mm, point.raw_center_g)
                for point in settled[: pending_candidate.settled_index]
            ]
        )
        pending_taut_slope = seed_slope if pending_taut_fit is None else pending_taut_fit[0]
        pending_taut_r_squared = None if pending_taut_fit is None else pending_taut_fit[2]
        if any(candidate.outcome == "rejected" for candidate in candidates):
            return SetupBaselineEstimate(
                status="ambiguous",
                reason=(
                    "Candidate knees failed completed spatial checks and the final candidate did not "
                    "have enough remaining travel for confirmation."
                ),
                taut_slope_g_per_mm=pending_taut_slope,
                taut_r_squared=pending_taut_r_squared,
                candidate_position_mm=pending_candidate.position_mm,
                additional_probe_positions_required=required,
                settled_positions=settled,
                candidates=tuple(candidates),
            )
        return SetupBaselineEstimate(
            status="candidate_unconfirmed",
            reason=(
                "A slope-collapse candidate was found, but the trace ended before a spatial plateau "
                "could be confirmed."
            ),
            taut_slope_g_per_mm=pending_taut_slope,
            taut_r_squared=pending_taut_r_squared,
            candidate_position_mm=pending_candidate.position_mm,
            additional_probe_positions_required=required,
            settled_positions=settled,
            candidates=tuple(candidates),
        )
    if candidates:
        return SetupBaselineEstimate(
            status="ambiguous",
            reason="One or more candidate knees failed the spatial-plateau or confidence checks.",
            taut_slope_g_per_mm=seed_slope,
            settled_positions=settled,
            candidates=tuple(candidates),
        )
    return SetupBaselineEstimate(
        status="no_plateau",
        reason="The response remained load-sensitive throughout the available relaxation travel.",
        taut_slope_g_per_mm=seed_slope,
        settled_positions=settled,
    )


@dataclass(frozen=True)
class SetupBaselineScenario:
    name: str
    description: str
    zero_position_mm: float = 0.045
    end_position_mm: float = 0.065
    motor_step_mm: float = 0.00125
    plateau_raw_g: float = 20.56
    taut_slope_g_per_mm: float = 20.5
    taut_curvature_fraction: float = 0.0
    slack_slope_g_per_mm: float = 0.10
    noise_g: float = 0.004
    samples_per_position: int = 3
    sample_interval_s: float = 0.202
    stale_first_sample: bool = False
    outlier_every_positions: int = 0
    false_knee_position_mm: float | None = None
    false_knee_offset_g: float = 0.0
    seed: int = 0
    expected_status: str = "confirmed"

    def validated(self) -> "SetupBaselineScenario":
        if self.zero_position_mm <= 0.0:
            raise ValueError("zero_position_mm must be positive")
        if self.end_position_mm <= 0.0:
            raise ValueError("end_position_mm must be positive")
        if self.motor_step_mm <= 0.0:
            raise ValueError("motor_step_mm must be positive")
        if self.samples_per_position < 1:
            raise ValueError("samples_per_position must be positive")
        return self


@dataclass(frozen=True)
class SetupBaselineSimulationTrace:
    scenario: SetupBaselineScenario
    observations: tuple[SetupBaselineObservation, ...]
    estimate: SetupBaselineEstimate

    def summary(self) -> dict[str, Any]:
        payload = self.estimate.summary()
        payload.update(
            {
                "scenario": self.scenario.name,
                "description": self.scenario.description,
                "expected_status": self.scenario.expected_status,
                "true_zero_position_mm": self.scenario.zero_position_mm,
                "zero_error_um": (
                    None
                    if self.estimate.zero_position_mm is None
                    else (self.estimate.zero_position_mm - self.scenario.zero_position_mm) * 1000.0
                ),
                "observation_count": len(self.observations),
            }
        )
        return payload


def _scenario_raw_load_g(scenario: SetupBaselineScenario, position_mm: float) -> float:
    remaining = max(0.0, scenario.zero_position_mm - position_mm)
    if remaining > 0.0:
        normalized = remaining / max(scenario.zero_position_mm, scenario.motor_step_mm)
        value = scenario.plateau_raw_g - scenario.taut_slope_g_per_mm * remaining * (
            1.0 + scenario.taut_curvature_fraction * normalized
        )
    else:
        value = scenario.plateau_raw_g + scenario.slack_slope_g_per_mm * (
            position_mm - scenario.zero_position_mm
        )
    if (
        scenario.false_knee_position_mm is not None
        and abs(position_mm - scenario.false_knee_position_mm) <= scenario.motor_step_mm * 0.25
    ):
        value += scenario.false_knee_offset_g
    return float(value)


def run_setup_baseline_scenario(
    scenario: SetupBaselineScenario,
    detector_config: SetupBaselineDetectorConfig | None = None,
) -> SetupBaselineSimulationTrace:
    scenario.validated()
    detector = detector_config or SetupBaselineDetectorConfig(motor_step_mm=scenario.motor_step_mm)
    rng = random.Random(scenario.seed)
    observations: list[SetupBaselineObservation] = []
    elapsed_s = 0.0
    previous_true = _scenario_raw_load_g(scenario, 0.0)
    move_count = int(round(scenario.end_position_mm / scenario.motor_step_mm))
    for move_index in range(move_count + 1):
        position = move_index * scenario.motor_step_mm
        true_value = _scenario_raw_load_g(scenario, position)
        sample_index = 0
        if scenario.stale_first_sample and move_index > 0:
            observations.append(
                SetupBaselineObservation(
                    elapsed_s=elapsed_s,
                    position_mm=position,
                    raw_load_g=previous_true + rng.gauss(0.0, scenario.noise_g),
                    fresh_after_move=False,
                    move_index=move_index,
                    sample_index=sample_index,
                )
            )
            elapsed_s += scenario.sample_interval_s
            sample_index += 1
        for _ in range(scenario.samples_per_position):
            value = true_value + rng.gauss(0.0, scenario.noise_g)
            if (
                scenario.outlier_every_positions > 0
                and move_index > 0
                and move_index % scenario.outlier_every_positions == 0
                and sample_index == 1
            ):
                value += rng.choice((-1.0, 1.0)) * 0.025
            observations.append(
                SetupBaselineObservation(
                    elapsed_s=elapsed_s,
                    position_mm=position,
                    raw_load_g=value,
                    fresh_after_move=True,
                    move_index=move_index,
                    sample_index=sample_index,
                )
            )
            elapsed_s += scenario.sample_interval_s
            sample_index += 1
        previous_true = true_value
        estimate = detect_setup_baseline(observations, detector)
        if estimate.confirmed:
            return SetupBaselineSimulationTrace(scenario, tuple(observations), estimate)
    return SetupBaselineSimulationTrace(
        scenario,
        tuple(observations),
        detect_setup_baseline(observations, detector),
    )


def setup_baseline_scenarios() -> dict[str, SetupBaselineScenario]:
    return {
        "clean_piecewise": SetupBaselineScenario(
            name="clean_piecewise",
            description="Clean taut line followed by a nearly flat slack plateau.",
            seed=11,
        ),
        "delayed_feedback": SetupBaselineScenario(
            name="delayed_feedback",
            description="Each move first receives a stale pre-move scale response.",
            stale_first_sample=True,
            outlier_every_positions=11,
            seed=13,
        ),
        "curved_taut_branch": SetupBaselineScenario(
            name="curved_taut_branch",
            description="Mildly curved taut response challenges the linear fit confidence.",
            taut_curvature_fraction=0.045,
            slack_slope_g_per_mm=0.18,
            seed=17,
        ),
        "false_knee_then_plateau": SetupBaselineScenario(
            name="false_knee_then_plateau",
            description="A transient false slope collapse occurs before the true plateau.",
            false_knee_position_mm=0.0275,
            false_knee_offset_g=0.020,
            seed=19,
        ),
        "ambiguous_shallow_plateau": SetupBaselineScenario(
            name="ambiguous_shallow_plateau",
            description="The post-knee response remains too load-sensitive to call zero confidently.",
            slack_slope_g_per_mm=6.0,
            expected_status="ambiguous",
            seed=23,
        ),
        "no_plateau": SetupBaselineScenario(
            name="no_plateau",
            description="The response remains linear over all allowed travel.",
            zero_position_mm=0.090,
            end_position_mm=0.065,
            expected_status="no_plateau",
            seed=29,
        ),
    }


def scenario_by_name(name: str) -> SetupBaselineScenario:
    try:
        return setup_baseline_scenarios()[name]
    except KeyError as exc:
        raise ValueError(f"Unknown setup baseline scenario: {name}") from exc


def replay_setup_csv(
    path: Path | str,
    detector_config: SetupBaselineDetectorConfig | None = None,
) -> SetupBaselineEstimate:
    source = Path(path)
    observations: list[SetupBaselineObservation] = []
    move_index = -1
    previous_position: float | None = None
    with source.open(newline="", encoding="utf-8-sig") as handle:
        for sample_index, row in enumerate(csv.DictReader(handle)):
            position = float(row["raw_position_mm"])
            if previous_position is None or abs(position - previous_position) > 1e-9:
                move_index += 1
                previous_position = position
            observations.append(
                SetupBaselineObservation(
                    elapsed_s=float(row["elapsed_s"]),
                    position_mm=position,
                    raw_load_g=float(row["raw_load_g"]),
                    fresh_after_move=True,
                    move_index=move_index,
                    sample_index=sample_index,
                    source=str(source),
                )
            )
    return detect_setup_baseline(observations, detector_config)


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    materialized = list(rows)
    if not materialized:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in materialized:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(materialized)


def write_setup_baseline_outputs(
    trace: SetupBaselineSimulationTrace,
    output_dir: Path | str,
) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    observations_path = out / "setup_observations.csv"
    settled_path = out / "settled_positions.csv"
    candidates_path = out / "baseline_candidates.csv"
    summary_path = out / "summary.json"
    scenario_path = out / "scenario.json"
    _write_csv(observations_path, (observation.to_row() for observation in trace.observations))
    _write_csv(settled_path, (point.to_row() for point in trace.estimate.settled_positions))
    _write_csv(candidates_path, (candidate.to_row() for candidate in trace.estimate.candidates))
    summary_path.write_text(json.dumps(trace.summary(), indent=2), encoding="utf-8")
    scenario_path.write_text(json.dumps(asdict(trace.scenario), indent=2), encoding="utf-8")
    return {
        "observations": observations_path,
        "settled_positions": settled_path,
        "candidates": candidates_path,
        "summary": summary_path,
        "scenario": scenario_path,
    }


def write_setup_baseline_report(
    summaries: Sequence[dict[str, Any]],
    output_dir: Path | str,
) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "setup_baseline_matrix.json"
    markdown_path = out / "setup_baseline_matrix.md"
    json_path.write_text(json.dumps({"results": list(summaries)}, indent=2), encoding="utf-8")
    lines = [
        "# TMA setup baseline simulation matrix",
        "",
        "| Case | Status | Zero error (um) | Uncertainty (um) | Taut R2 | Extra probes needed |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for summary in summaries:
        error = summary.get("zero_error_um")
        uncertainty = summary.get("zero_uncertainty_mm")
        r_squared = summary.get("taut_r_squared")
        lines.append(
            "| {case} | {status} | {error} | {uncertainty} | {r_squared} | {probes} |".format(
                case=summary.get("scenario") or summary.get("source") or "replay",
                status=summary.get("status", "-"),
                error="-" if error is None else f"{float(error):.3f}",
                uncertainty="-" if uncertainty is None else f"{float(uncertainty) * 1000.0:.3f}",
                r_squared="-" if r_squared is None else f"{float(r_squared):.4f}",
                probes=int(summary.get("additional_probe_positions_required") or 0),
            )
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"summary": json_path, "report": markdown_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic TMA setup-baseline simulations and setup.csv replays."
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=DEFAULT_SETUP_BASELINE_SCENARIOS,
        help="Synthetic scenario to run. May be repeated; defaults to all scenarios.",
    )
    parser.add_argument(
        "--replay",
        action="append",
        type=Path,
        help="Existing setup.csv to replay read-only. May be repeated.",
    )
    parser.add_argument("--out", type=Path, default=None, help="Output directory for CSV/JSON reports.")
    args = parser.parse_args(argv)

    names = args.scenario or ([] if args.replay else list(DEFAULT_SETUP_BASELINE_SCENARIOS))
    summaries: list[dict[str, Any]] = []
    for name in names:
        trace = run_setup_baseline_scenario(scenario_by_name(name))
        summary = trace.summary()
        summaries.append(summary)
        if args.out is not None:
            write_setup_baseline_outputs(trace, args.out / name)
    for replay_path in args.replay or []:
        estimate = replay_setup_csv(replay_path)
        summary = estimate.summary()
        summary["source"] = str(replay_path)
        summaries.append(summary)
    if args.out is not None:
        write_setup_baseline_report(summaries, args.out)
    print(json.dumps({"results": summaries}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
