
from __future__ import annotations

import os
import re
import sys
import weakref
from typing import Any, Dict, Iterable, List, Tuple

from PyQt6 import QtCore, QtGui, QtWidgets

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

try:  # optional dependency
    from PyPDF2 import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore

from ..utils import (
    apply_system_theme,
    select_files_or_folder,
    save_figure,
    prepare_output_dir,
    get_last_output_dir,
    set_last_output_dir,
    run_with_console,
    get_readability,
    set_readability,
    apply_readability_fonts,
    restore_backend_choice,
    store_backend_choice,
    selected_backend,
    restore_png_dpi,
    store_png_dpi,
)  # type: ignore
from ..backends import wants_matplotlib, wants_origin

NumberRow = Tuple[float, float, float, float]  # T1, T2, Force, Strain


class _NoWheelSpinBox(QtWidgets.QSpinBox):
    """A QSpinBox that ignores wheel events unless focused."""

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # type: ignore[override]
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _NoWheelDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """A QDoubleSpinBox that ignores wheel events unless focused."""

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # type: ignore[override]
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


# -----------------------------------------------------------------------------
# Data loading
# -----------------------------------------------------------------------------
def parse_pdf_to_rows(path: str) -> List[NumberRow]:
    """Extract numeric rows from a PDF.

    Each valid line contains 4 semicolon-separated values: T1; T2; Force; Strain.
    Comma decimal separators are accepted, spaces ignored.
    """
    if PdfReader is None:
        raise RuntimeError("PyPDF2 not installed. Install with: pip install PyPDF2")
    rows: List[NumberRow] = []
    reader = PdfReader(path)
    num = r"-?\d+(?:[.,]\d+)?"
    pat = re.compile(rf"\s*({num})\s*;\s*({num})\s*;\s*({num})\s*;\s*({num})\s*")
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            s = raw.strip()
            m = pat.fullmatch(s)
            if not m:
                # scrub garbage characters and try again
                s2 = re.sub(r"[^\d;,.\-\s]", "", s)
                m = pat.fullmatch(s2)
            if m:
                try:
                    t1 = float(m.group(1).replace(",", "."))
                    t2 = float(m.group(2).replace(",", "."))
                    force = float(m.group(3).replace(",", "."))
                    strain = float(m.group(4).replace(",", "."))
                    rows.append((t1, t2, force, strain))
                except ValueError:
                    pass
    return rows


# -----------------------------------------------------------------------------
# A single plot window using Matplotlib for display
# -----------------------------------------------------------------------------
class PlotWindow(QtWidgets.QMainWindow):
    """Top level window holding a Matplotlib plot."""

    instances: "weakref.WeakSet[PlotWindow]" = weakref.WeakSet()

    def __init__(self, parent: QtWidgets.QWidget | None = None, *, controller: "PdfPlotterWindow" | None = None) -> None:
        super().__init__(parent)
        PlotWindow.instances.add(self)
        self.controller = controller
        self._last_lines: List[Tuple[str, np.ndarray, np.ndarray]] = []
        self._last_title: str = ""
        self.fig = Figure()
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)
        self.canvas.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.setCentralWidget(central)
        self._aspect_ratio = 1.0
        self._base_width = 1
        self._base_dpi = self.fig.dpi

    def apply_fixed_plot_size(self, fig_w_in: float, fig_h_in: float, *, resize_window: bool = False) -> None:
        dpi = self.logicalDpiX() or 100.0
        wpx = int(round(fig_w_in * dpi))
        hpx = int(round(fig_h_in * dpi))
        self._aspect_ratio = wpx / max(hpx, 1)
        self._base_width = wpx
        self._base_dpi = self.fig.dpi
        self.canvas.setMinimumSize(0, 0)
        self.canvas.resize(wpx, hpx)
        if resize_window:
            self.resize(self.sizeHint())
        else:
            self.updateGeometry()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        w = self.canvas.width()
        h = int(round(w / self._aspect_ratio))
        self.canvas.setFixedHeight(h)
        scale = w / self._base_width if self._base_width else 1
        self.fig.set_dpi(self._base_dpi * scale)
        self.canvas.draw_idle()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        try:
            PlotWindow.instances.discard(self)
        except Exception:
            pass
        super().closeEvent(event)


# -----------------------------------------------------------------------------
# Main settings window
# -----------------------------------------------------------------------------
class PdfPlotterWindow(QtWidgets.QWidget):
    """Settings window for plotting data extracted from PDFs."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF T1/T2 Plotter")
        self.resize(560, 760)

        # Loaded data: list of (path, rows)
        self.data: List[Tuple[str, List[NumberRow]]] = []

        # Track plot windows and last plotted data. ``plot_win`` is kept for
        # backward compatibility with older code expecting a single window.
        self.plot_wins: List[PlotWindow] = []
        self.plot_win: PlotWindow | None = None
        self._last_lines: List[Tuple[str, np.ndarray, np.ndarray]] = []
        self._last_title: str = ""
        self._last_x_label: str = ""
        self._last_y_label: str = ""

        # Make the settings UI scrollable
        outer = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        outer.addWidget(scroll)

        content = QtWidgets.QWidget()
        content.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        form = QtWidgets.QFormLayout(content)
        scroll.setWidget(content)

        # Files
        self.file_edit = QtWidgets.QLineEdit()
        self.browse_btn = QtWidgets.QPushButton("Open PDF(s)…")
        self.browse_btn.clicked.connect(self._load_files)
        file_box = self._hbox(self.file_edit, self.browse_btn)
        form.addRow("Files", file_box)

        # Variables
        self.y_checks: List[QtWidgets.QCheckBox] = []
        y_box = QtWidgets.QWidget()
        y_layout = QtWidgets.QHBoxLayout(y_box)
        for name in ["T1+T2", "T1", "T2", "T2–T1"]:
            cb = QtWidgets.QCheckBox(name)
            cb.setChecked(name == "T1+T2")
            cb.stateChanged.connect(self._maybe_auto_plot)
            y_layout.addWidget(cb)
            self.y_checks.append(cb)
        self.x_combo = QtWidgets.QComboBox()
        self.x_combo.addItems(["Force (N)", "Strain (mm)", "Force & Strain"])
        self.x_combo.currentIndexChanged.connect(self._maybe_auto_plot)
        form.addRow("Y variables", y_box)
        form.addRow("X variable", self.x_combo)

        # Plot mode and options
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Combined", "Separate"])
        self.mode_combo.currentIndexChanged.connect(self._maybe_auto_plot)
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])  # output backend
        restore_backend_choice("pdf_plotter", self.backend_combo, "matplotlib")
        self.backend_combo.currentIndexChanged.connect(self._maybe_auto_plot)
        self.zero_cb = QtWidgets.QCheckBox("First point at zero")
        self.zero_cb.setChecked(True)
        self.zero_cb.stateChanged.connect(self._maybe_auto_plot)
        form.addRow("Plot mode", self.mode_combo)
        form.addRow("Backend", self.backend_combo)
        form.addRow("Zero first", self.zero_cb)

        # Styling
        self.line_style = QtWidgets.QComboBox()
        self.line_style.addItems(["-", "--", ":", "-.", "None"])
        self.line_style.setCurrentIndex(0)
        self.marker_style = QtWidgets.QComboBox()
        self.marker_style.addItems(["o", "s", "d", "^", "v", "x", "+", ".", "None"])
        self.marker_style.setCurrentIndex(0)
        self.line_width = _NoWheelDoubleSpinBox()
        self.line_width.setRange(0.1, 10.0)
        self.line_width.setSingleStep(0.1)
        self.line_width.setValue(1.5)
        self.marker_size = _NoWheelDoubleSpinBox()
        self.marker_size.setRange(0.5, 30.0)
        self.marker_size.setSingleStep(0.5)
        self.marker_size.setValue(5.0)
        self.grid_cb = QtWidgets.QCheckBox()
        self.grid_cb.setChecked(True)
        for w in [self.line_style, self.marker_style]:
            w.currentIndexChanged.connect(self._maybe_auto_plot)
        for w in [self.line_width, self.marker_size, self.grid_cb]:
            (w.valueChanged if hasattr(w, "valueChanged") else w.stateChanged).connect(self._maybe_auto_plot)
        form.addRow("Line style", self.line_style)
        form.addRow("Marker", self.marker_style)
        form.addRow("Line width", self.line_width)
        form.addRow("Marker size", self.marker_size)
        form.addRow("Grid", self.grid_cb)

        self.dark_cb = QtWidgets.QCheckBox("Dark background")
        self.dark_cb.setChecked(False)
        self.dark_cb.toggled.connect(self._apply_dark_global)
        form.addRow("", self.dark_cb)

        # Legend
        self.legend_cb = QtWidgets.QCheckBox()
        self.legend_cb.setChecked(True)
        self.legend_loc = QtWidgets.QComboBox()
        self.legend_loc.addItems(
            [
                "best",
                "upper right",
                "upper left",
                "lower left",
                "lower right",
                "right",
                "center left",
                "center right",
                "lower center",
                "upper center",
                "center",
            ]
        )
        self.legend_loc.setCurrentIndex(0)
        self.legend_fs = _NoWheelSpinBox()
        self.legend_fs.setRange(6, 48)
        self.legend_fs.setValue(10)
        self.legend_cb.stateChanged.connect(self._maybe_auto_plot)
        self.legend_loc.currentIndexChanged.connect(self._maybe_auto_plot)
        self.legend_fs.valueChanged.connect(self._maybe_auto_plot)
        form.addRow("Legend", self.legend_cb)
        form.addRow("Legend loc", self.legend_loc)
        form.addRow("Legend size", self.legend_fs)

        # Fonts
        self.title_fs = _NoWheelSpinBox()
        self.title_fs.setRange(6, 72)
        self.title_fs.setValue(12)
        self.label_fs = _NoWheelSpinBox()
        self.label_fs.setRange(6, 72)
        self.label_fs.setValue(11)
        self.tick_fs = _NoWheelSpinBox()
        self.tick_fs.setRange(6, 48)
        self.tick_fs.setValue(10)
        for w in [self.title_fs, self.label_fs, self.tick_fs]:
            w.valueChanged.connect(self._maybe_auto_plot)
        form.addRow("Title size", self.title_fs)
        form.addRow("Label size", self.label_fs)
        form.addRow("Tick size", self.tick_fs)
        self.read_cb = QtWidgets.QCheckBox()
        self.read_cb.setChecked(get_readability("pdf_plotter"))
        form.addRow("Improve readability", self.read_cb)

        # Save options
        self.save_cb = QtWidgets.QCheckBox()
        self.save_cb.setChecked(False)
        self.out_dir = QtWidgets.QLineEdit(get_last_output_dir(os.getcwd(), key="pdf_plotter"))
        self.browse_out_btn = QtWidgets.QPushButton("Browse…")
        self.browse_out_btn.clicked.connect(self._browse_out)
        out_box = self._hbox(self.out_dir, self.browse_out_btn)
        self.subdir_cb = QtWidgets.QCheckBox("Create subfolder")
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(["png", "pdf", "svg"])
        fmt_box = self._hbox(self.format_combo)
        self.dpi_spin = _NoWheelSpinBox()
        self.dpi_spin.setRange(72, 3000)
        restore_png_dpi("pdf_plotter", self.dpi_spin, 1200)
        self.fig_w = _NoWheelDoubleSpinBox()
        self.fig_w.setRange(1.0, 1000.0)
        self.fig_w.setValue(180.0)  # default in mm
        self.fig_h = _NoWheelDoubleSpinBox()
        self.fig_h.setRange(1.0, 1000.0)
        self.fig_h.setValue(120.0)  # default in mm
        self.fig_units = QtWidgets.QComboBox()
        self.fig_units.addItems(["in", "cm", "mm"])
        self.fig_units.setCurrentIndex(2)  # default to mm
        self.lock_aspect_cb = QtWidgets.QCheckBox("Lock aspect ratio")
        self.lock_aspect_cb.setChecked(True)
        self._current_units = self.fig_units.currentText()
        self._aspect_ratio = self.fig_w.value() / max(self.fig_h.value(), 1e-9)
        self._updating_size = False

        # Wire unit/size behavior
        self.fig_units.currentTextChanged.connect(self._on_units_changed)
        self.lock_aspect_cb.toggled.connect(self._on_lock_toggled)
        self.fig_w.valueChanged.connect(self._on_width_changed)
        self.fig_h.valueChanged.connect(self._on_height_changed)
        self.save_now_btn = QtWidgets.QPushButton("Save Now")
        self.save_now_btn.clicked.connect(self.save_current)
        form.addRow("Save on plot", self.save_cb)
        form.addRow("Output dir", out_box)
        form.addRow("Create subfolder", self.subdir_cb)
        form.addRow("Format", fmt_box)
        form.addRow("DPI", self.dpi_spin)
        form.addRow("Figure size", self._hbox(self.fig_w, self.fig_h, self.fig_units, self.lock_aspect_cb))
        form.addRow("", self.save_now_btn)

        # Actions
        self.auto_cb = QtWidgets.QCheckBox("Auto update on change")
        self.auto_cb.setChecked(True)
        self.plot_btn = QtWidgets.QPushButton("Plot")
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.console = QtWidgets.QPlainTextEdit(); self.console.setReadOnly(True); self.console.setMaximumHeight(120)
        self.plot_btn.clicked.connect(lambda: run_with_console(self.plot, self.console))
        self.clear_btn.clicked.connect(self.clear_plot)
        btn_box = self._hbox(self.auto_cb, self.plot_btn, self.clear_btn)

        # Ensure the plot controls are always visible without scrolling by placing the
        # button row outside the scrollable area.
        outer.addWidget(btn_box)
        outer.addWidget(self.console)

    # --- Helpers ---------------------------------------------------------------
    def _hbox(self, *widgets: QtWidgets.QWidget) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        l = QtWidgets.QHBoxLayout(w)
        for x in widgets:
            l.addWidget(x)
        return w

    def _figure_size_inches(self) -> Tuple[float, float]:
        w = float(self.fig_w.value())
        h = float(self.fig_h.value())
        unit = self.fig_units.currentText()
        if unit == "cm":
            return w / 2.54, h / 2.54
        if unit == "mm":
            return w / 25.4, h / 25.4
        return w, h

    def _convert_units(self, value: float, from_unit: str, to_unit: str) -> float:
        to_mm = {"mm": 1.0, "cm": 10.0, "in": 25.4}
        if from_unit not in to_mm or to_unit not in to_mm:
            return value
        mm = value * to_mm[from_unit]
        return mm / to_mm[to_unit]

    def _apply_dark_global(self, on: bool) -> None:
        # Dark mode is applied when creating new plots
        self._maybe_auto_plot()

    def _line_colors(self, n: int) -> List[str]:
        """Return ``n`` distinct colors from Matplotlib's cycle."""
        cycle = list(plt.rcParams["axes.prop_cycle"].by_key().get("color", []))
        if n > len(cycle):
            reps = (n - 1) // len(cycle) + 1 if cycle else 0
            cycle = (cycle * (reps + 1))[:n]
        else:
            cycle = cycle[:n]
        if self.dark_cb.isChecked():
            adjusted: List[str] = []
            for c in cycle:
                q = QtGui.QColor(c)
                if q.lightness() < 128:
                    q = q.lighter(170)
                adjusted.append(q.name())
            return adjusted
        return cycle

    def _draw_on_axes(
        self,
        ax: Any,
        lines: Iterable[Tuple[str, np.ndarray, np.ndarray]],
        title: str,
        x_label: str,
        y_label: str,
    ) -> None:
        """Draw the given lines onto a Matplotlib Axes."""
        fig = ax.figure
        dark = self.dark_cb.isChecked()
        bg = "black" if dark else "white"
        fg = "white" if dark else "black"
        fig.patch.set_facecolor(bg)
        ax.set_facecolor(bg)

        ls = "None" if self.line_style.currentText() == "None" else self.line_style.currentText()
        marker = None if self.marker_style.currentText() == "None" else self.marker_style.currentText()
        lines = list(lines)
        colors = self._line_colors(len(lines))
        for (label, x, y), color in zip(lines, colors):
            ax.plot(
                x,
                y,
                linestyle=ls,
                marker=marker,
                linewidth=float(self.line_width.value()),
                markersize=float(self.marker_size.value()),
                color=color,
                label=label,
            )

        ax.set_xlabel(x_label, fontsize=int(self.label_fs.value()), color=fg)
        ax.set_ylabel(y_label, fontsize=int(self.label_fs.value()), color=fg)
        ax.grid(self.grid_cb.isChecked(), which="both", linestyle="--", alpha=0.4)
        ax.set_title(title, fontsize=int(self.title_fs.value()), color=fg)
        if self.legend_cb.isChecked():
            ax.legend(loc=self.legend_loc.currentText(), fontsize=int(self.legend_fs.value()))
        ax.tick_params(labelsize=int(self.tick_fs.value()), colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        fig.tight_layout()

    def _on_units_changed(self, new_unit: str) -> None:
        old_unit = getattr(self, "_current_units", new_unit)
        if new_unit == old_unit:
            return
        self._updating_size = True
        try:
            w_old = float(self.fig_w.value())
            h_old = float(self.fig_h.value())
            w_new = self._convert_units(w_old, old_unit, new_unit)
            h_new = self._convert_units(h_old, old_unit, new_unit)
            self.fig_w.setValue(w_new)
            self.fig_h.setValue(h_new)
            if self.lock_aspect_cb.isChecked():
                self._aspect_ratio = self.fig_w.value() / max(self.fig_h.value(), 1e-9)
            self._current_units = new_unit
        finally:
            self._updating_size = False
        self._maybe_auto_plot()

    def _on_lock_toggled(self, on: bool) -> None:
        self._aspect_ratio = self.fig_w.value() / max(self.fig_h.value(), 1e-9)

    def _on_width_changed(self, w: float) -> None:
        if self._updating_size:
            return
        if self.lock_aspect_cb.isChecked():
            self._updating_size = True
            try:
                h = w / max(self._aspect_ratio, 1e-9)
                self.fig_h.setValue(h)
            finally:
                self._updating_size = False
        self._maybe_auto_plot()

    def _on_height_changed(self, h: float) -> None:
        if self._updating_size:
            return
        if self.lock_aspect_cb.isChecked():
            self._updating_size = True
            try:
                w = h * self._aspect_ratio
                self.fig_w.setValue(w)
            finally:
                self._updating_size = False
        self._maybe_auto_plot()

    def _browse_out(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output directory", self.out_dir.text())
        if d:
            self.out_dir.setText(d)
            set_last_output_dir(d)

    def _maybe_auto_plot(self) -> None:
        if self.auto_cb.isChecked():
            self.plot()

    def _load_files(self) -> None:
        paths = select_files_or_folder(self, ext=".pdf", key="pdf_plotter")
        if not paths:
            return
        self.file_edit.setText(" ; ".join(paths))
        self.data.clear()
        total = 0
        for p in paths:
            try:
                r = parse_pdf_to_rows(p)
                self.data.append((p, r))
                total += len(r)
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", f"Failed to parse {p}:\n{e}")
        if total == 0:
            QtWidgets.QMessageBox.information(self, "No data", "No numeric rows found. Check the PDF contents.")

    # --- Plotting --------------------------------------------------------------
    def _collect_lines_by_file(self, x_name: str) -> Dict[str, List[Tuple[str, np.ndarray, np.ndarray]]]:
        lines_by_file: Dict[str, List[Tuple[str, np.ndarray, np.ndarray]]] = {}
        selected = [cb.text() for cb in self.y_checks if cb.isChecked()]
        if not selected:
            selected = ["T1+T2"]
        for path, rows in self.data:
            sets: List[Tuple[str, np.ndarray, np.ndarray]] = []
            for y_name in selected:
                xs: List[float] = []
                ys: List[float] = []
                for t1, t2, force, strain in rows:
                    x = force if x_name.startswith("Force") else strain
                    if y_name == "T1":
                        y = t1
                    elif y_name == "T2":
                        y = t2
                    elif y_name == "T2–T1":
                        y = t2 - t1
                    else:
                        y = t1 + t2
                    xs.append(x)
                    ys.append(y)
                if self.zero_cb.isChecked() and ys:
                    base = ys[0]
                    ys = [val - base for val in ys]
                xs_arr = np.asarray(xs, dtype=float)
                ys_arr = np.asarray(ys, dtype=float)
                sets.append((y_name, xs_arr, ys_arr))
            if sets:
                lines_by_file[path] = sets
        return lines_by_file

    def _plot_single(self, x_name: str) -> None:
        if self.read_cb.isChecked():
            apply_readability_fonts()
        set_readability("pdf_plotter", self.read_cb.isChecked())
        lines_by_file = self._collect_lines_by_file(x_name)
        if not lines_by_file:
            QtWidgets.QMessageBox.information(self, "No data", "No valid rows to plot.")
            return
        selected = [cb.text() for cb in self.y_checks if cb.isChecked()]
        if not selected:
            selected = ["T1+T2"]
        y_label = f"{' / '.join(selected)} (arb. u.)"
        x_label = x_name
        mode = self.mode_combo.currentText()
        store_png_dpi("pdf_plotter", int(self.dpi_spin.value()))
        backend = store_backend_choice(
            "pdf_plotter", selected_backend(self.backend_combo)
        )
        if mode == "Combined":
            lines: List[Tuple[str, np.ndarray, np.ndarray]] = []
            for path, sets in lines_by_file.items():
                base = os.path.splitext(os.path.basename(path))[0]
                for y_name, xs, ys in sets:
                    label = f"{base} {y_name}" if len(lines_by_file) > 1 else y_name
                    lines.append((label, xs, ys))
            if len(lines_by_file) == 1:
                base_title = os.path.splitext(os.path.basename(next(iter(lines_by_file))))[0]
                title = f"{y_label} vs {x_label} — {base_title}"
            else:
                title = f"{y_label} vs {x_label} — {len(lines_by_file)} files"
            if wants_matplotlib(backend):
                win = PlotWindow(None, controller=self)
                self._plot_to_window(win, lines, title, x_label, y_label)
                win.show()
                self.plot_wins.append(win)
                self.plot_win = win
                self._last_lines = win._last_lines
                self._last_title = title
                self._last_x_label = x_label
                self._last_y_label = y_label
                if self.save_cb.isChecked():
                    self._save_lines(self._last_lines, self._last_title, x_label, y_label)
            if wants_origin(backend):
                try:
                    self._plot_to_origin(lines, title, x_label, y_label)
                except Exception as e:
                    print(f"Origin plot failed: {e}")
        else:  # Separate
            for path, sets in lines_by_file.items():
                lines: List[Tuple[str, np.ndarray, np.ndarray]] = []
                base = os.path.splitext(os.path.basename(path))[0]
                for y_name, xs, ys in sets:
                    label = f"{base} {y_name}"
                    lines.append((label, xs, ys))
                title = f"{y_label} vs {x_label} — {base}"
                if wants_matplotlib(backend):
                    win = PlotWindow(None, controller=self)
                    self._plot_to_window(win, lines, title, x_label, y_label)
                    win.show()
                    self.plot_wins.append(win)
                    self.plot_win = win
                    self._last_lines = win._last_lines
                    self._last_title = title
                    self._last_x_label = x_label
                    self._last_y_label = y_label
                    if self.save_cb.isChecked():
                        self._save_lines(self._last_lines, self._last_title, x_label, y_label)
                if wants_origin(backend):
                    try:
                        self._plot_to_origin(lines, title, x_label, y_label)
                    except Exception as e:
                        print(f"Origin plot failed: {e}")

    def _plot_to_window(
        self,
        win: PlotWindow,
        lines: Iterable[Tuple[str, np.ndarray, np.ndarray]],
        title: str,
        x_label: str,
        y_label: str,
    ) -> None:
        fig_w, fig_h = self._figure_size_inches()
        win.apply_fixed_plot_size(fig_w, fig_h, resize_window=True)
        win.fig.clf()
        ax = win.fig.add_subplot(111)
        self._draw_on_axes(ax, lines, title, x_label, y_label)
        win.canvas.draw()
        win._last_lines = [(lbl, np.asarray(x, dtype=float), np.asarray(y, dtype=float)) for (lbl, x, y) in lines]
        win._last_title = title

    def _create_matplotlib_fig(
        self,
        lines: Iterable[Tuple[str, np.ndarray, np.ndarray]],
        title: str,
        x_label: str,
        y_label: str,
    ) -> Figure:
        fig_w, fig_h = self._figure_size_inches()
        fig = Figure(figsize=(fig_w, fig_h))
        ax = fig.add_subplot(111)
        self._draw_on_axes(ax, lines, title, x_label, y_label)
        return fig

    def _plot_to_origin(
        self,
        lines: Iterable[Tuple[str, np.ndarray, np.ndarray]],
        title: str,
        x_label: str,
        y_label: str,
    ) -> None:
        import originpro as op  # lazy import
        try:
            op.set_show()
        except Exception:
            pass
        gp = op.new_graph(template='scatter')
        gl = gp[0]
        for idx, (lbl, xs, ys) in enumerate(lines):
            w = op.new_sheet('w', lname=f'data_{idx}')
            w.from_list(0, np.asarray(xs, dtype=float).tolist())
            w.from_list(1, np.asarray(ys, dtype=float).tolist())
            w.cols_axis('XY')
            p = gl.add_plot(w, coly=1, colx=0, type='y')
            try:
                p.symbol_shape = 2
            except Exception:
                pass
            try:
                p.lname = lbl
            except Exception:
                pass
        try:
            gp.activate()
            esc_title = title.replace('"', "'")
            op.lt_exec('page.antialias=1; layer -aa 1;')
            op.lt_exec(f'title -s "{esc_title}";')
            op.lt_exec(f'lab -xb "{x_label}"; lab -yl "{y_label}"; legend;')
        except Exception:
            pass
        try:
            op.exit()
        except Exception:
            pass

    def _save_lines(
        self,
        lines: Iterable[Tuple[str, np.ndarray, np.ndarray]],
        title: str,
        x_label: str,
        y_label: str,
    ) -> None:
        out_dir = self.out_dir.text()
        if not out_dir:
            return
        fmt = self.format_combo.currentText()
        out_dir = prepare_output_dir(out_dir, "pdf_plotter", self.subdir_cb.isChecked())
        set_last_output_dir(self.out_dir.text(), key="pdf_plotter")
        base = title or "plot"
        safe = re.sub(r"[^\w\-\.]+", "_", base)
        base_path = os.path.join(out_dir, safe)
        fig = self._create_matplotlib_fig(lines, title, x_label, y_label)
        save_figure(fig, base_path, fmt, int(self.dpi_spin.value()))

    def save_current(self) -> None:
        if not self._last_lines:
            QtWidgets.QMessageBox.information(self, "No data", "Nothing to save.")
            return
        self._save_lines(self._last_lines, self._last_title, self._last_x_label, self._last_y_label)

    def plot(self) -> None:
        if not self.data:
            QtWidgets.QMessageBox.information(self, "No data", "Load PDF files first.")
            return
        self.clear_plot()
        x_choice = self.x_combo.currentText()
        targets = ["Force (N)", "Strain (mm)"] if x_choice == "Force & Strain" else [x_choice]
        for x_name in targets:
            self._plot_single(x_name)

    def clear_plot(self) -> None:
        for w in self.plot_wins:
            w.close()
        self.plot_wins = []
        self.plot_win = None
        self._last_lines = []
        self._last_title = ""
        self._last_x_label = ""
        self._last_y_label = ""


# -----------------------------------------------------------------------------
def main() -> QtWidgets.QWidget:
    win = PdfPlotterWindow()
    win.show()
    return win


if __name__ == "__main__":  # pragma: no cover - manual execution
    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    _w = main()
    app.exec()
