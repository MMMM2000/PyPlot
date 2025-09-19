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


def _lt_color_expr(hex_colour: str) -> str:
    """Return a LabTalk colour literal for ``hex_colour``."""

    colour = hex_colour.lstrip("#")
    if len(colour) != 6:
        return "color(0,0,0)"
    try:
        r = int(colour[0:2], 16)
        g = int(colour[2:4], 16)
        b = int(colour[4:6], 16)
    except ValueError:
        return "color(0,0,0)"
    return f"color({r},{g},{b})"


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
    directions, _ = _direction_profile(currents)
    inc_vals, dec_vals = _split_directional_values(resistances, directions)

    worksheet.from_list(0, currents.tolist())
    worksheet.from_list(1, resistances.tolist())
    worksheet.from_list(2, inc_vals.tolist())
    worksheet.from_list(3, dec_vals.tolist())
    worksheet.cols_axis('XYYY')
    try:
        worksheet.set_label(0, "Current (mA)")
        worksheet.set_label(1, "Resistance (Ohm)")
        worksheet.set_label(2, "Increasing")
        worksheet.set_label(3, "Decreasing")
    except Exception:
        pass

    try:
        worksheet.activate()
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

    book_hint = source_stem or workbook_name or "CA"
    book_short, sheet_short = _resolve_origin_names(origin_any, workbook, worksheet, book_hint)

    has_inc = bool(np.isfinite(inc_vals).any())
    has_dec = bool(np.isfinite(dec_vals).any())
    if not has_inc and not has_dec:
        return

    base_ref = f"[{book_short}]{sheet_short}!"
    inc_range = "__pyCA_inc"
    dec_range = "__pyCA_dec"

    try:
        worksheet.activate()
    except Exception:
        pass

    if has_inc:
        try:
            origin_any.lt_exec(f"range {inc_range} = {base_ref}(1,3);")
        except Exception:
            has_inc = False
    if has_dec:
        try:
            origin_any.lt_exec(f"range {dec_range} = {base_ref}(1,4);")
        except Exception:
            has_dec = False

    plots: List[Tuple[str, int]] = []
    graph_created = False
    if has_inc:
        try:
            origin_any.lt_exec(f"plotxy iy:={inc_range} plot:=201;")
            graph_created = True
            plots.append(("inc", 1))
        except Exception:
            has_inc = False
    if has_dec:
        if graph_created:
            try:
                origin_any.lt_exec(f"layer -i {dec_range};")
                plots.append(("dec", len(plots) + 1))
            except Exception:
                pass
        else:
            try:
                origin_any.lt_exec(f"plotxy iy:={dec_range} plot:=201;")
                graph_created = True
                plots.append(("dec", 1))
            except Exception:
                pass

    if not graph_created:
        return

    inc_colour = _lt_color_expr(ORIGIN_INCREASING_COLOUR)
    dec_colour = _lt_color_expr(ORIGIN_DECREASING_COLOUR)

    for role, idx in plots:
        colour = inc_colour if role == "inc" else dec_colour
        for cmd in (
            f"set p{idx} -k 1;",
            f"set p{idx} -w 2;",
            f"set p{idx} -z 4;",
            f"set p{idx} -c {colour};",
            f"set p{idx} -cf {colour};",
            f"set p{idx} -cl {colour};",
        ):
            try:
                origin_any.lt_exec(cmd)
            except Exception:
                pass

    legend_lines: List[str] = []
    for role, idx in plots:
        label = "Increasing" if role == "inc" else "Decreasing"
        legend_lines.append(rf"\L({idx}) {label}")
    if legend_lines:
        legend_text = "\\n".join(legend_lines)
        try:
            origin_any.lt_exec('legend.update=0;')
        except Exception:
            pass
        try:
            origin_any.lt_exec(f'legend.text$ = "{legend_text}";')
        except Exception:
            pass

    esc_title = title.replace('"', "'")
    esc_graph = source_stem.replace('"', "'") if source_stem else esc_title
    axis_cmds = [
        'page.antialias=1;',
        'layer.x.showAxes=1;',
        'layer.y.showAxes=1;',
        'layer.x.opposite=0;',
        'layer.y.opposite=0;',
        'layer.x.gridMajor=1;',
        'layer.y.gridMajor=1;',
        'lab -xb "Current (mA)";',
        'lab -yl "Resistance (Ohm)";',
        f'title -s "{esc_title}";',
        f'page.longname$ = "{esc_graph}";',
    ]
    for cmd in axis_cmds:
        try:
            origin_any.lt_exec(cmd)
        except Exception:
            pass

    try:
        origin_any.lt_exec('layer -a;')
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

