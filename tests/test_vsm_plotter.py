from __future__ import annotations

from pathlib import Path

import math

import importlib.util
import sys

import pandas as pd
import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "experiments" / "vsm_plotter.py"

spec = importlib.util.spec_from_file_location("vsm_plotter", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules["vsm_plotter"] = module
try:
    spec.loader.exec_module(module)  # type: ignore[call-arg]
except ImportError as exc:  # pragma: no cover - environment guard
    import pytest

    pytest.skip(f"VSM plotter dependencies missing: {exc}", allow_module_level=True)


def _write_sample(tmp_path: Path) -> Path:
    content = """@Section 0
Column 0: Time since start, Time [s]
Column 1: Applied Field, Applied Field [Oe]
Column 2: Signal parallel with sample, Moment [emu]
@@END Columns
@@End of Header.
@Time at start of measurement: 10:43:37
@@Data
New Section: Section 0:
0.0 0.0 0.0
1.0 5.0 0.2
2.0 -5.0 -0.2
@@END Data
"""
    path = tmp_path / "202507101320-Hys-a140-T-30-00.VSM-Hys-Data"
    path.write_text(content)
    return path


def test_read_vsm_file_parses_numeric_columns(tmp_path: Path) -> None:
    path = _write_sample(tmp_path)
    df = module._read_vsm_file(path)
    assert list(df.columns)[:3] == [
        "Time since start [s]",
        "Applied Field [Oe]",
        "Signal parallel with sample [emu]",
    ]
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3


def test_read_vsm_file_handles_inline_header_and_noise(tmp_path: Path) -> None:
    path = tmp_path / "202507101320-Hys-a050-T-30-00.VSM-Hys-Data"
    content = """@@End of Header.
Random metadata line
Instrument 1k Oe: Field Setting Par : FTCR = 5000; DR = 10000;
Time_since_start Applied_Field Loop
@@Data
New Section: Section 0:
0.0 0.0 0.0
@@END Data.
Notes between sections
@@Final Manipulated Data
New Section: Section 0:
1.0 5.0 0.2
2.0 -5.0 -0.3
@@END Data
"""
    path.write_text(content)

    df = module._read_vsm_file(path)

    assert list(df.columns) == ["Time since start", "Applied Field", "Loop"]
    assert len(df) == 2
    assert df.iloc[0].tolist() == [1.0, 5.0, 0.2]


def test_read_vsm_file_prefers_column_block_to_noise(tmp_path: Path) -> None:
    path = tmp_path / "202507101115-Hys-a000-T-30-00.VSM-Hys-Data"
    content = """Random preamble text
1k Oe: Field Setting Par : FTCR = 5000; DR = 10000;
@@Columns
Column 0: Time since start, Time [s]
Column 1: Raw Applied Field, Applied Field [Oe]
Column 2: Field Angle, Field Angle [deg]
@@END Columns
@@End of Header.
Time_since_start Raw_Applied_Field Field_Angle
@@Data
New Section: Section 0:
1.0 100.0 0.0
2.0 -100.0 0.0
@@END Data
"""
    path.write_text(content)

    df = module._read_vsm_file(path)

    assert list(df.columns) == [
        "Time since start [s]",
        "Raw Applied Field [Oe]",
        "Field Angle [deg]",
    ]
    assert df.iloc[0].tolist() == [1.0, 100.0, 0.0]


def test_parse_temperature_and_angle(tmp_path: Path) -> None:
    path = _write_sample(tmp_path)
    assert module._parse_temperature(path) == -30.0
    assert module._parse_angle(path) == 140.0


def test_parse_metadata_falls_back_to_header(tmp_path: Path) -> None:
    path = tmp_path / "fallback.VSM-Hys-Data"
    content = """@Filename: C\\data\\Ni\\20250712-Hys-a095-T-45-00.VSM-Hys-Data
@@Data
New Section: Section 0:
1.0 2.0 3.0
@@END Data
"""
    path.write_text(content)

    # Ensure cached values from previous tests do not leak in.
    module._metadata_from_file.cache_clear()  # type: ignore[attr-defined]

    assert module._parse_angle(path) == 95.0
    assert module._parse_temperature(path) == -45.0


def test_parse_handles_zero_angle_token(tmp_path: Path) -> None:
    path = tmp_path / "202507101115-Hys-a000-T-30-00.VSM-Hys-Data"
    path.write_text("@@Data\nNew Section: Section 0:\n1 2 3\n@@END Data\n")

    module._metadata_from_file.cache_clear()  # type: ignore[attr-defined]

    assert module._parse_angle(path) == 0.0
    assert module._parse_temperature(path) == -30.0


def test_parse_metadata_from_set_temperature_line(tmp_path: Path) -> None:
    path = tmp_path / "no_tokens.VSM-Hys-Data"
    content = """Action 0:      Set Field Angle to -15.5 [deg]
Action 1:      Set Sample Temperature to -29.5989 [degC]
@@Data
New Section: Section 0:
1 2 3
@@END Data
"""
    path.write_text(content)

    module._metadata_from_file.cache_clear()  # type: ignore[attr-defined]

    assert module._parse_angle(path) == -15.5
    assert module._parse_temperature(path) == -30.0


def test_metadata_rounds_action_block_values(tmp_path: Path) -> None:
    path = tmp_path / "rounding.VSM-Hys-Data"
    content = """Action 0:      Set Field Angle to 9.9998 [deg]
Action 1:      Set Sample Temperature to -30.1037 [degC]
@@Data
New Section: Section 0:
1 2 3
@@END Data
"""
    path.write_text(content)

    module._metadata_from_file.cache_clear()  # type: ignore[attr-defined]

    assert module._parse_angle(path) == 10.0
    assert module._parse_temperature(path) == -30.0


def test_parse_metadata_from_angle_offset(tmp_path: Path) -> None:
    path = tmp_path / "offset_only.VSM-Hys-Data"
    content = """Sample Angle Offset = 42.0
@@Data
New Section: Section 0:
1 2 3
@@END Data
"""
    path.write_text(content)

    module._metadata_from_file.cache_clear()  # type: ignore[attr-defined]

    assert module._parse_angle(path) == 42.0


def test_parse_metadata_handles_positive_temperature_token(tmp_path: Path) -> None:
    path = tmp_path / "202507101555-Hys-a000-T030-00.VSM-Hys-Data"
    path.write_text("@@Data\nNew Section: Section 0:\n1 2 3\n@@END Data\n")

    module._metadata_from_file.cache_clear()  # type: ignore[attr-defined]

    assert module._parse_angle(path) == 0.0
    assert module._parse_temperature(path) == 30.0


def test_clean_folder_name_sanitises_tokens() -> None:
    assert module._clean_folder_name("Folder name/with spaces") == "Folder_name_with_spaces"


def test_suggest_export_subfolder_prefers_measurement(tmp_path: Path) -> None:
    df = pd.DataFrame({"value": [1]})
    path = tmp_path / "202507101115-Hys-a000-T-30-00.VSM-Hys-Data"
    measurement = module.VSMMeasurement(path=path, temperature=None, angle=None, data=df)

    suggested = module._suggest_export_subfolder([measurement])

    assert suggested == "202507101115-Hys-a000-T-30-00.VSM-Hys-Data"


def test_temperature_subfolder_name_formats_signs() -> None:
    assert module._temperature_subfolder_name(-30.0) == "T-30C"
    assert module._temperature_subfolder_name(25.0) == "T_25C"
    assert module._temperature_subfolder_name(25.5).startswith("T_25.5C")


def test_apply_rescaling_handles_near_constant_loop(tmp_path: Path) -> None:
    flat_path = tmp_path / "flat.VSM-Hys-Data"
    reference_path = tmp_path / "reference.VSM-Hys-Data"
    df_flat = pd.DataFrame({"X": [-1000.0, 0.0, 1000.0], "Y": [1.77e-20, 1.78e-20, 1.79e-20]})
    df_reference = pd.DataFrame({"X": [-1000.0, 0.0, 1000.0], "Y": [-9.0e-4, 0.0, 9.0e-4]})

    results = module._apply_rescaling(
        [
            (flat_path, df_flat),
            (reference_path, df_reference),
        ],
        "X",
        "Y",
    )

    assert flat_path in results and reference_path in results
    assert results[flat_path].applied
    assert results[flat_path].replacement is None
    assert math.isclose(results[flat_path].target_left, -9.0e-4, rel_tol=1e-6, abs_tol=1e-12)
    assert math.isclose(results[flat_path].target_right, 9.0e-4, rel_tol=1e-6, abs_tol=1e-12)

    transformed = df_flat["Y"] * results[flat_path].scale + results[flat_path].offset
    assert math.isclose(float(transformed.iloc[0]), results[flat_path].target_left, rel_tol=1e-3, abs_tol=1e-9)
    assert math.isclose(float(transformed.iloc[-1]), results[flat_path].target_right, rel_tol=1e-3, abs_tol=1e-9)


def test_read_vsm_file_rejects_empty(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.VSM-Hys-Data"
    empty_path.write_text("@@Data\n@@END Data\n")
    try:
        module._read_vsm_file(empty_path)
    except ValueError as exc:
        assert "No data rows" in str(exc)
    else:  # pragma: no cover - ensure failure is surfaced
        raise AssertionError("Expected ValueError for empty VSM file")


def test_safe_float_handles_dash_tokens() -> None:
    assert module._safe_float("-30-00") == -30.0
    assert module._safe_float("30-50") == 30.50
    assert module._safe_float("+12-345") == 12.345
    assert module._safe_float("000-") == 0.0


def test_metadata_can_be_derived_from_columns(tmp_path: Path) -> None:
    content = """@@Columns
Column 0: Time since start, Time [s]
Column 1: Field Angle, Field Angle [deg]
Column 2: Temperature, Sample Temperature [degC]
Column 3: Applied Field, Applied Field [Oe]
@@END Columns
@@End of Header.
@@Data
New Section: Section 0:
0.0 12.0 -29.8 0.0
1.0 12.0 -30.2 1.0
2.0 12.0 -30.0 -1.0
@@END Data
"""
    path = tmp_path / "no_header_metadata.VSM-Hys-Data"
    path.write_text(content)

    df = module._read_vsm_file(path)
    angle, temperature = module._derive_metadata_from_dataframe(df)

    assert angle == pytest.approx(12.0)
    assert temperature == pytest.approx(-30.0, abs=0.2)


def test_estimate_edge_values_handles_two_points() -> None:
    df = pd.DataFrame({"H": [-10.0, 10.0], "M": [-1.0, 1.0]})

    left, right = module._estimate_edge_values(df, "H", "M")

    assert left == pytest.approx(-1.0)
    assert right == pytest.approx(1.0)


def test_estimate_edge_values_sorts_by_field() -> None:
    df = pd.DataFrame({"H": [10.0, -10.0, 5.0, -9.0], "M": [1.0, -1.0, 0.5, -0.9]})

    left, right = module._estimate_edge_values(df, "H", "M")

    assert left == pytest.approx(-0.95, abs=0.1)
    assert right == pytest.approx(1.0, abs=0.1)


def test_apply_rescaling_inverts_and_scales() -> None:
    base = pd.DataFrame({"H": [-10.0, 10.0], "M": [-1.0, 1.0]})
    flipped = pd.DataFrame({"H": [-10.0, 10.0], "M": [2.0, -2.0]})

    results = module._apply_rescaling(
        [(Path("base"), base), (Path("flipped"), flipped)],
        "H",
        "M",
    )

    assert Path("base") in results
    assert Path("flipped") in results

    base_result = results[Path("base")]
    flipped_result = results[Path("flipped")]

    assert base_result.scale == pytest.approx(1.0)
    assert base_result.offset == pytest.approx(0.0)

    transformed = flipped["M"] * flipped_result.scale + flipped_result.offset
    assert transformed.iloc[0] == pytest.approx(-1.0)
    assert transformed.iloc[1] == pytest.approx(1.0)


def test_apply_rescaling_generates_gradient_for_constant_measurements() -> None:
    base = pd.DataFrame({"H": [-10.0, 10.0], "M": [-1.0, 1.0]})
    flat = pd.DataFrame({"H": [-10.0, 10.0], "M": [0.2, 0.2]})

    results = module._apply_rescaling(
        [(Path("base"), base), (Path("flat"), flat)],
        "H",
        "M",
    )

    flat_result = results[Path("flat")]
    assert flat_result.applied is True
    assert flat_result.replacement is not None
    assert len(flat_result.replacement) == len(flat)
    assert flat_result.replacement.iloc[0] == pytest.approx(flat_result.target_left, abs=1e-9)
    assert flat_result.replacement.iloc[-1] == pytest.approx(flat_result.target_right, abs=1e-9)


def test_apply_rescaling_scales_near_constant_measurements() -> None:
    base = pd.DataFrame({"H": [-10.0, 10.0], "M": [-1.0, 1.0]})
    tiny = pd.DataFrame({"H": [-10.0, 10.0], "M": [-5e-20, 5e-20]})

    results = module._apply_rescaling(
        [(Path("base"), base), (Path("tiny"), tiny)],
        "H",
        "M",
    )

    base_result = results[Path("base")]
    tiny_result = results[Path("tiny")]

    assert tiny_result.replacement is None
    transformed = tiny["M"] * tiny_result.scale + tiny_result.offset
    assert transformed.iloc[0] == pytest.approx(base_result.target_left, rel=1e-6, abs=1e-12)
    assert transformed.iloc[-1] == pytest.approx(base_result.target_right, rel=1e-6, abs=1e-12)


def test_apply_rescaling_recovers_flat_edges() -> None:
    base = pd.DataFrame({"H": [-10.0, 10.0], "M": [-1.0, 1.0]})
    awkward = pd.DataFrame(
        {
            "H": [-10.0, -9.0, -8.0, 8.0, 9.0, 10.0],
            "M": [0.0, -0.6, -0.8, 0.7, 0.9, 0.0],
        }
    )

    results = module._apply_rescaling(
        [(Path("base"), base), (Path("awkward"), awkward)],
        "H",
        "M",
    )

    awkward_result = results[Path("awkward")]
    assert awkward_result.applied is True

    transformed = awkward["M"] * awkward_result.scale + awkward_result.offset
    assert transformed.min() == pytest.approx(awkward_result.target_left)
    assert transformed.max() == pytest.approx(awkward_result.target_right)


def test_find_vsm_files_recurses(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    top_file = root / "top.VSM-Hys-Data"
    nested_file = nested / "deep.VSM-Hys-Data"
    lowercase = nested / "alt.vsm-hys-data"
    other = nested / "ignore.txt"
    top_file.write_text("@@Data\n@@END Data\n")
    nested_file.write_text("@@Data\n@@END Data\n")
    lowercase.write_text("@@Data\n@@END Data\n")
    other.write_text("noop")

    results = module._find_vsm_files(root)

    assert [path.relative_to(root) for path in results] == [
        Path("nested/alt.vsm-hys-data"),
        Path("nested/deep.VSM-Hys-Data"),
        Path("top.VSM-Hys-Data"),
    ]


def test_find_vsm_files_includes_copy_suffix(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    expected = root / "202507101555-Hys-a000-T030-00.VSM-Hys-Data - Copy"
    expected.write_text("@@Data\n@@END Data\n")

    results = module._find_vsm_files(root)

    assert results == [expected]


def test_find_vsm_files_handles_suffixless_exports(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    expected = root / "202507101555-Hys-a000-T030-00"
    expected.write_text("@@Data\n@@END Data\n")

    results = module._find_vsm_files(root)

    assert results == [expected]


def test_apply_rescaling_symmetrises_targets() -> None:
    base = pd.DataFrame({"H": [-10.0, 10.0], "M": [-1.5, 1.2]})
    narrow = pd.DataFrame({"H": [-10.0, 10.0], "M": [-1e-3, 5e-4]})

    results = module._apply_rescaling(
        [(Path("base"), base), (Path("narrow"), narrow)],
        "H",
        "M",
    )

    base_result = results[Path("base")]
    narrow_result = results[Path("narrow")]

    assert base_result.target_right == pytest.approx(-base_result.target_left)

    transformed = narrow["M"] * narrow_result.scale + narrow_result.offset
    assert transformed.iloc[0] == pytest.approx(base_result.target_left, rel=1e-6, abs=1e-9)
    assert transformed.iloc[-1] == pytest.approx(base_result.target_right, rel=1e-6, abs=1e-9)


class _FakeSheet:
    def __init__(self) -> None:
        self.data = None
        self.labels: dict[int, str] = {}
        self.name = ""
        self.comment = ""
        self.activated = False
        self.column_comments: dict[int, str] = {}

    def from_df(self, df: pd.DataFrame) -> None:
        self.data = df

    def cols_axis(self, *_: object) -> None:  # pragma: no cover - behaviour not asserted
        return

    def set_label(self, col: int, label: str) -> None:
        self.labels[col] = label

    def set_comment(self, col: int, comment: str) -> None:
        self.column_comments[col] = comment

    def activate(self) -> None:  # pragma: no cover - behaviour not asserted
        self.activated = True


class _FakeBook(list):
    def __init__(self) -> None:
        super().__init__([_FakeSheet()])
        self.lname = ""
        self.name = ""
        self.activated = False

    def activate(self) -> None:
        self.activated = True

    def add_sheet(self) -> _FakeSheet:
        sheet = _FakeSheet()
        self.append(sheet)
        return sheet


class _FakePlot:
    def __init__(self, sheet: _FakeSheet) -> None:
        self.sheet = sheet
        self.legend = ""


class _FakeLayer:
    def __init__(self) -> None:
        self.plots: list[_FakePlot] = []
        self.rescaled = False

    def add_plot(self, sheet: _FakeSheet, **_: object) -> _FakePlot:
        plot = _FakePlot(sheet)
        self.plots.append(plot)
        return plot

    def rescale(self) -> None:
        self.rescaled = True


class _FakeGraph:
    def __init__(self) -> None:
        self.layers = [_FakeLayer()]
        self.lname = ""
        self.name = ""
        self.activated = False

    def __getitem__(self, index: int) -> _FakeLayer:
        return self.layers[index]

    def activate(self) -> None:
        self.activated = True


class _FakeOrigin:
    def __init__(self) -> None:
        self.books: list[_FakeBook] = []
        self.graphs: list[_FakeGraph] = []
        self.commands: list[str] = []

    def new_book(self, *_: object, **kwargs: object) -> _FakeBook:
        book = _FakeBook()
        book.lname = str(kwargs.get("lname", ""))
        self.books.append(book)
        return book

    def new_graph(self, *_: object, **__: object) -> _FakeGraph:
        graph = _FakeGraph()
        self.graphs.append(graph)
        return graph

    def lt_exec(self, command: str) -> None:
        self.commands.append(command)


def test_build_origin_group_sets_names_and_legends() -> None:
    plotter = module.VSMPlotter.__new__(module.VSMPlotter)
    # Bind helper methods expected by _build_origin_group
    plotter._escape_origin_text = module.VSMPlotter._escape_origin_text.__get__(plotter)
    plotter._origin_book_name = module.VSMPlotter._origin_book_name.__get__(plotter)
    plotter._origin_graph_short_name = module.VSMPlotter._origin_graph_short_name.__get__(plotter)

    measurement = module.VSMMeasurement(
        Path("202507101115-Hys-a010-T-30-00.VSM-Hys-Data"),
        -30.0,
        10.0,
        pd.DataFrame({"H": [-10.0, 10.0], "M": [-1.0, 1.0]}),
    )

    subset = pd.DataFrame({"H": [-10.0, 10.0], "M": [-1.0, 1.0]})
    fake_origin = _FakeOrigin()

    plotter._build_origin_group(
        fake_origin,
        -30.0,
        [(measurement, subset)],
        "H",
        "M",
    )

    assert fake_origin.books, "Expected a workbook to be created"
    assert fake_origin.graphs, "Expected a graph to be created"

    book = fake_origin.books[0]
    sheet = book[0]
    assert sheet.name == "a10"
    assert sheet.data.equals(subset)
    assert sheet.labels == {0: "H", 1: "M"}
    assert sheet.comment == "Angle 10°"
    assert sheet.column_comments == {1: "Angle 10°"}

    graph = fake_origin.graphs[0]
    assert graph.lname == "-30 °C"
    assert graph.name == plotter._origin_graph_short_name(-30.0)

    layer = graph[0]
    assert layer.plots, "Expected a plot to be added to the Origin layer"
    assert layer.plots[0].legend == "Angle 10°"
    assert any("wks.col2.comment$=\"Angle 10°\";" in cmd for cmd in fake_origin.commands)


def test_toggle_line_visibility_updates_lines_and_state() -> None:
    plotter = module.VSMPlotter.__new__(module.VSMPlotter)
    plotter._line_visibility = {}
    plotter._plot_tabs = {}
    plotter._angle_checkboxes = {}

    class _DummyDarkToggle:
        def isChecked(self) -> bool:
            return False

    plotter.dark_mode_checkbox = _DummyDarkToggle()
    plotter._refresh_tab_legend = module.VSMPlotter._refresh_tab_legend.__get__(plotter)
    plotter._toggle_line_visibility = module.VSMPlotter._toggle_line_visibility.__get__(plotter)

    class _DummyLine:
        def __init__(self, label: str) -> None:
            self.label = label
            self.visible = True

        def set_visible(self, value: bool) -> None:
            self.visible = value

        def get_visible(self) -> bool:
            return self.visible

        def get_label(self) -> str:
            return self.label

    class _DummyCanvas:
        def __init__(self) -> None:
            self.draw_calls = 0

        def draw_idle(self) -> None:
            self.draw_calls += 1

    class _DummyFigure:
        def __init__(self) -> None:
            self.tight_layout_calls = 0

        def tight_layout(self) -> None:
            self.tight_layout_calls += 1

    class _DummyLegend:
        def __init__(self, axes: "_DummyAxes") -> None:
            self.axes = axes
            self.handles: list[_DummyLine] = []
            self.labels: list[str] = []

        def remove(self) -> None:
            self.axes.removed_calls.append(True)

    class _DummyAxes:
        def __init__(self) -> None:
            self.figure = _DummyFigure()
            self.legend_history: list[tuple[list[_DummyLine], list[str], str]] = []
            self.removed_calls: list[bool] = []
            self.legend_ = _DummyLegend(self)

        def legend(
            self,
            handles: list[_DummyLine],
            labels: list[str],
            loc: str = "best",
        ) -> _DummyLegend:
            legend = _DummyLegend(self)
            legend.handles = handles
            legend.labels = labels
            self.legend_history.append((handles, labels, loc))
            self.legend_ = legend
            return legend

        def grid(self, *_: object, **__: object) -> None:
            return

    temperature = -30.0
    angle = 45.0
    line = _DummyLine("45°")
    axes = _DummyAxes()
    canvas = _DummyCanvas()
    tab_state = module.PlotTabState(axes=axes, canvas=canvas, lines={angle: line})
    plotter._plot_tabs[temperature] = tab_state

    plotter._toggle_line_visibility(temperature, angle, False)
    assert plotter._line_visibility[temperature][angle] is False
    assert not line.visible
    assert canvas.draw_calls == 1
    assert axes.removed_calls == [True]
    assert axes.legend_history[-1][0] == []

    plotter._toggle_line_visibility(temperature, angle, True)
    assert plotter._line_visibility[temperature][angle] is True
    assert line.visible
    assert canvas.draw_calls == 2
    handles, labels, _ = axes.legend_history[-1]
    assert handles == [line]
    assert labels == ["45°"]

