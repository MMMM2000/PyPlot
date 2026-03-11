import os
import math

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


def test_plot_creates_top_level_window():
    data = load_pdf_data(["sample_data/pdf_data/sample1.pdf"])
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

def test_parse_handles_six_column_format():
    rows = parse_pdf_to_rows('sample_data/pdf_data/189_2_c2 1000hz.pdf')
    assert rows, 'Expected rows from six-column pdf sample'
    t1, t2, force, strain = rows[0]
    assert all(isinstance(value, float) for value in (t1, t2, force, strain))
    # Force column is distinct from the derived T2-T1 column
    assert not math.isclose(force, t2 - t1, rel_tol=1e-3)
