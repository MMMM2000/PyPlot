from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib import ticker as mticker
from PIL import Image

from plotting.pyplot.app import PyPlotWorkbench
from plotting.pyplot.window import (
    CreateGraphDialog,
    FigureLayoutDialog,
    GraphSelectionDialog,
    TabDescriptor,
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


def test_origin_secondary_export_layer_hides_redundant_top_x_axis() -> None:
    class _Layer:
        def __init__(self) -> None:
            self.ints: dict[str, int] = {}
            self.commands: list[str] = []

        def set_int(self, key: str, value: int) -> None:
            self.ints[key] = int(value)

        def lt_exec(self, command: str) -> bool:
            self.commands.append(command)
            return True

    window = PyPlotWorkbench.__new__(PyPlotWorkbench)
    layer = _Layer()

    window._configure_origin_layer_axes(layer, secondary_axes_only=True)  # noqa: SLF001

    assert layer.ints["x.showAxes"] == 0
    assert layer.ints["x2.showlabel"] == 0
    assert layer.ints["y.showAxes"] == 2
    assert layer.ints["y2.showlabel"] == 1
    assert "layer.x2.showlabel=0;" in layer.commands
    assert 'layer.x2.title$="";' in layer.commands


def test_origin_secondary_export_layer_can_show_real_top_x_axis() -> None:
    class _Layer:
        def __init__(self) -> None:
            self.ints: dict[str, int] = {}
            self.commands: list[str] = []

        def set_int(self, key: str, value: int) -> None:
            self.ints[key] = int(value)

        def lt_exec(self, command: str) -> bool:
            self.commands.append(command)
            return True

    window = PyPlotWorkbench.__new__(PyPlotWorkbench)
    layer = _Layer()

    window._configure_origin_layer_axes(  # noqa: SLF001
        layer,
        secondary_axes_only=True,
        show_top_x=True,
    )

    assert layer.ints["x.showAxes"] == 2
    assert layer.ints["x.showlabel"] == 0
    assert layer.ints["x2.showlabel"] == 1
    assert "layer.x.showAxes=2;" in layer.commands
    assert "layer.x.showlabel=0;" in layer.commands
    assert "layer.x2.showlabel=1;" in layer.commands
    assert 'layer.x2.title$="";' not in layer.commands


def test_origin_export_layer_frame_uses_page_units_and_safe_margins() -> None:
    class _Layer:
        def __init__(self) -> None:
            self.floats: dict[str, float] = {}
            self.commands: list[str] = []

        def set_float(self, key: str, value: float) -> None:
            self.floats[key] = float(value)

        def lt_exec(self, command: str) -> bool:
            self.commands.append(command)
            return True

    window = PyPlotWorkbench.__new__(PyPlotWorkbench)
    layer = _Layer()

    window._set_origin_layer_frame(layer)  # noqa: SLF001

    assert layer.floats == {
        "top": 18.0,
        "left": 22.0,
        "width": 52.0,
        "height": 56.0,
    }
    assert (
        "layer -u 1; layer 52.000 56.000 22.000 18.000; "
        "layer.top=18.000; layer.left=22.000; "
        "layer.width=52.000; layer.height=56.000;"
    ) in layer.commands


def test_origin_export_legend_is_shrunk_and_placed_away_from_right_edge() -> None:
    class _Legend:
        def __init__(self) -> None:
            self.floats: dict[str, float] = {}
            self.ints: dict[str, int] = {}

        def set_float(self, key: str, value: float) -> None:
            self.floats[key] = float(value)

        def set_int(self, key: str, value: int) -> None:
            self.ints[key] = int(value)

    class _Layer:
        def __init__(self) -> None:
            self.legend = _Legend()
            self.activated = False
            self.commands: list[str] = []

        def activate(self) -> None:
            self.activated = True

        def label(self, name: str) -> object | None:
            return self.legend if name == "Legend" else None

        def lt_exec(self, command: str) -> bool:
            self.commands.append(command)
            return True

    window = PyPlotWorkbench.__new__(PyPlotWorkbench)
    layer = _Layer()

    window._style_origin_legend_for_export(layer)  # noqa: SLF001

    assert layer.activated is True
    assert layer.legend.floats["fsize"] == 8.0
    assert layer.legend.ints["show"] == 1
    assert "legend.fsize=8;" in layer.commands
    assert "legend.x=layer.x.from + legend.dx / 2;" in layer.commands
    assert "legend.y=layer.y.to - legend.dy / 2;" in layer.commands


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


def test_text_strikethrough_toggle_and_persistence(tmp_path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    restored = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        axes = window._current_axes()  # noqa: SLF001
        artist = window._create_text_annotation(  # noqa: SLF001
            axes,
            x=1.0,
            y=1.0,
            payload=_GraphTextDialogResult(
                text="Strike",
                font_family="DejaVu Serif",
                font_size=14.0,
                color="#111111",
                bold=False,
                italic=False,
                underline=False,
                strikethrough=False,
            ),
        )
        assert artist is not None
        window._set_format_selection(("text", (artist,)))  # noqa: SLF001
        window._apply_text_strikethrough(True)  # noqa: SLF001
        assert getattr(artist, "_mw_strikethrough", False) is True
        assert "\u0336" in artist.get_text()
        project_path = tmp_path / "strike_graph.pypj"
        window._write_project_file(project_path)  # noqa: SLF001
        restored._load_project_from_path(project_path)  # noqa: SLF001
        restored_axes = restored._current_axes()  # noqa: SLF001
        assert restored_axes is not None
        strikes = [text for text in restored_axes.texts if getattr(text, "_mw_strikethrough", False)]
        assert strikes
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        restored._clear_project_dirty()  # noqa: SLF001
        window.close()
        restored.close()
        app.processEvents()


def test_annotation_text_alignment_moves_selected_labels() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        axes = window._current_axes()  # noqa: SLF001
        assert axes is not None
        first = window._create_text_annotation(  # noqa: SLF001
            axes,
            x=1.0,
            y=2.0,
            payload=_GraphTextDialogResult(
                text="A",
                font_family="DejaVu Serif",
                font_size=14.0,
                color="#111111",
                bold=False,
                italic=False,
                underline=False,
            ),
        )
        second = window._create_text_annotation(  # noqa: SLF001
            axes,
            x=3.0,
            y=4.0,
            payload=_GraphTextDialogResult(
                text="B",
                font_family="DejaVu Serif",
                font_size=14.0,
                color="#111111",
                bold=False,
                italic=False,
                underline=False,
            ),
        )
        assert first is not None and second is not None
        window._set_format_selection(("text", (first, second)))  # noqa: SLF001
        window._align_selected_text_annotations("left")  # noqa: SLF001
        assert first.get_position()[0] == second.get_position()[0] == 1.0
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_annotation_snap_helper_uses_existing_text_positions() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        axes = window._current_axes()  # noqa: SLF001
        assert axes is not None
        existing = window._create_text_annotation(  # noqa: SLF001
            axes,
            x=1.0,
            y=1.0,
            payload=_GraphTextDialogResult(
                text="A",
                font_family="DejaVu Serif",
                font_size=14.0,
                color="#111111",
                bold=False,
                italic=False,
                underline=False,
            ),
        )
        assert existing is not None
        snapped_x, snapped_y, guide_x, guide_y = window._snap_annotation_point(axes, 1.01, 1.02)  # noqa: SLF001
        assert snapped_x == 1.0
        assert snapped_y == 1.0
        assert guide_x == 1.0
        assert guide_y == 1.0
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_annotation_copy_paste_and_duplicate() -> None:
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
                text="A",
                font_family="DejaVu Serif",
                font_size=14.0,
                color="#111111",
                bold=False,
                italic=False,
                underline=False,
            ),
        )
        assert artist is not None
        window._set_format_selection(("text", (artist,)))  # noqa: SLF001
        window._copy_selected_graph_objects()  # noqa: SLF001
        window._paste_graph_objects()  # noqa: SLF001
        window._duplicate_selected_graph_objects()  # noqa: SLF001
        texts = window._iter_graph_object_texts(axes)  # noqa: SLF001
        assert len(texts) == 3
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_single_visible_graph_subwindow_uses_half_workspace_width() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window.resize(1600, 980)
        window.show()
        app.processEvents()
        window._create_blank_graph()  # noqa: SLF001
        app.processEvents()

        arranger = getattr(window.tab_widget, "_arrange_subwindows", None)
        if callable(arranger):
            arranger()
        app.processEvents()

        current = window.tab_widget.currentWidget()
        assert current is not None
        subwindow_for = getattr(window.tab_widget, "_subwindow_for", None)
        assert callable(subwindow_for)
        sub = subwindow_for(current)
        assert sub is not None
        viewport = window.tab_widget._mdi.viewport().rect()  # noqa: SLF001
        assert viewport.width() > 0
        expected_max = max(1, (viewport.width() - 24) // 2)
        assert sub.width() <= expected_max + 6
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_arrow_style_persists_in_payload() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        axes = window._current_axes()  # noqa: SLF001
        assert axes is not None
        window._set_annotation_tool("arrow")  # noqa: SLF001
        canvas = window._current_canvas()  # noqa: SLF001
        press = SimpleNamespace(button=1, canvas=canvas, inaxes=axes, xdata=0.5, ydata=1.0, dblclick=False)
        move = SimpleNamespace(button=1, canvas=canvas, inaxes=axes, xdata=2.5, ydata=1.0, dblclick=False)
        release = SimpleNamespace(button=1, canvas=canvas, inaxes=axes, xdata=2.5, ydata=1.0, dblclick=False)
        window._handle_canvas_button_press(press)  # noqa: SLF001
        window._handle_canvas_motion(move)  # noqa: SLF001
        window._handle_canvas_button_release(release)  # noqa: SLF001
        arrow = next(shape for shape in window._iter_graph_object_shapes(axes) if shape.__class__.__name__ == "FancyArrowPatch")  # noqa: SLF001
        window._set_format_selection(("shape", arrow))  # noqa: SLF001
        combo = window._format_controls.arrow_style_combo  # noqa: SLF001
        assert combo is not None
        combo.setCurrentIndex(combo.findData("<->"))
        payloads = window._serialize_graph_object_payloads_for_axes(axes)  # noqa: SLF001
        arrow_payload = next(entry for entry in payloads if entry.get("kind") == "arrow")
        assert arrow_payload["arrowstyle"] == "<->"
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_shape_selection_can_adjust_zorder() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        axes = window._current_axes()  # noqa: SLF001
        assert axes is not None
        window._set_annotation_tool("rectangle")  # noqa: SLF001
        canvas = window._current_canvas()  # noqa: SLF001
        press = SimpleNamespace(button=1, canvas=canvas, inaxes=axes, xdata=0.5, ydata=1.0, dblclick=False)
        move = SimpleNamespace(button=1, canvas=canvas, inaxes=axes, xdata=2.5, ydata=3.0, dblclick=False)
        release = SimpleNamespace(button=1, canvas=canvas, inaxes=axes, xdata=2.5, ydata=3.0, dblclick=False)
        window._handle_canvas_button_press(press)  # noqa: SLF001
        window._handle_canvas_motion(move)  # noqa: SLF001
        window._handle_canvas_button_release(release)  # noqa: SLF001
        shape = next(iter(window._iter_graph_object_shapes(axes)))  # noqa: SLF001
        window._set_format_selection(("shape", shape))  # noqa: SLF001
        spin = window._format_controls.zorder_spin  # noqa: SLF001
        assert spin is not None
        spin.setValue(15.0)
        assert shape.get_zorder() == 15.0
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_annotation_paste_targets_selected_panel_axes() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        first_axes = window._current_axes()  # noqa: SLF001
        assert first_axes is not None
        first = window._create_text_annotation(  # noqa: SLF001
            first_axes,
            x=1.0,
            y=1.0,
            payload=_GraphTextDialogResult(
                text="A",
                font_family="DejaVu Serif",
                font_size=14.0,
                color="#111111",
                bold=False,
                italic=False,
                underline=False,
            ),
        )
        window._create_blank_graph()  # noqa: SLF001
        second_axes = window._current_axes()  # noqa: SLF001
        assert second_axes is not None
        second = window._create_text_annotation(  # noqa: SLF001
            second_axes,
            x=0.5,
            y=0.5,
            payload=_GraphTextDialogResult(
                text="B",
                font_family="DejaVu Serif",
                font_size=14.0,
                color="#111111",
                bold=False,
                italic=False,
                underline=False,
            ),
        )
        assert first is not None and second is not None
        window._set_format_selection(("text", (first,)))  # noqa: SLF001
        window._copy_selected_graph_objects()  # noqa: SLF001
        window._set_format_selection(("text", (second,)))  # noqa: SLF001
        window._paste_graph_objects()  # noqa: SLF001
        assert len(window._iter_graph_object_texts(first_axes)) == 1  # noqa: SLF001
        assert len(window._iter_graph_object_texts(second_axes)) == 2  # noqa: SLF001
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


def test_callout_annotation_serializes_as_boxed_text(tmp_path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    restored = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        axes = window._current_axes()  # noqa: SLF001
        artist = window._create_text_annotation(  # noqa: SLF001
            axes,
            x=1.0,
            y=1.0,
            payload=_GraphTextDialogResult(
                text="Note",
                font_family="DejaVu Serif",
                font_size=14.0,
                color="#111111",
                bold=False,
                italic=False,
                underline=False,
            ),
        )
        assert artist is not None
        artist.set_bbox({"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "#444444", "linewidth": 0.8})
        setattr(artist, "_mw_callout_box", True)
        payloads = window._serialize_graph_object_payloads_for_axes(axes)  # noqa: SLF001
        assert any(entry.get("callout_box") for entry in payloads)
        project_path = tmp_path / "callout_graph.pypj"
        window._write_project_file(project_path)  # noqa: SLF001
        restored._load_project_from_path(project_path)  # noqa: SLF001
        restored_axes = restored._current_axes()  # noqa: SLF001
        assert restored_axes is not None
        callouts = [text for text in restored_axes.texts if text.get_text() == "Note"]
        assert callouts
        assert callouts[0].get_bbox_patch() is not None
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        restored._clear_project_dirty()  # noqa: SLF001
        window.close()
        restored.close()
        app.processEvents()


def test_create_figure_builder_creates_shared_layout(monkeypatch) -> None:
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
        second_axes.set_xlabel("Field")
        second_axes.set_ylabel("Flux")
        second_axes.plot([0, 1, 2], [3, 2, 1], label="B")

        monkeypatch.setattr(
            FigureLayoutDialog,
            "exec",
            lambda self: int(QtWidgets.QDialog.DialogCode.Accepted),
        )
        monkeypatch.setattr(
            FigureLayoutDialog,
            "payload",
            lambda self: {
                "title": "Two Panel Figure",
                "rows": 2,
                "cols": 1,
                "share_x": True,
                "share_y": True,
                "panel_labels": "lower",
                "figure_width": 7.10,
                "figure_height": 4.80,
                "source_tabs": [first_tab, second_tab],
            },
        )

        window._open_create_figure_dialog()  # noqa: SLF001

        descriptor = window._tab_descriptors.get(window.tab_widget.currentWidget())  # noqa: SLF001
        assert descriptor is not None
        assert descriptor.kind == "layout_graph"
        figure = descriptor.axes.figure
        visible_axes = [axes for axes in figure.axes if axes.get_visible()]
        assert len(visible_axes) == 2
        size = figure.get_size_inches()
        assert round(float(size[0]), 2) == 7.10
        assert round(float(size[1]), 2) == 4.80
        xlims = [tuple(float(v) for v in axes.get_xlim()) for axes in visible_axes]
        ylims = [tuple(float(v) for v in axes.get_ylim()) for axes in visible_axes]
        assert xlims[0] == xlims[1]
        assert ylims[0] == ylims[1]
        assert any(text.get_text() == "(a)" for text in visible_axes[0].texts)
        assert any(text.get_text() == "(b)" for text in visible_axes[1].texts)
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_layout_graph_persists_in_project(tmp_path, monkeypatch) -> None:
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
        second_axes.set_title("Second")
        second_axes.set_xlabel("Field")
        second_axes.set_ylabel("Flux")
        second_axes.plot([0, 1, 2], [3, 2, 1], label="B")

        monkeypatch.setattr(
            FigureLayoutDialog,
            "exec",
            lambda self: int(QtWidgets.QDialog.DialogCode.Accepted),
        )
        monkeypatch.setattr(
            FigureLayoutDialog,
            "payload",
            lambda self: {
                "title": "Two Panel Figure",
                "rows": 2,
                "cols": 1,
                "share_x": True,
                "share_y": True,
                "panel_labels": "lower",
                "figure_width": 7.10,
                "figure_height": 4.80,
                "source_tabs": [first_tab, second_tab],
            },
        )
        window._open_create_figure_dialog()  # noqa: SLF001
        project_path = tmp_path / "layout_graph.pypj"
        window._write_project_file(project_path)  # noqa: SLF001

        restored._load_project_from_path(project_path)  # noqa: SLF001
        descriptors = list(restored._tab_descriptors.values())  # noqa: SLF001
        layouts = [descriptor for descriptor in descriptors if descriptor.kind == "layout_graph"]
        assert layouts
        figure = layouts[0].axes.figure
        visible_axes = [axes for axes in figure.axes if axes.get_visible()]
        assert len(visible_axes) == 2
        assert any(text.get_text() == "(a)" for text in visible_axes[0].texts)
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        restored._clear_project_dirty()  # noqa: SLF001
        window.close()
        restored.close()
        app.processEvents()


def test_figure_layout_dialog_defaults_to_mm_and_converts_to_inches() -> None:
    app = _ensure_app()
    payload_tab = QtWidgets.QWidget()
    dialog = FigureLayoutDialog(
        None,
        entries=[("Graph 1", "Graph 1", payload_tab)],
    )
    try:
        assert dialog.units_combo.currentData() == "mm"
        assert dialog.width_label.text() == "Width (mm)"
        assert dialog.height_label.text() == "Height (mm)"
        dialog.title_edit.setText("Figure")
        dialog.rows_spin.setValue(1)
        dialog.cols_spin.setValue(1)
        dialog.width_spin.setValue(25.4)
        dialog.height_spin.setValue(50.8)
        dialog.accept()
        payload = dialog.payload()
        assert payload is not None
        assert payload["figure_units"] == "mm"
        assert payload["figure_width"] == 1.0
        assert payload["figure_height"] == 2.0
        assert payload["panels"][0]["tab"] is payload_tab
    finally:
        dialog.close()
        app.processEvents()


def test_figure_layout_external_legend_and_panel_override(monkeypatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        first_tab = window.tab_widget.currentWidget()
        first_axes = window._current_axes()  # noqa: SLF001
        assert first_axes is not None
        first_axes.set_title("First")
        first_axes.plot([0, 1, 2], [1, 2, 3], label="A")

        window._create_blank_graph()  # noqa: SLF001
        second_tab = window.tab_widget.currentWidget()
        second_axes = window._current_axes()  # noqa: SLF001
        assert second_axes is not None
        second_axes.set_title("Second")
        second_axes.plot([0, 1, 2], [3, 2, 1], label="B")

        monkeypatch.setattr(
            FigureLayoutDialog,
            "exec",
            lambda self: int(QtWidgets.QDialog.DialogCode.Accepted),
        )
        monkeypatch.setattr(
            FigureLayoutDialog,
            "payload",
            lambda self: {
                "title": "Legend Figure",
                "rows": 2,
                "cols": 1,
                "share_x": True,
                "share_y": False,
                "panel_labels": "lower",
                "external_legend": True,
                "legend_placement": "right",
                "figure_units": "mm",
                "figure_width": 180 / 25.4,
                "figure_height": 120 / 25.4,
                "style_preset": "mono",
                "minor_ticks": True,
                "tick_direction": "in",
                "notation": "scientific",
                "x_ticks": "0,1,2",
                "y_ticks": "",
                "wspace": 0.12,
                "hspace": 0.18,
                "left_margin": 0.08,
                "right_margin": 0.82,
                "top_margin": 0.90,
                "bottom_margin": 0.12,
                "panels": [
                    {"tab": first_tab, "panel_title": "Panel One"},
                    {"tab": second_tab, "panel_title": "Panel Two"},
                ],
            },
        )

        window._open_create_figure_dialog()  # noqa: SLF001

        descriptor = window._tab_descriptors.get(window.tab_widget.currentWidget())  # noqa: SLF001
        assert descriptor is not None
        assert descriptor.kind == "layout_graph"
        figure = descriptor.axes.figure
        visible_axes = [axes for axes in figure.axes if axes.get_visible()]
        assert visible_axes[0].get_title() == "Panel One"
        assert visible_axes[1].get_title() == "Panel Two"
        assert figure.legends
        assert list(visible_axes[0].get_xticks())[:3] == [0.0, 1.0, 2.0]
        assert str(visible_axes[0].lines[0].get_color()).lower() in {"#111111", "#555555", "#888888", "#bbbbbb"}
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_layout_graph_plain_notation_resets_scaled_tick_formatters() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        first_tab = window.tab_widget.currentWidget()
        first_axes = window._current_axes()  # noqa: SLF001
        assert first_axes is not None
        first_axes.set_title("First")
        if first_tab is not None:
            descriptor = window._tab_descriptors.get(first_tab)  # noqa: SLF001
            if descriptor is not None:
                descriptor.title = "First"
            index = window.tab_widget.indexOf(first_tab)
            if index >= 0:
                window.tab_widget.setTabText(index, "First")
        first_axes.plot([0, 1, 2], [0.2, 0.4, 0.6], label="A")
        first_axes.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda value, _pos: f"{value * 100:.0f}")
        )

        window._create_blank_graph()  # noqa: SLF001
        second_tab = window.tab_widget.currentWidget()
        second_axes = window._current_axes()  # noqa: SLF001
        assert second_axes is not None
        second_axes.set_title("Second")
        if second_tab is not None:
            descriptor = window._tab_descriptors.get(second_tab)  # noqa: SLF001
            if descriptor is not None:
                descriptor.title = "Second"
            index = window.tab_widget.indexOf(second_tab)
            if index >= 0:
                window.tab_widget.setTabText(index, "Second")
        second_axes.plot([0, 1, 2], [0.3, 0.5, 0.7], label="B")
        second_axes.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda value, _pos: f"{value * 100:.0f}")
        )

        payload = {
            "title": "Layout",
            "source_titles": ["First", "Second"],
            "rows": 1,
            "cols": 2,
            "share_x": True,
            "share_y": True,
            "panel_labels": "lower",
            "figure_units": "mm",
            "figure_width": 120,
            "figure_height": 70,
            "notation": "plain",
        }
        window._automation_create_figure(payload)  # noqa: SLF001
        figure = window._current_axes().figure  # noqa: SLF001
        visible_axes = [axes for axes in figure.axes if axes.get_visible()]
        assert visible_axes
        assert float(visible_axes[0].get_ylim()[1]) < 2.0
        formatter = visible_axes[0].yaxis.get_major_formatter()
        assert isinstance(formatter, mticker.ScalarFormatter)
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_automation_house_style_ignores_manual_tick_lists() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    settings = QtCore.QSettings("microwire", "plotting")
    original = settings.value("figure_layout_house_style", "")
    try:
        settings.setValue(
            "figure_layout_house_style",
            json.dumps(
                {
                    "external_legend": True,
                    "legend_placement": "bottom",
                    "x_ticks": "0, 1, 2",
                    "y_ticks": "10, 20",
                    "style_preset": "mono",
                }
            ),
        )
        settings.sync()

        window._create_blank_graph()  # noqa: SLF001
        first_tab = window.tab_widget.currentWidget()
        first_axes = window._current_axes()  # noqa: SLF001
        assert first_axes is not None
        first_axes.set_title("First")
        first_axes.plot([0, 1, 2], [0.2, 0.4, 0.6], label="A")
        if first_tab is not None:
            descriptor = window._tab_descriptors.get(first_tab)  # noqa: SLF001
            if descriptor is not None:
                descriptor.title = "First"
            index = window.tab_widget.indexOf(first_tab)
            if index >= 0:
                window.tab_widget.setTabText(index, "First")

        window._create_blank_graph()  # noqa: SLF001
        second_tab = window.tab_widget.currentWidget()
        second_axes = window._current_axes()  # noqa: SLF001
        assert second_axes is not None
        second_axes.set_title("Second")
        second_axes.plot([0, 1, 2], [0.3, 0.5, 0.7], label="B")
        if second_tab is not None:
            descriptor = window._tab_descriptors.get(second_tab)  # noqa: SLF001
            if descriptor is not None:
                descriptor.title = "Second"
            index = window.tab_widget.indexOf(second_tab)
            if index >= 0:
                window.tab_widget.setTabText(index, "Second")

        window._automation_create_figure(  # noqa: SLF001
            {
                "title": "Layout",
                "source_titles": ["First", "Second"],
                "rows": 1,
                "cols": 2,
                "share_x": True,
                "share_y": True,
                "panel_labels": "lower",
            }
        )
        figure = window._current_axes().figure  # noqa: SLF001
        visible_axes = [axes for axes in figure.axes if axes.get_visible()]
        assert visible_axes
        assert float(visible_axes[0].get_ylim()[1]) < 2.0
        assert list(float(tick) for tick in visible_axes[0].get_yticks()) != [10.0, 20.0]
    finally:
        settings.setValue("figure_layout_house_style", original)
        settings.sync()
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_layout_export_uses_paper_figure_size(tmp_path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        for title, y_values in (
            ("First", [0.2, 0.4, 0.6]),
            ("Second", [0.3, 0.5, 0.7]),
            ("Third", [0.4, 0.6, 0.8]),
            ("Fourth", [0.5, 0.7, 0.9]),
        ):
            window._create_blank_graph()  # noqa: SLF001
            tab = window.tab_widget.currentWidget()
            axes = window._current_axes()  # noqa: SLF001
            assert axes is not None
            axes.set_title(title)
            axes.plot([0, 1, 2], y_values, label=title)
            if tab is not None:
                descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001
                if descriptor is not None:
                    descriptor.title = title
                index = window.tab_widget.indexOf(tab)
                if index >= 0:
                    window.tab_widget.setTabText(index, title)

        window._automation_create_figure(  # noqa: SLF001
            {
                "title": "Layout",
                "source_titles": ["First", "Second", "Third", "Fourth"],
                "rows": 2,
                "cols": 2,
                "share_x": True,
                "share_y": True,
                "panel_labels": "lower",
                "figure_units": "mm",
                "figure_width": 180,
                "figure_height": 120,
                "use_house_style": False,
            }
        )
        export_dir = tmp_path / "exports"
        export_paths = window._automation_export_all_figures(  # noqa: SLF001
            output_dir=export_dir,
            fmt="png",
            dpi=300,
        )
        layout_exports = [path for path in export_paths if "Layout" in path.name]
        assert layout_exports
        image = Image.open(layout_exports[0])
        assert image.size[0] >= 2000
        assert image.size[1] >= 1300
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_refresh_layout_figure_reuses_updated_source_data(monkeypatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        first_tab = window.tab_widget.currentWidget()
        first_axes = window._current_axes()  # noqa: SLF001
        assert first_axes is not None
        first_axes.set_title("First")
        first_axes.plot([0, 1, 2], [1, 2, 3], label="A")

        window._create_blank_graph()  # noqa: SLF001
        second_tab = window.tab_widget.currentWidget()
        second_axes = window._current_axes()  # noqa: SLF001
        assert second_axes is not None
        second_axes.set_title("Second")
        second_axes.plot([0, 1, 2], [3, 2, 1], label="B")

        monkeypatch.setattr(
            FigureLayoutDialog,
            "exec",
            lambda self: int(QtWidgets.QDialog.DialogCode.Accepted),
        )
        monkeypatch.setattr(
            FigureLayoutDialog,
            "payload",
            lambda self: {
                "title": "Refreshable Figure",
                "rows": 2,
                "cols": 1,
                "share_x": True,
                "share_y": True,
                "panel_labels": "lower",
                "external_legend": False,
                "legend_placement": "right",
                "figure_units": "mm",
                "figure_width": 180 / 25.4,
                "figure_height": 120 / 25.4,
                "wspace": 0.12,
                "hspace": 0.18,
                "left_margin": 0.08,
                "right_margin": 0.90,
                "top_margin": 0.90,
                "bottom_margin": 0.12,
                "panels": [
                    {"tab": first_tab, "panel_title": "First"},
                    {"tab": second_tab, "panel_title": "Second"},
                ],
            },
        )
        window._open_create_figure_dialog()  # noqa: SLF001

        # update source graph after layout creation
        first_axes.lines[0].set_ydata([10, 11, 12])
        window._refresh_current_layout_figure()  # noqa: SLF001

        descriptor = window._tab_descriptors.get(window.tab_widget.currentWidget())  # noqa: SLF001
        assert descriptor is not None
        figure = descriptor.axes.figure
        visible_axes = [axes for axes in figure.axes if axes.get_visible()]
        refreshed_y = list(visible_axes[0].lines[0].get_ydata())
        assert refreshed_y == [10, 11, 12]
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_clone_layout_figure_creates_copy(monkeypatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()  # noqa: SLF001
        first_tab = window.tab_widget.currentWidget()
        first_axes = window._current_axes()  # noqa: SLF001
        assert first_axes is not None
        first_axes.set_title("First")
        first_axes.plot([0, 1, 2], [1, 2, 3], label="A")

        monkeypatch.setattr(
            FigureLayoutDialog,
            "exec",
            lambda self: int(QtWidgets.QDialog.DialogCode.Accepted),
        )
        monkeypatch.setattr(
            FigureLayoutDialog,
            "payload",
            lambda self: {
                "title": "Cloneable Figure",
                "rows": 1,
                "cols": 1,
                "share_x": False,
                "share_y": False,
                "panel_labels": "none",
                "external_legend": False,
                "legend_placement": "right",
                "figure_units": "mm",
                "figure_width": 120.0,
                "figure_height": 80.0,
                "wspace": 0.10,
                "hspace": 0.10,
                "left_margin": 0.10,
                "right_margin": 0.95,
                "top_margin": 0.90,
                "bottom_margin": 0.12,
                "panels": [
                    {"tab": first_tab, "panel_title": "Panel A"},
                ],
            },
        )
        window._open_create_figure_dialog()  # noqa: SLF001
        before = len(window._tab_descriptors)  # noqa: SLF001
        window._clone_current_layout_figure()  # noqa: SLF001
        after = len(window._tab_descriptors)  # noqa: SLF001
        assert after == before + 1
        titles = [descriptor.title for descriptor in window._tab_descriptors.values()]  # noqa: SLF001
        assert "Cloneable Figure (copy)" in titles
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_figure_layout_dialog_can_save_and_load_house_style() -> None:
    app = _ensure_app()
    dialog = FigureLayoutDialog(None, entries=[])
    try:
        dialog.external_legend_cb.setChecked(True)
        dialog.legend_placement_combo.setCurrentIndex(dialog.legend_placement_combo.findData("bottom"))
        dialog.style_preset_combo.setCurrentIndex(dialog.style_preset_combo.findData("mono"))
        dialog.minor_ticks_cb.setChecked(True)
        dialog.tick_direction_combo.setCurrentIndex(dialog.tick_direction_combo.findData("in"))
        dialog.notation_combo.setCurrentIndex(dialog.notation_combo.findData("scientific"))
        dialog.x_ticks_edit.setText("0, 1, 2")
        dialog.y_ticks_edit.setText("10, 20")
        dialog._save_house_style()  # noqa: SLF001

        restored = FigureLayoutDialog(None, entries=[])
        try:
            restored._load_house_style()  # noqa: SLF001
            assert restored.external_legend_cb.isChecked() is True
            assert restored.legend_placement_combo.currentData() == "bottom"
            assert restored.style_preset_combo.currentData() == "mono"
            assert restored.minor_ticks_cb.isChecked() is True
            assert restored.tick_direction_combo.currentData() == "in"
            assert restored.notation_combo.currentData() == "scientific"
            assert restored.x_ticks_edit.text() == "0, 1, 2"
            assert restored.y_ticks_edit.text() == "10, 20"
        finally:
            restored.close()
    finally:
        dialog.close()
        app.processEvents()


def test_figure_layout_dialog_can_save_and_load_template(monkeypatch) -> None:
    app = _ensure_app()
    source_tab = QtWidgets.QWidget()
    dialog = FigureLayoutDialog(None, entries=[("Graph 1", "Graph 1", source_tab)])
    try:
        dialog.title_edit.setText("Template Figure")
        title_item = dialog.panels_table.item(0, 2)
        assert title_item is not None
        title_item.setText("Panel A")
        dialog.rows_spin.setValue(2)
        dialog.cols_spin.setValue(1)
        monkeypatch.setattr(
            QtWidgets.QInputDialog,
            "getText",
            lambda *args, **kwargs: ("My Template", True),
        )
        dialog._save_figure_template()  # noqa: SLF001

        restored = FigureLayoutDialog(None, entries=[("Graph X", "Graph X", QtWidgets.QWidget())])
        try:
            monkeypatch.setattr(
                QtWidgets.QInputDialog,
                "getItem",
                lambda *args, **kwargs: ("My Template", True),
            )
            restored._load_figure_template()  # noqa: SLF001
            assert restored.title_edit.text() == "Template Figure"
            assert restored.rows_spin.value() == 2
            assert restored.cols_spin.value() == 1
            restored_title = restored.panels_table.item(0, 2)
            assert restored_title is not None
            assert restored_title.text() == "Panel A"
        finally:
            restored.close()
    finally:
        dialog.close()
        app.processEvents()


def test_export_stem_for_descriptor_is_deterministic() -> None:
    descriptor = TabDescriptor(
        kind="layout_graph",
        title="Figure Alpha",
        root_label="Figure Layout",
        x_label="X",
        y_label="Y",
        canvas=None,  # type: ignore[arg-type]
        axes=None,
        lines={},
        metadata={},
    )
    stem = PyPlotWorkbench._export_stem_for_descriptor(3, descriptor)  # noqa: SLF001
    assert stem == "03-Figure_Alpha"
