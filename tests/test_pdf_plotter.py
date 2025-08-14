import os

# Use an offscreen platform for headless testing environments
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from plotting.pdf_plotter.pdf_gui import PdfPlotterWindow, parse_pdf_to_rows


def test_plot_creates_top_level_window():
    app = QtWidgets.QApplication([])
    win = PdfPlotterWindow()
    try:
        rows = parse_pdf_to_rows('sample_data/3D prud c.3 2mm iba data.pdf')
        win.data.append(('sample_data/3D prud c.3 2mm iba data.pdf', rows))
        win.plot()
        assert win.plot_wins
        # Plot windows should be top-level windows (no parent)
        assert all(w.parent() is None for w in win.plot_wins)
    finally:
        # Ensure windows and application are cleaned up
        for w in win.plot_wins:
            w.close()
        win.close()
        app.quit()
