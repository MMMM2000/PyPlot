from __future__ import annotations

from typing import Any

from plotting.shared.origin import (
    escape_origin_text,
    set_origin_axis_title,
    set_origin_graph_title,
)


class _Label:
    def __init__(self) -> None:
        self.text = ""
        self.show = False
        self._ints: dict[str, int] = {}
        self._floats: dict[str, float] = {}

    def set_int(self, key: str, value: int) -> None:
        self._ints[key] = int(value)

    def set_float(self, key: str, value: float) -> None:
        self._floats[key] = float(value)


class _Axis:
    def __init__(self) -> None:
        self.label = _Label()


class _Layer:
    def __init__(self, *, with_title_label: bool = True) -> None:
        self._axis = {"x": _Axis(), "y": _Axis(), "x2": _Axis(), "y2": _Axis()}
        self._title = _Label() if with_title_label else None
        self.commands: list[str] = []

    def axis(self, name: str) -> Any:
        return self._axis.get(name)

    def label(self, name: str) -> Any:
        if name in {"Title", "title", "py_title"}:
            return self._title
        return None

    def lt_exec(self, command: str) -> bool:
        self.commands.append(command)
        return True

    def get_float(self, key: str) -> float:
        values = {
            "x.from": 0.0,
            "x.to": 10.0,
            "y.from": 0.0,
            "y.to": 100.0,
        }
        return values[key]


class _Graph:
    def __init__(self, layer: _Layer) -> None:
        self._layer = layer
        self.commands: list[str] = []
        self.lname = ""
        self.name = ""

    def __getitem__(self, index: int) -> _Layer:
        assert index == 0
        return self._layer

    def lt_exec(self, command: str) -> bool:
        self.commands.append(command)
        return True


class _Origin:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def lt_exec(self, command: str) -> bool:
        self.commands.append(command)
        return True


def test_escape_origin_text_escapes_double_quotes() -> None:
    assert escape_origin_text('A "quoted" title') == 'A ""quoted"" title'


def test_set_origin_axis_title_sets_axis_label_text() -> None:
    layer = _Layer()
    set_origin_axis_title(layer, "x", "Temperature [°C]")
    assert layer.axis("x").label.text == "Temperature [°C]"


def test_set_origin_axis_title_falls_back_to_ltalk_when_axis_missing() -> None:
    layer = _Layer()
    layer._axis["x"] = None
    set_origin_axis_title(layer, "x", 'T "axis"')
    assert layer.commands
    assert layer.commands[-1] == 'label -s -xb "T ""axis""";'


def test_set_origin_graph_title_sets_title_label_and_graph_names() -> None:
    layer = _Layer(with_title_label=True)
    graph = _Graph(layer)
    origin = _Origin()
    set_origin_graph_title(origin, graph, layer, "Sample A")
    assert graph.lname == "Sample A"
    assert graph.name
    assert layer.label("Title").text == "Sample A"


def test_set_origin_graph_title_uses_ltalk_fallback_without_title_label() -> None:
    layer = _Layer(with_title_label=False)
    graph = _Graph(layer)
    origin = _Origin()
    set_origin_graph_title(origin, graph, layer, 'Sample "B"')
    combined = graph.commands + layer.commands + origin.commands
    assert any('title -s "Sample ""B""";' in command for command in combined)
