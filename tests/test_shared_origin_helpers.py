from __future__ import annotations

from typing import Any

from plotting.shared.origin import (
    escape_origin_text,
    origin_title_xy,
    set_origin_axis_title,
    set_origin_graph_title,
    origin_lt_exec,
    finish_origin_graph,
    set_origin_categorical_axis,
)


def test_title_position_respects_log_scales_and_page_headroom() -> None:
    class Layer:
        def get_float(self, name):
            return {"x.from": 1, "x.to": 100, "y.from": 1, "y.to": 1000,
                    "x.type": 2, "y.type": 2, "top": 12, "height": 72}[name]

    x, y = origin_title_xy(Layer())
    assert abs(x - 10) < 1e-9
    assert 2000 < y < 2300


def test_labtalk_failure_is_not_hidden_by_sdk_none_return() -> None:
    class Native:
        def LT_execute(self, command):
            return 0

    class Wrapper:
        obj = Native()

        def lt_exec(self, command):
            self.obj.LT_execute(command)  # originpro discards its native status.

    assert origin_lt_exec(Wrapper().lt_exec, "legend.smartpos=1;") is False


def test_matplotlib_axis_math_is_translated_to_origin_text() -> None:
    assert escape_origin_text(r"$\Delta h^{3/2}$") == "Δ h\\+(3/2)"
    assert escape_origin_text(r"$\ln(dp/dh)$") == "ln(dp/dh)"


def test_dedicated_export_finishing_uses_larger_frame_and_smart_legend() -> None:
    class Layer:
        def __init__(self):
            self.commands = []

        def lt_exec(self, command):
            self.commands.append(command)
            return True

    layer = Layer()
    finish_origin_graph(layer)
    assert "layer -u 1; layer 80 72 14 12;" in layer.commands
    assert layer.commands[-1] == "legend.smartpos=1;"


class _Label:
    def __init__(self) -> None:
        self.text = ""
        self.show = False
        self._ints: dict[str, int] = {}
        self._floats: dict[str, float] = {"x": 173.0}

    def set_int(self, key: str, value: int) -> None:
        self._ints[key] = int(value)

    def set_float(self, key: str, value: float) -> None:
        self._floats[key] = float(value)

    def get_float(self, key: str) -> float:
        return float(self._floats.get(key, 0.0))


class _Axis:
    def __init__(self) -> None:
        self.label = _Label()

    def set_limits(self, *limits):
        self.limits = limits


class _Layer:
    def __init__(self, *, with_title_label: bool = True) -> None:
        self._axis = {"x": _Axis(), "y": _Axis(), "x2": _Axis(), "y2": _Axis()}
        self._title = _Label() if with_title_label else None
        self._yr = _Label()
        self._xt = _Label()
        self.commands: list[str] = []

    def axis(self, name: str) -> Any:
        return self._axis.get(name)

    def label(self, name: str) -> Any:
        if name in {"Title", "title", "py_title"}:
            return self._title
        if name in {"xt", "XT", "Xt"}:
            return self._xt
        if name in {"yr", "YR", "Yr"}:
            return self._yr
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


def test_sample_captions_are_outside_the_data_frame_and_need_no_existing_labels():
    class Layer(_Layer):
        def __init__(self):
            super().__init__()
            self.labels = []

        def label(self, name):
            return next((label for label in self.labels if label.name == name), None)

        def add_label(self, text, *position):
            label = _Label()
            label.text = text
            label.name = ""
            label.set_float("width", 20)
            self.labels.append(label)
            return label

        def remove_label(self, label):
            assert label is not None
            self.labels.remove(label)

    layer = Layer()
    set_origin_categorical_axis(layer, ['Piece "A"', 'Piece B'])
    assert layer.axis("x").limits == (.5, 2.5, 1)
    first, second, caption = layer.labels
    assert first.text == 'Piece "A"'
    assert first._floats["y1"] < layer.get_float("y.from")
    assert first._floats["x1"] == 1 and second._floats["x1"] == 2
    assert caption._floats["y1"] < first._floats["y1"]
    assert caption.text == "Sample"
    assert "layer.x.showAxes=1; layer.x.showLabels=0;" in layer.commands


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


def test_escape_origin_text_converts_unicode_subscripts() -> None:
    assert escape_origin_text("Strain [%] (l₀ = 52.8 mm)") == "Strain [%] (l\\-(0) = 52.8 mm)"


def test_escape_origin_text_converts_unicode_superscripts() -> None:
    assert escape_origin_text("Current density [A/mm²]") == "Current density [A/mm\\+(2)]"


def test_set_origin_axis_title_sets_axis_label_text() -> None:
    layer = _Layer()
    set_origin_axis_title(layer, "x", "Temperature [°C]")
    assert layer.axis("x").label.text == "Temperature [°C]"


def test_set_origin_axis_title_falls_back_to_ltalk_when_axis_missing() -> None:
    layer = _Layer()
    layer._axis["x"] = None
    set_origin_axis_title(layer, "x", 'T "axis"')
    assert layer.commands
    assert 'label -s -xb "T ""axis""";' in layer.commands
    assert "layer.x.color = color(black);" in layer.commands


def test_set_origin_axis_title_uses_the_axis_range_for_right_label_position() -> None:
    layer = _Layer()
    original_get_float = layer.get_float
    layer.get_float = lambda key: {"x.from": 20.0, "x.to": 150.0}.get(key, original_get_float(key))
    layer._yr.set_float("x", 173.0)
    set_origin_axis_title(layer, "y2", "Magnetization (50 Oe) [emu]")
    assert layer.axis("y2").label.text == "Magnetization (50 Oe) [emu]"
    assert 150.0 < layer._yr.get_float("x") <= 180.0
    assert "layer.y2.color = color(black);" in layer.commands


def test_set_origin_axis_title_places_top_label_above_axis() -> None:
    layer = _Layer()
    set_origin_axis_title(layer, "x2", "Strain [%]")
    assert layer.axis("x2").label.text == "Strain [%]"
    assert layer.axis("x2").label.get_float("fsize") == 14.0
    assert layer._xt.get_float("x") == 5.0
    assert layer._xt.get_float("y") == 113.5
    assert layer._xt._ints.get("horzalign") == 1
    assert "layer.x2.color = color(black);" in layer.commands


def test_origin_title_xy_stays_inside_origin_ole_preview_bounds() -> None:
    layer = _Layer()
    assert origin_title_xy(layer) == (5.0, 124.0)


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
