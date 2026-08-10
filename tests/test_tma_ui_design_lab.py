from __future__ import annotations

import os
import sys

from PyQt6 import QtWidgets

from experiments import EXPERIMENTS
from experiments import tma_ui_design_lab


def _ensure_app() -> QtWidgets.QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def test_design_lab_is_registered_as_launcher_experiment() -> None:
    assert {
        "TMA UI Design Lab - Instrument Refined",
        "TMA UI Design Lab - Adaptive Workspace",
        "TMA UI Design Lab - Adaptive Workspace v2",
        "TMA UI Design Lab - Adaptive Workspace v3",
        "TMA UI Design Lab - Adaptive Workspace v4",
        "TMA UI Design Lab - Plot-First Control Room",
    }.issubset(EXPERIMENTS)


def test_launcher_factory_opens_adaptive_run_workspace() -> None:
    app = _ensure_app()
    window = EXPERIMENTS["TMA UI Design Lab - Adaptive Workspace"]()
    assert isinstance(window, tma_ui_design_lab.DesignWindow)
    try:
        app.processEvents()
        assert window.isVisible()
        assert window.direction.key == "adaptive"
        assert window.stack.currentIndex() == 1
        assert window.windowTitle() == "TMA UI Design Lab - Adaptive Workspace"
    finally:
        window.close()
        app.processEvents()


def test_launcher_factories_open_each_named_direction() -> None:
    app = _ensure_app()
    entries = {
        "TMA UI Design Lab - Instrument Refined": "refined",
        "TMA UI Design Lab - Adaptive Workspace": "adaptive",
        "TMA UI Design Lab - Adaptive Workspace v2": "adaptive-v2",
        "TMA UI Design Lab - Adaptive Workspace v3": "adaptive-v3",
        "TMA UI Design Lab - Adaptive Workspace v4": "adaptive-v4",
        "TMA UI Design Lab - Plot-First Control Room": "plot-first",
    }
    for name, direction in entries.items():
        window = EXPERIMENTS[name]()
        try:
            app.processEvents()
            assert isinstance(window, tma_ui_design_lab.DesignWindow)
            assert window.direction.key == direction
            assert window.stack.currentIndex() == 1
        finally:
            window.close()
            app.processEvents()


def test_all_design_directions_and_stages_construct() -> None:
    app = _ensure_app()
    for direction in tma_ui_design_lab.DIRECTIONS:
        window = tma_ui_design_lab.launch(direction=direction, stage="prepare")
        try:
            app.processEvents()
            assert window.stack.currentIndex() == 0
            window.set_stage("run")
            assert window.stack.currentIndex() == 1
            window.set_stage("review")
            assert window.stack.currentIndex() == 2
        finally:
            window.close()
            app.processEvents()


def test_adaptive_v3_remaining_sweeps_editor_applies_future_values() -> None:
    app = _ensure_app()
    window = tma_ui_design_lab.launch(direction="adaptive-v3", stage="run")
    try:
        assert "stress recovery hold" in window.dock_state.text().lower()
        window.start_button.click()
        app.processEvents()
        dialog = window.remaining_editor
        assert isinstance(dialog, tma_ui_design_lab.RemainingSweepsDialog)
        assert dialog.isVisible()
        assert "Active sweep: 500 MPa" in " ".join(
            item.text() for item in dialog.findChildren(QtWidgets.QLabel)
        )

        current_end = dialog.findChild(QtWidgets.QDoubleSpinBox, "remaining_current_end_mA")
        stress_end = dialog.findChild(QtWidgets.QDoubleSpinBox, "remaining_stress_end_mpa")
        assert current_end is not None
        assert stress_end is not None
        assert current_end.text() == "30 mA"
        assert stress_end.text() == "1000 MPa"
        current_equivalent = dialog.findChild(
            QtWidgets.QLabel,
            "remaining_current_end_mA_equivalent",
        )
        stress_equivalent = dialog.findChild(
            QtWidgets.QLabel,
            "remaining_stress_end_mpa_equivalent",
        )
        assert current_equivalent is not None
        assert current_equivalent.text() == "405 A/mm2"
        assert stress_equivalent is not None
        assert stress_equivalent.text() == "7.53 g"
        current_end.setValue(35.0)
        stress_end.setValue(900.0)
        assert current_end.text() == "35 mA"
        assert stress_end.text() == "900 MPa"
        assert current_equivalent.text() == "472.5 A/mm2"
        assert stress_equivalent.text() == "6.777 g"
        dialog._apply()  # noqa: SLF001 - deterministic prototype interaction
        app.processEvents()

        assert window.last_remaining_update is not None
        assert window.last_remaining_update["current_end_mA"] == 35.0
        assert window.last_remaining_update["stress_end_mpa"] == 900.0
        assert window.remaining_current_value is not None
        assert window.remaining_current_value.text() == "1 - 35 mA"
        assert window.remaining_current_equivalent is not None
        assert window.remaining_current_equivalent.text() == "13.5 - 472.5 A/mm2"
        assert window.remaining_stress_value is not None
        assert window.remaining_stress_value.text() == "550 - 900 MPa"
        assert window.remaining_stress_equivalent is not None
        assert window.remaining_stress_equivalent.text() == "4.141 - 6.777 g"
        assert "Active 500 MPa sweep unchanged" in window.dock_detail.text()
    finally:
        if window.remaining_editor is not None:
            window.remaining_editor.close()
        window.close()
        app.processEvents()


def test_adaptive_v3_prioritizes_outcome_and_scopes_resistance() -> None:
    app = _ensure_app()
    window = tma_ui_design_lab.launch(direction="adaptive-v3", stage="run")
    try:
        outcome = window.findChild(tma_ui_design_lab.PlotPanel, "strain_outcome_plot")
        stress_progress = window.findChild(
            tma_ui_design_lab.PlotPanel,
            "stress_progress_plot",
        )
        strain_progress = window.findChild(
            tma_ui_design_lab.PlotPanel,
            "strain_progress_plot",
        )
        current_progress = window.findChild(
            tma_ui_design_lab.PlotPanel,
            "current_progress_plot",
        )
        assert outcome is not None
        assert "Strain vs current" in outcome.title
        assert len(outcome.series) == 10
        assert stress_progress is not None
        assert strain_progress is not None
        assert current_progress is not None

        selector = window.findChild(
            QtWidgets.QComboBox,
            "resistance_target_selector",
        )
        resistance = window.findChild(
            tma_ui_design_lab.PlotPanel,
            "resistance_target_plot",
        )
        assert selector is not None
        assert resistance is not None
        assert selector.currentText() == "500 MPa"
        assert resistance.title == "500 MPa target"
        assert len(resistance.series) == 1
        selector.setCurrentText("300 MPa")
        app.processEvents()
        assert resistance.title == "300 MPa target"
        assert len(resistance.series) == 1
    finally:
        window.close()
        app.processEvents()


def test_adaptive_v4_links_target_navigation_across_workspace() -> None:
    app = _ensure_app()
    window = tma_ui_design_lab.launch(direction="adaptive-v4", stage="run")
    try:
        assert window.target_follow_button is not None
        assert window.target_follow_button.isChecked()
        assert window.target_view_mpa == 500
        assert window.target_outcome_plot is not None
        assert len(window.target_outcome_plot.series) == 1
        assert window.target_result_tabs is not None
        assert window.target_result_tabs.count() == 2
        assert window.target_resistance_plot is not None
        assert window.target_resistance_plot.title == "Resistance vs current | 500 MPa"
        assert len(window.target_resistance_plot.series) == 1

        assert window.target_tree is not None
        all_item = window.target_tree_items[None]
        window.target_tree.itemClicked.emit(all_item, 0)
        app.processEvents()
        assert window.target_view_mpa is None
        assert not window.target_follow_button.isChecked()
        assert len(window.target_outcome_plot.series) == 10
        assert window.target_resistance_plot.title == (
            "Resistance vs current | all measured stress targets"
        )
        assert len(window.target_resistance_plot.series) == 10

        target_item = window.target_tree_items[300]
        window.target_tree.itemClicked.emit(target_item, 0)
        app.processEvents()
        assert window.target_view_mpa == 300
        assert len(window.target_outcome_plot.series) == 1
        assert window.target_stress_progress is not None
        assert len(window.target_stress_progress.series[0][0]) < len(
            tma_ui_design_lab.DATA["time"]
        )
        assert window.target_resistance_plot.title == "Resistance vs current | 300 MPa"
        assert len(window.target_resistance_plot.series) == 1
        assert window.target_view_context is not None
        assert "Inspecting 300 MPa" in window.target_view_context.text()

        assert window.target_return_button is not None
        assert window.target_return_button.isEnabled()
        window.target_return_button.click()
        app.processEvents()
        assert window.target_follow_active
        assert window.target_view_mpa == 500
        assert window.target_follow_button.isChecked()
        assert not window.target_return_button.isEnabled()
    finally:
        window.close()
        app.processEvents()
