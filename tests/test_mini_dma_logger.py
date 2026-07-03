from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import csv
import dataclasses
import importlib
import json
import math
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pandas as pd

pytest.importorskip(
    "PyQt6.QtWidgets",
    reason="Qt widgets backend is unavailable",
    exc_type=ImportError,
)

from PyQt6 import QtCore, QtGui, QtWidgets
from data_logging.shared_power_supply.profiles import HMP4040_PROFILE

TEST_QSETTINGS_ROOT = Path(
    os.environ.get("PYTEST_QSETTINGS_ROOT", "artifacts/test-qsettings")
)
TEST_QSETTINGS_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["MINI_DMA_QSETTINGS_INI_DIR"] = str(TEST_QSETTINGS_ROOT)

mini_dma_mod = importlib.import_module(
    "data_logging.mini_dma_logger.mini_dma_logger"
)
stiff_guard_mod = importlib.import_module(
    "data_logging.mini_dma_logger.stiff_sample_guard"
)


@pytest.fixture(autouse=True)
def _block_real_tic_usb_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must not claim the bench Tic USB device."""

    def _blocked_backend() -> object:
        raise RuntimeError("real Tic USB access is disabled in tests")

    monkeypatch.setattr(mini_dma_mod, "_load_pyusb_backend", _blocked_backend)


def _test_settings() -> QtCore.QSettings:
    return QtCore.QSettings(
        str(TEST_QSETTINGS_ROOT / "mini_dma_logger.ini"),
        QtCore.QSettings.Format.IniFormat,
    )


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _snapshot_settings() -> dict[str, object]:
    settings = _test_settings()
    return {key: settings.value(key) for key in settings.allKeys()}


def _restore_settings(snapshot: dict[str, object]) -> None:
    settings = _test_settings()
    settings.clear()
    for key, value in snapshot.items():
        settings.setValue(key, value)
    settings.sync()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (800.0, "800 A/mm<sup>2</sup>"),
        (750.0, "750 A/mm<sup>2</sup>"),
        (80.0, "80 A/mm<sup>2</sup>"),
        (75.0, "75 A/mm<sup>2</sup>"),
        (8.0, "8 A/mm<sup>2</sup>"),
    ],
)
def test_compact_unit_format_preserves_integer_zeros(value: float, expected: str) -> None:
    assert mini_dma_mod._format_compact_unit(value, "A/mm<sup>2</sup>", decimals=0) == expected


def test_automation_control_loop_ticks_without_qt_event_processing() -> None:
    ticks: list[float] = []
    loop = mini_dma_mod.AutomationControlLoop(lambda: ticks.append(time.monotonic()))

    try:
        loop.start(20)
        deadline = time.monotonic() + 0.35
        while len(ticks) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)

        assert len(ticks) >= 4
        assert loop.is_running() is True
    finally:
        loop.stop()


def test_automation_control_loop_pause_resume_and_stop() -> None:
    ticks: list[float] = []
    loop = mini_dma_mod.AutomationControlLoop(lambda: ticks.append(time.monotonic()))

    try:
        loop.start(20)
        deadline = time.monotonic() + 0.3
        while len(ticks) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(ticks) >= 3

        loop.pause()
        paused_count = len(ticks)
        time.sleep(0.08)
        assert len(ticks) == paused_count
        assert loop.is_running() is True
        assert loop.is_paused() is True

        loop.resume()
        deadline = time.monotonic() + 0.3
        while len(ticks) <= paused_count and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(ticks) > paused_count

        loop.stop()
        stopped_count = len(ticks)
        time.sleep(0.08)
        assert len(ticks) == stopped_count
        assert loop.is_running() is False
    finally:
        loop.stop()


def test_main_window_automation_control_loop_ticks_off_ui_thread_without_qt_events(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    main_thread_id = threading.get_ident()
    tick_thread_ids: list[int] = []

    def fake_tick() -> None:
        tick_thread_ids.append(threading.get_ident())
        if len(tick_thread_ids) >= 3:
            window._stop_automation_control_loop()

    try:
        window._run_automation_control_tick = fake_tick  # type: ignore[method-assign]
        window._start_automation_control_loop(20)
        deadline = time.monotonic() + 0.5
        while len(tick_thread_ids) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)

        assert len(tick_thread_ids) >= 3
        assert all(thread_id != main_thread_id for thread_id in tick_thread_ids)
        assert window._auto_ramp_timer.isActive() is False
    finally:
        window._stop_automation_control_loop()
        _close_test_window(window)


def test_main_window_automation_tick_delegates_to_controller(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    calls: list[str] = []

    class FakeController:
        def tick(self) -> None:
            calls.append("tick")

    try:
        window._automation_controller = FakeController()  # type: ignore[assignment]
        window._handle_auto_ramp_tick()

        assert calls == ["tick"]
    finally:
        _close_test_window(window)


def test_background_control_loop_advances_recipe_without_ui_events(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    calls: list[int] = []

    try:
        window._automation_active = True
        window._automation_paused = False
        window._automation_steps = [
            mini_dma_mod.AutomationStep("sweep_current", note="background-loop"),
            mini_dma_mod.AutomationStep("sweep_current", note="background-loop"),
            mini_dma_mod.AutomationStep("sweep_current", note="background-loop"),
        ]
        window._automation_index = 0
        window._automation_total_steps = len(window._automation_steps)
        window._handle_current_sweep_step = lambda _step, index: calls.append(index) or True  # type: ignore[method-assign]
        window._update_recipe_progress = lambda **_kwargs: None  # type: ignore[method-assign]
        window._refresh_live_labels = lambda: None  # type: ignore[method-assign]

        window._start_automation_control_loop(20)
        deadline = time.monotonic() + 0.5
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)

        assert calls == [0, 1, 2]
        assert window._automation_index >= 3
        assert window._auto_ramp_timer.isActive() is False
    finally:
        window._stop_automation_control_loop()
        window._automation_active = False
        _close_test_window(window)


def test_run_log_batches_during_active_session(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._session_active = True
        window._automation_active = True
        window._automation_control_loop = mini_dma_mod.AutomationControlLoop(lambda: None)
        window._automation_control_loop.start(1000)

        window._log("first")
        window._log("second")

        assert "first" not in window.log_output.toPlainText()
        assert "second" not in window.log_output.toPlainText()
        assert len(window._pending_run_log_lines) == 2

        window._flush_pending_run_log_lines()

        text = window.log_output.toPlainText()
        assert "first" in text
        assert "second" in text
        assert window._pending_run_log_lines == []
    finally:
        if window._automation_control_loop is not None:
            window._automation_control_loop.stop()
            window._automation_control_loop = None
        window._session_active = False
        window._automation_active = False
        _close_test_window(window)


def test_worker_visual_updates_are_coalesced(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    callbacks: list[object] = []
    original_ui_thread_id = window._ui_thread_id

    try:
        window._ui_thread_id = -1
        window._run_on_ui_thread = lambda callback: callbacks.append(callback)  # type: ignore[method-assign]

        window._refresh_live_labels()
        window._refresh_live_labels()
        window._update_recipe_progress()
        window._update_recipe_progress()
        window._update_recipe_progress(complete=True)

        assert len(callbacks) == 2
        assert window._live_label_refresh_queued is True
        assert window._recipe_progress_update_queued is True
        assert window._recipe_progress_pending_complete is True
    finally:
        window._ui_thread_id = original_ui_thread_id
        _close_test_window(window)


def test_automation_controller_dispatches_steps_outside_main_window(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    calls: list[tuple[str, int]] = []

    try:
        window._automation_active = True
        window._automation_paused = False
        window._automation_steps = [
            mini_dma_mod.AutomationStep("sweep_current", note="controller-boundary"),
        ]
        window._automation_index = 0
        window._automation_total_steps = 1
        window._handle_current_sweep_step = lambda _step, index: calls.append(("sweep", index)) or True  # type: ignore[method-assign]
        window._update_recipe_progress = lambda **_kwargs: None  # type: ignore[method-assign]
        window._refresh_live_labels = lambda: None  # type: ignore[method-assign]

        window._automation_controller.tick()

        assert calls == [("sweep", 0)]
        assert window._automation_index == 1
        assert window._automation_completed_ticks == 1
        assert window._automation_tick_running is False
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_automation_controller_does_not_advance_progress_during_current_hold(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._automation_active = True
        window._automation_paused = False
        window._automation_steps = [
            mini_dma_mod.AutomationStep("sweep_current", note="controller-boundary"),
        ]
        window._automation_index = 0
        window._automation_total_steps = 100
        window._automation_completed_ticks = 42

        def _hold_step(_step: object, _index: int) -> bool:
            window._automation_phase = "current_hold"
            return False

        window._handle_current_sweep_step = _hold_step  # type: ignore[method-assign]
        window._update_recipe_progress = lambda **_kwargs: None  # type: ignore[method-assign]
        window._refresh_live_labels = lambda: None  # type: ignore[method-assign]

        window._automation_controller.tick()

        assert window._automation_index == 0
        assert window._automation_completed_ticks == 42
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_recipe_start_freezes_control_config_before_worker_ticks(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._preflight_recipe_hardware = lambda _steps, **_kwargs: True  # type: ignore[method-assign]
        window._prepare_continuity_current_for_recipe = lambda _steps: True  # type: ignore[method-assign]
        window._start_session = lambda **_kwargs: setattr(window, "_session_active", True)  # type: ignore[method-assign]
        window._start_automation_control_loop = lambda _interval_ms: None  # type: ignore[method-assign]
        window.spin_diameter.setValue(0.05)
        window.spin_steps_per_mm.setValue(800.0)
        window.spin_initial_length.setValue(25.0)
        window.spin_motion_speed_mm_s.setValue(0.75)
        window.spin_current_sweep_target_speed_mm_s.setValue(0.20)
        window.spin_current_sweep_hold_filter_window_s.setValue(2.5)
        window.spin_current_sweep_hold_noise_sigma.setValue(4.0)
        window.check_max_load.setChecked(True)
        window.spin_max_load_g.setValue(30.0)
        window.spin_raw_scale_limit_g.setValue(45.0)
        window.combo_scale_baud.setCurrentText("256000")
        window.edit_scale_request.setText(mini_dma_mod.KERN_KCP_SCALE_REQUEST)
        window.edit_scale_terminator.setText(mini_dma_mod.KERN_KCP_SCALE_TERMINATOR)

        window._start_auto_ramp()
        assert window._active_control_config is not None
        config = window._active_control_config

        window.spin_diameter.setValue(0.10)
        window.spin_steps_per_mm.setValue(100.0)
        window.spin_initial_length.setValue(80.0)
        window.spin_motion_speed_mm_s.setValue(5.0)
        window.spin_current_sweep_target_speed_mm_s.setValue(4.0)
        window.spin_current_sweep_hold_filter_window_s.setValue(10.0)
        window.spin_current_sweep_hold_noise_sigma.setValue(9.0)
        window.spin_max_load_g.setValue(90.0)
        window.spin_raw_scale_limit_g.setValue(100.0)

        assert config.diameter_mm == pytest.approx(0.05)
        assert config.steps_per_mm == pytest.approx(800.0)
        assert config.initial_length_mm == pytest.approx(25.0)
        assert config.motion_speed_mm_s == pytest.approx(0.75)
        assert config.current_sweep_target_speed_mm_s == pytest.approx(0.20)
        assert config.current_sweep_hold_filter_window_s == pytest.approx(2.5)
        assert config.current_sweep_hold_noise_sigma == pytest.approx(4.0)
        assert config.max_load_g == pytest.approx(30.0)
        assert config.raw_scale_limit_g == pytest.approx(45.0)
        assert config.scale_baudrate == 256000
        assert config.scale_request_command == mini_dma_mod.KERN_KCP_SCALE_REQUEST
        assert config.scale_terminator == mini_dma_mod.KERN_KCP_SCALE_TERMINATOR
        assert config.scale_readability_g == pytest.approx(0.01)
        assert window._raw_scale_display_limit_g() == pytest.approx(45.0)
        assert window._motor_step_mm() == pytest.approx(1.0 / 800.0)
        assert window._setup_motion_speed_cap_mm_s() == pytest.approx(0.75)
        assert window._current_sweep_stage_speed_cap_mm_s() == pytest.approx(0.20)
        assert window._current_sweep_hold_filter_window_s() == pytest.approx(2.5)
        assert window._current_sweep_hold_noise_sigma() == pytest.approx(4.0)
    finally:
        window._stop_automation_control_loop()
        _close_test_window(window)


def _build_window(
    tmp_path: Path,
    qtbot,
    *,
    preserve_settings: bool = False,
) -> mini_dma_mod.MainWindow:
    _ensure_app()
    snapshot = _snapshot_settings() if preserve_settings else {}
    if not preserve_settings:
        settings = _test_settings()
        settings.clear()
        settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)
    window.check_zero_position_on_start.setChecked(False)
    window.check_tare_on_start.setChecked(False)
    window.spin_zero_load_scale_g.setValue(0.0)
    return window


def _close_test_window(window: mini_dma_mod.MainWindow) -> None:
    snapshot = getattr(window, "_test_settings_snapshot", None)
    window.close()
    _ensure_app().processEvents()
    if isinstance(snapshot, dict):
        _restore_settings(snapshot)


def test_current_sweep_hold_bands_are_bounded_processed_signal_multipliers() -> None:
    assert mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_NOISE_CAP_TOLERANCE_FACTOR == pytest.approx(3.0)
    assert mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_ENTRY_TOLERANCE_FACTOR == pytest.approx(3.0)
    assert mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_LARGE_ERROR_FACTOR == pytest.approx(4.0)


def test_kern_scale_quantization_sets_worsening_evidence_floor(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window.combo_scale_baud.setCurrentText("256000")
        window.edit_scale_request.setText(mini_dma_mod.KERN_KCP_SCALE_REQUEST)
        window.edit_scale_terminator.setText(mini_dma_mod.KERN_KCP_SCALE_TERMINATOR)
        window.spin_diameter.setValue(0.0182)

        one_count_mpa = window._scale_quantization_band_for_basis(mini_dma_mod.HSW_BASIS_STRESS_MPA)
        floor = window._current_sweep_worsening_floor_for_basis(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            tolerance=0.171,
            filtered_signal=None,
        )

        assert one_count_mpa == pytest.approx(0.3769537067)
        assert one_count_mpa < floor
        assert one_count_mpa * 2.0 > floor
    finally:
        _close_test_window(window)


def test_kern_scale_uses_conservative_fast_feedback_hold_caps(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        assert window._current_sweep_hold_base_command_strain_pct() == pytest.approx(0.24)
        assert window._current_sweep_hold_adaptive_max_command_strain_pct() == pytest.approx(0.35)

        window.combo_scale_baud.setCurrentText("256000")
        window.edit_scale_request.setText(mini_dma_mod.KERN_KCP_SCALE_REQUEST)
        window.edit_scale_terminator.setText(mini_dma_mod.KERN_KCP_SCALE_TERMINATOR)

        assert window._current_sweep_hold_base_command_strain_pct() == pytest.approx(0.08)
        assert window._current_sweep_hold_adaptive_max_command_strain_pct() == pytest.approx(0.10)
    finally:
        _close_test_window(window)


def test_kern_hold_earned_resume_band_scales_from_entry_error(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window.combo_scale_baud.setCurrentText("256000")
        window.edit_scale_request.setText(mini_dma_mod.KERN_KCP_SCALE_REQUEST)
        window.edit_scale_terminator.setText(mini_dma_mod.KERN_KCP_SCALE_TERMINATOR)
        step = mini_dma_mod.AutomationStep(
            "current",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
        )
        signal = mini_dma_mod.ScaleControlSignal(
            value=54.0,
            latest_value=54.0,
            noise=0.2,
            slope_per_s=-2.0,
            sample_count=6,
            timestamp_s=12.0,
        )
        window._current_sweep_ramp_hold_entry_abs_error = 20.0

        band = window._current_sweep_hold_earned_resume_band(
            step,
            signed_error=4.0,
            resume_band=1.0,
            pause_band=8.0,
            filtered_signal=signal,
        )

        assert band == pytest.approx(
            20.0 * mini_dma_mod.KERN_CURRENT_SWEEP_HOLD_EARNED_RESUME_ENTRY_FRACTION
        )
    finally:
        _close_test_window(window)


def test_kern_hold_earned_resume_requires_improvement_and_non_away_slope(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window.combo_scale_baud.setCurrentText("256000")
        window.edit_scale_request.setText(mini_dma_mod.KERN_KCP_SCALE_REQUEST)
        window.edit_scale_terminator.setText(mini_dma_mod.KERN_KCP_SCALE_TERMINATOR)
        step = mini_dma_mod.AutomationStep(
            "current",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
        )
        window._current_sweep_ramp_hold_entry_abs_error = 20.0
        improving_signal = mini_dma_mod.ScaleControlSignal(
            value=58.0,
            latest_value=58.0,
            noise=0.2,
            slope_per_s=-2.0,
            sample_count=6,
            timestamp_s=12.0,
        )
        away_signal = mini_dma_mod.ScaleControlSignal(
            value=54.0,
            latest_value=54.0,
            noise=0.2,
            slope_per_s=2.0,
            sample_count=6,
            timestamp_s=12.0,
        )

        not_improved_enough = window._current_sweep_hold_earned_resume_band(
            step,
            signed_error=8.0,
            resume_band=1.0,
            pause_band=8.0,
            filtered_signal=improving_signal,
        )
        moving_away = window._current_sweep_hold_earned_resume_band(
            step,
            signed_error=4.0,
            resume_band=1.0,
            pause_band=8.0,
            filtered_signal=away_signal,
        )

        assert not_improved_enough == pytest.approx(1.0)
        assert moving_away == pytest.approx(1.0)
    finally:
        _close_test_window(window)


def test_prague_scale_ignores_kern_earned_resume_band(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window.combo_scale_baud.setCurrentText("9600")
        window.edit_scale_request.setText("P")
        window.edit_scale_terminator.setText("\\r\\n")
        step = mini_dma_mod.AutomationStep(
            "current",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
        )
        signal = mini_dma_mod.ScaleControlSignal(
            value=54.0,
            latest_value=54.0,
            noise=0.2,
            slope_per_s=-2.0,
            sample_count=6,
            timestamp_s=12.0,
        )
        window._current_sweep_ramp_hold_entry_abs_error = 20.0

        assert window._current_sweep_hold_earned_resume_band(
            step,
            signed_error=4.0,
            resume_band=1.0,
            pause_band=8.0,
            filtered_signal=signal,
        ) == pytest.approx(1.0)
    finally:
        _close_test_window(window)


def test_kern_held_recovery_uses_earned_resume_band_before_exact_seek(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    resumed: list[str] = []
    try:
        window.combo_scale_baud.setCurrentText("256000")
        window.edit_scale_request.setText(mini_dma_mod.KERN_KCP_SCALE_REQUEST)
        window.edit_scale_terminator.setText(mini_dma_mod.KERN_KCP_SCALE_TERMINATOR)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._current_sweep_ramp_hold_step_index = 1
        window._current_sweep_ramp_hold_started_s = 90.0
        window._current_sweep_ramp_hold_entry_abs_error = 20.0
        window._current_sweep_ramp_hold_entry_pause_band = 8.0
        window._current_sweep_ramp_hold_in_band_since_s = 100.0
        monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: 101.0)
        step = mini_dma_mod.AutomationStep(
            "sweep_current",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            current_hold_enabled=True,
            current_hold_resume_stable_s=0.5,
        )
        signal = mini_dma_mod.ScaleControlSignal(
            value=54.0,
            latest_value=54.0,
            noise=0.2,
            slope_per_s=-2.0,
            sample_count=6,
            timestamp_s=101.0,
        )
        window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: (4.0, 4.0, 1.0, 0.2)
        )
        window._scale_control_signal_for_basis = lambda *_args, **_kwargs: signal  # type: ignore[method-assign]
        window._current_sweep_filtered_window_spans_target = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
        window._seek_distribution_target = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("exact seek should not run"))
        )
        window._resume_current_sweep_ramp_from_hold = (  # type: ignore[method-assign]
            lambda **kwargs: resumed.append(str(kwargs["reason"]))
        )

        assert window._handle_current_sweep_held_recovery(
            step,
            plateau_index=1,
            tolerance=1.0,
        ) is False

        assert resumed
        assert "adaptive resume band" in resumed[-1]
    finally:
        _close_test_window(window)


def test_kern_held_recovery_preserves_base_resume_confirmation(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    resumed: list[str] = []
    try:
        window.combo_scale_baud.setCurrentText("256000")
        window.edit_scale_request.setText(mini_dma_mod.KERN_KCP_SCALE_REQUEST)
        window.edit_scale_terminator.setText(mini_dma_mod.KERN_KCP_SCALE_TERMINATOR)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._current_sweep_ramp_hold_step_index = 1
        window._current_sweep_ramp_hold_started_s = 90.0
        window._current_sweep_ramp_hold_entry_abs_error = None
        step = mini_dma_mod.AutomationStep(
            "sweep_current",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            current_start_mA=1.0,
            current_end_mA=60.0,
            current_hold_enabled=True,
            current_hold_resume_stable_s=0.5,
        )
        signal = mini_dma_mod.ScaleControlSignal(
            value=50.8,
            latest_value=50.8,
            noise=0.05,
            slope_per_s=0.0,
            sample_count=8,
            timestamp_s=100.0,
        )
        window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: (0.8, 0.8, 0.25, 0.05)
        )
        window._scale_control_signal_for_basis = lambda *_args, **_kwargs: signal  # type: ignore[method-assign]
        window._current_sweep_filtered_window_spans_target = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
        window._resume_current_sweep_ramp_from_hold = (  # type: ignore[method-assign]
            lambda **kwargs: resumed.append(str(kwargs["reason"]))
        )

        assert window._update_current_sweep_ramp_hold(step, 1, now_s=100.0) == (True, False)
        assert window._current_sweep_ramp_hold_in_band_since_s == pytest.approx(100.0)

        assert window._maybe_resume_current_sweep_held_recovery_from_adaptive_band(
            step,
            now_s=100.0,
        ) is False
        assert window._current_sweep_ramp_hold_in_band_since_s == pytest.approx(100.0)

        assert window._update_current_sweep_ramp_hold(step, 1, now_s=100.6) == (False, False)

        assert resumed
        assert "resume band" in resumed[-1]
    finally:
        _close_test_window(window)


def test_prague_held_recovery_ignores_kern_earned_resume_shortcut(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    resumed: list[str] = []
    try:
        window.combo_scale_baud.setCurrentText("9600")
        window.edit_scale_request.setText("P")
        window.edit_scale_terminator.setText("\\r\\n")
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._current_sweep_ramp_hold_step_index = 1
        window._current_sweep_ramp_hold_started_s = 90.0
        window._current_sweep_ramp_hold_entry_abs_error = 20.0
        window._current_sweep_ramp_hold_entry_pause_band = 8.0
        window._current_sweep_ramp_hold_in_band_since_s = 100.0
        step = mini_dma_mod.AutomationStep(
            "sweep_current",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            current_hold_enabled=True,
            current_hold_resume_stable_s=0.5,
        )
        signal = mini_dma_mod.ScaleControlSignal(
            value=54.0,
            latest_value=54.0,
            noise=0.2,
            slope_per_s=-2.0,
            sample_count=6,
            timestamp_s=101.0,
        )
        window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: (4.0, 4.0, 1.0, 0.2)
        )
        window._scale_control_signal_for_basis = lambda *_args, **_kwargs: signal  # type: ignore[method-assign]
        window._current_sweep_filtered_window_spans_target = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
        window._resume_current_sweep_ramp_from_hold = (  # type: ignore[method-assign]
            lambda **kwargs: resumed.append(str(kwargs["reason"]))
        )

        assert window._maybe_resume_current_sweep_held_recovery_from_adaptive_band(
            step,
            now_s=101.0,
        ) is False

        assert resumed == []
    finally:
        _close_test_window(window)


def test_kern_scale_waits_for_filter_window_when_filtered_signal_lags(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window.combo_scale_baud.setCurrentText("256000")
        window.edit_scale_request.setText(mini_dma_mod.KERN_KCP_SCALE_REQUEST)
        window.edit_scale_terminator.setText(mini_dma_mod.KERN_KCP_SCALE_TERMINATOR)
        window.spin_diameter.setValue(0.0182)
        seek_key = (mini_dma_mod.HSW_BASIS_STRESS_MPA, 1, 50.0)
        window._seek_last_filtered_value_by_key[seek_key] = 50.0
        window._seek_last_latest_signal_value_by_key[seek_key] = 50.0
        window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = 10.0
        window._latest_scale_timestamp = 10.05
        signal = mini_dma_mod.ScaleControlSignal(
            value=50.05,
            latest_value=50.7,
            noise=0.1,
            slope_per_s=0.0,
            sample_count=4,
            timestamp_s=10.05,
        )

        assert not window._filtered_signal_changed_after_last_correction(
            seek_key,
            signal,
            effective_tolerance=1.0,
        )
    finally:
        _close_test_window(window)


def test_prague_scale_waits_for_filter_window_when_filtered_signal_lags(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window.combo_scale_baud.setCurrentText("9600")
        window.edit_scale_request.setText("P")
        window.edit_scale_terminator.setText("\\r\\n")
        seek_key = (mini_dma_mod.HSW_BASIS_STRESS_MPA, 1, 50.0)
        window._seek_last_filtered_value_by_key[seek_key] = 50.0
        window._seek_last_latest_signal_value_by_key[seek_key] = 50.0
        window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = 10.0
        window._latest_scale_timestamp = 10.05
        signal = mini_dma_mod.ScaleControlSignal(
            value=50.05,
            latest_value=50.5,
            noise=0.1,
            slope_per_s=0.0,
            sample_count=4,
            timestamp_s=10.05,
        )

        assert not window._filtered_signal_changed_after_last_correction(
            seek_key,
            signal,
            effective_tolerance=1.0,
        )
    finally:
        _close_test_window(window)


def test_current_sweep_load_stress_control_disables_cruise_feedback(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_phase = "current"
        window._automation_step_note = "1"

        assert window._seek_supports_cruise_feedback(mini_dma_mod.HSW_BASIS_STRESS_MPA) is False
        assert window._seek_supports_cruise_feedback(mini_dma_mod.HSW_BASIS_LOAD_G) is False
    finally:
        _close_test_window(window)


def _wait_for_tic_commands(window: mini_dma_mod.MainWindow) -> None:
    dispatcher = getattr(window, "_tic_command_dispatcher", None)
    if dispatcher is not None:
        assert dispatcher.wait_until_idle(timeout_s=2.0)


def test_length_setup_dialog_close_clears_owned_widgets_and_restores_focus(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    focus_requests: list[bool] = []
    window._restore_main_window_focus_soon = lambda: focus_requests.append(True)  # type: ignore[method-assign]

    try:
        window._show_length_setup_dialog()
        dialog = window._length_setup_dialog
        assert dialog is not None
        assert window._length_setup_status_label is not None
        assert window._length_setup_progress is not None

        window._close_length_setup_dialog()
        _ensure_app().processEvents()

        assert window._length_setup_dialog is None
        assert window._length_setup_status_label is None
        assert window._length_setup_progress is None
        assert window._button_length_setup_pause is None
        assert window._button_length_setup_stop is None
        assert focus_requests
    finally:
        _close_test_window(window)


def test_window_close_suppresses_recovery_prompt_and_closes_setup_dialog(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    recovery_prompts: list[bool] = []

    def fail_if_recovery_prompted() -> None:
        recovery_prompts.append(True)

    window._ask_recovery_after_stop = fail_if_recovery_prompted  # type: ignore[method-assign]

    try:
        window._show_length_setup_dialog()
        assert window._length_setup_dialog is not None

        window.close()
        _ensure_app().processEvents()

        assert window._window_closing is True
        assert window._length_setup_dialog is None
        assert recovery_prompts == []
    finally:
        _close_test_window(window)


class _ImmediateTicDispatcher:
    def __init__(self, controller: object) -> None:
        self.controller = controller

    def set_target_position(self, position_steps: int, max_speed: int | None = None) -> int:
        self.controller.set_target_position(position_steps, max_speed=max_speed)
        return 1

    def reset_command_timeout(self) -> None:
        if hasattr(self.controller, "reset_command_timeout"):
            self.controller.reset_command_timeout()

    def halt_and_hold(self) -> None:
        if hasattr(self.controller, "halt_and_hold"):
            self.controller.halt_and_hold()

    def set_current_position(self, position_steps: int) -> None:
        if hasattr(self.controller, "set_current_position"):
            self.controller.set_current_position(position_steps)

    def wait_until_idle(self, *, timeout_s: float = 2.0) -> bool:
        return True

    def stop(self, *, timeout_s: float = 2.0) -> None:
        return None


def _use_immediate_tic_dispatcher(window: mini_dma_mod.MainWindow, controller: object) -> None:
    window._tic_command_dispatcher = _ImmediateTicDispatcher(controller)  # type: ignore[assignment]
    window._tic_command_dispatcher_key = (
        window.edit_ticcmd_path.text().strip(),
        window.edit_tic_serial.text().strip(),
        bool(window.check_tic_native_usb.isChecked()),
    )


def _set_plot_tile(
    window: mini_dma_mod.MainWindow,
    index: int,
    x_key: str,
    y_left_key: str,
    y_right_key: str,
    *,
    visible: bool = True,
) -> None:
    tile = window._plot_tiles[index]
    tile.visible.setChecked(visible)
    for combo, key in (
        (tile.x_combo, x_key),
        (tile.y_left_combo, y_left_key),
        (tile.y_right_combo, y_right_key),
    ):
        combo_index = combo.findData(key)
        assert combo_index >= 0, key
        combo.setCurrentIndex(combo_index)


def _saved_plot_tile_values(index: int) -> dict[str, object]:
    settings = _test_settings()
    prefix = f"plot_tile_{index}"
    return {
        "visible": settings.value(f"{prefix}_visible", type=bool),
        "x": settings.value(f"{prefix}_x", type=str),
        "y_left": settings.value(f"{prefix}_y_left", type=str),
        "y_right": settings.value(f"{prefix}_y_right", type=str),
    }


def _saved_recipe_plot_tile_values(mode: str, index: int) -> dict[str, object]:
    settings = _test_settings()
    mode_key = re.sub(r"[^A-Za-z0-9_]+", "_", mode).strip("_") or "recipe"
    prefix = f"plot_tile_recipe_{mode_key}_{index}"
    return {
        "visible": settings.value(f"{prefix}_visible", type=bool),
        "x": settings.value(f"{prefix}_x", type=str),
        "y_left": settings.value(f"{prefix}_y_left", type=str),
        "y_right": settings.value(f"{prefix}_y_right", type=str),
    }


USER_DASHBOARD_PLOTS = [
    ("elapsed_s", "load_g", "stress_mpa"),
    ("elapsed_s", "position_mm", "strain_pct"),
    ("elapsed_s", "current_set_mA", "current_measured_mA"),
    ("elapsed_s", "resistance_ohm", "voltage_V"),
]


def test_review_window_can_skip_settings_persistence(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    expected_values = {
        "name_composition": "Ni50Fe27Ga23",
        "name_wire": "12/2",
        "name_specimen": "",
        "name_condition": "test",
        "diameter_mm": 0.0191,
        "builder_project_path": "G:/My Drive/1 Projects/Praha/microwire_project.pydpj",
        "log_dir": "C:/Users/Martin/Downloads",
        "log_name": "Ni50Fe27Ga23 12_2 test",
        "sample_name": "Ni50Fe27Ga23 12/2 test",
    }
    settings.clear()
    for key, value in expected_values.items():
        settings.setValue(key, value)
    settings.sync()

    try:
        window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
        qtbot.addWidget(window)

        window.edit_name_composition.setText("temporary")
        window.edit_name_wire.setText("99/9")
        window.edit_name_condition.setText("review")
        window.spin_diameter.setValue(0.12345)
        window.edit_project_path.setText("")
        window.edit_log_dir.setText(str(tmp_path / "artifacts"))
        window.edit_log_name.setText("temporary_review")
        window.edit_sample_name.setText("temporary sample")
        window.close()
        _ensure_app().processEvents()

        preserved = {key: settings.value(key) for key in expected_values}
        assert float(preserved.pop("diameter_mm")) == pytest.approx(expected_values["diameter_mm"])
        expected_without_diameter = dict(expected_values)
        expected_without_diameter.pop("diameter_mm")
        assert preserved == expected_without_diameter
    finally:
        _restore_settings(snapshot)


def test_review_window_can_skip_dashboard_plot_persistence(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    for index, (x_key, y_left_key, y_right_key) in enumerate(USER_DASHBOARD_PLOTS):
        prefix = f"plot_tile_{index}"
        settings.setValue(f"{prefix}_visible", True)
        settings.setValue(f"{prefix}_x", x_key)
        settings.setValue(f"{prefix}_y_left", y_left_key)
        settings.setValue(f"{prefix}_y_right", y_right_key)
    settings.sync()

    try:
        window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
        qtbot.addWidget(window)

        window._apply_plot_preset("dma")
        window._plot_tiles[3].visible.setChecked(False)
        window.close()
        _ensure_app().processEvents()

        for index, (x_key, y_left_key, y_right_key) in enumerate(USER_DASHBOARD_PLOTS):
            assert _saved_plot_tile_values(index) == {
                "visible": True,
                "x": x_key,
                "y_left": y_left_key,
                "y_right": y_right_key,
            }
    finally:
        _restore_settings(snapshot)


def test_main_window_persists_sample_fields_by_default(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.sync()

    try:
        window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
        qtbot.addWidget(window)

        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setText("12/2")
        window.edit_name_specimen.setText("")
        window.edit_name_condition.setText("test")
        window.spin_diameter.setValue(0.0191)
        window.edit_project_path.setText("G:/My Drive/1 Projects/Praha/microwire_project.pydpj")
        window.edit_log_dir.setText("C:/Users/Martin/Downloads")
        window.edit_log_name.setText("Ni50Fe27Ga23 12_2 test")
        window.edit_sample_name.setText("Ni50Fe27Ga23 12/2 test")
        window.close()
        _ensure_app().processEvents()

        assert settings.value("name_composition") == "Ni50Fe27Ga23"
        assert settings.value("name_wire") == "12/2"
        assert settings.value("name_condition") == "test"
        assert float(settings.value("diameter_mm")) == pytest.approx(0.0191)
        assert settings.value("builder_project_path") == "G:/My Drive/1 Projects/Praha/microwire_project.pydpj"
        assert settings.value("log_dir") == "C:/Users/Martin/Downloads"
        assert settings.value("log_name") == "Ni50Fe27Ga23 12_2 test"
        assert settings.value("sample_name") == "Ni50Fe27Ga23 12/2 test"
    finally:
        _restore_settings(snapshot)


def test_main_window_persists_dashboard_plots_by_default(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.sync()

    try:
        window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
        qtbot.addWidget(window)
        recipe_key = str(window.combo_recipe_mode.currentData())

        for index, (x_key, y_left_key, y_right_key) in enumerate(USER_DASHBOARD_PLOTS):
            _set_plot_tile(window, index, x_key, y_left_key, y_right_key)
        window.close()
        _ensure_app().processEvents()

        for index, (x_key, y_left_key, y_right_key) in enumerate(USER_DASHBOARD_PLOTS):
            assert _saved_recipe_plot_tile_values(recipe_key, index) == {
                "visible": True,
                "x": x_key,
                "y_left": y_left_key,
                "y_right": y_right_key,
            }
            assert _saved_plot_tile_values(index) == {
                "visible": True,
                "x": "elapsed_s",
                "y_left": "load_g",
                "y_right": "",
            }
    finally:
        _restore_settings(snapshot)


def test_dashboard_plot_choices_persist_immediately(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.sync()

    try:
        window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
        qtbot.addWidget(window)
        recipe_key = str(window.combo_recipe_mode.currentData())

        _set_plot_tile(window, 0, "elapsed_s", "position_mm", "strain_pct")
        _ensure_app().processEvents()

        assert _saved_recipe_plot_tile_values(recipe_key, 0) == {
            "visible": True,
            "x": "elapsed_s",
            "y_left": "position_mm",
            "y_right": "strain_pct",
        }
    finally:
        window.close()
        _ensure_app().processEvents()
        _restore_settings(snapshot)


def test_normal_open_close_preserves_saved_sample_and_dashboard_settings(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    expected_values = {
        "name_composition": "Ni50Fe27Ga23",
        "name_wire": "12/2",
        "name_specimen": "",
        "name_condition": "test",
        "diameter_mm": 0.0191,
        "builder_project_path": "G:/My Drive/1 Projects/Praha/microwire_project.pydpj",
        "log_dir": "C:/Users/Martin/Downloads",
        "log_name": "custom_saved_log",
        "sample_name": "custom saved sample",
    }
    settings.clear()
    for key, value in expected_values.items():
        settings.setValue(key, value)
    for index, (x_key, y_left_key, y_right_key) in enumerate(USER_DASHBOARD_PLOTS):
        prefix = f"plot_tile_{index}"
        settings.setValue(f"{prefix}_visible", True)
        settings.setValue(f"{prefix}_x", x_key)
        settings.setValue(f"{prefix}_y_left", y_left_key)
        settings.setValue(f"{prefix}_y_right", y_right_key)
    settings.sync()

    try:
        window = mini_dma_mod.MainWindow()
        qtbot.addWidget(window)
        window.close()
        _ensure_app().processEvents()

        preserved = {key: settings.value(key) for key in expected_values}
        assert float(preserved.pop("diameter_mm")) == pytest.approx(expected_values["diameter_mm"])
        expected_without_diameter = dict(expected_values)
        expected_without_diameter.pop("diameter_mm")
        assert preserved == expected_without_diameter
        for index, (x_key, y_left_key, y_right_key) in enumerate(USER_DASHBOARD_PLOTS):
            assert _saved_plot_tile_values(index) == {
                "visible": True,
                "x": x_key,
                "y_left": y_left_key,
                "y_right": y_right_key,
            }
    finally:
        _restore_settings(snapshot)


def test_test_log_dir_override_does_not_replace_saved_output_dir(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("log_dir", "C:/Users/Martin/Downloads")
    settings.sync()

    try:
        window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
        qtbot.addWidget(window)
        window.close()
        _ensure_app().processEvents()

        assert settings.value("log_dir") == "C:/Users/Martin/Downloads"
    finally:
        _restore_settings(snapshot)


def test_restore_keeps_custom_sample_and_log_names(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("name_composition", "Ni50Fe27Ga23")
    settings.setValue("name_wire", "12/2")
    settings.setValue("name_specimen", "")
    settings.setValue("name_condition", "test")
    settings.setValue("sample_name", "custom saved sample")
    settings.setValue("log_name", "custom_saved_log")
    settings.sync()

    try:
        window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
        window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
        qtbot.addWidget(window)

        assert window.edit_sample_name.text() == "custom saved sample"
        assert window.edit_log_name.text() == "custom_saved_log"
    finally:
        _close_test_window(window)


def test_restore_replaces_stale_auto_sample_name_from_saved_fields(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("name_composition", "Ni46Fe27Ga23Co2Cu2")
    settings.setValue("name_wire", "2/8")
    settings.setValue("name_specimen", "")
    settings.setValue("name_condition", "")
    settings.setValue("sample_name", "Ni50Fe27Ga23 12_2 heat shield")
    settings.setValue("log_name", "Ni46Fe27Ga23Co2Cu2 2_8 iso-stress")
    settings.sync()

    try:
        window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
        window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
        qtbot.addWidget(window)

        assert window.edit_name_composition.text() == "Ni46Fe27Ga23Co2Cu2"
        assert window.edit_name_wire.text() == "2/8"
        assert window.edit_sample_name.text() == "Ni46Fe27Ga23Co2Cu2 2/8"
        assert "Ni46Fe27Ga23Co2Cu2 2/8" in window.label_recipe_sample.text()
    finally:
        _close_test_window(window)


def test_session_start_persists_sample_fields_without_close(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.sync()

    try:
        window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
        qtbot.addWidget(window)
        window.check_zero_position_on_start.setChecked(False)
        window.check_tare_on_start.setChecked(False)
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setText("12/2")
        window.edit_name_condition.setText("test")
        window.edit_sample_name.setText("saved before close")
        window.edit_project_path.setText("G:/My Drive/1 Projects/Praha/microwire_project.pydpj")

        window._start_session(record_initial_point=False)

        assert settings.value("name_composition") == "Ni50Fe27Ga23"
        assert settings.value("name_wire") == "12/2"
        assert settings.value("name_condition") == "test"
        assert settings.value("sample_name") == "saved before close"
        assert settings.value("builder_project_path") == "G:/My Drive/1 Projects/Praha/microwire_project.pydpj"
    finally:
        window.close()
        _ensure_app().processEvents()
        _restore_settings(snapshot)


def test_zero_load_reference_defaults_to_measured_hanging_weight(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
        assert not hasattr(window, "check_hardware_tare_on_start")
    finally:
        _close_test_window(window)


def test_load_g_from_stress_mpa_inverts_stress_conversion() -> None:
    load_g = mini_dma_mod.load_g_from_stress_mpa(10.0, 0.03)

    assert load_g == pytest.approx(0.7208, rel=5e-4)
    assert mini_dma_mod.stress_mpa_from_load_g(load_g, 0.03) == pytest.approx(10.0)


def test_length_setup_steps_precede_current_sweep_recipe(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
    assert mode_index >= 0
    window.combo_recipe_mode.setCurrentIndex(mode_index)
    window.spin_setup_preload_stress_mpa.setValue(10.0)
    window.spin_setup_preload_duration_s.setValue(5.0)
    window.spin_setup_preload_stable_s.setValue(2.0)
    window.spin_setup_zero_stable_s.setValue(1.0)
    window.spin_current_sweep_target_start.setValue(0.0)
    window.spin_current_sweep_target_end.setValue(10.0)
    window.spin_current_sweep_target_step.setValue(10.0)
    window.spin_control_interval.setValue(250)

    try:
        steps, summary, interval_ms = window._build_automation_recipe()

        assert interval_ms == 250
        assert "length setup" in summary
        assert steps[0].action == "starting_length_prompt"
        actions = [step.action for step in steps]
        assert "starting_length_prompt" in actions
        assert "measure_length_prompt" not in actions
        assert "mark_setup_return_zero" in actions
        assert "apply_length_setup" in actions
        assert "start_session" in actions
        start_length_index = actions.index("starting_length_prompt")
        mark_index = actions.index("mark_setup_return_zero")
        apply_index = actions.index("apply_length_setup")
        session_index = actions.index("start_session")
        first_recipe_index = actions.index("set_current")
        assert start_length_index < mark_index < apply_index < session_index < first_recipe_index
        preload_step = next(step for step in steps if step.note == "setup_preload")
        assert preload_step.action == "ramp_target"
        assert preload_step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA
        assert preload_step.target_end_value == pytest.approx(10.0)
        assert preload_step.target_ramp_rate_value_s == pytest.approx(2.0)
        preload_settle = next(step for step in steps if step.action == "settle" and step.note == "setup_preload")
        return_zero_index = next(
            index
            for index, step in enumerate(steps)
            if step.action == "seek_target" and step.note == "setup_return_zero"
        )
        assert preload_settle.duration_s == pytest.approx(2.0)
        assert not any(step.action == "settle" and step.note == "setup_return_zero" for step in steps)
        assert steps[return_zero_index - 1].action == "mark_setup_return_zero"
        assert steps[return_zero_index + 1].action == "apply_length_setup"
    finally:
        _close_test_window(window)


def test_preload_length_setup_calculates_l0_from_tensile_stage_delta(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_positive_motion_is_tension.setChecked(False)

    try:
        l0 = window._apply_preload_length_result(
            measured_length_mm=30.5,
            preload_position_mm=-1.5,
            zero_position_mm=-1.0,
        )

        assert l0 == pytest.approx(30.0)
        assert window.spin_initial_length.value() == pytest.approx(30.0)
        assert window._position_reference_mm == pytest.approx(-1.0)
        assert "Computed l0 = 30" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_length_setup_uses_linear_unload_intercept_for_l0(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_positive_motion_is_tension.setChecked(False)
    window.check_tension_load_positive.setChecked(False)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window._setup_measured_length_mm = 30.5
    window._setup_preload_position_mm = -1.5
    window._setup_return_zero_start_point_index = 0
    window._current_position_mm = -0.82
    window._effective_position_mm = -0.82
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

    try:
        for index, (raw_position_mm, stress_mpa) in enumerate(
            [
                (-1.50, 20.0),
                (-1.42, 16.0),
                (-1.34, 12.0),
                (-1.26, 8.0),
                (-1.18, 4.0),
                (-1.10, 0.4),
                (-0.95, 0.2),
                (-0.82, 0.1),
            ]
        ):
            load_g = mini_dma_mod.load_g_from_stress_mpa(stress_mpa, window.spin_diameter.value())
            assert load_g is not None
            point = window._capture_measurement_point(
                elapsed_s=index * 0.25,
                position_mm=raw_position_mm,
                effective_position_mm=raw_position_mm,
                raw_load_g=load_g,
                load_g=load_g,
            )
            window._length_setup_points.append(point)

        assert window._handle_apply_length_setup_step() is True

        assert window._setup_zero_position_mm == pytest.approx(-1.1)
        assert window.spin_initial_length.value() == pytest.approx(30.1)
        assert window._active_control_config is not None
        assert window._active_control_config.initial_length_mm == pytest.approx(30.1)
        assert "linear unload fit" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_setup_return_zero_stops_at_linear_unload_slack_onset(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    targets: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: targets.append(target_mm) or True  # type: ignore[method-assign]
    window.spin_diameter.setValue(0.0137)
    window.spin_steps_per_mm.setValue(800.0)
    window._automation_step_note = "setup_return_zero"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window._automation_phase = "seek"
    window._setup_return_zero_start_point_index = 0
    window._current_position_mm = 7.18
    window._effective_position_mm = 7.18

    try:
        unload_points = [
            (7.000, 20.0),
            (7.012, 16.5),
            (7.025, 12.5),
            (7.038, 8.7),
            (7.052, 5.2),
            (7.066, 3.6),
            (7.080, 2.5),
            (7.110, 1.2),
            (7.135, 1.0),
            (7.155, 0.9),
            (7.170, 0.9),
            (7.180, 0.9),
        ]
        for index, (raw_position_mm, stress_mpa) in enumerate(unload_points):
            load_g = mini_dma_mod.load_g_from_stress_mpa(stress_mpa, window.spin_diameter.value())
            assert load_g is not None
            point = window._capture_measurement_point(
                elapsed_s=index * 0.25,
                position_mm=raw_position_mm,
                effective_position_mm=raw_position_mm,
                raw_load_g=load_g,
                load_g=load_g,
            )
            window._length_setup_points.append(point)

        assert window._maybe_start_setup_unload_baseline_fallback() is True

        assert targets == [pytest.approx(7.078, abs=0.01)]
        assert window._setup_zero_position_mm == pytest.approx(7.078, abs=0.01)
        assert window._setup_zero_fallback_return_position_mm == pytest.approx(7.078, abs=0.01)
        assert window._setup_zero_fallback_reason == "linear_unload_slack"
        assert "slack onset" in window.log_output.toPlainText()

        window._setup_zero_fallback_return_position_mm = None
        assert window._zero_return_requires_true_zero(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
        ) is False
    finally:
        _close_test_window(window)


def test_setup_return_zero_accepts_after_linear_unload_baseline_is_committed(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.2)
    window._automation_step_note = "setup_return_zero"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window._automation_phase = "seek"
    window._setup_zero_position_mm = 7.078
    window._setup_zero_fallback_return_position_mm = None
    window._latest_scale_value_g = 21.17
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 7.078
    window._effective_position_mm = 7.078
    window._move_to_position_mm = pytest.fail  # type: ignore[method-assign]

    try:
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.005,
        ) is True
        assert window._zero_load_scale_reference_g() == pytest.approx(21.17)
    finally:
        _close_test_window(window)


def test_apply_length_setup_uses_committed_slack_onset_baseline(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_positive_motion_is_tension.setChecked(True)
    window._setup_measured_length_mm = 61.613
    window._setup_preload_position_mm = 7.0475
    window._setup_zero_position_mm = 7.1125
    window._current_position_mm = 7.1125
    window._effective_position_mm = 7.1125
    window._fit_setup_unload_zero_position_mm = pytest.fail  # type: ignore[method-assign]

    try:
        assert window._handle_apply_length_setup_step() is True

        assert window._position_reference_mm == pytest.approx(7.1125)
        assert window.spin_initial_length.value() == pytest.approx(61.678)
    finally:
        _close_test_window(window)


def test_apply_length_setup_commits_current_zero_load_reference(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.2)
    window._setup_measured_length_mm = 51.28
    window._setup_preload_position_mm = -0.375
    window._setup_zero_position_mm = -0.18125
    window._current_position_mm = -0.18125
    window._effective_position_mm = -0.18125
    window._latest_scale_value_g = 21.075
    window._latest_scale_timestamp = time.time()
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

    try:
        assert window._handle_apply_length_setup_step() is True

        assert window._zero_load_scale_reference_g() == pytest.approx(21.075)
        assert window._run_zero_load_scale_g == pytest.approx(21.075)
        assert window._effective_load_from_raw_g(21.075) == pytest.approx(0.0)
    finally:
        _close_test_window(window)


def test_restore_cleans_chained_run_suffix_from_saved_log_name(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    settings = _test_settings()
    settings.clear()
    settings.setValue("log_name", "Ni50Fe27Ga23 12_2 test_run04_run02_run02")
    settings.sync()

    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    qtbot.addWidget(window)

    try:
        assert window.edit_log_name.text() == "Ni50Fe27Ga23 12_2 test"
        assert settings.value("log_name") == "Ni50Fe27Ga23 12_2 test"
    finally:
        _close_test_window(window)


def test_starting_length_prompt_updates_stiffness_prior_length(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(12.0)

    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QInputDialog,
        "getDouble",
        lambda *_args, **_kwargs: (42.5, True),
    )

    try:
        assert window._handle_starting_length_prompt_step() is True

        assert window.spin_initial_length.value() == pytest.approx(42.5)
        assert window._setup_starting_length_mm == pytest.approx(42.5)
        assert window._setup_measured_length_mm == pytest.approx(42.5)
        assert window._setup_preload_position_mm == pytest.approx(window._current_position_mm)
        assert "Mounted length accepted" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_single_length_setup_applies_l0_from_starting_reference(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_positive_motion_is_tension.setChecked(False)
    window._current_position_mm = -1.5
    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QInputDialog,
        "getDouble",
        lambda *_args, **_kwargs: (30.5, True),
    )

    try:
        assert window._handle_starting_length_prompt_step() is True
        assert window._handle_mark_setup_return_zero_step() is True
        window._current_position_mm = -1.0
        window._effective_position_mm = -1.0
        window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

        assert window._handle_apply_length_setup_step() is True

        assert window.spin_initial_length.value() == pytest.approx(30.0)
        assert window._setup_zero_position_mm == pytest.approx(-1.0)
        assert "Length reference already captured" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_setup_preload_ramp_skips_when_starting_above_preload(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._current_distribution_value = lambda *_args, **_kwargs: 24.0  # type: ignore[method-assign]

    def _unexpected_seek(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("setup preload should not seek when already above preload")

    window._seek_distribution_target = _unexpected_seek  # type: ignore[method-assign]
    step = mini_dma_mod.AutomationStep(
        "ramp_target",
        target_value=20.0,
        target_end_value=20.0,
        target_ramp_rate_value_s=4.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        note="setup_preload",
    )

    try:
        assert window._handle_target_ramp_step(step, 1) is True
        assert window._setup_preload_ramp_skipped is True
        assert "already above setup preload" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_setup_zero_plateau_fallback_updates_reference_and_accepts_current_position(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    targets: list[float] = []

    def _capture_move(target_mm: float, **_kwargs: object) -> bool:
        targets.append(target_mm)
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.2)
    window.spin_setup_zero_tolerance_g.setValue(0.02)
    window.spin_steps_per_mm.setValue(800.0)
    window._automation_step_note = "setup_return_zero"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window._automation_phase = "seek"
    window._latest_scale_timestamp = time.time()
    window._setup_return_zero_start_point_index = 0
    window._length_setup_start_monotonic = time.monotonic()

    for index, position_mm in enumerate([7.000, 7.012, 7.024, 7.036, 7.048, 7.060]):
        window._latest_scale_value_g = 21.170 + (0.0005 if index % 2 else 0.0)
        window._current_position_mm = position_mm
        window._effective_position_mm = position_mm
        window._record_length_setup_point()
        window._length_setup_points[-1].elapsed_s = index * 0.4

    window._latest_scale_value_g = 21.17
    window._current_position_mm = 7.072
    window._effective_position_mm = 7.072

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.02,
        )

        assert reached is False
        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
        assert window._zero_load_scale_reference_g() == pytest.approx(21.17, abs=0.001)
        assert targets == []
        assert window._setup_zero_fallback_return_position_mm is None
        assert window._setup_zero_position_mm == pytest.approx(7.072)
        assert "zero-load plateau" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_setup_zero_plateau_fallback_accepts_short_stable_zero_travel(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    targets: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: targets.append(target_mm) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.2)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(20.0)
    window._automation_step_note = "setup_return_zero"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window._automation_phase = "seek"
    window._latest_scale_timestamp = time.time()
    window._setup_return_zero_start_point_index = 0
    window._length_setup_start_monotonic = time.monotonic()

    for index, position_mm in enumerate([7.000, 7.012, 7.024, 7.036, 7.048, 7.060]):
        window._latest_scale_value_g = 21.170 + (0.0005 if index % 2 else 0.0)
        window._current_position_mm = position_mm
        window._effective_position_mm = position_mm
        window._record_length_setup_point()
        window._length_setup_points[-1].elapsed_s = index * 0.4

    window._latest_scale_value_g = 21.170
    window._current_position_mm = 7.072
    window._effective_position_mm = 7.072

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.02,
        )

        assert reached is False
        assert window._zero_load_scale_reference_g() == pytest.approx(21.17, abs=0.001)
        assert targets == []
    finally:
        _close_test_window(window)


def test_setup_zero_plateau_fallback_waits_for_stable_plateau_time(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    targets: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: targets.append(target_mm) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.2)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(20.0)
    window._automation_step_note = "setup_return_zero"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window._automation_phase = "seek"
    window._latest_scale_timestamp = time.time()
    window._setup_return_zero_start_point_index = 0
    window._length_setup_start_monotonic = time.monotonic()

    for index, position_mm in enumerate([7.000, 7.012, 7.024, 7.036, 7.048, 7.060]):
        window._latest_scale_value_g = 21.170 + (0.0005 if index % 2 else 0.0)
        window._current_position_mm = position_mm
        window._effective_position_mm = position_mm
        window._record_length_setup_point()
        window._length_setup_points[-1].elapsed_s = index * 0.1

    window._latest_scale_value_g = 21.170
    window._current_position_mm = 7.072
    window._effective_position_mm = 7.072

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.02,
        )

        assert reached is False
        assert window._zero_load_scale_reference_g() == pytest.approx(21.2)
        assert targets != [pytest.approx(7.000)]
        assert "zero-load plateau" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_setup_zero_plateau_fallback_scales_travel_with_wire_length(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    targets: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: targets.append(target_mm) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.2)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(40.0)
    window._automation_step_note = "setup_return_zero"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window._automation_phase = "seek"
    window._latest_scale_timestamp = time.time()
    window._setup_return_zero_start_point_index = 0
    window._length_setup_start_monotonic = time.monotonic()

    for index, position_mm in enumerate([7.000, 7.012, 7.024, 7.036, 7.048, 7.060]):
        window._latest_scale_value_g = 21.170 + (0.0005 if index % 2 else 0.0)
        window._current_position_mm = position_mm
        window._effective_position_mm = position_mm
        window._record_length_setup_point()
        window._length_setup_points[-1].elapsed_s = index * 0.4

    window._latest_scale_value_g = 21.170
    window._current_position_mm = 7.072
    window._effective_position_mm = 7.072

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.02,
        )

        assert reached is False
        assert window._zero_load_scale_reference_g() == pytest.approx(21.17, abs=0.001)
        assert targets == []
        assert "zero-load plateau" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_setup_zero_plateau_fallback_rejects_loaded_baseline_drift(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.2)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.0)
    window._automation_step_note = "setup_return_zero"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window._automation_phase = "seek"
    window._latest_scale_timestamp = time.time()
    window._setup_return_zero_start_point_index = 0
    window._length_setup_start_monotonic = time.monotonic()

    for index, position_mm in enumerate([7.000, 7.020, 7.040, 7.060, 7.080, 7.100]):
        window._latest_scale_value_g = 21.085 + (0.0005 if index % 2 else 0.0)
        window._current_position_mm = position_mm
        window._effective_position_mm = position_mm
        window._record_length_setup_point()
        window._length_setup_points[-1].elapsed_s = index * 0.4

    window._latest_scale_value_g = 21.085
    window._current_position_mm = 7.120
    window._effective_position_mm = 7.120

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.005,
        )

        assert reached is False
        assert window._zero_load_scale_reference_g() == pytest.approx(21.2)
        assert window._setup_zero_position_mm is None
        assert "zero-load plateau" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_target_ramp_continues_through_near_zero_load_after_l0(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append(target_mm)
        window._current_position_mm = target_mm
        effective = kwargs.get("effective_target_mm")
        if effective is not None:
            window._effective_position_mm = float(effective)
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0089)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._automation_steps = [
        mini_dma_mod.AutomationStep(
            "ramp_target",
            target_value=50.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            note="1",
        )
    ]
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        note="1",
    )
    window._position_reference_mm = 0.0
    window._current_position_mm = 0.40
    window._effective_position_mm = 0.40
    window._last_move_target_mm = 0.40
    window._last_effective_move_target_mm = 0.40
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = time.time()

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.25,
        )

        assert reached is False
        assert window._automation_active is True
        assert window._session_stop_reason is None
        assert moves
        log_text = window.log_output.toPlainText().lower()
        assert "mechanical load loss detected" not in log_text
    finally:
        _close_test_window(window)


def test_bench_current_sweep_can_take_up_mechanical_slack_after_l0(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append(target_mm)
        window._current_position_mm = target_mm
        effective = kwargs.get("effective_target_mm")
        if effective is not None:
            window._effective_position_mm = float(effective)
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.set_bench_mechanical_slack_takeup(allow=True, max_seek_mm=10.0)
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0089)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._automation_steps = [
        mini_dma_mod.AutomationStep(
            "ramp_target",
            target_value=50.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            note="1",
        )
    ]
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        note="1",
    )
    window._position_reference_mm = 0.0
    window._current_position_mm = 0.40
    window._effective_position_mm = 0.40
    window._last_move_target_mm = 0.40
    window._last_effective_move_target_mm = 0.40
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = time.time()

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.25,
        )

        assert reached is False
        assert window._automation_active is True
        assert moves
        log_text = window.log_output.toPlainText().lower()
        assert "mechanical load loss detected" not in log_text
    finally:
        _close_test_window(window)


def test_run_zero_load_fallback_does_not_persist_as_default(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_zero_load_scale_g.setValue(21.2)

    try:
        window._set_run_zero_load_scale_reference(21.17, reason="test fallback")

        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
        assert window._zero_load_scale_reference_g() == pytest.approx(21.17)

        window._clear_run_zero_load_scale_reference()

        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
        assert window._zero_load_scale_reference_g() == pytest.approx(21.2)
    finally:
        _close_test_window(window)


def test_setup_zero_plateau_fallback_waits_until_return_position_is_reached(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_step_note = "setup_return_zero"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window._automation_phase = "seek"
    window._setup_zero_fallback_return_position_mm = -1.0
    window.spin_steps_per_mm.setValue(800.0)
    window._latest_scale_value_g = 21.17
    window._latest_scale_timestamp = time.time()
    window.spin_zero_load_scale_g.setValue(21.17)
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

    try:
        window._current_position_mm = -1.2
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.02,
        ) is False

        window._current_position_mm = -1.0
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.02,
        ) is True
        assert window._setup_zero_fallback_return_position_mm is None
    finally:
        _close_test_window(window)


def test_setup_return_zero_accepts_stable_near_zero_without_more_travel(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.2)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_setup_zero_stable_s.setValue(3.0)
    window._automation_step_note = "setup_return_zero"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window._automation_phase = "seek"
    window._setup_return_zero_start_point_index = 0
    window._latest_scale_timestamp = time.time()
    window._length_setup_start_monotonic = time.monotonic()
    window._move_to_position_mm = pytest.fail  # type: ignore[method-assign]

    for index, raw_load_g in enumerate([21.1500, 21.1500, 21.1505, 21.1500, 21.1500]):
        window._latest_scale_value_g = raw_load_g
        window._current_position_mm = -1.9175
        window._effective_position_mm = -1.9175
        window._record_length_setup_point()
        window._length_setup_points[-1].elapsed_s = index * 0.8

    try:
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.005,
        ) is True

        assert window._setup_zero_position_mm == pytest.approx(-1.9175)
        assert window._zero_load_scale_reference_g() == pytest.approx(21.15025, abs=0.001)
        assert "Accepted stable near-zero load plateau" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_pending_linear_unload_fallback_accepts_stable_near_zero_plateau(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.2)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_setup_zero_stable_s.setValue(1.0)
    window._active_control_config = window._freeze_control_config()
    window._automation_step_note = "setup_return_zero"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window._automation_phase = "seek"
    window._setup_return_zero_start_point_index = 0
    window._setup_zero_position_mm = -1.925
    window._setup_zero_fallback_return_position_mm = -1.925
    window._setup_zero_fallback_reason = "linear_unload_slack"
    window._latest_scale_timestamp = time.time()
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
    window._move_to_position_mm = pytest.fail  # type: ignore[method-assign]

    for index, raw_load_g in enumerate([21.1300, 21.1305, 21.1298, 21.1302, 21.1301]):
        window._latest_scale_value_g = raw_load_g
        window._current_position_mm = -1.94875
        window._effective_position_mm = -1.94875
        window._record_length_setup_point()
        window._length_setup_points[-1].elapsed_s = index * 0.4

    try:
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.005,
        ) is True

        assert window._setup_zero_fallback_return_position_mm is None
        assert window._setup_zero_position_mm == pytest.approx(-1.94875)
        assert window._zero_load_scale_reference_g() == pytest.approx(21.1301, abs=0.001)
        assert "stable near-zero load plateau" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_setup_return_zero_ignores_legacy_zero_stable_setting(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_setup_zero_stable_s.setValue(30.0)
    window._active_control_config = window._freeze_control_config()

    try:
        assert window._setup_zero_stable_duration_s() == pytest.approx(
            mini_dma_mod.SETUP_ZERO_FALLBACK_MIN_TIME_S
        )
    finally:
        _close_test_window(window)


def test_current_sweep_final_zero_plateau_fallback_accepts_near_zero_load(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    targets: list[float] = []

    def _capture_move(target_mm: float, **_kwargs: object) -> bool:
        targets.append(target_mm)
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.2)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_diameter.setValue(0.0191)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._automation_phase = "target_ramp"
    window._automation_step_note = "5"
    window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
    window._session_active = True
    window._session_logging_enabled = False
    window._end_zero_fallback_armed = True
    window._end_zero_fallback_start_point_index = 0
    window._session_start_monotonic = time.monotonic()

    for index, position_mm in enumerate([7.000, 7.012, 7.024, 7.036, 7.048, 7.060]):
        raw_g = 21.170 + (0.0005 if index % 2 else 0.0)
        window._latest_scale_value_g = raw_g
        window._latest_scale_timestamp = time.time()
        window._current_position_mm = position_mm
        window._effective_position_mm = position_mm
        point = window._capture_measurement_point(
            elapsed_s=float(index),
            position_mm=position_mm,
            effective_position_mm=position_mm,
            raw_load_g=raw_g,
            load_g=window._current_effective_load_g(),
        )
        window._session_points.append(point)

    window._latest_scale_value_g = 21.17
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 7.072
    window._effective_position_mm = 7.072

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=0.0,
            tolerance=0.25,
        )

        assert reached is False
        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
        assert window._zero_load_scale_reference_g() == pytest.approx(21.17, abs=0.001)
        assert targets == []
        assert window._end_zero_fallback_return_position_mm is None
        assert window._end_zero_fallback_armed is False
        assert "zero-load plateau" in window.log_output.toPlainText()
    finally:
        window._session_active = False
        _close_test_window(window)


def test_recovery_load_zero_plateau_fallback_accepts_near_zero_load(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    targets: list[float] = []

    def _capture_move(target_mm: float, **_kwargs: object) -> bool:
        targets.append(target_mm)
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.2)
    window.spin_steps_per_mm.setValue(800.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.RECOVERY_LOAD
    window._automation_phase = "recover"
    window._automation_step_note = "0"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window._end_zero_fallback_armed = True
    window._end_zero_fallback_start_point_index = 0
    window._recovery_start_monotonic = time.monotonic()
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

    for index, position_mm in enumerate([7.000, 7.012, 7.024, 7.036, 7.048, 7.060]):
        window._latest_scale_value_g = 21.170 + (0.0005 if index % 2 else 0.0)
        window._latest_scale_timestamp = time.time()
        window._current_position_mm = position_mm
        window._effective_position_mm = position_mm
        window._record_recovery_point()
        window._recovery_points[-1].elapsed_s = index * 0.4

    window._latest_scale_value_g = 21.17
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 7.072
    window._effective_position_mm = 7.072

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.02,
        )

        assert reached is False
        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
        assert window._zero_load_scale_reference_g() == pytest.approx(21.17, abs=0.001)
        assert targets == []
        assert window._end_zero_fallback_return_position_mm is None
        assert window._end_zero_fallback_armed is False
    finally:
        _close_test_window(window)


def test_recovery_plot_updates_pyqtgraph_curves(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._show_recovery_plot_dialog("Recovery test")
        window._recovery_points = [
            window._capture_measurement_point(
                elapsed_s=0.0,
                position_mm=0.0,
                effective_position_mm=0.0,
                raw_load_g=1.0,
                load_g=1.0,
            ),
            window._capture_measurement_point(
                elapsed_s=1.0,
                position_mm=-0.1,
                effective_position_mm=-0.1,
                raw_load_g=0.1,
                load_g=0.1,
            ),
        ]
        window._refresh_recovery_plot()

        assert window._recovery_plot_widget is not None
        assert isinstance(window._recovery_plot_widget, mini_dma_mod.pg.PlotWidget)
        assert window._recovery_left_curve is not None
        assert window._recovery_right_curve is not None
        left_x, left_y = window._recovery_left_curve.getData()
        right_x, right_y = window._recovery_right_curve.getData()
        assert list(left_x) == pytest.approx([0.0, 1.0])
        assert list(left_y) == pytest.approx([1.0, 0.1])
        assert list(right_x) == pytest.approx([0.0, 1.0])
        assert list(right_y) == pytest.approx([point.position_mm for point in window._recovery_points])
        assert window._recovery_left_curve.opts["pen"].color().name().lower() == "#fbbf24"
        assert window._recovery_right_curve.opts["pen"].color().name().lower() == "#60a5fa"
    finally:
        _close_test_window(window)


def test_dashboard_plot_updates_pyqtgraph_left_and_right_curves(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._session_points = [
        window._capture_measurement_point(
            elapsed_s=0.0,
            position_mm=0.0,
            effective_position_mm=0.0,
            raw_load_g=21.2,
            load_g=0.0,
        ),
        window._capture_measurement_point(
            elapsed_s=1.0,
            position_mm=0.2,
            effective_position_mm=0.2,
            raw_load_g=22.0,
            load_g=0.8,
        ),
    ]

    try:
        window._plot_tiles[0].y_right_combo.setCurrentIndex(
            window._plot_tiles[0].y_right_combo.findData("position_mm")
        )
        window._refresh_plots()

        assert window._dashboard_plot_widgets
        assert isinstance(window._dashboard_plot_widgets[0], mini_dma_mod.pg.PlotWidget)
        assert window._dashboard_left_curves[0] is not None
        assert window._dashboard_right_curves[0] is not None
        assert window._dashboard_plot_bundles[0].right_view is not None
        x_values, y_values = window._dashboard_left_curves[0].getData()
        right_x_values, right_y_values = window._dashboard_right_curves[0].getData()
        assert list(x_values) == pytest.approx([0.0, 1.0])
        assert list(y_values) == pytest.approx([0.0, 0.8])
        assert list(right_x_values) == pytest.approx([0.0, 1.0])
        assert list(right_y_values) == pytest.approx(
            [point.position_mm for point in window._session_points]
        )
    finally:
        _close_test_window(window)


def test_parse_mlx90614_probe_line_returns_temperature_sample() -> None:
    sample = mini_dma_mod._parse_mlx90614_probe_line(
        "MLX90614,42,1234,2370,23.01,40.25,14808,15670,2",
        timestamp_s=100.5,
    )

    assert sample is not None
    assert sample.sequence == 42
    assert sample.device_elapsed_ms == 1234
    assert sample.read_us == 2370
    assert sample.ambient_c == pytest.approx(23.01)
    assert sample.object_c_apparent == pytest.approx(40.25)
    assert sample.raw_ambient == 14808
    assert sample.raw_object == 15670
    assert sample.flags == 2


def test_parse_mlx90614_probe_line_rejects_failed_zero_kelvin_read() -> None:
    assert (
        mini_dma_mod._parse_mlx90614_probe_line(
            "MLX90614,43,1240,800,-273.15,-273.15,0,0,3",
            timestamp_s=100.6,
        )
        is None
    )


def test_parse_mlx90640_text_frame_returns_max_temperature_sample() -> None:
    lines = ["FRAME_BEGIN,1234,22.50"]
    for row in range(24):
        values = [20.0 + row * 0.1 + col * 0.01 for col in range(32)]
        lines.append("ROW," + str(row) + "," + ",".join(f"{value:.2f}" for value in values))
    lines.append("FRAME_END")
    lines[6] = lines[6].replace("20.81", "45.50", 1)

    sample = mini_dma_mod._parse_mlx90640_text_frame(lines, timestamp_s=100.5)

    assert sample is not None
    assert sample.sensor_type == "mlx90640"
    assert sample.device_elapsed_ms == 1234
    assert sample.ambient_c == pytest.approx(22.5)
    assert sample.object_c_apparent == pytest.approx(45.5)
    assert sample.frame_max_c == pytest.approx(45.5)
    assert sample.frame_min_c == pytest.approx(20.0)
    assert sample.frame_width == 32
    assert sample.frame_height == 24
    assert sample.frame_hotspot_row == 5
    assert sample.frame_hotspot_col == 31


def test_mini_dma_plot_channels_include_ir_temperature(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        visible_keys = {channel.key for channel in window._plot_config_channels()}
        all_keys = {channel.key for channel in window._plot_channels()}

        assert "temperature_c" in visible_keys
        assert "ir_object_c_apparent" not in visible_keys
        assert "ir_delta_c" not in visible_keys
        assert "ir_ambient_c" not in visible_keys
        assert "ir_sample_age_s" not in visible_keys
        assert {"ir_object_c_apparent", "ir_delta_c", "ir_ambient_c", "ir_sample_age_s"} <= all_keys
    finally:
        _close_test_window(window)


def test_legacy_ir_plot_settings_map_to_temperature_channel(tmp_path: Path, qtbot) -> None:
    settings = _test_settings()
    settings.clear()
    settings.setValue("plot_tile_0_x", "elapsed_s")
    settings.setValue("plot_tile_0_y_left", "ir_object_c_apparent")
    settings.setValue("plot_tile_0_y_right", "ir_delta_c")
    settings.sync()
    window = _build_window(tmp_path, qtbot, preserve_settings=True)

    try:
        values = window._read_dashboard_plot_tile_settings(0, None)

        assert values["y_left"] == "temperature_c"
        assert values["y_right"] == "temperature_c"
    finally:
        _close_test_window(window)


def test_dashboard_temperature_channel_uses_coalesced_ir_value(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    base_time = 1000.0
    samples = [
        mini_dma_mod.IrTemperatureSample(
            timestamp_s=base_time + offset_s,
            raw_text=f"sample {index}",
            sequence=index,
            device_elapsed_ms=index,
            read_us=1000,
            ambient_c=23.0,
            object_c_apparent=value_c,
            raw_ambient=0,
            raw_object=0,
            flags=0,
            sensor_type=mini_dma_mod.IR_SENSOR_MLX90640,
            frame_max_c=value_c,
        )
        for index, (offset_s, value_c) in enumerate(
            [(-0.30, 20.0), (-0.20, 30.0), (-0.10, 40.0), (0.0, 50.0)]
        )
    ]

    try:
        with window._ir_state_lock:
            for sample in samples:
                window._latest_ir_sample = sample
                window._ir_temperature_buffer.add_sample(sample)

        snapshot = window._latest_ir_snapshot(now_s=base_time)
        point = mini_dma_mod.MeasurementPoint(
            elapsed_s=0.0,
            timestamp_utc="2026-06-12 12:00:00",
            raw_position_mm=0.0,
            position_mm=0.0,
            raw_load_g=0.0,
            load_g=0.0,
            preload_state="disabled",
            strain_pct=0.0,
            stress_mpa=0.0,
            current_set_mA=1.0,
            current_measured_mA=1.0,
            voltage_V=0.1,
            resistance_ohm=100.0,
            power_W=0.0001,
            automation_phase="manual",
            automation_basis=None,
            automation_target_value=None,
            plateau_index=None,
            plateau_label=None,
            ir_object_c_apparent=snapshot["object_c_apparent"],  # type: ignore[arg-type]
            ir_temperature_c=snapshot["dashboard_temperature_c"],  # type: ignore[arg-type]
        )
        channel = window._plot_channel("temperature_c")

        assert snapshot["object_c_apparent"] == pytest.approx(50.0)
        assert snapshot["dashboard_temperature_c"] == pytest.approx(40.0)
        assert channel is not None
        assert channel.getter(point) == pytest.approx(40.0)
    finally:
        _close_test_window(window)


def test_signal_buffers_snapshot_while_samples_are_appended() -> None:
    scale_buffer = mini_dma_mod.ScaleSignalBuffer()
    ir_buffer = mini_dma_mod.IrTemperatureBuffer(maxlen=4000)
    stop = threading.Event()
    errors: list[BaseException] = []

    def _writer() -> None:
        index = 0
        try:
            while not stop.is_set():
                timestamp_s = 1000.0 + index * 0.001
                scale_buffer.add_sample(
                    timestamp_s=timestamp_s,
                    raw_g=float(index),
                    applied_load_g=float(index),
                    raw_text=str(index),
                )
                ir_buffer.add_sample(
                    mini_dma_mod.IrTemperatureSample(
                        timestamp_s=timestamp_s,
                        raw_text=str(index),
                        sequence=index,
                        device_elapsed_ms=index,
                        read_us=1000,
                        ambient_c=23.0,
                        object_c_apparent=30.0 + (index % 5),
                        raw_ambient=0,
                        raw_object=0,
                        flags=0,
                    )
                )
                index += 1
        except BaseException as exc:  # pragma: no cover - reported by assertion below
            errors.append(exc)

    writer = threading.Thread(target=_writer, daemon=True)
    writer.start()
    try:
        for _index in range(500):
            scale_buffer.recent_summary(window_s=0.5)
            scale_buffer.recent_samples(window_s=0.5)
            ir_buffer.sample_rate_hz(window_s=0.5)
            ir_buffer.recent_object_mean_c(window_s=0.25)
    finally:
        stop.set()
        writer.join(timeout=2.0)

    assert errors == []


def test_session_logs_ir_temperature_sidecar_and_measurement_columns(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window.edit_log_name.setText("ir_temperature_session")
        window._start_session(enable_logging=True, record_initial_point=False)
        assert window._session_active is True
        sample = mini_dma_mod.IrTemperatureSample(
            timestamp_s=window._session_start_wall_s + 0.25,
            raw_text="MLX90614,7,900,2370,23.10,41.50,14813,15733,2",
            sequence=7,
            device_elapsed_ms=900,
            read_us=2370,
            ambient_c=23.10,
            object_c_apparent=41.50,
            raw_ambient=14813,
            raw_object=15733,
            flags=2,
            config1="0x9795",
        )

        window._handle_ir_sample(sample)
        assert window._record_current_point(quiet=True) is True
        window._stop_session(reason="test_complete", detail="IR logging test complete.")

        run_dir = tmp_path / "ir_temperature_session"
        measurement_rows = list(csv.DictReader((run_dir / "measurement.csv").open(encoding="utf-8", newline="")))
        ir_rows = list(csv.DictReader((run_dir / "ir_temperature.csv").open(encoding="utf-8", newline="")))
        metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))

        assert measurement_rows
        assert measurement_rows[-1]["ir_object_c_apparent"] == "41.500000"
        assert measurement_rows[-1]["ir_ambient_c"] == "23.100000"
        assert measurement_rows[-1]["ir_delta_c"] == "0.000000"
        assert measurement_rows[-1]["ir_raw_object"] == "15733"
        assert measurement_rows[-1]["ir_config1"] == "0x9795"
        assert ir_rows == [
            {
                "elapsed_s": "0.250000",
                "timestamp_utc": ir_rows[0]["timestamp_utc"],
                "sensor_type": "mlx90614",
                "host_time_s": f"{sample.timestamp_s:.6f}",
                "device_elapsed_ms": "900",
                "sequence": "7",
                "ambient_c": "23.100000",
                "object_c_apparent": "41.500000",
                "delta_c": "0.000000",
                "frame_min_c": "",
                "frame_mean_c": "",
                "frame_max_c": "",
                "frame_center_c": "",
                "frame_hotspot_row": "",
                "frame_hotspot_col": "",
                "frame_width": "",
                "frame_height": "",
                "raw_ambient": "14813",
                "raw_object": "15733",
                "read_us": "2370",
                "flags": "2",
                "sample_rate_hz": "",
                "config1": "0x9795",
            }
        ]
        assert metadata["logging"]["ir_temperature_sidecar"] == "ir_temperature.csv"
        assert metadata["logging"]["ir_temperature_sample_count"] == 1
        assert metadata["ir_thermometer"]["baseline_object_c_apparent"] == pytest.approx(41.5)
    finally:
        _close_test_window(window)


def test_session_logs_mlx90640_frame_summary_as_ir_temperature(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window.edit_log_name.setText("mlx90640_session")
        window._start_session(enable_logging=True, record_initial_point=False)
        assert window._session_active is True
        sample = mini_dma_mod.IrTemperatureSample(
            timestamp_s=window._session_start_wall_s + 0.25,
            raw_text="FRAME_BEGIN,1000,24.00\n...",
            sequence=0,
            device_elapsed_ms=1000,
            read_us=0,
            ambient_c=24.0,
            object_c_apparent=47.25,
            raw_ambient=0,
            raw_object=0,
            flags=0,
            sensor_type="mlx90640",
            frame_min_c=22.0,
            frame_mean_c=24.5,
            frame_max_c=47.25,
            frame_center_c=25.0,
            frame_hotspot_row=11,
            frame_hotspot_col=16,
            frame_width=32,
            frame_height=24,
        )

        window._handle_ir_sample(sample)
        assert window._record_current_point(quiet=True) is True
        window._stop_session(reason="test_complete", detail="IR logging test complete.")

        run_dir = tmp_path / "mlx90640_session"
        measurement_rows = list(csv.DictReader((run_dir / "measurement.csv").open(encoding="utf-8", newline="")))
        ir_rows = list(csv.DictReader((run_dir / "ir_temperature.csv").open(encoding="utf-8", newline="")))

        assert measurement_rows[-1]["ir_object_c_apparent"] == "47.250000"
        assert measurement_rows[-1]["ir_ambient_c"] == "24.000000"
        assert ir_rows[-1]["sensor_type"] == "mlx90640"
        assert ir_rows[-1]["object_c_apparent"] == "47.250000"
        assert ir_rows[-1]["frame_min_c"] == "22.000000"
        assert ir_rows[-1]["frame_mean_c"] == "24.500000"
        assert ir_rows[-1]["frame_max_c"] == "47.250000"
        assert ir_rows[-1]["frame_center_c"] == "25.000000"
        assert ir_rows[-1]["frame_hotspot_row"] == "11"
        assert ir_rows[-1]["frame_hotspot_col"] == "16"
        assert ir_rows[-1]["frame_width"] == "32"
        assert ir_rows[-1]["frame_height"] == "24"
    finally:
        _close_test_window(window)


def test_paused_recipe_does_not_append_session_temperature_telemetry_or_plot_samples(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window.edit_log_name.setText("paused_recipe_logging")
        window._latest_scale_value_g = 21.2
        window._latest_scale_text = "21.200 g"
        window._latest_scale_timestamp = time.time()
        window._start_session(enable_logging=True, record_initial_point=False)
        window._automation_active = True
        window._automation_paused = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        sample = mini_dma_mod.IrTemperatureSample(
            timestamp_s=window._session_start_wall_s + 0.25,
            raw_text="MLX90614,7,900,2370,23.10,41.50,14813,15733,2",
            sequence=7,
            device_elapsed_ms=900,
            read_us=2370,
            ambient_c=23.10,
            object_c_apparent=41.50,
            raw_ambient=14813,
            raw_object=15733,
            flags=2,
            config1="0x9795",
        )

        assert window._record_current_point(quiet=True) is True
        window._handle_ir_sample(sample)
        window._write_ui_telemetry_sample(
            started_s=window._session_start_monotonic + 0.2,
            finished_s=window._session_start_monotonic + 0.212,
            previous_ui_s=window._session_start_monotonic,
            scale_sample_changed=True,
            dialog_sample_recorded=False,
            live_plot_sample_recorded=False,
            dashboard_plot_refreshed=False,
        )
        recorded, refreshed = window._record_live_plot_sample_from_ui_refresh()

        assert recorded is False
        assert refreshed is False
        assert window._session_points == []
        assert window._session_ir_temperature_count == 0
        assert window._session_ui_telemetry_count == 0
        assert window._live_plot_points == []
    finally:
        window._automation_paused = False
        window._automation_active = False
        _close_test_window(window)


def test_dashboard_plot_uses_secondary_axis_without_duplicate_equivalent_curve(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_diameter.setValue(0.02)
    window.spin_initial_length.setValue(40.0)
    window._session_points = [
        window._capture_measurement_point(
            elapsed_s=0.0,
            position_mm=0.0,
            effective_position_mm=0.0,
            raw_load_g=21.2,
            load_g=0.0,
        ),
        window._capture_measurement_point(
            elapsed_s=1.0,
            position_mm=0.2,
            effective_position_mm=0.2,
            raw_load_g=22.0,
            load_g=0.8,
        ),
    ]

    try:
        window._plot_tiles[0].y_right_combo.setCurrentIndex(
            window._plot_tiles[0].y_right_combo.findData("stress_mpa")
        )
        window._refresh_plots()

        bundle = window._dashboard_plot_bundles[0]
        x_values, y_values = bundle.left_curve.getData()
        right_x_values, right_y_values = bundle.right_curve.getData()
        assert list(x_values) == pytest.approx([0.0, 1.0])
        assert list(y_values) == pytest.approx([0.0, 0.8])
        assert right_x_values is None or len(right_x_values) == 0
        assert right_y_values is None or len(right_y_values) == 0
        assert bundle.plot_item.getAxis("right").labelText == "Stress (MPa)"
        assert bundle.right_view is not None
        right_range = bundle.right_view.viewRange()[1]
        expected_stress = mini_dma_mod.stress_mpa_from_load_g(0.8, window.spin_diameter.value())
        assert expected_stress is not None
        assert right_range[0] < 0.0
        assert right_range[1] > expected_stress
    finally:
        _close_test_window(window)


def test_dashboard_plot_panel_keeps_axis_padding_and_compact_log(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        margins = window._dashboard_plot_canvas_layout.contentsMargins()
        assert margins.left() >= 10
        assert margins.top() >= 10
        assert margins.right() >= 10
        assert margins.bottom() >= 14
        assert window._dashboard_plot_grid.horizontalSpacing() >= 18
        assert window._dashboard_plot_grid.verticalSpacing() >= 18
        assert window.log_output.maximumHeight() <= 96
        assert window._dashboard_plot_splitter.childrenCollapsible() is False
        assert window._dashboard_plot_widgets
        assert all(widget.minimumWidth() >= 320 for widget in window._dashboard_plot_widgets)
        assert all(widget.minimumHeight() >= 230 for widget in window._dashboard_plot_widgets)
        assert all(widget.maximumHeight() > 10000 for widget in window._dashboard_plot_widgets)
    finally:
        _close_test_window(window)


def test_dashboard_plot_viewboxes_keep_data_edge_padding(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._plot_tiles[0].y_right_combo.setCurrentIndex(
            window._plot_tiles[0].y_right_combo.findData("position_mm")
        )
        window._refresh_plots()

        bundle = window._dashboard_plot_bundles[0]

        assert bundle.plot_item.vb.state["defaultPadding"] >= 0.04
        assert bundle.right_view is not None
        assert bundle.right_view.state["defaultPadding"] >= 0.04
    finally:
        _close_test_window(window)


def test_dashboard_pyqtgraph_axes_match_curve_colors_and_use_major_grid_only(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._plot_tiles[0].y_right_combo.setCurrentIndex(
            window._plot_tiles[0].y_right_combo.findData("position_mm")
        )
        window._refresh_plots()

        bundle = window._dashboard_plot_bundles[0]
        left_axis = bundle.plot_item.getAxis("left")
        right_axis = bundle.plot_item.getAxis("right")

        assert left_axis.textPen().color().name().lower() == "#fbbf24"
        assert left_axis.pen().color().name().lower() == "#fbbf24"
        assert right_axis.textPen().color().name().lower() == "#60a5fa"
        assert right_axis.pen().color().name().lower() == "#60a5fa"
        assert left_axis.pen().color().name().lower() != right_axis.pen().color().name().lower()
        assert left_axis.style["maxTickLevel"] == 0
        assert right_axis.style["maxTickLevel"] == 0
        assert left_axis.grid is False
        assert right_axis.grid is False
        assert left_axis.autoSIPrefix is False
        assert right_axis.autoSIPrefix is False
        assert left_axis.autoSIPrefixScale == 1.0
        assert right_axis.autoSIPrefixScale == 1.0
        assert left_axis.labelUnitPrefix == ""
        assert right_axis.labelUnitPrefix == ""
        assert bundle.left_curve.opts["symbol"] == "o"
        assert bundle.right_curve.opts["symbol"] == "s"
        assert bundle.left_curve.opts["pen"].widthF() <= 0.9
        assert bundle.right_curve.opts["pen"].widthF() <= 0.9
    finally:
        _close_test_window(window)


def test_dashboard_pyqtgraph_empty_top_and_right_axes_are_frame_lines(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._plot_tiles[0].y_right_combo.setCurrentIndex(
            window._plot_tiles[0].y_right_combo.findData("")
        )
        window._refresh_plots()

        bundle = window._dashboard_plot_bundles[0]
        top_axis = bundle.plot_item.getAxis("top")
        right_axis = bundle.plot_item.getAxis("right")

        assert top_axis.isVisible()
        assert right_axis.isVisible()
        assert top_axis.style["showValues"] is False
        assert right_axis.style["showValues"] is False
        assert top_axis.style["tickLength"] == 0
        assert right_axis.style["tickLength"] == 0
        assert top_axis.labelText == ""
        assert right_axis.labelText == ""
        assert right_axis.pen().color().name().lower() == right_axis.textPen().color().name().lower()
        assert right_axis.pen().color().name().lower() != "#f59e0b"
    finally:
        _close_test_window(window)


def test_apply_length_setup_uses_plateau_zero_position_after_return_fallback(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_positive_motion_is_tension.setChecked(False)
    window._setup_measured_length_mm = 30.5
    window._setup_preload_position_mm = -1.5
    window._setup_zero_position_mm = -1.0
    window._current_position_mm = -10.0
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

    try:
        assert window._handle_apply_length_setup_step() is True

        assert window.spin_initial_length.value() == pytest.approx(30.0)
        assert window._position_reference_mm == pytest.approx(-1.0)
    finally:
        _close_test_window(window)


def _wheel_event(delta_y: int = -120) -> QtGui.QWheelEvent:
    return QtGui.QWheelEvent(
        QtCore.QPointF(10.0, 10.0),
        QtCore.QPointF(10.0, 10.0),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, delta_y),
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def _set_copper_current_sweep_defaults(window: mini_dma_mod.MainWindow) -> None:
    window.spin_current_sweep_target_start.setValue(0.0)
    window.spin_current_sweep_target_end.setValue(9.0)
    window.spin_current_sweep_target_step.setValue(3.0)
    window.check_current_sweep_return_target.setChecked(True)
    window.spin_current_sweep_start_mA.setValue(1.0)
    window.spin_current_sweep_end_mA.setValue(3.0)
    window.spin_current_sweep_step_mA.setValue(1.0)
    window.spin_current_sweep_target_ramp_rate.setValue(0.1)
    window.spin_current_sweep_target_speed_mm_s.setValue(1.0)
    window.check_current_sweep_reverse_current.setChecked(True)
    window.spin_control_interval.setValue(250)
    window.spin_log_interval.setValue(500)


def test_scale_signal_buffer_summarizes_interval() -> None:
    buffer = mini_dma_mod.ScaleSignalBuffer(window_s=5.0)
    buffer.add_sample(timestamp_s=100.0, raw_g=21.2, applied_load_g=0.0, raw_text="21.200 g")
    buffer.add_sample(timestamp_s=100.1, raw_g=21.0, applied_load_g=0.2, raw_text="21.000 g")
    buffer.add_sample(timestamp_s=100.2, raw_g=20.9, applied_load_g=0.3, raw_text="20.900 g")

    summary = buffer.interval_summary(since_s=100.0, until_s=100.2)

    assert summary.raw_last_g == pytest.approx(20.9)
    assert summary.applied_last_g == pytest.approx(0.3)
    assert summary.load_mean_g == pytest.approx((0.0 + 0.2 + 0.3) / 3.0)
    assert summary.load_min_g == pytest.approx(0.0)
    assert summary.load_max_g == pytest.approx(0.3)
    assert summary.sample_count == 3
    assert summary.sample_rate_hz == pytest.approx(10.0)


def test_scale_signal_buffer_trims_old_samples() -> None:
    buffer = mini_dma_mod.ScaleSignalBuffer(window_s=1.0)
    buffer.add_sample(timestamp_s=10.0, raw_g=1.0, applied_load_g=1.0, raw_text="1")
    buffer.add_sample(timestamp_s=11.5, raw_g=2.0, applied_load_g=2.0, raw_text="2")

    summary = buffer.interval_summary(since_s=None, until_s=None)

    assert summary.sample_count == 1
    assert summary.raw_last_g == pytest.approx(2.0)


def test_scale_measurement_updates_freshness_off_ui_thread(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    timestamp_s = time.time()

    try:
        thread = threading.Thread(
            target=window._handle_scale_measurement,
            args=(12.5, "12.5 g", timestamp_s),
        )
        thread.start()
        thread.join(timeout=1.0)

        assert not thread.is_alive()
        assert window._has_fresh_scale_reading()
        assert window._latest_scale_timestamp == pytest.approx(timestamp_s)
        assert window._scale_signal_buffer.latest() is not None
    finally:
        _close_test_window(window)


def _calibration_point(
    *,
    position_mm: float,
    load_g: float,
    phase: str,
    strain_pct: float | None = None,
    stress_mpa: float | None = None,
) -> mini_dma_mod.MeasurementPoint:
    return mini_dma_mod.MeasurementPoint(
        elapsed_s=0.0,
        timestamp_utc="2026-04-28 00:00:00",
        raw_position_mm=position_mm,
        position_mm=position_mm,
        raw_load_g=load_g,
        load_g=load_g,
        preload_state=mini_dma_mod.PRELOAD_DISABLED,
        strain_pct=strain_pct,
        stress_mpa=stress_mpa,
        current_set_mA=None,
        current_measured_mA=None,
        voltage_V=None,
        resistance_ohm=None,
        power_W=None,
        automation_phase=phase,
        automation_basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        automation_target_value=None,
        plateau_index=None,
        plateau_label=None,
    )


def test_calibration_report_estimates_stiffness_and_backlash() -> None:
    points = [
        _calibration_point(position_mm=0.0, load_g=0.000, phase="calibration_baseline"),
        _calibration_point(position_mm=0.0, load_g=0.005, phase="calibration_baseline"),
        _calibration_point(position_mm=0.0, load_g=0.010, phase="calibration_baseline"),
        _calibration_point(
            position_mm=0.00,
            load_g=1.00,
            strain_pct=0.00,
            stress_mpa=100.0,
            phase="calibration_forward",
        ),
        _calibration_point(
            position_mm=0.01,
            load_g=1.05,
            strain_pct=0.10,
            stress_mpa=105.0,
            phase="calibration_forward",
        ),
        _calibration_point(
            position_mm=0.02,
            load_g=1.10,
            strain_pct=0.20,
            stress_mpa=110.0,
            phase="calibration_forward",
        ),
        _calibration_point(
            position_mm=0.03,
            load_g=1.15,
            strain_pct=0.30,
            stress_mpa=115.0,
            phase="calibration_forward",
        ),
        _calibration_point(
            position_mm=0.02,
            load_g=1.15,
            strain_pct=0.30,
            stress_mpa=115.0,
            phase="calibration_reverse",
        ),
        _calibration_point(
            position_mm=0.01,
            load_g=1.10,
            strain_pct=0.20,
            stress_mpa=110.0,
            phase="calibration_reverse",
        ),
        _calibration_point(
            position_mm=0.00,
            load_g=1.05,
            strain_pct=0.10,
            stress_mpa=105.0,
            phase="calibration_reverse",
        ),
        _calibration_point(
            position_mm=-0.01,
            load_g=1.00,
            strain_pct=0.00,
            stress_mpa=100.0,
            phase="calibration_reverse",
        ),
    ]

    report = mini_dma_mod.calibration_report_from_points(points)

    assert report["baseline"]["load_std_g"] == pytest.approx(0.005)
    assert report["forward"]["stiffness_g_per_mm"] == pytest.approx(5.0)
    assert report["reverse"]["stiffness_g_per_mm"] == pytest.approx(5.0)
    assert report["average_stiffness_g_per_mm"] == pytest.approx(5.0)
    assert report["backlash_mm"] == pytest.approx(0.01)
    assert report["stress_strain"]["forward"]["modulus_mpa"] == pytest.approx(5000.0)
    assert report["stress_strain"]["reverse"]["modulus_mpa"] == pytest.approx(5000.0)
    assert report["stress_strain"]["average_modulus_mpa"] == pytest.approx(5000.0)
    assert report["stress_strain"]["average_modulus_gpa"] == pytest.approx(5.0)
    assert report["sample_counts"] == {
        "baseline": 3,
        "forward": 4,
        "reverse": 4,
    }


def test_calibration_backlash_is_quantized_to_tic_units(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)

    try:
        assert window._quantize_backlash_mm(0.0196) == pytest.approx(0.02)
        assert window._quantize_backlash_mm(0.0002) == pytest.approx(0.0)
    finally:
        _close_test_window(window)


def test_stop_session_finalizes_partial_calibration_report(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("partial_calibration")
    window.check_zero_position_on_start.setChecked(False)
    window.check_tare_on_start.setChecked(False)

    try:
        window._start_session(record_initial_point=False)
        window._automation_name = mini_dma_mod.CALIBRATION
        window._session_points = [
            _calibration_point(
                position_mm=0.0,
                load_g=0.25,
                phase=mini_dma_mod.CALIBRATION_FORWARD,
            )
        ]
        json_path = window._session_json_path
        assert json_path is not None

        window._stop_session()

        metadata = json.loads(json_path.read_text(encoding="utf-8"))
        assert metadata["session_state"] == "finished"
        assert metadata["calibration"]["report"]["status"] == "insufficient_data"
        assert metadata["calibration"]["report"]["sample_counts"]["forward"] == 1
    finally:
        _close_test_window(window)


def test_session_holds_sleep_guard_until_stopped(tmp_path: Path, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _build_window(tmp_path, qtbot)
    calls: list[str] = []

    class _FakeSleepGuard:
        def acquire(self) -> None:
            calls.append("acquire")

        def release(self) -> None:
            calls.append("release")

    monkeypatch.setattr(mini_dma_mod, "create_experiment_sleep_guard", lambda _reason: _FakeSleepGuard())

    try:
        window._start_session(record_initial_point=False)
        assert calls == ["acquire"]

        window._stop_session()
        assert calls == ["acquire", "release"]
    finally:
        _close_test_window(window)


def test_nonpersistent_calibration_does_not_overwrite_saved_servo_settings(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.setValue("backlash_mm", 0.456)
    settings.setValue("calibration_stiffness_g_per_mm", 123.0)
    settings.setValue("calibration_stiffness_length_mm", 45.0)
    settings.setValue("calibration_load_noise_g", 0.078)
    settings.sync()

    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)
    window.spin_backlash_mm.setValue(0.0)
    window.spin_initial_length.setValue(20.0)
    window._session_points = [
        _calibration_point(position_mm=0.0, load_g=0.0, phase=mini_dma_mod.CALIBRATION_BASELINE),
        _calibration_point(position_mm=0.01, load_g=0.1, phase=mini_dma_mod.CALIBRATION_FORWARD),
        _calibration_point(position_mm=0.02, load_g=0.2, phase=mini_dma_mod.CALIBRATION_FORWARD),
        _calibration_point(position_mm=0.00, load_g=0.1, phase=mini_dma_mod.CALIBRATION_REVERSE),
        _calibration_point(position_mm=0.01, load_g=0.2, phase=mini_dma_mod.CALIBRATION_REVERSE),
    ]

    try:
        window._finalize_calibration_report()

        settings.sync()
        assert float(settings.value("backlash_mm")) == pytest.approx(0.456)
        assert float(settings.value("calibration_stiffness_g_per_mm")) == pytest.approx(123.0)
        assert float(settings.value("calibration_stiffness_length_mm")) == pytest.approx(45.0)
        assert float(settings.value("calibration_load_noise_g")) == pytest.approx(0.078)
        assert window.spin_backlash_mm.value() != pytest.approx(0.456)
    finally:
        _close_test_window(window)


def test_calibration_recipe_builds_automatic_sequence(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CALIBRATION)
    assert mode_index >= 0
    window.combo_recipe_mode.setCurrentIndex(mode_index)
    window.spin_calibration_baseline_s.setValue(1.0)
    window.spin_calibration_start_load_g.setValue(1.0)
    window.spin_calibration_end_load_g.setValue(2.0)
    window.spin_calibration_load_step_g.setValue(1.0)
    window.spin_calibration_move_step_mm.setValue(0.01)
    window.spin_calibration_steps_per_direction.setValue(2)
    window.spin_control_interval.setValue(250)
    window.check_positive_motion_is_tension.setChecked(True)

    try:
        steps, summary, interval_ms = window._build_automation_recipe()

        assert interval_ms == 250
        assert "calibration" in summary
        assert window.combo_recipe_mode.itemText(mode_index) == "Calibration"
        actions = [step.action for step in steps]
        assert steps[0].action == "starting_length_prompt"
        assert "start_session" in actions
        first_record = next(step for step in steps if step.action == "calibration_record")
        assert first_record.note == "calibration_baseline"
        assert any(
            step.action == "seek_target"
            and step.basis == mini_dma_mod.HSW_BASIS_LOAD_G
            and step.target_value == pytest.approx(1.0)
            for step in steps
        )
        forward_moves = [
            step for step in steps if step.action == "calibration_move" and step.note == "calibration_forward"
        ]
        reverse_moves = [
            step for step in steps if step.action == "calibration_move" and step.note == "calibration_reverse"
        ]
        assert [step.relative_mm for step in forward_moves] == [pytest.approx(0.01)] * 4
        assert [step.relative_mm for step in reverse_moves] == [pytest.approx(-0.01)] * 4
        assert steps[-1].action == "calibration_record"
    finally:
        _close_test_window(window)


def test_calibration_recipe_includes_length_setup_by_default(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CALIBRATION)
    assert mode_index >= 0
    window.combo_recipe_mode.setCurrentIndex(mode_index)
    window.spin_setup_preload_stress_mpa.setValue(10.0)
    window.spin_setup_preload_duration_s.setValue(10.0)
    window.spin_setup_zero_stable_s.setValue(1.0)
    window.spin_calibration_baseline_s.setValue(1.0)
    window.spin_calibration_start_load_g.setValue(0.25)
    window.spin_calibration_end_load_g.setValue(0.5)
    window.spin_calibration_load_step_g.setValue(0.25)
    window.spin_control_interval.setValue(250)

    try:
        steps, summary, interval_ms = window._build_automation_recipe()

        assert interval_ms == 250
        assert "Includes length setup" in summary
        actions = [step.action for step in steps]
        assert steps[0].action == "starting_length_prompt"
        assert steps[0].note == "setup_start_length"
        assert "starting_length_prompt" in actions
        assert "measure_length_prompt" not in actions
        assert "mark_setup_return_zero" in actions
        assert "apply_length_setup" in actions
        assert "start_session" in actions
        assert actions.index("starting_length_prompt") < actions.index("mark_setup_return_zero")
        assert actions.index("mark_setup_return_zero") < actions.index("apply_length_setup")
        assert actions.index("apply_length_setup") < actions.index("start_session") < actions.index("calibration_record")
    finally:
        _close_test_window(window)


def test_calibration_defaults_are_safe_for_microwire_startup(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CALIBRATION)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        assert window.spin_calibration_start_load_g.value() <= 0.25
        assert window.spin_calibration_end_load_g.value() <= 1.0
        assert window.spin_calibration_preload_speed_mm_s.value() <= 0.25
    finally:
        _close_test_window(window)


def test_legacy_copper_calibration_setting_opens_generic_calibration(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("recipe_mode", mini_dma_mod.CALIBRATION_COPPER)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        assert window.combo_recipe_mode.currentData() == mini_dma_mod.CALIBRATION
        assert window.combo_recipe_mode.currentText() == "Calibration"
    finally:
        _close_test_window(window)


def test_legacy_calibration_defaults_migrate_to_microwire_safe_values(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("calibration_defaults_version", 2)
    settings.setValue("calibration_start_load_g", 1.0)
    settings.setValue("calibration_end_load_g", 5.0)
    settings.setValue("calibration_load_step_g", 1.0)
    settings.setValue("calibration_preload_speed_mm_s", 1.0)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        assert window.spin_calibration_start_load_g.value() == pytest.approx(0.25)
        assert window.spin_calibration_end_load_g.value() == pytest.approx(1.0)
        assert window.spin_calibration_load_step_g.value() == pytest.approx(0.25)
        assert window.spin_calibration_preload_speed_mm_s.value() == pytest.approx(0.2)
    finally:
        _close_test_window(window)


def test_calibration_uses_fast_preload_seek_separate_from_micro_moves(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CALIBRATION)
    assert mode_index >= 0
    window.combo_recipe_mode.setCurrentIndex(mode_index)
    window.spin_calibration_preload_nudge_mm.setValue(0.1)
    window.spin_calibration_preload_speed_mm_s.setValue(0.75)
    window.spin_calibration_move_step_mm.setValue(0.01)
    window.spin_calibration_speed_mm_s.setValue(0.2)

    try:
        window._automation_active = True
        window._automation_name = mini_dma_mod.CALIBRATION
        window._automation_phase = "seek"

        assert window._seek_nudge_mm() == pytest.approx(0.1)
        assert window._motion_speed_for_current_context(manual_jog=False) == pytest.approx(0.75)

        window._automation_phase = mini_dma_mod.CALIBRATION_FORWARD

        assert window._motion_speed_for_current_context(manual_jog=False) == pytest.approx(0.2)
    finally:
        _close_test_window(window)


def test_recipe_stack_sizes_to_visible_page(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CALIBRATION)
    assert mode_index >= 0
    window.combo_recipe_mode.setCurrentIndex(mode_index)

    try:
        assert window.recipe_stack.sizeHint().height() == window.recipe_stack.currentWidget().sizeHint().height()
    finally:
        _close_test_window(window)


def test_move_command_keeps_confirmed_position_until_status_refresh(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.target_steps: int | None = None

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.target_steps = position_steps
            self.max_speed = max_speed

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window._current_position_mm = 1.25
    window._current_position_steps = 125
    window.spin_steps_per_mm.setValue(100.0)

    try:
        moved = window._move_to_position_mm(2.0, speed_mm_s=1.0)
        _wait_for_tic_commands(window)

        assert moved is True
        assert controller.target_steps == 200
        assert controller.max_speed == 1000000
        assert window._current_position_mm == pytest.approx(1.25)
        assert window._current_position_steps == 125
        assert window._last_move_target_mm == pytest.approx(2.0)
    finally:
        _close_test_window(window)


def test_calibration_relative_moves_chain_from_commanded_targets(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    times = iter([10.0, 10.0, 10.1, 10.1, 10.2, 10.2])
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: next(times))

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    _use_immediate_tic_dispatcher(window, controller)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._current_position_mm = 0.0
    window._current_position_steps = 0
    window._last_move_target_mm = 0.0
    window._last_tic_status_time_s = time.time()
    window.spin_steps_per_mm.setValue(100.0)
    step = mini_dma_mod.AutomationStep(
        "calibration_move",
        relative_mm=0.01,
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        note=mini_dma_mod.CALIBRATION_FORWARD,
    )

    try:
        assert window._handle_calibration_move_step(step, 1) is True
        _wait_for_tic_commands(window)
        assert window._handle_calibration_move_step(step, 2) is True
        _wait_for_tic_commands(window)

        assert controller.targets == [1, 2]
        assert window._current_position_steps == 0
        assert window._last_move_target_mm == pytest.approx(0.02)
    finally:
        _close_test_window(window)


def test_record_current_point_uses_commanded_position_without_tic_status_refresh(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("cached_tic_position")
    window.check_zero_position_on_start.setChecked(False)
    window.check_tare_on_start.setChecked(False)
    window._latest_scale_value_g = window.spin_zero_load_scale_g.value()
    window._latest_scale_timestamp = time.time()
    window._refresh_tic_status = lambda: (_ for _ in ()).throw(AssertionError("status should be cached"))  # type: ignore[method-assign]

    try:
        window._start_session(record_initial_point=False)
        window._current_position_mm = 0.0
        window._current_position_steps = 0
        window._last_tic_status_time_s = time.time()
        window._last_move_target_mm = 0.5
        window._last_commanded_position_steps = 50
        window._last_motion_command_time_s = time.time() + 0.1

        assert window._record_current_point(quiet=True) is True
        assert window._session_points[-1].raw_position_mm == pytest.approx(0.5)
    finally:
        _close_test_window(window)


def test_calibration_record_waits_for_fresh_scale_after_move(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("calibration_feedback_wait")
    window.check_zero_position_on_start.setChecked(False)
    window.check_tare_on_start.setChecked(False)
    window._latest_scale_value_g = window.spin_zero_load_scale_g.value()
    stale_s = time.time()
    window._latest_scale_timestamp = stale_s

    stop_calls = 0

    def _count_stop(**_kwargs: object) -> None:
        nonlocal stop_calls
        stop_calls += 1

    try:
        window._start_session(record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CALIBRATION
        window._stop_auto_ramp = _count_stop  # type: ignore[method-assign]
        window._last_motion_command_time_s = stale_s + 0.1
        step = mini_dma_mod.AutomationStep(
            "calibration_record",
            target_value=0.25,
            basis=mini_dma_mod.HSW_BASIS_LOAD_G,
            note=mini_dma_mod.CALIBRATION_FORWARD,
        )

        assert window._handle_timed_record_step(step, 5, calibration=True) is False
        assert stop_calls == 0
        assert window._session_points == []

        window._latest_scale_timestamp = time.time() + 0.2
        assert window._handle_timed_record_step(step, 5, calibration=True) is True
        assert len(window._session_points) == 1
        assert window._session_points[0].automation_phase == mini_dma_mod.CALIBRATION_FORWARD
    finally:
        _close_test_window(window)


def test_move_command_rejects_sub_step_targets(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.called = False

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.called = True

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    _use_immediate_tic_dispatcher(window, controller)
    window._current_position_steps = 0
    window._current_position_mm = 0.0
    window.spin_steps_per_mm.setValue(100.0)

    try:
        moved = window._move_to_position_mm(0.0001)

        assert moved is False
        assert controller.called is False
        assert "rounds to the current motor step" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_manual_jog_repeats_from_last_commanded_target(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    times = iter([0.0, 1.0, 2.0])
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: next(times))

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    _use_immediate_tic_dispatcher(window, controller)
    window._current_position_steps = 0
    window._current_position_mm = 0.0
    window._last_move_target_mm = 0.0
    window._manual_jog_uses_last_target = False
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_jog_mm.setValue(0.1)

    try:
        window._jog_relative(-1.0)
        _wait_for_tic_commands(window)
        window._jog_relative(-1.0)
        _wait_for_tic_commands(window)
        window._jog_relative(1.0)
        _wait_for_tic_commands(window)

        assert controller.targets == [-10, -20, -10]
        assert window._last_move_target_mm == pytest.approx(-0.1)
    finally:
        _close_test_window(window)


def test_manual_jog_press_resyncs_stale_previous_target(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    times = iter([0.0, 0.1])
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: next(times))

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    _use_immediate_tic_dispatcher(window, controller)
    window._refresh_tic_status = lambda: None  # type: ignore[method-assign]
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_jog_mm.setValue(0.1)
    window._current_position_mm = 0.0
    window._current_position_steps = 0
    window._last_move_target_mm = 3.4
    window._last_effective_move_target_mm = 3.4
    window._last_motion_command_time_s = None
    window._last_tic_status_time_s = time.time()
    window._manual_jog_uses_last_target = True

    try:
        window._start_manual_jog(-1.0)
        window._stop_manual_jog()
        window._handle_manual_jog_button_clicked(-1.0)
        _wait_for_tic_commands(window)

        assert controller.targets == [-10]
    finally:
        _close_test_window(window)


def test_manual_jog_press_uses_recent_good_tic_status_without_blocking_refresh(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    refresh_calls: list[bool] = []

    window._refresh_tic_status = lambda: refresh_calls.append(True) or True  # type: ignore[method-assign]
    window._tic_motor_power_ok = True
    window._last_tic_vin_v = 12.0
    window._last_tic_status_time_s = time.time()
    window._last_motion_command_time_s = None
    window._current_position_mm = 1.25
    window._effective_position_mm = 9.0
    window._last_effective_move_target_mm = 9.0
    window._last_move_target_mm = 9.0
    window._manual_jog_uses_last_target = True

    try:
        window._prepare_manual_jog_press()

        assert refresh_calls == []
        assert window._effective_position_mm == pytest.approx(1.25)
        assert window._last_effective_move_target_mm == pytest.approx(1.25)
        assert window._last_move_target_mm == pytest.approx(1.25)
        assert window._manual_jog_uses_last_target is False
    finally:
        _close_test_window(window)


def test_manual_jog_press_refreshes_stale_tic_status(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    refresh_calls: list[bool] = []

    window._refresh_tic_status = lambda: refresh_calls.append(True) or True  # type: ignore[method-assign]
    window._tic_motor_power_ok = True
    window._last_tic_status_time_s = time.time() - (mini_dma_mod.MANUAL_JOG_TIC_STATUS_FRESH_S + 1.0)

    try:
        window._prepare_manual_jog_press()

        assert refresh_calls == [True]
    finally:
        _close_test_window(window)


def test_held_manual_jog_advances_by_configured_linear_speed(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    times = iter([10.0, 10.12, 10.24])
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: next(times))

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    _use_immediate_tic_dispatcher(window, controller)
    window._current_position_steps = 0
    window._current_position_mm = 0.0
    window._last_move_target_mm = 0.0
    window._manual_jog_uses_last_target = False
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_jog_mm.setValue(0.01)
    window.spin_motion_speed_mm_s.setValue(1.0)

    try:
        window._jog_relative(-1.0)
        _wait_for_tic_commands(window)
        window._jog_relative(-1.0)
        _wait_for_tic_commands(window)
        window._jog_relative(-1.0)
        _wait_for_tic_commands(window)

        assert controller.targets == [-1, -13, -25]
    finally:
        _close_test_window(window)


def test_held_manual_jog_caps_delayed_timer_tick(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    clock = {"now": 10.0}
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: clock["now"])

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_jog_mm.setValue(0.01)
    window.spin_motion_speed_mm_s.setValue(1.0)

    try:
        window._start_manual_jog(-1.0)
        clock["now"] = 10.8
        window._handle_manual_jog_timer()
        _wait_for_tic_commands(window)

        assert controller.targets == [-7]
    finally:
        window._manual_jog_timer.stop()
        _close_test_window(window)


def test_manual_jog_delayed_timer_does_not_batch_large_move(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    clock = {"now": 10.0}
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: clock["now"])

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    _use_immediate_tic_dispatcher(window, controller)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_jog_mm.setValue(0.00625)
    window.spin_motion_speed_mm_s.setValue(0.1)

    try:
        window._start_manual_jog(1.0)
        clock["now"] = 20.0
        window._handle_manual_jog_timer()
        _wait_for_tic_commands(window)

        assert controller.targets == [6]
        assert "1000.00 um" not in window.log_output.toPlainText()
    finally:
        window._manual_jog_timer.stop()
        _close_test_window(window)


def test_manual_jog_buttons_use_press_hold_motion(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    started: list[float] = []
    stopped: list[bool] = []
    single_steps: list[float] = []
    window._start_manual_jog = lambda direction: started.append(direction)  # type: ignore[method-assign]
    window._stop_manual_jog = lambda: stopped.append(True)  # type: ignore[method-assign]
    window._jog_relative = lambda direction, *, force_step=False: single_steps.append(direction)  # type: ignore[method-assign]

    try:
        tension_button = window.findChild(QtWidgets.QPushButton, "manual_jog_tension_button")
        relax_button = window.findChild(QtWidgets.QPushButton, "manual_jog_relax_button")

        assert tension_button is not None
        assert relax_button is not None
        assert tension_button.autoRepeat() is False
        assert relax_button.autoRepeat() is False

        tension_button.pressed.emit()
        tension_button.released.emit()
        tension_button.clicked.emit()

        assert started == [window._tension_motion_sign()]
        assert stopped == [True]
        assert single_steps == [window._tension_motion_sign()]
    finally:
        _close_test_window(window)


def test_manual_jog_release_suppresses_click_after_continuous_hold(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    single_steps: list[float] = []
    window._jog_relative = lambda direction, *, force_step=False: single_steps.append(direction)  # type: ignore[method-assign]
    window._manual_jog_click_suppressed = True

    try:
        window._handle_manual_jog_button_clicked(1.0)

        assert single_steps == []
        assert window._manual_jog_click_suppressed is False
    finally:
        _close_test_window(window)


def test_manual_auto_connect_button_runs_manual_preflight(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    called: list[str] = []
    window._ensure_tic_ready_for_recipe = lambda: called.append("tic") or True  # type: ignore[method-assign]
    window._ensure_scale_ready_for_recipe = lambda: called.append("scale") or True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: called.append("supply") or True  # type: ignore[method-assign]
    window._prepare_current_sweep_supply_channel = lambda: called.append("current") or True  # type: ignore[method-assign]
    window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]

    try:
        button = window.findChild(QtWidgets.QPushButton, "manual_auto_connect_button")

        assert button is not None

        button.clicked.emit()

        qtbot.waitUntil(lambda: called == ["scale", "supply", "current", "tic"], timeout=1000)
        assert called == ["scale", "supply", "current", "tic"]
        assert button.isEnabled()
        assert button.text() == "Auto-connect hardware"
    finally:
        _close_test_window(window)


def test_manual_auto_connect_button_disables_while_queued(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    called: list[str] = []
    window._ensure_tic_ready_for_recipe = lambda: called.append("tic") or True  # type: ignore[method-assign]
    window._ensure_scale_ready_for_recipe = lambda: called.append("scale") or True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: called.append("supply") or True  # type: ignore[method-assign]
    window._prepare_current_sweep_supply_channel = lambda: called.append("current") or True  # type: ignore[method-assign]
    window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]

    try:
        button = window.findChild(QtWidgets.QPushButton, "manual_auto_connect_button")

        assert button is not None

        button.clicked.emit()

        assert not button.isEnabled()
        assert button.text() == "Auto-connecting..."
        qtbot.waitUntil(lambda: button.isEnabled(), timeout=1000)
    finally:
        _close_test_window(window)


def test_manual_auto_connect_enables_motor_supply_before_tic_status(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    called: list[str] = []
    window._ensure_scale_ready_for_recipe = lambda: called.append("scale") or True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: called.append("supply") or True  # type: ignore[method-assign]
    window._prepare_current_sweep_supply_channel = lambda: called.append("current") or True  # type: ignore[method-assign]
    window._enable_motor_supply_output = lambda: called.append("motor") or True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: called.append("tic") or True  # type: ignore[method-assign]
    window.check_motor_supply_power.setChecked(True)

    try:
        window._run_manual_auto_connect_hardware()

        assert called == ["scale", "supply", "current", "supply", "motor", "tic"]
        assert "Manual hardware auto-connect completed." in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_direct_hmp_manual_auto_connect_applies_ch2_motor_defaults_before_tic_status(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    called: list[str] = []
    window._ensure_scale_ready_for_recipe = lambda: called.append("scale") or True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: called.append("supply") or True  # type: ignore[method-assign]
    window._prepare_current_sweep_supply_channel = lambda: called.append("current") or True  # type: ignore[method-assign]
    window._enable_motor_supply_output = lambda: called.append("motor") or True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: called.append("tic") or True  # type: ignore[method-assign]

    try:
        profile_index = window.combo_supply_profile.findData("hmp4030")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(0)
        )
        window.combo_motor_supply_channel.setCurrentIndex(window.combo_motor_supply_channel.findData(0))
        window.check_motor_supply_power.setChecked(False)

        window._run_manual_auto_connect_hardware()

        assert window.combo_current_sweep_supply_channel.currentData() == 3
        assert window.combo_motor_supply_channel.currentData() == 2
        assert window.check_motor_supply_power.isChecked()
        assert called == ["scale", "supply", "current", "supply", "motor", "tic"]
        assert "Direct HMP TMA bench defaults applied for Tic preflight" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_manual_auto_connect_preserves_live_stress_conversion(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    called: list[str] = []
    window._ensure_scale_ready_for_recipe = lambda: called.append("scale") or True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: called.append("supply") or True  # type: ignore[method-assign]
    window._prepare_current_sweep_supply_channel = lambda: called.append("current") or True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: called.append("tic") or True  # type: ignore[method-assign]
    window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.2)
    window.spin_diameter.setValue(0.01)
    window._latest_scale_value_g = 21.155
    window._latest_scale_text = "21.155 g"
    window._latest_scale_timestamp = time.time()

    try:
        expected_stress = mini_dma_mod.stress_mpa_from_load_g(0.045, 0.01)

        window._run_manual_auto_connect_hardware()

        assert expected_stress == pytest.approx(5.617, rel=5e-4)
        assert called == ["scale", "supply", "current", "tic"]
        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
        assert window.spin_diameter.value() == pytest.approx(0.01)
        assert window._dashboard_value_labels["load_g"].text() == "0.045 g"
        assert window._dashboard_value_labels["stress_mpa"].text() == "5.6 MPa"
    finally:
        _close_test_window(window)


def test_manual_auto_connect_connects_selected_ir_without_hardware_steal(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    called: list[str] = []
    window._ensure_scale_ready_for_recipe = lambda: called.append("scale") or True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: called.append("supply") or True  # type: ignore[method-assign]
    window._prepare_current_sweep_supply_channel = lambda: called.append("current") or True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: called.append("tic") or True  # type: ignore[method-assign]
    window._connect_ir_thermometer = lambda **_kwargs: called.append("ir") or True  # type: ignore[method-assign]
    window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]

    try:
        window.combo_ir_port.addItem("Synthetic IR", "COM_IR")
        window.combo_ir_port.setCurrentIndex(window.combo_ir_port.findData("COM_IR"))
        window.combo_ir_sensor.setCurrentIndex(
            window.combo_ir_sensor.findData(mini_dma_mod.IR_SENSOR_MLX90640)
        )

        window._run_manual_auto_connect_hardware()

        assert called == ["scale", "supply", "current", "tic", "ir"]
    finally:
        _close_test_window(window)


def test_manual_auto_connect_skips_disabled_optional_ir(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    called: list[str] = []
    window._ensure_scale_ready_for_recipe = lambda: called.append("scale") or True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: called.append("supply") or True  # type: ignore[method-assign]
    window._prepare_current_sweep_supply_channel = lambda: called.append("current") or True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: called.append("tic") or True  # type: ignore[method-assign]
    window._connect_ir_thermometer = lambda **_kwargs: called.append("ir") or True  # type: ignore[method-assign]
    window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]

    try:
        window.combo_ir_port.addItem("Synthetic IR", "COM_IR")
        window.combo_ir_port.setCurrentIndex(window.combo_ir_port.findData("COM_IR"))
        window.check_ir_enabled.setChecked(False)

        window._run_manual_auto_connect_hardware()

        assert called == ["scale", "supply", "current", "tic"]
        assert "IR disabled" in window.label_ir_live.text()
    finally:
        _close_test_window(window)


def test_manual_auto_connect_leaves_active_ir_connection_alone(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    called: list[str] = []
    window._ensure_scale_ready_for_recipe = lambda: called.append("scale") or True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: called.append("supply") or True  # type: ignore[method-assign]
    window._prepare_current_sweep_supply_channel = lambda: called.append("current") or True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: called.append("tic") or True  # type: ignore[method-assign]
    window._connect_ir_thermometer = lambda **_kwargs: called.append("ir") or True  # type: ignore[method-assign]
    window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]

    try:
        window._ir_thread = QtCore.QThread(window)

        window._run_manual_auto_connect_hardware()

        assert called == ["scale", "supply", "current", "tic"]
    finally:
        window._ir_thread = None
        _close_test_window(window)


def test_shared_broker_manual_auto_connect_applies_kosice_ch2_ch3_defaults_before_tic_status(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    called: list[str] = []
    window._ensure_scale_ready_for_recipe = lambda: called.append("scale") or True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: called.append("supply") or True  # type: ignore[method-assign]
    window._prepare_current_sweep_supply_channel = lambda: called.append("current") or True  # type: ignore[method-assign]
    window._enable_motor_supply_output = lambda: called.append("motor") or True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: called.append("tic") or True  # type: ignore[method-assign]

    try:
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        assert window.combo_current_sweep_supply_channel.currentData() == 0
        assert window.combo_motor_supply_channel.currentData() == 0
        assert not window.check_motor_supply_power.isChecked()

        window._run_manual_auto_connect_hardware()

        assert window.combo_current_sweep_supply_channel.currentData() == 3
        assert window.combo_motor_supply_channel.currentData() == 2
        assert window.check_motor_supply_power.isChecked()
        assert called == ["scale", "supply", "current", "supply", "motor", "tic"]
        assert "Shared HMP TMA bench defaults applied for Tic preflight" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_manual_auto_connect_applies_tic_settings_after_status(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    called: list[str] = []
    window._ensure_scale_ready_for_recipe = lambda: called.append("scale") or True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: called.append("supply") or True  # type: ignore[method-assign]
    window._prepare_current_sweep_supply_channel = lambda: called.append("current") or True  # type: ignore[method-assign]
    window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]

    def _tic_ready() -> bool:
        called.append("tic")
        window._tic_status_text = "\n".join(
            [
                "VIN voltage: 12.00 V",
                "Step mode: 1/8 step",
                "Max speed: 10000000",
                "Max acceleration: 100000",
                "Max deceleration: 100000",
                "Current limit: 343 mA",
                "Errors currently stopping the motor: None",
            ]
        )
        return True

    window._ensure_tic_ready_for_recipe = _tic_ready  # type: ignore[method-assign]
    window._apply_tic_configured_step_mode = (  # type: ignore[method-assign]
        lambda: called.append("step") or (True, "PASS: Tic step mode 1/8 step")
    )
    window._apply_tic_current_limit = (  # type: ignore[method-assign]
        lambda: called.append("current_limit") or (True, "PASS: Tic current limit 343 mA.")
    )
    window._apply_tic_motion_limits = (  # type: ignore[method-assign]
        lambda: called.append("motion")
        or (True, "PASS: Tic motion limits speed 10000000, accel 100000, decel 100000.")
    )

    try:
        window._run_manual_auto_connect_hardware()

        assert called == ["scale", "supply", "current", "tic", "step", "current_limit", "motion"]
        log_text = window.log_output.toPlainText()
        assert "Manual hardware auto-connect: PASS: Tic step mode 1/8 step" in log_text
        assert "Manual hardware auto-connect: PASS: Tic current limit 343 mA." in log_text
        assert "Manual hardware auto-connect: PASS: Tic motion limits speed 10000000" in log_text
    finally:
        _close_test_window(window)


def test_manual_auto_connect_shows_progress_dialog_while_queued(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_scale_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._prepare_current_sweep_supply_channel = lambda: True  # type: ignore[method-assign]
    window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]

    try:
        button = window.findChild(QtWidgets.QPushButton, "manual_auto_connect_button")

        assert button is not None

        button.clicked.emit()

        progress = window._manual_auto_connect_progress
        assert progress is not None
        assert progress.isVisible()
        assert progress.minimum() == 0
        assert progress.maximum() == 0
        assert "Connecting hardware" in progress.labelText()

        qtbot.waitUntil(lambda: button.isEnabled(), timeout=1000)
        assert window._manual_auto_connect_progress is None
    finally:
        _close_test_window(window)


def test_recipe_preflight_shows_auto_connect_progress_when_requested(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    warnings: list[str] = []
    progress_seen: list[bool] = []

    def _fail_scale() -> bool:
        progress = window._manual_auto_connect_progress
        progress_seen.append(progress is not None and progress.isVisible())
        return False

    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]
    window._apply_tic_configured_step_mode = lambda: (True, "PASS")  # type: ignore[method-assign]
    window._apply_tic_current_limit = lambda: (True, "PASS")  # type: ignore[method-assign]
    window._apply_tic_motion_limits = lambda: (True, "PASS")  # type: ignore[method-assign]
    window._ensure_scale_ready_for_recipe = _fail_scale  # type: ignore[method-assign]
    window._tic_motor_power_ok = None
    window._scale_thread = None

    try:
        ok = window._preflight_recipe_hardware(
            [
                mini_dma_mod.AutomationStep(
                    "seek_target",
                    target_value=0.0,
                    basis=mini_dma_mod.HSW_BASIS_LOAD_G,
                ),
                mini_dma_mod.AutomationStep("record", basis=mini_dma_mod.HSW_BASIS_LOAD_G),
            ],
            show_progress=True,
        )

        assert ok is False
        assert progress_seen == [True]
        assert window._manual_auto_connect_progress is None
        assert warnings
    finally:
        _close_test_window(window)


def test_recipe_preflight_does_not_show_auto_connect_progress_by_default(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    shown: list[bool] = []
    window._show_manual_auto_connect_progress = lambda: shown.append(True)  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]
    window._apply_tic_configured_step_mode = lambda: (True, "PASS")  # type: ignore[method-assign]
    window._apply_tic_current_limit = lambda: (True, "PASS")  # type: ignore[method-assign]
    window._apply_tic_motion_limits = lambda: (True, "PASS")  # type: ignore[method-assign]
    window._ensure_scale_ready_for_recipe = lambda: True  # type: ignore[method-assign]

    try:
        ok = window._preflight_recipe_hardware(
            [
                mini_dma_mod.AutomationStep(
                    "seek_target",
                    target_value=0.0,
                    basis=mini_dma_mod.HSW_BASIS_LOAD_G,
                ),
                mini_dma_mod.AutomationStep("record", basis=mini_dma_mod.HSW_BASIS_LOAD_G),
            ]
        )

        assert ok is True
        assert shown == []
    finally:
        _close_test_window(window)


def test_manual_auto_connect_prepares_current_sweep_channel_without_enabling_output(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    configured: list[tuple[int, float, float, bool]] = []
    selected: list[int | None] = []

    class _FakeSupply:
        def is_connected(self) -> bool:
            return True

        def disconnect(self) -> None:
            pass

        def configure_channel(self, *, channel: int, voltage_v: float, current_a: float, output_on: bool) -> None:
            configured.append((channel, voltage_v, current_a, output_on))

        def select_channel(self, channel: int | None = None) -> None:
            selected.append(channel)

    window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_scale_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]
    window._supply_controller = _FakeSupply()  # type: ignore[assignment]
    window.combo_supply_profile.setCurrentIndex(window.combo_supply_profile.findData("hmp4040"))
    window.combo_current_sweep_supply_channel.setCurrentIndex(
        window.combo_current_sweep_supply_channel.findData(4)
    )
    window.spin_supply_voltage_limit.setValue(32.05)
    window.spin_supply_manual_current.setValue(1.0)

    try:
        button = window.findChild(QtWidgets.QPushButton, "manual_auto_connect_button")

        assert button is not None

        button.clicked.emit()

        qtbot.waitUntil(lambda: button.isEnabled(), timeout=1000)
        assert configured == [(4, pytest.approx(32.05), pytest.approx(0.001), False)]
        assert selected[-1] is None
        assert window._supply_output_enabled is False
        assert window._supply_last_setpoint_mA == pytest.approx(1.0)
    finally:
        _close_test_window(window)


def test_manual_auto_connect_warns_when_a_step_fails(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    warnings: list[str] = []

    try:
        window._ensure_scale_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._ensure_supply_ready_for_recipe = lambda: False  # type: ignore[method-assign]
        window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]
        original_warning = QtWidgets.QMessageBox.warning
        QtWidgets.QMessageBox.warning = (  # type: ignore[method-assign]
            lambda _parent, _title, message: warnings.append(str(message))
        )
        try:
            window._show_manual_auto_connect_progress()
            window._run_manual_auto_connect_hardware()
        finally:
            QtWidgets.QMessageBox.warning = original_warning  # type: ignore[method-assign]

        assert warnings == [
            "Hardware auto-connect did not complete:\n\n- Power supply connection failed."
        ]
        assert "Manual hardware auto-connect did not complete: Power supply connection failed." in (
            window.log_output.toPlainText()
        )
    finally:
        _close_test_window(window)


def test_microwire_entry_does_not_insert_slash_before_fourth_digit(qtbot) -> None:
    edit = mini_dma_mod.MicrowireLineEdit()
    qtbot.addWidget(edit)

    edit.show()
    edit.setFocus()

    qtbot.keyClicks(edit, "1")
    assert edit.text() == "1"
    assert edit.cursorPosition() == 1

    qtbot.keyClicks(edit, "23")
    assert edit.text() == "123"
    assert edit.cursorPosition() == 3

    qtbot.keyClicks(edit, "4")
    assert edit.text() == "123/4"
    assert edit.cursorPosition() == len("123/4")


def test_microwire_entry_allows_manual_slash_and_right_side_typing(qtbot) -> None:
    edit = mini_dma_mod.MicrowireLineEdit()
    qtbot.addWidget(edit)

    edit.show()
    edit.setFocus()

    qtbot.keyClicks(edit, "11/1")

    assert edit.text() == "11/1"
    assert edit.cursorPosition() == len("11/1")


def test_recipe_stop_resets_manual_jog_base_to_confirmed_position(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window._automation_active = True
    window._automation_steps = [mini_dma_mod.AutomationStep("move", target_mm=5.0)]
    window._automation_index = 0
    window._automation_total_steps = 1
    window._last_move_target_mm = 5.0
    window._manual_jog_uses_last_target = True
    window._current_position_mm = 1.2
    window._current_position_steps = 120
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_jog_mm.setValue(0.1)
    window.check_positive_motion_is_tension.setChecked(False)
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
    window._ask_recovery_after_stop = lambda: None  # type: ignore[method-assign]

    try:
        window._stop_auto_ramp(user_initiated=True)
        window._jog_relative(-window._tension_motion_sign())
        _wait_for_tic_commands(window)

        assert controller.targets == [130]
        assert window._manual_jog_uses_last_target is True
        assert window._last_move_target_mm == pytest.approx(1.3)
    finally:
        _close_test_window(window)


def test_settings_wheel_guard_scrolls_panel_without_changing_values(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        scrollbar = window._control_scroll_area.verticalScrollBar()
        scrollbar.setRange(0, 100)
        scrollbar.setValue(50)

        window.spin_ramp_distance.setValue(1.0)
        assert window.eventFilter(window.spin_ramp_distance, _wheel_event()) is True
        assert window.spin_ramp_distance.value() == pytest.approx(1.0)
        assert scrollbar.value() > 50

        scrollbar.setValue(50)
        assert window.eventFilter(window.spin_ramp_distance.lineEdit(), _wheel_event()) is True
        assert window.spin_ramp_distance.value() == pytest.approx(1.0)
        assert scrollbar.value() > 50

        combo = window.combo_recipe_mode
        combo.setCurrentIndex(0)
        assert window.eventFilter(combo, _wheel_event()) is True
        assert combo.currentIndex() == 0
    finally:
        _close_test_window(window)


def test_settings_panel_avoids_horizontal_scrolling(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert window._control_scroll_area.horizontalScrollBarPolicy() == (
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.edit_run_notes.horizontalScrollBarPolicy() == (
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.log_output.horizontalScrollBarPolicy() == (
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        window.resize(440, 700)
        window._make_settings_panel_width_friendly()
        _ensure_app().processEvents()
        assert window._control_scroll_area.horizontalScrollBar().height() == 0
        assert window._control_scroll_area.horizontalScrollBar().isVisible() is False
        assert window.edit_run_notes.lineWrapMode() == QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth
        assert window.log_output.lineWrapMode() == QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth
    finally:
        _close_test_window(window)


def test_long_recipe_estimates_use_minutes_and_show_progress(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_LOAD)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        _set_copper_current_sweep_defaults(window)
        window.spin_current_sweep_target_end.setValue(20.0)
        window.spin_current_sweep_target_step.setValue(5.0)
        window.spin_current_sweep_end_mA.setValue(5.0)
        window.spin_current_sweep_step_mA.setValue(0.5)
        window.spin_current_sweep_interval.setValue(500)
        window._update_recipe_mode_ui()

        assert "Estimated duration: 8.1 min" in window.label_recipe_estimate.text()
        assert window.recipe_progress.maximum() > 100
        assert window.recipe_progress.value() == 0
        assert "Estimated:" in window.recipe_progress.format()
        assert "8.1 min" in window.recipe_progress.format()
    finally:
        _close_test_window(window)


def test_recipe_start_keeps_timed_progress_estimate(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_LOAD)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        _set_copper_current_sweep_defaults(window)
        steps, _, interval_ms = window._build_automation_recipe()
        _, expected_ticks = window._estimate_recipe_points_and_ticks(steps, interval_ms)
        _, expected_progress_ticks = window._estimate_recipe_points_and_ticks(
            steps,
            interval_ms,
            include_current_hold_estimate=False,
        )
        assert expected_ticks > len(steps)
        assert expected_ticks >= expected_progress_ticks

        window._preflight_recipe_hardware = lambda _steps, **_kwargs: True  # type: ignore[method-assign]
        window._start_session = lambda **_kwargs: setattr(window, "_session_active", True)  # type: ignore[method-assign]

        window._start_auto_ramp()

        assert window._automation_total_steps == expected_progress_ticks
        assert window.recipe_progress.maximum() == expected_progress_ticks
        assert window._automation_completed_ticks == 0
    finally:
        _close_test_window(window)


def test_recipe_start_discards_stale_resume_after_controls_change(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        window.spin_current_sweep_target_start.setValue(250.0)
        window.spin_current_sweep_target_end.setValue(1000.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(50.0)
        window.spin_current_sweep_step_mA.setValue(1.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window._update_recipe_mode_ui()
        stale_step = mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=50.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=1.0,
            current_end_mA=50.0,
            current_ramp_rate_mA_s=1.0,
        )
        window._resume_recipe_state = mini_dma_mod.AutomationResumeState(
            steps=[stale_step],
            index=0,
            interval_ms=50,
            total_steps=1,
            name=mini_dma_mod.CURRENT_SWEEP_STRESS,
            origin_mm=0.0,
            summary="Started iso-stress current sweep: 50.0000 MPa to 50.0000 MPa",
            current_setpoint_mA=1.0,
        )
        window._session_active = True
        window._ask_resume_stopped_recipe = pytest.fail  # type: ignore[method-assign]
        window._preflight_recipe_hardware = lambda _steps, **_kwargs: True  # type: ignore[method-assign]
        window._prepare_continuity_current_for_recipe = lambda _steps: True  # type: ignore[method-assign]
        window._start_automation_control_loop = lambda _interval_ms: None  # type: ignore[method-assign]

        window._start_auto_ramp()

        assert window._resume_recipe_state is None
        assert window._automation_active is True
        assert any(
            step.target_value == pytest.approx(250.0)
            for step in window._automation_steps
            if step.action == "sweep_current"
        )
        assert not any(
            step.target_value == pytest.approx(50.0)
            for step in window._automation_steps
            if step.action == "sweep_current"
        )
        assert "Discarded stopped-recipe resume state" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_recipe_progress_shows_throttled_time_remaining(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._automation_active = True
        window._automation_total_steps = 100
        window._automation_completed_ticks = 10
        window._automation_progress_started_s = time.monotonic() - 10.0
        window._automation_progress_last_format_update_s = 0.0

        window._update_recipe_progress()

        first_format = window.recipe_progress.format()
        assert "ETA" in first_format
        assert "1.5 min" in first_format

        window._automation_completed_ticks = 20
        window._update_recipe_progress()

        assert window.recipe_progress.value() == 20
        assert window.recipe_progress.format() == first_format
    finally:
        _close_test_window(window)


def test_recipe_progress_shows_compact_current_sweep_context_during_hold(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        step = mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=150.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=1.0,
            current_end_mA=60.0,
            current_ramp_rate_mA_s=0.4,
            current_hold_enabled=True,
        )
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [step]
        window._automation_total_steps = 11991
        window._automation_completed_ticks = 11990
        window._automation_interval_ms = 100
        window._automation_progress_started_s = time.monotonic() - 1200.0
        window._automation_progress_last_format_update_s = 0.0
        window._active_current_sweep_step_index = 0
        window._active_current_sweep_last_setpoint_mA = 36.2
        window._active_current_sweep_display_target_mA = 60.0
        window._active_current_sweep_display_direction = 1.0
        window._set_automation_context(
            phase="current_hold",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=150.0,
            plateau_index=1,
        )

        window._update_recipe_progress()

        assert window.recipe_progress.maximum() == 11991
        assert window.recipe_progress.value() == 11990
        text = window.recipe_progress.format()
        assert "Overall  99%" in text
        assert "150 MPa, recovering at 36.2 mA" in text
        assert "sweep 1/1" in text
        assert "ETA" in text
        assert "current 59%" not in text
    finally:
        _close_test_window(window)


def test_bench_guard_stops_bad_current_hold_quality() -> None:
    from data_logging.mini_dma_logger.bench_automation import (
        MiniDmaBenchGuardrails,
        _check_guardrails,
    )

    class _FakeWindow:
        def __init__(self) -> None:
            self._automation_phase = "current_hold"
            self._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
            self._automation_target_value = 150.0
            self._current_sweep_ramp_hold_started_s = time.monotonic() - 300.0
            self._session_points = [SimpleNamespace(stress_mpa=162.5)]
            self.auto_stop_kwargs: dict[str, object] | None = None
            self.session_stop_kwargs: dict[str, object] | None = None

        def _stop_auto_ramp(self, **kwargs: object) -> None:
            self.auto_stop_kwargs = dict(kwargs)

        def _stop_session(self, **kwargs: object) -> None:
            self.session_stop_kwargs = dict(kwargs)

    window = _FakeWindow()
    guardrails = MiniDmaBenchGuardrails(
        max_stress_mpa=300.0,
        recovery_stress_mpa=150.0,
        current_hold_quality_timeout_s=240.0,
        current_hold_quality_error_mpa=8.0,
    )

    event = _check_guardrails(window, guardrails)

    assert event is not None
    assert event["type"] == "current_hold_quality_timeout"
    assert event["error_mpa"] == pytest.approx(12.5)
    assert window.auto_stop_kwargs is not None
    assert window.auto_stop_kwargs["stop_reason"] == "current_hold_quality_timeout"
    assert window.session_stop_kwargs is not None
    assert window.session_stop_kwargs["reason"] == "current_hold_quality_timeout"


def test_current_sweep_estimate_includes_hold_allowance(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        step = mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=20.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=0.0,
            current_end_mA=60.0,
            current_ramp_rate_mA_s=1.0,
            current_hold_enabled=True,
            current_hold_resume_stable_s=0.5,
        )

        points, ticks = window._estimate_recipe_points_and_ticks([step], 1000)

        assert ticks == 74
        assert points == 74
    finally:
        _close_test_window(window)


def test_recipe_progress_uses_schedule_eta_at_start_instead_of_early_spike(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._automation_active = True
        window._automation_total_steps = 3600
        window._automation_completed_ticks = 1
        window._automation_interval_ms = 500
        window._automation_estimated_total_s = 1800.0
        window._automation_progress_started_s = time.monotonic() - 300.0
        window._automation_progress_last_format_update_s = 0.0

        window._update_recipe_progress()

        assert "ETA" in window.recipe_progress.format()
        assert "25.0 min" in window.recipe_progress.format()
        assert "h" not in window.recipe_progress.format()
    finally:
        _close_test_window(window)


def test_live_eta_projects_learned_current_sweep_overhead(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        step = mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=20.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=0.0,
            current_end_mA=60.0,
            current_ramp_rate_mA_s=1.0,
            current_hold_enabled=True,
        )
        window._automation_steps = [step, step]
        window._automation_index = 0
        window._current_sweep_duration_overheads_s = [120.0]

        learned_extra_s = window._learned_current_sweep_extra_remaining_s()

        assert learned_extra_s == pytest.approx(210.5)
    finally:
        _close_test_window(window)


def test_current_task_summary_shows_current_sweep_phase(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._automation_steps = [
        mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=50.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=1.0,
            current_end_mA=80.0,
            current_ramp_rate_mA_s=1.0,
            note="1",
        ),
        mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=50.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=80.0,
            current_end_mA=1.0,
            current_ramp_rate_mA_s=1.0,
            note="1",
        ),
        mini_dma_mod.AutomationStep(
            "settle",
            target_value=50.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_mA=1.0,
            duration_s=0.5,
            note="1",
        ),
        mini_dma_mod.AutomationStep(
            "ramp_target",
            target_value=100.0,
            target_start_value=50.0,
            target_end_value=100.0,
            target_ramp_rate_value_s=5.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            note="2",
        ),
    ]

    try:
        window._automation_index = 0
        assert window._current_task_summary() == "At 50 MPa: increasing current to 80 mA"

        window._automation_phase = "current"
        window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
        window._automation_target_value = 50.0
        window._active_current_sweep_display_target_mA = 80.0
        window._active_current_sweep_display_direction = 1.0
        window._automation_index = 1
        assert window._current_task_summary() == "At 50 MPa: increasing current to 80 mA"

        window._automation_phase = "idle"
        window._automation_index = 1
        assert window._current_task_summary() == "At 50 MPa: decreasing current to 1 mA"

        window._automation_phase = "settle"
        window._automation_index = 2
        assert window._current_task_summary() == "At 50 MPa: decreasing current to 1 mA"

        window._automation_phase = "current_hold"
        window._active_current_sweep_last_setpoint_mA = 42.4
        assert window._current_task_summary() == "At 50 MPa: holding 42.4 mA, recovering target"

        window._automation_phase = "target_ramp"
        window._automation_index = 3
        assert window._current_task_summary() == "Ramp up to 100 MPa"

        window._active_target_ramp_step_index = 3
        window._automation_index = 4
        assert window._current_task_summary() == "Ramp up to 100 MPa"
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_bench_stress_recovery_starts_stress_seek_with_supply_off(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    loop_intervals: list[int] = []
    window._preflight_recipe_hardware = lambda _steps: True  # type: ignore[method-assign]
    window._show_recovery_plot_dialog = lambda _title: None  # type: ignore[method-assign]
    window._start_automation_control_loop = lambda interval_ms: loop_intervals.append(interval_ms)  # type: ignore[method-assign]

    try:
        assert window.start_bench_stress_recovery(50.0, reason="test guard") is True

        assert window._automation_active is True
        assert window._automation_name == mini_dma_mod.RECOVERY_LOAD
        assert window._automation_steps[0].action == "seek_target"
        assert window._automation_steps[0].basis == mini_dma_mod.HSW_BASIS_STRESS_MPA
        assert window._automation_steps[0].target_value == pytest.approx(50.0)
        assert loop_intervals == [window._control_interval_ms()]
        assert "Bench high-stress guard triggered" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_double_spin_boxes_trim_zero_only_decimals(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_jog_mm.setValue(0.01)
        window.spin_current_sweep_target_end.setValue(20.0)
        window.spin_current_sweep_tolerance.setValue(0.25)

        assert window.spin_jog_mm.text().startswith("0.01")
        assert window.spin_current_sweep_target_end.text().startswith("20 ")
        assert window.spin_current_sweep_tolerance.text().startswith("0.25")
    finally:
        _close_test_window(window)


def test_hardware_tab_has_no_separate_heating_program(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert not hasattr(window, "heating_recipe_box")
        assert not hasattr(window, "combo_heating_mode")
        assert not hasattr(window, "combo_heat_limit_action")
    finally:
        _close_test_window(window)


def test_dma_plot_preset_keeps_standard_run_tiles(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._apply_plot_preset("dma")

        assert window._plot_tiles[0].x_combo.currentData() == "elapsed_s"
        assert window._plot_tiles[0].y_left_combo.currentData() == "load_g"
        assert window._plot_tiles[0].y_right_combo.currentData() == ""
        assert window._plot_tiles[1].y_left_combo.currentData() == "position_mm"
        assert window._plot_tiles[1].y_right_combo.currentData() == ""
        assert window._plot_tiles[2].y_left_combo.currentData() == "current_measured_mA"
        assert window._plot_tiles[3].y_left_combo.currentData() == "resistance_ohm"
    finally:
        _close_test_window(window)


def test_current_sweep_modes_are_separate_recipe_choices(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        expected = {
            "Iso-load current sweep": (mini_dma_mod.CURRENT_SWEEP_LOAD, mini_dma_mod.HSW_BASIS_LOAD_G),
            "Iso-stress current sweep": (mini_dma_mod.CURRENT_SWEEP_STRESS, mini_dma_mod.HSW_BASIS_STRESS_MPA),
            "Iso-strain current sweep": (mini_dma_mod.CURRENT_SWEEP_STRAIN, mini_dma_mod.HSW_BASIS_STRAIN_PCT),
            "Iso-stress fatigue": (mini_dma_mod.CURRENT_SWEEP_FATIGUE, mini_dma_mod.HSW_BASIS_STRESS_MPA),
        }

        labels = {
            window.combo_recipe_mode.itemText(index): window.combo_recipe_mode.itemData(index)
            for index in range(window.combo_recipe_mode.count())
        }

        assert "Iso-load / iso-stress / iso-strain current sweep" not in labels
        assert window.combo_current_sweep_basis.isHidden() is True
        for label, (mode, basis) in expected.items():
            assert labels[label] == mode
            index = window.combo_recipe_mode.findData(mode)
            assert index >= 0
            window.combo_recipe_mode.setCurrentIndex(index)
            assert window._current_sweep_basis() == basis
    finally:
        _close_test_window(window)


def test_current_sweep_target_values_are_recipe_mode_specific(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        stress_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        strain_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRAIN)
        load_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_LOAD)

        window.combo_recipe_mode.setCurrentIndex(stress_index)
        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(1000.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.spin_current_sweep_target_ramp_rate.setValue(5.0)

        window.combo_recipe_mode.setCurrentIndex(strain_index)

        assert window._current_sweep_basis() == mini_dma_mod.HSW_BASIS_STRAIN_PCT
        assert window.spin_current_sweep_target_start.value() == pytest.approx(0.0)
        assert window.spin_current_sweep_target_end.value() == pytest.approx(0.5)
        assert window.spin_current_sweep_target_step.value() == pytest.approx(0.1)
        assert window.spin_current_sweep_target_ramp_rate.value() == pytest.approx(0.05)

        window.spin_current_sweep_target_end.setValue(0.3)
        window.combo_recipe_mode.setCurrentIndex(load_index)
        assert window.spin_current_sweep_target_end.value() == pytest.approx(9.0)

        window.combo_recipe_mode.setCurrentIndex(stress_index)
        assert window.spin_current_sweep_target_end.value() == pytest.approx(1000.0)

        window.combo_recipe_mode.setCurrentIndex(strain_index)
        assert window.spin_current_sweep_target_end.value() == pytest.approx(0.3)
    finally:
        _close_test_window(window)


def test_current_sweep_recipe_commands_at_least_one_milliamp(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.spin_current_sweep_start_mA.setValue(0.0)
        window.spin_current_sweep_end_mA.setValue(0.0)

        steps, _summary, _interval_ms = window._build_automation_recipe()

        set_current_steps = [step for step in steps if step.action == "set_current"]
        sweep_steps = [step for step in steps if step.action == "sweep_current"]
        assert set_current_steps
        assert sweep_steps
        assert all(step.current_mA == pytest.approx(1.0) for step in set_current_steps)
        assert all(step.current_start_mA == pytest.approx(1.0) for step in sweep_steps)
        assert all(step.current_end_mA == pytest.approx(1.0) for step in sweep_steps)
    finally:
        _close_test_window(window)


def test_current_sweep_recipe_has_no_post_sweep_settle_step(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(50.0)
        window.spin_current_sweep_target_step.setValue(25.0)

        steps, _summary, _interval_ms = window._build_automation_recipe()

        assert any(step.action == "sweep_current" for step in steps)
        assert not any(step.action == "settle" and step.note == "1" for step in steps)
        assert not hasattr(window, "spin_current_sweep_settle_s")
    finally:
        _close_test_window(window)


def test_iso_stress_fatigue_recipe_builds_repeated_current_cycles(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_FATIGUE)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.check_pre_measurement_setup_enabled.setChecked(False)
        window.spin_current_sweep_target_start.setValue(150.0)
        window.spin_current_sweep_target_end.setValue(900.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.spin_current_sweep_target_ramp_rate.setValue(5.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(60.0)
        window.spin_current_sweep_step_mA.setValue(1.0)
        window.spin_current_sweep_fatigue_cycles.setValue(3)
        window.check_current_sweep_hold_on_error.setChecked(True)
        window.check_current_sweep_first_overheating.setChecked(False)
        window.check_current_sweep_reverse_current.setChecked(False)

        steps, summary, interval_ms = window._build_automation_recipe()
        payload = window._current_recipe_payload()

        set_current_steps = [step for step in steps if step.action == "set_current"]
        ramp_steps = [step for step in steps if step.action == "ramp_target"]
        sweep_steps = [step for step in steps if step.action == "sweep_current"]

        assert interval_ms == window._control_interval_ms()
        assert len(set_current_steps) == 3
        assert len(ramp_steps) == 3
        assert len(sweep_steps) == 6
        assert [step.note for step in sweep_steps] == ["1", "1", "2", "2", "3", "3"]
        assert [(step.current_start_mA, step.current_end_mA) for step in sweep_steps] == [
            (pytest.approx(1.0), pytest.approx(60.0)),
            (pytest.approx(60.0), pytest.approx(1.0)),
            (pytest.approx(1.0), pytest.approx(60.0)),
            (pytest.approx(60.0), pytest.approx(1.0)),
            (pytest.approx(1.0), pytest.approx(60.0)),
            (pytest.approx(60.0), pytest.approx(1.0)),
        ]
        assert all(step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA for step in set_current_steps)
        assert all(step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA for step in ramp_steps)
        assert all(step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA for step in sweep_steps)
        assert all(step.target_value == pytest.approx(150.0) for step in set_current_steps)
        assert all(step.target_value == pytest.approx(150.0) for step in ramp_steps)
        assert all(step.target_value == pytest.approx(150.0) for step in sweep_steps)
        assert "iso-stress fatigue" in summary
        assert "3 cycle" in summary
        assert "First overheating" not in summary
        assert payload["recipe"]["current_sweep"]["reverse_current"] is True
    finally:
        _close_test_window(window)


def test_iso_stress_fatigue_recipe_can_start_with_first_overheating(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_FATIGUE)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.check_pre_measurement_setup_enabled.setChecked(False)
        window.spin_current_sweep_target_start.setValue(150.0)
        window.spin_current_sweep_target_ramp_rate.setValue(5.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(60.0)
        window.spin_current_sweep_step_mA.setValue(1.0)
        window.spin_current_sweep_fatigue_cycles.setValue(2)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.spin_current_sweep_first_overheating_target_mpa.setValue(20.0)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(40.0)

        steps, summary, _interval_ms = window._build_automation_recipe()

        set_current_steps = [step for step in steps if step.action == "set_current"]
        ramp_steps = [step for step in steps if step.action == "ramp_target"]
        sweep_steps = [step for step in steps if step.action == "sweep_current"]

        assert [step.note for step in set_current_steps] == ["first_overheating", "1", "2"]
        assert [step.note for step in ramp_steps] == ["first_overheating", "1", "2"]
        assert [step.note for step in sweep_steps] == ["first_overheating", "first_overheating", "1", "1", "2", "2"]
        assert [(step.current_start_mA, step.current_end_mA) for step in sweep_steps[:2]] == [
            (pytest.approx(1.0), pytest.approx(40.0)),
            (pytest.approx(40.0), pytest.approx(1.0)),
        ]
        assert ramp_steps[0].target_start_value == pytest.approx(0.0)
        assert ramp_steps[0].target_end_value == pytest.approx(20.0)
        assert ramp_steps[1].target_start_value == pytest.approx(20.0)
        assert ramp_steps[1].target_end_value == pytest.approx(150.0)
        assert "First overheating enabled" in summary
        assert "40 mA" in summary
    finally:
        _close_test_window(window)


def test_iso_stress_fatigue_ui_hides_ladder_and_keeps_preheat_controls(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_FATIGUE)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)

        assert window.label_current_sweep_targets_section.text() == "Stress target"
        assert window.label_current_sweep_target_start.text() == "Stress"
        assert window.row_current_sweep_target_end.isHidden() is True
        assert window.label_current_sweep_target_end.isHidden() is True
        assert window.row_current_sweep_target_step.isHidden() is True
        assert window.label_current_sweep_target_step.isHidden() is True
        assert window.label_current_sweep_fatigue_section.isHidden() is False
        assert window.spin_current_sweep_fatigue_cycles.isHidden() is False
        assert window.check_current_sweep_first_overheating.isHidden() is False
        assert window.label_current_sweep_first_overheating_section.isHidden() is False
        assert window.row_current_sweep_first_overheating_target.isHidden() is True
        window.check_current_sweep_first_overheating.setChecked(True)
        assert window.row_current_sweep_first_overheating_target.isHidden() is False
        assert window.check_current_sweep_first_overheating_use_normal_end.isHidden() is False

        stress_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        window.combo_recipe_mode.setCurrentIndex(stress_index)
        assert window.label_current_sweep_targets_section.text() == "Stress targets"
        assert window.label_current_sweep_target_start.text() == "Start"
        assert window.row_current_sweep_target_end.isHidden() is False
        assert window.row_current_sweep_target_step.isHidden() is False
        assert window.label_current_sweep_fatigue_section.isHidden() is True
    finally:
        _close_test_window(window)


def test_dashboard_plot_choices_are_recipe_mode_specific(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        stress_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        constant_index = window.combo_recipe_mode.findData(mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP)
        assert stress_index >= 0
        assert constant_index >= 0

        window.combo_recipe_mode.setCurrentIndex(stress_index)
        _set_plot_tile(window, 0, "elapsed_s", "stress_mpa", "current_measured_mA")

        window.combo_recipe_mode.setCurrentIndex(constant_index)
        assert window._plot_tiles[0].x_combo.currentData() == "elapsed_s"
        assert window._plot_tiles[0].y_left_combo.currentData() == "load_g"
        assert window._plot_tiles[0].y_right_combo.currentData() == ""

        _set_plot_tile(window, 0, "strain_pct", "stress_mpa", "current_set_mA")

        window.combo_recipe_mode.setCurrentIndex(stress_index)
        assert window._plot_tiles[0].x_combo.currentData() == "elapsed_s"
        assert window._plot_tiles[0].y_left_combo.currentData() == "stress_mpa"
        assert window._plot_tiles[0].y_right_combo.currentData() == "current_measured_mA"

        window.combo_recipe_mode.setCurrentIndex(constant_index)
        assert window._plot_tiles[0].x_combo.currentData() == "strain_pct"
        assert window._plot_tiles[0].y_left_combo.currentData() == "stress_mpa"
        assert window._plot_tiles[0].y_right_combo.currentData() == "current_set_mA"
    finally:
        _close_test_window(window)


def test_constant_current_stress_strain_recipe_builds_fixed_mechanical_scans(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        stress_index = window.combo_constant_current_start_basis.findData(mini_dma_mod.HSW_BASIS_STRESS_MPA)
        window.combo_constant_current_start_basis.setCurrentIndex(stress_index)
        displacement_index = window.combo_constant_current_step_basis.findData(
            mini_dma_mod.MECHANICAL_STEP_DISPLACEMENT_MM
        )
        window.combo_constant_current_step_basis.setCurrentIndex(displacement_index)
        window.spin_constant_current_start_target.setValue(0.0)
        window.spin_constant_current_end_target.setValue(500.0)
        window.spin_constant_current_step_size.setValue(0.01)
        window.spin_constant_current_hold_s.setValue(1.0)
        window.spin_constant_current_move_speed_mm_s.setValue(0.2)
        window.spin_constant_current_start_mA.setValue(40.0)
        window.spin_constant_current_end_mA.setValue(50.0)
        window.spin_constant_current_step_mA.setValue(10.0)
        window.spin_constant_current_transition_stress_mpa.setValue(10.0)
        window.spin_constant_current_transition_rate_mA_s.setValue(1.0)
        window.spin_constant_current_transition_settle_s.setValue(1.0)

        steps, summary, interval_ms = window._build_automation_recipe()

        recipe_start = next(index for index, step in enumerate(steps) if step.action == "start_session")
        recipe_steps = steps[recipe_start + 1 :]
        sweep_steps = [step for step in recipe_steps if step.action == "sweep_current"]
        transition_settle_steps = [
            step
            for step in recipe_steps
            if step.action == "settle" and str(step.note or "").endswith(":transition_settle")
        ]
        zero_steps = [step for step in recipe_steps if step.action == "mark_current_zero"]
        scan_steps = [step for step in recipe_steps if step.action == "mechanical_scan"]

        assert interval_ms == window._control_interval_ms()
        assert not any(step.action == "set_current" for step in recipe_steps)
        assert [(step.current_start_mA, step.current_end_mA) for step in sweep_steps] == pytest.approx(
            [(1.0, 40.0), (40.0, 50.0)]
        )
        assert all(step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA for step in sweep_steps)
        assert all(step.target_value == pytest.approx(10.0) for step in sweep_steps)
        assert all(step.current_ramp_rate_mA_s == pytest.approx(1.0) for step in sweep_steps)
        assert all(step.current_hold_enabled is True for step in sweep_steps)
        assert all(step.mechanical_step_speed_mm_s == pytest.approx(0.2) for step in scan_steps)
        assert len(transition_settle_steps) == 2
        assert [step.current_mA for step in zero_steps] == pytest.approx([40.0, 50.0])
        assert [step.note for step in recipe_steps[:6]] == [
            "1:transition_seek",
            "1",
            "1:transition_settle",
            "1:start",
            "1:zero",
            "1:up",
        ]
        assert len(scan_steps) == 4
        assert all(step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA for step in scan_steps)
        assert scan_steps[0].target_value == pytest.approx(500.0)
        assert scan_steps[1].target_value == pytest.approx(0.0)
        assert all(step.mechanical_step_basis == mini_dma_mod.MECHANICAL_STEP_DISPLACEMENT_MM for step in scan_steps)
        assert all(step.mechanical_step_value == pytest.approx(0.01) for step in scan_steps)
        assert all(step.duration_s == pytest.approx(1.0) for step in scan_steps)
        assert not any(step.action == "ramp_target" for step in recipe_steps)
        assert "Started iso-current stress-strain recipe" in summary
        assert "Current transitions ramp at 1.000 mA/s" in summary
        assert "Each current leg scans up and back" in summary
    finally:
        _close_test_window(window)


def test_mini_dma_recipe_dropdown_hides_legacy_open_loop_recipes(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        items = {
            window.combo_recipe_mode.itemText(index): window.combo_recipe_mode.itemData(index)
            for index in range(window.combo_recipe_mode.count())
        }

        assert "Displacement ramp" not in items
        assert "Cyclic displacement" not in items
        assert "Displacement hold" not in items
        assert "Hsw plateau scan" not in items
        assert items["Elastocaloric effect"] == mini_dma_mod.ELASTOCALORIC_EFFECT
    finally:
        _close_test_window(window)


def test_elastocaloric_recipe_builds_single_strain_jump_with_current_transition(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.ELASTOCALORIC_EFFECT)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.spin_constant_current_start_target.setValue(0.0)
        window.spin_constant_current_end_target.setValue(4.0)
        window.spin_constant_current_start_mA.setValue(50.0)
        window.spin_constant_current_move_speed_mm_s.setValue(5.0)
        window.spin_elastocaloric_stabilize_s.setValue(30.0)
        window.spin_constant_current_hold_s.setValue(6.0)
        window.spin_elastocaloric_release_record_s.setValue(8.0)
        window.spin_constant_current_transition_stress_mpa.setValue(10.0)
        window.spin_constant_current_transition_rate_mA_s.setValue(1.0)
        window.spin_constant_current_transition_settle_s.setValue(1.0)

        window.show()
        qtbot.waitExposed(window)

        steps, summary, interval_ms = window._build_automation_recipe()

        recipe_start = next(index for index, step in enumerate(steps) if step.action == "start_session")
        recipe_steps = steps[recipe_start + 1 :]
        sweep_steps = [step for step in recipe_steps if step.action == "sweep_current"]
        scan_steps = [step for step in recipe_steps if step.action == "mechanical_scan"]
        stabilize_steps = [
            step
            for step in recipe_steps
            if step.action == "settle" and step.note == "temperature_stabilize"
        ]

        assert interval_ms == window._control_interval_ms()
        assert window.spin_constant_current_hold_s.isVisibleTo(window.recipe_stack)
        assert window.spin_constant_current_move_speed_mm_s.isVisibleTo(window.recipe_stack)
        assert window.spin_elastocaloric_stabilize_s.isVisibleTo(window.recipe_stack)
        assert window.spin_elastocaloric_release_record_s.isVisibleTo(window.recipe_stack)
        assert not window.combo_constant_current_step_basis.isVisibleTo(window.recipe_stack)
        assert not window.spin_constant_current_step_size.isVisibleTo(window.recipe_stack)
        assert [(step.current_start_mA, step.current_end_mA) for step in sweep_steps] == pytest.approx([(1.0, 50.0)])
        assert all(step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA for step in sweep_steps)
        assert all(step.target_value == pytest.approx(10.0) for step in sweep_steps)
        assert len(stabilize_steps) == 1
        assert stabilize_steps[0].duration_s == pytest.approx(30.0)
        assert stabilize_steps[0].current_mA == pytest.approx(50.0)
        assert [step.note for step in recipe_steps[:7]] == [
            "transition_seek",
            "transition",
            "transition_settle",
            "start",
            "temperature_stabilize",
            "1:zero",
            "1:up",
        ]
        assert len(scan_steps) == 2
        assert [(step.target_value, step.note) for step in scan_steps] == [(4.0, "1:up"), (0.0, "1:down")]
        assert all(step.basis == mini_dma_mod.HSW_BASIS_STRAIN_PCT for step in scan_steps)
        assert all(step.mechanical_step_basis == mini_dma_mod.HSW_BASIS_STRAIN_PCT for step in scan_steps)
        assert all(step.mechanical_step_value == pytest.approx(4.0) for step in scan_steps)
        assert all(step.mechanical_step_speed_mm_s == pytest.approx(5.0) for step in scan_steps)
        assert [step.duration_s for step in scan_steps] == pytest.approx([6.0, 8.0])
        assert "Started elastocaloric effect recipe" in summary
        assert "one jump" in summary
    finally:
        _close_test_window(window)


def test_iso_current_stress_ramp_recipe_builds_target_ramps_with_transition(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CONSTANT_CURRENT_STRESS_RAMP)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.spin_constant_current_start_target.setValue(0.0)
        window.spin_constant_current_end_target.setValue(400.0)
        window.spin_constant_current_stress_ramp_rate_mpa_s.setValue(5.0)
        window.spin_constant_current_start_mA.setValue(50.0)
        window.spin_constant_current_end_mA.setValue(50.0)
        window.spin_constant_current_step_mA.setValue(10.0)
        window.spin_constant_current_transition_stress_mpa.setValue(10.0)
        window.spin_constant_current_transition_rate_mA_s.setValue(1.0)
        window.spin_constant_current_transition_settle_s.setValue(1.0)

        steps, summary, interval_ms = window._build_automation_recipe()

        recipe_start = next(index for index, step in enumerate(steps) if step.action == "start_session")
        recipe_steps = steps[recipe_start + 1 :]
        sweep_steps = [step for step in recipe_steps if step.action == "sweep_current"]
        ramp_steps = [step for step in recipe_steps if step.action == "ramp_target"]
        zero_steps = [step for step in recipe_steps if step.action == "mark_current_zero"]

        assert interval_ms == window._control_interval_ms()
        assert [(step.current_start_mA, step.current_end_mA) for step in sweep_steps] == pytest.approx([(1.0, 50.0)])
        assert all(step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA for step in sweep_steps)
        assert all(step.target_value == pytest.approx(10.0) for step in sweep_steps)
        assert len(zero_steps) == 1
        assert zero_steps[0].current_mA == pytest.approx(50.0)
        assert [step.note for step in recipe_steps[:6]] == [
            "1:transition_seek",
            "1",
            "1:transition_settle",
            "1:start",
            "1:zero",
            "1:up",
        ]
        assert [(step.target_start_value, step.target_end_value) for step in ramp_steps] == pytest.approx(
            [(0.0, 400.0), (400.0, 0.0)]
        )
        assert all(step.current_mA == pytest.approx(50.0) for step in ramp_steps)
        assert all(step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA for step in ramp_steps)
        assert all(step.target_ramp_rate_value_s == pytest.approx(5.0) for step in ramp_steps)
        assert not any(step.action == "mechanical_scan" for step in recipe_steps)
        assert "Started iso-current stress ramp recipe" in summary
        assert "at 5.0000 MPa/s" in summary
        assert "Current transitions ramp at 1.000 mA/s" in summary
    finally:
        _close_test_window(window)


def test_iso_current_stress_ramp_ui_shows_ramp_rate_not_step_controls(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CONSTANT_CURRENT_STRESS_RAMP)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)

        assert window.spin_constant_current_stress_ramp_rate_mpa_s.isVisibleTo(window.recipe_stack)
        assert not window.combo_constant_current_step_basis.isVisibleTo(window.recipe_stack)
        assert not window.spin_constant_current_step_size.isVisibleTo(window.recipe_stack)
        assert not window.spin_constant_current_hold_s.isVisibleTo(window.recipe_stack)
        assert not window.spin_constant_current_move_speed_mm_s.isVisibleTo(window.recipe_stack)
        assert not window.combo_constant_current_start_basis.isVisibleTo(window.recipe_stack)
        assert "stress ramp" in window.label_recipe_summary.text().casefold()

        strain_mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP)
        window.combo_recipe_mode.setCurrentIndex(strain_mode_index)

        assert not window.spin_constant_current_stress_ramp_rate_mpa_s.isVisibleTo(window.recipe_stack)
        assert window.combo_constant_current_step_basis.isVisibleTo(window.recipe_stack)
        assert window.spin_constant_current_step_size.isVisibleTo(window.recipe_stack)
    finally:
        _close_test_window(window)


def test_iso_current_stress_ramp_requires_supply_for_transition_current(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CONSTANT_CURRENT_STRESS_RAMP)
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.check_motor_supply_power.setChecked(False)
        window.check_continuity_monitor.setChecked(False)

        steps, _summary, _interval_ms = window._build_automation_recipe()

        assert any(step.action == "sweep_current" for step in steps)
        assert window._recipe_requires_supply(steps) is True
    finally:
        _close_test_window(window)


def test_constant_current_zero_mark_records_current_specific_origin(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    records: list[str] = []
    try:
        window._automation_active = True
        window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP
        window._session_active = True
        window._last_move_target_mm = -0.125
        window._last_effective_move_target_mm = -0.125
        window._manual_jog_uses_last_target = True
        window._record_scheduled_recipe_point = lambda step: records.append(step.note) or True  # type: ignore[method-assign]
        step = mini_dma_mod.AutomationStep(
            "mark_current_zero",
            target_value=0.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_mA=40.0,
            note="2:zero",
        )

        assert window._handle_current_zero_mark_step(step) is True

        assert window._constant_current_step_base_position_by_note["2"] == pytest.approx(-0.125)
        assert window._active_constant_current_zero_position_mm == pytest.approx(-0.125)
        assert window._active_constant_current_zero_current_mA == pytest.approx(40.0)
        assert records == ["2:zero"]
    finally:
        _close_test_window(window)


def test_constant_current_measurement_logs_current_relative_zero_coordinates(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP)
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.spin_initial_length.setValue(10.0)
        window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP
        window._automation_active = True
        window._session_active = True
        window.check_positive_motion_is_tension.setChecked(True)
        window._position_reference_mm = 0.0
        window._active_constant_current_zero_position_mm = 0.25
        window._active_constant_current_zero_current_mA = 40.0

        point = window._capture_measurement_point(
            elapsed_s=1.0,
            position_mm=0.4,
            effective_position_mm=0.4,
            raw_load_g=0.0,
            load_g=0.0,
        )

        assert point.current_zero_position_mm == pytest.approx(0.25)
        assert point.current_l0_mm == pytest.approx(10.25)
        assert point.current_relative_position_mm == pytest.approx(0.15)
        assert point.current_relative_strain_pct == pytest.approx(100.0 * 0.15 / 10.25)
        assert "current_zero_position_mm" in mini_dma_mod.MEASUREMENT_CSV_FIELDNAMES
        assert "current_l0_mm" in mini_dma_mod.MEASUREMENT_CSV_FIELDNAMES
        assert "current_relative_strain_pct" in mini_dma_mod.MEASUREMENT_CSV_FIELDNAMES
    finally:
        _close_test_window(window)


def test_measurement_logs_strain_from_raw_position_not_effective_target(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window.spin_initial_length.setValue(10.0)
        window.check_positive_motion_is_tension.setChecked(True)
        window._position_reference_mm = 0.0
        window._active_constant_current_zero_position_mm = 0.25
        window._active_constant_current_zero_current_mA = 50.0

        point = window._capture_measurement_point(
            elapsed_s=1.0,
            position_mm=0.40,
            effective_position_mm=-0.20,
            raw_load_g=0.0,
            load_g=0.0,
        )

        assert point.raw_position_mm == pytest.approx(0.40)
        assert point.position_mm == pytest.approx(0.40)
        assert point.strain_pct == pytest.approx(4.0)
        assert point.current_relative_position_mm == pytest.approx(0.15)
        assert point.current_relative_strain_pct == pytest.approx(100.0 * 0.15 / 10.25)
    finally:
        _close_test_window(window)


def test_constant_current_recipe_commands_at_least_one_milliamp(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP)
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.spin_constant_current_start_mA.setValue(0.0)
        window.spin_constant_current_end_mA.setValue(0.0)
        window.spin_constant_current_step_mA.setValue(0.01)

        steps, _summary, _interval_ms = window._build_automation_recipe()

        sweep_steps = [step for step in steps if step.action == "sweep_current"]
        assert sweep_steps
        assert [(step.current_start_mA, step.current_end_mA) for step in sweep_steps] == pytest.approx([(1.0, 1.0)])
    finally:
        _close_test_window(window)


def test_setup_preload_ramp_starts_from_live_stress_not_zero(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    requested_targets: list[float] = []
    try:
        window.spin_setup_preload_stress_mpa.setValue(20.0)
        window.spin_setup_preload_duration_s.setValue(5.0)
        step = next(
            step
            for step in window._build_pre_measurement_setup_steps()
            if step.action == "ramp_target" and step.note == "setup_preload"
        )
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._active_control_config = window._freeze_control_config()
        window._current_distribution_value = lambda *_args, **_kwargs: 10.0  # type: ignore[method-assign]

        def _record_seek(_basis: str, target_value: float, _tolerance: float) -> bool:
            requested_targets.append(target_value)
            return False

        window._seek_distribution_target = _record_seek  # type: ignore[method-assign]

        assert window._handle_target_ramp_step(step, 1) is False

        assert requested_targets
        assert requested_targets[0] >= 9.5
    finally:
        _close_test_window(window)


def test_stop_session_from_worker_is_queued_to_ui_thread(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    queued: list[object] = []
    try:
        window._session_active = True
        window._is_ui_thread = lambda: False  # type: ignore[method-assign]
        window._call_on_ui_thread_sync = lambda callback: queued.append(callback)  # type: ignore[method-assign]

        window._stop_session()

        assert len(queued) == 1
        assert window._session_active is True
    finally:
        window._session_active = False
        _close_test_window(window)


def test_completed_recipe_cleanup_from_worker_is_queued_to_ui_thread(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    queued: list[object] = []
    try:
        window._automation_active = True
        window._automation_steps = []
        window._automation_index = 0
        window._is_ui_thread = lambda: False  # type: ignore[method-assign]
        window._call_on_ui_thread_sync = lambda callback: queued.append(callback)  # type: ignore[method-assign]

        window._handle_auto_ramp_tick()

        assert len(queued) == 1
        assert window._automation_active is True
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_recipe_current_command_clamps_zero_to_continuity_floor(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def initialize_output(self, *, current_mA: float, reset_on_start: bool) -> None:
            self.commands.append(current_mA)

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(current_mA)

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window._automation_active = True
    try:
        assert window._set_recipe_current_mA(0.0) is True

        assert supply.commands == pytest.approx([1.0])
        assert window._supply_last_setpoint_mA == pytest.approx(1.0)
    finally:
        _close_test_window(window)


def test_recipe_current_command_uses_active_recipe_limit_not_visible_edits(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.1}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.1

        def set_current_limit_mA(self, value: float) -> None:
            raise PermissionError(f"Cannot change CH4 role while it is leased ({value}).")

        def initialize_output(self, *, current_mA: float, reset_on_start: bool) -> None:
            self.commands.append(current_mA)

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(current_mA)

        def disconnect(self) -> None:
            return None

    first_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=20.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=40.0,
        current_ramp_rate_mA_s=0.4,
        note="first_overheating",
    )

    supply = _FakeSupply()
    try:
        window._supply_controller = supply  # type: ignore[assignment]
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window._current_sweep_channel_limit_checked = (4, 40.0)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [first_sweep]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._supply_output_enabled = True

        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(30.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(35.0)

        assert window._set_recipe_current_mA(28.0) is True

        assert supply.commands == pytest.approx([28.0])
        assert window._automation_active is True
        assert window._current_sweep_channel_limit_checked == (4, 40.0)
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_recipe_current_command_accepts_higher_checked_limit_after_runtime_lowering(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.1}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.1

        def set_current_limit_mA(self, value: float) -> None:
            raise PermissionError(f"Cannot change CH4 role while it is leased ({value}).")

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(current_mA)

        def disconnect(self) -> None:
            return None

    lowered_first_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=20.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=35.0,
        current_ramp_rate_mA_s=0.4,
        note="first_overheating",
    )

    supply = _FakeSupply()
    try:
        window._supply_controller = supply  # type: ignore[assignment]
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window._current_sweep_channel_limit_checked = (4, 40.0)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [lowered_first_sweep]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._supply_output_enabled = True

        assert window._set_recipe_current_mA(30.0) is True

        assert supply.commands == pytest.approx([30.0])
        assert window._current_sweep_channel_limit_checked == (4, 40.0)
        assert "Current-sweep channel limit update failed" not in window.log_output.toPlainText()
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_recipe_current_command_retries_when_current_output_readback_is_off(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.commands: list[tuple[str, float | None]] = []
            self.output_checks = 0

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def initialize_output(self, *, current_mA: float, reset_on_start: bool) -> None:
            self.commands.append(("initialize", current_mA))

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(("current", current_mA))

        def output_on(self) -> None:
            self.commands.append(("output_on", None))

        def output_state(self, channel: int | None = None) -> bool:
            self.output_checks += 1
            return self.output_checks >= 2

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window.combo_current_sweep_supply_channel.setCurrentIndex(
        window.combo_current_sweep_supply_channel.findData(4)
    )
    window._automation_active = True

    try:
        assert window._set_recipe_current_mA(10.0) is True

        assert supply.commands == [("initialize", 10.0), ("output_on", None)]
        assert window._supply_output_enabled is True
        assert window._supply_last_setpoint_mA == pytest.approx(10.0)
        assert "output readback is OFF; retrying output enable" in window.log_output.toPlainText()
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_recipe_current_command_uses_active_sweep_limit_before_setting_ch4(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.commands: list[tuple[str, float]] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def set_current_limit_mA(self, current_limit_mA: float) -> None:
            self.commands.append(("limit", current_limit_mA))

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(("current", current_mA))

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window.combo_current_sweep_supply_channel.setCurrentIndex(
        window.combo_current_sweep_supply_channel.findData(4)
    )
    window.spin_supply_manual_current.setValue(1.0)
    window.spin_current_sweep_start_mA.setValue(1.0)
    window.spin_current_sweep_end_mA.setValue(85.0)
    window._supply_output_enabled = True
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._automation_steps = [
        mini_dma_mod.AutomationStep(
            "sweep_current",
            current_start_mA=1.0,
            current_end_mA=85.0,
        )
    ]

    try:
        assert window._set_recipe_current_mA(50.0) is True
        assert window._set_recipe_current_mA(51.0) is True
        window.spin_current_sweep_end_mA.setValue(90.0)
        assert window._set_recipe_current_mA(52.0) is True

        assert [name for name, _value in supply.commands] == [
            "limit",
            "current",
            "current",
            "current",
        ]
        assert [value for _name, value in supply.commands] == pytest.approx([85.0, 50.0, 51.0, 52.0])
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_recipe_current_command_enables_stale_off_current_output(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.commands: list[tuple[str, float | None]] = []
            self.output_checks = 0

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(("current", current_mA))

        def output_on(self) -> None:
            self.commands.append(("output_on", None))

        def output_state(self, channel: int | None = None) -> bool:
            self.output_checks += 1
            return self.output_checks >= 2

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window.combo_current_sweep_supply_channel.setCurrentIndex(
        window.combo_current_sweep_supply_channel.findData(4)
    )
    window._supply_output_enabled = True
    window._automation_active = True

    try:
        assert window._set_recipe_current_mA(20.0) is True

        assert supply.commands == [("current", 20.0), ("output_on", None)]
        assert window._supply_output_enabled is True
        assert window._supply_last_setpoint_mA == pytest.approx(20.0)
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_recipe_current_command_fails_when_current_output_readback_stays_off(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.commands: list[str] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def initialize_output(self, *, current_mA: float, reset_on_start: bool) -> None:
            self.commands.append("initialize")

        def output_on(self) -> None:
            self.commands.append("output_on")

        def output_state(self, channel: int | None = None) -> bool:
            return False

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window.combo_current_sweep_supply_channel.setCurrentIndex(
        window.combo_current_sweep_supply_channel.findData(4)
    )
    window._automation_active = True

    try:
        assert window._set_recipe_current_mA(10.0) is False

        assert supply.commands == ["initialize", "output_on"]
        assert window._supply_output_enabled is False
        assert "current-sweep CH4 output did not report ON" in window.log_output.toPlainText()
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_constant_current_recipe_has_no_max_step_cap_setting(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP)
        window.combo_recipe_mode.setCurrentIndex(mode_index)

        assert not hasattr(window, "spin_constant_current_step_limit")
        labels = [label.text() for label in window.recipe_stack.currentWidget().findChildren(QtWidgets.QLabel)]
        assert "Max steps per leg" not in labels
    finally:
        _close_test_window(window)


def test_iso_current_transition_controls_are_collapsible_and_always_on(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP)
        window.combo_recipe_mode.setCurrentIndex(mode_index)

        assert window.button_constant_current_transition_details.arrowType() == QtCore.Qt.ArrowType.RightArrow
        assert window.constant_current_transition_panel.isHidden() is True
        assert window.check_constant_current_transition_enabled.isHidden() is True
        assert window.check_constant_current_return_to_start.isHidden() is True
        assert window.check_constant_current_transition_hold_on_error.isChecked() is True
        assert "hold on" in window.label_constant_current_transition_summary.text()

        window.button_constant_current_transition_details.setChecked(True)

        assert window.button_constant_current_transition_details.arrowType() == QtCore.Qt.ArrowType.DownArrow
        assert window.constant_current_transition_panel.isHidden() is False
    finally:
        _close_test_window(window)


def test_constant_current_mechanical_scan_uses_fixed_displacement_steps(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    values = iter([0.0, 100.0, 250.0, 300.0, 490.0, 510.0, 510.0])
    records: list[bool] = []

    try:
        window._automation_active = True
        window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP
        window._session_active = True
        window._last_move_target_mm = 0.0
        window._last_effective_move_target_mm = 0.0
        window._current_distribution_value = lambda *_args, **_kwargs: next(values)  # type: ignore[method-assign]
        window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
        window._record_scheduled_recipe_point = lambda _step: records.append(True) or True  # type: ignore[method-assign]
        window._tension_motion_sign = lambda: 1.0  # type: ignore[method-assign]
        step = mini_dma_mod.AutomationStep(
            "mechanical_scan",
            target_value=500.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_mA=20.0,
            mechanical_step_basis=mini_dma_mod.MECHANICAL_STEP_DISPLACEMENT_MM,
            mechanical_step_value=0.01,
            mechanical_step_speed_mm_s=0.05,
            mechanical_step_limit=10,
            duration_s=0.0,
            note="1:up",
        )

        finished = False
        for _ in range(10):
            finished = window._handle_mechanical_scan_step(step, 4)
            if moves:
                window._last_move_target_mm = moves[-1]
                window._manual_jog_uses_last_target = True
            if finished:
                break
        assert finished is True

        assert moves == pytest.approx([0.01, 0.02, 0.03])
        assert len(records) == 3
    finally:
        _close_test_window(window)


def test_constant_current_mechanical_scan_waits_for_fresh_post_move_feedback(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    values = iter([0.0, None, 100.0, 100.0])
    records: list[bool] = []

    try:
        window._automation_active = True
        window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP
        window._session_active = True
        window._last_move_target_mm = 0.0
        window._last_effective_move_target_mm = 0.0
        window._manual_jog_uses_last_target = True
        window._current_distribution_value = lambda *_args, **_kwargs: next(values)  # type: ignore[method-assign]
        window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
        window._record_scheduled_recipe_point = lambda _step: records.append(True) or True  # type: ignore[method-assign]
        window._tension_motion_sign = lambda: 1.0  # type: ignore[method-assign]
        step = mini_dma_mod.AutomationStep(
            "mechanical_scan",
            target_value=50.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_mA=20.0,
            mechanical_step_basis=mini_dma_mod.MECHANICAL_STEP_DISPLACEMENT_MM,
            mechanical_step_value=0.01,
            mechanical_step_speed_mm_s=0.05,
            duration_s=0.0,
            note="1:up",
        )

        assert window._handle_mechanical_scan_step(step, 4) is False
        assert moves == pytest.approx([0.01])
        assert records == []

        assert window._handle_mechanical_scan_step(step, 4) is False
        assert records == []

        assert window._handle_mechanical_scan_step(step, 4) is False
        assert records == [True]
    finally:
        _close_test_window(window)


def test_constant_current_return_scan_clamps_to_mechanical_start(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    values = iter([120.0, 80.0, 20.0, 20.0, 0.0])
    records: list[bool] = []

    try:
        window._automation_active = True
        window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP
        window._session_active = True
        window._last_move_target_mm = 0.03
        window._last_effective_move_target_mm = 0.03
        window._manual_jog_uses_last_target = True
        window._constant_current_step_base_position_by_note["1"] = 0.0
        window._current_distribution_value = lambda *_args, **_kwargs: next(values)  # type: ignore[method-assign]
        window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
        window._record_scheduled_recipe_point = lambda _step: records.append(True) or True  # type: ignore[method-assign]
        window._tension_motion_sign = lambda: 1.0  # type: ignore[method-assign]
        step = mini_dma_mod.AutomationStep(
            "mechanical_scan",
            target_value=0.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_mA=50.0,
            mechanical_step_basis=mini_dma_mod.MECHANICAL_STEP_DISPLACEMENT_MM,
            mechanical_step_value=0.02,
            mechanical_step_speed_mm_s=0.05,
            mechanical_step_limit=10,
            duration_s=0.0,
            note="1:down",
        )

        finished = False
        for _ in range(10):
            finished = window._handle_mechanical_scan_step(step, 8)
            if moves:
                window._last_move_target_mm = moves[-1]
            if finished:
                break
        assert finished is True

        assert moves == pytest.approx([0.01, 0.0])
        assert len(records) == 2
    finally:
        _close_test_window(window)


def test_status_bar_is_hidden_so_run_log_is_not_duplicated(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._log("single visible log")

        assert window.statusBar().isHidden() is True
        assert "single visible log" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_developer_run_log_mirror_writes_log_lines(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    mirror_path = tmp_path / "mini_dma_run_log.txt"

    try:
        window._run_log_mirror_path = mirror_path
        window._run_log_mirror_enabled = True

        window._log("mirror probe")

        assert "mirror probe" in mirror_path.read_text(encoding="utf-8")
    finally:
        _close_test_window(window)


def test_sample_tab_is_streamlined_and_named_sample(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        tab_widget = window._control_scroll_area.widget().findChild(QtWidgets.QTabWidget)
        tab_labels = [tab_widget.tabText(index) for index in range(tab_widget.count())]
        assert tab_labels == ["Recipe", "Sample", "Hardware"]

        tab_widget.setCurrentIndex(1)
        visible_texts = {
            widget.text()
            for widget in tab_widget.widget(1).findChildren((QtWidgets.QLabel, QtWidgets.QCheckBox, QtWidgets.QPushButton))
            if widget.isVisible()
        }
        forbidden = {
            "Gauge length l0",
            "Auto-fill sample name and base filename from the fields above",
            "Apply naming fields now",
            "Zero strain/stress only after preload is reached",
            "Preload threshold",
            "Set current position as gauge zero",
            "Set current Tic position to 0 when the session starts",
            "Start session",
            "Stop session",
            "Record point now",
        }
        assert visible_texts.isdisjoint(forbidden)
    finally:
        _close_test_window(window)


def test_naming_fields_always_autofill_sample_and_filename(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert not hasattr(window, "check_auto_name")
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setText("12/2")
        window.edit_name_condition.setText("calibration")

        assert window.edit_sample_name.text() == "Ni50Fe27Ga23 12/2 calibration"
        assert window.edit_log_name.text() == "Ni50Fe27Ga23 12_2 calibration"
    finally:
        _close_test_window(window)


def test_stale_split_wire_sample_name_updates_from_naming_fields(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_sample_name.setText("Ni50Mn25Ga25 4 2 demo")
        window.edit_name_composition.setText("Ni49Fe26Ga23Co2")
        window.edit_name_wire.setText("3/6")
        window.edit_name_condition.setText("Kosice test")

        assert window.edit_sample_name.text() == "Ni49Fe26Ga23Co2 3/6 Kosice test"
        assert window.label_recipe_sample.text().startswith("Sample: Ni49Fe26Ga23Co2 3/6 Kosice test")
        assert window.edit_log_name.text() == "Ni49Fe26Ga23Co2 3_6 Kosice test"
    finally:
        _close_test_window(window)


def test_saved_sample_fields_and_builder_project_autoimport_diameter(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "d (um)": 19.1,
                                "Imax (mA)": 37.5,
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    settings = _test_settings()
    settings.clear()
    settings.setValue("name_composition", "Ni50Fe27Ga23")
    settings.setValue("name_wire", "12/2")
    settings.setValue("name_condition", "test")
    settings.setValue("builder_project_path", str(project_path))
    settings.setValue("diameter_mm", 0.03)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        assert window.edit_name_composition.text() == "Ni50Fe27Ga23"
        assert window.edit_name_wire.text() == "12/2"
        assert window.edit_name_condition.text() == "test"
        assert window.edit_sample_name.text() == "Ni50Fe27Ga23 12/2 test"
        assert window.edit_log_name.text() == "Ni50Fe27Ga23 12_2 test"
        assert Path(window.edit_project_path.text()) == project_path
        assert window._builder_project_path == project_path
        assert "background" in window.label_project_status.text()
        qtbot.waitUntil(lambda: "Imported" in window.label_project_status.text(), timeout=3000)
        assert window.spin_diameter.value() == pytest.approx(0.0191)
        assert "Imported" in window.label_project_status.text()
        assert "diameter 19.1 um" in window.label_project_status.text()
        assert "#16a34a" in window.spin_diameter.styleSheet()
    finally:
        _close_test_window(window)


def test_fabrication_suggestions_fill_diameter_when_project_has_no_diameter(tmp_path: Path, qtbot) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/3",
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    window = _build_window(tmp_path, qtbot)

    try:
        window._fabrication_records_by_composition = {
            "Ni50Fe27Ga23": [
                mini_dma_mod.FabricationSampleRecord(
                    composition="Ni50Fe27Ga23",
                    draw=12,
                    piece=2,
                    label="12/2",
                    diameter_mm=0.011,
                ),
                mini_dma_mod.FabricationSampleRecord(
                    composition="Ni50Fe27Ga23",
                    draw=12,
                    piece=3,
                    label="12/3",
                    diameter_mm=0.0125,
                ),
            ]
        }
        window._refresh_fabrication_completers()

        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setText("12/3")
        window._sync_auto_name_fields()

        assert window.spin_diameter.value() == pytest.approx(0.0125)
        qtbot.waitUntil(lambda: "no project diameter" in window.label_project_status.text(), timeout=3000)
        assert "no project diameter" in window.label_project_status.text()
        assert "fabrication diameter 12.5 um" in window.label_fabrication_status.text()
        assert "#16a34a" in window.spin_diameter.styleSheet()
        completer_model = window.edit_name_wire.completer().model()
        suggestions = [
            completer_model.data(completer_model.index(row, 0))
            for row in range(completer_model.rowCount())
        ]
        assert suggestions == ["12/2", "12/3"]
    finally:
        _close_test_window(window)


def test_loading_fabrication_folder_indexes_workbooks_without_blocking_ui(
    tmp_path: Path,
    qtbot,
) -> None:
    folder = tmp_path / "fabrication"
    composition_folder = folder / "Ni50Fe27Ga23"
    composition_folder.mkdir(parents=True)
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_fabrication_folder.setText(str(folder))
        started_s = time.monotonic()
        window._load_fabrication_folder_from_ui()
        elapsed_s = time.monotonic() - started_s

        assert elapsed_s < 0.05
        assert window._fabrication_load_active()
        assert window.button_load_fabrication.text() == "Cancel loading"
        qtbot.waitUntil(lambda: not window._fabrication_load_active(), timeout=3000)
        assert "Ni50Fe27Ga23" in window._fabrication_records_by_composition
        assert "composition suggestion" in window.label_fabrication_status.text()
    finally:
        window._cancel_fabrication_folder_load()
        _close_test_window(window)


def test_fabrication_worker_indexes_real_builder_workbooks(qtbot) -> None:
    root = Path("sample_data/database_builder/microwire data/Ni50Fe27Ga23")
    if not root.exists():
        pytest.skip("sample fabrication data is unavailable")
    worker = mini_dma_mod.FabricationSuggestionWorker(root, composition="Ni50Fe27Ga23")
    result: dict[str, object] = {}

    worker.succeeded.connect(
        lambda root_obj, records_obj, file_count, composition_obj: result.update(
            root=root_obj,
            records=records_obj,
            file_count=file_count,
            composition=composition_obj,
        )
    )
    worker.failed.connect(lambda root_obj, message: result.update(error=message))
    worker.cancelled.connect(lambda root_obj: result.update(cancelled=True))
    worker.run()

    assert "error" not in result
    assert "cancelled" not in result
    assert result["root"] == root
    assert result["composition"] == "Ni50Fe27Ga23"
    assert int(result["file_count"]) > 0
    records = result["records"]
    assert isinstance(records, dict)
    assert "Ni50Fe27Ga23" in records
    assert any(record.diameter_mm is not None for record in records["Ni50Fe27Ga23"])


def test_project_diameter_is_preferred_over_fabrication_suggestion(tmp_path: Path, qtbot) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/3",
                                "d (um)": 19.1,
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    window = _build_window(tmp_path, qtbot)

    try:
        window._fabrication_records_by_composition = {
            "Ni50Fe27Ga23": [
                mini_dma_mod.FabricationSampleRecord(
                    composition="Ni50Fe27Ga23",
                    draw=12,
                    piece=3,
                    label="12/3",
                    diameter_mm=0.011,
                )
            ]
        }
        window._refresh_fabrication_completers()
        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setText("12/3")

        window._sync_auto_name_fields()

        qtbot.waitUntil(lambda: window.spin_diameter.value() == pytest.approx(0.0191), timeout=3000)
        assert window.spin_diameter.value() == pytest.approx(0.0191)
        assert "Imported" in window.label_project_status.text()
        assert "diameter 19.1 um" in window.label_project_status.text()
    finally:
        _close_test_window(window)


def test_project_auto_import_skips_condition_only_name_changes(
    tmp_path: Path,
    qtbot,
) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/3",
                                "d (um)": 19.1,
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setText("12/3")
        qtbot.waitUntil(lambda: window.spin_diameter.value() == pytest.approx(0.0191), timeout=3000)
        qtbot.waitUntil(lambda: window._builder_project_import_thread is None, timeout=3000)
        state_key = window._builder_project_last_auto_import_state_key
        assert state_key is not None
        assert "#16a34a" in window.spin_diameter.styleSheet()

        window.edit_name_condition.setText("temperature test")

        assert window.edit_sample_name.text() == "Ni50Fe27Ga23 12/3 temperature test"
        assert window.spin_diameter.value() == pytest.approx(0.0191)
        assert "#16a34a" in window.spin_diameter.styleSheet()
        assert window._builder_project_last_auto_import_state_key == state_key
        assert window._builder_project_import_thread is None
        assert not window._builder_project_import_timer.isActive()
    finally:
        _close_test_window(window)


def test_project_diameter_invalidates_immediately_then_updates_for_changed_microwire(
    tmp_path: Path,
    qtbot,
) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni46Fe27Ga23Cu2Co2",
                                "Microwire": "2/1",
                                "d (um)": 18.2,
                            },
                            {
                                "Composition": "Ni46Fe27Ga23Cu2Co2",
                                "Microwire": "2/7",
                                "d (um)": 14.4,
                            },
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni46Fe27Ga23Cu2Co2")
        window.edit_name_wire.setText("2/1")
        qtbot.waitUntil(lambda: window.spin_diameter.value() == pytest.approx(0.0182), timeout=3000)
        assert "#16a34a" in window.spin_diameter.styleSheet()

        window.edit_name_wire.setText("2/7")

        assert "#dc2626" in window.spin_diameter.styleSheet()
        assert window.spin_diameter.value() == pytest.approx(0.0182)

        qtbot.waitUntil(lambda: window.spin_diameter.value() == pytest.approx(0.0144), timeout=3000)
        assert "#16a34a" in window.spin_diameter.styleSheet()
        assert "diameter 14.4 um" in window.label_project_status.text()
    finally:
        _close_test_window(window)


def test_project_auto_import_retries_after_sample_change_during_background_import(
    tmp_path: Path,
    qtbot,
) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni44Fe27Ga23Cu3Co3",
                                "Microwire": "1/5",
                                "d (um)": 17.6,
                            },
                            {
                                "Composition": "Ni44Fe27Ga23Cu3Co3",
                                "Microwire": "1/6",
                                "d (um)": 16.3,
                            },
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni44Fe27Ga23Cu3Co3")
        window._builder_import_in_progress = True

        window.edit_name_wire.setText("1/6")

        assert window._builder_project_import_retry_pending is True
        assert "border" in window.spin_diameter.styleSheet()

        window._builder_import_in_progress = False
        window._run_pending_builder_project_auto_import_if_needed()

        assert window._builder_project_import_retry_pending is False
        assert window._builder_project_import_timer.isActive()
        window._builder_project_import_timer.stop()
    finally:
        window._builder_import_in_progress = False
        _close_test_window(window)


def test_cached_builder_project_suggestions_are_visible_before_background_import_finishes(
    tmp_path: Path,
    qtbot,
) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/3",
                                "d (um)": 19.1,
                            },
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "d (um)": 18.4,
                            },
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with mini_dma_mod._BUILDER_PROJECT_CACHE_LOCK:
        mini_dma_mod._BUILDER_PROJECT_CACHE.clear()
    mini_dma_mod._read_builder_project_cache_entry(project_path)
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        assert window._builder_project_sample_suggestions == {}

        started = window._start_saved_builder_project_auto_import(project_path, quiet=True)

        assert started is True
        assert window._builder_project_sample_suggestions == {"Ni50Fe27Ga23": ("12/2", "12/3")}
        assert "cached sample suggestions are ready" in window.label_project_status.text()
        completer_model = window.edit_name_wire.completer().model()
        suggestions = [
            completer_model.data(completer_model.index(row, 0))
            for row in range(completer_model.rowCount())
        ]
        assert suggestions == ["12/2", "12/3"]
    finally:
        window._stop_builder_project_import_thread()
        _close_test_window(window)


def test_project_diameter_stays_marked_stale_when_changed_microwire_has_no_match(
    tmp_path: Path,
    qtbot,
) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni46Fe27Ga23Cu2Co2",
                                "Microwire": "2/1",
                                "d (um)": 18.2,
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni46Fe27Ga23Cu2Co2")
        window.edit_name_wire.setText("2/1")
        qtbot.waitUntil(lambda: window.spin_diameter.value() == pytest.approx(0.0182), timeout=3000)
        assert "#16a34a" in window.spin_diameter.styleSheet()

        window.edit_name_wire.setText("2/7")

        assert "border" in window.spin_diameter.styleSheet()
        qtbot.waitUntil(lambda: "no matching sample row" in window.label_project_status.text(), timeout=3000)
        assert "border" in window.spin_diameter.styleSheet()
        assert window.spin_diameter.value() == pytest.approx(0.0182)
    finally:
        _close_test_window(window)


def test_sample_wire_change_from_project_to_fabrication_fallback_is_safe(
    tmp_path: Path,
    qtbot,
) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "10/1",
                                "d (um)": 12.4,
                                "Imax (mA)": 800.0,
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    window = _build_window(tmp_path, qtbot)

    try:
        window._fabrication_records_by_composition = {
            "Ni50Fe27Ga23": [
                mini_dma_mod.FabricationSampleRecord(
                    composition="Ni50Fe27Ga23",
                    draw=10,
                    piece=4,
                    label="10/4",
                    diameter_mm=0.0136,
                )
            ]
        }
        window._refresh_fabrication_completers()
        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setText("10/1")
        qtbot.waitUntil(lambda: window.spin_diameter.value() == pytest.approx(0.0124), timeout=3000)

        assert window.spin_diameter.value() == pytest.approx(0.0124)
        assert window.spin_current_sweep_end_mA.value() == pytest.approx(800.0)
        assert "Imported" in window.label_project_status.text()

        window.edit_name_wire.setText("10/4")
        qtbot.waitUntil(lambda: "no matching sample row" in window.label_project_status.text(), timeout=3000)

        assert window.edit_sample_name.text() == "Ni50Fe27Ga23 10/4"
        assert window.edit_log_name.text() == "Ni50Fe27Ga23 10_4"
        assert "no matching sample row" in window.label_project_status.text()
        assert window.spin_diameter.value() == pytest.approx(0.0136)
        assert "fabrication diameter 13.6 um" in window.label_fabrication_status.text()
    finally:
        _close_test_window(window)


def test_sample_name_auto_import_failure_is_reported_without_crashing(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "10/4",
                                "d (um)": 13.6,
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    window = _build_window(tmp_path, qtbot)

    def fail_apply(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated sample import failure")

    try:
        monkeypatch.setattr(window, "_apply_project_match", fail_apply)
        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setText("10/4")
        qtbot.waitUntil(
            lambda: "Failed to apply saved project sample match" in window.label_project_status.text(),
            timeout=3000,
        )

        assert window.edit_sample_name.text() == "Ni50Fe27Ga23 10/4"
        assert "Failed to apply saved project sample match" in window.label_project_status.text()
        assert window._sync_name_fields_in_progress is False
    finally:
        _close_test_window(window)


def test_microwire_field_ui_typing_reports_bad_fabrication_data_without_crashing(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._fabrication_records_by_composition = {
            "Ni50Fe27Ga23": [
                mini_dma_mod.FabricationSampleRecord(
                    composition="Ni50Fe27Ga23",
                    draw=10,
                    piece=4,
                    label="10/4",
                    diameter_mm="not-a-number",  # type: ignore[arg-type]
                )
            ]
        }
        window._refresh_fabrication_completers()
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setFocus()

        assert window.edit_name_wire.completer() is not None
        qtbot.keyClicks(window.edit_name_wire, "10/4")
        qtbot.wait(20)

        assert window.edit_sample_name.text() == "Ni50Fe27Ga23 10/4"
        assert "Fabrication diameter import failed" in window.label_fabrication_status.text()
        assert "border" in window.spin_diameter.styleSheet()
    finally:
        _close_test_window(window)


def test_fabrication_completer_activation_applies_sample_without_rebuilding_popup(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.show()
        qtbot.wait(20)
        window._fabrication_records_by_composition = {
            "Ni50Fe27Ga23": [
                mini_dma_mod.FabricationSampleRecord(
                    composition="Ni50Fe27Ga23",
                    draw=10,
                    piece=1,
                    label="10/1",
                    diameter_mm=0.0124,
                ),
                mini_dma_mod.FabricationSampleRecord(
                    composition="Ni50Fe27Ga23",
                    draw=10,
                    piece=4,
                    label="10/4",
                    diameter_mm=0.0136,
                ),
            ]
        }
        window._refresh_fabrication_completers()
        composition_completer = window.edit_name_composition.completer()
        wire_completer = window.edit_name_wire.completer()

        assert composition_completer is not None
        assert wire_completer is not None
        popup = wire_completer.popup()
        assert popup is not None

        composition_completer.activated.emit("Ni50Fe27Ga23")
        assert window.edit_name_composition.text() == "Ni50Fe27Ga23"
        assert window.edit_name_wire.completer() is wire_completer

        window.edit_name_wire._show_available_completions()
        qtbot.wait(20)
        assert popup.isVisible()

        wire_completer.activated.emit("10/4")
        qtbot.wait(20)

        assert window.edit_name_wire.text() == "10/4"
        assert window.edit_sample_name.text() == "Ni50Fe27Ga23 10/4"
        assert window.spin_diameter.value() == pytest.approx(0.0136)
        assert "fabrication diameter 13.6 um" in window.label_fabrication_status.text()
        assert window.edit_name_wire.completer() is wire_completer
        assert not popup.isVisible()

        window.edit_name_wire._show_available_completions()
        qtbot.wait(20)
        assert not popup.isVisible()
    finally:
        _close_test_window(window)


def test_fabrication_completer_popup_hides_when_application_deactivates(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.show()
        qtbot.wait(20)
        window._fabrication_records_by_composition = {
            "Ni51Fe25Ga24": [
                mini_dma_mod.FabricationSampleRecord(
                    composition="Ni51Fe25Ga24",
                    draw=1,
                    piece=1,
                    label="1/1",
                    diameter_mm=0.0112,
                )
            ]
        }
        window._refresh_fabrication_completers()
        window.edit_name_composition.setText("Ni51Fe25Ga24")
        wire_completer = window.edit_name_wire.completer()
        assert wire_completer is not None
        popup = wire_completer.popup()
        assert popup is not None

        window.edit_name_wire._show_available_completions()
        qtbot.wait(20)
        assert popup.isVisible()

        app = QtWidgets.QApplication.instance()
        assert app is not None
        app.sendEvent(app, QtCore.QEvent(QtCore.QEvent.Type.ApplicationDeactivate))
        qtbot.wait(20)

        assert not popup.isVisible()
    finally:
        _close_test_window(window)


def test_fabrication_completer_large_dataset_reuses_models_during_quick_wire_changes(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        records = [
            mini_dma_mod.FabricationSampleRecord(
                composition="Ni50Fe27Ga23",
                draw=draw,
                piece=piece,
                label=f"{draw}/{piece}",
                diameter_mm=0.010 + ((draw * 10 + piece) % 100) / 100000.0,
            )
            for draw in range(1, 801)
            for piece in range(1, 6)
        ]
        window._fabrication_records_by_composition = {"Ni50Fe27Ga23": records}
        window._refresh_fabrication_completers()
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        qtbot.wait(20)

        wire_completer = window.edit_name_wire.completer()
        assert wire_completer is not None
        assert wire_completer.model().rowCount() == len(records)

        started_s = time.perf_counter()
        for value in ("10/1", "10/4", "11/1", "11/4", "12/1", "12/4") * 5:
            window.edit_name_wire.setText(value)
        qtbot.wait(20)
        elapsed_s = time.perf_counter() - started_s

        assert elapsed_s < 0.25
        assert window.edit_name_wire.completer() is wire_completer
        assert window.edit_sample_name.text() == "Ni50Fe27Ga23 12/4"
        assert "fabrication diameter" in window.label_fabrication_status.text()
    finally:
        _close_test_window(window)


def test_microwire_field_click_shows_loaded_suggestions_without_rebuilding(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        records = [
            mini_dma_mod.FabricationSampleRecord(
                composition="Ni44Fe27Ga23Cu3Co3",
                draw=1,
                piece=piece,
                label=f"1/{piece}",
                diameter_mm=0.016 + piece / 10000.0,
            )
            for piece in range(1, 8)
        ]
        window._fabrication_records_by_composition = {"Ni44Fe27Ga23Cu3Co3": records}
        window._refresh_fabrication_completers()
        window.edit_name_composition.setText("Ni44Fe27Ga23Cu3Co3")
        window.edit_name_wire.setText("1/5")
        wire_completer = window.edit_name_wire.completer()
        assert wire_completer is not None
        assert wire_completer.model().rowCount() == len(records)

        def fail_rebuild() -> None:
            raise AssertionError("clicking the microwire field must not rebuild suggestion models")

        monkeypatch.setattr(window, "_refresh_fabrication_completers", fail_rebuild)

        started_s = time.perf_counter()
        for _ in range(10):
            window.edit_name_wire._show_available_completions()
        elapsed_s = time.perf_counter() - started_s

        assert elapsed_s < 0.1
        assert window.edit_name_wire.completer() is wire_completer
        assert wire_completer.completionPrefix() == ""
    finally:
        _close_test_window(window)


def test_fabrication_completer_missing_diameter_selection_is_reported_without_exception(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._fabrication_records_by_composition = {
            "Ni50Fe27Ga23": [
                mini_dma_mod.FabricationSampleRecord(
                    composition="Ni50Fe27Ga23",
                    draw=10,
                    piece=4,
                    label="10/4",
                    diameter_mm=None,
                )
            ]
        }
        window._refresh_fabrication_completers()
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        wire_completer = window.edit_name_wire.completer()
        assert wire_completer is not None

        wire_completer.activated.emit("10/4")
        qtbot.wait(20)

        assert window.edit_name_wire.text() == "10/4"
        assert window.edit_sample_name.text() == "Ni50Fe27Ga23 10/4"
        assert window.spin_diameter.value() == pytest.approx(0.03)
        assert "border" in window.spin_diameter.styleSheet()
    finally:
        _close_test_window(window)


def test_microwire_field_ui_typing_reports_project_match_errors_without_crashing(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(json.dumps({"sections": {"microscope": {"rows": []}}}), encoding="utf-8")
    window = _build_window(tmp_path, qtbot)

    def fail_match(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("simulated project match failure")

    try:
        monkeypatch.setattr(window, "_find_project_sample", fail_match)
        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setFocus()

        qtbot.keyClicks(window.edit_name_wire, "10/4")
        qtbot.wait(20)

        assert window.edit_sample_name.text() == "Ni50Fe27Ga23 10/4"
        assert window._sync_name_fields_in_progress is False
    finally:
        _close_test_window(window)


def test_name_typing_debounces_builder_project_auto_import(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(json.dumps({"sections": {"microscope": {"rows": []}}}), encoding="utf-8")
    window = _build_window(tmp_path, qtbot)
    calls: list[dict[str, object]] = []

    def _record_import(**kwargs: object) -> bool:
        calls.append(kwargs)
        return True

    try:
        monkeypatch.setattr(window, "_auto_import_builder_project_if_possible", _record_import)
        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setFocus()

        qtbot.keyClicks(window.edit_name_wire, "10/4")

        assert calls == []
        qtbot.waitUntil(lambda: bool(calls), timeout=1000)
        assert calls[-1] == {
            "update_identity": False,
            "quiet": True,
            "async_load": True,
        }
    finally:
        _close_test_window(window)


def test_builder_project_rows_feed_sample_completers(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._builder_project_sample_suggestions = {
            "Ni44Fe27Ga23Cu3Co3": ("1/2", "1/5", "1/6", "1/7")
        }
        window._refresh_fabrication_completers()

        composition_model = window.edit_name_composition.completer().model()
        compositions = [
            composition_model.data(composition_model.index(row, 0))
            for row in range(composition_model.rowCount())
        ]
        assert "Ni44Fe27Ga23Cu3Co3" in compositions

        window.edit_name_composition.setText("Ni44Fe27Ga23Cu3Co3")
        microwire_model = window.edit_name_wire.completer().model()
        microwires = [
            microwire_model.data(microwire_model.index(row, 0))
            for row in range(microwire_model.rowCount())
        ]
        assert microwires == ["1/2", "1/5", "1/6", "1/7"]
    finally:
        _close_test_window(window)


def test_builder_project_cache_reuses_payload_for_sample_suggestions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "microscope": {
                        "rows": [
                            {"Composition": "Ni44Fe27Ga23Cu3Co3", "Microwire": "1/5"},
                            {"Composition": "Ni44Fe27Ga23Cu3Co3", "Microwire": "1/2"},
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with mini_dma_mod._BUILDER_PROJECT_CACHE_LOCK:
        mini_dma_mod._BUILDER_PROJECT_CACHE.clear()

    original_read_text = Path.read_text
    read_paths: list[Path] = []

    def _read_text_spy(self: Path, *args: object, **kwargs: object) -> str:
        if self == project_path:
            read_paths.append(self)
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text_spy)

    try:
        first = mini_dma_mod._read_builder_project_cache_entry(project_path)
        second = mini_dma_mod._read_builder_project_cache_entry(project_path)

        assert first is second
        assert read_paths == [project_path]
        assert first.suggestions == {"Ni44Fe27Ga23Cu3Co3": ("1/2", "1/5")}
    finally:
        with mini_dma_mod._BUILDER_PROJECT_CACHE_LOCK:
            mini_dma_mod._BUILDER_PROJECT_CACHE.clear()


def test_builder_project_stale_async_suggestions_are_ignored(tmp_path: Path, qtbot) -> None:
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(json.dumps({"sections": {"microscope": {"rows": []}}}), encoding="utf-8")
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni44Fe27Ga23Cu3Co3")
        window.edit_name_wire.setText("1/5")
        current_key = window._project_import_request_key(project_path)
        stale_key = (str(project_path), "Ni50Fe27Ga23", "12/2", "")
        window._builder_project_path = project_path
        window._builder_project_import_request_key = current_key
        window._builder_project_sample_suggestions = {"Existing": ("9/9",)}

        window._handle_builder_project_suggestions(
            project_path,
            stale_key,
            {"Ni50Fe27Ga23": ("12/2",)},
        )

        assert window._builder_project_sample_suggestions == {"Existing": ("9/9",)}

        window._handle_builder_project_suggestions(
            project_path,
            current_key,
            {"Ni44Fe27Ga23Cu3Co3": ("1/5", "1/2")},
        )

        assert window._builder_project_sample_suggestions == {
            "Ni44Fe27Ga23Cu3Co3": ("1/5", "1/2")
        }
    finally:
        _close_test_window(window)


def test_stopping_builder_project_import_clears_retry_state(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeThread:
        def __init__(self) -> None:
            self.quit_called = False
            self.wait_timeout: int | None = None

        def quit(self) -> None:
            self.quit_called = True

        def wait(self, timeout_ms: int) -> None:
            self.wait_timeout = timeout_ms

    thread = _FakeThread()

    try:
        window._builder_project_import_thread = thread  # type: ignore[assignment]
        window._builder_project_import_worker = object()  # type: ignore[assignment]
        window._builder_project_import_request_key = ("project.pydpj", "Ni50Fe27Ga23", "12/2", "")
        window._builder_import_in_progress = True
        window._builder_project_import_retry_pending = True

        window._stop_builder_project_import_thread()

        assert window._builder_project_import_thread is None
        assert window._builder_project_import_worker is None
        assert window._builder_project_import_request_key is None
        assert window._builder_import_in_progress is False
        assert window._builder_project_import_retry_pending is False
        assert thread.quit_called is True
        assert thread.wait_timeout == 1500
    finally:
        _close_test_window(window)


def test_failed_fabrication_composition_load_is_not_retried_forever(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    root = tmp_path / "fabrication"
    root.mkdir()
    started: list[tuple[Path, str | None]] = []
    original_start = window._start_fabrication_folder_load

    try:
        window._fabrication_folder_path = root
        window._builder_project_sample_suggestions = {
            "Ni44Fe27Ga23Cu3Co3": ("1/1",)
        }
        window._refresh_fabrication_completers()
        with QtCore.QSignalBlocker(window.edit_name_composition):
            window.edit_name_composition.setText("Ni44Fe27Ga23Cu3Co3")

        def _capture_start(path: Path, *, composition: str | None = None) -> None:
            started.append((path, composition))

        window._start_fabrication_folder_load = _capture_start  # type: ignore[method-assign]
        window._ensure_fabrication_composition_loaded()

        assert started == [(root, "Ni44Fe27Ga23Cu3Co3")]

        window._fabrication_loading_composition = "Ni44Fe27Ga23Cu3Co3"
        window._handle_fabrication_load_failure(root, "No fabrication folder matched composition Ni44Fe27Ga23Cu3Co3.")
        window._finish_fabrication_thread(QtCore.QThread(), mini_dma_mod.FabricationSuggestionWorker(root))
        started.clear()

        window._start_fabrication_folder_load = _capture_start  # type: ignore[method-assign]
        window._ensure_fabrication_composition_loaded()

        normalized = mini_dma_mod._normalized_token("Ni44Fe27Ga23Cu3Co3")
        assert normalized
        assert started == []
        assert normalized in window._fabrication_loaded_compositions
    finally:
        window._start_fabrication_folder_load = original_start  # type: ignore[method-assign]
        _close_test_window(window)


def test_wire_diameter_displays_micrometers_while_storing_mm(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_diameter.setValue(0.0149)

        assert window.spin_diameter.value() == pytest.approx(0.0149)
        assert window.spin_diameter.suffix().strip() == "um"
        assert "14.9" in window.spin_diameter.text()
    finally:
        _close_test_window(window)


def test_project_row_uses_show_annealing_instead_of_manual_import_button(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        button_labels = {button.text() for button in window.findChildren(QtWidgets.QPushButton)}

        assert "Show annealing" in button_labels
        assert "Import sample info" not in button_labels
    finally:
        _close_test_window(window)


def test_project_annealing_preview_loads_sources_for_current_sample(tmp_path: Path, qtbot) -> None:
    annealing_path = tmp_path / "Ni50Fe27Ga23 12_3 1000mA.txt"
    annealing_path.write_text(
        "\n".join(
            [
                "0.001\t0.18\t180",
                "0.002\t0.39\t195",
                "0.003\t0.63\t210",
            ]
        ),
        encoding="utf-8",
    )
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "sections": {
                    "annealing": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/3",
                                "_sources": [str(annealing_path)],
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_project_path.setText(str(project_path))
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setText("12/3")
        payload = window._read_builder_project_payload(project_path)
        candidates = window._extract_project_annealing_candidates(payload)
        candidate_index = window._choose_project_annealing_candidate(candidates)

        assert candidate_index == 0
        series, missing_sources, failed_sources = window._load_annealing_preview_series(
            project_path=project_path,
            sources=candidates[candidate_index]["sources"],
        )

        assert missing_sources == []
        assert failed_sources == []
        assert len(series) == 1
        assert series[0]["setpoint_mA"] == pytest.approx(1000.0)
        assert list(series[0]["currents"]) == pytest.approx([1.0, 2.0, 3.0])
        assert list(series[0]["resistances"]) == pytest.approx([180.0, 195.0, 210.0])
    finally:
        _close_test_window(window)


def test_annealing_preview_dialog_stacks_each_graph_in_scroll_area(qtbot) -> None:
    if mini_dma_mod.FigureCanvas is None:
        pytest.skip("Matplotlib Qt backend is unavailable")

    dialog = mini_dma_mod.AnnealingPreviewDialog(
        None,
        "Ni50Fe27Ga23 12/3",
        [
            {
                "label": "100 mA",
                "frame": pd.DataFrame(
                    {
                        "I_mA": [1.0, 2.0, 3.0, 2.0, 1.0],
                        "R_Ohm": [180.0, 190.0, 205.0, 198.0, 185.0],
                    }
                ),
            },
            {
                "label": "1000 mA",
                "frame": pd.DataFrame(
                    {
                        "I_mA": [1.0, 2.0, 3.0, 2.0, 1.0],
                        "R_Ohm": [210.0, 225.0, 260.0, 245.0, 220.0],
                    }
                ),
            },
        ],
    )
    qtbot.addWidget(dialog)

    try:
        assert dialog.findChild(QtWidgets.QScrollArea) is not None
        qtbot.waitUntil(
            lambda: len(dialog.findChildren(mini_dma_mod.FigureCanvas)) == 2,
            timeout=5000,
        )
        assert len(dialog.findChildren(mini_dma_mod.FigureCanvas)) == 2
        first_canvas = dialog.findChildren(mini_dma_mod.FigureCanvas)[0]
        first_axes = first_canvas.figure.axes[0]
        assert first_axes.get_xlabel() == "Current [mA]"
        assert first_axes.get_ylabel() == "Resistance [\u03a9]"
        assert [text.get_text() for text in first_axes.get_legend().get_texts()] == [
            "Increasing 1",
            "Decreasing 1",
        ]
        dialog.resize(700, 520)
        dialog.show()
        qtbot.wait(50)
        scroll_bar = dialog.findChild(QtWidgets.QScrollArea).verticalScrollBar()
        assert scroll_bar.maximum() > 0
        scroll_bar.setValue(0)
        assert dialog._scroll_preview_by_wheel_delta(-120)
        assert scroll_bar.value() > 0
    finally:
        dialog.close()


def test_wire_diameter_is_marked_until_imported_but_manual_edits_still_work(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._mark_diameter_imported(False)
        assert "border" in window.spin_diameter.styleSheet()

        window.spin_diameter.setValue(0.0191)

        assert window.spin_diameter.value() == pytest.approx(0.0191)
        assert "border" in window.spin_diameter.styleSheet()
    finally:
        _close_test_window(window)


def test_recipe_header_and_equivalent_labels_show_diameter_load_and_stress(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.resize(760, 980)
        window.show()
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setText("12/2")
        window.spin_diameter.setValue(0.03)
        window.spin_setup_preload_stress_mpa.setValue(10.0)
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.spin_current_sweep_target_start.setValue(10.0)
        window.spin_current_sweep_target_ramp_rate.setValue(1.0)
        window.spin_current_sweep_step_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(50.0)
        window._update_recipe_mode_ui()

        assert "Sample: Ni50Fe27Ga23 12/2" in window.label_recipe_sample.text()
        assert "diameter 30 um" in window.label_recipe_sample.text()
        assert window.label_current_sweep_targets_section.text() == "Stress targets"
        assert "0.721 g" in window.label_setup_preload_stress_equiv.text()
        assert "0.072 g/s" in window.label_setup_preload_ramp_equiv.text()
        assert window.spin_setup_zero_tolerance_g.isHidden() is True
        assert "0.721 g" in window.label_current_target_start_equiv.text()
        assert "0.072 g/s" in window.label_current_target_ramp_equiv.text()
        assert "1.442 g" in window.label_current_first_overheating_target_equiv.text()
        assert window.spin_current_sweep_step_mA.singleStep() == pytest.approx(0.2)
        assert window.spin_current_sweep_target_start.width() == window.spin_current_sweep_target_end.width()
        assert window.spin_current_sweep_target_end.width() == window.spin_current_sweep_step_mA.width()
        assert window.spin_current_sweep_target_start.width() <= 260
        assert window.label_current_target_start_equiv.wordWrap() is False
        assert window.label_current_end_density.wordWrap() is False
        assert window.label_current_target_start_equiv.width() >= window.label_current_target_start_equiv.sizeHint().width()
        assert window.label_current_end_density.width() >= window.label_current_end_density.sizeHint().width()
        assert "70.7 A/mm<sup>2</sup>" in window.label_current_end_density.text()
        assert "1.41 A/mm<sup>2</sup>/s" in window.label_current_rate_density.text()
        assert window.label_current_end_density.textFormat() == QtCore.Qt.TextFormat.RichText
        assert window.label_current_rate_density.textFormat() == QtCore.Qt.TextFormat.RichText
        assert "palette(mid)" not in window.label_current_target_start_equiv.styleSheet()
        qtbot.wait(50)
        single_line_height = window.fontMetrics().lineSpacing() * 1.8
        for label_text in ("Stress", "Ramp rate"):
            labels = [
                label
                for label in window.findChildren(QtWidgets.QLabel)
                if label.text() == label_text and label.isVisible()
            ]
            assert labels
            assert all(label.wordWrap() is False for label in labels)
            assert max(label.height() for label in labels) <= single_line_height * 1.25
        assert window.label_current_rate_density.height() <= single_line_height

        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_LOAD)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        assert window.label_current_sweep_targets_section.text() == "Load targets"
        window.spin_current_sweep_target_start.setValue(
            mini_dma_mod.load_g_from_stress_mpa(10.0, window.spin_diameter.value())
        )
        window._update_recipe_mode_ui()

        assert window.label_current_target_start_equiv.text().endswith("MPa")
        assert float(window.label_current_target_start_equiv.text().split()[0]) == pytest.approx(10.0, rel=2e-4)
    finally:
        _close_test_window(window)


@pytest.mark.parametrize(
    ("density", "expected"),
    [
        (800.0, "800 A/mm<sup>2</sup>"),
        (750.0, "750 A/mm<sup>2</sup>"),
        (80.0, "80 A/mm<sup>2</sup>"),
        (75.0, "75 A/mm<sup>2</sup>"),
        (8.0, "8 A/mm<sup>2</sup>"),
    ],
)
def test_current_density_equivalent_preserves_significant_zeros(
    tmp_path: Path,
    qtbot,
    density: float,
    expected: str,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_diameter.setValue(0.03)
        area_mm2 = math.pi * (window.spin_diameter.value() / 2.0) ** 2
        current_mA = density * area_mm2 * 1000.0

        assert window._current_density_text(current_mA) == expected
    finally:
        _close_test_window(window)


def test_recipe_setup_panel_is_collapsible_and_can_disable_setup(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_setup_preload_stress_mpa.setValue(20.0)
        window.spin_setup_preload_duration_s.setValue(5.0)
        window._update_recipe_mode_ui()

        assert window.check_pre_measurement_setup_enabled.isChecked()
        assert window.check_pre_measurement_setup_enabled.parent() is window.setup_details_panel
        assert window.setup_details_panel.isVisible() is False
        assert "20 MPa" in window.label_setup_summary.text()
        assert "5 s" in window.label_setup_summary.text()

        steps = window._build_automation_recipe()[0]
        assert steps[0].action == "starting_length_prompt"

        window.check_pre_measurement_setup_enabled.setChecked(False)
        window._update_recipe_mode_ui()

        assert window._pre_measurement_setup_enabled() is False
        assert "Off" in window.label_setup_summary.text()
        steps = window._build_automation_recipe()[0]
        assert steps[0].action != "starting_length_prompt"
    finally:
        _close_test_window(window)


def test_recipe_file_controls_are_hidden_until_enabled_and_track_status(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    recipe_path = tmp_path / "recipe.recipe.json"

    try:
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)

        assert window.action_show_recipe_file_controls.isChecked() is False
        assert window.recipe_file_controls_widget.isHidden() is True
        assert window.label_recipe_file_status.isHidden() is True

        window.action_show_recipe_file_controls.setChecked(True)

        assert window.recipe_file_controls_widget.isHidden() is False
        assert window.label_recipe_file_status.isHidden() is False
        assert "Unsaved" in window.label_recipe_file_status.text()

        window._save_recipe_to_path(recipe_path)

        assert recipe_path.name in window.label_recipe_file_status.text()
        assert "Saved" in window.label_recipe_file_status.text()
        assert "#16a34a" in window.label_recipe_file_status.styleSheet()

        window.spin_current_sweep_end_mA.setValue(window.spin_current_sweep_end_mA.value() + 1.0)
        window._update_recipe_mode_ui()

        assert "Unsaved changes" in window.label_recipe_file_status.text()
        assert "#dc2626" in window.label_recipe_file_status.styleSheet()
    finally:
        _close_test_window(window)


def test_recipe_advanced_panels_restore_defaults(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_setup_preload_stress_mpa.setValue(33.0)
        window.spin_setup_preload_duration_s.setValue(12.0)
        window.spin_setup_slack_speed_strain_pct_s.setValue(2.0)
        window.spin_setup_slack_step_cap_stress_mpa.setValue(75.0)
        window.spin_setup_preload_stable_s.setValue(8.0)
        window.check_pre_measurement_setup_enabled.setChecked(False)

        window.button_restore_setup_defaults.click()

        assert window.check_pre_measurement_setup_enabled.isChecked() is True
        assert window.spin_setup_preload_stress_mpa.value() == pytest.approx(20.0)
        assert window.spin_setup_preload_duration_s.value() == pytest.approx(
            mini_dma_mod.SETUP_PRELOAD_DEFAULT_DURATION_S
        )
        assert window.spin_setup_slack_speed_strain_pct_s.value() == pytest.approx(
            mini_dma_mod.SETUP_SLACK_DEFAULT_STRAIN_RATE_PCT_S
        )
        assert window.spin_setup_slack_step_cap_stress_mpa.value() == pytest.approx(
            mini_dma_mod.SETUP_PRELOAD_MAX_SLACK_STEP_STRESS_MPA
        )
        assert window.spin_setup_preload_stable_s.value() == pytest.approx(3.0)

        window.spin_current_sweep_target_speed_mm_s.setValue(1.0)
        window.spin_current_sweep_max_correction_stress_mpa.setValue(123.0)
        window.spin_current_sweep_hold_filter_window_s.setValue(9.0)

        window.button_restore_current_sweep_advanced_defaults.click()

        assert window.spin_current_sweep_target_speed_mm_s.value() == pytest.approx(
            mini_dma_mod.SERVO_CURRENT_SWEEP_MAX_STAGE_SPEED_MM_S
        )
        assert window.spin_current_sweep_max_correction_stress_mpa.value() == pytest.approx(
            mini_dma_mod.SERVO_CURRENT_SWEEP_MAX_CORRECTION_STRESS_MPA
        )
        assert window.spin_current_sweep_hold_filter_window_s.value() == pytest.approx(
            mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_FILTER_WINDOW_S
        )

        window.spin_motion_speed_mm_s.setValue(2.0)
        window.spin_jog_mm.setValue(0.5)
        window.spin_setup_return_duration_s.setValue(12.0)

        window.button_restore_manual_action_defaults.click()

        assert window.spin_motion_speed_mm_s.value() == pytest.approx(1.0)
        assert window.spin_jog_mm.value() == pytest.approx(0.1)
        assert window.spin_setup_return_duration_s.value() == pytest.approx(
            mini_dma_mod.SETUP_RETURN_DEFAULT_DURATION_S
        )
    finally:
        _close_test_window(window)


def test_setup_preload_target_ramp_uses_global_stage_speed(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_motion_speed_mm_s.setValue(1.25)
        window.spin_calibration_speed_mm_s.setValue(0.05)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CALIBRATION
        window._set_automation_context(phase="target_ramp", basis=mini_dma_mod.HSW_BASIS_STRESS_MPA)
        assert window._motion_speed_for_current_context(manual_jog=False) == pytest.approx(1.25)
    finally:
        _close_test_window(window)


def test_setup_preload_correction_step_uses_global_speed_interval(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_steps_per_mm.setValue(10000.0)
        window.spin_motion_speed_mm_s.setValue(1.0)
        window.spin_calibration_preload_nudge_mm.setValue(0.01)
        window._automation_interval_ms = 250
        window._automation_active = True
        window._automation_name = mini_dma_mod.CALIBRATION
        window._set_automation_context(
            phase="target_ramp",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            note="setup_preload",
        )

        assert window._seek_step_mm(error_value=5.0, tolerance=0.25) == pytest.approx(0.25)
    finally:
        _close_test_window(window)


def test_zero_current_plot_channels_hide_current_and_resistance(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    zero_point = mini_dma_mod.MeasurementPoint(
        elapsed_s=0.0,
        timestamp_utc="2026-05-07 10:00:00",
        raw_position_mm=0.0,
        position_mm=0.0,
        raw_load_g=21.16,
        load_g=0.0,
        preload_state="disabled",
        strain_pct=0.0,
        stress_mpa=0.0,
        current_set_mA=0.0,
        current_measured_mA=0.0,
        voltage_V=0.0,
        resistance_ohm=0.0,
        power_W=0.0,
        automation_phase="start",
        automation_basis=None,
        automation_target_value=None,
        plateau_index=None,
        plateau_label=None,
    )
    live_point = dataclasses.replace(
        zero_point,
        elapsed_s=1.0,
        current_set_mA=1.0,
        current_measured_mA=1.0,
        voltage_V=0.1,
        resistance_ohm=100.0,
        power_W=0.0001,
    )

    try:
        current_channel = window._plot_channel("current_measured_mA")
        resistance_channel = window._plot_channel("resistance_ohm")

        assert current_channel is not None
        assert resistance_channel is not None
        assert current_channel.getter(zero_point) is None
        assert resistance_channel.getter(zero_point) is None
        assert current_channel.getter(live_point) == pytest.approx(1.0)
        assert resistance_channel.getter(live_point) == pytest.approx(100.0)
    finally:
        _close_test_window(window)


def test_output_folder_open_button_opens_current_log_dir(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    opened: list[str] = []

    def _capture_open(url: QtCore.QUrl) -> bool:
        opened.append(url.toLocalFile())
        return True

    monkeypatch.setattr(mini_dma_mod.QtGui.QDesktopServices, "openUrl", _capture_open)
    window.edit_log_dir.setText(str(tmp_path / "new-output"))

    try:
        window._open_log_dir()

        assert [Path(path) for path in opened] == [tmp_path / "new-output"]
        assert (tmp_path / "new-output").is_dir()
    finally:
        _close_test_window(window)


def test_setup_preload_slack_takeup_uses_slack_strain_speed(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.target_steps: int | None = None
            self.max_speed: int | None = None

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.target_steps = position_steps
            self.max_speed = max_speed

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.17)
    window.spin_steps_per_mm.setValue(10000.0)
    window.spin_diameter.setValue(0.03)
    window.spin_initial_length.setValue(20.0)
    window.spin_scale_interval.setValue(250)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window.spin_setup_slack_speed_strain_pct_s.setValue(1.0)
    window.spin_setup_preload_duration_s.setValue(10.0)
    window.spin_calibration_preload_nudge_mm.setValue(0.01)
    window.spin_setup_preload_tolerance_mpa.setValue(0.25)
    window._automation_interval_ms = 50
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        note="setup_preload",
    )
    calibrated_stiffness = mini_dma_mod.load_g_from_stress_mpa(10.0, window.spin_diameter.value())
    assert calibrated_stiffness is not None
    window._calibrated_stiffness_g_per_mm = calibrated_stiffness
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._current_position_mm = 1.0
    window._current_position_steps = 10000
    window._last_move_target_mm = 1.0
    window._latest_scale_value_g = 21.17
    window._latest_scale_timestamp = time.time()

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=10.0,
            tolerance=0.25,
        )
        _wait_for_tic_commands(window)

        assert reached is False
        assert controller.target_steps == 9500
        assert controller.max_speed == 20_000_000
    finally:
        _close_test_window(window)


def test_setup_preload_slack_takeup_caps_single_step_by_stiffness_prior(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        window._last_move_target_mm = target_mm
        window._last_effective_move_target_mm = kwargs.get("effective_position_mm", target_mm)  # type: ignore[assignment]
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.16)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.767)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window.spin_setup_slack_speed_strain_pct_s.setValue(1.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        231.692884,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._current_position_mm = 7.015
    window._effective_position_mm = 7.015
    window._last_move_target_mm = 7.015
    window._last_effective_move_target_mm = 7.015
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        note="setup_preload",
    )
    window._latest_scale_value_g = 21.16
    window._latest_scale_timestamp = time.time()

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=window._auto_requested_tolerance_for_basis(mini_dma_mod.HSW_BASIS_STRESS_MPA),
        )

        assert reached is False
        assert moves
        _, effective_target_mm = moves[-1]
        assert effective_target_mm is not None
        assert abs(effective_target_mm - 7.015) <= (50.0 / 231.692884) + 1e-9
    finally:
        _close_test_window(window)


def test_setup_preload_waits_for_post_move_feedback_before_next_correction(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.17)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_diameter.setValue(0.0137)
    window.spin_scale_interval.setValue(250)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window._calibrated_stiffness_g_per_mm = 1.0
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        note="setup_preload",
    )
    sample_time_s = time.time()
    window._latest_scale_value_g = 21.17
    window._latest_scale_timestamp = sample_time_s
    window._last_motion_command_time_s = sample_time_s - 0.05
    window._last_motion_expected_complete_time_s = sample_time_s + 5.0
    window._last_move_target_mm = 7.6725
    window._last_effective_move_target_mm = 7.6725
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = sample_time_s - 0.3

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=window._auto_requested_tolerance_for_basis(mini_dma_mod.HSW_BASIS_STRESS_MPA),
        )

        assert reached is False
        assert moves == []
        assert "post-move scale feedback" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_setup_preload_above_target_relaxes_instead_of_stopping(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.17)
    window.spin_diameter.setValue(0.0137)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        note="setup_preload",
    )
    overload_g = mini_dma_mod.load_g_from_stress_mpa(100.0, window.spin_diameter.value())
    assert overload_g is not None
    window._latest_scale_value_g = 21.17 - overload_g
    window._latest_scale_timestamp = time.time()

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=window._auto_requested_tolerance_for_basis(mini_dma_mod.HSW_BASIS_STRESS_MPA),
        )

        assert reached is False
        assert moves
        assert window._automation_active is True
        assert "overload" not in window.log_output.toPlainText().lower()
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_setup_preload_above_target_uses_ramp_cap_without_cruise(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    commands: list[dict[str, object]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        commands.append({"target_mm": target_mm, **kwargs})
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.17)
    window.spin_diameter.setValue(0.0137)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window.spin_setup_preload_duration_s.setValue(20.0)
    window._calibrated_stiffness_g_per_mm = 22.7
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        note="setup_preload",
    )
    overload_g = mini_dma_mod.load_g_from_stress_mpa(170.0, window.spin_diameter.value())
    assert overload_g is not None
    window._latest_scale_value_g = 21.17 - overload_g
    window._latest_scale_timestamp = time.time()

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=window._auto_requested_tolerance_for_basis(mini_dma_mod.HSW_BASIS_STRESS_MPA),
        )

        assert reached is False
        assert commands
        sensitivity = mini_dma_mod.stress_mpa_from_load_g(22.7, window.spin_diameter.value())
        assert sensitivity is not None
        expected_cap_mm_s = max(window._minimum_held_speed_mm_s(), ((170.0 - 20.0) / 20.0) / sensitivity)
        assert commands[0]["speed_mm_s"] == pytest.approx(expected_cap_mm_s)
        assert commands[0]["chain_from_last_target"] is False
        assert abs(float(commands[0]["target_mm"]) - window._current_position_mm) >= window._motor_step_mm()
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_setup_preload_relaxation_waits_for_post_move_feedback(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.17)
    window.spin_diameter.setValue(0.0137)
    window.spin_steps_per_mm.setValue(800.0)
    window._calibrated_stiffness_g_per_mm = 22.7
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        note="setup_preload",
    )
    overload_g = mini_dma_mod.load_g_from_stress_mpa(100.0, window.spin_diameter.value())
    assert overload_g is not None
    sample_time_s = time.time()
    window._latest_scale_value_g = 21.17 - overload_g
    window._latest_scale_timestamp = sample_time_s
    window._last_motion_command_time_s = sample_time_s - 0.05
    window._last_motion_expected_complete_time_s = sample_time_s + 5.0
    window._last_move_target_mm = 7.6725
    window._last_effective_move_target_mm = 7.6725

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=window._auto_requested_tolerance_for_basis(mini_dma_mod.HSW_BASIS_STRESS_MPA),
        )

        assert reached is False
        assert moves == []
        assert "post-move scale feedback" in window.log_output.toPlainText()
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_setup_return_zero_keeps_initial_time_based_unload_speed(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.17)
    window.spin_setup_return_duration_s.setValue(5.0)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window._calibrated_stiffness_g_per_mm = 22.7
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="seek",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=0.0,
        note="setup_return_zero",
    )

    try:
        first_speed = window._setup_return_zero_speed_mm_s(mini_dma_mod.HSW_BASIS_LOAD_G, 1.5)
        second_speed = window._setup_return_zero_speed_mm_s(mini_dma_mod.HSW_BASIS_LOAD_G, 0.3)

        assert first_speed == pytest.approx((1.5 / 22.7) / 5.0)
        assert second_speed == pytest.approx(first_speed)
    finally:
        _close_test_window(window)


def test_setup_preload_leaves_slack_mode_after_load_response(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    commands: list[dict[str, object]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        commands.append({"target_mm": target_mm, **kwargs})
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.17)
    window.spin_diameter.setValue(0.03)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(80.0)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window.spin_setup_slack_speed_strain_pct_s.setValue(1.0)
    window.spin_setup_preload_duration_s.setValue(20.0)
    window._calibrated_stiffness_g_per_mm = 100.0
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        note="setup_preload",
    )
    response_g = mini_dma_mod.load_g_from_stress_mpa(5.0, window.spin_diameter.value())
    assert response_g is not None
    window._latest_scale_value_g = 21.17 - response_g
    window._latest_scale_timestamp = time.time()

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=window._auto_requested_tolerance_for_basis(mini_dma_mod.HSW_BASIS_STRESS_MPA),
        )

        assert reached is False
        assert commands
        commands.clear()
        window._latest_scale_value_g = 21.17
        window._latest_scale_timestamp = time.time() + 1.0

        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=window._auto_requested_tolerance_for_basis(mini_dma_mod.HSW_BASIS_STRESS_MPA),
        )

        assert reached is False
        assert commands
        sensitivity = mini_dma_mod.stress_mpa_from_load_g(100.0, window.spin_diameter.value())
        assert sensitivity is not None
        expected_cap_mm_s = max(window._minimum_held_speed_mm_s(), (20.0 / 20.0) / sensitivity)
        assert commands[0]["speed_mm_s"] == pytest.approx(expected_cap_mm_s)
        assert commands[0]["speed_mm_s"] < 0.8
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_setup_preload_relaxes_after_first_contact_jump_instead_of_accepting_backlash_hold(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    commands: list[dict[str, object]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        commands.append({"target_mm": target_mm, **kwargs})
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.16)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(55.0)
    window.spin_backlash_mm.setValue(0.02)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window.spin_setup_preload_duration_s.setValue(10.0)
    window._calibrated_stiffness_g_per_mm = 22.7
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        note="setup_preload",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
    window._setup_preload_engaged_seek_keys.add(seek_key)
    window._seek_last_error_by_key[seek_key] = 14.0
    window._seek_last_value_by_key[seek_key] = 6.0
    window._seek_last_effective_position_by_key[seek_key] = 3.21875
    window._seek_live_stiffness_by_key[seek_key] = 10000.0
    window._seek_live_stiffness_g_per_mm = 10000.0
    window._current_position_mm = 6.9775
    window._effective_position_mm = 6.9775
    window._last_effective_move_target_mm = 6.9775
    window._last_move_target_mm = 6.9775
    window._last_move_direction = 1.0
    current_load_g = mini_dma_mod.load_g_from_stress_mpa(26.7, window.spin_diameter.value())
    assert current_load_g is not None
    window._latest_scale_value_g = 21.16 - current_load_g
    window._latest_scale_timestamp = time.time()

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=window._auto_requested_tolerance_for_basis(mini_dma_mod.HSW_BASIS_STRESS_MPA),
        )

        assert reached is False
        assert commands
        assert commands[0]["target_mm"] > 6.9775
        assert "backlash-limited tolerance" not in window.log_output.toPlainText()
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_setup_preload_first_contact_jump_does_not_seed_live_stiffness(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.16)
    window.spin_diameter.setValue(0.0191)
    window.spin_initial_length.setValue(55.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        note="setup_preload",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
    window._current_position_mm = 7.10
    window._effective_position_mm = 7.10
    window._last_effective_move_target_mm = 7.10
    window._seek_last_value_by_key[seek_key] = 0.0
    window._seek_last_effective_position_by_key[seek_key] = 3.0
    window._seek_last_stiffness_value_by_basis[mini_dma_mod.HSW_BASIS_STRESS_MPA] = 0.0
    window._seek_last_stiffness_position_by_basis[mini_dma_mod.HSW_BASIS_STRESS_MPA] = 3.0

    try:
        window._update_live_seek_stiffness(seek_key, mini_dma_mod.HSW_BASIS_STRESS_MPA, 26.7)

        assert seek_key not in window._seek_live_stiffness_by_key
        assert window._seek_live_stiffness_g_per_mm is None
        assert window._seek_last_value_by_key[seek_key] == pytest.approx(26.7)
        assert window._seek_last_effective_position_by_key[seek_key] == pytest.approx(
            window._current_effective_tensile_position_mm()
        )
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_calibration_target_acceptance_caps_inflated_live_stiffness(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)
    window._automation_name = mini_dma_mod.CALIBRATION
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_LOAD_G, 0.75)
    window._seek_live_stiffness_by_key[seek_key] = 200.0

    try:
        tolerance = window._seek_effective_tolerance(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            0.005,
            seek_key=seek_key,
        )

        assert tolerance == pytest.approx(0.05)
        assert not window._target_reversal_is_practical_hold(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            error_value=0.17,
            tolerance=0.005,
            seek_key=seek_key,
        )
    finally:
        _close_test_window(window)


def test_setup_preload_target_ramp_uses_elapsed_mpa_rate(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = [100.0]
    captured_targets: list[float] = []

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: now_s[0])

    def _capture_seek(_basis: str, target_value: float, _tolerance: float) -> bool:
        captured_targets.append(target_value)
        return False

    window._seek_distribution_target = _capture_seek  # type: ignore[method-assign]
    step = mini_dma_mod.AutomationStep(
        "ramp_target",
        target_value=10.0,
        target_start_value=0.0,
        target_end_value=10.0,
        target_ramp_rate_value_s=0.5,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        note="setup_preload",
    )

    try:
        assert window._handle_target_ramp_step(step, 4) is False

        now_s[0] = 102.5

        assert window._handle_target_ramp_step(step, 4) is False
        assert captured_targets == [pytest.approx(0.0), pytest.approx(1.25)]
    finally:
        _close_test_window(window)


def test_setup_preload_target_ramp_above_target_skips_preload_from_live_value(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    captured_targets: list[float] = []
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.17)
    window.spin_diameter.setValue(0.0137)
    window.spin_setup_preload_duration_s.setValue(10.0)
    overload_g = mini_dma_mod.load_g_from_stress_mpa(80.0, window.spin_diameter.value())
    assert overload_g is not None
    window._latest_scale_value_g = 21.17 - overload_g
    window._latest_scale_timestamp = time.time()

    def _capture_seek(_basis: str, target_value: float, _tolerance: float) -> bool:
        captured_targets.append(target_value)
        return False

    window._seek_distribution_target = _capture_seek  # type: ignore[method-assign]
    step = mini_dma_mod.AutomationStep(
        "ramp_target",
        target_value=20.0,
        target_start_value=0.0,
        target_end_value=20.0,
        target_ramp_rate_value_s=2.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        note="setup_preload",
    )

    try:
        assert window._handle_target_ramp_step(step, 4) is True

        assert captured_targets == []
        assert window._setup_preload_ramp_skipped is True
        assert "already above setup preload" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_setup_preload_live_start_above_target_skips_preload_relaxation(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    captured_targets: list[float] = []

    try:
        window.spin_setup_preload_duration_s.setValue(5.0)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._active_control_config = window._freeze_control_config()
        window._current_distribution_value = lambda *_args, **_kwargs: 320.0  # type: ignore[method-assign]

        def _capture_seek(_basis: str, target_value: float, _tolerance: float) -> bool:
            captured_targets.append(target_value)
            return False

        window._seek_distribution_target = _capture_seek  # type: ignore[method-assign]
        step = mini_dma_mod.AutomationStep(
            "ramp_target",
            target_value=20.0,
            target_start_value=None,
            target_end_value=20.0,
            target_ramp_rate_value_s=4.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            note="setup_preload",
        )

        assert window._handle_target_ramp_step(step, 4) is True

        assert captured_targets == []
        assert window._setup_preload_ramp_skipped is True
    finally:
        window._active_control_config = None
        _close_test_window(window)


def test_setup_preload_target_ramp_finishes_inside_automatic_tolerance(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = [111.0]
    moves: list[float] = []

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: now_s[0])

    def _capture_move(target_mm: float, **_kwargs: object) -> bool:
        moves.append(target_mm)
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._active_target_ramp_step_index = 7
    window._active_target_ramp_started_s = 100.0
    window._active_target_ramp_start_value = 0.0
    window.check_tension_load_positive.setChecked(False)
    window.spin_diameter.setValue(0.03)
    window.spin_setup_preload_tolerance_mpa.setValue(0.25)
    load_g = mini_dma_mod.load_g_from_stress_mpa(10.05, window.spin_diameter.value())
    assert load_g is not None
    window._latest_scale_value_g = load_g
    window._latest_scale_timestamp = time.time()

    step = mini_dma_mod.AutomationStep(
        "ramp_target",
        target_value=10.0,
        target_start_value=0.0,
        target_end_value=10.0,
        target_ramp_rate_value_s=1.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        note="setup_preload",
    )

    try:
        assert window._handle_target_ramp_step(step, 7) is True
        assert moves == []
        assert window._active_target_ramp_step_index is None
    finally:
        _close_test_window(window)


def test_current_sweep_setup_preload_rejects_contact_scale_residual(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = [111.0]
    moves: list[float] = []

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: now_s[0])
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._active_target_ramp_step_index = 7
    window._active_target_ramp_started_s = 100.0
    window._active_target_ramp_start_value = 0.0
    window.check_tension_load_positive.setChecked(False)
    window.spin_diameter.setValue(0.0089)
    window.spin_setup_preload_tolerance_mpa.setValue(0.25)
    window._current_distribution_value = lambda *_args, **_kwargs: 17.34  # type: ignore[method-assign]
    window._seek_filtered_control_signal = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    window._latest_scale_value_g = 0.1
    window._latest_scale_timestamp = time.time()

    step = mini_dma_mod.AutomationStep(
        "ramp_target",
        target_value=20.0,
        target_start_value=0.0,
        target_end_value=20.0,
        target_ramp_rate_value_s=4.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        note="setup_preload",
    )

    try:
        window._set_automation_context(
            phase="target_ramp",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            note="setup_preload",
        )
        assert window._seek_effective_tolerance(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            0.25,
        ) < 2.66
        assert window._handle_target_ramp_step(step, 7) is False
        assert moves
        assert window._active_target_ramp_step_index == 7
    finally:
        _close_test_window(window)


def test_displacement_ramp_uses_global_control_and_log_clocks(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert window.combo_recipe_mode.findData("ramp") < 0
        window.spin_control_interval.setValue(50)
        window.spin_log_interval.setValue(200)
        window.spin_ramp_distance.setValue(1.0)
        window.spin_ramp_step.setValue(0.1)
        window.spin_ramp_interval.setValue(1000)

        steps, summary, interval_ms = window._build_automation_recipe()
        recipe_start = next(index for index, step in enumerate(steps) if step.action == "start_session")
        recipe_steps = steps[recipe_start + 1 :]

        assert interval_ms == 50
        assert all(step.action != "record" for step in recipe_steps)
        assert not any(step.action == "move" for step in recipe_steps)
        assert "Started calibration" in summary
        assert "control every 50 ms" in summary
        assert "log every 200 ms" in summary
    finally:
        _close_test_window(window)


def test_timing_controls_are_opened_from_settings_menu(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        menu_titles = [action.text().replace("&", "") for action in window.menuBar().actions()]
        assert "Settings" in menu_titles
        assert window.action_timing_settings is not None
        assert window.action_timing_settings.text() == "Timing..."
        assert window.spin_control_interval.isHidden()
        assert window.spin_log_interval.isHidden()
        assert window.spin_ui_interval.isHidden()
        assert window.spin_graph_interval.isHidden()
        assert window.spin_graph_interval.value() == 500
        assert window.spin_scale_interval.isHidden()
        assert window.spin_tic_status_interval.isHidden()
        assert window.spin_tic_keepalive_interval.isHidden()
        assert window.spin_supply_read_interval.isHidden()
    finally:
        _close_test_window(window)


def test_mini_dma_ir_panel_exposes_sensor_choice_and_help(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        help_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.text().replace("&", "") == "Help"
        )
        assert help_menu is not None
        assert help_menu.isEnabled()
        assert any(action.text() == "View Help" for action in help_menu.actions())

        assert window.check_ir_enabled.text() == "Enable optional IR camera/thermometer"
        assert window.check_ir_enabled.isChecked() is True
        sensor_labels = [
            window.combo_ir_sensor.itemText(index)
            for index in range(window.combo_ir_sensor.count())
        ]
        assert sensor_labels == [
            "MLX90640 Cube raw camera",
            "MLX90614 spot thermometer",
        ]
        assert window.combo_ir_sensor.currentData() == mini_dma_mod.IR_SENSOR_MLX90640
        assert window.label_ir_rate.text() == "Camera refresh"
        assert window.button_ir_flash_firmware.text() == "Flash firmware"
        assert "Cube raw camera firmware" in window.label_ir_status.text()
    finally:
        _close_test_window(window)


def test_mini_dma_ir_can_be_disabled_without_temperature_snapshots(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        sample = mini_dma_mod.IrTemperatureSample(
            timestamp_s=10.0,
            raw_text="MLX90614,7,900,2370,23.10,41.50,14813,15733,2",
            sequence=7,
            device_elapsed_ms=900,
            read_us=2370,
            ambient_c=23.10,
            object_c_apparent=41.50,
            raw_ambient=14813,
            raw_object=15733,
            flags=2,
            config1="0x9795",
        )
        window._handle_ir_sample(sample)

        window.check_ir_enabled.setChecked(False)

        assert window.combo_ir_port.isEnabled() is False
        assert window.button_ir_connect.isEnabled() is False
        assert window._manual_auto_connect_should_connect_ir() is False
        assert window._latest_ir_snapshot(now_s=11.0)["object_c_apparent"] is None
        assert window._latest_ir_config1() == ""
        assert "auto-connect will skip" in window.label_ir_status.text()
    finally:
        _close_test_window(window)


def test_mini_dma_ir_camera_mode_exposes_camera_refresh_rates(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert window.combo_ir_sensor.currentData() == mini_dma_mod.IR_SENSOR_MLX90640
        assert [
            window.combo_ir_rate.itemText(index)
            for index in range(window.combo_ir_rate.count())
        ] == ["16 Hz", "32 Hz", "64 Hz"]
        assert window.combo_ir_rate.currentData() == 7
    finally:
        _close_test_window(window)


def test_mini_dma_ir_sensor_selection_updates_rate_defaults(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.combo_ir_sensor.setCurrentIndex(
            window.combo_ir_sensor.findData(mini_dma_mod.IR_SENSOR_MLX90640)
        )
        assert window.combo_ir_rate.currentData() == 7
        assert window.label_ir_rate.text() == "Camera refresh"

        window.combo_ir_sensor.setCurrentIndex(
            window.combo_ir_sensor.findData(mini_dma_mod.IR_SENSOR_MLX90614)
        )
        assert [
            window.combo_ir_rate.itemText(index)
            for index in range(window.combo_ir_rate.count())
        ] == ["10 Hz", "50 Hz", "100 Hz", "Max stream"]
        assert window.label_ir_rate.text() == "Sample interval"
        assert "thermometer firmware" in window.label_ir_status.text()
    finally:
        _close_test_window(window)


def test_mini_dma_live_camera_button_opens_embedded_frame_view(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        from experiments.thermal_camera_viewer import ThermalFrame

        frame = ThermalFrame(
            elapsed_ms=100,
            ambient_c=24.0,
            values=(20.0, 21.0, 22.0, 31.0),
            unit="C",
            raw_read_us=12000,
            sequence=2,
            width=2,
            height=2,
        )
        with window._ir_state_lock:
            window._latest_ir_frame = frame

        window._open_live_thermal_camera_viewer()
        QtWidgets.QApplication.processEvents()

        dialog = window._thermal_camera_dialog
        assert isinstance(dialog, mini_dma_mod.MiniDmaThermalCameraDialog)
        assert dialog.windowFlags() & QtCore.Qt.WindowType.WindowMinimizeButtonHint
        assert dialog.pause_button.text() == "Pause view"
        assert [
            dialog.refresh_combo.itemText(index)
            for index in range(dialog.refresh_combo.count())
        ] == ["1 fps", "5 fps", "10 fps", "20 fps", "Max"]
        assert dialog.minimumWidth() <= 430
        assert dialog.parent() is None
        assert dialog._latest_frame is frame
        assert dialog.image_label.pixmap() is not None
        assert "Max 31.00 C" in dialog.stats_label.text()
    finally:
        if window._thermal_camera_dialog is not None:
            window._thermal_camera_dialog.close()
        _close_test_window(window)


def test_mini_dma_live_camera_button_opens_while_ir_connected(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    messages: list[str] = []
    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QMessageBox,
        "information",
        lambda _parent, _title, text: messages.append(str(text)),
    )

    try:
        window._ir_thread = QtCore.QThread(window)

        window._open_live_thermal_camera_viewer()

        assert not messages
        assert isinstance(window._thermal_camera_dialog, mini_dma_mod.MiniDmaThermalCameraDialog)
        assert "Waiting for calibrated MLX90640 frames" in window._thermal_camera_dialog.stats_label.text()
    finally:
        if window._thermal_camera_dialog is not None:
            window._thermal_camera_dialog.close()
        window._ir_thread = None
        _close_test_window(window)


def test_mini_dma_live_camera_popup_pause_and_display_throttle(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments.thermal_camera_viewer import ThermalFrame

    now = {"s": 10.0}
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: now["s"])
    dialog = mini_dma_mod.MiniDmaThermalCameraDialog()
    qtbot.addWidget(dialog)
    frame1 = ThermalFrame(
        elapsed_ms=100,
        ambient_c=24.0,
        values=(20.0, 21.0, 22.0, 31.0),
        unit="C",
        raw_read_us=12000,
        sequence=1,
        width=2,
        height=2,
    )
    frame2 = dataclasses.replace(frame1, elapsed_ms=150, sequence=2, values=(21.0, 22.0, 23.0, 32.0))
    frame3 = dataclasses.replace(frame1, elapsed_ms=200, sequence=3, values=(22.0, 23.0, 24.0, 33.0))

    try:
        dialog.update_frame(frame1)
        now["s"] += 0.05
        dialog.update_frame(frame2)

        assert dialog._latest_frame is frame1
        assert dialog._pending_frame is frame2

        dialog.pause_button.click()
        assert dialog.pause_button.text() == "Resume view"
        now["s"] += 1.0
        dialog.update_frame(frame3)

        assert dialog._latest_frame is frame1
        assert dialog._pending_frame is frame3

        dialog.pause_button.click()
        assert dialog.pause_button.text() == "Pause view"
        assert dialog._latest_frame is frame3

        max_index = dialog.refresh_combo.findText("Max")
        assert max_index >= 0
        dialog.refresh_combo.setCurrentIndex(max_index)
        now["s"] += 0.001
        dialog.update_frame(frame2)

        assert dialog._latest_frame is frame2
    finally:
        dialog.close()


def test_mini_dma_flash_firmware_runs_programmer_for_selected_sensor(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    firmware_dir = tmp_path / "firmware"
    binary_path = firmware_dir / "build" / "stm32cube_mlx90640_stream.bin"
    binary_path.parent.mkdir(parents=True)
    binary_path.write_bytes(b"firmware")
    programmer_path = tmp_path / "STM32_Programmer_CLI.exe"
    programmer_path.write_text("", encoding="utf-8")
    commands: list[list[str]] = []
    infos: list[str] = []
    warnings: list[str] = []
    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: mini_dma_mod.QtWidgets.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QMessageBox,
        "information",
        lambda _parent, _title, text: infos.append(str(text)),
    )
    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(str(text)),
    )
    window._thermal_firmware_paths = lambda _sensor: (  # type: ignore[method-assign]
        "MLX90640 thermal camera",
        firmware_dir,
        binary_path,
    )
    window._stm32_programmer_cli_path = lambda: programmer_path  # type: ignore[method-assign]

    def _fake_run(command, **_kwargs):
        commands.append([str(part) for part in command])
        return SimpleNamespace(returncode=0, stdout="OK", stderr="")

    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    try:
        window.combo_ir_sensor.setCurrentIndex(
            window.combo_ir_sensor.findData(mini_dma_mod.IR_SENSOR_MLX90640)
        )

        window._flash_selected_ir_firmware()

        assert not warnings
        assert infos and "Flashed MLX90640 thermal camera firmware" in infos[-1]
        assert commands == [
            [
                str(programmer_path),
                "-c",
                "port=SWD",
                "-w",
                str(binary_path),
                "0x08000000",
                "-v",
                "-rst",
            ]
        ]
    finally:
        _close_test_window(window)


def test_ir_worker_records_selected_sensor_mode() -> None:
    worker = mini_dma_mod.Mlx90614Worker(
        port_name="COM10",
        baudrate=2000000,
        interval_code=7,
        sensor_mode=mini_dma_mod.IR_SENSOR_MLX90640,
    )

    assert worker.sensor_mode == mini_dma_mod.IR_SENSOR_MLX90640

    fallback = mini_dma_mod.Mlx90614Worker(
        port_name="COM10",
        baudrate=2000000,
        interval_code=7,
        sensor_mode="unknown",
    )

    assert fallback.sensor_mode == mini_dma_mod.IR_SENSOR_MLX90640


def test_mlx90640_cube_worker_does_not_flush_pending_camera_packets() -> None:
    class _FakePort:
        def __init__(self, worker: mini_dma_mod.Mlx90614Worker) -> None:
            self.worker = worker
            self.events: list[tuple[str, bytes | None]] = []

        def reset_input_buffer(self) -> None:
            self.events.append(("reset_input_buffer", None))

        def write(self, data: bytes) -> int:
            self.events.append(("write", data))
            return len(data)

        def flush(self) -> None:
            self.events.append(("flush", None))

        def read(self, _size: int) -> bytes:
            self.worker.stop()
            return b""

    worker = mini_dma_mod.Mlx90614Worker(
        port_name="COM10",
        baudrate=2000000,
        interval_code=7,
        sensor_mode=mini_dma_mod.IR_SENSOR_MLX90640,
    )
    port = _FakePort(worker)

    worker._run_mlx90640_cube_raw(port)  # noqa: SLF001

    assert ("reset_input_buffer", None) not in port.events
    assert ("write", b"7\n") in port.events


def test_connect_ir_thermometer_does_not_start_duplicate_reader(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    created: list[object] = []

    class _UnexpectedWorker:
        def __init__(self, *_args, **_kwargs) -> None:
            created.append(self)

    monkeypatch.setattr(mini_dma_mod, "Mlx90614Worker", _UnexpectedWorker)

    try:
        window._ir_thread = QtCore.QThread(window)

        assert window._connect_ir_thermometer(show_errors=False)
        assert not created
        assert "already connected" in window.label_ir_status.text()
    finally:
        window._ir_thread = None
        _close_test_window(window)


def test_mlx90640_worker_reports_silent_camera_stream(monkeypatch) -> None:
    class SilentPort:
        def reset_input_buffer(self) -> None:
            pass

        def write(self, _payload: bytes) -> None:
            pass

        def flush(self) -> None:
            pass

        def read(self, _size: int) -> bytes:
            time.sleep(0.005)
            return b""

    monkeypatch.setattr(mini_dma_mod, "MLX90640_SILENT_STATUS_TIMEOUT_S", 0.02)
    statuses: list[str] = []
    worker = mini_dma_mod.Mlx90614Worker(
        port_name="COM10",
        baudrate=2000000,
        interval_code=7,
        sensor_mode=mini_dma_mod.IR_SENSOR_MLX90640,
    )

    def collect_status(message: str) -> None:
        statuses.append(message)
        if "No MLX90640 serial bytes" in message:
            worker.stop()

    worker.status_changed.connect(collect_status)
    worker._run_mlx90640_cube_raw(SilentPort())  # noqa: SLF001

    assert any("No MLX90640 serial bytes" in message for message in statuses)
    assert any("stm32cube_mlx90640_stream" in message for message in statuses)


def test_mlx90640_worker_reports_mlx90614_firmware_bytes(monkeypatch) -> None:
    class WrongFirmwarePort:
        def __init__(self) -> None:
            self._chunks = [b"MLX90614_BOOT,probe\r\n"]

        def reset_input_buffer(self) -> None:
            pass

        def write(self, _payload: bytes) -> None:
            pass

        def flush(self) -> None:
            pass

        def read(self, _size: int) -> bytes:
            if self._chunks:
                return self._chunks.pop(0)
            time.sleep(0.005)
            return b""

    monkeypatch.setattr(mini_dma_mod, "MLX90640_SILENT_STATUS_TIMEOUT_S", 10.0)
    statuses: list[str] = []
    worker = mini_dma_mod.Mlx90614Worker(
        port_name="COM10",
        baudrate=2000000,
        interval_code=7,
        sensor_mode=mini_dma_mod.IR_SENSOR_MLX90640,
    )

    def collect_status(message: str) -> None:
        statuses.append(message)
        if "MLX90614 thermometer firmware" in message:
            worker.stop()

    worker.status_changed.connect(collect_status)
    worker._run_mlx90640_cube_raw(WrongFirmwarePort())  # noqa: SLF001

    assert any("MLX90614 thermometer firmware" in message for message in statuses)


def test_ir_sample_from_thermal_frame_records_camera_summary() -> None:
    frame = SimpleNamespace(
        unit="C",
        values=[20.0, 21.0, 22.0, 31.5, 23.0, 24.0],
        width=3,
        height=2,
        sequence=42,
        elapsed_ms=1200,
        raw_read_us=15000,
        ambient_c=25.0,
        flags=1,
    )

    sample = mini_dma_mod._ir_sample_from_thermal_frame(frame, timestamp_s=10.0)

    assert sample is not None
    assert sample.sensor_type == mini_dma_mod.IR_SENSOR_MLX90640
    assert sample.object_c_apparent == pytest.approx(31.5)
    assert sample.frame_min_c == pytest.approx(20.0)
    assert sample.frame_mean_c == pytest.approx(sum(frame.values) / len(frame.values))
    assert sample.frame_center_c == pytest.approx(23.0)
    assert sample.frame_hotspot_row == 1
    assert sample.frame_hotspot_col == 0
    assert sample.frame_width == 3
    assert sample.frame_height == 2


def test_ir_sample_from_thermal_frame_rejects_raw_units() -> None:
    frame = SimpleNamespace(unit="raw", values=[1000, 1001], width=2, height=1)

    assert mini_dma_mod._ir_sample_from_thermal_frame(frame, timestamp_s=10.0) is None


def test_scale_request_poll_interval_migrates_to_response_time(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("scale_baud", "9600")
    settings.setValue("scale_request", "\\x1bp")
    settings.setValue("scale_interval_ms", 50)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        assert window.spin_scale_interval.value() == mini_dma_mod.DEFAULT_SCALE_REQUEST_INTERVAL_MS
    finally:
        _close_test_window(window)


def test_scale_worker_request_mode_uses_response_timeout() -> None:
    worker = mini_dma_mod.ScaleWorker(
        port_name="COM6",
        baudrate=9600,
        poll_interval_ms=50,
        request_command="\\x1bp",
        request_terminator="",
    )

    assert worker._read_timeout_s() == pytest.approx(mini_dma_mod.SCALE_REQUEST_TIMEOUT_MIN_S)
    assert worker._request_poll_delay_s(started_s=10.0, finished_s=10.02) == pytest.approx(0.03)
    assert worker._request_poll_delay_s(started_s=10.0, finished_s=10.06) == pytest.approx(0.0)


def test_kern_kcp_scale_preset_uses_standard_request() -> None:
    class _FakeCombo:
        def __init__(self) -> None:
            self.current_text = ""

        def findText(self, text: str) -> int:  # noqa: N802 - Qt-style test double
            return 0 if text == "256000" else -1

        def setCurrentText(self, text: str) -> None:  # noqa: N802 - Qt-style test double
            self.current_text = text

    class _FakeEdit:
        def __init__(self) -> None:
            self.text_value = ""

        def setText(self, text: str) -> None:  # noqa: N802 - Qt-style test double
            self.text_value = text

    class _FakeSpin:
        def __init__(self) -> None:
            self.value_set = 0

        def setValue(self, value: int) -> None:  # noqa: N802 - Qt-style test double
            self.value_set = int(value)

    window = mini_dma_mod.MainWindow.__new__(mini_dma_mod.MainWindow)
    window.combo_scale_baud = _FakeCombo()
    window.edit_scale_request = _FakeEdit()
    window.edit_scale_terminator = _FakeEdit()
    window.spin_scale_interval = _FakeSpin()
    messages: list[str] = []
    window._log = messages.append  # type: ignore[method-assign]

    window._apply_kern_kcp_scale_preset()

    assert window.combo_scale_baud.current_text == "256000"
    assert window.edit_scale_request.text_value == mini_dma_mod.KERN_KCP_SCALE_REQUEST
    assert window.edit_scale_terminator.text_value == mini_dma_mod.KERN_KCP_SCALE_TERMINATOR
    assert window.spin_scale_interval.value_set == 50
    assert "Košice KERN KCP" in messages[-1]


def test_gng_scale_preset_preserves_prague_cadence() -> None:
    class _FakeCombo:
        def __init__(self) -> None:
            self.current_text = ""

        def findText(self, text: str) -> int:  # noqa: N802 - Qt-style test double
            return 0 if text == "9600" else -1

        def setCurrentText(self, text: str) -> None:  # noqa: N802 - Qt-style test double
            self.current_text = text

    class _FakeEdit:
        def __init__(self) -> None:
            self.text_value = ""

        def setText(self, text: str) -> None:  # noqa: N802 - Qt-style test double
            self.text_value = text

    class _FakeSpin:
        def __init__(self) -> None:
            self.value_set = 0

        def setValue(self, value: int) -> None:  # noqa: N802 - Qt-style test double
            self.value_set = int(value)

    window = mini_dma_mod.MainWindow.__new__(mini_dma_mod.MainWindow)
    window.combo_scale_baud = _FakeCombo()
    window.edit_scale_request = _FakeEdit()
    window.edit_scale_terminator = _FakeEdit()
    window.spin_scale_interval = _FakeSpin()
    messages: list[str] = []
    window._log = messages.append  # type: ignore[method-assign]

    window._apply_gng_scale_preset()

    assert window.combo_scale_baud.current_text == "9600"
    assert window.edit_scale_request.text_value == "\\x1bp"
    assert window.edit_scale_terminator.text_value == ""
    assert window.spin_scale_interval.value_set == 250
    assert "Prague G&G" in messages[-1]


def test_scale_auto_detect_accepts_kern_kcp_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, bytes]] = []

    def _fake_read_serial_bytes(
        port_name: str,
        *,
        baudrate: int,
        payload: bytes = b"",
        total_wait_s: float = 0.8,
        **_kwargs: object,
    ) -> bytes:
        del port_name, total_wait_s
        calls.append((baudrate, payload))
        if payload == b"SI\r\n" and baudrate == 256000:
            return b"S S +12.34 g\r\n"
        return b""

    monkeypatch.setattr(mini_dma_mod, "_read_serial_bytes", _fake_read_serial_bytes)
    window = mini_dma_mod.MainWindow.__new__(mini_dma_mod.MainWindow)

    match = window._probe_scale_candidate("COM4")

    assert match == {
        "port": "COM4",
        "baudrate": 256000,
        "request_command": mini_dma_mod.KERN_KCP_SCALE_REQUEST,
        "terminator": mini_dma_mod.KERN_KCP_SCALE_TERMINATOR,
        "raw_text": "S S +12.34 g",
    }
    assert calls[0] == (256000, b"SI\r\n")


def test_scale_auto_detect_falls_back_to_gng_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, bytes]] = []

    def _fake_read_serial_bytes(
        port_name: str,
        *,
        baudrate: int,
        payload: bytes = b"",
        total_wait_s: float = 0.8,
        **_kwargs: object,
    ) -> bytes:
        del port_name, total_wait_s
        calls.append((baudrate, payload))
        if payload == b"\x1bp" and baudrate == 9600:
            return b"   +12.34 g\r\n"
        return b""

    monkeypatch.setattr(mini_dma_mod, "_read_serial_bytes", _fake_read_serial_bytes)
    window = mini_dma_mod.MainWindow.__new__(mini_dma_mod.MainWindow)

    match = window._probe_scale_candidate("COM4")

    assert match == {
        "port": "COM4",
        "baudrate": 9600,
        "request_command": "\\x1bp",
        "terminator": "",
        "raw_text": "+12.34 g",
    }
    assert calls[:3] == [(256000, b"SI\r\n"), (256000, b"S\r\n"), (9600, b"\x1bp")]


def test_supply_scientific_notation_current_reply_is_parsed_as_amps() -> None:
    assert mini_dma_mod._parse_first_float("2.150E-2") == pytest.approx(0.0215)
    assert mini_dma_mod._parse_first_float("+1.00e-3 A") == pytest.approx(0.001)


def test_hardware_cadence_settings_restore_and_update_timers(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("tic_status_interval_ms", 1500)
    settings.setValue("tic_keepalive_interval_ms", 350)
    settings.setValue("supply_read_interval_ms", 1250)
    settings.setValue("graph_refresh_interval_ms", 500)
    settings.setValue("current_sweep_supply_channel", 2)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        assert window.spin_tic_status_interval.value() == 1500
        assert window.spin_tic_keepalive_interval.value() == 350
        assert window.spin_supply_read_interval.value() == 1250
        assert window.spin_graph_interval.value() == 500
        assert window.combo_current_sweep_supply_channel.currentData() == 2
        assert window._build_supply_controller().selected_channel() == 2
        assert window._status_timer.interval() == 1500
        assert window._tic_keepalive_timer.interval() == 350

        window.spin_tic_keepalive_interval.setValue(450)
        assert window._tic_keepalive_timer.interval() == 450

        window._save_settings()
        assert int(settings.value("tic_status_interval_ms")) == 1500
        assert int(settings.value("tic_keepalive_interval_ms")) == 450
        assert int(settings.value("supply_read_interval_ms")) == 1250
        assert int(settings.value("graph_refresh_interval_ms")) == 500
        assert int(settings.value("current_sweep_supply_channel")) == 2
    finally:
        _close_test_window(window)


def test_hmp4040_profile_change_requires_manual_channel_selection(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        profile_index = window.combo_supply_profile.findData("hmp4040")
        assert profile_index >= 0

        window.combo_supply_profile.setCurrentIndex(profile_index)

        assert window.combo_current_sweep_supply_channel.currentData() == 0
        assert window.combo_motor_supply_channel.currentData() == 0
        assert window._build_supply_controller().selected_channel() == 0
    finally:
        _close_test_window(window)


def test_hmp4040_restored_profile_keeps_channels_unselected_without_saved_channels(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("supply_profile", "hmp4040")
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        assert window.combo_supply_profile.currentData() == "hmp4040"
        assert window.combo_supply_baud.currentText() == "115200"
        assert window.combo_current_sweep_supply_channel.currentData() == 0
        assert window.combo_motor_supply_channel.currentData() == 0
        assert window.spin_supply_voltage_limit.value() == pytest.approx(32.05)
    finally:
        _close_test_window(window)


def test_auto_detect_supply_port_identifies_hmp4040(tmp_path: Path, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        monkeypatch.setattr(
            mini_dma_mod.list_ports,
            "comports",
            lambda: [SimpleNamespace(device="COM7", description="Rohde supply")],
        )

        def _probe_supply(port_name: str):
            assert port_name == "COM7"
            return {
                "port": "COM7",
                "baudrate": 115200,
                "profile_id": "hmp4040",
                "idn_text": "Rohde&Schwarz,HMP4040,123456,HW1.0/SW3.0",
            }

        monkeypatch.setattr(window, "_probe_supply_candidate", _probe_supply)

        window._refresh_supply_ports()
        detected = window._auto_detect_supply_port()

        assert detected is True
        assert window.combo_supply_port.currentData() == "COM7"
        assert window.combo_supply_baud.currentText() == "115200"
        assert window.combo_supply_profile.currentData() == "hmp4040"
        assert window.combo_current_sweep_supply_channel.currentData() == 0
        assert window.combo_motor_supply_channel.currentData() == 0
    finally:
        _close_test_window(window)


def test_supply_idn_parser_distinguishes_hmp4040_from_hmp4030() -> None:
    assert (
        mini_dma_mod._supply_profile_id_from_idn("Rohde&Schwarz,HMP4040,123456,HW1.0/SW3.0")
        == "hmp4040"
    )
    assert (
        mini_dma_mod._supply_profile_id_from_idn("HAMEG,HMP4030,022982747,HW50020001/SW2.50")
        == "hmp4030"
    )


def test_load_target_ramp_waits_for_feedback_between_moves(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._automation_phase = "target_ramp"
    window._automation_step_note = "setup_preload"
    window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
    window._automation_interval_ms = 50
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window.spin_setup_preload_tolerance_mpa.setValue(0.25)
    window.spin_diameter.setValue(0.03)
    window._current_position_mm = 0.0
    window._current_position_steps = 0
    window._last_move_target_mm = 0.0
    feedback_s = time.time()
    window._last_motion_command_time_s = feedback_s - 0.1
    window._latest_scale_value_g = window.spin_zero_load_scale_g.value()
    window._latest_scale_timestamp = feedback_s
    targets: list[tuple[float, bool]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        chain = bool(kwargs.get("chain_from_last_target", False))
        targets.append((target_mm, chain))
        window._last_move_target_mm = target_mm
        window._manual_jog_uses_last_target = chain
        window._last_motion_command_time_s = time.time()
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]

    try:
        for _ in range(2):
            assert window._seek_distribution_target(
                mini_dma_mod.HSW_BASIS_STRESS_MPA,
                target_value=10.0,
                tolerance=0.25,
            ) is False

        assert len(targets) == 1
        assert targets[0] == (pytest.approx(-0.075), False)
    finally:
        _close_test_window(window)


def test_length_setup_dialog_contains_live_graph_and_records_setup_points(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._show_length_setup_dialog()

        assert window._length_setup_stress_plot_widget is not None
        assert isinstance(window._length_setup_stress_plot_widget, mini_dma_mod.pg.PlotWidget)
        assert window._length_setup_displacement_plot_widget is not None
        assert isinstance(window._length_setup_displacement_plot_widget, mini_dma_mod.pg.PlotWidget)
        assert window._length_setup_stress_plot is not None
        assert window._length_setup_stress_plot.right_view is not None
        right_axis = window._length_setup_stress_plot.plot_item.getAxis("right")
        assert right_axis.width() >= 58

        window._latest_scale_value_g = 21.5
        window._latest_scale_timestamp = time.time()
        window.spin_zero_load_scale_g.setValue(21.2)
        window.check_tension_load_positive.setChecked(False)
        window._current_position_mm = -0.2
        window._effective_position_mm = -0.2
        window._record_length_setup_point()

        assert len(window._length_setup_points) == 1
        point = window._length_setup_points[0]
        assert point.load_g == pytest.approx(0.3)
        assert point.stress_mpa is not None
        assert window._length_setup_stress_curve is not None
        assert window._length_setup_load_curve is not None
        assert window._length_setup_displacement_curve is not None
        assert window._length_setup_stress_curve.opts.get("symbol") == "o"
        assert window._length_setup_displacement_curve.opts.get("symbol") == "o"
        stress_x, stress_y = window._length_setup_stress_curve.getData()
        load_x, load_y = window._length_setup_load_curve.getData()
        displacement_x, displacement_y = window._length_setup_displacement_curve.getData()
        assert len(stress_x) == 1
        assert len(load_x) == 1
        assert len(displacement_x) == 1
        assert list(stress_y) == pytest.approx([point.stress_mpa])
        assert list(load_y) == pytest.approx([0.3])
        assert list(displacement_y) == pytest.approx([point.position_mm])
    finally:
        _close_test_window(window)


def test_length_setup_plot_sorts_points_by_elapsed_time(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._show_length_setup_dialog()
        window._latest_scale_value_g = 21.5
        window._latest_scale_timestamp = time.time()
        window.spin_zero_load_scale_g.setValue(21.2)
        window.check_tension_load_positive.setChecked(False)
        window._current_position_mm = -0.2
        window._effective_position_mm = -0.2
        window._record_length_setup_point()

        base = window._length_setup_points[0]
        window._length_setup_points = [
            dataclasses.replace(base, elapsed_s=2.0, position_mm=-0.2, load_g=0.3),
            dataclasses.replace(base, elapsed_s=0.0, position_mm=0.0, load_g=0.1),
            dataclasses.replace(base, elapsed_s=1.0, position_mm=-0.1, load_g=0.2),
        ]

        window._refresh_length_setup_plot()

        assert window._length_setup_displacement_curve is not None
        displacement_x, displacement_y = window._length_setup_displacement_curve.getData()
        assert list(displacement_x) == pytest.approx([0.0, 1.0, 2.0])
        assert list(displacement_y) == pytest.approx([0.0, -0.1, -0.2])
        assert all(
            float(next_x) >= float(current_x)
            for current_x, next_x in zip(displacement_x, displacement_x[1:])
        )
    finally:
        _close_test_window(window)


def test_length_setup_timer_records_prompt_samples(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._show_length_setup_dialog()
        window._automation_active = True
        window._set_automation_context(phase="starting_length", note="starting_length")
        window._latest_scale_value_g = 21.5
        window._latest_scale_timestamp = time.time()
        window.spin_zero_load_scale_g.setValue(21.2)
        window.check_tension_load_positive.setChecked(False)
        window._current_position_mm = -0.2
        window._effective_position_mm = -0.2

        window._handle_ui_refresh_timer()
        window._handle_ui_refresh_timer()

        assert len(window._length_setup_points) == 1
        assert window._length_setup_points[0].load_g == pytest.approx(0.3)
    finally:
        _close_test_window(window)


def test_ui_refresh_adds_live_plot_sample_without_logging_or_supply_io(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    clock = {"now": 100.0}
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: clock["now"])
    window._refresh_supply_snapshot = lambda: pytest.fail("live plot refresh must not query the supply")  # type: ignore[method-assign]
    plot_refreshes: list[bool] = []
    window._refresh_plots = lambda: plot_refreshes.append(True)  # type: ignore[method-assign]

    try:
        window._session_active = True
        window._session_logging_enabled = True
        window._session_start_monotonic = 90.0
        window.spin_zero_load_scale_g.setValue(21.2)
        window.spin_initial_length.setValue(20.0)
        window.check_tension_load_positive.setChecked(True)
        window.check_positive_motion_is_tension.setChecked(True)
        window._latest_scale_value_g = 21.0
        window._latest_scale_timestamp = 123.0
        window._current_position_mm = -0.4
        window._effective_position_mm = 0.1
        window._position_reference_mm = 0.0
        window._supply_last_setpoint_mA = 50.0
        window._supply_snapshot = {
            "current_mA": 49.0,
            "voltage_V": 2.45,
            "resistance_ohm": 50.0,
            "power_W": 0.120,
        }

        window._handle_ui_refresh_timer()
        window._handle_ui_refresh_timer()

        assert len(window._live_plot_points) == 1
        assert window._session_points == []
        assert plot_refreshes == [True]
        point = window._live_plot_points[0]
        assert point.elapsed_s == pytest.approx(10.0)
        assert point.raw_position_mm == pytest.approx(-0.4)
        assert point.position_mm == pytest.approx(-0.4)
        assert point.strain_pct == pytest.approx(-2.0)
        assert point.load_g == pytest.approx(0.2)
        assert point.current_measured_mA == pytest.approx(49.0)
        assert point.resistance_ohm == pytest.approx(50.0)
    finally:
        _close_test_window(window)


def test_ui_refresh_throttles_dashboard_graph_redraws_but_keeps_live_samples(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    clock = {"now": 100.0}
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: clock["now"])
    plot_refreshes: list[float] = []
    window._refresh_plots = lambda: plot_refreshes.append(clock["now"])  # type: ignore[method-assign]

    try:
        window._session_active = True
        window._session_logging_enabled = True
        window._session_start_monotonic = 90.0
        window.spin_zero_load_scale_g.setValue(21.2)
        window.check_tension_load_positive.setChecked(True)
        window._latest_scale_value_g = 21.0
        window._latest_scale_timestamp = 123.0
        window._current_position_mm = -0.4
        window._effective_position_mm = -0.4
        window._position_reference_mm = 0.0
        window.spin_graph_interval.setValue(1000)

        window._handle_ui_refresh_timer()
        clock["now"] += 0.2
        window._latest_scale_value_g = 20.9
        window._latest_scale_timestamp = 124.0
        window._handle_ui_refresh_timer()
        clock["now"] += 1.0
        window._latest_scale_value_g = 20.8
        window._latest_scale_timestamp = 125.0
        window._handle_ui_refresh_timer()

        assert len(window._live_plot_points) == 3
        assert plot_refreshes == [100.0, 101.2]
    finally:
        _close_test_window(window)


def test_display_plot_points_include_live_samples_between_logged_rows(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    def _point(elapsed_s: float, position_mm: float, load_g: float) -> mini_dma_mod.MeasurementPoint:
        return mini_dma_mod.MeasurementPoint(
            elapsed_s=elapsed_s,
            timestamp_utc="2026-05-11 00:00:00",
            raw_position_mm=position_mm,
            position_mm=position_mm,
            raw_load_g=load_g,
            load_g=load_g,
            preload_state=mini_dma_mod.PRELOAD_DISABLED,
            strain_pct=None,
            stress_mpa=None,
            current_set_mA=None,
            current_measured_mA=None,
            voltage_V=None,
            resistance_ohm=None,
            power_W=None,
            automation_phase="current",
            automation_basis=None,
            automation_target_value=None,
            plateau_index=None,
            plateau_label=None,
        )

    logged = _point(elapsed_s=1.0, position_mm=0.1, load_g=0.1)
    live = _point(elapsed_s=1.5, position_mm=0.2, load_g=0.2)

    try:
        window._session_points = [logged]
        window._live_plot_points = [live]

        assert window._display_plot_points() == [logged, live]
    finally:
        _close_test_window(window)


def test_display_plot_points_downsample_old_points_but_keep_recent_samples(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    def _point(elapsed_s: float) -> mini_dma_mod.MeasurementPoint:
        return mini_dma_mod.MeasurementPoint(
            elapsed_s=elapsed_s,
            timestamp_utc="2026-05-11 00:00:00",
            raw_position_mm=elapsed_s,
            position_mm=elapsed_s,
            raw_load_g=elapsed_s,
            load_g=elapsed_s,
            preload_state=mini_dma_mod.PRELOAD_DISABLED,
            strain_pct=None,
            stress_mpa=None,
            current_set_mA=None,
            current_measured_mA=None,
            voltage_V=None,
            resistance_ohm=None,
            power_W=None,
            automation_phase="current",
            automation_basis=None,
            automation_target_value=None,
            plateau_index=None,
            plateau_label=None,
        )

    try:
        total_points = mini_dma_mod.DISPLAY_PLOT_MAX_POINTS + 800
        window._session_points = [_point(float(index)) for index in range(total_points)]

        display_points = window._display_plot_points()

        assert len(display_points) <= mini_dma_mod.DISPLAY_PLOT_MAX_POINTS
        assert display_points[0].elapsed_s == pytest.approx(0.0)
        assert [point.elapsed_s for point in display_points[-mini_dma_mod.DISPLAY_PLOT_RECENT_POINTS :]] == [
            float(index)
            for index in range(total_points - mini_dma_mod.DISPLAY_PLOT_RECENT_POINTS, total_points)
        ]
    finally:
        _close_test_window(window)


def test_display_plot_points_keep_older_downsample_stable_as_new_samples_arrive(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    def _point(elapsed_s: float) -> mini_dma_mod.MeasurementPoint:
        return mini_dma_mod.MeasurementPoint(
            elapsed_s=elapsed_s,
            timestamp_utc="2026-05-11 00:00:00",
            raw_position_mm=elapsed_s,
            position_mm=elapsed_s,
            raw_load_g=elapsed_s,
            load_g=elapsed_s,
            preload_state=mini_dma_mod.PRELOAD_DISABLED,
            strain_pct=None,
            stress_mpa=None,
            current_set_mA=None,
            current_measured_mA=None,
            voltage_V=None,
            resistance_ohm=None,
            power_W=None,
            automation_phase="current",
            automation_basis=None,
            automation_target_value=None,
            plateau_index=None,
            plateau_label=None,
        )

    try:
        first_total = mini_dma_mod.DISPLAY_PLOT_MAX_POINTS + 800
        second_total = first_total + 80
        window._session_points = [_point(float(index)) for index in range(first_total)]
        first_display = window._display_plot_points()
        first_old = [point.elapsed_s for point in first_display[:40]]

        window._session_points = [_point(float(index)) for index in range(second_total)]
        second_display = window._display_plot_points()
        second_old = [point.elapsed_s for point in second_display[:40]]

        assert second_old == first_old
        assert [point.elapsed_s for point in second_display[-mini_dma_mod.DISPLAY_PLOT_RECENT_POINTS :]] == [
            float(index)
            for index in range(second_total - mini_dma_mod.DISPLAY_PLOT_RECENT_POINTS, second_total)
        ]
    finally:
        _close_test_window(window)


def test_display_plot_points_bridge_cached_history_to_recent_tail(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    def _point(elapsed_s: float) -> mini_dma_mod.MeasurementPoint:
        return mini_dma_mod.MeasurementPoint(
            elapsed_s=elapsed_s,
            timestamp_utc="2026-05-11 00:00:00",
            raw_position_mm=elapsed_s,
            position_mm=elapsed_s,
            raw_load_g=elapsed_s,
            load_g=elapsed_s,
            preload_state=mini_dma_mod.PRELOAD_DISABLED,
            strain_pct=None,
            stress_mpa=None,
            current_set_mA=None,
            current_measured_mA=None,
            voltage_V=None,
            resistance_ohm=None,
            power_W=None,
            automation_phase="current",
            automation_basis=None,
            automation_target_value=None,
            plateau_index=None,
            plateau_label=None,
        )

    try:
        first_total = mini_dma_mod.DISPLAY_PLOT_MAX_POINTS + 5000
        second_total = first_total + 80
        window._session_points = [_point(float(index)) for index in range(first_total)]
        window._display_plot_points()

        window._session_points = [_point(float(index)) for index in range(second_total)]
        display_points = window._display_plot_points()

        recent_start_s = float(second_total - mini_dma_mod.DISPLAY_PLOT_RECENT_POINTS)
        before_recent = [point.elapsed_s for point in display_points if point.elapsed_s < recent_start_s]

        assert before_recent
        assert recent_start_s - before_recent[-1] <= mini_dma_mod.DISPLAY_PLOT_BREAK_GAP_S
        assert not display_points[-mini_dma_mod.DISPLAY_PLOT_RECENT_POINTS].plot_gap_before
    finally:
        _close_test_window(window)


def test_display_plot_points_reuses_cached_old_history_for_repeated_refreshes(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    calls: list[int] = []

    def _point(elapsed_s: float) -> mini_dma_mod.MeasurementPoint:
        return mini_dma_mod.MeasurementPoint(
            elapsed_s=elapsed_s,
            timestamp_utc="2026-05-11 00:00:00",
            raw_position_mm=elapsed_s,
            position_mm=elapsed_s,
            raw_load_g=elapsed_s,
            load_g=elapsed_s,
            preload_state=mini_dma_mod.PRELOAD_DISABLED,
            strain_pct=None,
            stress_mpa=None,
            current_set_mA=None,
            current_measured_mA=None,
            voltage_V=None,
            resistance_ohm=None,
            power_W=None,
            automation_phase="current",
            automation_basis=None,
            automation_target_value=None,
            plateau_index=None,
            plateau_label=None,
        )

    original_sampler = window._stable_downsample_older_plot_points

    def _counting_sampler(points: list[mini_dma_mod.MeasurementPoint], budget: int):
        calls.append(len(points))
        return original_sampler(points, budget)

    monkeypatch.setattr(window, "_stable_downsample_older_plot_points", _counting_sampler)

    try:
        total_points = mini_dma_mod.DISPLAY_PLOT_MAX_POINTS + 5000
        window._session_points = [_point(float(index)) for index in range(total_points)]

        first_display = window._display_plot_points()
        second_display = window._display_plot_points()

        assert len(calls) == 1
        assert [point.elapsed_s for point in second_display] == [
            point.elapsed_s for point in first_display
        ]

        window._session_points.append(_point(float(total_points)))
        third_display = window._display_plot_points()

        assert len(calls) == 2
        assert third_display[-1].elapsed_s == pytest.approx(float(total_points))

        window._session_points = [_point(float(index) + 0.25) for index in range(total_points)]
        replacement_display = window._display_plot_points()

        assert len(calls) == 3
        assert replacement_display[0].elapsed_s == pytest.approx(0.25)
    finally:
        _close_test_window(window)


def test_plot_xy_values_break_line_across_hidden_display_gap(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    def _point(elapsed_s: float, load_g: float) -> mini_dma_mod.MeasurementPoint:
        return mini_dma_mod.MeasurementPoint(
            elapsed_s=elapsed_s,
            timestamp_utc="2026-05-11 00:00:00",
            raw_position_mm=load_g,
            position_mm=load_g,
            raw_load_g=load_g,
            load_g=load_g,
            preload_state=mini_dma_mod.PRELOAD_DISABLED,
            strain_pct=None,
            stress_mpa=None,
            current_set_mA=None,
            current_measured_mA=None,
            voltage_V=None,
            resistance_ohm=None,
            power_W=None,
            automation_phase="current",
            automation_basis=None,
            automation_target_value=None,
            plateau_index=None,
            plateau_label=None,
        )

    try:
        x_channel = window._plot_channel("elapsed_s")
        y_channel = window._plot_channel("load_g")
        assert x_channel is not None
        assert y_channel is not None

        x_values, y_values = window._plot_xy_values(
            [_point(10.0, 1.0), _point(12.0, 1.2), _point(180.0, 3.0)],
            x_channel,
            y_channel,
        )

        assert x_values[0:2] == pytest.approx([10.0, 12.0])
        assert y_values[0:2] == pytest.approx([1.0, 1.2])
        assert math.isnan(x_values[2])
        assert math.isnan(y_values[2])
        assert x_values[3] == pytest.approx(180.0)
        assert y_values[3] == pytest.approx(3.0)
    finally:
        _close_test_window(window)


def test_display_plot_points_connect_downsampled_history_to_recent_tail(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    def _point(elapsed_s: float, load_g: float) -> mini_dma_mod.MeasurementPoint:
        return mini_dma_mod.MeasurementPoint(
            elapsed_s=elapsed_s,
            timestamp_utc="2026-05-11 00:00:00",
            raw_position_mm=0.0,
            position_mm=0.0,
            raw_load_g=load_g,
            load_g=load_g,
            preload_state=mini_dma_mod.PRELOAD_DISABLED,
            strain_pct=None,
            stress_mpa=None,
            current_set_mA=None,
            current_measured_mA=None,
            voltage_V=None,
            resistance_ohm=None,
            power_W=None,
            automation_phase="current",
            automation_basis=None,
            automation_target_value=None,
            plateau_index=None,
            plateau_label=None,
        )

    try:
        total_points = mini_dma_mod.DISPLAY_PLOT_MAX_POINTS + 800
        window._session_points = [_point(index * 0.2, float(index % 13)) for index in range(total_points)]

        x_channel = window._plot_channel("elapsed_s")
        y_channel = window._plot_channel("load_g")
        assert x_channel is not None
        assert y_channel is not None

        x_values, y_values = window._plot_xy_values(window._display_plot_points(), x_channel, y_channel)

        assert not any(math.isnan(value) for value in x_values)
        assert not any(math.isnan(value) for value in y_values)
    finally:
        _close_test_window(window)


def test_length_setup_dialog_has_local_pause_stop_and_progress(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.halt_count = 0

        def halt_and_hold(self) -> None:
            self.halt_count += 1

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window._disable_supply_output = lambda: None  # type: ignore[method-assign]
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
    window._ask_recovery_after_stop = lambda: None  # type: ignore[method-assign]

    try:
        window._automation_active = True
        window._automation_paused = False
        window._automation_steps = [
            mini_dma_mod.AutomationStep("ramp_target", note="setup_preload"),
            mini_dma_mod.AutomationStep("start_session", note="recipe_start"),
            mini_dma_mod.AutomationStep("record"),
        ]
        window._automation_index = 0
        window._automation_total_steps = 12
        window._automation_completed_ticks = 3
        window._automation_interval_ms = 250
        window._show_length_setup_dialog()
        window._update_recipe_progress()
        window._update_recipe_buttons()

        assert window._length_setup_progress is not None
        assert window._length_setup_progress.value() == 0
        assert "Setup progress" in window._length_setup_progress.format()
        assert "/12" not in window._length_setup_progress.format()
        assert "samples" in window._length_setup_progress.format()
        assert window._button_length_setup_pause is not None
        assert window._button_length_setup_pause.text() == "Pause setup"
        assert window._button_length_setup_stop is not None
        assert window._button_length_setup_stop.isEnabled()

        window._button_length_setup_pause.click()

        assert window._automation_paused is True
        assert controller.halt_count == 1
        assert window._button_length_setup_pause.text() == "Resume setup"

        window._button_length_setup_stop.click()

        assert window._automation_active is False
        assert window._length_setup_dialog is None or not window._length_setup_dialog.isVisible()
    finally:
        _close_test_window(window)


def test_length_setup_progress_tracks_active_ramp_phase_without_tick_counts(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = [102.5]
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: now_s[0])

    try:
        window._automation_active = True
        window._automation_steps = [
            mini_dma_mod.AutomationStep("starting_length_prompt", note="setup_start_length"),
            mini_dma_mod.AutomationStep(
                "ramp_target",
                target_value=20.0,
                target_end_value=20.0,
                basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
                note="setup_preload",
            ),
            mini_dma_mod.AutomationStep("settle", duration_s=3.0, note="setup_preload"),
            mini_dma_mod.AutomationStep("mark_setup_return_zero", note="setup_return_zero_start"),
            mini_dma_mod.AutomationStep("seek_target", note="setup_return_zero"),
            mini_dma_mod.AutomationStep("settle", duration_s=3.0, note="setup_return_zero"),
            mini_dma_mod.AutomationStep("apply_length_setup", note="setup_apply_l0"),
            mini_dma_mod.AutomationStep("start_session", note="recipe_start"),
        ]
        window._automation_index = 1
        window._automation_total_steps = 20000
        window._automation_completed_ticks = 1234
        window._active_target_ramp_step_index = 1
        window._active_target_ramp_started_s = 100.0
        window._active_target_ramp_start_value = 320.0
        window._active_target_ramp_rate_value_s = 60.0
        window._setup_preload_engaged_seek_keys.add(
            window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
        )
        window._show_length_setup_dialog()

        window._update_length_setup_progress()

        assert window._length_setup_progress is not None
        assert window._length_setup_progress.maximum() == 1000
        assert 490 <= window._length_setup_progress.value() <= 510
        text = window._length_setup_progress.format()
        assert "Preload ramp" in text
        assert "50%" in text
        assert "20000" not in text
        assert "1234" not in text
    finally:
        _close_test_window(window)


def test_length_setup_preload_progress_uses_live_target_error_not_elapsed_timeout(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: 118.0)

    try:
        window._automation_active = True
        window._automation_steps = [
            mini_dma_mod.AutomationStep(
                "ramp_target",
                target_value=20.0,
                target_end_value=20.0,
                basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
                note="setup_preload",
            )
        ]
        window._automation_index = 0
        window._active_target_ramp_step_index = 0
        window._active_target_ramp_started_s = 100.0
        window._active_target_ramp_start_value = 140.0
        window._active_target_ramp_rate_value_s = 24.0
        window._setup_preload_engaged_seek_keys.add(
            window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
        )
        monkeypatch.setattr(window, "_current_distribution_value", lambda _basis: 29.0)
        window._show_length_setup_dialog()

        window._update_length_setup_progress()

        assert window._length_setup_progress is not None
        assert window._length_setup_progress.value() < 1000
        assert "100%" not in window._length_setup_progress.format()
    finally:
        _close_test_window(window)


def test_length_setup_progress_does_not_move_backward_when_ramp_estimate_resets(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = [108.0]
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: now_s[0])

    try:
        window._automation_active = True
        window._automation_steps = [
            mini_dma_mod.AutomationStep(
                "ramp_target",
                target_value=20.0,
                target_end_value=20.0,
                basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
                note="setup_preload",
            ),
        ]
        window._automation_index = 0
        window._active_target_ramp_step_index = 0
        window._active_target_ramp_started_s = 100.0
        window._active_target_ramp_start_value = 0.0
        window._active_target_ramp_rate_value_s = 2.0
        window._setup_preload_engaged_seek_keys.add(
            window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
        )
        window._show_length_setup_dialog()
        window._update_length_setup_progress()
        first_value = window._length_setup_progress.value()

        window._active_target_ramp_started_s = 107.0
        window._active_target_ramp_start_value = 18.0
        window._active_target_ramp_rate_value_s = 2.0
        window._update_length_setup_progress()

        assert window._length_setup_progress.value() >= first_value
    finally:
        _close_test_window(window)


def test_length_setup_progress_does_not_start_timed_settle_state(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._automation_active = True
        window._automation_steps = [mini_dma_mod.AutomationStep("settle", duration_s=3.0, note="setup_preload")]
        window._automation_index = 0
        window._active_timed_step_index = None
        window._show_length_setup_dialog()

        window._update_length_setup_progress()

        assert window._active_timed_step_index is None
        assert window._length_setup_progress is not None
        assert window._length_setup_progress.value() == 0
    finally:
        _close_test_window(window)


def test_length_setup_progress_is_indeterminate_during_slack_takeup(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = [125.0]
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: now_s[0])

    try:
        window._automation_active = True
        window._automation_steps = [
            mini_dma_mod.AutomationStep(
                "ramp_target",
                target_value=20.0,
                target_end_value=20.0,
                basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
                note="setup_preload",
            ),
        ]
        window._automation_index = 0
        window._active_target_ramp_step_index = 0
        window._active_target_ramp_started_s = 100.0
        window._active_target_ramp_start_value = 0.0
        window._active_target_ramp_rate_value_s = 4.0
        window._current_distribution_value = lambda *_args, **_kwargs: 0.0  # type: ignore[method-assign]
        window._set_automation_context(
            phase="target_ramp",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            note="setup_preload",
        )
        window._show_length_setup_dialog()

        window._update_length_setup_progress()

        assert window._length_setup_progress is not None
        assert window._length_setup_progress.minimum() == 0
        assert window._length_setup_progress.maximum() == 0
        text = window._length_setup_progress.format()
        assert "Slack take-up" in text
        assert "100%" not in text
    finally:
        _close_test_window(window)


def test_length_setup_automation_values_answer_setup_prompts(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    prompt_calls: list[bool] = []
    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QInputDialog,
        "getDouble",
        lambda *_args, **_kwargs: prompt_calls.append(True) or (0.0, False),
    )

    try:
        window.set_length_setup_automation_values(
            starting_length_mm=20.0,
            preload_length_mm=20.4,
        )

        assert window._handle_starting_length_prompt_step() is True
        assert window._setup_starting_length_mm == pytest.approx(20.0)
        assert window._setup_measured_length_mm == pytest.approx(20.0)
        assert window._setup_preload_position_mm == pytest.approx(window._current_position_mm)
        assert window.spin_initial_length.value() == pytest.approx(20.0)

        assert prompt_calls == []
    finally:
        _close_test_window(window)


def test_technical_hardware_details_are_hidden_by_default(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert window.advanced_hardware_panel.isVisible() is False
        assert window.combo_scale_baud.isVisible() is False
        assert window.edit_scale_request.isVisible() is False
        assert window.edit_ticcmd_path.isVisible() is False
        assert window.spin_steps_per_mm.isVisible() is False
        tab_labels = [window._control_scroll_area.widget().findChild(QtWidgets.QTabWidget).tabText(index) for index in range(3)]
        assert tab_labels == ["Recipe", "Sample", "Hardware"]
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_LOAD)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        window._update_recipe_mode_ui()

        assert window.spin_current_sweep_nudge_mm.isHidden() is True
        assert window.spin_current_sweep_balance_speed_mm_s.isHidden() is True
        assert window.action_current_sweep_advanced_settings is not None
        assert window.action_current_sweep_advanced_settings.text() == "Current sweep advanced..."
        assert window.current_sweep_advanced_panel.isHidden() is True
        assert window.spin_current_sweep_max_correction_stress_mpa.isHidden() is True
        assert window.spin_current_sweep_hold_correction_stress_mpa.isHidden() is True
        assert window.spin_current_sweep_hold_filter_window_s.isHidden() is True
        assert window.row_current_sweep_first_overheating_target.isHidden() is True
        assert window.label_current_sweep_first_overheating_target is not None
        assert window.label_current_sweep_first_overheating_target.isHidden() is True
        assert window.check_current_sweep_first_overheating_use_normal_end.isHidden() is True
        window.action_current_sweep_advanced_settings.trigger()
        qtbot.waitUntil(lambda: window._current_sweep_advanced_dialog is not None)
        assert window._current_sweep_advanced_dialog is not None
        assert window._current_sweep_advanced_dialog.isVisible() is True
        assert window.spin_current_sweep_max_correction_stress_mpa.minimumWidth() >= 130
        assert window.spin_current_sweep_hold_filter_window_s.minimumWidth() >= 130
        assert window.spin_current_sweep_max_correction_stress_mpa.isHidden() is False
        assert window.spin_current_sweep_hold_correction_stress_mpa.isHidden() is False
        assert window.spin_current_sweep_hold_filter_window_s.isHidden() is False
        assert window.check_current_sweep_first_overheating.isHidden() is False
        assert window.label_current_sweep_first_overheating_section.text() == "First overheating"
        assert window.check_current_sweep_first_overheating.text() == "Enable first-overheating sweep"
        assert window.row_current_sweep_first_overheating_target.isHidden() is True
        assert window.label_current_sweep_first_overheating_target.isHidden() is True
        assert window.check_current_sweep_first_overheating_use_normal_end.text() == "Use normal max current"
        assert window.check_current_sweep_first_overheating_use_normal_end.isChecked() is True
        assert window.check_current_sweep_first_overheating_use_normal_end.isHidden() is True
        assert window.spin_current_sweep_first_overheating_end_mA.isEnabled() is False
        assert window.row_current_sweep_first_overheating_end.isHidden() is True
        assert window.label_current_sweep_first_overheating_end is not None
        assert window.label_current_sweep_first_overheating_end.isHidden() is True
        window.check_current_sweep_first_overheating.setChecked(True)
        assert window.row_current_sweep_first_overheating_target.isHidden() is False
        assert window.label_current_sweep_first_overheating_target.isHidden() is False
        assert window.check_current_sweep_first_overheating_use_normal_end.isHidden() is False
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        assert window.spin_current_sweep_first_overheating_end_mA.isEnabled() is True
        assert window.row_current_sweep_first_overheating_end.isHidden() is False
        assert window.label_current_sweep_first_overheating_end.isHidden() is False
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(True)
        assert window.spin_current_sweep_first_overheating_end_mA.isEnabled() is False
        assert window.row_current_sweep_first_overheating_end.isHidden() is True
        assert window.label_current_sweep_first_overheating_end.isHidden() is True
        assert window.label_current_sweep_targets_section.text() == "Load targets"
        assert window.label_current_sweep_current_section.text() == "Current sweep"
        assert window.check_current_sweep_return_target.isHidden() is True
        assert window.check_current_sweep_reverse_current.isHidden() is True
        assert window.spin_current_sweep_hold_correction_stress_mpa.value() == pytest.approx(
            mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_MAX_CORRECTION_STRESS_MPA
        )
        assert not hasattr(window, "check_hardware_tare_on_start")
        assert window.button_scale_connect.text() in {"Connect scale", "Disconnect scale"}
        assert window.button_scale_tare.text() == "Capture zero-load"
        assert window.button_scale_hardware_tare.text() == "Tare scale"
        assert window.button_scale_hardware_tare.isHidden() is False
        assert window.button_advanced_software_tare.isVisible() is False
        assert window.button_save_recipe.text() == "Save recipe"
        assert window.button_load_recipe.text() == "Load recipe"
        assert window.button_manual_action_settings.text() == "Manual action settings"
        assert window.manual_action_settings_panel.isVisible() is False
        window.button_manual_action_settings.setChecked(True)
        assert window.spin_motion_speed_mm_s.isHidden() is False
        assert window.spin_jog_mm.isHidden() is False
        assert window.spin_setup_return_duration_s.isHidden() is False
        assert window.button_start_recipe.text() == "Start recipe"
        assert window.button_start_recipe.parent() is window.recipe_action_footer
        assert window.recipe_progress.parent() is window.recipe_action_footer
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        assert window.recipe_progress.font().family() != fixed_font.family()
        assert "padding-left" not in window.recipe_progress.styleSheet()
        assert window.label_current_task.parent() is window.recipe_action_footer
        assert window.label_current_task.isVisible() is False
        assert window.label_recipe_estimate.isVisible() is False
    finally:
        _close_test_window(window)


def test_motion_resolution_spinboxes_clamp_manual_edits_to_motor_step(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_steps_per_mm.setValue(100.0)
        window._clamp_motion_resolution_controls()

        editor = window.spin_current_sweep_nudge_mm.lineEdit()
        editor.setText("0.002")
        window.spin_current_sweep_nudge_mm.interpretText()

        assert window.spin_current_sweep_nudge_mm.value() == pytest.approx(0.01)

        editor.setText("0.02")
        window.spin_current_sweep_nudge_mm.interpretText()

        assert window.spin_current_sweep_nudge_mm.value() == pytest.approx(0.02)

        speed_editor = window.spin_motion_speed_mm_s.lineEdit()
        speed_editor.setText("0.002")
        window.spin_motion_speed_mm_s.interpretText()

        assert window.spin_motion_speed_mm_s.value() == pytest.approx(0.01)
        assert window._motion_speed_for_current_context(manual_jog=True) == pytest.approx(0.01)
    finally:
        _close_test_window(window)


def test_stale_saved_ticcmd_path_falls_back_to_discovered_install(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovered = tmp_path / "ticcmd.exe"
    discovered.write_bytes(b"")
    original_settings = _snapshot_settings()
    settings = _test_settings()
    settings.setValue("ticcmd_path", str(tmp_path / "missing_ticcmd.exe"))
    settings.setValue("jog_mm", 0.0001)
    settings.sync()
    monkeypatch.setattr(mini_dma_mod, "_find_ticcmd", lambda: str(discovered))

    window = _build_window(tmp_path, qtbot, preserve_settings=True)

    try:
        assert window.edit_ticcmd_path.text() == str(discovered)
        assert window.spin_jog_mm.value() >= 0.01
    finally:
        _close_test_window(window)
        _restore_settings(original_settings)


def test_recipe_preflight_reports_scale_and_supply_together(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    warnings: list[str] = []

    try:
        window._ensure_scale_ready_for_recipe = lambda: False  # type: ignore[method-assign]
        window._ensure_supply_ready_for_recipe = lambda: False  # type: ignore[method-assign]
        window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]

        monkeypatch.setattr(
            mini_dma_mod.QtWidgets.QMessageBox,
            "warning",
            lambda _parent, _title, message: warnings.append(message),
        )

        ok = window._preflight_recipe_hardware(
            [
                mini_dma_mod.AutomationStep(
                    "seek_target",
                    target_value=5.0,
                    basis=mini_dma_mod.HSW_BASIS_LOAD_G,
                ),
                mini_dma_mod.AutomationStep("set_current", current_mA=10.0),
            ]
        )

        assert ok is False
        assert len(warnings) == 1
        assert "Scale is not connected" in warnings[0]
        assert "Power supply is not connected" in warnings[0]
    finally:
        _close_test_window(window)


def test_length_setup_prompt_blocks_nested_automation_tick(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    nested_steps: list[str] = []

    def _prompt_reenters_event_loop(*args: object, **kwargs: object) -> tuple[float, bool]:
        window._handle_auto_ramp_tick()
        return 42.461, True

    try:
        monkeypatch.setattr(
            mini_dma_mod.QtWidgets.QInputDialog,
            "getDouble",
            _prompt_reenters_event_loop,
        )
        monkeypatch.setattr(
            window,
            "_handle_mark_setup_return_zero_step",
            lambda: nested_steps.append("advanced") or True,
        )
        window._automation_active = True
        window._automation_paused = False
        window._automation_index = 0
        window._automation_steps = [
            mini_dma_mod.AutomationStep("starting_length_prompt", note="setup_start_length"),
            mini_dma_mod.AutomationStep("mark_setup_return_zero", note="setup_return_zero_start"),
        ]

        window._handle_auto_ramp_tick()

        assert nested_steps == []
        assert window._automation_index == 1
        assert window._setup_measured_length_mm == pytest.approx(42.461)
    finally:
        _close_test_window(window)


def test_recipe_preflight_restores_real_gram_zero_load_reference_before_setup(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.check_tension_load_positive.setChecked(True)
        window.spin_zero_load_scale_g.setValue(0.0)
        window._latest_scale_value_g = 17.325
        window._latest_scale_timestamp = time.time()
        window._ensure_scale_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]
        window._apply_tic_configured_step_mode = lambda: (True, "PASS")  # type: ignore[method-assign]
        window._apply_tic_current_limit = lambda: (True, "PASS")  # type: ignore[method-assign]
        window._apply_tic_motion_limits = lambda: (True, "PASS")  # type: ignore[method-assign]

        ok = window._preflight_recipe_hardware(
            [
                mini_dma_mod.AutomationStep(
                    "seek_target",
                    target_value=20.0,
                    basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
                )
            ]
        )

        assert ok is True
        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
        assert window._current_effective_load_g() == pytest.approx(3.875)
    finally:
        _close_test_window(window)


def test_tic_status_warns_when_motor_power_vin_is_low(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def get_status(self) -> str:
            return "\n".join(
                [
                    "VIN voltage: 0.32 V",
                    "Operation state: Soft error",
                    "Current position: 42",
                    "Errors currently stopping the motor: Low VIN",
                ]
            )

    window._build_tic_controller = lambda: _FakeController()  # type: ignore[method-assign]

    try:
        assert window._refresh_tic_status() is True

        assert window._tic_motor_power_ok is False
        assert window._last_tic_vin_v == pytest.approx(0.32)
        assert "Motor power" in window.label_card_motion.text()
        assert "0.32 V" in window.label_tic_summary.text()
    finally:
        _close_test_window(window)


def test_tic_status_missing_vin_uses_recent_good_power_briefly(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = 1000.0

    class _FakeController:
        calls = 0

        def get_status(self) -> str:
            self.calls += 1
            if self.calls == 1:
                return "\n".join(
                    [
                        "VIN voltage: 12.00 V",
                        "Operation state: Normal",
                        "Current position: 42",
                        "Errors currently stopping the motor: none",
                    ]
                )
            return "\n".join(
                [
                    "Operation state: Normal",
                    "Current position: 42",
                    "Errors currently stopping the motor: none",
                ]
            )

    controller = _FakeController()
    monkeypatch.setattr(mini_dma_mod.time, "time", lambda: now_s)
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]

    try:
        assert window._refresh_tic_status() is True
        assert window._tic_motor_power_ok is True
        assert window._last_tic_vin_v == pytest.approx(12.0)

        now_s += 5.0
        assert window._refresh_tic_status() is True

        assert window._tic_motor_power_ok is None
        assert window._last_tic_vin_v == pytest.approx(12.0)
        assert "stale" in window.label_tic_summary.text().lower()
        assert "12.00 V" in window.label_tic_summary.text()
    finally:
        _close_test_window(window)


def test_tic_status_missing_vin_blocks_after_recent_good_power_expires(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = 1000.0

    class _FakeController:
        calls = 0

        def get_status(self) -> str:
            self.calls += 1
            if self.calls == 1:
                return "\n".join(
                    [
                        "VIN voltage: 12.00 V",
                        "Operation state: Normal",
                        "Current position: 42",
                        "Errors currently stopping the motor: none",
                    ]
                )
            return "\n".join(
                [
                    "Operation state: Normal",
                    "Current position: 42",
                    "Errors currently stopping the motor: none",
                ]
            )

    controller = _FakeController()
    monkeypatch.setattr(mini_dma_mod.time, "time", lambda: now_s)
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]

    try:
        assert window._refresh_tic_status() is True
        now_s += mini_dma_mod.TIC_MOTOR_POWER_STALE_GRACE_S + 1.0

        assert window._refresh_tic_status() is True

        assert window._tic_motor_power_ok is False
        assert "Motor power" in window.label_card_motion.text()
    finally:
        _close_test_window(window)


def test_recipe_preflight_blocks_when_tic_motor_power_is_low(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    warnings: list[str] = []

    class _FakeController:
        def get_status(self) -> str:
            return "\n".join(
                [
                    "VIN voltage: 0.10 V",
                    "Operation state: Soft error",
                    "Current position: 0",
                    "Errors currently stopping the motor: Low VIN",
                ]
            )

    window._build_tic_controller = lambda: _FakeController()  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._enable_motor_supply_output = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    try:
        ok = window._preflight_recipe_hardware([mini_dma_mod.AutomationStep("move", target_mm=1.0)])

        assert ok is False
        assert len(warnings) == 1
        assert "Motor controller" in warnings[0]
        assert "VIN 0.10 V" in warnings[0]
    finally:
        _close_test_window(window)


def test_recipe_preflight_reports_tic_status_read_failure(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    warnings: list[str] = []

    class _FakeController:
        def get_status(self) -> str:
            raise RuntimeError("Access denied")

    window._build_tic_controller = lambda: _FakeController()  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._enable_motor_supply_output = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    try:
        ok = window._preflight_recipe_hardware([mini_dma_mod.AutomationStep("move", target_mm=1.0)])

        assert ok is False
        assert len(warnings) == 1
        assert "Motor controller status could not be read" in warnings[0]
        assert "Access denied" in warnings[0]
        assert "other TMA/test processes" in warnings[0]
    finally:
        _close_test_window(window)


def test_recipe_preflight_requires_native_usb_for_tic_recipes(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    warnings: list[str] = []
    tic_checked: list[bool] = []

    window.check_tic_native_usb.setChecked(False)
    window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_scale_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: tic_checked.append(True) or True  # type: ignore[method-assign]
    monkeypatch.setattr(
        mini_dma_mod.QtWidgets.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    try:
        ok = window._preflight_recipe_hardware([mini_dma_mod.AutomationStep("move", target_mm=1.0)])

        assert ok is False
        assert len(warnings) == 1
        assert "native USB Tic control" in warnings[0]
        assert "ticcmd remains available for diagnostics only" in warnings[0]
        assert tic_checked == []
    finally:
        _close_test_window(window)


def test_controlled_current_sweep_defaults_match_copper_test_recipe(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_LOAD)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        _set_copper_current_sweep_defaults(window)

        steps, summary, interval_ms = window._build_automation_recipe()

        current_steps = [step for step in steps if step.action == "set_current"]
        current_sweep_steps = [step for step in steps if step.action == "sweep_current"]
        target_ramps = [step for step in steps if step.action == "ramp_target" and step.note != "setup_preload"]
        setup_ramps = [step for step in steps if step.action == "ramp_target" and step.note == "setup_preload"]

        assert interval_ms == 250
        assert len(setup_ramps) == 1
        assert len(current_sweep_steps) == 8
        assert len(target_ramps) == 5
        assert current_sweep_steps[0].basis == mini_dma_mod.HSW_BASIS_LOAD_G
        assert current_sweep_steps[0].target_value == pytest.approx(0.0)
        assert current_sweep_steps[0].current_start_mA == pytest.approx(1.0)
        assert current_sweep_steps[0].current_end_mA == pytest.approx(3.0)
        assert current_sweep_steps[0].current_ramp_rate_mA_s == pytest.approx(1.0)
        assert target_ramps[1].target_start_value == pytest.approx(0.0)
        assert target_ramps[1].target_end_value == pytest.approx(3.0)
        assert target_ramps[1].target_ramp_rate_value_s == pytest.approx(0.1)
        assert all(step.action != "record" for step in steps)
        assert {step.target_value for step in current_sweep_steps} == {0.0, 3.0, 6.0, 9.0}
        assert current_steps[0].current_mA == pytest.approx(1.0)
        assert max(step.current_end_mA for step in current_sweep_steps if step.current_end_mA is not None) == pytest.approx(3.0)
        assert target_ramps[-1].target_end_value == pytest.approx(0.0)
        assert "iso-load current sweep" in summary.lower()
        assert "control every 250 ms" in summary
        assert "log every 500 ms" in summary
        assert "mA/s" in summary
    finally:
        _close_test_window(window)


def test_current_sweep_first_overheating_uses_independent_preheat_target(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        _set_copper_current_sweep_defaults(window)
        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(100.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.spin_current_sweep_first_overheating_target_mpa.setValue(20.0)

        steps, summary, _interval_ms = window._build_automation_recipe()

        current_sweep_steps = [step for step in steps if step.action == "sweep_current"]
        ramp_steps = [
            step
            for step in steps
            if step.action == "ramp_target" and step.note != "setup_preload"
        ]

        assert [(step.basis, step.target_end_value) for step in ramp_steps[:3]] == [
            (mini_dma_mod.HSW_BASIS_STRESS_MPA, pytest.approx(20.0)),
            (mini_dma_mod.HSW_BASIS_STRESS_MPA, pytest.approx(50.0)),
            (mini_dma_mod.HSW_BASIS_STRESS_MPA, pytest.approx(100.0)),
        ]
        assert [step.target_value for step in current_sweep_steps[:6]] == [
            pytest.approx(20.0),
            pytest.approx(20.0),
            pytest.approx(50.0),
            pytest.approx(50.0),
            pytest.approx(100.0),
            pytest.approx(100.0),
        ]
        assert all(step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA for step in current_sweep_steps[:2])
        assert "first overheating enabled" in summary.lower()
        assert "20.0000 MPa preheat target" in summary
    finally:
        _close_test_window(window)


def test_current_sweep_first_overheating_can_use_independent_max_current(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        _set_copper_current_sweep_defaults(window)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(40.0)
        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(100.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.spin_current_sweep_first_overheating_target_mpa.setValue(20.0)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(60.0)

        steps, summary, _interval_ms = window._build_automation_recipe()

        current_sweep_steps = [step for step in steps if step.action == "sweep_current"]
        assert [(step.current_start_mA, step.current_end_mA) for step in current_sweep_steps[:6]] == [
            (pytest.approx(1.0), pytest.approx(60.0)),
            (pytest.approx(60.0), pytest.approx(1.0)),
            (pytest.approx(1.0), pytest.approx(40.0)),
            (pytest.approx(40.0), pytest.approx(1.0)),
            (pytest.approx(1.0), pytest.approx(40.0)),
            (pytest.approx(40.0), pytest.approx(1.0)),
        ]
        assert "60 mA" in summary
    finally:
        _close_test_window(window)


def test_current_sweep_returns_current_to_start_by_default(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_LOAD)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        _set_copper_current_sweep_defaults(window)

        steps, _summary, _interval_ms = window._build_automation_recipe()

        current_sweep_steps = [step for step in steps if step.action == "sweep_current"]

        assert len(current_sweep_steps) == 8
        assert [(step.current_start_mA, step.current_end_mA) for step in current_sweep_steps[:2]] == [
            (1.0, 3.0),
            (3.0, 1.0),
        ]
        assert all(step.current_end_mA == pytest.approx(1.0) for step in current_sweep_steps[1::2])
    finally:
        _close_test_window(window)


def test_current_sweep_can_skip_nominal_reverse_current_steps(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_LOAD)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        _set_copper_current_sweep_defaults(window)
        window.check_current_sweep_reverse_current.setChecked(False)

        steps, summary, _interval_ms = window._build_automation_recipe()

        current_sweep_steps = [step for step in steps if step.action == "sweep_current"]

        assert len(current_sweep_steps) == 4
        assert all(step.current_start_mA == pytest.approx(1.0) for step in current_sweep_steps)
        assert all(step.current_end_mA == pytest.approx(3.0) for step in current_sweep_steps)
        assert "Nominal current reverse sweeps are disabled." in summary
    finally:
        _close_test_window(window)


def test_current_sweep_recipe_payload_preserves_reverse_current_false(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        _set_copper_current_sweep_defaults(window)
        window.check_current_sweep_reverse_current.setChecked(False)

        payload = window._current_recipe_payload()

        assert payload["recipe"]["current_sweep"]["reverse_current"] is False

        window.check_current_sweep_reverse_current.setChecked(True)
        window._apply_recipe_payload(payload)

        assert window.check_current_sweep_reverse_current.isChecked() is False
    finally:
        _close_test_window(window)


def test_current_sweep_can_skip_final_target_return(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        _set_copper_current_sweep_defaults(window)
        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(100.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.check_current_sweep_return_target.setChecked(False)

        steps, _summary, _interval_ms = window._build_automation_recipe()

        target_ramps = [
            step
            for step in steps
            if step.action == "ramp_target" and step.note != "setup_preload"
        ]
        set_current_steps = [step for step in steps if step.action == "set_current"]

        assert [step.target_end_value for step in target_ramps] == [
            pytest.approx(50.0),
            pytest.approx(100.0),
        ]
        assert [step.target_value for step in set_current_steps] == [
            pytest.approx(50.0),
            pytest.approx(100.0),
        ]
    finally:
        _close_test_window(window)


def test_current_sweep_ramp_uses_elapsed_time_and_milliamp_resolution(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 1.0}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 1.0

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(current_mA)

        def initialize_output(self, *, current_mA: float, reset_on_start: bool) -> None:
            self.commands.append(current_mA)

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._seek_distribution_target = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
    ticks = iter([100.0, 100.0, 100.4, 101.1, 102.1])

    def _fake_monotonic() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 102.1

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", _fake_monotonic)
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=3.0,
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        current_start_mA=1.0,
        current_end_mA=3.0,
        current_ramp_rate_mA_s=1.0,
    )

    try:
        assert window._handle_current_sweep_step(step, 4) is False
        assert supply.commands == [1.0]

        assert window._handle_current_sweep_step(step, 4) is False
        assert supply.commands == [1.0]

        assert window._handle_current_sweep_step(step, 4) is False
        assert supply.commands == [1.0, 2.0]

        assert window._handle_current_sweep_step(step, 4) is True
        assert supply.commands == [1.0, 2.0, 3.0]
    finally:
        _close_test_window(window)


def test_current_sweep_logs_scheduled_points_when_strain_target_is_already_reached(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 1.0}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 1.0

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(current_mA)

        def initialize_output(self, *, current_mA: float, reset_on_start: bool) -> None:
            self.commands.append(current_mA)

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = 1.0
    window._supply_snapshot = {"current_mA": 1.0, "voltage_V": 0.06, "resistance_ohm": 60.0, "power_W": 0.00006}
    window._refresh_supply_snapshot = lambda **_kwargs: dict(window._supply_snapshot)  # type: ignore[method-assign]
    window._seek_distribution_target = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    window._session_active = True
    window._session_logging_enabled = True
    window._session_start_monotonic = 90.0
    window._session_start_wall_s = 90.0
    window._last_session_log_timestamp_s = 90.0
    window._latest_scale_timestamp = time.time()
    window._latest_scale_value_g = 21.2
    window._current_position_mm = 0.0
    window._effective_position_mm = 0.0
    window._position_reference_mm = 0.0
    window._write_point = lambda _point, **_kwargs: None  # type: ignore[method-assign]
    window._write_session_metadata = lambda **_kwargs: None  # type: ignore[method-assign]
    window._refresh_plots = lambda: None  # type: ignore[method-assign]
    window._handle_raw_scale_display_limit_status = lambda: False  # type: ignore[method-assign]
    ticks = iter([100.0, 100.0])
    walls = iter([100.0, 100.0, 100.0])

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: next(ticks, 100.0))
    monkeypatch.setattr(mini_dma_mod.time, "time", lambda: next(walls, 100.0))

    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=0.1,
        basis=mini_dma_mod.HSW_BASIS_STRAIN_PCT,
        current_start_mA=1.0,
        current_end_mA=80.0,
        current_ramp_rate_mA_s=1.0,
    )

    try:
        assert window._handle_current_sweep_step(step, 4) is False

        assert len(window._session_points) == 1
        assert window._session_points[-1].automation_phase == "current"
        assert window._session_points[-1].automation_basis == mini_dma_mod.HSW_BASIS_STRAIN_PCT
        assert window._session_points[-1].automation_target_value == pytest.approx(0.1)
    finally:
        window._session_active = False
        _close_test_window(window)


def test_current_sweep_holds_current_ramp_when_target_error_is_too_large(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 1.0}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 1.0

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(current_mA)

        def initialize_output(self, *, current_mA: float, reset_on_start: bool) -> None:
            self.commands.append(current_mA)

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    seek_calls: list[tuple[str, float, float]] = []
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._current_distribution_value = lambda *_args, **_kwargs: 10.0  # type: ignore[method-assign]
    base_s = time.time()
    signals = (
        mini_dma_mod.ScaleControlSignal(
            value=10.0,
            latest_value=10.0,
            noise=0.1,
            slope_per_s=6.0,
            sample_count=8,
            timestamp_s=base_s,
        ),
        mini_dma_mod.ScaleControlSignal(
            value=10.0,
            latest_value=10.0,
            noise=0.1,
            slope_per_s=6.0,
            sample_count=8,
            timestamp_s=base_s + 1.2,
        ),
    )
    signal_calls = 0

    def _fake_signal(*_args: object, **_kwargs: object) -> mini_dma_mod.ScaleControlSignal:
        nonlocal signal_calls
        signal = signals[min(signal_calls // 2, len(signals) - 1)]
        signal_calls += 1
        return signal

    window._scale_control_signal_for_basis = _fake_signal  # type: ignore[method-assign]

    def _fake_seek(basis: str, target_value: float, tolerance: float) -> bool:
        seek_calls.append((basis, target_value, tolerance))
        return False

    window._seek_distribution_target = _fake_seek  # type: ignore[method-assign]
    ticks = iter([100.0, 100.0, 102.0, 102.0])

    def _fake_monotonic() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 102.0

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", _fake_monotonic)
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=0.0,
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.0,
    )

    try:
        assert window._handle_current_sweep_step(step, 4) is False
        assert window._handle_current_sweep_step(step, 4) is False

        assert supply.commands == [1.0]
        assert len(seek_calls) == 2
        assert "holding current ramp" in window.log_output.toPlainText().lower()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_uses_absolute_stress_error_on_current_up_ramp(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (-11.0, 11.0, 0.25, 0.0)
    )
    base_s = time.time()
    signals = (
        mini_dma_mod.ScaleControlSignal(
            value=39.0,
            latest_value=39.0,
            noise=0.2,
            slope_per_s=-8.0,
            sample_count=8,
            timestamp_s=base_s,
        ),
        mini_dma_mod.ScaleControlSignal(
            value=39.0,
            latest_value=39.0,
            noise=0.2,
            slope_per_s=-8.0,
            sample_count=8,
            timestamp_s=base_s + 1.2,
        ),
    )
    signal_calls = 0

    def _fake_signal(*_args: object, **_kwargs: object) -> mini_dma_mod.ScaleControlSignal:
        nonlocal signal_calls
        signal = signals[min(signal_calls, len(signals) - 1)]
        signal_calls += 1
        return signal

    window._scale_control_signal_for_basis = _fake_signal  # type: ignore[method-assign]
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
    )

    try:
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=100.0)

        assert holding is True
        assert stopped is False
        assert window._current_sweep_ramp_hold_step_index == 4
    finally:
        _close_test_window(window)


def test_current_sweep_hold_pauses_despite_large_transient_noise(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (24.0, 24.0, 0.5, 12.0)
    )
    signal = mini_dma_mod.ScaleControlSignal(
        value=74.0,
        latest_value=74.0,
        noise=12.0,
        slope_per_s=10.0,
        sample_count=8,
        timestamp_s=time.time(),
    )
    window._scale_control_signal_for_basis = lambda *_args, **_kwargs: signal  # type: ignore[method-assign]
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=20.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
    )

    try:
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=100.0)

        assert holding is True
        assert stopped is False
        assert window._current_sweep_ramp_hold_step_index == 4
    finally:
        _close_test_window(window)


def test_current_sweep_hold_has_no_timeout_stop(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._current_sweep_ramp_hold_step_index = 4
    window._current_sweep_ramp_hold_started_s = 100.0
    window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (20.0, 20.0, 0.25, 0.0)
    )
    stop_calls: list[dict[str, object]] = []
    window._stop_auto_ramp = lambda **kwargs: stop_calls.append(kwargs)  # type: ignore[method-assign]
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
    )

    try:
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=10000.0)

        assert holding is True
        assert stopped is False
        assert stop_calls == []
        assert "recipe stopped because the current ramp was held" not in window.log_output.toPlainText().lower()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_resumes_inside_calculated_noise_recovery_band_when_window_spans_target(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._current_sweep_ramp_hold_step_index = 4
    window._current_sweep_ramp_hold_started_s = 100.0
    window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (4.5, 4.5, 0.25, 2.5)
    )
    now_s = time.time()
    for index, stress in enumerate((46.0, 48.5, 51.0, 54.0)):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress, window.spin_diameter.value())
        assert load_g is not None
        sample_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=sample_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.0,
    )

    try:
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=102.0)

        assert holding is False
        assert stopped is False
        assert window._current_sweep_ramp_hold_step_index is None
        assert "inside resume band" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_stays_paused_when_noise_band_is_one_sided(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._current_sweep_ramp_hold_step_index = 4
    window._current_sweep_ramp_hold_started_s = 100.0
    window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (22.0, 22.0, 1.8, 12.0)
    )
    now_s = time.time()
    for index, stress in enumerate((68.0, 71.0, 72.0, 76.0, 80.0)):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress, window.spin_diameter.value())
        assert load_g is not None
        sample_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=sample_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.0,
    )

    try:
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=102.0)

        assert holding is True
        assert stopped is False
        assert window._current_sweep_ramp_hold_step_index == 4
        assert "inside resume band" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_stays_paused_outside_recovery_band(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._current_sweep_ramp_hold_step_index = 4
    window._current_sweep_ramp_hold_started_s = 100.0
    window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (12.0, 12.0, 0.25, 0.0)
    )
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.0,
    )

    try:
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=102.0)

        assert holding is True
        assert stopped is False
        assert window._current_sweep_ramp_hold_step_index == 4
    finally:
        _close_test_window(window)


def test_current_sweep_hold_recovery_band_uses_physical_tolerance_without_fixed_floor(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._current_sweep_ramp_hold_step_index = 4
    window._current_sweep_ramp_hold_started_s = 100.0
    window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (7.0, 7.0, 6.0, 0.0)
    )
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.0,
    )

    try:
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=102.0)

        assert holding is True
        assert stopped is False
        assert window._current_sweep_ramp_hold_step_index == 4
    finally:
        _close_test_window(window)


def test_current_sweep_hold_uses_absolute_stress_error_on_current_down_ramp(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (-11.0, 11.0, 0.25, 0.0)
    )
    base_s = time.time()
    signals = (
        mini_dma_mod.ScaleControlSignal(
            value=39.0,
            latest_value=39.0,
            noise=0.2,
            slope_per_s=-8.0,
            sample_count=8,
            timestamp_s=base_s,
        ),
        mini_dma_mod.ScaleControlSignal(
            value=39.0,
            latest_value=39.0,
            noise=0.2,
            slope_per_s=-8.0,
            sample_count=8,
            timestamp_s=base_s + 1.2,
        ),
    )
    signal_calls = 0

    def _fake_signal(*_args: object, **_kwargs: object) -> mini_dma_mod.ScaleControlSignal:
        nonlocal signal_calls
        signal = signals[min(signal_calls, len(signals) - 1)]
        signal_calls += 1
        return signal

    window._scale_control_signal_for_basis = _fake_signal  # type: ignore[method-assign]
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=80.0,
        current_end_mA=1.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
    )

    try:
        holding, stopped = window._update_current_sweep_ramp_hold(
            step,
            4,
            now_s=100.0,
        )

        assert holding is False
        assert stopped is False

        holding, stopped = window._update_current_sweep_ramp_hold(
            step,
            4,
            now_s=102.0,
        )

        assert holding is True
        assert stopped is False
        assert window._current_sweep_ramp_hold_step_index == 4
    finally:
        _close_test_window(window)


def test_current_sweep_hold_requires_filtered_error_above_noise_band(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
    )

    try:
        window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: (3.0, 3.0, 0.25, 2.0)
        )
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=100.0)
        assert holding is False
        assert stopped is False

        window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: (7.0, 7.0, 0.25, 2.0)
        )
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=101.0)
        assert holding is False
        assert stopped is False
    finally:
        _close_test_window(window)


def test_current_sweep_hold_requires_filtered_error_above_transformation_band(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    base_s = time.time()
    signals = (
        mini_dma_mod.ScaleControlSignal(
            value=60.0,
            latest_value=60.0,
            noise=0.5,
            slope_per_s=7.0,
            sample_count=8,
            timestamp_s=base_s,
        ),
        mini_dma_mod.ScaleControlSignal(
            value=60.0,
            latest_value=60.0,
            noise=0.5,
            slope_per_s=7.0,
            sample_count=8,
            timestamp_s=base_s + 1.2,
        ),
    )
    signal_calls = 0

    def _fake_signal(*_args: object, **_kwargs: object) -> mini_dma_mod.ScaleControlSignal:
        nonlocal signal_calls
        signal = signals[min(signal_calls, len(signals) - 1)]
        signal_calls += 1
        return signal

    window._scale_control_signal_for_basis = _fake_signal  # type: ignore[method-assign]
    window._current_sweep_target_error_and_tolerance = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (10.0, 10.0, 0.25, 0.5)
    )
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
    )

    try:
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=100.0)
        assert holding is False
        assert stopped is False

        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=102.0)

        assert holding is True
        assert stopped is False
    finally:
        _close_test_window(window)


def test_current_sweep_does_not_hold_for_flat_filtered_stress_fluctuation(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    now_s = time.time()
    load_g = mini_dma_mod.load_g_from_stress_mpa(54.0, window.spin_diameter.value())
    assert load_g is not None
    for index in range(8):
        timestamp_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
    )

    try:
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=100.0)

        assert holding is False
        assert stopped is False
        assert window._current_sweep_ramp_hold_step_index is None
    finally:
        _close_test_window(window)


def test_current_sweep_hold_requires_persistent_moving_filtered_error(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window.spin_zero_load_scale_g.setValue(0.0)
    live_load_g = mini_dma_mod.load_g_from_stress_mpa(61.0, window.spin_diameter.value())
    assert live_load_g is not None
    window._latest_scale_value_g = live_load_g
    window._latest_scale_timestamp = time.time()
    base_s = time.time()
    signals = (
        mini_dma_mod.ScaleControlSignal(
                value=61.0,
                latest_value=61.0,
                noise=0.5,
                slope_per_s=6.0,
                sample_count=8,
                timestamp_s=base_s,
        ),
        mini_dma_mod.ScaleControlSignal(
                value=66.0,
                latest_value=66.0,
                noise=0.5,
                slope_per_s=6.0,
                sample_count=8,
                timestamp_s=base_s + 1.2,
        ),
    )
    signal_calls = 0

    def _fake_signal(*_args: object, **_kwargs: object) -> mini_dma_mod.ScaleControlSignal:
        nonlocal signal_calls
        signal = signals[min(signal_calls // 2, len(signals) - 1)]
        signal_calls += 1
        return signal

    window._scale_control_signal_for_basis = _fake_signal  # type: ignore[method-assign]
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
    )

    try:
        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=100.0)
        assert holding is False
        assert stopped is False

        holding, stopped = window._update_current_sweep_ramp_hold(step, 4, now_s=102.0)

        assert holding is True
        assert stopped is False
        assert window._current_sweep_ramp_hold_step_index == 4
    finally:
        _close_test_window(window)


def test_current_sweep_seek_uses_filtered_scale_signal_to_ignore_single_sample_spike(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.time()
    window.spin_zero_load_scale_g.setValue(0.0)
    window.check_tension_load_positive.setChecked(False)
    window.spin_diameter.setValue(0.014)
    window.spin_steps_per_mm.setValue(800.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    for index, stress_mpa in enumerate([49.6, 50.1, 50.2, 49.9, 80.0]):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress_mpa, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    window._last_motion_command_time_s = None
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.5,
        )

        assert reached is True
        assert moves == []
    finally:
        _close_test_window(window)


def test_current_sweep_reversal_waits_for_confirmed_filtered_sign(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.time()
    window.spin_zero_load_scale_g.setValue(0.0)
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_diameter.setValue(0.014)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(40.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        300.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = 40.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = 8.0
    window._current_position_mm = 1.0
    window._effective_position_mm = 1.0
    window._last_move_target_mm = 1.0
    window._last_effective_move_target_mm = 1.0
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]

    def _add_filtered_samples(stress_values: list[float], *, start_s: float) -> None:
        for index, stress_mpa in enumerate(stress_values):
            load_g = mini_dma_mod.load_g_from_stress_mpa(stress_mpa, window.spin_diameter.value())
            assert load_g is not None
            timestamp_s = start_s + index * 0.25
            window._scale_signal_buffer.add_sample(
                timestamp_s=timestamp_s,
                raw_g=load_g,
                applied_load_g=load_g,
                raw_text=f"{load_g:.5f} g",
            )
            window._latest_scale_timestamp = timestamp_s
            window._latest_scale_value_g = load_g

    try:
        _add_filtered_samples([56.0, 55.7, 55.4, 55.2, 55.0], start_s=now_s)
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.5,
        )

        assert reached is False
        assert moves == []
        assert "confirming filtered reversal" in window.log_output.toPlainText().lower()

        _add_filtered_samples([55.1, 55.3, 55.2], start_s=now_s + 2.0)
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.5,
        )

        assert reached is False
        assert moves
    finally:
        _close_test_window(window)


def test_current_sweep_hold_waits_when_filtered_stress_is_returning_to_target(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    base_s = time.time()
    window.spin_zero_load_scale_g.setValue(0.0)
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_diameter.setValue(0.014)
    window.spin_steps_per_mm.setValue(800.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        300.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = 40.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    window._current_distribution_value = lambda *_args, **_kwargs: 30.0  # type: ignore[method-assign]
    window._latest_scale_timestamp = base_s
    window._latest_scale_value_g = 0.0
    signals = (
        mini_dma_mod.ScaleControlSignal(
            value=30.0,
            latest_value=30.0,
            noise=0.2,
            slope_per_s=12.0,
            sample_count=8,
            timestamp_s=base_s,
        ),
        mini_dma_mod.ScaleControlSignal(
            value=32.0,
            latest_value=32.0,
            noise=0.2,
            slope_per_s=12.0,
            sample_count=8,
            timestamp_s=base_s + 1.2,
        ),
    )
    signal_calls = 0

    def _fake_signal(*_args: object, **_kwargs: object) -> mini_dma_mod.ScaleControlSignal:
        nonlocal signal_calls
        signal = signals[min(signal_calls, len(signals) - 1)]
        signal_calls += 1
        window._latest_scale_timestamp = signal.timestamp_s
        window._latest_scale_value_g = 0.0
        return signal

    window._scale_control_signal_for_basis = _fake_signal  # type: ignore[method-assign]
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]

    try:
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.5,
        ) is False
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.5,
        ) is False

        assert moves == []
        assert "waiting for the current-hold load/stress error" in window.log_output.toPlainText().lower()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_response_stiffness_ignores_opposite_direction_response(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_positive_motion_is_tension.setChecked(True)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_value_by_key[seek_key] = 40.0
    window._seek_last_effective_position_by_key[seek_key] = 0.0
    window._current_position_mm = 0.1
    window._effective_position_mm = 0.1

    try:
        window._update_current_sweep_hold_response_stiffness(
            seek_key,
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            35.0,
        )

        assert seek_key not in window._current_sweep_hold_response_stiffness_by_key
        assert window._current_sweep_hold_response_count_by_key.get(seek_key, 0) == 0

        window._update_current_sweep_hold_response_stiffness(
            seek_key,
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            45.0,
        )

        assert window._current_sweep_hold_response_count_by_key[seek_key] == 1
    finally:
        _close_test_window(window)


def test_current_sweep_hold_response_stiffness_requires_target_improvement(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_positive_motion_is_tension.setChecked(True)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_value_by_key[seek_key] = 55.0
    window._seek_last_effective_position_by_key[seek_key] = 0.0
    window._current_position_mm = 0.1
    window._effective_position_mm = 0.1

    try:
        window._update_current_sweep_hold_response_stiffness(
            seek_key,
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            60.0,
        )

        assert seek_key not in window._current_sweep_hold_response_stiffness_by_key
        assert window._current_sweep_hold_response_count_by_key.get(seek_key, 0) == 0
    finally:
        _close_test_window(window)


def test_current_sweep_ramp_resumes_without_wall_clock_current_jump(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 1.0}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 1.0

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(current_mA)

        def initialize_output(self, *, current_mA: float, reset_on_start: bool) -> None:
            self.commands.append(current_mA)

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    measured_values = iter([10.0, 10.0, 0.0, 0.0, 0.0, 0.0])
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._current_distribution_value = lambda *_args, **_kwargs: next(measured_values)  # type: ignore[method-assign]
    base_s = time.time()
    signals = (
        mini_dma_mod.ScaleControlSignal(
            value=10.0,
            latest_value=10.0,
            noise=0.1,
            slope_per_s=6.0,
            sample_count=8,
            timestamp_s=base_s,
        ),
        mini_dma_mod.ScaleControlSignal(
            value=10.0,
            latest_value=10.0,
            noise=0.1,
            slope_per_s=6.0,
            sample_count=8,
            timestamp_s=base_s + 1.2,
        ),
        mini_dma_mod.ScaleControlSignal(
            value=0.0,
            latest_value=0.0,
            noise=0.1,
            slope_per_s=0.0,
            sample_count=8,
            timestamp_s=base_s + 2.4,
        ),
    )
    signal_calls = 0

    def _fake_signal(*_args: object, **_kwargs: object) -> mini_dma_mod.ScaleControlSignal:
        nonlocal signal_calls
        signal = signals[min(signal_calls // 2, len(signals) - 1)]
        signal_calls += 1
        return signal

    window._scale_control_signal_for_basis = _fake_signal  # type: ignore[method-assign]
    window._seek_distribution_target = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
    ticks = iter([100.0, 100.0, 101.2, 102.3, 102.7])

    def _fake_monotonic() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 106.1

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", _fake_monotonic)
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=0.0,
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        current_start_mA=1.0,
        current_end_mA=5.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.0,
    )

    try:
        assert window._handle_current_sweep_step(step, 4) is False
        assert window._handle_current_sweep_step(step, 4) is False
        assert supply.commands == [1.0]

        assert window._handle_current_sweep_step(step, 4) is False
        assert "resumed current ramp" in window.log_output.toPlainText().lower()

        assert window._handle_current_sweep_step(step, 4) is False
        assert supply.commands == [1.0]

        assert window._handle_current_sweep_step(step, 4) is False
        assert supply.commands == [1.0, 3.0]
    finally:
        _close_test_window(window)


def test_current_sweep_hold_waits_for_resume_band_after_recovery_seek_accepts_target(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 1.0}

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 1.0

        def set_current_mA(self, _current_mA: float) -> None:
            return None

        def disconnect(self) -> None:
            return None

    window._supply_controller = _FakeSupply()  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._active_current_sweep_step_index = 4
    window._active_current_sweep_started_s = 90.0
    window._active_current_sweep_wall_started_s = 90.0
    window._active_current_sweep_last_setpoint_mA = 10.0
    window._current_sweep_ramp_hold_step_index = 4
    window._current_sweep_ramp_hold_started_s = 100.0
    window._update_current_sweep_ramp_hold = lambda *_args, **_kwargs: (True, False)  # type: ignore[method-assign]
    window._seek_distribution_target = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: 105.0)
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=80.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_resume_stable_s=1.0,
    )

    try:
        assert window._handle_current_sweep_step(step, 4) is False

        assert window._current_sweep_ramp_hold_step_index == 4
        assert window._active_current_sweep_started_s == pytest.approx(90.0)
        assert window._current_sweep_ramp_hold_seek_accepted_since_s == pytest.approx(105.0)
        log_text = window.log_output.toPlainText().lower()
        assert "confirming stable recovery before resuming current" in log_text
        assert "resumed current ramp after holding" not in log_text
    finally:
        _close_test_window(window)


def test_current_sweep_hold_resumes_after_recovery_seek_stays_accepted(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 1.0}

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 1.0

        def set_current_mA(self, _current_mA: float) -> None:
            return None

        def disconnect(self) -> None:
            return None

    window._supply_controller = _FakeSupply()  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._active_current_sweep_step_index = 4
    window._active_current_sweep_started_s = 90.0
    window._active_current_sweep_wall_started_s = 90.0
    window._active_current_sweep_last_setpoint_mA = 10.0
    window._current_sweep_ramp_hold_step_index = 4
    window._current_sweep_ramp_hold_started_s = 100.0
    window._current_sweep_ramp_hold_seek_accepted_since_s = 105.0
    window._update_current_sweep_ramp_hold = lambda *_args, **_kwargs: (True, False)  # type: ignore[method-assign]
    window._seek_distribution_target = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: 106.2)
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=80.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_resume_stable_s=1.0,
    )

    try:
        assert window._handle_current_sweep_step(step, 4) is False

        assert window._current_sweep_ramp_hold_step_index is None
        assert window._active_current_sweep_started_s == pytest.approx(96.2)
        assert "recovery seek stayed accepted for 1.00 s" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_resume_from_hold_starts_post_hold_throttle(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._active_current_sweep_started_s = 90.0
    window._current_sweep_ramp_hold_started_s = 100.0
    window._active_current_sweep_display_direction = 1.0

    try:
        window._resume_current_sweep_ramp_from_hold(now_s=106.0, reason="test")

        assert window._active_current_sweep_started_s == pytest.approx(96.0)
        assert window._active_current_sweep_last_schedule_update_s == pytest.approx(106.0)
        assert window._current_sweep_post_hold_throttle_until_s == pytest.approx(
            106.0 + mini_dma_mod.SERVO_CURRENT_SWEEP_POST_HOLD_THROTTLE_S
        )
    finally:
        _close_test_window(window)


def test_current_sweep_resume_from_hold_skips_post_hold_throttle_on_down_sweep(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._active_current_sweep_started_s = 90.0
    window._current_sweep_ramp_hold_started_s = 100.0
    window._active_current_sweep_display_direction = -1.0

    try:
        window._resume_current_sweep_ramp_from_hold(now_s=106.0, reason="test")

        assert window._active_current_sweep_started_s == pytest.approx(96.0)
        assert window._active_current_sweep_last_schedule_update_s == pytest.approx(106.0)
        assert window._current_sweep_post_hold_throttle_until_s == pytest.approx(0.0)
    finally:
        _close_test_window(window)


def test_current_sweep_post_hold_throttle_slows_effective_ramp_schedule(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._active_current_sweep_started_s = 90.0
    window._active_current_sweep_last_schedule_update_s = 100.0
    window._current_sweep_post_hold_throttle_until_s = 108.0

    try:
        window._apply_current_sweep_post_hold_ramp_throttle(now_s=104.0)

        expected_delay = 4.0 * (1.0 - mini_dma_mod.SERVO_CURRENT_SWEEP_POST_HOLD_THROTTLE_FACTOR)
        assert window._active_current_sweep_started_s == pytest.approx(90.0 + expected_delay)
        assert window._active_current_sweep_last_schedule_update_s == pytest.approx(104.0)
        assert window._current_sweep_post_hold_throttle_until_s == pytest.approx(108.0)

        window._apply_current_sweep_post_hold_ramp_throttle(now_s=110.0)

        expected_delay += 4.0 * (1.0 - mini_dma_mod.SERVO_CURRENT_SWEEP_POST_HOLD_THROTTLE_FACTOR)
        assert window._active_current_sweep_started_s == pytest.approx(90.0 + expected_delay)
        assert window._active_current_sweep_last_schedule_update_s == pytest.approx(110.0)
        assert window._current_sweep_post_hold_throttle_until_s == pytest.approx(0.0)
    finally:
        _close_test_window(window)


def test_current_sweep_scheduled_log_waits_for_fresh_scale_without_stopping(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._session_active = True
    window._session_logging_enabled = True
    window._session_start_monotonic = time.monotonic() - 10.0
    window._last_session_log_timestamp_s = time.time() - 10.0
    window._last_motion_command_time_s = time.time()
    window._latest_scale_timestamp = window._last_motion_command_time_s - 0.1
    window._latest_scale_value_g = 21.0
    window._set_automation_context(
        phase="current",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=200.0,
        plateau_index=3,
    )
    step = mini_dma_mod.AutomationStep(
        "set_current",
        target_value=200.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_mA=1.0,
        note="3",
    )

    try:
        assert window._record_scheduled_recipe_point(step) is True
        assert window._automation_active is True
        assert len(window._session_points) == 0
        assert "Point not recorded because load/stress feedback is stale" not in window.log_output.toPlainText()
        assert "Waiting for a fresh scale reading" in window.log_output.toPlainText()
    finally:
        window._session_active = False
        _close_test_window(window)


def test_current_sweep_settle_advances_after_timed_recovery_even_if_target_is_noisy(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    attempts = {"count": 0}

    def _fake_seek(*_args: object, **_kwargs: object) -> bool:
        attempts["count"] += 1
        return attempts["count"] >= 2

    window._seek_distribution_target = _fake_seek  # type: ignore[method-assign]
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._automation_steps = [
        mini_dma_mod.AutomationStep(
            "settle",
            target_value=100.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            duration_s=0.0,
            note="2",
        )
    ]
    window._automation_total_steps = 1
    window._automation_index = 0

    try:
        window._handle_auto_ramp_tick()

        assert window._automation_active is True
        assert window._automation_index == 1
        assert attempts["count"] == 1
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_setup_preload_settle_requires_continuous_target_stability_in_current_sweep(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = [100.0]
    seek_results = [False, True, True]

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: now_s[0])

    def _fake_seek(*_args: object, **_kwargs: object) -> bool:
        return seek_results.pop(0)

    window._seek_distribution_target = _fake_seek  # type: ignore[method-assign]
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._automation_steps = [
        mini_dma_mod.AutomationStep(
            "settle",
            target_value=20.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            duration_s=3.0,
            note="setup_preload",
        )
    ]
    window._automation_total_steps = 1
    window._automation_index = 0

    try:
        window._handle_auto_ramp_tick()

        assert window._automation_active is True
        assert window._automation_index == 0
        assert window._active_timed_step_index is None

        now_s[0] = 101.0
        window._handle_auto_ramp_tick()

        assert window._automation_index == 0
        assert window._active_timed_step_index == 0

        now_s[0] = 104.1
        window._handle_auto_ramp_tick()

        assert window._automation_index == 1
        assert window._active_timed_step_index is None
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_current_sweep_voltage_limit_reverses_current_to_start_without_stopping_recipe(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 1.0}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 1.0

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(current_mA)

        def initialize_output(self, *, current_mA: float, reset_on_start: bool) -> None:
            self.commands.append(current_mA)

        def measure(self) -> dict[str, float | None]:
            return {
                "current_mA": 4.0,
                "voltage_V": 5.02,
                "resistance_ohm": 1.25,
                "power_W": 0.020,
            }

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = 4.0
    window._active_current_sweep_step_index = 4
    window._active_current_sweep_started_s = 99.0
    window._active_current_sweep_last_setpoint_mA = 4.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window.spin_supply_voltage_limit.setValue(5.0)
    window._seek_distribution_target = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    ticks = iter([100.0, 100.6, 101.2])

    def _fake_monotonic() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 101.6

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", _fake_monotonic)
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=3.0,
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        current_start_mA=2.0,
        current_end_mA=6.0,
        current_ramp_rate_mA_s=2.0,
    )

    try:
        window._refresh_supply_snapshot(force=True)

        assert window._automation_active is True

        assert window._handle_current_sweep_step(step, 4) is False
        assert supply.commands == [3.0]

        assert window._handle_current_sweep_step(step, 4) is True
        assert supply.commands == [3.0, 2.0]
        assert window._supply_last_setpoint_mA == pytest.approx(2.0)
        assert "reversing recipe current back to the sweep start current" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_voltage_limited_unwind_keeps_return_leg_without_high_current_restart(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 1.0}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 1.0

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(current_mA)

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = 4.0
    window._active_current_sweep_step_index = 4
    window._active_current_sweep_started_s = 99.0
    window._active_current_sweep_last_setpoint_mA = 4.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._seek_distribution_target = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    ticks = iter([100.0, 100.6])

    def _fake_monotonic() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 101.6

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", _fake_monotonic)
    up_step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=70.0,
        current_ramp_rate_mA_s=5.0,
        note="1",
    )
    return_step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=70.0,
        current_end_mA=1.0,
        current_ramp_rate_mA_s=5.0,
        note="1",
    )
    window._automation_steps = [
        mini_dma_mod.AutomationStep("set_current", target_value=50.0, basis=mini_dma_mod.HSW_BASIS_STRESS_MPA),
        mini_dma_mod.AutomationStep("ramp_target", target_value=50.0, basis=mini_dma_mod.HSW_BASIS_STRESS_MPA),
        mini_dma_mod.AutomationStep("record", target_value=50.0, basis=mini_dma_mod.HSW_BASIS_STRESS_MPA),
        mini_dma_mod.AutomationStep("settle", target_value=50.0, basis=mini_dma_mod.HSW_BASIS_STRESS_MPA),
        up_step,
        return_step,
    ]
    window._current_sweep_voltage_limit_step_index = 4
    window._current_sweep_voltage_limit_started_s = 100.0
    window._current_sweep_voltage_limit_start_mA = 4.0

    try:
        assert window._handle_current_sweep_step(up_step, 4) is False
        assert supply.commands == []

        assert window._handle_current_sweep_step(up_step, 4) is True
        assert supply.commands == [1.0]
        assert 5 in window._current_sweep_voltage_limited_return_steps

        assert window._handle_current_sweep_step(return_step, 5) is True

        assert supply.commands == [1.0]
        assert 5 not in window._current_sweep_voltage_limited_return_steps
        log_text = window.log_output.toPlainText()
        assert "keeping the unwind as the return leg" in log_text
        assert "Skipped paired nominal reverse current sweep" in log_text
    finally:
        _close_test_window(window)


def test_voltage_limit_during_nominal_return_keeps_rate_limited_return(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(current_mA)

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = 60.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._automation_steps = [
        mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=50.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=1.0,
            current_end_mA=60.0,
            current_ramp_rate_mA_s=1.0,
            note="1",
        ),
        mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=50.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=60.0,
            current_end_mA=1.0,
            current_ramp_rate_mA_s=1.0,
            note="1",
        ),
    ]
    window._seek_distribution_target = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    window._current_sweep_voltage_limit_step_index = 1
    window._current_sweep_voltage_limit_started_s = 100.0
    window._current_sweep_voltage_limit_start_mA = 60.0
    ticks = iter([100.0, 100.4])

    def _fake_monotonic() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 100.4

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", _fake_monotonic)
    return_step = window._automation_steps[1]

    try:
        assert window._handle_current_sweep_step(return_step, 1) is False
        assert supply.commands == pytest.approx([60.0, 59.6])

        assert window._handle_current_sweep_step(return_step, 1) is False
        assert supply.commands == pytest.approx([60.0, 59.6])
        assert window._current_sweep_voltage_limit_step_index is None
        assert "continuing the rate-limited return" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_voltage_limit_uses_measured_current_when_setpoint_state_is_missing(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(current_mA)

        def measure(self) -> dict[str, float | None]:
            return {
                "current_mA": 49.7,
                "voltage_V": 32.002,
                "resistance_ohm": 644.0,
                "power_W": 1.59,
            }

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = None
    window._active_current_sweep_step_index = 4
    window._active_current_sweep_started_s = 90.0
    window._active_current_sweep_last_setpoint_mA = None
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window.spin_supply_voltage_limit.setValue(32.05)
    window._seek_distribution_target = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    ticks = iter([100.0, 100.0, 100.2, 100.4])

    def _fake_monotonic() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 100.6

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", _fake_monotonic)
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=50.0,
        current_ramp_rate_mA_s=1.0,
    )

    try:
        window._refresh_supply_snapshot(force=True)

        assert window._current_sweep_voltage_limit_step_index == 4
        assert window._current_sweep_voltage_limit_start_mA == pytest.approx(49.6)
        assert window._handle_current_sweep_step(step, 4) is False
        assert supply.commands == [49.6]
        assert window._handle_current_sweep_step(step, 4) is False
        assert supply.commands == pytest.approx([49.6, 49.4])
        assert window._active_current_sweep_step_index == 4
    finally:
        _close_test_window(window)


def test_current_sweep_voltage_limit_keeps_current_hold_active(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def measure(self) -> dict[str, float | None]:
            return {
                "current_mA": 49.7,
                "voltage_V": 32.002,
                "resistance_ohm": 644.0,
                "power_W": 1.59,
            }

        def disconnect(self) -> None:
            return None

    window._supply_controller = _FakeSupply()  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = 49.8
    window._active_current_sweep_step_index = 4
    window._active_current_sweep_last_setpoint_mA = 49.8
    window._current_sweep_ramp_hold_step_index = 4
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window.spin_supply_voltage_limit.setValue(32.05)

    try:
        window._refresh_supply_snapshot(force=True)

        assert window._current_sweep_voltage_limit_step_index is None
        assert window._current_sweep_ramp_hold_step_index == 4
        assert "keeping the held current" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_voltage_limit_unwind_holds_current_when_target_load_collapses(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(float(current_mA))

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    seeks: list[tuple[str | None, float | None, float]] = []
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._active_current_sweep_step_index = 4
    window._active_current_sweep_started_s = 100.0
    window._active_current_sweep_wall_started_s = 100.0
    window._active_current_sweep_last_schedule_update_s = 100.0
    window._active_current_sweep_last_setpoint_mA = 60.0
    window._active_current_sweep_display_direction = -1.0
    window._current_sweep_voltage_limit_step_index = 4
    window._current_sweep_voltage_limit_started_s = 100.0
    window._current_sweep_voltage_limit_start_mA = 60.0
    window._current_sweep_target_error_and_tolerance = lambda *_args, **_kwargs: (-50.0, 50.0, 1.0, 0.0)  # type: ignore[method-assign]
    window._current_sweep_hold_entry_confirmed = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    window._seek_distribution_target = lambda basis, target, tolerance: seeks.append((basis, target, tolerance)) or False  # type: ignore[method-assign]
    window._maybe_record_scheduled_point = lambda **_kwargs: None  # type: ignore[method-assign]
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: 105.0)
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=70.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=1.0,
        current_hold_resume_tolerance_factor=0.5,
        current_hold_resume_stable_s=0.0,
        note="4",
    )

    try:
        assert window._handle_current_sweep_step(step, 4) is False

        assert supply.commands == []
        assert window._current_sweep_ramp_hold_step_index == 4
        assert window._current_sweep_voltage_limit_step_index == 4
        assert seeks == [
            (
                mini_dma_mod.HSW_BASIS_STRESS_MPA,
                50.0,
                pytest.approx(window._automation_tolerance_for_step(step)),
            )
        ]
        assert "Holding current ramp at 60.000 mA" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_pauses_voltage_limit_unwind_schedule(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._active_current_sweep_started_s = 90.0
        window._current_sweep_voltage_limit_step_index = 4
        window._current_sweep_voltage_limit_started_s = 90.0
        window._current_sweep_ramp_hold_step_index = 4
        window._current_sweep_ramp_hold_started_s = 100.0

        window._resume_current_sweep_ramp_from_hold(now_s=105.0, reason="test")

        assert window._active_current_sweep_started_s == pytest.approx(95.0)
        assert window._current_sweep_voltage_limit_started_s == pytest.approx(95.0)
    finally:
        _close_test_window(window)


def test_voltage_limit_unwind_waits_for_target_recovery_before_completing(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.commands: list[float] = []

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def set_current_mA(self, current_mA: float) -> None:
            self.commands.append(float(current_mA))

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._active_current_sweep_step_index = 4
    window._active_current_sweep_last_setpoint_mA = 10.0
    window._current_sweep_voltage_limit_step_index = 4
    window._current_sweep_voltage_limit_started_s = 100.0
    window._current_sweep_voltage_limit_start_mA = 10.0
    window._current_distribution_value = lambda *_args, **_kwargs: 0.0  # type: ignore[method-assign]
    window._seek_distribution_target = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
    trace_rows: list[dict[str, object]] = []
    window._write_control_trace = lambda **kwargs: trace_rows.append(dict(kwargs))  # type: ignore[method-assign]
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: 120.0)
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=20.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=50.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        note="4",
    )

    try:
        assert window._handle_current_sweep_voltage_unwind(
            step,
            step_index=4,
            ramp_rate_mA_s=1.0,
            target_mA=1.0,
        ) is False

        assert supply.commands == [pytest.approx(1.0)]
        assert window._current_sweep_voltage_limit_step_index == 4
        assert window._active_current_sweep_step_index == 4
        assert trace_rows[-1]["reason"] == "current_returned_waiting_for_target_recovery"
    finally:
        _close_test_window(window)


def test_voltage_limit_unwind_open_circuit_stops_before_mechanical_seek(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.output_off_calls = 0

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def output_off(self) -> None:
            self.output_off_calls += 1

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    recovery_prompts: list[str] = []
    trace_rows: list[dict[str, object]] = []
    seek_calls: list[tuple[object, ...]] = []
    window._ask_wire_break_recovery_after_stop = recovery_prompts.append  # type: ignore[method-assign]
    window._maybe_offer_run_cleanup = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = 25.4
    window._supply_snapshot = {
        "current_mA": 0.1,
        "voltage_V": 32.054,
        "resistance_ohm": None,
        "power_W": 0.0,
    }
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._active_current_sweep_step_index = 4
    window._active_current_sweep_last_setpoint_mA = 25.4
    window._current_sweep_voltage_limit_step_index = 4
    window._current_sweep_voltage_limit_started_s = 100.0
    window._current_sweep_voltage_limit_start_mA = 25.4
    window.spin_supply_voltage_limit.setValue(32.05)
    window._write_control_trace = lambda **kwargs: trace_rows.append(dict(kwargs))  # type: ignore[method-assign]
    window._seek_distribution_target = lambda *args, **_kwargs: seek_calls.append(args) or False  # type: ignore[method-assign]
    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: 101.0)
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=950.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=50.0,
        current_ramp_rate_mA_s=1.0,
        note="4",
    )

    try:
        assert window._handle_current_sweep_voltage_unwind(
            step,
            step_index=4,
            ramp_rate_mA_s=1.0,
            target_mA=1.0,
        ) is True

        assert window._automation_active is False
        assert window._current_sweep_voltage_limit_step_index is None
        assert supply.output_off_calls >= 1
        assert seek_calls == []
        assert len(recovery_prompts) == 1
        assert "Wire break detected" in recovery_prompts[0]
        assert trace_rows[-1]["decision"] == "stop"
        assert trace_rows[-1]["reason"] == "wire_break_or_contact_loss"
        assert trace_rows[-1]["result"] == "stopped"
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_current_sweep_open_circuit_zero_current_stops_recipe_and_offers_recovery(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.output_off_calls = 0

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def measure(self) -> dict[str, float | None]:
            return {
                "current_mA": 0.0,
                "voltage_V": 30.0,
                "resistance_ohm": None,
                "power_W": 0.0,
            }

        def output_off(self) -> None:
            self.output_off_calls += 1

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    recovery_prompts: list[str] = []
    window._ask_wire_break_recovery_after_stop = recovery_prompts.append  # type: ignore[method-assign]
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = 55.0
    window._active_current_sweep_step_index = 4
    window._active_current_sweep_last_setpoint_mA = 55.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._automation_phase = "current"
    window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
    window._session_active = True
    window._session_logging_enabled = True
    window._latest_scale_value_g = window.spin_zero_load_scale_g.value()
    window._latest_scale_timestamp = time.time()
    window.spin_supply_voltage_limit.setValue(30.0)

    try:
        recorded = window._record_current_point(quiet=True, require_fresh_after_move=False)

        assert recorded is False
        assert window._automation_active is False
        assert window._session_active is False
        assert window._session_points == []
        assert window._current_sweep_voltage_limit_step_index is None
        assert supply.output_off_calls >= 1
        assert len(recovery_prompts) == 1
        assert "Wire break detected" in recovery_prompts[0]
        assert "measured current 0 mA" in recovery_prompts[0]
        log_text = window.log_output.toPlainText()
        assert "Wire break detected" in log_text
        assert "reversing recipe current" not in log_text
    finally:
        window._automation_active = False
        window._session_active = False
        _close_test_window(window)


def test_wire_break_stop_from_worker_defers_recovery_prompt_to_ui_thread(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    scheduled_callbacks: list[object] = []
    recovery_prompts: list[str] = []
    ui_thread_id = window._ui_thread_id
    run_on_ui_thread = window._run_on_ui_thread
    window._ask_wire_break_recovery_after_stop = recovery_prompts.append  # type: ignore[method-assign]
    window._run_on_ui_thread = scheduled_callbacks.append  # type: ignore[method-assign]
    window._ui_thread_id = -1
    window._automation_active = True
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = 55.0
    window._supply_snapshot = {
        "current_mA": 0.0,
        "voltage_V": 30.0,
        "resistance_ohm": None,
        "power_W": 0.0,
    }

    try:
        window._stop_for_wire_break()

        assert recovery_prompts == []
        assert scheduled_callbacks
        assert window._wire_break_stop_in_progress is True
    finally:
        window._ui_thread_id = ui_thread_id
        window._run_on_ui_thread = run_on_ui_thread  # type: ignore[method-assign]
        window._wire_break_stop_in_progress = False
        window._automation_active = False
        window._session_active = False
        _close_test_window(window)


def test_continuity_open_circuit_at_one_milliamp_stops_calibration(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        profile = {"reset_on_start": False, "current_resolution_mA": 0.2}

        def __init__(self) -> None:
            self.output_off_calls = 0

        def is_connected(self) -> bool:
            return True

        def current_resolution_mA(self) -> float:
            return 0.2

        def measure(self) -> dict[str, float | None]:
            return {
                "current_mA": 0.0,
                "voltage_V": 30.0,
                "resistance_ohm": None,
                "power_W": 0.0,
            }

        def output_off(self) -> None:
            self.output_off_calls += 1

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    recovery_prompts: list[str] = []
    window._ask_wire_break_recovery_after_stop = recovery_prompts.append  # type: ignore[method-assign]
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = 1.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._automation_phase = "target_ramp"
    window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
    window._session_active = True
    window._session_logging_enabled = False
    window.spin_supply_voltage_limit.setValue(30.0)
    window.check_continuity_monitor.setChecked(True)
    window.spin_continuity_current_mA.setValue(1.0)

    try:
        window._refresh_supply_snapshot(force=True)

        assert window._automation_active is False
        assert window._session_active is False
        assert supply.output_off_calls >= 1
        assert len(recovery_prompts) == 1
        assert "Wire break detected" in recovery_prompts[0]
    finally:
        window._automation_active = False
        window._session_active = False
        _close_test_window(window)


def test_continuity_current_makes_non_current_recipe_require_supply(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_continuity_monitor.setChecked(True)

    try:
        assert window._recipe_requires_supply([mini_dma_mod.AutomationStep("move", target_mm=1.0)]) is True

        window.check_continuity_monitor.setChecked(False)

        assert window._recipe_requires_supply([mini_dma_mod.AutomationStep("move", target_mm=1.0)]) is False
    finally:
        _close_test_window(window)


def test_current_sweep_length_setup_starts_continuity_current(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    currents: list[float] = []
    window.check_continuity_monitor.setChecked(True)
    window.spin_continuity_current_mA.setValue(1.0)
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_recipe_current_mA = lambda current_mA, **_kwargs: currents.append(current_mA) or True  # type: ignore[method-assign]
    steps = [
        mini_dma_mod.AutomationStep("starting_length_prompt", note="setup_start_length"),
        mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=50.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=1.0,
            current_end_mA=70.0,
            current_ramp_rate_mA_s=1.0,
            note="1",
        ),
    ]

    try:
        assert window._prepare_continuity_current_for_recipe(steps) is True

        assert currents == [pytest.approx(1.0)]
        assert "Continuity monitor enabled" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_hmp4030_initial_current_command_preserves_sub_milliamp_resolution() -> None:
    written: list[bytes] = []

    class _FakePort:
        is_open = True

        def reset_input_buffer(self) -> None:
            return None

        def write(self, payload: bytes) -> None:
            written.append(payload)

        def flush(self) -> None:
            return None

    controller = mini_dma_mod.PowerSupplyController(
        port_name="COM3",
        baudrate=115200,
        profile_id="hmp4030",
        max_voltage_v=5.0,
    )
    controller._serial = _FakePort()  # type: ignore[assignment]

    controller.initialize_output(current_mA=0.2, reset_on_start=False)

    assert b"CURR 0.0002\n" in written


def test_supply_shutdown_turns_output_off_and_resets_current_channel() -> None:
    written: list[bytes] = []

    class _FakePort:
        is_open = True

        def reset_input_buffer(self) -> None:
            return None

        def write(self, payload: bytes) -> None:
            written.append(payload)

        def flush(self) -> None:
            return None

    controller = mini_dma_mod.PowerSupplyController(
        port_name="COM3",
        baudrate=115200,
        profile_id="hmp4030",
        max_voltage_v=30.0,
        channel_select=3,
    )
    controller._serial = _FakePort()  # type: ignore[assignment]

    controller.shutdown_output(reset_voltage_v=1.0, reset_current_mA=1.0)

    assert written == [
        b"INST:NSEL 3\n",
        b"OUTP OFF\n",
        b"VOLT 1.000\n",
        b"CURR 0.0010\n",
        b"OUTP OFF\n",
    ]


def test_shared_broker_supply_controller_leases_current_and_motor_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeBrokerClient:
        def __init__(self, *, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.calls: list[tuple[str, dict[str, object]]] = []

        def request(self, action: str, **payload: object) -> dict[str, object]:
            self.calls.append((action, dict(payload)))
            return {"ok": True, "snapshot": {"model": "hmp4040"}}

        def lease(self, *, channel: int, owner: str, role: str) -> dict[str, object]:
            self.calls.append(("lease", {"channel": channel, "owner": owner, "role": role}))
            return {"lease_id": f"lease-{channel}"}

        def release(self, *, channel: int, lease_id: str) -> None:
            self.calls.append(("release", {"channel": channel, "lease_id": lease_id}))

        def configure_channel(
            self,
            *,
            channel: int,
            lease_id: str,
            voltage_v: float,
            current_a: float,
            output_on: bool,
        ) -> None:
            self.calls.append(
                (
                    "configure_channel",
                    {
                        "channel": channel,
                        "lease_id": lease_id,
                        "voltage_v": voltage_v,
                        "current_a": current_a,
                        "output_on": output_on,
                    },
                )
            )

        def set_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
            self.calls.append(
                ("set_current", {"channel": channel, "lease_id": lease_id, "current_mA": current_mA})
            )

        def set_output(self, *, channel: int, lease_id: str, output_on: bool) -> None:
            self.calls.append(
                ("set_output", {"channel": channel, "lease_id": lease_id, "output_on": output_on})
            )

        def measure_channel(self, *, channel: int) -> dict[str, float | None]:
            self.calls.append(("measure_channel", {"channel": channel}))
            return {
                "voltage_V": 0.5,
                "current_mA": 10.0,
            }

    clients: list[_FakeBrokerClient] = []

    def _client_factory(*, host: str, port: int) -> _FakeBrokerClient:
        client = _FakeBrokerClient(host=host, port=port)
        clients.append(client)
        return client

    monkeypatch.setattr(mini_dma_mod, "BrokerJsonClient", _client_factory)
    controller = mini_dma_mod.SharedBrokerSupplyController(
        host="127.0.0.1",
        port=8765,
        max_voltage_v=1.0,
        current_channel=4,
        motor_channel=3,
        current_limit_a=0.08,
        motor_voltage_limit_v=12.0,
        motor_current_limit_a=0.5,
    )

    controller.connect()
    controller.configure_channel(channel=3, voltage_v=12.0, current_a=0.5, output_on=True)
    controller.initialize_output(current_mA=10.0, reset_on_start=True)
    controller.set_current_mA(10.4)
    readback = controller.measure()
    assert readback["current_mA"] == pytest.approx(10.0)
    assert readback["resistance_ohm"] == pytest.approx(50.0)
    assert readback["power_W"] == pytest.approx(0.005)
    controller.shutdown_output(reset_voltage_v=1.0, reset_current_mA=1.0)
    controller.disconnect()

    assert clients[0].host == "127.0.0.1"
    assert clients[0].port == 8765
    assert clients[0].calls == [
        ("snapshot", {}),
        ("snapshot", {}),
        (
            "assign_role",
            {
                "channel": 3,
                "role": mini_dma_mod.ROLE_MINI_DMA_MOTOR,
                "confirmed": True,
                "voltage_limit_v": 12.0,
                "current_limit_a": 0.5,
            },
        ),
        (
            "lease",
            {
                "channel": 3,
                "owner": "mini_dma_logger",
                "role": mini_dma_mod.ROLE_MINI_DMA_MOTOR,
            },
        ),
        (
            "configure_channel",
            {
                "channel": 3,
                "lease_id": "lease-3",
                "voltage_v": 12.0,
                "current_a": 0.5,
                "output_on": True,
            },
        ),
        ("snapshot", {}),
        (
            "assign_role",
            {
                "channel": 4,
                "role": mini_dma_mod.ROLE_MINI_DMA_CURRENT,
                "confirmed": True,
                "voltage_limit_v": 1.0,
                "current_limit_a": 0.08,
            },
        ),
        (
            "lease",
            {
                "channel": 4,
                "owner": "mini_dma_logger",
                "role": mini_dma_mod.ROLE_MINI_DMA_CURRENT,
            },
        ),
        (
            "configure_channel",
            {
                "channel": 4,
                "lease_id": "lease-4",
                "voltage_v": 1.0,
                "current_a": 0.01,
                "output_on": True,
            },
        ),
        ("set_current", {"channel": 4, "lease_id": "lease-4", "current_mA": 10.4}),
        ("measure_channel", {"channel": 4}),
        ("set_output", {"channel": 4, "lease_id": "lease-4", "output_on": False}),
        (
            "configure_channel",
            {
                "channel": 4,
                "lease_id": "lease-4",
                "voltage_v": 1.0,
                "current_a": 0.001,
                "output_on": False,
            },
        ),
        ("set_output", {"channel": 4, "lease_id": "lease-4", "output_on": False}),
        (
            "configure_channel",
            {
                "channel": 4,
                "lease_id": "lease-4",
                "voltage_v": 1.0,
                "current_a": 0.001,
                "output_on": False,
            },
        ),
        ("release", {"channel": 4, "lease_id": "lease-4"}),
        ("set_output", {"channel": 3, "lease_id": "lease-3", "output_on": False}),
        ("release", {"channel": 3, "lease_id": "lease-3"}),
    ]


def test_shared_broker_supply_controller_does_not_rewrite_confirmed_channel_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeBrokerClient:
        def __init__(self, *, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.calls: list[tuple[str, dict[str, object]]] = []

        def request(self, action: str, **payload: object) -> dict[str, object]:
            self.calls.append((action, dict(payload)))
            if action == "snapshot":
                return {
                    "ok": True,
                    "snapshot": {
                        "model": "hmp4040",
                        "bench_profile": {
                            "channels": {
                                "4": {
                                    "role": mini_dma_mod.ROLE_MINI_DMA_CURRENT,
                                    "confirmed": True,
                                    "voltage_limit_v": 32.05,
                                    "current_limit_a": 0.04,
                                }
                            }
                        },
                    },
                }
            return {"ok": True}

    clients: list[_FakeBrokerClient] = []

    def _client_factory(*, host: str, port: int) -> _FakeBrokerClient:
        client = _FakeBrokerClient(host=host, port=port)
        clients.append(client)
        return client

    monkeypatch.setattr(mini_dma_mod, "BrokerJsonClient", _client_factory)
    controller = mini_dma_mod.SharedBrokerSupplyController(
        host="127.0.0.1",
        port=8765,
        max_voltage_v=32.05,
        current_channel=4,
        current_limit_a=0.04,
    )

    controller.connect()
    controller.set_current_limit_mA(60.0)

    assign_calls = [call for call in clients[0].calls if call[0] == "assign_role"]
    assert assign_calls == []
    assert controller.current_limit_a == pytest.approx(0.06)


def test_shared_broker_supply_controller_rolls_back_refused_current_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeBrokerClient:
        def request(self, action: str, **_payload: object) -> dict[str, object]:
            if action == "snapshot":
                return {
                    "snapshot": {
                        "bench_profile": {
                            "channels": {
                                "4": {
                                    "role": mini_dma_mod.ROLE_MINI_DMA_CURRENT,
                                    "confirmed": True,
                                    "voltage_limit_v": 32.05,
                                    "current_limit_a": 0.03,
                                }
                            }
                        }
                    }
                }
            if action == "assign_role":
                raise PermissionError("Cannot change CH4 role while it is leased.")
            return {"ok": True}

    monkeypatch.setattr(
        mini_dma_mod,
        "BrokerJsonClient",
        lambda *, host, port: _FakeBrokerClient(),
    )
    controller = mini_dma_mod.SharedBrokerSupplyController(
        host="127.0.0.1",
        port=8765,
        max_voltage_v=32.05,
        current_channel=4,
        current_limit_a=0.03,
    )

    controller.connect()
    controller.set_current_limit_mA(32.0)

    assert controller.current_limit_a == pytest.approx(0.032)


def test_shared_broker_supply_controller_does_not_lower_limit_while_leased(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeBrokerClient:
        def __init__(self, *, host: str, port: int) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def request(self, action: str, **payload: object) -> dict[str, object]:
            self.calls.append((action, dict(payload)))
            if action == "snapshot":
                return {
                    "snapshot": {
                        "bench_profile": {
                            "channels": {
                                "4": {
                                    "role": mini_dma_mod.ROLE_MINI_DMA_CURRENT,
                                    "confirmed": True,
                                    "voltage_limit_v": 32.05,
                                    "current_limit_a": 0.04,
                                }
                            }
                        }
                    }
                }
            if action == "assign_role":
                raise PermissionError("Cannot change CH4 role while it is leased.")
            return {"ok": True}

        def lease(self, *, channel: int, owner: str, role: str) -> dict[str, object]:
            self.calls.append(("lease", {"channel": channel, "owner": owner, "role": role}))
            return {"lease_id": "lease-4"}

        def configure_channel(self, **payload: object) -> dict[str, object]:
            self.calls.append(("configure_channel", dict(payload)))
            return {"ok": True}

    clients: list[_FakeBrokerClient] = []

    def _client_factory(*, host: str, port: int) -> _FakeBrokerClient:
        client = _FakeBrokerClient(host=host, port=port)
        clients.append(client)
        return client

    monkeypatch.setattr(mini_dma_mod, "BrokerJsonClient", _client_factory)
    controller = mini_dma_mod.SharedBrokerSupplyController(
        host="127.0.0.1",
        port=8765,
        max_voltage_v=32.05,
        current_channel=4,
        current_limit_a=0.04,
    )

    controller.connect()
    controller.configure_channel(channel=4, voltage_v=32.05, current_a=0.001, output_on=True)
    controller.set_current_limit_mA(35.0)

    assert controller.current_limit_a == pytest.approx(0.035)
    assert [call for call in clients[0].calls if call[0] == "assign_role"] == []


def test_shared_broker_supply_controller_retries_after_stale_current_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeBrokerClient:
        def __init__(self, *, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.lease_count = 0
            self.calls: list[tuple[str, dict[str, object]]] = []

        def request(self, action: str, **payload: object) -> dict[str, object]:
            self.calls.append((action, dict(payload)))
            if action == "snapshot":
                return {
                    "ok": True,
                    "snapshot": {
                        "model": "hmp4040",
                        "bench_profile": {
                            "channels": {
                                "4": {
                                    "role": mini_dma_mod.ROLE_MINI_DMA_CURRENT,
                                    "confirmed": True,
                                    "voltage_limit_v": 32.05,
                                    "current_limit_a": 0.08,
                                }
                            }
                        },
                    },
                }
            return {"ok": True}

        def lease(self, *, channel: int, owner: str, role: str) -> dict[str, object]:
            self.lease_count += 1
            lease_id = "lease-stale" if self.lease_count == 1 else "lease-fresh"
            self.calls.append(
                ("lease", {"channel": channel, "owner": owner, "role": role, "lease_id": lease_id})
            )
            return {"lease_id": lease_id}

        def set_current(self, *, channel: int, lease_id: str, current_mA: float) -> None:
            self.calls.append(
                ("set_current", {"channel": channel, "lease_id": lease_id, "current_mA": current_mA})
            )
            if lease_id == "lease-stale":
                raise RuntimeError("valid lease required for CH4")

    clients: list[_FakeBrokerClient] = []

    def _client_factory(*, host: str, port: int) -> _FakeBrokerClient:
        client = _FakeBrokerClient(host=host, port=port)
        clients.append(client)
        return client

    monkeypatch.setattr(mini_dma_mod, "BrokerJsonClient", _client_factory)
    controller = mini_dma_mod.SharedBrokerSupplyController(
        host="127.0.0.1",
        port=8765,
        max_voltage_v=32.05,
        current_channel=4,
        current_limit_a=0.08,
    )

    controller.connect()
    controller.set_current_mA(10.0)

    set_current_calls = [call for call in clients[0].calls if call[0] == "set_current"]
    lease_calls = [call for call in clients[0].calls if call[0] == "lease"]
    assert lease_calls == [
        (
            "lease",
            {
                "channel": 4,
                "owner": "mini_dma_logger",
                "role": mini_dma_mod.ROLE_MINI_DMA_CURRENT,
                "lease_id": "lease-stale",
            },
        ),
        (
            "lease",
            {
                "channel": 4,
                "owner": "mini_dma_logger",
                "role": mini_dma_mod.ROLE_MINI_DMA_CURRENT,
                "lease_id": "lease-fresh",
            },
        ),
    ]
    assert set_current_calls == [
        ("set_current", {"channel": 4, "lease_id": "lease-stale", "current_mA": 10.0}),
        ("set_current", {"channel": 4, "lease_id": "lease-fresh", "current_mA": 10.0}),
    ]


def test_shared_broker_profile_builds_broker_supply_controller(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.edit_shared_broker_host.setText("localhost")
        window.spin_shared_broker_port.setValue(9999)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window.combo_motor_supply_channel.setCurrentIndex(window.combo_motor_supply_channel.findData(3))
        window.spin_supply_voltage_limit.setValue(30.0)
        window.spin_current_sweep_end_mA.setValue(60.0)
        window.spin_motor_supply_voltage.setValue(12.0)
        window.spin_motor_supply_current_limit.setValue(0.5)

        controller = window._build_supply_controller()

        assert isinstance(controller, mini_dma_mod.SharedBrokerSupplyController)
        assert controller.host == "localhost"
        assert controller.port == 9999
        assert controller.selected_channel() == 4
        assert controller.motor_channel == 3
        assert controller.max_voltage_v == pytest.approx(30.0)
        assert controller.current_limit_a == pytest.approx(0.06)
        assert controller.motor_voltage_limit_v == pytest.approx(12.0)
        assert controller.motor_current_limit_a == pytest.approx(0.5)
    finally:
        _close_test_window(window)


def test_supply_channels_default_to_unselected_and_have_no_profile_default_label(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        current_labels = [
            window.combo_current_sweep_supply_channel.itemText(index)
            for index in range(window.combo_current_sweep_supply_channel.count())
        ]

        assert "Profile default" not in current_labels
        assert window.combo_current_sweep_supply_channel.currentData() == 0
        assert window.combo_motor_supply_channel.currentData() == 0
        assert window._current_sweep_supply_channel() is None
        assert window._motor_supply_channel() is None
    finally:
        _close_test_window(window)


def test_current_sweep_channel_setup_requires_explicit_channel(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    configured: list[object] = []

    class _FakeSupply:
        def is_connected(self) -> bool:
            return True

        def disconnect(self) -> None:
            return None

        def configure_channel(self, **kwargs: object) -> None:
            configured.append(kwargs)

    try:
        window._supply_controller = _FakeSupply()  # type: ignore[assignment]

        assert window._prepare_current_sweep_supply_channel() is False

        assert configured == []
        assert "Select a current-sweep supply channel before preparing the output." in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_motor_supply_enable_requires_explicit_channel(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    warnings: list[str] = []
    configured: list[object] = []

    class _FakeSupply:
        def is_connected(self) -> bool:
            return True

        def disconnect(self) -> None:
            return None

        def configure_channel(self, **kwargs: object) -> None:
            configured.append(kwargs)

    try:
        window._supply_controller = _FakeSupply()  # type: ignore[assignment]
        original_warning = QtWidgets.QMessageBox.warning
        QtWidgets.QMessageBox.warning = (  # type: ignore[method-assign]
            lambda _parent, _title, message: warnings.append(str(message))
        )
        try:
            assert window._enable_motor_supply_output() is False
        finally:
            QtWidgets.QMessageBox.warning = original_warning  # type: ignore[method-assign]

        assert configured == []
        assert warnings == [
            "Failed to enable motor supply channel: Select a motor supply channel before enabling motor power."
        ]
    finally:
        _close_test_window(window)


def test_motor_supply_enable_fails_when_output_readback_stays_off(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    warnings: list[str] = []

    class _FakeSupply:
        def is_connected(self) -> bool:
            return True

        def disconnect(self) -> None:
            return None

        def configure_channel(self, **_kwargs: object) -> None:
            return None

        def select_channel(self, channel: int | None = None) -> None:
            return None

        def output_state(self, channel: int | None = None) -> bool:
            return False

    try:
        window._supply_controller = _FakeSupply()  # type: ignore[assignment]
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window.combo_motor_supply_channel.setCurrentIndex(window.combo_motor_supply_channel.findData(3))
        monkeypatch_warning = QtWidgets.QMessageBox.warning
        QtWidgets.QMessageBox.warning = (  # type: ignore[method-assign]
            lambda _parent, _title, message: warnings.append(str(message))
        )
        try:
            assert window._enable_motor_supply_output() is False
        finally:
            QtWidgets.QMessageBox.warning = monkeypatch_warning  # type: ignore[method-assign]

        assert "CH3 output did not report ON" in warnings[0]
        assert "Motor supply CH3 enabled" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_shared_broker_supply_connect_validates_broker_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeBrokerClient:
        def __init__(self, *, host: str, port: int) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def request(self, action: str, **payload: object) -> dict[str, object]:
            self.calls.append((action, payload))
            return {"ok": True, "snapshot": {"model": "hmp4040"}}

    clients: list[_FakeBrokerClient] = []

    def _client_factory(*, host: str, port: int) -> _FakeBrokerClient:
        client = _FakeBrokerClient(host=host, port=port)
        clients.append(client)
        return client

    monkeypatch.setattr(mini_dma_mod, "BrokerJsonClient", _client_factory)
    controller = mini_dma_mod.SharedBrokerSupplyController(
        host="127.0.0.1",
        port=8765,
        max_voltage_v=1.0,
        current_channel=4,
        motor_channel=3,
    )

    controller.connect()

    assert controller.is_connected() is True
    assert clients[0].calls == [("snapshot", {})]


def test_shared_broker_auto_connect_starts_local_broker_when_endpoint_is_down(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    broker_started = False

    class _FakeBrokerClient:
        def __init__(self, *, host: str, port: int) -> None:
            self.host = host
            self.port = port

        def request(self, action: str, **payload: object) -> dict[str, object]:
            if not broker_started:
                raise TimeoutError("timed out")
            if action == "snapshot":
                return {"ok": True, "snapshot": {"model": "hmp4040"}}
            if action == "measure_channel":
                return {
                    "ok": True,
                    "readback": {
                        "voltage_V": 0.0,
                        "current_mA": 0.0,
                        "resistance_ohm": None,
                        "power_W": 0.0,
                    },
                }
            return {"ok": True}

    class _FakeDriver:
        def __init__(self, *, port_name: str, baudrate: int, timeout_s: float) -> None:
            self.port_name = port_name
            self.baudrate = baudrate
            self.timeout_s = timeout_s
            self.profile = None
            self.closed = False
            self.identify_calls = 0

        def connect(self) -> None:
            return None

        def identify(self) -> str:
            self.identify_calls += 1
            if self.identify_calls == 1:
                return ""
            self.profile = HMP4040_PROFILE
            return "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62"

        def close(self) -> None:
            self.closed = True

        def configure_channel(self, **_kwargs: object) -> None:
            return None

        def set_current_mA(self, **_kwargs: object) -> None:
            return None

        def set_output(self, **_kwargs: object) -> None:
            return None

        def measure(self, **_kwargs: object) -> dict[str, float | None]:
            return {"voltage_V": 0.0, "current_mA": 0.0}

    class _FakeServer:
        def __init__(self) -> None:
            self.shutdown_called = False
            self.close_called = False

        def shutdown(self) -> None:
            self.shutdown_called = True

        def server_close(self) -> None:
            self.close_called = True

    class _FakeThread:
        def __init__(self) -> None:
            self.joined = False

        def join(self, timeout: float | None = None) -> None:
            self.joined = True

    started: dict[str, object] = {}

    def _fake_start_broker_server(broker: object, *, host: str, port: int) -> tuple[_FakeServer, _FakeThread]:
        nonlocal broker_started
        broker_started = True
        server = _FakeServer()
        thread = _FakeThread()
        started.update({"broker": broker, "host": host, "port": port, "server": server, "thread": thread})
        return server, thread

    try:
        monkeypatch.setattr(mini_dma_mod, "BrokerJsonClient", _FakeBrokerClient)
        monkeypatch.setattr(mini_dma_mod, "HmpSerialDriver", _FakeDriver)
        monkeypatch.setattr(mini_dma_mod, "start_broker_server", _fake_start_broker_server)
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.combo_supply_port.addItem("COM3", "COM3")
        window.combo_supply_port.setCurrentIndex(window.combo_supply_port.findData("COM3"))
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window.check_motor_supply_power.setChecked(True)
        window.combo_motor_supply_channel.setCurrentIndex(window.combo_motor_supply_channel.findData(3))
        window.spin_supply_voltage_limit.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(40.0)
        window.spin_motor_supply_voltage.setValue(12.0)
        window.spin_motor_supply_current_limit.setValue(0.4)

        assert window._connect_supply(show_errors=False) is True

        assert broker_started is True
        assert started["host"] == "127.0.0.1"
        assert started["port"] == 8765
        assert getattr(started["broker"], "driver").identify_calls == 2
        broker_profile = getattr(started["broker"], "bench_profile")
        assert broker_profile.channels[4].voltage_limit_v == pytest.approx(1.0)
        assert broker_profile.channels[4].current_limit_a == pytest.approx(0.04)
        assert broker_profile.channels[3].voltage_limit_v == pytest.approx(12.0)
        assert broker_profile.channels[3].current_limit_a == pytest.approx(0.4)
        assert isinstance(window._supply_controller, mini_dma_mod.SharedBrokerSupplyController)
        assert "Started shared HMP broker" in window.log_output.toPlainText()
        assert "Supply connected through shared HMP broker" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_shared_broker_preflight_connects_without_serial_auto_detect(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    calls: list[str] = []

    class _FakeBrokerClient:
        def __init__(self, *, host: str, port: int) -> None:
            self.host = host
            self.port = port

        def request(self, action: str, **payload: object) -> dict[str, object]:
            return {"ok": True, "snapshot": {"model": "hmp4040"}}

    try:
        monkeypatch.setattr(mini_dma_mod, "BrokerJsonClient", _FakeBrokerClient)
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window._auto_detect_supply_port = lambda: calls.append("auto-detect") or True  # type: ignore[method-assign]

        assert window._ensure_supply_ready_for_recipe() is True

        assert calls == []
        assert isinstance(window._supply_controller, mini_dma_mod.SharedBrokerSupplyController)
    finally:
        _close_test_window(window)


def test_shared_broker_preflight_repairs_bad_endpoint_without_serial_fallback(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    clients: list[tuple[str, int]] = []
    owned_broker_starts: list[bool] = []

    class _FakeBrokerClient:
        def __init__(self, *, host: str, port: int) -> None:
            self.host = host
            self.port = port
            clients.append((host, port))

        def request(self, action: str, **payload: object) -> dict[str, object]:
            if self.port != mini_dma_mod.DEFAULT_SHARED_BROKER_PORT:
                raise TimeoutError("wrong broker endpoint")
            if action == "snapshot":
                return {"ok": True, "snapshot": {"model": "hmp4040"}}
            return {"ok": True}

    try:
        monkeypatch.setattr(mini_dma_mod, "BrokerJsonClient", _FakeBrokerClient)
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.edit_shared_broker_host.setText("localhost")
        window.spin_shared_broker_port.setValue(9999)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window.check_motor_supply_power.setChecked(True)
        window.combo_motor_supply_channel.setCurrentIndex(window.combo_motor_supply_channel.findData(3))
        window._start_owned_shared_broker = lambda: owned_broker_starts.append(True)  # type: ignore[method-assign]

        assert window._ensure_supply_ready_for_recipe() is True

        assert clients == [("localhost", 9999), ("127.0.0.1", mini_dma_mod.DEFAULT_SHARED_BROKER_PORT)]
        assert owned_broker_starts == []
        assert window.edit_shared_broker_host.text() == "127.0.0.1"
        assert window.spin_shared_broker_port.value() == mini_dma_mod.DEFAULT_SHARED_BROKER_PORT
        assert isinstance(window._supply_controller, mini_dma_mod.SharedBrokerSupplyController)
        log_text = window.log_output.toPlainText()
        assert "trying default 127.0.0.1:8765" in log_text
        assert "Supply connected through shared HMP broker" in log_text
    finally:
        _close_test_window(window)


def test_shared_broker_preflight_auto_starts_owned_broker_when_default_broker_is_down(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    owned_broker_starts: list[bool] = []
    broker_started = False

    class _FakeBrokerClient:
        def __init__(self, *, host: str, port: int) -> None:
            self.host = host
            self.port = port

        def request(self, action: str, **payload: object) -> dict[str, object]:
            if not broker_started:
                raise TimeoutError("broker unavailable")
            if action == "snapshot":
                return {"ok": True, "snapshot": {"model": "hmp4040"}}
            if action == "measure_channel":
                return {
                    "ok": True,
                    "readback": {
                        "voltage_V": 0.0,
                        "current_mA": 0.0,
                        "resistance_ohm": None,
                        "power_W": 0.0,
                    },
                }
            return {"ok": True}

    try:
        monkeypatch.setattr(mini_dma_mod, "BrokerJsonClient", _FakeBrokerClient)
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.spin_shared_broker_port.setValue(mini_dma_mod.DEFAULT_SHARED_BROKER_PORT)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )

        def _fake_start_owned_shared_broker() -> None:
            nonlocal broker_started
            owned_broker_starts.append(True)
            broker_started = True

        window._start_owned_shared_broker = _fake_start_owned_shared_broker  # type: ignore[method-assign]

        assert window._ensure_supply_ready_for_recipe() is True

        assert owned_broker_starts == [True]
        log_text = window.log_output.toPlainText()
        assert "TMA will not open the HMP serial port" not in log_text
        assert "Supply connected through shared HMP broker" in log_text
        assert isinstance(window._supply_controller, mini_dma_mod.SharedBrokerSupplyController)
    finally:
        _close_test_window(window)


def test_shared_broker_owned_start_auto_detects_hmp_port_without_leaving_shared_profile(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    started: dict[str, object] = {}

    class _FakeDriver:
        def __init__(self, *, port_name: str, baudrate: int, timeout_s: float) -> None:
            self.port_name = port_name
            self.baudrate = baudrate
            self.timeout_s = timeout_s
            self.profile = HMP4040_PROFILE
            self.closed = False

        def connect(self) -> None:
            return None

        def identify(self) -> str:
            return "ROHDE&SCHWARZ,HMP4040,102416,HW50020003/SW2.62"

        def close(self) -> None:
            self.closed = True

        def configure_channel(self, **_kwargs: object) -> None:
            return None

        def set_current_mA(self, **_kwargs: object) -> None:
            return None

        def set_output(self, **_kwargs: object) -> None:
            return None

    class _FakeServer:
        def shutdown(self) -> None:
            return None

        def server_close(self) -> None:
            return None

    class _FakeThread:
        def join(self, timeout: float | None = None) -> None:
            return None

    def _fake_start_broker_server(broker: object, *, host: str, port: int) -> tuple[_FakeServer, _FakeThread]:
        started.update({"broker": broker, "host": host, "port": port})
        return _FakeServer(), _FakeThread()

    try:
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.combo_supply_port.clear()
        monkeypatch.setattr(
            mini_dma_mod,
            "list_ports",
            SimpleNamespace(comports=lambda: [SimpleNamespace(device="COM7", description="HMP")]),
        )
        monkeypatch.setattr(
            window,
            "_probe_supply_candidate",
            lambda _port: {
                "port": "COM7",
                "baudrate": 115200,
                "profile_id": "hmp4040",
                "idn_text": "ROHDE&SCHWARZ,HMP4040",
            },
        )
        monkeypatch.setattr(mini_dma_mod, "HmpSerialDriver", _FakeDriver)
        monkeypatch.setattr(mini_dma_mod, "start_broker_server", _fake_start_broker_server)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window.check_motor_supply_power.setChecked(True)
        window.combo_motor_supply_channel.setCurrentIndex(window.combo_motor_supply_channel.findData(3))

        window._start_owned_shared_broker()

        assert window.combo_supply_port.currentData() == "COM7"
        assert window.combo_supply_profile.currentData() == "shared_hmp_broker"
        assert started["host"] == "127.0.0.1"
        assert started["port"] == 8765
        assert "Auto-detected HMP supply on COM7" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_recipe_preflight_does_not_rewrite_shared_broker_current_limit(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    limits: list[float] = []

    class _FakeSupply:
        def is_connected(self) -> bool:
            return True

        def set_current_limit_mA(self, current_limit_mA: float) -> None:
            limits.append(current_limit_mA)

        def disconnect(self) -> None:
            return None

    try:
        window._supply_controller = _FakeSupply()  # type: ignore[assignment]
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window.spin_supply_manual_current.setValue(1.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(85.0)
        window._recipe_requires_tic = lambda _steps: False  # type: ignore[method-assign]
        window._recipe_requires_scale = lambda _steps: False  # type: ignore[method-assign]
        window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._restore_default_zero_load_reference_if_real_grams = lambda _value: None  # type: ignore[method-assign]

        steps = [mini_dma_mod.AutomationStep("sweep_current", current_start_mA=1.0, current_end_mA=85.0)]
        assert window._preflight_recipe_hardware(steps) is True

        assert limits == []
        assert "limit checked" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_recipe_preflight_does_not_push_independent_first_overheating_limit_to_broker(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    limits: list[float] = []

    class _FakeSupply:
        def is_connected(self) -> bool:
            return True

        def set_current_limit_mA(self, current_limit_mA: float) -> None:
            limits.append(current_limit_mA)

        def disconnect(self) -> None:
            return None

    try:
        window._supply_controller = _FakeSupply()  # type: ignore[assignment]
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window.spin_supply_manual_current.setValue(1.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(40.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(60.0)
        window._recipe_requires_tic = lambda _steps: False  # type: ignore[method-assign]
        window._recipe_requires_scale = lambda _steps: False  # type: ignore[method-assign]
        window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._restore_default_zero_load_reference_if_real_grams = lambda _value: None  # type: ignore[method-assign]

        steps = [
            mini_dma_mod.AutomationStep("sweep_current", current_start_mA=1.0, current_end_mA=60.0)
        ]
        assert window._preflight_recipe_hardware(steps) is True

        assert limits == []
        assert "recipe maximum" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_recipe_preflight_ignores_stale_shared_broker_limit_lease_error(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    limit_attempts: list[float] = []

    class _FakeSupply:
        current_limit_a = 0.04

        def is_connected(self) -> bool:
            return True

        def set_current_limit_mA(self, current_limit_mA: float) -> None:
            limit_attempts.append(current_limit_mA)
            raise PermissionError("Cannot change CH4 role while it is leased.")

        def disconnect(self) -> None:
            return None

    try:
        window._supply_controller = _FakeSupply()  # type: ignore[assignment]
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window.spin_supply_manual_current.setValue(1.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(40.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(60.0)
        window._automation_active = False
        window._supply_output_enabled = False
        window._recipe_requires_tic = lambda _steps: False  # type: ignore[method-assign]
        window._recipe_requires_scale = lambda _steps: False  # type: ignore[method-assign]
        window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._restore_default_zero_load_reference_if_real_grams = lambda _value: None  # type: ignore[method-assign]

        steps = [
            mini_dma_mod.AutomationStep("sweep_current", current_start_mA=1.0, current_end_mA=60.0)
        ]
        assert window._preflight_recipe_hardware(steps) is True

        assert limit_attempts == []
        assert window._current_sweep_channel_limit_checked is not None
        assert window._current_sweep_channel_limit_checked[0] == 4
        assert math.isinf(window._current_sweep_channel_limit_checked[1])
        log_text = window.log_output.toPlainText()
        assert "Current-sweep channel limit update failed" not in log_text
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_prepare_current_sweep_channel_does_not_rewrite_shared_broker_limit_before_configure(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    calls: list[tuple[str, float | None]] = []

    class _FakeSupply:
        def is_connected(self) -> bool:
            return True

        def set_current_limit_mA(self, current_limit_mA: float) -> None:
            calls.append(("limit", current_limit_mA))

        def configure_channel(self, *, channel: int, voltage_v: float, current_a: float, output_on: bool) -> None:
            calls.append(("configure", current_a * 1000.0))

        def select_channel(self, channel: int | None = None) -> None:
            calls.append(("select", None if channel is None else float(channel)))

        def disconnect(self) -> None:
            return None

    try:
        window._supply_controller = _FakeSupply()  # type: ignore[assignment]
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window.spin_supply_manual_current.setValue(1.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(85.0)

        assert window._prepare_current_sweep_supply_channel() is True

        assert calls[:2] == [("configure", pytest.approx(1.0)), ("select", None)]
    finally:
        _close_test_window(window)


def test_supply_reads_are_throttled_during_fast_recipe_logging(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        def __init__(self) -> None:
            self.measure_count = 0

        def is_connected(self) -> bool:
            return True

        def disconnect(self) -> None:
            return None

        def measure(self) -> dict[str, float | None]:
            self.measure_count += 1
            return {
                "voltage_V": 1.0,
                "current_mA": 1.0,
                "resistance_ohm": 1000.0,
                "power_W": 0.001,
            }

    supply = _FakeSupply()
    times = iter([10.0, 10.2, 11.0])

    def _fake_monotonic() -> float:
        try:
            return next(times)
        except StopIteration:
            return 11.0

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", _fake_monotonic)
    window._supply_controller = supply  # type: ignore[assignment]

    try:
        first = window._refresh_supply_snapshot()
        second = window._refresh_supply_snapshot()
        third = window._refresh_supply_snapshot()

        assert supply.measure_count == 2
        assert first == second
        assert third == first
    finally:
        _close_test_window(window)


def test_tic_target_position_energizes_and_exits_safe_start(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(args: list[str], **_kwargs: object) -> _Completed:
        calls.append(args)
        return _Completed()

    controller = mini_dma_mod.TicController(command_path="ticcmd", device_serial="00501366")
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")
    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    controller.set_target_position(42, max_speed=12345)

    assert calls == [
        [
            "ticcmd.exe",
            "-d",
            "00501366",
            "--energize",
            "--reset-command-timeout",
            "--exit-safe-start",
            "--max-speed",
            "12345",
            "--position",
            "42",
        ]
    ]


def test_tic_controller_sets_step_mode_with_ticcmd(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(args: list[str], **_kwargs: object) -> _Completed:
        calls.append(args)
        return _Completed()

    controller = mini_dma_mod.TicController(command_path="ticcmd", device_serial="00501366")
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")
    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    controller.set_step_mode("4")

    assert calls == [["ticcmd.exe", "-d", "00501366", "--step-mode", "4"]]


def test_tic_controller_sets_motion_limits_with_ticcmd(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(args: list[str], **_kwargs: object) -> _Completed:
        calls.append(args)
        return _Completed()

    controller = mini_dma_mod.TicController(command_path="ticcmd", device_serial="00501366")
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")
    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    controller.set_motion_limits(max_speed=10_000_000, max_accel=100_000, max_decel=100_000)

    assert calls == [
        [
            "ticcmd.exe",
            "-d",
            "00501366",
            "--max-speed",
            "10000000",
            "--max-accel",
            "100000",
            "--max-decel",
            "100000",
        ]
    ]


def test_tic_controller_run_hides_ticcmd_console_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _StartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 0
            self.wShowWindow = None

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(args: list[str], **kwargs: object) -> _Completed:
        captured["args"] = args
        captured.update(kwargs)
        return _Completed()

    monkeypatch.setattr(mini_dma_mod.os, "name", "nt")
    monkeypatch.setattr(mini_dma_mod.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(mini_dma_mod.subprocess, "STARTF_USESHOWWINDOW", 0x00000001, raising=False)
    monkeypatch.setattr(mini_dma_mod.subprocess, "STARTUPINFO", _StartupInfo, raising=False)
    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)
    controller = mini_dma_mod.TicController(command_path="ticcmd", device_serial="00501366")
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")

    controller.run("--status")

    startupinfo = captured["startupinfo"]
    assert captured["creationflags"] == 0x08000000
    assert startupinfo.dwFlags & 0x00000001
    assert startupinfo.wShowWindow == 0


def test_tic_status_falls_back_when_full_status_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class _Completed:
        returncode = 0
        stdout = "VIN voltage: 12.00 V\nOperation state: Normal\n"
        stderr = ""

    def _fake_run(args: list[str], **kwargs: object) -> _Completed:
        calls.append(args)
        if "--full" in args and "--status" in args:
            raise subprocess.TimeoutExpired(args, timeout=float(kwargs.get("timeout", 5.0)))
        return _Completed()

    controller = mini_dma_mod.TicController(command_path="ticcmd", device_serial="00501366")
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")
    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    status = controller.get_status()

    assert "VIN voltage: 12.00 V" in status
    assert calls == [
        ["ticcmd.exe", "-d", "00501366", "--status", "--full"],
        ["ticcmd.exe", "-d", "00501366", "--status"],
    ]


def test_tic_status_raises_instead_of_returning_device_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class _Completed:
        returncode = 0
        stdout = "00501366, Tic T500\n"
        stderr = ""

    def _fake_run(args: list[str], **_kwargs: object) -> _Completed:
        calls.append(args)
        return _Completed()

    controller = mini_dma_mod.TicController(command_path="ticcmd", device_serial="00501366")
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")
    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError, match="VIN voltage"):
        controller.get_status()

    assert calls == [
        ["ticcmd.exe", "-d", "00501366", "--status", "--full"],
        ["ticcmd.exe", "-d", "00501366", "--status"],
        ["ticcmd.exe", "-d", "00501366", "--full"],
    ]


def test_tic_units_per_mm_follow_microstep_factor() -> None:
    assert mini_dma_mod.tic_units_per_mm(100.0, "full") == pytest.approx(100.0)
    assert mini_dma_mod.tic_units_per_mm(100.0, "1/2 step") == pytest.approx(200.0)
    assert mini_dma_mod.tic_units_per_mm(100.0, "4") == pytest.approx(400.0)
    assert mini_dma_mod.tic_units_per_mm(100.0, "1/8 step") == pytest.approx(800.0)


def test_native_tic_usb_controller_sends_control_transfers(monkeypatch: pytest.MonkeyPatch) -> None:
    transfers: list[tuple[int, int, int, int, object]] = []

    class _FakeDevice:
        idVendor = mini_dma_mod.TIC_USB_VENDOR_ID
        iProduct = 1
        iSerialNumber = 2

        def ctrl_transfer(
            self,
            request_type: int,
            request: int,
            value: int = 0,
            index: int = 0,
            data_or_wLength: object = None,
            *,
            timeout: int | None = None,
        ) -> bytes:
            transfers.append((request_type, request, value, index, data_or_wLength))
            return b""

    device = _FakeDevice()

    class _FakeCore:
        @staticmethod
        def find(*, find_all: bool, idVendor: int, backend: object | None = None) -> list[_FakeDevice]:
            assert find_all is True
            assert idVendor == mini_dma_mod.TIC_USB_VENDOR_ID
            return [device]

    class _FakeUtil:
        @staticmethod
        def get_string(_device: _FakeDevice, index: int) -> str:
            return {1: "Pololu Tic T500", 2: "00501366"}[index]

    monkeypatch.setattr(
        mini_dma_mod,
        "_load_pyusb_backend",
        lambda: (_FakeCore, _FakeUtil, object()),
    )

    controller = mini_dma_mod.NativeTicUsbController(device_serial="00501366")
    controller.set_target_position(-42, max_speed=12345)
    controller.set_target_velocity(250)
    controller.set_current_position(0)
    controller.set_step_mode("8")
    controller.set_current_limit_mA(500)
    controller.set_motion_limits(max_speed=10_000_000, max_accel=100_000, max_decel=100_000)
    controller.halt_and_hold()

    assert transfers == [
        (0x40, 0x85, 0, 0, None),
        (0x40, 0x8C, 0, 0, None),
        (0x40, 0x83, 0, 0, None),
        (0x40, 0xE6, 12345, 0, None),
        (0x40, 0xE0, 0xFFD6, 0xFFFF, None),
        (0x40, 0x85, 0, 0, None),
        (0x40, 0x8C, 0, 0, None),
        (0x40, 0x83, 0, 0, None),
        (0x40, 0xE3, 250, 0, None),
        (0x40, 0xEC, 0, 0, None),
        (0x40, 0x94, 3, 0, None),
        (0x40, 0x91, 4, 0, None),
        (0x40, 0xE6, 0x9680, 0x0098, None),
        (0x40, 0xEA, 0x86A0, 0x0001, None),
        (0x40, 0xE9, 0x86A0, 0x0001, None),
        (0x40, 0x89, 0, 0, None),
    ]


def test_native_tic_usb_controller_formats_status(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeDevice:
        idVendor = mini_dma_mod.TIC_USB_VENDOR_ID
        iProduct = 1
        iSerialNumber = 2

        def ctrl_transfer(
            self,
            request_type: int,
            request: int,
            value: int = 0,
            index: int = 0,
            data_or_wLength: object = None,
            *,
            timeout: int | None = None,
        ) -> bytes:
            assert (request_type, request, value, index, data_or_wLength) == (0xC0, 0xA1, 0, 0, 0x4B)
            data = bytearray(0x4B)
            data[0x00] = 10
            data[0x02:0x04] = (0).to_bytes(2, "little")
            data[0x16:0x1A] = (8_000_000).to_bytes(4, "little")
            data[0x1A:0x1E] = (80_000).to_bytes(4, "little")
            data[0x1E:0x22] = (90_000).to_bytes(4, "little")
            data[0x22:0x26] = (-42).to_bytes(4, "little", signed=True)
            data[0x33:0x35] = (12345).to_bytes(2, "little")
            data[0x49] = 3
            data[0x4A] = 3
            return bytes(data)

    device = _FakeDevice()

    class _FakeCore:
        @staticmethod
        def find(*, find_all: bool, idVendor: int, backend: object | None = None) -> list[_FakeDevice]:
            return [device]

    class _FakeUtil:
        @staticmethod
        def get_string(_device: _FakeDevice, index: int) -> str:
            return {1: "Pololu Tic T500", 2: "00501366"}[index]

    monkeypatch.setattr(
        mini_dma_mod,
        "_load_pyusb_backend",
        lambda: (_FakeCore, _FakeUtil, object()),
    )

    status = mini_dma_mod.NativeTicUsbController(device_serial="00501366").get_status()

    assert "Operation state: Normal" in status
    assert "Current position: -42" in status
    assert "VIN voltage: 12.35 V" in status
    assert "Max speed: 8000000" in status
    assert "Max acceleration: 90000" in status
    assert "Max deceleration: 80000" in status
    assert "Step mode: 1/8 step" in status
    assert "Current limit: 343 mA" in status
    assert "Errors currently stopping the motor: None" in status


def test_native_tic_usb_controller_accepts_single_device_when_serial_string_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeDevice:
        idVendor = mini_dma_mod.TIC_USB_VENDOR_ID
        iProduct = 2
        iSerialNumber = 3

        def ctrl_transfer(
            self,
            request_type: int,
            request: int,
            value: int = 0,
            index: int = 0,
            data_or_wLength: object = None,
            *,
            timeout: int | None = None,
        ) -> bytes:
            data = bytearray(0x35)
            data[0x00] = 10
            data[0x33:0x35] = (12000).to_bytes(2, "little")
            return bytes(data)

    device = _FakeDevice()

    class _FakeCore:
        @staticmethod
        def find(*, find_all: bool, idVendor: int, backend: object | None = None) -> list[_FakeDevice]:
            return [device]

    class _FakeUtil:
        @staticmethod
        def get_string(_device: _FakeDevice, index: int) -> str:
            raise ValueError("The device has no langid")

    monkeypatch.setattr(
        mini_dma_mod,
        "_load_pyusb_backend",
        lambda: (_FakeCore, _FakeUtil, object()),
    )

    status = mini_dma_mod.NativeTicUsbController(device_serial="00501366").get_status()

    assert "VIN voltage: 12.00 V" in status


def test_native_tic_usb_controller_rejects_ambiguous_unreadable_serials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeDevice:
        idVendor = mini_dma_mod.TIC_USB_VENDOR_ID
        iProduct = 2
        iSerialNumber = 3

    class _FakeCore:
        @staticmethod
        def find(*, find_all: bool, idVendor: int, backend: object | None = None) -> list[_FakeDevice]:
            return [_FakeDevice(), _FakeDevice()]

    class _FakeUtil:
        @staticmethod
        def get_string(_device: _FakeDevice, index: int) -> str:
            raise ValueError("The device has no langid")

    monkeypatch.setattr(
        mini_dma_mod,
        "_load_pyusb_backend",
        lambda: (_FakeCore, _FakeUtil, object()),
    )

    with pytest.raises(RuntimeError, match="No Pololu Tic USB device"):
        mini_dma_mod.NativeTicUsbController(device_serial="00501366")


def test_libusb_wheel_library_finder_accepts_bundled_dll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import libusb._platform as libusb_platform

    dll_path = tmp_path / "libusb-1.0.dll"
    dll_path.write_bytes(b"fake dll")
    monkeypatch.setattr(libusb_platform, "DLL_PATH", dll_path)

    assert mini_dma_mod._find_libusb_wheel_library("usb-1.0") == str(dll_path)
    assert mini_dma_mod._find_libusb_wheel_library("libusb-1.0") == str(dll_path)
    assert mini_dma_mod._find_libusb_wheel_library("other") is None


def test_tic_controller_prefers_native_usb_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeNative:
        def __init__(self, *, device_serial: str = "") -> None:
            self.device_serial = device_serial
            self.targets: list[tuple[int, int | None]] = []
            self.current_limits: list[float] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append((position_steps, max_speed))

        def set_current_limit_mA(self, target_mA: float) -> int:
            self.current_limits.append(target_mA)
            return 343

        def get_status(self) -> str:
            return "VIN voltage: 12.00 V\nTransport: native USB\n"

    created: list[_FakeNative] = []

    def _make_native(*, device_serial: str = "") -> _FakeNative:
        native = _FakeNative(device_serial=device_serial)
        created.append(native)
        return native

    monkeypatch.setattr(mini_dma_mod, "NativeTicUsbController", _make_native)

    logs: list[str] = []
    controller = mini_dma_mod.TicController(
        command_path="ticcmd",
        device_serial="00501366",
        prefer_native_usb=True,
        transport_logger=logs.append,
    )
    controller.set_target_position(42, max_speed=123)
    applied_current_limit = controller.set_current_limit_mA(343)
    status = controller.get_status()

    assert len(created) == 1
    assert created[0].device_serial == "00501366"
    assert created[0].targets == [(42, 123)]
    assert created[0].current_limits == [343]
    assert applied_current_limit == 343
    assert "Transport: native USB" in status
    assert logs == ["Tic transport: native USB active."]


def test_tic_controller_serializes_native_usb_status_and_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeNative:
        def __init__(self, *, device_serial: str = "") -> None:
            self.device_serial = device_serial
            self.active = False
            self.calls: list[str] = []

        def _enter(self, name: str) -> None:
            if self.active:
                raise RuntimeError(f"concurrent native USB access during {name}")
            self.active = True
            self.calls.append(name)
            time.sleep(0.03)
            self.active = False

        def get_status(self) -> str:
            self._enter("status")
            return "VIN voltage: 12.00 V\nTransport: native USB\n"

        def reset_command_timeout(self) -> None:
            self._enter("keepalive")

    created: list[_FakeNative] = []

    def _make_native(*, device_serial: str = "") -> _FakeNative:
        native = _FakeNative(device_serial=device_serial)
        created.append(native)
        return native

    monkeypatch.setattr(mini_dma_mod, "NativeTicUsbController", _make_native)
    controller = mini_dma_mod.TicController(
        command_path="ticcmd",
        device_serial="00501366",
        prefer_native_usb=True,
    )
    errors: list[BaseException] = []

    def _call(method: str) -> None:
        try:
            getattr(controller, method)()
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_call, args=("get_status",)),
        threading.Thread(target=_call, args=("reset_command_timeout",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1.0)

    assert errors == []
    assert len(created) == 1
    assert sorted(created[0].calls) == ["keepalive", "status"]


def test_tic_controller_auto_falls_back_to_ticcmd_when_native_usb_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(args: list[str], **_kwargs: object) -> _Completed:
        calls.append(args)
        return _Completed()

    def _fail_native(*, device_serial: str = "") -> object:
        raise RuntimeError("no native USB access")

    logs: list[str] = []
    controller = mini_dma_mod.TicController(
        command_path="ticcmd",
        device_serial="00501366",
        prefer_native_usb=True,
        transport_logger=logs.append,
    )
    monkeypatch.setattr(mini_dma_mod, "NativeTicUsbController", _fail_native)
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")
    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    controller.reset_command_timeout()

    assert calls == [["ticcmd.exe", "-d", "00501366", "--reset-command-timeout"]]
    assert logs == ["Tic transport fallback: using ticcmd because native USB setup failed: no native USB access"]


def test_tic_controller_falls_back_to_ticcmd_when_native_status_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class _FakeNative:
        def __init__(self, *, device_serial: str = "") -> None:
            self.device_serial = device_serial

        def get_status(self) -> str:
            raise RuntimeError("native access denied")

    class _Completed:
        returncode = 0
        stdout = "VIN voltage: 12.00 V\nOperation state: Normal\n"
        stderr = ""

    def _fake_run(args: list[str], **_kwargs: object) -> _Completed:
        calls.append(args)
        return _Completed()

    logs: list[str] = []
    controller = mini_dma_mod.TicController(
        command_path="ticcmd",
        device_serial="00501366",
        prefer_native_usb=True,
        transport_logger=logs.append,
    )
    monkeypatch.setattr(mini_dma_mod, "NativeTicUsbController", _FakeNative)
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")
    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    status = controller.get_status()

    assert "VIN voltage: 12.00 V" in status
    assert calls == [["ticcmd.exe", "-d", "00501366", "--status", "--full"]]
    assert logs == ["Tic transport fallback: using ticcmd because native status failed: native access denied"]


def test_tic_controller_can_disable_ticcmd_status_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class _FakeNative:
        def __init__(self, *, device_serial: str = "") -> None:
            self.device_serial = device_serial

        def get_status(self) -> str:
            raise RuntimeError("native access denied")

    controller = mini_dma_mod.TicController(
        command_path="ticcmd",
        device_serial="00501366",
        prefer_native_usb=True,
        allow_ticcmd_fallback=False,
    )
    monkeypatch.setattr(mini_dma_mod, "NativeTicUsbController", _FakeNative)
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")
    monkeypatch.setattr(
        mini_dma_mod.subprocess,
        "run",
        lambda args, **_kwargs: calls.append(list(args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    with pytest.raises(RuntimeError, match="native access denied"):
        controller.get_status()

    assert calls == []


def test_tic_controller_falls_back_to_ticcmd_when_native_move_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class _FakeNative:
        def __init__(self, *, device_serial: str = "") -> None:
            self.device_serial = device_serial

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            raise RuntimeError("native access denied")

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(args: list[str], **_kwargs: object) -> _Completed:
        calls.append(args)
        return _Completed()

    logs: list[str] = []
    controller = mini_dma_mod.TicController(
        command_path="ticcmd",
        device_serial="00501366",
        prefer_native_usb=True,
        transport_logger=logs.append,
    )
    monkeypatch.setattr(mini_dma_mod, "NativeTicUsbController", _FakeNative)
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")
    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    controller.set_target_position(-42, max_speed=123)

    assert calls == [
        [
            "ticcmd.exe",
            "-d",
            "00501366",
            "--energize",
            "--reset-command-timeout",
            "--exit-safe-start",
            "--max-speed",
            "123",
            "--position",
            "-42",
        ]
    ]
    assert logs == ["Tic transport fallback: using ticcmd because native position command failed: native access denied"]


def test_tic_controller_reopens_native_usb_once_before_ticcmd_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class _FakeNative:
        def __init__(self, *, device_serial: str = "") -> None:
            self.device_serial = device_serial
            self.targets: list[tuple[int, int | None]] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            if len(created) == 1:
                raise OSError(2, "Entity not found")
            self.targets.append((position_steps, max_speed))

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(args: list[str], **_kwargs: object) -> _Completed:
        calls.append(args)
        return _Completed()

    created: list[_FakeNative] = []

    def _make_native(*, device_serial: str = "") -> _FakeNative:
        native = _FakeNative(device_serial=device_serial)
        created.append(native)
        return native

    logs: list[str] = []
    controller = mini_dma_mod.TicController(
        command_path="ticcmd",
        device_serial="00501366",
        prefer_native_usb=True,
        transport_logger=logs.append,
    )
    monkeypatch.setattr(mini_dma_mod, "NativeTicUsbController", _make_native)
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")
    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    controller.set_target_position(-42, max_speed=123)

    assert len(created) == 2
    assert created[1].targets == [(-42, 123)]
    assert calls == []
    assert logs == ["Tic transport: native USB active."]


def test_tic_controller_is_reused_until_connection_settings_change(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    created: list[tuple[str, str]] = []

    class _FakeController:
        def __init__(
            self,
            command_path: str,
            device_serial: str,
            *,
            prefer_native_usb: bool = False,
            allow_ticcmd_fallback: bool = True,
            transport_logger: object | None = None,
        ) -> None:
            created.append((command_path, device_serial))

    monkeypatch.setattr(mini_dma_mod, "TicController", _FakeController)
    window.edit_ticcmd_path.setText("ticcmd-a")
    window.edit_tic_serial.setText("serial-a")

    try:
        first = window._build_tic_controller()
        second = window._build_tic_controller()
        window.edit_tic_serial.setText("serial-b")
        third = window._build_tic_controller()

        assert first is second
        assert third is not first
        assert created == [("ticcmd-a", "serial-a"), ("ticcmd-a", "serial-b")]
    finally:
        _close_test_window(window)


def test_main_window_disables_ticcmd_fallback_when_native_usb_is_preferred(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.check_tic_native_usb.setChecked(True)
        controller = window._build_tic_controller()

        assert controller.prefer_native_usb is True
        assert controller.allow_ticcmd_fallback is False
    finally:
        _close_test_window(window)


def test_tic_command_dispatcher_coalesces_pending_target_moves() -> None:
    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[tuple[int, int | None]] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append((position_steps, max_speed))

    controller = _FakeController()
    dispatcher = mini_dma_mod.TicCommandDispatcher(lambda: controller, autostart=False)

    try:
        dispatcher.set_target_position(10, max_speed=100)
        dispatcher.set_target_position(20, max_speed=200)
        dispatcher.set_target_position(30, max_speed=300)
        dispatcher.start()

        assert dispatcher.wait_until_idle(timeout_s=2.0)
        assert controller.targets == [(30, 300)]
    finally:
        dispatcher.stop()


def test_tic_command_dispatcher_clears_previous_error_after_success() -> None:
    class _FakeController:
        def __init__(self) -> None:
            self.fail_next = True

        def reset_command_timeout(self) -> None:
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("temporary tic failure")

    controller = _FakeController()
    dispatcher = mini_dma_mod.TicCommandDispatcher(lambda: controller, autostart=False)

    try:
        dispatcher.start()
        dispatcher.reset_command_timeout()
        assert dispatcher.wait_until_idle(timeout_s=2.0)
        assert isinstance(dispatcher.last_error(), RuntimeError)

        dispatcher.reset_command_timeout()
        assert dispatcher.wait_until_idle(timeout_s=2.0)
        assert dispatcher.last_error() is None
    finally:
        dispatcher.stop()


def test_move_to_position_uses_persistent_tic_dispatcher(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeDispatcher:
        def __init__(self) -> None:
            self.targets: list[tuple[int, int | None]] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append((position_steps, max_speed))

    dispatcher = _FakeDispatcher()
    window._tic_command_dispatcher = dispatcher  # type: ignore[assignment]
    window._tic_command_dispatcher_key = (
        window.edit_ticcmd_path.text().strip(),
        window.edit_tic_serial.text().strip(),
        bool(window.check_tic_native_usb.isChecked()),
    )
    window.spin_steps_per_mm.setValue(100.0)
    window._current_position_steps = 0
    window._last_commanded_position_steps = 0

    try:
        assert window._move_to_position_mm(0.5, speed_mm_s=1.0) is True

        assert dispatcher.targets == [(50, 1_000_000)]
    finally:
        _close_test_window(window)


def test_manual_halt_waits_for_persistent_tic_dispatcher(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeDispatcher:
        def __init__(self) -> None:
            self.halted = False
            self.waited = False

        def halt_and_hold(self) -> None:
            self.halted = True

        def wait_until_idle(self, *, timeout_s: float = 2.0) -> bool:
            self.waited = True
            return True

        def last_error(self) -> Exception | None:
            return None

    dispatcher = _FakeDispatcher()
    window._build_tic_dispatcher = lambda: dispatcher  # type: ignore[method-assign]
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

    try:
        window._halt_tic()

        assert dispatcher.halted is True
        assert dispatcher.waited is True
    finally:
        _close_test_window(window)


def test_zero_position_waits_for_persistent_tic_dispatcher_before_reset(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeDispatcher:
        def __init__(self) -> None:
            self.zero_steps: int | None = None
            self.waited = False

        def set_current_position(self, position_steps: int) -> None:
            self.zero_steps = position_steps

        def wait_until_idle(self, *, timeout_s: float = 2.0) -> bool:
            self.waited = True
            return True

        def last_error(self) -> Exception | None:
            return None

    dispatcher = _FakeDispatcher()
    window._build_tic_dispatcher = lambda: dispatcher  # type: ignore[method-assign]
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

    try:
        window._current_position_steps = 123
        window._current_position_mm = 1.23
        window._zero_tic_position()

        assert dispatcher.zero_steps == 0
        assert dispatcher.waited is True
        assert window._current_position_steps == 0
        assert window._current_position_mm == pytest.approx(0.0)
    finally:
        _close_test_window(window)


def test_tic_keepalive_resets_command_timeout_during_active_recipe(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []
            self.keepalive_count = 0

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

        def reset_command_timeout(self) -> None:
            self.keepalive_count += 1

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window._tic_motor_power_ok = True
    window._automation_active = True
    window.spin_steps_per_mm.setValue(100.0)

    try:
        assert window._move_to_position_mm(0.5) is True
        window._handle_tic_keepalive_timer()
        _wait_for_tic_commands(window)

        assert controller.targets == [50]
        assert controller.keepalive_count == 1
    finally:
        _close_test_window(window)


def test_tic_keepalive_resets_command_timeout_during_motor_step_calibration(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeDispatcher:
        def __init__(self) -> None:
            self.keepalive_count = 0

        def reset_command_timeout(self) -> None:
            self.keepalive_count += 1

    dispatcher = _FakeDispatcher()
    window._build_tic_dispatcher = lambda: dispatcher  # type: ignore[method-assign]
    window._tic_motor_power_ok = True
    window._automation_active = False
    window._motor_step_calibration_active = True
    window._manual_jog_timer.stop()

    try:
        window._handle_tic_keepalive_timer()

        assert dispatcher.keepalive_count == 1
    finally:
        _close_test_window(window)


def test_negative_scale_reading_is_reported_as_positive_tensile_load(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.check_tension_load_positive.setChecked(True)
        window._latest_scale_value_g = -5.0
        window._latest_scale_timestamp = time.time()
        window._load_offset_g = 0.0

        assert window._current_effective_load_g() == pytest.approx(5.0)
        assert mini_dma_mod.stress_mpa_from_load_g(
            window._current_effective_load_g(),
            0.03,
        ) > 0
    finally:
        _close_test_window(window)


def test_zero_load_reference_maps_real_scale_weight_to_applied_load(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.check_tension_load_positive.setChecked(True)
        window.spin_zero_load_scale_g.setValue(21.2)
        window._latest_scale_value_g = 17.2
        window._latest_scale_timestamp = time.time()

        assert window._current_effective_load_g() == pytest.approx(4.0)

        window._latest_scale_value_g = 21.25
        assert window._current_effective_load_g() == pytest.approx(0.0)
    finally:
        _close_test_window(window)


def test_real_gram_scale_reading_restores_default_zero_load_reference_when_saved_zero_is_tared(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.check_tension_load_positive.setChecked(True)
        window.spin_zero_load_scale_g.setValue(0.0)
        window._handle_scale_measurement(17.325, "17.32500 g", time.time())

        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
        assert window._current_effective_load_g() == pytest.approx(3.875)
        assert "restored to 21.20000 g" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_physical_tared_scale_near_zero_keeps_zero_load_reference_at_zero(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.check_tension_load_positive.setChecked(True)
        window.spin_zero_load_scale_g.setValue(0.0)
        window._handle_scale_measurement(-0.250, "-0.25000 g", time.time())

        assert window.spin_zero_load_scale_g.value() == pytest.approx(0.0)
        assert window._current_effective_load_g() == pytest.approx(0.250)
    finally:
        _close_test_window(window)


def test_dashboard_load_and_stress_show_missing_scale_instead_of_zero(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._latest_scale_timestamp = None
        window._latest_scale_value_g = 0.0

        window._refresh_live_labels()

        assert window._dashboard_value_labels["load_g"].text() == "-"
        assert window._dashboard_value_labels["stress_mpa"].text() == "-"
        assert "No readings yet" in window.label_card_scale.text()
    finally:
        _close_test_window(window)


def test_dashboard_load_and_stress_mark_stale_scale(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.check_tension_load_positive.setChecked(True)
        window.spin_zero_load_scale_g.setValue(21.2)
        window._latest_scale_value_g = 20.2
        window._latest_scale_timestamp = time.time() - mini_dma_mod.STALE_SCALE_AFTER_S - 2.0

        window._refresh_live_labels()

        assert window._dashboard_value_labels["load_g"].text().startswith("stale ")
        assert window._dashboard_value_labels["stress_mpa"].text().startswith("stale ")
        assert "stale" in window.label_card_scale.text()
    finally:
        _close_test_window(window)


def test_zero_load_reference_is_default_max_applied_load_limit(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.check_tension_load_positive.setChecked(True)
        window.spin_zero_load_scale_g.setValue(21.2)
        window.check_max_load.setChecked(False)
        window._latest_scale_timestamp = time.time()

        window._latest_scale_value_g = 0.2
        assert window._is_max_load_exceeded() is False

        window._latest_scale_value_g = -0.2
        assert window._is_max_load_exceeded() is True

        window.check_max_load.setChecked(True)
        window.spin_max_load_g.setValue(9.0)
        window._latest_scale_value_g = 12.0
        assert window._is_max_load_exceeded() is True
    finally:
        _close_test_window(window)


def test_zero_load_reference_can_handle_scale_reading_increasing_under_tension(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.check_tension_load_positive.setChecked(False)
        window.spin_zero_load_scale_g.setValue(0.0)
        window._latest_scale_value_g = 3.0
        window._latest_scale_timestamp = time.time()

        assert window._current_effective_load_g() == pytest.approx(3.0)
    finally:
        _close_test_window(window)


def test_logged_load_uses_positive_applied_tension(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("positive_tension_log")
    window.check_tension_load_positive.setChecked(True)
    window._latest_scale_value_g = -5.0
    window._latest_scale_text = "-5.000 g"
    window._latest_scale_timestamp = time.time()

    try:
        window._start_session()
        window._stop_session()

        txt_path = tmp_path / "positive_tension_log" / "measurement.txt"
        csv_path = tmp_path / "positive_tension_log" / "measurement.csv"

        txt_lines = txt_path.read_text(encoding="utf-8").splitlines()
        assert txt_lines[-1].split("\t")[1] == "5.000000"

        rows = list(csv.DictReader(csv_path.open(encoding="utf-8", newline="")))
        assert rows[0]["raw_load_g"] == "-5.000000"
        assert rows[0]["load_g"] == "5.000000"
    finally:
        _close_test_window(window)


def test_session_writes_raw_scale_sidecar_and_interval_summary(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    clock = {"t": 1000.0}
    monkeypatch.setattr(mini_dma_mod.time, "time", lambda: clock["t"])
    window.edit_log_name.setText("scale_buffered_session")
    window.check_tension_load_positive.setChecked(True)
    window.check_zero_on_preload.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.2)
    window.spin_current_sweep_log_interval.setValue(500)
    window._latest_scale_value_g = 21.2
    window._latest_scale_text = "21.200 g"
    window._latest_scale_timestamp = 1000.0
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

    try:
        window._start_session()
        assert len(window._session_points) == 1

        clock["t"] = 1000.10
        window._handle_scale_measurement(21.0, "21.000 g", clock["t"])
        clock["t"] = 1000.15
        window._handle_scale_measurement(20.8, "20.800 g", clock["t"])
        clock["t"] = 1000.20
        window._handle_scale_measurement(20.6, "20.600 g", clock["t"])

        clock["t"] = 1000.25
        assert window._maybe_record_scheduled_point() is True
        assert len(window._session_points) == 1

        clock["t"] = 1000.60
        assert window._maybe_record_scheduled_point() is True
        assert len(window._session_points) == 2

        window._stop_session()

        run_dir = tmp_path / "scale_buffered_session"
        rows = list(csv.DictReader((run_dir / "measurement.csv").open(encoding="utf-8", newline="")))
        assert len(rows) == 2
        assert rows[-1]["raw_load_g"] == "20.600000"
        assert rows[-1]["load_g"] == "0.400000"
        assert rows[-1]["load_raw_last_g"] == "20.600000"
        assert rows[-1]["load_mean_g"] == "0.400000"
        assert rows[-1]["load_min_g"] == "0.200000"
        assert rows[-1]["load_max_g"] == "0.600000"
        assert rows[-1]["load_sample_count"] == "3"
        assert rows[-1]["scale_sample_rate_hz"] == "20.000000"

        raw_rows = list(
            csv.DictReader((run_dir / "scale_raw.csv").open(encoding="utf-8", newline=""))
        )
        assert [row["raw_load_g"] for row in raw_rows] == ["21.000000", "20.800000", "20.600000"]
        assert [row["applied_load_g"] for row in raw_rows] == ["0.200000", "0.400000", "0.600000"]

        metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["logging"]["log_interval_ms"] == 500
        assert metadata["logging"]["raw_scale_sidecar"] == "scale_raw.csv"
        assert metadata["logging"]["setup_csv"] == "setup.csv"
        assert metadata["logging"]["ui_telemetry_csv"] == "ui_telemetry.csv"
        assert metadata["logging"]["raw_scale_sample_count"] == 3
    finally:
        _close_test_window(window)


def test_session_writes_ui_refresh_telemetry(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("ui_telemetry_session")

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        assert window._session_ui_telemetry_path is not None
        window._ui_heartbeat_interval_ms = 16.0
        window._ui_heartbeat_fps = 62.5
        window.spin_graph_interval.setValue(500)
        window._write_ui_telemetry_sample(
            started_s=window._session_start_monotonic + 0.2,
            finished_s=window._session_start_monotonic + 0.212,
            previous_ui_s=window._session_start_monotonic,
            scale_sample_changed=True,
            dialog_sample_recorded=False,
            live_plot_sample_recorded=True,
            dashboard_plot_refreshed=True,
        )
        window._stop_session()

        rows = list(csv.DictReader((tmp_path / "ui_telemetry_session" / "ui_telemetry.csv").open(encoding="utf-8", newline="")))
        metadata = json.loads((tmp_path / "ui_telemetry_session" / "metadata.json").read_text(encoding="utf-8"))

        assert len(rows) == 1
        assert rows[0]["target_interval_ms"] == str(window._ui_refresh_interval_ms())
        assert rows[0]["actual_interval_ms"] == "200.000"
        assert rows[0]["ui_fps"] == "5.000"
        assert rows[0]["ui_heartbeat_interval_ms"] == "16.000"
        assert rows[0]["ui_heartbeat_fps"] == "62.500"
        assert rows[0]["handler_duration_ms"] == "12.000"
        assert rows[0]["graph_refresh_interval_ms"] == "500"
        assert rows[0]["task_text"] == "Manual mode"
        assert rows[0]["scale_sample_changed"] == "1"
        assert rows[0]["live_plot_sample_recorded"] == "1"
        assert rows[0]["dashboard_plot_refreshed"] == "1"
        assert metadata["logging"]["ui_telemetry_sample_count"] == 1
    finally:
        _close_test_window(window)


def test_session_control_trace_logs_current_task_text(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("control_trace_task")

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [
            mini_dma_mod.AutomationStep(
                "sweep_current",
                target_value=30.0,
                basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
                current_start_mA=1.0,
                current_end_mA=70.0,
                current_ramp_rate_mA_s=1.0,
                note="1",
            )
        ]
        window._automation_index = 0
        window._set_automation_context(
            phase="current",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=30.0,
            plateau_index=1,
        )
        window._active_current_sweep_display_target_mA = 70.0
        window._active_current_sweep_display_direction = 1.0
        window._supply_output_enabled = True
        window._supply_last_setpoint_mA = 42.0
        window._supply_snapshot = {
            "current_mA": 41.8,
            "voltage_V": 31.95,
            "resistance_ohm": 764.0,
            "power_W": 1.336,
        }
        window._supply_snapshot_monotonic = time.monotonic() - 2.0
        window.spin_supply_voltage_limit.setValue(32.05)
        window._write_control_trace(
            decision="accept",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=30.0,
            current_value=29.8,
            error_value=-0.2,
            tolerance=1.0,
            result="reached",
        )
        window._stop_session()

        rows = list(
            csv.DictReader((tmp_path / "control_trace_task" / "control_trace.csv").open(encoding="utf-8", newline=""))
        )

        assert len(rows) == 1
        assert rows[0]["task_text"] == "At 30 MPa: increasing current to 70 mA"
        assert rows[0]["supply_output_enabled"] == "1"
        assert rows[0]["supply_setpoint_mA"] == "42"
        assert rows[0]["supply_measured_current_mA"] == "41.8"
        assert rows[0]["supply_voltage_V"] == "31.95"
        assert rows[0]["supply_voltage_limit_V"] == "32.05"
        assert rows[0]["supply_resistance_ohm"] == "764"
        assert rows[0]["supply_power_W"] == "1.336"
        assert float(rows[0]["supply_snapshot_age_s"]) == pytest.approx(2.0, abs=0.5)
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_session_control_trace_accepts_row_local_task_text(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("control_trace_task_override")

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [
            mini_dma_mod.AutomationStep(
                "sweep_current",
                target_value=100.0,
                basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
                current_start_mA=60.0,
                current_end_mA=1.0,
                current_ramp_rate_mA_s=1.0,
                note="1",
            )
        ]
        window._automation_index = 0
        window._set_automation_context(
            phase="target_ramp",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=150.0,
            plateau_index=2,
        )
        window._active_current_sweep_display_target_mA = 1.0
        window._active_current_sweep_display_direction = -1.0
        window._write_control_trace(
            decision="correction",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=150.0,
            current_value=5.0,
            error_value=145.0,
            tolerance=1.0,
            result="move_sent",
            task_text="Ramp up to 150 MPa",
        )
        window._stop_session()

        rows = list(
            csv.DictReader(
                (tmp_path / "control_trace_task_override" / "control_trace.csv").open(
                    encoding="utf-8",
                    newline="",
                )
            )
        )

        assert len(rows) == 1
        assert rows[0]["task_text"] == "Ramp up to 150 MPa"
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_control_trace_write_failure_disables_trace_without_stopping(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    rows: list[dict[str, object]] = []

    class _FailingTraceWriter:
        def writerow(self, row: dict[str, object]) -> None:
            rows.append(row)

    class _FailingTraceHandle:
        def __init__(self) -> None:
            self.closed = False

        def flush(self) -> None:
            raise OSError(22, "Invalid argument")

        def close(self) -> None:
            self.closed = True

    handle = _FailingTraceHandle()

    try:
        window._session_active = True
        window._session_start_monotonic = time.monotonic()
        window._session_control_trace_writer = _FailingTraceWriter()  # type: ignore[assignment]
        window._session_control_trace_handle = handle

        window._write_control_trace(decision="wait", basis=mini_dma_mod.HSW_BASIS_STRESS_MPA)
        window._write_control_trace(decision="wait", basis=mini_dma_mod.HSW_BASIS_STRESS_MPA)

        assert len(rows) == 1
        assert handle.closed is True
        assert window._session_control_trace_writer is None
        assert window._session_control_trace_handle is None
        assert window._session_active is True
        assert "Control trace disabled after write failure" in window.log_output.toPlainText()
    finally:
        window._session_active = False
        _close_test_window(window)


def test_current_sweep_runtime_update_extends_active_step_and_logs_trace(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("runtime_recipe_update")

    active_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=70.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.5,
        note="1",
    )
    future_set_current = mini_dma_mod.AutomationStep(
        "set_current",
        target_value=100.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_mA=1.0,
        note="2",
    )
    future_ramp = mini_dma_mod.AutomationStep(
        "ramp_target",
        target_value=100.0,
        target_start_value=50.0,
        target_end_value=100.0,
        target_ramp_rate_value_s=5.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        note="2",
    )
    future_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=100.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=70.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.5,
        note="2",
    )
    future_reverse = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=100.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=70.0,
        current_end_mA=1.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.5,
        note="2",
    )

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [active_sweep, future_set_current, future_ramp, future_sweep, future_reverse]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._automation_phase = "current_hold"
        window._active_current_sweep_last_setpoint_mA = 44.0
        window._current_sweep_ramp_hold_step_index = 0
        window._current_sweep_ramp_hold_started_s = 123.0
        window._automation_interval_ms = 250
        window._recipe_estimated_points, window._automation_total_steps = window._estimate_recipe_points_and_ticks(
            window._automation_steps,
            window._automation_interval_ms,
        )

        window.spin_current_sweep_start_mA.setValue(2.0)
        window.spin_current_sweep_end_mA.setValue(80.0)
        window.spin_current_sweep_step_mA.setValue(0.6)
        window.spin_current_sweep_target_ramp_rate.setValue(3.0)
        window.check_current_sweep_hold_on_error.setChecked(False)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is True

        assert window._automation_steps[0] is not active_sweep
        assert window._automation_steps[0].current_start_mA == pytest.approx(1.0)
        assert window._automation_steps[0].current_end_mA == pytest.approx(80.0)
        assert window._automation_steps[0].current_ramp_rate_mA_s == pytest.approx(0.6)
        assert window._automation_steps[0].current_hold_enabled is False
        assert window._automation_phase == "current_hold"
        assert window._active_current_sweep_last_setpoint_mA == pytest.approx(44.0)
        assert window._current_sweep_ramp_hold_step_index is None
        assert window._current_sweep_ramp_hold_started_s == pytest.approx(0.0)
        assert window._active_current_sweep_started_s > 123.0
        assert window._automation_steps[1].current_mA == pytest.approx(2.0)
        assert window._automation_steps[2].target_ramp_rate_value_s == pytest.approx(3.0)
        assert window._automation_steps[3].current_start_mA == pytest.approx(2.0)
        assert window._automation_steps[3].current_end_mA == pytest.approx(80.0)
        assert window._automation_steps[3].current_ramp_rate_mA_s == pytest.approx(0.6)
        assert window._automation_steps[3].current_hold_enabled is False
        assert window._automation_steps[4].current_start_mA == pytest.approx(80.0)
        assert window._automation_steps[4].current_end_mA == pytest.approx(2.0)
        assert window._current_sweep_recipe_overrides

        window._stop_session()
        rows = list(
            csv.DictReader((tmp_path / "runtime_recipe_update" / "control_trace.csv").open(encoding="utf-8", newline=""))
        )
        assert len(rows) == 1
        assert rows[0]["decision"] == "recipe_update"
        assert rows[0]["task_text"] == "Updated active and remaining current sweeps"
        reason = json.loads(rows[0]["reason"])
        assert reason["active_step_updated"] is True
        assert reason["changed_step_count"] == 5
        assert reason["visible_values"]["current_end_mA"] == pytest.approx(80.0)

        metadata = json.loads((tmp_path / "runtime_recipe_update" / "metadata.json").read_text(encoding="utf-8"))
        overrides = metadata["controlled_current_sweep"]["runtime_overrides"]
        assert overrides[0]["active_step_updated"] is True
        assert overrides[0]["changed_step_count"] == 5
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_current_sweep_runtime_update_keeps_active_plateau_reverse_before_next_target(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("runtime_keep_active_reverse")

    active_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=400.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=80.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=False,
        note="8",
    )
    active_reverse = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=400.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=80.0,
        current_end_mA=1.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=False,
        note="8",
    )
    old_future = [
        mini_dma_mod.AutomationStep(
            "set_current",
            target_value=450.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_mA=1.0,
            note="9",
        ),
        mini_dma_mod.AutomationStep(
            "ramp_target",
            target_value=450.0,
            target_start_value=400.0,
            target_end_value=450.0,
            target_ramp_rate_value_s=5.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            note="9",
        ),
        mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=450.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=1.0,
            current_end_mA=80.0,
            current_ramp_rate_mA_s=1.0,
            current_hold_enabled=False,
            note="9",
        ),
    ]

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [active_sweep, active_reverse, *old_future]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
        window._automation_target_value = 400.0
        window._active_current_sweep_last_setpoint_mA = 42.0
        window._automation_interval_ms = 250
        window._recipe_estimated_points, window._automation_total_steps = window._estimate_recipe_points_and_ticks(
            window._automation_steps,
            window._automation_interval_ms,
        )

        window.spin_current_sweep_target_start.setValue(400.0)
        window.spin_current_sweep_target_end.setValue(500.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(85.0)
        window.spin_current_sweep_step_mA.setValue(0.4)
        window.check_current_sweep_hold_on_error.setChecked(False)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is True

        assert window._automation_steps[0].target_value == pytest.approx(400.0)
        assert window._automation_steps[0].current_start_mA == pytest.approx(1.0)
        assert window._automation_steps[0].current_end_mA == pytest.approx(85.0)
        assert window._automation_steps[1].target_value == pytest.approx(400.0)
        assert window._automation_steps[1].current_start_mA == pytest.approx(85.0)
        assert window._automation_steps[1].current_end_mA == pytest.approx(1.0)
        assert window._automation_steps[1].current_ramp_rate_mA_s == pytest.approx(0.4)
        assert window._automation_steps[2].action == "set_current"
        assert window._automation_steps[2].target_value == pytest.approx(450.0)
        assert window._automation_steps[2].current_mA == pytest.approx(1.0)

        window._stop_session()
        metadata = json.loads((tmp_path / "runtime_keep_active_reverse" / "metadata.json").read_text(encoding="utf-8"))
        override = metadata["controlled_current_sweep"]["runtime_overrides"][0]
        assert override["active_step_updated"] is True
        assert override["tail_replanned"] is True
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_current_sweep_runtime_update_disables_active_hold_without_current_target_change(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("runtime_disable_active_hold")

    active_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=20.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=85.0,
        current_ramp_rate_mA_s=0.4,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.5,
        note="1",
    )
    future_sweep = dataclasses.replace(active_sweep, target_value=50.0, note="2")

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [active_sweep, future_sweep]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._automation_phase = "current_hold"
        window._active_current_sweep_started_s = 100.0
        window._active_current_sweep_last_schedule_update_s = 123.0
        window._active_current_sweep_last_setpoint_mA = 64.0
        window._current_sweep_ramp_hold_step_index = 0
        window._current_sweep_ramp_hold_started_s = 123.0
        window._current_sweep_ramp_hold_in_band_since_s = 124.0
        window._current_sweep_ramp_hold_seek_accepted_since_s = 125.0
        window._automation_interval_ms = 250
        window._recipe_estimated_points, window._automation_total_steps = window._estimate_recipe_points_and_ticks(
            window._automation_steps,
            window._automation_interval_ms,
        )

        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(85.0)
        window.spin_current_sweep_step_mA.setValue(0.4)
        window.check_current_sweep_hold_on_error.setChecked(False)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is True

        assert window._automation_steps[0] is not active_sweep
        assert window._automation_steps[0].current_end_mA == pytest.approx(85.0)
        assert window._automation_steps[0].current_hold_enabled is False
        assert window._automation_steps[1].current_hold_enabled is False
        assert window._current_sweep_ramp_hold_step_index is None
        assert window._current_sweep_ramp_hold_started_s == pytest.approx(0.0)
        assert window._current_sweep_ramp_hold_in_band_since_s is None
        assert window._current_sweep_ramp_hold_seek_accepted_since_s is None
        assert window._active_current_sweep_last_setpoint_mA == pytest.approx(64.0)
        assert window._active_current_sweep_started_s > 123.0

        window._stop_session()
        metadata = json.loads((tmp_path / "runtime_disable_active_hold" / "metadata.json").read_text(encoding="utf-8"))
        override = metadata["controlled_current_sweep"]["runtime_overrides"][0]
        assert override["active_step_updated"] is True
        assert override["active_step_message"] == "active current ramp settings updated"
        assert override["changed_step_count"] == 2
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_current_sweep_runtime_update_leaves_active_step_when_setpoint_beyond_new_target(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("runtime_recipe_update_conservative")

    active_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=60.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.5,
        note="1",
    )
    future_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=100.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=60.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.5,
        note="2",
    )

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [active_sweep, future_sweep]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._automation_phase = "current_hold"
        window._active_current_sweep_last_setpoint_mA = 56.0
        window._automation_interval_ms = 250
        window._recipe_estimated_points, window._automation_total_steps = window._estimate_recipe_points_and_ticks(
            window._automation_steps,
            window._automation_interval_ms,
        )

        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(55.0)
        window.spin_current_sweep_step_mA.setValue(0.5)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is True

        assert window._automation_steps[0] is active_sweep
        assert window._automation_steps[0].current_end_mA == pytest.approx(60.0)
        assert window._active_current_sweep_last_setpoint_mA == pytest.approx(56.0)
        assert window._automation_steps[1].current_end_mA == pytest.approx(55.0)
        assert window._automation_steps[1].current_ramp_rate_mA_s == pytest.approx(0.5)

        window._stop_session()
        metadata = json.loads(
            (tmp_path / "runtime_recipe_update_conservative" / "metadata.json").read_text(encoding="utf-8")
        )
        override = metadata["controlled_current_sweep"]["runtime_overrides"][0]
        assert override["active_step_updated"] is False
        assert "already beyond" in override["active_step_message"]
        assert override["changed_step_count"] == 1
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_first_overheating_runtime_update_changes_active_ramp_before_old_max(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("runtime_firstheat_update")

    first_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=20.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=40.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=False,
        note="first_overheating",
    )
    first_reverse = dataclasses.replace(first_sweep, current_start_mA=40.0, current_end_mA=1.0)

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [first_sweep, first_reverse]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
        window._automation_target_value = 20.0
        window._active_current_sweep_last_setpoint_mA = 25.0
        window._automation_interval_ms = 250

        window.spin_current_sweep_target_start.setValue(20.0)
        window.spin_current_sweep_target_end.setValue(20.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(80.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(55.0)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is True

        assert window._automation_steps[0].current_end_mA == pytest.approx(55.0)
        assert window._automation_steps[0].note == "first_overheating"
        assert window._automation_steps[1].current_start_mA == pytest.approx(55.0)
        assert window._automation_steps[1].current_end_mA == pytest.approx(1.0)

        window._stop_session()
        metadata = json.loads((tmp_path / "runtime_firstheat_update" / "metadata.json").read_text(encoding="utf-8"))
        override = metadata["controlled_current_sweep"]["runtime_overrides"][0]
        assert override["active_step_updated"] is True
        assert override["visible_values"]["first_overheating_current_end_mA"] == pytest.approx(55.0)
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_first_overheating_runtime_update_rejects_after_preheat_ramp(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("runtime_firstheat_too_late")

    first_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=20.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=40.0,
        current_ramp_rate_mA_s=1.0,
        note="first_overheating",
    )
    normal_start = mini_dma_mod.AutomationStep(
        "set_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_mA=1.0,
        note="1",
    )

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [first_sweep, normal_start]
        window._automation_index = 1
        window._active_current_sweep_step_index = None
        window._active_current_sweep_last_setpoint_mA = 10.0
        window._automation_interval_ms = 250

        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(50.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(80.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(55.0)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is False
        assert window._automation_steps[0].current_end_mA == pytest.approx(40.0)
        assert "can only be changed before" in window.log_output.toPlainText()

        window._stop_session()
        rows = list(
            csv.DictReader((tmp_path / "runtime_firstheat_too_late" / "control_trace.csv").open(encoding="utf-8", newline=""))
        )
        assert rows[0]["result"] == "rejected"
        assert rows[0]["task_text"] == "Rejected first-overheating max-current update"
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_first_overheating_runtime_update_rejects_lowering_below_active_setpoint(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("runtime_firstheat_lower_reject")

    first_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=20.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=60.0,
        current_ramp_rate_mA_s=1.0,
        note="first_overheating",
    )

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [first_sweep]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._active_current_sweep_last_setpoint_mA = 56.0
        window._automation_interval_ms = 250

        window.spin_current_sweep_target_start.setValue(20.0)
        window.spin_current_sweep_target_end.setValue(20.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(80.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(55.0)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is False

        assert window._automation_steps[0] is first_sweep
        assert window._automation_steps[0].current_end_mA == pytest.approx(60.0)
        assert "was not lowered" in window.log_output.toPlainText()
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_first_overheating_runtime_update_refreshes_channel_limit_for_raise(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    limits: list[float] = []

    class _FakeSupply:
        def current_resolution_mA(self) -> float:
            return 0.1

        def set_current_limit_mA(self, value: float) -> None:
            limits.append(value)

        def disconnect(self) -> None:
            return None

    first_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=20.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=40.0,
        current_ramp_rate_mA_s=1.0,
        note="first_overheating",
    )

    try:
        window._supply_controller = _FakeSupply()  # type: ignore[assignment]
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [first_sweep]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._active_current_sweep_last_setpoint_mA = 25.0

        window.spin_supply_manual_current.setValue(1.0)
        window.spin_current_sweep_target_start.setValue(20.0)
        window.spin_current_sweep_target_end.setValue(20.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(30.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(60.0)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is True

        assert limits == pytest.approx([60.0])
        assert window._automation_steps[0].current_end_mA == pytest.approx(60.0)
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_first_overheating_runtime_update_lowers_without_refreshing_leased_limit(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        def current_resolution_mA(self) -> float:
            return 0.1

        def set_current_limit_mA(self, value: float) -> None:
            raise PermissionError(f"Cannot change CH4 role while it is leased ({value}).")

        def disconnect(self) -> None:
            return None

    first_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=20.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=40.0,
        current_ramp_rate_mA_s=1.0,
        note="first_overheating",
    )
    first_reverse = dataclasses.replace(first_sweep, current_start_mA=40.0, current_end_mA=1.0)

    try:
        window._supply_controller = _FakeSupply()  # type: ignore[assignment]
        profile_index = window.combo_supply_profile.findData("shared_hmp_broker")
        assert profile_index >= 0
        window.combo_supply_profile.setCurrentIndex(profile_index)
        window.combo_current_sweep_supply_channel.setCurrentIndex(
            window.combo_current_sweep_supply_channel.findData(4)
        )
        window._current_sweep_channel_limit_checked = (4, 40.0)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [first_sweep, first_reverse]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._active_current_sweep_last_setpoint_mA = 25.0

        window.spin_supply_manual_current.setValue(1.0)
        window.spin_current_sweep_target_start.setValue(20.0)
        window.spin_current_sweep_target_end.setValue(20.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(30.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(35.0)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is True

        assert window._automation_steps[0].current_end_mA == pytest.approx(35.0)
        assert window._automation_steps[1].current_start_mA == pytest.approx(35.0)
        assert window._current_sweep_channel_limit_checked == (4, 40.0)
        assert "Current-sweep channel limit update failed" not in window.log_output.toPlainText()
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_first_overheating_runtime_update_rejects_limit_failure_without_stopping(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        def __init__(self) -> None:
            self.current_limit_mA = 30.0

        def current_resolution_mA(self) -> float:
            return 0.1

        def set_current_limit_mA(self, value: float) -> None:
            self.current_limit_mA = value
            raise PermissionError("Cannot change CH4 role while it is leased.")

        def disconnect(self) -> None:
            return None

    first_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=20.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=30.0,
        current_ramp_rate_mA_s=1.0,
        note="first_overheating",
    )

    try:
        window._supply_controller = _FakeSupply()  # type: ignore[assignment]
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [first_sweep]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._active_current_sweep_last_setpoint_mA = 20.0

        window.spin_supply_manual_current.setValue(1.0)
        window.spin_current_sweep_target_start.setValue(20.0)
        window.spin_current_sweep_target_end.setValue(20.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(30.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(32.0)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is False

        assert window._automation_active is True
        assert window._automation_steps[0] is first_sweep
        assert window._automation_steps[0].current_end_mA == pytest.approx(30.0)
        assert window._current_sweep_recipe_overrides[-1]["result"] == "rejected"
        assert "Current-sweep channel limit update failed" in window.log_output.toPlainText()
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_normal_current_sweep_runtime_update_ignores_independent_first_overheating_max(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    active_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=80.0,
        current_ramp_rate_mA_s=1.0,
        note="1",
    )

    try:
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [active_sweep]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._active_current_sweep_last_setpoint_mA = 40.0

        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(50.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(90.0)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(30.0)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is True

        assert window._automation_steps[0].note == "1"
        assert window._automation_steps[0].current_end_mA == pytest.approx(90.0)
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_current_sweep_runtime_update_replans_future_stress_targets(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("runtime_target_replan")

    active_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=50.0,
        current_ramp_rate_mA_s=1.0,
        note="1",
    )
    old_future = [
        mini_dma_mod.AutomationStep(
            "set_current",
            target_value=100.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_mA=1.0,
            note="2",
        ),
        mini_dma_mod.AutomationStep(
            "ramp_target",
            target_value=100.0,
            target_start_value=50.0,
            target_end_value=100.0,
            target_ramp_rate_value_s=5.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            note="2",
        ),
        mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=100.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=1.0,
            current_end_mA=50.0,
            current_ramp_rate_mA_s=1.0,
            note="2",
        ),
        mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=100.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=50.0,
            current_end_mA=1.0,
            current_ramp_rate_mA_s=1.0,
            note="2",
        ),
    ]

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [active_sweep, *old_future]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._automation_interval_ms = 250
        window._recipe_estimated_points, window._automation_total_steps = window._estimate_recipe_points_and_ticks(
            window._automation_steps,
            window._automation_interval_ms,
        )

        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(150.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.spin_current_sweep_target_ramp_rate.setValue(4.0)
        window.check_current_sweep_return_target.setChecked(False)
        window.spin_current_sweep_start_mA.setValue(2.0)
        window.spin_current_sweep_end_mA.setValue(80.0)
        window.spin_current_sweep_step_mA.setValue(0.5)

        assert window._apply_current_sweep_pending_overrides(show_message=False) is True

        assert window._automation_steps[0] is not active_sweep
        assert window._automation_steps[0].current_end_mA == pytest.approx(80.0)
        future_targets = [
            step.target_value
            for step in window._automation_steps[1:]
            if step.action == "sweep_current" and step.current_start_mA < step.current_end_mA
        ]
        assert future_targets == [pytest.approx(100.0), pytest.approx(150.0)]
        future_ramps = [step for step in window._automation_steps[1:] if step.action == "ramp_target"]
        assert [(step.target_start_value, step.target_end_value) for step in future_ramps] == [
            (pytest.approx(50.0), pytest.approx(100.0)),
            (pytest.approx(100.0), pytest.approx(150.0)),
        ]
        assert all(step.target_ramp_rate_value_s == pytest.approx(4.0) for step in future_ramps)
        assert all(
            step.current_ramp_rate_mA_s == pytest.approx(0.5)
            for step in window._automation_steps[1:]
            if step.action == "sweep_current"
        )

        window._stop_session()
        metadata = json.loads((tmp_path / "runtime_target_replan" / "metadata.json").read_text(encoding="utf-8"))
        override = metadata["controlled_current_sweep"]["runtime_overrides"][0]
        assert override["active_step_updated"] is True
        assert override["tail_replanned"] is True
        assert override["visible_values"]["target_end"] == pytest.approx(150.0)
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_current_sweep_runtime_update_button_waits_for_pending_changes(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("runtime_pending_ui")

    active_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=70.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.5,
        note="1",
    )
    future_set_current = mini_dma_mod.AutomationStep(
        "set_current",
        target_value=100.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_mA=1.0,
        note="2",
    )
    future_ramp = mini_dma_mod.AutomationStep(
        "ramp_target",
        target_value=100.0,
        target_start_value=50.0,
        target_end_value=100.0,
        target_ramp_rate_value_s=5.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        note="2",
    )
    future_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=100.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=70.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.5,
        note="2",
    )
    future_reverse = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=100.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=70.0,
        current_end_mA=1.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.5,
        note="2",
    )

    try:
        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(100.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.spin_current_sweep_target_ramp_rate.setValue(5.0)
        window.check_current_sweep_return_target.setChecked(False)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(70.0)
        window.spin_current_sweep_step_mA.setValue(1.0)
        window.check_current_sweep_hold_on_error.setChecked(True)
        window.spin_current_sweep_hold_pause_factor.setValue(2.0)
        window.spin_current_sweep_hold_resume_factor.setValue(1.0)
        window.spin_current_sweep_hold_resume_stable_s.setValue(0.5)

        window._start_session(enable_logging=False, record_initial_point=False)
        window._automation_active = True
        window._automation_paused = False
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [active_sweep, future_set_current, future_ramp, future_sweep, future_reverse]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._active_current_sweep_last_setpoint_mA = 44.0
        window._automation_interval_ms = 250
        window._recipe_estimated_points, window._automation_total_steps = window._estimate_recipe_points_and_ticks(
            window._automation_steps,
            window._automation_interval_ms,
        )
        window._current_sweep_runtime_applied_values = window._current_sweep_visible_runtime_values_from_controls()
        window._update_recipe_buttons()

        assert window._current_sweep_pending_update_preview()["changed_steps"] == []
        assert window.button_apply_current_sweep_edits.isHidden() is True
        assert window.button_apply_current_sweep_edits.isEnabled() is False
        assert window.spin_current_sweep_end_mA.property("_mini_dma_runtime_pending") is False

        window.spin_current_sweep_end_mA.setValue(80.0)
        window._update_recipe_buttons()

        preview = window._current_sweep_pending_update_preview()
        assert preview["changed_steps"]
        assert window.button_apply_current_sweep_edits.isHidden() is False
        assert window.button_apply_current_sweep_edits.isEnabled() is True
        assert window.spin_current_sweep_end_mA.property("_mini_dma_runtime_pending") is True
        assert window.spin_current_sweep_start_mA.property("_mini_dma_runtime_pending") is False

        assert window._apply_current_sweep_pending_overrides(show_message=False) is True

        assert window.spin_current_sweep_end_mA.property("_mini_dma_runtime_pending") is False
        assert window.button_apply_current_sweep_edits.isHidden() is True
        assert window.button_apply_current_sweep_edits.isEnabled() is False
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_current_sweep_runtime_pending_highlight_tracks_target_replan_fields(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    active_sweep = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_start_mA=1.0,
        current_end_mA=50.0,
        current_ramp_rate_mA_s=1.0,
        current_hold_enabled=True,
        current_hold_pause_tolerance_factor=2.0,
        current_hold_resume_tolerance_factor=1.0,
        current_hold_resume_stable_s=0.5,
        note="1",
    )
    old_future = [
        mini_dma_mod.AutomationStep(
            "set_current",
            target_value=100.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_mA=1.0,
            note="2",
        ),
        mini_dma_mod.AutomationStep(
            "ramp_target",
            target_value=100.0,
            target_start_value=50.0,
            target_end_value=100.0,
            target_ramp_rate_value_s=5.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            note="2",
        ),
        mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=100.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=1.0,
            current_end_mA=50.0,
            current_ramp_rate_mA_s=1.0,
            current_hold_enabled=True,
            current_hold_pause_tolerance_factor=2.0,
            current_hold_resume_tolerance_factor=1.0,
            current_hold_resume_stable_s=0.5,
            note="2",
        ),
        mini_dma_mod.AutomationStep(
            "sweep_current",
            target_value=100.0,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            current_start_mA=50.0,
            current_end_mA=1.0,
            current_ramp_rate_mA_s=1.0,
            current_hold_enabled=True,
            current_hold_pause_tolerance_factor=2.0,
            current_hold_resume_tolerance_factor=1.0,
            current_hold_resume_stable_s=0.5,
            note="2",
        ),
    ]

    try:
        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(100.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.spin_current_sweep_target_ramp_rate.setValue(5.0)
        window.check_current_sweep_return_target.setChecked(False)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(50.0)
        window.spin_current_sweep_step_mA.setValue(1.0)
        window.check_current_sweep_hold_on_error.setChecked(True)
        window.spin_current_sweep_hold_pause_factor.setValue(2.0)
        window.spin_current_sweep_hold_resume_factor.setValue(1.0)
        window.spin_current_sweep_hold_resume_stable_s.setValue(0.5)

        window._automation_active = True
        window._automation_paused = False
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [active_sweep, *old_future]
        window._automation_index = 0
        window._active_current_sweep_step_index = 0
        window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
        window._automation_target_value = 50.0
        window._current_sweep_runtime_applied_values = window._current_sweep_visible_runtime_values_from_controls()
        window._update_recipe_buttons()

        assert window.button_apply_current_sweep_edits.isHidden() is True

        window.spin_current_sweep_target_end.setValue(150.0)
        window._update_recipe_buttons()

        assert window.button_apply_current_sweep_edits.isHidden() is False
        assert window.spin_current_sweep_target_end.property("_mini_dma_runtime_pending") is True
        assert window.spin_current_sweep_target_start.property("_mini_dma_runtime_pending") is False
        assert window.spin_current_sweep_step_mA.property("_mini_dma_runtime_pending") is False
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_current_sweep_runtime_editability_marks_locked_controls(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._automation_active = True
        window._automation_paused = False
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._update_recipe_buttons()

        assert window.spin_current_sweep_target_start.isReadOnly() is False
        assert window.spin_current_sweep_target_end.isReadOnly() is False
        assert window.spin_current_sweep_step_mA.isReadOnly() is False
        assert window.spin_current_sweep_target_speed_mm_s.isReadOnly() is True
        assert window.spin_current_sweep_first_overheating_target_mpa.isReadOnly() is True
        assert window.combo_current_sweep_basis.isEnabled() is False
        assert window.spin_current_sweep_target_speed_mm_s.property("_mini_dma_runtime_locked") is True

        window._automation_active = False
        window._update_recipe_buttons()

        assert window.spin_current_sweep_target_speed_mm_s.isReadOnly() is False
        assert window.spin_current_sweep_first_overheating_target_mpa.isReadOnly() is False
        assert window.combo_current_sweep_basis.isEnabled() is True
        assert window.spin_current_sweep_target_speed_mm_s.property("_mini_dma_runtime_locked") is False
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_setup_raw_scale_samples_are_logged_before_main_measurement_starts(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("setup_raw_samples")

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        assert window._session_active is True
        assert window._session_raw_scale_path is not None

        window._write_raw_scale_sample(
            mini_dma_mod.ScaleSample(
                timestamp_s=time.time(),
                raw_g=21.19,
                applied_load_g=0.02,
                raw_text="21.190 g",
            )
        )

        rows = list(csv.DictReader(window._session_raw_scale_path.open(encoding="utf-8", newline="")))
        assert len(rows) == 1
        assert rows[0]["raw_load_g"] == "21.190000"
        assert rows[0]["applied_load_g"] == "0.020000"
    finally:
        _close_test_window(window)


def test_raw_scale_elapsed_stays_continuous_when_measurement_logging_starts(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("raw_elapsed_continuous")

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        assert window._session_raw_scale_path is not None
        window._session_raw_scale_start_wall_s = 1000.0

        window._write_raw_scale_sample(
            mini_dma_mod.ScaleSample(
                timestamp_s=1001.0,
                raw_g=21.19,
                applied_load_g=0.02,
                raw_text="21.190 g",
            )
        )
        window._begin_recipe_logging()
        window._write_raw_scale_sample(
            mini_dma_mod.ScaleSample(
                timestamp_s=1002.0,
                raw_g=21.18,
                applied_load_g=0.03,
                raw_text="21.180 g",
            )
        )

        rows = list(csv.DictReader(window._session_raw_scale_path.open(encoding="utf-8", newline="")))
        assert [row["elapsed_s"] for row in rows] == ["1.000000", "2.000000"]
    finally:
        _close_test_window(window)


def test_length_setup_points_are_written_to_setup_sidecar(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("setup_sidecar_session")
    window.check_tension_load_positive.setChecked(True)
    window.check_zero_on_preload.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.2)
    window._latest_scale_value_g = 21.0
    window._latest_scale_text = "21.000 g"
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 0.25
    window._effective_position_mm = 0.25
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

    try:
        window._start_session(enable_logging=False, record_initial_point=False)
        window._set_automation_context(
            phase="target_ramp",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=10.0,
            note="setup_preload",
        )
        assert window._record_length_setup_point() is True
        window._stop_session()

        run_dir = tmp_path / "setup_sidecar_session"
        setup_rows = list(csv.DictReader((run_dir / "setup.csv").open(encoding="utf-8", newline="")))
        measurement_rows = list(csv.DictReader((run_dir / "measurement.csv").open(encoding="utf-8", newline="")))
        metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))

        assert len(setup_rows) == 1
        assert setup_rows[0]["automation_phase"] == "target_ramp"
        assert setup_rows[0]["automation_basis"] == mini_dma_mod.HSW_BASIS_STRESS_MPA
        assert setup_rows[0]["raw_position_mm"] == "0.250000"
        assert setup_rows[0]["load_g"] == "0.200000"
        assert measurement_rows == []
        assert metadata["logging"]["setup_csv"] == "setup.csv"
    finally:
        _close_test_window(window)


def test_global_control_and_log_intervals_migrate_from_current_sweep_settings(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("current_sweep_interval_ms", 375)
    settings.setValue("current_sweep_log_interval_ms", 875)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        assert window.spin_control_interval.value() == 375
        assert window.spin_log_interval.value() == 875
        assert window.spin_current_sweep_interval.value() == 375
        assert window.spin_current_sweep_log_interval.value() == 875
        window.spin_control_interval.setValue(250)
        window.spin_log_interval.setValue(500)
        window._save_settings()
        assert int(settings.value("control_interval_ms")) == 250
        assert int(settings.value("log_interval_ms")) == 500
        assert int(settings.value("current_sweep_interval_ms")) == 250
        assert int(settings.value("current_sweep_log_interval_ms")) == 500
    finally:
        _close_test_window(window)


def test_logged_load_clamps_non_tensile_side_of_reference_to_zero(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("positive_tension_magnitude_log")
    window.check_tension_load_positive.setChecked(False)
    window.check_zero_on_preload.setChecked(False)
    window._latest_scale_value_g = -2.5
    window._latest_scale_text = "-2.500 g"
    window._latest_scale_timestamp = time.time()
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

    try:
        window._start_session()
        window._stop_session()

        rows = list(
            csv.DictReader(
                (tmp_path / "positive_tension_magnitude_log" / "measurement.csv").open(
                    encoding="utf-8",
                    newline="",
                )
            )
        )
        assert rows[0]["raw_load_g"] == "-2.500000"
        assert rows[0]["load_g"] == "0.000000"
    finally:
        _close_test_window(window)


def test_logged_displacement_is_positive_for_tensile_pull_direction(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("positive_displacement_log")
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.check_zero_on_preload.setChecked(False)
    window._latest_scale_value_g = -1.0
    window._latest_scale_text = "-1.000 g"
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 0.0
    window._current_position_steps = 0
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]

    try:
        window._start_session()
        window._current_position_mm = -0.5
        window._current_position_steps = -50
        window._record_current_point()

        point = window._session_points[-1]
        assert point.raw_position_mm == pytest.approx(-0.5)
        assert point.position_mm == pytest.approx(0.5)
        assert point.strain_pct == pytest.approx((0.5 / window.spin_initial_length.value()) * 100.0)

        rows = list(
            csv.DictReader(
                (tmp_path / "positive_displacement_log" / "measurement.csv").open(
                    encoding="utf-8",
                    newline="",
                )
            )
        )
        assert rows[-1]["raw_position_mm"] == "-0.500000"
        assert rows[-1]["position_mm"] == "0.500000"
    finally:
        _close_test_window(window)


@pytest.mark.parametrize(("automation_active", "expected_flush", "expected_throttle"), [(True, False, True), (False, True, False)])
def test_record_current_point_throttles_disk_work_only_during_automation(
    tmp_path: Path,
    qtbot,
    automation_active: bool,
    expected_flush: bool,
    expected_throttle: bool,
) -> None:
    window = _build_window(tmp_path, qtbot)
    write_flushes: list[bool] = []
    metadata_throttles: list[bool] = []

    try:
        now_s = time.monotonic()
        window._session_active = True
        window._session_logging_enabled = True
        window._session_start_monotonic = now_s - 10.0
        window._session_start_wall_s = time.time() - 10.0
        window._last_session_log_timestamp_s = None
        window._last_session_data_flush_s = now_s
        window._last_dashboard_plot_refresh_s = now_s
        window._automation_active = automation_active
        window._latest_scale_value_g = 0.25
        window._latest_scale_timestamp = time.time()
        window._latest_scale_text = "0.250 g"
        window._write_point = lambda _point, *, flush=True: write_flushes.append(bool(flush))  # type: ignore[method-assign]
        window._write_session_metadata = lambda **kwargs: metadata_throttles.append(bool(kwargs.get("throttle")))  # type: ignore[method-assign]
        window._refresh_plots = lambda: None  # type: ignore[method-assign]
        window._refresh_live_labels = lambda: None  # type: ignore[method-assign]

        assert window._record_current_point(quiet=True, require_fresh_after_move=False) is True

        assert write_flushes == [expected_flush]
        assert metadata_throttles == [expected_throttle]
    finally:
        window._automation_active = False
        window._session_active = False
        _close_test_window(window)


def test_emergency_stop_turns_off_current_halts_tic_and_stops_session(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("emergency_stop")
    window._record_current_point = lambda: None  # type: ignore[method-assign]

    class _FakeSupply:
        def __init__(self) -> None:
            self.off_count = 0
            self.configured: list[tuple[int, float, float, bool]] = []
            self.selected: list[int | None] = []

        def is_connected(self) -> bool:
            return True

        def disconnect(self) -> None:
            return None

        def configure_channel(self, *, channel: int, voltage_v: float, current_a: float, output_on: bool) -> None:
            self.configured.append((channel, voltage_v, current_a, output_on))

        def select_channel(self, channel: int | None = None) -> None:
            self.selected.append(channel)

        def output_off(self) -> None:
            self.off_count += 1

        def measure(self) -> dict[str, float | None]:
            return {
                "current_mA": 0.0,
                "voltage_V": 0.0,
                "resistance_ohm": None,
                "power_W": 0.0,
            }

    class _FakeTic:
        def __init__(self) -> None:
            self.halted = False

        def halt_and_hold(self) -> None:
            self.halted = True

    supply = _FakeSupply()
    tic = _FakeTic()
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = 12.5
    window._build_tic_controller = lambda: tic  # type: ignore[method-assign]
    window.combo_motor_supply_channel.setCurrentIndex(window.combo_motor_supply_channel.findData(2))

    try:
        window._start_session()
        window._automation_active = True
        window._auto_ramp_timer.start(100)

        assert window.button_emergency_stop.text() == "EMERGENCY STOP"
        assert "background-color: #b91c1c" in window.button_emergency_stop.styleSheet()

        window._emergency_stop()

        assert supply.off_count >= 1
        assert (2, 12.0, 0.5, False) in supply.configured
        assert tic.halted is True
        assert window._supply_output_enabled is False
        assert window._supply_last_setpoint_mA == pytest.approx(1.0)
        assert window._automation_active is False
        assert window._session_active is False
        assert not window._auto_ramp_timer.isActive()
    finally:
        _close_test_window(window)


def test_tensile_load_seek_uses_motion_direction_independent_of_scale_sign(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 1.0
    window.spin_distribution_nudge_mm.setValue(0.1)

    targets: list[float] = []

    def _capture_move(target_mm: float, **_kwargs: object) -> bool:
        targets.append(target_mm)
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )

        assert reached is False
        assert targets == [pytest.approx(0.9)]
    finally:
        _close_test_window(window)


def test_recipe_seek_does_not_stack_corrections_ahead_of_confirmed_position(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(False)
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 1.0
    window._current_position_steps = 100
    window._last_move_target_mm = 1.0
    window._manual_jog_uses_last_target = False
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_distribution_nudge_mm.setValue(0.1)

    try:
        window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )
        _wait_for_tic_commands(window)
        window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )
        _wait_for_tic_commands(window)

        assert controller.targets == [90]
    finally:
        _close_test_window(window)


def test_load_seek_continues_from_commanded_target_after_fresh_feedback_without_tic_status(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 0.0
    window._current_position_steps = 0
    window._last_move_target_mm = 0.0
    window._last_tic_status_time_s = time.time() - 10.0
    window._manual_jog_uses_last_target = False
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_distribution_nudge_mm.setValue(0.1)

    try:
        window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )
        assert window._build_tic_dispatcher().wait_until_idle(timeout_s=2.0)
        window._latest_scale_timestamp = time.time()
        window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )
        assert window._build_tic_dispatcher().wait_until_idle(timeout_s=2.0)

        assert controller.targets == [-10]

        window._last_motion_expected_complete_time_s = time.time() - 0.1
        window._latest_scale_timestamp = time.time()
        window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )
        assert window._build_tic_dispatcher().wait_until_idle(timeout_s=2.0)

        assert controller.targets == [-10, -20]
        assert "Move skipped" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_seek_overshoot_uses_fine_reverse_correction(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    targets: list[float] = []

    def _capture_move(target_mm: float, **_kwargs: object) -> bool:
        targets.append(target_mm)
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 1.0
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_distribution_nudge_mm.setValue(0.1)

    try:
        window._latest_scale_value_g = 0.0
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        ) is False

        window._latest_scale_value_g = -6.0
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        window._latest_scale_timestamp = time.time()
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        ) is False

        assert targets == [pytest.approx(0.9), pytest.approx(1.025)]
        assert "Overshoot detected" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_load_seek_accepts_near_target_crossing_without_reverse_hunt(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_initial_length.setValue(20.0)
    window.spin_distribution_nudge_mm.setValue(0.1)
    window.spin_backlash_mm.setValue(0.03)
    window._calibrated_stiffness_g_per_mm = 10.0
    window._calibrated_stiffness_length_mm = 20.0
    window._current_position_mm = 1.0
    window._current_position_steps = 100
    window._last_move_target_mm = 1.0
    window._last_move_direction = -1.0
    window._latest_scale_timestamp = time.time()

    try:
        window._latest_scale_value_g = 0.0
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        ) is False
        _wait_for_tic_commands(window)

        window._latest_scale_value_g = -5.35
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        window._latest_scale_timestamp = time.time()
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        ) is True
        _wait_for_tic_commands(window)

        assert controller.targets == [90]
        assert "backlash take-up" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_seek_direction_reversal_applies_backlash_takeup(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 1.0
    window._current_position_steps = 100
    window._last_move_target_mm = 1.0
    window._manual_jog_uses_last_target = False
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_distribution_nudge_mm.setValue(0.1)
    window.spin_backlash_mm.setValue(0.03)

    try:
        window._latest_scale_value_g = 0.0
        window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )
        _wait_for_tic_commands(window)

        window._latest_scale_value_g = -20.0
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        window._latest_scale_timestamp = time.time()
        window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )
        _wait_for_tic_commands(window)

        assert len(controller.targets) == 2
        assert controller.targets[0] == 90
        assert controller.targets[1] == 96
        assert "backlash take-up" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_calibration_seek_ignores_existing_backlash_compensation(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._automation_phase = "seek"
    window._automation_step_note = "1"
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 1.0
    window._current_position_steps = 100
    window._last_move_target_mm = 1.0
    window._manual_jog_uses_last_target = False
    window._last_move_direction = 1.0
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_distribution_nudge_mm.setValue(0.1)
    window.spin_backlash_mm.setValue(0.03)
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_LOAD_G, 5.0)
    window._seek_last_error_by_key[seek_key] = 1.0

    try:
        window._latest_scale_value_g = -4.0
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )
        _wait_for_tic_commands(window)

        assert reached is False
        assert controller.targets == [99]
        assert "backlash take-up" not in window.log_output.toPlainText()
        assert "backlash-limited tolerance" not in window.log_output.toPlainText()
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_backlash_takeup_is_not_logged_as_tensile_displacement(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.check_zero_on_preload.setChecked(False)
    window.spin_steps_per_mm.setValue(1000.0)
    window.spin_initial_length.setValue(10.0)
    window.spin_distribution_nudge_mm.setValue(0.1)
    window.spin_distribution_seek_speed_mm_s.setValue(10.0)
    window.spin_backlash_mm.setValue(0.03)
    window._automation_interval_ms = 1000
    window._position_reference_mm = 0.0
    window._current_position_mm = 0.1
    window._effective_position_mm = 0.1
    window._last_effective_move_target_mm = 0.1
    window._current_position_steps = 100
    window._last_move_target_mm = 0.1
    window._last_move_direction = 1.0
    window._latest_scale_value_g = 5.10
    window._latest_scale_timestamp = time.time()
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_LOAD_G, 5.0)
    window._seek_last_error_by_key[seek_key] = 1.0

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.02,
        )
        _wait_for_tic_commands(window)
        point = window._capture_measurement_point(
            elapsed_s=1.0,
            position_mm=window._measurement_position_mm(),
            effective_position_mm=window._measurement_effective_position_mm(),
            raw_load_g=5.10,
            load_g=5.10,
        )

        assert reached is False
        assert controller.targets == [45]
        assert point.raw_position_mm == pytest.approx(0.045)
        assert point.position_mm == pytest.approx(0.045)
        assert point.strain_pct == pytest.approx(0.45)
    finally:
        _close_test_window(window)


def test_seek_uses_calibrated_length_scaled_sensitivity_for_correction_distance(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.target_steps: int | None = None

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.target_steps = position_steps

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_steps_per_mm.setValue(1000.0)
    window.spin_initial_length.setValue(40.0)
    window.spin_distribution_nudge_mm.setValue(1.0)
    window.spin_distribution_seek_speed_mm_s.setValue(10.0)
    window._automation_interval_ms = 1000
    window._calibrated_stiffness_g_per_mm = 10.0
    window._calibrated_stiffness_length_mm = 20.0
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = time.time()

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=1.0,
            tolerance=0.01,
        )
        _wait_for_tic_commands(window)

        assert reached is False
        assert controller.target_steps == 150
    finally:
        _close_test_window(window)


def test_seek_tolerance_floor_uses_stiffness_and_motor_step(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_initial_length.setValue(20.0)
    window._calibrated_stiffness_g_per_mm = 10.0
    window._calibrated_stiffness_length_mm = 20.0

    try:
        assert window._seek_effective_tolerance(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            requested_tolerance=0.005,
        ) == pytest.approx(0.1)
    finally:
        _close_test_window(window)


def test_automation_tolerance_uses_automatic_load_floor(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_diameter.setValue(0.02)
    window.spin_setup_preload_tolerance_mpa.setValue(99.0)
    window.spin_calibration_tolerance_g.setValue(9.0)
    window.spin_current_sweep_tolerance.setValue(99.0)
    window._automation_name = mini_dma_mod.CALIBRATION

    try:
        setup_step = mini_dma_mod.AutomationStep(
            "target_ramp",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            note="setup_preload",
        )
        calibration_step = mini_dma_mod.AutomationStep(
            "settle",
            basis=mini_dma_mod.HSW_BASIS_LOAD_G,
            note=mini_dma_mod.CALIBRATION_PRELOAD,
        )
        stress_tolerance = window._automation_tolerance_for_step(setup_step)
        load_tolerance = window._automation_tolerance_for_step(calibration_step)

        assert load_tolerance == pytest.approx(0.005)
        assert stress_tolerance == pytest.approx(
            mini_dma_mod.stress_mpa_from_load_g(0.005, 0.02)
        )
    finally:
        _close_test_window(window)


def test_dashboard_uses_compact_live_value_cells_without_overview(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert not hasattr(window, "overview_section")
        assert "speed_mm_s" in window._dashboard_value_labels
        assert "scale" not in window._dashboard_value_labels
        assert "task" in window._dashboard_value_labels
        assert not window._dashboard_value_labels["task"].wordWrap()
        assert window._dashboard_value_labels["task"].height() > 0
        assert window.dashboard_status_box.parentWidget() is window.dashboard_header
        positions = [
            window.dashboard_status_box.layout().getItemPosition(index)[:2]
            for index in range(window.dashboard_status_box.layout().count())
        ]
        assert max(row for row, _column in positions) == 2
        assert max(column for _row, column in positions) == 2
        width = window._dashboard_value_labels["speed_mm_s"].minimumWidth()
        assert 54 <= width <= 90
        assert not window._dashboard_value_labels["speed_mm_s"].font().fixedPitch()
    finally:
        _close_test_window(window)


def test_setup_zero_plateau_fallback_runs_before_target_acceptance(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_zero_load_scale_g.setValue(21.17)
    window._calibrated_stiffness_g_per_mm = 1.56
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="seek",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=0.0,
        note="setup_return_zero",
    )
    window._setup_return_zero_start_point_index = 0
    window._current_position_mm = 38.44
    window._current_position_steps = 3844
    window._last_move_target_mm = 38.44
    window._latest_scale_value_g = 21.16
    window._latest_scale_timestamp = time.time()
    for index in range(8):
        raw = 21.165 if index % 2 == 0 else 21.16
        point = _calibration_point(
            position_mm=37.65 + index * 0.12,
            load_g=abs(21.17 - raw),
            phase="setup_return_zero",
        )
        point.raw_load_g = raw
        point.elapsed_s = index * 0.4
        window._length_setup_points.append(point)

    moves: list[float] = []

    def _capture_move(target_mm: float, **_kwargs: object) -> bool:
        moves.append(target_mm)
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.005,
        )

        assert reached is False
        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.17)
        assert window._zero_load_scale_reference_g() == pytest.approx(21.1625)
        assert moves == []
        assert "Detected zero-load plateau" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_recovery_zero_plateau_fallback_runs_before_target_acceptance(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_zero_load_scale_g.setValue(21.17)
    window._calibrated_stiffness_g_per_mm = 1.56
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.RECOVERY_LOAD
    window._set_automation_context(
        phase="recover",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=0.0,
        note="0",
    )
    window._end_zero_fallback_armed = True
    window._end_zero_fallback_start_point_index = 0
    window._current_position_mm = 38.44
    window._current_position_steps = 3844
    window._last_move_target_mm = 38.44
    window._latest_scale_value_g = 21.16
    window._latest_scale_timestamp = time.time()
    for index in range(8):
        raw = 21.165 if index % 2 == 0 else 21.16
        point = _calibration_point(
            position_mm=37.65 + index * 0.12,
            load_g=abs(21.17 - raw),
            phase="recovery_load",
        )
        point.raw_load_g = raw
        point.elapsed_s = index * 0.4
        window._recovery_points.append(point)

    moves: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.005,
        )

        assert reached is False
        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.17)
        assert window._zero_load_scale_reference_g() == pytest.approx(21.1625)
        assert moves == []
        assert window._end_zero_fallback_armed is False
    finally:
        _close_test_window(window)


def test_setup_preload_tiny_baseline_load_uses_stiffness_capped_slack_takeup(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.target_steps: int | None = None
            self.max_speed: int | None = None

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.target_steps = position_steps
            self.max_speed = max_speed

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_steps_per_mm.setValue(10000.0)
    window.spin_diameter.setValue(0.0137)
    window.spin_zero_load_scale_g.setValue(21.17)
    window.spin_scale_interval.setValue(250)
    window.spin_initial_length.setValue(20.0)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window.spin_setup_slack_speed_strain_pct_s.setValue(1.0)
    window.spin_setup_preload_duration_s.setValue(10.0)
    window._calibrated_stiffness_g_per_mm = 1.56
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        note="setup_preload",
    )
    window._current_position_mm = 1.0
    window._current_position_steps = 10000
    window._last_move_target_mm = 1.0
    window._latest_scale_value_g = 21.15
    window._latest_scale_timestamp = time.time()

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=window._auto_requested_tolerance_for_basis(mini_dma_mod.HSW_BASIS_STRESS_MPA),
        )
        _wait_for_tic_commands(window)

        assert reached is False
        assert controller.target_steps is not None
        stiffness_mpa_per_mm = mini_dma_mod.stress_mpa_from_load_g(1.56, window.spin_diameter.value())
        assert stiffness_mpa_per_mm is not None
        assert abs((controller.target_steps / 10000.0) - 1.0) <= (
            50.0 / stiffness_mpa_per_mm
        ) + window._motor_step_mm()
        assert controller.max_speed == 20_000_000
    finally:
        _close_test_window(window)


def test_setup_preload_fast_scale_quantization_does_not_collapse_to_single_step(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        window.spin_steps_per_mm.setValue(800.0)
        window.spin_diameter.setValue(0.0182)
        window.spin_scale_interval.setValue(50)
        window.spin_motion_speed_mm_s.setValue(0.1)
        window.spin_setup_slack_step_cap_stress_mpa.setValue(50.0)
        window._automation_interval_ms = 50
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._set_automation_context(
            phase="target_ramp",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            note="setup_preload",
        )
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
        apparent_sensitivity_mpa_per_mm = 520.0
        apparent_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
            apparent_sensitivity_mpa_per_mm,
            window.spin_diameter.value(),
        )
        assert apparent_stiffness_g_per_mm is not None
        window._seek_live_stiffness_by_key[seek_key] = apparent_stiffness_g_per_mm

        correction_mm = window._predictive_seek_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            error_value=4.0,
            tolerance=window._auto_requested_tolerance_for_basis(mini_dma_mod.HSW_BASIS_STRESS_MPA),
            seek_key=seek_key,
        )

        assert correction_mm == pytest.approx(4.0 / apparent_sensitivity_mpa_per_mm * 0.75)
        assert correction_mm > window._motor_step_mm() * 3.0
    finally:
        _close_test_window(window)


def test_setup_return_zero_uses_return_time_speed(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.17)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_diameter.setValue(0.0137)
    window.spin_setup_return_duration_s.setValue(5.0)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window._calibrated_stiffness_g_per_mm = 22.7
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=0.0,
        note="setup_return_zero",
    )
    window._latest_scale_value_g = 20.17
    window._latest_scale_timestamp = time.time()

    try:
        speed = window._seek_speed_mm_s(
            -1.0,
            0.005,
            basis=mini_dma_mod.HSW_BASIS_LOAD_G,
            current_value=1.0,
        )

        assert speed == pytest.approx((1.0 / 22.7) / 5.0)
    finally:
        _close_test_window(window)


def test_setup_return_zero_uses_strain_floor_for_tiny_residual_load(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(80.0)
    window.spin_setup_return_duration_s.setValue(5.0)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window._calibrated_stiffness_g_per_mm = 100.0
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="seek",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=0.0,
        note="setup_return_zero",
    )

    try:
        speed = window._setup_return_zero_speed_mm_s(mini_dma_mod.HSW_BASIS_LOAD_G, 0.01)

        expected_floor_mm = window.spin_initial_length.value() * 0.001
        assert speed == pytest.approx(expected_floor_mm / window.spin_setup_return_duration_s.value())
    finally:
        _close_test_window(window)


def test_calibration_auto_recovery_uses_setup_return_time_speed(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_setup_return_duration_s.setValue(5.0)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window._automation_name = mini_dma_mod.CALIBRATION
    window._current_position_mm = 7.5
    window._recipe_origin_mm = 8.0
    captured: dict[str, object] = {}

    def _capture_preflight(_steps: object) -> bool:
        captured["steps"] = _steps
        return False

    window._preflight_recipe_hardware = _capture_preflight  # type: ignore[method-assign]

    try:
        window._start_recovery_position_origin()

        steps = captured["steps"]
        assert isinstance(steps, list)
        assert steps[0].action == "move"
        assert steps[0].duration_s == pytest.approx(5.0)
    finally:
        _close_test_window(window)


def test_pending_calibration_auto_recovery_uses_setup_return_time_after_session_stop(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_setup_return_duration_s.setValue(5.0)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window._automation_name = ""
    window._current_position_mm = 7.5
    window._recipe_origin_mm = 8.0
    window._pending_recovery_return_duration_s = 5.0
    captured: dict[str, object] = {}

    def _capture_preflight(_steps: object) -> bool:
        captured["steps"] = _steps
        return False

    window._preflight_recipe_hardware = _capture_preflight  # type: ignore[method-assign]

    try:
        window._start_recovery_position_origin()

        steps = captured["steps"]
        assert isinstance(steps, list)
        assert steps[0].action == "move"
        assert steps[0].duration_s == pytest.approx(5.0)
    finally:
        _close_test_window(window)


def test_manual_recovery_uses_return_time_setting(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_setup_return_duration_s.setValue(4.0)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window._automation_name = ""
    window._current_position_mm = 7.5
    window._position_reference_mm = 7.7
    captured: dict[str, object] = {}

    def _capture_preflight(_steps: object) -> bool:
        captured["steps"] = _steps
        return False

    window._preflight_recipe_hardware = _capture_preflight  # type: ignore[method-assign]

    try:
        window._start_recovery_displacement_zero()

        steps = captured["steps"]
        assert isinstance(steps, list)
        assert steps[0].action == "move"
        assert steps[0].duration_s == pytest.approx(4.0)
    finally:
        _close_test_window(window)


def test_setup_preload_near_target_fallback_uses_minimum_speed(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_diameter.setValue(0.0137)
    window.spin_motion_speed_mm_s.setValue(1.0)
    window.spin_scale_interval.setValue(250)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CALIBRATION
    window._automation_phase = "settle"
    window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
    window._automation_step_note = "setup_preload"
    window._current_position_mm = 0.0
    window._last_move_target_mm = 0.0
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(19.7, 0.0137)
    window._latest_scale_timestamp = time.time()

    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        speed = kwargs.get("speed_mm_s")
        moves.append((target_mm, None if speed is None else float(speed)))
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=0.25,
        )

        assert reached is False
        assert moves == [(pytest.approx(0.01), pytest.approx(0.01))]
    finally:
        _close_test_window(window)


def test_live_speed_summary_reports_equivalent_rates(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_diameter.setValue(0.02)
    window.spin_initial_length.setValue(25.0)
    window._calibrated_stiffness_g_per_mm = 2.0
    window._calibrated_stiffness_length_mm = 25.0
    window._last_commanded_speed_mm_s = 0.5
    window._effective_average_speed_mm_s = 0.5

    try:
        text = window._live_speed_summary_text()

        assert "Average speed: 500 um/s" in text
        assert "Command cap: 0.5 mm/s" in text
        assert "1 g/s" in text
        assert "MPa/s" in text
        assert "2 %/s" in text
    finally:
        _close_test_window(window)


def test_dashboard_speed_reports_effective_average_um_per_s(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._last_commanded_speed_mm_s = 5.0
        window._current_position_mm = 0.0
        window._effective_position_mm = 0.0
        window._reset_effective_linear_speed_sample(now_s=100.0)
        window._current_position_mm = 0.010
        window._effective_position_mm = 0.010

        window._sample_effective_linear_speed(now_s=101.0)
        speed_values = window._live_speed_values()
        window._set_dashboard_value(
            "speed_mm_s",
            window._live_linear_speed_text(speed_values["speed_mm_s"]),
        )

        assert speed_values["speed_mm_s"] == pytest.approx(0.010)
        assert window._dashboard_value_labels["speed_mm_s"].text() == "10 um/s"
        assert "5 mm/s" not in window._dashboard_value_labels["speed_mm_s"].text()
    finally:
        _close_test_window(window)


def test_dashboard_speed_holds_average_between_one_second_samples(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._current_position_mm = 0.0
        window._effective_position_mm = 0.0
        window._reset_effective_linear_speed_sample(now_s=100.0)
        window._current_position_mm = 0.010
        window._effective_position_mm = 0.010
        window._sample_effective_linear_speed(now_s=101.0)
        assert window._effective_average_speed_mm_s == pytest.approx(0.010)

        window._sample_effective_linear_speed(now_s=101.2)

        assert window._effective_average_speed_mm_s == pytest.approx(0.010)
    finally:
        _close_test_window(window)


def test_live_speed_summary_marks_command_cap_as_secondary(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._last_commanded_speed_mm_s = 5.0
        window._effective_average_speed_mm_s = 0.010

        text = window._live_speed_summary_text()

        assert "Average speed: 10 um/s" in text
        assert "Command cap: 5 mm/s" in text
    finally:
        _close_test_window(window)


def test_setup_preload_near_target_uses_single_step_without_backlash_injection(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.767)
    window.spin_backlash_mm.setValue(0.02)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        346.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="settle",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        note="setup_preload",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
    window._setup_preload_engaged_seek_keys.add(seek_key)
    window._seek_last_error_by_key[seek_key] = 1.2
    window._last_move_direction = 1.0
    window._current_position_mm = 6.80
    window._effective_position_mm = 6.80
    window._last_move_target_mm = 6.80
    window._last_effective_move_target_mm = 6.80
    window._latest_scale_timestamp = time.time()
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        20.8,
        window.spin_diameter.value(),
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves
        target_mm, effective_mm = moves[-1]
        assert abs(target_mm - 6.80) <= window._motor_step_mm() + 1e-12
        assert effective_mm == pytest.approx(target_mm)
        assert "backlash take-up" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_reverse_correction_uses_fine_step_instead_of_predictive_backlash(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_steps_per_mm.setValue(1000.0)
    window.spin_initial_length.setValue(20.0)
    window.spin_distribution_nudge_mm.setValue(1.0)
    window.spin_backlash_mm.setValue(0.03)
    window._calibrated_stiffness_g_per_mm = 100.0
    window._calibrated_stiffness_length_mm = 20.0
    window._current_position_mm = 0.1
    window._effective_position_mm = 0.1
    window._last_effective_move_target_mm = 0.1
    window._current_position_steps = 100
    window._last_move_target_mm = 0.1
    window._last_move_direction = -1.0
    window._latest_scale_value_g = 4.0
    window._latest_scale_timestamp = time.time()
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._set_automation_context(
        phase="current",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=5.0,
        plateau_index=1,
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.02,
        )
        _wait_for_tic_commands(window)

        assert reached is False
        assert controller.targets
        assert abs((controller.targets[-1] / 1000.0) - 0.1) < window.spin_backlash_mm.value()
        assert "backlash take-up" not in window.log_output.toPlainText()
        assert "within backlash-limited tolerance" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_overshoot_shrinks_correction_to_target_space_step(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("speed_mm_s")))  # type: ignore[arg-type]
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(59.85)
    window.spin_backlash_mm.setValue(0.0)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        113.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = 9.5
    window._seek_last_value_by_key[seek_key] = 40.5
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 0.3
    window._seek_last_effective_position_by_key[seek_key] = 6.7275
    window._current_position_mm = 6.7275
    window._effective_position_mm = 6.7275
    window._last_move_target_mm = 6.7275
    window._last_effective_move_target_mm = 6.7275
    window._latest_scale_timestamp = time.time()
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        55.8,
        window.spin_diameter.value(),
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves
        correction_mm = abs(moves[-1][0] - 6.7275)
        assert correction_mm == pytest.approx(1.0 / 113.0, abs=0.001 / 113.0)
        assert moves[-1][1] is not None
        assert moves[-1][1] >= 0.05
    finally:
        _close_test_window(window)


def test_current_sweep_large_overshoot_falls_back_to_single_motor_step(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0125)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(46.944)
    window.spin_backlash_mm.setValue(0.0)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        197.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = 14.0
    window._seek_last_value_by_key[seek_key] = 36.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 0.3
    window._seek_last_effective_position_by_key[seek_key] = 6.7275
    window._current_position_mm = 6.7275
    window._effective_position_mm = 6.7275
    window._last_move_target_mm = 6.7275
    window._last_effective_move_target_mm = 6.7275
    window._latest_scale_timestamp = time.time()
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        90.0,
        window.spin_diameter.value(),
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.4,
        )

        assert reached is False
        assert moves
        _target_mm, effective_mm = moves[-1]
        assert effective_mm is not None
        assert abs(effective_mm - 6.7275) == pytest.approx(window._motor_step_mm())
        assert "protective single-step correction" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_ignores_accumulated_correction_travel_limit(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_target_mm")))
        window._current_position_mm = target_mm
        effective = kwargs.get("effective_target_mm")
        if effective is not None:
            window._effective_position_mm = float(effective)
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0125)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(46.944)
    window.spin_current_sweep_max_seek_mm.setValue(0.10)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        197.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = -20.0
    window._seek_last_value_by_key[seek_key] = 70.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 0.3
    window._seek_last_effective_position_by_key[seek_key] = 6.7275
    window._seek_travel_by_key[seek_key] = 0.101
    window._current_position_mm = 6.7275
    window._effective_position_mm = 6.7275
    window._latest_scale_timestamp = time.time()
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        90.0,
        window.spin_diameter.value(),
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.4,
        )

        assert reached is False
        assert window._automation_active is True
        assert moves
        assert "exceeded the correction travel limit" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_ignores_correction_travel_limit(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_target_mm")))
        window._current_position_mm = target_mm
        effective = kwargs.get("effective_target_mm")
        if effective is not None:
            window._effective_position_mm = float(effective)
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0125)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(46.944)
    window.spin_current_sweep_max_seek_mm.setValue(0.10)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        197.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = -20.0
    window._seek_last_value_by_key[seek_key] = 70.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 0.3
    window._seek_last_effective_position_by_key[seek_key] = 6.7275
    window._seek_travel_by_key[seek_key] = 0.101
    window._current_position_mm = 6.7275
    window._effective_position_mm = 6.7275
    window._latest_scale_timestamp = time.time()
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        90.0,
        window.spin_diameter.value(),
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.4,
        )

        assert reached is False
        assert window._automation_active is True
        assert "exceeded the correction travel limit" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_large_error_uses_fast_stage_speed(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.0)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_current_sweep_hold_correction_stress_mpa.setValue(30.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        800.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )

    try:
        speed = window._seek_speed_mm_s(
            -100.0,
            0.4,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            seek_key=window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0),
            current_value=150.0,
        )

        assert speed == pytest.approx(5.0)
    finally:
        _close_test_window(window)


def test_current_sweep_hold_fast_recovery_threshold_stays_at_default_when_hold_cap_is_raised(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.0)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_current_sweep_hold_correction_stress_mpa.setValue(40.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )

    try:
        speed = window._seek_speed_mm_s(
            -35.0,
            0.4,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            seek_key=window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0),
            current_value=85.0,
        )

        assert speed == pytest.approx(5.0)
    finally:
        _close_test_window(window)


def test_current_sweep_hold_large_error_clamps_to_one_tic_when_worsening(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0125)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.0)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_current_sweep_hold_correction_stress_mpa.setValue(30.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        800.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = -80.0
    window._seek_last_value_by_key[seek_key] = 130.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 0.3
    window._seek_last_effective_position_by_key[seek_key] = 0.0
    window._seek_out_of_band_sign_by_key[seek_key] = -1.0
    window._seek_out_of_band_since_by_key[seek_key] = time.time() - 2.0
    window._current_position_mm = 0.0
    window._effective_position_mm = 0.0
    window._last_motion_command_time_s = time.time() - 1.0
    window._last_motion_expected_complete_time_s = time.time() - 0.8
    load_g = mini_dma_mod.load_g_from_stress_mpa(
        150.0,
        window.spin_diameter.value(),
    )
    assert load_g is not None
    now_s = time.time()
    for index in range(5):
        timestamp_s = now_s - 1.2 + index * 0.3
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    window._seek_last_filtered_value_by_key[seek_key] = 150.0
    window._seek_last_scale_timestamp_by_clock[(mini_dma_mod.HSW_BASIS_STRESS_MPA, 1)] = (
        window._latest_scale_timestamp - 0.1
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.4,
        )

        assert reached is False
        assert moves, window.log_output.toPlainText()
        target_mm, effective_mm = moves[-1]
        commanded_mm = abs(target_mm if effective_mm is None else effective_mm)
        assert commanded_mm == pytest.approx(window._motor_step_mm())
    finally:
        _close_test_window(window)


def test_current_sweep_hold_unstable_response_disables_fast_recovery_speed(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.0)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_current_sweep_hold_correction_stress_mpa.setValue(30.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        800.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)

    try:
        normal_speed = window._seek_speed_mm_s(
            -100.0,
            0.4,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            seek_key=seek_key,
            current_value=150.0,
        )
        for _ in range(mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_UNSTABLE_LEVEL):
            window._note_current_sweep_hold_instability(seek_key)

        damped_speed = window._seek_speed_mm_s(
            -100.0,
            0.4,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            seek_key=seek_key,
            current_value=150.0,
        )

        assert normal_speed == pytest.approx(5.0)
        assert damped_speed < normal_speed
    finally:
        _close_test_window(window)


def test_current_sweep_hold_quiet_response_keeps_normal_post_move_sample_gate(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = -38.0
    window._seek_filtered_control_signal = (  # type: ignore[method-assign]
        lambda _basis: mini_dma_mod.ScaleControlSignal(
            value=90.0,
            latest_value=90.0,
            noise=0.2,
            slope_per_s=0.5,
            sample_count=7,
            timestamp_s=time.time(),
        )
    )

    try:
        required_samples = window._seek_required_post_move_samples(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            -40.0,
            0.4,
            seek_key=seek_key,
        )

        assert required_samples == 1
    finally:
        _close_test_window(window)


def test_current_sweep_hold_volatile_response_waits_before_compounding_move(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []
    trace_rows: list[dict[str, object]] = []
    now_s = time.time()

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        return True

    def _capture_trace(**kwargs: object) -> None:
        trace_rows.append(dict(kwargs))

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window._write_control_trace = _capture_trace  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        300.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = -8.0
    window._seek_last_value_by_key[seek_key] = 58.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 0.3
    window._seek_last_filtered_value_by_key[seek_key] = 58.0
    window._seek_last_effective_position_by_key[seek_key] = 0.0
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = now_s - 0.5
    window._seek_post_move_sample_count_by_key[seek_key] = 0
    window._seek_out_of_band_sign_by_key[seek_key] = -1.0
    window._seek_out_of_band_since_by_key[seek_key] = now_s - 2.0
    window._current_position_mm = 0.01
    window._effective_position_mm = 0.01
    window._last_move_target_mm = 0.01
    window._last_effective_move_target_mm = 0.01
    window._last_motion_command_time_s = now_s - 0.8
    window._last_motion_expected_complete_time_s = now_s - 0.7
    load_g = mini_dma_mod.load_g_from_stress_mpa(110.0, window.spin_diameter.value())
    assert load_g is not None
    for index in range(5):
        timestamp_s = now_s - 0.4 + index * 0.1
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.4,
        )

        assert reached is False
        assert not moves
        assert trace_rows[-1]["reason"] == "volatile_post_move_response"
        assert trace_rows[-1]["required_fresh_samples"] == (
            mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_VOLATILE_EXTRA_SAMPLES
        )
    finally:
        _close_test_window(window)


def test_current_sweep_hold_volatile_response_keeps_waiting_while_still_rising(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []
    trace_rows: list[dict[str, object]] = []
    now_s = time.time()

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        return True

    def _capture_trace(**kwargs: object) -> None:
        trace_rows.append(dict(kwargs))

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window._write_control_trace = _capture_trace  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0151)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        300.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = -61.0
    window._seek_last_value_by_key[seek_key] = 111.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 1.0
    window._seek_last_filtered_value_by_key[seek_key] = 111.0
    window._seek_last_effective_position_by_key[seek_key] = 0.05
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = now_s - 1.0
    window._seek_post_move_sample_count_by_key[seek_key] = (
        mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_VOLATILE_EXTRA_SAMPLES
    )
    window._seek_out_of_band_sign_by_key[seek_key] = -1.0
    window._seek_out_of_band_since_by_key[seek_key] = now_s - 2.0
    window._current_position_mm = 0.07
    window._effective_position_mm = 0.07
    window._last_move_target_mm = 0.07
    window._last_effective_move_target_mm = 0.07
    window._last_motion_command_time_s = now_s - 1.5
    window._last_motion_expected_complete_time_s = now_s - 1.4
    for index, stress_mpa in enumerate([187.0, 218.0, 242.0, 271.0, 275.0]):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress_mpa, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s - 1.0 + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    window._seek_filtered_control_signal = (  # type: ignore[method-assign]
        lambda _basis: mini_dma_mod.ScaleControlSignal(
            value=242.0,
            latest_value=275.0,
            noise=0.5,
            slope_per_s=68.0,
            sample_count=5,
            timestamp_s=now_s,
        )
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.4,
        )

        assert reached is False
        assert not moves
        assert trace_rows[-1]["reason"] == "volatile_response_unsettled"
    finally:
        _close_test_window(window)


def test_kern_current_sweep_hold_runaway_drift_bypasses_volatile_wait(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []
    trace_rows: list[dict[str, object]] = []
    now_s = time.time()

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        return True

    def _capture_trace(**kwargs: object) -> None:
        trace_rows.append(dict(kwargs))

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window._write_control_trace = _capture_trace  # type: ignore[method-assign]
    window.combo_scale_baud.setCurrentText("256000")
    window.edit_scale_request.setText(mini_dma_mod.KERN_KCP_SCALE_REQUEST)
    window.edit_scale_terminator.setText(mini_dma_mod.KERN_KCP_SCALE_TERMINATOR)
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0182)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(37.0)
    window.spin_backlash_mm.setValue(0.0)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_current_sweep_hold_correction_stress_mpa.setValue(30.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        300.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = -20.0
    window._seek_last_value_by_key[seek_key] = 70.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 1.0
    window._seek_last_filtered_value_by_key[seek_key] = 70.0
    window._seek_last_effective_position_by_key[seek_key] = 0.05
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = now_s - 1.0
    window._seek_post_move_sample_count_by_key[seek_key] = (
        mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_VOLATILE_EXTRA_SAMPLES
    )
    window._seek_out_of_band_sign_by_key[seek_key] = -1.0
    window._seek_out_of_band_since_by_key[seek_key] = now_s - 2.0
    window._current_position_mm = 0.07
    window._effective_position_mm = 0.07
    window._last_move_target_mm = 0.07
    window._last_effective_move_target_mm = 0.07
    window._last_motion_command_time_s = now_s - 1.5
    window._last_motion_expected_complete_time_s = now_s - 1.4
    for index, stress_mpa in enumerate([70.0, 72.0, 74.0, 76.0, 78.0]):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress_mpa, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s - 1.0 + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    window._seek_filtered_control_signal = (  # type: ignore[method-assign]
        lambda _basis: mini_dma_mod.ScaleControlSignal(
            value=74.0,
            latest_value=78.0,
            noise=0.5,
            slope_per_s=4.0,
            sample_count=5,
            timestamp_s=now_s,
        )
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.4,
        )

        assert reached is False
        assert moves, trace_rows
        assert "current_hold_drift_recovery" in str(trace_rows[-1]["reason"])
        commanded_mm = abs(float(trace_rows[-1]["correction_mm"]))
        assert commanded_mm > window._motor_step_mm()
    finally:
        _close_test_window(window)


def test_kern_current_sweep_hold_high_noise_runaway_escapes_single_step(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []
    trace_rows: list[dict[str, object]] = []
    now_s = time.time()

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        return True

    def _capture_trace(**kwargs: object) -> None:
        trace_rows.append(dict(kwargs))

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window._write_control_trace = _capture_trace  # type: ignore[method-assign]
    window.combo_scale_baud.setCurrentText("256000")
    window.edit_scale_request.setText(mini_dma_mod.KERN_KCP_SCALE_REQUEST)
    window.edit_scale_terminator.setText(mini_dma_mod.KERN_KCP_SCALE_TERMINATOR)
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0182)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(37.0)
    window.spin_backlash_mm.setValue(0.0)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_current_sweep_hold_correction_stress_mpa.setValue(30.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        300.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = 18.0
    window._seek_last_value_by_key[seek_key] = 32.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 0.4
    window._seek_last_filtered_value_by_key[seek_key] = 35.0
    window._seek_last_effective_position_by_key[seek_key] = 0.02
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = now_s - 1.0
    window._seek_post_move_sample_count_by_key[seek_key] = (
        mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_VOLATILE_EXTRA_SAMPLES
    )
    window._seek_out_of_band_sign_by_key[seek_key] = 1.0
    window._seek_out_of_band_since_by_key[seek_key] = now_s - 2.0
    for _ in range(mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_UNSTABLE_LEVEL):
        window._note_current_sweep_hold_instability(seek_key)
    window._current_position_mm = 0.04
    window._effective_position_mm = 0.04
    window._last_move_target_mm = 0.04
    window._last_effective_move_target_mm = 0.04
    window._last_motion_command_time_s = now_s - 1.5
    window._last_motion_expected_complete_time_s = now_s - 1.4
    for index, stress_mpa in enumerate([34.0, 32.0, 30.0, 29.0, 28.0]):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress_mpa, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s - 1.0 + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    window._seek_filtered_control_signal = (  # type: ignore[method-assign]
        lambda _basis: mini_dma_mod.ScaleControlSignal(
            value=29.0,
            latest_value=28.0,
            noise=9.0,
            slope_per_s=-14.0,
            sample_count=30,
            timestamp_s=now_s,
        )
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.4,
        )

        assert reached is False
        assert moves, trace_rows
        assert "current_hold_unstable_drift_recovery" in str(trace_rows[-1]["reason"])
        commanded_mm = abs(float(trace_rows[-1]["correction_mm"]))
        assert commanded_mm > window._motor_step_mm()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_volatile_response_can_resume_after_turning_back(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = time.time()

    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0151)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        300.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = -61.0
    window._seek_last_value_by_key[seek_key] = 111.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 1.0
    window._seek_last_filtered_value_by_key[seek_key] = 111.0
    window._seek_last_effective_position_by_key[seek_key] = 0.05
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = now_s - 1.0
    window._seek_post_move_sample_count_by_key[seek_key] = (
        mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_VOLATILE_EXTRA_SAMPLES
    )
    window._seek_out_of_band_sign_by_key[seek_key] = -1.0
    window._seek_out_of_band_since_by_key[seek_key] = now_s - 2.0
    window._current_position_mm = 0.07
    window._effective_position_mm = 0.07
    window._last_move_target_mm = 0.07
    window._last_effective_move_target_mm = 0.07
    window._last_motion_command_time_s = now_s - 1.5
    window._last_motion_expected_complete_time_s = now_s - 1.4
    for index, stress_mpa in enumerate([275.0, 242.0, 218.0, 187.0, 160.0]):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress_mpa, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s - 1.0 + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    filtered_signal = mini_dma_mod.ScaleControlSignal(
        value=218.0,
        latest_value=160.0,
        noise=0.5,
        slope_per_s=-68.0,
        sample_count=5,
        timestamp_s=now_s,
    )

    try:
        assert window._current_sweep_hold_volatile_response_active(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            -168.0,
            0.4,
            filtered_signal,
            seek_key=seek_key,
        ) is True
        assert window._current_sweep_hold_volatile_response_unsettled(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            -168.0,
            0.4,
            filtered_signal,
            seek_key=seek_key,
        ) is False
    finally:
        _close_test_window(window)


def test_current_sweep_hold_unstable_response_clamps_large_error_to_single_step(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0125)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.0)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_current_sweep_hold_correction_stress_mpa.setValue(30.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        800.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = -80.0
    window._seek_last_value_by_key[seek_key] = 130.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 0.3
    window._seek_last_effective_position_by_key[seek_key] = 0.0
    window._seek_out_of_band_sign_by_key[seek_key] = -1.0
    window._seek_out_of_band_since_by_key[seek_key] = time.time() - 2.0
    for _ in range(mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_UNSTABLE_LEVEL):
        window._note_current_sweep_hold_instability(seek_key)
    window._current_position_mm = 0.0
    window._effective_position_mm = 0.0
    window._last_motion_command_time_s = time.time() - 1.0
    window._last_motion_expected_complete_time_s = time.time() - 0.8
    load_g = mini_dma_mod.load_g_from_stress_mpa(
        150.0,
        window.spin_diameter.value(),
    )
    assert load_g is not None
    now_s = time.time()
    for index in range(5):
        timestamp_s = now_s - 1.2 + index * 0.3
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    window._seek_last_filtered_value_by_key[seek_key] = 130.0
    window._seek_last_scale_timestamp_by_clock[(mini_dma_mod.HSW_BASIS_STRESS_MPA, 1)] = (
        window._latest_scale_timestamp - 0.1
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.4,
        )

        assert reached is False
        assert moves, window.log_output.toPlainText()
        target_mm, effective_mm = moves[-1]
        commanded_mm = abs(target_mm if effective_mm is None else effective_mm)
        assert commanded_mm == pytest.approx(window._motor_step_mm())
        assert "unstable" in window.log_output.toPlainText().lower()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_unstable_response_decays_after_stable_samples(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)

    try:
        for _ in range(mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_UNSTABLE_LEVEL):
            window._note_current_sweep_hold_instability(seek_key)
        assert window._current_sweep_hold_unstable_response_active(seek_key) is True

        for _ in range(mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_UNSTABLE_STABLE_SAMPLES):
            window._note_current_sweep_hold_stable_response(seek_key)

        assert window._current_sweep_hold_unstable_response_active(seek_key) is False
    finally:
        _close_test_window(window)


def test_current_sweep_hold_moving_away_uses_dynamic_recovery_when_worsening(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0125)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(82.567)
    window.spin_backlash_mm.setValue(0.0)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_current_sweep_hold_correction_stress_mpa.setValue(40.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        690.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = -12.0
    window._seek_last_value_by_key[seek_key] = 62.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 0.3
    window._seek_last_effective_position_by_key[seek_key] = 0.0
    window._seek_out_of_band_sign_by_key[seek_key] = -1.0
    window._seek_out_of_band_since_by_key[seek_key] = time.time() - 2.0
    window._current_position_mm = 0.0
    window._effective_position_mm = 0.0
    window._last_motion_command_time_s = time.time() - 1.0
    window._last_motion_expected_complete_time_s = time.time() - 0.8
    now_s = time.time()
    for index, stress in enumerate([62.0, 64.0, 66.0, 68.0, 70.0]):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s - 1.0 + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    window._seek_last_filtered_value_by_key[seek_key] = 62.0
    window._seek_last_scale_timestamp_by_clock[(mini_dma_mod.HSW_BASIS_STRESS_MPA, 1)] = (
        window._latest_scale_timestamp - 0.1
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves, window.log_output.toPlainText()
        target_mm, effective_mm = moves[-1]
        commanded_mm = abs(target_mm if effective_mm is None else effective_mm)
        assert commanded_mm > window._motor_step_mm() * 5.0
        assert commanded_mm < 0.05
        assert "drifting away from target" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_reversal_uses_correction_step_without_predictive_backlash(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append(
            (
                target_mm,
                kwargs.get("effective_position_mm"),  # type: ignore[arg-type]
                kwargs.get("speed_mm_s"),  # type: ignore[arg-type]
            )
        )
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(59.85)
    window.spin_backlash_mm.setValue(0.02)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        113.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = 9.5
    window._seek_last_value_by_key[seek_key] = 40.5
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 0.3
    window._seek_last_effective_position_by_key[seek_key] = 6.7275
    window._current_position_mm = 6.7275
    window._effective_position_mm = 6.7275
    window._last_move_target_mm = 6.7275
    window._last_effective_move_target_mm = 6.7275
    window._last_move_direction = 1.0
    window._latest_scale_timestamp = time.time()
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        55.8,
        window.spin_diameter.value(),
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves
        target_mm, effective_mm, speed_mm_s = moves[-1]
        correction_mm = abs(target_mm - 6.7275)
        assert correction_mm == pytest.approx(1.0 / 113.0, abs=0.001 / 113.0)
        assert effective_mm == pytest.approx(target_mm)
        assert speed_mm_s is not None
        assert speed_mm_s >= 0.05
        assert "backlash take-up" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_near_target_reversal_does_not_send_backlash_only_move(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0137)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.767)
    window.spin_backlash_mm.setValue(0.02)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        305.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = 3.0
    window._last_move_direction = 1.0
    window._current_position_mm = 6.94
    window._effective_position_mm = 6.94
    window._last_move_target_mm = 6.94
    window._last_effective_move_target_mm = 6.94
    window._latest_scale_timestamp = time.time()
    load_g = mini_dma_mod.load_g_from_stress_mpa(62.0, window.spin_diameter.value())
    assert load_g is not None
    for index in range(5):
        timestamp_s = time.time() - 1.2 + index * 0.3
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    window._seek_out_of_band_sign_by_key[seek_key] = -1.0
    window._seek_out_of_band_since_by_key[seek_key] = time.time() - 2.0
    window._seek_pending_reversal_by_key[seek_key] = (-1.0, window._latest_scale_timestamp - 1.0)

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves
        target_mm, effective_mm = moves[-1]
        assert abs(target_mm - 6.94) < float(window.spin_backlash_mm.value())
        assert effective_mm == pytest.approx(target_mm)
        assert "backlash take-up" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_reversal_does_not_preadd_backlash_to_near_target_correction(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.767)
    window.spin_backlash_mm.setValue(0.02)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        346.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="settle",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
    window._seek_last_error_by_key[seek_key] = 1.2
    window._last_move_direction = 1.0
    window._current_position_mm = 6.80
    window._effective_position_mm = 6.80
    window._last_move_target_mm = 6.80
    window._last_effective_move_target_mm = 6.80
    window._latest_scale_timestamp = time.time()
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        20.8,
        window.spin_diameter.value(),
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves
        target_mm, effective_mm = moves[-1]
        assert abs(target_mm - 6.80) <= window._motor_step_mm() + 1e-12
        assert effective_mm == pytest.approx(target_mm)
        assert "backlash take-up" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_uses_gated_small_stress_correction(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append(
            (
                target_mm,
                kwargs.get("effective_position_mm"),  # type: ignore[arg-type]
                kwargs.get("speed_mm_s"),  # type: ignore[arg-type]
            )
        )
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.767)
    window.spin_backlash_mm.setValue(0.0)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        224.502066,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_error_by_key[seek_key] = 12.0
    window._seek_last_value_by_key[seek_key] = 38.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 0.3
    window._seek_last_effective_position_by_key[seek_key] = 6.7
    window._seek_out_of_band_sign_by_key[seek_key] = 1.0
    window._seek_out_of_band_since_by_key[seek_key] = time.time() - 2.0
    window._current_position_mm = 6.7
    window._effective_position_mm = 6.7
    window._last_move_target_mm = 6.7
    window._last_effective_move_target_mm = 6.7
    now_s = time.time()
    load_g = mini_dma_mod.load_g_from_stress_mpa(38.0, window.spin_diameter.value())
    assert load_g is not None
    for index in range(5):
        timestamp_s = now_s - 1.2 + index * 0.3
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        assert window._seek_supports_cruise_feedback(mini_dma_mod.HSW_BASIS_STRESS_MPA) is False

        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves
        _target_mm, effective_mm, _speed_mm_s = moves[-1]
        assert effective_mm is not None
        correction_mm = abs(effective_mm - 6.7)
        assert correction_mm <= (5.0 / 224.502066) + 1e-9
    finally:
        _close_test_window(window)


def test_current_sweep_hold_improving_recovery_can_grow_above_one_tic(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []
    trace_rows: list[dict[str, object]] = []
    now_s = time.time()

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        return True

    def _capture_trace(**kwargs: object) -> None:
        trace_rows.append(dict(kwargs))

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window._write_control_trace = _capture_trace  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        20000.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    previous_position_mm = 6.70000
    current_position_mm = previous_position_mm + window._motor_step_mm()
    window._current_position_mm = current_position_mm
    window._effective_position_mm = current_position_mm
    window._last_move_target_mm = current_position_mm
    window._last_effective_move_target_mm = current_position_mm
    window._last_motion_command_time_s = now_s - 2.0
    window._last_motion_expected_complete_time_s = now_s - 2.0
    window._seek_last_error_by_key[seek_key] = 20.0
    window._seek_last_value_by_key[seek_key] = 30.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 1.0
    window._seek_last_filtered_value_by_key[seek_key] = 30.0
    window._seek_last_effective_position_by_key[seek_key] = previous_position_mm
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = now_s - 1.0
    window._seek_post_move_sample_count_by_key[seek_key] = 2
    load_g = mini_dma_mod.load_g_from_stress_mpa(32.0, window.spin_diameter.value())
    assert load_g is not None
    for index in range(7):
        timestamp_s = now_s - 1.2 + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves
        _target_mm, effective_mm = moves[-1]
        assert effective_mm is not None
        correction_mm = abs(float(effective_mm) - current_position_mm)
        assert correction_mm > window._motor_step_mm() * 1.25
        assert correction_mm == pytest.approx(window._motor_step_mm() * 1.6)
        assert trace_rows[-1]["reason"] == "gated;current_hold_improving_recovery"
    finally:
        _close_test_window(window)


def test_current_sweep_hold_unstable_but_improving_recovery_can_escape_one_tic(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []
    trace_rows: list[dict[str, object]] = []
    now_s = time.time()

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        return True

    def _capture_trace(**kwargs: object) -> None:
        trace_rows.append(dict(kwargs))

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window._write_control_trace = _capture_trace  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        20000.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    for _ in range(mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_UNSTABLE_LEVEL):
        window._note_current_sweep_hold_instability(seek_key)
    assert window._current_sweep_hold_unstable_response_active(seek_key) is True

    previous_position_mm = 6.70000
    current_position_mm = previous_position_mm + window._motor_step_mm()
    window._current_position_mm = current_position_mm
    window._effective_position_mm = current_position_mm
    window._last_move_target_mm = current_position_mm
    window._last_effective_move_target_mm = current_position_mm
    window._last_motion_command_time_s = now_s - 2.0
    window._last_motion_expected_complete_time_s = now_s - 2.0
    window._seek_last_error_by_key[seek_key] = 20.0
    window._seek_last_value_by_key[seek_key] = 30.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 1.0
    window._seek_last_filtered_value_by_key[seek_key] = 30.0
    window._seek_last_effective_position_by_key[seek_key] = previous_position_mm
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = now_s - 1.0
    window._seek_post_move_sample_count_by_key[seek_key] = 2
    load_g = mini_dma_mod.load_g_from_stress_mpa(32.0, window.spin_diameter.value())
    assert load_g is not None
    for index in range(7):
        timestamp_s = now_s - 1.2 + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves
        _target_mm, effective_mm = moves[-1]
        assert effective_mm is not None
        correction_mm = abs(float(effective_mm) - current_position_mm)
        assert correction_mm > window._motor_step_mm() * 1.25
        assert correction_mm == pytest.approx(window._motor_step_mm() * 1.6)
        assert trace_rows[-1]["reason"] == "gated;current_hold_unstable_improving_recovery"
        assert "cautiously widening" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_volatile_containment_clamps_improving_recovery(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []
    trace_rows: list[dict[str, object]] = []
    now_s = time.time()

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        return True

    def _capture_trace(**kwargs: object) -> None:
        trace_rows.append(dict(kwargs))

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window._write_control_trace = _capture_trace  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0151)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        20000.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    for _ in range(mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_UNSTABLE_LEVEL):
        window._note_current_sweep_hold_instability(seek_key)
    assert window._current_sweep_hold_unstable_response_active(seek_key) is True

    previous_position_mm = 6.70000
    current_position_mm = previous_position_mm + window._motor_step_mm()
    window._current_position_mm = current_position_mm
    window._effective_position_mm = current_position_mm
    window._last_move_target_mm = current_position_mm
    window._last_effective_move_target_mm = current_position_mm
    window._last_motion_command_time_s = now_s - 2.0
    window._last_motion_expected_complete_time_s = now_s - 2.0
    window._seek_last_error_by_key[seek_key] = -220.0
    window._seek_last_value_by_key[seek_key] = 270.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 1.0
    window._seek_last_filtered_value_by_key[seek_key] = 270.0
    window._seek_last_effective_position_by_key[seek_key] = previous_position_mm
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = now_s - 1.0
    window._seek_post_move_sample_count_by_key[seek_key] = 2
    load_g = mini_dma_mod.load_g_from_stress_mpa(180.0, window.spin_diameter.value())
    assert load_g is not None
    for index in range(7):
        timestamp_s = now_s - 1.2 + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves
        _target_mm, effective_mm = moves[-1]
        assert effective_mm is not None
        correction_mm = abs(float(effective_mm) - current_position_mm)
        assert correction_mm == pytest.approx(window._motor_step_mm())
        assert trace_rows[-1]["reason"] == "gated;current_hold_volatile_capped"
        assert "capping the next correction" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_same_sign_drift_uses_dynamic_recovery(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []
    trace_rows: list[dict[str, object]] = []
    now_s = time.time()

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() - 0.1
        return True

    def _capture_trace(**kwargs: object) -> None:
        trace_rows.append(dict(kwargs))

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window._write_control_trace = _capture_trace  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        300.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    for _ in range(mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_UNSTABLE_LEVEL):
        window._note_current_sweep_hold_instability(seek_key)
    previous_position_mm = 6.70000
    current_position_mm = previous_position_mm + window._motor_step_mm()
    window._current_position_mm = current_position_mm
    window._effective_position_mm = current_position_mm
    window._last_move_target_mm = current_position_mm
    window._last_effective_move_target_mm = current_position_mm
    window._last_motion_command_time_s = now_s - 2.0
    window._last_motion_expected_complete_time_s = now_s - 2.0
    window._seek_last_error_by_key[seek_key] = -10.0
    window._seek_last_value_by_key[seek_key] = 60.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 1.0
    window._seek_last_filtered_value_by_key[seek_key] = 60.0
    window._seek_last_effective_position_by_key[seek_key] = previous_position_mm
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = now_s - 1.0
    window._seek_post_move_sample_count_by_key[seek_key] = 2
    for index, stress_mpa in enumerate([61.0, 63.0, 65.0, 67.0, 69.0, 71.0, 73.0]):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress_mpa, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s - 1.5 + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    drift_signal = mini_dma_mod.ScaleControlSignal(
        value=67.0,
        latest_value=73.0,
        noise=0.1,
        slope_per_s=8.0,
        sample_count=7,
        timestamp_s=now_s,
    )
    drift_step_mm = window._current_sweep_hold_drift_recovery_step_mm(
        mini_dma_mod.HSW_BASIS_STRESS_MPA,
        -10.0,
        -17.0,
        0.171,
        window._motor_step_mm(),
        drift_signal,
        seek_key=seek_key,
    )
    assert drift_step_mm is not None
    assert drift_step_mm > window._motor_step_mm() * 10.0
    window._current_sweep_hold_drift_recovery_step_mm = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: drift_step_mm
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves
        _target_mm, effective_mm = moves[-1]
        assert effective_mm is not None
        correction_mm = abs(float(effective_mm) - current_position_mm)
        assert correction_mm > window._motor_step_mm() * 10.0
        assert trace_rows[-1]["reason"] == "gated;current_hold_unstable_drift_recovery"
        assert "drifting away from target" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_drift_recovery_scales_down_for_stiff_response(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = time.time()

    window.spin_steps_per_mm.setValue(800.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    drift_signal = mini_dma_mod.ScaleControlSignal(
        value=67.0,
        latest_value=73.0,
        noise=0.1,
        slope_per_s=8.0,
        sample_count=7,
        timestamp_s=now_s,
    )

    def drift_step_for_sensitivity(sensitivity_per_mm: float) -> float | None:
        window._basis_sensitivity_per_mm = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: sensitivity_per_mm
        )
        return window._current_sweep_hold_drift_recovery_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            -10.0,
            -17.0,
            0.171,
            window._motor_step_mm(),
            drift_signal,
            seek_key=seek_key,
        )

    try:
        soft_step_mm = drift_step_for_sensitivity(300.0)
        stiff_step_mm = drift_step_for_sensitivity(3000.0)

        assert soft_step_mm is not None
        assert stiff_step_mm is not None
        assert soft_step_mm > window._motor_step_mm() * 10.0
        assert stiff_step_mm < soft_step_mm * 0.2
        assert stiff_step_mm < 0.005
        assert stiff_step_mm > window._motor_step_mm() * 1.25
    finally:
        _close_test_window(window)


def test_offline_stiff_sample_guard_writes_reproducible_result(
    tmp_path: Path,
    qtbot,
) -> None:
    plan = {
        "schema_version": 1,
        "kind": "mini_dma_offline_stiff_sample_guard",
        "drift_condition": {
            "basis": mini_dma_mod.HSW_BASIS_STRESS_MPA,
            "target_mpa": 50.0,
            "previous_error_mpa": -10.0,
            "current_error_mpa": -17.0,
            "tolerance_mpa": 0.171,
            "filtered_slope_mpa_per_s": 8.0,
            "filtered_noise_mpa": 0.1,
            "filtered_sample_count": 7,
        },
        "acceptance": {
            "stiff_10x_max_step_mm": 0.005,
            "stiff_10x_max_fraction_of_soft_step": 0.2,
            "stiff_50x_max_step_mm": 0.002,
        },
        "sensitivity_cases": [
            {"id": "soft_reference", "sensitivity_mpa_per_mm": 300.0},
            {"id": "stiff_10x", "sensitivity_mpa_per_mm": 3000.0},
            {"id": "stiff_50x", "sensitivity_mpa_per_mm": 15000.0},
        ],
        "historical_oscillation_cases": [
            {
                "id": "historical_reversal",
                "source_run": "synthetic historical reversal",
                "sample": "oscillatory stiff sample",
                "basis": mini_dma_mod.HSW_BASIS_STRESS_MPA,
                "target_mpa": 50.0,
                "previous_error_mpa": -5.27133701,
                "current_error_mpa": 4.39658663,
                "tolerance_mpa": 0.182413653,
                "filtered_slope_mpa_per_s": 0.0,
                "filtered_noise_mpa": 0.1,
                "filtered_sample_count": 7,
                "sensitivity_mpa_per_mm": 1590.24028,
                "expected": "decline_dynamic_escape",
            }
        ],
    }
    plan_path = tmp_path / "stiff-guard.json"
    out_json = tmp_path / "stiff-result.json"
    out_md = tmp_path / "stiff-result.md"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def _window_factory(**_kwargs: object) -> mini_dma_mod.MainWindow:
        window = _build_window(tmp_path, qtbot)
        window.spin_steps_per_mm.setValue(800.0)
        return window

    result = stiff_guard_mod.run_guard(
        plan_path,
        out_json=out_json,
        out_markdown=out_md,
        repo_root=Path.cwd(),
        window_factory=_window_factory,
    )

    assert result["passed"] is True
    by_id = {row["id"]: row for row in result["results"]}
    assert by_id["soft_reference"]["dynamic_step_mm"] == pytest.approx(0.032485009728586)
    assert by_id["stiff_10x"]["dynamic_step_mm"] == pytest.approx(0.0032485009728586)
    assert by_id["stiff_50x"]["dynamic_step_mm"] is None
    historical_by_id = {row["id"]: row for row in result["historical_oscillation_results"]}
    assert historical_by_id["historical_reversal"]["dynamic_step_mm"] is None
    assert result["historical_checks"][0]["passed"] is True
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["passed"] is True
    markdown = out_md.read_text(encoding="utf-8")
    assert "stiff_10x" in markdown
    assert "Historical Oscillation Cases" in markdown


def test_current_sweep_hold_worsening_recovery_clamps_back_to_one_tic(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, float | None]] = []
    trace_rows: list[dict[str, object]] = []
    now_s = time.time()

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append((target_mm, kwargs.get("effective_position_mm")))  # type: ignore[arg-type]
        return True

    def _capture_trace(**kwargs: object) -> None:
        trace_rows.append(dict(kwargs))

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window._write_control_trace = _capture_trace  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        300.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    previous_position_mm = 6.70000
    current_position_mm = previous_position_mm + window._motor_step_mm() * 4.0
    window._current_position_mm = current_position_mm
    window._effective_position_mm = current_position_mm
    window._last_move_target_mm = current_position_mm
    window._last_effective_move_target_mm = current_position_mm
    window._last_motion_command_time_s = now_s - 2.0
    window._last_motion_expected_complete_time_s = now_s - 2.0
    window._seek_last_error_by_key[seek_key] = 10.0
    window._seek_last_value_by_key[seek_key] = 40.0
    window._seek_last_time_by_key[seek_key] = time.monotonic() - 1.0
    window._seek_last_filtered_value_by_key[seek_key] = 40.0
    window._seek_last_effective_position_by_key[seek_key] = previous_position_mm
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = now_s - 1.0
    load_g = mini_dma_mod.load_g_from_stress_mpa(37.0, window.spin_diameter.value())
    assert load_g is not None
    for index in range(7):
        timestamp_s = now_s - 1.2 + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves
        _target_mm, effective_mm = moves[-1]
        assert effective_mm is not None
        correction_mm = abs(float(effective_mm) - current_position_mm)
        assert correction_mm == pytest.approx(window._motor_step_mm())
        assert trace_rows[-1]["reason"] == "gated;current_hold_worsened_single_step"
    finally:
        _close_test_window(window)


def test_current_sweep_hold_instability_shrinks_trust_region_after_prediction_failures(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(42.461)
    window.spin_current_sweep_hold_correction_stress_mpa.setValue(30.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=250.0,
        plateau_index=5,
        note="5",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 250.0)

    try:
        normal_mm = window._current_sweep_max_stress_correction_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            332.67878,
            error_value=165.322519,
            seek_key=seek_key,
        )
        assert normal_mm is not None

        for _ in range(3):
            window._note_current_sweep_hold_instability(seek_key)

        stabilized_mm = window._current_sweep_max_stress_correction_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            332.67878,
            error_value=165.322519,
            seek_key=seek_key,
        )

        assert stabilized_mm is not None
        assert stabilized_mm < normal_mm * 0.2
        assert stabilized_mm * 332.67878 <= 6.0
    finally:
        _close_test_window(window)


def test_current_sweep_hold_waits_for_filtered_signal_to_change_before_repeating_correction(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.time()

    def _capture_move(target_mm: float, **_kwargs: object) -> bool:
        moves.append(float(target_mm))
        command_s = time.time() - 0.4
        window._last_motion_command_time_s = command_s
        window._last_motion_expected_complete_time_s = command_s
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        500.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_out_of_band_sign_by_key[seek_key] = 1.0
    window._seek_out_of_band_since_by_key[seek_key] = now_s - 2.0
    for index in range(5):
        load_g = mini_dma_mod.load_g_from_stress_mpa(35.0, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )
        assert reached is False
        assert len(moves) == 1

        load_g = mini_dma_mod.load_g_from_stress_mpa(35.0, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s + 1.50
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert len(moves) == 1
        assert "filtered control signal" in window.log_output.toPlainText().lower()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_retries_when_filtered_signal_stays_unchanged_for_full_window(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.time()

    def _capture_move(target_mm: float, **_kwargs: object) -> bool:
        moves.append(float(target_mm))
        command_s = time.time() - 0.4
        window._last_motion_command_time_s = command_s
        window._last_motion_expected_complete_time_s = command_s
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window.spin_current_sweep_hold_filter_window_s.setValue(1.8)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        500.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_out_of_band_sign_by_key[seek_key] = 1.0
    window._seek_out_of_band_since_by_key[seek_key] = now_s - 2.0
    load_g = mini_dma_mod.load_g_from_stress_mpa(44.0, window.spin_diameter.value())
    assert load_g is not None
    for index in range(5):
        timestamp_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )
        assert reached is False
        assert len(moves) == 1

        timestamp_s = now_s + 3.0
        for index in range(8):
            sample_s = timestamp_s - 1.75 + index * 0.25
            window._scale_signal_buffer.add_sample(
                timestamp_s=sample_s,
                raw_g=load_g,
                applied_load_g=load_g,
                raw_text=f"{load_g:.5f} g",
            )
            window._latest_scale_timestamp = sample_s
            window._latest_scale_value_g = load_g

        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert len(moves) == 2
        assert "filtered control signal" not in window.log_output.toPlainText().lower()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_requires_persistent_out_of_band_error_before_correction(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.time()
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(float(target_mm)) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        500.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    load_g = mini_dma_mod.load_g_from_stress_mpa(44.0, window.spin_diameter.value())
    assert load_g is not None
    for index in range(5):
        timestamp_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves == []
        assert "persist" in window.log_output.toPlainText().lower()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_large_error_bypasses_persistent_gate(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.time()
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(float(target_mm)) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        500.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    load_g = mini_dma_mod.load_g_from_stress_mpa(35.0, window.spin_diameter.value())
    assert load_g is not None
    for index in range(5):
        timestamp_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert len(moves) == 1
        assert "persist" not in window.log_output.toPlainText().lower()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_noisy_large_error_bypasses_persistent_gate(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.time()
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(float(target_mm)) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        500.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    for index, stress in enumerate([55.0, 95.0, 55.0, 95.0, 75.0, 75.0, 75.0]):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert len(moves) == 1
        assert "persist" not in window.log_output.toPlainText().lower()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_moving_away_bypasses_persistent_gate(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.time()
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(float(target_mm)) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0125)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        690.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    for index, stress in enumerate([60.0, 64.0, 68.0, 72.0, 76.0]):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is False
        assert len(moves) == 1
        assert "persist" not in window.log_output.toPlainText().lower()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_accepts_small_filtered_fluctuation_around_target_without_correction(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.time()
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(float(target_mm)) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        500.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    for index in range(8):
        stress = 49.0 if index % 2 == 0 else 51.0
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.171,
        )

        assert reached is True
        assert moves == []
    finally:
        _close_test_window(window)


def test_current_sweep_hold_does_not_accept_large_error_as_noise_band(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.time()
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(float(target_mm)) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0125)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        690.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    for index in range(8):
        stress = 78.0 + (0.5 if index % 2 else 0.0)
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_out_of_band_sign_by_key[seek_key] = -1.0
    window._seek_out_of_band_since_by_key[seek_key] = now_s - 2.0

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=0.4,
        )

        assert reached is False
        assert moves
    finally:
        _close_test_window(window)


def test_current_sweep_hold_does_not_accept_one_sided_transformation_scatter_as_noise(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.time()
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(float(target_mm)) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.0)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        500.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    for index, stress in enumerate((60.0, 65.0, 72.0, 79.0, 84.0)):
        load_g = mini_dma_mod.load_g_from_stress_mpa(stress, window.spin_diameter.value())
        assert load_g is not None
        timestamp_s = now_s + index * 0.25
        window._scale_signal_buffer.add_sample(
            timestamp_s=timestamp_s,
            raw_g=load_g,
            applied_load_g=load_g,
            raw_text=f"{load_g:.5f} g",
        )
        window._latest_scale_timestamp = timestamp_s
        window._latest_scale_value_g = load_g

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            tolerance=1.8,
        )

        assert reached is False
        assert "filtered control signal" not in window.log_output.toPlainText().lower()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_uses_smooth_dynamic_stress_cap(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_current_sweep_near_correction_stress_mpa.setValue(1.0)
        window.spin_current_sweep_mid_correction_stress_mpa.setValue(5.0)
        window.spin_current_sweep_hold_correction_stress_mpa.setValue(30.0)
        window._automation_phase = "current_hold"

        correction_mm = window._current_sweep_max_stress_correction_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            sensitivity_per_mm=200.0,
            error_value=40.0,
        )

        assert correction_mm is not None
        assert correction_mm == pytest.approx(window._current_sweep_hold_base_command_cap_mm())
    finally:
        _close_test_window(window)


def test_current_sweep_hold_large_error_without_response_uses_geometry_base_cap(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_initial_length.setValue(58.0)
        window.spin_current_sweep_max_correction_strain_pct.setValue(5.0)
        window.spin_current_sweep_hold_correction_stress_mpa.setValue(100.0)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._set_automation_context(
            phase="current_hold",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=50.0,
            plateau_index=1,
        )
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)

        correction_mm = window._current_sweep_max_stress_correction_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            sensitivity_per_mm=10.0,
            error_value=80.0,
            seek_key=seek_key,
        )

        assert correction_mm is not None
        assert correction_mm == pytest.approx(
            58.0 * mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_BASE_COMMAND_STRAIN_PCT / 100.0
        )
    finally:
        _close_test_window(window)


def test_current_sweep_hold_adaptive_large_error_floor_scales_with_band_target_and_quantization(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._scale_quantization_band_for_basis = lambda _basis: 0.0  # type: ignore[method-assign]
        floor_50_mpa = window._current_sweep_hold_adaptive_large_error_floor_for_basis(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            2.0,
            seek_key=(mini_dma_mod.HSW_BASIS_STRESS_MPA, 1, 50.0),
        )
        floor_500_mpa = window._current_sweep_hold_adaptive_large_error_floor_for_basis(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            2.0,
            seek_key=(mini_dma_mod.HSW_BASIS_STRESS_MPA, 1, 500.0),
        )

        assert floor_50_mpa == pytest.approx(
            2.0 * mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_LARGE_ERROR_BAND_FACTOR
        )
        assert floor_500_mpa == pytest.approx(
            500.0 * mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_LARGE_ERROR_TARGET_FRACTION
        )

        window._scale_quantization_band_for_basis = lambda _basis: 4.0  # type: ignore[method-assign]
        quantized_floor = window._current_sweep_hold_adaptive_large_error_floor_for_basis(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            2.0,
            seek_key=(mini_dma_mod.HSW_BASIS_STRESS_MPA, 1, 50.0),
        )
        assert quantized_floor == pytest.approx(
            4.0
            * mini_dma_mod.SCALE_QUANTIZATION_WORSENING_FACTOR
            * mini_dma_mod.SERVO_CURRENT_SWEEP_HOLD_ADAPTIVE_LARGE_ERROR_BAND_FACTOR
        )
    finally:
        _close_test_window(window)


def test_current_sweep_hold_uses_adaptive_response_stiffness_for_large_errors(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_initial_length.setValue(58.0)
        window.spin_current_sweep_max_correction_strain_pct.setValue(1.0)
        window.spin_current_sweep_hold_correction_stress_mpa.setValue(100.0)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._set_automation_context(
            phase="current_hold",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=150.0,
            plateau_index=3,
        )
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 150.0)
        adaptive_load_stiffness = mini_dma_mod.load_g_from_stress_mpa(450.0, window.spin_diameter.value())
        assert adaptive_load_stiffness is not None
        window._current_sweep_hold_response_stiffness_by_key[seek_key] = adaptive_load_stiffness
        window._current_sweep_hold_response_count_by_key[seek_key] = 3

        correction_mm = window._current_sweep_max_stress_correction_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            sensitivity_per_mm=300.0,
            error_value=40.0,
            seek_key=seek_key,
        )

        assert correction_mm is not None
        assert correction_mm == pytest.approx(0.064, rel=0.05)
        assert correction_mm < (20.0 / 300.0)
    finally:
        _close_test_window(window)


def test_current_sweep_hold_adaptive_response_respects_strain_rail(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_initial_length.setValue(58.0)
        window.spin_current_sweep_max_correction_strain_pct.setValue(1.0)
        window.spin_current_sweep_hold_correction_stress_mpa.setValue(100.0)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._set_automation_context(
            phase="current_hold",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=150.0,
            plateau_index=3,
        )
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 150.0)
        adaptive_load_stiffness = mini_dma_mod.load_g_from_stress_mpa(150.0, window.spin_diameter.value())
        assert adaptive_load_stiffness is not None
        window._current_sweep_hold_response_stiffness_by_key[seek_key] = adaptive_load_stiffness
        window._current_sweep_hold_response_count_by_key[seek_key] = 4

        correction_mm = window._current_sweep_max_stress_correction_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            sensitivity_per_mm=100.0,
            error_value=180.0,
            seek_key=seek_key,
        )

        assert correction_mm is not None
        assert correction_mm == pytest.approx(0.203)
    finally:
        _close_test_window(window)


def test_current_sweep_hold_rejects_response_stiffness_that_would_increase_steps(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_current_sweep_hold_correction_stress_mpa.setValue(100.0)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._set_automation_context(
            phase="current_hold",
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=150.0,
            plateau_index=3,
        )
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 150.0)
        softer_load_stiffness = mini_dma_mod.load_g_from_stress_mpa(150.0, window.spin_diameter.value())
        assert softer_load_stiffness is not None

        baseline_correction_mm = window._current_sweep_max_stress_correction_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            sensitivity_per_mm=450.0,
            error_value=80.0,
            seek_key=seek_key,
        )
        window._current_sweep_hold_response_stiffness_by_key[seek_key] = softer_load_stiffness
        window._current_sweep_hold_response_count_by_key[seek_key] = 4
        learned_correction_mm = window._current_sweep_max_stress_correction_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            sensitivity_per_mm=450.0,
            error_value=80.0,
            seek_key=seek_key,
        )

        assert baseline_correction_mm is not None
        assert learned_correction_mm == pytest.approx(baseline_correction_mm)
    finally:
        _close_test_window(window)


def test_very_near_current_sweep_waits_for_two_fresh_samples_after_move(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.767)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        346.0,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="settle",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
    first_sample_s = time.time()
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = first_sample_s - 1.0
    window._seek_post_move_sample_count_by_key[seek_key] = 1
    window._last_motion_command_time_s = first_sample_s - 0.2
    window._last_motion_expected_complete_time_s = first_sample_s - 0.1
    window._last_move_target_mm = 6.80
    window._last_effective_move_target_mm = 6.80
    window._current_position_mm = 6.80
    window._effective_position_mm = 6.80
    window._latest_scale_timestamp = first_sample_s
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        20.45,
        window.spin_diameter.value(),
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=0.171,
        )

        assert reached is False
        assert moves == []
        assert "2 fresh scale samples" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_settle_accepts_target_on_already_used_near_target_scale_sample(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.767)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        602.814969,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="settle",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
    sample_s = time.time()
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = sample_s
    window._latest_scale_timestamp = sample_s
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        19.8514417,
        window.spin_diameter.value(),
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=0.171133118,
        )

        assert reached is True
        assert "Waiting for a new scale sample" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_settle_accepts_target_before_two_sample_correction_gate(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_initial_length.setValue(61.767)
    window._calibrated_stiffness_g_per_mm = mini_dma_mod.load_g_from_stress_mpa(
        602.814969,
        window.spin_diameter.value(),
    )
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="settle",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=20.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 20.0)
    sample_s = time.time()
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = sample_s - 1.0
    window._seek_post_move_sample_count_by_key[seek_key] = 1
    window._last_motion_command_time_s = sample_s - 0.2
    window._last_motion_expected_complete_time_s = sample_s - 0.1
    window._latest_scale_timestamp = sample_s
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        20.022575,
        window.spin_diameter.value(),
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=20.0,
            tolerance=0.171133118,
        )

        assert reached is True
        assert "2 fresh scale samples" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_recovery_load_zero_accepts_before_two_sample_correction_gate(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(False)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.135)
    window.spin_steps_per_mm.setValue(800.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.RECOVERY_LOAD
    window._set_automation_context(
        phase="recovery",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=0.0,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_LOAD_G, 0.0)
    sample_s = time.time()
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = sample_s - 1.0
    window._seek_post_move_sample_count_by_key[seek_key] = 1
    window._last_motion_command_time_s = sample_s - 0.2
    window._last_motion_expected_complete_time_s = sample_s - 0.1
    window._latest_scale_timestamp = sample_s
    window._latest_scale_value_g = 21.135

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=mini_dma_mod.SERVO_AUTO_TOLERANCE_LOAD_G,
        )

        assert reached is True
        assert "2 fresh scale samples" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_recovery_load_zero_does_not_accept_backlash_limited_residual_load(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_backlash_mm.setValue(0.02)
    window._last_move_direction = -1.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.RECOVERY_LOAD
    window._active_control_config = window._freeze_control_config()
    window._set_automation_context(
        phase="recovery",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=0.0,
    )
    window._latest_scale_timestamp = time.time()
    window._latest_scale_value_g = -0.815
    window._basis_sensitivity_per_mm = lambda *args, **kwargs: 100.0  # type: ignore[method-assign]
    window._move_to_position_mm = (  # type: ignore[method-assign]
        lambda target_mm, **_kwargs: moves.append(float(target_mm)) or True
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=mini_dma_mod.SERVO_AUTO_TOLERANCE_LOAD_G,
        )

        assert reached is False
        assert moves
        assert "backlash-limited tolerance" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_resume_band_does_not_expand_with_transformation_noise(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_active = True
    window._active_control_config = window._freeze_control_config()
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._current_sweep_ramp_hold_step_index = 0
    window._current_sweep_ramp_hold_started_s = 10.0
    window._current_sweep_ramp_hold_in_band_since_s = 10.0
    step = mini_dma_mod.AutomationStep(
        "sweep_current",
        target_value=50.0,
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        current_hold_enabled=True,
    )
    monkeypatch.setattr(
        window,
        "_current_sweep_target_error_and_tolerance",
        lambda *_args, **_kwargs: (10.0, 10.0, 0.2, 100.0),
    )

    try:
        holding, _changed = window._update_current_sweep_ramp_hold(step, 0, now_s=11.0)

        assert holding is True
        assert window._current_sweep_ramp_hold_step_index == 0
        assert "Resumed current ramp" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_target_acceptance_excludes_motor_step_floor(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(1000.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._active_control_config = window._freeze_control_config()
    window._set_automation_context(
        phase="current",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=30.0,
    )
    monkeypatch.setattr(
        window,
        "_basis_sensitivity_per_mm",
        lambda *_args, **_kwargs: 45_000.0,
    )

    try:
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 30.0)

        assert window._seek_effective_tolerance(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            0.2,
            seek_key=seek_key,
        ) == pytest.approx(45.0)
        assert window._seek_target_acceptance_tolerance(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            0.2,
            seek_key=seek_key,
        ) == pytest.approx(0.2)
    finally:
        _close_test_window(window)


def test_current_sweep_target_ramp_does_not_accept_zero_load_inside_step_floor(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    now_s = time.monotonic()
    window.spin_steps_per_mm.setValue(1000.0)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(True)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._active_control_config = window._freeze_control_config()
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=30.0,
    )
    window._latest_scale_timestamp = now_s
    window._latest_scale_value_g = 0.0
    monkeypatch.setattr(window, "_has_fresh_scale_reading", lambda *args, **kwargs: True)
    monkeypatch.setattr(window, "_current_distribution_value", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(window, "_seek_filtered_control_signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(window, "_basis_sensitivity_per_mm", lambda *args, **kwargs: 45_000.0)
    monkeypatch.setattr(
        window,
        "_move_to_position_mm",
        lambda target_mm, **_kwargs: moves.append(float(target_mm)) or True,
    )

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=30.0,
            tolerance=0.2,
        )

        assert reached is False
        assert moves
    finally:
        _close_test_window(window)


def test_current_sweep_does_not_update_live_stiffness_from_sweep_fluctuations(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(20.0)
    window.spin_diameter.setValue(0.0191)
    window._calibrated_stiffness_g_per_mm = 20.0
    window._calibrated_stiffness_length_mm = 20.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_stiffness_value_by_basis[mini_dma_mod.HSW_BASIS_STRESS_MPA] = 40.0
    window._seek_last_stiffness_position_by_basis[mini_dma_mod.HSW_BASIS_STRESS_MPA] = 0.0
    window._seek_last_value_by_key[seek_key] = 40.0
    window._seek_last_effective_position_by_key[seek_key] = 0.0
    window._current_position_mm = 0.02
    window._effective_position_mm = 0.02

    try:
        window._update_live_seek_stiffness(
            seek_key,
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            90.0,
        )

        assert seek_key not in window._seek_live_stiffness_by_key
        assert window._seek_live_stiffness_g_per_mm is None
        assert window._basis_sensitivity_per_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            seek_key=seek_key,
        ) == pytest.approx(
            mini_dma_mod.stress_mpa_from_load_g(20.0, window.spin_diameter.value())
        )
    finally:
        _close_test_window(window)


def test_current_sweep_target_ramp_updates_live_stiffness_before_heating(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_initial_length.setValue(20.0)
    window.spin_diameter.setValue(0.0191)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_last_stiffness_value_by_basis[mini_dma_mod.HSW_BASIS_STRESS_MPA] = 40.0
    window._seek_last_stiffness_position_by_basis[mini_dma_mod.HSW_BASIS_STRESS_MPA] = 0.0
    window._seek_last_value_by_key[seek_key] = 40.0
    window._seek_last_effective_position_by_key[seek_key] = 0.0
    window._current_position_mm = 0.02
    window._effective_position_mm = 0.02

    try:
        window._update_live_seek_stiffness(
            seek_key,
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            90.0,
        )

        assert seek_key in window._seek_live_stiffness_by_key
        assert window._seek_live_stiffness_g_per_mm is not None
        assert window._basis_sensitivity_per_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            seek_key=seek_key,
        ) > 0.0
    finally:
        _close_test_window(window)


def test_current_sweep_target_ramp_prefers_local_stiffness_over_stiffer_prior(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_diameter.setValue(0.0191)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    local_load_stiffness = 10.0
    window._seek_live_stiffness_by_key[seek_key] = local_load_stiffness
    window._seek_live_stiffness_g_per_mm = 200.0
    window._calibrated_stiffness_g_per_mm = 300.0

    try:
        assert window._basis_sensitivity_per_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            seek_key=seek_key,
        ) == pytest.approx(
            mini_dma_mod.stress_mpa_from_load_g(local_load_stiffness, window.spin_diameter.value())
        )
    finally:
        _close_test_window(window)


def test_current_sweep_hold_keeps_conservative_stiffness_prior(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_diameter.setValue(0.0191)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
    window._seek_live_stiffness_by_key[seek_key] = 10.0
    conservative_load_stiffness = 300.0
    window._seek_live_stiffness_g_per_mm = 200.0
    window._calibrated_stiffness_g_per_mm = conservative_load_stiffness

    try:
        assert window._basis_sensitivity_per_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            seek_key=seek_key,
        ) == pytest.approx(
            mini_dma_mod.stress_mpa_from_load_g(conservative_load_stiffness, window.spin_diameter.value())
        )
    finally:
        _close_test_window(window)


def test_current_sweep_seek_uses_target_stage_speed_for_dynamic_balance(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.target_steps: int | None = None
            self.max_speed: int | None = None

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.target_steps = position_steps
            self.max_speed = max_speed

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 1.0
    window._current_position_steps = 10000
    window._last_move_target_mm = 1.0
    window._manual_jog_uses_last_target = False
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._automation_phase = "current"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window.spin_steps_per_mm.setValue(10000.0)
    window.spin_current_sweep_nudge_mm.setValue(0.002)
    window.spin_current_sweep_balance_speed_mm_s.setValue(0.05)
    window.spin_motion_speed_mm_s.setValue(1.0)

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )
        _wait_for_tic_commands(window)

        assert reached is False
        assert controller.target_steps == -1250
        assert controller.max_speed == 450_000_000
    finally:
        _close_test_window(window)


def test_current_sweep_load_target_ramp_uses_target_stage_speed(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.target_steps: int | None = None
            self.max_speed: int | None = None

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.target_steps = position_steps
            self.max_speed = max_speed

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 1.0
    window._current_position_steps = 10000
    window._last_move_target_mm = 1.0
    window._manual_jog_uses_last_target = False
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._automation_phase = "target_ramp"
    window._automation_basis = mini_dma_mod.HSW_BASIS_LOAD_G
    window.spin_steps_per_mm.setValue(10000.0)
    window.spin_scale_interval.setValue(250)
    window.spin_current_sweep_nudge_mm.setValue(0.1)
    window.spin_current_sweep_balance_speed_mm_s.setValue(0.05)
    window.spin_current_sweep_target_speed_mm_s.setValue(1.0)

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )
        _wait_for_tic_commands(window)

        assert reached is False
        assert controller.target_steps == 7500
        assert controller.max_speed == 100_000_000
    finally:
        _close_test_window(window)


def test_current_sweep_target_ramp_large_error_can_use_stage_speed_cap(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._calibrated_stiffness_g_per_mm = 10.0
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=5.0,
        plateau_index=1,
    )
    window._active_target_ramp_rate_value_s = 0.5
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_LOAD_G, 5.0)

    try:
        speed_cap = window._target_ramp_speed_cap_mm_s(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            seek_key=seek_key,
            current_value=0.0,
            target_value=5.0,
        )

        assert speed_cap is None
    finally:
        _close_test_window(window)


def test_current_sweep_target_ramp_probe_cap_uses_near_stress_after_reversal(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_diameter.setValue(0.03)
    window.spin_steps_per_mm.setValue(800.0)
    window._calibrated_stiffness_g_per_mm = 72.0
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )

    try:
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
        sensitivity = window._basis_sensitivity_per_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            seek_key=seek_key,
        )
        assert sensitivity is not None

        probe_mm = window._current_sweep_target_ramp_probe_correction_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            sensitivity,
        )

        assert probe_mm == pytest.approx(max(window._motor_step_mm(), 1.0 / sensitivity))
    finally:
        _close_test_window(window)


def test_current_sweep_target_ramp_starts_with_gated_feedback(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_diameter.setValue(0.03)
    window._calibrated_stiffness_g_per_mm = 72.0
    window._calibrated_stiffness_length_mm = float(window.spin_initial_length.value())
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)

    try:
        assert (
            window._seek_cruise_feedback_allowed(
                mini_dma_mod.HSW_BASIS_STRESS_MPA,
                error_value=50.0,
                tolerance=0.1,
                speed_mm_s=0.05,
                seek_key=seek_key,
                previous_error=60.0,
            )
            is False
        )
    finally:
        _close_test_window(window)


def test_current_sweep_stress_correction_is_capped_by_planned_mpa_change(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(80.0)
    window.spin_diameter.setValue(0.03)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_current_sweep_max_correction_strain_pct.setValue(1.0)
    window._calibrated_stiffness_g_per_mm = 72.0
    window._calibrated_stiffness_length_mm = 80.0
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )

    try:
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
        correction_mm = window._predictive_seek_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            error_value=50.0,
            tolerance=0.1,
            seek_key=seek_key,
        )
        sensitivity = mini_dma_mod.stress_mpa_from_load_g(72.0, window.spin_diameter.value())
        assert sensitivity is not None
        assert correction_mm == pytest.approx(7.5 / sensitivity)
        assert correction_mm < 80.0 * 0.01
    finally:
        _close_test_window(window)


def test_current_sweep_current_phase_large_error_uses_fast_recovery_cap(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(80.0)
    window.spin_diameter.setValue(0.03)
    window.spin_steps_per_mm.setValue(800.0)
    window._calibrated_stiffness_g_per_mm = 72.0
    window._calibrated_stiffness_length_mm = 80.0
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )

    try:
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
        sensitivity = mini_dma_mod.stress_mpa_from_load_g(72.0, window.spin_diameter.value())
        assert sensitivity is not None

        large_error_step = window._predictive_seek_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            error_value=120.0,
            tolerance=0.1,
            seek_key=seek_key,
        )
        near_error_step = window._predictive_seek_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            error_value=4.0,
            tolerance=0.1,
            seek_key=seek_key,
        )

        assert large_error_step == pytest.approx(10.0 / sensitivity)
        assert near_error_step == pytest.approx(max(window._motor_step_mm(), 1.0 / sensitivity))
    finally:
        _close_test_window(window)


def test_iso_current_stress_ramp_target_uses_larger_dynamic_step_cap(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(50.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_current_sweep_nudge_mm.setValue(0.01)
    stiffness_load_g = mini_dma_mod.load_g_from_stress_mpa(142.0, window.spin_diameter.value())
    assert stiffness_load_g is not None
    window._calibrated_stiffness_g_per_mm = stiffness_load_g
    window._calibrated_stiffness_length_mm = 50.0
    window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRESS_RAMP
    window._active_target_ramp_start_value = 0.0
    window._active_target_ramp_rate_value_s = 5.0
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=270.0,
        plateau_index=1,
        note="1:up",
    )

    try:
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 270.0)
        correction_mm = window._predictive_seek_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            error_value=95.0,
            tolerance=0.1,
            seek_key=seek_key,
        )

        assert correction_mm > window.spin_current_sweep_nudge_mm.value()
        assert correction_mm == pytest.approx(50.0 * 0.0012)
    finally:
        _close_test_window(window)


def test_iso_current_stress_ramp_lagging_target_adds_feedforward(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(50.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    stiffness_load_g = mini_dma_mod.load_g_from_stress_mpa(142.0, window.spin_diameter.value())
    assert stiffness_load_g is not None
    window._calibrated_stiffness_g_per_mm = stiffness_load_g
    window._calibrated_stiffness_length_mm = 50.0
    window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRESS_RAMP
    window._active_target_ramp_start_value = 0.0
    window._active_target_ramp_rate_value_s = 5.0
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=200.0,
        plateau_index=1,
        note="1:up",
    )

    try:
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 200.0)
        lagging_step = window._predictive_seek_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            error_value=2.0,
            tolerance=0.1,
            seek_key=seek_key,
        )
        ahead_step = window._predictive_seek_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            error_value=-2.0,
            tolerance=0.1,
            seek_key=seek_key,
        )
        lagging_speed = window._seek_speed_mm_s(
            2.0,
            0.1,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            seek_key=seek_key,
            current_value=198.0,
        )
        ahead_speed = window._seek_speed_mm_s(
            -2.0,
            0.1,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
            seek_key=seek_key,
            current_value=202.0,
        )

        assert lagging_step > ahead_step
        assert lagging_step - ahead_step == pytest.approx(1.5 / 142.0)
        assert lagging_speed > ahead_speed
    finally:
        _close_test_window(window)


def test_iso_current_stress_ramp_tapers_correction_near_endpoint(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(50.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    stiffness_load_g = mini_dma_mod.load_g_from_stress_mpa(142.0, window.spin_diameter.value())
    assert stiffness_load_g is not None
    window._calibrated_stiffness_g_per_mm = stiffness_load_g
    window._calibrated_stiffness_length_mm = 50.0
    window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRESS_RAMP
    window._active_target_ramp_start_value = 0.0
    window._active_target_ramp_end_value = 400.0
    window._active_target_ramp_rate_value_s = 5.0
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=392.0,
        plateau_index=1,
        note="1:up",
    )

    try:
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 392.0)
        correction_mm = window._predictive_seek_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            error_value=60.0,
            tolerance=0.1,
            seek_key=seek_key,
        )

        assert correction_mm == pytest.approx(3.0 / 142.0)
    finally:
        _close_test_window(window)


def test_iso_current_stress_ramp_keeps_moving_when_ahead_of_mid_ramp_target(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_initial_length.setValue(50.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    stiffness_load_g = mini_dma_mod.load_g_from_stress_mpa(142.0, window.spin_diameter.value())
    assert stiffness_load_g is not None
    window._calibrated_stiffness_g_per_mm = stiffness_load_g
    window._calibrated_stiffness_length_mm = 50.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRESS_RAMP
    window._active_target_ramp_start_value = 0.0
    window._active_target_ramp_end_value = 400.0
    window._active_target_ramp_rate_value_s = 5.0
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=200.0,
        plateau_index=1,
        note="1:up",
    )
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 10.0
    window._effective_position_mm = 10.0
    moves: list[float] = []
    trace_rows: list[dict[str, object]] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
    window._write_control_trace = lambda **kwargs: trace_rows.append(dict(kwargs))  # type: ignore[method-assign]

    try:
        window._current_distribution_value = lambda *_args, **_kwargs: 202.0  # type: ignore[method-assign]
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=200.0,
            tolerance=0.1,
        )

        assert reached is False
        assert len(moves) == 1
        assert moves[-1] < 10.0

        window._current_distribution_value = lambda *_args, **_kwargs: 198.0  # type: ignore[method-assign]
        window._latest_scale_timestamp = time.time() + 1.0
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=200.0,
            tolerance=0.1,
        )

        assert reached is False
        assert len(moves) == 2
        assert moves[-1] < 10.0
    finally:
        _close_test_window(window)


def test_iso_current_stress_ramp_accepts_ahead_at_endpoint(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_initial_length.setValue(50.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    stiffness_load_g = mini_dma_mod.load_g_from_stress_mpa(142.0, window.spin_diameter.value())
    assert stiffness_load_g is not None
    window._calibrated_stiffness_g_per_mm = stiffness_load_g
    window._calibrated_stiffness_length_mm = 50.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRESS_RAMP
    window._active_target_ramp_start_value = 0.0
    window._active_target_ramp_end_value = 400.0
    window._active_target_ramp_rate_value_s = 5.0
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=400.0,
        plateau_index=1,
        note="1:up",
    )
    window._latest_scale_timestamp = time.time()
    moves: list[float] = []
    trace_rows: list[dict[str, object]] = []
    window._current_distribution_value = lambda *_args, **_kwargs: 404.0  # type: ignore[method-assign]
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
    window._write_control_trace = lambda **kwargs: trace_rows.append(dict(kwargs))  # type: ignore[method-assign]

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=400.0,
            tolerance=0.1,
        )

        assert reached is True
        assert moves == []
        assert trace_rows[-1]["reason"] == "monotonic_target_ramp_endpoint"
    finally:
        _close_test_window(window)


def test_iso_current_stress_ramp_inside_mid_ramp_tolerance_still_moves(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_initial_length.setValue(50.0)
    window.spin_diameter.setValue(0.0191)
    window.spin_steps_per_mm.setValue(800.0)
    stiffness_load_g = mini_dma_mod.load_g_from_stress_mpa(142.0, window.spin_diameter.value())
    assert stiffness_load_g is not None
    window._calibrated_stiffness_g_per_mm = stiffness_load_g
    window._calibrated_stiffness_length_mm = 50.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRESS_RAMP
    window._active_target_ramp_start_value = 0.0
    window._active_target_ramp_end_value = 400.0
    window._active_target_ramp_rate_value_s = 5.0
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=200.0,
        plateau_index=1,
        note="1:up",
    )
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 10.0
    window._effective_position_mm = 10.0
    moves: list[float] = []
    window._current_distribution_value = lambda *_args, **_kwargs: 200.02  # type: ignore[method-assign]
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            target_value=200.0,
            tolerance=0.1,
        )

        assert reached is False
        assert len(moves) == 1
        assert moves[-1] < 10.0
    finally:
        _close_test_window(window)


def test_iso_current_stress_ramp_rate_error_adjusts_continuous_step(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(50.0)
    window.spin_diameter.setValue(0.0191)
    stiffness_load_g = mini_dma_mod.load_g_from_stress_mpa(142.0, window.spin_diameter.value())
    assert stiffness_load_g is not None
    window._calibrated_stiffness_g_per_mm = stiffness_load_g
    window._calibrated_stiffness_length_mm = 50.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRESS_RAMP
    window._active_target_ramp_start_value = 0.0
    window._active_target_ramp_end_value = 400.0
    window._active_target_ramp_rate_value_s = 5.0
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=200.0,
        plateau_index=1,
        note="1:up",
    )

    try:
        base_step = window._iso_current_stress_ramp_continuous_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            142.0,
            error_value=0.0,
            rate_error_value_s=None,
        )
        slow_step = window._iso_current_stress_ramp_continuous_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            142.0,
            error_value=0.0,
            rate_error_value_s=3.0,
        )
        fast_step = window._iso_current_stress_ramp_continuous_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            142.0,
            error_value=0.0,
            rate_error_value_s=-3.0,
        )

        assert base_step is not None
        assert slow_step is not None
        assert fast_step is not None
        assert slow_step > base_step > fast_step
        assert fast_step >= window._motor_step_mm()
    finally:
        _close_test_window(window)


def test_current_sweep_hold_phase_uses_faster_recovery_cap(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(80.0)
    window.spin_diameter.setValue(0.03)
    window.spin_steps_per_mm.setValue(800.0)
    window._calibrated_stiffness_g_per_mm = 72.0
    window._calibrated_stiffness_length_mm = 80.0
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
    )

    try:
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
        sensitivity = mini_dma_mod.stress_mpa_from_load_g(72.0, window.spin_diameter.value())
        assert sensitivity is not None

        hold_error_step = window._predictive_seek_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            error_value=120.0,
            tolerance=0.1,
            seek_key=seek_key,
        )

        assert hold_error_step == pytest.approx(30.0 / sensitivity)
    finally:
        _close_test_window(window)


def test_flat_seek_feedback_continues_for_shape_memory_plateau(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(True)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._automation_plateau_index = 1
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 0.0
    window._current_position_steps = 0
    window.spin_current_sweep_nudge_mm.setValue(0.1)

    moves: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]

    try:
        start_sample_time_s = time.time()
        for index in range(6):
            window._latest_scale_timestamp = start_sample_time_s + index * 0.3
            assert window._seek_distribution_target(
                mini_dma_mod.HSW_BASIS_LOAD_G,
                target_value=3.0,
                tolerance=0.25,
            ) is False

        assert len(moves) >= 5
        assert "maximum correction travel" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_seek_target_logs_feedback_sample_before_next_move(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("seek_feedback_sample")
    window.check_tension_load_positive.setChecked(True)
    window._latest_scale_value_g = -1.0
    window._latest_scale_text = "-1.000 g"
    window._latest_scale_timestamp = time.time()
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
    window._move_to_position_mm = lambda _target_mm, **_kwargs: True  # type: ignore[method-assign]

    try:
        window._start_session()
        initial_count = len(window._session_points)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
        window._set_automation_context(
            phase="seek",
            basis=mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=3.0,
            plateau_index=1,
        )
        window._last_motion_command_time_s = time.time() - 0.01
        window._latest_scale_timestamp = time.time()
        window._last_session_log_timestamp_s = time.time() - (
            window._current_sweep_log_interval_ms() / 1000.0
        ) - 0.01

        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=3.0,
            tolerance=0.25,
        )

        assert reached is False
        assert len(window._session_points) == initial_count + 1
        assert window._session_points[-1].automation_phase == "seek"
        assert window._session_points[-1].load_g == pytest.approx(1.0)
    finally:
        _close_test_window(window)


def test_recovery_sampling_does_not_append_to_session_log(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("recovery_sampling")
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window._latest_scale_value_g = -1.0
    window._latest_scale_timestamp = time.time()
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
    window._move_to_position_mm = lambda _target_mm, **_kwargs: True  # type: ignore[method-assign]

    try:
        window._start_session()
        initial_count = len(window._session_points)
        window._automation_active = True
        window._automation_name = mini_dma_mod.RECOVERY_LOAD
        window._recovery_start_monotonic = time.monotonic()
        window._set_automation_context(
            phase="recover",
            basis=mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            plateau_index=0,
        )

        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=0.25,
        )

        assert reached is False
        assert len(window._session_points) == initial_count
        assert len(window._recovery_points) == 1
        assert window._recovery_points[-1].load_g == pytest.approx(1.0)
    finally:
        _close_test_window(window)


def test_recovery_timer_records_live_points_during_position_move(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.check_tension_load_positive.setChecked(True)
    window.spin_zero_load_scale_g.setValue(21.2)

    try:
        window._show_recovery_plot_dialog("Recovery test")
        window._automation_active = True
        window._automation_name = mini_dma_mod.RECOVERY_POSITION
        window._set_automation_context(phase="recover", note="displacement to 0")
        window._latest_scale_value_g = 21.0
        window._latest_scale_timestamp = time.time()
        window._current_position_mm = 0.2
        window._effective_position_mm = 0.2

        window._handle_ui_refresh_timer()
        window._handle_ui_refresh_timer()

        assert len(window._recovery_points) == 1
        assert window._recovery_points[0].load_g == pytest.approx(0.2)
        assert len(window._session_points) == 0
    finally:
        _close_test_window(window)


def test_recovery_position_start_restarts_ui_refresh_timer(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._preflight_recipe_hardware = lambda _steps: True  # type: ignore[method-assign]
    window._ui_refresh_timer.stop()
    window._current_position_mm = 1.0
    window._effective_position_mm = 1.0
    window._position_reference_mm = 0.0
    window._start_automation_control_loop = lambda _interval_ms: None  # type: ignore[method-assign]

    try:
        window._start_recovery_displacement_zero()

        assert window._automation_active is True
        assert window._automation_name == mini_dma_mod.RECOVERY_POSITION
        assert [step.action for step in window._automation_steps] == ["move"]
        assert window._ui_refresh_timer.isActive() is True
    finally:
        _close_test_window(window)


def test_recovery_uses_manual_motion_speed(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._automation_active = True
    window._automation_name = mini_dma_mod.RECOVERY_LOAD
    window.spin_motion_speed_mm_s.setValue(1.0)
    window.spin_current_sweep_balance_speed_mm_s.setValue(0.05)

    try:
        assert window._motion_speed_for_current_context(manual_jog=False) == pytest.approx(1.0)
    finally:
        _close_test_window(window)


def test_recipe_completion_stops_session_logging(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("recipe_completion")
    window._latest_scale_timestamp = time.time()
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
    window._preflight_recipe_hardware = lambda _steps: True  # type: ignore[method-assign]
    window._start_automation_control_loop = lambda _interval_ms: None  # type: ignore[method-assign]

    try:
        window._start_session()
        window._automation_active = True
        window._automation_steps = [mini_dma_mod.AutomationStep("record")]
        window._automation_index = 1
        window._automation_total_steps = 1
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD

        window._handle_auto_ramp_tick()

        assert window._automation_active is False
        assert window._session_active is False
        assert "Recipe completed" in window.log_output.toPlainText()
        assert "Skipped displacement recovery" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_constant_current_completion_does_not_start_hidden_origin_recovery(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("constant_current_completion")
    recovery_calls: list[bool] = []
    window._start_recovery_position_origin = lambda: recovery_calls.append(True)  # type: ignore[method-assign]

    try:
        window._start_session()
        window._automation_active = True
        window._automation_steps = []
        window._automation_index = 0
        window._automation_total_steps = 0
        window._automation_name = mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP
        window.check_return_to_origin.setChecked(True)

        window._handle_auto_ramp_tick()

        assert window._automation_active is False
        assert window._session_active is False
        assert recovery_calls == []
        assert "Recipe completed" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_user_stop_stops_recipe_session_logging(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("recipe_user_stop")
    window._latest_scale_timestamp = time.time()
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
    window._ask_recovery_after_stop = lambda: None  # type: ignore[method-assign]

    try:
        window._start_session()
        window._automation_active = True
        window._automation_steps = [mini_dma_mod.AutomationStep("record")]
        window._automation_index = 0
        window._automation_total_steps = 1
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD

        window._stop_recipe_from_button()

        assert window._automation_active is False
        assert window._session_active is False
        assert window._session_csv_handle is None
        assert "Recipe stopped" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_control_stop_with_recovery_closes_session_and_stops_dashboard_time_points(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    recovery_prompts: list[bool] = []
    window.edit_log_name.setText("control_stop_recovery")
    window._latest_scale_value_g = 21.2
    window._latest_scale_text = "21.200 g"
    window._latest_scale_timestamp = time.time()
    window._ask_recovery_after_stop = lambda: recovery_prompts.append(True)  # type: ignore[method-assign]

    try:
        window._start_session(enable_logging=True, record_initial_point=False)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._automation_steps = [mini_dma_mod.AutomationStep("record")]
        window._automation_index = 0
        window._automation_total_steps = 1
        window._record_live_plot_sample_from_ui_refresh()
        assert window._session_active is True
        assert len(window._live_plot_points) == 1

        window._stop_auto_ramp(
            log_completion=False,
            offer_recovery=True,
            stop_reason="recipe_control_stop",
            stop_detail="Synthetic control stop.",
        )
        window._latest_scale_timestamp = time.time()
        recorded, refreshed = window._record_live_plot_sample_from_ui_refresh()

        assert recovery_prompts == [True]
        assert window._automation_active is False
        assert window._session_active is False
        assert window._session_csv_handle is None
        assert window._live_plot_points == []
        assert recorded is False
        assert refreshed is False
        assert "Session stopped" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_zero_current_points_do_not_report_resistance(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("zero_current_resistance")
    window.check_zero_on_preload.setChecked(False)
    window._latest_scale_value_g = -1.0
    window._latest_scale_timestamp = time.time()
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
    window._supply_last_setpoint_mA = 0.0
    window._supply_snapshot = {
        "current_mA": 0.01,
        "voltage_V": 1.0,
        "resistance_ohm": 100.0,
        "power_W": 0.00001,
    }

    try:
        window._start_session()

        assert window._session_points[-1].resistance_ohm is None
        rows = list(
            csv.DictReader(
                (tmp_path / "zero_current_resistance" / "measurement.csv").open(
                    encoding="utf-8",
                    newline="",
                )
            )
        )
        assert rows[-1]["resistance_ohm"] == ""
    finally:
        _close_test_window(window)


def test_manual_recipe_stop_turns_current_off_and_keeps_resume_state(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        def __init__(self) -> None:
            self.off_count = 0

        def is_connected(self) -> bool:
            return True

        def output_off(self) -> None:
            self.off_count += 1

        def disconnect(self) -> None:
            return None

        def measure(self) -> dict[str, float | None]:
            return {
                "current_mA": 0.0,
                "voltage_V": 0.0,
                "resistance_ohm": None,
                "power_W": 0.0,
            }

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._supply_last_setpoint_mA = 2.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._automation_steps = [
        mini_dma_mod.AutomationStep("set_current", current_mA=1.0),
        mini_dma_mod.AutomationStep("record"),
    ]
    window._automation_index = 1
    window._automation_total_steps = 2
    window._automation_interval_ms = 250
    window._last_recipe_summary = "test recipe"
    window._ask_recovery_after_stop = lambda: None  # type: ignore[method-assign]

    try:
        window._stop_auto_ramp(user_initiated=True)

        assert supply.off_count == 1
        assert window._supply_output_enabled is False
        assert window._resume_recipe_state is not None
        assert window._resume_recipe_state.index == 1
        assert window._resume_recipe_state.current_setpoint_mA == pytest.approx(2.0)
    finally:
        _close_test_window(window)


def test_motor_supply_channel_is_enabled_before_recipe_tic_preflight(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        def __init__(self) -> None:
            self.configured: list[tuple[int, float, float, bool]] = []
            self.selected_anneal = 0

        def is_connected(self) -> bool:
            return True

        def configure_channel(self, *, channel: int, voltage_v: float, current_a: float, output_on: bool) -> None:
            self.configured.append((channel, voltage_v, current_a, output_on))

        def select_channel(self, channel: int | None = None) -> None:
            if channel is None:
                self.selected_anneal += 1

        def disconnect(self) -> None:
            return None

    supply = _FakeSupply()
    window._supply_controller = supply  # type: ignore[assignment]
    window.check_motor_supply_power.setChecked(True)
    window.combo_motor_supply_channel.setCurrentIndex(window.combo_motor_supply_channel.findData(2))
    window.spin_motor_supply_voltage.setValue(12.0)
    window.spin_motor_supply_current_limit.setValue(1.5)
    window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_scale_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._apply_tic_configured_step_mode = lambda: (True, "PASS")  # type: ignore[method-assign]
    window._apply_tic_current_limit = lambda: (True, "PASS")  # type: ignore[method-assign]
    window._apply_tic_motion_limits = lambda: (True, "PASS")  # type: ignore[method-assign]

    try:
        ok = window._preflight_recipe_hardware([mini_dma_mod.AutomationStep("move", target_mm=0.0)])

        assert ok is True
        assert supply.configured == [(2, 12.0, 1.5, True)]
        assert supply.selected_anneal == 0
    finally:
        _close_test_window(window)


def test_hmp4030_defaults_keep_safe_voltage_and_require_manual_channel_selection(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert mini_dma_mod.SUPPLY_PROFILES["hmp4030"]["max_voltage"] == pytest.approx(32.05)
        assert mini_dma_mod.SUPPLY_PROFILES["hmp4030"]["channel_select"] == 3
        assert window.spin_supply_voltage_limit.value() == pytest.approx(32.05)
        assert window.combo_current_sweep_supply_channel.currentData() == 0
        assert window.combo_motor_supply_channel.currentData() == 0
        assert window.spin_motor_supply_voltage.value() == pytest.approx(12.0)
        assert window.spin_motor_supply_current_limit.value() == pytest.approx(0.5)
        assert window.spin_tic_current_limit_mA.value() == 343
    finally:
        _close_test_window(window)


def test_shared_broker_defaults_match_current_kosice_hmp4030_wiring() -> None:
    assert mini_dma_mod.SUPPLY_PROFILES["shared_hmp_broker"]["channel_select"] == 3
    assert mini_dma_mod.SUPPLY_PROFILES["shared_hmp_broker"]["motor_supply_channel"] == 2


def test_tic_current_limit_keeps_cool_bench_default() -> None:
    class _FakeTicController:
        def __init__(self) -> None:
            self.commands: list[tuple[str, ...]] = []

        def run(self, *args: str, timeout_s: float = 5.0) -> str:
            self.commands.append(args)
            return ""

    controller = _FakeTicController()

    applied = mini_dma_mod.apply_tic_current_limit_mA(controller, mini_dma_mod.DEFAULT_TIC_CURRENT_LIMIT_MA)

    assert applied == 343
    assert controller.commands == [("--current", "343")]


def test_tic_current_limit_uses_t500_safe_step_at_or_below_target() -> None:
    assert mini_dma_mod.safe_tic_current_limit_mA(343) == 343
    assert mini_dma_mod.safe_tic_current_limit_mA(400) == 343
    assert mini_dma_mod.safe_tic_current_limit_mA(500) == 495
    assert mini_dma_mod.tic_t500_current_limit_code(500) == 4


def test_current_sweep_recipe_filename_is_concise_and_descriptive(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        window.combo_recipe_mode.setCurrentIndex(index)
        window.spin_setup_preload_stress_mpa.setValue(20.0)
        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(500.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(80.0)
        window.spin_current_sweep_step_mA.setValue(1.0)
        window.check_current_sweep_hold_on_error.setChecked(True)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.spin_current_sweep_first_overheating_target_mpa.setValue(20.0)

        assert window._suggest_recipe_filename() == (
            "iso-stress_setup20MPa_target50-500x50MPa_current1-80mA_1mAps_hold_firstheat20MPa.recipe.json"
        )
    finally:
        _close_test_window(window)


def test_iso_stress_fatigue_recipe_filename_is_concise_and_descriptive(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_FATIGUE)
        window.combo_recipe_mode.setCurrentIndex(index)
        window.spin_setup_preload_stress_mpa.setValue(20.0)
        window.spin_current_sweep_target_start.setValue(150.0)
        window.spin_current_sweep_fatigue_cycles.setValue(100)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(60.0)
        window.spin_current_sweep_step_mA.setValue(1.0)
        window.check_current_sweep_hold_on_error.setChecked(True)

        assert window._suggest_recipe_filename() == (
            "iso-stress-fatigue_setup20MPa_stress150MPa_100cycles_current1-60mA_1mAps_hold.recipe.json"
        )
        window.check_current_sweep_first_overheating.setChecked(True)
        window.spin_current_sweep_first_overheating_target_mpa.setValue(20.0)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(40.0)
        assert window._suggest_recipe_filename() == (
            "iso-stress-fatigue_setup20MPa_stress150MPa_100cycles_current1-60mA_1mAps_hold_"
            "firstheat20MPa_firstmax40mA.recipe.json"
        )
    finally:
        _close_test_window(window)


def test_current_sweep_recipe_round_trips_from_json(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    recipe_path = tmp_path / "iso-stress_setup20MPa_target50-500x50MPa_current1-80mA_1mAps_hold.recipe.json"

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        window.combo_recipe_mode.setCurrentIndex(index)
        window.spin_setup_preload_stress_mpa.setValue(20.0)
        window.spin_setup_preload_duration_s.setValue(10.0)
        window.spin_setup_preload_stable_s.setValue(2.5)
        window.spin_setup_zero_stable_s.setValue(4.0)
        window.spin_setup_slack_step_cap_stress_mpa.setValue(75.0)
        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_target_end.setValue(500.0)
        window.spin_current_sweep_target_step.setValue(50.0)
        window.spin_current_sweep_target_ramp_rate.setValue(2.0)
        window.spin_current_sweep_target_speed_mm_s.setValue(4.0)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(80.0)
        window.spin_current_sweep_step_mA.setValue(1.0)
        window.check_current_sweep_hold_on_error.setChecked(True)
        window.check_current_sweep_first_overheating.setChecked(True)
        window.spin_current_sweep_first_overheating_target_mpa.setValue(20.0)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(False)
        window.spin_current_sweep_first_overheating_end_mA.setValue(90.0)
        window.spin_current_sweep_hold_correction_stress_mpa.setValue(30.0)

        window._save_recipe_to_path(recipe_path)
        payload = json.loads(recipe_path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert payload["recipe"]["mode"] == mini_dma_mod.CURRENT_SWEEP_STRESS
        assert payload["recipe"]["setup"]["slack_step_cap_stress_mpa"] == pytest.approx(75.0)
        assert payload["recipe"]["setup"]["preload_stable_s"] == pytest.approx(2.5)
        assert payload["recipe"]["setup"]["zero_stable_s"] == pytest.approx(4.0)
        assert payload["recipe"]["current_sweep"]["first_overheating_target_mpa"] == pytest.approx(20.0)
        assert payload["recipe"]["current_sweep"]["first_overheating_use_normal_current_end"] is False
        assert payload["recipe"]["current_sweep"]["first_overheating_current_end_mA"] == pytest.approx(90.0)

        window.spin_setup_preload_stress_mpa.setValue(5.0)
        window.spin_setup_preload_stable_s.setValue(0.0)
        window.spin_setup_zero_stable_s.setValue(0.0)
        window.spin_setup_slack_step_cap_stress_mpa.setValue(10.0)
        window.spin_current_sweep_target_end.setValue(25.0)
        window.spin_current_sweep_end_mA.setValue(5.0)
        window.check_current_sweep_hold_on_error.setChecked(False)
        window.check_current_sweep_first_overheating.setChecked(False)
        window.spin_current_sweep_first_overheating_target_mpa.setValue(75.0)
        window.check_current_sweep_first_overheating_use_normal_end.setChecked(True)
        window.spin_current_sweep_first_overheating_end_mA.setValue(10.0)
        window.check_current_sweep_return_target.setChecked(False)

        window._load_recipe_from_path(recipe_path)

        assert window.combo_recipe_mode.currentData() == mini_dma_mod.CURRENT_SWEEP_STRESS
        assert window.spin_setup_preload_stress_mpa.value() == pytest.approx(20.0)
        assert window.spin_setup_preload_stable_s.value() == pytest.approx(2.5)
        assert window.spin_setup_zero_stable_s.value() == pytest.approx(4.0)
        assert window.spin_setup_slack_step_cap_stress_mpa.value() == pytest.approx(75.0)
        assert window.spin_current_sweep_target_end.value() == pytest.approx(500.0)
        assert window.spin_current_sweep_end_mA.value() == pytest.approx(80.0)
        assert window.check_current_sweep_hold_on_error.isChecked() is True
        assert window.check_current_sweep_first_overheating.isChecked() is True
        assert window.spin_current_sweep_first_overheating_target_mpa.value() == pytest.approx(20.0)
        assert window.check_current_sweep_first_overheating_use_normal_end.isChecked() is False
        assert window.spin_current_sweep_first_overheating_end_mA.value() == pytest.approx(90.0)
        assert window.check_current_sweep_return_target.isChecked() is True
        assert "Loaded recipe" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_iso_stress_fatigue_recipe_round_trips_from_json(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    recipe_path = tmp_path / "iso-stress-fatigue_setup20MPa_stress150MPa_12cycles.recipe.json"

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_FATIGUE)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        window.spin_setup_preload_stress_mpa.setValue(20.0)
        window.spin_current_sweep_target_start.setValue(150.0)
        window.spin_current_sweep_target_end.setValue(999.0)
        window.spin_current_sweep_target_step.setValue(25.0)
        window.spin_current_sweep_target_ramp_rate.setValue(5.0)
        window.spin_current_sweep_fatigue_cycles.setValue(12)
        window.spin_current_sweep_start_mA.setValue(1.0)
        window.spin_current_sweep_end_mA.setValue(60.0)
        window.spin_current_sweep_step_mA.setValue(1.0)
        window.check_current_sweep_hold_on_error.setChecked(True)
        window.check_current_sweep_first_overheating.setChecked(True)

        window._save_recipe_to_path(recipe_path)
        payload = json.loads(recipe_path.read_text(encoding="utf-8"))
        current_sweep = payload["recipe"]["current_sweep"]
        assert payload["recipe"]["mode"] == mini_dma_mod.CURRENT_SWEEP_FATIGUE
        assert current_sweep["basis"] == mini_dma_mod.HSW_BASIS_STRESS_MPA
        assert current_sweep["target_start"] == pytest.approx(150.0)
        assert current_sweep["fatigue_cycles"] == 12
        assert current_sweep["first_overheating"] is True
        assert current_sweep["reverse_current"] is True

        payload["recipe"]["current_sweep"]["reverse_current"] = False
        recipe_path.write_text(json.dumps(payload), encoding="utf-8")

        window.combo_recipe_mode.setCurrentIndex(window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS))
        window.spin_current_sweep_target_start.setValue(50.0)
        window.spin_current_sweep_fatigue_cycles.setValue(1)
        window.spin_current_sweep_end_mA.setValue(5.0)
        window.check_current_sweep_hold_on_error.setChecked(False)
        window.check_current_sweep_first_overheating.setChecked(False)
        window.check_current_sweep_reverse_current.setChecked(False)

        window._load_recipe_from_path(recipe_path)

        assert window.combo_recipe_mode.currentData() == mini_dma_mod.CURRENT_SWEEP_FATIGUE
        assert window.spin_current_sweep_target_start.value() == pytest.approx(150.0)
        assert window.spin_current_sweep_fatigue_cycles.value() == 12
        assert window.spin_current_sweep_end_mA.value() == pytest.approx(60.0)
        assert window.check_current_sweep_hold_on_error.isChecked() is True
        assert window.check_current_sweep_first_overheating.isChecked() is True
        assert window.check_current_sweep_reverse_current.isChecked() is True
    finally:
        _close_test_window(window)


def test_elastocaloric_recipe_round_trips_from_json(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    recipe_path = tmp_path / "elastocaloric_setup10MPa_strain0-4pct_current50mA.recipe.json"

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.ELASTOCALORIC_EFFECT)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        window.spin_constant_current_start_target.setValue(0.5)
        window.spin_constant_current_end_target.setValue(4.25)
        window.spin_constant_current_move_speed_mm_s.setValue(6.0)
        window.spin_elastocaloric_stabilize_s.setValue(45.0)
        window.spin_constant_current_hold_s.setValue(7.0)
        window.spin_elastocaloric_release_record_s.setValue(9.0)
        window.spin_constant_current_start_mA.setValue(50.0)
        window.spin_constant_current_transition_stress_mpa.setValue(10.0)
        window.spin_constant_current_transition_rate_mA_s.setValue(1.5)
        window.spin_constant_current_transition_settle_s.setValue(2.0)
        window.check_constant_current_transition_hold_on_error.setChecked(True)

        window._save_recipe_to_path(recipe_path)
        payload = json.loads(recipe_path.read_text(encoding="utf-8"))
        elastocaloric = payload["recipe"]["elastocaloric_effect"]
        assert payload["recipe"]["mode"] == mini_dma_mod.ELASTOCALORIC_EFFECT
        assert elastocaloric["start_strain_pct"] == pytest.approx(0.5)
        assert elastocaloric["jump_strain_pct"] == pytest.approx(4.25)
        assert elastocaloric["jump_speed_mm_s"] == pytest.approx(6.0)
        assert elastocaloric["temperature_stabilize_s"] == pytest.approx(45.0)
        assert elastocaloric["record_after_jump_s"] == pytest.approx(7.0)
        assert elastocaloric["record_after_release_s"] == pytest.approx(9.0)
        assert elastocaloric["current_mA"] == pytest.approx(50.0)
        assert elastocaloric["transition_enabled"] is True
        assert elastocaloric["transition_stress_mpa"] == pytest.approx(10.0)
        assert elastocaloric["transition_rate_mA_s"] == pytest.approx(1.5)
        assert elastocaloric["transition_settle_s"] == pytest.approx(2.0)
        assert elastocaloric["transition_hold_on_error"] is True

        window.combo_recipe_mode.setCurrentIndex(window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS))
        window.spin_constant_current_start_target.setValue(0.0)
        window.spin_constant_current_end_target.setValue(1.0)
        window.spin_constant_current_move_speed_mm_s.setValue(0.5)
        window.spin_elastocaloric_stabilize_s.setValue(1.0)
        window.spin_constant_current_hold_s.setValue(1.0)
        window.spin_elastocaloric_release_record_s.setValue(1.0)
        window.spin_constant_current_start_mA.setValue(5.0)
        window.spin_constant_current_transition_stress_mpa.setValue(2.0)
        window.spin_constant_current_transition_rate_mA_s.setValue(0.1)
        window.spin_constant_current_transition_settle_s.setValue(0.0)
        window.check_constant_current_transition_hold_on_error.setChecked(False)

        window._load_recipe_from_path(recipe_path)

        assert window.combo_recipe_mode.currentData() == mini_dma_mod.ELASTOCALORIC_EFFECT
        assert window.spin_constant_current_start_target.value() == pytest.approx(0.5)
        assert window.spin_constant_current_end_target.value() == pytest.approx(4.25)
        assert window.spin_constant_current_move_speed_mm_s.value() == pytest.approx(6.0)
        assert window.spin_elastocaloric_stabilize_s.value() == pytest.approx(45.0)
        assert window.spin_constant_current_hold_s.value() == pytest.approx(7.0)
        assert window.spin_elastocaloric_release_record_s.value() == pytest.approx(9.0)
        assert window.spin_constant_current_start_mA.value() == pytest.approx(50.0)
        assert window.spin_constant_current_transition_stress_mpa.value() == pytest.approx(10.0)
        assert window.spin_constant_current_transition_rate_mA_s.value() == pytest.approx(1.5)
        assert window.spin_constant_current_transition_settle_s.value() == pytest.approx(2.0)
        assert window.check_constant_current_transition_hold_on_error.isChecked() is True
        assert window.spin_elastocaloric_stabilize_s.isVisibleTo(window.recipe_stack)
        assert "Loaded recipe" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_recipe_round_trips_disabled_return_target(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    recipe_path = tmp_path / "current_sweep_no_return.recipe.json"
    index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
    assert index >= 0

    try:
        window.combo_recipe_mode.setCurrentIndex(index)
        window.check_current_sweep_return_target.setChecked(False)

        window._save_recipe_to_path(recipe_path)
        payload = json.loads(recipe_path.read_text(encoding="utf-8"))
        assert payload["recipe"]["current_sweep"]["return_target"] is False

        window.check_current_sweep_return_target.setChecked(True)
        window._load_recipe_from_path(recipe_path)

        assert window.check_current_sweep_return_target.isChecked() is False
    finally:
        _close_test_window(window)


def test_current_sweep_settings_preserve_disabled_return_target(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
    assert index >= 0

    try:
        window.combo_recipe_mode.setCurrentIndex(index)
        window.check_current_sweep_return_target.setChecked(False)

        window._save_settings()

        assert window.check_current_sweep_return_target.isChecked() is False
        assert window.settings.value("current_sweep_return_target", True, type=bool) is False
    finally:
        _close_test_window(window)


def test_current_sweep_settings_load_disabled_return_target(tmp_path: Path, qtbot) -> None:
    settings = _test_settings()
    settings.clear()
    settings.setValue("current_sweep_return_target", False)
    settings.sync()

    window = _build_window(tmp_path, qtbot, preserve_settings=True)

    try:
        assert window.check_current_sweep_return_target.isChecked() is False
    finally:
        _close_test_window(window)


def test_provision_bench_configures_supply_tic_and_reports_status(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeSupply:
        def __init__(self) -> None:
            self.configured: list[tuple[int, float, float, bool]] = []
            self.selected = 0

        def is_connected(self) -> bool:
            return True

        def configure_channel(self, *, channel: int, voltage_v: float, current_a: float, output_on: bool) -> None:
            self.configured.append((channel, voltage_v, current_a, output_on))

        def select_channel(self, channel: int | None = None) -> None:
            self.selected = 3 if channel is None else channel

        def disconnect(self) -> None:
            return None

    class _FakeTic:
        def __init__(self) -> None:
            self.current_limits: list[float] = []
            self.step_modes: list[str] = []
            self.max_speed = 800_000
            self.max_accel = 40_000
            self.max_decel = 40_000
            self.motion_limit_calls: list[tuple[str, ...]] = []

        def set_step_mode(self, step_mode: str) -> None:
            self.step_modes.append(step_mode)

        def run(self, *args: str, timeout_s: float = 5.0) -> str:
            if args and args[0] == "--current":
                self.current_limits.append(float(args[1]))
            if args and args[0] == "--max-speed":
                self.motion_limit_calls.append(tuple(args))
                self.max_speed = int(args[1])
                self.max_accel = int(args[3])
                self.max_decel = int(args[5])
            return ""

        def get_status(self) -> str:
            return "\n".join(
                [
                    "VIN voltage: 12.00 V",
                    "Step mode: 1/8 step",
                    f"Max speed: {self.max_speed}",
                    f"Max acceleration: {self.max_accel}",
                    f"Max deceleration: {self.max_decel}",
                    "Current limit: 343 mA",
                    "Errors currently stopping the motor: None",
                ]
            )

    supply = _FakeSupply()
    tic = _FakeTic()
    window._supply_controller = supply  # type: ignore[assignment]
    window._build_tic_controller = lambda: tic  # type: ignore[method-assign]
    window._refresh_tic_status = lambda: setattr(window, "_tic_status_text", tic.get_status()) or True  # type: ignore[method-assign]
    window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_scale_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window.combo_current_sweep_supply_channel.setCurrentIndex(
        window.combo_current_sweep_supply_channel.findData(3)
    )
    window.combo_motor_supply_channel.setCurrentIndex(window.combo_motor_supply_channel.findData(2))

    try:
        ok = window._provision_bench_hardware()

        assert ok is True
        assert supply.configured == [(2, 12.0, 0.5, True)]
        assert supply.selected == 3
        assert tic.step_modes == ["8"]
        assert tic.current_limits == [343.0]
        assert tic.motion_limit_calls == [
            ("--max-speed", "10000000", "--max-accel", "100000", "--max-decel", "100000")
        ]
        assert "PASS: Motor supply CH2" in window.label_hardware_provisioning_status.text()
        assert "PASS: Tic step mode" in window.label_hardware_provisioning_status.text()
        assert "PASS: Tic current limit 343 mA" in window.label_hardware_provisioning_status.text()
        assert "PASS: Tic motion limits speed 10000000, accel 100000, decel 100000" in (
            window.label_hardware_provisioning_status.text()
        )
    finally:
        _close_test_window(window)


def test_restore_idle_tic_motion_limits_refreshes_after_dynamic_move(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeTic:
        def __init__(self) -> None:
            self.max_speed = 1_156_981
            self.max_accel = 100_000
            self.max_decel = 100_000
            self.calls: list[tuple[int, int, int]] = []

        def set_motion_limits(
            self,
            *,
            max_speed: int | None = None,
            max_accel: int | None = None,
            max_decel: int | None = None,
        ) -> None:
            self.max_speed = int(max_speed if max_speed is not None else self.max_speed)
            self.max_accel = int(max_accel if max_accel is not None else self.max_accel)
            self.max_decel = int(max_decel if max_decel is not None else self.max_decel)
            self.calls.append((self.max_speed, self.max_accel, self.max_decel))

        def get_status(self) -> str:
            return "\n".join(
                [
                    "VIN voltage: 12.00 V",
                    "Step mode: 1/8 step",
                    f"Max speed: {self.max_speed}",
                    f"Max acceleration: {self.max_accel}",
                    f"Max deceleration: {self.max_decel}",
                    "Current limit: 343 mA",
                    "Errors currently stopping the motor: None",
                ]
            )

    tic = _FakeTic()
    window._tic_status_text = "\n".join(
        [
            "VIN voltage: 12.00 V",
            "Step mode: 1/8 step",
            "Max speed: 10000000",
            "Max acceleration: 100000",
            "Max deceleration: 100000",
            "Current limit: 343 mA",
            "Errors currently stopping the motor: None",
        ]
    )
    window._build_tic_controller = lambda: tic  # type: ignore[method-assign]
    window._refresh_tic_status = lambda: setattr(window, "_tic_status_text", tic.get_status()) or True  # type: ignore[method-assign]

    try:
        window._restore_idle_tic_motion_limits()

        assert tic.calls == [(10_000_000, 100_000, 100_000)]
        assert "Restored Tic idle motion limits" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_recipe_preflight_blocks_start_when_tic_current_limit_fails(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        warnings: list[str] = []
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "warning",
            lambda _parent, _title, message: warnings.append(str(message)),
        )
        window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._ensure_scale_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]
        window._apply_tic_configured_step_mode = lambda: (True, "PASS")  # type: ignore[method-assign]
        window._apply_tic_current_limit = lambda: (False, "FAIL: Tic current limit could not be set.")  # type: ignore[method-assign]

        ok = window._preflight_recipe_hardware([mini_dma_mod.AutomationStep("move", target_mm=0.0)])

        assert ok is False
        assert "Tic current limit could not be set" in window.log_output.toPlainText()
        assert warnings and "Tic current limit could not be set" in warnings[-1]
    finally:
        _close_test_window(window)


def test_recipe_preflight_allows_existing_tic_current_limit_when_write_handle_is_busy(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    class _BusyTic:
        def set_current_limit_mA(self, _target_mA: float) -> int:
            raise RuntimeError(
                "Failed to open generic handle. Access is denied. "
                "Windows error code 0x5."
            )

    try:
        warnings: list[str] = []
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "warning",
            lambda _parent, _title, message: warnings.append(str(message)),
        )
        window._ensure_supply_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._ensure_scale_ready_for_recipe = lambda: True  # type: ignore[method-assign]
        window._apply_direct_hmp_bench_defaults_for_tic_preflight = lambda: None  # type: ignore[method-assign]
        window._apply_tic_configured_step_mode = lambda: (True, "PASS")  # type: ignore[method-assign]
        window._build_tic_controller = lambda: _BusyTic()  # type: ignore[method-assign]
        window._tic_status_text = "\n".join(
            [
                "VIN voltage: 12.00 V",
                "Step mode: 1/8 step",
                "Max speed: 10000000",
                "Max acceleration: 100000",
                "Max deceleration: 100000",
                "Current limit: 343 mA",
                "Errors currently stopping the motor: None",
            ]
        )

        ok = window._preflight_recipe_hardware([mini_dma_mod.AutomationStep("move", target_mm=0.0)])

        assert ok is True
        assert not warnings
        assert "Tic current limit already 343 mA" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_apply_tic_configured_step_mode_writes_selected_mode(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeTic:
        def __init__(self) -> None:
            self.step_modes: list[str] = []
            self.current_limits: list[float] = []

        def set_step_mode(self, step_mode: str) -> None:
            self.step_modes.append(step_mode)

        def set_current_limit_mA(self, target_mA: float) -> int:
            self.current_limits.append(target_mA)
            return mini_dma_mod.DEFAULT_TIC_CURRENT_LIMIT_MA

    tic = _FakeTic()
    try:
        window._build_tic_controller = lambda: tic  # type: ignore[method-assign]
        window._tic_status_text = "VIN voltage: 12.00 V\nErrors currently stopping the motor: None"
        window.spin_full_steps_per_mm.setValue(100.0)
        window.combo_tic_step_mode.setCurrentIndex(window.combo_tic_step_mode.findData("8"))

        ok, message = window._apply_tic_configured_step_mode()

        assert ok is True
        assert "Tic step mode 1/8 step" in message
        assert tic.step_modes == ["8"]
        assert tic.current_limits == []
        assert window.spin_steps_per_mm.value() == pytest.approx(800.0)
    finally:
        _close_test_window(window)


def test_max_load_limit_allows_relaxing_manual_move(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.check_max_load.setChecked(True)
    window.spin_max_load_g.setValue(20.0)
    window._latest_scale_value_g = -20.03
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 0.0
    window._current_position_steps = 0
    window._last_move_target_mm = 0.0
    window._manual_jog_uses_last_target = False
    window.spin_steps_per_mm.setValue(100.0)

    try:
        tensioning_move = window._move_to_position_mm(-0.1, manual_jog=True)
        relaxing_move = window._move_to_position_mm(0.1, manual_jog=True)
        _wait_for_tic_commands(window)

        assert tensioning_move is False
        assert relaxing_move is True
        assert controller.targets == [10]
        assert "Relaxing moves are still allowed" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_move_log_distinguishes_tic_unit_from_commanded_micrometers(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []
            self.max_speeds: list[int | None] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)
            self.max_speeds.append(max_speed)

    controller = _FakeController()
    _use_immediate_tic_dispatcher(window, controller)
    window.check_positive_motion_is_tension.setChecked(True)
    window.spin_steps_per_mm.setValue(800.0)
    window._current_position_mm = 0.0
    window._current_position_steps = 0
    window._last_commanded_position_steps = 0
    window._last_move_target_mm = 0.0

    try:
        assert window._move_to_position_mm(-0.00125, speed_mm_s=5.0) is True

        assert controller.targets == [-1]
        log_text = window.log_output.toPlainText()
        assert "Motor command -1.25 um (relax) at 5 mm/s -> target -0.0013 mm (-1 Tic unit)." in log_text
        assert "Motor step -1 um" not in log_text
        assert "Move command sent to" not in log_text
    finally:
        _close_test_window(window)


def test_raw_scale_display_limit_blocks_standard_moves_when_exceeded(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_raw_scale_limit_g.setValue(30.0)
    window._latest_scale_value_g = 30.05
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 0.0
    window._current_position_steps = 0
    window._last_move_target_mm = 0.0
    window._manual_jog_uses_last_target = False
    window.spin_steps_per_mm.setValue(100.0)

    try:
        pushing_down_move = window._move_to_position_mm(0.1, manual_jog=True)
        unloading_move = window._move_to_position_mm(-0.1, manual_jog=True)
        _wait_for_tic_commands(window)

        assert pushing_down_move is False
        assert unloading_move is False
        assert controller.targets == []
        assert "raw scale display" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_raw_scale_display_limit_blocks_raw_step_moves_when_exceeded(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[tuple[int, int | None]] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append((position_steps, max_speed))

    controller = _FakeController()
    _use_immediate_tic_dispatcher(window, controller)
    window.spin_raw_scale_limit_g.setValue(30.0)
    window._latest_scale_value_g = 30.05
    window._latest_scale_timestamp = time.time()
    window.spin_steps_per_mm.setValue(1000.0)
    window._current_position_steps = 1200
    window._current_position_mm = 1.2
    window._last_commanded_position_steps = 1200
    window._last_move_target_mm = 1.2

    try:
        positive_step = window._move_relative_raw_tic_steps(800, speed_steps_per_s=8.0)
        negative_step = window._move_relative_raw_tic_steps(-800, speed_steps_per_s=8.0)
        _wait_for_tic_commands(window)

        assert positive_step is False
        assert negative_step is False
        assert controller.targets == []
        assert "raw scale display" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_applied_load_limit_does_not_stop_active_recipe_when_exceeded(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
    window._refresh_supply_snapshot = lambda: None  # type: ignore[method-assign]
    stopped = False

    def _record_stop(**_kwargs: object) -> None:
        nonlocal stopped
        stopped = True

    window._stop_auto_ramp = _record_stop  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.check_max_load.setChecked(True)
    window.spin_max_load_g.setValue(20.0)
    window.spin_zero_load_scale_g.setValue(21.16)
    window._latest_scale_value_g = 0.80
    window._latest_scale_timestamp = time.time()
    window._automation_active = True
    window._session_active = True

    try:
        window._handle_status_timer()

        assert stopped is False
        assert window._automation_active is True
        assert "Automation stopped because applied load" not in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_applied_load_limit_blocks_tensioning_move_without_stopping_recipe(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append(position_steps)

    controller = _FakeController()
    _use_immediate_tic_dispatcher(window, controller)
    stopped = False

    def _record_stop(**_kwargs: object) -> None:
        nonlocal stopped
        stopped = True

    window._stop_auto_ramp = _record_stop  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.check_max_load.setChecked(True)
    window.spin_max_load_g.setValue(20.0)
    window.spin_zero_load_scale_g.setValue(21.16)
    window._latest_scale_value_g = 0.80
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 0.0
    window._current_position_steps = 0
    window._last_move_target_mm = 0.0
    window._manual_jog_uses_last_target = False
    window.spin_steps_per_mm.setValue(100.0)
    window._automation_active = True

    try:
        moved = window._move_to_position_mm(-0.1)
        _wait_for_tic_commands(window)

        assert moved is False
        assert stopped is False
        assert window._automation_active is True
        assert controller.targets == []
        assert "Relaxing moves are still allowed" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_applied_load_limit_halts_tensioning_motion_without_stopping_recipe(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeDispatcher:
        def __init__(self) -> None:
            self.halt_count = 0

        def halt_and_hold(self) -> None:
            self.halt_count += 1

        def wait_until_idle(self, *, timeout_s: float = 2.0) -> bool:
            return True

    dispatcher = _FakeDispatcher()
    window._build_tic_dispatcher = lambda: dispatcher  # type: ignore[method-assign]
    window._wait_for_tic_dispatcher = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
    window._refresh_supply_snapshot = lambda: None  # type: ignore[method-assign]
    stopped = False

    def _record_stop(**_kwargs: object) -> None:
        nonlocal stopped
        stopped = True

    window._stop_auto_ramp = _record_stop  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.check_max_load.setChecked(True)
    window.spin_max_load_g.setValue(20.0)
    window.spin_zero_load_scale_g.setValue(21.16)
    window._latest_scale_value_g = 0.80
    window._latest_scale_timestamp = time.time()
    window._last_move_direction = -1.0
    window._last_motion_command_time_s = time.time()
    window._last_tic_status_time_s = None
    window._automation_active = True
    window._session_active = True

    try:
        window._handle_status_timer()

        assert dispatcher.halt_count == 1
        assert stopped is False
        assert window._automation_active is True
        assert window._last_move_direction == 0.0
        assert "Applied-load limit reached" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_raw_scale_display_limit_halts_and_stops_recipe_immediately(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeDispatcher:
        def __init__(self) -> None:
            self.halt_count = 0

        def halt_and_hold(self) -> None:
            self.halt_count += 1

        def wait_until_idle(self, *, timeout_s: float = 2.0) -> bool:
            return True

    dispatcher = _FakeDispatcher()
    window._build_tic_dispatcher = lambda: dispatcher  # type: ignore[method-assign]
    window._wait_for_tic_dispatcher = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
    window._refresh_supply_snapshot = lambda: None  # type: ignore[method-assign]
    stop_calls: list[dict[str, object]] = []

    def _record_stop(**kwargs: object) -> None:
        stop_calls.append(kwargs)
        window._automation_active = False

    window._stop_auto_ramp = _record_stop  # type: ignore[method-assign]
    window.spin_raw_scale_limit_g.setValue(30.0)
    window._latest_scale_value_g = 30.05
    window._latest_scale_timestamp = time.time()
    window._last_move_direction = 1.0
    window._last_motion_command_time_s = time.time()
    window._automation_active = True
    window._session_active = True

    try:
        window._handle_status_timer()

        assert dispatcher.halt_count == 1
        assert stop_calls == [{"log_completion": False, "offer_recovery": False}]
        assert window._automation_active is False
        assert "Raw scale display safety stop" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_distribution_seek_rejects_stale_scale_readings(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._latest_scale_value_g = 12.0
    window._latest_scale_timestamp = time.time() - (
        mini_dma_mod.CLOSED_LOOP_STALE_SCALE_ABORT_AFTER_S + 5.0
    )

    called = False

    def _fail_if_called(_target_mm: float, **_kwargs: object) -> bool:
        nonlocal called
        called = True
        return True

    window._move_to_position_mm = _fail_if_called  # type: ignore[method-assign]

    try:
        with pytest.raises(RuntimeError, match="stale"):
            window._seek_distribution_target(
                mini_dma_mod.HSW_BASIS_LOAD_G,
                target_value=15.0,
                tolerance=0.5,
            )
        assert called is False
    finally:
        _close_test_window(window)


def test_distribution_seek_waits_through_brief_stale_scale_gap(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._latest_scale_value_g = 12.0
    window._latest_scale_timestamp = time.time() - (
        mini_dma_mod.STALE_SCALE_AFTER_S + 1.0
    )

    called = False

    def _fail_if_called(_target_mm: float, **_kwargs: object) -> bool:
        nonlocal called
        called = True
        return True

    window._move_to_position_mm = _fail_if_called  # type: ignore[method-assign]

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=15.0,
            tolerance=0.5,
        )

        assert reached is False
        assert called is False
        assert "temporarily stale" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_session_metadata_keeps_original_created_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_smoke")
    window._record_current_point = lambda: None  # type: ignore[method-assign]

    values = iter(
        [
            "2026-04-23 10:00:00",
            "2026-04-23 10:05:00",
            "2026-04-23 10:10:00",
            "2026-04-23 10:15:00",
        ]
    )

    def _fake_timestamp() -> str:
        try:
            return next(values)
        except StopIteration:
            return "2026-04-23 10:15:00"

    monkeypatch.setattr(mini_dma_mod, "_utc_timestamp", _fake_timestamp)

    try:
        window._start_session()
        assert window._session_json_path is not None
        first_payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))

        window._write_session_metadata()
        second_payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))

        assert first_payload["created_utc"] == second_payload["created_utc"]
        window._stop_session()
    finally:
        _close_test_window(window)


def test_session_metadata_records_source_control_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_git")
    window._record_current_point = lambda: None  # type: ignore[method-assign]

    replies = {
        ("branch", "--show-current"): "codex/test-branch\n",
        ("rev-parse", "HEAD"): "abc123\n",
        ("status", "--short"): " M data_logging/mini_dma_logger/mini_dma_logger.py\n",
        ("config", "--get", "remote.origin.url"): "https://example.test/repo.git\n",
    }

    def _fake_run(args: list[str], **_kwargs: object) -> object:
        class Result:
            returncode = 0
            stdout = replies[tuple(args[3:])]

        return Result()

    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    try:
        window._start_session()
        assert window._session_json_path is not None
        payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))

        assert payload["source_control"]["branch"] == "codex/test-branch"
        assert payload["source_control"]["commit"] == "abc123"
        assert payload["source_control"]["is_dirty"] is True
        assert payload["source_control"]["remote_url"] == "https://example.test/repo.git"
    finally:
        window._stop_session()
        _close_test_window(window)


def test_session_metadata_records_control_logic_version_and_fingerprint(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_control_logic")
    window._record_current_point = lambda: None  # type: ignore[method-assign]

    try:
        window._start_session()
        assert window._session_json_path is not None
        first_payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))
        first_logic = first_payload["control_logic"]

        assert first_logic["name"] == "mini_dma_control"
        assert first_logic["version"]
        assert first_logic["profile"] == "processed-center-response-gated-hold"
        assert first_logic["fingerprint"].startswith("sha256:")
        assert len(first_logic["fingerprint"]) == len("sha256:") + 64
        assert "current_hold_persistent_error_gate" in first_logic["features"]
        assert "current_hold_automatic_entry_gate" in first_logic["features"]
        assert "current_sweep_mechanical_load_loss_guard" not in first_logic["features"]
        assert "current_hold_recovery_tolerance_band" in first_logic["features"]
        assert "current_hold_retry_after_filter_window" in first_logic["features"]
        assert "conservative_current_hold_response_stiffness" in first_logic["features"]
        assert "current_hold_unstable_response_damps_to_single_motor_steps" in first_logic["features"]
        assert "current_hold_improving_recovery_scales_cautiously" in first_logic["features"]
        assert "current_hold_large_error_uses_geometry_base_cap_before_response" in first_logic["features"]
        assert "current_hold_response_stiffness_requires_error_improvement" in first_logic["features"]
        assert "current_hold_adaptive_cap_growth_is_response_earned" in first_logic["features"]
        assert "current_hold_adaptive_large_error_floor_scales_with_band" in first_logic["features"]
        assert "current_sweep_reverse_current_recipe_flag" in first_logic["features"]
        assert "control_constants" in first_logic["fingerprint_fields"]
        assert "current_hold_noise_sigma" in first_logic["fingerprint_fields"]

        old_fingerprint = first_logic["fingerprint"]
        window.spin_current_sweep_hold_noise_sigma.setValue(
            window.spin_current_sweep_hold_noise_sigma.value() + 1.0
        )
        window._write_session_metadata()
        second_payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))

        assert second_payload["control_logic"]["fingerprint"] != old_fingerprint
    finally:
        window._stop_session()
        _close_test_window(window)


def test_session_metadata_records_manual_recipe_stop_reason(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_manual_stop")
    window._record_current_point = lambda: None  # type: ignore[method-assign]
    window._ask_recovery_after_stop = lambda: None  # type: ignore[method-assign]

    try:
        window._start_session()
        window._automation_active = True
        window._automation_steps = [mini_dma_mod.AutomationStep("record")]
        window._automation_index = 0

        window._stop_auto_ramp(user_initiated=True)

        assert window._session_json_path is not None
        payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))
        assert payload["session_state"] == "finished"
        assert payload["stop"]["reason"] == "manual_recipe_stop"
        assert payload["stop"]["category"] == "operator"
        assert "Manual" in payload["stop"]["label"]
        assert "Manual recipe stop" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_session_metadata_records_recipe_completed_stop_reason(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_recipe_complete")
    window._record_current_point = lambda: None  # type: ignore[method-assign]

    try:
        window._start_session()
        window._stop_session(reason="recipe_completed", detail="Recipe completed.")

        assert window._session_json_path is not None
        payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))
        assert payload["session_state"] == "finished"
        assert payload["stop"]["reason"] == "recipe_completed"
        assert payload["stop"]["category"] == "normal"
        assert payload["stop"]["detail"] == "Recipe completed."
    finally:
        _close_test_window(window)


def test_stop_session_schedules_run_summary_generation(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_summary_generation")
    window._record_current_point = lambda: None  # type: ignore[method-assign]
    requested: list[tuple[Path, bool]] = []
    window._start_run_summary_generation = (  # type: ignore[method-assign]
        lambda run_dir, *, offer_cleanup=False: requested.append((run_dir, offer_cleanup))
    )

    try:
        window._start_session()
        window._stop_session(reason="recipe_completed", detail="Recipe completed.")

        assert window._session_base_path is not None
        assert requested == [(window._session_base_path.parent, True)]
    finally:
        _close_test_window(window)


def test_control_worker_error_finalizes_session_and_summary(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_worker_error")
    window._record_current_point = lambda: None  # type: ignore[method-assign]
    window._run_on_ui_thread = lambda callback: callback()  # type: ignore[method-assign]
    window._ask_recovery_after_stop = lambda: None  # type: ignore[method-assign]
    requested: list[tuple[Path, bool]] = []
    window._start_run_summary_generation = (  # type: ignore[method-assign]
        lambda run_dir, *, offer_cleanup=False: requested.append((run_dir, offer_cleanup))
    )

    try:
        window._start_session()
        window._automation_active = True
        window._automation_steps = [mini_dma_mod.AutomationStep("record", note="test")]
        window._automation_index = 0

        window._handle_automation_control_loop_error(OSError(22, "Invalid argument"))

        assert window._automation_active is False
        assert window._session_json_path is not None
        payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))
        assert payload["session_state"] == "finished"
        assert payload["stop"]["reason"] == "recipe_control_worker_error"
        assert payload["stop"]["category"] == "fault"
        assert "Invalid argument" in payload["stop"]["detail"]
        assert window._session_base_path is not None
        assert requested == [(window._session_base_path.parent, False)]
    finally:
        window._automation_active = False
        _close_test_window(window)


def test_session_metadata_does_not_replace_fault_stop_with_app_closed(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_fault_then_app_closed")
    window._record_current_point = lambda: None  # type: ignore[method-assign]

    try:
        window._start_session()
        window._mark_session_stop_reason(
            "mechanical_load_loss",
            detail="Current-sweep mechanical load loss detected.",
        )
        window._stop_session(reason="app_closed", detail="Application window closed while session was active.")

        assert window._session_json_path is not None
        payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))
        assert payload["session_state"] == "finished"
        assert payload["stop"]["reason"] == "mechanical_load_loss"
        assert payload["stop"]["detail"] == "Current-sweep mechanical load loss detected."
    finally:
        _close_test_window(window)


def test_session_stop_recovers_metadata_when_output_folder_was_moved(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_moved_folder")
    window._record_current_point = lambda: None  # type: ignore[method-assign]
    recovery_root = tmp_path / "recovered"
    monkeypatch.setattr(window, "_session_recovery_root", lambda: recovery_root, raising=False)

    try:
        window._start_session()
        assert window._session_json_path is not None
        window._session_points.append(
            mini_dma_mod.MeasurementPoint(
                elapsed_s=1.25,
                timestamp_utc="2026-05-26 09:00:00",
                raw_position_mm=0.0,
                position_mm=0.1,
                raw_load_g=21.5,
                load_g=0.3,
                preload_state=mini_dma_mod.PRELOAD_DISABLED,
                strain_pct=0.2,
                stress_mpa=12.3,
                current_set_mA=10.0,
                current_measured_mA=9.8,
                voltage_V=1.2,
                resistance_ohm=122.0,
                power_W=0.0118,
                automation_phase="current",
                automation_basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
                automation_target_value=50.0,
                plateau_index=1,
                plateau_label="Stress 50 MPa",
            )
        )
        window._session_json_path = tmp_path / "missing_output" / "metadata.json"

        window._stop_session(reason="recipe_control_stop", detail="metadata write failed")

        recovery_dirs = list(recovery_root.glob("MiniDMA_recovered_*"))
        assert len(recovery_dirs) == 1
        recovered_metadata = json.loads((recovery_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
        with (recovery_dirs[0] / "measurement.csv").open("r", encoding="utf-8", newline="") as handle:
            recovered_rows = list(csv.DictReader(handle))
        assert recovered_metadata["session_state"] == "finished"
        assert recovered_metadata["point_count"] == 1
        assert recovered_metadata["stop"]["reason"] == "recipe_control_stop"
        assert recovered_metadata["recovery"]["reason"] == "metadata_write_failed"
        assert len(recovered_rows) == 1
        assert recovered_rows[0]["stress_mpa"] == "12.300000"
        assert "Emergency session recovery saved" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_session_metadata_records_wire_break_stop_reason(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_wire_break")
    window._record_current_point = lambda: None  # type: ignore[method-assign]
    recovery_prompts: list[str] = []
    window._ask_wire_break_recovery_after_stop = recovery_prompts.append  # type: ignore[method-assign]

    try:
        window._start_session()
        window._automation_active = True
        window._supply_output_enabled = True
        window._supply_last_setpoint_mA = 100.0
        window._supply_snapshot = {
            "current_mA": 0.0,
            "voltage_V": float(window.spin_supply_voltage_limit.value()),
            "resistance_ohm": None,
            "power_W": None,
        }

        window._stop_for_wire_break()

        assert window._session_json_path is not None
        payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))
        assert payload["session_state"] == "finished"
        assert payload["stop"]["reason"] == "wire_break_or_contact_loss"
        assert payload["stop"]["category"] == "fault"
        assert "Wire break" in payload["stop"]["label"]
        assert recovery_prompts
    finally:
        _close_test_window(window)


def test_session_metadata_records_emergency_stop_reason(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_emergency_stop")
    window._record_current_point = lambda: None  # type: ignore[method-assign]
    window._disable_motor_supply_output = lambda: True  # type: ignore[method-assign]
    window._disable_supply_output = lambda: None  # type: ignore[method-assign]

    class _Dispatcher:
        def halt_and_hold(self) -> None:
            return

    window._build_tic_dispatcher = lambda: _Dispatcher()  # type: ignore[method-assign]
    window._wait_for_tic_dispatcher = lambda *_args, **_kwargs: True  # type: ignore[method-assign]

    try:
        window._start_session()
        window._automation_active = True
        window._automation_steps = [mini_dma_mod.AutomationStep("record")]
        window._automation_index = 0

        window._emergency_stop()

        assert window._session_json_path is not None
        payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))
        assert payload["session_state"] == "finished"
        assert payload["stop"]["reason"] == "emergency_stop"
        assert payload["stop"]["category"] == "operator"
        assert "Emergency" in payload["stop"]["label"]
    finally:
        _close_test_window(window)


def test_recipe_sample_header_tracks_sample_name(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_sample_name.setText("Ni51Fe26Ga21 156/2 s1")

        assert window.label_recipe_sample.text() == "Sample: Ni51Fe26Ga21 156/2 s1 | diameter 30 um"

        window.edit_sample_name.clear()

        assert window.label_recipe_sample.text() == "Sample: (unnamed sample) | diameter 30 um"
    finally:
        _close_test_window(window)


def test_length_setup_window_title_includes_sample_name(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_sample_name.setText("Ni50Fe27Ga23 10/4 calibration")

    try:
        window._show_length_setup_dialog()

        assert window._length_setup_dialog is not None
        assert "Ni50Fe27Ga23 10/4 calibration" in window._length_setup_dialog.windowTitle()
    finally:
        _close_test_window(window)


def test_prepare_session_files_can_save_as_next_run_without_replacing_existing(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("same_sample")
    for suffix in (".txt", ".csv", ".json", ".scale_raw.csv"):
        (tmp_path / f"same_sample{suffix}").write_text("old", encoding="utf-8")
    (tmp_path / "same_sample_run02.csv").write_text("old run 2", encoding="utf-8")
    window._ask_existing_output_action = (  # type: ignore[method-assign]
        lambda _paths: mini_dma_mod.OUTPUT_COLLISION_NEXT
    )

    try:
        (
            txt_handle,
            csv_handle,
            _csv_writer,
            raw_scale_handle,
            _raw_scale_writer,
            ir_temperature_handle,
            _ir_temperature_writer,
            control_trace_handle,
            _control_trace_writer,
            ui_telemetry_handle,
            _ui_telemetry_writer,
            setup_txt_handle,
            setup_csv_handle,
            _setup_csv_writer,
            txt_path,
            csv_path,
            json_path,
            raw_scale_path,
            ir_temperature_path,
            control_trace_path,
            ui_telemetry_path,
            setup_txt_path,
            setup_csv_path,
        ) = window._prepare_session_files(created_utc="2026-04-28 12:00:00")
        for handle in (
            txt_handle,
            csv_handle,
            raw_scale_handle,
            ir_temperature_handle,
            control_trace_handle,
            ui_telemetry_handle,
            setup_txt_handle,
            setup_csv_handle,
        ):
            handle.close()

        assert txt_path == tmp_path / "same_sample_run03" / "measurement.txt"
        assert csv_path == tmp_path / "same_sample_run03" / "measurement.csv"
        assert json_path == tmp_path / "same_sample_run03" / "metadata.json"
        assert raw_scale_path == tmp_path / "same_sample_run03" / "scale_raw.csv"
        assert ir_temperature_path == tmp_path / "same_sample_run03" / "ir_temperature.csv"
        assert control_trace_path == tmp_path / "same_sample_run03" / "control_trace.csv"
        assert ui_telemetry_path == tmp_path / "same_sample_run03" / "ui_telemetry.csv"
        assert setup_txt_path == tmp_path / "same_sample_run03" / "setup.txt"
        assert setup_csv_path == tmp_path / "same_sample_run03" / "setup.csv"
        assert window.edit_log_name.text() == "same_sample_run03"
        assert (tmp_path / "same_sample.txt").read_text(encoding="utf-8") == "old"
        assert (tmp_path / "same_sample_run02.csv").read_text(encoding="utf-8") == "old run 2"
    finally:
        _close_test_window(window)


def test_existing_output_message_names_sample_and_output_folder(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_sample_name.setText("Ni50Fe27Ga23 10/4 calibration")
    window.edit_log_name.setText("Ni50Fe27Ga23 10_4 calibration")
    paths = mini_dma_mod._session_paths_for_basename(tmp_path, "Ni50Fe27Ga23 10_4 calibration")

    try:
        message = window._current_session_identity_text(paths)

        assert "Sample: Ni50Fe27Ga23 10/4 calibration" in message
        assert "Base filename: Ni50Fe27Ga23 10_4 calibration" in message
        assert str(tmp_path / "Ni50Fe27Ga23 10_4 calibration") in message
    finally:
        _close_test_window(window)


def test_stale_output_base_filename_syncs_to_current_sample(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_name_composition.setText("Ni50Fe27Ga23")
    window.edit_name_wire.setText("10/4")
    window.edit_name_specimen.setText("calibration")
    window.edit_sample_name.setText("Ni50Fe27Ga23 10/4 calibration")
    window.edit_log_name.setText("Ni50Fe27Ga23 12_2 test")

    try:
        window._sync_stale_log_name_from_sample()

        assert window.edit_log_name.text() == "Ni50Fe27Ga23 10_4 calibration"
    finally:
        _close_test_window(window)


def test_stale_output_base_filename_syncs_when_only_condition_changed(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
    window.combo_recipe_mode.setCurrentIndex(index)
    window.edit_name_composition.setText("Ni50Fe27Ga23")
    window.edit_name_wire.setText("12/2")
    window.edit_name_condition.setText("cer cap")
    window.edit_sample_name.setText("Ni50Fe27Ga23 12/2 cer cap")
    window.edit_log_name.setText("Ni50Fe27Ga23 12_2 with glass iso-stress")

    try:
        window._sync_stale_log_name_from_sample()

        assert window.edit_log_name.text() == "Ni50Fe27Ga23 12_2 cer cap iso-stress"
    finally:
        _close_test_window(window)


def test_auto_output_base_filename_includes_current_sweep_recipe_type(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRAIN)
    window.combo_recipe_mode.setCurrentIndex(index)
    window.edit_name_composition.setText("Ni50Fe27Ga23")
    window.edit_name_wire.setText("11/1")
    window.edit_sample_name.setText("Ni50Fe27Ga23 11/1")
    window.edit_log_name.setText(mini_dma_mod.DEFAULT_LOG_BASENAME)

    try:
        window._sync_stale_log_name_from_sample()

        assert window.edit_log_name.text() == "Ni50Fe27Ga23 11_1 iso-strain"
    finally:
        _close_test_window(window)


def test_auto_output_base_filename_uses_iso_current_for_constant_current_recipe(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    index = window.combo_recipe_mode.findData(mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP)
    window.combo_recipe_mode.setCurrentIndex(index)
    window.edit_name_composition.setText("Ni50Fe27Ga23")
    window.edit_name_wire.setText("11/1")
    window.edit_sample_name.setText("Ni50Fe27Ga23 11/1")
    window.edit_log_name.setText(mini_dma_mod.DEFAULT_LOG_BASENAME)

    try:
        window._sync_stale_log_name_from_sample()

        assert window.edit_log_name.text() == "Ni50Fe27Ga23 11_1 iso-current"
    finally:
        _close_test_window(window)


def test_prepare_session_files_does_not_chain_run_suffixes(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("same_sample_run02")
    (tmp_path / "same_sample_run02").mkdir()
    (tmp_path / "same_sample_run03").mkdir()
    window._ask_existing_output_action = (  # type: ignore[method-assign]
        lambda _paths: mini_dma_mod.OUTPUT_COLLISION_NEXT
    )

    try:
        (
            txt_handle,
            csv_handle,
            _csv_writer,
            raw_scale_handle,
            _raw_scale_writer,
            ir_temperature_handle,
            _ir_temperature_writer,
            control_trace_handle,
            _control_trace_writer,
            ui_telemetry_handle,
            _ui_telemetry_writer,
            setup_txt_handle,
            setup_csv_handle,
            _setup_csv_writer,
            txt_path,
            *_paths,
        ) = window._prepare_session_files(created_utc="2026-04-28 12:03:00")
        for handle in (
            txt_handle,
            csv_handle,
            raw_scale_handle,
            ir_temperature_handle,
            control_trace_handle,
            ui_telemetry_handle,
            setup_txt_handle,
            setup_csv_handle,
        ):
            handle.close()

        assert txt_path == tmp_path / "same_sample_run04" / "measurement.txt"
        assert window.edit_log_name.text() == "same_sample_run04"
        assert not (tmp_path / "same_sample_run02_run02").exists()
    finally:
        _close_test_window(window)


def test_prepare_session_files_can_replace_existing_outputs(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_sample_name.setText("repeat sample")
    window.edit_log_name.setText("replace_sample")
    txt_path = tmp_path / "replace_sample" / "measurement.txt"
    txt_path.parent.mkdir()
    txt_path.write_text("old", encoding="utf-8")
    monkeypatch.setattr(
        mini_dma_mod,
        "_move_path_to_trash",
        lambda path: shutil.move(str(path), str(tmp_path / "trash_replace_sample")),
    )
    window._ask_existing_output_action = (  # type: ignore[method-assign]
        lambda _paths: mini_dma_mod.OUTPUT_COLLISION_REPLACE
    )

    try:
        (
            txt_handle,
            csv_handle,
            _csv_writer,
            raw_scale_handle,
            _raw_scale_writer,
            ir_temperature_handle,
            _ir_temperature_writer,
            control_trace_handle,
            _control_trace_writer,
            ui_telemetry_handle,
            _ui_telemetry_writer,
            setup_txt_handle,
            setup_csv_handle,
            _setup_csv_writer,
            returned_txt_path,
            *_paths,
        ) = window._prepare_session_files(created_utc="2026-04-28 12:05:00")
        for handle in (
            txt_handle,
            csv_handle,
            raw_scale_handle,
            ir_temperature_handle,
            control_trace_handle,
            ui_telemetry_handle,
            setup_txt_handle,
            setup_csv_handle,
        ):
            handle.close()

        assert returned_txt_path == txt_path
        assert (tmp_path / "trash_replace_sample" / "measurement.txt").read_text(encoding="utf-8") == "old"
        assert txt_path.read_text(encoding="utf-8").startswith("Displacement\t")
        assert window.edit_log_name.text() == "replace_sample"
    finally:
        _close_test_window(window)


def test_prepare_session_files_moves_replaced_output_to_trash_first(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_sample_name.setText("repeat sample")
    window.edit_log_name.setText("replace_sample")
    txt_path = tmp_path / "replace_sample" / "measurement.txt"
    txt_path.parent.mkdir()
    txt_path.write_text("old", encoding="utf-8")
    trash_dir = tmp_path / "trash"

    def _fake_move_to_trash(path: Path) -> None:
        trash_dir.mkdir()
        shutil.move(str(path), str(trash_dir / path.name))

    monkeypatch.setattr(mini_dma_mod, "_move_path_to_trash", _fake_move_to_trash)
    window._ask_existing_output_action = (  # type: ignore[method-assign]
        lambda _paths: mini_dma_mod.OUTPUT_COLLISION_REPLACE
    )

    try:
        (
            txt_handle,
            csv_handle,
            _csv_writer,
            raw_scale_handle,
            _raw_scale_writer,
            ir_temperature_handle,
            _ir_temperature_writer,
            control_trace_handle,
            _control_trace_writer,
            ui_telemetry_handle,
            _ui_telemetry_writer,
            setup_txt_handle,
            setup_csv_handle,
            _setup_csv_writer,
            returned_txt_path,
            *_paths,
        ) = window._prepare_session_files(created_utc="2026-04-28 12:05:00")
        for handle in (
            txt_handle,
            csv_handle,
            raw_scale_handle,
            ir_temperature_handle,
            control_trace_handle,
            ui_telemetry_handle,
            setup_txt_handle,
            setup_csv_handle,
        ):
            handle.close()

        assert returned_txt_path == txt_path
        assert (trash_dir / "replace_sample" / "measurement.txt").read_text(encoding="utf-8") == "old"
        assert txt_path.read_text(encoding="utf-8").startswith("Displacement\t")
        assert "Trash/Recycling Bin" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_prepare_session_files_preserves_replaced_output_when_trash_unavailable(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("replace_sample")
    txt_path = tmp_path / "replace_sample" / "measurement.txt"
    txt_path.parent.mkdir()
    txt_path.write_text("old", encoding="utf-8")
    monkeypatch.setattr(
        mini_dma_mod,
        "_move_path_to_trash",
        lambda _path: (_ for _ in ()).throw(OSError("trash disabled")),
    )
    window._ask_existing_output_action = (  # type: ignore[method-assign]
        lambda _paths: mini_dma_mod.OUTPUT_COLLISION_REPLACE
    )

    try:
        (
            txt_handle,
            csv_handle,
            _csv_writer,
            raw_scale_handle,
            _raw_scale_writer,
            ir_temperature_handle,
            _ir_temperature_writer,
            control_trace_handle,
            _control_trace_writer,
            ui_telemetry_handle,
            _ui_telemetry_writer,
            setup_txt_handle,
            setup_csv_handle,
            _setup_csv_writer,
            returned_txt_path,
            *_paths,
        ) = window._prepare_session_files(created_utc="2026-04-28 12:05:00")
        for handle in (
            txt_handle,
            csv_handle,
            raw_scale_handle,
            ir_temperature_handle,
            control_trace_handle,
            ui_telemetry_handle,
            setup_txt_handle,
            setup_csv_handle,
        ):
            handle.close()

        preserved = list(tmp_path.glob("replace_sample_replaced_*"))
        assert len(preserved) == 1
        assert (preserved[0] / "measurement.txt").read_text(encoding="utf-8") == "old"
        assert returned_txt_path == txt_path
        assert txt_path.read_text(encoding="utf-8").startswith("Displacement\t")
    finally:
        _close_test_window(window)


def test_session_start_no_longer_captures_zero_load_reference(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("no_start_capture_zero_load")
    window.spin_zero_load_scale_g.setValue(0.0)
    window._latest_scale_value_g = 21.2
    window._latest_scale_text = "21.200 g"
    window._latest_scale_timestamp = time.time()

    capture_called = False

    def _capture_should_not_run() -> bool:
        nonlocal capture_called
        capture_called = True
        return False

    window._capture_zero_load_scale_reference = _capture_should_not_run  # type: ignore[method-assign]

    try:
        window._start_session()

        assert window._session_active is True
        assert capture_called is False
        assert window.spin_zero_load_scale_g.value() == pytest.approx(0.0)
        assert "Zero-load scale reference set to 21.20000 g" not in window.log_output.toPlainText()
        window._stop_session()
    finally:
        _close_test_window(window)


def test_find_ticcmd_detects_localappdata_pololu_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_app_data = tmp_path / "LocalAppData"
    ticcmd_path = local_app_data / "Programs" / "Pololu" / "Tic" / "bin" / "ticcmd.exe"
    ticcmd_path.parent.mkdir(parents=True)
    ticcmd_path.write_bytes(b"")

    monkeypatch.setattr(mini_dma_mod.shutil, "which", lambda _name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert mini_dma_mod._find_ticcmd() == str(ticcmd_path)


def test_auto_detect_scale_port_applies_detected_settings(tmp_path: Path, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        monkeypatch.setattr(
            mini_dma_mod.list_ports,
            "comports",
            lambda: [
                SimpleNamespace(device="COM6", description="USB Serial A"),
                SimpleNamespace(device="COM3", description="USB Serial B"),
            ],
        )

        def _probe_scale(port_name: str):
            if port_name == "COM6":
                return {
                    "port": "COM6",
                    "baudrate": 9600,
                    "request_command": "\\x1bp",
                    "terminator": "",
                    "raw_text": "0.000 g",
                }
            return None

        monkeypatch.setattr(window, "_probe_scale_candidate", _probe_scale)

        window._refresh_scale_ports()
        detected = window._auto_detect_scale_port()

        assert detected is True
        assert window.combo_scale_port.currentData() == "COM6"
        assert window.combo_scale_baud.currentText() == "9600"
        assert window.edit_scale_request.text() == "\\x1bp"
        assert window.edit_scale_terminator.text() == ""
        assert window.spin_scale_interval.value() == 250
    finally:
        _close_test_window(window)


def test_fast_scale_auto_connect_prefers_ch340_scale_over_saved_prolific_port(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        monkeypatch.setattr(
            mini_dma_mod.list_ports,
            "comports",
            lambda: [
                SimpleNamespace(device="COM4", description="Prolific PL2303GT USB Serial COM Port"),
                SimpleNamespace(device="COM5", description="USB-SERIAL CH340"),
            ],
        )
        window.combo_scale_port.clear()
        window.combo_scale_port.addItem("COM4 - Prolific", "COM4")
        window.combo_scale_port.addItem("COM5 - CH340", "COM5")
        window.combo_scale_port.setCurrentIndex(window.combo_scale_port.findData("COM4"))
        probes: list[str] = []

        def _probe(port_name: str):
            probes.append(port_name)
            if port_name == "COM5":
                return {
                    "port": "COM5",
                    "baudrate": 256000,
                    "request_command": mini_dma_mod.KERN_KCP_SCALE_REQUEST,
                    "terminator": mini_dma_mod.KERN_KCP_SCALE_TERMINATOR,
                    "raw_text": "S S      57.13 g",
                }
            return None

        monkeypatch.setattr(window, "_fast_probe_scale_candidate", _probe)

        detected = window._fast_auto_detect_scale_port()

        assert detected is True
        assert probes == ["COM4", "COM5"]
        assert window.combo_scale_port.currentData() == "COM5"
        assert window.combo_scale_baud.currentText() == "256000"
        assert window.spin_scale_interval.value() == 50
    finally:
        _close_test_window(window)


def test_auto_detect_supply_port_applies_detected_settings(tmp_path: Path, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        monkeypatch.setattr(
            mini_dma_mod.list_ports,
            "comports",
            lambda: [
                SimpleNamespace(device="COM6", description="USB Serial A"),
                SimpleNamespace(device="COM3", description="USB Serial B"),
            ],
        )

        def _probe_supply(port_name: str):
            if port_name == "COM3":
                return {
                    "port": "COM3",
                    "baudrate": 115200,
                    "profile_id": "hmp4030",
                    "idn_text": "HAMEG,HMP4030,022982747,HW50020001/SW2.50",
                }
            return None

        monkeypatch.setattr(window, "_probe_supply_candidate", _probe_supply)

        window._refresh_supply_ports()
        detected = window._auto_detect_supply_port()

        assert detected is True
        assert window.combo_supply_port.currentData() == "COM3"
        assert window.combo_supply_baud.currentText() == "115200"
        assert window.combo_supply_profile.currentData() == "hmp4030"
    finally:
        _close_test_window(window)


def test_auto_detect_tic_sets_path_and_serial(tmp_path: Path, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeTicController:
        def __init__(self, command_path: str = "ticcmd", device_serial: str = "") -> None:
            self.command_path = command_path
            self.device_serial = device_serial

        def run(self, *args: str, timeout_s: float = 5.0) -> str:
            assert args == ("--list",)
            return "00501366,         Tic T500 Stepper Motor Controller            \n"

    try:
        monkeypatch.setattr(mini_dma_mod, "_find_ticcmd", lambda: r"C:\\tools\\ticcmd.exe")
        monkeypatch.setattr(mini_dma_mod, "TicController", _FakeTicController)
        window.edit_ticcmd_path.setText("")

        detected = window._auto_detect_tic()

        assert detected is True
        assert window.edit_ticcmd_path.text() == r"C:\\tools\\ticcmd.exe"
        assert window.edit_tic_serial.text() == "00501366"
    finally:
        _close_test_window(window)


def test_current_sweep_saved_20_mpa_hold_cap_migrates_to_30_mpa(tmp_path: Path, qtbot) -> None:
    settings = _test_settings()
    snapshot = _snapshot_settings()
    settings.clear()
    settings.setValue("current_sweep_servo_defaults_version", 3)
    settings.setValue("current_sweep_hold_correction_stress_mpa", 20.0)
    settings.sync()

    window = _build_window(tmp_path, qtbot, preserve_settings=True)

    try:
        assert window.spin_current_sweep_hold_correction_stress_mpa.value() == pytest.approx(30.0)
    finally:
        _close_test_window(window)
        _restore_settings(snapshot)


def test_current_sweep_saved_overlarge_hold_cap_migrates_to_30_mpa(tmp_path: Path, qtbot) -> None:
    settings = _test_settings()
    snapshot = _snapshot_settings()
    settings.clear()
    settings.setValue("current_sweep_servo_defaults_version", 4)
    settings.setValue("current_sweep_hold_correction_stress_mpa", 100.0)
    settings.sync()

    window = _build_window(tmp_path, qtbot, preserve_settings=True)

    try:
        assert window.spin_current_sweep_hold_correction_stress_mpa.value() == pytest.approx(30.0)
    finally:
        _close_test_window(window)
        _restore_settings(snapshot)


def test_saved_old_graph_interval_migrates_to_500_ms(tmp_path: Path, qtbot) -> None:
    settings = _test_settings()
    snapshot = _snapshot_settings()
    settings.clear()
    settings.setValue("graph_refresh_interval_ms", 1000)
    settings.sync()

    window = _build_window(tmp_path, qtbot, preserve_settings=True)

    try:
        assert window.spin_graph_interval.value() == 500
    finally:
        _close_test_window(window)
        _restore_settings(snapshot)


def test_apply_name_fields_uses_display_wire_and_file_safe_wire_token(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_name_composition.setText("Ni51Fe26Ga21")
        window.edit_name_wire.setText("156_2")
        window.edit_name_specimen.setText("s1")
        window.edit_name_condition.setText("preload test")
        recipe_index = window.combo_recipe_mode.findData("ramp")
        if recipe_index >= 0:
            window.combo_recipe_mode.setCurrentIndex(recipe_index)

        window._apply_name_fields()

        assert window.edit_sample_name.text() == "Ni51Fe26Ga21 156/2 s1 preload test"
        assert window.edit_log_name.text() == "Ni51Fe26Ga21 156_2 s1 preload test"
    finally:
        _close_test_window(window)


def test_hardware_tare_sends_escape_t_and_clears_software_offset(tmp_path: Path, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _build_window(tmp_path, qtbot)
    window._load_offset_g = 1.234
    window.spin_zero_load_scale_g.setValue(21.2)
    window.combo_scale_port.clear()
    window.combo_scale_port.addItem("COM6", "COM6")
    window.combo_scale_port.setCurrentIndex(0)
    if window.combo_scale_baud.findText("9600") >= 0:
        window.combo_scale_baud.setCurrentText("9600")

    written: list[bytes] = []

    class _FakeSerial:
        def __init__(self, *args, **kwargs) -> None:
            self.rts = False
            self.dtr = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def reset_input_buffer(self) -> None:
            return None

        def reset_output_buffer(self) -> None:
            return None

        def write(self, payload: bytes) -> None:
            written.append(payload)

        def flush(self) -> None:
            return None

        def readline(self) -> bytes:
            return b"0.000 g \\r\\n"

    try:
        monkeypatch.setattr(mini_dma_mod.serial, "Serial", _FakeSerial)

        assert window._tare_scale_hardware() is True
        assert written and written[0] == b"\x1bt"
        assert window._load_offset_g == 0.0
        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
    finally:
        _close_test_window(window)


def test_current_sweep_predictive_correction_uses_stress_cap_not_feedback_interval(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(30.56)
    window.spin_diameter.setValue(0.0137)
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_scale_interval.setValue(250)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_max_correction_strain_pct.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window._calibrated_stiffness_g_per_mm = 1.56
    window._calibrated_stiffness_length_mm = 30.56
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        target_value=50.0,
        plateau_index=1,
        note="1",
    )

    try:
        seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_STRESS_MPA, 50.0)
        correction_mm = window._predictive_seek_step_mm(
            mini_dma_mod.HSW_BASIS_STRESS_MPA,
            error_value=-300.0,
            tolerance=0.5,
            seek_key=seek_key,
        )
        speed_mm_s = window._current_sweep_dynamic_speed_cap_mm_s()
        command_mm = window._seek_command_step_mm(
            correction_mm,
            speed_mm_s,
            basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        )

        sensitivity = mini_dma_mod.stress_mpa_from_load_g(1.56, window.spin_diameter.value())
        assert sensitivity is not None
        assert correction_mm == pytest.approx(10.0 / sensitivity)
        assert correction_mm < 30.56 * 0.05
        assert command_mm == pytest.approx(correction_mm)
        assert command_mm > window._motor_step_mm()
    finally:
        _close_test_window(window)


def test_mini_dma_defaults_to_provisional_microstep_steps_per_mm(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert window.spin_full_steps_per_mm.value() == pytest.approx(100.0)
        assert window.combo_tic_step_mode.currentData() == "8"
        assert window.spin_steps_per_mm.value() == pytest.approx(800.0)
        tooltip = window.spin_steps_per_mm.toolTip()
        assert "Tic units/mm" in tooltip
        assert "100 full motor steps/mm" in tooltip
        assert "1/8" in tooltip
        assert "800 Tic units/mm" in tooltip
    finally:
        _close_test_window(window)


def test_apply_tic_step_mode_preserves_physical_mm_position(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        class _FakeController:
            def __init__(self) -> None:
                self.step_modes: list[str] = []
                self.positions: list[int] = []
                self.halted = False

            def set_step_mode(self, step_mode: str) -> None:
                self.step_modes.append(step_mode)

            def halt_and_hold(self) -> None:
                self.halted = True

            def set_current_position(self, position_steps: int) -> None:
                self.positions.append(position_steps)

        controller = _FakeController()
        _use_immediate_tic_dispatcher(window, controller)
        window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
        window._refresh_tic_status = lambda: True  # type: ignore[method-assign]
        window.spin_full_steps_per_mm.setValue(100.0)
        window.combo_tic_step_mode.setCurrentIndex(window.combo_tic_step_mode.findData("8"))
        window.spin_steps_per_mm.setValue(800.0)
        window._current_position_steps = 4800
        window._current_position_mm = 4800 / 800.0
        window._effective_position_mm = window._current_position_mm
        window._last_effective_move_target_mm = window._current_position_mm
        window._last_move_target_mm = window._current_position_mm
        window._last_commanded_position_steps = 4800
        window.combo_tic_step_mode.setCurrentIndex(window.combo_tic_step_mode.findData("4"))

        assert window._apply_tic_step_mode(confirm=False) is True

        expected_mm = 4800 / 800.0
        expected_steps = round(expected_mm * 400.0)
        assert controller.halted is True
        assert controller.step_modes == ["4"]
        assert controller.positions == [expected_steps]
        assert window.spin_steps_per_mm.value() == pytest.approx(400.0)
        assert window._current_position_mm == pytest.approx(expected_mm)
        assert window._effective_position_mm == pytest.approx(expected_mm)
        assert window._last_move_target_mm == pytest.approx(expected_mm)
        assert window._last_commanded_position_steps == expected_steps
    finally:
        _close_test_window(window)


def test_apply_tic_step_mode_keeps_requested_mode_after_status_refresh(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        class _FakeController:
            def __init__(self) -> None:
                self.step_modes: list[str] = []
                self.positions: list[int] = []

            def set_step_mode(self, step_mode: str) -> None:
                self.step_modes.append(step_mode)

            def halt_and_hold(self) -> None:
                return None

            def set_current_position(self, position_steps: int) -> None:
                self.positions.append(position_steps)

        controller = _FakeController()
        _use_immediate_tic_dispatcher(window, controller)
        window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
        monkeypatch.setattr(
            mini_dma_mod.QtWidgets.QMessageBox,
            "question",
            lambda *_args, **_kwargs: mini_dma_mod.QtWidgets.QMessageBox.StandardButton.Yes,
        )
        window.spin_full_steps_per_mm.setValue(100.0)
        window.combo_tic_step_mode.setCurrentIndex(window.combo_tic_step_mode.findData("4"))
        window.spin_steps_per_mm.setValue(800.0)
        window._current_position_steps = 800
        window._current_position_mm = 1.0
        window._last_commanded_position_steps = 800

        refresh_calls = 0

        def _status_refresh_resets_to_live_mode() -> bool:
            nonlocal refresh_calls
            refresh_calls += 1
            mode = "8" if refresh_calls == 1 else "4"
            units = 800.0 if mode == "8" else 400.0
            window.combo_tic_step_mode.setCurrentIndex(window.combo_tic_step_mode.findData(mode))
            window.spin_steps_per_mm.setValue(units)
            return True

        window._refresh_tic_status = _status_refresh_resets_to_live_mode  # type: ignore[method-assign]

        assert window._apply_tic_step_mode(confirm=True) is True

        assert controller.step_modes == ["4"]
        assert controller.positions == [400]
        assert window.combo_tic_step_mode.currentData() == "4"
    finally:
        _close_test_window(window)


def test_refresh_tic_status_updates_step_mode_and_tic_units(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    try:
        class _FakeController:
            def get_status(self) -> str:
                return "\n".join(
                    [
                        "VIN voltage: 12.00 V",
                        "Operation state: Normal",
                        "Current position: 400",
                        "Step mode: 1/4 step",
                        "Max speed: 40000000",
                        "Max acceleration: 100000",
                        "Max deceleration: 100000",
                        "Current limit: 343 mA",
                        "Errors currently stopping the motor: None",
                    ]
                )

        window._build_tic_controller = lambda: _FakeController()  # type: ignore[method-assign]
        window.spin_full_steps_per_mm.setValue(100.0)
        window.combo_tic_step_mode.setCurrentIndex(window.combo_tic_step_mode.findData("8"))
        window.spin_steps_per_mm.setValue(800.0)

        assert window._refresh_tic_status() is True

        assert window.combo_tic_step_mode.currentData() == "4"
        assert window.spin_steps_per_mm.value() == pytest.approx(400.0)
        assert window._current_position_mm == pytest.approx(1.0)
        assert "1/4 step" in window.label_tic_settings_summary.text()
        assert "400 Tic units/mm" in window.label_tic_settings_summary.text()
    finally:
        _close_test_window(window)


def test_legacy_default_steps_per_mm_migrates_to_provisional_microstep_value(tmp_path: Path, qtbot) -> None:
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("steps_per_mm", 100.0)
    settings.setValue("motor_defaults_version", 1)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    qtbot.addWidget(window)

    try:
        assert window.spin_steps_per_mm.value() == pytest.approx(800.0)
    finally:
        _close_test_window(window)
        _restore_settings(snapshot)


def test_custom_steps_per_mm_survives_motor_defaults_migration(tmp_path: Path, qtbot) -> None:
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("steps_per_mm", 1000.0)
    settings.setValue("motor_defaults_version", 1)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    qtbot.addWidget(window)

    try:
        assert window.spin_steps_per_mm.value() == pytest.approx(1000.0)
    finally:
        _close_test_window(window)
        _restore_settings(snapshot)


def test_motor_step_calibration_dialog_shows_progress_and_points(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._show_motor_step_calibration_dialog(
            total_moves=5,
            signed_increment_steps=800,
            speed_steps_per_s=8.0,
        )

        assert window._motor_step_calibration_dialog is not None
        assert window._motor_step_calibration_dialog.isVisible()
        assert window._motor_step_calibration_progress is not None
        assert window._motor_step_calibration_progress.maximum() == 500
        assert window._motor_step_calibration_status_label is not None
        assert "baseline" in window._motor_step_calibration_status_label.text().lower()

        window._update_motor_step_calibration_dialog(
            "Moving calibration point 1/5.",
            completed_moves=0,
            total_moves=5,
            active_move_fraction=0.5,
            detail="Current 5227 steps -> target 6027 steps.",
        )

        assert window._motor_step_calibration_progress.value() == 50
        assert "Moving calibration point 1/5" in window._motor_step_calibration_status_label.text()
        assert window._motor_step_calibration_detail_label is not None
        assert "6027 steps" in window._motor_step_calibration_detail_label.text()

        point = mini_dma_mod.MotorStepCalibrationPoint(
            point_index=1,
            timestamp_utc="2026-05-05 10:00:00",
            tic_position_steps=6027,
            entered_displacement_mm=1.23,
            move_command_steps=800,
            move_speed_steps_per_s=8.0,
        )
        window._append_motor_step_calibration_dialog_point(point)

        assert window._motor_step_calibration_points_view is not None
        assert "reading 1.230000 mm" in window._motor_step_calibration_points_view.toPlainText()
    finally:
        window._close_motor_step_calibration_dialog()
        _close_test_window(window)


def test_motor_step_calibration_run_shows_dialog_before_baseline_prompt(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window._ensure_tic_ready_for_recipe = lambda: True  # type: ignore[method-assign]
    window._stop_tic_keepalive = lambda: None  # type: ignore[method-assign]

    def _cancel_baseline(*_args: object, **_kwargs: object) -> tuple[float, bool]:
        assert window._motor_step_calibration_dialog is not None
        assert window._motor_step_calibration_dialog.isVisible()
        assert window._motor_step_calibration_active is True
        return 0.0, False

    monkeypatch.setattr(mini_dma_mod.QtWidgets.QInputDialog, "getDouble", _cancel_baseline)

    try:
        window._run_motor_step_calibration()

        assert window._motor_step_calibration_dialog is None
        assert window._motor_step_calibration_active is False
    finally:
        window._close_motor_step_calibration_dialog()
        _close_test_window(window)


def test_motor_step_calibration_report_fits_external_gauge_points() -> None:
    points = [
        mini_dma_mod.MotorStepCalibrationPoint(
            point_index=0,
            timestamp_utc="2026-05-05 09:00:00",
            tic_position_steps=1200,
            entered_displacement_mm=1.25,
        ),
        mini_dma_mod.MotorStepCalibrationPoint(
            point_index=1,
            timestamp_utc="2026-05-05 09:02:00",
            tic_position_steps=2000,
            entered_displacement_mm=2.25,
            move_command_steps=800,
            move_speed_steps_per_s=8.0,
        ),
        mini_dma_mod.MotorStepCalibrationPoint(
            point_index=2,
            timestamp_utc="2026-05-05 09:04:00",
            tic_position_steps=2800,
            entered_displacement_mm=3.25,
            move_command_steps=800,
            move_speed_steps_per_s=8.0,
        ),
    ]

    report = mini_dma_mod.motor_step_calibration_report_from_points(points)

    assert report["status"] == "ok"
    assert report["recommended_steps_per_mm"] == pytest.approx(800.0)
    assert report["movement_direction"] == "external_reading_increases_with_positive_tic_steps"
    assert report["r2"] == pytest.approx(1.0)
    assert report["max_residual_mm"] == pytest.approx(0.0)
    assert report["point_estimates"][0]["steps_per_mm_from_baseline"] == pytest.approx(800.0)


def test_motor_step_calibration_report_uses_absolute_slope_for_reversed_gauge() -> None:
    points = [
        mini_dma_mod.MotorStepCalibrationPoint(
            point_index=0,
            timestamp_utc="2026-05-05 09:00:00",
            tic_position_steps=0,
            entered_displacement_mm=5.0,
        ),
        mini_dma_mod.MotorStepCalibrationPoint(
            point_index=1,
            timestamp_utc="2026-05-05 09:02:00",
            tic_position_steps=800,
            entered_displacement_mm=4.0,
        ),
        mini_dma_mod.MotorStepCalibrationPoint(
            point_index=2,
            timestamp_utc="2026-05-05 09:04:00",
            tic_position_steps=1600,
            entered_displacement_mm=3.0,
        ),
    ]

    report = mini_dma_mod.motor_step_calibration_report_from_points(points)

    assert report["status"] == "ok"
    assert report["recommended_steps_per_mm"] == pytest.approx(800.0)
    assert report["signed_steps_per_mm"] == pytest.approx(-800.0)
    assert report["movement_direction"] == "external_reading_decreases_with_positive_tic_steps"


def test_motor_step_calibration_writes_csv_and_json_log(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    points = [
        mini_dma_mod.MotorStepCalibrationPoint(
            point_index=0,
            timestamp_utc="2026-05-05 09:00:00",
            tic_position_steps=100,
            entered_displacement_mm=0.5,
        ),
        mini_dma_mod.MotorStepCalibrationPoint(
            point_index=1,
            timestamp_utc="2026-05-05 09:02:00",
            tic_position_steps=900,
            entered_displacement_mm=1.5,
            move_command_steps=800,
            move_speed_steps_per_s=8.0,
        ),
    ]
    report = mini_dma_mod.motor_step_calibration_report_from_points(points)

    try:
        csv_path, json_path = window._write_motor_step_calibration_log(
            points,
            report,
            move_increment_steps=800,
            move_speed_steps_per_s=8.0,
            applied_to_settings=False,
        )

        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        metadata = json.loads(json_path.read_text(encoding="utf-8"))

        assert rows[1]["relative_tic_steps"] == "800"
        assert rows[1]["relative_displacement_mm"] == "1.000000"
        assert rows[1]["estimated_steps_per_mm_from_baseline"] == "800.000000"
        assert metadata["report"]["recommended_steps_per_mm"] == pytest.approx(800.0)
        assert metadata["applied_to_settings"] is False
    finally:
        _close_test_window(window)


def test_motor_step_calibration_move_uses_raw_tic_steps_not_current_calibration(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.targets: list[tuple[int, int | None]] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append((position_steps, max_speed))

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    _use_immediate_tic_dispatcher(window, controller)
    window.spin_steps_per_mm.setValue(1000.0)
    window._current_position_steps = 1200
    window._current_position_mm = 1.2
    window._last_commanded_position_steps = 1200
    window._last_move_target_mm = 1.2

    try:
        moved = window._move_relative_raw_tic_steps(800, speed_steps_per_s=8.0)
        _wait_for_tic_commands(window)

        assert moved is True
        assert controller.targets == [(2000, 80000)]
        assert window._last_commanded_position_steps == 2000
        assert window._last_move_target_mm == pytest.approx(2.0)
    finally:
        _close_test_window(window)


def test_current_sweep_dynamic_speed_cap_uses_strain_rate_and_stage_cap(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(30.56)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)

    try:
        assert window._current_sweep_dynamic_speed_cap_mm_s() == pytest.approx(30.56 * 0.15)

        window.spin_current_sweep_correction_rate_pct_s.setValue(100.0)
        assert window._current_sweep_dynamic_speed_cap_mm_s() == pytest.approx(5.0)

        window.spin_current_sweep_target_speed_mm_s.setValue(3.0)
        assert window._current_sweep_dynamic_speed_cap_mm_s() == pytest.approx(3.0)
    finally:
        _close_test_window(window)


def test_active_current_sweep_motion_context_uses_strain_rate_cap(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_initial_length.setValue(30.56)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD

    try:
        assert window._motion_speed_for_current_context(manual_jog=False) == pytest.approx(30.56 * 0.15)
    finally:
        _close_test_window(window)


def test_gated_seek_command_speed_compensates_for_feedback_dead_time(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_scale_interval.setValue(250)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(100.0)
    window.spin_initial_length.setValue(30.56)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD

    try:
        speed = window._seek_feedback_compensated_speed_mm_s(
            1.0,
            0.5,
            basis=mini_dma_mod.HSW_BASIS_LOAD_G,
            cruise_mode=False,
        )

        assert speed == pytest.approx(2.5)
    finally:
        _close_test_window(window)


def test_gated_seek_command_speed_uses_hard_cap_when_dead_time_dominates(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_scale_interval.setValue(250)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_initial_length.setValue(30.56)
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD

    try:
        speed = window._seek_feedback_compensated_speed_mm_s(
            1.0,
            0.1,
            basis=mini_dma_mod.HSW_BASIS_LOAD_G,
            cruise_mode=False,
        )

        assert speed == pytest.approx(30.56 * 0.15)
    finally:
        _close_test_window(window)


def test_move_duration_includes_tic_acceleration_and_deceleration(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)
    window._tic_status_text = "\n".join(
        [
            "Max acceleration: 100000",
            "Max deceleration: 100000",
        ]
    )

    try:
        # 100000 Tic accel units = 1000 microsteps/s^2 = 1.25 mm/s^2 at 800 units/mm.
        assert window._move_duration_s(1.0, 1.0) == pytest.approx(1.8)
        assert window._move_duration_s(0.1, 1.0) == pytest.approx(2.0 * math.sqrt(0.1 / 1.25))
    finally:
        _close_test_window(window)


def test_move_duration_falls_back_to_linear_without_tic_acceleration(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert window._move_duration_s(1.0, 0.5) == pytest.approx(2.0)
    finally:
        _close_test_window(window)


def test_seek_travel_interval_uses_tic_acceleration_limit(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.spin_steps_per_mm.setValue(800.0)
    window.spin_scale_interval.setValue(250)
    window._tic_status_text = "\n".join(
        [
            "Max acceleration: 100000",
            "Max deceleration: 100000",
        ]
    )

    try:
        # At 1.25 mm/s^2, 250 ms from rest reaches only 0.5*a*t^2 = 0.0390625 mm.
        assert window._seek_travel_during_interval_mm(
            1.0,
            mini_dma_mod.HSW_BASIS_LOAD_G,
        ) == pytest.approx(0.0390625)
        assert window._seek_speed_limited_step_mm(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            1.0,
        ) == pytest.approx(0.0390625)
    finally:
        _close_test_window(window)


def test_setup_zero_return_does_not_accept_high_residual_inside_inflated_tolerance(
    tmp_path: Path,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(21.17)
    window.spin_steps_per_mm.setValue(100.0)
    window._seek_live_stiffness_g_per_mm = 46.0
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="seek",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=0.0,
        note="setup_return_zero",
    )
    window._latest_scale_value_g = 20.71
    window._latest_scale_timestamp = time.time()
    window._current_position_mm = 36.82
    window._effective_position_mm = 36.82
    window._last_move_target_mm = 36.82
    window._last_effective_move_target_mm = 36.82
    window._current_position_steps = 3682
    window._last_commanded_position_steps = 3682
    window._setup_return_zero_start_point_index = 0
    window._length_setup_start_monotonic = time.monotonic()

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.0,
            tolerance=mini_dma_mod.SERVO_AUTO_TOLERANCE_LOAD_G,
        )

        assert reached is False
        assert moves
    finally:
        _close_test_window(window)


def test_far_load_seek_can_cruise_on_fresh_inflight_scale_sample(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[tuple[float, bool, float | None]] = []

    def _capture_move(target_mm: float, **kwargs: object) -> bool:
        moves.append(
            (
                target_mm,
                bool(kwargs.get("chain_from_last_target")),
                kwargs.get("speed_mm_s"),  # type: ignore[arg-type]
            )
        )
        window._last_move_target_mm = target_mm
        window._last_motion_command_time_s = time.time()
        window._last_motion_expected_complete_time_s = time.time() + 10.0
        return True

    window._move_to_position_mm = _capture_move  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_scale_interval.setValue(250)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_initial_length.setValue(30.56)
    window._calibrated_stiffness_g_per_mm = 1.0
    window._calibrated_stiffness_length_mm = 30.56
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._set_automation_context(
        phase="current",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=5.0,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_LOAD_G, 5.0)
    sample_time_s = time.time()
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = sample_time_s - 0.3
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = sample_time_s
    window._last_motion_command_time_s = sample_time_s - 0.1
    window._last_motion_expected_complete_time_s = sample_time_s + 10.0
    window._last_move_target_mm = 0.0
    window._last_effective_move_target_mm = 0.0

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=mini_dma_mod.SERVO_AUTO_TOLERANCE_LOAD_G,
        )

        assert reached is False
        assert moves
        assert moves[-1][1] is True
        assert window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] == pytest.approx(sample_time_s)
    finally:
        _close_test_window(window)


def test_near_load_seek_waits_for_post_move_feedback_even_with_new_scale_sample(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_scale_interval.setValue(250)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_initial_length.setValue(30.56)
    window._calibrated_stiffness_g_per_mm = 1.0
    window._calibrated_stiffness_length_mm = 30.56
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._set_automation_context(
        phase="current",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=0.2,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(mini_dma_mod.HSW_BASIS_LOAD_G, 0.2)
    window._seek_last_error_by_key[seek_key] = -0.1
    sample_time_s = time.time()
    window._seek_last_scale_timestamp_by_clock[(seek_key[0], seek_key[1])] = sample_time_s - 0.3
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = sample_time_s
    window._last_motion_command_time_s = sample_time_s - 0.1
    window._last_motion_expected_complete_time_s = sample_time_s + 10.0
    window._last_move_target_mm = 0.0
    window._last_effective_move_target_mm = 0.0

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=0.2,
            tolerance=mini_dma_mod.SERVO_AUTO_TOLERANCE_LOAD_G,
        )

        assert reached is False
        assert moves == []
        assert "post-move scale feedback" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_load_target_ramp_waits_for_new_scale_sample_even_as_target_changes(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    moves: list[float] = []
    window._move_to_position_mm = lambda target_mm, **_kwargs: moves.append(target_mm) or True  # type: ignore[method-assign]
    window.check_tension_load_positive.setChecked(True)
    window.check_positive_motion_is_tension.setChecked(False)
    window.spin_zero_load_scale_g.setValue(0.0)
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_scale_interval.setValue(250)
    window.spin_current_sweep_target_speed_mm_s.setValue(5.0)
    window.spin_current_sweep_correction_rate_pct_s.setValue(15.0)
    window.spin_initial_length.setValue(30.56)
    window._calibrated_stiffness_g_per_mm = 1.0
    window._calibrated_stiffness_length_mm = 30.56
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._set_automation_context(
        phase="target_ramp",
        basis=mini_dma_mod.HSW_BASIS_LOAD_G,
        target_value=5.0,
        plateau_index=1,
    )
    sample_time_s = time.time()
    window._latest_scale_value_g = 0.0
    window._latest_scale_timestamp = sample_time_s

    try:
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=mini_dma_mod.SERVO_AUTO_TOLERANCE_LOAD_G,
        ) is False
        assert len(moves) == 1

        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.1,
            tolerance=mini_dma_mod.SERVO_AUTO_TOLERANCE_LOAD_G,
        ) is False
        assert len(moves) == 1
        assert "new scale sample" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)
