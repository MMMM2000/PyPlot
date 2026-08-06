from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault(
    "MINI_DMA_QSETTINGS_INI_DIR",
    str(Path("artifacts/test-qsettings-adaptive-workspace")),
)

pytest.importorskip("PyQt6.QtWidgets", reason="Qt widgets backend is unavailable")

from PyQt6 import QtCore, QtWidgets

adaptive_mod = importlib.import_module(
    "data_logging.mini_dma_logger.adaptive_workspace"
)
mini_dma_mod = importlib.import_module(
    "data_logging.mini_dma_logger.mini_dma_logger"
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def _point(
    *,
    elapsed_s: float,
    target_mpa: float,
    current_mA: float,
    strain_pct: float,
    phase: str = "current_ramp",
) -> object:
    return mini_dma_mod.MeasurementPoint(
        elapsed_s=elapsed_s,
        timestamp_utc="2026-07-24T08:00:00.000Z",
        raw_position_mm=strain_pct / 10.0,
        position_mm=strain_pct / 10.0,
        raw_load_g=target_mpa / 100.0,
        load_g=target_mpa / 100.0,
        preload_state=mini_dma_mod.PRELOAD_ACTIVE,
        strain_pct=strain_pct,
        stress_mpa=target_mpa + 1.5,
        current_set_mA=current_mA,
        current_measured_mA=current_mA + 0.1,
        voltage_V=2.0,
        resistance_ohm=200.0 + current_mA,
        power_W=0.02,
        automation_phase=phase,
        automation_basis=mini_dma_mod.HSW_BASIS_STRESS_MPA,
        automation_target_value=target_mpa,
        plateau_index=int(target_mpa),
        plateau_label=f"{target_mpa:g} MPa",
    )


def _synthetic_points() -> list[object]:
    return [
        _point(
            elapsed_s=float(index),
            target_mpa=target,
            current_mA=1.0 + local_index,
            strain_pct=(target / 100.0) + local_index * 0.2,
        )
        for index, (target, local_index) in enumerate(
            (
                (50.0, 0),
                (50.0, 1),
                (50.0, 2),
                (100.0, 0),
                (100.0, 1),
                (100.0, 2),
            )
        )
    ]


@pytest.fixture
def window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> object:
    app = _ensure_app()
    monkeypatch.setattr(
        mini_dma_mod,
        "list_ports",
        SimpleNamespace(comports=lambda: []),
    )
    monkeypatch.setattr(
        mini_dma_mod,
        "_load_pyusb_backend",
        lambda: (_ for _ in ()).throw(RuntimeError("USB disabled in UI test")),
    )
    value = mini_dma_mod.MainWindow(log_dir=str(tmp_path), persist_settings=False)
    value.resize(1280, 768)
    value.show()
    app.processEvents()
    try:
        yield value
    finally:
        value.close()
        app.processEvents()


def test_target_model_groups_only_stress_target_points() -> None:
    points = _synthetic_points()
    points.append(
        mini_dma_mod.MeasurementPoint(
            **{
                **points[-1].__dict__,
                "elapsed_s": 7.0,
                "automation_basis": mini_dma_mod.HSW_BASIS_LOAD_G,
                "automation_target_value": 2.0,
            }
        )
    )

    assert adaptive_mod.measured_stress_targets(points) == [50.0, 100.0]
    selected = adaptive_mod.points_for_target(points, 50.0)
    assert len(selected) == 3
    assert all(point.automation_target_value == 50.0 for point in selected)


def test_target_model_excludes_continuous_target_ramp_setpoints() -> None:
    points = [
        _point(
            elapsed_s=float(index),
            target_mpa=float(index),
            current_mA=1.0,
            strain_pct=0.0,
            phase="target_ramp",
        )
        for index in range(101)
    ]
    points.extend(
        _point(
            elapsed_s=102.0 + index,
            target_mpa=100.0,
            current_mA=1.0 + index,
            strain_pct=0.2 * index,
            phase="current",
        )
        for index in range(3)
    )

    assert adaptive_mod.measured_stress_targets(points) == [100.0]
    assert len(adaptive_mod.points_for_target(points, 100.0)) == 3


def test_current_sweep_defaults_to_adaptive_but_custom_dashboard_remains(
    window: object,
) -> None:
    combo = window.combo_recipe_mode
    combo.setCurrentIndex(combo.findData(mini_dma_mod.CURRENT_SWEEP_STRESS))

    assert window._adaptive_plot_stack.currentIndex() == 0
    assert not window._dashboard_view_tabs.isVisible()
    assert window._dashboard_view_tabs.currentIndex() == 0
    assert len(window._plot_tiles) == 4

    window._set_control_view("review")
    assert window._adaptive_plot_stack.currentIndex() == 1
    assert all(tile.x_combo.count() > 0 for tile in window._plot_tiles)

    combo.setCurrentIndex(combo.findData(mini_dma_mod.CALIBRATION))
    assert window._adaptive_plot_stack.currentIndex() == 1
    assert not window._dashboard_view_tabs.isVisible()


@pytest.mark.parametrize(
    "mode",
    [
        mini_dma_mod.CURRENT_SWEEP_LOAD,
        mini_dma_mod.CURRENT_SWEEP_STRAIN,
    ],
)
def test_non_stress_current_sweeps_keep_custom_dashboard(
    window: object,
    mode: str,
) -> None:
    combo = window.combo_recipe_mode
    combo.setCurrentIndex(combo.findData(mode))

    assert window._adaptive_plot_stack.currentIndex() == 1
    assert not window._dashboard_view_tabs.isVisible()
    assert not window._control_view_tabs.isTabEnabled(1)
    assert window._control_view_tabs.isTabEnabled(0)
    assert window._control_view_stack.currentIndex() == 0


@pytest.mark.parametrize(
    ("mode", "adaptive_supported"),
    [
        (mini_dma_mod.CALIBRATION, False),
        (mini_dma_mod.CURRENT_SWEEP_LOAD, False),
        (mini_dma_mod.CURRENT_SWEEP_STRESS, True),
        (mini_dma_mod.CURRENT_SWEEP_STRAIN, False),
        (mini_dma_mod.CURRENT_SWEEP_FATIGUE, False),
        (mini_dma_mod.CONSTANT_CURRENT_STRAIN_SWEEP, False),
        (mini_dma_mod.CONSTANT_CURRENT_STRESS_RAMP, False),
        (mini_dma_mod.ELASTOCALORIC_EFFECT, False),
    ],
)
def test_recipe_matrix_routes_each_mode_to_a_compatible_workspace(
    window: object,
    mode: str,
    adaptive_supported: bool,
) -> None:
    combo = window.combo_recipe_mode
    combo.setCurrentIndex(combo.findData(mode))
    window._adaptive_workspace_user_prefers_custom = False
    window._sync_dashboard_workspace_mode()

    assert window._adaptive_workspace_supported() is adaptive_supported
    assert window._adaptive_plot_stack.currentIndex() == (
        0 if adaptive_supported else 1
    )
    assert window._control_view_tabs.isTabEnabled(1) is adaptive_supported
    assert len(window._plot_tiles) == 4
    assert all(tile.x_combo.count() > 0 for tile in window._plot_tiles)

    window._set_control_view("run")
    assert window._control_view_stack.currentIndex() == (
        1 if adaptive_supported else 0
    )


def test_active_stress_run_ignores_staged_recipe_selector_change(
    window: object,
) -> None:
    combo = window.combo_recipe_mode
    combo.setCurrentIndex(combo.findData(mini_dma_mod.CURRENT_SWEEP_STRESS))
    window._adaptive_workspace_user_prefers_custom = False
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._sync_dashboard_workspace_mode()

    combo.setCurrentIndex(combo.findData(mini_dma_mod.CALIBRATION))
    window._sync_dashboard_workspace_mode()

    assert window._adaptive_plot_stack.currentIndex() == 0
    assert not window._dashboard_view_tabs.isVisible()
    assert window._control_view_tabs.isTabEnabled(1)

    window._automation_active = False
    window._automation_name = ""


def test_active_nonstress_run_cannot_be_replaced_by_staged_stress_workspace(
    window: object,
) -> None:
    combo = window.combo_recipe_mode
    combo.setCurrentIndex(combo.findData(mini_dma_mod.CURRENT_SWEEP_FATIGUE))
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_FATIGUE
    window._adaptive_workspace_user_prefers_custom = False
    window._sync_dashboard_workspace_mode()

    combo.setCurrentIndex(combo.findData(mini_dma_mod.CURRENT_SWEEP_STRESS))
    window._sync_dashboard_workspace_mode()

    assert window._dashboard_recipe_mode() == mini_dma_mod.CURRENT_SWEEP_FATIGUE
    assert window._adaptive_plot_stack.currentIndex() == 1
    assert not window._control_view_tabs.isTabEnabled(1)

    window._automation_active = False
    window._automation_name = ""


def test_target_navigation_scopes_results_and_progress(
    window: object,
) -> None:
    app = _ensure_app()
    combo = window.combo_recipe_mode
    combo.setCurrentIndex(combo.findData(mini_dma_mod.CURRENT_SWEEP_STRESS))
    window._session_points = _synthetic_points()
    window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
    window._automation_target_value = 100.0
    window._automation_phase = "current_hold"
    window._adaptive_workspace_user_prefers_custom = False
    window._adaptive_target_selection = adaptive_mod.StressTargetSelection.follow_active()
    window._refresh_plots()
    app.processEvents()

    navigator = window._adaptive_target_navigator
    assert navigator.target_list.topLevelItemCount() == 21
    assert navigator.target_list.topLevelItem(0).text(0) == "All targets"
    assert navigator.active_label.text() == "Active  100 MPa"
    active_item = navigator.target_list.topLevelItem(2)
    assert active_item.text(0) == "100 MPa"
    assert active_item.font(0).bold()
    assert active_item.foreground(0).color().name() == "#e8ad43"
    assert active_item.toolTip(0).startswith("Active target.")
    assert navigator.inspected_label.text() == "Inspecting  100 MPa"
    assert "Following active target | live target 100 MPa" in (
        window._adaptive_workspace_context_label.text()
    )

    strain_curves = window._adaptive_result_curves["strain"]
    strain_x, _strain_y = strain_curves[0].getData()
    assert len(strain_x) == 3
    progress_x, _progress_y = (
        window._adaptive_plot_bundles["stress"].left_curve.getData()
    )
    assert len(progress_x) == 3

    first_item = navigator.target_list.topLevelItem(1)
    navigator.target_list.itemClicked.emit(first_item, 0)
    app.processEvents()
    assert window._adaptive_target_selection == (
        adaptive_mod.StressTargetSelection.target(50.0)
    )
    assert navigator.active_label.text() == "Active  100 MPa"
    assert navigator.inspected_label.text() == "Inspecting  50 MPa"
    assert "Inspecting 50 MPa | live target 100 MPa" in (
        window._adaptive_workspace_context_label.text()
    )
    assert window._adaptive_return_to_active_button.isEnabled()
    window._adaptive_return_to_active_button.click()
    app.processEvents()
    assert window._adaptive_target_selection.mode == "follow_active"
    assert not window._adaptive_return_to_active_button.isEnabled()

    navigator.target_list.itemClicked.emit(navigator.target_list.topLevelItem(0), 0)
    app.processEvents()
    assert window._adaptive_target_selection.mode == "all"
    all_progress_x, _all_progress_y = (
        window._adaptive_plot_bundles["stress"].left_curve.getData()
    )
    assert len(all_progress_x) == 6
    visible_result_curves = [
        curve for curve in window._adaptive_result_curves["strain"][:2]
        if len(curve.getData()[0]) > 0
    ]
    assert len(visible_result_curves) == 2
    inactive_pen = visible_result_curves[0].opts["pen"]
    active_pen = visible_result_curves[1].opts["pen"]
    assert inactive_pen.color().name() == "#56b6b0"
    assert inactive_pen.color().alpha() == 118
    assert inactive_pen.widthF() == pytest.approx(0.9)
    assert visible_result_curves[0].opts["symbolSize"] == pytest.approx(2.2)
    assert active_pen.color().name() == "#e8ad43"
    assert active_pen.color().alpha() == 255
    assert active_pen.widthF() == pytest.approx(2.2)
    assert visible_result_curves[1].opts["symbolSize"] == pytest.approx(4.0)

    future_item = navigator.target_list.topLevelItem(
        navigator.target_list.topLevelItemCount() - 1
    )
    navigator.target_list.itemClicked.emit(future_item, 0)
    app.processEvents()
    assert navigator.inspected_label.text() == "Inspecting  1000 MPa"
    assert all(
        curve.getData()[0] is None or len(curve.getData()[0]) == 0
        for curve in window._adaptive_result_curves["strain"]
    )


def test_follow_active_keeps_last_measured_target_during_next_target_ramp(
    window: object,
) -> None:
    window.combo_recipe_mode.setCurrentIndex(
        window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
    )
    window._session_points = _synthetic_points()
    window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
    window._automation_target_value = 73.0
    window._automation_phase = "target_ramp"
    window._active_target_ramp_end_value = 150.0
    window._adaptive_target_selection = adaptive_mod.StressTargetSelection.follow_active()

    measured, active, selected = window._adaptive_target_context(
        window._session_points
    )

    assert measured == [50.0, 100.0]
    assert active == 150.0
    assert selected == 100.0


def test_run_and_prepare_views_resize_control_column_for_compact_workspace(
    window: object,
) -> None:
    window.combo_recipe_mode.setCurrentIndex(
        window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
    )
    window._set_control_view("run")
    assert window._control_view_stack.currentIndex() == 1
    assert window._control_column.minimumWidth() == 210
    assert window._control_column.maximumWidth() == 280
    assert window._control_column.width() <= 280

    window._adaptive_target_navigator.configure_button.click()
    assert window._control_view_stack.currentIndex() == 0
    assert window._control_column.minimumWidth() == 560
    assert window.control_tabs.count() == 3
    assert [window.control_tabs.tabText(index) for index in range(3)] == [
        "Recipe",
        "Sample",
        "Hardware",
    ]


def test_adaptive_workspace_uses_full_width_header_and_action_dock(
    window: object,
) -> None:
    app = _ensure_app()
    window.combo_recipe_mode.setCurrentIndex(
        window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
    )
    window._set_control_view("run")
    app.processEvents()

    central = window.centralWidget()
    assert window.dashboard_header.parentWidget() is central
    assert window.recipe_action_footer.parentWidget() is central
    assert window._control_view_tabs.parentWidget() is window.dashboard_header
    assert window.dashboard_header.width() >= central.width() - 2
    assert window.recipe_action_footer.width() >= central.width() - 2
    assert window._adaptive_summary_labels["target"].isVisible()
    assert window._adaptive_sweep_progress.isVisible()
    assert [
        window._control_view_tabs.tabText(index)
        for index in range(window._control_view_tabs.count())
    ] == ["Prepare", "Run", "Review"]
    assert not window._dashboard_view_tabs.isVisible()
    assert [
        window._adaptive_inspector_tabs.tabText(index)
        for index in range(window._adaptive_inspector_tabs.count())
    ] == ["Active sweep", "Remaining recipe"]
    assert window._adaptive_return_to_active_button.text() == "Following active"
    assert not window.recipe_progress.isVisible()
    assert not window._control_view_tabs.usesScrollButtons()
    assert window._control_view_tabs.minimumWidth() >= 250


def test_configure_plots_button_is_managed_by_header_layout(window: object) -> None:
    app = _ensure_app()
    window.combo_recipe_mode.setCurrentIndex(
        window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
    )
    window._set_control_view("review")
    app.processEvents()

    header_layout = window.dashboard_header.layout()
    assert header_layout is not None
    assert header_layout.indexOf(window.button_plot_setup) >= 0
    assert window.button_plot_setup.isVisible()
    assert window.button_plot_setup.geometry().left() > 0


def test_footer_buttons_fit_full_prepare_labels(window: object) -> None:
    window._set_control_view("prepare")
    window._automation_active = False
    window._automation_paused = False
    window._update_recipe_buttons()

    for button in (
        window.button_start_recipe,
        window.button_pause_recipe,
        window.button_stop_recipe,
    ):
        required_width = button.fontMetrics().horizontalAdvance(button.text()) + 34
        assert button.minimumWidth() >= required_width


def test_adaptive_inspector_uses_real_measurement_state(window: object) -> None:
    app = _ensure_app()
    window.combo_recipe_mode.setCurrentIndex(
        window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
    )
    window._session_points = _synthetic_points()
    window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
    window._automation_target_value = 100.0
    window._automation_phase = "current_hold"
    window._automation_plateau_label = "100 MPa"
    window._adaptive_workspace_user_prefers_custom = False
    window._refresh_plots()
    app.processEvents()

    assert window._adaptive_target_headline_label.text() == "100 MPa"
    assert window._adaptive_workspace_phase_label.text() == "STRESS RECOVERY HOLD"
    assert window._adaptive_summary_labels["target"].text() == "100 MPa"
    assert window._adaptive_summary_labels["target_equivalent"].text().endswith(" g")
    assert window._adaptive_summary_labels["processed"].text() == "101.5 MPa"
    assert window._adaptive_summary_labels["current"].text() == "3.10 mA"
    assert "A/mm" in window._adaptive_summary_labels["current_equivalent"].text()
    assert "MPa" in window._adaptive_summary_labels["error"].text()
    assert window._adaptive_remaining_recipe_labels["targets"].text()
    assert window._adaptive_remaining_recipe_labels["current"].text().endswith("mA")
    plot_background = (
        window._adaptive_plot_bundles["strain"]
        .widget.backgroundBrush()
        .color()
        .name()
    )
    expected_background = window._qcolor_from_rgb(
        window._plot_theme()["axes_rgb"]
    ).name()
    assert expected_background == "#171a1f"
    assert expected_background != "#ffffff"
    assert plot_background == expected_background
    assert all(
        bundle.widget.backgroundBrush().color().name() == plot_background
        for bundle in window._dashboard_plot_bundles
    )
    assert all(
        bundle.widget.backgroundBrush().color().name() == plot_background
        for bundle in window._adaptive_plot_bundles.values()
    )


def test_adaptive_current_phase_uses_distinct_status_and_measurement(
    window: object,
) -> None:
    window.combo_recipe_mode.setCurrentIndex(
        window.combo_recipe_mode.findData(mini_dma_mod.CURRENT_SWEEP_STRESS)
    )
    window._session_points = _synthetic_points()
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._automation_basis = mini_dma_mod.HSW_BASIS_STRESS_MPA
    window._automation_target_value = 100.0
    window._automation_phase = "current"

    window._refresh_plots()

    assert window._adaptive_workspace_phase_label.text() == "CURRENT SWEEP"
    assert (
        window._adaptive_workspace_phase_hint_label.text()
        == "3.1 mA measured"
    )
    window._automation_active = False


def test_run_log_button_reuses_existing_log_panel(window: object) -> None:
    app = _ensure_app()
    assert not window._dashboard_log_container.isVisible()

    window._run_log_button.click()
    app.processEvents()
    assert window._dashboard_log_container.isVisible()
    assert window._run_log_button.text() == "Hide log"

    window._run_log_button.click()
    app.processEvents()
    assert not window._dashboard_log_container.isVisible()
    assert window._run_log_button.text() == "Run log"


def test_active_sweep_keeps_update_action_discoverable(window: object) -> None:
    window._automation_active = True
    window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
    window._update_recipe_buttons()

    assert window.button_apply_current_sweep_edits.isVisible()
    assert not window.button_apply_current_sweep_edits.isEnabled()

    window._automation_active = False
    window._automation_name = ""


def test_adaptive_progress_uses_stable_overlay_text(window: object) -> None:
    window._automation_active = True
    window.recipe_progress.setRange(0, 100)
    window.recipe_progress.setValue(48)
    window.recipe_progress.setFormat("Overall 48% | 500 MPa | ETA 54 min")

    window._sync_adaptive_sweep_progress()

    assert not window._adaptive_sweep_progress.isTextVisible()
    assert window._adaptive_sweep_progress_label.text() == "48%  |  ETA 54 min"
    progress_layout = window._adaptive_sweep_progress_label.parentWidget().layout()
    assert progress_layout.indexOf(window._adaptive_sweep_progress_label) == 0
    assert progress_layout.indexOf(window._adaptive_sweep_progress) == 1
    window._automation_active = False


def test_adaptive_tab_bars_do_not_draw_overlapping_base_lines(
    window: object,
) -> None:
    assert not window._adaptive_result_tabs.tabBar().drawBase()
    assert not window._adaptive_inspector_tabs.tabBar().drawBase()


def test_manual_actions_expose_only_autoconnect_and_press_hold_motion(
    window: object,
) -> None:
    buttons = window.manual_actions_box.findChildren(
        QtWidgets.QPushButton,
        options=QtCore.Qt.FindChildOption.FindDirectChildrenOnly,
    )

    assert [button.text() for button in buttons] == [
        "Auto-connect hardware",
        "Move up",
        "Move down",
    ]
    assert window.findChild(QtWidgets.QPushButton, "manual_jog_tension_button") is not None
    assert window.findChild(QtWidgets.QPushButton, "manual_jog_relax_button") is not None
    assert all("Stop motion" not in button.text() for button in buttons)


def test_hardware_tab_uses_readiness_workspace_and_retains_full_settings(
    window: object,
) -> None:
    hardware_tab = window.control_tabs.widget(2)

    assert window.tma_bench_workspace.parent() is hardware_tab
    assert hardware_tab.findChildren(QtWidgets.QGroupBox) == []
    assert window._hardware_settings_dialog.windowTitle() == "TMA hardware settings"
    assert [
        window._hardware_settings_tabs.tabText(index)
        for index in range(window._hardware_settings_tabs.count())
    ] == [
        "Connections",
        "Safety and references",
        "Power supply",
        "IR temperature",
    ]
    window._show_hardware_settings_dialog("safety")
    assert window._hardware_settings_tabs.currentIndex() == 1
    assert window.spin_motion_speed_mm_s.isHidden() is False
    assert window.spin_jog_mm.isHidden() is False
    window._hardware_settings_dialog.hide()

    window.tma_bench_workspace.device_selected.emit("supply")
    assert window._hardware_settings_tabs.currentIndex() == 2
    window._hardware_settings_dialog.hide()

    assert all(
        row.findChildren(QtWidgets.QAbstractButton) == []
        for row in window.tma_bench_workspace.device_rows.values()
    )


def test_hardware_workspace_reports_ready_and_locks_edits_during_session(
    window: object,
) -> None:
    class _ConnectedSupply:
        @staticmethod
        def is_connected() -> bool:
            return True

    window.check_ir_enabled.blockSignals(True)
    window.check_ir_enabled.setChecked(False)
    window.check_ir_enabled.blockSignals(False)
    window._scale_thread = object()
    window._latest_scale_timestamp = time.time()
    window._latest_scale_value_g = window._zero_load_scale_reference_g()
    window._tic_status_text = "Motor ready"
    window._tic_motor_power_ok = True
    window._supply_controller = _ConnectedSupply()
    try:
        window._refresh_bench_workspace()
        assert window.tma_bench_workspace.headline_label.text() == "Bench ready"
        assert not window.tma_bench_workspace.primary_button.isVisible()
        assert window.tma_bench_workspace.settings_button.isEnabled()
        assert window._hardware_settings_tabs.isEnabled()
        assert window.tma_bench_workspace.owner_widget.isHidden()

        window._automation_active = True
        window._automation_name = mini_dma_mod.CURRENT_SWEEP_STRESS
        window._refresh_bench_workspace()
        assert window.tma_bench_workspace.headline_label.text() == "Bench in use"
        assert window.tma_bench_workspace.settings_button.isEnabled()
        assert not window._hardware_settings_tabs.isEnabled()
        assert not window.tma_bench_workspace.owner_widget.isHidden()
    finally:
        window._automation_active = False
        window._automation_name = ""
        window._scale_thread = None
        window._latest_scale_timestamp = None
        window._tic_status_text = ""
        window._tic_motor_power_ok = None
        window._supply_controller = None
        window.check_ir_enabled.blockSignals(True)
        window.check_ir_enabled.setChecked(True)
        window.check_ir_enabled.blockSignals(False)
