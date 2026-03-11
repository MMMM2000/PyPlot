import logging
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple, cast

from PyQt6 import QtWidgets

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.collections import PathCollection
from matplotlib.colors import to_hex
from matplotlib.typing import ColorType
from matplotlib.figure import Figure
from matplotlib.axes import Axes

from plotting.shared.config import load_config
from plotting.shared.common import maybe_handle_outliers
from plotting.shared.utils import save_figure, show_plots
from plotting.shared.origin import (
    hide_origin_workbook,
    schedule_origin_release,
    set_origin_axis_title,
    set_origin_graph_title,
)
from plotting.shared.readability import apply_readability_fonts, apply_readability
from plotting.shared.backends import wants_matplotlib, wants_origin
from tqdm import tqdm

_CFG = load_config().get("stress_sensitivity", {})
OUTPUT_DIR = _CFG.get("OUTPUT_DIR", os.getcwd())
PLOT_SUM = bool(_CFG.get("PLOT_SUM", True))
PLOT_DT = bool(_CFG.get("PLOT_DT", True))
PLOT_T1 = bool(_CFG.get("PLOT_T1", True))
PLOT_T2 = bool(_CFG.get("PLOT_T2", True))
PLOT_VARS = [v for v, b in [("sum", PLOT_SUM), ("dT", PLOT_DT), ("T1", PLOT_T1), ("T2", PLOT_T2)] if b]

INCLUDE_DEPENDENCE = bool(_CFG.get("INCLUDE_DEPENDENCE", False))
MED_WINDOW = int(_CFG.get("MED_WINDOW", 5))
MA_WINDOW = int(_CFG.get("MA_WINDOW", 20))
SHOW_PLOTS = bool(_CFG.get("SHOW_PLOTS", True))
SAVE_PLOTS = bool(_CFG.get("SAVE_PLOTS", False))
SAVE_FORMAT = _CFG.get("SAVE_FORMAT", "png")
PNG_DPI = int(_CFG.get("PNG_DPI", 1200))
MAX_SHOW = 8
BACKEND = str(_CFG.get("BACKEND", "matplotlib"))
IMPROVE_READABILITY = False
SHOW_LEGEND = bool(_CFG.get("SHOW_LEGEND", True))
LEGEND_SIZE = int(_CFG.get("LEGEND_SIZE", 18))
LEGEND_ORIENTATION = str(_CFG.get("LEGEND_ORIENTATION", "auto"))
LEGEND_SHOW_SYMBOLS = bool(_CFG.get("LEGEND_SHOW_SYMBOLS", False))
LEGEND_SYMBOL_SIZE = float(_CFG.get("LEGEND_SYMBOL_SIZE", 10))
TICK_SIZE = int(_CFG.get("TICK_SIZE", 18))
AXIS_LABEL_SIZE = int(_CFG.get("AXIS_LABEL_SIZE", 18))
TITLE_SIZE = int(_CFG.get("TITLE_SIZE", 22))
SHOW_TICK_LABELS = bool(_CFG.get("SHOW_TICK_LABELS", True))
SHOW_AXIS_LABELS = bool(_CFG.get("SHOW_AXIS_LABELS", True))
SHOW_TITLE = bool(_CFG.get("SHOW_TITLE", True))

BASE_LOAD = float(_CFG.get("BASE_LOAD", 2.5))
END_LOAD = float(_CFG.get("END_LOAD", 17.5))

# ---- Appearance settings ----
# Raw data points
RAW_ALT_COLORS = ["#45A1D6", "#F09C67"]  # alternating raw point colors
RAW_COLORS = {BASE_LOAD: RAW_ALT_COLORS[0], END_LOAD: RAW_ALT_COLORS[1]}
RAW_MARKER = "o"
RAW_MARKER_SIZE = 1.0
RAW_ALPHA = 1.0

# Mean values
MEAN_COLORS = {"a": "red", "b": "black"}
MEAN_MARKER = "o"
MEAN_MARKER_SIZE = 3
MEAN_LINE_WIDTH = 1

# Annotation and legend sizes
DELTA_LABEL_SIZE = 30
MINI_DELTA_LABEL_SIZE = 12
LEGEND_MARKER_SIZE = 6

# Layout tweaks
OFFSET = 0.25
JITTER_SPAN = 0.04
MEAN_SHIFT = OFFSET * 2
# Horizontal span of each miniature stress dependence curve.
#
# A value of ``1.0`` makes neighbouring curves touch so that the raw data of
# one sample ends exactly where the next sample's raw data begins (e.g. where
# ``s3-1a`` ends ``s3-1b`` begins).  The setting can still be overridden via the
# configuration file if a different spacing is desired.
CURVE_WIDTH = float(_CFG.get("CURVE_WIDTH", 1.0))

FNAME_RE = re.compile(
    r"^(?P<composition>.+?)\s+"
    r"(?P<title>\S+)\s+"
    r"(?P<sample_end>\S+[ab])\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<load>\d+(?:,\d+)?)(?P<dir>[ab])$"
)
LABELS = {
    "T1": "T1 (µs)",
    "T2": "T2 (µs)",
    "dT": "T2–T1 (µs)",
    "sum": "T1+T2 (µs)",
}

_EXPORT_ORDER = ("T1", "T2", "dT", "sum")

logger = logging.getLogger("PyPlot.stress_sensitivity")


def _sanitise_stem(*parts: str) -> str:
    stem = "_".join(part.strip().replace(" ", "_") for part in parts if part)
    return re.sub(r"[^A-Za-z0-9_.-]", "_", stem) or "stress_sensitivity"



def parse_metadata(stem: str) -> Dict[str, Any] | None:
    m = FNAME_RE.match(stem)
    if not m:
        return None
    md = m.groupdict()
    md["load"] = float(md["load"].replace(",", "."))
    md["sample"] = re.sub(r"[ab]$", "", md["sample_end"])
    return md


def load_data(files: List[str]) -> pd.DataFrame:
    files = sorted(files)
    if not files:
        raise FileNotFoundError("No files selected")
    dfs = []
    for fn in files:
        md = parse_metadata(Path(fn).stem)
        if md is None:
            logger.warning(f"Skipping {fn}")
            continue
        df = pd.read_csv(
            fn,
            sep=";",
            header=None,
            names=["T1", "T2", "dT", "sum"],
            engine="python",
            on_bad_lines="skip",
        )
        df["filename"] = Path(fn).name
        df["line"] = np.arange(len(df))
        df[["T1", "T2", "dT", "sum"]] = df[["T1", "T2", "dT", "sum"]].apply(pd.to_numeric, errors="coerce")
        for k, v in md.items():
            df[k] = v
        dfs.append(df)
    if not dfs:
        raise ValueError("No valid files selected. Check filenames.")
    return pd.concat(dfs, ignore_index=True)


def plot_variable(df: pd.DataFrame, var: str, save_flag: bool, out_dir: str) -> Tuple[Figure, str]:
    comp = df['composition'].iat[0]
    title = df['title'].iat[0]
    anneal = df['anneal'].iat[0]
    sample = df['sample_end'].iat[0]
    mean_color = MEAN_COLORS.get(sample[-1], "black")

    base_mean = df[(df['dir'] == 'b') & (df['load'] == BASE_LOAD)][var].mean()
    end_mean = df[(df['dir'] == 'b') & (df['load'] == END_LOAD)][var].mean()
    if np.isnan(base_mean) or np.isnan(end_mean):
        return plt.figure(), ""

    raw = df[(df['dir'] == 'b') & (df['load'].isin([BASE_LOAD, END_LOAD]))].copy()
    raw['sample_idx'] = 1
    raw['x_center'] = raw['sample_idx'] + raw['load'].map({BASE_LOAD: -OFFSET, END_LOAD: OFFSET})
    np.random.seed(0)
    raw['x'] = raw['x_center'] + np.random.uniform(-JITTER_SPAN, JITTER_SPAN, len(raw))
    raw['y'] = raw[var] - base_mean

    means = raw.groupby('load')['y'].mean().reset_index()

    dep = df[df['dir'] == 'b'].groupby('load')[var].mean().sort_index().reset_index()
    dep['y'] = dep[var] - base_mean

    y_min = min(raw['y'].min(), dep['y'].min())
    y_max = max(raw['y'].max(), dep['y'].max())
    y_range = y_max - y_min if y_max != y_min else 1.0
    delta_offset = 0.05 * y_range

    fig, ax = plt.subplots(figsize=(7, 5))
    for load in [BASE_LOAD, END_LOAD]:
        sub = raw[raw['load'] == load]
        ax.scatter(
            sub['x'],
            sub['y'],
            c=RAW_COLORS.get(load, 'gray'),
            marker=RAW_MARKER,
            s=RAW_MARKER_SIZE,
            alpha=RAW_ALPHA,
            label=f'raw {load}g',
        )

    ax.plot(
        [1 - OFFSET, 1 + OFFSET],
        means.set_index('load').loc[[BASE_LOAD, END_LOAD], 'y'],
        MEAN_MARKER+'-',
        c=mean_color,
        markersize=MEAN_MARKER_SIZE,
        linewidth=MEAN_LINE_WIDTH,
        label='mean marked end' if sample.endswith('a') else 'mean unmarked end',
    )

    if INCLUDE_DEPENDENCE and not dep.empty:
        start = dep['load'].iloc[0]
        end = dep['load'].iloc[-1]
        x_start = 1 - MEAN_SHIFT
        x_end = 1 + MEAN_SHIFT
        scale = (x_end - x_start) / (end - start) if end != start else 1.0
        x_vals = (dep['load'] - start) * scale + x_start
        med = dep['y'].rolling(MED_WINDOW, center=True, min_periods=1).median()
        proc = med.rolling(MA_WINDOW, center=True, min_periods=1).mean()
        ax.plot(x_vals, proc, color='black', label=f'dependence med {MED_WINDOW} mwa {MA_WINDOW}')

    delta = end_mean - base_mean
    ax.annotate(
        f"\u0394(17.5b–2.5b) = {delta:.1f}",
        xy=(1, delta),
        xytext=(0, np.sign(delta) * 5),
        textcoords="offset points",
        ha="center",
        va="bottom" if delta >= 0 else "top",
        fontsize=DELTA_LABEL_SIZE,
    )

    ax.set_xticks([1])
    ax.set_xticklabels([sample])
    ax.set_xlabel('Sample')
    ax.set_ylabel(LABELS[var])
    ax.set_title(f"{comp} {title} {sample} {anneal} — {LABELS[var]}")
    ax.grid(True)

    legend = ax.legend(loc='best')
    for text, handle in zip(legend.get_texts(), legend.legend_handles):
        if isinstance(handle, Line2D):
            rawcol = handle.get_color()
            if handle.get_markerfacecolor() and handle.get_markerfacecolor() != 'none':
                rawcol = handle.get_markerfacecolor()
            handle.set_markersize(LEGEND_MARKER_SIZE)
        elif isinstance(handle, (Patch, PathCollection)):
            rawcol = handle.get_facecolor()
            if isinstance(handle, PathCollection):
                handle.set_sizes([LEGEND_MARKER_SIZE ** 2])
            if isinstance(rawcol, np.ndarray) and rawcol.ndim > 1:
                rawcol = rawcol[0]
        else:
            rawcol = 'black'
        text.set_color(to_hex(cast(ColorType, rawcol)))

    fig.tight_layout()
    apply_readability(ax, globals())
    fname = f"{comp} {title} {sample} {anneal} {var}"
    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        save_figure(fig, os.path.join(out_dir, fname), SAVE_FORMAT, PNG_DPI)
    return fig, f"{fname}.{SAVE_FORMAT}"


def _draw_mini_dependence(
    ax: Axes,
    df: pd.DataFrame,
    var: str,
    center: float,
    width: float,
) -> Tuple[float, float]:
    """Draw a stress dependence curve scaled around ``center``.

    The curve shows only unloading (``b``) data with raw points and means.
    The y values are shifted so that the first load is at zero."""

    base_mean = df[(df["dir"] == "b") & (df["load"] == BASE_LOAD)][var].mean()
    if np.isnan(base_mean):
        return 0.0, 0.0

    sub = df[df["dir"] == "b"].copy()
    x_start = center - width / 2
    x_end = center + width / 2

    # Distribute each load into an equal-width segment so that raw points from
    # adjacent loads touch but do not overlap. When the raw data for one load
    # ends, the next load begins immediately to its right.
    loads_sorted = sorted(sub["load"].unique())
    n_loads = len(loads_sorted)
    seg_width = (x_end - x_start) / n_loads if n_loads else width
    seg_starts = {load: x_start + i * seg_width for i, load in enumerate(loads_sorted)}
    seg_ends = {load: seg_start + seg_width for load, seg_start in seg_starts.items()}
    centers = {load: (seg_starts[load] + seg_ends[load]) / 2 for load in loads_sorted}

    np.random.seed(0)
    sub["x_center"] = sub["load"].map(centers)
    sub["x"] = sub.apply(
        lambda r: np.random.uniform(seg_starts[r["load"]], seg_ends[r["load"]]),
        axis=1,
    )
    sub["y"] = sub[var] - base_mean
    color_map = {load: RAW_ALT_COLORS[i % len(RAW_ALT_COLORS)] for i, load in enumerate(loads_sorted)}
    colors = sub["load"].map(color_map)

    ax.scatter(
        sub["x"],
        sub["y"],
        c=colors,
        marker=RAW_MARKER,
        s=RAW_MARKER_SIZE,
        alpha=RAW_ALPHA,
    )

    means = sub.groupby("load").agg({"x_center": "mean", "y": "mean"}).reset_index()
    sample_end = str(df["sample_end"].iat[0]) if "sample_end" in df.columns else ""
    mean_color = MEAN_COLORS.get(sample_end[-1], "black")
    ax.plot(
        means["x_center"],
        means["y"],
        MEAN_MARKER + "-",
        c=mean_color,
        markersize=MEAN_MARKER_SIZE,
        linewidth=MEAN_LINE_WIDTH,
    )

    if INCLUDE_DEPENDENCE:
        dep = sub.groupby("load")[var].mean().sort_index().reset_index()
        dep["y"] = dep[var] - base_mean
        med = dep["y"].rolling(MED_WINDOW, center=True, min_periods=1).median()
        proc = med.rolling(MA_WINDOW, center=True, min_periods=1).mean()
        x_vals = dep["load"].map(centers)
        ax.plot(x_vals, proc, color="black", linewidth=1)
        dep_min, dep_max = proc.min(), proc.max()
    else:
        dep_min, dep_max = sub["y"].min(), sub["y"].max()

    y_min = min(sub["y"].min(), dep_min)
    y_max = max(sub["y"].max(), dep_max)
    return y_min, y_max


def plot_samples(df: pd.DataFrame, var: str, save_flag: bool, out_dir: str) -> Tuple[Figure, str]:
    """Plot miniature stress dependence curves for all samples in ``df``."""
    comp = df['composition'].iat[0]
    title = df['title'].iat[0]
    anneal = df['anneal'].iat[0]

    sample_order = list(dict.fromkeys(df['sample_end'].tolist()))
    sample_labels = df.set_index("sample_end").get("sample_label", pd.Series(dtype=str))
    samples = sample_order
    # Give each sample enough horizontal space so its miniature dependence curve
    # spans ``CURVE_WIDTH`` units.  The figure width scales with ``CURVE_WIDTH``
    # to retain roughly the same level of detail regardless of the setting.
    fig_width = max(9.0, len(samples) * CURVE_WIDTH * 1.8)
    fig, ax = plt.subplots(figsize=(fig_width, 6.5))

    y_min, y_max = np.inf, -np.inf
    deltas = []
    for idx, sample in enumerate(samples, start=1):
        sub = df[df['sample_end'] == sample]
        _min, _max = _draw_mini_dependence(ax, sub, var, idx, CURVE_WIDTH)
        base_mean = sub[(sub['dir'] == 'b') & (sub['load'] == BASE_LOAD)][var].mean()
        end_mean = sub[(sub['dir'] == 'b') & (sub['load'] == END_LOAD)][var].mean()
        delta = end_mean - base_mean
        deltas.append((idx, delta))
        y_min = min(y_min, _min, 0, delta)
        y_max = max(y_max, _max, 0, delta)

    ax.set_xlim(0.5, len(samples) + 0.5)
    ax.set_xticks(range(1, len(samples) + 1))
    labels = [sample_labels.get(s, s) for s in samples]
    ax.set_xticklabels(labels)
    ax.set_xlabel('Sample')
    ax.set_ylabel(LABELS[var])
    ax.set_title(f"{comp} {title} {anneal} — {LABELS[var]}")
    ax.grid(True)
    y_range = (y_max - y_min) if y_max != y_min else 1.0
    delta_offset = 0.05 * y_range
    if y_min < y_max:
        ax.set_ylim(
            y_min - 0.02 * y_range,
            y_max + delta_offset + 0.02 * y_range,
        )
    for idx, delta in deltas:
        ax.annotate(
            f"{delta:.1f}",
            (idx, delta),
            xytext=(0, np.sign(delta) * 5),
            textcoords="offset points",
            ha='center',
            va='bottom' if delta >= 0 else 'top',
            fontsize=MINI_DELTA_LABEL_SIZE,
        )

    handles = [
        Line2D([0], [0], marker=RAW_MARKER, linestyle='', color='none',
               markerfacecolor=RAW_ALT_COLORS[0], markersize=LEGEND_MARKER_SIZE,
               label='raw odd loads'),
        Line2D([0], [0], marker=RAW_MARKER, linestyle='', color='none',
               markerfacecolor=RAW_ALT_COLORS[1], markersize=LEGEND_MARKER_SIZE,
               label='raw even loads'),
        Line2D([0], [0], marker=MEAN_MARKER, color=MEAN_COLORS['a'],
               markersize=LEGEND_MARKER_SIZE, linewidth=MEAN_LINE_WIDTH,
               label='mean marked end'),
        Line2D([0], [0], marker=MEAN_MARKER, color=MEAN_COLORS['b'],
               markersize=LEGEND_MARKER_SIZE, linewidth=MEAN_LINE_WIDTH,
               label='mean unmarked end'),
    ]
    if INCLUDE_DEPENDENCE:
        handles.append(Line2D([0], [0], color='black', label=f'dependence med {MED_WINDOW} mwa {MA_WINDOW}'))
    legend = ax.legend(handles=handles, loc='best')
    for text, handle in zip(legend.get_texts(), legend.legend_handles):
        if isinstance(handle, Line2D):
            rawcol = handle.get_color()
            if handle.get_markerfacecolor() and handle.get_markerfacecolor() != 'none':
                rawcol = handle.get_markerfacecolor()
            handle.set_markersize(LEGEND_MARKER_SIZE)
        elif isinstance(handle, (Patch, PathCollection)):
            rawcol = handle.get_facecolor()
            if isinstance(handle, PathCollection):
                handle.set_sizes([LEGEND_MARKER_SIZE ** 2])
            if isinstance(rawcol, np.ndarray) and rawcol.ndim > 1:
                rawcol = rawcol[0]
        else:
            rawcol = 'black'
        text.set_color(to_hex(cast(ColorType, rawcol)))

    fig.tight_layout()
    fig.subplots_adjust(left=0.12, right=0.98, bottom=0.2, top=0.88)
    apply_readability(ax, globals())
    fname = f"{comp} {title} {anneal} {var}"
    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        save_figure(fig, os.path.join(out_dir, fname), SAVE_FORMAT, PNG_DPI)
    return fig, f"{fname}.{SAVE_FORMAT}"


def plot_samples_origin(
    df: pd.DataFrame,
    var: str,
    include_dep: bool = INCLUDE_DEPENDENCE,
    med_window: int = MED_WINDOW,
    ma_window: int = MA_WINDOW,
) -> None:
    """Create an Origin graph approximating the Matplotlib multi-sample view."""

    import originpro as op  # lazy import
    # Ensure Origin UI is shown when creating books/graphs
    try:
        op.set_show()
    except Exception:
        pass

    work_df = df.copy()
    if "sample_label" not in work_df.columns:
        if "sample" in work_df.columns:
            work_df["sample_label"] = work_df["sample"].astype(str)
        else:
            work_df["sample_label"] = work_df.get("sample_end", "").astype(str)

    comp = work_df['composition'].iat[0]
    title = work_df['title'].iat[0]
    anneal = work_df['anneal'].iat[0]

    sample_order = list(dict.fromkeys(work_df["sample_end"].tolist()))
    label_map = (
        work_df[["sample_end", "sample_label"]]
        .drop_duplicates("sample_end")
        .set_index("sample_end")
        .get("sample_label", pd.Series(dtype=str))
    )
    samples = sample_order
    sample_idx = {s: i + 1 for i, s in enumerate(samples)}
    delta_map: dict[str, float] = {}

    # Compute per-sample deltas for label placement
    base_vals = (
        work_df[(work_df.get("dir") == "b") & (work_df.get("load") == BASE_LOAD)]
        .groupby("sample_end")[var]
        .mean()
    )
    end_vals = (
        work_df[(work_df.get("dir") == "b") & (work_df.get("load") == END_LOAD)]
        .groupby("sample_end")[var]
        .mean()
    )
    delta_series = (end_vals - base_vals.reindex(end_vals.index)).dropna()
    delta_map.update(delta_series.to_dict())

    # Build raw points and means for b-direction only, baseline at BASE_LOAD
    frames_raw_odd = []
    frames_raw_even = []
    mean_lines: list[tuple[pd.DataFrame, str]] = []
    cont_lines: list[pd.DataFrame] = []

    for s in samples:
        sub = df[(df['sample_end'] == s) & (df['dir'] == 'b')].copy()
        if sub.empty:
            continue
        base_mean = sub[sub['load'] == BASE_LOAD][var].mean()
        if np.isnan(base_mean):
            base_mean = 0.0
        loads_sorted = sorted(sub['load'].unique())
        n_loads = len(loads_sorted)
        center = sample_idx[s]
        x_start = center - CURVE_WIDTH / 2
        seg_width = (CURVE_WIDTH / n_loads) if n_loads else CURVE_WIDTH
        seg_starts = {load: x_start + i * seg_width for i, load in enumerate(loads_sorted)}
        seg_ends = {load: seg_starts[load] + seg_width for load in loads_sorted}
        centers = {load: (seg_starts[load] + seg_ends[load]) / 2 for load in loads_sorted}
        # raw
        rng = np.random.default_rng(0)
        sub['x'] = sub.apply(lambda r: rng.uniform(seg_starts[r['load']], seg_ends[r['load']]), axis=1)
        sub['y'] = sub[var] - base_mean
        # split odd/even by ordinal index
        parity = {load: (i % 2) for i, load in enumerate(loads_sorted)}
        odd = sub[sub['load'].map(lambda l: parity[l] == 1)][['x', 'y']].rename(columns={'x': 'X', 'y': 'Y'})
        even = sub[sub['load'].map(lambda l: parity[l] == 0)][['x', 'y']].rename(columns={'x': 'X', 'y': 'Y'})
        frames_raw_odd.append(odd)
        frames_raw_even.append(even)
        # means
        m = sub.groupby('load').agg(x_center=('x', 'mean'), y=('y', 'mean')).reset_index()
        mean_df = m[['x_center', 'y']].rename(columns={'x_center': 'X', 'y': 'Y'})
        mean_color = MEAN_COLORS.get(s[-1], 'black')
        mean_lines.append((mean_df, mean_color))
        # processed dependence
        if include_dep:
            dep = sub.sort_values('load')
            med = dep['y'].rolling(med_window, center=True, min_periods=1).median()
            proc = med.rolling(ma_window, center=True, min_periods=1).mean()
            x_vals = dep['load'].map(centers)
            cont_lines.append(pd.DataFrame({'X': x_vals, 'Y': proc}))

    raw_odd = pd.concat(frames_raw_odd, ignore_index=True) if frames_raw_odd else pd.DataFrame(columns=['X','Y'])
    raw_even = pd.concat(frames_raw_even, ignore_index=True) if frames_raw_even else pd.DataFrame(columns=['X','Y'])

    # Push to Origin (single worksheet with all series)
    book_obj = op.new_book('w', lname="Stress Sens (Python)")
    book = cast(Any, book_obj)
    gp_obj = op.new_graph(template='scatter')
    gp = cast(Any, gp_obj)
    try:
        gl = cast(Any, gp[0])
    except Exception:
        gl = None
    if gp is None or gl is None:
        return
    try:
        book.activate()
    except Exception:
        pass

    try:
        gp.activate()
        op.lt_exec('window -s 0 0 1200 800;')
    except Exception:
        pass

    # Build a combined worksheet
    series_lengths: list[int] = [len(raw_odd), len(raw_even)]
    series_lengths.extend(len(df_) for df_, _ in mean_lines)
    series_lengths.extend(len(df_) for df_ in cont_lines)
    if 'processed_df' in locals():
        series_lengths.append(len(processed_df))
    max_len = max(series_lengths) if series_lengths else 0
    def _pad(series: list[Any], target: int) -> list[Any]:
        return series + [np.nan] * (target - len(series))

    plot_data: dict[str, list[Any]] = {}
    plot_comments: dict[str, str] = {}
    plot_units: dict[str, str] = {}

    plot_data["raw_odd_x"] = _pad(raw_odd["X"].to_list(), max_len)
    plot_data["raw_odd_y"] = _pad(raw_odd["Y"].to_list(), max_len)
    plot_comments["raw_odd_x"] = "Raw odd loads (X)"
    plot_comments["raw_odd_y"] = "Raw odd loads"
    plot_units["raw_odd_y"] = LABELS.get(var, "")

    plot_data["raw_even_x"] = _pad(raw_even["X"].to_list(), max_len)
    plot_data["raw_even_y"] = _pad(raw_even["Y"].to_list(), max_len)
    plot_comments["raw_even_x"] = "Raw even loads (X)"
    plot_comments["raw_even_y"] = "Raw even loads"
    plot_units["raw_even_y"] = LABELS.get(var, "")

    mean_column_pairs: list[tuple[str, str, str]] = []
    for idx, (m_df, color) in enumerate(mean_lines, start=1):
        x_key = f"mean{idx}_x"
        y_key = f"mean{idx}_y"
        plot_data[x_key] = _pad(m_df["X"].to_list(), max_len)
        plot_data[y_key] = _pad(m_df["Y"].to_list(), max_len)
        plot_comments[x_key] = f"Mean line {idx} (X)"
        plot_comments[y_key] = f"Mean line {idx}"
        plot_units[y_key] = LABELS.get(var, "")
        mean_column_pairs.append((x_key, y_key, color))

    cont_column_pairs: list[tuple[str, str]] = []
    if cont_lines:
        for idx, cont_df in enumerate(cont_lines, start=1):
            x_key = f"cont{idx}_x"
            y_key = f"cont{idx}_y"
            plot_data[x_key] = _pad(cont_df["X"].to_list(), max_len)
            plot_data[y_key] = _pad(cont_df["Y"].to_list(), max_len)
            plot_comments[x_key] = f"Processed {idx} (X)"
            plot_comments[y_key] = f"Processed {idx} med {med_window} + mwa {ma_window}"
            plot_units[y_key] = LABELS.get(var, "")
            cont_column_pairs.append((x_key, y_key))

    processed_df, _meta = build_workbook_table(df)
    proc_padded = processed_df.reindex(range(max_len)).reset_index(drop=True)
    combined_df = proc_padded.copy()
    for key, values in plot_data.items():
        combined_df[key] = values

    sheet = op.new_sheet('w', lname='Stress Sens')
    w = cast(Any, sheet)
    w.from_df(combined_df)

    # Column metadata
    try:
        cols = w.cols
        units_map = {
            "load": "g",
            "dir": "",
            "line": "",
            "sample_end": "",
            "sample_name": "",
            "sample_label": "",
            "filename": "",
        }
        friendly = {
            "sample_end": "Sample",
            "sample_name": "Sample name",
            "sample_label": "Label",
            "filename": "Filename",
            "dir": "Direction",
            "line": "Line",
        }
        for col_name in combined_df.columns:
            units_map.setdefault(col_name, "")
        for idx, col in enumerate(combined_df.columns):
            col_obj = cols[idx]
            lname = friendly.get(col, col)
            col_obj.lname = lname
            base_key = None
            if col in LABELS:
                units = LABELS[col]
            elif col.startswith("baseline_"):
                base_key = col.split("_", 1)[1]
                units = LABELS.get(base_key, "")
            elif col.startswith("delta_"):
                base_key = col.split("_", 1)[1]
                units = LABELS.get(base_key, "")
            elif col.endswith("_relative"):
                base_key = col.rsplit("_", 1)[0]
                units = LABELS.get(base_key, "")
            elif col.endswith("_x"):
                units = plot_units.get(col, "")
            elif col.endswith("_y"):
                units = plot_units.get(col, LABELS.get(var, ""))
            else:
                units = units_map.get(col, "")
            col_obj.units = units
            comments = ""
            if col.startswith("baseline_"):
                comments = f"Baseline at {BASE_LOAD} g"
            elif col.startswith("delta_"):
                comments = f"Delta @ {END_LOAD} g - baseline"
            elif col.endswith("_relative"):
                comments = "Value - baseline"
            elif col == "load":
                comments = "Applied load"
            elif col == "dir":
                comments = "b = unmarked end"
            elif col == "sample_name":
                comments = "Display sample label"
            elif col == "sample_label":
                comments = "Label from file"
            elif col in plot_comments:
                comments = plot_comments[col]
            elif col.endswith("_x"):
                comments = lname + " (X)"
            elif col.endswith("_y"):
                comments = lname + " (Y)"
            col_obj.comments = comments
            try:
                w.set_label(idx, lname)
                w.set_units(idx, units)
                w.set_comments(idx, comments)
            except Exception:
                pass
    except Exception:
        pass

    # Use combined sheet for plots
    idx_raw_odd_x = combined_df.columns.get_loc("raw_odd_x")
    idx_raw_odd_y = combined_df.columns.get_loc("raw_odd_y")
    idx_raw_even_x = combined_df.columns.get_loc("raw_even_x")
    idx_raw_even_y = combined_df.columns.get_loc("raw_even_y")
    p = cast(Any, gl.add_plot(w, coly=idx_raw_odd_y, colx=idx_raw_odd_x, type='s'))
    try:
        p.color = RAW_ALT_COLORS[1]
        p.symbol_size = RAW_MARKER_SIZE
        p.legend = "raw marked end"
        p.lname = "raw marked end"
    except Exception:
        pass
    p = cast(Any, gl.add_plot(w, coly=idx_raw_even_y, colx=idx_raw_even_x, type='s'))
    try:
        p.color = RAW_ALT_COLORS[0]
        p.symbol_size = RAW_MARKER_SIZE
        p.legend = "raw unmarked end"
        p.lname = "raw unmarked end"
    except Exception:
        pass

    for plot_idx, (x_key, y_key, color) in enumerate(mean_column_pairs, start=1):
        try:
            idx_x = combined_df.columns.get_loc(x_key)
            idx_y = combined_df.columns.get_loc(y_key)
        except Exception:
            continue
        p = cast(Any, gl.add_plot(w, coly=idx_y, colx=idx_x, type='y'))
        try:
            p.color = color
            p.symbol_shape = 2
            p.symbol_size = LEGEND_MARKER_SIZE
            p.legend = "mean marked end" if plot_idx == 1 else "mean unmarked end"
            p.lname = "mean marked end" if plot_idx == 1 else "mean unmarked end"
        except Exception:
            pass

    if INCLUDE_DEPENDENCE:
        for x_key, y_key in cont_column_pairs:
            try:
                idx_x = combined_df.columns.get_loc(x_key)
                idx_y = combined_df.columns.get_loc(y_key)
            except Exception:
                continue
            p = cast(Any, gl.add_plot(w, coly=idx_y, colx=idx_x, type='y'))
            try:
                p.color = 'black'
                p.legend = f"dependence med {med_window} mwa {ma_window}"
                p.lname = f"dependence med {med_window} mwa {ma_window}"
            except Exception:
                pass

    title_str = f"{comp} {title} {anneal} — {LABELS.get(var, var)}".strip(" —")

    try:
        gl.rescale()
    except Exception:
        pass
    try:
        gp.activate()
        op.lt_exec('legend -o;')
    except Exception:
        pass
    try:
        legend = gl.label('Legend')
        legend_lines = [
            "\\c(1) raw marked end",
            "\\c(2) raw unmarked end",
            "\\c(3) mean marked end",
            "\\c(4) mean unmarked end",
        ]
        if INCLUDE_DEPENDENCE and cont_column_pairs:
            legend_lines.append("\\c(5) dependence")
        legend.text = "\n".join(legend_lines)
        op.lt_exec('legend.update=0;')
    except Exception:
        pass

    try:
        x_axis = gl.axis('x')
    except Exception:
        x_axis = None
    try:
        y_axis = gl.axis('y')
    except Exception:
        y_axis = None

    try:
        if x_axis is not None:
            x_axis.set_limits(0.5, len(samples) + 0.5, 1.0)
            x_axis.title = ''
    except Exception:
        pass
    try:
        if y_axis is not None:
            y_axis.title = LABELS.get(var, "")
    except Exception:
        pass
    for attr in (
        'x.top', 'y.right',
        'x.top.label.show', 'y.right.label.show',
        'x.top.ticklabels', 'y.right.ticklabels',
        'x.label.show', 'x.ticklabels',
        'x.bottom.ticklabels', 'x.major.ticklabels',
    ):
        try:
            gl.set_int(attr, 0)
        except Exception:
            continue
    # avoid LT errors; rely on set_int calls above

    for idx in range(1, len(samples) + 1):
        try:
            gl.remove_label(f'py_xtick{idx}')
        except Exception:
            pass
    for name in ('py_xlabel', 'py_title'):
        try:
            gl.remove_label(name)
        except Exception:
            pass

    try:
        bottom = getattr(y_axis, 'from_', None)
        top = getattr(y_axis, 'to', None)
    except Exception:
        bottom = None
        top = None
    y_range = (top - bottom) if (top is not None and bottom is not None) else 1.0
    label_y = float(bottom - 0.12 * y_range) if bottom is not None else 0.0
    title_y = float((top if top is not None else 0.0) + 0.12 * y_range)
    title_center = (len(samples) + 1) / 2.0

    for idx, sample in enumerate(samples, start=1):
        label = label_map.get(sample, sample)
        try:
            lbl = gl.add_label(label, float(idx), label_y)
        except Exception:
            lbl = None
        if lbl is None:
            continue
        try:
            lbl.name = f'py_xtick{idx}'
            lbl.set_int('attach', 0)
            lbl.set_int('horzalign', 1)
            lbl.set_int('vertalign', 2)
            lbl.set_int('fontweight', 700)
        except Exception:
            pass

    if y_axis is not None and bottom is not None and top is not None:
        safe_range = y_range if y_range else 1.0
        try:
            y_axis.set_limits(bottom - 0.15 * safe_range, top + 0.12 * safe_range)
        except Exception:
            pass

    try:
        axis_label = gl.add_label(
            'Sample',
            float(title_center),
            float(label_y - 0.06 * y_range),
        )
    except Exception:
        axis_label = None
    if axis_label is not None:
        try:
            axis_label.name = 'py_xlabel'
            axis_label.set_int('attach', 0)
            axis_label.set_int('horzalign', 1)
            axis_label.set_int('vertalign', 2)
            axis_label.set_int('fontweight', 700)
        except Exception:
            pass

    try:
        set_origin_axis_title(gl, 'y', LABELS.get(var, ""))
    except Exception:
        pass
    try:
        set_origin_graph_title(op, gp, gl, title_str)
    except Exception:
        pass

    # Delta labels mirroring Matplotlib
    for idx, sample in enumerate(samples, start=1):
        delta_val = delta_map.get(sample)
        if delta_val is None or np.isnan(delta_val):
            continue
        try:
            lbl = gl.add_label(f"{delta_val:.1f}", float(idx), float(delta_val))
        except Exception:
            lbl = None
        if lbl is None:
            continue
        try:
            lbl.set_int('attach', 0)
            lbl.set_int('horzalign', 1)
            lbl.set_int('vertalign', 0 if delta_val >= 0 else 1)
            lbl.set_int('fontweight', 700)
            lbl.set_int('fontheight', 14)
        except Exception:
            pass

    try:
        hide_origin_workbook(op, book, gp)
    except Exception:
        pass

def build_workbook_table(
    grp: pd.DataFrame,
    *,
    baseline_mode: str = "relative",
    include_cont: bool = INCLUDE_DEPENDENCE,
    med_window: int = MED_WINDOW,
    ma_window: int = MA_WINDOW,
) -> tuple[pd.DataFrame, Dict[str, str]]:
    """Return a processed stress-sensitivity table for workbooks/exports."""

    comp = str(grp["composition"].iat[0]) if "composition" in grp else ""
    title = str(grp["title"].iat[0]) if "title" in grp else ""
    anneal = str(grp["anneal"].iat[0]) if "anneal" in grp else ""
    if "sample_end" not in grp.columns:
        grp = grp.copy()
        if "sample" in grp.columns:
            grp["sample_end"] = grp["sample"]
        else:
            grp["sample_end"] = np.arange(len(grp))
    if "sample_label" not in grp.columns:
        if "sample" in grp.columns:
            grp = grp.copy()
            grp["sample_label"] = grp["sample"].astype(str)
        else:
            grp = grp.copy()
            grp["sample_label"] = grp.get("sample_end", "").astype(str)
    sample_source = grp.copy()
    if "sample" not in sample_source.columns:
        sample_source["sample"] = sample_source["sample_end"].astype(str)
    sample_names = sample_source[["sample_end", "sample"]]
    sample_names = (
        sample_names.dropna(subset=["sample_end"])
        .drop_duplicates("sample_end")
        .set_index("sample_end")["sample"]
    )

    work = grp.copy()
    work["load"] = pd.to_numeric(work.get("load"), errors="coerce")
    work["line"] = pd.to_numeric(work.get("line"), errors="coerce")
    work.sort_values(["sample_end", "dir", "load", "line"], inplace=True, na_position="last")
    if "sample" in work:
        work["sample_label"] = work["sample"].astype(str)
    else:
        work["sample_label"] = work.get("sample_end", "").astype(str)
    work["sample_name"] = work["sample_end"].map(sample_names).fillna(work["sample_label"])

    base_mask = (
        (work.get("dir") == "b")
        & work["load"].notna()
        & (work["load"].sub(BASE_LOAD).abs() < 1e-6)
    )
    end_mask = (
        (work.get("dir") == "b")
        & work["load"].notna()
        & (work["load"].sub(END_LOAD).abs() < 1e-6)
    )

    baselines = {
        var: work.loc[base_mask].groupby("sample_end")[var].mean()
        for var in _EXPORT_ORDER
    }
    end_means = {
        var: work.loc[end_mask].groupby("sample_end")[var].mean()
        for var in _EXPORT_ORDER
    }

    for var in _EXPORT_ORDER:
        base_series = baselines[var].reset_index().drop_duplicates("sample_end").set_index("sample_end")[var]
        end_series = end_means[var].reset_index().drop_duplicates("sample_end").set_index("sample_end")[var]
        work[f"baseline_{var}"] = work["sample_end"].map(base_series)
        work[f"{var}_relative"] = work[var] - work[f"baseline_{var}"]
        delta_series = end_series - base_series.reindex(end_series.index)
        work[f"delta_{var}"] = work["sample_end"].map(delta_series)

    columns: list[str] = [
        "composition",
        "title",
        "anneal",
        "sample_end",
        "sample_name",
        "sample_label",
        "filename",
        "dir",
        "load",
        "line",
    ]
    for var in _EXPORT_ORDER:
        columns.extend([var, f"{var}_relative", f"baseline_{var}", f"delta_{var}"])

    export_cols = [col for col in columns if col in work.columns]
    export_df = work[export_cols].copy()
    return export_df, {"composition": comp, "title": title, "anneal": anneal}


def export_group_to_txt(grp: pd.DataFrame, directory: str | Path) -> Path:
    """Export stress sensitivity measurements with baseline metadata."""

    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    export_df, meta = build_workbook_table(grp)
    comp = meta.get("composition", "")
    title = meta.get("title", "")
    anneal = meta.get("anneal", "")

    stem = _sanitise_stem("stress_sensitivity", comp, title, anneal)
    path = out_dir / f"{stem}.txt"
    export_df.to_csv(path, sep="\t", index=False, float_format="%.10g")
    return path


def main(files: List[str], backend: str = BACKEND) -> None:
    if IMPROVE_READABILITY:
        apply_readability_fonts()
    data = load_data(files)
    data = maybe_handle_outliers(data)
    groups = data.groupby(['composition', 'title', 'anneal'])
    total = len(groups) * len(PLOT_VARS)
    do_show = SHOW_PLOTS and wants_matplotlib(backend) and (total <= MAX_SHOW)
    if SHOW_PLOTS and wants_matplotlib(backend) and not do_show:
        logger.warning(f"Too many plots ({total}); only saving to '{OUTPUT_DIR}'.")

    progress = ProgressDialog(total) if total else None
    plots: List[Tuple[Figure, str]] = []
    for _, grp in groups:
        for var in PLOT_VARS:
            if progress and getattr(progress, 'cancelled', False):
                break
            if wants_matplotlib(backend):
                fig, fname = plot_samples(grp, var, SAVE_PLOTS, OUTPUT_DIR)
                if fname:
                    plots.append((fig, fname))
            if wants_origin(backend):
                try:
                    plot_samples_origin(grp, var, include_dep=INCLUDE_DEPENDENCE, med_window=MED_WINDOW, ma_window=MA_WINDOW)
                except Exception as e:
                        logger.error(f"Origin plot failed: {e}")
            if progress:
                progress.update()
        if progress and getattr(progress, 'cancelled', False):
            break
    if progress and not getattr(progress, 'cancelled', False):
        progress.destroy()
    elif progress and getattr(progress, 'cancelled', False):
        plt.close('all')
        logger.info('Cancelled.')
        return

    if wants_matplotlib(backend):
        if do_show:
            show_plots()
        else:
            plt.close('all')
    else:
        plt.close('all')

    if wants_origin(backend):
        schedule_origin_release()


class ProgressDialog:
    """CLI progress bar used when no GUI is provided."""

    def __init__(self, total: int):
        self.pbar = tqdm(total=total, desc="Processing", unit="plot", disable=True)
        self.cancelled = False
        self.root = self

    def update(self) -> None:
        self.pbar.update(1)

    def destroy(self) -> None:
        self.pbar.close()
