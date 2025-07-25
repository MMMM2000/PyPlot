#!/usr/bin/env python3
import os
import re
import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
import matplotlib.ticker as mticker
import tkinter as tk
from tkinter import filedialog, ttk

from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.colors import to_hex

# ======================================================================
#                            DEFAULT CONFIGURATION
# Values below are used to pre-fill the GUI and can be adjusted
# interactively when running the script.

# ======================================================================
#                            DEFAULT CONFIGURATION
# The variables below provide defaults for the GUI fields.

OUTPUT_DIR     = os.getcwd()  # output directory for saved plots

# Variables to plot
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
SAVE_PLOTS     = True     # True = save PNG files
MAX_SHOW       = 8        # if total plots > MAX_SHOW, only show
# ======================================================================

def ask_user():
    """Return (paths, cfg) gathered via Tk file dialog and options window."""
    root = tk.Tk(); root.withdraw()
    paths = filedialog.askopenfilenames(
        title="Select measurement files",
        filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
    )
    if not paths:
        sys.exit("No files selected.")
    root.destroy()

    win = tk.Tk()
    win.title("Stress Dependence Settings")

    cfg = {
        "sum":         tk.BooleanVar(win, PLOT_SUM),
        "dT":          tk.BooleanVar(win, PLOT_DT),
        "T1":          tk.BooleanVar(win, PLOT_T1),
        "T2":          tk.BooleanVar(win, PLOT_T2),
        "baseline":    tk.StringVar(win, BASELINE_MODE),
        "show":        tk.BooleanVar(win, SHOW_PLOTS),
        "save":        tk.BooleanVar(win, SAVE_PLOTS),
        "out_dir":     tk.StringVar(win, OUTPUT_DIR),
        "processed":   tk.BooleanVar(win, PLOT_PROCESSED),
        "med_window":  tk.IntVar(win, MED_WINDOW),
        "ma_window":   tk.IntVar(win, MA_WINDOW),
    }

    ttk.Label(win, text="Variables to plot:").grid(row=0, column=0, sticky='w')
    ttk.Checkbutton(win, text="T1+T2", variable=cfg["sum"]).grid(row=1, column=0, sticky='w', padx=5)
    ttk.Checkbutton(win, text="T2–T1", variable=cfg["dT"]).grid(row=2, column=0, sticky='w', padx=5)
    ttk.Checkbutton(win, text="T1", variable=cfg["T1"]).grid(row=3, column=0, sticky='w', padx=5)
    ttk.Checkbutton(win, text="T2", variable=cfg["T2"]).grid(row=4, column=0, sticky='w', padx=5)

    base = ttk.LabelFrame(win, text="Baseline")
    base.grid(row=0, column=1, rowspan=2, padx=10, pady=5, sticky='n')
    ttk.Radiobutton(base, text="First", variable=cfg["baseline"], value='first').grid(row=0, column=0, sticky='w')
    ttk.Radiobutton(base, text="Min", variable=cfg["baseline"], value='min').grid(row=1, column=0, sticky='w')

    out_frame = ttk.LabelFrame(win, text="Output")
    out_frame.grid(row=2, column=1, rowspan=3, padx=10, pady=5, sticky='n')
    ttk.Checkbutton(out_frame, text="Show plots", variable=cfg["show"]).grid(row=0, column=0, sticky='w')
    ttk.Checkbutton(out_frame, text="Save plots", variable=cfg["save"]).grid(row=1, column=0, sticky='w')

    def browse_out():
        d = filedialog.askdirectory(title="Select output directory",
                                    initialdir=cfg["out_dir"].get())
        if d:
            cfg["out_dir"].set(d)

    ttk.Label(out_frame, text="Directory:").grid(row=2, column=0, sticky='w')
    ttk.Entry(out_frame, textvariable=cfg["out_dir"], width=25).grid(row=3, column=0, sticky='w')
    ttk.Button(out_frame, text="Browse", command=browse_out).grid(row=3, column=1, padx=2)

    proc = ttk.LabelFrame(win, text="Processed curve")
    proc.grid(row=5, column=0, columnspan=2, padx=5, pady=5, sticky='we')
    ttk.Checkbutton(proc, text="Plot processed", variable=cfg["processed"]).grid(row=0, column=0, sticky='w')
    ttk.Label(proc, text="Med window:").grid(row=1, column=0, sticky='e')
    ttk.Entry(proc, textvariable=cfg["med_window"], width=6).grid(row=1, column=1, sticky='w')
    ttk.Label(proc, text="MA window:").grid(row=1, column=2, sticky='e')
    ttk.Entry(proc, textvariable=cfg["ma_window"], width=6).grid(row=1, column=3, sticky='w')

    def on_run():
        win.destroy()

    ttk.Button(win, text="Run", command=on_run).grid(row=6, column=0, columnspan=2, pady=10)
    win.mainloop()
    return paths, cfg
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

def load_data(files):
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

    # baseline correction
    means = df.groupby(['dir','load'])[var].mean().reset_index()
    first = df['load'].min()
    if BASELINE_MODE == 'first':
        base = means.loc[(means.dir=='a')&(means.load==first), var].iloc[0]
    else:
        base = means.loc[means.dir=='a', var].min()

    # jittered x positions
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

    # optional processed line
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

    # mean curves
    ax.plot(means.loc[means.dir=='a','load'], means.loc[means.dir=='a','y'],
            MEAN_MARKER+'-', c=MEAN_COLORS['a'],
            markersize=MEAN_MSIZE, linewidth=MEAN_LW, label='mean ↑')
    ax.plot(means.loc[means.dir=='b','load'], means.loc[means.dir=='b','y'],
            MEAN_MARKER+'-', c=MEAN_COLORS['b'],
            markersize=MEAN_MSIZE, linewidth=MEAN_LW, label='mean ↓')

    # annotate delta
    maxl  = df['load'].max()
    delta = means.loc[(means.dir=='a')&(means.load==maxl),'y'].iloc[0]
    ax.text(0.95, 0.05, f"Δ={delta:.2f}µs",
            transform=ax.transAxes, ha='right', va='bottom',
            fontsize=12, bbox=dict(facecolor='white', alpha=0.6))

    # axis labels & styling
    ax.set_xticks(sorted(df['load'].unique()))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,pos: f"{x:g}"))
    ax.set_xlabel('Applied load (g)')
    ax.set_ylabel(LABELS[var])
    ax.set_title(f"{comp} {title} {samp} {anneal} — {LABELS[var]}")
    ax.grid(True)

    # legend text color matching handle color (Pylance‐safe)
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

        hexcol = to_hex(rawcol)  # type: ignore[arg-type]
        text.set_color(hexcol)

    fig.tight_layout()

    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        fname = f"{comp} {title} {samp} {anneal} {var}.png"
        fig.savefig(os.path.join(out_dir, fname), dpi=300)

    return fig

def main(files):
    data   = load_data(files)
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
    paths, cfg = ask_user()

    # apply configuration
    PLOT_VARS.clear()
    if cfg['sum'].get():
        PLOT_VARS.append('sum')
    if cfg['dT'].get():
        PLOT_VARS.append('dT')
    if cfg['T1'].get():
        PLOT_VARS.append('T1')
    if cfg['T2'].get():
        PLOT_VARS.append('T2')

    BASELINE_MODE = cfg['baseline'].get()
    SHOW_PLOTS    = cfg['show'].get()
    SAVE_PLOTS    = cfg['save'].get()
    OUTPUT_DIR    = cfg['out_dir'].get()
    PLOT_PROCESSED= cfg['processed'].get()
    MED_WINDOW    = cfg['med_window'].get()
    MA_WINDOW     = cfg['ma_window'].get()

    main(paths)
