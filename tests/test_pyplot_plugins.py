from __future__ import annotations

import sys

from PyQt6 import QtWidgets

from plotting.pyplot.window import TOOLBAR_SECTION_PROPERTY
from plotting.pyplot.app import PyPlotWorkbench
from plotting.plugins import PyPlotPlugin
from plotting.plugins.temperature_dependence import TemperatureDependencePlugin
from plotting.plugins.temperature_sensitivity import TemperatureSensitivityPlugin
from plotting.plugins.current_annealing import CurrentAnnealingPlugin
from plotting.plugins.stress_dependence import StressDependencePlugin
from plotting.plugins.stress_sensitivity import StressSensitivityPlugin
from plotting.plugins.hsw_load_compare import HswLoadComparePlugin
from plotting.plugins.maxion_continuous import MaxionContinuousPlugin
from plotting.plugins.pdf_plotter import PdfPlotterPlugin
from plotting.plugins.hysteresis_loops import HysteresisLoopsPlugin
from plotting.plugins.hsw_distribution import HswDistributionPlugin
from plotting.plugins.strain_3d_plot import Strain3DPlotPlugin


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def _iter_toolbar_sections(widget: QtWidgets.QWidget | None) -> list[str]:
    if widget is None:
        return []
    sections: list[str] = []
    for child in widget.findChildren(QtWidgets.QWidget):
        title = child.property(TOOLBAR_SECTION_PROPERTY)
        if isinstance(title, str) and title.strip():
            sections.append(title.strip())
    return sections


def test_plugin_settings_provide_toolbar_sections_and_mount() -> None:
    _ensure_app()
    factories = {
        "Temperature Dependence": TemperatureDependencePlugin,
        "Temperature Sensitivity": TemperatureSensitivityPlugin,
        "Current Annealing": CurrentAnnealingPlugin,
        "Stress Dependence": StressDependencePlugin,
        "Stress Sensitivity": StressSensitivityPlugin,
        "Hsw Load Compare": HswLoadComparePlugin,
        "Maxion Continuous": MaxionContinuousPlugin,
        "PDF Plotter": PdfPlotterPlugin,
        "Hysteresis Loops": HysteresisLoopsPlugin,
        "Hsw Distribution": HswDistributionPlugin,
        "Strain 3D Plot": Strain3DPlotPlugin,
    }

    plugin_factories = {name: (lambda host, cls=cls, n=name: cls(host, n)) for name, cls in factories.items()}
    window = PyPlotWorkbench(plotters=plugin_factories)
    try:
        combo = getattr(window, "_plotter_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)

        for name in factories:
            index = combo.findText(name)
            assert index >= 0, f"{name} not present in plugin selector"
            combo.setCurrentIndex(index)
            plugin = window._current_plugin  # noqa: SLF001 - test hook
            assert isinstance(plugin, PyPlotPlugin)

            panel = plugin.panel_widget()
            settings = plugin.settings_widget()

            section_titles = _iter_toolbar_sections(settings)
            assert section_titles, f"{name} settings expose no toolbar sections"

            if settings is not None:
                window._set_plugin_settings_widget(settings)  # noqa: SLF001
            if panel is not None:
                window._set_script_panel(panel)  # noqa: SLF001

            # Build the drop-down menus to ensure no runtime errors.
            for title, anchor in window._graph_settings_sections:  # type: ignore[attr-defined]
                if anchor is not None:
                    menu = window._build_graph_section_menu(title, anchor)  # noqa: SLF001
                    assert menu is not None
                    menu.deleteLater()
    finally:
        window.close()
