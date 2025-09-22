from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Tuple, cast

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from ..utils import (
    apply_readability,
    apply_readability_fonts,
    format_annealing_title,
    save_figure,
    schedule_origin_release,
    show_plots,
)
from ..backends import wants_matplotlib, wants_origin

# Defaults
OUTPUT_DIR = os.getcwd()
SHOW_PLOTS = True
SAVE_PLOTS = False
SAVE_FORMAT = "png"
PNG_DPI = 1200
BACKEND = "matplotlib"
IMPROVE_READABILITY = True
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


def _lt_literal(text: str) -> str:
    """Return a LabTalk-safe string literal."""

    escaped = text.replace("\\", "\\\\").replace('"', "\"")
    return f'"{escaped}"'


def load_file(path: str) -> pd.DataFrame:
    """Load current annealing tri-column file: I(A) V(V) R(Ohm).

    Returns a DataFrame with I_mA and R_Ohm columns.
    """
    df = pd.read_csv(path, sep=None, engine="python", header=None, comment="#")
    if df.shape[1] < 3:
        raise ValueError(f"{path}: expected at least 3 columns (I, V, R)")
    df = df.iloc[:, :3]
    df.columns = ["I_A", "V_V", "R_Ohm"]
    df["I_A"] = df["I_A"].astype(float)
    df["I_mA"] = df["I_A"] * 1e3
    df["R_Ohm"] = df["R_Ohm"].astype(float)
    df = df[df["I_mA"] != 0].reset_index(drop=True)
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


def plot_one(df: pd.DataFrame, title: str) -> Tuple[Figure, str]:
    fig, ax = plt.subplots(figsize=(8, 4.5))

    currents = df["I_mA"].to_numpy(dtype=float)
    resistances = df["R_Ohm"].to_numpy(dtype=float)
    _, segments = _direction_profile(currents)

    if currents.size == 0:
        pass
    elif currents.size == 1:
        ax.plot(currents, resistances, marker="o", linestyle="None", color="r", markersize=3)
    else:
        for start, end, direction in segments:
            color = "r" if direction >= 0 else "b"
            ax.plot(
                currents[start:end],
                resistances[start:end],
                color=color,
                marker="o",
                linestyle="-",
                markersize=3,
                markerfacecolor=color,
                markeredgecolor=color,
                linewidth=1.5,
            )

    ax.set_xlabel("Current (mA)")
    ax.set_ylabel("Resistance (Ohm)")
    ax.set_title(title)
    ax.grid(True, ls="--", alpha=0.3)
    fig.tight_layout()
    apply_readability(ax, globals())
    fname = title.replace(os.sep, "_")
    return fig, fname


def plot_one_origin(df: pd.DataFrame, title: str, source_name: str) -> None:
    import originpro as op  # lazy import

    origin_any: Any = cast(Any, op)
    try:
        origin_any.set_show()
    except Exception:
        pass

    source_stem = Path(source_name).stem or title
    workbook_name = source_stem[:32] if source_stem else title[:32]
    legend_label = source_stem or title
    legend_literal = _lt_literal(legend_label)

    book_obj: Any | None
    try:
        book_obj = origin_any.new_book('w', lname=workbook_name)
    except Exception:
        book_obj = None

    workbook: Any | None = cast(Any, book_obj) if book_obj is not None else None
    worksheet: Any | None = None
    if workbook is not None:
        try:
            workbook.activate()
        except Exception:
            pass
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
        if sheet_obj is None:
            return
        worksheet = cast(Any, sheet_obj)
        try:
            workbook = getattr(worksheet, 'parent', None)
        except Exception:
            workbook = None

    if worksheet is None:
        return

    currents = df["I_mA"].to_numpy(dtype=float)
    resistances = df["R_Ohm"].to_numpy(dtype=float)

    try:
        worksheet.from_list(0, currents.tolist())
        worksheet.from_list(1, resistances.tolist())
    except Exception:
        return

    try:
        worksheet.cols_axis('XY')
    except Exception:
        pass
    try:
        worksheet.set_label(0, "Current (mA)")
        worksheet.set_label(1, "Resistance (Ohm)")
    except Exception:
        pass

    _, segments = _direction_profile(currents)
    inc_x: List[float] = []
    inc_y: List[float] = []
    dec_x: List[float] = []
    dec_y: List[float] = []

    for start, end, direction in segments:
        if end <= start:
            continue
        xs = currents[start:end].tolist()
        ys = resistances[start:end].tolist()
        if direction >= 0:
            target_x, target_y = inc_x, inc_y
        else:
            target_x, target_y = dec_x, dec_y
        if target_x:
            target_x.append(float('nan'))
            target_y.append(float('nan'))
        target_x.extend(xs)
        target_y.extend(ys)

    graph_obj: Any | None
    try:
        graph_obj = origin_any.new_graph(template='scatter')
    except Exception:
        graph_obj = None
    if graph_obj is None:
        return

    graph = cast(Any, graph_obj)
    try:
        graph.activate()
    except Exception:
        pass

    try:
        layer = cast(Any, graph[0])
    except Exception:
        return

    try:
        origin_any.lt_exec('layer -c;')
    except Exception:
        pass

    if workbook is not None:
        try:
            workbook.activate()
        except Exception:
            pass

    inc_plot: Any | None = None
    if inc_x:
        inc_sheet_obj: Any | None
        try:
            inc_sheet_obj = origin_any.new_sheet('w', lname='increasing')
        except Exception:
            inc_sheet_obj = None
        if inc_sheet_obj is not None:
            inc_sheet = cast(Any, inc_sheet_obj)
            try:
                inc_sheet.from_list(0, inc_x)
                inc_sheet.from_list(1, inc_y)
            except Exception:
                inc_sheet = None
            if inc_sheet is not None:
                try:
                    inc_sheet.cols_axis('XY')
                except Exception:
                    pass
                inc_plot = cast(Any, layer.add_plot(inc_sheet, coly=1, colx=0, type='y'))
                if inc_plot is not None:
                    try:
                        inc_plot.color = '#ff0000'
                        inc_plot.line_width = 1.5
                        inc_plot.symbol_shape = 2
                        inc_plot.symbol_size = 4
                        inc_plot.symbol_edge_color = '#ff0000'
                        inc_plot.symbol_fill_color = '#ff0000'
                        inc_plot.legend = 'Increasing current'
                    except Exception:
                        pass

    if workbook is not None:
        try:
            workbook.activate()
        except Exception:
            pass

    dec_plot: Any | None = None
    if dec_x:
        dec_sheet_obj: Any | None
        try:
            dec_sheet_obj = origin_any.new_sheet('w', lname='decreasing')
        except Exception:
            dec_sheet_obj = None
        if dec_sheet_obj is not None:
            dec_sheet = cast(Any, dec_sheet_obj)
            try:
                dec_sheet.from_list(0, dec_x)
                dec_sheet.from_list(1, dec_y)
            except Exception:
                dec_sheet = None
            if dec_sheet is not None:
                try:
                    dec_sheet.cols_axis('XY')
                except Exception:
                    pass
                dec_plot = cast(Any, layer.add_plot(dec_sheet, coly=1, colx=0, type='y'))
                if dec_plot is not None:
                    try:
                        dec_plot.color = '#0000ff'
                        dec_plot.line_width = 1.5
                        dec_plot.symbol_shape = 2
                        dec_plot.symbol_size = 4
                        dec_plot.symbol_edge_color = '#0000ff'
                        dec_plot.symbol_fill_color = '#0000ff'
                        dec_plot.legend = 'Decreasing current'
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

    try:
        origin_any.lt_exec('legendupdate;')
    except Exception:
        pass

    if inc_plot is not None:
        try:
            origin_any.lt_exec(f'legend -s 1 {_lt_literal("Increasing current")};')
        except Exception:
            pass
    if dec_plot is not None:
        try:
            origin_any.lt_exec(f'legend -s 2 {_lt_literal("Decreasing current")};')
        except Exception:
            pass

    try:
        origin_any.lt_exec('lab -xb "Current (mA)"; lab -yl "Resistance (Ohm)";')
    except Exception:
        pass

    try:
        origin_any.lt_exec(f'title -s {legend_literal};')
    except Exception:
        pass

    if workbook is not None:
        try:
            workbook.activate()
            origin_any.lt_exec(f'page.longname$ = {legend_literal};')
        except Exception:
            pass

    try:
        graph.activate()
        origin_any.lt_exec(f'page.longname$ = {legend_literal};')
    except Exception:
        pass


def main(files: List[str], backend: str = BACKEND) -> None:
    if IMPROVE_READABILITY:
        apply_readability_fonts()
    outs: List[Tuple[Figure, str]] = []
    use_origin = wants_origin(backend)
    try:
        for path in files:
            df = load_file(path)
            title = format_annealing_title(Path(path).stem)
            if wants_matplotlib(backend):
                fig, fname = plot_one(df, title)
                outs.append((fig, fname))
            if use_origin:
                try:
                    plot_one_origin(df, title, Path(path).name)
                except Exception as e:
                    print(f"Origin plot failed for {title}: {e}")
    finally:
        if use_origin:
            schedule_origin_release()

    if wants_matplotlib(backend):
        if SHOW_PLOTS:
            show_plots()
        else:
            plt.close('all')
        if SAVE_PLOTS and outs:
            Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
            for fig, fname in outs:
                save_figure(fig, Path(OUTPUT_DIR) / fname, SAVE_FORMAT, PNG_DPI)

