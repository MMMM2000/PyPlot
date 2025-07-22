#!/usr/bin/env python3
import re, glob
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
import matplotlib.ticker as mticker
import os

# ==============================================================================
#                            USER CONFIGURATION
#
# 1) Directory where your data lives (absolute or relative):
#      e.g. DATA_DIR = "/Users/martin/Library/CloudStorage/GoogleDrive-.../python plot"
#           or DATA_DIR = "data_files"
#
# 2) File-glob for your data files inside that folder:
#      GLOB_PATTERN = "*.txt"
#
# 3) Pick which variables to plot (uncomment to enable):
#      PLOT_SUM = True    # T1 + T2
#      PLOT_DT  = True    # T2 – T1
#      PLOT_T1  = True
#      PLOT_T2  = True
#
DATA_DIR      = "/Users/martin/Library/CloudStorage/GoogleDrive-elias@rvmagnetics.com/My Drive/1 Projects/python plot"
GLOB_PATTERN = "FeSiBP 188_1 s2b 68mA *.txt"

PLOT_SUM = True
PLOT_DT  = True
PLOT_T1  = True
PLOT_T2  = True

# build the list automatically
PLOT_VARS = []
if PLOT_SUM: PLOT_VARS.append("sum")
if PLOT_DT:  PLOT_VARS.append("dT")
if PLOT_T1:  PLOT_VARS.append("T1")
if PLOT_T2:  PLOT_VARS.append("T2")

# 4) Raw‐data styling
RAW_COLORS       = {"a": "C0", "b": "C1"}
RAW_MARKER       = "o"
RAW_MARKER_SIZE  = 0.3
RAW_ALPHA        = 0.4

# 5) Mean‐curve styling
MEAN_COLORS      = {"a": "red",   "b": "green"}
MEAN_MARKER      = "o"
MEAN_MARKER_SIZE = 8
MEAN_LINEWIDTH   = 3

# 6) Spread/cloud parameters
OFFSET      = 0.5    # shift loading left, unloading right
JITTER_SPAN = 0.5    # ± random jitter around each center

# 7) Print counts per (dir,load)?
PRINT_COUNTS = False
# ==============================================================================
#                       END USER CONFIGURATION

# -------------------------------------------------------------------
# Filename metadata regex: composition, title, sample_end, anneal,
# load (with comma), dir (a/b)
FNAME_RE = re.compile(
    r"^(?P<composition>.+?)\s+"
    r"(?P<title>\S+)\s+"
    r"(?P<sample_end>s\d+[ab])\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<load>\d+(?:,\d+)?)"
    r"(?P<dir>[ab])$"
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
    # convert comma to dot and to float
    md["load"] = float(md["load"].replace(",", "."))
    return md

def load_data(data_dir, pattern):
    # Glob inside the specified directory
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
        # attach metadata columns
        for k, v in md.items():
            df[k] = v
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)

def plot_variable(df, var):
    # metadata for title
    comp, title, samp, anneal = (
        df["composition"].iat[0],
        df["title"].iat[0],
        df["sample_end"].iat[0],
        df["anneal"].iat[0],
    )

    # 1) compute raw-point centers + jitter
    df["x_center"] = df["load"] + df["dir"].map({"a": -OFFSET, "b": +OFFSET})
    np.random.seed(0)
    df["x"] = df["x_center"] + np.random.uniform(-JITTER_SPAN, JITTER_SPAN, len(df))

    # 2) compute baseline from first ascending load
    first_load = df["load"].min()
    baseline = df.loc[(df["dir"]=="a") & (df["load"]==first_load), var].mean()
    # shift all data by that baseline
    df["y"] = df[var] - baseline

    if PRINT_COUNTS:
        print(f"\nCounts for {var} at {comp} {title} {samp} {anneal}:")
        print(df.groupby(["dir","load"]).size().unstack(fill_value=0))

    # 3) start figure
    plt.figure(figsize=(9,5))

    # 4) scatter raw loading/unloading
    plt.scatter(
        df.loc[df.dir=="a","x"],
        df.loc[df.dir=="a","y"],
        color=RAW_COLORS["a"],
        marker=RAW_MARKER,
        s=RAW_MARKER_SIZE,
        alpha=RAW_ALPHA,
        label="raw loading (a)"
    )
    plt.scatter(
        df.loc[df.dir=="b","x"],
        df.loc[df.dir=="b","y"],
        color=RAW_COLORS["b"],
        marker=RAW_MARKER,
        s=RAW_MARKER_SIZE,
        alpha=RAW_ALPHA,
        label="raw unloading (b)"
    )

    # 5) compute & plot mean curves
    means = df.groupby(["dir","load"])[var].mean().reset_index()
    # apply the same baseline shift
    means["y"] = means[var] - baseline

    plt.plot(
        means.loc[means.dir=="a","load"],
        means.loc[means.dir=="a","y"],
        MEAN_MARKER + "-",
        color=MEAN_COLORS["a"],
        markersize=MEAN_MARKER_SIZE,
        linewidth=MEAN_LINEWIDTH,
        label="mean loading"
    )
    plt.plot(
        means.loc[means.dir=="b","load"],
        means.loc[means.dir=="b","y"],
        MEAN_MARKER + "-",
        color=MEAN_COLORS["b"],
        markersize=MEAN_MARKER_SIZE,
        linewidth=MEAN_LINEWIDTH,
        label="mean unloading"
    )

    # 6) annotate delta between first & last ascending-load means
    max_load = df["load"].max()
    delta = means.loc[(means["dir"]=="a") & (means["load"]==max_load), "y"].iloc[0]
    ax = plt.gca()
    ax.text(
        0.95, 0.05, f"Δ = {delta:.2f} µs",
        transform=ax.transAxes,
        ha="right", va="bottom",
        fontsize=12,
        bbox=dict(facecolor="white", alpha=0.6, edgecolor="none")
    )

    # 7) format axes & title
    ax.set_xticks(sorted(df["load"].unique()))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, pos: f"{x:g}"))
    plt.xlabel("Applied load (g)")
    plt.ylabel(LABELS[var])
    plt.title(f"{comp} {title} {samp} {anneal} — {LABELS[var]} vs load")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    # no plt.show() here; we'll call it once at the end

def main():
    # load from your chosen directory + pattern
    data = load_data(DATA_DIR, GLOB_PATTERN)
    for var in PLOT_VARS:
        if var not in LABELS:
            print(f"⚠️ Unknown var '{var}', skipping.")
            continue
        plot_variable(data, var)
    # finally pop up all figures at once
    plt.show()

if __name__ == "__main__":
    main()
