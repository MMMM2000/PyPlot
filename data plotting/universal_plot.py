#!/usr/bin/env python3
import re, glob, os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
import matplotlib.ticker as mticker

# ==============================================================================
#                            USER CONFIGURATION
#
# 1) DATA_DIR: directory where your data lives (absolute or relative):
#      e.g. "/path/to/folder"
DATA_DIR            = "G:/Shared drives/Projekty/VAIA/WP1 - MicroWire Development/stress depencence/PyQt5_jednoduchy_VCP_logger"

# 2) GLOB_PATTERN: pattern to match your .txt files inside DATA_DIR
GLOB_PATTERN        = "FeSiBP 188_1 s3-1a 68mA *.txt"

# 3) Which vars to plot (uncomment to enable)
PLOT_SUM            = True    # T1 + T2
PLOT_DT             = True    # T2 – T1
PLOT_T1             = True
PLOT_T2             = True

# Automatically builds PLOT_VARS list
PLOT_VARS = []
if PLOT_SUM:  PLOT_VARS.append("sum")
if PLOT_DT:   PLOT_VARS.append("dT")
if PLOT_T1:   PLOT_VARS.append("T1")
if PLOT_T2:   PLOT_VARS.append("T2")

# 4) Baseline mode: choose 'first' to zero the first ascending load,
#                  or 'min' to zero at the lowest ascending-load average
BASELINE_MODE = 'first'    # zero at first ascending‐load mean
# BASELINE_MODE = 'min'    # zero at lowest ascending‐load mean

# 5) Raw‐data styling
RAW_COLORS          = {"a": "#0072B2", "b": "#D55E00"}
RAW_MARKER          = "o"
RAW_MARKER_SIZE     = 0.3
RAW_ALPHA           = 1.0

# 6) Mean‐curve styling
MEAN_COLORS         = {"a": "#BA1111", "b": "#7917AA"}
MEAN_MARKER         = "o"
MEAN_MARKER_SIZE    = 8
MEAN_LINEWIDTH      = 3

# 7) Spread/cloud parameters
OFFSET              = 0.5     # shift loading left, unloading right
JITTER_SPAN         = 0.5     # ± random jitter around each center

# 8) Print counts per (dir,load)?
PRINT_COUNTS        = False

# 9) Processed‐data overlay: median + moving average
PLOT_PROCESSED      = False   # toggle processed data on/off
MEDIAN_WINDOW       = 5       # samples for median filter
MOVING_AVG_WINDOW   = 20      # samples for moving-average

# 10) Processed‐curve styling (markers only)
PROCESSED_COLORS      = {"a": "#E69F00", "b": "#56B4E9"}
PROCESSED_MARKER      = "s"
PROCESSED_MARKER_SIZE = 0.5
PROCESSED_ALPHA       = 0.5
# ==============================================================================
#                       END USER CONFIGURATION

# -------------------------------------------------------------------
# Filename metadata regex: composition, title, sample_end, anneal,
# load (with comma), dir (a/b)
FNAME_RE = re.compile(
    r"^(?P<composition>.+?)\s+"              # e.g. FeSiBP
    r"(?P<title>\S+)\s+"                     # e.g. 188_1
    r"(?P<sample_end>s\d+(?:-\d+)?[ab])\s+"  # e.g. s2-1a or s3-1b
    r"(?P<anneal>\S+)\s+"                    # e.g. 68mA
    r"(?P<load>\d+(?:,\d+)?)(?P<dir>[ab])$"   # e.g. 10a, 2,5b
)

# human-readable axis labels
LABELS = {
    "T1":  "T1 (µs)",
    "T2":  "T2 (µs)",
    "dT":  "T2 – T1 (µs)",
    "sum": "T1 + T2 (µs)"
}

def parse_metadata(stem):
    m = FNAME_RE.match(stem)
    if not m:
        return None
    md = m.groupdict()
    md["load"] = float(md["load"].replace(",", "."))
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
            print(f"Skipping unrecognized name: {fn}")
            continue
        df = pd.read_csv(fn, sep=";", header=None, names=["T1","T2","dT","sum"])
        for k, v in md.items():
            df[k] = v
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)

def plot_variable(df, var):
    comp, title, samp, anneal = (
        df["composition"].iat[0],
        df["title"].iat[0],
        df["sample_end"].iat[0],
        df["anneal"].iat[0],
    )

    # 1) compute means early for baseline
    means = df.groupby(["dir","load"])[var].mean().reset_index()
    first_load = df["load"].min()
    if BASELINE_MODE == 'first':
        baseline = means.loc[(means.dir=="a") & (means.load==first_load), var].iloc[0]
    else:  # 'min'
        baseline = means.loc[means.dir=="a", var].min()

    # 2) raw‐point centers + jitter
    df["x_center"] = df["load"] + df["dir"].map({"a": -OFFSET, "b": +OFFSET})
    np.random.seed(0)
    df["x"] = df["x_center"] + np.random.uniform(-JITTER_SPAN, JITTER_SPAN, len(df))

    # 3) shift data
    df["y"] = df[var] - baseline

    if PRINT_COUNTS:
        print(f"\nCounts for {var}, {comp} {title} {samp} {anneal}:")
        print(df.groupby(["dir","load"]).size().unstack(fill_value=0))

    plt.figure(figsize=(9,5))

    # 4) scatter raw loading/unloading
    plt.scatter(
        df.loc[df.dir=="a","x"], df.loc[df.dir=="a","y"],
        color=RAW_COLORS["a"], marker=RAW_MARKER,
        s=RAW_MARKER_SIZE, alpha=RAW_ALPHA,
        label="raw ↑"
    )
    plt.scatter(
        df.loc[df.dir=="b","x"], df.loc[df.dir=="b","y"],
        color=RAW_COLORS["b"], marker=RAW_MARKER,
        s=RAW_MARKER_SIZE, alpha=RAW_ALPHA,
        label="raw ↓"
    )

    # 5) processed overlay (markers only)
    if PLOT_PROCESSED:
        desc = f"med{MEDIAN_WINDOW}+mwa{MOVING_AVG_WINDOW}"
        for d in ("a","b"):
            sub = df[df["dir"]==d].sort_values("x").copy()
            sub["y_med"] = sub["y"].rolling(window=MEDIAN_WINDOW,
                                            center=True, min_periods=1).median()
            sub["y_proc"] = sub["y_med"].rolling(window=MOVING_AVG_WINDOW,
                                                 center=True, min_periods=1).mean()
            plt.scatter(
                sub["x"], sub["y_proc"],
                color=PROCESSED_COLORS[d],
                marker=PROCESSED_MARKER,
                s=PROCESSED_MARKER_SIZE,
                alpha=PROCESSED_ALPHA,
                label=f"{desc} {'↑' if d=='a' else '↓'}"
            )

    # 6) mean curves
    means["y"] = means[var] - baseline
    plt.plot(
        means.loc[means.dir=="a","load"], means.loc[means.dir=="a","y"],
        MEAN_MARKER+"-", color=MEAN_COLORS["a"],
        markersize=MEAN_MARKER_SIZE, linewidth=MEAN_LINEWIDTH,
        label="mean ↑"
    )
    plt.plot(
        means.loc[means.dir=="b","load"], means.loc[means.dir=="b","y"],
        MEAN_MARKER+"-", color=MEAN_COLORS["b"],
        markersize=MEAN_MARKER_SIZE, linewidth=MEAN_LINEWIDTH,
        label="mean ↓"
    )

    # 7) Δ annotation
    max_load = df["load"].max()
    delta = means.loc[(means["dir"]=="a") & (means["load"]==max_load), "y"].iloc[0]
    ax = plt.gca()
    ax.text(
        0.95, 0.05, f"Δ = {delta:.2f} µs",
        transform=ax.transAxes, ha="right", va="bottom",
        fontsize=12, bbox=dict(facecolor="white", alpha=0.6)
    )

    # 8) format & labels + legend
    ax.set_xticks(sorted(df["load"].unique()))
    ax.xaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, pos: f"{x:g}")
    )
    plt.xlabel("Applied load (g)")
    plt.ylabel(LABELS[var])
    plt.title(f"{comp} {title} {samp} {anneal} — {LABELS[var]} vs load")
    plt.grid(True)
    plt.legend(loc='best')
    plt.tight_layout()

def main():
    data = load_data(DATA_DIR, GLOB_PATTERN)
    for var in PLOT_VARS:
        if var not in LABELS:
            print(f"⚠️ Unknown var '{var}', skipping.")
            continue
        plot_variable(data, var)
    plt.show()

if __name__ == "__main__":
    main()
