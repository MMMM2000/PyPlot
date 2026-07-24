from __future__ import annotations

import importlib
import os
import sys
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
    assert window._dashboard_view_tabs.isVisible()
    assert window._dashboard_view_tabs.currentIndex() == 0
    assert len(window._plot_tiles) == 4

    window._dashboard_view_tabs.setCurrentIndex(1)
    assert window._adaptive_plot_stack.currentIndex() == 1
    assert window.button_plot_setup.isVisible()
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
    assert not window._control_view_tabs.isTabEnabled(0)
    assert window._control_view_stack.currentIndex() == 0


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
    assert window._dashboard_view_tabs.isVisible()
    assert window._control_view_tabs.isTabEnabled(0)

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
    assert navigator.target_list.count() == 20
    assert navigator.active_label.text() == "Active  100 MPa"
    assert navigator.inspected_label.text() == "Inspecting  100 MPa"
    assert "Following active target | 100 MPa" in (
        window._adaptive_workspace_context_label.text()
    )

    strain_curves = window._adaptive_result_curves["strain"]
    strain_x, _strain_y = strain_curves[0].getData()
    assert len(strain_x) == 3
    progress_x, _progress_y = (
        window._adaptive_plot_bundles["stress"].left_curve.getData()
    )
    assert len(progress_x) == 3

    first_item = navigator.target_list.item(0)
    navigator.target_list.itemClicked.emit(first_item)
    app.processEvents()
    assert window._adaptive_target_selection == (
        adaptive_mod.StressTargetSelection.target(50.0)
    )
    assert navigator.active_label.text() == "Active  100 MPa"
    assert navigator.inspected_label.text() == "Inspecting  50 MPa"
    assert "Inspecting 50 MPa | active 100 MPa" in (
        window._adaptive_workspace_context_label.text()
    )

    navigator.all_button.click()
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

    future_item = navigator.target_list.item(navigator.target_list.count() - 1)
    navigator.target_list.itemClicked.emit(future_item)
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
    assert window._adaptive_summary_labels["processed"].text() == "101.5 MPa"
    assert window._adaptive_summary_labels["current"].text() == "3.10 mA"
    assert (
        window._adaptive_plot_bundles["strain"]
        .widget.backgroundBrush()
        .color()
        .name()
        == "#191c20"
    )


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
