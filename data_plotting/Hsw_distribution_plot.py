#!/usr/bin/env python3
"""
Switching‐Field Distribution with Robust Outlier Removal

1) File picker for .txt data files
2) GUI for plot selection, bin sizing, and outlier method/threshold
3) Outlier removal (Z-score, IQR, or MAD) before any calculations
4) Histogram (auto/manual), dp/dh, and plots
"""

import os, sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog, ttk

# 1) File selection
root = tk.Tk(); root.withdraw()
file_paths = filedialog.askopenfilenames(
    title="Select one or more .txt data files",
    filetypes=[("Text files","*.txt"),("All files","*.*")]
)
if not file_paths:
    sys.exit("No files selected.")
root.destroy()

# 2) Configuration GUI
cfg_win = tk.Tk()
cfg_win.title("Plot & Outlier Configuration")

# Control variables (created after cfg_win exists)
cfg = {
    "raw":       tk.BooleanVar(master=cfg_win, value=True),
    "hist":      tk.BooleanVar(master=cfg_win, value=True),
    "ind_log":   tk.BooleanVar(master=cfg_win, value=True),
    "comb_log":  tk.BooleanVar(master=cfg_win, value=True),
    "bin_mode":  tk.StringVar(master=cfg_win, value="auto"),
    "bin_width": tk.DoubleVar(master=cfg_win, value=1e-4),
    "out_method":tk.StringVar(master=cfg_win, value="z-score"),
    "out_threshold": tk.DoubleVar(master=cfg_win, value=3.0),
    "highlight": tk.BooleanVar(master=cfg_win, value=False),
}

# Plot selection
plots = [
    ("Raw TT/HH vs Index", "raw"),
    ("Counts Histogram",    "hist"),
    ("Individual ln(dp/dh)", "ind_log"),
    ("Combined ln(dp/dh)",   "comb_log"),
]
for i,(text,key) in enumerate(plots):
    ttk.Checkbutton(cfg_win, text=text, variable=cfg[key])\
       .grid(row=i, column=0, sticky="w", padx=5, pady=2)

# Bin sizing
bin_frame = ttk.LabelFrame(cfg_win, text="Bin Sizing")
bin_frame.grid(row=0, column=1, rowspan=3, padx=10, pady=5, sticky="n")
ttk.Radiobutton(bin_frame, text="Automatic", variable=cfg["bin_mode"], value="auto")\
    .grid(row=0, column=0, sticky="w", pady=2)
ttk.Radiobutton(bin_frame, text="Manual Δh =", variable=cfg["bin_mode"], value="manual")\
    .grid(row=1, column=0, sticky="w", pady=2)
ttk.Entry(bin_frame, textvariable=cfg["bin_width"], width=8)\
    .grid(row=1, column=1, sticky="w", pady=2)
ttk.Label(bin_frame, text="(only if manual)").grid(row=1, column=2, sticky="w")

# Outlier removal
out_frame = ttk.LabelFrame(cfg_win, text="Outlier Removal")
out_frame.grid(row=3, column=0, columnspan=2, padx=5, pady=5, sticky="we")
ttk.Label(out_frame, text="Method:").grid(row=0, column=0, sticky="e")
ttk.OptionMenu(out_frame, cfg["out_method"], cfg["out_method"].get(),
               "z-score", "IQR", "MAD").grid(row=0, column=1, sticky="w", padx=5)
ttk.Label(out_frame, text="Threshold:").grid(row=1, column=0, sticky="e")
ttk.Entry(out_frame, textvariable=cfg["out_threshold"], width=8)\
    .grid(row=1, column=1, sticky="w", padx=5)
ttk.Checkbutton(out_frame, text="Highlight on raw plot",
                variable=cfg["highlight"])\
    .grid(row=2, column=0, columnspan=2, sticky="w")

# Run button
def run_and_close():
    cfg_win.destroy()

ttk.Button(cfg_win, text="Run", command=run_and_close)\
    .grid(row=5, column=0, columnspan=2, pady=10)

cfg_win.mainloop()

# 3) Load, normalize, and outlier‐filter
data = {}
for path in file_paths:
    df = pd.read_csv(path, sep=';', header=None, usecols=[0,1],
                     names=['TT','HH'])
    # Normalize
    df['TT_norm'] = df['TT']/df['TT'].max()
    df['HH_norm'] = df['HH']/df['HH'].max()
    # Compute mask
    method = cfg["out_method"].get()
    thr    = cfg["out_threshold"].get()
    mask = np.ones(len(df), dtype=bool)

    if method == "z-score":
        for col in ['TT_norm','HH_norm']:
            z = (df[col]-df[col].mean())/df[col].std()
            mask &= np.abs(z) < thr

    elif method == "IQR":
        for col in ['TT_norm','HH_norm']:
            q1,q3 = df[col].quantile([0.25,0.75])
            iqr = q3 - q1
            mask &= df[col].between(q1-thr*iqr, q3+thr*iqr)

    elif method == "MAD":
        for col in ['TT_norm','HH_norm']:
            med = df[col].median()
            mad = np.median(np.abs(df[col]-med))
            z_mad = 0.6745*(df[col]-med)/(mad if mad>0 else 1e-9)
            mask &= np.abs(z_mad) < thr

    # Highlight mask stored, then drop outliers
    data[path] = {"df": df[mask].copy(), "mask": mask}

# 4) Build histograms & compute dp/dh
hist = {}
for path,info in data.items():
    name = os.path.splitext(os.path.basename(path))[0]
    df   = info["df"]
    hist[name] = {}
    for col in ['TT_norm','HH_norm']:
        vals = df[col].values
        mn, mx = vals.min(), vals.max()

        if cfg["bin_mode"].get()=="auto":
            N = len(vals)
            for nb in range(N,1,-1):
                cnts, edges = np.histogram(vals, bins=nb, range=(mn,mx))
                if np.all(cnts>0):
                    counts, bins = cnts, edges
                    break
            else:
                fb = min(50, N//2)
                counts, bins = np.histogram(vals, bins=fb, range=(mn,mx))
        else:
            Δh = cfg["bin_width"].get()
            bins = np.arange(mn, mx+Δh, Δh)
            counts, _ = np.histogram(vals, bins=bins)

        Δh = bins[1]-bins[0]
        Ni = np.cumsum(counts[::-1])[::-1]
        hazard = counts/Ni
        J = hazard.sum()
        dpdh = (hazard/Δh)/J
        centers = 0.5*(bins[:-1]+bins[1:])

        hist[name][col] = {
            "centers":centers,
            "counts": counts,
            "dpdh":    dpdh,
            "Δh":       Δh
        }

# 5) Plot as requested
for path,info in data.items():
    name = os.path.splitext(os.path.basename(path))[0]
    df   = info["df"]
    orig_mask = info["mask"]

    # Raw
    if cfg["raw"].get():
        fig,(ax1,ax2) = plt.subplots(2,1,sharex=True,figsize=(6,4))
        ax1.scatter(df.index+1, df['TT'], s=0.5, alpha=0.7, label="inlier")
        if cfg["highlight"].get():
            bad = ~orig_mask
            ax1.scatter(np.where(bad)[0]+1, df['TT'][bad], s=10,
                        c='r',marker='x', label="outlier")
        ax1.set_ylabel("TT (raw)")
        ax1.legend(loc="upper right",fontsize="x-small")

        ax2.scatter(df.index+1, df['HH'], s=0.5, alpha=0.7, color='C1', label="inlier")
        if cfg["highlight"].get():
            bad = ~orig_mask
            ax2.scatter(np.where(bad)[0]+1, df['HH'][bad], s=10,
                        c='r',marker='x', label="outlier")
        ax2.set_ylabel("HH (raw)")
        ax2.set_xlabel("Index")
        ax2.legend(loc="upper right",fontsize="x-small")

        fig.suptitle(name)
        fig.tight_layout(rect=[0,0,1,0.95])

    # Histogram
    if cfg["hist"].get():
        for col,h in hist[name].items():
            plt.figure()
            plt.bar(h["centers"], h["counts"], width=h["Δh"],
                    align="center",alpha=0.6,edgecolor='k')
            plt.title(f"{name} — {col}: Counts vs H/Hsw,max")
            plt.xlabel("H/Hsw,max"); plt.ylabel("Counts")
            plt.grid(ls='--',alpha=0.3)

    # Individual log
    if cfg["ind_log"].get():
        for col,h in hist[name].items():
            c, dp = h["centers"], h["dpdh"]
            x = (1-c)**1.5; m = dp>0
            plt.figure()
            plt.plot(x[m], np.log(dp[m]), '-o',markersize=4)
            plt.title(f"{name} — {col}: ln(dp/dh) vs h^(3/2)")
            plt.xlabel(r"$h^{3/2}$"); plt.ylabel(r"$\ln(dp/dh)$")
            plt.grid(ls='--',alpha=0.3)

    # Combined log
    if cfg["comb_log"].get():
        plt.figure()
        for col,h in hist[name].items():
            c, dp = h["centers"], h["dpdh"]
            x = (1-c)**1.5; m = dp>0
            plt.plot(x[m], np.log(dp[m]), '-o',markersize=4,label=col)
        plt.title(f"{name} — Combined ln(dp/dh) vs h^(3/2)")
        plt.xlabel(r"$h^{3/2}$"); plt.ylabel(r"$\ln(dp/dh)$")
        plt.legend(); plt.grid(ls='--',alpha=0.3)

plt.show()