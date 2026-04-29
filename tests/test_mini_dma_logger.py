from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import csv
import importlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip(
    "PyQt6.QtWidgets",
    reason="Qt widgets backend is unavailable",
    exc_type=ImportError,
)

from PyQt6 import QtCore, QtGui, QtWidgets

mini_dma_mod = importlib.import_module(
    "data_logging.mini_dma_logger.mini_dma_logger"
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _snapshot_settings() -> dict[str, object]:
    settings = QtCore.QSettings("microwire", "mini_dma_logger")
    return {key: settings.value(key) for key in settings.allKeys()}


def _restore_settings(snapshot: dict[str, object]) -> None:
    settings = QtCore.QSettings("microwire", "mini_dma_logger")
    settings.clear()
    for key, value in snapshot.items():
        settings.setValue(key, value)
    settings.sync()


def _build_window(tmp_path: Path, qtbot) -> mini_dma_mod.MainWindow:
    _ensure_app()
    snapshot = _snapshot_settings()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)
    window.check_zero_position_on_start.setChecked(False)
    window.check_tare_on_start.setChecked(False)
    window.check_hardware_tare_on_start.setChecked(False)
    window.spin_zero_load_scale_g.setValue(0.0)
    return window


def _close_test_window(window: mini_dma_mod.MainWindow) -> None:
    snapshot = getattr(window, "_test_settings_snapshot", None)
    window.close()
    _ensure_app().processEvents()
    if isinstance(snapshot, dict):
        _restore_settings(snapshot)


def test_zero_load_reference_defaults_to_measured_hanging_weight(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = QtCore.QSettings("microwire", "mini_dma_logger")
    settings.clear()
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
        assert window.check_hardware_tare_on_start.isChecked() is False
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
    window.spin_setup_preload_ramp_rate_mpa_s.setValue(1.0)
    window.spin_setup_zero_stable_s.setValue(1.0)
    window.spin_current_sweep_target_start.setValue(0.0)
    window.spin_current_sweep_target_end.setValue(10.0)
    window.spin_current_sweep_target_step.setValue(10.0)
    window.spin_current_sweep_interval.setValue(250)

    try:
        steps, summary, interval_ms = window._build_automation_recipe()

        assert interval_ms == 250
        assert "length setup" in summary
        assert steps[0].action == "ramp_target"
        assert steps[0].basis == mini_dma_mod.HSW_BASIS_STRESS_MPA
        assert steps[0].target_end_value == pytest.approx(10.0)
        actions = [step.action for step in steps]
        assert "measure_length_prompt" in actions
        assert "apply_length_setup" in actions
        assert "start_session" in actions
        prompt_index = actions.index("measure_length_prompt")
        apply_index = actions.index("apply_length_setup")
        session_index = actions.index("start_session")
        first_recipe_index = actions.index("set_current")
        assert prompt_index < apply_index < session_index < first_recipe_index
        preload_step = next(step for step in steps if step.note == "setup_preload")
        assert preload_step.action == "ramp_target"
        assert preload_step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA
        assert preload_step.target_end_value == pytest.approx(10.0)
        assert preload_step.target_ramp_rate_value_s == pytest.approx(1.0)
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
    window.spin_current_sweep_interval.setValue(250)
    window.spin_current_sweep_log_interval.setValue(500)


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
    window.spin_calibration_interval.setValue(250)
    window.check_positive_motion_is_tension.setChecked(True)

    try:
        steps, summary, interval_ms = window._build_automation_recipe()

        assert interval_ms == 250
        assert "calibration" in summary
        assert window.combo_recipe_mode.itemText(mode_index) == "Calibration"
        actions = [step.action for step in steps]
        assert steps[0].action == "ramp_target"
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


def test_calibration_recipe_includes_mandatory_length_setup(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CALIBRATION)
    assert mode_index >= 0
    window.combo_recipe_mode.setCurrentIndex(mode_index)
    window.spin_setup_preload_stress_mpa.setValue(10.0)
    window.spin_setup_preload_ramp_rate_mpa_s.setValue(1.0)
    window.spin_setup_zero_stable_s.setValue(1.0)
    window.spin_calibration_baseline_s.setValue(1.0)
    window.spin_calibration_start_load_g.setValue(0.25)
    window.spin_calibration_end_load_g.setValue(0.5)
    window.spin_calibration_load_step_g.setValue(0.25)
    window.spin_calibration_interval.setValue(250)

    try:
        steps, summary, interval_ms = window._build_automation_recipe()

        assert interval_ms == 250
        assert "mandatory length setup" in summary
        actions = [step.action for step in steps]
        assert steps[0].action == "ramp_target"
        assert steps[0].note == "setup_preload"
        assert "measure_length_prompt" in actions
        assert "apply_length_setup" in actions
        assert "start_session" in actions
        assert actions.index("measure_length_prompt") < actions.index("apply_length_setup")
        assert actions.index("apply_length_setup") < actions.index("start_session") < actions.index("calibration_record")
    finally:
        _close_test_window(window)


def test_calibration_defaults_are_safe_for_microwire_startup(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = QtCore.QSettings("microwire", "mini_dma_logger")
    settings.clear()
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
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
    settings = QtCore.QSettings("microwire", "mini_dma_logger")
    settings.clear()
    settings.setValue("recipe_mode", mini_dma_mod.CALIBRATION_COPPER)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
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
    settings = QtCore.QSettings("microwire", "mini_dma_logger")
    settings.clear()
    settings.setValue("calibration_defaults_version", 2)
    settings.setValue("calibration_start_load_g", 1.0)
    settings.setValue("calibration_end_load_g", 5.0)
    settings.setValue("calibration_load_step_g", 1.0)
    settings.setValue("calibration_preload_speed_mm_s", 1.0)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
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
    ramp_index = window.combo_recipe_mode.findData("ramp")
    assert ramp_index >= 0
    window.combo_recipe_mode.setCurrentIndex(ramp_index)

    try:
        moved = window._move_to_position_mm(2.0)

        assert moved is True
        assert controller.target_steps == 200
        assert controller.max_speed == 1000000
        assert window._current_position_mm == pytest.approx(1.25)
        assert window._current_position_steps == 125
        assert window._last_move_target_mm == pytest.approx(2.0)
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
    window._current_position_steps = 0
    window._current_position_mm = 0.0
    window._last_move_target_mm = 0.0
    window._manual_jog_uses_last_target = False
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_jog_mm.setValue(0.1)

    try:
        window._jog_relative(-1.0)
        window._jog_relative(-1.0)
        window._jog_relative(1.0)

        assert controller.targets == [-10, -20, -10]
        assert window._last_move_target_mm == pytest.approx(-0.1)
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
    window._current_position_steps = 0
    window._current_position_mm = 0.0
    window._last_move_target_mm = 0.0
    window._manual_jog_uses_last_target = False
    window.spin_steps_per_mm.setValue(100.0)
    window.spin_jog_mm.setValue(0.01)
    window.spin_motion_speed_mm_s.setValue(1.0)

    try:
        window._jog_relative(-1.0)
        window._jog_relative(-1.0)
        window._jog_relative(-1.0)

        assert controller.targets == [-1, -13, -25]
    finally:
        _close_test_window(window)


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

        assert "Estimated duration: 8.3 min" in window.label_recipe_estimate.text()
        assert window.recipe_progress.maximum() > 100
        assert window.recipe_progress.value() == 0
        assert "idle" in window.recipe_progress.format()
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
        assert expected_ticks > len(steps)

        window._preflight_recipe_hardware = lambda _steps: True  # type: ignore[method-assign]
        window._start_session = lambda **_kwargs: setattr(window, "_session_active", True)  # type: ignore[method-assign]

        window._start_auto_ramp()

        assert window._automation_total_steps == expected_ticks
        assert window.recipe_progress.maximum() == expected_ticks
        assert window._automation_completed_ticks == 0
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
    settings = QtCore.QSettings("microwire", "mini_dma_logger")
    settings.clear()
    settings.setValue("name_composition", "Ni50Fe27Ga23")
    settings.setValue("name_wire", "12/2")
    settings.setValue("name_condition", "test")
    settings.setValue("builder_project_path", str(project_path))
    settings.setValue("diameter_mm", 0.03)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
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
        assert window.spin_diameter.value() == pytest.approx(0.0191)
        assert "Imported" in window.label_project_status.text()
        assert "diameter 0.01910 mm" in window.label_project_status.text()
        assert "border" not in window.spin_diameter.styleSheet()
    finally:
        _close_test_window(window)


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
        window.edit_name_composition.setText("Ni50Fe27Ga23")
        window.edit_name_wire.setText("12/2")
        window.spin_diameter.setValue(0.03)
        window.spin_setup_preload_stress_mpa.setValue(10.0)
        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.spin_current_sweep_target_start.setValue(10.0)
        window.spin_current_sweep_target_ramp_rate.setValue(1.0)
        window._update_recipe_mode_ui()

        assert "Sample: Ni50Fe27Ga23 12/2" in window.label_recipe_sample.text()
        assert "diameter 0.03 mm" in window.label_recipe_sample.text()
        assert "0.7208 g" in window.label_setup_preload_stress_equiv.text()
        assert "0.07208 g/s" in window.label_setup_preload_ramp_equiv.text()
        assert "0.7208 g" in window.label_current_target_start_equiv.text()
        assert "0.07208 g/s" in window.label_current_target_ramp_equiv.text()
        assert "palette(mid)" not in window.label_current_target_start_equiv.styleSheet()

        mode_index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_LOAD)
        assert mode_index >= 0
        window.combo_recipe_mode.setCurrentIndex(mode_index)
        window.spin_current_sweep_target_start.setValue(
            mini_dma_mod.load_g_from_stress_mpa(10.0, window.spin_diameter.value())
        )
        window._update_recipe_mode_ui()

        assert window.label_current_target_start_equiv.text().endswith("MPa")
        assert float(window.label_current_target_start_equiv.text().split()[0]) == pytest.approx(10.0, rel=2e-4)
    finally:
        _close_test_window(window)


def test_setup_preload_target_ramp_uses_setup_stage_speed(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.spin_setup_stage_speed_mm_s.setValue(1.25)
        window.spin_calibration_speed_mm_s.setValue(0.05)
        window._automation_active = True
        window._automation_name = mini_dma_mod.CALIBRATION
        window._set_automation_context(phase="target_ramp", basis=mini_dma_mod.HSW_BASIS_STRESS_MPA)
        assert window._motion_speed_for_current_context(manual_jog=False) == pytest.approx(1.25)
    finally:
        _close_test_window(window)


def test_length_setup_dialog_contains_live_graph_and_records_setup_points(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window._show_length_setup_dialog()

        assert window._length_setup_canvas is not None
        assert window._length_setup_figure is not None

        window._latest_scale_value_g = 21.5
        window._latest_scale_timestamp = time.time()
        window.spin_zero_load_scale_g.setValue(21.2)
        window.check_tension_load_positive.setChecked(False)
        window._current_position_mm = -0.2
        window._record_length_setup_point()

        assert len(window._length_setup_points) == 1
        point = window._length_setup_points[0]
        assert point.load_g == pytest.approx(0.3)
        assert point.stress_mpa is not None
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

        assert window.spin_current_sweep_nudge_mm.isHidden() is False
        assert window.spin_current_sweep_balance_speed_mm_s.isHidden() is False
        assert window.check_hardware_tare_on_start.isHidden() is False
        assert window.button_scale_connect.text() in {"Connect scale", "Disconnect scale"}
        assert window.button_scale_tare.text() == "Capture zero-load"
        assert window.button_advanced_software_tare.isVisible() is False
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
    settings = QtCore.QSettings("microwire", "mini_dma_logger")
    settings.setValue("ticcmd_path", str(tmp_path / "missing_ticcmd.exe"))
    settings.setValue("jog_mm", 0.0001)
    settings.sync()
    monkeypatch.setattr(mini_dma_mod, "_find_ticcmd", lambda: str(discovered))

    window = _build_window(tmp_path, qtbot)

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


def test_controlled_current_sweep_defaults_match_copper_test_recipe(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        index = window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_LOAD)
        assert index >= 0
        window.combo_recipe_mode.setCurrentIndex(index)
        _set_copper_current_sweep_defaults(window)

        steps, summary, interval_ms = window._build_automation_recipe()

        record_steps = [step for step in steps if step.action == "record"]
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
        assert record_steps[-1].target_value == pytest.approx(0.0)
        assert {step.target_value for step in current_sweep_steps} == {0.0, 3.0, 6.0, 9.0}
        assert current_steps[0].current_mA == pytest.approx(1.0)
        assert max(step.current_end_mA for step in current_sweep_steps if step.current_end_mA is not None) == pytest.approx(3.0)
        assert target_ramps[-1].target_end_value == pytest.approx(0.0)
        assert "iso-load current sweep" in summary.lower()
        assert "mA/s" in summary
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

        assert controller.targets == [50]
        assert controller.keepalive_count == 1
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

        txt_path = tmp_path / "positive_tension_log.txt"
        csv_path = tmp_path / "positive_tension_log.csv"

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

        rows = list(csv.DictReader((tmp_path / "scale_buffered_session.csv").open(encoding="utf-8", newline="")))
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
            csv.DictReader((tmp_path / "scale_buffered_session.scale_raw.csv").open(encoding="utf-8", newline=""))
        )
        assert [row["raw_load_g"] for row in raw_rows] == ["21.000000", "20.800000", "20.600000"]
        assert [row["applied_load_g"] for row in raw_rows] == ["0.200000", "0.400000", "0.600000"]

        metadata = json.loads((tmp_path / "scale_buffered_session.json").read_text(encoding="utf-8"))
        assert metadata["logging"]["log_interval_ms"] == 500
        assert metadata["logging"]["raw_scale_sidecar"] == "scale_buffered_session.scale_raw.csv"
        assert metadata["logging"]["raw_scale_sample_count"] == 3
    finally:
        _close_test_window(window)


def test_current_sweep_control_and_log_intervals_restore_independently(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = QtCore.QSettings("microwire", "mini_dma_logger")
    settings.clear()
    settings.setValue("current_sweep_interval_ms", 375)
    settings.setValue("current_sweep_log_interval_ms", 875)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        assert window.spin_current_sweep_interval.value() == 375
        assert window.spin_current_sweep_log_interval.value() == 875
        window.spin_current_sweep_interval.setValue(250)
        window.spin_current_sweep_log_interval.setValue(500)
        window._save_settings()
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

        rows = list(csv.DictReader((tmp_path / "positive_tension_magnitude_log.csv").open(encoding="utf-8", newline="")))
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

        rows = list(csv.DictReader((tmp_path / "positive_displacement_log.csv").open(encoding="utf-8", newline="")))
        assert rows[-1]["raw_position_mm"] == "-0.500000"
        assert rows[-1]["position_mm"] == "0.500000"
    finally:
        _close_test_window(window)


def test_emergency_stop_turns_off_current_halts_tic_and_stops_session(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("emergency_stop")
    window._record_current_point = lambda: None  # type: ignore[method-assign]

    class _FakeSupply:
        def __init__(self) -> None:
            self.off_count = 0

        def is_connected(self) -> bool:
            return True

        def disconnect(self) -> None:
            return None

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

    try:
        window._start_session()
        window._automation_active = True
        window._auto_ramp_timer.start(100)

        assert window.button_emergency_stop.text() == "EMERGENCY STOP"
        assert "background-color: #b91c1c" in window.button_emergency_stop.styleSheet()

        window._emergency_stop()

        assert supply.off_count >= 1
        assert tic.halted is True
        assert window._supply_output_enabled is False
        assert window._supply_last_setpoint_mA == pytest.approx(0.0)
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
        window._latest_scale_timestamp = time.time()
        window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )

        assert controller.targets == [90, 90]
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
        assert window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        ) is False

        assert targets == [pytest.approx(0.9), pytest.approx(1.025)]
        assert "Overshoot detected" in window.log_output.toPlainText()
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

        window._latest_scale_value_g = -6.0
        window._latest_scale_timestamp = time.time()
        window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )

        assert controller.targets == [90, 106]
        assert "backlash take-up" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_current_sweep_seek_uses_recipe_balancing_speed(tmp_path: Path, qtbot) -> None:
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

        assert reached is False
        assert controller.target_steps == 9980
        assert controller.max_speed == 5_000_000
    finally:
        _close_test_window(window)


def test_current_sweep_target_ramp_uses_target_stage_speed(tmp_path: Path, qtbot) -> None:
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
    window.spin_steps_per_mm.setValue(10000.0)
    window.spin_current_sweep_nudge_mm.setValue(0.1)
    window.spin_current_sweep_balance_speed_mm_s.setValue(0.05)
    window.spin_current_sweep_target_speed_mm_s.setValue(1.0)

    try:
        reached = window._seek_distribution_target(
            mini_dma_mod.HSW_BASIS_LOAD_G,
            target_value=5.0,
            tolerance=0.25,
        )

        assert reached is False
        assert controller.target_steps == 9000
        assert controller.max_speed == 100_000_000
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
        for _ in range(6):
            assert window._seek_distribution_target(
                mini_dma_mod.HSW_BASIS_LOAD_G,
                target_value=3.0,
                tolerance=0.25,
            ) is False

        assert len(moves) == 6
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

    try:
        window._start_session()
        window._automation_active = True
        window._automation_steps = [mini_dma_mod.AutomationStep("record")]
        window._automation_index = 1
        window._automation_total_steps = 1
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD

        window._handle_auto_ramp_tick()

        assert window._automation_active is True
        assert window._automation_name == mini_dma_mod.RECOVERY_POSITION
        assert window._session_active is False
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
        rows = list(csv.DictReader((tmp_path / "zero_current_resistance.csv").open(encoding="utf-8", newline="")))
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

    try:
        ok = window._preflight_recipe_hardware([mini_dma_mod.AutomationStep("move", target_mm=0.0)])

        assert ok is True
        assert supply.configured == [(2, 12.0, 1.5, True)]
        assert supply.selected_anneal == 1
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

        assert tensioning_move is False
        assert relaxing_move is True
        assert controller.targets == [10]
        assert "Relaxing moves are still allowed" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_distribution_seek_rejects_stale_scale_readings(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._latest_scale_value_g = 12.0
    window._latest_scale_timestamp = time.time() - (
        mini_dma_mod.STALE_SCALE_AFTER_S + 5.0
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


def test_recipe_sample_header_tracks_sample_name(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_sample_name.setText("Ni51Fe26Ga21 156/2 s1")

        assert window.label_recipe_sample.text() == "Sample: Ni51Fe26Ga21 156/2 s1 | diameter 0.03 mm"

        window.edit_sample_name.clear()

        assert window.label_recipe_sample.text() == "Sample: (unnamed sample) | diameter 0.03 mm"
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
            txt_path,
            csv_path,
            json_path,
            raw_scale_path,
        ) = window._prepare_session_files(created_utc="2026-04-28 12:00:00")
        for handle in (txt_handle, csv_handle, raw_scale_handle):
            handle.close()

        assert txt_path.name == "same_sample_run03.txt"
        assert csv_path.name == "same_sample_run03.csv"
        assert json_path.name == "same_sample_run03.json"
        assert raw_scale_path.name == "same_sample_run03.scale_raw.csv"
        assert window.edit_log_name.text() == "same_sample_run03"
        assert (tmp_path / "same_sample.txt").read_text(encoding="utf-8") == "old"
        assert (tmp_path / "same_sample_run02.csv").read_text(encoding="utf-8") == "old run 2"
    finally:
        _close_test_window(window)


def test_prepare_session_files_can_replace_existing_outputs(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_sample_name.setText("repeat sample")
    window.edit_log_name.setText("replace_sample")
    txt_path = tmp_path / "replace_sample.txt"
    txt_path.write_text("old", encoding="utf-8")
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
            returned_txt_path,
            *_paths,
        ) = window._prepare_session_files(created_utc="2026-04-28 12:05:00")
        for handle in (txt_handle, csv_handle, raw_scale_handle):
            handle.close()

        assert returned_txt_path == txt_path
        assert txt_path.read_text(encoding="utf-8").startswith("Displacement\t")
        assert window.edit_log_name.text() == "replace_sample"
    finally:
        _close_test_window(window)


def test_session_start_can_capture_zero_load_reference_without_remote_tare(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("capture_zero_load")
    window.check_hardware_tare_on_start.setChecked(True)
    window._latest_scale_value_g = 21.2
    window._latest_scale_text = "21.200 g"
    window._latest_scale_timestamp = time.time()

    remote_tare_called = False

    def _remote_tare_should_not_run() -> bool:
        nonlocal remote_tare_called
        remote_tare_called = True
        return False

    window._tare_scale_hardware = _remote_tare_should_not_run  # type: ignore[method-assign]

    try:
        window._start_session()

        assert window._session_active is True
        assert remote_tare_called is False
        assert window.spin_zero_load_scale_g.value() == pytest.approx(21.2)
        assert "Zero-load scale reference set to 21.20000 g" in window.log_output.toPlainText()
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
    finally:
        _close_test_window(window)
