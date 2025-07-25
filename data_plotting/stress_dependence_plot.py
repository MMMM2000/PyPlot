#!/usr/bin/env python3
import re, glob, os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
import matplotlib.ticker as mticker

from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.colors import to_hex

# ======================================================================
#                            USER CONFIGURATION
#
DATA_DIR       = "G:/Shared drives/Projekty/VAIA/WP1 - MicroWire Development/stress depencence/data"
OUTPUT_DIR     = "G:/Shared drives/Projekty/VAIA/WP1 - MicroWire Development/stress depencence/plots/FeSiBP 156_2 74mA"
GLOB_PATTERN   = "FeSiBP 156_2 s2-1a 74mA *.txt"

PLOT_SUM       = True
PLOT_DT        = True
PLOT_T1        = True
PLOT_T2        = True
PLOT_VARS = []
if PLOT_SUM:  PLOT_VARS.append("sum")
if PLOT_DT:   PLOT_VARS.append("dT")
if PLOT_T1:   PLOT_VARS.append("T1")
if PLOT_T2:   PLOT_VARS.append("T2")

BASELINE_MODE  = 'first'      # or 'min'

RAW_COLORS     = {"a":"#45A1D6","b":"#F09C67"}
RAW_MARKER     = 'o'
RAW_MARKER_SIZE= 0.3
RAW_ALPHA      = 1.0

MEAN_COLORS    = {"a":"#00306E","b":"#965308"}
MEAN_MARKER    = 'o'
MEAN_MSIZE     = 8
MEAN_LW        = 3

OFFSET         = 0.5
JITTER_SPAN    = 0.5

PRINT_COUNTS   = False

PLOT_PROCESSED = False
MED_WINDOW     = 5
MA_WINDOW      = 20
PROC_COLORS    = {"a":"#E69F00","b":"#56B4E9"}
PROC_MARKER    = 's'
PROC_MSIZE     = 0.5
PROC_ALPHA     = 0.5

SHOW_PLOTS     = True
SAVE_PLOTS     = False
MAX_SHOW       = 8
# ======================================================================

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
    "sum":"T1+T2 (µs)"
}

def parse_metadata(stem):
    m = FNAME_RE.match(stem)
    if not m:
        return None
    md = m.groupdict()
    md['load'] = float(md['load'].replace(',', '.'))
    return md

def load_data(data_dir, pattern):
    search = os.path.join(data_dir, pattern)
    files = sorted(glob.glob(search))
    if not files:
        raise FileNotFoundError(f"No files match {search!r}")
    dfs = []
    for fn in files:
        md = parse_metadata(Path(fn).stem)
        if md is None:
            print(f"Skipping {fn}")
            continue
        df = pd.read_csv(
            fn, sep=';', header=None,
            names=['T1','T2','dT','sum'],
            engine='python', on_bad_lines='skip'
        )
        for k,v in md.items():
            df[k] = v
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)

def plot_variable(df, var, save_flag, out_dir):
    comp, title, samp, anneal = (
        df['composition'].iat[0],
        df['title'].iat[0],
        df['sample_end'].iat[0],
        df['anneal'].iat[0],
    )

    means = df.groupby(['dir','load'])[var].mean().reset_index()
    first = df['load'].min()
    if BASELINE_MODE == 'first':
        base = means.loc[(means.dir=='a')&(means.load==first), var].iloc[0]
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
            sub['y_med']  = sub['y'].rolling(MED_WINDOW, center=True, min_periods=1).median()
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

    maxl  = df['load'].max()
    delta = means.loc[(means.dir=='a')&(means.load==maxl),'y'].iloc[0]
    ax.text(0.95, 0.05, f"Δ={delta:.2f}µs",
            transform=ax.transAxes, ha='right', va='bottom',
            fontsize=12, bbox=dict(facecolor='white', alpha=0.6))

    ax.set_xticks(sorted(df['load'].unique()))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,pos: f"{x:g}"))
    ax.set_xlabel('Applied load (g)')
    ax.set_ylabel(LABELS[var])
    ax.set_title(f"{comp} {title} {samp} {anneal} — {LABELS[var]}")
    ax.grid(True)

    # legend with explicit isinstance + hex conversion, suppressing the arg-type warning
    legend = ax.legend(loc='best')
    for text, handle in zip(legend.get_texts(), legend.legend_handles):
        if isinstance(handle, Line2D):
            rawcol = handle.get_color()
        elif isinstance(handle, Patch):
            rawcol = handle.get_facecolor()
            if isinstance(rawcol, np.ndarray) and rawcol.ndim > 1:
                rawcol = rawcol[0]
        else:
            rawcol = 'black'

        # Pylance will now be happy
        hexcol = to_hex(rawcol)  # type: ignore[arg-type]
        text.set_color(hexcol)

    fig.tight_layout()

    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        fname = f"{comp} {title} {samp} {anneal} {var}.png"
        fig.savefig(os.path.join(out_dir, fname), dpi=300)

    return fig

def main():
    data   = load_data(DATA_DIR, GLOB_PATTERN)
    groups = data.groupby(['composition','title','sample_end','anneal'])
    total  = len(groups) * len(PLOT_VARS)
    do_show = SHOW_PLOTS and (total <= MAX_SHOW)
    if SHOW_PLOTS and not do_show:
        print(f"Too many plots ({total}); only saving to '{OUTPUT_DIR}'.")

    for _, grp in groups:
        for var in PLOT_VARS:
            plot_variable(grp, var, SAVE_PLOTS, OUTPUT_DIR)

    if do_show:
        plt.show()
    else:
        plt.close('all')

    print(f"Done: processed {total} plots.")

if __name__ == '__main__':
    main()