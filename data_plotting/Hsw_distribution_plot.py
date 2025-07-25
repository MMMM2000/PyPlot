#!/usr/bin/env python3
"""
Switching‐Field Distribution Script (with counts)

Steps:
  1) Select .txt files
  2) Load TT & HH
  3) Normalize → TT_norm, HH_norm
  4) Finest histograms, no empty bins
  5) Compute dp/dh via cumulative counts (store counts too)
  6) Plot raw data, Counts histograms, and ln(dp/dh) vs h^(3/2)
"""

import os, sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog

# === User Configuration ===
PLOT_RAW_SERIES   = True
PLOT_HIST         = True
PLOT_LOG          = True
SHOW_ALL_AT_ONCE  = True

# 1) File selection
root = tk.Tk(); root.withdraw()
file_paths = filedialog.askopenfilenames(title="Select .txt files",
                                         filetypes=[("Text files","*.txt"),("All files","*.*")])
if not file_paths:
    sys.exit("No files selected.")

# 2) Load data
data_dict = {}
for path in file_paths:
    df = pd.read_csv(path, sep=';', header=None, usecols=[0,1], names=['TT','HH'])
    key = os.path.splitext(os.path.basename(path))[0]
    data_dict[key] = df
    print(f"Loaded '{key}' — {len(df)} rows")

# 3) Normalize
for name, df in data_dict.items():
    df['TT_norm'] = df['TT'] / df['TT'].max()
    df['HH_norm'] = df['HH'] / df['HH'].max()
    print(f"[{name}] Hmax_TT={df['TT'].max():.6g}, Hmax_HH={df['HH'].max():.6g}")

# 4+5) Build histograms & dp/dh (store counts)
histograms = {}
for name, df in data_dict.items():
    histograms[name] = {}
    print(f"\nBuilding dp/dh for '{name}':")
    for col in ['TT_norm','HH_norm']:
        h_vals = df[col].values
        N = len(h_vals)

        # finest bins
        for n_bins in range(N,1,-1):
            counts, edges = np.histogram(h_vals, bins=n_bins,
                                         range=(h_vals.min(), h_vals.max()))
            if np.all(counts>0):
                break
        else:
            n_bins = min(50, N//2)
            counts, edges = np.histogram(h_vals, bins=n_bins,
                                         range=(h_vals.min(), h_vals.max()))
            print(f"  • Warning: fallback to n_bins={n_bins}")

        delta_h = edges[1] - edges[0]
        Ni      = np.cumsum(counts[::-1])[::-1]
        hazard  = counts / Ni
        J       = hazard.sum()
        dpdh    = (hazard/delta_h)/J
        centers = 0.5*(edges[:-1] + edges[1:])

        # Store counts as well!
        histograms[name][col] = {
            'centers':  centers,   # H/Hsw,max bin centers
            'counts':   counts,    # raw counts per bin
            'dpdh':     dpdh,      # normalized density
            'delta_h':  delta_h,   # bin width
            'n_bins':   n_bins
        }
        print(f"  • {col}: n_bins={n_bins}, Δh={delta_h:.3e}")

# 6) Plotting
for name, df in data_dict.items():
    if PLOT_RAW_SERIES:
        fig = plt.figure()
        ax1 = fig.add_subplot(211)
        ax1.scatter(df.index+1, df['TT'], s=0.5)
        ax1.set_title(f"{name} — TT (raw)")
        ax1.set_ylabel("TT")
        ax2 = fig.add_subplot(212, sharex=ax1)
        ax2.scatter(df.index+1, df['HH'], s=0.5, color='C1')
        ax2.set_title(f"{name} — HH (raw)")
        ax2.set_xlabel("Index")
        ax2.set_ylabel("HH")
        fig.tight_layout()
        if not SHOW_ALL_AT_ONCE:
            plt.show()

    data = histograms[name]
    for col, hist in data.items():
        centers_norm = hist['centers']
        counts       = hist['counts']
        dp           = hist['dpdh']
        delta_h      = hist['delta_h']
        h_red        = 1.0 - centers_norm
        x_log        = h_red**1.5

        if PLOT_HIST:
            plt.figure()
            plt.bar(centers_norm, counts, width=delta_h,
                    align='center', alpha=0.6, edgecolor='k')
            plt.title(f"{name} — {col}: Counts vs H/Hsw,max")
            plt.xlabel("H/Hsw,max")
            plt.ylabel("Counts")
            plt.grid(ls='--', alpha=0.3)
            plt.tight_layout()
            if not SHOW_ALL_AT_ONCE:
                plt.show()

        if PLOT_LOG:
            mask = dp>0
            plt.figure()
            plt.scatter(x_log[mask], np.log(dp[mask]), s=20, alpha=0.7)
            plt.title(f"{name} — {col}: ln(dp/dh) vs h^(3/2)")
            plt.xlabel(r"$h^{3/2}$")
            plt.ylabel(r"$\ln(dp/dh)$")
            plt.grid(ls='--', alpha=0.3)
            plt.tight_layout()
            if not SHOW_ALL_AT_ONCE:
                plt.show()

if SHOW_ALL_AT_ONCE:
    plt.show()