from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6 import QtCore, QtWidgets

from plotting.pyplot.app import PyPlotWorkbench
from plotting.pyplot.window import (
    PRIMARY_DOCK_EXPAND_THRESHOLD,
    PRIMARY_DOCK_EXPANDED_FRACTION,
    PRIMARY_DOCK_EXPANDED_MAX,
    PRIMARY_DOCK_DEFAULT_WIDTH,
)


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


def test_graph_formatting_controls_apply_to_current_axes() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        assert window._axes_by_tab
        axes = next(iter(window._axes_by_tab.values()))
        assert axes is not None
        line = axes.plot([1.0, 2.0, 3.0], [2.0, 4.0, 8.0], marker="o", label="Series")[0]

        controls = window._graph_format_controls
        assert isinstance(controls.get("title_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("x_label_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("y_label_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("x_scale_combo"), QtWidgets.QComboBox)
        assert isinstance(controls.get("y_scale_combo"), QtWidgets.QComboBox)

        controls["title_edit"].setText("Edited title")
        controls["x_label_edit"].setText("Edited X")
        controls["y_label_edit"].setText("Edited Y")
        controls["title_font_spin"].setValue(20)
        controls["label_font_spin"].setValue(14)
        controls["tick_font_spin"].setValue(11)
        controls["tick_length_spin"].setValue(9.0)
        controls["tick_width_spin"].setValue(1.8)
        controls["line_width_spin"].setValue(3.4)
        controls["marker_size_spin"].setValue(7.2)
        controls["x_scale_combo"].setCurrentIndex(controls["x_scale_combo"].findData("log"))
        controls["y_scale_combo"].setCurrentIndex(controls["y_scale_combo"].findData("log"))
        controls["x_min_edit"].setText("1")
        controls["x_max_edit"].setText("10")
        controls["y_min_edit"].setText("1")
        controls["y_max_edit"].setText("20")
        controls["show_grid_cb"].setChecked(True)
        controls["show_legend_cb"].setChecked(True)

        window._apply_graph_format(apply_all=True)

        assert axes.get_title() == "Edited title"
        assert axes.get_xlabel() == "Edited X"
        assert axes.get_ylabel() == "Edited Y"
        assert axes.get_xscale() == "log"
        assert axes.get_yscale() == "log"
        x_min, x_max = axes.get_xlim()
        y_min, y_max = axes.get_ylim()
        assert x_min == pytest.approx(1.0)
        assert x_max == pytest.approx(10.0)
        assert y_min == pytest.approx(1.0)
        assert y_max == pytest.approx(20.0)
        assert line.get_linewidth() == pytest.approx(3.4)
        assert line.get_markersize() == pytest.approx(7.2)
    finally:
        window.close()
        app.processEvents()


def test_export_current_graph_pdf_uses_pdf_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        captured: dict[str, object] = {}

        def _fake_save_graph_for_current_tab(
            *, parent: QtWidgets.QWidget | None = None, preferred_suffix: str | None = None
        ) -> bool:
            captured["parent"] = parent
            captured["suffix"] = preferred_suffix
            return True

        monkeypatch.setattr(window, "_save_graph_for_current_tab", _fake_save_graph_for_current_tab)
        window._export_current_graph_pdf()
        assert captured.get("suffix") == ".pdf"
    finally:
        window.close()
        app.processEvents()


def test_apply_graph_format_rebuilds_legend_with_visible_lines_only() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        assert window._axes_by_tab  # noqa: SLF001 - test hook
        axes = next(iter(window._axes_by_tab.values()))  # noqa: SLF001 - test hook
        assert axes is not None

        axes.plot([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], label="Visible")
        hidden = axes.plot([1.0, 2.0, 3.0], [2.0, 3.0, 4.0], label="Hidden")[0]
        hidden.set_visible(False)
        axes.legend(loc="best")

        controls = window._graph_format_controls
        controls["show_legend_cb"].setChecked(True)
        window._apply_graph_format(apply_all=True)

        legend = axes.get_legend()
        assert legend is not None
        labels = [text.get_text() for text in legend.get_texts()]
        assert labels == ["Visible"]
    finally:
        window.close()
        app.processEvents()


def test_save_graph_works_with_mdi_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        out_path = tmp_path / "saved_graph.png"
        monkeypatch.setattr(
            QtWidgets.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(out_path), "PNG Image (*.png)"),
        )
        assert window._save_graph_for_current_tab()
        assert out_path.exists()
    finally:
        window.close()
        app.processEvents()


def test_current_axes_and_canvas_resolve_with_mdi_proxy() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = window._current_axes()
        canvas = window._current_canvas()
        assert axes is not None
        assert canvas is not None
    finally:
        window.close()
        app.processEvents()


def test_single_mdi_subwindow_fills_viewport_after_arrange() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(1500, 950)
        window._create_blank_graph()
        app.processEvents()
        tab_proxy = window.tab_widget
        mdi = getattr(tab_proxy, "_mdi", None)
        assert isinstance(mdi, QtWidgets.QMdiArea)
        tab_proxy._arrange_subwindows()  # noqa: SLF001 - exercising internal layout logic
        app.processEvents()
        sub = mdi.activeSubWindow()
        assert isinstance(sub, QtWidgets.QMdiSubWindow)
        viewport = mdi.viewport().rect()
        margin = getattr(tab_proxy, "_layout_margin", 6)
        assert sub.geometry().width() >= viewport.width() - margin * 2
        assert sub.geometry().height() >= viewport.height() - margin * 2
    finally:
        window.close()
        app.processEvents()


def test_primary_dock_target_width_scales_on_large_window() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(PRIMARY_DOCK_EXPAND_THRESHOLD + 1200, 950)
        app.processEvents()
        dock = getattr(window, "project_dock", None)
        assert isinstance(dock, QtWidgets.QDockWidget)
        target = window._primary_dock_target_width(  # noqa: SLF001 - internal sizing helper
            dock,
            PRIMARY_DOCK_DEFAULT_WIDTH,
        )
        expected_min = int(window.width() * PRIMARY_DOCK_EXPANDED_FRACTION)
        if PRIMARY_DOCK_EXPANDED_MAX > 0:
            expected_min = min(expected_min, PRIMARY_DOCK_EXPANDED_MAX)
        assert target >= expected_min
    finally:
        window.close()
        app.processEvents()


def test_apply_graph_format_supports_tick_increment_and_count() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = next(iter(window._axes_by_tab.values()))  # noqa: SLF001 - test hook
        assert axes is not None
        axes.plot([0.0, 50.0, 100.0], [0.0, 5.0, 10.0], label="Series")

        controls = window._graph_format_controls
        assert isinstance(controls.get("x_tick_mode_combo"), QtWidgets.QComboBox)
        assert isinstance(controls.get("y_tick_mode_combo"), QtWidgets.QComboBox)
        assert isinstance(controls.get("x_tick_step_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("y_tick_count_spin"), QtWidgets.QSpinBox)

        controls["x_tick_mode_combo"].setCurrentIndex(
            controls["x_tick_mode_combo"].findData("step")
        )
        controls["x_tick_step_edit"].setText("25")
        controls["y_tick_mode_combo"].setCurrentIndex(
            controls["y_tick_mode_combo"].findData("count")
        )
        controls["y_tick_count_spin"].setValue(4)

        window._apply_graph_format(apply_all=False)

        x_ticks = axes.get_xticks()
        assert len(x_ticks) >= 3
        assert x_ticks[1] - x_ticks[0] == pytest.approx(25.0)
        assert axes.yaxis.get_major_locator().__class__.__name__ == "MaxNLocator"
    finally:
        window.close()
        app.processEvents()


def test_apply_graph_format_sets_figure_dimensions_and_axes_aspect() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = next(iter(window._axes_by_tab.values()))  # noqa: SLF001 - test hook
        assert axes is not None
        axes.plot([0.0, 1.0, 2.0], [0.0, 1.0, 4.0], label="Series")

        controls = window._graph_format_controls
        assert isinstance(controls.get("figure_width_spin"), QtWidgets.QDoubleSpinBox)
        assert isinstance(controls.get("figure_height_spin"), QtWidgets.QDoubleSpinBox)
        assert isinstance(controls.get("axes_aspect_combo"), QtWidgets.QComboBox)
        assert isinstance(controls.get("axes_aspect_ratio_spin"), QtWidgets.QDoubleSpinBox)

        controls["figure_width_spin"].setValue(8.0)
        controls["figure_height_spin"].setValue(5.0)
        controls["axes_aspect_combo"].setCurrentIndex(
            controls["axes_aspect_combo"].findData("custom")
        )
        controls["axes_aspect_ratio_spin"].setValue(1.5)
        window._apply_graph_format(apply_all=False)

        figure = axes.figure
        assert figure is not None
        width, height = figure.get_size_inches()
        assert width == pytest.approx(8.0, rel=1e-2)
        assert height == pytest.approx(5.0, rel=1e-2)
        assert float(axes.get_aspect()) == pytest.approx(1.5)
    finally:
        window.close()
        app.processEvents()


def test_double_click_title_works_when_click_is_outside_axes(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001 - test hook
        assert descriptor is not None
        axes = descriptor.axes
        canvas = descriptor.canvas
        assert axes is not None
        assert canvas is not None
        axes.set_title("Clickable Title")
        canvas.draw()

        renderer = canvas.get_renderer()
        bbox = axes.title.get_window_extent(renderer=renderer)
        called: dict[str, object] = {}

        def _capture(target_axes, field):
            called["axes"] = target_axes
            called["field"] = field

        monkeypatch.setattr(window, "_edit_axes_text_from_double_click", _capture)
        event = SimpleNamespace(
            dblclick=True,
            inaxes=None,
            x=float(bbox.x0 + bbox.width * 0.5),
            y=float(bbox.y0 + bbox.height * 0.5),
            canvas=canvas,
        )
        window._handle_canvas_button_press(event)  # noqa: SLF001 - event hook
        assert called.get("axes") is axes
        assert called.get("field") == "title"
    finally:
        window.close()
        app.processEvents()


def test_object_manager_lists_line_items_for_each_axes() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        assert window._axes_by_tab  # noqa: SLF001 - test hook
        tab, axes = next(iter(window._axes_by_tab.items()))  # noqa: SLF001 - test hook
        assert axes is not None
        secondary = axes.twinx()
        axes.plot([0.0, 1.0], [1.0, 2.0], label="Left axis")
        secondary.plot([0.0, 1.0], [2.0, 3.0], label="Right axis")

        index = window.tab_widget.indexOf(tab)
        if index >= 0:
            window.tab_widget.setCurrentIndex(index)
        window._rebuild_object_manager_for_tab(tab)

        tree = getattr(window, "object_tree", None)
        assert isinstance(tree, QtWidgets.QTreeWidget)
        root = tree.topLevelItem(0)
        assert root is not None

        labels: list[str] = []

        def _collect(item: QtWidgets.QTreeWidgetItem) -> None:
            for idx in range(item.childCount()):
                child = item.child(idx)
                data = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("kind") == "line":
                    labels.append(child.text(0))
                _collect(child)

        _collect(root)
        assert set(labels) >= {"Left axis", "Right axis"}
    finally:
        window.close()
        app.processEvents()


def test_project_explorer_graph_item_activation_switches_to_tab() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        window._create_blank_graph()
        app.processEvents()

        tree = getattr(window, "project_tree", None)
        assert isinstance(tree, QtWidgets.QTreeWidget)
        assert tree.topLevelItemCount() > 0

        plots_root = tree.topLevelItem(0)
        assert plots_root is not None
        assert plots_root.text(0) == "Plots"
        assert plots_root.childCount() >= 2

        first_plot_item = plots_root.child(0)
        second_plot_item = plots_root.child(1)
        assert first_plot_item is not None
        assert second_plot_item is not None

        first_payload = first_plot_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        second_payload = second_plot_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        assert isinstance(first_payload, tuple) and len(first_payload) >= 2
        assert isinstance(second_payload, tuple) and len(second_payload) >= 2
        assert first_payload[0] == "graph"
        assert second_payload[0] == "graph"

        first_tab = first_payload[1]
        second_tab = second_payload[1]
        assert isinstance(first_tab, QtWidgets.QWidget)
        assert isinstance(second_tab, QtWidgets.QWidget)

        second_index = window.tab_widget.indexOf(second_tab)
        assert second_index >= 0
        window.tab_widget.setCurrentIndex(second_index)
        assert window.tab_widget.currentWidget() is second_tab

        window._dispatch_project_item_activation(first_plot_item, 0)
        assert window.tab_widget.currentWidget() is first_tab
    finally:
        window.close()
        app.processEvents()


def test_apply_axes_text_value_updates_descriptor_and_tree() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001 - test hook
        assert descriptor is not None
        axes = descriptor.axes
        assert axes is not None

        assert window._apply_axes_text_value(axes, field="title", value="Updated Title")
        assert window._apply_axes_text_value(axes, field="x_label", value="Updated X")
        assert window._apply_axes_text_value(axes, field="y_label", value="Updated Y")
        assert axes.get_title() == "Updated Title"
        assert axes.get_xlabel() == "Updated X"
        assert axes.get_ylabel() == "Updated Y"
        assert descriptor.title == "Updated Title"
        assert descriptor.x_label == "Updated X"
        assert descriptor.y_label == "Updated Y"

        tree_item = window._graph_tree_items.get(tab)  # noqa: SLF001 - test hook
        assert isinstance(tree_item, QtWidgets.QTreeWidgetItem)
        assert tree_item.text(1) == "Updated Title"
    finally:
        window.close()
        app.processEvents()


def test_apply_axis_scale_settings_supports_log_and_validation() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001 - test hook
        assert descriptor is not None
        axes = descriptor.axes
        assert axes is not None

        ok = window._apply_axis_scale_settings(
            axes,
            "x",
            scale="log",
            auto_limits=False,
            lower=1.0,
            upper=10.0,
            show_dialog_errors=False,
        )
        assert ok
        assert axes.get_xscale() == "log"
        x_min, x_max = axes.get_xlim()
        assert x_min == pytest.approx(1.0)
        assert x_max == pytest.approx(10.0)

        failed = window._apply_axis_scale_settings(
            axes,
            "y",
            scale="log",
            auto_limits=False,
            lower=-1.0,
            upper=10.0,
            show_dialog_errors=False,
        )
        assert not failed
    finally:
        window.close()
        app.processEvents()
