from __future__ import annotations

import os
import re
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


_LT_NAME_CLEANER = re.compile(r"[^A-Za-z0-9_]")


def _origin_short_name(obj: Any) -> str:
    """Return the Origin short name for ``obj`` when available."""

    for attr in ("GetName", "ShortName", "Name", "name"):
        try:
            value = getattr(obj, attr)
        except Exception:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _sanitize_lt_name(base: str, prefix: str) -> str:
    """Return an Origin-safe short name derived from ``base``."""

    cleaned = _LT_NAME_CLEANER.sub("", base or "")
    if not cleaned:
        cleaned = prefix
    if cleaned[0].isdigit():
        cleaned = f"{prefix}{cleaned}"
    if len(cleaned) > 13:
        cleaned = f"{cleaned[:10]}{abs(hash(cleaned)) % 1000:03d}"
    return cleaned[:13]


def _resolve_origin_names(
    origin_any: Any, workbook: Any | None, sheet: Any, hint: str
) -> Tuple[str, str]:
    """Ensure workbook and sheet short names exist and return them."""

    book_short = ""
    if workbook is not None:
        book_short = _origin_short_name(workbook)
    if not book_short:
        desired = _sanitize_lt_name(hint, "CA")
        try:
            if workbook is not None and hasattr(workbook, "activate"):
                workbook.activate()
        except Exception:
            pass
        try:
            origin_any.lt_exec(f'page.name$ = "{desired}";')
        except Exception:
            pass
        if workbook is not None:
            book_short = _origin_short_name(workbook)
        if not book_short:
            book_short = desired

    sheet_short = _origin_short_name(sheet)
    if not sheet_short:
        desired = _sanitize_lt_name("Sheet1", "S")
        try:
            if hasattr(sheet, "activate"):
                sheet.activate()
        except Exception:
            pass
        try:
            origin_any.lt_exec(f'wks.name$ = "{desired}";')
        except Exception:
            pass
        sheet_short = _origin_short_name(sheet) or desired

    return book_short, sheet_short


def _safe_assign(obj: Any, attr: str, value: Any) -> None:
    try:
        setattr(obj, attr, value)
    except Exception:
        pass


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
    workbook_name = source_stem[:30] if source_stem else title[:30]

    book_obj: Any | None
    try:
        book_obj = origin_any.new_book('w', lname=workbook_name)
    except Exception:
        book_obj = None

    workbook: Any | None = None
    worksheet: Any | None = None
    if book_obj is not None:
        workbook = cast(Any, book_obj)
        try:
            workbook.activate()
        except Exception:
            pass
        try:
            worksheet = workbook[0]
        except Exception:
            worksheet = None
    if worksheet is None:
        w_sheet: Any | None = origin_any.new_sheet('w', lname=workbook_name)
        if w_sheet is None:
            return
        worksheet = cast(Any, w_sheet)
        try:
            workbook = getattr(worksheet, 'parent', None)
        except Exception:
            workbook = None

    if worksheet is None:
        return

    try:
        worksheet.activate()
    except Exception:
        pass

    currents = df["I_mA"].to_numpy(dtype=float)
    resistances = df["R_Ohm"].to_numpy(dtype=float)

    worksheet.from_list(0, currents.tolist())
    worksheet.from_list(1, resistances.tolist())
    try:
        worksheet.cols_axis('XY')
    except Exception:
        pass
    try:
        worksheet.set_label(0, "Current (mA)")
        worksheet.set_label(1, "Resistance (Ohm)")
    except Exception:
        pass

    try:
        worksheet.activate()
    except Exception:
        pass

    legend_label = source_stem or title
    esc_legend = legend_label.replace('"', "'")

    try:
        origin_any.lt_exec(
            'wks.col1.lname$ = "Current (mA)";',
            'wks.col1.unit$ = "mA";',
            'wks.col2.lname$ = "Resistance";',
            'wks.col2.unit$ = "Ohm";',
            f'wks.col2.comment$ = "{esc_legend}";',
        )
    except Exception:
        pass

    book_hint = source_stem or workbook_name or "CA"
    _resolve_origin_names(origin_any, workbook, worksheet, book_hint)

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
        layer = None
    if layer is None:
        return

    try:
        plot_obj = layer.add_plot(worksheet, coly=1, colx=0, type='y')
    except Exception:
        plot_obj = None
    if plot_obj is None:
        return

    plot = cast(Any, plot_obj)

    try:
        origin_any.lt_exec('layer -i 1;', 'set %C -d 202;', 'set %C -z 4;')
    except Exception:
        pass

    _safe_assign(plot, "symbol_shape", 2)
    _safe_assign(plot, "symbol_size", 6)
    _safe_assign(plot, "line_width", 2)
    _safe_assign(plot, "legend", legend_label)

    try:
        layer.rescale()
    except Exception:
        pass

    try:
        title_label = layer.label('Title')
    except Exception:
        title_label = None
    if title_label is not None and hasattr(title_label, 'text'):
        try:
            cast(Any, title_label).text = title
        except Exception:
            pass

    try:
        x_axis = layer.axis('x')
    except Exception:
        x_axis = None
    try:
        y_axis = layer.axis('y')
    except Exception:
        y_axis = None

    if x_axis is not None:
        try:
            x_axis.title = "Current (mA)"
        except Exception:
            pass
    if y_axis is not None:
        try:
            y_axis.title = "Resistance (Ohm)"
        except Exception:
            pass

    esc_graph = (source_stem or title).replace('"', "'")
    try:
        origin_any.lt_exec(
            f'legend -s 0 "{esc_legend}";',
            f'page.longname$ = "{esc_graph}";',
            'layer.x.showAxes=3;',
            'layer.y.showAxes=3;',
        )
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

