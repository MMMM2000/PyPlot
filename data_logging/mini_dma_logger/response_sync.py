from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class ResponseSample:
    timestamp_s: float
    value: float


@dataclass(frozen=True)
class ResponseGateResult:
    released: bool
    reason: str
    fresh_sample_count: int
    center: float | None = None
    noise: float = 0.0
    slope_per_s: float = 0.0
    observed: bool = False
    timed_out: bool = False
    crossed_target: bool = False


@dataclass
class PendingResponse:
    feedback_ready_after_s: float
    pre_value: float
    pre_error: float
    correction_tensile_mm: float
    min_fresh_samples: int
    response_wait_s: float
    timeout_s: float


class ResponseSyncTracker:
    """One-command response gate for fast, noisy force feedback."""

    def __init__(self, *, learning_alpha: float = 0.30, nominal_damping: float = 0.50) -> None:
        self.learning_alpha = min(1.0, max(1e-6, float(learning_alpha)))
        self.nominal_damping = min(1.0, max(0.05, float(nominal_damping)))
        self.response_damping = self.nominal_damping
        self.learned_response_time_s: float | None = None
        self.learned_sensitivity_per_mm: float | None = None
        self.pending: PendingResponse | None = None
        self.crossing_count = 0

    @staticmethod
    def requirements(*, sample_rate_hz: float | None, filter_window_s: float) -> tuple[int, float, float]:
        rate_hz = float(sample_rate_hz or 0.0)
        if not math.isfinite(rate_hz) or rate_hz <= 0.0:
            rate_hz = 20.0
        sample_period_s = 1.0 / rate_hz
        min_fresh_samples = max(5, int(math.ceil(rate_hz * max(0.20, sample_period_s * 3.0))))
        response_wait_s = max(min_fresh_samples * sample_period_s, max(0.001, filter_window_s) / 6.0)
        timeout_s = max(response_wait_s * 8.0, max(0.001, filter_window_s) * 2.0)
        return min_fresh_samples, response_wait_s, timeout_s

    def arm(
        self,
        *,
        feedback_ready_after_s: float,
        pre_value: float,
        pre_error: float,
        correction_tensile_mm: float,
        sample_rate_hz: float | None,
        filter_window_s: float,
    ) -> None:
        min_samples, initial_wait_s, timeout_s = self.requirements(
            sample_rate_hz=sample_rate_hz,
            filter_window_s=filter_window_s,
        )
        learned_wait = self.learned_response_time_s
        response_wait_s = initial_wait_s if learned_wait is None else max(initial_wait_s, learned_wait)
        self.pending = PendingResponse(
            feedback_ready_after_s=float(feedback_ready_after_s),
            pre_value=float(pre_value),
            pre_error=float(pre_error),
            correction_tensile_mm=float(correction_tensile_mm),
            min_fresh_samples=min_samples,
            response_wait_s=min(response_wait_s, timeout_s),
            timeout_s=timeout_s,
        )

    def evaluate(
        self,
        samples: Sequence[ResponseSample],
        *,
        target_value: float,
        tolerance: float,
        quantization_band: float,
        now_s: float,
    ) -> ResponseGateResult:
        pending = self.pending
        if pending is None:
            return ResponseGateResult(True, "idle", 0)
        fresh = [sample for sample in samples if sample.timestamp_s > pending.feedback_ready_after_s + 1e-9]
        if len(fresh) < pending.min_fresh_samples:
            return ResponseGateResult(False, "fresh_post_move_samples", len(fresh))

        center, noise, slope = _robust_signal(fresh)
        age_s = max(0.0, float(fresh[-1].timestamp_s) - pending.feedback_ready_after_s)
        if age_s + 1e-12 < pending.response_wait_s:
            return ResponseGateResult(
                False,
                "response_observation_window",
                len(fresh),
                center=center,
                noise=noise,
                slope_per_s=slope,
            )

        response = center - pending.pre_value
        threshold = max(abs(float(tolerance)) * 0.25, noise * 0.50, abs(float(quantization_band)), 1e-9)
        directional = response * pending.correction_tensile_mm > 0.0
        observed = directional and abs(response) >= threshold
        timed_out = age_s + 1e-12 >= pending.timeout_s
        if not observed and not timed_out:
            return ResponseGateResult(
                False,
                "directional_response",
                len(fresh),
                center=center,
                noise=noise,
                slope_per_s=slope,
            )

        post_error = float(target_value) - center
        crossed = pending.pre_error * post_error < 0.0
        alpha = self.learning_alpha
        if observed:
            sensitivity = abs(response / pending.correction_tensile_mm)
            if math.isfinite(sensitivity) and sensitivity > 0.0:
                if self.learned_sensitivity_per_mm is None:
                    self.learned_sensitivity_per_mm = sensitivity
                else:
                    self.learned_sensitivity_per_mm += alpha * (
                        sensitivity - self.learned_sensitivity_per_mm
                    )
            if self.learned_response_time_s is None:
                self.learned_response_time_s = age_s
            else:
                self.learned_response_time_s += alpha * (age_s - self.learned_response_time_s)
            if crossed:
                self.crossing_count += 1
                self.response_damping = max(0.125, self.response_damping * 0.50)
            elif abs(post_error) < abs(pending.pre_error):
                self.response_damping += alpha * (self.nominal_damping - self.response_damping)
            else:
                self.response_damping = max(0.125, self.response_damping * 0.70)
        else:
            self.response_damping = max(0.125, self.response_damping * 0.70)
            if self.learned_response_time_s is None:
                self.learned_response_time_s = pending.timeout_s
            else:
                self.learned_response_time_s = min(
                    pending.timeout_s,
                    max(self.learned_response_time_s, age_s),
                )
        self.pending = None
        return ResponseGateResult(
            True,
            "response_observed" if observed else "response_timeout",
            len(fresh),
            center=center,
            noise=noise,
            slope_per_s=slope,
            observed=observed,
            timed_out=timed_out,
            crossed_target=crossed,
        )

    def correction_cap_mm(self, *, error: float, motor_step_mm: float) -> float | None:
        sensitivity = self.learned_sensitivity_per_mm
        if sensitivity is None or not math.isfinite(sensitivity) or sensitivity <= 0.0:
            return None
        predicted = abs(float(error)) / sensitivity * self.response_damping
        return max(abs(float(motor_step_mm)), predicted)


def _robust_signal(samples: Sequence[ResponseSample]) -> tuple[float, float, float]:
    values = [float(sample.value) for sample in samples]
    center = float(statistics.median(values))
    deviations = [abs(value - center) for value in values]
    noise = 1.4826 * float(statistics.median(deviations)) if deviations else 0.0
    mean_t = sum(sample.timestamp_s for sample in samples) / len(samples)
    mean_v = sum(values) / len(values)
    denominator = sum((sample.timestamp_s - mean_t) ** 2 for sample in samples)
    slope = 0.0
    if denominator > 0.0:
        slope = sum(
            (sample.timestamp_s - mean_t) * (value - mean_v)
            for sample, value in zip(samples, values, strict=False)
        ) / denominator
    return center, max(0.0, noise), float(slope)
