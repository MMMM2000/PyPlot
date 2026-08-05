from __future__ import annotations

import os
import sys

from PyQt6 import QtWidgets

from experiments import EXPERIMENTS
from experiments import tma_recipe_workspace_lab as lab


def _ensure_app() -> QtWidgets.QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def test_recipe_studies_are_registered() -> None:
    assert "TMA UI Design Lab - Adaptive Iso-load" in EXPERIMENTS
    assert "TMA UI Design Lab - Adaptive Iso-strain" in EXPERIMENTS


def test_recipe_workspaces_use_recipe_specific_outcomes_and_units() -> None:
    app = _ensure_app()
    expectations = {
        "iso-load": ("Strain vs current", "g", "MPa"),
        "iso-strain": ("Stress vs current", "%", "mm"),
    }
    for key, (tab_text, primary_unit, alternate_unit) in expectations.items():
        window = lab.RecipeWorkspaceWindow(lab.PROFILES[key])
        try:
            window.show()
            app.processEvents()
            assert window.result_tabs.tabText(0) == tab_text
            active_item = window.target_items[window.profile.active_target]
            assert primary_unit in active_item.text(0)
            assert alternate_unit in active_item.text(1)
            assert window.result_tabs.tabText(1) == "Resistance vs current"
        finally:
            window.close()
            app.processEvents()


def test_target_navigation_scopes_both_result_tabs() -> None:
    app = _ensure_app()
    window = lab.RecipeWorkspaceWindow(lab.PROFILES["iso-strain"])
    try:
        window.show()
        app.processEvents()
        assert len(window.outcome_plot.series) == 1
        window.target_tree.itemClicked.emit(window.target_items[None], 0)
        app.processEvents()
        assert len(window.outcome_plot.series) == len(window.profile.targets)
        assert len(window.resistance_plot.series) == len(window.profile.targets)
        assert "Comparing all measured" in window.view_context.text()
        window.return_button.click()
        app.processEvents()
        assert window.follow_active
        assert len(window.outcome_plot.series) == 1
    finally:
        window.close()
        app.processEvents()
