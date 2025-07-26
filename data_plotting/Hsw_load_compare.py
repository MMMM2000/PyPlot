#!/usr/bin/env python3
"""Compare Hsw probability density at multiple loads.

Select measurement files (.txt) following the naming scheme used by
``stress_dependence_plot.py``:

``
<composition> <title> <sample_end> <anneal> <load><dir>.txt
``

Only ascending files (ending in ``a``) are considered. Each file must contain
``TT`` and ``HH`` columns separated by semicolons. The script computes
probability density functions of the normalized switching fields and stacks the
plots from lowest to highest load for easy comparison.  An options window lets
you choose whether to plot TT, HH or both curves.
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog, ttk

# Filename metadata regex copied from ``stress_dependence_plot.py``
FNAME_RE = re.compile(
    r"^(?P<comp>.+?)\s+"
    r"(?P<title>\S+)\s+"
    r"(?P<sample_end>s\d+(?:-\d+)?[ab])\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<load>\d+(?:,\d+)?)(?P<dir>[ab])$"
)


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
    }
    ttk.Checkbutton(win, text="Plot TT", variable=cfg["TT"]).grid(
        row=0, column=0, sticky="w", padx=5
    )
    ttk.Checkbutton(win, text="Plot HH", variable=cfg["HH"]).grid(
        row=1, column=0, sticky="w", padx=5
    )

    def on_run() -> None:
        win.destroy()

    ttk.Button(win, text="Run", command=on_run).grid(row=2, column=0, pady=10)
    win.mainloop()
    return cfg


# ----------------------------------------------------------------------
# Data utilities
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
    if not md or md["dir"] != "a":  # ignore unloading
        return None, None
    df = pd.read_csv(
        path,
        sep=";",
        header=None,
        names=["TT", "HH"],
        engine="python",
        on_bad_lines="skip",
    )
    df["TTn"] = df["TT"] / df["TT"].max()
    df["HHn"] = df["HH"] / df["HH"].max()
    return md, df


def compute_pdf(values: np.ndarray, bins: np.ndarray) -> np.ndarray:
    counts, _ = np.histogram(values, bins=bins)
    Ni = np.cumsum(counts[::-1])[::-1]
    hazard = counts / (Ni + 1e-12)
    dh = bins[1] - bins[0]
    pdf = (hazard / dh) / (hazard.sum() + 1e-12)
    return pdf


# ----------------------------------------------------------------------
# Main plotting logic
# ----------------------------------------------------------------------

def main():
    paths = ask_files()
    cfg = ask_options()

    data = []
    for p in paths:
        md, df = load_file(p)
        if md is None:
            print(f"Skipping {p}")
            continue
        data.append((md, df))

    if not data:
        sys.exit("No valid ascending-load files selected.")

    # sort by load ascending
    data.sort(key=lambda tup: tup[0]["load"])

    bins = np.linspace(0.0, 1.0, 51)
    centers = 0.5 * (bins[:-1] + bins[1:])

    # compute pdfs and determine global y limit
    pdfs = {}
    y_max = 0.0
    for md, df in data:
        load = md["load"]
        pdfs[load] = {}
        if cfg["TT"].get():
            tt_pdf = compute_pdf(df["TTn"].to_numpy(), bins)
            pdfs[load]["TT"] = tt_pdf
            y_max = max(y_max, tt_pdf.max())
        if cfg["HH"].get():
            hh_pdf = compute_pdf(df["HHn"].to_numpy(), bins)
            pdfs[load]["HH"] = hh_pdf
            y_max = max(y_max, hh_pdf.max())

    loads = sorted(pdfs.keys())
    nrows = len(loads)
    fig, axes = plt.subplots(nrows=nrows, ncols=1, sharex=True, figsize=(7, 2.0 * nrows))
    if nrows == 1:
        axes = [axes]

    for ax, load in zip(axes, loads):
        if "TT" in pdfs[load]:
            ax.plot(centers, pdfs[load]["TT"], label="TT")
        if "HH" in pdfs[load]:
            ax.plot(centers, pdfs[load]["HH"], label="HH")
        ax.set_ylim(0, y_max * 1.05)
        ax.set_ylabel(f"{load:g} g")
        ax.grid(True, linestyle="--", alpha=0.3)
        if cfg["TT"].get() and cfg["HH"].get():
            ax.legend(loc="best", fontsize="small")

    axes[-1].set_xlabel("h = H/Hsw,max")
    axes[0].set_title("Hsw probability density vs load")
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
