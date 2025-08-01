import os
from pathlib import Path
from typing import List, Dict, Any, Tuple
import re

from PyQt6 import QtWidgets

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from ..config import load_config
from ..common import maybe_handle_outliers

_CFG = load_config().get("temperature_dependence", {})
OUTPUT_DIR = _CFG.get("OUTPUT_DIR", os.getcwd())
PLOT_SUM = bool(_CFG.get("PLOT_SUM", True))
PLOT_DT = bool(_CFG.get("PLOT_DT", True))
PLOT_T1 = bool(_CFG.get("PLOT_T1", True))
PLOT_T2 = bool(_CFG.get("PLOT_T2", True))
PLOT_VARS = [v for v, b in [("sum", PLOT_SUM), ("dT", PLOT_DT), ("T1", PLOT_T1), ("T2", PLOT_T2)] if b]
PLOT_MODE = _CFG.get("PLOT_MODE", "raw")  # 'raw', 'processed', 'both'
MED_WINDOW = int(_CFG.get("MED_WINDOW", 5))
MA_WINDOW = int(_CFG.get("MA_WINDOW", 20))
SHOW_PLOTS = bool(_CFG.get("SHOW_PLOTS", True))
SAVE_PLOTS = bool(_CFG.get("SAVE_PLOTS", False))

RAW_COLORS = {25: "#45A1D6", 100: "#F09C67"}
OVERALL_COLOR = "#6B6B6B"
PROC_COLOR = "#F09C67"
MARKER = "o"
MARKER_SIZE = 0.3
PROC_LW = 2
JITTER_SPAN = 0.5


class ProgressDialog:
    """Fallback progress indicator used when no GUI is provided."""

    def __init__(self, total: int):
        self.total = total
        self.count = 0
        self.cancelled = False
        self.root = self

    def update(self) -> None:
        self.count += 1

    def destroy(self) -> None:
        pass

NAME_RE = re.compile(
    r"^(?P<composition>.+?)\s+"
    r"(?P<sample>\S+)\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<temp>\d+C|overall)$"
)

LABELS = {
    "T1": "T1 (µs)",
    "T2": "T2 (µs)",
    "dT": "T2–T1 (µs)",
    "sum": "T1+T2 (µs)",
}

def parse_metadata(stem: str) -> Dict[str, Any] | None:
    m = NAME_RE.match(stem)
    if not m:
        return None
    md = m.groupdict()
    temp = md["temp"].lower()
    md["continuous"] = temp == "overall"
    md["temp_val"] = None if md["continuous"] else int(temp.rstrip("c"))
    return md


def load_data(files: List[str]) -> pd.DataFrame:
    files = sorted(files)
    if not files:
        raise FileNotFoundError("No files selected")
    dfs = []
    for fn in files:
        md = parse_metadata(Path(fn).stem)
        if md is None:
            print(f"Skipping {fn}")
            continue
        if md["continuous"]:
            df = pd.read_csv(
                fn,
                sep=";",
                header=None,
                names=["T1", "T2", "dT", "sum"],
                engine="python",
                on_bad_lines="skip",
            )
            df["continuous"] = True
            df["temp"] = np.nan
        else:
            df = pd.read_csv(
                fn,
                sep=";",
                header=None,
                names=["T1", "T2", "dT", "sum"],
                engine="python",
                on_bad_lines="skip",
            )
            df["temp"] = md["temp_val"]
            df["continuous"] = False
        df["filename"] = Path(fn).name
        df["line"] = np.arange(len(df))
        df[["T1", "T2", "dT", "sum"]] = df[["T1", "T2", "dT", "sum"]].apply(pd.to_numeric, errors="coerce")
        df["temp"] = pd.to_numeric(df["temp"], errors="coerce")
        for k, v in md.items():
            if k not in {"temp_val", "continuous", "temp"}:
                df[k] = v
        dfs.append(df)
    if not dfs:
        raise FileNotFoundError("No valid files selected")
    data = pd.concat(dfs, ignore_index=True)
    cont_mask = data["continuous"]
    if cont_mask.any():
        temps = data.loc[~cont_mask, "temp"].dropna()
        if not temps.empty:
            t_min, t_max = temps.min(), temps.max()
        else:
            t_min, t_max = 0.0, float(len(data.loc[cont_mask]) - 1)
        data.loc[cont_mask, "temp"] = np.linspace(t_min, t_max, cont_mask.sum())
    return data


def plot_variable(df: pd.DataFrame, var: str, save_flag: bool, out_dir: str) -> Tuple[Figure, str]:
    comp = df["composition"].iat[0]
    sample = df["sample"].iat[0]
    anneal = df["anneal"].iat[0]

    fig, ax = plt.subplots(figsize=(9, 5))

    if PLOT_MODE in ("raw", "both"):
        sub = df[df["continuous"]]
        if not sub.empty:
            ax.scatter(
                sub["temp"],
                sub[var],
                c=OVERALL_COLOR,
                s=MARKER_SIZE,
                marker=MARKER,
                label="raw overall",
            )
        for temp in sorted(df.loc[~df["continuous"], "temp"].unique()):
            s = df[(~df["continuous"]) & (df["temp"] == temp)]
            jitter = np.random.uniform(-JITTER_SPAN, JITTER_SPAN, len(s))
            color = RAW_COLORS.get(int(temp), next(iter(RAW_COLORS.values())))
            ax.scatter(
                temp + jitter,
                s[var],
                c=color,
                s=MARKER_SIZE,
                marker=MARKER,
                label=f"raw {int(temp)}\N{DEGREE SIGN}C",
            )

    if PLOT_MODE in ("processed", "both"):
        sub = df[df["continuous"]].sort_values("temp")
        if not sub.empty:
            med = sub[var].rolling(MED_WINDOW, center=True, min_periods=1).median()
            proc = med.rolling(MA_WINDOW, center=True, min_periods=1).mean()
            ax.plot(sub["temp"], proc, color=PROC_COLOR, linewidth=PROC_LW, label=f"med{MED_WINDOW}+mwa{MA_WINDOW}")

    ax.set_xlabel("Temperature (°C)")
    ax.set_ylabel(LABELS[var])
    ax.set_title(f"{comp} {sample} {anneal} — {LABELS[var]}")
    ax.grid(True)
    ax.legend()
    fig.tight_layout()

    fname = f"{comp} {sample} {anneal} {var}.png"
    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        fig.savefig(os.path.join(out_dir, fname), dpi=300)
    return fig, fname


def main(files: List[str]) -> None:
    data = load_data(files)
    data = maybe_handle_outliers(data)
    total = len(PLOT_VARS)
    progress = ProgressDialog(total) if total else None
    plots: List[Tuple[Figure, str]] = []
    for var in PLOT_VARS:
        if progress and getattr(progress, "cancelled", False):
            break
        fig, fname = plot_variable(data, var, SAVE_PLOTS, OUTPUT_DIR)
        plots.append((fig, fname))
        if progress:
            progress.update()
    if progress and not getattr(progress, "cancelled", False):
        progress.destroy()
    elif progress and getattr(progress, "cancelled", False):
        plt.close("all")
        print("Cancelled.")
        return
    if SHOW_PLOTS:
        plt.show()
    else:
        plt.close("all")

    if (not SAVE_PLOTS) and plots and QtWidgets.QApplication.instance() is not None:
        reply = QtWidgets.QMessageBox.question(
            None,
            "Save Plots",
            "Save generated plots?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            out = QtWidgets.QFileDialog.getExistingDirectory(None, "Select output directory", str(OUTPUT_DIR))
            if out:
                os.makedirs(out, exist_ok=True)
                for fig, fname in plots:
                    fig.savefig(os.path.join(out, fname), dpi=300)

    print("Done.")
