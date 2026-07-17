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


@dataclass(frozen=True, slots=True)
class GainEstimate:
    load_per_mm_g: float | None
    uncertainty_g_per_mm: float | None
    confidence: float
    observable_windows: int
    excluded_windows: int
    trusted: bool


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
    max_probe_attempts: int = 3
    probe_growth: float = 2.0
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
    probe_attempt: int


def _median_absolute_deviation(values: list[float], center: float) -> float:
    return statistics.median(abs(value - center) for value in values)


def _finite(values: tuple[float, ...]) -> bool:
    return all(math.isfinite(value) for value in values)


class ForceControlPolicy:
    """Deterministic dual-profile force-control state machine."""

    def __init__(self, config: ForceControlConfig) -> None:
        if config.initial_load_per_mm_g is not None and config.initial_load_per_mm_g <= 0.0:
            raise ValueError("initial_load_per_mm_g must be positive")
        if config.max_probe_attempts < 1:
            raise ValueError("max_probe_attempts must be positive")
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
            return self._request_probe(inputs, observation, probe_attempt=1, reason="probe_requested")
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
        return max(
            inputs.tolerance_g,
            inputs.quantization_g,
            inputs.readability_g,
            self.config.noise_sigma * inputs.robust_noise_g,
        )

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
        correction = self._correction_for_error(inputs, error_g + ramp_feedforward_g)
        if correction is None:
            return self._request_probe(inputs, observation, 1, "trajectory_gain_untrusted")
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
                    next_attempt = pending.probe_attempt + 1
                    if next_attempt > self.config.max_probe_attempts:
                        return self._decision(
                            inputs,
                            ForceControlState.FAULT,
                            ForceControlAction.FAULT,
                            "response_unobservable",
                        )
                    error_g = inputs.target_load_g - inputs.filtered_load_g
                    if abs(error_g) <= self._deadband_g(inputs):
                        return self._decision(
                            inputs,
                            state,
                            ForceControlAction.NONE,
                            "response_arrived_inside_deadband",
                        )
                    correction = self._correction_for_error(inputs, error_g)
                    if correction is not None:
                        self._pending = _PendingCommand(
                            inputs.intent,
                            observation,
                            correction,
                            next_attempt,
                        )
                        return self._decision(
                            inputs,
                            state,
                            ForceControlAction.MOVE_RELATIVE,
                            "response_unobservable_bounded_retry",
                            correction_mm=correction,
                        )
                    return self._request_probe(
                        inputs,
                        observation,
                        next_attempt,
                        "response_unobservable_probe",
                        direction=1.0 if pending.correction_mm >= 0.0 else -1.0,
                        previous_motion_mm=abs(pending.correction_mm),
                    )

        error_g = inputs.target_load_g - inputs.filtered_load_g
        if abs(error_g) <= self._deadband_g(inputs):
            return self._decision(inputs, state, ForceControlAction.NONE, "inside_deadband")
        correction = self._correction_for_error(inputs, error_g)
        if correction is None:
            return self._request_probe(
                inputs,
                observation,
                1,
                "gain_untrusted",
                direction=1.0 if error_g >= 0.0 else -1.0,
            )
        self._pending = _PendingCommand(inputs.intent, observation, correction, 0)
        return self._decision(
            inputs,
            state,
            ForceControlAction.MOVE_RELATIVE,
            "landing_correction",
            correction_mm=correction,
        )

    def _correction_for_error(self, inputs: ForceControlInput, error_g: float) -> float | None:
        gain = self.gain_estimate
        if gain.load_per_mm_g is None or not gain.trusted:
            return None
        raw = error_g / gain.load_per_mm_g * self.config.correction_fraction
        return self._bounded_motion(raw, inputs)

    def _request_probe(
        self,
        inputs: ForceControlInput,
        observation: _Observation,
        probe_attempt: int,
        reason: str,
        direction: float = 1.0,
        previous_motion_mm: float = 0.0,
    ) -> ForceControlDecision:
        gain = self.gain_estimate
        minimum = self._minimum_informative_motion(
            observation.observation_floor_g,
            inputs.motor_resolution_mm,
            gain,
        )
        base = minimum if minimum is not None else inputs.motor_resolution_mm * 2.0
        requested = max(
            base * (self.config.probe_growth ** max(0, probe_attempt - 1)),
            previous_motion_mm * self.config.probe_growth,
        )
        correction = self._bounded_motion(math.copysign(requested, direction), inputs)
        if abs(correction) < inputs.motor_resolution_mm:
            return self._decision(
                inputs,
                ForceControlState.FAULT,
                ForceControlAction.FAULT,
                "probe_below_motor_resolution",
            )
        if inputs.intent in _LANDING_INTENTS:
            self._pending = _PendingCommand(inputs.intent, observation, correction, probe_attempt)
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
