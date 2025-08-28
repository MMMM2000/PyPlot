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
from tqdm import tqdm

from ..config import load_config
from ..common import maybe_handle_outliers
from ..utils import save_figure
from ..backends import wants_matplotlib, wants_origin

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
PNG_DPI = int(_CFG.get("PNG_DPI", 1000))
MAX_SHOW = 8
BACKEND = str(_CFG.get("BACKEND", "matplotlib"))

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

    comp = df['composition'].iat[0]
    title = df['title'].iat[0]
    anneal = df['anneal'].iat[0]

    samples = sorted(df['sample_end'].unique())
    sample_idx = {s: i + 1 for i, s in enumerate(samples)}

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

    # Push to Origin
    book = op.new_book('w', lname="Stress Sens (Python)")
    book.activate()
    gp = op.new_graph(template='scatter')
    gl = gp[0]

    if not raw_odd.empty:
        w = op.new_sheet('w', lname='raw_odd')
        w.from_df(raw_odd)
        w.cols_axis('XY')
        p = gl.add_plot(w, coly=1, colx=0, type='s')
        try:
            p.color = RAW_ALT_COLORS[1]
        except Exception:
            pass
    if not raw_even.empty:
        w = op.new_sheet('w', lname='raw_even')
        w.from_df(raw_even)
        w.cols_axis('XY')
        p = gl.add_plot(w, coly=1, colx=0, type='s')
        try:
            p.color = RAW_ALT_COLORS[0]
        except Exception:
            pass

    for mean_df, color in mean_lines:
        w = op.new_sheet('w', lname='mean')
        w.from_df(mean_df)
        w.cols_axis('XY')
        p = gl.add_plot(w, coly=1, colx=0, type='y')
        try:
            p.color = color
            p.symbol_shape = 2
        except Exception:
            pass

    for cont_df in cont_lines:
        w = op.new_sheet('w', lname='cont')
        w.from_df(cont_df)
        w.cols_axis('XY')
        p = gl.add_plot(w, coly=1, colx=0, type='y')
        try:
            p.color = 'black'
        except Exception:
            pass

    try:
        gl.rescale()
        gp.activate()
        op.lt_exec('page.antialias=1;')
        op.lt_exec('layer -aa 1;')
        op.lt_exec('lab -xb "Sample";')
        op.lt_exec(f'lab -yl "{LABELS[var]}";')
        esc = (f"{comp} {title} {anneal} - {LABELS[var]}").replace('"', "'")
        op.lt_exec(f'title -s "{esc}";')
        op.lt_exec('legend;')
    except Exception:
        pass


def main(files: List[str], backend: str = BACKEND) -> None:
    data = load_data(files)
    data = maybe_handle_outliers(data)
    groups = data.groupby(['composition', 'title', 'anneal'])
    total = len(groups) * len(PLOT_VARS)
    do_show = SHOW_PLOTS and wants_matplotlib(backend) and (total <= MAX_SHOW)
    if SHOW_PLOTS and wants_matplotlib(backend) and not do_show:
        print(f"Too many plots ({total}); only saving to '{OUTPUT_DIR}'.")

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
                    print(f"Origin plot failed: {e}")
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

    if wants_matplotlib(backend):
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
                        base = os.path.join(out, Path(fname).stem)
                        save_figure(fig, base, SAVE_FORMAT, PNG_DPI)
    else:
        plt.close('all')

    print(f'Done: processed {total} plots.')


class ProgressDialog:
    """CLI progress bar used when no GUI is provided."""

    def __init__(self, total: int):
        self.pbar = tqdm(total=total, desc="Processing", unit="plot")
        self.cancelled = False
        self.root = self

    def update(self) -> None:
        self.pbar.update(1)

    def destroy(self) -> None:
        self.pbar.close()
