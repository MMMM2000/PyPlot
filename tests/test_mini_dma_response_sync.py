from __future__ import annotations

import math

import pytest

from data_logging.mini_dma_logger.response_sync import ResponseSample, ResponseSyncTracker


def _samples(start_s: float, values: list[float], *, dt_s: float = 0.05) -> list[ResponseSample]:
    return [
        ResponseSample(timestamp_s=start_s + (index + 1) * dt_s, value=value)
        for index, value in enumerate(values)
    ]


def test_response_sync_requires_fresh_samples_after_motor_completion() -> None:
    tracker = ResponseSyncTracker()
    tracker.arm(
        feedback_ready_after_s=10.2,
        pre_value=20.0,
        pre_error=30.0,
        correction_tensile_mm=0.02,
        sample_rate_hz=20.0,
        filter_window_s=1.8,
    )

    pre_completion = _samples(10.0, [20.0, 21.0, 22.0, 23.0, 24.0])
    result = tracker.evaluate(
        pre_completion,
        target_value=50.0,
        tolerance=1.0,
        quantization_band=0.4,
        now_s=10.25,
    )
    assert result.released is False
    assert result.fresh_sample_count == 1

    post_completion = pre_completion + _samples(10.2, [24.0, 25.0, 26.0, 27.0, 28.0, 29.0, 30.0])
    result = tracker.evaluate(
        post_completion,
        target_value=50.0,
        tolerance=1.0,
        quantization_band=0.4,
        now_s=10.55,
    )
    assert result.released is True
    assert result.observed is True
    assert tracker.pending is None


def test_response_sync_keeps_one_command_in_flight_until_directional_response() -> None:
    tracker = ResponseSyncTracker()
    tracker.arm(
        feedback_ready_after_s=5.0,
        pre_value=40.0,
        pre_error=10.0,
        correction_tensile_mm=0.01,
        sample_rate_hz=20.0,
        filter_window_s=1.8,
    )

    for count in range(1, 8):
        flat = _samples(5.0, [40.0] * count)
        result = tracker.evaluate(
            flat,
            target_value=50.0,
            tolerance=1.0,
            quantization_band=0.4,
            now_s=flat[-1].timestamp_s,
        )
        assert result.released is False
        assert tracker.pending is not None

    response = _samples(5.0, [40.0, 40.0, 40.5, 42.0, 44.0, 45.0, 45.5])
    result = tracker.evaluate(
        response,
        target_value=50.0,
        tolerance=1.0,
        quantization_band=0.4,
        now_s=response[-1].timestamp_s,
    )
    assert result.released is True
    assert result.reason == "response_observed"
    assert tracker.learned_sensitivity_per_mm is not None


def test_response_sync_timeout_releases_damped_but_never_reuses_stale_samples() -> None:
    tracker = ResponseSyncTracker()
    tracker.arm(
        feedback_ready_after_s=1.0,
        pre_value=30.0,
        pre_error=20.0,
        correction_tensile_mm=0.01,
        sample_rate_hz=20.0,
        filter_window_s=1.8,
    )
    pending = tracker.pending
    assert pending is not None

    stale = _samples(0.0, [30.0] * 5)
    result = tracker.evaluate(
        stale,
        target_value=50.0,
        tolerance=1.0,
        quantization_band=0.4,
        now_s=100.0,
    )
    assert result.released is False
    assert result.fresh_sample_count == 0

    flat = _samples(1.0, [30.0] * int(math.ceil(pending.timeout_s / 0.05) + 1))
    result = tracker.evaluate(
        flat,
        target_value=50.0,
        tolerance=1.0,
        quantization_band=0.4,
        now_s=flat[-1].timestamp_s,
    )
    assert result.released is True
    assert result.timed_out is True
    assert tracker.response_damping < tracker.nominal_damping


def test_response_sync_fake_kern_plant_converges_through_noise_and_transformation() -> None:
    tracker = ResponseSyncTracker()
    target = 50.0
    plant = 8.0
    sensitivity = 520.0
    motor_step_mm = 1.0 / 800.0
    timestamp_s = 0.0
    maxima: list[float] = [plant]

    def correct_once(current: float) -> float:
        nonlocal timestamp_s
        error = target - current
        learned_cap = tracker.correction_cap_mm(error=error, motor_step_mm=motor_step_mm)
        correction = abs(error) / sensitivity * 0.50
        if learned_cap is not None:
            correction = min(correction, learned_cap)
        correction = math.copysign(max(motor_step_mm, correction), error)
        ready_s = timestamp_s + 0.10
        tracker.arm(
            feedback_ready_after_s=ready_s,
            pre_value=current,
            pre_error=error,
            correction_tensile_mm=correction,
            sample_rate_hz=20.0,
            filter_window_s=1.8,
        )
        next_value = current + correction * sensitivity
        values = [next_value + offset for offset in (1.6, -1.2, 0.8, -0.6, 0.3, -0.2, 0.0)]
        samples = _samples(ready_s, values)
        timestamp_s = samples[-1].timestamp_s
        result = tracker.evaluate(
            samples,
            target_value=target,
            tolerance=1.0,
            quantization_band=0.4,
            now_s=timestamp_s,
        )
        assert result.released is True
        assert result.center is not None
        maxima.extend(values)
        return result.center

    for _ in range(8):
        plant = correct_once(plant)
        if abs(target - plant) <= 1.5:
            break
    assert abs(target - plant) <= 1.5
    assert max(maxima) < 80.0

    plant -= 18.0
    for _ in range(8):
        plant = correct_once(plant)
        if abs(target - plant) <= 1.5:
            break
    assert abs(target - plant) <= 1.5
    assert tracker.learned_sensitivity_per_mm == pytest.approx(sensitivity, rel=0.20)
