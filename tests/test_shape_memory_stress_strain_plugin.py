from __future__ import annotations

import os
import sys

from PyQt6 import QtWidgets

from plotting.plugins.base import PyPlotPlugin
from plotting.plugins.shape_memory_stress_strain.shape_memory_stress_strain_plugin import (
    DEFAULT_LAYOUT_MODE,
    LAYOUT_DUAL_AXIS,
    LAYOUT_MODE_SETTINGS_KEY,
)
from plotting.pyplot.app import PyPlotWorkbench


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def test_shape_memory_defaults_to_dual_axis_layout() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Shape Memory Stress/Strain")
    try:
        plugin = window._current_plugin  # noqa: SLF001 - test hook
        assert isinstance(plugin, PyPlotPlugin)
        window.settings.remove(LAYOUT_MODE_SETTINGS_KEY)
        window.settings.sync()
        plugin.settings_widget()
        assert plugin._stored_layout_mode() == DEFAULT_LAYOUT_MODE  # type: ignore[attr-defined]  # noqa: SLF001
        combo = getattr(plugin, "_layout_mode_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)
        assert combo.currentData() == LAYOUT_DUAL_AXIS
    finally:
        window.settings.remove(LAYOUT_MODE_SETTINGS_KEY)
        window.settings.sync()
        window.close()
        app.processEvents()


def test_shape_memory_open_origin_keeps_dual_axis_mode(monkeypatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Shape Memory Stress/Strain")
    try:
        plugin = window._current_plugin  # noqa: SLF001 - test hook
        assert isinstance(plugin, PyPlotPlugin)
        plugin._dataset = [object()]  # type: ignore[attr-defined]  # noqa: SLF001 - bypass load prompt
        plugin.settings_widget()
        combo = getattr(plugin, "_layout_mode_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)
        target_index = combo.findData(LAYOUT_DUAL_AXIS)
        assert target_index >= 0
        combo.setCurrentIndex(target_index)

        opened: dict[str, int] = {"count": 0}
        generated: dict[str, int] = {"count": 0}

        monkeypatch.setattr(window, "_open_origin_shared", lambda: opened.__setitem__("count", opened["count"] + 1))
        monkeypatch.setattr(plugin, "generate", lambda: generated.__setitem__("count", generated["count"] + 1))

        plugin.open_origin()

        assert opened["count"] == 1
        assert generated["count"] == 0
        assert combo.currentData() == LAYOUT_DUAL_AXIS
    finally:
        window.close()
        app.processEvents()
