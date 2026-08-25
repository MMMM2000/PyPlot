"""Pure force-control policy for TMA load regulation.

The policy consumes snapshots and returns declarative decisions.  It owns no
timers, motors, scales, or GUI objects.  Load values are always expressed in
grams; conversion from stress or other experiment-level units belongs to the
caller.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from enum import Enum


class ForceControlProfile(str, Enum):
    PRAGUE_LEGACY = "prague_legacy"
    KOSICE_ADAPTIVE = "kosice_adaptive"


class ForceControlIntent(str, Enum):
    SETUP = "setup"
    TRACK_TRAJECTORY = "track_trajectory"
    ACQUIRE_TARGET = "acquire_target"
    HOLD_TARGET = "hold_target"
    RECOVER_DISTURBANCE = "recover_disturbance"
    PROBE_REQUIRED = "probe_required"
    FAULT = "fault"


class ForceControlState(str, Enum):
    SETUP = "setup"
    TRACK_TRAJECTORY = "track_trajectory"
    ACQUIRE_TARGET = "acquire_target"
    HOLD_TARGET = "hold_target"
    RECOVER_DISTURBANCE = "recover_disturbance"
    PROBE_REQUIRED = "probe_required"
    FAULT = "fault"


class ForceControlAction(str, Enum):
    NONE = "none"
    WAIT_FOR_SAMPLE = "wait_for_sample"
    WAIT_FOR_MOTOR = "wait_for_motor"
    MOVE_RELATIVE = "move_relative"
    PROBE_RELATIVE = "probe_relative"
    FAULT = "fault"


_LANDING_INTENTS = {
    ForceControlIntent.SETUP,
    ForceControlIntent.ACQUIRE_TARGET,
    ForceControlIntent.HOLD_TARGET,
    ForceControlIntent.RECOVER_DISTURBANCE,
}


@dataclass(frozen=True, slots=True)
class ForceControlInput:
    intent: ForceControlIntent
    target_load_g: float
    current_load_g: float
    filtered_load_g: float
    tolerance_g: float
    robust_noise_g: float
    quantization_g: float
    readability_g: float
    position_mm: float
    motor_resolution_mm: float
    max_safe_correction_mm: float
    speed_mm_s: float
    target_ramp_g_s: float
    ramp_active: bool
    current_mA: float
    current_changing: bool
    feedback_fresh: bool
    motor_complete: bool
    timestamp_s: float
    context_key: str = "default"
    response_observation_complete: bool = True
    filtered_slope_g_s: float = 0.0


@dataclass(frozen=True, slots=True)
class GainEstimate:
    load_per_mm_g: float | None
    uncertainty_g_per_mm: float | None
    confidence: float
    observable_windows: int
    excluded_windows: int
    trusted: bool


@dataclass(frozen=True, slots=True)
class RobustLinearGainEstimate:
    load_per_mm_g: float | None
    uncertainty_g_per_mm: float | None
    confidence: float
    point_count: int
    position_span_mm: float
    r_squared: float | None
    trusted: bool


class RobustLinearGainEstimator:
    """Estimate local load stiffness from several independent position samples.

    The median pairwise slope is deliberately used instead of a single
    move/response quotient. Repeated readings at one motor position do not earn
    confidence, and one delayed or spurious scale value cannot replace the
    trusted stiffness by itself.
    """

    def __init__(
        self,
        *,
        minimum_points: int = 5,
        maximum_points: int = 15,
        minimum_r_squared: float = 0.90,
        maximum_relative_uncertainty: float = 0.50,
    ) -> None:
        if minimum_points < 3:
            raise ValueError("minimum_points must be at least 3")
        if maximum_points < minimum_points:
            raise ValueError("maximum_points must be at least minimum_points")
        self.minimum_points = int(minimum_points)
        self.maximum_points = int(maximum_points)
        self.minimum_r_squared = float(minimum_r_squared)
        self.maximum_relative_uncertainty = float(maximum_relative_uncertainty)
        self._points: list[tuple[float, float]] = []
        self._motor_resolution_mm = 0.0
        self._observation_floor_g = 0.0

    def reset(self) -> None:
        self._points.clear()
        self._motor_resolution_mm = 0.0
        self._observation_floor_g = 0.0

    def observe(
        self,
        *,
        position_mm: float,
        load_g: float,
        motor_resolution_mm: float,
        observation_floor_g: float,
    ) -> RobustLinearGainEstimate:
        values = (
            float(position_mm),
            float(load_g),
            float(motor_resolution_mm),
            float(observation_floor_g),
        )
        if not _finite(values) or motor_resolution_mm <= 0.0 or observation_floor_g < 0.0:
            raise ValueError("invalid robust gain observation")
        self._motor_resolution_mm = max(self._motor_resolution_mm, abs(float(motor_resolution_mm)))
        self._observation_floor_g = max(self._observation_floor_g, abs(float(observation_floor_g)))
        independent_floor = self._motor_resolution_mm * 0.5
        if not self._points or abs(float(position_mm) - self._points[-1][0]) >= independent_floor:
            self._points.append((float(position_mm), float(load_g)))
            self._points = self._points[-self.maximum_points :]
        return self.estimate

    @property
    def estimate(self) -> RobustLinearGainEstimate:
        points = self._points
        point_count = len(points)
        if point_count < 2:
            return RobustLinearGainEstimate(None, None, 0.0, point_count, 0.0, None, False)
        positions = [point[0] for point in points]
        loads = [point[1] for point in points]
        position_span = max(positions) - min(positions)
        pairwise_slopes = [
            (loads[right] - loads[left]) / (positions[right] - positions[left])
            for left in range(point_count - 1)
            for right in range(left + 1, point_count)
            if abs(positions[right] - positions[left]) >= self._motor_resolution_mm
            and (loads[right] - loads[left]) * (positions[right] - positions[left]) > 0.0
        ]
        if not pairwise_slopes:
            return RobustLinearGainEstimate(None, None, 0.0, point_count, position_span, None, False)
        slope = statistics.median(pairwise_slopes)
        slope_mad = _median_absolute_deviation(pairwise_slopes, slope)
        uncertainty = 1.4826 * slope_mad
        intercept = statistics.median(
            load - slope * position for position, load in points
        )
        residuals = [
            load - (slope * position + intercept)
            for position, load in points
        ]
        residual_center = statistics.median(residuals)
        residual_mad = _median_absolute_deviation(residuals, residual_center)
        if residual_mad <= 1e-18:
            inlier_points = [
                point
                for point, residual in zip(points, residuals, strict=True)
                if abs(residual - residual_center) <= max(self._observation_floor_g, 1e-12)
            ]
        else:
            residual_limit = max(
                self._observation_floor_g,
                4.5 * 1.4826 * residual_mad,
            )
            inlier_points = [
                point
                for point, residual in zip(points, residuals, strict=True)
                if abs(residual - residual_center) <= residual_limit
            ]
        if len(inlier_points) < 2:
            inlier_points = points
        residual_sum = sum(
            (load - (slope * position + intercept)) ** 2
            for position, load in inlier_points
        )
        inlier_loads = [load for _position, load in inlier_points]
        mean_load = statistics.mean(inlier_loads)
        total_sum = sum((load - mean_load) ** 2 for load in inlier_loads)
        r_squared = 1.0 if total_sum <= 1e-18 else 1.0 - residual_sum / total_sum
        relative_uncertainty = uncertainty / slope if slope > 0.0 else math.inf
        minimum_span = max(
            self._motor_resolution_mm * max(3, self.minimum_points - 1),
            self._observation_floor_g / max(slope, 1e-12) * 2.0,
        )
        point_confidence = min(1.0, point_count / float(self.minimum_points + 2))
        span_confidence = min(1.0, position_span / max(minimum_span, 1e-12))
        precision_confidence = max(0.0, 1.0 - relative_uncertainty)
        fit_confidence = max(0.0, min(1.0, r_squared))
        confidence = point_confidence * span_confidence * precision_confidence * fit_confidence
        trusted = (
            point_count >= self.minimum_points
            and position_span >= minimum_span
            and slope > 0.0
            and r_squared >= self.minimum_r_squared
            and relative_uncertainty <= self.maximum_relative_uncertainty
        )
        return RobustLinearGainEstimate(
            load_per_mm_g=float(slope),
            uncertainty_g_per_mm=float(uncertainty),
            confidence=float(confidence),
            point_count=point_count,
            position_span_mm=float(position_span),
            r_squared=float(r_squared),
            trusted=trusted,
        )


@dataclass(frozen=True, slots=True)
class ForceControlDecision:
    intent: ForceControlIntent
    state: ForceControlState
    action: ForceControlAction
    correction_mm: float = 0.0
    reason: str = ""
    effective_deadband_g: float = 0.0
    minimum_informative_motion_mm: float | None = None
    gain: GainEstimate = field(
        default_factory=lambda: GainEstimate(None, None, 0.0, 0, 0, False)
    )
    pending_response: bool = False


@dataclass(frozen=True, slots=True)
class ForceControlConfig:
    profile: ForceControlProfile
    initial_load_per_mm_g: float | None = None
    initial_gain_relative_uncertainty: float = 0.25
    noise_sigma: float = 3.0
    response_sigma: float = 2.0
    correction_fraction: float = 0.25
    max_target_fraction_per_command: float = 0.10
    trajectory_feedforward_horizon_s: float = 0.20
    disturbance_prediction_horizon_s: float = 0.35
    reversal_confirmation_s: float = 0.30
    stationary_position_steps: float = 0.5
    minimum_gain_windows: int = 2
    minimum_gain_confidence: float = 0.45
    maximum_gain_relative_uncertainty: float = 0.75


@dataclass(frozen=True, slots=True)
class _Observation:
    timestamp_s: float
    position_mm: float
    load_g: float
    current_mA: float
    current_changing: bool
    motor_resolution_mm: float
    observation_floor_g: float


@dataclass(frozen=True, slots=True)
class _PendingCommand:
    intent: ForceControlIntent
    start: _Observation
    correction_mm: float


def _median_absolute_deviation(values: list[float], center: float) -> float:
    return statistics.median(abs(value - center) for value in values)


def _finite(values: tuple[float, ...]) -> bool:
    return all(math.isfinite(value) for value in values)


class ForceControlPolicy:
    """Deterministic dual-profile force-control state machine."""

    def __init__(self, config: ForceControlConfig) -> None:
        if config.initial_load_per_mm_g is not None and config.initial_load_per_mm_g <= 0.0:
            raise ValueError("initial_load_per_mm_g must be positive")
        if not 0.0 < config.correction_fraction <= 1.0:
            raise ValueError("correction_fraction must be in (0, 1]")
        if not 0.0 < config.max_target_fraction_per_command <= 1.0:
            raise ValueError("max_target_fraction_per_command must be in (0, 1]")
        self.config = config
        self.reset()

    def reset(self, context_key: str | None = None) -> None:
        """Discard pending response and learned context."""
        self._context_key = context_key
        self._last_timestamp_s: float | None = None
        self._last_observation: _Observation | None = None
        self._learning_anchor: _Observation | None = None
        self._pending: _PendingCommand | None = None
        self._gain_candidates: list[float] = []
        self._excluded_windows = 0
        self._drift_candidates_g_s: list[float] = []
        self._last_command_direction = 0.0
        self._reversal_candidate_direction = 0.0
        self._reversal_candidate_since_s: float | None = None

    def cancel_pending(self) -> None:
        """Forget an offered command that the transport did not accept."""
        self._pending = None

    @property
    def gain_estimate(self) -> GainEstimate:
        candidates = self._gain_candidates
        if candidates:
            gain = statistics.median(candidates)
            uncertainty = 1.4826 * _median_absolute_deviation(candidates, gain)
            relative_uncertainty = uncertainty / gain if gain > 0.0 else math.inf
            sample_confidence = min(1.0, len(candidates) / max(1, self.config.minimum_gain_windows + 2))
            precision_confidence = max(0.0, 1.0 - relative_uncertainty)
            confidence = sample_confidence * precision_confidence
            trusted = (
                len(candidates) >= self.config.minimum_gain_windows
                and confidence >= self.config.minimum_gain_confidence
                and relative_uncertainty <= self.config.maximum_gain_relative_uncertainty
            )
            if not trusted and self.config.initial_load_per_mm_g is not None:
                initial = self.config.initial_load_per_mm_g
                initial_uncertainty = initial * self.config.initial_gain_relative_uncertainty
                return GainEstimate(
                    initial,
                    initial_uncertainty,
                    max(confidence, 1.0 - self.config.initial_gain_relative_uncertainty),
                    len(candidates),
                    self._excluded_windows,
                    True,
                )
            return GainEstimate(
                gain,
                uncertainty,
                confidence,
                len(candidates),
                self._excluded_windows,
                trusted,
            )
        initial = self.config.initial_load_per_mm_g
        if initial is None:
            return GainEstimate(None, None, 0.0, 0, self._excluded_windows, False)
        uncertainty = initial * self.config.initial_gain_relative_uncertainty
        confidence = max(0.0, 1.0 - self.config.initial_gain_relative_uncertainty)
        return GainEstimate(initial, uncertainty, confidence, 0, self._excluded_windows, True)

    def decide(self, inputs: ForceControlInput) -> ForceControlDecision:
        fault = self._validate(inputs)
        if fault is not None:
            self._pending = None
            return self._decision(
                inputs,
                state=ForceControlState.FAULT,
                action=ForceControlAction.FAULT,
                reason=fault,
            )

        if self._context_key != inputs.context_key:
            self.reset(inputs.context_key)

        if self._last_timestamp_s is not None and inputs.timestamp_s < self._last_timestamp_s:
            self._pending = None
            return self._decision(
                inputs,
                state=ForceControlState.FAULT,
                action=ForceControlAction.FAULT,
                reason="timestamp_not_monotonic",
            )
        if self._last_timestamp_s is not None and inputs.timestamp_s == self._last_timestamp_s:
            return self._decision(
                inputs,
                self._state_for_intent(inputs.intent),
                ForceControlAction.WAIT_FOR_SAMPLE,
                "sample_not_advanced",
            )
        self._last_timestamp_s = inputs.timestamp_s

        if inputs.intent is ForceControlIntent.FAULT:
            self._pending = None
            return self._decision(inputs, ForceControlState.FAULT, ForceControlAction.FAULT, "fault_intent")
        if not inputs.feedback_fresh:
            return self._decision(
                inputs,
                self._state_for_intent(inputs.intent),
                ForceControlAction.WAIT_FOR_SAMPLE,
                "stale_feedback",
            )

        observation = self._observation(inputs)
        self._observe_stationary_drift(observation)
        self._learn_from_cumulative_window(observation)
        self._last_observation = observation

        if inputs.intent is ForceControlIntent.TRACK_TRAJECTORY:
            self._pending = None
            return self._track_trajectory(inputs, observation)
        if inputs.intent is ForceControlIntent.PROBE_REQUIRED:
            return self._request_probe(inputs, observation, reason="probe_requested")
        if inputs.intent not in _LANDING_INTENTS:
            return self._decision(inputs, ForceControlState.FAULT, ForceControlAction.FAULT, "unsupported_intent")
        return self._land_or_hold(inputs, observation)

    def _validate(self, inputs: ForceControlInput) -> str | None:
        numeric = (
            inputs.target_load_g,
            inputs.current_load_g,
            inputs.filtered_load_g,
            inputs.tolerance_g,
            inputs.robust_noise_g,
            inputs.quantization_g,
            inputs.readability_g,
            inputs.position_mm,
            inputs.motor_resolution_mm,
            inputs.max_safe_correction_mm,
            inputs.speed_mm_s,
            inputs.target_ramp_g_s,
            inputs.current_mA,
            inputs.timestamp_s,
            inputs.filtered_slope_g_s,
        )
        if not _finite(numeric):
            return "non_finite_input"
        if inputs.tolerance_g < 0.0 or inputs.robust_noise_g < 0.0:
            return "negative_load_resolution"
        if inputs.quantization_g < 0.0 or inputs.readability_g < 0.0:
            return "negative_load_resolution"
        if inputs.motor_resolution_mm <= 0.0 or inputs.max_safe_correction_mm <= 0.0:
            return "invalid_motion_limit"
        if inputs.speed_mm_s < 0.0:
            return "negative_speed"
        return None

    def _observation_floor_g(self, inputs: ForceControlInput) -> float:
        return max(
            inputs.quantization_g,
            inputs.readability_g,
            self.config.response_sigma * inputs.robust_noise_g,
        )

    def _deadband_g(self, inputs: ForceControlInput) -> float:
        deadband = max(
            inputs.tolerance_g,
            inputs.quantization_g,
            inputs.readability_g,
            self.config.noise_sigma * inputs.robust_noise_g,
        )
        if self.config.profile is not ForceControlProfile.KOSICE_ADAPTIVE:
            return deadband
        gain = self.gain_estimate
        minimum_motion = self._minimum_informative_motion(
            self._observation_floor_g(inputs),
            inputs.motor_resolution_mm,
            gain,
        )
        if minimum_motion is None or gain.load_per_mm_g is None:
            return deadband
        # Do not regulate more tightly than one load change that this scale and
        # actuator can resolve together.  This scales with the live wire gain,
        # scale readability, and motor resolution instead of a fixed load.
        return max(deadband, minimum_motion * gain.load_per_mm_g)

    def _observation(self, inputs: ForceControlInput) -> _Observation:
        return _Observation(
            timestamp_s=inputs.timestamp_s,
            position_mm=inputs.position_mm,
            load_g=inputs.filtered_load_g,
            current_mA=inputs.current_mA,
            current_changing=inputs.current_changing,
            motor_resolution_mm=inputs.motor_resolution_mm,
            observation_floor_g=self._observation_floor_g(inputs),
        )

    def _drift_g_s(self) -> float:
        return statistics.median(self._drift_candidates_g_s) if self._drift_candidates_g_s else 0.0

    def _observe_stationary_drift(self, current: _Observation) -> None:
        previous = self._last_observation
        if previous is None:
            return
        dt = current.timestamp_s - previous.timestamp_s
        stationary_limit = max(
            current.motor_resolution_mm,
            previous.motor_resolution_mm,
        ) * self.config.stationary_position_steps
        if (
            dt > 0.0
            and abs(current.position_mm - previous.position_mm) <= stationary_limit
            and not current.current_changing
            and not previous.current_changing
        ):
            self._drift_candidates_g_s.append((current.load_g - previous.load_g) / dt)
            self._drift_candidates_g_s = self._drift_candidates_g_s[-21:]

    def _learn_from_cumulative_window(self, current: _Observation) -> None:
        if self.config.profile is not ForceControlProfile.KOSICE_ADAPTIVE:
            return
        anchor = self._learning_anchor
        if anchor is None:
            self._learning_anchor = current
            return
        position_delta = current.position_mm - anchor.position_mm
        step_floor = max(current.motor_resolution_mm, anchor.motor_resolution_mm)
        gain = self.gain_estimate
        informative_motion = self._minimum_informative_motion(
            max(current.observation_floor_g, anchor.observation_floor_g),
            step_floor,
            gain,
        )
        if informative_motion is None or abs(position_delta) < informative_motion:
            return
        if current.current_changing or anchor.current_changing:
            self._excluded_windows += 1
            self._learning_anchor = current
            return
        dt = current.timestamp_s - anchor.timestamp_s
        adjusted_load_delta = current.load_g - anchor.load_g - self._drift_g_s() * dt
        load_floor = max(current.observation_floor_g, anchor.observation_floor_g)
        if abs(adjusted_load_delta) < load_floor or adjusted_load_delta * position_delta <= 0.0:
            self._excluded_windows += 1
            self._learning_anchor = current
            return
        candidate = adjusted_load_delta / position_delta
        if candidate <= 0.0 or not math.isfinite(candidate):
            self._excluded_windows += 1
        else:
            self._gain_candidates.append(candidate)
            self._gain_candidates = self._robust_gain_candidates(self._gain_candidates[-31:])
        self._learning_anchor = current

    @staticmethod
    def _robust_gain_candidates(values: list[float]) -> list[float]:
        if len(values) < 4:
            return values
        center = statistics.median(values)
        mad = _median_absolute_deviation(values, center)
        if mad <= 0.0:
            return [value for value in values if value == center]
        limit = 4.5 * 1.4826 * mad
        return [value for value in values if abs(value - center) <= limit]

    def _minimum_informative_motion(
        self,
        observation_floor_g: float,
        motor_resolution_mm: float,
        gain: GainEstimate,
    ) -> float | None:
        if gain.load_per_mm_g is None or not gain.trusted:
            return None
        conservative_gain = gain.load_per_mm_g
        if gain.uncertainty_g_per_mm is not None:
            conservative_gain = max(
                gain.load_per_mm_g - gain.uncertainty_g_per_mm,
                gain.load_per_mm_g * 0.10,
            )
        required = observation_floor_g / conservative_gain
        steps = max(1, math.ceil(required / motor_resolution_mm))
        return steps * motor_resolution_mm

    def _track_trajectory(
        self,
        inputs: ForceControlInput,
        observation: _Observation,
    ) -> ForceControlDecision:
        if not inputs.motor_complete:
            return self._decision(
                inputs,
                ForceControlState.TRACK_TRAJECTORY,
                ForceControlAction.WAIT_FOR_MOTOR,
                "trajectory_motor_active",
            )
        if not inputs.response_observation_complete:
            return self._decision(
                inputs,
                ForceControlState.TRACK_TRAJECTORY,
                ForceControlAction.WAIT_FOR_SAMPLE,
                "trajectory_response_window_pending",
            )
        error_g = inputs.target_load_g - inputs.filtered_load_g
        deadband_g = self._deadband_g(inputs)
        ramp_feedforward_g = (
            inputs.target_ramp_g_s * self.config.trajectory_feedforward_horizon_s
            if inputs.ramp_active
            else 0.0
        )
        trajectory_error_g = error_g + ramp_feedforward_g
        correction = self._correction_for_error(inputs, trajectory_error_g)
        if correction is None:
            return self._request_probe(
                inputs,
                observation,
                "trajectory_gain_untrusted",
                direction=1.0 if trajectory_error_g >= 0.0 else -1.0,
            )
        if abs(error_g) <= deadband_g and abs(ramp_feedforward_g) <= deadband_g:
            return self._decision(
                inputs,
                ForceControlState.TRACK_TRAJECTORY,
                ForceControlAction.NONE,
                "trajectory_in_band",
            )
        return self._decision(
            inputs,
            ForceControlState.TRACK_TRAJECTORY,
            ForceControlAction.MOVE_RELATIVE,
            "trajectory_correction",
            correction_mm=correction,
        )

    def _land_or_hold(
        self,
        inputs: ForceControlInput,
        observation: _Observation,
    ) -> ForceControlDecision:
        state = self._state_for_intent(inputs.intent)
        pending = self._pending
        if pending is None and not inputs.motor_complete:
            return self._decision(
                inputs,
                state,
                ForceControlAction.WAIT_FOR_MOTOR,
                "motor_active_before_landing",
            )
        if pending is None and not inputs.response_observation_complete:
            return self._decision(
                inputs,
                state,
                ForceControlAction.WAIT_FOR_SAMPLE,
                "response_window_pending_before_landing",
            )
        if pending is not None:
            if pending.intent is not inputs.intent:
                self._pending = None
                if not inputs.motor_complete:
                    return self._decision(
                        inputs,
                        state,
                        ForceControlAction.WAIT_FOR_MOTOR,
                        "intent_changed_while_motor_active",
                    )
                if not inputs.response_observation_complete:
                    return self._decision(
                        inputs,
                        state,
                        ForceControlAction.WAIT_FOR_SAMPLE,
                        "intent_changed_during_response_window",
                    )
            elif not inputs.motor_complete:
                return self._decision(inputs, state, ForceControlAction.WAIT_FOR_MOTOR, "pending_motor")
            elif not inputs.response_observation_complete:
                return self._decision(
                    inputs,
                    state,
                    ForceControlAction.WAIT_FOR_SAMPLE,
                    "response_window_pending",
                )
            else:
                response_g = observation.load_g - pending.start.load_g
                moved_mm = observation.position_mm - pending.start.position_mm
                observable = (
                    abs(response_g) >= max(observation.observation_floor_g, pending.start.observation_floor_g)
                    and abs(moved_mm) >= inputs.motor_resolution_mm
                )
                self._pending = None
                if not observable:
                    error_g = inputs.target_load_g - inputs.filtered_load_g
                    if abs(error_g) <= self._deadband_g(inputs):
                        return self._decision(
                            inputs,
                            state,
                            ForceControlAction.NONE,
                            "response_inside_achievable_deadband",
                        )
                    correction = self._correction_for_error(
                        inputs,
                        self._disturbance_aware_error(inputs, error_g),
                    )
                    if correction is not None:
                        return self._issue_landing_correction(
                            inputs,
                            observation,
                            state,
                            correction,
                            "sub_resolution_response_bounded_correction",
                        )
                    return self._request_probe(
                        inputs,
                        observation,
                        "sub_resolution_response_probe",
                        direction=1.0 if pending.correction_mm >= 0.0 else -1.0,
                    )

        error_g = inputs.target_load_g - inputs.filtered_load_g
        if abs(error_g) <= self._deadband_g(inputs):
            return self._decision(inputs, state, ForceControlAction.NONE, "inside_deadband")
        correction = self._correction_for_error(
            inputs,
            self._disturbance_aware_error(inputs, error_g),
        )
        if correction is None:
            return self._request_probe(
                inputs,
                observation,
                "gain_untrusted",
                direction=1.0 if error_g >= 0.0 else -1.0,
            )
        return self._issue_landing_correction(
            inputs,
            observation,
            state,
            correction,
            "landing_correction",
        )

    def _disturbance_aware_error(self, inputs: ForceControlInput, error_g: float) -> float:
        """Project same-direction material drift without chasing a predicted crossing."""
        if inputs.intent is not ForceControlIntent.RECOVER_DISTURBANCE:
            return error_g
        projected = error_g - (
            inputs.filtered_slope_g_s * self.config.disturbance_prediction_horizon_s
        )
        if error_g == 0.0 or error_g * projected <= 0.0:
            return error_g
        return projected if abs(projected) > abs(error_g) else error_g

    def _issue_landing_correction(
        self,
        inputs: ForceControlInput,
        observation: _Observation,
        state: ForceControlState,
        correction: float,
        reason: str,
    ) -> ForceControlDecision:
        direction = math.copysign(1.0, correction)
        if self._last_command_direction not in {0.0, direction}:
            if self._reversal_candidate_direction != direction:
                self._reversal_candidate_direction = direction
                self._reversal_candidate_since_s = inputs.timestamp_s
                return self._decision(
                    inputs,
                    state,
                    ForceControlAction.WAIT_FOR_SAMPLE,
                    "direction_reversal_confirmation",
                )
            since_s = self._reversal_candidate_since_s
            if (
                since_s is None
                or inputs.timestamp_s - since_s < self.config.reversal_confirmation_s
            ):
                return self._decision(
                    inputs,
                    state,
                    ForceControlAction.WAIT_FOR_SAMPLE,
                    "direction_reversal_confirmation",
                )
        self._reversal_candidate_direction = 0.0
        self._reversal_candidate_since_s = None
        self._last_command_direction = direction
        self._pending = _PendingCommand(inputs.intent, observation, correction)
        return self._decision(
            inputs,
            state,
            ForceControlAction.MOVE_RELATIVE,
            reason,
            correction_mm=correction,
        )

    def _correction_for_error(self, inputs: ForceControlInput, error_g: float) -> float | None:
        gain = self.gain_estimate
        if gain.load_per_mm_g is None or not gain.trusted:
            return None
        raw = error_g / gain.load_per_mm_g * self.config.correction_fraction
        correction = self._bounded_motion(raw, inputs)
        if self.config.profile is not ForceControlProfile.KOSICE_ADAPTIVE:
            return correction
        minimum = self._minimum_informative_motion(
            self._observation_floor_g(inputs),
            inputs.motor_resolution_mm,
            gain,
        )
        if minimum is None or abs(correction) >= minimum:
            return correction
        informative = self._bounded_motion(math.copysign(minimum, error_g), inputs)
        return informative if abs(informative) >= minimum else correction

    def _request_probe(
        self,
        inputs: ForceControlInput,
        observation: _Observation,
        reason: str,
        direction: float = 1.0,
    ) -> ForceControlDecision:
        gain = self.gain_estimate
        minimum = self._minimum_informative_motion(
            observation.observation_floor_g,
            inputs.motor_resolution_mm,
            gain,
        )
        base = minimum if minimum is not None else inputs.motor_resolution_mm * 2.0
        correction = self._bounded_motion(math.copysign(base, direction), inputs)
        if abs(correction) < inputs.motor_resolution_mm:
            return self._decision(
                inputs,
                ForceControlState.FAULT,
                ForceControlAction.FAULT,
                "probe_below_motor_resolution",
            )
        if inputs.intent in _LANDING_INTENTS:
            self._pending = _PendingCommand(inputs.intent, observation, correction)
        return self._decision(
            inputs,
            ForceControlState.PROBE_REQUIRED,
            ForceControlAction.PROBE_RELATIVE,
            reason,
            correction_mm=correction,
        )

    @staticmethod
    def _state_for_intent(intent: ForceControlIntent) -> ForceControlState:
        return ForceControlState(intent.value)

    def _bounded_motion(self, value: float, inputs: ForceControlInput) -> float:
        command_limit = inputs.max_safe_correction_mm
        gain = self.gain_estimate
        if gain.load_per_mm_g is not None and gain.trusted:
            target_relative_limit = (
                abs(inputs.target_load_g)
                / max(gain.load_per_mm_g, 1e-12)
                * self.config.max_target_fraction_per_command
            )
            command_limit = min(
                command_limit,
                max(inputs.motor_resolution_mm, target_relative_limit),
            )
        limited = max(-command_limit, min(command_limit, value))
        if limited == 0.0:
            return 0.0
        steps = max(1, round(abs(limited) / inputs.motor_resolution_mm))
        quantized = math.copysign(steps * inputs.motor_resolution_mm, limited)
        return max(-command_limit, min(command_limit, quantized))

    def _decision(
        self,
        inputs: ForceControlInput,
        state: ForceControlState,
        action: ForceControlAction,
        reason: str,
        *,
        correction_mm: float = 0.0,
    ) -> ForceControlDecision:
        gain = self.gain_estimate
        return ForceControlDecision(
            intent=inputs.intent,
            state=state,
            action=action,
            correction_mm=correction_mm,
            reason=reason,
            effective_deadband_g=self._deadband_g(inputs),
            minimum_informative_motion_mm=self._minimum_informative_motion(
                self._observation_floor_g(inputs),
                inputs.motor_resolution_mm,
                gain,
            ),
            gain=gain,
            pending_response=self._pending is not None,
        )
