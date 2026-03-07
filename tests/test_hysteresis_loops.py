from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib
import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

matplotlib.use("Agg", force=True)

from PyQt6 import QtWidgets

from plotting.plugins.base import EmbeddedWidgetPlugin, PyPlotPlugin
from plotting.plugins.hysteresis_loops import core
from plotting.pyplot.app import PyPlotWorkbench
from plotting.pyplot.window import TOOLBAR_SECTION_PROPERTY


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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


def test_load_and_plot_hysteresis_loop() -> None:
    path = Path("sample_data/hysteresis_loops/FeSiBP 159_9 s1 200C.dat")
    x, y = core.load_loop(path)
    assert len(x) == len(y) and len(x) > 0
    fig = core.plot_loops([path], mode="Combined", show=False)
    assert hasattr(fig, "axes") and len(fig.axes) == 1


def test_load_loop_supports_txt_with_comments(tmp_path: Path) -> None:
    path = tmp_path / "example 250C.txt"
    path.write_text(
        "\n".join(
            [
                "# header",
                "100\t0.10\t1",
                "50\t0.05\t2",
                "0\t0.00\t3",
                "-50\t-0.05\t4",
            ]
        ),
        encoding="utf-8",
    )
    x, y = core.load_loop(path)
    assert x.tolist() == pytest.approx([100.0, 50.0, 0.0, -50.0])
    assert y.tolist() == pytest.approx([0.10, 0.05, 0.0, -0.05])


def test_plot_loops_returns_multiple_figures_for_multiple_groups(tmp_path: Path) -> None:
    first = tmp_path / "SampleA 200C.dat"
    second = tmp_path / "SampleB 250C.dat"
    first.write_text("100 0.1\n0 0.0\n-100 -0.1\n", encoding="utf-8")
    second.write_text("100 0.2\n0 0.0\n-100 -0.2\n", encoding="utf-8")

    figures = core.plot_loops([first, second], mode="Combined", show=False)
    assert isinstance(figures, list)
    assert len(figures) == 2


def test_hysteresis_loops_plugin_uses_modern_shared_pyplot_ui() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Hysteresis Loops")
    try:
        plugin = window._current_plugin  # noqa: SLF001 - test hook
        assert isinstance(plugin, PyPlotPlugin)
        assert not isinstance(plugin, EmbeddedWidgetPlugin)
        sections = _iter_toolbar_sections(plugin.settings_widget())
        assert "Plot mode" in sections
        assert "Appearance" not in sections
    finally:
        window.close()
        app.processEvents()


def test_hysteresis_loops_folder_import_loads_dat_files_and_enables_plot(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Hysteresis Loops")
    try:
        source_dir = tmp_path / "hysteresis"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "FeSiBP 159_9 s1 ascast.dat").write_text(
            "200 0.20\n100 0.10\n0 0.00\n-100 -0.10\n",
            encoding="utf-8",
        )
        (source_dir / "FeSiBP 159_9 s1 200C.dat").write_text(
            "200 0.25\n100 0.12\n0 0.00\n-100 -0.12\n",
            encoding="utf-8",
        )

        window._select_directories = (  # type: ignore[assignment]
            lambda _parent=None, *, title, start_dir: [str(source_dir)]
        )
        window._import_data_from_folder()
        app.processEvents()

        plugin = window._current_plugin  # noqa: SLF001 - test hook
        assert plugin is not None
        assert getattr(plugin, "_records", [])
        assert window.plot_button.isEnabled()
        assert window._worksheets  # noqa: SLF001 - `.dat` import path is supported
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_hysteresis_loops_generate_creates_plot_tabs_with_shared_labels(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Hysteresis Loops")
    try:
        source_dir = tmp_path / "hysteresis_generate"
        source_dir.mkdir(parents=True, exist_ok=True)
        first = source_dir / "FeSiBP 159_9 s1 ascast.dat"
        second = source_dir / "FeSiBP 159_9 s1 200C.dat"
        first.write_text("200 6.20e-10\n100 6.10e-10\n0 -6.00e-10\n-100 -6.10e-10\n", encoding="utf-8")
        second.write_text("200 6.25e-10\n100 6.12e-10\n0 -6.00e-10\n-100 -6.12e-10\n", encoding="utf-8")

        window._commit_selected_paths([first, second])  # noqa: SLF001 - test hook
        plugin = window._current_plugin  # noqa: SLF001 - test hook
        assert isinstance(plugin, PyPlotPlugin)
        plugin.settings_widget()
        mode_combo = getattr(plugin, "_mode_combo", None)
        assert isinstance(mode_combo, QtWidgets.QComboBox)
        mode_combo.setCurrentText("Combined")
        plugin.load_data()
        plugin.generate()
        app.processEvents()

        assert len(getattr(plugin, "_plot_tabs", [])) == 1
        plot_tabs = getattr(plugin, "_plot_tabs", [])
        if plot_tabs:
            window.tab_widget.setCurrentWidget(plot_tabs[0])
            app.processEvents()
        axes = window._current_axes()  # noqa: SLF001 - test hook
        assert axes is not None
        assert axes.get_xlabel() == "Magnetic field [A/m]"
        y_label = axes.get_ylabel()
        assert "Magnetic flux" in y_label
        assert "Wb" in y_label
        assert "\u00d710" in y_label
        legend_lines = axes.get_lines()
        assert legend_lines
        assert str(legend_lines[0].get_marker()).strip().lower() not in {"", "none"}
        offset_text = axes.yaxis.get_offset_text()
        assert not bool(offset_text.get_visible())
        assert window.open_origin_button.isEnabled()
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_hysteresis_loops_separate_mode_creates_one_tab_per_file(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Hysteresis Loops")
    try:
        first = tmp_path / "Sample 200C.dat"
        second = tmp_path / "Sample 250C.dat"
        first.write_text("200 0.20\n100 0.10\n0 0.00\n-100 -0.10\n", encoding="utf-8")
        second.write_text("200 0.22\n100 0.11\n0 0.00\n-100 -0.11\n", encoding="utf-8")

        window._commit_selected_paths([first, second])  # noqa: SLF001
        plugin = window._current_plugin  # noqa: SLF001
        plugin.settings_widget()
        mode_combo = getattr(plugin, "_mode_combo", None)
        assert isinstance(mode_combo, QtWidgets.QComboBox)
        mode_combo.setCurrentText("Separate")
        plugin.load_data()
        plugin.generate()
        app.processEvents()

        assert len(getattr(plugin, "_plot_tabs", [])) == 2
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_plot_new_preserves_existing_hysteresis_tabs_and_adds_only_new_data(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Hysteresis Loops")
    try:
        first = tmp_path / "SampleA 200C.dat"
        second = tmp_path / "SampleB 250C.dat"
        first.write_text("100 6.2e-10\n0 -6.0e-10\n-100 -6.2e-10\n", encoding="utf-8")
        second.write_text("100 5.2e-10\n0 -5.0e-10\n-100 -5.2e-10\n", encoding="utf-8")

        window._commit_selected_paths([first])  # noqa: SLF001
        plugin = window._current_plugin  # noqa: SLF001
        assert isinstance(plugin, PyPlotPlugin)
        plugin.settings_widget()
        mode_combo = getattr(plugin, "_mode_combo", None)
        assert isinstance(mode_combo, QtWidgets.QComboBox)
        mode_combo.setCurrentText("Combined")
        plugin.load_data()
        window._generate_plots()  # noqa: SLF001
        app.processEvents()
        assert len(getattr(plugin, "_plot_tabs", [])) == 1

        window._import_paths([second])  # noqa: SLF001 - marks second file as new
        scope_combo = getattr(window, "_plot_scope_combo", None)
        assert isinstance(scope_combo, QtWidgets.QComboBox)
        scope_combo.setCurrentIndex(scope_combo.findData("new"))
        window._generate_plots()  # noqa: SLF001
        app.processEvents()

        plot_tabs = getattr(plugin, "_plot_tabs", [])
        assert len(plot_tabs) == 2
        labels = []
        for tab in plot_tabs:
            index = window.tab_widget.indexOf(tab)
            if index >= 0:
                labels.append(window.tab_widget.tabText(index))
        assert any("SampleA" in label for label in labels)
        assert any("SampleB" in label for label in labels)
        assert not getattr(window, "_pending_new_plot_paths", None)  # noqa: SLF001
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()
