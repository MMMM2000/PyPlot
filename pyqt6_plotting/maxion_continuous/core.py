import os
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple

from PyQt6 import QtWidgets

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

OUTPUT_DIR = os.getcwd()
SHOW_PLOTS = True
SAVE_PLOTS = False
PLOT_MODE = "both"  # 'raw', 'processed', 'both'
MARKER_SIZE = 0.1
MED_WINDOW = 5
MA_WINDOW = 20


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


def plot_channel(y: pd.Series, head: int, coils: int, ch: int) -> Tuple[plt.Figure, str]:
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
    fname = f"head{head}_{coils}coils_CH{ch}_sum.png"
    if SAVE_PLOTS:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        fig.savefig(os.path.join(OUTPUT_DIR, fname), dpi=300)
    return fig, fname


def main(files: List[str]):
    total = len(files) * 3
    progress = ProgressDialog(total) if total else None
    plots: List[Tuple[plt.Figure, str]] = []
    for path in files:
        head, coils = parse_name(Path(path).stem)
        df = load_file(path)
        for ch in (1, 2, 3):
            if progress and getattr(progress, 'cancelled', False):
                break
            y = df[f"ch{ch}_t1"] + df[f"ch{ch}_t2"]
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
                    fig.savefig(os.path.join(out, fname), dpi=300)

    print('Done.')
