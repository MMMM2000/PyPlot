import os
import math
from pathlib import Path

import pytest

# Use an offscreen platform for headless testing environments
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
from matplotlib.figure import Figure

from plotting.plugins.pdf_plotter.core import (
    PdfPlotStyle,
    collect_lines_by_file,
    create_matplotlib_figure,
    load_pdf_data,
    parse_pdf_to_rows,
)


def test_plot_creates_top_level_window(signal_data_root: Path):
    data = load_pdf_data([signal_data_root / "pdf_data" / "four_columns.pdf"])
    lines_by_file = collect_lines_by_file(
        data,
        x_name="Force (N)",
        selected_vars=["T1+T2"],
        zero_first=True,
    )
    assert lines_by_file
    path, lines = next(iter(lines_by_file.items()))
    title = f"T1+T2 (arb. u.) vs Force (N) - {os.path.splitext(os.path.basename(path))[0]}"
    fig = create_matplotlib_figure(
        [(f"{os.path.splitext(os.path.basename(path))[0]} {label}", xs, ys) for label, xs, ys in lines],
        title=title,
        x_label="Force (N)",
        y_label="T1+T2 (arb. u.)",
        style=PdfPlotStyle(),
    )
    assert isinstance(fig, Figure)
    assert fig.axes

def test_parse_handles_six_column_format(signal_data_root: Path):
    rows = parse_pdf_to_rows(signal_data_root / "pdf_data" / "six_columns.pdf")
    assert rows == [(1.0, 3.0, 7.0, 0.0), (2.0, 5.0, 17.0, 0.1), (3.0, 7.0, 27.0, 0.2), (4.0, 9.0, 37.0, 0.3)]
    t1, t2, force, strain = rows[0]
    assert all(isinstance(value, float) for value in (t1, t2, force, strain))
    # Force column is distinct from the derived T2-T1 column
    assert not math.isclose(force, t2 - t1, rel_tol=1e-3)
