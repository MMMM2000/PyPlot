import os
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple

from PyQt6 import QtWidgets

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.collections import PathCollection
from matplotlib.colors import to_hex

from ..config import load_config
from ..common import maybe_handle_outliers

_CFG = load_config().get("stress_sensitivity", {})
OUTPUT_DIR = _CFG.get("OUTPUT_DIR", os.getcwd())
PLOT_SUM = bool(_CFG.get("PLOT_SUM", True))
PLOT_DT = bool(_CFG.get("PLOT_DT", True))
PLOT_T1 = bool(_CFG.get("PLOT_T1", True))
PLOT_T2 = bool(_CFG.get("PLOT_T2", True))
PLOT_VARS = [v for v, b in [("sum", PLOT_SUM), ("dT", PLOT_DT), ("T1", PLOT_T1), ("T2", PLOT_T2)] if b]

INCLUDE_DEPENDENCE = bool(_CFG.get("INCLUDE_DEPENDENCE", True))
MED_WINDOW = int(_CFG.get("MED_WINDOW", 5))
MA_WINDOW = int(_CFG.get("MA_WINDOW", 20))
SHOW_PLOTS = bool(_CFG.get("SHOW_PLOTS", True))
SAVE_PLOTS = bool(_CFG.get("SAVE_PLOTS", False))
MAX_SHOW = 8

BASE_LOAD = float(_CFG.get("BASE_LOAD", 2.5))
END_LOAD = float(_CFG.get("END_LOAD", 17.5))

RAW_COLORS = {BASE_LOAD: "#45A1D6", END_LOAD: "#F09C67"}
RAW_MARKER = "o"
RAW_MARKER_SIZE = 0.3
RAW_ALPHA = 1.0
MEAN_COLORS = {BASE_LOAD: "#00306E", END_LOAD: "#965308"}
MEAN_MARKER = 'o'
MEAN_MSIZE = 8
MEAN_LW = 3
OFFSET = 0.25
JITTER_SPAN = 0.25
MEAN_SHIFT = OFFSET * 2

FNAME_RE = re.compile(
    r"^(?P<composition>.+?)\s+"
    r"(?P<title>\S+)\s+"
    r"(?P<sample_end>s\d+(?:-\d+)?[ab])\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<load>\d+(?:,\d+)?)(?P<dir>[ab])$"
)


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


def plot_variable(df: pd.DataFrame, var: str, save_flag: bool, out_dir: str) -> Tuple[plt.Figure, str]:
    comp = df['composition'].iat[0]
    title = df['title'].iat[0]
    anneal = df['anneal'].iat[0]
    sample = df['sample_end'].iat[0]

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
        c='black',
        markersize=MEAN_MSIZE,
        linewidth=MEAN_LW,
        label='means',
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
    ax.plot([1, 1], [0, delta], color='black', linewidth=1, zorder=0)
    ax.annotate(f"{delta:.1f}", (0.9, delta + delta_offset), ha='right', va='center', fontsize=10)

    ax.set_xticks([1])
    ax.set_xticklabels([sample])
    ax.set_xlabel('Sample')
    ax.set_ylabel(var)
    ax.set_title(f"{comp} {title} {sample} {anneal} — {var}")
    ax.grid(True)

    legend = ax.legend(loc='best')
    for text, handle in zip(legend.get_texts(), legend.legend_handles):
        if isinstance(handle, Line2D):
            rawcol = handle.get_color()
        elif isinstance(handle, (Patch, PathCollection)):
            rawcol = handle.get_facecolor()
            if isinstance(rawcol, np.ndarray) and rawcol.ndim > 1:
                rawcol = rawcol[0]
        else:
            rawcol = 'black'
        text.set_color(to_hex(rawcol))

    fig.tight_layout()
    fname = f"{comp} {title} {sample} {anneal} {var}.png"
    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        fig.savefig(os.path.join(out_dir, fname), dpi=300)
    return fig, fname


def main(files: List[str]) -> None:
    data = load_data(files)
    data = maybe_handle_outliers(data)
    groups = data.groupby(['composition', 'title', 'sample_end', 'anneal'])
    total = len(groups) * len(PLOT_VARS)
    do_show = SHOW_PLOTS and (total <= MAX_SHOW)
    if SHOW_PLOTS and not do_show:
        print(f"Too many plots ({total}); only saving to '{OUTPUT_DIR}'.")

    progress = ProgressDialog(total) if total else None
    plots: List[Tuple[plt.Figure, str]] = []
    for _, grp in groups:
        for var in PLOT_VARS:
            if progress and getattr(progress, 'cancelled', False):
                break
            fig, fname = plot_variable(grp, var, SAVE_PLOTS, OUTPUT_DIR)
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
