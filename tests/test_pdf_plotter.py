import os

# Use an offscreen platform for headless testing environments
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets
from matplotlib.figure import Figure

from plotting.pdf_plotter.pdf_gui import PdfPlotterWindow, parse_pdf_to_rows


def test_plot_creates_matplotlib_figure():
    app = QtWidgets.QApplication([])
    win = PdfPlotterWindow()
    try:
        rows = parse_pdf_to_rows('sample_data/3D prud c.3 2mm iba data.pdf')
        win.data.append(('sample_data/3D prud c.3 2mm iba data.pdf', rows))
        win.plot()
        assert win.last_fig is not None
        assert isinstance(win.last_fig, Figure)
    finally:
        win.clear_plot()
        win.close()
        app.quit()
