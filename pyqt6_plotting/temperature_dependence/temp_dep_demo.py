from __future__ import annotations
import sys
import pathlib
import tkinter as tk
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from pyqt6_plotting.temperature_dependence.core import load_data
else:
    from .core import load_data

DATA_DIR = Path(__file__).resolve().parents[2] / "sample_data" / "temperature_dependence"

RAW_COLORS = {25: "#45A1D6", 100: "#F09C67"}
MEAN_COLORS = {25: "#00306E", 100: "#965308"}
RAW_MARKER = "o"
RAW_MSIZE = 0.3
MEAN_MARKER = "o"
MEAN_MSIZE = 8
OFFSET = 0.25
JITTER_SPAN = 0.25
MEAN_SHIFT = OFFSET * 2


def run_plot(med_window: int, ma_window: int) -> None:
    files = [str(p) for p in DATA_DIR.glob("*.txt")]
    df = load_data(files)
    comp = df["composition"].iat[0]
    anneal = df["anneal"].iat[0]
    samples = sorted(df["sample"].unique())
    sample_idx = {s: i + 1 for i, s in enumerate(samples)}
    df["sample_idx"] = df["sample"].map(sample_idx).astype(float)

    fig, ax = plt.subplots(figsize=(10, 5))
    np.random.seed(0)

    raw = df[~df["continuous"]]
    for temp in sorted(raw["temp"].dropna().unique()):
        sub = raw[raw["temp"] == temp]
        x_center = sub["sample_idx"] + {-1: None, 25: -OFFSET, 100: OFFSET}.get(int(temp), 0)
        jitter = np.random.uniform(-JITTER_SPAN, JITTER_SPAN, len(sub))
        ax.scatter(x_center + jitter, sub["sum"], color=RAW_COLORS.get(int(temp), "gray"), s=RAW_MSIZE, marker=RAW_MARKER, label=f"raw {int(temp)}C")

    means = raw.groupby(["sample", "sample_idx", "temp"])["sum"].mean().reset_index()
    legend_done = set()

    for s in samples:
        idx = sample_idx[s]
        m25 = means[(means["sample"] == s) & (means["temp"] == 25)]["sum"]
        m100 = means[(means["sample"] == s) & (means["temp"] == 100)]["sum"]
        has_overall = not df[(df["sample"] == s) & (df["continuous"])].empty

        # Use horizontal offset only if a continuous "overall" measurement is
        # available for this sample
        if has_overall:
            x25 = idx - MEAN_SHIFT
            x100 = idx + MEAN_SHIFT
        else:
            x25 = x100 = idx

        # Always show the difference between 100°C and 25°C means
        if not m25.empty and not m100.empty:
            ax.plot([idx, idx], [m25.iloc[0], m100.iloc[0]], color="black", linewidth=1)
            delta = m100.iloc[0] - m25.iloc[0]
            ax.annotate(
                f"{delta:.1f}",
                (idx - 0.1, (m25.iloc[0] + m100.iloc[0]) / 2),
                ha="right",
                va="center",
                fontsize=10,
            )

        if not m25.empty:
            lbl = None if "m25" in legend_done else "mean 25C"
            ax.plot(
                x25,
                m25.iloc[0],
                MEAN_MARKER,
                c=MEAN_COLORS[25],
                markersize=MEAN_MSIZE,
                linestyle="None",
                label=lbl,
            )
            legend_done.add("m25")
        if not m100.empty:
            lbl = None if "m100" in legend_done else "mean 100C"
            ax.plot(
                x100,
                m100.iloc[0],
                MEAN_MARKER,
                c=MEAN_COLORS[100],
                markersize=MEAN_MSIZE,
                linestyle="None",
                label=lbl,
            )
            legend_done.add("m100")

        if has_overall:
            cont = (
                df[(df["sample"] == s) & (df["continuous"])]
                .sort_values("temp")
            )
            if not cont.empty:
                med = cont["sum"].rolling(med_window, center=True, min_periods=1).median()
                proc = med.rolling(ma_window, center=True, min_periods=1).mean()
                start = cont["temp"].iloc[0]
                end = cont["temp"].iloc[-1]
                # Stretch the curve from the 25°C mean position to the 100°C mean position
                scale = (x100 - x25) / (end - start)
                x = (cont["temp"] - start) * scale + x25
                lbl = (
                    None
                    if "overall" in legend_done
                    else f"overall med{med_window}+mwa{ma_window}"
                )
                ax.plot(x, proc, color="black", label=lbl)
                legend_done.add("overall")

    ax.set_xticks(list(sample_idx.values()))
    ax.set_xticklabels(samples)
    ax.set_xlabel("Sample")
    ax.set_ylabel("sum")
    ax.set_title(f"{comp} {anneal}")
    ax.grid(True)
    ax.legend()
    fig.tight_layout()
    plt.show()


def main() -> None:
    root = tk.Tk()
    root.title("Temp Dep Demo")
    tk.Label(root, text="Median window:").grid(row=0, column=0, sticky="e")
    med_var = tk.IntVar(value=1000)
    tk.Entry(root, textvariable=med_var, width=6).grid(row=0, column=1)
    tk.Label(root, text="MA window:").grid(row=1, column=0, sticky="e")
    ma_var = tk.IntVar(value=1000)
    tk.Entry(root, textvariable=ma_var, width=6).grid(row=1, column=1)

    def on_run() -> None:
        run_plot(med_var.get(), ma_var.get())

    tk.Button(root, text="Run", command=on_run).grid(row=2, column=0, columnspan=2, pady=5)
    root.mainloop()


if __name__ == "__main__":
    main()
