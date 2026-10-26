from dataclasses import replace
import math

import pytest

from data_logging.mini_dma_logger.current_hold_replay import (
    CurrentHoldReplay, ReplayConfig, PRODUCTION_METHODS, app,
)
from scripts.mini_dma_current_hold_replay import utc_seconds, clock_audit, replay, validate_overrides


def feed(host, now, stress):
    load = app.load_g_from_stress_mpa(stress, host.config.diameter_mm)
    host.deliver_scale(sample_s=now, arrival_s=now, applied_load_g=load)
    return host.tick(now, drive_supply=True)


def test_uses_actual_production_bodies_with_isolated_clock(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("UI, settings or hardware boundary used")
    monkeypatch.setattr(app.MainWindow, "__init__", forbidden)
    monkeypatch.setattr(app.QtCore, "QSettings", forbidden)
    monkeypatch.setattr(app.QtWidgets.QApplication, "__init__", forbidden)
    for driver in (app.NativeTicUsbController, app.TicController, app.ScaleWorker,
                   app.SharedPowerSupplyBrokerProcess, app.TmaControlProcess):
        monkeypatch.setattr(driver, "__init__", forbidden)
    original_time = app.time
    a, b = CurrentHoldReplay(), CurrentHoldReplay()
    for name in PRODUCTION_METHODS:
        assert getattr(a, name).__func__.__code__ is getattr(app.MainWindow, name).__code__
    feed(a, 1, 50)
    assert b.clock.now_s == 0
    assert app.time is original_time
    assert not isinstance(a, app.MainWindow)


def test_real_filter_retains_linear_trend_and_rejects_isolated_spike():
    host = CurrentHoldReplay()
    for i in range(9):
        t = i * .2
        stress = 50 + 2*t + (100 if i == 4 else 0)
        feed(host, t, stress)
    signal = host._scale_control_signal_for_basis("stress_mpa", trend_aware=True)
    assert signal.value == pytest.approx(53.2)
    assert signal.slope_per_s == pytest.approx(2)
    assert signal.noise == pytest.approx(0, abs=1e-10)


@pytest.mark.parametrize("profile", list(app.ForceControlProfile))
def test_real_hold_freezes_fake_supply_and_resume_does_not_catch_up(profile):
    host = CurrentHoldReplay(replace(ReplayConfig(), force_control_profile=profile))
    rows = []
    for i in range(121):
        t = i*.25
        stress = 50 if t < 4 or t >= 12 else 90
        rows.append(feed(host, t, stress))
    held = [r for r in rows if r["holding"]]
    assert held
    assert len({r["current_mA"] for r in held}) == 1
    assert not rows[-1]["holding"]
    assert rows[-1]["current_mA"] < 1 + rows[-1]["time_s"] - 4
    assert any("Resumed" in message for message in host.logs)
    for a, b in zip(rows, rows[1:]):
        assert 0 <= b["current_mA"] - a["current_mA"] <= .250001


def test_kosice_confirmation_requires_new_sample_evidence():
    host = CurrentHoldReplay(replace(ReplayConfig(), force_control_profile=app.ForceControlProfile.KOSICE_ADAPTIVE))
    for i in range(3):
        result = feed(host, i*.1, 90)
    assert not result["holding"]
    for t in (.3, .4, .8, 1.0):
        assert not host.tick(t)["holding"]
    assert feed(host, 1.1, 90)["holding"]


def test_delay_dropout_and_stale_feedback_are_not_freshened_by_ticks():
    host = CurrentHoldReplay()
    host.deliver_scale(sample_s=0, arrival_s=.5, applied_load_g=1.0)
    assert host._latest_scale_timestamp == 0
    assert host._latest_scale_arrival_monotonic_s == .5
    result = host.tick(.5 + app.STALE_SCALE_AFTER_S + .1, drive_supply=True)
    assert not result["fresh"]
    assert result["current_mA"] == 1.0
    assert host._latest_scale_timestamp == 0


def test_motor_completion_requires_post_move_feedback():
    host = CurrentHoldReplay()
    feed(host, 0, 50)
    host.simulate_motor_move(now_s=1, duration_s=2)
    feed(host, 2, 50)
    assert not host._has_fresh_scale_reading(after_s=host._motion_feedback_ready_after_s())
    feed(host, 3.1, 50)
    assert host._has_fresh_scale_reading(
        after_s=host._motion_feedback_ready_after_s(),
        after_monotonic_s=host._motion_feedback_ready_after_monotonic_s())


def test_rejects_future_duplicate_and_reverse_clock_inputs():
    host = CurrentHoldReplay()
    with pytest.raises(ValueError):
        host.deliver_scale(sample_s=1, arrival_s=0, applied_load_g=1)
    feed(host, 1, 50)
    with pytest.raises(ValueError):
        feed(host, 1, 50)
    with pytest.raises(ValueError):
        host.tick(.5)
    with pytest.raises(ValueError):
        host.tick(math.nan)


def test_step_reset_clears_previous_hold():
    host = CurrentHoldReplay()
    for i in range(20):
        feed(host, i*.25, 100)
    assert host._current_sweep_ramp_hold_step_index == 0
    host.start_step(target_mpa=100, now_s=5)
    assert not feed(host, 5, 100)["holding"]


def recording():
    meta = {"wire_diameter_mm": .0191, "initial_length_mm": 50,
            "scale": {"baud": 9600, "request_command": r"\x1bp", "line_ending": "", "poll_interval_ms": 250},
            "control": {"control_interval_ms": 50}, "controlled_current_sweep": {
                "current_start_mA": 1, "current_end_mA": 80, "current_ramp_rate_mA_s": 1,
                "current_ramp_hold_on_error": True}}
    cfg = ReplayConfig()
    for name in ("filter_window_s", "noise_sigma", "min_pause_stress_mpa", "min_resume_stress_mpa",
                 "pause_factor", "resume_factor", "resume_stable_s"):
        meta["controlled_current_sweep"]["current_ramp_hold_" + name] = getattr(cfg, "current_sweep_hold_" + name)
    raw = [{"utc_s": 100 + i*.25, "elapsed_s": str(i*.25),
            "applied_load_g": str(app.load_g_from_stress_mpa(50, .0191))} for i in range(20)]
    controls = [{"utc_s": 101 + i, "elapsed_s": str(0 if i == 2 else i),
                 "automation_phase": "current", "automation_basis": "stress_mpa",
                 "automation_target_value": "50", "plateau_index": "1", "timestamp_utc": str(101+i)} for i in range(4)]
    return meta, raw, controls


def test_timestamp_join_handles_elapsed_reset_without_future_scale_input():
    meta, raw, controls = recording()
    early, report = replay(raw, controls, [], meta, offset_s=0, duration_s=4)
    late, _ = replay(raw, controls, [], meta, offset_s=.999, duration_s=4)
    assert clock_audit(controls)["elapsed_resets"] == 1
    assert report["latest_sample_never_future"]
    assert early[0]["latest_sample_s"] < late[0]["latest_sample_s"]
    assert len(early) == 3  # Reset row is not treated as a continuation of the prior step.
    assert all(row["cold_start"] for row in early)
    # A giant future outlier cannot alter any earlier output.
    raw.append({"utc_s": 200, "elapsed_s": "200", "applied_load_g": "999"})
    assert replay(raw, controls, [], meta, offset_s=0, duration_s=4)[0] == early


def test_naive_recorded_utc_is_explicitly_utc():
    assert utc_seconds("2026-05-11 08:30:53") == utc_seconds("2026-05-11T08:30:53+00:00")


def test_indexed_replay_selects_exact_logged_sample_prefix():
    meta, raw, controls = recording()
    for i, row in enumerate(raw):
        row["sample_index"] = str(i+1)
    for i, row in enumerate(controls):
        row["raw_scale_sample_count"] = str(5+i*4)
        row["latest_scale_age_s"] = ".1"
    rows, report = replay(raw, controls, [], meta, offset_s=0, duration_s=4, sample_indexed=True)
    assert report["sample_indexed"]
    assert rows[0]["latest_sample_s"] == pytest.approx(2)
    assert rows[0]["time_s"] == pytest.approx(2.101)
    raw[4]["sample_index"] = "999"
    with pytest.raises(ValueError, match="contiguous"):
        replay(raw, controls, [], meta, offset_s=0, duration_s=4, sample_indexed=True)


def test_only_explicit_target_only_runtime_overrides_are_supported():
    meta, _, _ = recording()
    change = {"timestamp_utc": "2026-08-25 15:21:13", "changes": [
        {"old": {"target_value": 220}, "new": {"target_value": 200}}]}
    meta["controlled_current_sweep"]["runtime_overrides"] = [change]
    assert validate_overrides(meta)[0]["changed_fields"] == ["target_value"]
    change["changes"][0]["new"]["current_hold_resume_stable_s"] = 0
    with pytest.raises(ValueError, match="non-target"):
        validate_overrides(meta)


@pytest.mark.parametrize("motor_busy", [False, True])
def test_real_cycle_center_resume_uses_fake_motor_state(motor_busy):
    host = CurrentHoldReplay(replace(ReplayConfig(), current_sweep_hold_filter_window_s=1.6))
    for i in range(8):
        feed(host, i*.25, 90)
    assert host._current_sweep_ramp_hold_step_index == 0
    if motor_busy:
        host.simulate_motor_move(now_s=1.75, duration_s=100)
    for i in range(8, 180):
        feed(host, i*.25, 50 + (2 if i % 2 else -2))
    cycle_resumes = [r for r in host.trace_events if r["result"] == "cycle_center_resume"]
    assert bool(cycle_resumes) is not motor_busy
    if motor_busy:
        assert host._current_sweep_ramp_hold_step_index == 0
