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

from ..config import load_config
from ..common import maybe_handle_outliers

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
MAX_SHOW = 8

BASE_LOAD = float(_CFG.get("BASE_LOAD", 2.5))
END_LOAD = float(_CFG.get("END_LOAD", 17.5))

# ---- Appearance settings ----
# Raw data points
RAW_ALT_COLORS = ["#45A1D6", "#F09C67"]  # alternating raw point colors
RAW_COLORS = {BASE_LOAD: RAW_ALT_COLORS[0], END_LOAD: RAW_ALT_COLORS[1]}
RAW_MARKER = "o"
RAW_MARKER_SIZE = 0.1
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
# A value of ``1.0`` makes neighbouring curves touch so that the right edge of
# one sample lines up with the left edge of the next (e.g. where ``s3-1a`` ends
# ``s3-1b`` begins).  The setting can still be overridden via the configuration
# file if a different spacing is desired.
CURVE_WIDTH = float(_CFG.get("CURVE_WIDTH", 1.0))

FNAME_RE = re.compile(
    r"^(?P<composition>.+?)\s+"
    r"(?P<title>\S+)\s+"
    r"(?P<sample_end>s\d+(?:-\d+)?[ab])\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<load>\d+(?:,\d+)?)(?P<dir>[ab])$"
)
LABELS = {
    "T1": "T1 (µs)",
    "T2": "T2 (µs)",
    "dT": "T2–T1 (µs)",
    "sum": "T1+T2 (µs)",
}



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
            print(f"Skipping {fn}")
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
    fname = f"{comp} {title} {sample} {anneal} {var}.png"
    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        fig.savefig(os.path.join(out_dir, fname), dpi=300)
    return fig, fname


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
    start = sub["load"].min()
    end = sub["load"].max()
    x_start = center - width / 2
    x_end = center + width / 2
    scale = (x_end - x_start) / (end - start) if end != start else 1.0
    sub["x_center"] = (sub["load"] - start) * scale + x_start
    np.random.seed(0)
    sub["x"] = sub["x_center"] + np.random.uniform(-JITTER_SPAN, JITTER_SPAN, len(sub))
    sub["y"] = sub[var] - base_mean
    loads_sorted = sorted(sub["load"].unique())
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
        x_vals = (dep["load"] - start) * scale + x_start
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

    samples = sorted(df['sample_end'].unique())
    # Give each sample enough horizontal space so its miniature dependence curve
    # spans ``CURVE_WIDTH`` units.  The figure width scales with ``CURVE_WIDTH``
    # to retain roughly the same level of detail regardless of the setting.
    fig, ax = plt.subplots(figsize=(max(7, len(samples) * CURVE_WIDTH * 2), 5))

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
    ax.set_xticklabels(samples)
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
    fname = f"{comp} {title} {anneal} {var}.png"
    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        fig.savefig(os.path.join(out_dir, fname), dpi=300)
    return fig, fname


def main(files: List[str]) -> None:
    data = load_data(files)
    data = maybe_handle_outliers(data)
    groups = data.groupby(['composition', 'title', 'anneal'])
    total = len(groups) * len(PLOT_VARS)
    do_show = SHOW_PLOTS and (total <= MAX_SHOW)
    if SHOW_PLOTS and not do_show:
        print(f"Too many plots ({total}); only saving to '{OUTPUT_DIR}'.")

    progress = ProgressDialog(total) if total else None
    plots: List[Tuple[Figure, str]] = []
    for _, grp in groups:
        for var in PLOT_VARS:
            if progress and getattr(progress, 'cancelled', False):
                break
            fig, fname = plot_samples(grp, var, SAVE_PLOTS, OUTPUT_DIR)
            if fname:
                plots.append((fig, fname))
            if progress:
                progress.update()
        if progress and getattr(progress, 'cancelled', False):
            break
    if progress and not getattr(progress, 'cancelled', False):
        progress.destroy()
    elif progress and getattr(progress, 'cancelled', False):
        plt.close('all')
        print('Cancelled.')
        return

    if do_show:
        plt.show()
    else:
        plt.close('all')

    if not SAVE_PLOTS and plots and QtWidgets.QApplication.instance() is not None:
        reply = QtWidgets.QMessageBox.question(
            None,
            "Save Plots",
            "Save generated plots?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            out = QtWidgets.QFileDialog.getExistingDirectory(None, "Select output directory", str(OUTPUT_DIR))
            if out:
                os.makedirs(out, exist_ok=True)
                for fig, fname in plots:
                    fig.savefig(os.path.join(out, fname), dpi=300)

    print(f'Done: processed {total} plots.')


class ProgressDialog:
    """Fallback progress indicator used when no GUI is provided."""

    def __init__(self, total: int):
        self.total = total
        self.count = 0
        self.cancelled = False
        self.root = self

    def update(self) -> None:
        self.count += 1

    def destroy(self) -> None:
        pass
