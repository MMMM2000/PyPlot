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
from ..utils import save_figure, origin_session
from ..backends import wants_matplotlib, wants_origin
from ..config import load_config

_CFG = load_config().get("maxion_continuous", {})

OUTPUT_DIR = _CFG.get("OUTPUT_DIR", os.getcwd())
SHOW_PLOTS = _CFG.get("SHOW_PLOTS", True)
SAVE_PLOTS = _CFG.get("SAVE_PLOTS", False)
PLOT_MODE = _CFG.get("PLOT_MODE", "both")  # 'raw', 'processed', 'both'
MARKER_SIZE = _CFG.get("MARKER_SIZE", 0.1)
MED_WINDOW = _CFG.get("MED_WINDOW", 5)
MA_WINDOW = _CFG.get("MA_WINDOW", 20)
SAVE_FORMAT = _CFG.get("SAVE_FORMAT", "png")
PNG_DPI = _CFG.get("PNG_DPI", 1200)
IMPROVE_READABILITY = _CFG.get("IMPROVE_READABILITY", False)
TEXT_SIZE = _CFG.get("TEXT_SIZE", 18)
TITLE_SIZE = _CFG.get("TITLE_SIZE", 22)
BACKEND = _CFG.get("BACKEND", "matplotlib")


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
    """Load measurement files into a DataFrame for outlier detection."""
    dfs = []
    for fn in files:
        df = load_file(fn)
        for ch in (1, 2, 3):
            series = df[f"ch{ch}_t1"] + df[f"ch{ch}_t2"]
            sub = pd.DataFrame(
                {
                    "sum": series,
                    "filename": f"{Path(fn).name}_CH{ch}",
                    "line": np.arange(len(series)),
                }
            )
            dfs.append(sub)
    if not dfs:
        raise FileNotFoundError("No files selected")
    return pd.concat(dfs, ignore_index=True)


def plot_channel(y: pd.Series, head: int, coils: int, ch: int) -> Tuple[Figure, str]:
    rc = (
        {
            "font.size": TEXT_SIZE,
            "axes.titlesize": TITLE_SIZE,
            "axes.labelsize": TEXT_SIZE,
            "legend.fontsize": TEXT_SIZE,
            "xtick.labelsize": TEXT_SIZE,
            "ytick.labelsize": TEXT_SIZE,
        }
        if IMPROVE_READABILITY
        else {}
    )
    with plt.rc_context(rc):
        fig, ax = plt.subplots(figsize=(9, 4))
        x = np.arange(len(y)) / 1e4
        if PLOT_MODE in ("raw", "both"):
            ax.scatter(x, y.to_numpy() / 1e3, s=MARKER_SIZE, label="raw")
        if PLOT_MODE in ("processed", "both"):
            med = y.rolling(MED_WINDOW, center=True, min_periods=1).median()
            proc = med.rolling(MA_WINDOW, center=True, min_periods=1).mean()
            ax.scatter(x, proc.to_numpy() / 1e3, s=MARKER_SIZE, label=f"med{MED_WINDOW}+mwa{MA_WINDOW}")
        ax.set_xlabel("Sample index (×10⁴)")
        ax.set_ylabel("T1+T2 (arb units, ×10³)")
        ax.set_title(f"Head {head} — {coils} coils — CH{ch} T1+T2")
        ax.grid(True)
        ax.legend()
        fig.tight_layout()
    fname = f"head{head}_{coils}coils_CH{ch}_sum"
    if SAVE_PLOTS:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        save_figure(fig, os.path.join(OUTPUT_DIR, fname), SAVE_FORMAT, PNG_DPI)
    return fig, f"{fname}.{SAVE_FORMAT}"


def plot_channel_origin(y: pd.Series, head: int, coils: int, ch: int) -> None:
    with origin_session() as op:
        x = (np.arange(len(y)) / 1e4).tolist()
        book = op.new_book('w', lname="Maxion (Python)")
        book.activate()
        gp = op.new_graph(template='scatter')
        gl = gp[0]

        # Raw
        w_raw = op.new_sheet('w', lname='raw')
        w_raw.from_list(0, x)
        w_raw.from_list(1, (y.to_numpy() / 1e3).tolist())
        w_raw.cols_axis('XY')
        gl.add_plot(w_raw, coly=1, colx=0, type='s')

        # Processed
        if PLOT_MODE in ("processed", "both"):
            med = y.rolling(MED_WINDOW, center=True, min_periods=1).median()
            proc = med.rolling(MA_WINDOW, center=True, min_periods=1).mean()
            w_proc = op.new_sheet('w', lname='proc')
            w_proc.from_list(0, x)
            w_proc.from_list(1, (proc.to_numpy() / 1e3).tolist())
            w_proc.cols_axis('XY')
            p = gl.add_plot(w_proc, coly=1, colx=0, type='y')
            try:
                p.line_width = 1
            except Exception:
                pass

        try:
            gp.activate()
            op.lt_exec('page.antialias=1;')
            op.lt_exec('layer -aa 1;')
            op.lt_exec('lab -xb "Sample index (x10^4)";')
            op.lt_exec('lab -yl "T1+T2 (arb units, x10^3)";')
            esc = (f"Head {head} - {coils} coils - CH{ch} T1+T2").replace('"', "'")
            op.lt_exec(f'title -s "{esc}";')
            op.lt_exec('legend;')
        except Exception:
            pass


def main(files: List[str], backend: str = BACKEND):
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
            if wants_matplotlib(backend):
                fig, fname = plot_channel(y, head, coils, ch)
                plots.append((fig, fname))
            if wants_origin(backend):
                try:
                    plot_channel_origin(y, head, coils, ch)
                except Exception as e:
                    print(f"Origin plot failed: {e}")
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

    print('Done.')
