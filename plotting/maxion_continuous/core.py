import os
import re
from pathlib import Path
from typing import List, Tuple, Any

from PyQt6 import QtWidgets

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from ..common import maybe_handle_outliers_series
from ..utils import save_figure, origin_session, show_plots
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
SHOW_LEGEND = _CFG.get("SHOW_LEGEND", True)
LEGEND_SIZE = _CFG.get("LEGEND_SIZE", 18)
LEGEND_ORIENTATION = _CFG.get("LEGEND_ORIENTATION", "auto")  # 'auto', 'horizontal', 'vertical'
LEGEND_SHOW_SYMBOLS = _CFG.get("LEGEND_SHOW_SYMBOLS", False)
LEGEND_SYMBOL_SIZE = _CFG.get("LEGEND_SYMBOL_SIZE", 10)
TICK_SIZE = _CFG.get("TICK_SIZE", 18)
AXIS_LABEL_SIZE = _CFG.get("AXIS_LABEL_SIZE", 18)
TITLE_SIZE = _CFG.get("TITLE_SIZE", 22)
SHOW_TICK_LABELS = _CFG.get("SHOW_TICK_LABELS", True)
SHOW_AXIS_LABELS = _CFG.get("SHOW_AXIS_LABELS", True)
SHOW_TITLE = _CFG.get("SHOW_TITLE", True)
SCALE_X_1E4 = _CFG.get("SCALE_X_1E4", True)
SCALE_Y_1E3 = _CFG.get("SCALE_Y_1E3", True)
CENTER_MEDIAN_Y = _CFG.get("CENTER_MEDIAN_Y", False)
CENTER_MEDIAN_SOURCE = _CFG.get("CENTER_MEDIAN_SOURCE", "raw")
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
            "axes.titlesize": TITLE_SIZE,
            "axes.labelsize": AXIS_LABEL_SIZE,
            "legend.fontsize": LEGEND_SIZE,
            "xtick.labelsize": TICK_SIZE,
            "ytick.labelsize": TICK_SIZE,
        }
        if IMPROVE_READABILITY
        else {}
    )
    with plt.rc_context(rc):
        fig, ax = plt.subplots(figsize=(9, 4))
        proc = None
        if PLOT_MODE in ("processed", "both") or (CENTER_MEDIAN_Y and CENTER_MEDIAN_SOURCE == "processed"):
            med = y.rolling(MED_WINDOW, center=True, min_periods=1).median()
            proc = med.rolling(MA_WINDOW, center=True, min_periods=1).mean()
        offset = 0.0
        if CENTER_MEDIAN_Y:
            if CENTER_MEDIAN_SOURCE == "raw":
                offset = float(y.median())
            else:
                offset = float(proc.median()) if proc is not None else 0.0
            y = y - offset
            if proc is not None:
                proc = proc - offset
        x = np.arange(len(y))
        x_label = "Sample index"
        if IMPROVE_READABILITY and SCALE_X_1E4:
            x = x / 1e4
            x_label += " (×10⁴)"
        artists: list[Any] = []
        labels: list[str] = []
        y_vals = y.to_numpy()
        if IMPROVE_READABILITY and SCALE_Y_1E3:
            y_vals = y_vals / 1e3
        if PLOT_MODE in ("raw", "both"):
            sc = ax.scatter(x, y_vals, s=MARKER_SIZE, label="raw")
            artists.append(sc); labels.append("raw")
        if PLOT_MODE in ("processed", "both") and proc is not None:
            proc_vals = proc.to_numpy()
            if IMPROVE_READABILITY and SCALE_Y_1E3:
                proc_vals = proc_vals / 1e3
            sc = ax.scatter(x, proc_vals, s=MARKER_SIZE, label=f"med{MED_WINDOW}+mwa{MA_WINDOW}")
            artists.append(sc); labels.append(f"med{MED_WINDOW}+mwa{MA_WINDOW}")
        y_label = "T1+T2 (arb. u.)"
        if IMPROVE_READABILITY and SCALE_Y_1E3:
            y_label = "T1+T2 (arb. u., ×10³)"
        if IMPROVE_READABILITY:
            if SHOW_AXIS_LABELS:
                ax.set_xlabel(x_label)
                ax.set_ylabel(y_label)
            else:
                ax.set_xlabel("")
                ax.set_ylabel("")
            if not SHOW_TICK_LABELS:
                ax.tick_params(labelbottom=False, labelleft=False)
            if SHOW_TITLE:
                ax.set_title(f"Head {head} — {coils} coils — CH{ch} T1+T2")
            else:
                ax.set_title("")
        else:
            ax.set_xlabel(x_label)
            ax.set_ylabel(y_label)
            ax.set_title(f"Head {head} — {coils} coils — CH{ch} T1+T2")
        ax.grid(True)
        if SHOW_LEGEND and artists:
            ncol = 1
            if LEGEND_ORIENTATION == "horizontal":
                ncol = len(labels)
            elif LEGEND_ORIENTATION == "auto" and len(labels) > 3:
                ncol = len(labels)
            leg = ax.legend(
                artists,
                labels,
                ncol=ncol,
                handlelength=1 if LEGEND_SHOW_SYMBOLS else 0,
                handletextpad=0,
            )
            for handle, text in zip(leg.legend_handles, leg.get_texts()):
                color = (
                    handle.get_facecolor()[0]
                    if hasattr(handle, "get_facecolor")
                    else handle.get_color()
                )
                text.set_color(color)
                if not LEGEND_SHOW_SYMBOLS:
                    handle.set_visible(False)
                else:
                    try:
                        handle.set_sizes([LEGEND_SYMBOL_SIZE])
                    except Exception:
                        try:
                            handle.set_markersize(LEGEND_SYMBOL_SIZE)
                        except Exception:
                            pass
        fig.tight_layout()
    fname = f"head{head}_{coils}coils_CH{ch}_sum"
    if SAVE_PLOTS:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        save_figure(fig, os.path.join(OUTPUT_DIR, fname), SAVE_FORMAT, PNG_DPI)
    return fig, f"{fname}.{SAVE_FORMAT}"


def plot_channel_origin(y: pd.Series, head: int, coils: int, ch: int) -> None:
    with origin_session() as op:
        proc = None
        if PLOT_MODE in ("processed", "both") or (CENTER_MEDIAN_Y and CENTER_MEDIAN_SOURCE == "processed"):
            med = y.rolling(MED_WINDOW, center=True, min_periods=1).median()
            proc = med.rolling(MA_WINDOW, center=True, min_periods=1).mean()
        offset = 0.0
        if CENTER_MEDIAN_Y:
            if CENTER_MEDIAN_SOURCE == "raw":
                offset = float(y.median())
            else:
                offset = float(proc.median()) if proc is not None else 0.0
            y = y - offset
            if proc is not None:
                proc = proc - offset
        x = np.arange(len(y))
        if IMPROVE_READABILITY and SCALE_X_1E4:
            x = x / 1e4
        x_list = x.tolist()
        y_vals = y.to_numpy()
        if IMPROVE_READABILITY and SCALE_Y_1E3:
            y_vals = y_vals / 1e3
        book = op.new_book('w', lname="Maxion (Python)")
        book.activate()
        gp = op.new_graph(template='scatter')
        gl = gp[0]

        # Raw
        w_raw = op.new_sheet('w', lname='raw')
        w_raw.from_list(0, x_list)
        w_raw.from_list(1, y_vals.tolist())
        w_raw.cols_axis('XY')
        gl.add_plot(w_raw, coly=1, colx=0, type='s')

        # Processed
        if PLOT_MODE in ("processed", "both") and proc is not None:
            proc_vals = proc.to_numpy()
            if IMPROVE_READABILITY and SCALE_Y_1E3:
                proc_vals = proc_vals / 1e3
            w_proc = op.new_sheet('w', lname='proc')
            w_proc.from_list(0, x_list)
            w_proc.from_list(1, proc_vals.tolist())
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
            x_lab = "Sample index (x10^4)" if IMPROVE_READABILITY and SCALE_X_1E4 else "Sample index"
            y_lab = "T1+T2 (arb. u., x10^3)" if IMPROVE_READABILITY and SCALE_Y_1E3 else "T1+T2 (arb. u.)"
            op.lt_exec(f'lab -xb "{x_lab}";')
            op.lt_exec(f'lab -yl "{y_lab}";')
            esc = (f"Head {head} - {coils} coils - CH{ch} T1+T2").replace('"', "'")
            op.lt_exec(f'title -s "{esc}";')
            if SHOW_LEGEND:
                op.lt_exec('legend -o; legend.textcolor=1;')
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
        show_plots()
    else:
        plt.close('all')

    print('Done.')
