import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ===========================
# Outlier Comparison Script
# ===========================
# 1) Point this to your data file:
FILE_PATH = r"G:\Shared drives\Projekty\VAIA\WP1 - MicroWire Development\stress depencence\data\FeSiBP 156_2 s4-2b 74mA 2,5b.txt"

# 2) Set thresholds for each method
threshold_z    = 3.0    # Z-score cutoff (|z| < threshold_z)
threshold_iqr  = 2    # IQR multiplier (keep within Q1 - k*IQR ... Q3 + k*IQR)
threshold_mad  = 3.0    # MAD‐based z‐score (|z_mad| < threshold_mad)
threshold_trim = 0.1    # Percentile trim (remove bottom/top threshold_trim %)

# 3) Load and normalize
df = pd.read_csv(FILE_PATH, sep=';', header=None, usecols=[0,1], names=['TT','HH'])
df['TT_norm'] = df['TT'] / df['TT'].max()
df['HH_norm'] = df['HH'] / df['HH'].max()

# 4) Compute masks

# Z-score mask
zT = (df['TT_norm'] - df['TT_norm'].mean()) / df['TT_norm'].std()
zH = (df['HH_norm'] - df['HH_norm'].mean()) / df['HH_norm'].std()
mask_z = (np.abs(zT) < threshold_z) & (np.abs(zH) < threshold_z)

# IQR mask
def iqr_mask(s, k):
    q1, q3 = s.quantile([0.25,0.75])
    iqr = q3 - q1
    return s.between(q1 - k*iqr, q3 + k*iqr)
mask_iqr = iqr_mask(df['TT_norm'], threshold_iqr) & iqr_mask(df['HH_norm'], threshold_iqr)

# MAD mask
def mad_mask(s, k):
    med = s.median()
    mad = np.median(np.abs(s - med))
    z_mad = 0.6745 * (s - med) / (mad if mad>0 else 1e-9)
    return np.abs(z_mad) < k
mask_mad = mad_mask(df['TT_norm'], threshold_mad) & mad_mask(df['HH_norm'], threshold_mad)

# Percentile Trim mask
p = threshold_trim / 100.0
mask_trim = (
    df['TT_norm'].between(df['TT_norm'].quantile(p), df['TT_norm'].quantile(1-p)) &
    df['HH_norm'].between(df['HH_norm'].quantile(p), df['HH_norm'].quantile(1-p))
)

# 5) Plot comparison
methods = {
    f'Z-score (thr={threshold_z})': mask_z,
    f'IQR (k={threshold_iqr})':      mask_iqr,
    f'MAD (thr={threshold_mad})':    mask_mad,
    f'Trim {threshold_trim}%':       mask_trim
}

fig, axes = plt.subplots(4, 1, figsize=(10, 16), sharex=True)
for ax, (method, mask) in zip(axes, methods.items()):
    # Inliers
    ax.scatter(df.index[mask]+1, df['TT'][mask], s=2, label='TT inlier', alpha=0.6)
    ax.scatter(df.index[mask]+1, df['HH'][mask], s=2, label='HH inlier', alpha=0.6, color='C1')
    # Outliers
    ax.scatter(df.index[~mask]+1, df['TT'][~mask], s=30, c='r', marker='x', label='TT outlier')
    ax.scatter(df.index[~mask]+1, df['HH'][~mask], s=30, c='m', marker='x', label='HH outlier')
    ax.set_title(f"Outlier Detection: {method}")
    ax.set_ylabel("Switching Field")
    ax.legend(loc='upper right', fontsize='small')

axes[-1].set_xlabel("Index")
fig.suptitle("Comparison of Outlier Detection Methods", fontsize=16, y=0.95)
plt.tight_layout(rect=[0,0,1,0.96])
plt.show()