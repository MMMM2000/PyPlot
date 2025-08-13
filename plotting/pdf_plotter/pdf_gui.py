
from __future__ import annotations

import os
import re
import sys
import weakref
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets

from matplotlib.figure import Figure

try:  # optional dependency
    from PyPDF2 import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore

# Support running both as a package module and as a standalone script
try:
    # When launched via `python -m plotting.pdf_plotter.pdf_gui` or imported from launcher
    from ..utils import apply_system_theme  # type: ignore
except Exception:
    # When launched directly: `python plotting/pdf_plotter/pdf_gui.py`
    from pathlib import Path as _Path
    _root = str(_Path(__file__).resolve().parents[2])  # repo root
    if _root not in sys.path:
        sys.path.append(_root)
    from plotting.utils import apply_system_theme  # type: ignore

NumberRow = Tuple[float, float, float, float]  # T1, T2, Force, Strain


# -----------------------------------------------------------------------------
# Data loading
# -----------------------------------------------------------------------------
def parse_pdf_to_rows(path: str) -> np.ndarray:
    """Extract numeric rows from a PDF.

    Each valid line contains 4 semicolon-separated values: T1; T2; Force; Strain.
    Comma decimal separators are accepted, spaces ignored.  Returns a NumPy
    array with columns ``[T1, T2, Force, Strain]``.
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
    return np.asarray(rows, dtype=float)


# -----------------------------------------------------------------------------
# Small dialog to edit labels for a specific plot window
# -----------------------------------------------------------------------------
class LabelDialog(QtWidgets.QDialog):
    def __init__(self, title: str, xlabel: str, ylabel: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Labels")
        form = QtWidgets.QFormLayout(self)
        self.title_edit = QtWidgets.QLineEdit(title)
        self.x_edit = QtWidgets.QLineEdit(xlabel)
        self.y_edit = QtWidgets.QLineEdit(ylabel)
        form.addRow("Title", self.title_edit)
        form.addRow("X label", self.x_edit)
        form.addRow("Y label", self.y_edit)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def get_values(self) -> Tuple[str, str, str]:
        return self.title_edit.text(), self.x_edit.text(), self.y_edit.text()


# -----------------------------------------------------------------------------
# A single plot window with a pyqtgraph widget
# -----------------------------------------------------------------------------
class PlotWindow(QtWidgets.QWidget):
    """Window containing a pyqtgraph plot widget."""

    instances: "weakref.WeakSet[PlotWindow]" = weakref.WeakSet()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        PlotWindow.instances.add(self)

        self.plot_widget = pg.PlotWidget()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.plot_widget)

        self._last_lines: List[Tuple[str, np.ndarray, np.ndarray]] = []
        self._last_title: str = ""
        self.legend: pg.LegendItem | None = None

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        try:
            PlotWindow.instances.discard(self)
        except Exception:
            pass
        super().closeEvent(event)


# -----------------------------------------------------------------------------
# Window manager to apply current settings to selected plot windows
# -----------------------------------------------------------------------------
class WindowManagerDialog(QtWidgets.QDialog):
    """Select plot windows to apply current settings to."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Window Manager")
        self.resize(420, 320)
        layout = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel(
            "Select plot windows to re-apply CURRENT settings (style, labels, figure size). Data stays the same."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        layout.addWidget(self.list)
        btns = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.apply_btn = QtWidgets.QPushButton("Apply to selected")
        btns.addWidget(self.refresh_btn)
        btns.addWidget(self.apply_btn)
        layout.addLayout(btns)

        self.refresh_btn.clicked.connect(self.refresh)
        self.apply_btn.clicked.connect(self.apply_to_selected)

        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for w in list(PlotWindow.instances):
            title = w._last_title or "(untitled)"
            it = QtWidgets.QListWidgetItem(title)
            it.setData(QtCore.Qt.ItemDataRole.UserRole, w)
            self.list.addItem(it)

    def apply_to_selected(self) -> None:
        parent = self.parent()
        if not isinstance(parent, PdfPlotterWindow):
            self.reject()
            return
        selected_items: List[QtWidgets.QListWidgetItem] = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it is not None and it.isSelected():
                selected_items.append(it)
        if not selected_items:
            QtWidgets.QMessageBox.information(self, "No selection", "Select one or more plot windows.")
            return
        for it in selected_items:
            w = it.data(QtCore.Qt.ItemDataRole.UserRole) if it is not None else None
            if isinstance(w, PlotWindow) and getattr(w, "_last_lines", None):
                parent._plot_to_window(w, w._last_lines, w._last_title)
        self.accept()


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
        self.data: List[Tuple[str, np.ndarray]] = []

        # Track plot windows
        self.plot_win: PlotWindow | None = None
        self.plot_wins: List[PlotWindow] = []
        self.last_plot_window: PlotWindow | None = None

        # Make the settings UI scrollable
        outer = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        content = QtWidgets.QWidget()
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
            cb.stateChanged.connect(self._sync_labels_from_choices)
            cb.stateChanged.connect(self._maybe_auto_plot)
            y_layout.addWidget(cb)
            self.y_checks.append(cb)
        self.x_combo = QtWidgets.QComboBox()
        self.x_combo.addItems(["Force (N)", "Strain (mm)"])
        self.x_combo.currentIndexChanged.connect(self._sync_labels_from_choices)
        self.x_combo.currentIndexChanged.connect(self._maybe_auto_plot)
        form.addRow("Y variables", y_box)
        form.addRow("X variable", self.x_combo)

        # Plot mode and options
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Combined", "Separate"])
        self.mode_combo.currentIndexChanged.connect(self._maybe_auto_plot)
        self.zero_cb = QtWidgets.QCheckBox("First point at zero")
        self.zero_cb.setChecked(True)
        self.zero_cb.stateChanged.connect(self._maybe_auto_plot)
        form.addRow("Plot mode", self.mode_combo)
        form.addRow("Zero first", self.zero_cb)

        # Styling
        self.line_style = QtWidgets.QComboBox()
        self.line_style.addItems(["-", "--", ":", "-.", "None"])
        self.line_style.setCurrentIndex(0)
        self.marker_style = QtWidgets.QComboBox()
        self.marker_style.addItems(["o", "s", "d", "^", "v", "x", "+", ".", "None"])
        self.marker_style.setCurrentIndex(0)
        self.line_width = QtWidgets.QDoubleSpinBox()
        self.line_width.setRange(0.1, 10.0)
        self.line_width.setSingleStep(0.1)
        self.line_width.setValue(1.5)
        self.marker_size = QtWidgets.QDoubleSpinBox()
        self.marker_size.setRange(0.5, 30.0)
        self.marker_size.setSingleStep(0.5)
        self.marker_size.setValue(5.0)
        self.color_btn = QtWidgets.QPushButton()
        self._color = QtGui.QColor("#1f77b4")
        self._update_color_btn()
        self.color_btn.clicked.connect(self._pick_color)
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
        form.addRow("Color", self.color_btn)
        form.addRow("Grid", self.grid_cb)

        # Labels
        self.title_edit = QtWidgets.QLineEdit()
        self.x_label_edit = QtWidgets.QLineEdit("Force (N)")
        self.y_label_edit = QtWidgets.QLineEdit("T1+T2")
        self.y_units_edit = QtWidgets.QLineEdit("arb. units")
        for w in [self.title_edit, self.x_label_edit, self.y_label_edit, self.y_units_edit]:
            w.textChanged.connect(self._maybe_auto_plot)
        form.addRow("Title", self.title_edit)
        form.addRow("X label", self.x_label_edit)
        form.addRow("Y label", self.y_label_edit)
        form.addRow("Y units", self.y_units_edit)

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
        self.legend_fs = QtWidgets.QSpinBox()
        self.legend_fs.setRange(6, 48)
        self.legend_fs.setValue(10)
        self.legend_cb.stateChanged.connect(self._maybe_auto_plot)
        self.legend_loc.currentIndexChanged.connect(self._maybe_auto_plot)
        self.legend_fs.valueChanged.connect(self._maybe_auto_plot)
        form.addRow("Legend", self.legend_cb)
        form.addRow("Legend loc", self.legend_loc)
        form.addRow("Legend size", self.legend_fs)

        # Fonts
        self.title_fs = QtWidgets.QSpinBox()
        self.title_fs.setRange(6, 72)
        self.title_fs.setValue(12)
        self.label_fs = QtWidgets.QSpinBox()
        self.label_fs.setRange(6, 72)
        self.label_fs.setValue(11)
        self.tick_fs = QtWidgets.QSpinBox()
        self.tick_fs.setRange(6, 48)
        self.tick_fs.setValue(10)
        for w in [self.title_fs, self.label_fs, self.tick_fs]:
            w.valueChanged.connect(self._maybe_auto_plot)
        form.addRow("Title size", self.title_fs)
        form.addRow("Label size", self.label_fs)
        form.addRow("Tick size", self.tick_fs)

        # Save options
        self.save_cb = QtWidgets.QCheckBox()
        self.save_cb.setChecked(False)
        self.out_dir = QtWidgets.QLineEdit(os.getcwd())
        self.browse_out_btn = QtWidgets.QPushButton("Browse…")
        self.browse_out_btn.clicked.connect(self._browse_out)
        out_box = self._hbox(self.out_dir, self.browse_out_btn)
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(["png", "pdf"])
        self.dpi_spin = QtWidgets.QSpinBox()
        self.dpi_spin.setRange(72, 600)
        self.dpi_spin.setValue(300)
        self.fig_w = QtWidgets.QDoubleSpinBox()
        self.fig_w.setRange(1.0, 1000.0)
        self.fig_w.setValue(180.0)  # default in mm
        self.fig_h = QtWidgets.QDoubleSpinBox()
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
        self.export_btn = QtWidgets.QPushButton("Export Matplotlib")
        self.export_btn.clicked.connect(self.export_current)
        form.addRow("Save on plot", self.save_cb)
        form.addRow("Output dir", out_box)
        form.addRow("Format", self.format_combo)
        form.addRow("DPI", self.dpi_spin)
        form.addRow("Figure size", self._hbox(self.fig_w, self.fig_h, self.fig_units, self.lock_aspect_cb))
        form.addRow("", self.export_btn)

        # Actions
        self.auto_cb = QtWidgets.QCheckBox("Auto update on change")
        self.auto_cb.setChecked(True)
        self.plot_btn = QtWidgets.QPushButton("Plot")
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.manager_btn = QtWidgets.QPushButton("Window Manager…")
        self.plot_btn.clicked.connect(self.plot)
        self.clear_btn.clicked.connect(self.clear_plot)
        self.manager_btn.clicked.connect(self.open_manager)
        btn_box = self._hbox(self.auto_cb, self.plot_btn, self.clear_btn, self.manager_btn)

        self._sync_labels_from_choices()
        # Ensure the plot controls are always visible without scrolling by placing the
        # button row outside the scrollable area.
        outer.addWidget(btn_box)

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

    def _update_color_btn(self) -> None:
        pix = QtGui.QPixmap(24, 16)
        pix.fill(self._color)
        self.color_btn.setIcon(QtGui.QIcon(pix))
        self.color_btn.setText(self._color.name())

    def _pick_color(self) -> None:
        c = QtWidgets.QColorDialog.getColor(self._color, self, "Pick color")
        if c and c.isValid():
            self._color = c
            self._update_color_btn()
            self._maybe_auto_plot()

    def _sync_labels_from_choices(self) -> None:
        x_name = self.x_combo.currentText()
        sel = [cb.text() for cb in self.y_checks if cb.isChecked()]
        if not sel:
            sel = ["T1+T2"]
            self.y_checks[0].setChecked(True)
        self.x_label_edit.setText(x_name)
        self.y_label_edit.setText(" / ".join(sel))

    def _maybe_auto_plot(self) -> None:
        if self.auto_cb.isChecked():
            self.plot()

    def _load_files(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Select PDF files", "", "PDF files (*.pdf)")
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
    def _collect_lines_by_file(self) -> Dict[str, List[Tuple[str, np.ndarray, np.ndarray]]]:
        lines_by_file: Dict[str, List[Tuple[str, np.ndarray, np.ndarray]]] = {}
        x_name = self.x_combo.currentText()
        selected = [cb.text() for cb in self.y_checks if cb.isChecked()]
        if not selected:
            selected = ["T1+T2"]
        for path, rows in self.data:
            sets: List[Tuple[str, np.ndarray, np.ndarray]] = []
            x_vals = rows[:, 2] if x_name.startswith("Force") else rows[:, 3]
            for y_name in selected:
                if y_name == "T1":
                    y_vals = rows[:, 0]
                elif y_name == "T2":
                    y_vals = rows[:, 1]
                elif y_name == "T2–T1":
                    y_vals = rows[:, 1] - rows[:, 0]
                else:
                    y_vals = rows[:, 0] + rows[:, 1]
                if self.zero_cb.isChecked() and y_vals.size:
                    y_vals = y_vals - y_vals[0]
                sets.append((y_name, x_vals, y_vals))
            if sets:
                lines_by_file[path] = sets
        return lines_by_file

    def _plot_to_window(self, win: PlotWindow, lines: Iterable[Tuple[str, np.ndarray, np.ndarray]], title: str) -> None:
        pw = win.plot_widget
        pw.clear()
        if win.legend is not None:
            pw.removeItem(win.legend)
            win.legend = None

        style_map = {
            "-": QtCore.Qt.PenStyle.SolidLine,
            "--": QtCore.Qt.PenStyle.DashLine,
            ":": QtCore.Qt.PenStyle.DotLine,
            "-.": QtCore.Qt.PenStyle.DashDotLine,
        }
        ls = self.line_style.currentText()
        marker = self.marker_style.currentText()
        for i, (label, x, y) in enumerate(lines):
            color = self._color.name() if i == 0 else None
            pen = None if ls == "None" else pg.mkPen(color=color, width=float(self.line_width.value()), style=style_map.get(ls, QtCore.Qt.PenStyle.SolidLine))
            symbol = None if marker == "None" else ("o" if marker == "." else marker)
            pw.plot(x, y, pen=pen, symbol=symbol, symbolSize=float(self.marker_size.value()), name=label)

        x_lab = self.x_label_edit.text()
        units = self.y_units_edit.text().strip()
        y_lab = self.y_label_edit.text().strip()
        if units:
            y_lab = f"{y_lab} ({units})"
        pw.setLabel("bottom", x_lab)
        pw.setLabel("left", y_lab)
        pw.setTitle(title)
        pw.showGrid(self.grid_cb.isChecked(), self.grid_cb.isChecked(), alpha=0.4)
        if self.legend_cb.isChecked():
            win.legend = pw.addLegend()
        font = QtGui.QFont()
        font.setPointSize(int(self.tick_fs.value()))
        pw.getAxis("bottom").setTickFont(font)
        pw.getAxis("left").setTickFont(font)

        win._last_lines = list(lines)
        win._last_title = title

    def _export_window(self, win: PlotWindow) -> None:
        out_dir = self.out_dir.text()
        if not out_dir:
            return
        os.makedirs(out_dir, exist_ok=True)
        ext = self.format_combo.currentText().lower()
        base = win._last_title or "plot"
        safe = re.sub(r"[^\w\-\.]+", "_", base)
        path = os.path.join(out_dir, f"{safe}.{ext}")

        fig_w, fig_h = self._figure_size_inches()
        fig = Figure(figsize=(fig_w, fig_h))
        ax = fig.add_subplot(111)

        ls = 'None' if self.line_style.currentText() == "None" else self.line_style.currentText()
        marker = None if self.marker_style.currentText() == "None" else self.marker_style.currentText()
        for i, (label, x, y) in enumerate(win._last_lines):
            color = self._color.name() if i == 0 else None
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

        x_lab = self.x_label_edit.text()
        units = self.y_units_edit.text().strip()
        y_lab = self.y_label_edit.text().strip()
        if units:
            y_lab = f"{y_lab} ({units})"
        ax.set_xlabel(x_lab, fontsize=int(self.label_fs.value()))
        ax.set_ylabel(y_lab, fontsize=int(self.label_fs.value()))
        ax.grid(self.grid_cb.isChecked(), which="both", linestyle="--", alpha=0.4)
        ax.set_title(win._last_title, fontsize=int(self.title_fs.value()))
        if self.legend_cb.isChecked():
            ax.legend(loc=self.legend_loc.currentText(), fontsize=int(self.legend_fs.value()))
        ax.tick_params(labelsize=int(self.tick_fs.value()))
        fig.tight_layout()
        fig.savefig(path, dpi=int(self.dpi_spin.value()))

    def export_current(self) -> None:
        if self.last_plot_window is None:
            QtWidgets.QMessageBox.information(self, "No data", "Nothing to export.")
            return
        self._export_window(self.last_plot_window)

    def plot(self) -> None:
        if not self.data:
            QtWidgets.QMessageBox.information(self, "No data", "Load PDF files first.")
            return

        lines_by_file = self._collect_lines_by_file()
        if not lines_by_file:
            QtWidgets.QMessageBox.information(self, "No data", "No valid rows to plot.")
            return

        selected = [cb.text() for cb in self.y_checks if cb.isChecked()]
        x_name = self.x_combo.currentText()

        mode = self.mode_combo.currentText()
        if mode == "Combined":
            if self.plot_win is None:
                self.plot_win = PlotWindow(None)
            lines: List[Tuple[str, np.ndarray, np.ndarray]] = []
            for path, sets in lines_by_file.items():
                base = os.path.basename(path)
                for y_name, xs, ys in sets:
                    label = f"{base} {y_name}" if len(lines_by_file) > 1 else y_name
                    lines.append((label, xs, ys))
            title = self.title_edit.text().strip()
            if not title:
                base = os.path.basename(next(iter(lines_by_file))) if len(lines_by_file) == 1 else f"{len(lines_by_file)} files"
                title = f"{' / '.join(selected)} vs {x_name} — {base}"
            self._plot_to_window(self.plot_win, lines, title)
            self.plot_win.show()
            self.last_plot_window = self.plot_win
            if self.save_cb.isChecked():
                self._export_window(self.plot_win)
        else:  # Separate
            for w in self.plot_wins:
                w.close()
            self.plot_wins = []
            for path, sets in lines_by_file.items():
                win = PlotWindow(None)
                lines: List[Tuple[str, np.ndarray, np.ndarray]] = []
                base = os.path.basename(path)
                for y_name, xs, ys in sets:
                    label = f"{base} {y_name}"
                    lines.append((label, xs, ys))
                title = self.title_edit.text().strip() or f"{' / '.join(selected)} vs {x_name} — {base}"
                self._plot_to_window(win, lines, title)
                win.show()
                self.plot_wins.append(win)
                self.last_plot_window = win
                if self.save_cb.isChecked():
                    self._export_window(win)

    def clear_plot(self) -> None:
        if self.plot_win:
            self.plot_win.plot_widget.clear()
        for w in self.plot_wins:
            w.plot_widget.clear()

    def open_manager(self) -> None:
        dlg = WindowManagerDialog(self)
        dlg.exec()


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