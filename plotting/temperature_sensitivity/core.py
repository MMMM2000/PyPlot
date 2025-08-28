import os
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple, cast, Callable

from PyQt6 import QtWidgets, QtCore

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.collections import PathCollection
from matplotlib.colors import to_hex
from matplotlib.figure import Figure
from matplotlib.typing import ColorType

from ..config import load_config
from .. import common
from ..utils import save_figure
from ..backends import wants_matplotlib, wants_origin

# Load default configuration
_CFG = load_config().get("temperature_sensitivity", {})
OUTPUT_DIR = _CFG.get("OUTPUT_DIR", os.getcwd())
PLOT_SUM = bool(_CFG.get("PLOT_SUM", True))
PLOT_DT = bool(_CFG.get("PLOT_DT", True))
PLOT_T1 = bool(_CFG.get("PLOT_T1", True))
PLOT_T2 = bool(_CFG.get("PLOT_T2", True))
PLOT_VARS = [v for v, b in [("sum", PLOT_SUM), ("dT", PLOT_DT), ("T1", PLOT_T1), ("T2", PLOT_T2)] if b]
RAW_COLORS = {25: "#45A1D6", 100: "#F09C67"}
RAW_MARKER = "o"
RAW_MARKER_SIZE = 0.3
RAW_ALPHA = 1.0
MEAN_COLORS = {25: "#00306E", 100: "#965308"}
MEAN_MARKER = 'o'
MEAN_MSIZE = 8
MEAN_LW = 3
LEGEND_MARKER_SIZE = 6
OFFSET = 0.25
JITTER_SPAN = 0.25
SHOW_PLOTS = bool(_CFG.get("SHOW_PLOTS", True))
SAVE_PLOTS = bool(_CFG.get("SAVE_PLOTS", False))
SAVE_FORMAT = _CFG.get("SAVE_FORMAT", "png")
PNG_DPI = int(_CFG.get("PNG_DPI", 1000))
BASELINE_MODE = _CFG.get("BASELINE_MODE", "none")
if BASELINE_MODE not in {"none", "zero_25", "both"}:
    # backwards compatibility for old ZERO_25_BASELINE flag
    BASELINE_MODE = "zero_25" if bool(_CFG.get("ZERO_25_BASELINE", False)) else "none"
INCLUDE_CONTINUOUS = bool(_CFG.get("INCLUDE_CONTINUOUS", True))
MED_WINDOW = int(_CFG.get("MED_WINDOW", 5))
MA_WINDOW = int(_CFG.get("MA_WINDOW", 20))
MEAN_SHIFT = OFFSET * 2
MAX_SHOW = 8
BACKEND = str(_CFG.get("BACKEND", "matplotlib"))

LABELS = {
    "T1": "T1 (µs)",
    "T2": "T2 (µs)",
    "dT": "T2–T1 (µs)",
    "sum": "T1+T2 (µs)",
}

FNAME_RE = re.compile(
    r"^(?P<composition>.+?)\s+"
    r"(?P<sample>\S+)\s+"
    r"(?P<anneal>\S+)\s+"
    r"(?P<temp>\d+(?:-\d+)?C)$"
)

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


def non_modal_question(
    title: str,
    text: str,
    buttons: QtWidgets.QMessageBox.standardButtons = (
        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
    ),
) -> QtWidgets.QMessageBox.StandardButton:
    """Return the clicked button of a non-modal question dialog."""

    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Icon.Question, title, text)
    box.setStandardButtons(buttons)
    box.setWindowModality(QtCore.Qt.WindowModality.NonModal)
    result = {
        "btn": QtWidgets.QMessageBox.StandardButton.NoButton
    }

    def _finished(code: int) -> None:
        result["btn"] = QtWidgets.QMessageBox.StandardButton(code)
        loop.quit()

    loop = QtCore.QEventLoop()
    box.finished.connect(_finished)
    box.show()
    loop.exec()
    return result["btn"]

def parse_metadata(stem: str) -> Dict[str, Any] | None:
    m = FNAME_RE.match(stem)
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
            print(f"Skipping {fn}")
            continue
        df = pd.read_csv(
            fn,
            sep=';',
            header=None,
            names=['T1','T2','dT','sum'],
            engine='python',
            on_bad_lines='skip',
        )
        df['filename'] = Path(fn).name
        df['line'] = np.arange(len(df))
        df[['T1', 'T2', 'dT', 'sum']] = df[['T1', 'T2', 'dT', 'sum']].apply(pd.to_numeric, errors='coerce')
        if md['continuous']:
            df['continuous'] = True
            df['temp'] = np.nan
        else:
            df['temp'] = md['temp_val']
            df['continuous'] = False
        for k, v in md.items():
            if k not in {'temp_val', 'continuous', 'temp'}:
                df[k] = v
        dfs.append(df)
    if not dfs:
        raise FileNotFoundError("No valid files selected")
    data = pd.concat(dfs, ignore_index=True)
    cont_mask = data['continuous']
    if cont_mask.any():
        temps = data.loc[~cont_mask, 'temp'].dropna()
        if not temps.empty:
            t_min, t_max = temps.min(), temps.max()
        else:
            t_min, t_max = 0.0, float(len(data.loc[cont_mask]) - 1)
        data.loc[cont_mask, 'temp'] = np.linspace(t_min, t_max, cont_mask.sum())
    return data


def detect_outliers(
    df: pd.DataFrame,
    column: str = "sum",
    quantile: float = 0.9,
    factor: float = 3.0,
    progress: Callable[[int, int], None] | None = None,
) -> pd.DataFrame:
    """Return a DataFrame of rows that are statistical outliers.

    Outliers are determined locally for each point using at most the 10
    neighbouring values on either side. This prevents early transient regions
    from affecting later measurements in the same file.
    """

    if not (0 < quantile < 1):
        raise ValueError("quantile must be between 0 and 1")

    out_rows = []
    low_q = (1 - quantile) / 2
    high_q = 1 - low_q

    total = int(df[column].count())
    processed = 0

    for fname, grp in df.groupby("filename"):
        sub = grp[[column]].dropna().reset_index()
        values = sub[column].to_numpy()
        if values.size == 0:
            continue
        for idx, val in enumerate(values):
            start = max(0, idx - 10)
            end = min(values.size, idx + 11)
            window = values[start:end]
            med = np.median(window)
            q_low = np.quantile(window, low_q)
            q_high = np.quantile(window, high_q)
            rng = q_high - q_low
            if rng <= 0:
                processed += 1
                if progress:
                    progress(processed, total)
                continue
            if abs(val - med) > factor * rng:
                out_rows.append(grp.loc[[sub["index"].iloc[idx]]])
            processed += 1
            if progress:
                progress(processed, total)

    if out_rows:
        return pd.concat(out_rows, ignore_index=False)
    return pd.DataFrame(columns=df.columns)


def handle_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Check for and optionally remove outliers.

    If ``AUTO_REMOVE_OUTLIERS`` is set the outliers are dropped without any
    user interaction. Otherwise a message box is shown when running inside a Qt
    application asking whether to remove them. When no Qt application is
    running the outliers are removed automatically with a short notice.
    """
    out_df = detect_outliers(df)
    if out_df.empty:
        return df

    files = ", ".join(sorted(out_df["filename"].unique()))

    if common.AUTO_REMOVE_OUTLIERS:
        print(f"Automatically removing outliers from {files}.")
        return df.drop(out_df.index)

    app = QtWidgets.QApplication.instance()

    # Plot outliers for visual confirmation
    figs: List[Figure] = []
    for fname, grp in df.groupby("filename"):
        if fname not in out_df["filename"].values:
            continue
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.plot(grp["line"], grp["sum"], "o", ms=2, label="data")
        sub = out_df[out_df["filename"] == fname]
        ax.plot(sub["line"], sub["sum"], "ro", ms=6, label="outlier")
        ax.set_title(fname)
        ax.set_xlabel("Index")
        ax.set_ylabel("T1+T2 (µs)")
        ax.legend()
        fig.tight_layout()
        figs.append(fig)

    if app is None:
        print(f"Removing outliers from {files}.")
        plt.close("all")
        return df.drop(out_df.index)

    for fig in figs:
        fig.show()

    reply = non_modal_question(
        "Outliers detected",
        f"Outliers detected in: {files}.\nRemove them?",
    )
    plt.close("all")
    if reply == QtWidgets.QMessageBox.StandardButton.Yes:
        return df.drop(out_df.index)
    return df


def plot_variable(
    df: pd.DataFrame,
    var: str,
    save_flag: bool,
    out_dir: str,
    baseline_mode: str = BASELINE_MODE,
    include_cont: bool = INCLUDE_CONTINUOUS,
    med_window: int = MED_WINDOW,
    ma_window: int = MA_WINDOW,
) -> Tuple[Figure, str]:
    comp = df['composition'].iat[0]
    anneal = df['anneal'].iat[0]
    samples = sorted(df['sample'].unique())
    sample_idx = {s: i + 1 for i, s in enumerate(samples)}
    df['sample_idx'] = df['sample'].map(sample_idx).astype(float)

    raw = df[~df['continuous']].copy()
    cont = df[df['continuous']].copy()
    cont_samples = set(cont['sample_idx'].unique())

    means = raw.groupby(['temp', 'sample_idx'])[var].mean().reset_index()
    baseline = means[means['temp'] == 25].set_index('sample_idx')[var].to_dict()

    raw['x_center'] = raw['sample_idx'] + raw['temp'].map({25: -OFFSET, 100: OFFSET})
    np.random.seed(0)
    raw['x'] = raw['x_center'] + np.random.uniform(-JITTER_SPAN, JITTER_SPAN, len(raw))

    if baseline_mode == "zero_25":
        raw['y'] = raw.apply(lambda r: r[var] - baseline.get(r['sample_idx'], 0.0), axis=1)
        means[var] = means.apply(lambda r: r[var] - baseline.get(r['sample_idx'], 0.0), axis=1)
        if include_cont and not cont.empty:
            cont['y'] = cont.apply(lambda r: r[var] - baseline.get(r['sample_idx'], 0.0), axis=1)
    else:
        raw['y'] = raw[var]
        if include_cont and not cont.empty:
            cont['y'] = cont[var]

    all_y = [raw['y']]
    if include_cont and not cont.empty:
        all_y.append(cont['y'])
    y_min = min(s.min() for s in all_y)
    y_max = max(s.max() for s in all_y)
    y_range = y_max - y_min if y_max != y_min else 1.0
    delta_offset = 0.05 * y_range

    fig, ax = plt.subplots(figsize=(9, 5))
    legend_done: set[str] = set()
    for temp in sorted(raw['temp'].unique()):
        sub = raw[raw['temp'] == temp]
        ax.scatter(
            sub['x'],
            sub['y'],
            c=RAW_COLORS.get(temp, 'gray'),
            marker=RAW_MARKER,
            s=RAW_MARKER_SIZE,
            alpha=RAW_ALPHA,
            label=f'raw {temp}\N{DEGREE SIGN}C',
        )

    for temp in sorted(raw['temp'].unique()):
        m = means[means['temp'] == temp].copy()
        if include_cont and not cont.empty:
            offset = {-1: 0.0, 25: -MEAN_SHIFT, 100: MEAN_SHIFT}.get(int(temp), 0.0)
            m_x = [
                r.sample_idx + (offset if r.sample_idx in cont_samples else 0.0)
                for r in m.itertuples()
            ]
        else:
            m_x = m['sample_idx']
        ax.plot(
            m_x,
            m[var],
            MEAN_MARKER,
            linestyle='None',
            c=MEAN_COLORS.get(temp, 'gray'),
            markersize=MEAN_MSIZE,
            label=f'mean {int(temp)}\N{DEGREE SIGN}C',
        )

    # Connect 25°C and 100°C means per sample and show delta
    pivot = means.pivot(index='sample_idx', columns='temp', values=var)
    if 25 in pivot.columns and 100 in pivot.columns:
        for idx, row in pivot.dropna(subset=[25, 100]).iterrows():
            x = idx
            y25 = row[25]
            y100 = row[100]
            has_cont = include_cont and (idx in cont_samples)
            if not has_cont:
                ax.plot(
                    [x, x],
                    [y25, y100],
                    color='black',
                    linewidth=1,
                    zorder=0,
                )
            delta = y100 - y25
            delta_x = x - 0.1
            delta_y = y100 + (delta_offset if has_cont else 0.0)
            ax.annotate(
                f"{delta:.1f}",
                (delta_x, delta_y),
                ha='right',
                va='bottom' if has_cont else 'center',
                fontsize=10,
            )

    if include_cont and not cont.empty:
        for s in samples:
            sub = cont[cont['sample'] == s].sort_values('temp')
            if sub.empty:
                continue
            med = sub['y'].rolling(med_window, center=True, min_periods=1).median()
            proc = med.rolling(ma_window, center=True, min_periods=1).mean()
            start = sub['temp'].iloc[0]
            end = sub['temp'].iloc[-1]
            x_start = sample_idx[s] - MEAN_SHIFT
            x_end = sample_idx[s] + MEAN_SHIFT
            scale = (x_end - x_start) / (end - start) if end != start else 1.0
            x_vals = (sub['temp'] - start) * scale + x_start
            lbl = None if 'cont' in legend_done else f'25-100C med {med_window} mwa {ma_window}'
            ax.plot(x_vals, proc, color='black', label=lbl)
            legend_done.add('cont')

    ax.set_ylim(y_min - 0.02 * y_range, y_max + 0.02 * y_range)

    ticks = [sample_idx[s] for s in samples]
    ax.set_xticks(ticks)
    ax.set_xticklabels(samples)
    ax.set_xlabel('Sample')
    ax.set_ylabel(LABELS[var])
    ax.set_title(f"{comp} {anneal} — {LABELS[var]}")
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
    fname = f"{comp} {anneal} {var}"
    if save_flag:
        os.makedirs(out_dir, exist_ok=True)
        save_figure(fig, os.path.join(out_dir, fname), SAVE_FORMAT, PNG_DPI)
    return fig, f"{fname}.{SAVE_FORMAT}"


def plot_variable_origin(
    df: pd.DataFrame,
    var: str,
    baseline_mode: str = BASELINE_MODE,
    include_cont: bool = INCLUDE_CONTINUOUS,
    med_window: int = MED_WINDOW,
    ma_window: int = MA_WINDOW,
) -> None:
    """Create an Origin graph roughly matching the Matplotlib style."""

    import originpro as op  # lazy import

    comp = df['composition'].iat[0]
    anneal = df['anneal'].iat[0]
    samples = sorted(df['sample'].unique())
    sample_idx = {s: i + 1 for i, s in enumerate(samples)}
    df = df.copy()
    df['sample_idx'] = df['sample'].map(sample_idx).astype(float)

    raw = df[~df['continuous']].copy()
    cont = df[df['continuous']].copy()

    means = raw.groupby(['temp', 'sample_idx'])[var].mean().reset_index()
    baseline = means[means['temp'] == 25].set_index('sample_idx')[var].to_dict()

    raw['x_center'] = raw['sample_idx'] + raw['temp'].map({25: -OFFSET, 100: OFFSET}).fillna(0)
    rng = np.random.default_rng(0)
    raw['X'] = raw['x_center'] + rng.uniform(-JITTER_SPAN, JITTER_SPAN, len(raw))
    if baseline_mode == 'zero_25':
        raw['Y'] = raw.apply(lambda r: r[var] - baseline.get(r['sample_idx'], 0.0), axis=1)
        means[var] = means.apply(lambda r: r[var] - baseline.get(r['sample_idx'], 0.0), axis=1)
        if include_cont and not cont.empty:
            cont['Y'] = cont.apply(lambda r: r[var] - baseline.get(sample_idx.get(r['sample'], 0.0), 0.0), axis=1)
    else:
        raw['Y'] = raw[var]
        if include_cont and not cont.empty:
            cont['Y'] = cont[var]

    # Build Origin graph
    book = op.new_book('w', lname="Temp Sens (Python)")
    book.activate()
    gp = op.new_graph(template='scatter')
    gl = gp[0]

    # Raw scatter for 25C and 100C
    for t, color in ((25, RAW_COLORS.get(25, '#45A1D6')), (100, RAW_COLORS.get(100, '#F09C67'))):
        sub = raw[raw['temp'] == t][['X', 'Y']]
        if sub.empty:
            continue
        w = op.new_sheet('w', lname=f'raw_{t}')
        w.from_df(sub.reset_index(drop=True))
        w.cols_axis('XY')
        p = gl.add_plot(w, coly=1, colx=0, type='s')
        try:
            p.color = color
        except Exception:
            pass

    # Mean markers per temperature
    for t, color in ((25, MEAN_COLORS.get(25, 'black')), (100, MEAN_COLORS.get(100, 'black'))):
        m = means[means['temp'] == t]
        if m.empty:
            continue
        mdf = pd.DataFrame({'X': m['sample_idx'], 'Y': m[var]})
        w = op.new_sheet('w', lname=f'mean_{t}')
        w.from_df(mdf.reset_index(drop=True))
        w.cols_axis('XY')
        p = gl.add_plot(w, coly=1, colx=0, type='s')
        try:
            p.color = MEAN_COLORS.get( 'a' if t==25 else 'b', color)
            p.set_cmd('-k 2')  # circle marker type
        except Exception:
            pass

    # Connect 25C and 100C per sample when no continuous data
    if not cont.empty:
        cont_samples = set(cont['sample'].unique())
    else:
        cont_samples = set()
    piv = means.pivot(index='sample_idx', columns='temp', values=var)
    if 25 in piv.columns and 100 in piv.columns:
        for idx, row in piv.dropna(subset=[25, 100]).iterrows():
            if samples[int(idx)-1] in cont_samples:
                continue
            w = op.new_sheet('w', lname=f'link_{int(idx)}')
            w.from_list(0, [idx, idx])
            w.from_list(1, [row[25], row[100]])
            w.cols_axis('XY')
            p = gl.add_plot(w, coly=1, colx=0, type='y')
            try:
                p.color = 'black'
                p.set_cmd('-w 1')
            except Exception:
                pass

    # Continuous processed per sample
    if include_cont and not cont.empty:
        for s in samples:
            sub = cont[cont['sample'] == s].sort_values('temp')
            if sub.empty:
                continue
            med = sub['Y'].rolling(med_window, center=True, min_periods=1).median()
            proc = med.rolling(ma_window, center=True, min_periods=1).mean()
            start = sub['temp'].iloc[0]
            end = sub['temp'].iloc[-1]
            x_start = sample_idx[s] - MEAN_SHIFT
            x_end = sample_idx[s] + MEAN_SHIFT
            scale = (x_end - x_start) / (end - start) if end != start else 1.0
            x_vals = (sub['temp'] - start) * scale + x_start
            w = op.new_sheet('w', lname=f'cont_{s}')
            w.from_list(0, x_vals.to_numpy())
            w.from_list(1, proc.to_numpy())
            w.cols_axis('XY')
            p = gl.add_plot(w, coly=1, colx=0, type='y')
            try:
                p.color = 'black'
                p.set_cmd('-w 1')
            except Exception:
                pass

    try:
        gl.rescale()
        gp.activate()
        op.lt_exec('page.antialias=1;')
        op.lt_exec('layer -aa 1;')
        op.lt_exec('lab -xb "Sample";')
        op.lt_exec(f'lab -yl "{LABELS[var]}";')
        esc = (f"{comp} {anneal} - {LABELS[var]}").replace('"', "'")
        op.lt_exec(f'title -s "{esc}";')
        op.lt_exec('legend;')
    except Exception:
        pass


from ..common import maybe_handle_outliers


def main(files: List[str], backend: str = BACKEND):
    data = load_data(files)
    data = maybe_handle_outliers(data)
    groups = data.groupby(['composition', 'anneal'])
    modes = [BASELINE_MODE] if BASELINE_MODE != "both" else ["none", "zero_25"]
    total = len(groups) * len(PLOT_VARS) * len(modes)
    do_show = SHOW_PLOTS and wants_matplotlib(backend) and (total <= MAX_SHOW)
    if SHOW_PLOTS and wants_matplotlib(backend) and not do_show:
        print(f"Too many plots ({total}); only saving to '{OUTPUT_DIR}'.")

    progress = ProgressDialog(total) if total else None
    plots: List[Tuple[Figure, str]] = []
    for _, grp in groups:
        for var in PLOT_VARS:
            for mode in modes:
                if progress and getattr(progress, 'cancelled', False):
                    break
                if wants_matplotlib(backend):
                    fig, fname = plot_variable(
                        grp,
                        var,
                        SAVE_PLOTS,
                        OUTPUT_DIR,
                        baseline_mode=mode,
                        include_cont=INCLUDE_CONTINUOUS,
                        med_window=MED_WINDOW,
                        ma_window=MA_WINDOW,
                    )
                    if BASELINE_MODE == "both":
                        stem, ext = os.path.splitext(fname)
                        fname = f"{stem}_{mode}{ext}"
                    plots.append((fig, fname))
                if wants_origin(backend):
                    try:
                        plot_variable_origin(
                            grp,
                            var,
                            baseline_mode=mode,
                            include_cont=INCLUDE_CONTINUOUS,
                            med_window=MED_WINDOW,
                            ma_window=MA_WINDOW,
                        )
                    except Exception as e:
                        print(f"Origin plot failed: {e}")
                if progress:
                    progress.update()
            if progress and getattr(progress, 'cancelled', False):
                break
        if progress and getattr(progress, 'cancelled', False):
            break
    if progress and not getattr(progress, 'cancelled', False):
        progress.destroy()
    elif progress and getattr(progress, 'cancelled', False):
        plt.close('all')
        print('Cancelled.')
        return

    if wants_matplotlib(backend):
        if do_show:
            plt.show()
        else:
            plt.close('all')
        if not SAVE_PLOTS and plots and QtWidgets.QApplication.instance() is not None:
            reply = non_modal_question(
                "Save Plots",
                "Save generated plots?",
            )
            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                out = QtWidgets.QFileDialog.getExistingDirectory(None, "Select output directory", str(OUTPUT_DIR))
                if out:
                    os.makedirs(out, exist_ok=True)
                    for fig, fname in plots:
                        base = os.path.join(out, Path(fname).stem)
                        save_figure(fig, base, SAVE_FORMAT, PNG_DPI)
    else:
        plt.close('all')

    print(f'Done: processed {total} plots.')
