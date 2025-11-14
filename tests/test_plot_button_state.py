from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6 import QtWidgets

from plotting.pyplot.app import PyPlotWorkbench


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def _activate_temperature_plugin(window: PyPlotWorkbench) -> None:
    combo = getattr(window, "_plotter_combo", None)
    assert isinstance(combo, QtWidgets.QComboBox)
    index = combo.findText("Temperature Sensitivity")
    assert index >= 0
    combo.setCurrentIndex(index)


def test_plot_button_requires_data_before_click() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        _activate_temperature_plugin(window)
        plot_action = getattr(window, "plot_button", None)
        assert hasattr(plot_action, "isEnabled")

        # No selections and no loaded data => disabled
        window._selected_path_entries = []
        window._current_plugin._data = None  # type: ignore[attr-defined]
        window._update_action_states()
        assert not plot_action.isEnabled()

        # Imported file paths without loaded data -> still disabled
        window._selected_path_entries = [Path(__file__)]
        window._current_plugin._data = None  # type: ignore[attr-defined]
        window._update_action_states()
        assert not plot_action.isEnabled()

        # Loaded data enables the button
        window._current_plugin._data = object()  # type: ignore[attr-defined]
        window._update_action_states()
        assert plot_action.isEnabled()
    finally:
        window.close()
        app.processEvents()
