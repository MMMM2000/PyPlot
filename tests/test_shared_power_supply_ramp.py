from __future__ import annotations

import pytest

from data_logging.shared_power_supply.ramp import RateLimitedCurrentRamp


def test_rate_limited_ramp_steps_by_resolution_without_catchup_jump() -> None:
    ramp = RateLimitedCurrentRamp(
        initial_mA=1.0,
        target_mA=3.0,
        rate_mA_s=1.0,
        resolution_mA=0.2,
        max_step_mA=0.2,
        now_s=0.0,
    )

    assert ramp.next_setpoint(now_s=0.1) is None
    assert ramp.next_setpoint(now_s=0.2) == pytest.approx(1.2)
    assert ramp.next_setpoint(now_s=1.2) == pytest.approx(1.4)


def test_rate_limited_ramp_never_overshoots_descending_target() -> None:
    ramp = RateLimitedCurrentRamp(
        initial_mA=3.0,
        target_mA=1.0,
        rate_mA_s=1.0,
        resolution_mA=0.2,
        max_step_mA=0.2,
        now_s=0.0,
    )

    assert ramp.next_setpoint(now_s=0.2) == pytest.approx(2.8)
    ramp.update_target(target_mA=2.6, rate_mA_s=1.0, now_s=0.25)
    assert ramp.next_setpoint(now_s=0.45) == pytest.approx(2.6)
    assert ramp.is_complete


def test_rate_limited_ramp_quantizes_target_and_rate() -> None:
    ramp = RateLimitedCurrentRamp(
        initial_mA=0.0,
        target_mA=0.3,
        rate_mA_s=0.3,
        resolution_mA=0.2,
        max_step_mA=0.2,
        now_s=0.0,
    )

    assert ramp.target_mA == pytest.approx(0.2)
    assert ramp.rate_mA_s == pytest.approx(0.2)
    assert ramp.next_setpoint(now_s=1.0) == pytest.approx(0.2)
