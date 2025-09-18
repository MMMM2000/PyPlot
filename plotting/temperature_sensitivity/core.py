import os
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple, cast, Callable, Protocol

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
from ..utils import save_figure, show_plots, apply_readability_fonts, apply_readability
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
RAW_MARKER_SIZE = 1.0
RAW_ALPHA = 1.0
MEAN_COLORS = {25: "#00306E", 100: "#965308"}
MEAN_MARKER = 'o'
MEAN_MSIZE = 8
MEAN_LW = 3
LEGEND_MARKER_SIZE = 6
IMPROVE_READABILITY = True
SHOW_LEGEND = bool(_CFG.get("SHOW_LEGEND", True))
LEGEND_SIZE = int(_CFG.get("LEGEND_SIZE", 18))
LEGEND_ORIENTATION = str(_CFG.get("LEGEND_ORIENTATION", "auto"))
LEGEND_LOCATION = str(_CFG.get("LEGEND_LOCATION", "outside_right")).lower()
LEGEND_SHOW_SYMBOLS = bool(_CFG.get("LEGEND_SHOW_SYMBOLS", False))
LEGEND_SYMBOL_SIZE = float(_CFG.get("LEGEND_SYMBOL_SIZE", 10))
TICK_SIZE = int(_CFG.get("TICK_SIZE", 18))
AXIS_LABEL_SIZE = int(_CFG.get("AXIS_LABEL_SIZE", 18))
TITLE_SIZE = int(_CFG.get("TITLE_SIZE", 22))
SHOW_TICK_LABELS = bool(_CFG.get("SHOW_TICK_LABELS", True))
SHOW_AXIS_LABELS = bool(_CFG.get("SHOW_AXIS_LABELS", True))
SHOW_TITLE = bool(_CFG.get("SHOW_TITLE", True))
OFFSET = 0.25
JITTER_SPAN = 0.25
SHOW_PLOTS = bool(_CFG.get("SHOW_PLOTS", True))
SAVE_PLOTS = bool(_CFG.get("SAVE_PLOTS", False))
SAVE_FORMAT = _CFG.get("SAVE_FORMAT", "png")
PNG_DPI = int(_CFG.get("PNG_DPI", 1200))
BASELINE_MODE = _CFG.get("BASELINE_MODE", "none")
if BASELINE_MODE not in {"none", "zero_25", "both"}:
    # backwards compatibility for old ZERO_25_BASELINE flag
    BASELINE_MODE = "zero_25" if bool(_CFG.get("ZERO_25_BASELINE", False)) else "none"
INCLUDE_CONTINUOUS = bool(_CFG.get("INCLUDE_CONTINUOUS", True))
MED_WINDOW = int(_CFG.get("MED_WINDOW", 5))
MA_WINDOW = int(_CFG.get("MA_WINDOW", 200))
MEAN_SHIFT = OFFSET * 2
MAX_SHOW = 8
OUTLIER_PROGRESS_THRESHOLD = 1000
BACKEND = str(_CFG.get("BACKEND", "matplotlib"))

TS_LABELS = {
    "T1": "T1 (\u03BCs)",
    "T2": "T2 (\u03BCs)",
    "dT": "T2-T1 (\u03BCs)",
    "sum": "T1+T2 (\u03BCs)",
}


def _format_temp_label(temp: float | int) -> str:
    """Return ``temp`` formatted without unnecessary decimal places."""

    try:
        value = float(temp)
    except Exception:
        return str(temp)
    return f"{int(value)}" if value.is_integer() else f"{value:g}"

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
        self._step = max(1, total // 10) if total else 0
        self._next = self._step

    def update(self) -> None:
        self.count += 1
        if not self.total or not self._step:
            return
        if self.count >= self._next or self.count == self.total:
            pct = (self.count / self.total) * 100
            print(f"Progress: {self.count}/{self.total} ({pct:.0f}%)")
            self._next = min(self.total, self.count + self._step)

    def destroy(self) -> None:
        pass


class ProgressReporter(Protocol):
    cancelled: bool

    def update(self) -> None:
        ...


class _OutlierCancelled(Exception):
    """Raised when outlier detection is cancelled by the user."""


def non_modal_question(
    title: str,
    text: str,
    buttons: QtWidgets.QMessageBox.StandardButton = (
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
    progress: ProgressReporter | Callable[..., None] | None = None,
) -> pd.DataFrame:
    """Return a DataFrame of rows that are statistical outliers.

    Outliers are determined locally for each point using at most the 10
    neighbouring values on either side. This prevents early transient regions
    from affecting later measurements in the same file.
    """

    if not (0 < quantile < 1):
        raise ValueError("quantile must be between 0 and 1")

    out_rows: list[pd.DataFrame] = []
    low_q = (1 - quantile) / 2
    high_q = 1 - low_q

    total = int(df[column].count())
    processed = 0
    reporter: ProgressReporter | None = None
    callback: Callable[..., None] | None = None
    if progress is not None:
        update_attr = getattr(progress, "update", None)
        if callable(update_attr):
            reporter = cast(ProgressReporter, progress)
        elif callable(progress):
            callback = cast(Callable[..., None], progress)

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
                if reporter is not None:
                    reporter.update()
                    if getattr(reporter, "cancelled", False):
                        raise _OutlierCancelled()
                elif callback is not None:
                    try:
                        callback(processed, total)
                    except TypeError:
                        callback()
                continue
            if abs(val - med) > factor * rng:
                out_rows.append(grp.loc[[sub["index"].iloc[idx]]])
            processed += 1
            if reporter is not None:
                reporter.update()
                if getattr(reporter, "cancelled", False):
                    raise _OutlierCancelled()
            elif callback is not None:
                try:
                    callback(processed, total)
                except TypeError:
                    callback()

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
    progress: ProgressDialog | None = None
    out_df = pd.DataFrame()
    total_points = int(df["sum"].count())
    try:
        if total_points >= OUTLIER_PROGRESS_THRESHOLD:
            progress = ProgressDialog(total_points)
            out_df = detect_outliers(df, progress=progress)
        else:
            out_df = detect_outliers(df)
    except _OutlierCancelled:
        if progress:
            progress.destroy()
            progress = None
        plt.close('all')
        print("Outlier detection cancelled.")
        return df
    finally:
        if progress:
            progress.destroy()
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
        ax.set_title(str(fname))
        ax.set_xlabel("Index")
        ax.set_ylabel("T1+T2 (\u03BCs)")
        ax.legend()
        fig.tight_layout()
        apply_readability(ax, globals())
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
    display_samples = [s.replace('_', '/') for s in samples]
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
    plot_top = y_max + 0.08 * y_range
    title_level = plot_top - 0.02 * y_range

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
            label=f'raw {_format_temp_label(temp)}\N{DEGREE SIGN}C',
        )

    for temp in sorted(raw['temp'].unique()):
        m = means[means['temp'] == temp].copy()
        if include_cont and not cont.empty:
            offset = {-1: 0.0, 25: -MEAN_SHIFT, 100: MEAN_SHIFT}.get(int(temp), 0.0)
            m_x = np.array(
                [
                    float(
                        r.sample_idx
                        + (offset if r.sample_idx in cont_samples else 0.0)
                    )
                    for r in m.itertuples()
                ],
                dtype=float,
            )
        else:
            m_x = m['sample_idx'].astype(float).to_numpy()
        y_vals = m[var].astype(float).to_numpy()
        ax.plot(
            m_x,
            y_vals,
            MEAN_MARKER,
            linestyle='None',
            c=MEAN_COLORS.get(temp, 'gray'),
            markersize=MEAN_MSIZE,
            label=f'mean {_format_temp_label(temp)}\N{DEGREE SIGN}C',
        )

    # Connect 25Â°C and 100Â°C means per sample and show delta
    pivot = means.pivot(index='sample_idx', columns='temp', values=var)
    if 25 in pivot.columns and 100 in pivot.columns:
        for idx, row in pivot.dropna(subset=[25, 100]).iterrows():
            x = float(idx)
            y25 = float(row[25])
            y100 = float(row[100])
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
    ax.set_xticklabels(display_samples)

    def _legend_kwargs_from_location(loc_value: str) -> dict[str, Any]:
        loc = (loc_value or "inside").strip().lower()
        if loc in {"outside_right", "outside", "outside right"}:
            return {"loc": "center left", "bbox_to_anchor": (1.02, 0.5), "borderaxespad": 0.0}
        if loc in {"inside", "auto", "best", ""}:
            return {"loc": "best"}
        return {"loc": loc}

    def _colorize_legend(legend_obj: Any, adjust_sizes: bool = False) -> None:
        if legend_obj is None:
            return
        handles: list[Any] = []
        for attr in ("legendHandles", "legend_handles"):
            found = getattr(legend_obj, attr, None)
            if found:
                handles = list(found)
                break
        for text, handle in zip(legend_obj.get_texts(), handles):
            rawcol: ColorType | str = 'black'
            if isinstance(handle, Line2D):
                rawcol = handle.get_color()
                face = handle.get_markerfacecolor()
                if face not in (None, 'none'):
                    rawcol = face
                if adjust_sizes:
                    try:
                        handle.set_markersize(LEGEND_MARKER_SIZE)
                    except Exception:
                        pass
            elif isinstance(handle, (Patch, PathCollection)):
                rawcol = handle.get_facecolor()
                if isinstance(handle, PathCollection) and adjust_sizes:
                    try:
                        handle.set_sizes([LEGEND_MARKER_SIZE ** 2])
                    except Exception:
                        pass
                if isinstance(rawcol, np.ndarray) and rawcol.ndim > 1:
                    rawcol = rawcol[0]
            try:
                text.set_color(to_hex(cast(ColorType, rawcol)))
            except Exception:
                pass

    def _apply_symbol_visibility(legend_obj: Any) -> None:
        if legend_obj is None:
            return

        show_symbols = bool(globals().get("LEGEND_SHOW_SYMBOLS", False))
        marker_size = float(globals().get("LEGEND_SYMBOL_SIZE", 10))
        handles: list[Any] = []
        for attr in ("legendHandles", "legend_handles"):
            found = getattr(legend_obj, attr, None)
            if found:
                handles = list(found)
                break

        for handle in handles:
            handle_any = cast(Any, handle)
            if isinstance(handle, PathCollection):
                try:
                    if show_symbols:
                        handle.set_alpha(1.0)
                        handle.set_sizes([marker_size ** 2])
                    else:
                        handle.set_alpha(0.0)
                        handle.set_sizes([0.1])
                except Exception:
                    pass
            elif isinstance(handle, Patch):
                try:
                    handle.set_alpha(1.0 if show_symbols else 0.0)
                except Exception:
                    pass

            marker_sizer = getattr(handle_any, "set_markersize", None)
            if callable(marker_sizer):
                try:
                    marker_sizer(marker_size if show_symbols else 0.1)
                except Exception:
                    pass

            marker_setter = getattr(handle_any, "set_marker", None)
            if callable(marker_setter):
                if show_symbols:
                    marker_getter = getattr(handle_any, "get_marker", None)
                    current = None
                    if callable(marker_getter):
                        try:
                            current = marker_getter()
                        except Exception:
                            current = None
                    if current in (None, "", " ", "None"):
                        for candidate in ("o", "s", "."):
                            try:
                                marker_setter(candidate)
                                break
                            except Exception:
                                continue
                else:
                    for empty in (None, "", " "):
                        try:
                            marker_setter(empty)
                            break
                        except Exception:
                            continue

    ax.set_xlabel('Sample')
    ax.set_ylabel(TS_LABELS[var])
    ax.set_title(f"{comp} {anneal} — {TS_LABELS[var]}")
    ax.grid(True)

    legend_kwargs = _legend_kwargs_from_location(str(globals().get("LEGEND_LOCATION", "inside")))
    legend = ax.legend(**legend_kwargs)
    _colorize_legend(legend, adjust_sizes=True)
    _apply_symbol_visibility(legend)

    apply_readability(ax, globals())
    updated = ax.get_legend()
    _apply_symbol_visibility(updated)
    _colorize_legend(updated)

    final_loc = str(globals().get("LEGEND_LOCATION", "inside") or "inside").strip().lower()
    if final_loc in {"outside_right", "outside", "outside right"}:
        fig.tight_layout(rect=(0.0, 0.0, 0.8, 1.0))
    else:
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

    if df.empty:
        return

    import originpro as op  # lazy import
    try:
        op.set_show()
    except Exception:
        pass

    comp = df['composition'].iat[0]
    anneal = df['anneal'].iat[0]
    samples = sorted(df['sample'].unique())
    display_samples = [s.replace('_', '/') for s in samples]
    sample_idx = {s: i + 1 for i, s in enumerate(samples)}
    idx_to_sample = {idx: sample for sample, idx in sample_idx.items()}
    display_by_idx: dict[float, str] = {}
    for sample, label in zip(samples, display_samples):
        idx = sample_idx[sample]
        display_by_idx[idx] = label
        display_by_idx[float(idx)] = label

    work = df.copy()
    work['sample_idx'] = work['sample'].map(sample_idx).astype(float)
    raw = work[~work['continuous']].copy()
    cont = work[work['continuous']].copy()

    means = raw.groupby(['temp', 'sample_idx'])[var].mean().reset_index()
    baseline = means[means['temp'] == 25].set_index('sample_idx')[var].to_dict()

    rng = np.random.default_rng(0)
    raw['x_center'] = raw['sample_idx'] + raw['temp'].map({25: -OFFSET, 100: OFFSET}).fillna(0.0)
    raw['X'] = raw['x_center'] + rng.uniform(-JITTER_SPAN, JITTER_SPAN, len(raw))

    def _baseline_for(idx: float) -> float:
        return baseline.get(idx, 0.0)

    if baseline_mode == 'zero_25':
        raw['Y'] = raw.apply(lambda r: r[var] - _baseline_for(r['sample_idx']), axis=1)
        means[var] = means.apply(lambda r: r[var] - _baseline_for(r['sample_idx']), axis=1)
        if include_cont and not cont.empty:
            cont['Y'] = cont.apply(lambda r: r[var] - _baseline_for(r['sample_idx']), axis=1)
    else:
        raw['Y'] = raw[var]
        if include_cont and not cont.empty:
            cont['Y'] = cont[var]

    all_y = [raw['Y']] if not raw.empty else []
    if include_cont and not cont.empty:
        all_y.append(cont['Y'])
    if all_y:
        y_min = min(series.min() for series in all_y)
        y_max = max(series.max() for series in all_y)
    else:
        y_min = y_max = 0.0
    y_range = y_max - y_min if y_max != y_min else 1.0
    delta_pad = max(0.04 * y_range, 0.4)
    title_gap = max(0.06 * y_range, 0.5)
    plot_top = y_max + delta_pad + title_gap
    title_level = y_max + delta_pad + 0.6 * title_gap

    cont_samples = set(cont['sample']) if include_cont else set()
    means['plot_x'] = means['sample_idx']
    if include_cont and cont_samples:
        def _shift(row: pd.Series) -> float:
            sample = idx_to_sample.get(row['sample_idx'])
            if sample not in cont_samples:
                return row['sample_idx']
            if row['temp'] == 25:
                return row['sample_idx'] - MEAN_SHIFT
            if row['temp'] == 100:
                return row['sample_idx'] + MEAN_SHIFT
            return row['sample_idx']
        means['plot_x'] = means.apply(_shift, axis=1)

    mean_positions: dict[tuple[float, float], float] = {}
    if not means.empty:
        mean_positions = {
            (float(row.sample_idx), float(row.temp)): float(row.plot_x)
            for row in means.itertuples()
        }

    sample_label_positions: dict[str, float] = {
        sample: mean_positions.get((float(sample_idx[sample]), 25.0), float(sample_idx[sample]))
        for sample in samples
    }

    delta_labels: list[tuple[float, float, str]] = []
    pivot = means.pivot(index='sample_idx', columns='temp', values=var)
    if 25 in pivot.columns and 100 in pivot.columns:
        for idx, row in pivot.dropna(subset=[25, 100]).iterrows():
            sample = idx_to_sample.get(idx)
            has_cont = sample in cont_samples
            delta = row[100] - row[25]
            extra = delta_pad if has_cont else max(delta_pad * 0.5, 0.3)
            y_top = row[100] + extra
            x_pos = mean_positions.get((float(idx), 100.0), float(idx))
            delta_labels.append((x_pos, y_top, f"{delta:.1f}"))

    cont_processed: list[pd.DataFrame] = []
    if include_cont and not cont.empty:
        cont = cont.sort_values('temp')
        for sample in samples:
            sub = cont[cont['sample'] == sample]
            if sub.empty:
                continue
            med = sub['Y'].rolling(med_window, center=True, min_periods=1).median()
            proc = med.rolling(ma_window, center=True, min_periods=1).mean()
            start = sub['temp'].iloc[0]
            end = sub['temp'].iloc[-1]
            x_start = sample_idx[sample] - MEAN_SHIFT
            x_end = sample_idx[sample] + MEAN_SHIFT
            scale = (x_end - x_start) / (end - start) if end != start else 1.0
            x_vals = (sub['temp'] - start) * scale + x_start
            cont_processed.append(pd.DataFrame({'X': x_vals.to_numpy(), 'Y': proc.to_numpy(), 'sample': sample}))

    book_obj = op.new_book('w', lname="Temp Sens (Python)")
    book = cast(Any, book_obj)
    if book is not None:
        try:
            book.activate()
        except Exception:
            pass
    gp_obj = op.new_graph(template='scatter')
    gp = cast(Any, gp_obj)
    try:
        gl = cast(Any, gp[0])
    except Exception:
        gl = None
    if gl is None:
        return

    legend_entries: list[str] = []

    for temp in sorted(raw['temp'].dropna().unique()):
        sub = raw[raw['temp'] == temp]
        if sub.empty:
            continue
        temp_label = _format_temp_label(temp)
        sheet = op.new_sheet('w', lname=f'raw_{temp_label}')
        if sheet is None:
            continue
        w = cast(Any, sheet)
        w.from_list(0, sub['X'].to_list())
        w.from_list(1, sub['Y'].to_list())
        w.cols_axis('XY')
        plot_obj = gl.add_plot(w, coly=1, colx=0, type='s')
        if plot_obj is None:
            continue
        p = cast(Any, plot_obj)
        color = RAW_COLORS.get(int(temp), RAW_COLORS.get(temp, '#45A1D6'))
        legend_label = f"raw {temp_label}\N{DEGREE SIGN}C"
        try:
            w.set_label(1, legend_label)
        except Exception:
            pass
        try:
            p.color = color
            p.symbol_shape = 2
            p.symbol_size = RAW_MARKER_SIZE
            p.symbol_edge_color = color
            p.symbol_fill_color = color
            p.line_width = 0
            p.legend = legend_label
        except Exception:
            pass
        legend_entries.append(legend_label)

    for temp in sorted(means['temp'].dropna().unique()):
        sub = means[means['temp'] == temp]
        if sub.empty:
            continue
        temp_label = _format_temp_label(temp)
        sheet = op.new_sheet('w', lname=f'mean_{temp_label}')
        if sheet is None:
            continue
        w = cast(Any, sheet)
        labels = [display_by_idx.get(val, idx_to_sample.get(val, str(val))) for val in sub['sample_idx']]
        w.from_list(0, labels)
        w.from_list(1, sub['plot_x'].to_list())
        w.from_list(2, sub[var].to_list())
        try:
            w.set_label(0, "Sample")
            w.set_label(1, "Position")
            w.set_label(2, "Value")
        except Exception:
            pass
        plot_obj = gl.add_plot(w, coly=2, colx=1, type='s')
        if plot_obj is None:
            continue
        p = cast(Any, plot_obj)
        color = MEAN_COLORS.get(int(temp), MEAN_COLORS.get(temp, 'black'))
        legend_label = f"mean {temp_label}\N{DEGREE SIGN}C"
        try:
            w.set_label(2, legend_label)
        except Exception:
            pass
        try:
            p.color = color
            p.symbol_shape = 2
            p.symbol_size = MEAN_MSIZE
            p.symbol_edge_color = color
            p.symbol_fill_color = color
            p.legend = legend_label
        except Exception:
            pass
        legend_entries.append(legend_label)

    cont_label = f"25-100C med {med_window} mwa {ma_window}"
    cont_label_added = False
    for idx, cont_df in enumerate(cont_processed, start=1):
        sheet = op.new_sheet('w', lname=f'cont_{idx}')
        if sheet is None:
            continue
        w = cast(Any, sheet)
        w.from_list(0, cont_df['X'].tolist())
        w.from_list(1, cont_df['Y'].tolist())
        w.cols_axis('XY')
        plot_obj = gl.add_plot(w, coly=1, colx=0, type='y')
        if plot_obj is None:
            continue
        p = cast(Any, plot_obj)
        try:
            p.color = 'black'
            p.line_width = 1
            if cont_label_added:
                try:
                    p.legend = ''
                except Exception:
                    p.legend = False
            else:
                try:
                    w.set_label(1, cont_label)
                except Exception:
                    pass
                try:
                    p.legend = cont_label
                except Exception:
                    pass
                cont_label_added = True
        except Exception:
            pass
        legend_entries.append(cont_label if cont_label_added and idx == 1 else "")

    try:
        gl.rescale()
    except Exception:
        pass

    try:
        gp.activate()
    except Exception:
        pass

    try:
        x_axis = gl.axis('x')
    except Exception:
        x_axis = None
    try:
        y_axis = gl.axis('y')
    except Exception:
        y_axis = None

    base_pad = min(max(0.02 * y_range, 0.3), y_range * 0.1)
    label_gap = min(max(0.12 * y_range, 0.5), y_range * 0.4)
    label_extra = min(max(0.05 * y_range, 0.3), y_range * 0.2)
    tick_level = y_min - label_gap
    label_bottom = tick_level - label_extra
    axis_bottom = y_min - base_pad

    try:
        if x_axis is not None:
            x_axis.set_limits(0.5, len(samples) + 0.5, 1.0)
    except Exception:
        pass
    try:
        if y_axis is not None:
            y_axis.set_limits(axis_bottom, plot_top)
    except Exception:
        pass

    try:
        if x_axis is not None:
            x_axis.title = "Sample"
    except Exception:
        pass
    try:
        if y_axis is not None:
            y_axis.title = TS_LABELS[var]
    except Exception:
        pass

    try:
        gl.set_int('legend.update', 0)
        gl.set_int('legend.box', 0)
        gl.set_int('legend.just', 1)
    except Exception:
        pass

    legend_text = "\\n".join([entry for entry in legend_entries if entry])
    try:
        legend = gl.label('Legend')
    except Exception:
        legend = None
    if legend is not None:
        try:
            legend.text = legend_text
        except Exception:
            pass
        try:
            legend_loc = str(globals().get("LEGEND_LOCATION", "inside")).lower()
            if legend_loc in {"outside_right", "outside", "outside right"}:
                legend.set_float('x1', 1.02)
                legend.set_float('y1', 0.5)
            else:
                legend.set_float('x1', 0.18)
                legend.set_float('y1', 0.88)
        except Exception:
            pass

    try:
        gl.set_int('x.top', 0)
        gl.set_int('y.right', 0)
        gl.set_int('x.top.label.show', 0)
        gl.set_int('y.right.label.show', 0)
        gl.set_int('x.top.ticklabels', 0)
        gl.set_int('y.right.ticklabels', 0)
    except Exception:
        pass

    for idx in range(1, len(samples) + 1):
        try:
            gl.remove_label(f'py_xtick{idx}')
        except Exception:
            pass
    try:
        gl.set_int('x.label.show', 0)
    except Exception:
        pass
    try:
        gl.set_int('x.ticklabels', 0)
    except Exception:
        pass

    manual_labels_added = False
    for idx, sample in enumerate(samples, start=1):
        text = display_by_idx.get(sample_idx[sample], sample.replace('_', '/'))
        x_pos = sample_label_positions.get(sample, float(sample_idx[sample]))
        try:
            label = gl.add_label(text, float(x_pos), tick_level)
        except Exception:
            label = None
        if label is None:
            continue
        try:
            label.name = f'py_xtick{idx}'
            label.set_int('attach', 0)
            try:
                label.set_int('horzalign', 1)
            except Exception:
                pass
            try:
                label.set_int('vertalign', 0)
            except Exception:
                pass
        except Exception:
            pass
        manual_labels_added = True
    if manual_labels_added and y_axis is not None:
        try:
            y_axis.set_limits(label_bottom, plot_top)
        except Exception:
            pass

    for idx in range(1, len(delta_labels) + 1):
        try:
            gl.remove_label(f'py_delta{idx}')
        except Exception:
            pass
    for idx, (x_pos, y_pos, text) in enumerate(delta_labels, start=1):
        try:
            label = gl.add_label(text, float(x_pos), float(y_pos))
        except Exception:
            label = None
        if label is None:
            continue
        try:
            label.name = f'py_delta{idx}'
            label.set_int('attach', 0)
            try:
                label.set_int('horzalign', 1)
            except Exception:
                pass
            try:
                label.set_int('vertalign', 0)
            except Exception:
                pass
        except Exception:
            pass

    title = f"{comp} {anneal} — {TS_LABELS[var]}"
    try:
        title_label = gl.label('Title')
    except Exception:
        title_label = None
    if title_label is not None:
        try:
            title_label.text = title
        except Exception:
            pass
    try:
        gl.remove_label('py_title')
    except Exception:
        pass
    title_center = (len(samples) + 1) / 2.0
    try:
        manual_title = gl.add_label(title, title_center, title_level)
    except Exception:
        manual_title = None
    if manual_title is not None:
        try:
            manual_title.name = 'py_title'
            manual_title.set_int('attach', 0)
            try:
                manual_title.set_int('horzalign', 1)
            except Exception:
                pass
            try:
                manual_title.set_int('vertalign', 0)
            except Exception:
                pass
        except Exception:
            pass

from ..common import maybe_handle_outliers


def main(files: List[str], backend: str = BACKEND, preprocessed_data: pd.DataFrame | None = None):
    apply_readability_fonts()
    if preprocessed_data is not None:
        data = preprocessed_data.copy(deep=True)
        print("Using results from the immediate outlier check.")
    else:
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
        print("Cancelled.")
        return

    if wants_matplotlib(backend):
        if do_show:
            show_plots()
        else:
            plt.close('all')
    else:
        plt.close('all')

