from __future__ import annotations

import contextlib
from pathlib import Path

import pandas as pd
from plotting.plugins.vsm_temperature_scan import core as module


def test_build_series_orders_sections_and_fields() -> None:
    processor = module.VSMTemperatureScanProcessor()
    processor.set_split_directions(True)
    frame = pd.DataFrame(
        {
            "temperature": [10, 20, 30, 40, 10, 20, 30, 40],
            "field": [5, 5, 5, 5, 10000, 10000, 10000, 10000],
            "signal": [1, 2, 3, 4, 5, 6, 7, 8],
            "section_index": [0, 0, 1, 1, 0, 0, 1, 1],
        }
    )

    series = processor._build_series(frame)

    order = [(int(entry.field), entry.segment_index) for entry in series]
    assert order == [(10000, 0), (10000, 1), (5, 0), (5, 1)]


def test_plot_title_includes_field_labels() -> None:
    processor = module.VSMTemperatureScanProcessor()
    title = processor._plot_title("Sample", "VSM Temperature Scan", [5, 10000])
    assert "Sample - VSM Temperature Scan" in title
    assert "5 Oe" in title
    assert "10000 Oe" in title


def test_combine_dual_field_entries_merges_high_low() -> None:
    processor = module.VSMTemperatureScanProcessor()
    low_frame = pd.DataFrame(
        {"temperature": [10, 20], "field": [5, 5], "signal": [1.0, 1.2]}
    )
    high_frame = pd.DataFrame(
        {"temperature": [10, 20], "field": [10000, 10000], "signal": [2.0, 2.2]}
    )
    other_frame = pd.DataFrame(
        {"temperature": [10, 20], "field": [8000, 8000], "signal": [3.0, 3.2]}
    )
    entries = [
        module.VSMEntry(path=Path("low.txt"), sample="Sample", dataframe=low_frame),
        module.VSMEntry(path=Path("high.txt"), sample="Sample", dataframe=high_frame),
        module.VSMEntry(path=Path("other.txt"), sample="Other", dataframe=other_frame),
    ]

    combined = processor._combine_dual_field_entries(entries)

    assert len(combined) == 2
    combined_sample = next(entry for entry in combined if entry.sample == "Sample")
    fields = set(combined_sample.dataframe["field"].tolist())
    assert 5 in fields
    assert 10000 in fields


def test_parse_file_appends_orientation_from_filename(tmp_path: Path) -> None:
    processor = module.VSMTemperatureScanProcessor()
    path = tmp_path / "202602010101-TSCN-a090-example.txt"
    path.write_text(
        "\n".join(
            [
                "@Samplename: Ni50Fe27Ga23 5-4 no glass 2",
                "@@End of Header.",
                "Time_since_start Applied_Field Signal_X_direction Sample_Temperature_For_Plot_",
                "New Section: Section 0:",
                "0 10000 0.00051 25.0",
                "1 10000 0.00050 26.0",
            ]
        ),
        encoding="utf-8",
    )

    _frame, sample = processor._parse_file(path)
    assert sample == "Ni50Fe27Ga23 5-4 no glass 2 (90°)"


def test_axis_label_uses_kilooe_for_high_fields() -> None:
    processor = module.VSMTemperatureScanProcessor()
    label = processor._axis_label_for_fields([10000], base="Magnetization")
    assert "10kOe" in label


def test_plot_origin_uses_named_axes_and_sets_titles(monkeypatch) -> None:
    class _FakeLabel:
        def __init__(self) -> None:
            self.text = ""
            self._ints: dict[str, int] = {}

        def set_int(self, key: str, value: int) -> None:
            self._ints[key] = int(value)

    class _FakeAxis:
        def __init__(self) -> None:
            self.label = _FakeLabel()
            self.title = ""
            self.show_labels = True
            self.showLabels = True
            self.showlabels = True

    class _FakePlot:
        def __init__(self, index: int) -> None:
            self.index = index
            self.legend = ""
            self.color = ""

            class _Symbol:
                def __init__(self) -> None:
                    self.color = ""

            self.symbol = _Symbol()

    class _FakeLayer:
        def __init__(self) -> None:
            self._axes: dict[str, _FakeAxis] = {}
            self.axis_calls: list[str] = []
            self._plot_count = 0
            self._legend = _FakeLabel()

        def axis(self, axis_name: str) -> _FakeAxis:
            assert isinstance(axis_name, str), "plot_origin must use named axes (x/y/x2/y2)"
            self.axis_calls.append(axis_name)
            return self._axes.setdefault(axis_name, _FakeAxis())

        def label(self, label_name: str) -> _FakeLabel | None:
            if label_name == "Legend":
                return self._legend
            return None

        def add_plot(self, _sheet: object, *, coly: int, colx: int) -> _FakePlot:
            _ = coly, colx
            plot = _FakePlot(self._plot_count)
            self._plot_count += 1
            return plot

        def lt_exec(self, _cmd: str) -> None:
            return

        def rescale(self) -> None:
            return

        def set_int(self, _key: str, _value: int) -> None:
            return

    class _FakeGraph(list):
        def __init__(self) -> None:
            super().__init__([_FakeLayer()])
            self.title_meta: str | None = None
            self.lname = ""
            self.name = ""
            self.lt_commands: list[str] = []

        def set_str(self, key: str, value: str) -> None:
            if key == "title":
                self.title_meta = value

        def add_layer(self) -> _FakeLayer:
            layer = _FakeLayer()
            self.append(layer)
            return layer

        def lt_exec(self, cmd: str) -> None:
            self.lt_commands.append(cmd)

        def activate(self) -> None:
            return

    class _FakeColumn:
        def __init__(self) -> None:
            self.LongName = ""
            self.Units = ""
            self.Comment = ""
            self.Type = 0

    class _FakeColumns:
        def __init__(self) -> None:
            self._columns: dict[int, _FakeColumn] = {}

        def __call__(self, index: int) -> _FakeColumn:
            return self._columns.setdefault(index, _FakeColumn())

    class _FakeSheetObj:
        def __init__(self) -> None:
            self.Columns = _FakeColumns()

    class _FakeSheet:
        def __init__(self) -> None:
            self.name = ""
            self.obj = _FakeSheetObj()

        def from_list(self, *_args, **_kwargs) -> None:
            return

        def cols_axis(self, _roles: str) -> None:
            return

        def header_rows(self, _rows: str) -> None:
            return

    class _FakeBook(list):
        def __init__(self) -> None:
            super().__init__([_FakeSheet()])
            self.lname = ""

        def add_sheet(self) -> _FakeSheet:
            sheet = _FakeSheet()
            self.append(sheet)
            return sheet

        def activate(self) -> None:
            return

    class _FakeOrigin:
        def __init__(self) -> None:
            self.graphs: list[_FakeGraph] = []
            self.lt_commands: list[str] = []

        def new_book(self, _kind: str) -> _FakeBook:
            return _FakeBook()

        def new_graph(self, template: str | None = None) -> _FakeGraph:
            _ = template
            graph = _FakeGraph()
            self.graphs.append(graph)
            return graph

        def lt_exec(self, command: str) -> None:
            self.lt_commands.append(command)

    fake_origin = _FakeOrigin()

    @contextlib.contextmanager
    def _fake_origin_session(*, keep_open: bool = False):
        _ = keep_open
        yield fake_origin

    monkeypatch.setattr(module, "op", object())
    monkeypatch.setattr(module, "origin_session", _fake_origin_session)

    processor = module.VSMTemperatureScanProcessor()
    frame = pd.DataFrame(
        {
            "temperature": [30.0, 40.0, 50.0],
            "field": [10000.0, 10000.0, 10000.0],
            "signal": [2.0e-4, 1.9e-4, 1.8e-4],
            "section_index": [0, 0, 0],
        }
    )
    entry = module.VSMEntry(path=Path("scan.txt"), sample="Sample", dataframe=frame)
    processor.plot_origin([entry])

    assert fake_origin.graphs, "Expected at least one Origin graph to be created"
    graph = fake_origin.graphs[0]
    layer = graph[0]
    assert graph.title_meta is not None and "VSM Temperature Scan" in graph.title_meta
    assert any(cmd.startswith('title -s "') for cmd in graph.lt_commands)
    assert {"x", "y", "x2"}.issubset(set(layer.axis_calls))
    assert layer._axes["x"].label.text == "Temperature [°C]"
    assert "[emu]" in layer._axes["y"].label.text
    assert "\\l(" in layer._legend.text


def test_plot_origin_combines_legend_entries_for_dual_axis(monkeypatch) -> None:
    class _FakeLabel:
        def __init__(self) -> None:
            self.text = ""
            self._ints: dict[str, int] = {}

        def set_int(self, key: str, value: int) -> None:
            self._ints[key] = int(value)

    class _FakeAxis:
        def __init__(self) -> None:
            self.label = _FakeLabel()
            self.title = ""

    class _FakePlot:
        def __init__(self, index: int) -> None:
            self.index = index + 1
            self.legend = ""
            self.color = ""

            class _Symbol:
                def __init__(self) -> None:
                    self.color = ""

            self.symbol = _Symbol()

    class _FakeLayer:
        def __init__(self) -> None:
            self._axes: dict[str, _FakeAxis] = {}
            self._plot_count = 0
            self._legend = _FakeLabel()

        def axis(self, axis_name: str) -> _FakeAxis:
            return self._axes.setdefault(axis_name, _FakeAxis())

        def label(self, label_name: str) -> _FakeLabel | None:
            if label_name == "Legend":
                return self._legend
            return None

        def add_plot(self, _sheet: object, *, coly: int, colx: int) -> _FakePlot:
            _ = coly, colx
            plot = _FakePlot(self._plot_count)
            self._plot_count += 1
            return plot

        def lt_exec(self, _cmd: str) -> None:
            return

        def rescale(self) -> None:
            return

        def set_int(self, _key: str, _value: int) -> None:
            return

    class _FakeColumn:
        def __init__(self) -> None:
            self.LongName = ""
            self.Units = ""
            self.Comment = ""
            self.Type = 0

    class _FakeColumns:
        def __init__(self) -> None:
            self._columns: dict[int, _FakeColumn] = {}

        def __call__(self, index: int) -> _FakeColumn:
            return self._columns.setdefault(index, _FakeColumn())

    class _FakeSheetObj:
        def __init__(self) -> None:
            self.Columns = _FakeColumns()

    class _FakeSheet:
        def __init__(self) -> None:
            self.name = ""
            self.obj = _FakeSheetObj()

        def from_list(self, *_args, **_kwargs) -> None:
            return

        def cols_axis(self, _roles: str) -> None:
            return

        def header_rows(self, _rows: str) -> None:
            return

    class _FakeBook(list):
        def __init__(self) -> None:
            super().__init__([_FakeSheet()])
            self.lname = ""

        def add_sheet(self) -> _FakeSheet:
            sheet = _FakeSheet()
            self.append(sheet)
            return sheet

        def activate(self) -> None:
            return

    class _FakeGraph(list):
        def __init__(self) -> None:
            super().__init__([_FakeLayer()])
            self.title_meta: str | None = None
            self.lname = ""
            self.name = ""
            self.lt_commands: list[str] = []

        def set_str(self, key: str, value: str) -> None:
            if key == "title":
                self.title_meta = value

        def add_layer(self) -> _FakeLayer:
            layer = _FakeLayer()
            self.append(layer)
            return layer

        def lt_exec(self, cmd: str) -> None:
            self.lt_commands.append(cmd)

        def activate(self) -> None:
            return

    class _FakeOrigin:
        def __init__(self) -> None:
            self.graphs: list[_FakeGraph] = []

        def new_book(self, _kind: str) -> _FakeBook:
            return _FakeBook()

        def new_graph(self, template: str | None = None) -> _FakeGraph:
            _ = template
            graph = _FakeGraph()
            self.graphs.append(graph)
            return graph

        def lt_exec(self, _command: str) -> None:
            return

    fake_origin = _FakeOrigin()

    @contextlib.contextmanager
    def _fake_origin_session(*, keep_open: bool = False):
        _ = keep_open
        yield fake_origin

    monkeypatch.setattr(module, "op", object())
    monkeypatch.setattr(module, "origin_session", _fake_origin_session)

    processor = module.VSMTemperatureScanProcessor()
    frame = pd.DataFrame(
        {
            "temperature": [30.0, 40.0, 50.0, 30.0, 40.0, 50.0],
            "field": [10000.0, 10000.0, 10000.0, 50.0, 50.0, 50.0],
            "signal": [2.0e-4, 1.9e-4, 1.8e-4, 1.0e-4, 0.9e-4, 0.8e-4],
            "section_index": [0, 0, 0, 0, 0, 0],
        }
    )
    entry = module.VSMEntry(path=Path("scan_dual.txt"), sample="Sample", dataframe=frame)
    processor.plot_origin([entry])

    assert fake_origin.graphs
    graph = fake_origin.graphs[0]
    assert len(graph) >= 2
    primary = graph[0]
    secondary = graph[1]
    assert "\\L(1." in primary._legend.text
    assert "\\L(2." in primary._legend.text
    assert secondary._legend.text == ""


def test_plot_origin_keeps_primary_legend_when_layer_wrappers_change(monkeypatch) -> None:
    class _FakeLabel:
        def __init__(self) -> None:
            self.text = ""
            self._ints: dict[str, int] = {}

        def set_int(self, key: str, value: int) -> None:
            self._ints[key] = int(value)

    class _FakeAxis:
        def __init__(self) -> None:
            self.label = _FakeLabel()
            self.title = ""

    class _FakePlot:
        def __init__(self, index: int) -> None:
            self.index = index + 1
            self.legend = ""
            self.color = ""

            class _Symbol:
                def __init__(self) -> None:
                    self.color = ""

            self.symbol = _Symbol()

    class _LayerState:
        def __init__(self) -> None:
            self.axes: dict[str, _FakeAxis] = {}
            self.plot_count = 0
            self.legend = _FakeLabel()

    class _LayerView:
        def __init__(self, state: _LayerState) -> None:
            self._state = state

        @property
        def _legend(self) -> _FakeLabel:
            return self._state.legend

        def axis(self, axis_name: str) -> _FakeAxis:
            return self._state.axes.setdefault(axis_name, _FakeAxis())

        def label(self, label_name: str) -> _FakeLabel | None:
            if label_name in {"Legend", "legend"}:
                return self._state.legend
            return None

        def add_plot(self, _sheet: object, *, coly: int, colx: int) -> _FakePlot:
            _ = coly, colx
            plot = _FakePlot(self._state.plot_count)
            self._state.plot_count += 1
            return plot

        def lt_exec(self, _cmd: str) -> None:
            return

        def rescale(self) -> None:
            return

        def set_int(self, _key: str, _value: int) -> None:
            return

    class _FakeColumn:
        def __init__(self) -> None:
            self.LongName = ""
            self.Units = ""
            self.Comment = ""
            self.Type = 0

    class _FakeColumns:
        def __init__(self) -> None:
            self._columns: dict[int, _FakeColumn] = {}

        def __call__(self, index: int) -> _FakeColumn:
            return self._columns.setdefault(index, _FakeColumn())

    class _FakeSheetObj:
        def __init__(self) -> None:
            self.Columns = _FakeColumns()

    class _FakeSheet:
        def __init__(self) -> None:
            self.name = ""
            self.obj = _FakeSheetObj()

        def from_list(self, *_args, **_kwargs) -> None:
            return

        def cols_axis(self, _roles: str) -> None:
            return

        def header_rows(self, _rows: str) -> None:
            return

    class _FakeBook(list):
        def __init__(self) -> None:
            super().__init__([_FakeSheet()])
            self.lname = ""

        def add_sheet(self) -> _FakeSheet:
            sheet = _FakeSheet()
            self.append(sheet)
            return sheet

        def activate(self) -> None:
            return

    class _FakeGraph:
        def __init__(self) -> None:
            self._layers = [_LayerState()]
            self.title_meta: str | None = None
            self.lname = ""
            self.name = ""
            self.lt_commands: list[str] = []

        def __len__(self) -> int:
            return len(self._layers)

        def __getitem__(self, index: int) -> _LayerView:
            # Origin can return a new wrapper on each lookup; identity is not stable.
            return _LayerView(self._layers[index])

        def set_str(self, key: str, value: str) -> None:
            if key == "title":
                self.title_meta = value

        def add_layer(self) -> _LayerView:
            self._layers.append(_LayerState())
            return _LayerView(self._layers[-1])

        def lt_exec(self, cmd: str) -> None:
            self.lt_commands.append(cmd)

        def activate(self) -> None:
            return

    class _FakeOrigin:
        def __init__(self) -> None:
            self.graphs: list[_FakeGraph] = []

        def new_book(self, _kind: str) -> _FakeBook:
            return _FakeBook()

        def new_graph(self, template: str | None = None) -> _FakeGraph:
            _ = template
            graph = _FakeGraph()
            self.graphs.append(graph)
            return graph

        def lt_exec(self, _command: str) -> None:
            return

    fake_origin = _FakeOrigin()

    @contextlib.contextmanager
    def _fake_origin_session(*, keep_open: bool = False):
        _ = keep_open
        yield fake_origin

    monkeypatch.setattr(module, "op", object())
    monkeypatch.setattr(module, "origin_session", _fake_origin_session)

    processor = module.VSMTemperatureScanProcessor()
    frame = pd.DataFrame(
        {
            "temperature": [30.0, 40.0, 50.0, 30.0, 40.0, 50.0],
            "field": [10000.0, 10000.0, 10000.0, 50.0, 50.0, 50.0],
            "signal": [2.0e-4, 1.9e-4, 1.8e-4, 1.0e-4, 0.9e-4, 0.8e-4],
            "section_index": [0, 0, 0, 0, 0, 0],
        }
    )
    entry = module.VSMEntry(path=Path("scan_dual_wrapped.txt"), sample="Sample", dataframe=frame)
    processor.plot_origin([entry])

    assert fake_origin.graphs
    graph = fake_origin.graphs[0]
    primary = graph[0]
    secondary = graph[1]
    assert "\\L(1." in primary._legend.text
    assert "\\L(2." in primary._legend.text
    assert secondary._legend.text == ""
