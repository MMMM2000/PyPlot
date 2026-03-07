from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Sequence, Tuple

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import numpy as np
import pandas as pd

from plotting.shared.backends import wants_matplotlib, wants_origin
from plotting.shared.config import load_config
from plotting.shared.readability import apply_readability_fonts, apply_readability
from plotting.shared.utils import show_plots

_CFG = load_config().get("hysteresis_loops", {})
MODE = _CFG.get("MODE", "Combined")
SHOW_PLOTS = bool(_CFG.get("SHOW_PLOTS", True))
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

X_AXIS_LABEL = "H [A/m]"
Y_AXIS_LABEL = "F [Wb]"

_TEMP_RE = re.compile(r"(\d+)\s*([°]?[Cc])", re.IGNORECASE)


@dataclass(frozen=True)
class HysteresisLoopRecord:
    path: Path
    base_name: str
    anneal_sort_key: float
    anneal_label: str
    x: np.ndarray
    y: np.ndarray


def _clean_numeric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()
    for column in cleaned.columns:
        series = cleaned[column].astype(str).str.replace("\u2212", "-", regex=False).str.strip()
        cleaned[column] = pd.to_numeric(series, errors="coerce")
    return cleaned


def _read_numeric_frame(path: Path) -> pd.DataFrame:
    errors: list[str] = []
    candidates: tuple[str | None, ...] = (r"\s+", "\t", ",", ";", None)
    for sep in candidates:
        try:
            frame = pd.read_csv(
                path,
                sep=sep,
                engine="python",
                comment="#",
                header=None,
                dtype=str,
                on_bad_lines="skip",
            )
        except (OSError, csv.Error, pd.errors.ParserError, UnicodeDecodeError) as exc:
            errors.append(str(exc))
            continue
        if frame.empty:
            continue
        frame = frame.dropna(axis=1, how="all")
        if frame.empty:
            continue
        numeric = _clean_numeric_frame(frame)
        usable = [
            column
            for column in numeric.columns
            if int(numeric[column].notna().sum()) >= 2
        ]
        if len(usable) < 2:
            continue
        subset = numeric.loc[:, usable[:2]].dropna(how="any")
        if not subset.empty:
            return subset.reset_index(drop=True)
    message = "; ".join(errors[:3]) if errors else "no usable numeric columns found"
    raise ValueError(f"{path.name}: {message}")


def load_loop(path: str | Path) -> Tuple[np.ndarray, np.ndarray]:
    source = Path(path)
    data = _read_numeric_frame(source)
    x = data.iloc[:, 0].to_numpy(dtype=float)
    y = data.iloc[:, 1].to_numpy(dtype=float)
    return x, y


def _parse_meta(filename: str) -> Tuple[str, float, str]:
    """Return ``(base_name, sort_key, label)`` for one hysteresis source file."""

    name = os.path.splitext(os.path.basename(filename))[0]
    norm = name.lower().replace("-", "").replace("_", "")
    if "ascast" in norm or ("asc" in norm and "cast" in norm):
        parts = name.split()
        base = " ".join(parts[:-1]) if len(parts) > 1 else name
        return base, float("-inf"), "as-cast"
    match = _TEMP_RE.search(name)
    if match:
        temperature = float(match.group(1))
        base = name[: match.start()].strip()
        return base, temperature, f"{int(temperature)}°C"
    parts = name.split()
    base = " ".join(parts[:-1]) if len(parts) > 1 else name
    return base, float("-inf"), "as-cast"


def load_records(paths: Iterable[str | Path]) -> list[HysteresisLoopRecord]:
    records: list[HysteresisLoopRecord] = []
    for entry in paths:
        path = Path(entry)
        x, y = load_loop(path)
        base_name, anneal_sort_key, anneal_label = _parse_meta(path.name)
        records.append(
            HysteresisLoopRecord(
                path=path,
                base_name=base_name,
                anneal_sort_key=anneal_sort_key,
                anneal_label=anneal_label,
                x=x,
                y=y,
            )
        )
    return records


def sort_records(records: Sequence[HysteresisLoopRecord]) -> list[HysteresisLoopRecord]:
    return sorted(
        records,
        key=lambda record: (
            record.anneal_sort_key,
            record.anneal_label.casefold(),
            record.path.name.casefold(),
        ),
    )


def group_records(
    records: Sequence[HysteresisLoopRecord],
) -> dict[str, list[HysteresisLoopRecord]]:
    grouped: dict[str, list[HysteresisLoopRecord]] = {}
    for record in sort_records(records):
        grouped.setdefault(record.base_name, []).append(record)
    return grouped


def _apply_legend_line_colors(ax: Any) -> None:
    legend = ax.get_legend()
    if legend is None:
        return
    handles = getattr(legend, "legend_handles", None)
    if handles is None:
        handles = getattr(legend, "legendHandles", [])
    for handle, text in zip(handles, legend.get_texts()):
        get_color = getattr(handle, "get_color", None)
        if callable(get_color):
            try:
                text.set_color(get_color())
            except Exception:
                continue


def _style_axes(ax: Any, *, title: str | None = None) -> None:
    ax.grid(True, linestyle="--", alpha=0.3)
    ax.set_xlabel(X_AXIS_LABEL, fontsize=AXIS_LABEL_SIZE)
    ax.set_ylabel(Y_AXIS_LABEL, fontsize=AXIS_LABEL_SIZE)
    if title and SHOW_TITLE:
        ax.set_title(title, fontsize=TITLE_SIZE, pad=10)
    ax.tick_params(axis="both", labelsize=TICK_SIZE)
    apply_readability(ax, globals())
    _apply_legend_line_colors(ax)


def combined_figure(records: Sequence[HysteresisLoopRecord]) -> Figure:
    ordered = sort_records(records)
    if not ordered:
        raise ValueError("No hysteresis loop data available.")
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    for record in ordered:
        ax.plot(record.x, record.y, linewidth=1.4, label=record.anneal_label)
    if SHOW_LEGEND and len(ordered) > 1:
        ax.legend(
            title="Anneal",
            loc="best",
            fontsize=LEGEND_SIZE,
            labelcolor="linecolor",
        )
    _style_axes(ax, title=ordered[0].base_name)
    fig.tight_layout()
    _apply_legend_line_colors(ax)
    return fig


def stacked_figure(records: Sequence[HysteresisLoopRecord]) -> Figure:
    ordered = sort_records(records)
    if not ordered:
        raise ValueError("No hysteresis loop data available.")
    fig, axes = plt.subplots(
        len(ordered),
        1,
        sharex=True,
        figsize=(7.2, max(2.4, 1.8 * len(ordered))),
        gridspec_kw={"hspace": 0.05},
    )
    if len(ordered) == 1:
        axes = [axes]
    for ax, record in zip(axes, ordered):
        ax.plot(record.x, record.y, linewidth=1.3, label=record.anneal_label)
        ax.text(
            0.02,
            0.96,
            record.anneal_label,
            transform=ax.transAxes,
            va="top",
            ha="left",
        )
        _style_axes(ax)
    axes[-1].set_xlabel(X_AXIS_LABEL, fontsize=AXIS_LABEL_SIZE)
    if SHOW_TITLE:
        fig.suptitle(ordered[0].base_name, fontsize=TITLE_SIZE)
    fig.tight_layout()
    return fig


def separate_figures(records: Sequence[HysteresisLoopRecord]) -> list[Figure]:
    figures: list[Figure] = []
    for record in sort_records(records):
        fig, ax = plt.subplots(figsize=(7.0, 4.4))
        ax.plot(record.x, record.y, linewidth=1.4, label=record.anneal_label)
        if SHOW_LEGEND:
            ax.legend(loc="best", fontsize=LEGEND_SIZE, labelcolor="linecolor")
        _style_axes(ax, title=f"{record.base_name} - {record.anneal_label}")
        fig.tight_layout()
        figures.append(fig)
    return figures


def _origin_plot_combined(loaded: Sequence[Tuple[str, np.ndarray, np.ndarray]]) -> None:
    import originpro as op  # pragma: no cover - Origin only

    origin_any: Any = op
    try:
        origin_any.set_show()
    except Exception:
        pass

    book: Any = origin_any.new_book("w", lname="Hysteresis (Python)")
    book.activate()
    graph: Any = origin_any.new_graph(template="line")
    layer: Any = graph[0]

    base_title = None
    for path, x, y in loaded:
        base, _temperature, label = _parse_meta(path)
        if base_title is None:
            base_title = base
        worksheet: Any = origin_any.new_sheet("w", lname=label)
        worksheet.from_list(0, x)
        worksheet.from_list(1, y)
        worksheet.cols_axis("XY")
        layer.add_plot(worksheet, coly=1, colx=0, type="y")

    try:
        graph.activate()
        origin_any.lt_exec('lab -xb "H [A/m]";')
        origin_any.lt_exec('lab -yl "F [Wb]";')
        if base_title:
            safe_title = base_title.replace('"', "'")
            origin_any.lt_exec(f'title -s "{safe_title}";')
        origin_any.lt_exec("legend;")
    except Exception:
        pass


def _origin_plot_separate(loaded: Sequence[Tuple[str, np.ndarray, np.ndarray]]) -> None:
    import originpro as op  # pragma: no cover - Origin only

    origin_any: Any = op
    try:
        origin_any.set_show()
    except Exception:
        pass

    book: Any = origin_any.new_book("w", lname="Hysteresis (Python)")
    book.activate()
    for path, x, y in loaded:
        base, _temperature, label = _parse_meta(path)
        worksheet: Any = origin_any.new_sheet("w", lname=label)
        worksheet.from_list(0, x)
        worksheet.from_list(1, y)
        worksheet.cols_axis("XY")
        graph: Any = origin_any.new_graph(template="line")
        layer: Any = graph[0]
        layer.add_plot(worksheet, coly=1, colx=0, type="y")
        try:
            graph.activate()
            safe_title = f"{base} - {label}".replace('"', "'")
            origin_any.lt_exec(f'title -s "{safe_title}";')
            origin_any.lt_exec('lab -xb "H [A/m]";')
            origin_any.lt_exec('lab -yl "F [Wb]";')
        except Exception:
            pass


def plot_loops(
    paths: Sequence[str | Path],
    mode: str = "Combined",
    show: bool = True,
    backend: str = BACKEND,
) -> Figure | List[Figure] | None:
    if IMPROVE_READABILITY:
        apply_readability_fonts()

    records = load_records(paths)
    grouped = group_records(records)

    if not wants_matplotlib(backend) and wants_origin(backend):
        loaded = [(str(record.path), record.x, record.y) for record in records]
        mode_key = mode.strip().lower()
        if mode_key == "separate":
            _origin_plot_separate(loaded)
        else:
            _origin_plot_combined(loaded)
        return None

    mode_key = mode.strip().lower()
    if mode_key == "separate":
        figures = separate_figures(records)
        if show:
            show_plots()
        return figures

    builders = {
        "combined": combined_figure,
        "stacked": stacked_figure,
    }
    builder = builders.get(mode_key, combined_figure)
    figures = [builder(group) for group in grouped.values()]
    if show:
        show_plots()
    if len(figures) == 1:
        return figures[0]
    return figures


def main(files: List[str], backend: str = BACKEND) -> None:
    plot_loops(
        files,
        mode=MODE,
        show=SHOW_PLOTS and wants_matplotlib(backend),
        backend=backend,
    )
