import os

# Use an offscreen platform for headless testing environments
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets
import pytest

from plotting.pdf_plotter.pdf_gui import PdfPlotterWindow, parse_pdf_to_rows


@pytest.fixture()
def app():
    app = QtWidgets.QApplication.instance()
    created = False
    if app is None:
        app = QtWidgets.QApplication([])
        created = True
    yield app
    if created:
        app.quit()


def test_plot_creates_top_level_window(app):
    win = PdfPlotterWindow()
    try:
        rows = parse_pdf_to_rows('sample_data/3D prud c.3 2mm iba data.pdf')
        win.data.append(('sample_data/3D prud c.3 2mm iba data.pdf', rows))
        win.plot()
        assert win.plot_win is not None
        # Plot window should be a top-level window (no parent)
        assert win.plot_win.parent() is None
    finally:
        # Ensure windows and application are cleaned up
        win.close()


def test_y_unit_scaling_and_label(app):
    win = PdfPlotterWindow()
    try:
        rows = parse_pdf_to_rows('sample_data/3D prud c.3 2mm iba data.pdf')
        win.data.append(('sample_data/3D prud c.3 2mm iba data.pdf', rows))
        win.plot()
        assert win.plot_win is not None
        line = win.plot_win.ax.lines[0]
        y_us = line.get_ydata()[1]
        assert "(µs)" in win.plot_win.ax.get_ylabel()
        win.y_unit_combo.setCurrentText("ms")
        win.plot()
        line_ms = win.plot_win.ax.lines[0]
        y_ms = line_ms.get_ydata()[1]
        assert abs(y_ms - y_us * 1e-3) < 1e-6
        assert "(ms)" in win.plot_win.ax.get_ylabel()
    finally:
        win.close()
