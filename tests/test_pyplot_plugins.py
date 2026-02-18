from __future__ import annotations

import contextlib
import sys
from typing import Iterator

import os
from pathlib import Path

import pandas as pd
import pytest

from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from plotting.pyplot.window import (
    TOOLBAR_SECTION_PROPERTY,
    TabDescriptor,
    WorksheetColumnMeta,
    WorksheetData,
    WorkbookData,
)
from plotting.pyplot.app import PyPlotWorkbench, main as pyplot_main
from plotting.plugins import ExternalPlotterPlugin, PyPlotPlugin, builtin_plugin_registry
from plotting.plugins.temperature_sensitivity import core as temp_sens_core
from plotting.plugins.temperature_sensitivity import temp_sens_plugin

_HEADLESS = not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
_OFFSCREEN = os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen"
pytestmark = pytest.mark.skipif(
    _HEADLESS or _OFFSCREEN,
    reason="PyQt plug-in tests require a display-capable Qt platform",
)


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


def _menu_by_object_name(
    menu_bar: QtWidgets.QMenuBar,
    object_name: str,
) -> QtWidgets.QMenu | None:
    for action in menu_bar.actions():
        menu = action.menu()
        if menu is not None and menu.objectName() == object_name:
            return menu
    return None


def test_plugin_settings_provide_toolbar_sections_and_mount() -> None:
    _ensure_app()
    registry = builtin_plugin_registry()
    plugin_factories = {
        name: (lambda host, cls=cls, n=name: cls(host, n)) for name, cls in registry.items()
    }
    window = PyPlotWorkbench(plotters=plugin_factories)
    try:
        combo = getattr(window, "_plotter_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)

        for name in sorted(registry):
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


def test_window_menu_exposes_arrangement_actions() -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window_menu = _menu_by_object_name(window.menuBar(), "mw_shared_window")
        assert window_menu is not None
        labels = {
            action.text().replace("&", "").strip().lower()
            for action in window_menu.actions()
            if not action.isSeparator()
        }
        assert "cascade" in labels
        assert "tile vertical" in labels
        assert "tile horizontal" in labels
    finally:
        window.close()


def test_window_arrangement_mode_defaults_to_cascade() -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        _make_simple_plot_tab(window, plugin_name="Shared Test Plugin")
        _make_simple_plot_tab(window, plugin_name="Shared Test Plugin")
        arrange_mode = getattr(window.tab_widget, "arrangement_mode", None)
        assert callable(arrange_mode)
        assert arrange_mode() == "cascade"
    finally:
        window.close()


def test_switching_subwindows_keeps_manual_geometry_in_cascade_mode() -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        first = _make_simple_plot_tab(window, plugin_name="Shared Test Plugin")
        second = _make_simple_plot_tab(window, plugin_name="Shared Test Plugin")
        set_mode = getattr(window.tab_widget, "set_arrangement_mode", None)
        assert callable(set_mode)
        set_mode("cascade")
        subwindow_for = getattr(window.tab_widget, "_subwindow_for", None)
        assert callable(subwindow_for)
        first_sub = subwindow_for(first)
        second_sub = subwindow_for(second)
        assert first_sub is not None
        assert second_sub is not None

        first_rect = QtCore.QRect(40, 60, 620, 420)
        second_rect = QtCore.QRect(120, 140, 600, 400)
        first_sub.setGeometry(first_rect)
        second_sub.setGeometry(second_rect)

        window.tab_widget.setCurrentWidget(first)
        window.tab_widget.setCurrentWidget(second)

        assert first_sub.geometry().width() == first_rect.width()
        assert first_sub.geometry().height() == first_rect.height()
        assert second_sub.geometry().width() == second_rect.width()
        assert second_sub.geometry().height() == second_rect.height()
    finally:
        window.close()


def test_double_click_legend_opens_shared_legend_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        _make_simple_plot_tab(window, plugin_name="Shared Test Plugin")
        axes = window._current_axes()  # noqa: SLF001
        assert axes is not None

        called: dict[str, object] = {}

        def _fake_open_graph_format_dialog(
            checked: bool = False,
            *,
            focus_key: str | None = None,
            select_all: bool = False,
        ) -> bool:
            called["checked"] = checked
            called["focus_key"] = focus_key
            called["select_all"] = select_all
            return True

        monkeypatch.setattr(window, "_open_graph_format_dialog", _fake_open_graph_format_dialog)
        opened = window._open_shared_graph_format_from_double_click(  # noqa: SLF001
            axes=axes,
            legend=True,
        )
        assert opened is True
        assert called.get("focus_key") == "show_legend_cb"
    finally:
        window.close()


def test_object_manager_legend_double_click_uses_shared_graph_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        tab = _make_simple_plot_tab(window, plugin_name="Shared Test Plugin")
        axes = window._current_axes()  # noqa: SLF001
        assert axes is not None
        legend = axes.legend(loc="best")
        assert legend is not None
        window._rebuild_object_manager_for_tab(tab)  # noqa: SLF001

        item = QtWidgets.QTreeWidgetItem(["Legend"])
        item.setData(
            0,
            QtCore.Qt.ItemDataRole.UserRole,
            {"kind": "legend", "object": legend},
        )

        called: dict[str, object] = {}

        def _fake_open(
            target_axes: object,
            *,
            text_field: str | None = None,
            axis: str | None = None,
            legend: bool = False,
        ) -> bool:
            called["axes"] = target_axes
            called["text_field"] = text_field
            called["axis"] = axis
            called["legend"] = legend
            return True

        monkeypatch.setattr(window, "_open_shared_graph_format_from_canvas_target", _fake_open)
        window._handle_object_item_double_click(item, 0)  # noqa: SLF001
        assert called.get("axes") is axes
        assert called.get("legend") is True
    finally:
        window.close()


def test_graph_option_apply_refreshes_open_graphs() -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        _make_simple_plot_tab(window, plugin_name="Shared Test Plugin")
        axes = window._current_axes()  # noqa: SLF001
        assert axes is not None

        updated = window._clean_graph_option_payload(  # noqa: SLF001
            {
                **window.GRAPH_OPTION_DEFAULTS,
                "figure_width": 9.0,
                "figure_height": 4.5,
                "line_width": 2.2,
                "legend_columns": 2,
            }
        )
        window._store_graph_option_defaults(  # noqa: SLF001
            global_payload=updated,
            plugin_key="",
            plugin_override_enabled=False,
            plugin_payload=None,
            refresh_open_graphs=True,
        )

        width, height = axes.figure.get_size_inches()
        assert float(width) == pytest.approx(9.0, rel=1e-2)
        assert float(height) == pytest.approx(4.5, rel=1e-2)
        line = axes.get_lines()[0]
        assert float(line.get_linewidth()) == pytest.approx(2.2, rel=1e-2)
    finally:
        window.close()


def test_push_workbooks_to_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_app()

    window = PyPlotWorkbench(plotters={})

    df = pd.DataFrame({"time": [0.0, 1.0], "value": [2.0, 4.0]})
    worksheet = WorksheetData(
        key="sheet::1",
        name="Sheet/1",
        dataframe=df,
        columns={
            "time": WorksheetColumnMeta(long_name="Time", units="s"),
            "value": WorksheetColumnMeta(long_name="Value", units="A", comments="Test column"),
        },
    )
    workbook = WorkbookData(key="workbook::1", name="Test Book!", worksheets=[worksheet.key])
    window._workbooks[workbook.key] = workbook
    window._worksheets[worksheet.key] = worksheet

    class _FakeSheet:
        def __init__(self) -> None:
            self.data: pd.DataFrame | None = None
            self.labels: dict[int, str] = {}
            self.comments: dict[int, str] = {}
            self.axis_roles: str | None = None
            self.name = ""

        def from_df(self, frame: pd.DataFrame) -> None:
            self.data = frame.copy()

        def cols_axis(self, roles: str) -> None:
            self.axis_roles = roles

        def set_label(self, index: int, label: str) -> None:
            self.labels[index] = label

        def set_comment(self, index: int, comment: str) -> None:
            self.comments[index] = comment

        def activate(self) -> None:  # pragma: no cover - no-op for fake
            return

    class _FakeWorkbook(list):
        def __init__(self) -> None:
            super().__init__()
            self.lname = ""
            self.name = ""

        def activate(self) -> None:  # pragma: no cover - no-op for fake
            return

        def add_sheet(self, *_: object, lname: str | None = None) -> _FakeSheet:
            sheet = _FakeSheet()
            sheet.name = lname or "Sheet"
            self.append(sheet)
            return sheet

    class _FakeOrigin:
        def __init__(self) -> None:
            self.books: list[_FakeWorkbook] = []
            self.lt_commands: list[str] = []

        def new_book(self, *_: object, lname: str | None = None) -> _FakeWorkbook:
            book = _FakeWorkbook()
            book.lname = lname or ""
            self.books.append(book)
            return book

        def new_sheet(self, *_: object, lname: str | None = None) -> _FakeSheet:
            sheet = _FakeSheet()
            sheet.name = lname or "Sheet"
            return sheet

        def lt_exec(self, command: str) -> None:
            self.lt_commands.append(command)

    fake_origin = _FakeOrigin()

    @contextlib.contextmanager
    def fake_session(*_: object, **__: object) -> Iterator[_FakeOrigin]:
        yield fake_origin

    monkeypatch.setattr("plotting.pyplot.window.origin_session", fake_session)
    monkeypatch.setattr("plotting.pyplot.window.schedule_origin_release", lambda: None)

    exported, plotted, errors = window._push_workbooks_to_origin([workbook])

    assert exported == 1
    assert plotted == 0
    assert errors == []
    assert fake_origin.books, "expected workbook to be created"
    book = fake_origin.books[0]
    assert book.lname and "!" not in book.lname
    assert book.name == book.lname[:13]
    assert len(book) == 1
    sheet = book[0]
    assert isinstance(sheet, _FakeSheet)
    assert sheet.axis_roles == "XY"
    assert sheet.labels[0] == "Time"
    assert sheet.labels[1] == "Value"
    assert sheet.comments[1] == "Test column"
    assert any("unit$=\"s\"" in cmd for cmd in fake_origin.lt_commands)
    assert any("unit$=\"A\"" in cmd for cmd in fake_origin.lt_commands)
    assert sheet.data is not None
    pd.testing.assert_frame_equal(sheet.data.reset_index(drop=True), df.reset_index(drop=True))

    window.close()


def test_push_workbooks_to_origin_creates_graphs(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_app()

    window = PyPlotWorkbench(plotters={})

    df = pd.DataFrame({"time": [0.0, 1.0], "value": [2.0, 4.0]})
    worksheet = WorksheetData(
        key="sheet::graph",
        name="Plot data",
        dataframe=df,
        columns={
            "time": WorksheetColumnMeta(long_name="Time", units="s", comments="Sample A"),
            "value": WorksheetColumnMeta(long_name="Value", units="A", comments="Sample A"),
        },
        axis_roles="XY",
    )
    workbook = WorkbookData(key="workbook::graph", name="Graph Book", worksheets=[worksheet.key])
    window._workbooks[workbook.key] = workbook
    window._worksheets[worksheet.key] = worksheet

    class _FakeSheet:
        def __init__(self) -> None:
            self.data: pd.DataFrame | None = None
            self.name = ""

        def from_df(self, frame: pd.DataFrame) -> None:
            self.data = frame.copy()

        def cols_axis(self, roles: str) -> None:
            _ = roles

        def set_label(self, index: int, label: str) -> None:
            _ = (index, label)

        def set_comment(self, index: int, comment: str) -> None:
            _ = (index, comment)

        def activate(self) -> None:  # pragma: no cover - no-op for fake
            return

    class _FakePlot:
        def __init__(self) -> None:
            self.lname = ""
            self.long_name = ""
            self.name = ""

    class _FakeLayer:
        def __init__(self, commands: list[str]) -> None:
            self.plots: list[_FakePlot] = []
            self._commands = commands

        def add_plot(self, *_: object, **__: object) -> _FakePlot:
            plot = _FakePlot()
            self.plots.append(plot)
            return plot

        def lt_exec(self, command: str) -> None:
            self._commands.append(command)

    class _FakeGraph:
        def __init__(self, commands: list[str]) -> None:
            self.layer = _FakeLayer(commands)
            self.lname = ""
            self.name = ""

        def activate(self) -> None:  # pragma: no cover - no-op for fake
            return

        def __getitem__(self, index: int) -> _FakeLayer:
            assert index == 0
            return self.layer

    class _FakeWorkbook(list):
        def __init__(self) -> None:
            super().__init__()
            self.lname = ""
            self.name = ""

        def activate(self) -> None:  # pragma: no cover - no-op for fake
            return

        def add_sheet(self, *_: object, lname: str | None = None) -> _FakeSheet:
            sheet = _FakeSheet()
            sheet.name = lname or "Sheet"
            self.append(sheet)
            return sheet

    class _FakeOrigin:
        def __init__(self) -> None:
            self.books: list[_FakeWorkbook] = []
            self.graphs: list[_FakeGraph] = []
            self.lt_commands: list[str] = []

        def new_book(self, *_: object, lname: str | None = None) -> _FakeWorkbook:
            book = _FakeWorkbook()
            book.lname = lname or ""
            self.books.append(book)
            return book

        def new_sheet(self, *_: object, lname: str | None = None) -> _FakeSheet:
            sheet = _FakeSheet()
            sheet.name = lname or "Sheet"
            return sheet

        def new_graph(self, *_: object, **__: object) -> _FakeGraph:
            graph = _FakeGraph(self.lt_commands)
            self.graphs.append(graph)
            return graph

        def lt_exec(self, command: str) -> None:
            self.lt_commands.append(command)

    fake_origin = _FakeOrigin()

    @contextlib.contextmanager
    def fake_session(*_: object, **__: object) -> Iterator[_FakeOrigin]:
        yield fake_origin

    monkeypatch.setattr("plotting.pyplot.window.origin_session", fake_session)
    monkeypatch.setattr("plotting.pyplot.window.schedule_origin_release", lambda: None)

    exported, plotted, errors = window._push_workbooks_to_origin(
        [workbook],
        create_graphs=True,
    )

    assert exported == 1
    assert plotted == 1
    assert errors == []
    assert len(fake_origin.graphs) == 1
    assert fake_origin.graphs[0].layer.plots
    assert fake_origin.graphs[0].layer.plots[0].lname == "Sample A"
    assert any('label -s -xb "Time [s]";' in cmd for cmd in fake_origin.lt_commands)
    assert any('label -s -yl "Value [A]";' in cmd for cmd in fake_origin.lt_commands)
    assert any(cmd == "legend -o;" for cmd in fake_origin.lt_commands)
    assert not any("page.antialias" in cmd.lower() for cmd in fake_origin.lt_commands)

    window.close()


def test_shared_plot_workbook_tracks_multi_axis_metadata() -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        fig = Figure(figsize=(6, 4))
        ax_load = fig.add_subplot(111)
        ax_stress_y = ax_load.twinx()
        ax_stress = ax_stress_y.twiny()
        ax_load.set_title("Dual-axis overlay")
        ax_load.set_xlabel("Displacement [mm]")
        ax_load.set_ylabel("Load [g]")
        ax_stress.set_xlabel("Strain [%]")
        ax_stress_y.set_ylabel("Stress [MPa]")
        ax_stress_y.xaxis.set_visible(False)
        ax_stress.yaxis.set_visible(False)
        ax_load.plot([0.0, 1.0, 2.0], [0.0, 5.0, 10.0], label="Loading 1")
        ax_stress.plot([0.0, 2.0, 4.0], [0.0, 100.0, 200.0], label="Loading 1")
        canvas = FigureCanvas(fig)
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)
        descriptor = TabDescriptor(
            kind="unit_test_dual_axis",
            title="Dual-axis overlay",
            root_label="Dual-axis overlay",
            x_label="Displacement (mm) / Strain (%)",
            y_label="Load (g) / Stress (MPa)",
            canvas=canvas,
            axes=ax_load,
            lines={},
            metadata={"plugin": "Shared Test Plugin", "plot_kind": "shape_memory_dual_axis_overlay"},
        )
        window.tab_widget.addTab(tab, "Dual-axis")
        window.tab_widget.setCurrentWidget(tab)
        window._register_plot_tab(tab, canvas, ax_load, descriptor)  # noqa: SLF001

        assert window._shared_plot_workbook_by_tab  # noqa: SLF001
        workbook_key = window._shared_plot_workbook_by_tab.get(tab)  # noqa: SLF001
        assert workbook_key is not None
        workbook = window._workbooks.get(workbook_key)  # noqa: SLF001
        assert workbook is not None and workbook.worksheets
        worksheet = window._worksheets.get(workbook.worksheets[0])  # noqa: SLF001
        assert worksheet is not None

        x_units: set[str] = set()
        y_units: set[str] = set()
        for index, column in enumerate(worksheet.dataframe.columns):
            role = worksheet.axis_roles[index] if index < len(worksheet.axis_roles) else ""
            meta = worksheet.columns.get(column)
            if meta is None:
                continue
            if role == "X":
                x_units.add(meta.units)
            elif role == "Y":
                y_units.add(meta.units)
        assert {"mm", "%"}.issubset(x_units)
        assert {"g", "MPa"}.issubset(y_units)
    finally:
        window.close()


def test_mdi_visibility_queue_drops_deleted_subwindows() -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        first = _make_simple_plot_tab(window, plugin_name="Shared Test Plugin")
        second = _make_simple_plot_tab(window, plugin_name="Shared Test Plugin")
        window.tab_widget.set_max_visible_windows(1)
        window._clear_tab_list([first])  # noqa: SLF001
        window.tab_widget.setCurrentWidget(second)
        enforce = getattr(window.tab_widget, "_enforce_max_visible", None)
        assert callable(enforce)
        enforce()
    finally:
        window.close()


def _make_simple_plot_tab(window: PyPlotWorkbench, *, plugin_name: str) -> QtWidgets.QWidget:
    fig = Figure(figsize=(4, 3))
    axes = fig.add_subplot(111)
    axes.set_title("Example")
    axes.set_xlabel("Strain [%]")
    axes.set_ylabel("Stress [MPa]")
    axes.plot([0.0, 1.0, 2.0], [0.0, 10.0, 20.0], label="Loading 1")
    canvas = FigureCanvas(fig)
    tab = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(tab)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(canvas)
    descriptor = TabDescriptor(
        kind="unit_test",
        title="Example",
        root_label="Example Plot",
        x_label=axes.get_xlabel(),
        y_label=axes.get_ylabel(),
        canvas=canvas,
        axes=axes,
        lines={},
        metadata={"plugin": plugin_name},
    )
    window.tab_widget.addTab(tab, "Example Plot")
    window.tab_widget.setCurrentWidget(tab)
    window._register_plot_tab(tab, canvas, axes, descriptor)  # noqa: SLF001
    return tab


def test_shared_plot_workbook_is_created_from_plot_tab() -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        plugin_name = "Shared Test Plugin"
        _make_simple_plot_tab(window, plugin_name=plugin_name)
        assert window._shared_plot_workbook_by_tab  # noqa: SLF001
        workbook_key = next(iter(window._shared_plot_workbook_by_tab.values()))  # noqa: SLF001
        workbook = window._workbooks.get(workbook_key)  # noqa: SLF001
        assert workbook is not None
        assert workbook.worksheets
        worksheet = window._worksheets.get(workbook.worksheets[0])  # noqa: SLF001
        assert worksheet is not None
        assert list(worksheet.dataframe.columns) == ["Loading_1_x", "Loading_1_y"]
        assert worksheet.axis_roles == "XY"
        x_meta = worksheet.columns["Loading_1_x"]
        y_meta = worksheet.columns["Loading_1_y"]
        assert x_meta.long_name == "Strain"
        assert x_meta.units == "%"
        assert x_meta.comments == "Loading 1"
        assert y_meta.long_name == "Stress"
        assert y_meta.units == "MPa"
        assert y_meta.comments == "Loading 1"
    finally:
        window.close()


def test_clear_tab_list_removes_shared_workbook() -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        tab = _make_simple_plot_tab(window, plugin_name="Shared Test Plugin")
        assert window._shared_plot_workbook_by_tab  # noqa: SLF001
        window._clear_tab_list([tab])  # noqa: SLF001
        assert window.tab_widget.indexOf(tab) < 0
        assert not window._shared_plot_workbook_by_tab  # noqa: SLF001
    finally:
        window.close()


def test_open_origin_shared_exports_plugin_workbooks(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        plugin_name = "Shared Test Plugin"
        _make_simple_plot_tab(window, plugin_name=plugin_name)
        window._current_plotter_name = plugin_name  # noqa: SLF001

        captured: dict[str, object] = {}

        def _fake_push(
            workbooks: list[WorkbookData],
            *,
            create_graphs: bool = False,
        ) -> tuple[int, int, list[str]]:
            captured["count"] = len(workbooks)
            captured["create_graphs"] = create_graphs
            return (1, 1, [])

        monkeypatch.setattr(window, "_push_workbooks_to_origin", _fake_push)
        monkeypatch.setattr("plotting.pyplot.window.schedule_origin_release", lambda: None)
        monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args, **kwargs: None)
        monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *args, **kwargs: None)
        monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *args, **kwargs: None)

        window._open_origin_shared()  # noqa: SLF001
        assert captured.get("count") == 1
        assert captured.get("create_graphs") is True
    finally:
        window.close()


def test_open_origin_button_enabled_for_shared_plugin() -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        plugin = PyPlotPlugin(window, "Shared Test Plugin")
        window._current_plugin = plugin  # noqa: SLF001
        window._current_plotter_name = plugin.name  # noqa: SLF001
        _make_simple_plot_tab(window, plugin_name=plugin.name)
        window._sync_shared_action_states()  # noqa: SLF001
        assert window.open_origin_button.isEnabled()
    finally:
        window.close()


def test_available_plotters_wrapped_as_external_plugins() -> None:
    _ensure_app()

    launched: list[QtWidgets.QWidget] = []

    def _legacy_plotter() -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        widget.setObjectName("legacy_plotter_window")
        launched.append(widget)
        return widget

    window = pyplot_main(
        available_plotters={"Legacy Plot": _legacy_plotter},
        initial_plotter="Legacy Plot",
    )
    assert isinstance(window, PyPlotWorkbench)

    try:
        combo = getattr(window, "_plotter_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)
        assert combo.findText("Legacy Plot") >= 0

        plugin = window._current_plugin  # noqa: SLF001 - test hook
        assert isinstance(plugin, ExternalPlotterPlugin)

        panel = plugin.panel_widget()
        assert isinstance(panel, QtWidgets.QWidget)

        plugin.open_matplotlib()
        assert launched, "expected legacy launcher to run"
    finally:
        for widget in launched:
            if isinstance(widget, QtWidgets.QWidget):
                widget.close()
        window.close()


def test_temperature_plugin_registers_group_workbooks() -> None:
    _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        plugin = temp_sens_plugin.TemperatureSensitivityPlugin(window, "Temperature Sensitivity")
        sample_dir = Path("sample_data/temperature_dependence")
        files = [
            sample_dir / "Fe77Mo4B18Cu1 2_1 77mA 25C.txt",
            sample_dir / "Fe77Mo4B18Cu1 2_1 77mA 100C.txt",
        ]
        plugin._data = temp_sens_core.load_data([str(path) for path in files])
        plugin._register_workbooks(files)
        managed = plugin._managed_workbooks
        assert managed
        assert len(managed) == len(temp_sens_core.TS_LABELS)
        for key in managed:
            workbook = window._workbooks.get(key)
            assert workbook is not None
            assert workbook.worksheets
            first_sheet_key = workbook.worksheets[0]
            worksheet = window._worksheets.get(first_sheet_key)
            assert worksheet is not None
            assert "plot_value" in worksheet.dataframe.columns
    finally:
        window.close()
