import os
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple, cast

from PyQt6 import QtWidgets

from ..config import load_config
from ..utils import save_figure
from ..backends import wants_matplotlib, wants_origin

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.colors import to_hex
from matplotlib.collections import PathCollection
from matplotlib.typing import ColorType
from ..common import maybe_handle_outliers
from matplotlib.figure import Figure

# Load default configuration
_CFG = load_config().get("stress_dependence", {})
OUTPUT_DIR = _CFG.get("OUTPUT_DIR", os.getcwd())
PLOT_SUM = bool(_CFG.get("PLOT_SUM", True))
PLOT_DT = bool(_CFG.get("PLOT_DT", True))
PLOT_T1 = bool(_CFG.get("PLOT_T1", True))
PLOT_T2 = bool(_CFG.get("PLOT_T2", True))
PLOT_VARS = [
    v for v, b in [("sum", PLOT_SUM), ("dT", PLOT_DT), ("T1", PLOT_T1), ("T2", PLOT_T2)] if b
]
BASELINE_MODE = str(_CFG.get("BASELINE_MODE", "first"))  # 'first' or 'min'
RAW_COLORS = {"a": "#45A1D6", "b": "#F09C67"}
RAW_MARKER = "o"
RAW_MARKER_SIZE = 0.3
RAW_ALPHA = 1.0
MEAN_COLORS = {"a":"#00306E","b":"#965308"}
MEAN_MARKER = 'o'
MEAN_MSIZE = 8
MEAN_LW = 3
LEGEND_MARKER_SIZE = 6
OFFSET = 0.5
JITTER_SPAN = 0.5
PRINT_COUNTS = False
PLOT_PROCESSED = bool(_CFG.get("PLOT_PROCESSED", False))
MED_WINDOW = int(_CFG.get("MED_WINDOW", 5))
MA_WINDOW = int(_CFG.get("MA_WINDOW", 20))
PROC_COLORS = {"a":"#E69F00","b":"#56B4E9"}
PROC_MARKER = 's'
PROC_MSIZE = 0.5
PROC_ALPHA = 0.5
SHOW_PLOTS = bool(_CFG.get("SHOW_PLOTS", True))
SAVE_PLOTS = bool(_CFG.get("SAVE_PLOTS", False))
SAVE_FORMAT = _CFG.get("SAVE_FORMAT", "png")
PNG_DPI = int(_CFG.get("PNG_DPI", 1000))
MAX_SHOW = 8
BACKEND = str(_CFG.get("BACKEND", "matplotlib"))

# Origin styling knobs (roughly matching Matplotlib appearance)
ORIGIN_RAW_SYMBOL_SIZE = 1
ORIGIN_MEAN_SYMBOL_SIZE = 8
ORIGIN_MEAN_LINE_WIDTH = 3


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
    """Parse measurement metadata from a file name stem.

    Parameters
    ----------
    stem:
        File name without extension.

    Returns
    -------
    dict | None
        Dictionary with parsed fields or ``None`` if the pattern does not
        match.
    """
    m = FNAME_RE.match(stem)
    if not m:
        return None
    md = m.groupdict()
    md["load"] = float(md["load"].replace(",", "."))
    return md

def format_sample_end(sample_end: str) -> str:
    """Return a human readable representation of a sample end.

    The trailing ``a`` or ``b`` is mapped to ``marked`` or ``unmarked`` end
    respectively and shown in parentheses.  Other strings are returned as is.
    """
    if sample_end.endswith("a"):
        return f"{sample_end[:-1]} (marked end)"
    if sample_end.endswith("b"):
        return f"{sample_end[:-1]} (unmarked end)"
    return sample_end

def load_data(files: List[str]) -> pd.DataFrame:
    """Load measurement files into a single DataFrame."""
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
        df[["T1", "T2", "dT", "sum"]] = df[["T1", "T2", "dT", "sum"]].apply(
            pd.to_numeric, errors="coerce"
        )
        for k, v in md.items():
            df[k] = v
        dfs.append(df)
    if not dfs:
        raise ValueError("No valid files selected. Check filenames.")
    return pd.concat(dfs, ignore_index=True)

def plot_variable(df: pd.DataFrame, var: str, save_flag: bool, out_dir: str) -> Tuple[Figure, str]:
    """Plot a single variable and optionally save the figure."""
    comp, title, samp, anneal = (
        df['composition'].iat[0],
        df['title'].iat[0],
        df['sample_end'].iat[0],
        df['anneal'].iat[0],
    )

    means = df.groupby(['dir','load'])[var].mean().reset_index()
    first = df['load'].min()
    if BASELINE_MODE == 'first':
        base = means.loc[(means.dir=='a') & (means.load==first), var].iloc[0]
    else:
        base = means.loc[means.dir=='a', var].min()

    df['x_center'] = df['load'] + df['dir'].map({'a':-OFFSET,'b':+OFFSET})
    np.random.seed(0)
    df['x'] = df['x_center'] + np.random.uniform(-JITTER_SPAN, JITTER_SPAN, len(df))
    df['y'] = df[var] - base
    means['y'] = means[var] - base

    if PRINT_COUNTS:
        print(f"\nCounts for {var}, {comp} {title} {samp} {anneal}:")
        print(df.groupby(['dir','load']).size().unstack(fill_value=0))

    fig, ax = plt.subplots(figsize=(9,5))
    ax.scatter(df.loc[df.dir=='a','x'], df.loc[df.dir=='a','y'],
               c=RAW_COLORS['a'], marker=RAW_MARKER,
               s=RAW_MARKER_SIZE, alpha=RAW_ALPHA, label='raw ↑')
    ax.scatter(df.loc[df.dir=='b','x'], df.loc[df.dir=='b','y'],
               c=RAW_COLORS['b'], marker=RAW_MARKER,
               s=RAW_MARKER_SIZE, alpha=RAW_ALPHA, label='raw ↓')

    if PLOT_PROCESSED:
        desc = f"med{MED_WINDOW}+mwa{MA_WINDOW}"
        for d in ('a','b'):
            sub = df[df.dir==d].sort_values('x').copy()
            sub['y_med'] = sub['y'].rolling(MED_WINDOW, center=True, min_periods=1).median()
            sub['y_proc'] = sub['y_med'].rolling(MA_WINDOW, center=True, min_periods=1).mean()
            ax.scatter(sub['x'], sub['y_proc'],
                       c=PROC_COLORS[d], marker=PROC_MARKER,
                       s=PROC_MSIZE, alpha=PROC_ALPHA,
                       label=f"{desc} {'↑' if d=='a' else '↓'}")

    ax.plot(means.loc[means.dir=='a','load'], means.loc[means.dir=='a','y'],
            MEAN_MARKER+'-', c=MEAN_COLORS['a'],
            markersize=MEAN_MSIZE, linewidth=MEAN_LW, label='mean ↑')
    ax.plot(means.loc[means.dir=='b','load'], means.loc[means.dir=='b','y'],
            MEAN_MARKER+'-', c=MEAN_COLORS['b'],
            markersize=MEAN_MSIZE, linewidth=MEAN_LW, label='mean ↓')

    maxl = df['load'].max()
    delta = means.loc[(means.dir=='a')&(means.load==maxl),'y'].iloc[0]
    ax.text(0.95, 0.05, f"Δ={delta:.2f}µs",
            transform=ax.transAxes, ha='right', va='bottom',
            fontsize=12, bbox=dict(facecolor='white', alpha=0.6))

    ax.set_xticks(sorted(df['load'].unique()))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:g}"))
    ax.set_xlabel('Applied load (g)')
    ax.set_ylabel(LABELS[var])
    ax.set_title(f"{comp} {title} {format_sample_end(samp)} {anneal} — {LABELS[var]}")
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
    fname = f"{comp} {title} {samp} {anneal} {var}"
    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        save_figure(fig, os.path.join(out_dir, fname), SAVE_FORMAT, PNG_DPI)
    return fig, f"{fname}.{SAVE_FORMAT}"


def _origin_compute_tables(grp: pd.DataFrame, var: str):
    """Return jittered raw data and means for Origin plotting."""

    means = grp.groupby(["dir", "load"], as_index=False).agg({var: "mean"})
    first = float(means["load"].min())
    if BASELINE_MODE == "first":
        base = float(
            means.loc[(means["dir"] == "a") & (means["load"] == first), var].iloc[0]
        )
    else:
        base = float(means.loc[means["dir"] == "a", var].min())

    rng = np.random.default_rng(0)
    x_center = grp["load"] + grp["dir"].map({"a": -OFFSET, "b": +OFFSET})
    x = x_center + rng.uniform(-JITTER_SPAN, +JITTER_SPAN, size=len(grp))
    y = grp[var] - base

    raw = pd.DataFrame({"X": x, "Y": y, "dir": grp["dir"].to_numpy()})
    raw_a = raw[raw["dir"] == "a"][ ["X", "Y"] ].reset_index(drop=True)
    raw_b = raw[raw["dir"] == "b"][ ["X", "Y"] ].reset_index(drop=True)

    means = means.sort_values(["dir", "load"]).copy()
    means["X"] = means["load"]
    means["Y"] = means[var] - base
    mean_a = means[means["dir"] == "a"][ ["X", "Y"] ].reset_index(drop=True)
    mean_b = means[means["dir"] == "b"][ ["X", "Y"] ].reset_index(drop=True)
    return raw_a, raw_b, mean_a, mean_b


def _origin_title(grp: pd.DataFrame, var: str) -> str:
    comp, title, samp, anneal = (
        grp["composition"].iat[0],
        grp["title"].iat[0],
        grp["sample_end"].iat[0],
        grp["anneal"].iat[0],
    )
    return f"{comp} {title} {format_sample_end(samp)} {anneal} — {LABELS[var]}"


def _origin_build_graph(raw_a, raw_b, mean_a, mean_b, title: str, var: str) -> None:
    """Create an Origin graph mirroring the Matplotlib style."""

    import originpro as op  # Imported lazily
    # Ensure Origin UI is shown
    try:
        op.set_show()
    except Exception:
        pass

    book = op.new_book('w', lname="Stress Dependence (Python)")
    book.activate()

    def push_xy(df: pd.DataFrame, lname: str, legend_label: str):
        wks = op.new_sheet('w', lname=lname)
        wks.from_df(df)
        wks.cols_axis('XY')
        try:
            wks.activate()
            op.lt_exec(f'wks.col2.lname$ = "{legend_label}";')
        except Exception:
            pass
        return wks

    w_raw_a = push_xy(raw_a, "raw_a", "a raw")
    w_raw_b = push_xy(raw_b, "raw_b", "b raw")
    w_mean_a = push_xy(mean_a, "mean_a", "a mean")
    w_mean_b = push_xy(mean_b, "mean_b", "b mean")

    gp = op.new_graph(template='scatter')
    gl = gp[0]
    p_raw_a = gl.add_plot(w_raw_a, coly=1, colx=0, type='s')
    p_raw_b = gl.add_plot(w_raw_b, coly=1, colx=0, type='s')
    p_mean_a = gl.add_plot(w_mean_a, coly=1, colx=0, type='y')
    p_mean_b = gl.add_plot(w_mean_b, coly=1, colx=0, type='y')

    try:
        pra = p_raw_a
        prb = p_raw_b
        pma = p_mean_a
        pmb = p_mean_b
        pra.color = RAW_COLORS["a"]
        prb.color = RAW_COLORS["b"]
        pma.color = MEAN_COLORS["a"]
        pmb.color = MEAN_COLORS["b"]
        pra.symbol_size = ORIGIN_RAW_SYMBOL_SIZE
        prb.symbol_size = ORIGIN_RAW_SYMBOL_SIZE
        pma.symbol_size = ORIGIN_MEAN_SYMBOL_SIZE
        pmb.symbol_size = ORIGIN_MEAN_SYMBOL_SIZE
        try:
            pma.line_width = ORIGIN_MEAN_LINE_WIDTH
            pmb.line_width = ORIGIN_MEAN_LINE_WIDTH
        except Exception:
            pass
        # Prefer properties; avoid LT dialogs
        for plot in (pma, pmb):
            try:
                plot.symbol_shape = 2  # circle
            except Exception:
                pass
    except Exception:
        pass

    try:
        gl.rescale()
        gp.activate()
        esc = title.replace('"', "'")
        try:
            op.lt_exec('page.antialias=1;')
            op.lt_exec('layer -aa 1;')
            op.lt_exec('lab -xb "Applied load (g)";')
            op.lt_exec(f'lab -yl "{LABELS[var]}";')
            op.lt_exec(f'title -s "{esc}";')
            op.lt_exec('legend;')
            op.lt_exec('legend.textcolor=1;')
        except Exception:
            pass
    except Exception:
        pass


def plot_variable_origin(grp: pd.DataFrame, var: str) -> None:
    """Generate an Origin plot for the given variable."""

    raw_a, raw_b, mean_a, mean_b = _origin_compute_tables(grp, var)
    title = _origin_title(grp, var)
    _origin_build_graph(raw_a, raw_b, mean_a, mean_b, title, var)

def main(files: List[str], backend: str = BACKEND) -> None:
    data = load_data(files)
    data = maybe_handle_outliers(data)
    groups = data.groupby(['composition','title','sample_end','anneal'])
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
                fig, fname = plot_variable(grp, var, SAVE_PLOTS, OUTPUT_DIR)
                plots.append((fig, fname))
            if wants_origin(backend):
                try:
                    plot_variable_origin(grp, var)
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
