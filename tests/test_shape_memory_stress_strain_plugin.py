from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
from PyQt6 import QtWidgets

from plotting.plugins.base import PyPlotPlugin
from plotting.plugins.shape_memory_stress_strain.shape_memory_stress_strain_plugin import (
    DEFAULT_LAYOUT_MODE,
    LAYOUT_DUAL_AXIS,
    LAYOUT_MODE_SETTINGS_KEY,
    LAYOUT_SEPARATE_TABS,
    ShapeMemoryEntry,
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


def test_shape_memory_origin_export_uses_current_plot_tabs(monkeypatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Shape Memory Stress/Strain")
    try:
        plugin = window._current_plugin  # noqa: SLF001 - test hook
        assert isinstance(plugin, PyPlotPlugin)
        plugin.settings_widget()
        combo = getattr(plugin, "_layout_mode_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)

        frame = pd.DataFrame(
            {
                "displacement_mm": [0.0, 0.2, 0.5, 0.3, 0.1],
                "load_g": [0.0, 12.0, 28.0, 14.0, 1.0],
                "strain_pct": [0.0, 0.4, 1.0, 0.6, 0.2],
                "stress_mpa": [0.0, 40.0, 95.0, 48.0, 4.0],
            }
        )
        plugin._dataset = [  # type: ignore[attr-defined]  # noqa: SLF001
            ShapeMemoryEntry(path=Path("Ni50Fe27Ga23 50mA.txt"), frame=frame)
        ]

        combo.setCurrentIndex(combo.findData(LAYOUT_SEPARATE_TABS))
        plugin.generate()
        stale_tabs = list(plugin._plot_tabs)  # type: ignore[attr-defined]  # noqa: SLF001
        assert len(stale_tabs) == 2

        # Simulate restored/legacy separate graph tabs that are still registered
        # with the host but are no longer the plugin's current graph set.
        plugin._plot_tabs.clear()  # type: ignore[attr-defined]  # noqa: SLF001
        combo.setCurrentIndex(combo.findData(LAYOUT_DUAL_AXIS))
        plugin.generate()
        current_tabs = list(plugin._plot_tabs)  # type: ignore[attr-defined]  # noqa: SLF001
        assert len(current_tabs) == 1
        assert all(window.tab_widget.indexOf(tab) >= 0 for tab in stale_tabs + current_tabs)

        captured: dict[str, object] = {}

        def _fake_push(workbooks, *, create_graphs=False):
            captured["names"] = [workbook.name for workbook in workbooks]
            captured["create_graphs"] = create_graphs
            return (len(workbooks), len(workbooks), [])

        monkeypatch.setattr(window, "_push_workbooks_to_origin", _fake_push)
        monkeypatch.setattr("plotting.pyplot.window.schedule_origin_release", lambda: None)
        monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args, **kwargs: None)
        monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *args, **kwargs: None)
        monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *args, **kwargs: None)

        plugin.open_origin()

        assert captured["create_graphs"] is True
        assert captured["names"] == ["Ni50Fe27Ga23 50mA"]
    finally:
        window.settings.remove(LAYOUT_MODE_SETTINGS_KEY)
        window.settings.sync()
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()
