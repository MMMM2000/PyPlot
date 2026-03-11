import logging
import os
from pathlib import Path
from typing import List, Dict, Any, Tuple, cast
import re

from PyQt6 import QtWidgets

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from plotting.shared.config import load_config
from plotting.shared.common import maybe_handle_outliers
from plotting.shared.utils import save_figure, show_plots
from plotting.shared.origin import origin_session, set_origin_axis_title, set_origin_graph_title
from plotting.shared.readability import apply_readability_fonts, apply_readability
from plotting.shared.backends import wants_matplotlib, wants_origin

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
SAVE_FORMAT = _CFG.get("SAVE_FORMAT", "png")
PNG_DPI = int(_CFG.get("PNG_DPI", 1200))
MAX_SHOW = 8
BACKEND = str(_CFG.get("BACKEND", "matplotlib"))
IMPROVE_READABILITY = False
SHOW_LEGEND = bool(_CFG.get("SHOW_LEGEND", True))
LEGEND_SIZE = int(_CFG.get("LEGEND_SIZE", 18))
LEGEND_ORIENTATION = str(_CFG.get("LEGEND_ORIENTATION", "auto"))
LEGEND_SHOW_SYMBOLS = bool(_CFG.get("LEGEND_SHOW_SYMBOLS", False))
LEGEND_SYMBOL_SIZE = float(_CFG.get("LEGEND_SYMBOL_SIZE", 10))
TICK_SIZE = int(_CFG.get("TICK_SIZE", 18))
AXIS_LABEL_SIZE = int(_CFG.get("AXIS_LABEL_SIZE", 18))
TITLE_SIZE = int(_CFG.get("TITLE_SIZE", 22))
SHOW_TICK_LABELS = bool(_CFG.get("SHOW_TICK_LABELS", True))
SHOW_AXIS_LABELS = bool(_CFG.get("SHOW_AXIS_LABELS", True))
SHOW_TITLE = bool(_CFG.get("SHOW_TITLE", True))

logger = logging.getLogger("PyPlot.temperature_dependence")

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
    r"(?P<temp>\d+(?:-\d+)?C)$"
)

LABELS = {
    "T1": "T1 (µs)",
    "T2": "T2 (µs)",
    "dT": "T2–T1 (µs)",
    "sum": "T1+T2 (µs)",
}

_EXPORT_ORDER = ("sum", "dT", "T1", "T2")


def _sanitise_stem(*parts: str) -> str:
    stem = "_".join(part.strip().replace(" ", "_") for part in parts if part)
    return re.sub(r"[^A-Za-z0-9_.-]", "_", stem) or "temperature_dependence"


def _summarise_samples(samples: pd.Series | None) -> str:
    if samples is None:
        return ""
    try:
        uniques = [str(val) for val in pd.unique(samples.dropna()) if str(val)]
    except Exception:
        uniques = [str(val) for val in pd.unique(samples) if str(val)]
    if not uniques:
        return ""
    if len(uniques) == 1:
        return uniques[0]
    head = uniques[:3]
    suffix = "" if len(uniques) <= 3 else "_etc"
    return "-".join(head) + suffix

def parse_metadata(stem: str) -> Dict[str, Any] | None:
    m = NAME_RE.match(stem)
    if not m:
        return None
    md = m.groupdict()
    temp = md["temp"].lower()
    md["continuous"] = "-" in temp
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
            logger.warning(f"Skipping {fn}")
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
                label="raw 25-100C",
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
    apply_readability(ax, globals())

    fname = f"{comp} {sample} {anneal} {var}"
    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        save_figure(fig, os.path.join(out_dir, fname), SAVE_FORMAT, PNG_DPI)
    return fig, f"{fname}.{SAVE_FORMAT}"


def plot_variable_origin(df: pd.DataFrame, var: str) -> None:
    """Create an Origin graph matching the Matplotlib style."""

    with origin_session(keep_open=True) as op:
        comp = df["composition"].iat[0]
        sample = df["sample"].iat[0]
        anneal = df["anneal"].iat[0]

        # Prepare data tables
        raw_cont = df[df["continuous"]].sort_values("temp")[["temp", var]].rename(columns={"temp": "X", var: "Y"})
        raw_disc = df[~df["continuous"]][["temp", var]].copy()
        if not raw_disc.empty:
            jitter = np.random.uniform(-JITTER_SPAN, JITTER_SPAN, len(raw_disc))
            raw_disc["X"] = raw_disc["temp"].astype(float) + jitter
            raw_disc["Y"] = raw_disc[var].astype(float)
            raw_disc = raw_disc[["X", "Y"]]
        # Processed line
        proc = pd.DataFrame(columns=["X", "Y"])
        if not raw_cont.empty and PLOT_MODE in ("processed", "both"):
            sub = raw_cont.copy()
            med = sub["Y"].rolling(MED_WINDOW, center=True, min_periods=1).median()
            sub["Y"] = med.rolling(MA_WINDOW, center=True, min_periods=1).mean()
            proc = sub.rename(columns={"temp": "X"})

        # Push to Origin and build the graph
        book = op.new_book('w', lname="Temp Dependence (Python)")
        book.activate()
        gp = op.new_graph(template='line')
        gl = gp[0]

        if PLOT_MODE in ("raw", "both") and not raw_cont.empty:
            w_cont = cast(Any, op.new_sheet('w', lname="raw_cont"))
            w_cont.from_df(raw_cont)
            w_cont.cols_axis('XY')
            p_cont = cast(Any, gl.add_plot(w_cont, coly=1, colx=0, type='s'))
            try:
                p_cont.color = OVERALL_COLOR
                p_cont.symbol_shape = 2  # circle
                p_cont.legend = "raw 25-100C"
                p_cont.lname = "raw 25-100C"
            except Exception:
                pass
        if PLOT_MODE in ("raw", "both") and not raw_disc.empty:
            w_disc = cast(Any, op.new_sheet('w', lname="raw_disc"))
            w_disc.from_df(raw_disc)
            w_disc.cols_axis('XY')
            p_disc = cast(Any, gl.add_plot(w_disc, coly=1, colx=0, type='s'))
            try:
                p_disc.color = RAW_COLORS.get(25, '#45A1D6')
                disc_label = "raw discrete"
                p_disc.legend = disc_label
                p_disc.lname = disc_label
            except Exception:
                pass
        if PLOT_MODE in ("processed", "both") and not proc.empty:
            w_proc = cast(Any, op.new_sheet('w', lname="processed"))
            w_proc.from_df(proc)
            w_proc.cols_axis('XY')
            p_proc = cast(Any, gl.add_plot(w_proc, coly=1, colx=0, type='y'))
            try:
                p_proc.color = PROC_COLOR
                p_proc.line_width = PROC_LW
                proc_label = f"med{MED_WINDOW}+mwa{MA_WINDOW}"
                p_proc.legend = proc_label
                p_proc.lname = proc_label
            except Exception:
                pass

        try:
            gl.rescale()
            gp.activate()
            op.lt_exec('page.antialias=1;')
            op.lt_exec('layer -aa 1;')
            op.lt_exec('legend;')
        except Exception:
            pass
        for attr in (
            'x.showAxes', 'y.showAxes',
            'x.showlabel', 'x2.showlabel',
            'y.showlabel', 'y2.showlabel',
        ):
            try:
                gl.set_int(attr, 1 if attr in {'x.showAxes', 'y.showAxes', 'x.showlabel', 'y.showlabel'} else 0)
            except Exception:
                continue
        for cmd in (
            'layer.x.showAxes=1;',
            'layer.y.showAxes=1;',
            'layer.x.showlabel=1;',
            'layer.x2.showlabel=0;',
            'layer.y.showlabel=1;',
            'layer.y2.showlabel=0;',
        ):
            try:
                op.lt_exec(cmd)
            except Exception:
                continue
        legend_lines: list[str] = []
        if PLOT_MODE in ("raw", "both") and not raw_cont.empty:
            legend_lines.append("\\c(1) raw 25-100C")
        if PLOT_MODE in ("raw", "both") and not raw_disc.empty:
            legend_lines.append(f"\\c({len(legend_lines) + 1}) raw discrete")
        if PLOT_MODE in ("processed", "both") and not proc.empty:
            legend_lines.append(f"\\c({len(legend_lines) + 1}) med{MED_WINDOW}+mwa{MA_WINDOW}")
        if legend_lines:
            try:
                legend = gl.label('Legend')
                legend.text = "\n".join(legend_lines)
                op.lt_exec('legend.update=0;')
            except Exception:
                pass
        for token in ('xt', 'XT', 'yr', 'YR'):
            try:
                label = gl.label(token)
            except Exception:
                label = None
            if label is None:
                continue
            try:
                label.text = ""
            except Exception:
                pass
            try:
                label.set_int('show', 0)
            except Exception:
                pass
        try:
            set_origin_axis_title(gl, 'x', 'Temperature (\u00B0C)')
            set_origin_axis_title(gl, 'y', LABELS[var])
            set_origin_graph_title(op, gp, gl, f"{comp} {sample} {anneal} - {LABELS[var]}")
        except Exception:
            pass


def export_group_to_txt(
    grp: pd.DataFrame,
    directory: str | Path,
    *,
    include_processed: bool = True,
    med_window: int = MED_WINDOW,
    ma_window: int = MA_WINDOW,
) -> Path:
    """Persist raw and processed temperature dependence tables to ``directory``."""

    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    work = grp.copy()
    for column in ("composition", "anneal", "sample"):
        if column in work.columns:
            work[column] = work[column].astype(str)
    if "filename" in work.columns:
        work["filename"] = work["filename"].astype(str)
    work["temp"] = pd.to_numeric(work.get("temp"), errors="coerce")
    work["line"] = pd.to_numeric(work.get("line"), errors="coerce")
    work["continuous"] = work.get("continuous", False).astype(bool)

    sort_keys = [col for col in ("sample", "continuous", "temp", "line") if col in work.columns]
    if sort_keys:
        work.sort_values(sort_keys, inplace=True, na_position="last")

    if include_processed and work["continuous"].any():
        cont = work[work["continuous"]].copy()
        cont.sort_values(["sample", "temp", "line"], inplace=True)
        for var in _EXPORT_ORDER:
            if var not in cont.columns:
                continue
            med = cont.groupby("sample")[var].transform(
                lambda series: series.rolling(med_window, center=True, min_periods=1).median()
            )
            smooth = med.groupby(cont["sample"]).transform(
                lambda series: series.rolling(ma_window, center=True, min_periods=1).mean()
            )
            work.loc[cont.index, f"{var}_median"] = med
            work.loc[cont.index, f"{var}_smoothed"] = smooth

    columns: list[str] = [
        col
        for col in ("composition", "sample", "anneal", "continuous", "temp", "filename", "line")
        if col in work.columns
    ]
    for var in _EXPORT_ORDER:
        if var in work.columns:
            columns.append(var)
    for var in _EXPORT_ORDER:
        for suffix in ("median", "smoothed"):
            column = f"{var}_{suffix}"
            if column in work.columns:
                columns.append(column)

    export_df = work[columns].copy()
    comp_value = str(export_df["composition"].iat[0]) if "composition" in export_df else ""
    anneal_value = str(export_df["anneal"].iat[0]) if "anneal" in export_df else ""
    sample_value = _summarise_samples(export_df.get("sample")) if "sample" in export_df else ""

    stem = _sanitise_stem("temperature_dependence", comp_value, sample_value, anneal_value)
    path = out_dir / f"{stem}.txt"
    export_df.to_csv(path, sep="\t", index=False, float_format="%.10g")
    return path


def main(files: List[str], backend: str = BACKEND) -> None:
    if IMPROVE_READABILITY:
        apply_readability_fonts()
    data = load_data(files)
    data = maybe_handle_outliers(data)
    total = len(PLOT_VARS)
    progress = ProgressDialog(total) if total else None
    plots: List[Tuple[Figure, str]] = []
    for var in PLOT_VARS:
        if progress and getattr(progress, "cancelled", False):
            break
        if wants_matplotlib(backend):
            fig, fname = plot_variable(data, var, SAVE_PLOTS, OUTPUT_DIR)
            plots.append((fig, fname))
        if wants_origin(backend):
            try:
                plot_variable_origin(data, var)
            except Exception as e:
                logger.error(f"Origin plot failed: {e}")
        if progress:
            progress.update()
    if progress and not getattr(progress, "cancelled", False):
        progress.destroy()
    elif progress and getattr(progress, "cancelled", False):
        plt.close("all")
        logger.info("Cancelled.")
        return
    if wants_matplotlib(backend) and SHOW_PLOTS:
        show_plots()
    else:
        plt.close("all")

    logger.info("Done.")
