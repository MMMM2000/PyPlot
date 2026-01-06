import os
import math

import pytest

# Use an offscreen platform for headless testing environments
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
from PyQt6 import QtWidgets
from matplotlib.figure import Figure

from plotting.plugins.pdf_plotter.dialog import PdfPlotterWindow, parse_pdf_to_rows


def test_plot_creates_top_level_window():
    app = QtWidgets.QApplication.instance()
    created = False
    if app is None:
        app = QtWidgets.QApplication([])
        created = True
    win = PdfPlotterWindow()
    try:
        rows = parse_pdf_to_rows('sample_data/3D prud c.3 2mm iba data.pdf')
        win.data.append(('sample_data/3D prud c.3 2mm iba data.pdf', rows))
        win.plot()
        assert win.matplotlib_figures, "Expected at least one Matplotlib figure"
        fig = win.matplotlib_figures[-1]
        assert isinstance(fig, Figure)
    finally:
        # Ensure windows and application are cleaned up
        win.close()
        if created:
            app.quit()

def test_parse_handles_six_column_format():
    rows = parse_pdf_to_rows('sample_data/pdf_data/189_2_c2 1000hz.pdf')
    assert rows, 'Expected rows from six-column pdf sample'
    t1, t2, force, strain = rows[0]
    assert all(isinstance(value, float) for value in (t1, t2, force, strain))
    # Force column is distinct from the derived T2-T1 column
    assert not math.isclose(force, t2 - t1, rel_tol=1e-3)
