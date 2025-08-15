import os
import re
from pathlib import Path
from typing import List, Tuple

from PyQt6 import QtWidgets

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from ..common import maybe_handle_outliers_series
from ..utils import save_figure

OUTPUT_DIR = os.getcwd()
SHOW_PLOTS = True
SAVE_PLOTS = False
PLOT_MODE = "both"  # 'raw', 'processed', 'both'
MARKER_SIZE = 0.1
MED_WINDOW = 5
MA_WINDOW = 20
SAVE_FORMAT = "png"
PNG_DPI = 1000


class ProgressDialog:
    """Fallback progress indicator when no GUI dialog is provided."""

    def __init__(self, total: int):
        self.total = total
        self.count = 0
        self.cancelled = False
        self.root = self

    def update(self) -> None:
        self.count += 1

    def destroy(self) -> None:
        pass

NAME_RE = re.compile(r"^(?P<head>[1-6])\s.*\s(?P<coils>[23])\s(?:coils|cievky)$", re.IGNORECASE)


def parse_name(stem: str) -> tuple[int, int]:
    m = NAME_RE.match(stem)
    if not m:
        raise ValueError(f"Unrecognized file name: {stem}")
    return int(m.group("head")), int(m.group("coils"))


def load_file(path: str) -> pd.DataFrame:
    cols = [f"ch{i}_{t}" for i in range(1, 4) for t in ("t1", "t2")]
    return pd.read_csv(path, sep=";", header=None, names=cols, engine="python", on_bad_lines="skip")


def load_data(files: List[str]) -> pd.DataFrame:
    files = sorted(files)
    dfs: List[pd.DataFrame] = []
    for fn in files:
        head, coils = parse_name(Path(fn).stem)
        df = load_file(fn)
        for ch in (1, 2, 3):
            y = df[f"ch{ch}_t1"] + df[f"ch{ch}_t2"]
            sub = pd.DataFrame(
                {
                    "sum": y,
                    "line": np.arange(len(y)),
                    "filename": f"{Path(fn).name}_CH{ch}",
                    "head": head,
                    "coils": coils,
                    "channel": ch,
                }
            )
            dfs.append(sub)
    if not dfs:
        raise FileNotFoundError("No files selected")
    return pd.concat(dfs, ignore_index=True)


def plot_channel(y: pd.Series, head: int, coils: int, ch: int) -> Tuple[Figure, str]:
    fig, ax = plt.subplots(figsize=(9, 4))
    x = np.arange(len(y))
    if PLOT_MODE in ("raw", "both"):
        ax.scatter(x, y.to_numpy(), s=MARKER_SIZE, label="raw")
    if PLOT_MODE in ("processed", "both"):
        med = y.rolling(MED_WINDOW, center=True, min_periods=1).median()
        proc = med.rolling(MA_WINDOW, center=True, min_periods=1).mean()
        ax.scatter(x, proc.to_numpy(), s=MARKER_SIZE, label=f"med{MED_WINDOW}+mwa{MA_WINDOW}")
    ax.set_xlabel("Sample index")
    ax.set_ylabel("T1+T2 (arb units)")
    ax.set_title(f"Head {head} — {coils} coils — CH{ch} T1+T2")
    ax.grid(True)
    ax.legend()
    fig.tight_layout()
    fname = f"head{head}_{coils}coils_CH{ch}_sum"
    if SAVE_PLOTS:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        save_figure(fig, os.path.join(OUTPUT_DIR, fname), SAVE_FORMAT, PNG_DPI)
    return fig, f"{fname}.{SAVE_FORMAT}"


def main(files: List[str]):
    total = len(files) * 3
    progress = ProgressDialog(total) if total else None
    plots: List[Tuple[Figure, str]] = []
    for path in files:
        head, coils = parse_name(Path(path).stem)
        df = load_file(path)
        for ch in (1, 2, 3):
            if progress and getattr(progress, 'cancelled', False):
                break
            y = df[f"ch{ch}_t1"] + df[f"ch{ch}_t2"]
            y = maybe_handle_outliers_series(y, Path(path).name)
            fig, fname = plot_channel(y, head, coils, ch)
            plots.append((fig, fname))
            if progress:
                progress.update()
        if progress and getattr(progress, 'cancelled', False):
            break
    if progress and not getattr(progress, 'cancelled', False):
        progress.destroy()
    elif progress and getattr(progress, 'cancelled', False):
        plt.close('all')
        print('Cancelled.')
        return
    if SHOW_PLOTS:
        plt.show()
    else:
        plt.close('all')

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
                    base = os.path.join(out, Path(fname).stem)
                    save_figure(fig, base, SAVE_FORMAT, PNG_DPI)

    print('Done.')
