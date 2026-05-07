from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import csv
import dataclasses
import importlib
import json
import math
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

TEST_QSETTINGS_ROOT = Path(
    os.environ.get("PYTEST_QSETTINGS_ROOT", "artifacts/test-qsettings")
)
TEST_QSETTINGS_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["MINI_DMA_QSETTINGS_INI_DIR"] = str(TEST_QSETTINGS_ROOT)

mini_dma_mod = importlib.import_module(
    "data_logging.mini_dma_logger.mini_dma_logger"
)


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
    window.check_hardware_tare_on_start.setChecked(False)
    window.spin_zero_load_scale_g.setValue(0.0)
    return window


def _close_test_window(window: mini_dma_mod.MainWindow) -> None:
    snapshot = getattr(window, "_test_settings_snapshot", None)
    window.close()
    _ensure_app().processEvents()
    if isinstance(snapshot, dict):
        _restore_settings(snapshot)


def _wait_for_tic_commands(window: mini_dma_mod.MainWindow) -> None:
    dispatcher = getattr(window, "_tic_command_dispatcher", None)
    if dispatcher is not None:
        assert dispatcher.wait_until_idle(timeout_s=2.0)


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

        for index, (x_key, y_left_key, y_right_key) in enumerate(USER_DASHBOARD_PLOTS):
            _set_plot_tile(window, index, x_key, y_left_key, y_right_key)
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


def test_dashboard_plot_choices_persist_immediately(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.sync()

    try:
        window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
        qtbot.addWidget(window)

        _set_plot_tile(window, 0, "elapsed_s", "position_mm", "strain_pct")
        _ensure_app().processEvents()

        assert _saved_plot_tile_values(0) == {
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
        window.check_hardware_tare_on_start.setChecked(False)
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
    window.spin_setup_preload_duration_s.setValue(5.0)
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
        assert "measure_length_prompt" in actions
        assert "apply_length_setup" in actions
        assert "start_session" in actions
        start_length_index = actions.index("starting_length_prompt")
        prompt_index = actions.index("measure_length_prompt")
        apply_index = actions.index("apply_length_setup")
        session_index = actions.index("start_session")
        first_recipe_index = actions.index("set_current")
        assert start_length_index < prompt_index < apply_index < session_index < first_recipe_index
        preload_step = next(step for step in steps if step.note == "setup_preload")
        assert preload_step.action == "ramp_target"
        assert preload_step.basis == mini_dma_mod.HSW_BASIS_STRESS_MPA
        assert preload_step.target_end_value == pytest.approx(10.0)
        assert preload_step.target_ramp_rate_value_s == pytest.approx(2.0)
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
        assert "Starting length prior accepted" in window.log_output.toPlainText()
    finally:
        _close_test_window(window)


def test_setup_zero_plateau_fallback_updates_reference_and_returns_to_first_plateau_position(
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
        assert targets == [pytest.approx(7.000)]
        assert window._setup_zero_fallback_return_position_mm == pytest.approx(7.000)
        assert window._setup_zero_position_mm == pytest.approx(7.000)
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
        assert targets == [pytest.approx(7.000)]
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
        assert targets == [pytest.approx(7.000)]
        assert "zero-load plateau" in window.log_output.toPlainText()
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
        assert targets == [pytest.approx(7.000)]
        assert window._end_zero_fallback_return_position_mm == pytest.approx(7.000)
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
        assert targets == [pytest.approx(7.000)]
        assert window._end_zero_fallback_return_position_mm == pytest.approx(7.000)
    finally:
        _close_test_window(window)


def test_recovery_plot_shows_load_and_displacement_legend(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    dialog = mini_dma_mod.QtWidgets.QDialog(window)
    qtbot.addWidget(dialog)
    window._recovery_plot_dialog = dialog
    window._recovery_figure = mini_dma_mod.Figure(figsize=(4.0, 3.0))
    window._recovery_canvas = window.canvas
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

    try:
        dialog.show()
        window._refresh_recovery_plot()

        legend = window._recovery_figure.axes[0].get_legend()
        assert legend is not None
        assert [text.get_text() for text in legend.get_texts()] == ["load", "displacement"]
    finally:
        dialog.close()
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
    window.check_hardware_tare_on_start.setChecked(False)

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


def test_calibration_recipe_includes_mandatory_length_setup(tmp_path: Path, qtbot) -> None:
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
        assert "mandatory length setup" in summary
        actions = [step.action for step in steps]
        assert steps[0].action == "starting_length_prompt"
        assert steps[0].note == "setup_start_length"
        assert "starting_length_prompt" in actions
        assert "measure_length_prompt" in actions
        assert "apply_length_setup" in actions
        assert "start_session" in actions
        assert actions.index("starting_length_prompt") < actions.index("measure_length_prompt")
        assert actions.index("measure_length_prompt") < actions.index("apply_length_setup")
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
    ramp_index = window.combo_recipe_mode.findData("ramp")
    assert ramp_index >= 0
    window.combo_recipe_mode.setCurrentIndex(ramp_index)

    try:
        moved = window._move_to_position_mm(2.0)
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
    window.check_hardware_tare_on_start.setChecked(False)
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
    window.check_hardware_tare_on_start.setChecked(False)
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


def test_held_manual_jog_keeps_speed_when_timer_tick_is_delayed(
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

        assert controller.targets == [-80]
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

    try:
        button = window.findChild(QtWidgets.QPushButton, "manual_auto_connect_button")

        assert button is not None

        button.clicked.emit()

        assert called == ["tic", "scale"]
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
        assert "remaining" in first_format
        assert "1.5 min" in first_format

        window._automation_completed_ticks = 20
        window._update_recipe_progress()

        assert window.recipe_progress.value() == 20
        assert window.recipe_progress.format() == first_format
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
        assert window.spin_setup_zero_tolerance_g.isHidden() is True
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
        assert abs(effective_target_mm - 7.015) <= (5.0 / 231.692884) + 1e-9
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


def test_setup_preload_target_ramp_above_target_ramps_down_from_live_value(
    tmp_path: Path,
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _build_window(tmp_path, qtbot)
    now_s = [100.0]
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

    monkeypatch.setattr(mini_dma_mod.time, "monotonic", lambda: now_s[0])

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
        assert window._handle_target_ramp_step(step, 4) is False

        now_s[0] = 102.5

        assert window._handle_target_ramp_step(step, 4) is False
        assert captured_targets == [pytest.approx(80.0), pytest.approx(65.0)]
        assert window._active_target_ramp_rate_value_s == pytest.approx(6.0)
    finally:
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


def test_displacement_ramp_uses_global_control_and_log_clocks(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
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
        assert any(step.action == "move" for step in recipe_steps)
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
        assert window.spin_scale_interval.isHidden()
        assert window.spin_tic_status_interval.isHidden()
        assert window.spin_tic_keepalive_interval.isHidden()
        assert window.spin_supply_read_interval.isHidden()
    finally:
        _close_test_window(window)


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


def test_hardware_cadence_settings_restore_and_update_timers(tmp_path: Path, qtbot) -> None:
    _ensure_app()
    snapshot = _snapshot_settings()
    settings = _test_settings()
    settings.clear()
    settings.setValue("tic_status_interval_ms", 1500)
    settings.setValue("tic_keepalive_interval_ms", 350)
    settings.setValue("supply_read_interval_ms", 1250)
    settings.sync()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)

    try:
        assert window.spin_tic_status_interval.value() == 1500
        assert window.spin_tic_keepalive_interval.value() == 350
        assert window.spin_supply_read_interval.value() == 1250
        assert window._status_timer.interval() == 1500
        assert window._tic_keepalive_timer.interval() == 350

        window.spin_tic_keepalive_interval.setValue(450)
        assert window._tic_keepalive_timer.interval() == 450

        window._save_settings()
        assert int(settings.value("tic_status_interval_ms")) == 1500
        assert int(settings.value("tic_keepalive_interval_ms")) == 450
        assert int(settings.value("supply_read_interval_ms")) == 1250
    finally:
        _close_test_window(window)


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
        assert window._length_setup_progress.value() == 3
        assert "Setup progress" in window._length_setup_progress.format()
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
        current_hold_max_s=30.0,
    )

    try:
        assert window._handle_current_sweep_step(step, 4) is False
        assert window._handle_current_sweep_step(step, 4) is False

        assert supply.commands == [1.0]
        assert len(seek_calls) == 2
        assert "holding current ramp" in window.log_output.toPlainText().lower()
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
    measured_values = iter([10.0, 0.0, 0.0])
    window._supply_controller = supply  # type: ignore[assignment]
    window._supply_output_enabled = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_LOAD
    window._current_distribution_value = lambda *_args, **_kwargs: next(measured_values)  # type: ignore[method-assign]
    window._seek_distribution_target = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
    ticks = iter([100.0, 100.0, 105.0, 106.1])

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
        current_hold_max_s=30.0,
    )

    try:
        assert window._handle_current_sweep_step(step, 4) is False
        assert window._handle_current_sweep_step(step, 4) is False
        assert supply.commands == [1.0]

        assert window._handle_current_sweep_step(step, 4) is False
        assert supply.commands == [1.0, 2.0]
        assert "resumed current ramp" in window.log_output.toPlainText().lower()
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


def test_current_sweep_settle_waits_until_target_is_reached(tmp_path: Path, qtbot) -> None:
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
        assert window._automation_index == 0

        window._handle_auto_ramp_tick()

        assert window._automation_active is True
        assert window._automation_index == 1
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
            assert (request_type, request, value, index, data_or_wLength) == (0xC0, 0xA1, 0, 0, 0x35)
            data = bytearray(0x35)
            data[0x00] = 10
            data[0x02:0x04] = (0).to_bytes(2, "little")
            data[0x22:0x26] = (-42).to_bytes(4, "little", signed=True)
            data[0x33:0x35] = (12345).to_bytes(2, "little")
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
    assert "Errors currently stopping the motor: None" in status


def test_tic_controller_prefers_native_usb_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeNative:
        def __init__(self, *, device_serial: str = "") -> None:
            self.device_serial = device_serial
            self.targets: list[tuple[int, int | None]] = []

        def set_target_position(self, position_steps: int, max_speed: int | None = None) -> None:
            self.targets.append((position_steps, max_speed))

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
    controller.set_target_position(42, max_speed=123)

    assert len(created) == 1
    assert created[0].device_serial == "00501366"
    assert created[0].targets == [(42, 123)]


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

    controller = mini_dma_mod.TicController(
        command_path="ticcmd",
        device_serial="00501366",
        prefer_native_usb=True,
    )
    monkeypatch.setattr(mini_dma_mod, "NativeTicUsbController", _fail_native)
    monkeypatch.setattr(controller, "executable", lambda: "ticcmd.exe")
    monkeypatch.setattr(mini_dma_mod.subprocess, "run", _fake_run)

    controller.reset_command_timeout()

    assert calls == [["ticcmd.exe", "-d", "00501366", "--reset-command-timeout"]]


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
        assert metadata["logging"]["raw_scale_sample_count"] == 3
    finally:
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
        assert point.position_mm == pytest.approx(0.075)
        assert point.strain_pct == pytest.approx(0.75)
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


def test_dashboard_uses_fixed_live_value_cells_without_overview(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert not hasattr(window, "overview_section")
        assert "speed_mm_s" in window._dashboard_value_labels
        assert "scale" in window._dashboard_value_labels
        assert window.dashboard_status_box.parentWidget() is window.dashboard_header
        width = window._dashboard_value_labels["speed_mm_s"].minimumWidth()
        assert width >= 70
        assert window._dashboard_value_labels["speed_mm_s"].font().fixedPitch()
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
        assert moves == [pytest.approx(37.65)]
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
        assert moves == [pytest.approx(37.65)]
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
            5.0 / stiffness_mpa_per_mm
        ) + window._motor_step_mm()
        assert controller.max_speed == 20_000_000
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

    try:
        text = window._live_speed_summary_text()

        assert "0.5 mm/s" in text
        assert "1 g/s" in text
        assert "MPa/s" in text
        assert "2 %/s" in text
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
        assert abs((controller.targets[-1] / 1000.0) - 0.1) <= window._motor_step_mm() + 1e-12
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
        one_mpa_mm = 1.0 / 113.0
        assert correction_mm <= one_mpa_mm + 1e-9
        assert moves[-1][1] is not None
        assert moves[-1][1] >= 0.05
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
        assert correction_mm <= 1.0 / 113.0 + 1e-9
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
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        54.0,
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
        target_mm, effective_mm = moves[-1]
        assert abs(target_mm - 6.94) <= 1.0 / 305.0 + 1e-9
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
    window._current_position_mm = 6.7
    window._effective_position_mm = 6.7
    window._last_move_target_mm = 6.7
    window._last_effective_move_target_mm = 6.7
    window._latest_scale_timestamp = time.time()
    window._latest_scale_value_g = mini_dma_mod.load_g_from_stress_mpa(
        38.0,
        window.spin_diameter.value(),
    )

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
        assert correction_mm <= (1.0 / 224.502066) + 1e-9
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
        assert correction_mm == pytest.approx(10.0 / sensitivity)
        assert correction_mm < 80.0 * 0.01
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

    try:
        window._start_recovery_displacement_zero()

        assert window._automation_active is True
        assert window._automation_name == mini_dma_mod.RECOVERY_POSITION
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
        _wait_for_tic_commands(window)

        assert tensioning_move is False
        assert relaxing_move is True
        assert controller.targets == [10]
        assert "Relaxing moves are still allowed" in window.log_output.toPlainText()
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
            control_trace_handle,
            _control_trace_writer,
            setup_txt_handle,
            setup_csv_handle,
            _setup_csv_writer,
            txt_path,
            csv_path,
            json_path,
            raw_scale_path,
            control_trace_path,
            setup_txt_path,
            setup_csv_path,
        ) = window._prepare_session_files(created_utc="2026-04-28 12:00:00")
        for handle in (
            txt_handle,
            csv_handle,
            raw_scale_handle,
            control_trace_handle,
            setup_txt_handle,
            setup_csv_handle,
        ):
            handle.close()

        assert txt_path == tmp_path / "same_sample_run03" / "measurement.txt"
        assert csv_path == tmp_path / "same_sample_run03" / "measurement.csv"
        assert json_path == tmp_path / "same_sample_run03" / "metadata.json"
        assert raw_scale_path == tmp_path / "same_sample_run03" / "scale_raw.csv"
        assert control_trace_path == tmp_path / "same_sample_run03" / "control_trace.csv"
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
            control_trace_handle,
            _control_trace_writer,
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
            control_trace_handle,
            setup_txt_handle,
            setup_csv_handle,
        ):
            handle.close()

        assert txt_path == tmp_path / "same_sample_run04" / "measurement.txt"
        assert window.edit_log_name.text() == "same_sample_run04"
        assert not (tmp_path / "same_sample_run02_run02").exists()
    finally:
        _close_test_window(window)


def test_prepare_session_files_can_replace_existing_outputs(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_sample_name.setText("repeat sample")
    window.edit_log_name.setText("replace_sample")
    txt_path = tmp_path / "replace_sample" / "measurement.txt"
    txt_path.parent.mkdir()
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
            control_trace_handle,
            _control_trace_writer,
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
            control_trace_handle,
            setup_txt_handle,
            setup_csv_handle,
        ):
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
