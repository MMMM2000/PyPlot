from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PyQt6 import QtWidgets

from plotting.pyplot.app import PyPlotWorkbench


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


@pytest.mark.parametrize(
    ("plugin_name", "source_dir", "min_plots"),
    [
        ("Temperature Dependence", Path("sample_data/temperature_dependence"), 1),
        ("Temperature Sensitivity", Path("sample_data/temperature_dependence"), 1),
        ("Stress Dependence", Path("sample_data/stress_dependence"), 1),
        ("Stress Sensitivity", Path("sample_data/stress_dependence"), 1),
    ],
)
def test_legacy_signal_plugins_smoke_load_and_generate(
    plugin_name: str,
    source_dir: Path,
    min_plots: int,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter=plugin_name)
    try:
        resolved_dir = source_dir.resolve()
        window._select_directories = (  # type: ignore[assignment]
            lambda _parent=None, *, title, start_dir: [str(resolved_dir)]
        )
        window._import_data_from_folder()
        plugin = window._current_plugin  # noqa: SLF001 - test hook
        assert plugin is not None

        plugin.generate()
        app.processEvents()

        plot_tabs = getattr(plugin, "_plot_tabs", [])
        assert len(plot_tabs) >= min_plots
        assert window._worksheets  # noqa: SLF001 - imported workbook tree populated
        assert window.plot_button.isEnabled()
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()
