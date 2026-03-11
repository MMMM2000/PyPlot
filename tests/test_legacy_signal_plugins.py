from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PyQt6 import QtWidgets

from plotting.pyplot.app import PyPlotWorkbench
from plotting.plugins import builtin_plugin_registry
from plotting.plugins.base import EmbeddedWidgetPlugin


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def test_builtin_pyplot_registry_has_no_embedded_widget_plugins() -> None:
    for plugin_cls in builtin_plugin_registry().values():
        assert not issubclass(plugin_cls, EmbeddedWidgetPlugin)


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


def test_maxion_continuous_is_native_pyplot_plugin() -> None:
    plugin_cls = builtin_plugin_registry()["Maxion Continuous"]
    assert not issubclass(plugin_cls, EmbeddedWidgetPlugin)


def test_hsw_load_compare_is_native_pyplot_plugin() -> None:
    plugin_cls = builtin_plugin_registry()["Hsw Load Compare"]
    assert not issubclass(plugin_cls, EmbeddedWidgetPlugin)


def test_hsw_distribution_is_native_pyplot_plugin() -> None:
    plugin_cls = builtin_plugin_registry()["Hsw Distribution"]
    assert not issubclass(plugin_cls, EmbeddedWidgetPlugin)


def test_pdf_plotter_is_native_pyplot_plugin() -> None:
    plugin_cls = builtin_plugin_registry()["PDF Plotter"]
    assert not issubclass(plugin_cls, EmbeddedWidgetPlugin)


def test_strain_3d_plot_is_native_pyplot_plugin() -> None:
    plugin_cls = builtin_plugin_registry()["Strain 3D Plot"]
    assert not issubclass(plugin_cls, EmbeddedWidgetPlugin)


def test_maxion_continuous_smoke_load_and_generate() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Maxion Continuous")
    try:
        source = Path("sample_data/Maxion/1 final 2 coils.txt").resolve()
        window._commit_selected_paths([source])  # noqa: SLF001
        plugin = window._current_plugin  # noqa: SLF001
        assert plugin is not None
        plugin.load_data()
        plugin.generate()
        app.processEvents()
        assert len(getattr(plugin, "_plot_tabs", [])) >= 3
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_hsw_distribution_smoke_load_and_generate(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Hsw Distribution")
    try:
        paths: list[Path] = []
        for suffix in ("A", "B"):
            path = tmp_path / f"dist_{suffix}.txt"
            path.write_text("1,0;2,0\n1,1;2,1\n1,2;2,2\n1,3;2,3\n", encoding="utf-8")
            paths.append(path)
        window._commit_selected_paths(paths)  # noqa: SLF001
        plugin = window._current_plugin  # noqa: SLF001
        assert plugin is not None
        plugin.load_data()
        plugin.generate()
        app.processEvents()
        assert len(getattr(plugin, "_plot_tabs", [])) >= 1
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_pdf_plotter_smoke_load_and_generate() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="PDF Plotter")
    try:
        source = Path("sample_data/pdf_data/sample1.pdf").resolve()
        window._commit_selected_paths([source])  # noqa: SLF001
        plugin = window._current_plugin  # noqa: SLF001
        assert plugin is not None
        plugin.load_data()
        plugin.generate()
        app.processEvents()
        assert len(getattr(plugin, "_plot_tabs", [])) >= 1
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_strain_3d_plot_smoke_load_and_generate(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Strain 3D Plot")
    try:
        source = tmp_path / "strain_input.xlsx"
        import pandas as pd

        pd.DataFrame(
            {
                "Composition": ["Ni50Fe27Ga23", "Ni50Fe27Ga23", "Ni50Fe27Ga23"],
                "Microwire": ["5_4", "5_5", "5_6"],
                "Strain (%)": [1.0, 2.0, 3.0],
                "Temperature (°C)": [20.0, 40.0, 60.0],
                "Stress (MPa)": [100.0, 120.0, 140.0],
            }
        ).to_excel(source, index=False)
        window._commit_selected_paths([source])  # noqa: SLF001
        plugin = window._current_plugin  # noqa: SLF001
        assert plugin is not None
        plugin.load_data()
        plugin.generate()
        app.processEvents()
        assert len(getattr(plugin, "_plot_tabs", [])) >= 1
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_hsw_load_compare_smoke_load_and_generate(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Hsw Load Compare")
    try:
        paths: list[Path] = []
        for load in ("2,5", "5", "7,5"):
            path = tmp_path / f"FeSiB 85_10 s2-2a 47mA {load}a.txt"
            path.write_text("1,0;2,0\n1,1;2,1\n1,2;2,2\n", encoding="utf-8")
            paths.append(path)
        window._commit_selected_paths(paths)  # noqa: SLF001
        plugin = window._current_plugin  # noqa: SLF001
        assert plugin is not None
        plugin.load_data()
        plugin.generate()
        app.processEvents()
        assert len(getattr(plugin, "_plot_tabs", [])) >= 1
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()
