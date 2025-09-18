from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Tuple, cast

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from ..utils import save_figure, format_annealing_title, show_plots, apply_readability_fonts, apply_readability
from ..backends import wants_matplotlib, wants_origin

# Defaults
OUTPUT_DIR = os.getcwd()
SHOW_PLOTS = True
SAVE_PLOTS = False
SAVE_FORMAT = "png"
PNG_DPI = 1200
BACKEND = "matplotlib"
IMPROVE_READABILITY = True
SHOW_LEGEND = True
LEGEND_SIZE = 18
LEGEND_ORIENTATION = "auto"
LEGEND_SHOW_SYMBOLS = False
LEGEND_SYMBOL_SIZE = 10.0
TICK_SIZE = 18
AXIS_LABEL_SIZE = 18
TITLE_SIZE = 22
SHOW_TICK_LABELS = True
SHOW_AXIS_LABELS = True
SHOW_TITLE = True


def load_file(path: str) -> pd.DataFrame:
    """Load current annealing tri-column file: I(A) V(V) R(Ohm).

    Returns a DataFrame with I_mA and R_Ohm columns.
    """
    df = pd.read_csv(path, sep=None, engine="python", header=None, comment="#")
    if df.shape[1] < 3:
        raise ValueError(f"{path}: expected at least 3 columns (I, V, R)")
    df = df.iloc[:, :3]
    df.columns = ["I_A", "V_V", "R_Ohm"]
    df["I_A"] = df["I_A"].astype(float)
    df["I_mA"] = df["I_A"] * 1e3
    df["R_Ohm"] = df["R_Ohm"].astype(float)
    df = df[df["I_mA"] != 0].reset_index(drop=True)
    return df[["I_mA", "R_Ohm"]]


def plot_one(df: pd.DataFrame, title: str) -> Tuple[Figure, str]:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(df["I_mA"], df["R_Ohm"], "-o", markersize=3)
    ax.set_xlabel("Current (mA)")
    ax.set_ylabel("Resistance (Ohm)")
    ax.set_title(title)
    ax.grid(True, ls="--", alpha=0.3)
    fig.tight_layout()
    apply_readability(ax, globals())
    fname = title.replace(os.sep, "_")
    return fig, fname


def plot_one_origin(df: pd.DataFrame, title: str) -> None:
    import originpro as op  # lazy import
    origin_any: Any = cast(Any, op)
    try:
        origin_any.set_show()
    except Exception:
        pass
    w: Any = origin_any.new_sheet('w', lname=title[:30])
    w.from_list(0, df["I_mA"].to_list())
    w.from_list(1, df["R_Ohm"].to_list())
    w.cols_axis('XY')
    gp: Any = origin_any.new_graph(template='scatter')
    gl: Any = gp[0]
    plot_obj: Any = gl.add_plot(w, coly=1, colx=0, type='y')
    if plot_obj is not None:
        p: Any = plot_obj
        try:
            p.symbol_shape = 2
            p.line_connect = 1
        except Exception:
            pass
    try:
        gp.activate()
        esc = title.replace('"', "'")
        origin_any.lt_exec('page.antialias=1; layer -aa 1;')
        origin_any.lt_exec('lab -xb "Current (mA)"; lab -yl "Resistance (Ohm)";')
        label_method = getattr(gl, "label", None)
        if callable(label_method):
            legend_label = label_method('Legend')
            if legend_label is not None and hasattr(legend_label, "text"):
                try:
                    legend_label.text = esc
                except Exception:
                    pass
        origin_any.lt_exec('legend.update=0;')
    except Exception:
        pass

    try:
        origin_any.exit()
    except Exception:
        pass


def main(files: List[str], backend: str = BACKEND) -> None:
    if IMPROVE_READABILITY:
        apply_readability_fonts()
    outs: List[Tuple[Figure, str]] = []
    for path in files:
        df = load_file(path)
        title = format_annealing_title(Path(path).stem)
        if wants_matplotlib(backend):
            fig, fname = plot_one(df, title)
            outs.append((fig, fname))
        if wants_origin(backend):
            try:
                plot_one_origin(df, title)
            except Exception as e:
                print(f"Origin plot failed for {title}: {e}")

    if wants_matplotlib(backend):
        if SHOW_PLOTS:
            show_plots()
        else:
            plt.close('all')
        if SAVE_PLOTS and outs:
            Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
            for fig, fname in outs:
                save_figure(fig, Path(OUTPUT_DIR) / fname, SAVE_FORMAT, PNG_DPI)

