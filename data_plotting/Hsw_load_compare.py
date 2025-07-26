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


def ask_options() -> Dict[str, tk.BooleanVar]:
    win = tk.Tk()
    win.title("Hsw Load Compare Settings")

    cfg = {
        "TT": tk.BooleanVar(win, True),
        "HH": tk.BooleanVar(win, True),
        "raw": tk.BooleanVar(win, False),
        "hist": tk.BooleanVar(win, False),
    }
    ttk.Checkbutton(win, text="Plot TT", variable=cfg["TT"]).grid(row=0, column=0, sticky="w", padx=5)
    ttk.Checkbutton(win, text="Plot HH", variable=cfg["HH"]).grid(row=1, column=0, sticky="w", padx=5)
    ttk.Checkbutton(win, text="Show raw", variable=cfg["raw"]).grid(row=2, column=0, sticky="w", padx=5)
    ttk.Checkbutton(win, text="Show histograms", variable=cfg["hist"]).grid(row=3, column=0, sticky="w", padx=5)

    def on_run() -> None:
        win.destroy()

    ttk.Button(win, text="Run", command=on_run).grid(row=4, column=0, pady=10)
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
    vals_tt = df["TTn"].values
    vals_hh = df["HHn"].values

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
        "TT": {"centers": centers, "counts": cnt_tt, "pdf": pdf_tt, "dh": dh},
        "HH": {"centers": centers, "counts": cnt_hh, "pdf": pdf_hh, "dh": dh},
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
    )

    raw["TTn0"] = raw["TT"] / raw["TT"].max()
    raw["HHn0"] = raw["HH"] / raw["HH"].max()

    m_t, _, _ = core_mask(raw["TTn0"].values, CORE_BINS, CORE_MIN)
    m_h, _, _ = core_mask(raw["HHn0"].values, CORE_BINS, CORE_MIN)
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
    for md, raw, filt, _ in records:
        load = md["load"]
        hist = build_histograms(filt)
        hist_data[load] = hist
        pdf_ymax = max(pdf_ymax, hist["TT"]["pdf"].max(), hist["HH"]["pdf"].max())
        hist_ymax = max(hist_ymax, hist["TT"]["counts"].max(), hist["HH"]["counts"].max())
        raw_ymax = max(raw_ymax, raw[["TT", "HH"]].to_numpy(dtype=float).max())

    loads = sorted(hist_data.keys())
    nrows = len(loads)

    # ------------------------------------------------------------------
    # Probability density plots
    # ------------------------------------------------------------------
    fig_pdf, ax_pdf = plt.subplots(nrows=nrows, ncols=1, sharex=True, figsize=(7, 2.0 * nrows))
    if nrows == 1:
        ax_pdf = [ax_pdf]
    for ax, load in zip(ax_pdf, loads):
        data = hist_data[load]
        if cfg["TT"].get():
            ax.plot(data["TT"]["centers"], data["TT"]["pdf"], label="TT")
        if cfg["HH"].get():
            ax.plot(data["HH"]["centers"], data["HH"]["pdf"], label="HH")
        ax.set_ylim(0, pdf_ymax * 1.05)
        ax.set_xlim(0, 1)
        ax.grid(True, linestyle="--", alpha=0.3)
        ax.text(0.02, 0.85, f"{load:g} g", transform=ax.transAxes, va="top")
        if cfg["TT"].get() and cfg["HH"].get():
            ax.legend(fontsize="small")
    ax_pdf[-1].set_xlabel("h = H/Hsw,max")
    ax_pdf[0].set_ylabel("dp/dh")
    ax_pdf[0].set_title("Hsw probability density vs load")
    plt.tight_layout()

    # ------------------------------------------------------------------
    # Histogram plots
    # ------------------------------------------------------------------
    if cfg["hist"].get():
        fig_h, ax_h = plt.subplots(nrows=nrows, ncols=1, sharex=True, figsize=(7, 2.0 * nrows))
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
            ax.set_ylim(0, hist_ymax * 1.05)
            ax.set_xlim(0, 1)
            ax.grid(True, linestyle="--", alpha=0.3)
            ax.text(0.02, 0.85, f"{load:g} g", transform=ax.transAxes, va="top")
            if cfg["TT"].get() and cfg["HH"].get():
                ax.legend(fontsize="small")
        ax_h[-1].set_xlabel("h = H/Hsw,max")
        ax_h[0].set_ylabel("Counts")
        ax_h[0].set_title("Histogram of Hsw vs load")
        plt.tight_layout()

    # ------------------------------------------------------------------
    # Raw data plots
    # ------------------------------------------------------------------
    if cfg["raw"].get():
        fig_r, ax_r = plt.subplots(nrows=nrows, ncols=1, sharex=True, figsize=(7, 2.0 * nrows))
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
            ax.set_ylim(0, raw_ymax * 1.05)
            ax.grid(True, linestyle="--", alpha=0.3)
            ax.text(0.02, 0.85, f"{load:g} g", transform=ax.transAxes, va="top")
            if cfg["TT"].get() and cfg["HH"].get():
                ax.legend(fontsize="x-small")
        ax_r[-1].set_xlabel("Index")
        ax_r[0].set_ylabel("Switching Field")
        ax_r[0].set_title("Raw Hsw vs load (Histogram-Core filtered)")
        plt.tight_layout()

    plt.show()


if __name__ == "__main__":
    main()
