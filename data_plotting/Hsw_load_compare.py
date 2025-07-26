#!/usr/bin/env python3
"""Compare Hsw distributions across loads.

This script reads ascending-load measurement files following the naming
scheme used by ``stress_dependence_plot.py`` and plots probability
density curves stacked by load.  The same Histogram-Core filtering and
probability calculations used by ``Hsw_distribution_plot.py`` are
applied.  Optionally raw data and counts histograms can also be
displayed.  TT and HH curves may be toggled individually.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog, ttk

# ----------------------------------------------------------------------
# Utility to robustly parse numbers with different grouping/decimal separators
# ----------------------------------------------------------------------
def parse_float_str(x: str) -> float:
    s = x.strip().replace(" ", "")
    comma_count = s.count(',')
    dot_count = s.count('.')
    # Both comma and dot present: decide which is decimal separator by last occurrence
    if comma_count and dot_count:
        if s.rfind(',') > s.rfind('.'):
            decimal_sep, group_sep = ',', '.'
        else:
            decimal_sep, group_sep = '.', ','
        # Remove grouping separators, then normalize decimal
        s = s.replace(group_sep, '')
        if decimal_sep != '.':
            s = s.replace(decimal_sep, '.')
    # Only comma present: assume decimal if single, grouping if multiple
    elif comma_count == 1 and dot_count == 0:
        s = s.replace(',', '.')
    elif dot_count > 1 and comma_count == 0:
        # multiple dots but no comma: assume grouping separators
        s = s.replace('.', '')
    # Otherwise: single dot decimal or no separators; leave as-is
    return float(s)

# ----------------------------------------------------------------------
# Filename metadata (copied from stress_dependence_plot.py)
# ----------------------------------------------------------------------
FNAME_RE = re.compile(
    r"^(?P<comp>.+?)\s+"
    r"(?P<title>\S+)\s+"
    r"(?P<sample_end>s\d+(?:-\d+)?[ab])\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<load>\d+(?:,\d+)?)(?P<dir>[ab])$"
)

# Histogram-Core defaults (same as Hsw_distribution_plot.py)
CORE_BINS = 50
CORE_MIN = 3

# Default output behaviour
OUTPUT_DIR = Path.cwd()
SHOW_PLOTS = True
SAVE_PLOTS = False
SAME_HIST_Y = True


# ----------------------------------------------------------------------
# GUI helpers
# ----------------------------------------------------------------------

def ask_files() -> List[str]:
    root = tk.Tk()
    root.withdraw()
    paths = filedialog.askopenfilenames(
        title="Select Hsw measurement files",
        filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
    )
    root.destroy()
    if not paths:
        sys.exit("No files selected.")
    return list(paths)


def ask_options() -> Dict[str, tk.Variable]:
    win = tk.Tk()
    win.title("Hsw Load Compare Settings")

    cfg = {
        "TT": tk.BooleanVar(win, True),
        "HH": tk.BooleanVar(win, True),
        "raw": tk.BooleanVar(win, False),
        "hist": tk.BooleanVar(win, False),
        "share_y": tk.BooleanVar(win, SAME_HIST_Y),
        "show": tk.BooleanVar(win, SHOW_PLOTS),
        "save": tk.BooleanVar(win, SAVE_PLOTS),
        "out_dir": tk.StringVar(win, str(OUTPUT_DIR)),
    }

    plot_frame = ttk.LabelFrame(win, text="Plots")
    plot_frame.grid(row=0, column=0, padx=10, pady=5, sticky="n")
    ttk.Checkbutton(plot_frame, text="Plot TT", variable=cfg["TT"]).grid(row=0, column=0, sticky="w")
    ttk.Checkbutton(plot_frame, text="Plot HH", variable=cfg["HH"]).grid(row=1, column=0, sticky="w")
    ttk.Checkbutton(plot_frame, text="Show raw", variable=cfg["raw"]).grid(row=2, column=0, sticky="w")
    ttk.Checkbutton(plot_frame, text="Show histograms", variable=cfg["hist"]).grid(row=3, column=0, sticky="w")
    ttk.Checkbutton(plot_frame, text="Same hist Y", variable=cfg["share_y"]).grid(row=4, column=0, sticky="w")

    out_frame = ttk.LabelFrame(win, text="Output")
    out_frame.grid(row=0, column=1, padx=10, pady=5, sticky="n")
    ttk.Checkbutton(out_frame, text="Show plots", variable=cfg["show"]).grid(row=0, column=0, sticky="w")
    ttk.Checkbutton(out_frame, text="Save plots", variable=cfg["save"]).grid(row=1, column=0, sticky="w")
    ttk.Label(out_frame, text="Directory:").grid(row=2, column=0, sticky="w")
    ttk.Entry(out_frame, textvariable=cfg["out_dir"], width=25).grid(row=3, column=0, sticky="w")

    def browse() -> None:
        d = filedialog.askdirectory(title="Select output directory", initialdir=cfg["out_dir"].get())
        if d:
            cfg["out_dir"].set(d)

    ttk.Button(out_frame, text="Browse", command=browse).grid(row=3, column=1, padx=2)

    def on_run() -> None:
        win.destroy()

    ttk.Button(win, text="Run", command=on_run).grid(row=1, column=0, columnspan=2, pady=10)
    win.mainloop()
    return cfg


# ----------------------------------------------------------------------
# Histogram-Core utilities
# ----------------------------------------------------------------------

def core_mask(values: np.ndarray, n_bins: int, min_count: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    counts, edges = np.histogram(values, bins=n_bins, range=(values.min(), values.max()))
    dense = np.flatnonzero(counts > min_count)
    if dense.size == 0:
        mask = np.ones_like(values, dtype=bool)
    else:
        lo, hi = dense[0], dense[-1]
        idxs = np.minimum(np.searchsorted(edges, values) - 1, len(counts) - 1)
        mask = (idxs >= lo) & (idxs <= hi)
    return mask, edges, counts


def find_auto_bins(vals: np.ndarray) -> int:
    hmin, hmax = vals.min(), vals.max()
    N = len(vals)
    for B in range(N, 1, -1):
        cnts, _ = np.histogram(vals, bins=B, range=(hmin, hmax))
        if np.all(cnts > 0):
            return B
    return max(2, min(50, N // 2))


def build_histograms(df: pd.DataFrame) -> Dict[str, Dict[str, np.ndarray]]:
    vals_tt = df["TTn"].to_numpy()
    vals_hh = df["HHn"].to_numpy()

    B_tt = find_auto_bins(vals_tt)
    B_hh = find_auto_bins(vals_hh)
    bins = min(B_tt, B_hh)
    hmin = min(vals_tt.min(), vals_hh.min())
    hmax = max(vals_tt.max(), vals_hh.max())

    cnt_tt, edges = np.histogram(vals_tt, bins=bins, range=(hmin, hmax))
    cnt_hh, _ = np.histogram(vals_hh, bins=edges)

    centers = 0.5 * (edges[:-1] + edges[1:])
    dh = edges[1] - edges[0]

    Ni_tt = np.cumsum(cnt_tt[::-1])[::-1]
    haz_tt = cnt_tt / (Ni_tt + 1e-12)
    pdf_tt = (haz_tt / dh) / (haz_tt.sum() + 1e-12)

    Ni_hh = np.cumsum(cnt_hh[::-1])[::-1]
    haz_hh = cnt_hh / (Ni_hh + 1e-12)
    pdf_hh = (haz_hh / dh) / (haz_hh.sum() + 1e-12)

    return {
        "TT": {"centers": centers, "counts": cnt_tt, "dp": pdf_tt, "dh": dh},
        "HH": {"centers": centers, "counts": cnt_hh, "dp": pdf_hh, "dh": dh},
    }


# ----------------------------------------------------------------------
# Data loading and preprocessing
# ----------------------------------------------------------------------

def parse_metadata(stem: str):
    m = FNAME_RE.match(stem)
    if not m:
        return None
    md = m.groupdict()
    md["load"] = float(md["load"].replace(",", "."))
    return md


def load_file(path: str):
    md = parse_metadata(Path(path).stem)
    if not md or md["dir"] != "a":
        return None, None

    raw = pd.read_csv(
        path,
        sep=";",
        header=None,
        names=["TT", "HH"],
        usecols=[0, 1],
        engine="python",
        on_bad_lines="skip",
        converters={
            "TT": parse_float_str,
            "HH": parse_float_str,
        },
    )
    raw.dropna(subset=["TT", "HH"], inplace=True)

    raw["TTn0"] = raw["TT"] / raw["TT"].max()
    raw["HHn0"] = raw["HH"] / raw["HH"].max()

    m_t, _, _ = core_mask(raw["TTn0"].to_numpy(), CORE_BINS, CORE_MIN)
    m_h, _, _ = core_mask(raw["HHn0"].to_numpy(), CORE_BINS, CORE_MIN)
    mask = m_t & m_h

    filtered = raw.loc[mask, ["TT", "HH"]].reset_index(drop=True)
    filtered["TTn"] = filtered["TT"] / filtered["TT"].max()
    filtered["HHn"] = filtered["HH"] / filtered["HH"].max()

    return md, raw, filtered, mask


# ----------------------------------------------------------------------
# Main plotting logic
# ----------------------------------------------------------------------

def main() -> None:
    paths = ask_files()
    cfg = ask_options()

    # pull frequently used options
    cfg_show = cfg["show"].get()
    cfg_save = cfg["save"].get()
    cfg["out_dir"].set(Path(cfg["out_dir"].get()).expanduser().as_posix())

    records = []
    for p in paths:
        md, raw, filtered, mask = load_file(p)
        if md is None:
            print(f"Skipping {p}")
            continue
        records.append((md, raw, filtered, mask))

    if not records:
        sys.exit("No valid ascending-load files selected.")

    # sort ascending by load
    records.sort(key=lambda t: t[0]["load"])

    # build histograms
    hist_data = {}
    pdf_ymax = 0.0
    hist_ymax = 0.0
    raw_ymax = 0.0
    raw_ymin = float('inf')
    for md, raw, filt, mask in records:
        load = md["load"]
        hist = build_histograms(filt)
        hist_data[load] = hist
        pdf_ymax = max(pdf_ymax, hist["TT"]["dp"].max(), hist["HH"]["dp"].max())
        hist_ymax = max(hist_ymax, hist["TT"]["counts"].max(), hist["HH"]["counts"].max())
        masked_vals = raw.loc[mask, ["TT", "HH"]].to_numpy(dtype=float)
        raw_ymax = max(raw_ymax, masked_vals.max())
        raw_ymin = min(raw_ymin, masked_vals.min())

    loads = sorted(hist_data.keys())
    nrows = len(loads)

    # Global x-axis limits for histogram and log plots
    all_centers = np.concatenate([h["centers"] for hist in hist_data.values() for h in hist.values()])
    x_min, x_max = all_centers.min(), all_centers.max()

    # ------------------------------------------------------------------
    # Log probability density plots (ln(dp/dh) vs reduced switching field)
    # ------------------------------------------------------------------
    fig_log, ax_log = plt.subplots(nrows=nrows, ncols=1, sharex=True, figsize=(7, 2.0 * nrows))
    fig_log.subplots_adjust(hspace=0)
    if nrows == 1:
        ax_log = [ax_log]
    # Compute global log-plot limits
    log_x_vals = []
    log_y_vals = []
    for load in loads:
        for col in ("TT", "HH"):
            h = hist_data[load][col]
            valid = h["dp"] > 0
            log_x_vals.append((1 - h["centers"][valid])**1.5)
            log_y_vals.append(np.log(h["dp"][valid]))
    log_x_all = np.concatenate(log_x_vals)
    log_y_all = np.concatenate(log_y_vals)
    lx_min, lx_max = 0.0, log_x_all.max()
    ly_min, ly_max = log_y_all.min(), log_y_all.max()
    # add bottom padding so curves don't overlap the axis
    ly_pad = (ly_max - ly_min) * 0.05
    ly_lower = ly_min - ly_pad
    for ax, load in zip(ax_log, loads):
        for col in ("TT", "HH"):
            if cfg[col].get():
                h = hist_data[load][col]
                valid = h["dp"] > 0
                ax.plot((1 - h["centers"][valid])**1.5,
                        np.log(h["dp"][valid]), '-o', markersize=4, label=col)
        ax.set_xlim(lx_min, lx_max)
        ax.set_ylim(ly_lower, ly_max)
        ax.grid(True, linestyle="--", alpha=0.3)
        ax.text(0.02, 0.85, f"{load:g} g", transform=ax.transAxes, va="top")
        if cfg["TT"].get() and cfg["HH"].get():
            ax.legend(fontsize="small")
    ax_log[-1].set_xlabel(r"$(1-h)^{3/2}$")
    for ax in ax_log[:-1]:
        ax.tick_params(axis="x", bottom=False, labelbottom=False)
    # Removed axis-level ylabel
    # ax_log[0].set_ylabel(r"\ln(dp/dh)")
    ax_log[0].set_title("Combined ln(dp/dh) vs reduced switching field")
    fig_log.text(0.04, 0.5, "ln(dp/dh)", va='center', rotation='vertical')
    plt.tight_layout(h_pad=0)

    # ------------------------------------------------------------------
    # Histogram plots
    # ------------------------------------------------------------------
    if cfg["hist"].get():
        fig_h, ax_h = plt.subplots(nrows=nrows, ncols=1, sharex=True, figsize=(7, 2.0 * nrows))
        fig_h.subplots_adjust(hspace=0)
        if nrows == 1:
            ax_h = [ax_h]
        for ax, load in zip(ax_h, loads):
            data = hist_data[load]
            width = data["TT"]["dh"] * 0.4
            centers = data["TT"]["centers"]
            if cfg["TT"].get():
                ax.bar(centers - width / 2, data["TT"]["counts"], width=width, label="TT", alpha=0.6)
            if cfg["HH"].get():
                ax.bar(centers + width / 2, data["HH"]["counts"], width=width, label="HH", alpha=0.6)
            if cfg["share_y"].get():
                ylim = hist_ymax
            else:
                ylim = max(data["TT"]["counts"].max(), data["HH"]["counts"].max())
            ax.set_ylim(0, ylim * 1.05)
            ax.set_xlim(x_min, x_max)
            ax.grid(True, linestyle="--", alpha=0.3)
            ax.text(0.02, 0.85, f"{load:g} g", transform=ax.transAxes, va="top")
            if cfg["TT"].get() and cfg["HH"].get():
                ax.legend(fontsize="small")
        ax_h[-1].set_xlabel("h = H/Hsw,max")
        for ax in ax_h[:-1]:
            ax.tick_params(axis="x", bottom=False, labelbottom=False)
        # Removed axis-level ylabel
        # ax_h[0].set_ylabel("Counts")
        ax_h[0].set_title("Histogram of Hsw vs load")
        fig_h.text(0.04, 0.5, "Counts", va='center', rotation='vertical')
        plt.tight_layout(h_pad=0)

    # ------------------------------------------------------------------
    # Raw data plots
    # ------------------------------------------------------------------
    if cfg["raw"].get():
        fig_r, ax_r = plt.subplots(nrows=nrows, ncols=1, sharex=True, figsize=(7, 2.0 * nrows))
        fig_r.subplots_adjust(hspace=0)
        if nrows == 1:
            ax_r = [ax_r]
        for (md, raw, _, mask), ax in zip(records, ax_r):
            load = md["load"]
            idx = np.arange(len(raw)) + 1
            ax.scatter(idx[mask], raw["TT"][mask], s=2, label="TT inlier")
            ax.scatter(idx[mask], raw["HH"][mask], s=2, label="HH inlier", color="C1")
            trimmed = ~mask
            if trimmed.any():
                ax.scatter(idx[trimmed], raw["TT"][trimmed], s=10, c="r", marker="x", label="TT trimmed")
                ax.scatter(idx[trimmed], raw["HH"][trimmed], s=10, c="m", marker="x", label="HH trimmed")
            # Removed y-limits setting to allow auto scaling
            # ax.set_ylim(raw_ymin, raw_ymax * 1.05)
            ax.grid(True, linestyle="--", alpha=0.3)
            ax.text(0.02, 0.85, f"{load:g} g", transform=ax.transAxes, va="top")
            if cfg["TT"].get() and cfg["HH"].get():
                ax.legend(fontsize="x-small")
        ax_r[-1].set_xlabel("Index")
        for ax in ax_r[:-1]:
            ax.tick_params(axis="x", bottom=False, labelbottom=False)
        fig_r.text(0.04, 0.5, "Switching Field", va='center', rotation='vertical')
        ax_r[0].set_title("Raw Hsw vs load (Histogram-Core filtered)")
        plt.tight_layout(h_pad=0)

    if cfg_save:
        out = Path(cfg["out_dir"].get())
        out.mkdir(parents=True, exist_ok=True)
        fig_log.savefig(out / "log_compare.png", dpi=300)
        if cfg["hist"].get():
            fig_h.savefig(out / "hist_compare.png", dpi=300)
        if cfg["raw"].get():
            fig_r.savefig(out / "raw_compare.png", dpi=300)

    if cfg_show:
        plt.show()
    else:
        plt.close("all")


if __name__ == "__main__":
    main()
