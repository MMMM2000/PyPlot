from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pandas as pd
from PyQt6 import QtWidgets

from plotting.pyplot.app import PyPlotWorkbench
from plotting.pyplot.window import (
    CreateGraphDialog,
    GraphSelectionDialog,
    WorkbookData,
    WorksheetColumnMeta,
    WorksheetData,
    _GraphTextDialogResult,
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def _find_tree_item_by_object(tree: QtWidgets.QTreeWidget, target: object) -> bool:
    def _walk(item: QtWidgets.QTreeWidgetItem) -> bool:
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("object") is target:
            return True
        for index in range(item.childCount()):
            if _walk(item.child(index)):
                return True
        return False

    from PyQt6 import QtCore

    for index in range(tree.topLevelItemCount()):
        if _walk(tree.topLevelItem(index)):
            return True
    return False


def test_blank_graph_supports_text_annotations_and_mathtext_formatting(monkeypatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        axes = window._current_axes()  # noqa: SLF001
        assert axes is not None

        artist = window._create_text_annotation(  # noqa: SLF001
            axes,
            x=1.0,
            y=2.0,
            payload=_GraphTextDialogResult(
                text="H",
                font_family="DejaVu Serif",
                font_size=14.0,
                color="#111111",
                bold=False,
                italic=False,
                underline=False,
            ),
        )
        assert artist is not None
        window._rebuild_object_manager_for_tab(window.tab_widget.currentWidget())  # noqa: SLF001
        assert _find_tree_item_by_object(window.object_tree, artist)

        window._set_format_selection(("text", (artist,)))  # noqa: SLF001
        window._apply_text_family("DejaVu Sans")  # noqa: SLF001
        family = artist.get_fontfamily()
        assert family and family[0] == "DejaVu Sans"

        monkeypatch.setattr(
            QtWidgets.QInputDialog,
            "getText",
            lambda *args, **kwargs: ("c", True),
        )
        window._apply_text_subscript()  # noqa: SLF001
        assert artist.get_text() == "$H_{c}$"
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_rectangle_annotation_tool_creates_shape_object() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        canvas = window._current_canvas()  # noqa: SLF001
        axes = window._current_axes()  # noqa: SLF001
        assert canvas is not None
        assert axes is not None

        window._set_annotation_tool("rectangle")  # noqa: SLF001
        press = SimpleNamespace(button=1, canvas=canvas, inaxes=axes, xdata=0.5, ydata=1.0, dblclick=False)
        move = SimpleNamespace(button=1, canvas=canvas, inaxes=axes, xdata=2.5, ydata=3.0, dblclick=False)
        release = SimpleNamespace(button=1, canvas=canvas, inaxes=axes, xdata=2.5, ydata=3.0, dblclick=False)
        window._handle_canvas_button_press(press)  # noqa: SLF001
        window._handle_canvas_motion(move)  # noqa: SLF001
        window._handle_canvas_button_release(release)  # noqa: SLF001

        shapes = window._iter_graph_object_shapes(axes)  # noqa: SLF001
        assert len(shapes) == 1
        shape = shapes[0]
        assert round(float(shape.get_width()), 2) == 2.0
        assert round(float(shape.get_height()), 2) == 2.0
        window._rebuild_object_manager_for_tab(window.tab_widget.currentWidget())  # noqa: SLF001
        assert _find_tree_item_by_object(window.object_tree, shape)
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_compose_graph_overlays_visible_series(monkeypatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        first_tab = window.tab_widget.currentWidget()
        first_axes = window._current_axes()  # noqa: SLF001
        assert first_axes is not None
        first_axes.set_title("First")
        first_axes.set_xlabel("Field")
        first_axes.set_ylabel("Flux")
        first_axes.plot([0, 1, 2], [1, 2, 3], label="A")

        window._create_blank_graph()  # noqa: SLF001
        second_tab = window.tab_widget.currentWidget()
        second_axes = window._current_axes()  # noqa: SLF001
        assert second_axes is not None
        second_axes.set_title("Second")
        second_axes.plot([0, 1, 2], [3, 2, 1], label="B")

        monkeypatch.setattr(
            GraphSelectionDialog,
            "exec",
            lambda self: int(QtWidgets.QDialog.DialogCode.Accepted),
        )
        monkeypatch.setattr(
            GraphSelectionDialog,
            "selected_tabs",
            lambda self: [first_tab, second_tab],
        )

        window._compose_graph_from_existing()  # noqa: SLF001

        descriptor = window._tab_descriptors.get(window.tab_widget.currentWidget())  # noqa: SLF001
        assert descriptor is not None
        assert descriptor.kind == "composed_graph"
        lines = descriptor.axes.get_lines()
        assert len(lines) == 2
        assert descriptor.x_label == "Field"
        assert descriptor.y_label == "Flux"
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_manual_graph_and_annotations_persist_in_project(tmp_path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    restored = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        axes = window._current_axes()  # noqa: SLF001
        assert axes is not None
        axes.plot([0, 1, 2], [2, 3, 4], label="A")
        artist = window._create_text_annotation(  # noqa: SLF001
            axes,
            x=1.0,
            y=2.5,
            payload=_GraphTextDialogResult(
                text="$H_{c}$",
                font_family="DejaVu Serif",
                font_size=14.0,
                color="#111111",
                bold=False,
                italic=False,
                underline=False,
            ),
        )
        assert artist is not None
        project_path = tmp_path / "manual_graph.pypj"
        window._write_project_file(project_path)  # noqa: SLF001

        restored._load_project_from_path(project_path)  # noqa: SLF001
        descriptors = list(restored._tab_descriptors.values())  # noqa: SLF001
        assert any(descriptor.kind == "manual_graph" for descriptor in descriptors)
        restored_axes = restored._current_axes()  # noqa: SLF001
        assert restored_axes is not None
        assert any(text.get_text() == "$H_{c}$" for text in restored_axes.texts)
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        restored._clear_project_dirty()  # noqa: SLF001
        window.close()
        restored.close()
        app.processEvents()


def test_create_graph_dialog_builds_exact_xy_series(monkeypatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        workbook = WorkbookData(key=("manual", "builder"), name="Builder Workbook", worksheets=[])
        worksheet = WorksheetData(
            key=(workbook.key, "Sheet1"),
            name="Sheet1",
            dataframe=pd.DataFrame(
                {
                    "field": [0.0, 1.0, 2.0],
                    "flux_a": [1.0, 2.0, 3.0],
                    "flux_b": [3.0, 2.0, 1.0],
                }
            ),
            columns={
                "field": WorksheetColumnMeta(long_name="Field", units="A/m"),
                "flux_a": WorksheetColumnMeta(long_name="Flux A", units="Wb"),
                "flux_b": WorksheetColumnMeta(long_name="Flux B", units="Wb"),
            },
            workbook_key=workbook.key,
            axis_roles="XYY",
        )
        workbook.worksheets = [worksheet.key]
        window._register_imported_workbook(workbook, [worksheet])  # noqa: SLF001

        monkeypatch.setattr(
            CreateGraphDialog,
            "exec",
            lambda self: int(QtWidgets.QDialog.DialogCode.Accepted),
        )
        monkeypatch.setattr(
            CreateGraphDialog,
            "payload",
            lambda self: {
                "title": "Built Graph",
                "x_label": "",
                "y_label": "",
                "show_grid": True,
                "series": [
                    {"worksheet": worksheet, "x_column": "field", "y_column": "flux_a", "label": "A"},
                    {"worksheet": worksheet, "x_column": "field", "y_column": "flux_b", "label": "B"},
                ],
            },
        )

        window._open_create_graph_dialog()  # noqa: SLF001

        descriptor = window._tab_descriptors.get(window.tab_widget.currentWidget())  # noqa: SLF001
        assert descriptor is not None
        assert descriptor.kind == "manual_graph"
        assert descriptor.title == "Built Graph"
        assert descriptor.x_label == "Field [A/m]"
        assert descriptor.y_label == "Flux A [Wb]"
        assert len(descriptor.axes.get_lines()) == 2
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_composed_graph_persists_in_project(tmp_path, monkeypatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    restored = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        first_tab = window.tab_widget.currentWidget()
        first_axes = window._current_axes()  # noqa: SLF001
        assert first_axes is not None
        first_axes.set_title("First")
        first_axes.set_xlabel("Field")
        first_axes.set_ylabel("Flux")
        first_axes.plot([0, 1, 2], [1, 2, 3], label="A")

        window._create_blank_graph()  # noqa: SLF001
        second_tab = window.tab_widget.currentWidget()
        second_axes = window._current_axes()  # noqa: SLF001
        assert second_axes is not None
        second_axes.plot([0, 1, 2], [3, 2, 1], label="B")

        monkeypatch.setattr(
            GraphSelectionDialog,
            "exec",
            lambda self: int(QtWidgets.QDialog.DialogCode.Accepted),
        )
        monkeypatch.setattr(
            GraphSelectionDialog,
            "selected_tabs",
            lambda self: [first_tab, second_tab],
        )
        window._compose_graph_from_existing()  # noqa: SLF001

        project_path = tmp_path / "composed_graph.pypj"
        window._write_project_file(project_path)  # noqa: SLF001
        restored._load_project_from_path(project_path)  # noqa: SLF001

        descriptors = list(restored._tab_descriptors.values())  # noqa: SLF001
        composed = [descriptor for descriptor in descriptors if descriptor.kind == "composed_graph"]
        assert composed
        assert len(composed[0].axes.get_lines()) == 2
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        restored._clear_project_dirty()  # noqa: SLF001
        window.close()
        restored.close()
        app.processEvents()


def test_plugin_graph_annotation_persists_in_project(tmp_path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Hysteresis Loops")
    restored = PyPlotWorkbench(initial_plotter="Hysteresis Loops")
    try:
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
        window._import_paths([source])  # noqa: SLF001
        app.processEvents()
        window._current_plugin.generate()
        app.processEvents()
        axes = window._current_axes()  # noqa: SLF001
        assert axes is not None
        artist = window._create_text_annotation(  # noqa: SLF001
            axes,
            x=0.0,
            y=5.5e-10,
            payload=_GraphTextDialogResult(
                text="HH",
                font_family="DejaVu Serif",
                font_size=14.0,
                color="#111111",
                bold=True,
                italic=False,
                underline=False,
            ),
        )
        assert artist is not None
        project_path = tmp_path / "plugin_annotation.pypj"
        window._write_project_file(project_path)  # noqa: SLF001

        restored._load_project_from_path(project_path)  # noqa: SLF001
        restored_axes = restored._current_axes()  # noqa: SLF001
        assert restored_axes is not None
        assert any(text.get_text() == "HH" for text in restored_axes.texts)
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        restored._clear_project_dirty()  # noqa: SLF001
        window.close()
        restored.close()
        app.processEvents()
