from __future__ import annotations

import os
import re
import sys
from typing import List, Tuple

from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

try:
    from PyPDF2 import PdfReader
except Exception:  # pragma: no cover - optional dep at runtime
    PdfReader = None  # type: ignore

from ..utils import apply_system_theme

NumberRow = Tuple[float, float, float, float]  # T1, T2, Force, Strain


def parse_pdf_to_rows(path: str) -> List[NumberRow]:
    """Extract numeric rows from a PDF.

    Each valid line contains 4 semicolon-separated values: T1; T2; Force; Strain.
    Comma decimal separators are accepted.
    """
    if PdfReader is None:
        raise RuntimeError("Missing dependency PyPDF2. Install it and retry.")

    rows: List[NumberRow] = []
    reader = PdfReader(path)
    num = r"-?\d+(?:[.,]\d+)?"
    line_pattern = re.compile(rf"\s*({num})\s*;\s*({num})\s*;\s*({num})\s*;\s*({num})\s*")

    for page in reader.pages:
        text = page.extract_text() or ""
        for raw_line in text.splitlines():
            m = line_pattern.fullmatch(raw_line.strip())
            if not m:
                candidate = re.sub(r"[^\d;,\.\-\s]", "", raw_line).strip()
                m = line_pattern.fullmatch(candidate)
            if m:
                try:
                    t1 = float(m.group(1).replace(",", "."))
                    t2 = float(m.group(2).replace(",", "."))
                    force = float(m.group(3).replace(",", "."))
                    strain = float(m.group(4).replace(",", "."))
                    rows.append((t1, t2, force, strain))
                except ValueError:
                    continue
    return rows


class PlotWindow(QtWidgets.QWidget):
    """Simple window that hosts a matplotlib canvas."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plot")
        layout = QtWidgets.QVBoxLayout(self)
        fig = Figure(figsize=(8, 5), constrained_layout=True)
        self.canvas = FigureCanvas(fig)
        self.ax = fig.add_subplot(111)
        self.toolbar = NavigationToolbar(self.canvas, self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)


class PdfPlotterWindow(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF T1/T2 Plotter")
        self.resize(1100, 700)

        self.file_rows: list[tuple[str, list[NumberRow]]] = []
        self.plot_windows: list[PlotWindow] = []

        # Root layout
        root = QtWidgets.QVBoxLayout(self)

        # Controls panel (scrollable)
        ctrl_scroll = QtWidgets.QScrollArea()
        ctrl_scroll.setWidgetResizable(True)
        ctrl = QtWidgets.QWidget()
        ctrl_scroll.setWidget(ctrl)
        form = QtWidgets.QFormLayout(ctrl)

        # File selection
        file_box = QtWidgets.QWidget()
        hb = QtWidgets.QHBoxLayout(file_box)
        self.load_btn = QtWidgets.QPushButton("Load PDFs…")
        self.load_btn.clicked.connect(self.select_files)
        self.status_lbl = QtWidgets.QLabel("No files loaded.")
        self.status_lbl.setWordWrap(True)
        hb.addWidget(self.load_btn)
        hb.addWidget(self.status_lbl, 1)
        form.addRow("Files", file_box)

        # Variables
        self.t1_cb = QtWidgets.QCheckBox("T1")
        self.t2_cb = QtWidgets.QCheckBox("T2")
        self.dt_cb = QtWidgets.QCheckBox("T2–T1")
        self.sum_cb = QtWidgets.QCheckBox("T1+T2"); self.sum_cb.setChecked(True)
        var_box = QtWidgets.QWidget()
        vbl = QtWidgets.QVBoxLayout(var_box)
        for w in (self.sum_cb, self.t1_cb, self.t2_cb, self.dt_cb):
            vbl.addWidget(w)
        self.x_combo = QtWidgets.QComboBox(); self.x_combo.addItems(["Force (N)", "Strain (mm)"])
        form.addRow("Variables", var_box)
        form.addRow("X variable", self.x_combo)

        # Options
        self.zero_cb = QtWidgets.QCheckBox(); self.zero_cb.setChecked(True)
        self.separate_cb = QtWidgets.QCheckBox()
        form.addRow("Zero first point", self.zero_cb)
        form.addRow("Separate plots per file", self.separate_cb)

        # Styling
        self.line_style = QtWidgets.QComboBox(); self.line_style.addItems(["-", "--", ":", "-.", "None"]) ; self.line_style.setCurrentIndex(0)
        self.marker_style = QtWidgets.QComboBox(); self.marker_style.addItems(["o", "s", "^", "x", "+", ".", "None"]) ; self.marker_style.setCurrentIndex(0)
        self.line_width = QtWidgets.QDoubleSpinBox(); self.line_width.setRange(0.0, 20.0); self.line_width.setSingleStep(0.1); self.line_width.setValue(1.5)
        self.marker_size = QtWidgets.QDoubleSpinBox(); self.marker_size.setRange(0.0, 50.0); self.marker_size.setSingleStep(0.5); self.marker_size.setValue(5.0)
        self.color_btn = QtWidgets.QPushButton(); self._color = QtGui.QColor("#1f77b4") ; self._update_color_btn()
        self.color_btn.clicked.connect(self._pick_color)
        grid_cb = QtWidgets.QCheckBox(); grid_cb.setChecked(True)
        self.grid_cb = grid_cb
        form.addRow("Line style", self.line_style)
        form.addRow("Marker", self.marker_style)
        form.addRow("Line width", self.line_width)
        form.addRow("Marker size", self.marker_size)
        form.addRow("Color", self.color_btn)
        form.addRow("Grid", self.grid_cb)

        # Labels and title
        self.title_edit = QtWidgets.QLineEdit()
        self.x_label_edit = QtWidgets.QLineEdit("Force (N)")
        self.y_label_edit = QtWidgets.QLineEdit("T1+T2")
        form.addRow("Title", self.title_edit)
        form.addRow("X label", self.x_label_edit)
        form.addRow("Y label", self.y_label_edit)

        # Legend
        self.legend_cb = QtWidgets.QCheckBox(); self.legend_cb.setChecked(True)
        self.legend_loc = QtWidgets.QComboBox(); self.legend_loc.addItems(["best","upper right","upper left","lower left","lower right","right","center left","center right","lower center","upper center","center"]) ; self.legend_loc.setCurrentIndex(0)
        self.legend_fs = QtWidgets.QSpinBox(); self.legend_fs.setRange(6, 48); self.legend_fs.setValue(10)
        form.addRow("Legend", self.legend_cb)
        form.addRow("Legend loc", self.legend_loc)
        form.addRow("Legend size", self.legend_fs)

        # Fonts
        self.title_fs = QtWidgets.QSpinBox(); self.title_fs.setRange(6, 72); self.title_fs.setValue(12)
        self.label_fs = QtWidgets.QSpinBox(); self.label_fs.setRange(6, 72); self.label_fs.setValue(11)
        self.tick_fs = QtWidgets.QSpinBox(); self.tick_fs.setRange(6, 48); self.tick_fs.setValue(10)
        form.addRow("Title size", self.title_fs)
        form.addRow("Label size", self.label_fs)
        form.addRow("Tick size", self.tick_fs)

        # Save options
        self.save_cb = QtWidgets.QCheckBox(); self.save_cb.setChecked(False)
        self.out_dir = QtWidgets.QLineEdit(os.getcwd())
        self.browse_btn = QtWidgets.QPushButton("Browse…"); self.browse_btn.clicked.connect(self._browse_out)
        out_box = QtWidgets.QWidget(); out_layout = QtWidgets.QHBoxLayout(out_box); out_layout.addWidget(self.out_dir); out_layout.addWidget(self.browse_btn)
        self.format_combo = QtWidgets.QComboBox(); self.format_combo.addItems(["png","pdf"])
        self.dpi_spin = QtWidgets.QSpinBox(); self.dpi_spin.setRange(72, 600); self.dpi_spin.setValue(300)
        self.fig_w = QtWidgets.QDoubleSpinBox(); self.fig_w.setRange(1.0, 30.0); self.fig_w.setValue(8.0)
        self.fig_h = QtWidgets.QDoubleSpinBox(); self.fig_h.setRange(1.0, 30.0); self.fig_h.setValue(5.0)
        self.save_now_btn = QtWidgets.QPushButton("Save Now"); self.save_now_btn.clicked.connect(self.save_current)
        form.addRow("Save on plot", self.save_cb)
        form.addRow("Output dir", out_box)
        form.addRow("Format", self.format_combo)
        form.addRow("DPI", self.dpi_spin)
        form.addRow("Figure size (in)", self._hbox(self.fig_w, self.fig_h))
        form.addRow("", self.save_now_btn)

        # Actions
        self.auto_cb = QtWidgets.QCheckBox(); self.auto_cb.setChecked(True)
        self.plot_btn = QtWidgets.QPushButton("Plot")
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.plot_btn.clicked.connect(self.plot)
        self.clear_btn.clicked.connect(self.clear_plot)
        btn_box = self._hbox(self.auto_cb, self.plot_btn, self.clear_btn)
        self.auto_cb.setText("Auto update on change")
        form.addRow("", btn_box)

        # Assemble layout
        root.addWidget(ctrl_scroll, 1)

        # Wire updates
        # Connect change signals to trigger auto-plot
        for w in [self.x_combo, self.line_style, self.marker_style, self.legend_loc]:
            w.currentIndexChanged.connect(self._maybe_auto_plot)
        for w in [self.line_width, self.marker_size, self.legend_fs, self.title_fs, self.label_fs, self.tick_fs]:
            w.valueChanged.connect(self._maybe_auto_plot)
        for w in [self.grid_cb, self.legend_cb, self.zero_cb, self.separate_cb,
                  self.sum_cb, self.t1_cb, self.t2_cb, self.dt_cb]:
            w.stateChanged.connect(self._maybe_auto_plot)
        for w in [self.title_edit, self.x_label_edit, self.y_label_edit]:
            w.textChanged.connect(self._maybe_auto_plot)

        self.x_combo.currentTextChanged.connect(self._sync_labels_from_choices)
        for w in [self.sum_cb, self.t1_cb, self.t2_cb, self.dt_cb]:
            w.stateChanged.connect(self._sync_labels_from_choices)
        self._sync_labels_from_choices()

    def _hbox(self, *widgets: QtWidgets.QWidget) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QHBoxLayout(w)
        for x in widgets:
            l.addWidget(x)
        return w

    def _browse_out(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output directory", self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    def _update_color_btn(self) -> None:
        pix = QtGui.QPixmap(24, 16); pix.fill(self._color)
        self.color_btn.setIcon(QtGui.QIcon(pix))
        self.color_btn.setText(self._color.name())

    def _pick_color(self) -> None:
        col = QtWidgets.QColorDialog.getColor(self._color, self, "Select color")
        if col.isValid():
            self._color = col
            self._update_color_btn()
            self._maybe_auto_plot()

    def _selected_vars(self) -> list[str]:
        vars: list[str] = []
        if self.sum_cb.isChecked():
            vars.append(self.sum_cb.text())
        if self.t1_cb.isChecked():
            vars.append(self.t1_cb.text())
        if self.t2_cb.isChecked():
            vars.append(self.t2_cb.text())
        if self.dt_cb.isChecked():
            vars.append(self.dt_cb.text())
        return vars

    def _sync_labels_from_choices(self) -> None:
        # Keep text fields in sync with choices unless user edited
        if not self.x_label_edit.isModified():
            self.x_label_edit.setText(self.x_combo.currentText())
        if not self.y_label_edit.isModified():
            sel = self._selected_vars()
            if len(sel) == 1:
                self.y_label_edit.setText(sel[0])

    def select_files(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select PDF files",
            "",
            "PDF files (*.pdf);;All files (*)",
        )
        if not paths:
            return
        self.file_rows.clear()
        total = 0
        for p in paths:
            try:
                r = parse_pdf_to_rows(p)
                self.file_rows.append((p, r))
                total += len(r)
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", f"Failed to parse {os.path.basename(p)}:\n{e}")
        if total == 0:
            self.status_lbl.setText("No numeric rows found. Check the PDFs.")
        else:
            base = os.path.basename(self.file_rows[-1][0]) if len(self.file_rows) == 1 else f"{len(self.file_rows)} files"
            self.status_lbl.setText(f"Loaded {base} — {total} rows")
        self._maybe_auto_plot()

    def _maybe_auto_plot(self) -> None:
        if self.auto_cb.isChecked():
            self.plot()

    def compute_xy(self, rows: list[NumberRow], var: str, x_name: str) -> tuple[list[float], list[float]]:
        xs: list[float] = []
        ys: list[float] = []
        for t1, t2, force, strain in rows:
            if var == "T1":
                y = t1
            elif var == "T2":
                y = t2
            elif var in ("T2–T1", "T2-T1"):
                y = t2 - t1
            elif var == "T1+T2":
                y = t1 + t2
            else:
                continue
            x = force if x_name.startswith("Force") else strain
            xs.append(x)
            ys.append(y)
        return xs, ys

    def plot(self) -> None:
        if not self.file_rows:
            return
        vars = self._selected_vars()
        if not vars:
            return

        x_name = self.x_combo.currentText()
        separate = self.separate_cb.isChecked()
        zero_first = self.zero_cb.isChecked()

        # Close existing windows
        self.clear_plot()

        ls = None if self.line_style.currentText() == "None" else self.line_style.currentText()
        marker = None if self.marker_style.currentText() == "None" else self.marker_style.currentText()

        if separate and len(self.file_rows) > 1:
            for path, rows in self.file_rows:
                win = PlotWindow(self)
                ax = win.ax
                total_lines = len(vars)
                color = self._color.name() if total_lines == 1 else None
                for var in vars:
                    x, y = self.compute_xy(rows, var, x_name)
                    if not x:
                        continue
                    if zero_first and y:
                        offset = y[0]
                        y = [yy - offset for yy in y]
                    ax.plot(
                        x,
                        y,
                        linestyle=ls,
                        marker=marker,
                        linewidth=float(self.line_width.value()),
                        markersize=float(self.marker_size.value()),
                        color=color,
                        label=var,
                    )
                ax.set_xlabel(self.x_label_edit.text(), fontsize=int(self.label_fs.value()))
                ax.set_ylabel(self.y_label_edit.text(), fontsize=int(self.label_fs.value()))
                title = self.title_edit.text().strip()
                base = os.path.basename(path)
                if not title:
                    vars_str = ", ".join(vars)
                    title = f"{vars_str} vs {self.x_combo.currentText()} — {base}"
                ax.set_title(title, fontsize=int(self.title_fs.value()))
                ax.tick_params(labelsize=int(self.tick_fs.value()))
                ax.grid(self.grid_cb.isChecked(), which="both", linestyle="--", alpha=0.4)
                if self.legend_cb.isChecked():
                    ax.legend(loc=self.legend_loc.currentText(), fontsize=int(self.legend_fs.value()))
                win.canvas.figure.set_size_inches(float(self.fig_w.value()), float(self.fig_h.value()))
                win.canvas.draw()
                win.show()
                self.plot_windows.append(win)
        else:
            win = PlotWindow(self)
            ax = win.ax
            total_lines = len(vars) * len(self.file_rows)
            color = self._color.name() if total_lines == 1 else None
            for path, rows in self.file_rows:
                for var in vars:
                    x, y = self.compute_xy(rows, var, x_name)
                    if not x:
                        continue
                    if zero_first and y:
                        offset = y[0]
                        y = [yy - offset for yy in y]
                    label = var if len(self.file_rows) == 1 else f"{os.path.basename(path)} {var}"
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
            ax.set_xlabel(self.x_label_edit.text(), fontsize=int(self.label_fs.value()))
            ax.set_ylabel(self.y_label_edit.text(), fontsize=int(self.label_fs.value()))
            title = self.title_edit.text().strip()
            base = os.path.basename(self.file_rows[-1][0]) if len(self.file_rows) == 1 else f"{len(self.file_rows)} files"
            if not title:
                vars_str = ", ".join(vars)
                title = f"{vars_str} vs {self.x_combo.currentText()} — {base}"
            ax.set_title(title, fontsize=int(self.title_fs.value()))
            ax.tick_params(labelsize=int(self.tick_fs.value()))
            ax.grid(self.grid_cb.isChecked(), which="both", linestyle="--", alpha=0.4)
            if self.legend_cb.isChecked():
                ax.legend(loc=self.legend_loc.currentText(), fontsize=int(self.legend_fs.value()))
            win.canvas.figure.set_size_inches(float(self.fig_w.value()), float(self.fig_h.value()))
            win.canvas.draw()
            win.show()
            self.plot_windows.append(win)

        if self.save_cb.isChecked():
            self.save_current()

    def clear_plot(self) -> None:
        for w in self.plot_windows:
            w.close()
        self.plot_windows.clear()

    def save_current(self) -> None:
        if not self.plot_windows:
            QtWidgets.QMessageBox.information(self, "No data", "Nothing to save.")
            return
        out_dir = self.out_dir.text().strip() or os.getcwd()
        os.makedirs(out_dir, exist_ok=True)
        ext = self.format_combo.currentText().lower()
        base = self.title_edit.text().strip()
        if not base:
            base = f"{'_'.join(self._selected_vars())}_vs_{self.x_combo.currentText()}"
        safe = re.sub(r"[^\w\-\.]+", "_", base)
        path = os.path.join(out_dir, f"{safe}.{ext}")
        fig = self.plot_windows[0].canvas.figure
        fig.savefig(path, dpi=int(self.dpi_spin.value()))


def main() -> QtWidgets.QWidget:
    win = PdfPlotterWindow()
    win.show()
    return win


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    _w = main()
    app.exec()
