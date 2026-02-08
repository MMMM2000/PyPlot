from __future__ import annotations

from PyQt6 import QtWidgets

import launcher as launcher_module
from plotting.pyplot.app import PyPlotWorkbench


def test_launcher_window_smoke_offscreen(qtbot, monkeypatch) -> None:
    fake_registry = {
        "loggers": {"Fixture Logger": lambda: None},
        "plotters": {"Fixture Plotter": lambda: None},
        "emulators": {"Fixture Emulator": lambda: None},
    }
    monkeypatch.setattr(launcher_module, "_build_registry", lambda: fake_registry)

    window = launcher_module.MasterLauncher()
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(lambda: bool(getattr(window, "_registry_loaded", False)), timeout=5000)
    assert window.plot_list.count() >= 1
    assert window.plot_list.item(0).text() == "Fixture Plotter"


def test_pyplot_workbench_blank_graph_smoke_offscreen(qtbot) -> None:
    window = PyPlotWorkbench(plotters={})
    qtbot.addWidget(window)
    window.show()

    window._create_blank_graph()  # noqa: SLF001 - smoke check internal graph path
    qtbot.waitUntil(lambda: bool(window._axes_by_tab), timeout=5000)  # noqa: SLF001

    axes = window._current_axes()  # noqa: SLF001 - smoke check internal accessors
    canvas = window._current_canvas()  # noqa: SLF001
    assert axes is not None
    assert isinstance(canvas, QtWidgets.QWidget)
