"""Regressions for the data and view properties lost by shared Origin export."""
from __future__ import annotations

import numpy as np
import pytest
import json
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PyQt6 import QtCore, QtWidgets

from plotting.pyplot.app import PyPlotWorkbench
from plotting.pyplot.window import TabDescriptor, WorkbookData, GraphLineState


@pytest.fixture
def workbench(qapp, monkeypatch, tmp_path):
    original = QtCore.QSettings

    class Settings(original):
        def __init__(self, *args, **kwargs):
            if len(args) >= 2 and all(isinstance(v, str) for v in args[:2]):
                name = "-".join(args[:2]).replace("/", "_")
                super().__init__(str(tmp_path / f"{name}.ini"), original.Format.IniFormat)
            else:
                super().__init__(*args, **kwargs)

    monkeypatch.setattr(QtCore, "QSettings", Settings)
    window = PyPlotWorkbench(plotters={})
    yield window
    window._project_dirty = False
    window.close()


def worksheet(window, fig, ax):
    if not isinstance(fig.canvas, FigureCanvasAgg):
        FigureCanvasAgg(fig)
    descriptor = TabDescriptor(
        "synthetic", "Export regression", "Synthetic", ax.get_xlabel(),
        ax.get_ylabel(), fig.canvas, ax, {}, {"plugin": "Synthetic"},
    )
    built = window._build_shared_plot_workbook(QtWidgets.QWidget(), descriptor)
    assert built is not None
    return built[1][0]


def test_broken_trace_keeps_a_gap_instead_of_connecting_across_it(workbench):
    fig = Figure()
    ax = fig.subplots()
    ax.plot([1, 2, 3, 4, 5], [1, 2, np.nan, 4, 5], label="Interrupted")
    sheet = worksheet(workbench, fig, ax)
    assert len(sheet.dataframe) == 5
    assert sheet.dataframe.iloc[2].isna().all()
    assert sheet.dataframe.iloc[[0, 1, 3, 4], 1].tolist() == [1, 2, 4, 5]


def test_scatter_export_does_not_request_connecting_lines(workbench):
    fig = Figure()
    ax = fig.subplots()
    ax.scatter([1, 2, 3], [3, 1, 2], s=49, label="Measured points")
    sheet = worksheet(workbench, fig, ax)
    ymeta = list(sheet.columns.values())[1]
    assert ymeta.plot_linestyle.lower() == "none"
    assert ymeta.plot_markersize == pytest.approx(7)


def test_line_export_keeps_width_dash_and_marker_shape(workbench):
    fig = Figure()
    ax = fig.subplots()
    ax.plot([1, 2], [3, 4], lw=2.6, ls="--", marker="D", ms=3, label="Repeat")
    sheet = worksheet(workbench, fig, ax)
    ymeta = list(sheet.columns.values())[1]
    assert ymeta.plot_linewidth == pytest.approx(2.6)
    assert ymeta.plot_linestyle == "--"
    assert ymeta.plot_marker == "D"
    assert workbench._origin_plot_marker_size(sheet, 1) == pytest.approx(3)


def test_export_keeps_log_limits_and_hidden_legend(workbench):
    fig = Figure()
    ax = fig.subplots()
    ax.plot([1, 2, 3], [2, 3, 4], label="Signal")
    ax.set(xscale="log", xlim=(1.5, 2.8), ylim=(0, 6))
    ax.legend().set_visible(False)
    sheet = worksheet(workbench, fig, ax)
    view = sheet.metadata["origin_axes"]["0"]
    assert view["xscale"] == "log"
    assert view["xlim"] == [1.5, 2.8]
    assert view["ylim"] == [0.0, 6.0]
    assert view["legend_visible"] is False


def test_three_dimensional_export_uses_xyz_even_after_projection(workbench):
    fig = Figure()
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111, projection="3d")
    ax.scatter([1, 2, 3], [20, 40, 60], [100, 120, 140], label="Pieces")
    ax.set(xlabel="Strain [%]", ylabel="Temperature [C]", zlabel="Stress [MPa]")
    fig.canvas.draw()
    sheet = worksheet(workbench, fig, ax)
    assert sheet.axis_roles == "XYZ"
    assert sheet.dataframe.iloc[:, 0].tolist() == [1, 2, 3]
    assert sheet.dataframe.iloc[:, 1].tolist() == [20, 40, 60]
    assert sheet.dataframe.iloc[:, 2].tolist() == [100, 120, 140]
    assert list(sheet.columns.values())[2].units == "MPa"


def test_independent_panels_keep_distinct_axis_identity(workbench):
    fig = Figure()
    axes = fig.subplots(2, 1)
    for ax in axes:
        ax.plot([1, 2], [3, 4], label="Signal")
        ax.set(xlabel="Time [s]", ylabel="Signal [V]")
    sheet = worksheet(workbench, fig, axes[0])
    ymeta = [v for k, v in sheet.columns.items() if k.endswith("_y") or "_y_" in k]
    assert [m.plot_axis for m in ymeta] == [0, 1]
    assert sheet.metadata["origin_axes"]["0"]["panel"] != sheet.metadata["origin_axes"]["1"]["panel"]


def test_panel_metadata_keeps_hidden_upper_x_captions(workbench):
    fig = Figure()
    upper, lower = fig.subplots(2, 1)
    upper.plot([1, 2], [3, 4])
    lower.plot([1, 2], [5, 6])
    upper.tick_params(labelbottom=False)
    lower.set_xlabel("Time [s]")
    sheet = worksheet(workbench, fig, upper)
    view = sheet.metadata["origin_axes"]["0"]
    assert view["xlabel"] == ""
    assert view["x_ticklabels_visible"] is False


def test_tracked_primary_lines_do_not_drop_other_panels_or_raw_scatter(workbench):
    fig = Figure()
    FigureCanvasAgg(fig)
    first, second = fig.subplots(2, 1)
    line, = first.plot([1, 2], [3, 4], label="Processed")
    first.scatter([1, 2], [2, 3], label="Raw")
    second.plot([1, 2], [5, 6], label="Second load")
    state = GraphLineState(("tracked", 1), "Processed", line, [1, 2], [3, 4])
    descriptor = TabDescriptor("synthetic", "Panels", "Synthetic", "X", "Y", fig.canvas,
                               first, {state.key: state}, {"plugin": "Synthetic"})
    _, sheets = workbench._build_shared_plot_workbook(QtWidgets.QWidget(), descriptor)
    metas = [m for m in sheets[0].columns.values() if m.plot_axis >= 0]
    assert len(metas) == 3
    assert {m.comments for m in metas} == {"Processed", "Raw", "Second load"}


def test_three_dimensional_pieces_keep_their_labels_and_colors(workbench):
    fig = Figure()
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111, projection="3d")
    ax.scatter([1, 2], [20, 40], [100, 120], c=["#ff0000", "#0000ff"])
    ax.text(1, 20, 100, "Piece A")
    ax.text(2, 40, 120, "Piece B")
    fig.canvas.draw()
    sheet = worksheet(workbench, fig, ax)
    ymeta = [m for m in sheet.columns.values() if m.plot_z_column]
    assert [(m.comments, m.plot_color) for m in ymeta] == [("Piece A", "#ff0000"), ("Piece B", "#0000ff")]


def test_text_annotation_keeps_its_coordinate_system(workbench):
    fig = Figure()
    ax = fig.subplots()
    ax.plot([1, 2], [3, 4])
    ax.text(.2, .8, "Threshold = 42", transform=ax.transAxes)
    sheet = worksheet(workbench, fig, ax)
    annotation = sheet.metadata["origin_axes"]["0"]["annotations"][0]
    assert annotation["text"] == "Threshold = 42"
    assert annotation["coordinates"] == "axes"
    assert annotation["position"] == [.2, .8]


def test_figure_ylabel_is_preserved_for_shared_panel_export(workbench):
    fig = Figure()
    first, second = fig.subplots(2, 1)
    first.plot([1, 2], [3, 4])
    second.plot([1, 2], [5, 6])
    fig.supylabel("Probability density")
    sheet = worksheet(workbench, fig, first)
    captions = sheet.metadata["origin_axes"]["0"]["annotations"]
    assert any(text["text"] == "Probability density" and text["coordinates"] == "figure" for text in captions)


def test_scatter_symbol_is_not_replaced_by_a_circle(workbench):
    fig = Figure()
    ax = fig.subplots()
    ax.scatter([1, 2], [3, 4], marker="x", s=25)
    sheet = worksheet(workbench, fig, ax)
    assert list(sheet.columns.values())[1].plot_marker == "x"


def test_histogram_exports_bin_centers_and_counts(workbench):
    fig = Figure()
    ax = fig.subplots()
    counts, edges, _ = ax.hist([1, 1, 2, 3], bins=[.5, 1.5, 2.5, 3.5], label="Counts")
    sheet = worksheet(workbench, fig, ax)
    assert sheet.dataframe.iloc[:, 0].tolist() == [1, 2, 3]
    assert sheet.dataframe.iloc[:, 1].tolist() == counts.tolist()
    assert list(sheet.columns.values())[1].plot_kind == "column"


def test_twin_legend_keeps_labels_from_both_axes(workbench):
    fig = Figure()
    ax = fig.subplots()
    twin = ax.twinx()
    first, = ax.plot([1, 2], [3, 4], label="50 Oe")
    second, = twin.plot([1, 2], [30, 40], label="10000 Oe")
    ax.legend([first, second], ["50 Oe", "10000 Oe"])
    sheet = worksheet(workbench, fig, ax)
    assert sheet.metadata["origin_axes"]["0"]["legend_labels"] == ["50 Oe", "10000 Oe"]
    text = workbench._origin_legend_text(["50 Oe", "10000 Oe"], [(1, 1, "50 Oe"), (2, 1, "10000 Oe")], 1)
    assert text == "\\l(1.1) %(1.1)\n\\l(2.1) %(2.1)"


def test_vsm_temperature_export_does_not_duplicate_unit_in_long_name(workbench):
    fig = Figure()
    ax = fig.subplots()
    ax.plot([20, 30], [1, 2])
    ax.set_ylabel("Signal X [emu]")
    descriptor = TabDescriptor("synthetic", "VSM", "Synthetic", "Temperature", "Signal X [emu]",
                               fig.canvas, ax, {}, {"plugin": "VSM Temperature Scan"})
    _, sheets = workbench._build_shared_plot_workbook(QtWidgets.QWidget(), descriptor)
    meta = list(sheets[0].columns.values())[1]
    assert meta.long_name == "Magnetization"
    assert meta.units == "emu"


def test_annotation_at_zero_uses_explicit_coordinates_after_creation(workbench):
    class Label:
        def __init__(self):
            self.values = {"left": 10, "top": 20, "width": 8, "height": 4}

        def set_int(self, key, value):
            self.values[key] = value

        set_float = set_int

        def get_float(self, key):
            return self.values[key]

    class Layer:
        def add_label(self, *args):
            self.label = Label()
            return self.label

    layer = Layer()
    workbench._restore_origin_annotations(layer, {"annotations": [{
        "text": "Origin", "coordinates": "data", "position": [0, 0], "va": "bottom", "ha": "left",
    }]})
    assert layer.label.values["x1"] == 0
    assert layer.label.values["y1"] == 0
    assert layer.label.values["top"] == 16


def test_panel_caption_accepts_native_uppercase_axis_label_names(workbench):
    class Label:
        text = "Time"

        def __init__(self, values):
            self.values = values

        def get_float(self, key):
            return self.values[key]

        def set_float(self, key, value):
            self.values[key] = value

        def remove(self):
            pass

    class Layer:
        def __init__(self):
            self.caption = Label({"width": 10})
            self.anchor = Label({"left": 50})

        def label(self, token):
            return {"XB": self.caption, "py_axis_anchor": self.anchor}.get(token)

        def lt_exec(self, command):
            return True

    layer = Layer()
    workbench._center_origin_panel_x_caption(layer)
    assert layer.caption.values["left"] == 45


def test_line_style_emits_supported_native_commands_without_size_inflation():
    # Origin is an external COM process; this boundary records its documented
    # set-command API, rather than accepting arbitrary Python attributes.
    class Plot:
        __slots__ = ("commands",)

        def __init__(self):
            self.commands = []

        def set_cmd(self, *commands):
            self.commands.extend(commands)

    plot = Plot()
    PyPlotWorkbench._apply_origin_plot_style(
        plot, color="#1f77b4", show_symbols=True, symbol_size=3,
        marker="D", linestyle="--", line_width=2.6,
    )
    assert "-k 5" in plot.commands  # Origin's documented diamond symbol.
    assert "-z 3" in plot.commands
    assert "-d 1" in plot.commands
    assert "-w 1300" in plot.commands  # LabTalk widths are in 1/500 point.


def test_native_view_uses_log_scale_and_requested_limits(workbench):
    class Axis:
        def __init__(self):
            self.scale = "linear"
            self.limits = None

        def set_limits(self, lower, upper):
            self.limits = [lower, upper]

    class Layer:
        def __init__(self):
            self.axes = {name: Axis() for name in ("x", "y")}

        def axis(self, name):
            return self.axes[name]

    layer = Layer()
    workbench._apply_origin_view(layer, {"xscale": "log", "yscale": "linear", "xlim": [1.5, 2.8], "ylim": [0, 6]})
    assert layer.axes["x"].scale == "log10"
    assert layer.axes["x"].limits == [1.5, 2.8]
    assert layer.axes["y"].limits == [0, 6]


def test_saved_project_keeps_export_styles_and_view_metadata(workbench, tmp_path):
    fig = Figure()
    ax = fig.subplots()
    ax.plot([1, 2, 3], [3, np.nan, 4], marker="D", ls="--", lw=2.6)
    ax.set_xscale("log")
    sheet = worksheet(workbench, fig, ax)
    workbook = WorkbookData("saved-book", "Saved graph", [sheet.key])
    workbench._register_imported_workbook(workbook, [sheet])
    project = tmp_path / "origin-styles.pypj"
    workbench._write_project_file(project)
    payload = json.loads(project.read_text(encoding="utf-8"))
    restored = PyPlotWorkbench(plotters={})
    try:
        restored._apply_project_payload(payload, project_dir=tmp_path)
        loaded = restored._worksheets[sheet.key]
        ymeta = list(loaded.columns.values())[1]
        assert ymeta.plot_marker == "D"
        assert ymeta.plot_linestyle == "--"
        assert ymeta.plot_linewidth == pytest.approx(2.6)
        assert loaded.metadata["origin_axes"]["0"]["xscale"] == "log"
        assert loaded.dataframe.iloc[1].isna().all()
    finally:
        restored._project_dirty = False
        restored.close()


def test_long_captions_fit_a_single_column_figure(workbench):
    fig = Figure(figsize=(85 / 25.4, 2.6))
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.plot([1, 2, 3], [2, 3, 4])
    title = "Ni50Fe27Ga23 10/5 NG CA - repeated hysteresis measurement at 120 C"
    ax.set(title=title, xlabel="Applied magnetic field [Oe]", ylabel="Signal X direction [emu]")
    ax.title.set_fontsize(16)
    workbench._fit_figure_to_content(fig)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for text in (ax.title, ax.xaxis.label, ax.yaxis.label):
        bounds = text.get_window_extent(renderer)
        assert bounds.x0 >= -1 and bounds.y0 >= -1
        assert bounds.x1 <= fig.bbox.x1 + 1 and bounds.y1 <= fig.bbox.y1 + 1
    assert " ".join(ax.get_title().split()) == title


@pytest.mark.parametrize("module_name,class_name", [
    ("temperature_sensitivity.temp_sens_plugin", "TemperatureSensitivityPlugin"),
    ("stress_sensitivity.stress_sens_plugin", "StressSensitivityPlugin"),
    ("vsm_temperature_scan.vsm_temp_scan_plugin", "VSMTemperatureScanPlugin"),
    ("fmr.fmr_plugin", "FmrPlugin"),
    ("pdf_plotter.pdf_plotter_plugin", "PdfPlotterPlugin"),
    ("maxion_continuous.maxion_continuous_plugin", "MaxionContinuousPlugin"),
])
def test_custom_plugins_export_the_displayed_graph_before_recomputing(workbench, monkeypatch, module_name, class_name):
    from importlib import import_module

    plugin = getattr(import_module("plotting.plugins." + module_name), class_name)(workbench, "Synthetic")
    tab = QtWidgets.QWidget()
    workbench.tab_widget.addTab(tab, "Visible graph")
    plugin._plot_tabs = [tab]
    called = []
    monkeypatch.setattr(workbench, "_open_origin_shared", lambda: called.append(True))
    monkeypatch.setattr(plugin, "load_data", lambda: None)
    monkeypatch.setattr(plugin, "run_origin_export", lambda **kwargs: pytest.fail("Recomputed instead of exporting the visible graph"))
    plugin.open_origin()
    assert called == [True]


def test_origin_button_refreshes_cached_data_styles_and_view(workbench, monkeypatch):
    fig = Figure()
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    line, = ax.plot([1, 2], [3, 4], lw=1)
    descriptor = TabDescriptor("synthetic", "Current view", "Synthetic", "X", "Y", fig.canvas,
                               ax, {}, {"plugin": "Synthetic"})
    tab = QtWidgets.QWidget()
    workbench.tab_widget.addTab(tab, "Graph")
    workbench._tab_descriptors[tab] = descriptor
    workbench._current_plotter_name = "Synthetic"
    workbench._register_shared_plot_workbook_for_tab(tab, descriptor)
    line.set_ydata([30, 40])
    line.set_linewidth(2.5)
    ax.set_ylim(20, 50)
    exported = []

    def push(workbooks, **kwargs):
        exported.extend(workbench._worksheets[key] for wb in workbooks for key in wb.worksheets)
        return 1, 1, []

    monkeypatch.setattr(workbench, "_push_workbooks_to_origin", push)
    monkeypatch.setattr("plotting.pyplot.window.schedule_origin_release", lambda: None)
    workbench._automation_suppress_dialogs = True
    workbench._open_origin_shared()
    assert exported[0].dataframe.iloc[:, 1].tolist() == [30, 40]
    assert list(exported[0].columns.values())[1].plot_linewidth == 2.5
    assert exported[0].metadata["origin_axes"]["0"]["ylim"] == [20, 50]
