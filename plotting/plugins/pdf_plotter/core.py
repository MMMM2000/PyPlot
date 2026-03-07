from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple, cast

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore

NumberRow = Tuple[float, float, float, float]

_LINE_SANITIZE_RE = re.compile(r"[^\d;.,\-\+\s]")
_TOKEN_TRANSLATION = str.maketrans(
    {
        chr(0x2212): "-",
        chr(0x2012): "-",
        chr(0x2013): "-",
        chr(0x2014): "-",
        chr(0x2015): "-",
        chr(0xFF0D): "-",
        chr(0xFE63): "-",
        chr(0x00A0): " ",
        chr(0x202F): " ",
        chr(0x2009): " ",
        chr(0x200A): " ",
        chr(0x2007): " ",
        "'": "",
        "`": "",
    }
)


@dataclass
class PdfPlotStyle:
    line_style: str = "-"
    marker_style: str = "o"
    line_width: float = 1.5
    marker_size: float = 5.0
    show_grid: bool = True
    show_legend: bool = True
    legend_loc: str = "best"
    legend_font_size: int = 10
    title_font_size: int = 12
    label_font_size: int = 11
    tick_font_size: int = 10
    figure_width_in: float = 6.3
    figure_height_in: float = 4.7


def _normalize_numeric_token(token: str) -> float:
    token = token.strip()
    if not token:
        raise ValueError("empty token")
    if token.count(",") and token.count("."):
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "")
            token = token.replace(",", ".")
        else:
            token = token.replace(",", "")
    elif token.count(","):
        token = token.replace(",", ".")
    token = token.replace(" ", "")
    return float(token)


def resolve_pdf_pointer(path: Path) -> Path:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except Exception:
        return path
    if content.startswith("pdf_data/"):
        candidate = path.parent / content
        if candidate.exists():
            return candidate
    return path


def parse_pdf_to_rows(path: str | Path) -> List[NumberRow]:
    if PdfReader is None:
        raise RuntimeError("pypdf not installed. Install with: pip install pypdf")
    source = Path(path)
    if source.is_file():
        source = resolve_pdf_pointer(source)
    rows: List[NumberRow] = []
    reader = PdfReader(str(source))
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = raw.translate(_TOKEN_TRANSLATION).strip()
            if not line:
                continue
            sanitized = _LINE_SANITIZE_RE.sub("", line)
            parts = [segment.strip() for segment in sanitized.split(";") if segment.strip()]
            if len(parts) < 4:
                continue
            try:
                numbers = [_normalize_numeric_token(part) for part in parts]
            except ValueError:
                continue
            if len(numbers) >= 6:
                t1, t2 = numbers[0], numbers[1]
                force, strain = numbers[-2], numbers[-1]
            elif len(numbers) == 4:
                t1, t2, force, strain = numbers
            else:
                continue
            rows.append((t1, t2, force, strain))
    return rows


def load_pdf_data(paths: Iterable[str | Path]) -> List[Tuple[str, List[NumberRow]]]:
    loaded: List[Tuple[str, List[NumberRow]]] = []
    for entry in paths:
        path = Path(entry)
        loaded.append((str(path), parse_pdf_to_rows(path)))
    return loaded


def collect_lines_by_file(
    data: List[Tuple[str, List[NumberRow]]],
    *,
    x_name: str,
    selected_vars: List[str],
    zero_first: bool,
) -> Dict[str, List[Tuple[str, np.ndarray, np.ndarray]]]:
    lines_by_file: Dict[str, List[Tuple[str, np.ndarray, np.ndarray]]] = {}
    for path, rows in data:
        series_rows: List[Tuple[str, np.ndarray, np.ndarray]] = []
        for y_name in selected_vars:
            xs: List[float] = []
            ys: List[float] = []
            for t1, t2, force, strain in rows:
                x = force if x_name.startswith("Force") else strain
                if y_name == "T1":
                    y = t1
                elif y_name == "T2":
                    y = t2
                elif y_name == "T2-T1":
                    y = t2 - t1
                else:
                    y = t1 + t2
                xs.append(x)
                ys.append(y)
            if zero_first and ys:
                base = ys[0]
                ys = [value - base for value in ys]
            series_rows.append((y_name, np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)))
        if series_rows:
            lines_by_file[path] = series_rows
    return lines_by_file


def create_matplotlib_figure(
    lines: Iterable[Tuple[str, np.ndarray, np.ndarray]],
    *,
    title: str,
    x_label: str,
    y_label: str,
    style: PdfPlotStyle,
) -> Figure:
    fig = Figure(figsize=(style.figure_width_in, style.figure_height_in))
    ax = fig.add_subplot(111)
    ls = "None" if style.line_style == "None" else style.line_style
    marker = None if style.marker_style == "None" else style.marker_style
    for label, x, y in lines:
        ax.plot(
            x,
            y,
            linestyle=ls,
            marker=marker,
            linewidth=float(style.line_width),
            markersize=float(style.marker_size),
            label=label,
        )
    ax.set_xlabel(x_label, fontsize=int(style.label_font_size))
    ax.set_ylabel(y_label, fontsize=int(style.label_font_size))
    ax.set_title(title, fontsize=int(style.title_font_size))
    ax.grid(bool(style.show_grid), which="both", linestyle="--", alpha=0.4)
    if style.show_legend:
        ax.legend(loc=style.legend_loc, fontsize=int(style.legend_font_size))
    ax.tick_params(labelsize=int(style.tick_font_size))
    fig.tight_layout()
    return fig


def plot_lines_to_origin(
    lines: Iterable[Tuple[str, np.ndarray, np.ndarray]],
    *,
    title: str,
    x_label: str,
    y_label: str,
) -> None:
    import originpro as op  # pragma: no cover - Origin only

    try:
        op.set_show()
    except Exception:
        pass
    graph = cast(Any, op.new_graph(template="scatter"))
    layer = graph[0]
    for index, (label, xs, ys) in enumerate(lines):
        sheet = cast(Any, op.new_sheet("w", lname=f"data_{index}"))
        sheet.from_list(0, np.asarray(xs, dtype=float).tolist())
        sheet.from_list(1, np.asarray(ys, dtype=float).tolist())
        sheet.cols_axis("XY")
        plot_obj = layer.add_plot(sheet, coly=1, colx=0, type="y")
        if plot_obj is not None:
            try:
                plot_obj.symbol_shape = 2
            except Exception:
                pass
            try:
                plot_obj.lname = label
            except Exception:
                pass
    try:
        graph.activate()
        escaped_title = title.replace('"', "'")
        op.lt_exec('page.antialias=1; layer -aa 1;')
        op.lt_exec(f'title -s "{escaped_title}";')
        op.lt_exec(f'lab -xb "{x_label}"; lab -yl "{y_label}"; legend;')
    except Exception:
        pass
    try:
        op.exit()
    except Exception:
        pass
