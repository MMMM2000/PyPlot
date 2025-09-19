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
    release_origin,
    save_figure,
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

ORIGIN_INCREASING_COLOUR = "#d62728"
ORIGIN_DECREASING_COLOUR = "#1f77b4"


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
    direction = pd.Series(np.sign(deltas), index=range(1, count))
    direction.replace(0.0, np.nan, inplace=True)
    direction = direction.reindex(range(count))
    if direction.isna().all():
        direction.fillna(1.0, inplace=True)
    else:
        direction.ffill(inplace=True)
        direction.bfill(inplace=True)
    directions = direction.to_numpy(dtype=float)

    segments: List[Tuple[int, int, float]] = []
    start = 0
    current_dir = directions[0]
    for idx in range(1, count):
        if directions[idx] != current_dir:
            segments.append((start, idx, current_dir))
            start = idx
            current_dir = directions[idx]
    segments.append((start, count, current_dir))
    return directions, segments


def _split_directional_values(
    values: np.ndarray, directions: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """Return arrays for increasing and decreasing segments with NaNs elsewhere."""

    if values.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    inc = np.full(values.shape, np.nan, dtype=float)
    dec = np.full(values.shape, np.nan, dtype=float)
    mask_inc = directions >= 0
    mask_dec = directions < 0
    inc[mask_inc] = values[mask_inc]
    dec[mask_dec] = values[mask_dec]
    return inc, dec


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
    workbook_name = source_stem[:30] if source_stem else title[:30]

    book_obj: Any | None
    try:
        book_obj = origin_any.new_book('w', lname=workbook_name)
    except Exception:
        book_obj = None

    w: Any | None = None
    if book_obj is not None:
        book = cast(Any, book_obj)
        try:
            book.activate()
        except Exception:
            pass
        try:
            w = book[0]
        except Exception:
            w = None
    if w is None:
        w_sheet: Any | None = origin_any.new_sheet('w', lname=workbook_name)
        if w_sheet is None:
            return
        w = cast(Any, w_sheet)
    try:
        w.activate()
    except Exception:
        pass

    currents = df["I_mA"].to_numpy(dtype=float)
    resistances = df["R_Ohm"].to_numpy(dtype=float)
    directions, _ = _direction_profile(currents)
    inc_vals, dec_vals = _split_directional_values(resistances, directions)

    w.from_list(0, currents.tolist())
    w.from_list(1, resistances.tolist())
    w.from_list(2, inc_vals.tolist())
    w.from_list(3, dec_vals.tolist())
    w.cols_axis('XYYY')
    try:
        w.set_label(0, "Current (mA)")
        w.set_label(1, "Resistance (Ohm)")
        w.set_label(2, "Increasing")
        w.set_label(3, "Decreasing")
    except Exception:
        pass

    try:
        w.activate()
        origin_any.lt_exec(
            'wks.col1.lname$ = "Current (mA)";'
            'wks.col2.lname$ = "Resistance";'
            'wks.col3.lname$ = "Increasing";'
            'wks.col4.lname$ = "Decreasing";'
        )
    except Exception:
        pass
    try:
        esc_book = (source_stem or title).replace('"', "'")
        origin_any.lt_exec(f'page.longname$ = "{esc_book}";')
    except Exception:
        pass

    gp_obj: Any = origin_any.new_graph(template='scatter')
    if gp_obj is None:
        return
    gp: Any = gp_obj
    try:
        gp.activate()
    except Exception:
        pass
    try:
        gl: Any = gp[0]
    except Exception:
        return

    try:
        gl.activate()
    except Exception:
        pass

    try:
        origin_any.lt_exec('layer -d;')
    except Exception:
        pass

    plot_inc: Any = None
    plot_dec: Any = None
    legend_entries: List[Tuple[int, str]] = []
    plot_index = 1
    if np.isfinite(inc_vals).any():
        plot_inc = gl.add_plot(w, coly=2, colx=0, type='y')
    if np.isfinite(dec_vals).any():
        plot_dec = gl.add_plot(w, coly=3, colx=0, type='y')

    try:
        plotted_any = False
        if plot_inc is not None:
            p_inc = cast(Any, plot_inc)
            try:
                p_inc.symbol_shape = 2
                p_inc.symbol_size = 4
                p_inc.line_connect = 1
                try:
                    p_inc.color = ORIGIN_INCREASING_COLOUR
                except Exception:
                    pass
                try:
                    p_inc.symbol_edge_color = ORIGIN_INCREASING_COLOUR
                    p_inc.symbol_fill_color = ORIGIN_INCREASING_COLOUR
                except Exception:
                    pass
                try:
                    p_inc.legend = 'Increasing'
                except Exception:
                    pass
            except Exception:
                pass
            plotted_any = True
            idx_val = None
            for attr in ('index', 'plot_index', 'lt_index'):
                attr_val = getattr(p_inc, attr, None)
                if isinstance(attr_val, int) and attr_val >= 1:
                    idx_val = attr_val
                    break
            if idx_val is None:
                idx_val = plot_index
                plot_index += 1
            else:
                plot_index = max(plot_index, idx_val + 1)
            legend_entries.append((idx_val, 'Increasing'))
        if plot_dec is not None:
            p_dec = cast(Any, plot_dec)
            try:
                p_dec.symbol_shape = 2
                p_dec.symbol_size = 4
                p_dec.line_connect = 1
                try:
                    p_dec.color = ORIGIN_DECREASING_COLOUR
                except Exception:
                    pass
                try:
                    p_dec.symbol_edge_color = ORIGIN_DECREASING_COLOUR
                    p_dec.symbol_fill_color = ORIGIN_DECREASING_COLOUR
                except Exception:
                    pass
                try:
                    p_dec.legend = 'Decreasing'
                except Exception:
                    pass
            except Exception:
                pass
            plotted_any = True
            idx_val = None
            for attr in ('index', 'plot_index', 'lt_index'):
                attr_val = getattr(p_dec, attr, None)
                if isinstance(attr_val, int) and attr_val >= 1:
                    idx_val = attr_val
                    break
            if idx_val is None:
                idx_val = plot_index
                plot_index += 1
            else:
                plot_index = max(plot_index, idx_val + 1)
            legend_entries.append((idx_val, 'Decreasing'))
        if plotted_any:
            gl.rescale()
    except Exception:
        try:
            origin_any.lt_exec('layer -a;')
        except Exception:
            pass

    try:
        gp.activate()
    except Exception:
        pass

    esc = title.replace('"', "'")
    esc_long = source_stem.replace('"', "'")

    try:
        gl.set_int('title.show', 1)
    except Exception:
        pass
    try:
        gl.set_str('title.text$', esc)
    except Exception:
        pass

    title_label: Any | None = None
    try:
        title_label = gl.label('Title')
    except Exception:
        title_label = None
    if title_label is not None:
        try:
            title_label.text = title
        except Exception:
            pass
    else:
        try:
            gl.remove_label('py_title')
        except Exception:
            pass
        try:
            manual_title = gl.add_label(title, 0.5, 1.03)
        except Exception:
            manual_title = None
        if manual_title is not None:
            try:
                manual_title.name = 'py_title'
                manual_title.set_int('attach', 0)
                manual_title.set_int('horzalign', 1)
                manual_title.set_int('vertalign', 0)
            except Exception:
                pass

    try:
        axis_cmds = [
            'page.antialias=1;',
            'lab -xb "Current (mA)";',
            'lab -yl "Resistance (Ohm)";',
            'layer.x.gridMajor=1;',
            'layer.y.gridMajor=1;',
        ]
        for cmd in axis_cmds:
            try:
                origin_any.lt_exec(cmd)
            except Exception:
                pass
        if esc_long:
            origin_any.lt_exec(f'page.longname$ = "{esc_long}";')
        else:
            origin_any.lt_exec(f'page.longname$ = "{esc}";')
        if legend_entries:
            try:
                gl.set_int('legend.update', 0)
            except Exception:
                pass
            try:
                legend_obj = gl.label('Legend')
            except Exception:
                legend_obj = None
            legend_lines = [
                f'\\L({idx}) {text}' for idx, text in sorted(legend_entries, key=lambda item: item[0])
            ]
            legend_text = "\n".join(legend_lines)
            if legend_obj is not None:
                try:
                    legend_obj.text = legend_text
                except Exception:
                    try:
                        esc_legend = legend_text.replace('"', "'")
                        origin_any.lt_exec(f'legend.text$ = "{esc_legend}";')
                    except Exception:
                        pass
                try:
                    legend_obj.set_float('x1', 0.78)
                    legend_obj.set_float('y1', 0.85)
                except Exception:
                    pass
            else:
                try:
                    esc_legend = legend_text.replace('"', "'")
                    origin_any.lt_exec('legend;')
                    origin_any.lt_exec(f'legend.text$ = "{esc_legend}";')
                except Exception:
                    pass
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
            release_origin()

    if wants_matplotlib(backend):
        if SHOW_PLOTS:
            show_plots()
        else:
            plt.close('all')
        if SAVE_PLOTS and outs:
            Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
            for fig, fname in outs:
                save_figure(fig, Path(OUTPUT_DIR) / fname, SAVE_FORMAT, PNG_DPI)

