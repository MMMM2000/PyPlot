import tkinter as tk
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt

from .core import load_data

DATA_DIR = Path(__file__).resolve().parents[2] / "sample_data" / "temperature_dependence"
FILES = [
    "Fe77Mo4B18Cu1 4_3 77mA 25C.txt",
    "Fe77Mo4B18Cu1 4_3 77mA 100C.txt",
    "Fe77Mo4B18Cu1 4_3 77mA overall.txt",
]

OFFSET = 0.5

def run_plot(med_window: int, ma_window: int) -> None:
    all_files = [str(p) for p in DATA_DIR.glob("*.txt")]
    df = load_data(all_files)
    sub = df[(df["composition"] == "Fe77Mo4B18Cu1") & (df["sample"] == "4_3") & (df["anneal"] == "77mA")]
    means = sub[~sub["continuous"]].groupby("temp")["sum"].mean()
    mean25 = means.get(25)
    mean100 = means.get(100)
    cont = sub[sub["continuous"]].sort_values("temp")
    med = cont["sum"].rolling(med_window, center=True, min_periods=1).median()
    proc = med.rolling(ma_window, center=True, min_periods=1).mean()
    start = cont["temp"].iloc[0]
    end = cont["temp"].iloc[-1]
    scale = ((100 + OFFSET) - (25 - OFFSET)) / (end - start)
    x = (cont["temp"] - start) * scale + (25 - OFFSET)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(x, proc, color="black", label=f"overall med{med_window}+mwa{ma_window}")
    ax.plot(25 - OFFSET, mean25, "o", color="#45A1D6", label="mean 25C")
    ax.plot(100 + OFFSET, mean100, "o", color="#F09C67", label="mean 100C")
    ax.set_xlabel("Temperature (°C)")
    ax.set_ylabel("sum")
    ax.set_title("Fe77Mo4B18Cu1 4_3 77mA")
    ax.grid(True)
    ax.legend()
    fig.tight_layout()
    plt.show()

def main() -> None:
    root = tk.Tk()
    root.title("Temp Dep Test")
    tk.Label(root, text="Median window:").grid(row=0, column=0, sticky="e")
    med_var = tk.IntVar(value=100)
    tk.Entry(root, textvariable=med_var, width=6).grid(row=0, column=1)
    tk.Label(root, text="MA window:").grid(row=1, column=0, sticky="e")
    ma_var = tk.IntVar(value=100)
    tk.Entry(root, textvariable=ma_var, width=6).grid(row=1, column=1)
    def on_run() -> None:
        run_plot(med_var.get(), ma_var.get())
    tk.Button(root, text="Run", command=on_run).grid(row=2, column=0, columnspan=2, pady=5)
    root.mainloop()

if __name__ == "__main__":
    main()
