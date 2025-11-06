from __future__ import annotations

import contextlib
import sys
from typing import Iterator

import pandas as pd
import pytest

from PyQt6 import QtWidgets

from plotting.pyplot.window import (
    TOOLBAR_SECTION_PROPERTY,
    WorksheetColumnMeta,
    WorksheetData,
    WorkbookData,
)
from plotting.pyplot.app import PyPlotWorkbench, main as pyplot_main
from plotting.plugins import ExternalPlotterPlugin, PyPlotPlugin, builtin_plugin_registry


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
    def fake_session() -> Iterator[_FakeOrigin]:
        yield fake_origin

    monkeypatch.setattr("plotting.pyplot.window.origin_session", fake_session)
    monkeypatch.setattr("plotting.pyplot.window.schedule_origin_release", lambda: None)

    exported, errors = window._push_workbooks_to_origin([workbook])

    assert exported == 1
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
