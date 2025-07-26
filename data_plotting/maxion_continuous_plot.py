#!/usr/bin/env python3
"""Plot Maxion continuous measurement files."""

import os
import re
import sys
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog, ttk

# ======================================================================
#                            DEFAULT CONFIGURATION
# These values pre-fill the GUI and can be adjusted interactively.
# ======================================================================
OUTPUT_DIR = os.getcwd()
SHOW_PLOTS = True
SAVE_PLOTS = True
PLOT_MODE = "both"  # options: 'raw', 'processed', 'both'
MARKER_SIZE = 0.3
MED_WINDOW = 5
MA_WINDOW = 20

# filename pattern: "<head> ... <coils> coils" (or "cievky")
NAME_RE = re.compile(r"^(?P<head>[1-6])\s.*\s(?P<coils>[23])\s(?:coils|cievky)$", re.IGNORECASE)


def parse_name(stem: str) -> Tuple[int, int]:
    """Return (head, coils) parsed from filename stem."""
    m = NAME_RE.match(stem)
    if not m:
        raise ValueError(f"Unrecognized file name: {stem}")
    return int(m.group("head")), int(m.group("coils"))


def load_file(path: str) -> pd.DataFrame:
    """Load one measurement file."""
    cols = [f"ch{i}_{t}" for i in range(1, 4) for t in ("t1", "t2")]
    return pd.read_csv(path, sep=";", header=None, names=cols, engine="python", on_bad_lines="skip")


def plot_channel(y: pd.Series, head: int, coils: int, ch: int):
    """Plot T1+T2 for a single channel."""
    fig, ax = plt.subplots(figsize=(9, 4))
    x = np.arange(len(y))

    if PLOT_MODE in ("raw", "both"):
        ax.scatter(x, y.to_numpy(), s=MARKER_SIZE, label="raw")

    if PLOT_MODE in ("processed", "both"):
        med = y.rolling(MED_WINDOW, center=True, min_periods=1).median()
        proc = med.rolling(MA_WINDOW, center=True, min_periods=1).mean()
        ax.scatter(x, proc.to_numpy(), s=MARKER_SIZE,
                   label=f"med{MED_WINDOW}+mwa{MA_WINDOW}")

    ax.set_xlabel("Sample index")
    ax.set_ylabel("T1+T2 (arb units)")
    ax.set_title(f"Head {head} — {coils} coils — CH{ch} T1+T2")
    ax.grid(True)
    ax.legend()
    fig.tight_layout()

    if SAVE_PLOTS:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        fname = f"head{head}_{coils}coils_CH{ch}_sum.png"
        fig.savefig(os.path.join(OUTPUT_DIR, fname), dpi=300)

    return fig


def ask_user():
    """Return (paths, cfg) from file dialog and options window."""
    root = tk.Tk(); root.withdraw()
    paths = filedialog.askopenfilenames(
        title="Select measurement files",
        filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
    )
    if not paths:
        sys.exit("No files selected.")
    root.destroy()

    win = tk.Tk()
    win.title("Maxion Continuous Settings")

    cfg = {
        "show": tk.BooleanVar(win, SHOW_PLOTS),
        "save": tk.BooleanVar(win, SAVE_PLOTS),
        "out_dir": tk.StringVar(win, OUTPUT_DIR),
        "mode": tk.StringVar(win, PLOT_MODE),
        "marker": tk.DoubleVar(win, MARKER_SIZE),
        "med_window": tk.IntVar(win, MED_WINDOW),
        "ma_window": tk.IntVar(win, MA_WINDOW),
    }

    out_frame = ttk.LabelFrame(win, text="Output")
    out_frame.grid(row=0, column=0, padx=10, pady=5, sticky="n")
    ttk.Checkbutton(out_frame, text="Show plots", variable=cfg["show"]).grid(row=0, column=0, sticky="w")
    ttk.Checkbutton(out_frame, text="Save plots", variable=cfg["save"]).grid(row=1, column=0, sticky="w")

    ttk.Label(out_frame, text="Directory:").grid(row=2, column=0, sticky="w")
    ttk.Entry(out_frame, textvariable=cfg["out_dir"], width=25).grid(row=3, column=0, sticky="w")

    def browse_out():
        d = filedialog.askdirectory(title="Select output directory", initialdir=cfg["out_dir"].get())
        if d:
            cfg["out_dir"].set(d)

    ttk.Button(out_frame, text="Browse", command=browse_out).grid(row=3, column=1, padx=2)

    mode = ttk.LabelFrame(win, text="Data to plot")
    mode.grid(row=0, column=1, padx=10, pady=5, sticky="n")
    ttk.Radiobutton(mode, text="Raw", variable=cfg["mode"], value="raw").grid(row=0, column=0, sticky="w")
    ttk.Radiobutton(mode, text="Processed", variable=cfg["mode"], value="processed").grid(row=1, column=0, sticky="w")
    ttk.Radiobutton(mode, text="Both", variable=cfg["mode"], value="both").grid(row=2, column=0, sticky="w")

    proc = ttk.LabelFrame(win, text="Processed curve")
    proc.grid(row=1, column=0, padx=10, pady=5, sticky="we")
    ttk.Label(proc, text="Med window:").grid(row=0, column=0, sticky="e")
    ttk.Entry(proc, textvariable=cfg["med_window"], width=6).grid(row=0, column=1, sticky="w")
    ttk.Label(proc, text="MA window:").grid(row=0, column=2, sticky="e")
    ttk.Entry(proc, textvariable=cfg["ma_window"], width=6).grid(row=0, column=3, sticky="w")

    style = ttk.LabelFrame(win, text="Scatter")
    style.grid(row=1, column=1, padx=10, pady=5, sticky="we")
    ttk.Label(style, text="Marker size:").grid(row=0, column=0, sticky="e")
    ttk.Entry(style, textvariable=cfg["marker"], width=6).grid(row=0, column=1, sticky="w")

    def on_run():
        win.destroy()

    ttk.Button(win, text="Run", command=on_run).grid(row=2, column=0, columnspan=2, pady=10)
    win.mainloop()
    return paths, cfg


def main(files):
    for path in files:
        head, coils = parse_name(Path(path).stem)
        df = load_file(path)
        for ch in (1, 2, 3):
            y = df[f"ch{ch}_t1"] + df[f"ch{ch}_t2"]
            plot_channel(y, head, coils, ch)

    if SHOW_PLOTS:
        plt.show()
    else:
        plt.close('all')

    print("Done.")


if __name__ == "__main__":
    paths, cfg = ask_user()

    SHOW_PLOTS = cfg["show"].get()
    SAVE_PLOTS = cfg["save"].get()
    OUTPUT_DIR = cfg["out_dir"].get()
    PLOT_MODE = cfg["mode"].get()
    MARKER_SIZE = cfg["marker"].get()
    MED_WINDOW = cfg["med_window"].get()
    MA_WINDOW = cfg["ma_window"].get()

    main(paths)
