from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pytest
from PyQt6 import QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import launcher as launcher_module
from plotting.pyplot.app import PyPlotWorkbench
from plotting.pyplot.window import TabDescriptor


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def _wait_for_registry(window: launcher_module.MasterLauncher, app: QtWidgets.QApplication) -> None:
    for _ in range(40):
        app.processEvents()
        if getattr(window, "_registry_loaded", False):
            return
    raise AssertionError("Launcher registry did not finish loading in time.")


def test_launcher_plotting_list_refreshes_using_last_opened_order(
    monkeypatch,
) -> None:
    app = _ensure_app()
    fake_registry = {
        "loggers": {},
        "plotters": {
            "ZZ Plot A": lambda: None,
            "ZZ Plot B": lambda: None,
        },
        "emulators": {},
    }
    monkeypatch.setattr(launcher_module, "_build_registry", lambda: fake_registry)

    window = launcher_module.MasterLauncher()
    try:
        _wait_for_registry(window, app)
        assert window._sort_modes.get("plotters") == "last_used"  # noqa: SLF001 - test hook

        now = time.time()
        window._settings.setValue("launcher_last_order/seq", 200)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot A", 100)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot B", 200)
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot A", now - 100.0)  # noqa: SLF001 - test hook
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot B", now)  # noqa: SLF001 - test hook
        window._refresh_list("plotters")  # noqa: SLF001 - test hook
        app.processEvents()
        assert window.plot_list.item(0).text() == "ZZ Plot B"

        # Simulate tool usage while the launcher is hidden, then restore it:
        # _restore_launcher should refresh the visible order from settings.
        window._settings.setValue("launcher_last_order/seq", 250)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot A", 250)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot B", 200)
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot A", now + 200.0)  # noqa: SLF001 - test hook
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot B", now)  # noqa: SLF001 - test hook
        window.hide()
        window._restore_launcher()  # noqa: SLF001 - test hook
        app.processEvents()
        assert window.plot_list.item(0).text() == "ZZ Plot A"
    finally:
        window.close()
        app.processEvents()


def test_graph_option_defaults_apply_figure_size_to_new_plot_tabs() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._graph_option_defaults_global = window._clean_graph_option_payload(  # noqa: SLF001 - test hook
            {
                "figure_width": 8.4,
                "figure_height": 5.6,
                "figure_width_auto": False,
                "figure_height_auto": False,
            }
        )

        fig = Figure(figsize=(3.0, 3.0))
        axes = fig.add_subplot(111)
        axes.set_title("Example")
        axes.set_xlabel("X")
        axes.set_ylabel("Y")
        canvas = FigureCanvas(fig)

        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)

        descriptor = TabDescriptor(
            kind="unit_test",
            title="Example",
            root_label="Example Plot",
            x_label="X",
            y_label="Y",
            canvas=canvas,
            axes=axes,
            lines={},
            metadata={"plugin": "Unit Test Plugin"},
        )
        index = window.tab_widget.addTab(tab, "Example Plot")
        window.tab_widget.setCurrentIndex(index)
        window._register_plot_tab(tab, canvas, axes, descriptor)  # noqa: SLF001 - test hook

        width_in, height_in = fig.get_size_inches()
        assert width_in == pytest.approx(8.4, rel=1e-3)
        assert height_in == pytest.approx(5.6, rel=1e-3)
    finally:
        window.close()
        app.processEvents()


def test_launcher_detects_pyplot_automation_flags() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(  # noqa: SLF001 - internal parser
        [
            "--pyplot-plugin",
            "Hysteresis Loops",
            "--pyplot-import",
            "sample_data/hysteresis_loops",
            "--pyplot-plot",
        ]
    )
    assert launcher_module._is_pyplot_automation_requested(args) is True  # noqa: SLF001


def test_launcher_pyplot_automation_generates_summary_and_artifacts(tmp_path: Path) -> None:
    _ensure_app()
    source = tmp_path / "250C sample.dat"
    source.write_text(
        "\n".join(
            [
                "150 6.2e-10",
                "75 6.1e-10",
                "0 -6.0e-10",
                "-75 -6.1e-10",
                "-150 -6.2e-10",
            ]
        ),
        encoding="utf-8",
    )
    screenshot_path = tmp_path / "window.png"
    plot_path = tmp_path / "plot.png"
    summary_path = tmp_path / "summary.json"
    args = argparse.Namespace(
        pyplot_list_plugins=False,
        pyplot_plugin="Hysteresis Loops",
        pyplot_import=[str(source)],
        pyplot_plot=True,
        pyplot_open_graph_format=True,
        pyplot_open_origin=False,
        pyplot_screenshot=str(screenshot_path),
        pyplot_plot_image=str(plot_path),
        pyplot_summary_json=str(summary_path),
        pyplot_show_window=False,
        pyplot_wait_ms=0,
        visual_check=False,
    )

    exit_code = launcher_module._run_pyplot_automation(args, [])  # noqa: SLF001 - internal automation hook

    assert exit_code == 0
    assert screenshot_path.exists()
    assert plot_path.exists()
    assert summary_path.exists()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["plugin"] == "Hysteresis Loops"
    assert summary["tab_count"] >= 1
    assert summary["current_tab_has_axes"] is True
    assert summary["graph_format_visible"] is True
