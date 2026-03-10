from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Iterator

import pandas as pd
from PyQt6 import QtWidgets

from plotting.plugins.fmr.fmr_plugin import FmrEntry, FmrPlugin
from plotting.pyplot.app import PyPlotWorkbench


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


class _FakePlot:
    def __init__(self) -> None:
        self.color: str | None = None
        self.line_width: float | None = None
        self.symbol_shape: int | None = None
        self.symbol_size: float | None = None
        self.symbol_edge_color: str | None = None
        self.symbol_fill_color: str | None = None
        self.legend: str | None = None


class _FakeLayer:
    def __init__(self, commands: list[str]) -> None:
        self.plots: list[_FakePlot] = []
        self.flags: dict[str, int] = {}
        self.legend_added = False
        self._commands = commands
        self.graph: _FakeGraph | None = None

    def add_plot(self, *_: object, **__: object) -> _FakePlot:
        plot = _FakePlot()
        self.plots.append(plot)
        return plot

    def rescale(self) -> None:
        return

    def set_int(self, key: str, value: int) -> None:
        self.flags[key] = value

    def add_legend(self) -> None:
        self.legend_added = True

    def lt_exec(self, command: str) -> None:
        self._commands.append(command)


class _FakeGraph:
    def __init__(self, commands: list[str]) -> None:
        self.layer = _FakeLayer(commands)
        self.values: dict[str, str] = {}
        self.name = ""
        self.lname = ""
        self.layer.graph = self

    def __getitem__(self, index: int) -> _FakeLayer:
        assert index == 0
        return self.layer

    def activate(self) -> None:
        return

    def set_str(self, key: str, value: str) -> None:
        self.values[key] = value


class _FakeSheet:
    def __init__(self) -> None:
        self.name = "Data"
        self.labels: dict[tuple[int, str], str] = {}
        self.frame: pd.DataFrame | None = None
        self.header: str | None = None
        self.roles: str | None = None

    def from_df(self, frame: pd.DataFrame) -> None:
        self.frame = frame.copy()

    def header_rows(self, rows: str) -> None:
        self.header = rows

    def cols_axis(self, roles: str) -> None:
        self.roles = roles

    def set_label(self, index: int, label: str, kind: str) -> None:
        self.labels[(index, kind)] = label


class _FakeBook:
    def __init__(self) -> None:
        self.lname = ""
        self._sheets = [_FakeSheet()]

    def __len__(self) -> int:
        return len(self._sheets)

    def __getitem__(self, index: int) -> _FakeSheet:
        return self._sheets[index]

    def add_sheet(self) -> _FakeSheet:
        sheet = _FakeSheet()
        self._sheets.append(sheet)
        return sheet


class _FakeOrigin:
    def __init__(self) -> None:
        self.books: list[_FakeBook] = []
        self.graphs: list[_FakeGraph] = []
        self.lt_commands: list[str] = []

    def new_book(self, *_: object, **__: object) -> _FakeBook:
        book = _FakeBook()
        self.books.append(book)
        return book

    def new_graph(self, *_: object, **__: object) -> _FakeGraph:
        graph = _FakeGraph(self.lt_commands)
        self.graphs.append(graph)
        return graph

    def lt_exec(self, command: str) -> None:
        self.lt_commands.append(command)


def test_fmr_origin_export_sets_axis_labels_and_origin_render_flags(
    monkeypatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="FMR")
    try:
        plugin = window._current_plugin
        assert isinstance(plugin, FmrPlugin)
        plugin.settings_widget()
        assert plugin._phase_rotate_checkbox is not None
        plugin._phase_rotate_checkbox.setChecked(False)
        plugin._dataset = [
            FmrEntry(
                path=Path("sample.csv"),
                sample="Sample A",
                frame=pd.DataFrame(
                    {
                        "Field [Oe]": [-100.0, 0.0, 100.0],
                        "X [V]": [1.0, 0.5, 0.2],
                        "Y [V]": [0.1, 0.2, 0.3],
                    }
                ),
                units={"Field [Oe]": "Oe", "X [V]": "V", "Y [V]": "V"},
            )
        ]

        fake_origin = _FakeOrigin()

        @contextlib.contextmanager
        def _fake_origin_session(*_: object, **__: object) -> Iterator[_FakeOrigin]:
            yield fake_origin

        def _run_origin_export(**kwargs: object) -> bool:
            task = kwargs.get("task")
            assert callable(task)
            task()
            return True

        monkeypatch.setattr("plotting.plugins.fmr.fmr_plugin.origin_session", _fake_origin_session)
        monkeypatch.setattr(plugin, "run_origin_export", _run_origin_export)

        plugin.open_origin()

        assert len(fake_origin.books) == 1
        assert len(fake_origin.graphs) == 1
        graph = fake_origin.graphs[0]
        layer = graph.layer
        assert layer.legend_added
        assert layer.flags == {
            "antialias": 1,
            "use_speed_mode": 0,
            "speedmode": 0,
        }
        assert [plot.legend for plot in layer.plots] == ["X", "Y"]
        assert any('label -s -n title "Sample A";' in cmd for cmd in fake_origin.lt_commands)
        assert any('label -s -xb "Field [Oe]";' in cmd for cmd in fake_origin.lt_commands)
        assert any('label -s -yl "X [V]";' in cmd for cmd in fake_origin.lt_commands)
    finally:
        window.close()
        app.processEvents()
