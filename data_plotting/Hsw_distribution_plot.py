#!/usr/bin/env python3
"""
Hsw distribution with Histogram‐Core filtering, optional trimmed display,
and shared TT/HH bin counts (fixed so `bins` is always defined).
"""

import os, sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog, ttk

# ─────────────────────────────────────────────
# 1) File selection
# ─────────────────────────────────────────────
root = tk.Tk()
root.withdraw()
paths = filedialog.askopenfilenames(
    title="Select .txt data files",
    filetypes=[("Text files","*.txt"),("All files","*.*")]
)
if not paths:
    sys.exit("No files selected.")
root.destroy()

# ─────────────────────────────────────────────
# 2) Configuration GUI
# ─────────────────────────────────────────────
cfg_win = tk.Tk()
cfg_win.title("Hsw Distribution Settings")

cfg = {
    "raw":          tk.BooleanVar(cfg_win, True),
    "show_trimmed": tk.BooleanVar(cfg_win, True),
    "hist":         tk.BooleanVar(cfg_win, True),
    "ind_log":      tk.BooleanVar(cfg_win, True),
    "comb_log":     tk.BooleanVar(cfg_win, True),
    "bin_mode":     tk.StringVar(cfg_win, "auto"),
    "bin_width":    tk.DoubleVar(cfg_win, 1e-4),
    "share_bins":   tk.BooleanVar(cfg_win, False),
    "core_bins":    tk.IntVar(cfg_win, 50),
    "core_min":     tk.IntVar(cfg_win, 3),
}

# Plot toggles
ttk.Checkbutton(cfg_win, text="Raw TT/HH vs Index", variable=cfg["raw"])\
    .grid(row=0, column=0, sticky="w", padx=5, pady=2)
ttk.Checkbutton(cfg_win, text="Show trimmed data", variable=cfg["show_trimmed"])\
    .grid(row=1, column=0, sticky="w", padx=5, pady=2)
ttk.Checkbutton(cfg_win, text="Counts Histogram", variable=cfg["hist"])\
    .grid(row=2, column=0, sticky="w", padx=5, pady=2)
ttk.Checkbutton(cfg_win, text="Individual ln(dp/dh)", variable=cfg["ind_log"])\
    .grid(row=3, column=0, sticky="w", padx=5, pady=2)
ttk.Checkbutton(cfg_win, text="Combined ln(dp/dh)", variable=cfg["comb_log"])\
    .grid(row=4, column=0, sticky="w", padx=5, pady=2)

# Binning mode
bin_frame = ttk.LabelFrame(cfg_win, text="Final Histogram Binning")
bin_frame.grid(row=0, column=1, rowspan=3, padx=10, pady=5, sticky="n")
ttk.Radiobutton(bin_frame, text="Automatic", variable=cfg["bin_mode"], value="auto")\
    .grid(row=0, column=0, sticky="w")
ttk.Radiobutton(bin_frame, text="Manual Δh =", variable=cfg["bin_mode"], value="manual")\
    .grid(row=1, column=0, sticky="w")
ttk.Entry(bin_frame, textvariable=cfg["bin_width"], width=8)\
    .grid(row=1, column=1, sticky="w")
ttk.Checkbutton(bin_frame, text="Shared bins TT/HH", variable=cfg["share_bins"])\
    .grid(row=2, column=0, columnspan=2, sticky="w", pady=2)

# Histogram‐Core params
core_frame = ttk.LabelFrame(cfg_win, text="Histogram‐Core Filter")
core_frame.grid(row=3, column=1, rowspan=2, padx=10, pady=5, sticky="n")
ttk.Label(core_frame, text="n_bins:").grid(row=0, column=0, sticky="e")
ttk.Entry(core_frame, textvariable=cfg["core_bins"], width=6)\
    .grid(row=0, column=1, sticky="w")
ttk.Label(core_frame, text="min_count:").grid(row=1, column=0, sticky="e")
ttk.Entry(core_frame, textvariable=cfg["core_min"], width=6)\
    .grid(row=1, column=1, sticky="w")

def on_run():
    cfg_win.destroy()

ttk.Button(cfg_win, text="Run", command=on_run)\
    .grid(row=5, column=0, columnspan=2, pady=10)
cfg_win.mainloop()

# ─────────────────────────────────────────────
# 3) Histogram‐Core filter function
# ─────────────────────────────────────────────
def core_mask(values, n_bins, min_count):
    counts, edges = np.histogram(
        values, bins=n_bins, range=(values.min(), values.max())
    )
    dense = np.flatnonzero(counts > min_count)
    if dense.size == 0:
        mask = np.ones_like(values, dtype=bool)
    else:
        lo, hi = dense[0], dense[-1]
        idxs = np.minimum(np.searchsorted(edges, values) - 1, len(counts)-1)
        mask = (idxs >= lo) & (idxs <= hi)
    return mask, edges, counts

# ─────────────────────────────────────────────
# 4) Load → filter → re‐normalize
# ─────────────────────────────────────────────
raw_data = {}
data = {}
masks = {}

for path in paths:
    name = os.path.splitext(os.path.basename(path))[0]
    raw = pd.read_csv(path, sep=';', header=None, usecols=[0,1], names=['TT','HH'])
    raw['TTn0'] = raw['TT'] / raw['TT'].max()
    raw['HHn0'] = raw['HH'] / raw['HH'].max()

    n_bins = max(2, cfg["core_bins"].get())
    min_ct = max(1, cfg["core_min"].get())

    m_t, _, _ = core_mask(raw['TTn0'].values, n_bins, min_ct)
    m_h, _, _ = core_mask(raw['HHn0'].values, n_bins, min_ct)
    mask = m_t & m_h

    filtered = raw.loc[mask, ['TT','HH']].reset_index(drop=True)
    filtered['TTn'] = filtered['TT'] / filtered['TT'].max()
    filtered['HHn'] = filtered['HH'] / filtered['HH'].max()

    raw_data[name] = raw
    data[name]     = filtered
    masks[name]    = mask

# ─────────────────────────────────────────────
# 5) Helper to find auto bin count
# ─────────────────────────────────────────────
def find_auto_bins(vals):
    hmin, hmax = vals.min(), vals.max()
    N = len(vals)
    for B in range(N, 1, -1):
        cnts, _ = np.histogram(vals, bins=B, range=(hmin, hmax))
        if np.all(cnts > 0):
            return B
    return max(2, min(50, N//2))

# ─────────────────────────────────────────────
# 6) Build histograms + dp/dh on filtered data
# ─────────────────────────────────────────────
hist = {}
for name, df in data.items():
    hist[name] = {}
    vals_tt = df['TTn'].values
    vals_hh = df['HHn'].values

    for col, vals in [('TTn', vals_tt), ('HHn', vals_hh)]:
        hmin, hmax = vals.min(), vals.max()

        if cfg["bin_mode"].get() == "auto":
            if cfg["share_bins"].get():
                B_tt = find_auto_bins(vals_tt)
                B_hh = find_auto_bins(vals_hh)
                bins = min(B_tt, B_hh)
            else:
                bins = find_auto_bins(vals)
            counts, edges = np.histogram(vals, bins=bins, range=(hmin, hmax))

        else:
            dh = cfg["bin_width"].get()
            edges = np.arange(hmin, hmax + dh, dh)
            counts, _ = np.histogram(vals, bins=edges)

        centers = 0.5 * (edges[:-1] + edges[1:])
        Δh = edges[1] - edges[0]
        Ni = np.cumsum(counts[::-1])[::-1]
        hazard = counts / (Ni + 1e-12)
        dp = (hazard / Δh) / (hazard.sum() + 1e-12)

        hist[name][col] = {
            "centers": centers,
            "counts":  counts,
            "dp":      dp,
            "Δh":      Δh
        }

# ─────────────────────────────────────────────
# 7) Plot all requested figures
# ─────────────────────────────────────────────
for name, df in data.items():
    mask = masks[name]
    raw = raw_data[name]

    # Raw
    if cfg["raw"].get():
        plt.figure(figsize=(6,3))
        plt.scatter(df.index+1, df['TT'], s=2, label='TT inlier')
        plt.scatter(df.index+1, df['HH'], s=2, label='HH inlier', color='C1')
        if cfg["show_trimmed"].get():
            trimmed = ~mask
            plt.scatter(np.where(trimmed)[0]+1, raw['TT'][trimmed],
                        s=20, c='r', marker='x', label='TT trimmed')
            plt.scatter(np.where(trimmed)[0]+1, raw['HH'][trimmed],
                        s=20, c='m', marker='x', label='HH trimmed')
        plt.title(f"{name} — Raw with Histogram‐Core filter")
        plt.xlabel("Index"); plt.ylabel("Switching Field")
        plt.legend(fontsize='x-small'); plt.tight_layout()

    # Counts histogram
    if cfg["hist"].get():
        for col, h in hist[name].items():
            plt.figure()
            plt.bar(h["centers"], h["counts"],
                    width=h["Δh"], edgecolor='k', alpha=0.6)
            plt.title(f"{name} — {col}: counts")
            plt.xlabel("h = H/Hsw,max"); plt.ylabel("Counts")
            plt.grid(ls='--', alpha=0.3)

    # Individual ln(dp/dh)
    if cfg["ind_log"].get():
        for col, h in hist[name].items():
            valid = h["dp"] > 0
            x = (1 - h["centers"][valid]) ** 1.5
            y = np.log(h["dp"][valid])
            plt.figure()
            plt.plot(x, y, '-o', markersize=4)
            plt.title(f"{name} — {col}: ln(dp/dh) vs Δh^(3/2)")
            plt.xlabel(r"$\Delta h^{3/2}$"); plt.ylabel(r"$\ln(dp/dh)$")
            plt.grid(ls='--', alpha=0.3)

    # Combined ln(dp/dh)
    if cfg["comb_log"].get():
        plt.figure()
        for col, h in hist[name].items():
            valid = h["dp"] > 0
            plt.plot((1 - h["centers"][valid])**1.5,
                     np.log(h["dp"][valid]), '-o',
                     markersize=4, label=col)
        plt.title(f"{name} — Combined ln(dp/dh)")
        plt.xlabel(r"$\Delta h^{3/2}$"); plt.ylabel(r"$\ln(dp/dh)$")
        plt.legend(); plt.grid(ls='--', alpha=0.3)

plt.show()
