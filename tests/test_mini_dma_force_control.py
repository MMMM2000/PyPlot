from __future__ import annotations

from dataclasses import replace

import pytest

from data_logging.mini_dma_logger.force_control import (
    ForceControlAction,
    ForceControlConfig,
    ForceControlInput,
    ForceControlIntent,
    ForceControlPolicy,
    ForceControlProfile,
    ForceControlState,
)


def _input(**changes: object) -> ForceControlInput:
    values: dict[str, object] = {
        "intent": ForceControlIntent.ACQUIRE_TARGET,
        "target_load_g": 1.0,
        "current_load_g": 0.9,
        "filtered_load_g": 0.9,
        "tolerance_g": 0.005,
        "robust_noise_g": 0.002,
        "quantization_g": 0.01,
        "readability_g": 0.01,
        "position_mm": 0.0,
        "motor_resolution_mm": 0.01,
        "max_safe_correction_mm": 0.25,
        "speed_mm_s": 0.2,
        "target_ramp_g_s": 0.0,
        "ramp_active": False,
        "current_mA": 20.0,
        "current_changing": False,
        "feedback_fresh": True,
        "motor_complete": True,
        "timestamp_s": 1.0,
        "context_key": "wire-a",
    }
    values.update(changes)
    return ForceControlInput(**values)  # type: ignore[arg-type]


def _adaptive(*, gain: float | None = 1.0, **changes: object) -> ForceControlPolicy:
    config = ForceControlConfig(
        profile=ForceControlProfile.KOSICE_ADAPTIVE,
        initial_load_per_mm_g=gain,
        **changes,  # type: ignore[arg-type]
    )
    return ForceControlPolicy(config)


def test_profiles_and_intents_are_explicit() -> None:
    assert set(ForceControlProfile) == {
        ForceControlProfile.PRAGUE_LEGACY,
        ForceControlProfile.KOSICE_ADAPTIVE,
    }
    expected = {state.value for state in ForceControlState}
    assert {intent.value for intent in ForceControlIntent} == expected


def test_quantization_sets_deadband_and_minimum_informative_motion() -> None:
    policy = _adaptive(gain=1.0)

    decision = policy.decide(
        _input(
            intent=ForceControlIntent.HOLD_TARGET,
            target_load_g=1.009,
            current_load_g=1.0,
            filtered_load_g=1.0,
            robust_noise_g=0.001,
            quantization_g=0.02,
            readability_g=0.01,
        )
    )

    assert decision.action is ForceControlAction.NONE
    assert decision.reason == "inside_deadband"
    assert decision.effective_deadband_g == pytest.approx(0.03)
    assert decision.minimum_informative_motion_mm == pytest.approx(0.03)


def test_prague_profile_keeps_the_legacy_measurement_deadband() -> None:
    policy = ForceControlPolicy(
        ForceControlConfig(
            profile=ForceControlProfile.PRAGUE_LEGACY,
            initial_load_per_mm_g=1.0,
        )
    )

    decision = policy.decide(
        _input(
            intent=ForceControlIntent.HOLD_TARGET,
            target_load_g=1.009,
            current_load_g=1.0,
            filtered_load_g=1.0,
            robust_noise_g=0.001,
            quantization_g=0.02,
            readability_g=0.01,
        )
    )

    assert decision.action is ForceControlAction.NONE
    assert decision.effective_deadband_g == pytest.approx(0.02)


def test_pending_command_waits_for_complete_response_observation_window() -> None:
    policy = _adaptive(gain=1.0)
    first = policy.decide(_input(target_load_g=0.95, filtered_load_g=0.9, current_load_g=0.9))
    assert first.action is ForceControlAction.MOVE_RELATIVE
    assert first.correction_mm == pytest.approx(0.02)
    assert first.pending_response is True

    response = policy.decide(
        _input(
            target_load_g=0.95,
            filtered_load_g=0.9,
            current_load_g=0.9,
            position_mm=0.01,
            timestamp_s=2.0,
            response_observation_complete=False,
        )
    )

    assert response.state is ForceControlState.ACQUIRE_TARGET
    assert response.action is ForceControlAction.WAIT_FOR_SAMPLE
    assert response.reason == "response_window_pending"
    assert response.pending_response is True


def test_sub_resolution_response_never_faults_or_grows_geometrically() -> None:
    policy = _adaptive(gain=1.0)
    policy.decide(_input(target_load_g=0.95, max_safe_correction_mm=0.05))
    retry_one = policy.decide(
        _input(target_load_g=0.95, position_mm=0.02, timestamp_s=2.0, max_safe_correction_mm=0.05)
    )
    retry_two = policy.decide(
        _input(target_load_g=0.95, position_mm=0.04, timestamp_s=3.0, max_safe_correction_mm=0.05)
    )
    retry_three = policy.decide(
        _input(target_load_g=0.95, position_mm=0.06, timestamp_s=4.0, max_safe_correction_mm=0.05)
    )

    corrections = (retry_one, retry_two, retry_three)
    assert all(item.action is ForceControlAction.MOVE_RELATIVE for item in corrections)
    assert all(item.state is not ForceControlState.FAULT for item in corrections)
    assert all(
        item.reason == "sub_resolution_response_bounded_correction"
        for item in corrections
    )
    assert [abs(item.correction_mm) for item in corrections] == pytest.approx([0.02, 0.02, 0.02])


def test_run03_like_repeated_sub_resolution_responses_never_stop_control() -> None:
    policy = _adaptive(gain=1.55984728)
    target_load_g = 1.326
    current_load_g = target_load_g + 2.77351894 * target_load_g / 50.0
    position_mm = 9.84
    timestamp_s = 1.0
    decisions = []

    for _ in range(30):
        decision = policy.decide(
            _input(
                intent=ForceControlIntent.RECOVER_DISTURBANCE,
                target_load_g=target_load_g,
                current_load_g=current_load_g,
                filtered_load_g=current_load_g,
                tolerance_g=0.01,
                robust_noise_g=0.0,
                quantization_g=0.01,
                readability_g=0.01,
                position_mm=position_mm,
                motor_resolution_mm=0.01,
                timestamp_s=timestamp_s,
                context_key="kosice-run03-50mpa",
            )
        )
        decisions.append(decision)
        assert decision.action is not ForceControlAction.FAULT
        if decision.action is ForceControlAction.NONE:
            break
        if decision.action in {
            ForceControlAction.MOVE_RELATIVE,
            ForceControlAction.PROBE_RELATIVE,
        }:
            position_mm += decision.correction_mm
            current_load_g -= 0.003
        timestamp_s += 1.0

    assert decisions[-1].action is ForceControlAction.NONE
    assert any(
        item.reason == "sub_resolution_response_bounded_correction"
        for item in decisions
    )


def test_sub_resolution_response_inside_achievable_deadband_settles() -> None:
    policy = _adaptive(gain=0.9)
    first = policy.decide(
        _input(
            target_load_g=0.925,
            filtered_load_g=0.9,
            current_load_g=0.9,
            quantization_g=0.01,
            readability_g=0.01,
        )
    )
    assert first.action is ForceControlAction.MOVE_RELATIVE
    assert first.correction_mm == pytest.approx(0.02)

    settled = policy.decide(
        _input(
            target_load_g=0.925,
            filtered_load_g=0.908,
            current_load_g=0.908,
            position_mm=0.02,
            timestamp_s=2.0,
            quantization_g=0.01,
            readability_g=0.01,
        )
    )

    assert settled.action is ForceControlAction.NONE
    assert settled.reason == "response_inside_achievable_deadband"
    assert settled.effective_deadband_g == pytest.approx(0.018)


def test_transformation_trend_increases_same_direction_recovery() -> None:
    baseline = _adaptive(gain=1.0).decide(
        _input(
            intent=ForceControlIntent.RECOVER_DISTURBANCE,
            target_load_g=0.8,
            filtered_load_g=0.9,
            current_load_g=0.9,
        )
    )
    rising = _adaptive(gain=1.0).decide(
        _input(
            intent=ForceControlIntent.RECOVER_DISTURBANCE,
            target_load_g=0.8,
            filtered_load_g=0.9,
            current_load_g=0.9,
            filtered_slope_g_s=0.4,
        )
    )

    assert rising.correction_mm < baseline.correction_mm < 0.0
    assert abs(rising.correction_mm) > abs(baseline.correction_mm)


def test_direction_reversal_requires_persistent_error() -> None:
    policy = _adaptive(gain=1.0, reversal_confirmation_s=0.3)
    first = policy.decide(_input(target_load_g=0.8))
    assert first.correction_mm < 0.0

    crossing = policy.decide(
        _input(
            target_load_g=1.0,
            filtered_load_g=0.8,
            current_load_g=0.8,
            position_mm=first.correction_mm,
            timestamp_s=2.0,
        )
    )
    waiting = policy.decide(
        _input(
            target_load_g=1.0,
            filtered_load_g=0.8,
            current_load_g=0.8,
            position_mm=first.correction_mm,
            timestamp_s=2.2,
        )
    )
    confirmed = policy.decide(
        _input(
            target_load_g=1.0,
            filtered_load_g=0.8,
            current_load_g=0.8,
            position_mm=first.correction_mm,
            timestamp_s=2.31,
        )
    )

    assert crossing.reason == "direction_reversal_confirmation"
    assert waiting.reason == "direction_reversal_confirmation"
    assert confirmed.action is ForceControlAction.MOVE_RELATIVE
    assert confirmed.correction_mm > 0.0


def test_command_is_capped_relative_to_target_and_retry_does_not_escalate() -> None:
    policy = _adaptive(gain=1.0)
    command = policy.decide(_input(target_load_g=1.4))
    retry = policy.decide(
        _input(target_load_g=1.4, position_mm=command.correction_mm, timestamp_s=2.0)
    )

    assert command.correction_mm == pytest.approx(0.12)
    assert command.correction_mm <= 0.14
    assert retry.action is ForceControlAction.MOVE_RELATIVE
    assert retry.correction_mm == pytest.approx(command.correction_mm)


def test_tracking_has_no_pending_response_but_landing_serializes_commands() -> None:
    policy = _adaptive(gain=2.0)
    tracking = policy.decide(
        _input(intent=ForceControlIntent.TRACK_TRAJECTORY, target_load_g=1.2)
    )
    assert tracking.action is ForceControlAction.MOVE_RELATIVE
    assert tracking.pending_response is False

    tracking_next = policy.decide(
        _input(
            intent=ForceControlIntent.TRACK_TRAJECTORY,
            target_load_g=1.25,
            timestamp_s=2.0,
            position_mm=tracking.correction_mm,
            filtered_load_g=0.95,
        )
    )
    assert tracking_next.action is ForceControlAction.MOVE_RELATIVE
    assert tracking_next.pending_response is False

    landing = policy.decide(
        _input(intent=ForceControlIntent.ACQUIRE_TARGET, target_load_g=1.2, timestamp_s=3.0)
    )
    waiting = policy.decide(
        _input(
            intent=ForceControlIntent.ACQUIRE_TARGET,
            target_load_g=1.2,
            timestamp_s=4.0,
            motor_complete=False,
        )
    )
    assert landing.pending_response is True
    assert waiting.action is ForceControlAction.WAIT_FOR_MOTOR
    assert waiting.pending_response is True


def test_uncalibrated_descending_trajectory_probes_in_relaxation_direction() -> None:
    policy = _adaptive(gain=None)

    decision = policy.decide(
        _input(
            intent=ForceControlIntent.TRACK_TRAJECTORY,
            target_load_g=0.8,
            current_load_g=1.0,
            filtered_load_g=1.0,
            target_ramp_g_s=-0.1,
            ramp_active=True,
        )
    )

    assert decision.action is ForceControlAction.PROBE_RELATIVE
    assert decision.reason == "trajectory_gain_untrusted"
    assert decision.correction_mm < 0.0


def test_quiet_ramp_endpoint_switches_from_feedforward_to_acquisition() -> None:
    policy = _adaptive(gain=2.0)
    tracking = policy.decide(
        _input(
            intent=ForceControlIntent.TRACK_TRAJECTORY,
            target_load_g=1.0,
            current_load_g=1.0,
            filtered_load_g=1.0,
            tolerance_g=0.01,
            robust_noise_g=0.001,
            quantization_g=0.001,
            readability_g=0.001,
            target_ramp_g_s=0.10,
            ramp_active=True,
        )
    )
    acquired = policy.decide(
        _input(
            intent=ForceControlIntent.ACQUIRE_TARGET,
            target_load_g=1.0,
            current_load_g=1.0,
            filtered_load_g=1.0,
            tolerance_g=0.01,
            robust_noise_g=0.001,
            quantization_g=0.001,
            readability_g=0.001,
            target_ramp_g_s=0.0,
            ramp_active=False,
            timestamp_s=2.0,
        )
    )

    assert tracking.action is ForceControlAction.MOVE_RELATIVE
    assert tracking.reason == "trajectory_correction"
    assert acquired.action is ForceControlAction.NONE
    assert acquired.reason == "inside_deadband"


def test_intent_change_does_not_issue_a_second_command_while_motor_is_active() -> None:
    policy = _adaptive(gain=2.0)
    first = policy.decide(
        _input(intent=ForceControlIntent.ACQUIRE_TARGET, target_load_g=1.2)
    )

    changed = policy.decide(
        _input(
            intent=ForceControlIntent.HOLD_TARGET,
            target_load_g=1.2,
            timestamp_s=1.1,
            motor_complete=False,
            response_observation_complete=False,
        )
    )

    assert first.action is ForceControlAction.MOVE_RELATIVE
    assert changed.action is ForceControlAction.WAIT_FOR_MOTOR
    assert changed.reason == "intent_changed_while_motor_active"
    assert changed.correction_mm == 0.0


def test_hold_deadband_uses_filtered_load_and_noise_floor() -> None:
    policy = _adaptive(gain=2.0)
    decision = policy.decide(
        _input(
            intent=ForceControlIntent.HOLD_TARGET,
            target_load_g=1.0,
            current_load_g=1.2,
            filtered_load_g=0.988,
            robust_noise_g=0.005,
            quantization_g=0.001,
            readability_g=0.001,
        )
    )
    assert decision.action is ForceControlAction.NONE
    assert decision.effective_deadband_g == pytest.approx(0.02)


def test_adaptive_gain_learns_from_observable_cumulative_windows() -> None:
    policy = _adaptive(gain=1.0, minimum_gain_windows=2)
    policy.decide(_input(intent=ForceControlIntent.TRACK_TRAJECTORY, target_load_g=0.0))
    policy.decide(
        _input(
            intent=ForceControlIntent.TRACK_TRAJECTORY,
            target_load_g=0.0,
            position_mm=0.10,
            filtered_load_g=1.10,
            timestamp_s=2.0,
        )
    )
    learned = policy.decide(
        _input(
            intent=ForceControlIntent.TRACK_TRAJECTORY,
            target_load_g=0.0,
            position_mm=0.20,
            filtered_load_g=1.30,
            timestamp_s=3.0,
        )
    ).gain

    assert learned.observable_windows == 2
    assert learned.load_per_mm_g == pytest.approx(2.0)
    assert learned.uncertainty_g_per_mm == pytest.approx(0.0)
    assert learned.trusted is True


def test_gain_learning_excludes_current_change_and_removes_stationary_drift() -> None:
    policy = _adaptive(gain=1.0, minimum_gain_windows=1, minimum_gain_confidence=0.2)
    policy.decide(_input(intent=ForceControlIntent.TRACK_TRAJECTORY, target_load_g=0.0))
    changed_current = policy.decide(
        _input(
            intent=ForceControlIntent.TRACK_TRAJECTORY,
            target_load_g=0.0,
            position_mm=0.10,
            filtered_load_g=1.20,
            current_changing=True,
            timestamp_s=2.0,
        )
    )
    assert changed_current.gain.observable_windows == 0
    assert changed_current.gain.excluded_windows == 1

    policy.reset("drift-test")
    policy.decide(
        _input(
            intent=ForceControlIntent.TRACK_TRAJECTORY,
            target_load_g=0.0,
            context_key="drift-test",
            timestamp_s=10.0,
        )
    )
    policy.decide(
        _input(
            intent=ForceControlIntent.TRACK_TRAJECTORY,
            target_load_g=0.0,
            context_key="drift-test",
            filtered_load_g=0.92,
            timestamp_s=11.0,
        )
    )
    learned = policy.decide(
        _input(
            intent=ForceControlIntent.TRACK_TRAJECTORY,
            target_load_g=0.0,
            context_key="drift-test",
            position_mm=0.10,
            filtered_load_g=1.14,
            timestamp_s=12.0,
        )
    ).gain
    assert learned.observable_windows == 1
    assert learned.load_per_mm_g == pytest.approx(2.0)


def test_stale_feedback_does_not_consume_pending_response() -> None:
    policy = _adaptive(gain=1.0)
    policy.decide(_input(target_load_g=1.2))
    stale = policy.decide(
        _input(target_load_g=1.2, timestamp_s=2.0, feedback_fresh=False, motor_complete=True)
    )
    waiting = policy.decide(
        _input(target_load_g=1.2, timestamp_s=3.0, motor_complete=False)
    )

    assert stale.action is ForceControlAction.WAIT_FOR_SAMPLE
    assert stale.pending_response is True
    assert waiting.action is ForceControlAction.WAIT_FOR_MOTOR


def test_cancel_pending_prevents_probe_escalation_after_rejected_transport_move() -> None:
    policy = _adaptive(gain=1.0)
    offered = policy.decide(_input(target_load_g=1.2))
    assert offered.action is ForceControlAction.MOVE_RELATIVE
    assert offered.pending_response is True

    policy.cancel_pending()
    retried = policy.decide(_input(target_load_g=1.2, timestamp_s=2.0))

    assert retried.action is ForceControlAction.MOVE_RELATIVE
    assert retried.reason == "landing_correction"


def test_reset_and_context_change_clear_pending_and_learning() -> None:
    policy = _adaptive(gain=1.0, minimum_gain_windows=1, minimum_gain_confidence=0.2)
    policy.decide(_input(intent=ForceControlIntent.TRACK_TRAJECTORY, target_load_g=0.0))
    learned = policy.decide(
        _input(
            intent=ForceControlIntent.TRACK_TRAJECTORY,
            target_load_g=0.0,
            position_mm=0.10,
            filtered_load_g=1.10,
            timestamp_s=2.0,
        )
    )
    assert learned.gain.observable_windows == 1

    changed = policy.decide(
        replace(
            _input(intent=ForceControlIntent.HOLD_TARGET),
            context_key="wire-b",
            timestamp_s=3.0,
        )
    )
    assert changed.gain.observable_windows == 0

    policy.reset("wire-b")
    setup = policy.decide(
        _input(
            intent=ForceControlIntent.SETUP,
            context_key="wire-b",
            timestamp_s=4.0,
            target_load_g=1.2,
        )
    )
    assert setup.state is ForceControlState.SETUP
    assert setup.action is ForceControlAction.MOVE_RELATIVE
    assert setup.pending_response is True


def test_non_monotonic_timestamp_enters_fault() -> None:
    policy = ForceControlPolicy(
        ForceControlConfig(
            profile=ForceControlProfile.PRAGUE_LEGACY,
            initial_load_per_mm_g=1.0,
        )
    )
    policy.decide(_input())
    repeated = policy.decide(_input(timestamp_s=1.0))
    fault = policy.decide(_input(timestamp_s=0.5))
    assert repeated.action is ForceControlAction.WAIT_FOR_SAMPLE
    assert repeated.reason == "sample_not_advanced"
    assert fault.state is ForceControlState.FAULT
    assert fault.action is ForceControlAction.FAULT
    assert fault.reason == "timestamp_not_monotonic"
