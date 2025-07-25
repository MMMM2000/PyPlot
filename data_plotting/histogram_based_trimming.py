import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import itertools

# === USER SETTINGS ===
# Replace with your actual file path:
FILE_PATH = r"G:\Shared drives\Projekty\VAIA\WP1 - MicroWire Development\stress depencence\data\FeSiBP 156_2 s4-2b 74mA 2,5b.txt"
threshold = 3     # bins with counts <= threshold mark edges
initial_bins = 1  # starting number of bins; will double until sparse bins found

# === LOAD & NORMALIZE ===
df = pd.read_csv(
    FILE_PATH,
    sep=';', header=None,
    usecols=[0,1],
    names=['TT','HH']
)
df['TTn'] = df['TT'] / df['TT'].max()
df['HHn'] = df['HH'] / df['HH'].max()

def dynamic_trim_mask(values, threshold, initial_bins=1):
    """
    Dynamically increase bin count until at least one bin has <= threshold counts.
    Then define the core as bins strictly between the first and last sparse bins.
    Returns:
      mask: boolean array, True for values in the core region
      edges, counts, core_bins_indices
    """
    vmin, vmax = values.min(), values.max()
    bins = initial_bins
    while True:
        counts, edges = np.histogram(values, bins=bins, range=(vmin, vmax))
        sparse = np.where(counts <= threshold)[0]
        if len(sparse) > 0 or bins >= len(values):
            break
        bins *= 2

    if len(sparse) == 0:
        # no sparse bins found; core is all bins
        core_bins = list(range(len(counts)))
    else:
        first, last = sparse[0], sparse[-1]
        core_bins = list(range(first+1, last))

    bin_idx = np.minimum(np.searchsorted(edges, values) - 1, len(counts)-1)
    mask = np.isin(bin_idx, core_bins)
    return mask, edges, counts, core_bins

# Compute masks and histogram info
mask_tt, edges_tt, counts_tt, core_tt = dynamic_trim_mask(
    df['TTn'].values, threshold, initial_bins
)
mask_hh, edges_hh, counts_hh, core_hh = dynamic_trim_mask(
    df['HHn'].values, threshold, initial_bins
)
mask_keep = mask_tt & mask_hh

# === PLOTTING ===
fig, axes = plt.subplots(3, 1, figsize=(8, 12), sharex=False)

# 1) Raw series with marked outliers
ax1 = axes[0]
# TT inliers
ax1.scatter(df.index[mask_keep]+1, df['TT'][mask_keep], s=2, label='TT inlier', alpha=0.6)
# TT outliers
ax1.scatter(df.index[~mask_keep]+1, df['TT'][~mask_keep], s=20, c='r', marker='x', label='TT outlier')
# HH inliers
ax1.scatter(df.index[mask_keep]+1, df['HH'][mask_keep], s=2, label='HH inlier', alpha=0.6, color='C1')
# HH outliers
ax1.scatter(df.index[~mask_keep]+1, df['HH'][~mask_keep], s=20, c='m', marker='x', label='HH outlier')
ax1.set_title("Raw TT/HH with Trimmed Outliers")
ax1.set_ylabel("Switching Field")
ax1.legend(fontsize='small')

# 2) TT_norm histogram: core vs trimmed bins
ax2 = axes[1]
centers_tt = 0.5*(edges_tt[:-1] + edges_tt[1:])
colors_tt = ['blue' if i in core_tt else 'red' for i in range(len(counts_tt))]
ax2.bar(centers_tt, counts_tt, width=edges_tt[1]-edges_tt[0],
        color=colors_tt, edgecolor='k', alpha=0.7)
ax2.set_title("TT_norm Histogram (blue=core, red=trimmed)")
ax2.set_xlabel("TT / TT_max")
ax2.set_ylabel("Counts")
# legend
core_patch_tt = plt.Line2D([0], [0], color='blue', lw=4, label='Core bins')
trim_patch = plt.Line2D([0], [0], color='red', lw=4, label='Trimmed bins')
ax2.legend(handles=[core_patch_tt, trim_patch], fontsize='small')

# 3) HH_norm histogram: core vs trimmed bins
ax3 = axes[2]
centers_hh = 0.5*(edges_hh[:-1] + edges_hh[1:])
colors_hh = ['orange' if i in core_hh else 'red' for i in range(len(counts_hh))]
ax3.bar(centers_hh, counts_hh, width=edges_hh[1]-edges_hh[0],
        color=colors_hh, edgecolor='k', alpha=0.7)
ax3.set_title("HH_norm Histogram (orange=core, red=trimmed)")
ax3.set_xlabel("HH / HH_max")
ax3.set_ylabel("Counts")
core_patch_hh = plt.Line2D([0], [0], color='orange', lw=4, label='Core bins')
ax3.legend(handles=[core_patch_hh, trim_patch], fontsize='small')

plt.tight_layout()
plt.show()