import logging
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple, cast

from PyQt6 import QtWidgets

from plotting.shared.config import load_config
from plotting.shared.utils import save_figure, show_plots
from plotting.shared.readability import apply_readability_fonts, apply_readability
from plotting.shared.backends import wants_matplotlib, wants_origin

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.colors import to_hex
from matplotlib.collections import PathCollection
from matplotlib.typing import ColorType
from plotting.shared.common import maybe_handle_outliers
from matplotlib.figure import Figure

# Ensure a font with broad Unicode coverage so arrows and micro symbols render
plt.rcParams["font.family"] = "DejaVu Sans"

# Load default configuration
_CFG = load_config().get("stress_dependence", {})
OUTPUT_DIR = _CFG.get("OUTPUT_DIR", os.getcwd())
PLOT_SUM = bool(_CFG.get("PLOT_SUM", True))
PLOT_DT = bool(_CFG.get("PLOT_DT", True))
PLOT_T1 = bool(_CFG.get("PLOT_T1", True))
PLOT_T2 = bool(_CFG.get("PLOT_T2", True))
PLOT_VARS = [
    v for v, b in [("sum", PLOT_SUM), ("dT", PLOT_DT), ("T1", PLOT_T1), ("T2", PLOT_T2)] if b
]
BASELINE_MODE = str(_CFG.get("BASELINE_MODE", "first"))  # 'first' or 'min'
RAW_COLORS = {"a": "#45A1D6", "b": "#F09C67"}
RAW_MARKER = "o"

logger = logging.getLogger("PyPlot.stress_dependence")
RAW_MARKER_SIZE = 0.3
RAW_ALPHA = 1.0
MEAN_COLORS = {"a":"#00306E","b":"#965308"}
MEAN_MARKER = 'o'
MEAN_MSIZE = 8
MEAN_LW = 3
LEGEND_MARKER_SIZE = 6
OFFSET = 0.5
JITTER_SPAN = 0.5
PRINT_COUNTS = False
PLOT_PROCESSED = bool(_CFG.get("PLOT_PROCESSED", False))
MED_WINDOW = int(_CFG.get("MED_WINDOW", 5))
MA_WINDOW = int(_CFG.get("MA_WINDOW", 20))
PROC_COLORS = {"a":"#E69F00","b":"#56B4E9"}
PROC_MARKER = 's'
PROC_MSIZE = 0.5
PROC_ALPHA = 0.5
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

# Origin styling knobs (roughly matching Matplotlib appearance)
# Raw symbol size (keep small as requested)
ORIGIN_RAW_SYMBOL_SIZE = 1
ORIGIN_MEAN_SYMBOL_SIZE = 8
ORIGIN_MEAN_LINE_WIDTH = 2
# Legend placement is now handled automatically in Origin; no manual offsets

# Axis/legend labels with micro symbol (use unicode escape to be safe)
LABELS = {
    "T1": "T1 (\u03BCs)",
    "T2": "T2 (\u03BCs)",
    "dT": "T2-T1 (\u03BCs)",
    "sum": "T1+T2 (\u03BCs)",
}

_EXPORT_ORDER = ("T1", "T2", "dT", "sum")


def _baseline_for_variable(grp: pd.DataFrame, var: str) -> float:
    """Return the baseline used for ``var`` according to ``BASELINE_MODE``."""

    means = grp.groupby(["dir", "load"], as_index=False).agg({var: "mean"})
    if means.empty:
        return float("nan")
    first = float(means["load"].min())
    if BASELINE_MODE == "first":
        base_series = cast(
            pd.Series,
            means.loc[(means["dir"] == "a") & (means["load"] == first), var],
        )
        return float(base_series.iloc[0]) if not base_series.empty else float("nan")
    target = means.loc[means["dir"] == "a", var]
    return float(target.min()) if not target.empty else float("nan")


def _sanitise_stem(*parts: str) -> str:
    """Return a filesystem-friendly stem constructed from ``parts``."""

    stem = "_".join(part.strip().replace(" ", "_") for part in parts if part)
    return re.sub(r"[^A-Za-z0-9_.-]", "_", stem) or "stress_dependence"

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

FNAME_RE = re.compile(
    r"^(?P<composition>.+?)\s+"
    r"(?P<title>\S+)\s+"
    r"(?P<sample_end>\S+[ab])\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<load>\d+(?:,\d+)?)(?P<dir>[ab])$"
)


def parse_metadata(stem: str) -> Dict[str, Any] | None:
    """Parse measurement metadata from a file name stem.

    Parameters
    ----------
    stem:
        File name without extension.

    Returns
    -------
    dict | None
        Dictionary with parsed fields or ``None`` if the pattern does not
        match.
    """
    m = FNAME_RE.match(stem)
    if not m:
        return None
    md = m.groupdict()
    md["load"] = float(md["load"].replace(",", "."))
    return md

def format_sample_end(sample_end: str) -> str:
    """Return a human readable representation of a sample end.

    The trailing ``a`` or ``b`` is mapped to ``marked`` or ``unmarked`` end
    respectively and shown in parentheses.  Other strings are returned as is.
    """
    if sample_end.endswith("a"):
        return f"{sample_end[:-1]} (marked end)"
    if sample_end.endswith("b"):
        return f"{sample_end[:-1]} (unmarked end)"
    return sample_end

def explain_metadata_failure(stem: str) -> str:
    """Return a human-readable reason why ``stem`` did not match ``FNAME_RE``."""

    tokens = stem.split()
    if not tokens:
        return "filename is empty after removing the extension"

    load_token = tokens[-1]
    if not re.fullmatch(r"\d+(?:,\d+)?[ab]", load_token):
        return "missing load token at the end (expected something like '150a')"

    if len(tokens) < 4:
        return (
            "expected '<composition> <title> <sample_end> <anneal> <load><dir>' — add an"
            " annealing current such as '0mA'"
        )

    sample_token = tokens[-2]
    if not sample_token.lower().endswith(("a", "b")):
        return "sample end token should end with 'a' or 'b' (for example 'SG-1a')"

    anneal_token = tokens[-3]
    if not re.search(r"\d", anneal_token):
        return "annealing current is missing digits — add a value such as '47mA'"
    if not re.search(r"(?:ma|a)$", anneal_token.lower()):
        return "annealing token should end with 'mA' or 'A' (e.g. '47mA')"

    return (
        "filename does not match '<composition> <title> <sample_end> <anneal> <load><dir>'"
        " — check for stray spaces or missing tokens"
    )


def load_data(files: List[str]) -> pd.DataFrame:
    """Load measurement files into a single DataFrame."""
    files = sorted(files)
    if not files:
        raise FileNotFoundError("No files selected")
    dfs = []
    for fn in files:
        stem = Path(fn).stem
        md = parse_metadata(stem)
        if md is None:
            reason = explain_metadata_failure(stem)
            logger.warning(f"Skipping {fn}: {reason}")
            continue
        df = pd.read_csv(
            fn,
            sep=";",
            header=None,
            names=["T1", "T2", "dT", "sum"],
            engine="python",
            on_bad_lines="skip",
        )
        df["filename"] = Path(fn).name
        df["line"] = np.arange(len(df))
        df[["T1", "T2", "dT", "sum"]] = df[["T1", "T2", "dT", "sum"]].apply(
            pd.to_numeric, errors="coerce"
        )
        for k, v in md.items():
            df[k] = v
        dfs.append(df)
    if not dfs:
        raise ValueError("No valid files selected. Check filenames.")
    return pd.concat(dfs, ignore_index=True)

def plot_variable(df: pd.DataFrame, var: str, save_flag: bool, out_dir: str) -> Tuple[Figure, str]:
    """Plot a single variable and optionally save the figure."""
    comp, title, samp, anneal = (
        df['composition'].iat[0],
        df['title'].iat[0],
        df['sample_end'].iat[0],
        df['anneal'].iat[0],
    )

    means = df.groupby(['dir','load'])[var].mean().reset_index()
    first = df['load'].min()
    if BASELINE_MODE == 'first':
        base_series = cast(
            pd.Series,
            means.loc[(means['dir'] == 'a') & (means['load'] == first), var],
        )
        base = float(base_series.iloc[0]) if not base_series.empty else float('nan')
    else:
        base = float(means.loc[means['dir'] == 'a', var].min())

    df['x_center'] = df['load'] + df['dir'].map({'a':-OFFSET,'b':+OFFSET})
    np.random.seed(0)
    df['x'] = df['x_center'] + np.random.uniform(-JITTER_SPAN, JITTER_SPAN, len(df))
    df['y'] = df[var] - base
    means['y'] = means[var] - base

    if PRINT_COUNTS:
        logger.info(f"\nCounts for {var}, {comp} {title} {samp} {anneal}:")
        counts = df.groupby(['dir','load']).size().unstack(fill_value=0)
        logger.info("\n%s", counts.to_string())

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.scatter(
        df.loc[df.dir == 'a', 'x'],
        df.loc[df.dir == 'a', 'y'],
        c=RAW_COLORS['a'],
        marker=RAW_MARKER,
        s=RAW_MARKER_SIZE,
        alpha=RAW_ALPHA,
        label='raw \u2191',
    )
    ax.scatter(
        df.loc[df.dir == 'b', 'x'],
        df.loc[df.dir == 'b', 'y'],
        c=RAW_COLORS['b'],
        marker=RAW_MARKER,
        s=RAW_MARKER_SIZE,
        alpha=RAW_ALPHA,
        label='raw \u2193',
    )

    if PLOT_PROCESSED:
        desc = f"med{MED_WINDOW}+mwa{MA_WINDOW}"
        for d in ('a','b'):
            sub = df[df.dir==d].sort_values('x').copy()
            sub['y_med'] = sub['y'].rolling(MED_WINDOW, center=True, min_periods=1).median()
            sub['y_proc'] = sub['y_med'].rolling(MA_WINDOW, center=True, min_periods=1).mean()
            ax.scatter(
                sub['x'],
                sub['y_proc'],
                c=PROC_COLORS[d],
                marker=PROC_MARKER,
                s=PROC_MSIZE,
                alpha=PROC_ALPHA,
                label=f"{desc} {'\u2191' if d == 'a' else '\u2193'}",
            )

    ax.plot(
        means.loc[means.dir == 'a', 'load'],
        means.loc[means.dir == 'a', 'y'],
        MEAN_MARKER + '-',
        c=MEAN_COLORS['a'],
        markersize=MEAN_MSIZE,
        linewidth=MEAN_LW,
        label='mean \u2191',
    )
    ax.plot(
        means.loc[means.dir == 'b', 'load'],
        means.loc[means.dir == 'b', 'y'],
        MEAN_MARKER + '-',
        c=MEAN_COLORS['b'],
        markersize=MEAN_MSIZE,
        linewidth=MEAN_LW,
        label='mean \u2193',
    )

    maxl = df['load'].max()
    delta_series = cast(
        pd.Series,
        means.loc[(means['dir'] == 'a') & (means['load'] == maxl), 'y'],
    )
    delta = float(delta_series.iloc[0]) if not delta_series.empty else float('nan')
    ax.text(
        0.95,
        0.05,
        f"\u0394={delta:.2f} \u03BCs",
        transform=ax.transAxes,
        ha='right',
        va='bottom',
        fontsize=12,
        bbox=dict(facecolor='white', alpha=0.6),
    )

    ax.set_xticks(sorted(df['load'].unique()))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:g}"))
    ax.set_xlabel('Applied load (g)')
    ax.set_ylabel(LABELS[var])
    ax.set_title(
        f"{comp} {title} {format_sample_end(samp)} {anneal} \u2014 {LABELS[var]}"
    )
    ax.grid(True)

    legend = ax.legend(loc='best')
    for text, handle in zip(legend.get_texts(), legend.legend_handles):
        if isinstance(handle, Line2D):
            rawcol = handle.get_color()
            if handle.get_markerfacecolor() and handle.get_markerfacecolor() != 'none':
                rawcol = handle.get_markerfacecolor()
            handle.set_markersize(LEGEND_MARKER_SIZE)
        elif isinstance(handle, (Patch, PathCollection)):
            rawcol = handle.get_facecolor()
            if isinstance(handle, PathCollection):
                handle.set_sizes([LEGEND_MARKER_SIZE ** 2])
            if isinstance(rawcol, np.ndarray) and rawcol.ndim > 1:
                rawcol = rawcol[0]
        else:
            rawcol = 'black'
        text.set_color(to_hex(cast(ColorType, rawcol)))

    fig.tight_layout()
    apply_readability(ax, globals())
    fname = f"{comp} {title} {samp} {anneal} {var}"
    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        save_figure(fig, os.path.join(out_dir, fname), SAVE_FORMAT, PNG_DPI)
    return fig, f"{fname}.{SAVE_FORMAT}"


def _origin_compute_tables(grp: pd.DataFrame, var: str):
    """Return jittered raw data, means and delta for Origin plotting."""

    means = grp.groupby(["dir", "load"], as_index=False).agg({var: "mean"})
    base = _baseline_for_variable(grp, var)

    rng = np.random.default_rng(0)
    x_center = grp["load"] + grp["dir"].map({"a": -OFFSET, "b": +OFFSET})
    x = x_center + rng.uniform(-JITTER_SPAN, +JITTER_SPAN, size=len(grp))
    y = grp[var] - base

    raw = pd.DataFrame({"X": x, "Y": y, "dir": grp["dir"].to_numpy()})
    raw_a = raw[raw["dir"] == "a"][ ["X", "Y"] ].reset_index(drop=True)
    raw_b = raw[raw["dir"] == "b"][ ["X", "Y"] ].reset_index(drop=True)

    means = means.sort_values(["dir", "load"]).copy()
    means["X"] = means["load"]
    means["Y"] = means[var] - base
    mean_a = means[means["dir"] == "a"][ ["X", "Y"] ].reset_index(drop=True)
    mean_b = means[means["dir"] == "b"][ ["X", "Y"] ].reset_index(drop=True)
    # Delta = last (max load) minus first (min load) for direction 'a'
    if not mean_a.empty:
        first_x = float(mean_a["X"].min())
        last_x = float(mean_a["X"].max())
        # Use raw means (no baseline) to compute actual delta
        m_a_raw = means[means["dir"] == "a"].set_index("X")
        start_series = m_a_raw.loc[m_a_raw.index == first_x, var]
        end_series = m_a_raw.loc[m_a_raw.index == last_x, var]
        if start_series.empty or end_series.empty:
            delta = float("nan")
        else:
            start_val = float(start_series.iloc[0])
            end_val = float(end_series.iloc[0])
            delta = end_val - start_val
    else:
        delta = float("nan")
    return raw_a, raw_b, mean_a, mean_b, delta


def _origin_title(grp: pd.DataFrame, var: str) -> str:
    comp, title, samp, anneal = (
        grp["composition"].iat[0],
        grp["title"].iat[0],
        grp["sample_end"].iat[0],
        grp["anneal"].iat[0],
    )
    return f"{comp} {title} {format_sample_end(samp)} {anneal} \u2014 {LABELS[var]}"


def _origin_build_graph(
    raw_a,
    raw_b,
    mean_a,
    mean_b,
    title: str,
    var: str,
    delta: float,
) -> None:
    """Create an Origin graph mirroring the Matplotlib style."""

    import originpro as op  # Imported lazily
    # Ensure Origin UI is shown
    try:
        op.set_show()
    except Exception:
        pass

    try:
        op.exit()
    except Exception:
        pass

    # Defer legend tweaking until after plots are added.


    book = cast(Any, op.new_book('w', lname="Stress Dependence (Python)"))
    try:
        book.activate()
    except Exception:
        pass

    def push_xy(df: pd.DataFrame, lname: str, legend_label: str):
        wks = cast(Any, op.new_sheet('w', lname=lname))
        wks.from_df(df)
        wks.cols_axis('XY')
        try:
            wks.activate()
            op.lt_exec(f'wks.col2.lname$ = "{legend_label}";')
        except Exception:
            pass
        return wks

    w_raw_a = push_xy(raw_a, "raw_a", "raw up")
    w_raw_b = push_xy(raw_b, "raw_b", "raw down")
    w_mean_a = push_xy(mean_a, "mean_a", "mean up")
    w_mean_b = push_xy(mean_b, "mean_b", "mean down")

    gp = cast(Any, op.new_graph(template='scatter'))
    gl = gp[0]
    # Try to set the graph title using the OriginPython API. Some templates
    # may ignore this; a LabTalk fallback is applied later.
    label_method = getattr(gl, 'label', None)
    if callable(label_method):
        try:
            title_label = label_method('Title')
        except Exception:
            title_label = None
        if title_label is not None and hasattr(title_label, 'text'):
            try:
                cast(Any, title_label).text = title
            except Exception:
                pass
    p_raw_a = gl.add_plot(w_raw_a, coly=1, colx=0, type='s')
    p_raw_b = gl.add_plot(w_raw_b, coly=1, colx=0, type='s')
    p_mean_a = gl.add_plot(w_mean_a, coly=1, colx=0, type='y')
    p_mean_b = gl.add_plot(w_mean_b, coly=1, colx=0, type='y')
    # Ensure graph window is active for subsequent LabTalk commands
    try:
        gp.activate()
    except Exception:
        pass

    # Style plots via OriginPython properties; LabTalk will reapply colors later
    try:
        for plot_obj, size, col in (
            (p_raw_a, ORIGIN_RAW_SYMBOL_SIZE, RAW_COLORS["a"]),
            (p_raw_b, ORIGIN_RAW_SYMBOL_SIZE, RAW_COLORS["b"]),
            (p_mean_a, ORIGIN_MEAN_SYMBOL_SIZE, MEAN_COLORS["a"]),
            (p_mean_b, ORIGIN_MEAN_SYMBOL_SIZE, MEAN_COLORS["b"]),
        ):
            if plot_obj is None:
                continue
            plot_any = cast(Any, plot_obj)
            try:
                plot_any.symbol_shape = 2  # circle
                plot_any.symbol_size = size
                plot_any.color = col  # line and edge
                plot_any.symbol_edge_color = col
                plot_any.symbol_fill_color = col
            except Exception:
                pass
        for plot_obj, width in (
            (p_raw_a, 0),
            (p_raw_b, 0),
            (p_mean_a, ORIGIN_MEAN_LINE_WIDTH),
            (p_mean_b, ORIGIN_MEAN_LINE_WIDTH),
        ):
            if plot_obj is None:
                continue
            try:
                cast(Any, plot_obj).line_width = width
            except Exception:
                pass
    except Exception:
        pass

    # Axis labels and grid lines
    esc = title.replace('"', "'")
    for cmd in (
        'page.antialias=1;',
        'layer -aa 1;',
        'lab -xb "Applied load (g)";',
        f'lab -yl "{LABELS[var]}";',
        'lab -xt "";',
        'lab -yr "";',
        'layer.x.showAxes=1;',
        'layer.y.showAxes=1;',
        'layer.x.gridMajor=1;',
        'layer.y.gridMajor=1;',
        f'title -s "{esc}";',
    ):
        try:
            op.lt_exec(cmd)
        except Exception:
            pass

    # Build legend with color-matched entries
    try:
        legend_title = title.split(" \u2014 ")[0]
        legend = gl.label('Legend')
        legend.text = (
            f"{legend_title}\n"
            "\\c(1) raw ↑\n"
            "\\c(2) raw ↓\n"
            "\\c(3) mean ↑\n"
            "\\c(4) mean ↓"
        )
        try:
            op.lt_exec('legend.update=0;')
        except Exception:
            pass
    except Exception:
        pass

    # Reapply colors and solid symbol fill via LabTalk so means are not black
    try:
        colors = [RAW_COLORS["a"], RAW_COLORS["b"], MEAN_COLORS["a"], MEAN_COLORS["b"]]
        for idx, hexcol in enumerate(colors, start=1):
            r = int(hexcol[1:3], 16)
            g = int(hexcol[3:5], 16)
            b = int(hexcol[5:7], 16)
            op.lt_exec(
                f"layer -i {idx}; set %C -c color({r},{g},{b}); set %C -cf color({r},{g},{b}); set %C -kf 0;"
            )
    except Exception:
        pass


def plot_variable_origin(grp: pd.DataFrame, var: str) -> None:
    """Generate an Origin plot for the given variable."""

    raw_a, raw_b, mean_a, mean_b, delta = _origin_compute_tables(grp, var)
    # Build a clear title matching the Matplotlib figure
    comp = grp["composition"].iat[0]
    t = grp["title"].iat[0]
    samp = grp["sample_end"].iat[0]
    anneal = grp["anneal"].iat[0]
    title_to_use = (
        f"{comp} {t} {format_sample_end(samp)} {anneal} \u2014 {LABELS[var]}"
    )

    _origin_build_graph(raw_a, raw_b, mean_a, mean_b, title_to_use, var, delta)


def export_group_to_txt(grp: pd.DataFrame, directory: str | Path) -> Path:
    """Write ``grp`` to ``directory`` with baseline-adjusted columns.

    The exported table mirrors the data shown in Matplotlib/Origin:

    - raw measurements for every variable
    - baseline-adjusted versions that subtract the configured baseline
    - per-variable baseline values for traceability
    """

    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    comp = str(grp["composition"].iat[0]) if "composition" in grp else ""
    title = str(grp["title"].iat[0]) if "title" in grp else ""
    sample_end = str(grp["sample_end"].iat[0]) if "sample_end" in grp else ""
    anneal = str(grp["anneal"].iat[0]) if "anneal" in grp else ""

    baselines = {var: _baseline_for_variable(grp, var) for var in _EXPORT_ORDER}
    work = grp.copy()
    work["load"] = pd.to_numeric(work.get("load"), errors="coerce")
    work["line"] = pd.to_numeric(work.get("line"), errors="coerce")
    work.sort_values(["dir", "load", "line"], inplace=True, na_position="last")
    if "sample_end" in work:
        work["sample_label"] = work["sample_end"].map(format_sample_end)
    else:
        work["sample_label"] = ""

    for var, base in baselines.items():
        rel_col = f"{var}_relative"
        base_col = f"baseline_{var}"
        work[rel_col] = work[var] - base
        work[base_col] = base

    columns: list[str] = [
        "composition",
        "title",
        "sample_end",
        "sample_label",
        "anneal",
        "filename",
        "dir",
        "load",
        "line",
    ]
    for var in _EXPORT_ORDER:
        columns.extend([var, f"{var}_relative", f"baseline_{var}"])

    export_cols = [col for col in columns if col in work.columns]
    export_df = work[export_cols].copy()
    stem = _sanitise_stem("stress", comp, title, sample_end, anneal)
    path = out_dir / f"{stem}.txt"
    export_df.to_csv(path, sep="\t", index=False, float_format="%.10g")
    return path

def main(files: List[str], backend: str = BACKEND) -> None:
    if IMPROVE_READABILITY:
        apply_readability_fonts()
    data = load_data(files)
    data = maybe_handle_outliers(data)
    groups = data.groupby(['composition','title','sample_end','anneal'])
    total = len(groups) * len(PLOT_VARS)
    do_show = SHOW_PLOTS and wants_matplotlib(backend) and (total <= MAX_SHOW)
    if SHOW_PLOTS and wants_matplotlib(backend) and not do_show:
        logger.warning(f"Too many plots ({total}); only saving to '{OUTPUT_DIR}'.")

    progress = ProgressDialog(total) if total else None
    plots: List[Tuple[Figure, str]] = []
    for _, grp in groups:
        for var in PLOT_VARS:
            if progress and getattr(progress, 'cancelled', False):
                break
            if wants_matplotlib(backend):
                fig, fname = plot_variable(grp, var, SAVE_PLOTS, OUTPUT_DIR)
                plots.append((fig, fname))
            if wants_origin(backend):
                try:
                    plot_variable_origin(grp, var)
                except Exception as e:
                        logger.error(f"Origin plot failed: {e}")
            if progress:
                progress.update()
        if progress and getattr(progress, 'cancelled', False):
            break
    if progress and not getattr(progress, 'cancelled', False):
        progress.destroy()
    elif progress and getattr(progress, 'cancelled', False):
        plt.close('all')
        logger.info('Cancelled.')
        return

    if wants_matplotlib(backend):
        if do_show:
            show_plots()
        else:
            plt.close('all')
    else:
        plt.close('all')




