from __future__ import annotations

import csv
import os
import re
import math
from pathlib import Path
from typing import Any, List, Tuple, cast

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from .burnthrough import trim_burnthrough_glitch
from ..shared.backends import wants_matplotlib, wants_origin
from ..shared.utils import save_figure, show_plots, schedule_origin_release
from ..shared.origin import origin_session
from ..shared.readability import apply_readability_fonts, apply_readability

# Defaults
OUTPUT_DIR = os.getcwd()
SHOW_PLOTS = True
SAVE_PLOTS = False
SAVE_FORMAT = "png"
PNG_DPI = 1200
BACKEND = "matplotlib"
IMPROVE_READABILITY = False
SHOW_LEGEND = True
LEGEND_SIZE = 18
LEGEND_ORIENTATION = "auto"
LEGEND_SHOW_SYMBOLS = False
LEGEND_SYMBOL_SIZE = 10.0
TICK_SIZE = 18
AXIS_LABEL_SIZE = 18
TITLE_SIZE = 22
SHOW_TICK_LABELS = True
SHOW_AXIS_LABELS = True
SHOW_TITLE = True

ORIGIN_MODES: Tuple[str, str] = ("experimental", "simple")
ORIGIN_MODE: str = ORIGIN_MODES[1]


_SUBSCRIPT_PATTERN = re.compile(r"([A-Z][a-z])(\d+)")


def _format_origin_annotation(text: str) -> str:
    """Return Origin rich-text markup for the sample description."""

    formatted = text.replace("_", "/")

    def _sub(match: re.Match[str]) -> str:
        element, digits = match.groups()
        return f"{element}\\-({digits})"

    return _SUBSCRIPT_PATTERN.sub(_sub, formatted)


def load_file(path: str) -> pd.DataFrame:
    """Load current annealing tri-column file: I(A) V(V) R(Ohm).

    Returns a DataFrame with I_mA and R_Ohm columns.
    """
    def _read(sep: str | None) -> pd.DataFrame:
        return pd.read_csv(
            path,
            sep=sep,
            engine="python",
            header=None,
            comment="#",
            dtype=str,
        )

    try:
        df = _read(None)
    except (csv.Error, pd.errors.ParserError):
        df = _read(r"\s+")
    else:
        if df.shape[1] > 3:
            df = _read(r"\s+")
    if df.shape[1] < 3:
        raise ValueError(f"{path}: expected at least 3 columns (I, V, R)")
    df = df.iloc[:, :3].copy()
    df.columns = ["I_A", "V_V", "R_Ohm"]

    def _to_numeric(series: pd.Series) -> pd.Series:
        cleaned = (
            series.astype(str)
            .str.replace("\u2212", "-", regex=False)
            .str.replace(",", ".", regex=False)
            .str.strip()
        )
        return pd.to_numeric(cleaned, errors="coerce")

    df["I_A"] = _to_numeric(df["I_A"])
    df["V_V"] = _to_numeric(df["V_V"])
    df["R_Ohm"] = _to_numeric(df["R_Ohm"])
    df = df.dropna(subset=["I_A", "R_Ohm"]).reset_index(drop=True)
    while len(df) > 1 and float(df.loc[0, "R_Ohm"]) <= 0.0:
        df = df.iloc[1:].reset_index(drop=True)
    if df.empty:
        raise ValueError(f"{path}: no valid samples after parsing")
    median_abs = float(df["I_A"].abs().median()) if not df["I_A"].empty else 0.0
    if np.isnan(median_abs):
        median_abs = 0.0
    if median_abs > 20.0:
        df["I_mA"] = df["I_A"]
        df["I_A"] = df["I_A"] / 1e3
    else:
        df["I_mA"] = df["I_A"] * 1e3
    mask = (
        np.isfinite(df["I_mA"]) &
        np.isfinite(df["R_Ohm"]) &
        (df["I_mA"] != 0)
    )
    df = df.loc[mask].reset_index(drop=True)
    if df.empty:
        raise ValueError(f"{path}: no usable samples after filtering zeros")
    currents = df["I_mA"].to_numpy(dtype=float)
    resistances = df["R_Ohm"].to_numpy(dtype=float)
    trimmed_currents, trimmed_resistances = trim_burnthrough_glitch(currents, resistances)
    if trimmed_currents is not currents:
        # NumPy may or may not return the original view; guard with a length check.
        if trimmed_currents.shape[0] != currents.shape[0]:
            df = df.iloc[: trimmed_currents.shape[0]].copy()
            df["I_mA"] = trimmed_currents
            df["R_Ohm"] = trimmed_resistances
    if len(df.index) > 3:
        tolerance = 0.6
        mask = np.ones(len(df), dtype=bool)
        values = df["I_mA"].to_numpy(dtype=float)
        for idx, value in enumerate(values):
            if not math.isfinite(value):
                continue
            if abs(value - 1.0) <= tolerance and idx < len(values) - 1:
                mask[idx] = False
        if not mask.all():
            df = df.loc[mask].reset_index(drop=True)
    return df[["I_mA", "R_Ohm"]]


def _direction_profile(currents: np.ndarray) -> Tuple[np.ndarray, List[Tuple[int, int, float]]]:
    """Return per-sample directions and contiguous segments."""

    count = currents.size
    if count == 0:
        return np.array([], dtype=float), []
    if count == 1:
        return np.array([1.0], dtype=float), [(0, 1, 1.0)]

    deltas = np.diff(currents)
    abs_deltas = np.abs(deltas[np.isfinite(deltas)])
    if abs_deltas.size:
        tolerance = max(float(np.quantile(abs_deltas, 0.25) * 0.5), 0.01)
    else:
        tolerance = 0.01

    signed = np.sign(deltas)
    signed[np.abs(deltas) <= tolerance] = 0.0
    direction = pd.Series(signed, index=range(1, count))
    direction = direction.replace(0.0, np.nan).reindex(range(count))
    if direction.isna().all():
        direction[:] = 1.0
    else:
        direction = direction.ffill()
        direction = direction.bfill()
    directions = direction.to_numpy(dtype=float)

    window = min(7, max(3, count // 20))
    smoothed = pd.Series(directions).rolling(window=window, center=True, min_periods=1).median()
    smoothed = smoothed.ffill().bfill()
    smoothed_values = smoothed.to_numpy(dtype=float)
    smoothed_values = np.where(smoothed_values >= 0, 1.0, -1.0)

    if not np.any(smoothed_values < 0):
        smoothed_values[:] = 1.0
    elif not np.any(smoothed_values > 0):
        smoothed_values[:] = -1.0

    segments: List[Tuple[int, int, float]] = []
    start = 0
    current_dir = smoothed_values[0]
    for idx in range(1, count):
        if smoothed_values[idx] != current_dir:
            segments.append((start, idx, current_dir))
            start = idx
            current_dir = smoothed_values[idx]
    segments.append((start, count, current_dir))
    return smoothed_values, segments


def _normalise_origin_mode(mode: str | None) -> str:
    if not mode:
        return ORIGIN_MODES[0]
    normalised = str(mode).lower()
    return normalised if normalised in ORIGIN_MODES else ORIGIN_MODES[0]


def _clear_layer(layer: Any) -> None:
    remover = getattr(layer, "remove_plot", None)
    if callable(remover):
        removed = False
        try:
            count = len(layer)  # type: ignore[arg-type]
        except Exception:
            count = getattr(layer, "plot_count", None)
        if isinstance(count, int):
            for idx in range(count - 1, -1, -1):
                try:
                    remover(idx)
                    removed = True
                except Exception:
                    pass
        if not removed:
            for _ in range(8):
                try:
                    remover(0)
                    removed = True
                except Exception:
                    break
    clearer = getattr(layer, "clear", None)
    if callable(clearer):
        try:
            clearer()
        except Exception:
            pass


def _legend_label(layer: Any) -> Any | None:
    label_method = getattr(layer, "label", None)
    if not callable(label_method):
        return None
    try:
        legend = label_method("Legend")
    except Exception:
        legend = None
    if legend is None or not hasattr(legend, "text"):
        return None
    return cast(Any, legend)


def _set_graph_title(layer: Any, text: str) -> None:
    label_method = getattr(layer, "label", None)
    if not callable(label_method):
        return
    try:
        title_label = label_method("Title")
    except Exception:
        title_label = None
    if title_label is None or not hasattr(title_label, "text"):
        return
    try:
        cast(Any, title_label).text = text
    except Exception:
        pass


def _assign_long_name(target: Any | None, name: str) -> None:
    if target is None:
        return
    for attr in ("long_name", "longname", "lname"):
        if hasattr(target, attr):
            try:
                setattr(cast(Any, target), attr, name)
                return
            except Exception:
                continue


def _set_text_size(target: Any | None, size: int) -> bool:
    if target is None:
        return False
    for attr in ("font_size", "fontsize", "text_size", "height", "size", "FontSize"):
        if hasattr(target, attr):
            try:
                setattr(cast(Any, target), attr, size)
                return True
            except Exception:
                continue
    setter = getattr(target, "set_size", None)
    if callable(setter):
        try:
            setter(size)
            return True
        except Exception:
            return False
    return False


def _set_visibility(target: Any | None, visible: bool) -> bool:
    if target is None:
        return False
    for attr in ("visible", "Visible", "show", "Show"):
        if hasattr(target, attr):
            try:
                setattr(cast(Any, target), attr, bool(visible))
                return True
            except Exception:
                continue
    return False


def _apply_axis_labels(layer: Any, x_label: str, y_label: str) -> None:
    axis_method = getattr(layer, "axis", None)
    if not callable(axis_method):
        return
    show_axis = bool(globals().get("SHOW_AXIS_LABELS", True))
    axis_size = int(globals().get("AXIS_LABEL_SIZE", 18))
    for axis_name, label_text in (("x", x_label), ("y", y_label)):
        try:
            axis_obj = axis_method(axis_name)
        except Exception:
            axis_obj = None
        if axis_obj is None:
            continue
        label_obj = getattr(axis_obj, "label", None)
        text_value = label_text if show_axis else ""
        if label_obj is not None and hasattr(label_obj, "text"):
            try:
                cast(Any, label_obj).text = text_value
                _set_visibility(label_obj, show_axis)
                if show_axis:
                    _set_text_size(label_obj, axis_size)
                continue
            except Exception:
                pass
        for attr in ("title", "text"):
            if hasattr(axis_obj, attr):
                try:
                    setattr(cast(Any, axis_obj), attr, text_value)
                    break
                except Exception:
                    continue

        if not show_axis:
            for attr in ("label", "Label"):
                sub = getattr(axis_obj, attr, None)
                if sub is not None:
                    _set_visibility(sub, False)


def _apply_tick_settings(layer: Any, axis_name: str, axis_obj: Any | None) -> None:
    show_ticks = bool(globals().get("SHOW_TICK_LABELS", True))
    tick_size = int(globals().get("TICK_SIZE", 18))

    tick_obj: Any | None = None
    for attr in ("tick_labels", "tickLabels", "ticklabel", "TickLabels"):
        candidate = getattr(axis_obj, attr, None) if axis_obj is not None else None
        if candidate is not None:
            tick_obj = candidate
            break

    if tick_obj is not None:
        _set_visibility(tick_obj, show_ticks)
        if show_ticks:
            _set_text_size(tick_obj, tick_size)

    setter = getattr(axis_obj, "show", None)
    if callable(setter):
        try:
            setter(show_ticks)
        except Exception:
            pass


def _prepare_origin_workspace(
    currents: np.ndarray,
    resistances: np.ndarray,
    title: str,
    source_name: str,
) -> Tuple[Any, Any | None, Any | None, Any | None, Any | None, str]:
    import originpro as op  # lazy import

    origin_any: Any = cast(Any, op)
    try:
        origin_any.set_show()
    except Exception:
        pass

    source_stem = Path(source_name).stem or title
    legend_label = source_stem or title
    workbook_name = (source_stem or title)[:32] or "Annealing"

    workbook: Any | None
    try:
        book_obj = origin_any.new_book('w', lname=workbook_name)
        workbook = cast(Any, book_obj) if book_obj is not None else None
    except Exception:
        workbook = None

    worksheet: Any | None = None
    if workbook is not None:
        try:
            worksheet = cast(Any, workbook[0])
        except Exception:
            worksheet = None
    if worksheet is None:
        sheet_obj: Any | None
        try:
            sheet_obj = origin_any.new_sheet('w', lname='Data')
        except Exception:
            sheet_obj = None
        if sheet_obj is not None:
            worksheet = cast(Any, sheet_obj)
            try:
                workbook = getattr(worksheet, 'parent', workbook)
            except Exception:
                pass
    if worksheet is None:
        return origin_any, None, None, None, None, legend_label

    try:
        worksheet.from_list(0, currents.tolist())
        worksheet.from_list(1, resistances.tolist())
    except Exception:
        return origin_any, None, None, None, None, legend_label
    try:
        worksheet.cols_axis('XY')
    except Exception:
        pass
    try:
        worksheet.set_label(0, "Current (mA)")
        worksheet.set_label(1, "Resistance (Ω)")
    except Exception:
        pass

    graph: Any | None
    try:
        graph_obj = origin_any.new_graph(template='scatter')
        graph = cast(Any, graph_obj) if graph_obj is not None else None
    except Exception:
        graph = None
    if graph is None:
        return origin_any, workbook, worksheet, None, None, legend_label

    try:
        graph.activate()
    except Exception:
        pass

    try:
        layer = cast(Any, graph[0])
    except Exception:
        layer = None

    if layer is not None:
        _clear_layer(layer)

    return origin_any, workbook, worksheet, graph, layer, legend_label


def _hide_workbook(origin_any: Any, workbook: Any | None, graph: Any | None) -> None:
    if workbook is None:
        return

    activator = getattr(workbook, "activate", None)
    if callable(activator):
        try:
            activator()
        except Exception:
            pass

    commands = ["win -h 1;", "window -h 1;", "win -hc 1;", "window -hc 1;"]

    executors = [getattr(workbook, "lt_exec", None), getattr(origin_any, "lt_exec", None)]
    for cmd in commands:
        for executor in executors:
            if not callable(executor):
                continue
            try:
                executor(cmd)
                if graph is not None:
                    try:
                        graph.activate()
                    except Exception:
                        pass
                return
            except Exception:
                continue


def _apply_origin_readability(layer: Any, graph: Any | None) -> None:
    if layer is None:
        return
    legend = _legend_label(layer)
    show_legend = bool(globals().get("SHOW_LEGEND", True))
    legend_size = int(globals().get("LEGEND_SIZE", 18))
    if legend is not None:
        if show_legend:
            _set_visibility(legend, True)
            _set_text_size(legend, legend_size)
        else:
            try:
                legend.text = ""
            except Exception:
                pass
            _set_visibility(legend, False)

    label_method = getattr(layer, "label", None)
    title_label: Any | None = None
    if callable(label_method):
        try:
            title_label = label_method("Title")
        except Exception:
            title_label = None
    show_title = bool(globals().get("SHOW_TITLE", True))
    title_size = int(globals().get("TITLE_SIZE", 22))
    if title_label is not None:
        _set_visibility(title_label, show_title)
        if show_title:
            _set_text_size(title_label, title_size)

    for axis_name in ("x", "y"):
        axis_obj = None
        axis_method = getattr(layer, "axis", None)
        if callable(axis_method):
            try:
                axis_obj = axis_method(axis_name)
            except Exception:
                axis_obj = None
        _apply_tick_settings(layer, axis_name, axis_obj)

    if graph is not None and show_title:
        try:
            graph.activate()
        except Exception:
            pass


def _plot_origin_simple(
    workbook: Any | None,
    worksheet: Any | None,
    graph: Any | None,
    layer: Any | None,
    legend_label: str,
    display_label: str,
) -> None:
    if worksheet is None or graph is None or layer is None:
        return
    plot_obj = layer.add_plot(worksheet, coly=1, colx=0, type='y')
    if plot_obj is None:
        return
    plot_any = cast(Any, plot_obj)
    color = '#000000'
    try:
        plot_any.color = color
        plot_any.line_width = 1.5
        plot_any.symbol_shape = 2
        plot_any.symbol_size = 4
        plot_any.symbol_edge_color = color
        plot_any.symbol_fill_color = color
        plot_any.legend = display_label
    except Exception:
        try:
            plot_any.legend = display_label
        except Exception:
            pass
    dataset_index = getattr(plot_any, 'index', None)
    if not isinstance(dataset_index, int):
        dataset_index = 1
    legend = _legend_label(layer)
    if legend is not None:
        try:
            legend.text = f"\\l({dataset_index}) {display_label}"
        except Exception:
            pass
    try:
        layer.rescale()
    except Exception:
        pass
    try:
        graph.activate()
    except Exception:
        pass


def _plot_origin_experimental(
    origin_any: Any,
    workbook: Any | None,
    worksheet: Any | None,
    graph: Any | None,
    layer: Any | None,
    currents: np.ndarray,
    resistances: np.ndarray,
    legend_label: str,
    display_label: str | None = None,
) -> None:
    if graph is None or layer is None:
        return
    _, segments = _direction_profile(currents)
    if not segments:
        _plot_origin_simple(
            workbook,
            worksheet,
            graph,
            layer,
            legend_label,
            display_label or legend_label,
        )
        return

    inc_x: List[float] = []
    inc_y: List[float] = []
    dec_x: List[float] = []
    dec_y: List[float] = []

    previous_direction: float | None = None
    for start, end, direction in segments:
        if end <= start:
            previous_direction = direction
            continue
        xs = currents[start:end].tolist()
        ys = resistances[start:end].tolist()
        target_x, target_y = (inc_x, inc_y) if direction >= 0 else (dec_x, dec_y)
        if direction < 0 and previous_direction is not None and previous_direction >= 0 and start > 0:
            xs.insert(0, float(currents[start - 1]))
            ys.insert(0, float(resistances[start - 1]))
        if target_x and xs:
            target_x.append(float('nan'))
            target_y.append(float('nan'))
        target_x.extend(xs)
        target_y.extend(ys)
        previous_direction = direction

    legend_entries: List[Tuple[int, str]] = []

    def _add_direction_plot(data_x: List[float], data_y: List[float], label: str, color: str) -> None:
        if not data_x:
            return
        sheet_obj: Any | None
        try:
            sheet_obj = origin_any.new_sheet('w', lname=label.lower().replace(' ', '_'))
        except Exception:
            sheet_obj = None
        if sheet_obj is None:
            return
        sheet = cast(Any, sheet_obj)
        try:
            sheet.from_list(0, data_x)
            sheet.from_list(1, data_y)
            sheet.cols_axis('XY')
        except Exception:
            return
        plot_obj = layer.add_plot(sheet, coly=1, colx=0, type='y')
        if plot_obj is None:
            return
        plot_any = cast(Any, plot_obj)
        try:
            plot_any.color = color
            plot_any.line_width = 1.5
            plot_any.symbol_shape = 2
            plot_any.symbol_size = 4
            plot_any.symbol_edge_color = color
            plot_any.symbol_fill_color = color
            plot_any.legend = ''
        except Exception:
            try:
                plot_any.legend = ''
            except Exception:
                pass
        dataset_index = getattr(plot_any, 'index', None)
        if not isinstance(dataset_index, int):
            try:
                dataset_index = getattr(layer, 'plot_count', None)
            except Exception:
                dataset_index = None
        if not isinstance(dataset_index, int):
            try:
                dataset_index = len(layer)  # type: ignore[arg-type]
            except Exception:
                dataset_index = None
        if not isinstance(dataset_index, int):
            dataset_index = len(legend_entries) + 1
        legend_entries.append((dataset_index, label))

    _add_direction_plot(inc_x, inc_y, "Increasing current", '#d32f2f')
    _add_direction_plot(dec_x, dec_y, "Decreasing current", '#1976d2')

    legend = _legend_label(layer)
    if legend is not None and legend_entries:
        lines = []
        if display_label:
            lines.append(display_label)
        lines.extend(f"\\l({idx}) {text}" for idx, text in legend_entries if text)
        try:
            legend.text = "\n".join(lines)
        except Exception:
            pass

    try:
        layer.rescale()
    except Exception:
        pass
    try:
        graph.activate()
    except Exception:
        pass
def plot_one(
    df: pd.DataFrame,
    title: str,
    *,
    figsize: Tuple[float, float] | None = None,
    target_px: Tuple[int, int] | None = None,
) -> Tuple[Figure, str]:
    if target_px is not None:
        target_width, target_height = target_px
        dpi = 180.0
        width = max(float(target_width) / dpi, 0.5)
        height = max(float(target_height) / dpi, 0.5)
    else:
        if not figsize:
            figsize = (4.0, 2.25)
        width = max(float(figsize[0]), 0.5)
        height = max(float(figsize[1]), 0.5)
        dpi = 144.0
    fig, ax = plt.subplots(figsize=(width, height), dpi=dpi)

    currents = df["I_mA"].to_numpy(dtype=float)
    resistances = df["R_Ohm"].to_numpy(dtype=float)
    _, segments = _direction_profile(currents)
    marker_size = 4.0
    line_width = 1.6

    legend_handles: list[Line2D] = []
    legend_kinds: set[str] = set()
    if currents.size == 0:
        pass
    elif currents.size == 1:
        line = ax.plot(
            currents,
            resistances,
            marker="o",
            linestyle="None",
            color="r",
            markersize=marker_size,
        )[0]
        line.set_label("Sample")
    else:
        previous_direction: float | None = None
        inc_count = 0
        dec_count = 0
        for start, end, direction in segments:
            color = "r" if direction >= 0 else "b"
            if end <= start:
                previous_direction = direction
                continue
            segment_currents = currents[start:end]
            segment_resistances = resistances[start:end]
            if (
                direction < 0
                and previous_direction is not None
                and previous_direction >= 0
                and start > 0
            ):
                segment_currents = np.concatenate(
                    ([currents[start - 1]], segment_currents)
                )
                segment_resistances = np.concatenate(
                    ([resistances[start - 1]], segment_resistances)
                )
            if direction >= 0:
                inc_count += 1
                label = f"Increasing {inc_count}"
                legend_key = "increasing"
            else:
                dec_count += 1
                label = f"Decreasing {dec_count}"
                legend_key = "decreasing"
            line = ax.plot(
                segment_currents,
                segment_resistances,
                color=color,
                marker="o",
                linestyle="-",
                markersize=marker_size,
                markerfacecolor=color,
                markeredgecolor=color,
                linewidth=line_width,
                label=label,
            )[0]
            if legend_key not in legend_kinds:
                legend_handles.append(
                    Line2D(
                        [],
                        [],
                        color=color,
                        marker="o",
                        linestyle="-",
                        markersize=marker_size,
                        linewidth=line_width,
                        label="Increasing current"
                        if legend_key == "increasing"
                        else "Decreasing current",
                    )
                )
                legend_kinds.add(legend_key)
            previous_direction = direction

    ax.set_xlabel("Current (mA)")
    ax.set_ylabel("Resistance (Ω)")
    ax.set_title(title)
    ax.grid(True, ls="--", alpha=0.3)
    if legend_handles:
        ax.legend(handles=legend_handles, loc="best")
    fig.tight_layout()
    cfg = dict(globals())
    apply_readability(ax, cfg)
    fname = title.replace(os.sep, "_")
    return fig, fname


def plot_one_origin(
    df: pd.DataFrame,
    title: str,
    source_name: str,
    mode: str | None = None,
    *,
    return_handles: bool = False,
) -> dict[str, object] | None:
    currents = df["I_mA"].to_numpy(dtype=float)
    resistances = df["R_Ohm"].to_numpy(dtype=float)
    origin_any, workbook, worksheet, graph, layer, legend_label = _prepare_origin_workspace(
        currents,
        resistances,
        title,
        source_name,
    )
    handles: dict[str, object] = {
        "origin": origin_any,
        "workbook": workbook,
        "worksheet": worksheet,
        "graph": graph,
        "layer": layer,
        "legend_label": legend_label,
    }
    if graph is None or layer is None:
        return handles if return_handles else None

    display_label = _format_origin_annotation(legend_label)
    _apply_axis_labels(layer, "Current (mA)", "Resistance (Ω)")
    _set_graph_title(layer, display_label)
    _assign_long_name(graph, legend_label)
    _assign_long_name(workbook, legend_label)

    resolved_mode = _normalise_origin_mode(mode if mode is not None else ORIGIN_MODE)
    if resolved_mode == "simple":
        _plot_origin_simple(workbook, worksheet, graph, layer, legend_label, display_label)
        _hide_workbook(origin_any, workbook, graph)
    else:
        _plot_origin_experimental(
            origin_any,
            workbook,
            worksheet,
            graph,
            layer,
            currents,
            resistances,
            legend_label,
            display_label,
        )

    _apply_origin_readability(layer, graph)

    if return_handles:
        handles["graph"] = graph
        handles["layer"] = layer
        handles["workbook"] = workbook
        handles["worksheet"] = worksheet
        handles["legend_label"] = legend_label
        return handles

    return None


def main(files: List[str], backend: str = BACKEND) -> None:
    if IMPROVE_READABILITY:
        apply_readability_fonts()
    use_matplotlib = wants_matplotlib(backend)
    use_origin = wants_origin(backend)
    origin_mode = _normalise_origin_mode(ORIGIN_MODE)
    keep_open = bool(use_matplotlib and SHOW_PLOTS)
    prev_interactive = plt.isinteractive()
    if use_matplotlib and not SHOW_PLOTS:
        plt.ioff()

    open_figures: List[Figure] = []
    failures: List[Tuple[str, str]] = []
    successes = 0
    output_dir: Path | None = None

    try:
        for path in files:
            try:
                df = load_file(path)
            except Exception as exc:
                failures.append((path, f"load: {exc}"))
                print(f"ERROR: Failed to read {Path(path).name}: {exc}")
                continue

            title = format_annealing_title(Path(path).stem)
            success = True
            fig: Figure | None = None
            fname: str = ""

            if use_matplotlib:
                try:
                    fig, fname = plot_one(df, title)
                    if SAVE_PLOTS:
                        if output_dir is None:
                            output_dir = Path(OUTPUT_DIR)
                            output_dir.mkdir(parents=True, exist_ok=True)
                        save_figure(fig, output_dir / fname, SAVE_FORMAT, PNG_DPI)
                    if keep_open:
                        open_figures.append(fig)
                    else:
                        plt.close(fig)
                except Exception as exc:
                    failures.append((path, f"matplotlib: {exc}"))
                    print(
                        f"ERROR: Matplotlib plot failed for {Path(path).name}: {exc}"
                    )
                    success = False
                    if fig is not None:
                        plt.close(fig)

            if use_origin:
                try:
                    plot_one_origin(df, title, Path(path).name, mode=origin_mode)
                except Exception as e:
                    print(f"Origin plot failed for {title}: {e}")

            if success:
                successes += 1
    finally:
        if use_origin:
            schedule_origin_release()

    if use_matplotlib:
        if keep_open and open_figures:
            show_plots()
        elif not keep_open:
            plt.close("all")

    if use_matplotlib and not SHOW_PLOTS and prev_interactive:
        plt.ion()

    total = successes + len(failures)
    if total:
        print(f"Summary: processed {successes} of {total} file(s).")
        if failures:
            for path, reason in failures:
                print(f"  Skipped {Path(path).name}: {reason}")
    else:
        print("No files supplied for plotting.")
