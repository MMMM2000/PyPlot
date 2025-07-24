#!/usr/bin/env python3
import re, glob, os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
import matplotlib.ticker as mticker

# ======================================================================
#                            USER CONFIGURATION
#
# 1) DATA_DIR: where your .txt files live
DATA_DIR       = "G:/Shared drives/Projekty/VAIA/WP1 - MicroWire Development/stress depencence/data"

# 2) OUTPUT_DIR: where to save plot images
OUTPUT_DIR     = "G:/Shared drives/Projekty/VAIA/WP1 - MicroWire Development/stress depencence/plots/FeSiBP 156_2 74mA"

# 3) GLOB_PATTERN: wildcard pattern to select your files
#    Filenames must follow:
#      <composition> <title> <sample_end> <anneal> <load><dir>.txt
#    e.g. FeSiBP 188_1 s4-2a 68mA 10a.txt
#         FeSiBP 188_1 s*-* 68mA *.txt
GLOB_PATTERN   = "FeSiBP 156_2 s2-* 74mA *.txt"

# 4) Variables to plot
PLOT_SUM       = True    # T1+T2
PLOT_DT        = True    # T2–T1
PLOT_T1        = True
PLOT_T2        = True
PLOT_VARS = []
if PLOT_SUM:  PLOT_VARS.append("sum")
if PLOT_DT:   PLOT_VARS.append("dT")
if PLOT_T1:   PLOT_VARS.append("T1")
if PLOT_T2:   PLOT_VARS.append("T2")

# 5) Baseline mode: 'first' to zero first ascending load,
#                  'min' to zero lowest ascending-load mean
BASELINE_MODE  = 'first'  # options: 'first', 'min'

# 6) Styling raw data
RAW_COLORS     = {"a":"#45A1D6","b":"#F09C67"}
RAW_MARKER     = 'o'
RAW_MARKER_SIZE= 0.3
RAW_ALPHA      = 1.0

# 7) Styling mean curves
MEAN_COLORS    = {"a":"#00306E","b":"#965308"}
MEAN_MARKER    = 'o'
MEAN_MSIZE     = 8
MEAN_LW        = 3

# 8) Cloud spread
OFFSET         = 0.5
JITTER_SPAN    = 0.5

# 9) Print counts per (dir,load)?
PRINT_COUNTS   = False

# 10) Processed (median + moving avg)
PLOT_PROCESSED = False
MED_WINDOW     = 5
MA_WINDOW      = 20
PROC_COLORS    = {"a":"#E69F00","b":"#56B4E9"}
PROC_MARKER    = 's'
PROC_MSIZE     = 0.5
PROC_ALPHA     = 0.5

# 11) Output options
SHOW_PLOTS     = True     # True = display interactively
SAVE_PLOTS     = True    # True = save PNG files
MAX_SHOW       = 8        # if total plots > MAX_SHOW, only show
# ======================================================================

# Filename metadata regex
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
        for k, v in md.items():
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
    # baseline
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
    # raw scatter
    ax.scatter(df.loc[df.dir=='a','x'], df.loc[df.dir=='a','y'],
               c=RAW_COLORS['a'], marker=RAW_MARKER,
               s=RAW_MARKER_SIZE, alpha=RAW_ALPHA, label='raw ↑')
    ax.scatter(df.loc[df.dir=='b','x'], df.loc[df.dir=='b','y'],
               c=RAW_COLORS['b'], marker=RAW_MARKER,
               s=RAW_MARKER_SIZE, alpha=RAW_ALPHA, label='raw ↓')

    # processed
    if PLOT_PROCESSED:
        desc = f"med{MED_WINDOW}+mwa{MA_WINDOW}"
        for d in ('a','b'):
            sub = df[df.dir==d].sort_values('x').copy()
            sub['y_med'] = sub['y'].rolling(MED_WINDOW, center=True, min_periods=1).median()
            sub['y_proc']= sub['y_med'].rolling(MA_WINDOW, center=True, min_periods=1).mean()
            ax.scatter(sub['x'], sub['y_proc'],
                       c=PROC_COLORS[d], marker=PROC_MARKER,
                       s=PROC_MSIZE, alpha=PROC_ALPHA,
                       label=f"{desc} {'↑' if d=='a' else '↓'}")

    # mean curves
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
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,pos: f"{x:g}"))
    ax.set_xlabel('Applied load (g)')
    ax.set_ylabel(LABELS[var])
    ax.set_title(f"{comp} {title} {samp} {anneal} — {LABELS[var]}")
    ax.grid(True)
    legend = ax.legend(loc='best')
    for text, handle in zip(legend.get_texts(), legend.legend_handles):
        if hasattr(handle, 'get_color'):
            color = handle.get_color()
        elif hasattr(handle, 'get_facecolor'):
            color = handle.get_facecolor()
        else:
            color = 'black'
        if isinstance(color, np.ndarray):
            # use the first color if multiple are returned
            if color.ndim > 1:
                color = color[0]
        text.set_color(color)
    fig.tight_layout()

    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        fname = f"{comp} {title} {samp} {anneal} {var}.png"
        fig.savefig(os.path.join(out_dir, fname), dpi=300)
    return fig

def main():
    data = load_data(DATA_DIR, GLOB_PATTERN)
    groups = data.groupby(['composition','title','sample_end','anneal'])
    total = len(groups) * len(PLOT_VARS)
    do_show = SHOW_PLOTS and (total <= MAX_SHOW)
    if SHOW_PLOTS and not do_show:
        print(f"Too many plots ({total}); only saving to '{OUTPUT_DIR}'.")

    figs = []
    for _, grp in groups:
        for var in PLOT_VARS:
            fig = plot_variable(grp, var, SAVE_PLOTS, OUTPUT_DIR)
            figs.append(fig)

    if do_show:
        plt.show()
    else:
        plt.close('all')

    print(f"Done: processed {total} plots.")

if __name__ == '__main__':
    main()
