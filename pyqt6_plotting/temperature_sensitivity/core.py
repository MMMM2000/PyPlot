import os
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple

from PyQt6 import QtWidgets

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.collections import PathCollection
from matplotlib.colors import to_hex
from matplotlib.figure import Figure

from ..config import load_config

# Load default configuration
_CFG = load_config().get("temperature_sensitivity", {})
OUTPUT_DIR = _CFG.get("OUTPUT_DIR", os.getcwd())
PLOT_SUM = bool(_CFG.get("PLOT_SUM", True))
PLOT_DT = bool(_CFG.get("PLOT_DT", True))
PLOT_T1 = bool(_CFG.get("PLOT_T1", True))
PLOT_T2 = bool(_CFG.get("PLOT_T2", True))
PLOT_VARS = [v for v, b in [("sum", PLOT_SUM), ("dT", PLOT_DT), ("T1", PLOT_T1), ("T2", PLOT_T2)] if b]
RAW_COLORS = {25: "#45A1D6", 100: "#F09C67"}
RAW_MARKER = "o"
RAW_MARKER_SIZE = 0.3
RAW_ALPHA = 1.0
MEAN_COLORS = {25: "#00306E", 100: "#965308"}
MEAN_MARKER = 'o'
MEAN_MSIZE = 8
MEAN_LW = 3
OFFSET = 0.25
JITTER_SPAN = 0.25
SHOW_PLOTS = bool(_CFG.get("SHOW_PLOTS", True))
SAVE_PLOTS = bool(_CFG.get("SAVE_PLOTS", False))
BASELINE_MODE = _CFG.get("BASELINE_MODE", "none")
if BASELINE_MODE not in {"none", "zero_25", "both"}:
    # backwards compatibility for old ZERO_25_BASELINE flag
    BASELINE_MODE = "zero_25" if bool(_CFG.get("ZERO_25_BASELINE", False)) else "none"
MAX_SHOW = 8

LABELS = {
    "T1": "T1 (µs)",
    "T2": "T2 (µs)",
    "dT": "T2–T1 (µs)",
    "sum": "T1+T2 (µs)",
}

FNAME_RE = re.compile(
    r"^(?P<composition>.+?)\s+"
    r"(?P<sample>\S+)\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<temp>\d+)C$"
)

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

def parse_metadata(stem: str) -> Dict[str, Any] | None:
    m = FNAME_RE.match(stem)
    if not m:
        return None
    md = m.groupdict()
    md["temp"] = int(md["temp"])
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
            sep=';',
            header=None,
            names=['T1','T2','dT','sum'],
            engine='python',
            on_bad_lines='skip',
        )
        df['filename'] = Path(fn).name
        df['line'] = np.arange(len(df))
        df[['T1', 'T2', 'dT', 'sum']] = df[['T1', 'T2', 'dT', 'sum']].apply(pd.to_numeric, errors='coerce')
        for k, v in md.items():
            df[k] = v
        dfs.append(df)
    if not dfs:
        raise FileNotFoundError("No valid files selected")
    return pd.concat(dfs, ignore_index=True)


def detect_outliers(df: pd.DataFrame, column: str = "sum", threshold: float = 5.0) -> pd.DataFrame:
    """Return a DataFrame of rows that are statistical outliers.

    Outliers are detected per file using the median absolute deviation (MAD)
    which is more robust against noise than the interquartile range.  Values
    whose robust z-score exceeds ``threshold`` are considered outliers.  Only
    *column* is inspected.
    """

    out_rows = []
    for fname, grp in df.groupby("filename"):
        series = grp[column].dropna()
        if series.empty:
            continue
        med = series.median()
        mad = np.median(np.abs(series - med))
        if mad == 0:
            continue
        robust_z = np.abs(series - med) / (1.4826 * mad)
        mask = robust_z > threshold
        if mask.any():
            out_rows.append(grp[mask])

    if out_rows:
        return pd.concat(out_rows, ignore_index=False)
    return pd.DataFrame(columns=df.columns)


def handle_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Check for and optionally remove outliers.

    If a ``QApplication`` is active a message box is shown asking whether to
    remove the detected outliers. Plots of the affected files are displayed with
    the outliers highlighted.  Without a running Qt application the outliers are
    removed automatically and a message is printed to stdout.
    """
    out_df = detect_outliers(df)
    if out_df.empty:
        return df

    app = QtWidgets.QApplication.instance()
    files = ", ".join(sorted(out_df["filename"].unique()))

    # Plot outliers for visual confirmation
    figs: List[Figure] = []
    for fname, grp in df.groupby("filename"):
        if fname not in out_df["filename"].values:
            continue
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.plot(grp["line"], grp["sum"], "o", ms=2, label="data")
        sub = out_df[out_df["filename"] == fname]
        ax.plot(sub["line"], sub["sum"], "ro", ms=6, label="outlier")
        ax.set_title(fname)
        ax.set_xlabel("Index")
        ax.set_ylabel("sum")
        ax.legend()
        fig.tight_layout()
        figs.append(fig)

    if app is None:
        print(f"Removing outliers from {files}.")
        plt.close("all")
        return df.drop(out_df.index)

    for fig in figs:
        fig.show()

    reply = QtWidgets.QMessageBox.question(
        None,
        "Outliers detected",
        f"Outliers detected in: {files}.\nRemove them?",
        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
    )
    plt.close("all")
    if reply == QtWidgets.QMessageBox.StandardButton.Yes:
        return df.drop(out_df.index)
    return df


def plot_variable(df: pd.DataFrame, var: str, save_flag: bool, out_dir: str, baseline_mode: str = BASELINE_MODE) -> Tuple[plt.Figure, str]:
    comp = df['composition'].iat[0]
    anneal = df['anneal'].iat[0]
    samples = sorted(df['sample'].unique())
    sample_idx = {s: i + 1 for i, s in enumerate(samples)}
    df['sample_idx'] = df['sample'].map(sample_idx).astype(float)
    means = df.groupby(['temp', 'sample_idx'])[var].mean().reset_index()
    baseline = means[means['temp'] == 25].set_index('sample_idx')[var].to_dict()
    df['x_center'] = df['sample_idx'] + df['temp'].map({25: -OFFSET, 100: OFFSET})
    np.random.seed(0)
    df['x'] = df['x_center'] + np.random.uniform(-JITTER_SPAN, JITTER_SPAN, len(df))
    if baseline_mode == "zero_25":
        df['y'] = df.apply(lambda r: r[var] - baseline.get(r['sample_idx'], 0.0), axis=1)
        means[var] = means.apply(lambda r: r[var] - baseline.get(r['sample_idx'], 0.0), axis=1)
    else:
        df['y'] = df[var]

    fig, ax = plt.subplots(figsize=(9, 5))
    for temp in sorted(df['temp'].unique()):
        sub = df[df['temp'] == temp]
        ax.scatter(
            sub['x'],
            sub['y'],
            c=RAW_COLORS.get(temp, 'gray'),
            marker=RAW_MARKER,
            s=RAW_MARKER_SIZE,
            alpha=RAW_ALPHA,
            label=f'raw {temp}\N{DEGREE SIGN}C',
        )

    for temp in sorted(df['temp'].unique()):
        m = means[means['temp'] == temp].copy()
        m_x = m['sample_idx']
        ax.plot(
            m_x,
            m[var],
            MEAN_MARKER,
            linestyle='None',
            c=MEAN_COLORS.get(temp, 'gray'),
            markersize=MEAN_MSIZE,
            label=f'mean {temp}\N{DEGREE SIGN}C',
        )

    # Connect 25°C and 100°C means per sample and show delta
    pivot = means.pivot(index='sample_idx', columns='temp', values=var)
    if 25 in pivot.columns and 100 in pivot.columns:
        for idx, row in pivot.dropna(subset=[25, 100]).iterrows():
            x = idx
            y25 = row[25]
            y100 = row[100]
            ax.plot(
                [x, x],
                [y25, y100],
                color='black',
                linewidth=1,
                zorder=0,
            )
            delta = y100 - y25
            ax.annotate(
                f"{delta:.1f}",
                (x - 0.1, (y25 + y100) / 2),
                ha='right',
                va='center',
                fontsize=10,
            )

    y_min, y_max = df['y'].min(), df['y'].max()
    y_range = y_max - y_min
    if y_range == 0:
        y_range = 1.0
    ax.set_ylim(y_min - 0.02 * y_range, y_max + 0.02 * y_range)

    ticks = [sample_idx[s] for s in samples]
    ax.set_xticks(ticks)
    ax.set_xticklabels(samples)
    ax.set_xlabel('Sample')
    ax.set_ylabel(LABELS[var])
    ax.set_title(f"{comp} {anneal} — {LABELS[var]}")
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
    fname = f"{comp} {anneal} {var}.png"
    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        fig.savefig(os.path.join(out_dir, fname), dpi=300)
    return fig, fname


def main(files: List[str]):
    data = load_data(files)
    data = handle_outliers(data)
    groups = data.groupby(['composition', 'anneal'])
    modes = [BASELINE_MODE] if BASELINE_MODE != "both" else ["none", "zero_25"]
    total = len(groups) * len(PLOT_VARS) * len(modes)
    do_show = SHOW_PLOTS and (total <= MAX_SHOW)
    if SHOW_PLOTS and not do_show:
        print(f"Too many plots ({total}); only saving to '{OUTPUT_DIR}'.")

    progress = ProgressDialog(total) if total else None
    plots: List[Tuple[plt.Figure, str]] = []
    for _, grp in groups:
        for var in PLOT_VARS:
            for mode in modes:
                if progress and getattr(progress, 'cancelled', False):
                    break
                fig, fname = plot_variable(grp, var, SAVE_PLOTS, OUTPUT_DIR, baseline_mode=mode)
                if BASELINE_MODE == "both":
                    stem, ext = os.path.splitext(fname)
                    fname = f"{stem}_{mode}{ext}"
                plots.append((fig, fname))
                if progress:
                    progress.update()
            if progress and getattr(progress, 'cancelled', False):
                break
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
