from __future__ import annotations

import os
import re
import sys
from typing import Dict, Iterable, List, Tuple

from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

try:  # pragma: no cover - optional dependency
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
    line_pattern = re.compile(
        rf"\s*({num})\s*;\s*({num})\s*;\s*({num})\s*;\s*({num})\s*"
    )

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
    """Window containing a matplotlib canvas and toolbar."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        fig = Figure(figsize=(8, 5), constrained_layout=True)
        self.canvas = FigureCanvas(fig)
        # Keep the canvas at a fixed size so the aspect ratio is always
        # respected. Users can adjust the figure size through the settings
        # dialog, but the resulting plot windows themselves cannot be resized
        # to distort the canvas.
        self.canvas.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        self.ax = fig.add_subplot(111)
        self.toolbar = NavigationToolbar(self.canvas, self)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 0, QtCore.Qt.AlignmentFlag.AlignCenter)

        # Allow editing labels per window via a context menu on the canvas.
        self.canvas.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.canvas.customContextMenuRequested.connect(self._show_context_menu)

    # ------------------------------------------------------------------
    def _show_context_menu(self, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        edit_act = menu.addAction("Edit labels…")
        action = menu.exec(self.canvas.mapToGlobal(pos))
        if action == edit_act:
            self._edit_labels()

    def _edit_labels(self) -> None:
        dlg = _LabelDialog(
            self.ax.get_title(), self.ax.get_xlabel(), self.ax.get_ylabel(), self
        )
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            title, xlab, ylab = dlg.values()
            self.ax.set_title(title)
            self.ax.set_xlabel(xlab)
            self.ax.set_ylabel(ylab)
            self.canvas.draw()


class _LabelDialog(QtWidgets.QDialog):
    """Small dialog to edit plot title and axis labels."""

    def __init__(
        self, title: str, xlab: str, ylab: str, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit labels")
        form = QtWidgets.QFormLayout(self)
        self._title = QtWidgets.QLineEdit(title)
        self._xlab = QtWidgets.QLineEdit(xlab)
        self._ylab = QtWidgets.QLineEdit(ylab)
        form.addRow("Title", self._title)
        form.addRow("X label", self._xlab)
        form.addRow("Y label", self._ylab)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    # ------------------------------------------------------------------
    def values(self) -> Tuple[str, str, str]:
        return self._title.text(), self._xlab.text(), self._ylab.text()


class PdfPlotterWindow(QtWidgets.QWidget):
    """Settings window for plotting data extracted from PDFs."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF T1/T2 Plotter")
        self.resize(520, 700)

        # Loaded data: list of (path, rows)
        self.data: List[Tuple[str, List[NumberRow]]] = []

        # Track plot windows
        self.plot_win: PlotWindow | None = None
        self.plot_wins: List[PlotWindow] = []
        self.last_plot_window: PlotWindow | None = None

        root = QtWidgets.QVBoxLayout(self)

        ctrl_scroll = QtWidgets.QScrollArea()
        ctrl_scroll.setWidgetResizable(True)
        ctrl = QtWidgets.QWidget()
        ctrl_scroll.setWidget(ctrl)
        form = QtWidgets.QFormLayout(ctrl)

        root.addWidget(ctrl_scroll)

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

        # Variable selection
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
        self.marker_style.addItems(["o", "s", "^", "x", "+", ".", "None"])
        self.marker_style.setCurrentIndex(0)
        self.line_width = QtWidgets.QDoubleSpinBox()
        self.line_width.setRange(0.0, 20.0)
        self.line_width.setSingleStep(0.1)
        self.line_width.setValue(1.5)
        self.marker_size = QtWidgets.QDoubleSpinBox()
        self.marker_size.setRange(0.0, 50.0)
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
        for w in [self.line_width, self.marker_size]:
            w.valueChanged.connect(self._maybe_auto_plot)
        self.grid_cb.stateChanged.connect(self._maybe_auto_plot)
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
        self.y_unit_edit = QtWidgets.QLineEdit("arb. units")
        for w in [self.title_edit, self.x_label_edit, self.y_label_edit]:
            w.textChanged.connect(self._maybe_auto_plot)
        self.y_unit_edit.textChanged.connect(self._sync_labels_from_choices)
        self.y_unit_edit.textChanged.connect(self._maybe_auto_plot)
        form.addRow("Title", self.title_edit)
        form.addRow("X label", self.x_label_edit)
        form.addRow("Y label", self.y_label_edit)
        form.addRow("Y units", self.y_unit_edit)

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
        self.browse_btn = QtWidgets.QPushButton("Browse…")
        self.browse_btn.clicked.connect(self._browse_out)
        out_box = QtWidgets.QWidget()
        out_layout = QtWidgets.QHBoxLayout(out_box)
        out_layout.addWidget(self.out_dir)
        out_layout.addWidget(self.browse_btn)
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(["png", "pdf"])
        self.dpi_spin = QtWidgets.QSpinBox()
        self.dpi_spin.setRange(72, 600)
        self.dpi_spin.setValue(300)
        self.fig_w = QtWidgets.QDoubleSpinBox()
        self.fig_w.setRange(1.0, 30.0)
        self.fig_w.setValue(8.0)
        self.fig_h = QtWidgets.QDoubleSpinBox()
        self.fig_h.setRange(1.0, 30.0)
        self.fig_h.setValue(5.0)
        self.save_now_btn = QtWidgets.QPushButton("Save Now")
        self.save_now_btn.clicked.connect(self.save_current)
        form.addRow("Save on plot", self.save_cb)
        form.addRow("Output dir", out_box)
        form.addRow("Format", self.format_combo)
        form.addRow("DPI", self.dpi_spin)
        form.addRow("Figure size (in)", self._hbox(self.fig_w, self.fig_h))
        form.addRow("", self.save_now_btn)

        # Actions
        self.auto_cb = QtWidgets.QCheckBox()
        self.auto_cb.setChecked(True)
        self.auto_cb.setText("Auto update on change")
        self.plot_btn = QtWidgets.QPushButton("Plot")
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.plot_btn.clicked.connect(self.plot)
        self.clear_btn.clicked.connect(self.clear_plot)
        btn_box = self._hbox(self.auto_cb, self.plot_btn, self.clear_btn)
        form.addRow("", btn_box)

        self._sync_labels_from_choices()

    # ------------------------------------------------------------------
    def _hbox(self, *widgets: QtWidgets.QWidget) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        l = QtWidgets.QHBoxLayout(w)
        for x in widgets:
            l.addWidget(x)
        return w

    def _browse_out(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select output directory", self.out_dir.text()
        )
        if d:
            self.out_dir.setText(d)

    def _update_color_btn(self) -> None:
        pix = QtGui.QPixmap(24, 16)
        pix.fill(self._color)
        self.color_btn.setIcon(QtGui.QIcon(pix))
        self.color_btn.setText(self._color.name())

    def _pick_color(self) -> None:
        col = QtWidgets.QColorDialog.getColor(self._color, self, "Select color")
        if col.isValid():
            self._color = col
            self._update_color_btn()
            self._maybe_auto_plot()

    def _sync_labels_from_choices(self) -> None:
        if not self.x_label_edit.isModified():
            self.x_label_edit.setText(self.x_combo.currentText())
        if not self.y_label_edit.isModified():
            selected = [cb.text() for cb in self.y_checks if cb.isChecked()]
            label = " / ".join(selected)
            units = self.y_unit_edit.text().strip()
            if units:
                label = f"{label} ({units})"
            self.y_label_edit.setText(label)

    # ------------------------------------------------------------------
    def select_files(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select PDF files",
            "",
            "PDF files (*.pdf);;All files (*)",
        )
        if not paths:
            return
        self.data.clear()
        total = 0
        for p in paths:
            try:
                r = parse_pdf_to_rows(p)
                self.data.append((p, r))
                total += len(r)
            except Exception as e:
                QtWidgets.QMessageBox.critical(
                    self, "Error", f"Failed to parse {os.path.basename(p)}:\n{e}"
                )
        if total == 0:
            self.status_lbl.setText("No numeric rows found. Check the PDFs.")
        else:
            base = (
                os.path.basename(paths[-1])
                if len(paths) == 1
                else f"{len(paths)} files"
            )
            self.status_lbl.setText(f"Loaded {base} — {total} rows")
        self._maybe_auto_plot()

    def _maybe_auto_plot(self) -> None:
        if self.auto_cb.isChecked():
            self.plot()

    # ------------------------------------------------------------------
    def compute_lines(self) -> Dict[str, List[Tuple[str, List[float], List[float]]]]:
        lines_by_file: Dict[str, List[Tuple[str, List[float], List[float]]]] = {}
        selected = [cb.text() for cb in self.y_checks if cb.isChecked()]
        x_name = self.x_combo.currentText()
        for path, rows in self.data:
            sets: List[Tuple[str, List[float], List[float]]] = []
            for y_name in selected:
                xs: List[float] = []
                ys: List[float] = []
                for t1, t2, force, strain in rows:
                    if y_name == "T1":
                        y = t1
                    elif y_name == "T2":
                        y = t2
                    elif y_name in ("T2–T1", "T2-T1"):
                        y = t2 - t1
                    elif y_name == "T1+T2":
                        y = t1 + t2
                    else:
                        continue
                    x = force if x_name.startswith("Force") else strain
                    xs.append(x)
                    ys.append(y)
                if self.zero_cb.isChecked() and ys:
                    base = ys[0]
                    ys = [y - base for y in ys]
                sets.append((y_name, xs, ys))
            if sets:
                lines_by_file[path] = sets
        return lines_by_file

    # ------------------------------------------------------------------
    def _plot_to_window(
        self,
        win: PlotWindow,
        lines: Iterable[Tuple[str, List[float], List[float]]],
        title: str,
    ) -> None:
        # Update the figure and canvas size first so window sizing is correct.
        fig_w, fig_h = float(self.fig_w.value()), float(self.fig_h.value())
        win.canvas.figure.set_size_inches(fig_w, fig_h)
        dpi = win.canvas.figure.dpi
        old_size = win.canvas.size()
        new_w, new_h = int(fig_w * dpi), int(fig_h * dpi)
        win.canvas.setFixedSize(new_w, new_h)

        ax = win.ax
        ax.clear()
        ls = (
            None
            if self.line_style.currentText() == "None"
            else self.line_style.currentText()
        )
        marker = (
            None
            if self.marker_style.currentText() == "None"
            else self.marker_style.currentText()
        )
        deltas: List[Tuple[str, float, str]] = []
        for i, (label, x, y) in enumerate(lines):
            color = self._color.name() if i == 0 else None
            (line,) = ax.plot(
                x,
                y,
                linestyle=ls,
                marker=marker,
                linewidth=float(self.line_width.value()),
                markersize=float(self.marker_size.value()),
                color=color,
                label=label,
            )
            if x and y:
                max_idx = max(range(len(x)), key=x.__getitem__)
                delta = y[max_idx] - y[0]
                deltas.append((label, delta, line.get_color()))

        ax.set_xlabel(self.x_label_edit.text(), fontsize=int(self.label_fs.value()))
        ax.set_ylabel(self.y_label_edit.text(), fontsize=int(self.label_fs.value()))
        ax.set_title(title, fontsize=int(self.title_fs.value()))
        ax.tick_params(labelsize=int(self.tick_fs.value()))
        ax.grid(self.grid_cb.isChecked(), which="both", linestyle="--", alpha=0.4)
        if self.legend_cb.isChecked():
            ax.legend(
                loc=self.legend_loc.currentText(), fontsize=int(self.legend_fs.value())
            )

        for i, (label, delta, color) in enumerate(deltas):
            ax.text(
                0.98,
                0.02 + i * 0.06,
                f"Δ {label}={delta:.2f} {self.y_unit_edit.text()}",
                transform=ax.transAxes,
                ha="right",
                va="bottom",
                color=color,
                fontsize=int(self.label_fs.value()),
            )

        win.canvas.draw()
        win.setWindowTitle(title)
        # Resize the window only when first shown or when the canvas size changes.
        if (not win.isVisible()) or (
            old_size.width() != new_w or old_size.height() != new_h
        ):
            win.adjustSize()
            win.setMinimumSize(win.size())

    # ------------------------------------------------------------------
    def plot(self) -> None:
        lines_by_file = self.compute_lines()
        if not lines_by_file:
            return

        selected = [cb.text() for cb in self.y_checks if cb.isChecked()]
        x_name = self.x_combo.currentText()

        if self.mode_combo.currentText() == "Combined":
            if self.plot_win is None:
                # Use a top-level window so it actually appears when shown.
                # Giving the parent here prevents the widget from being a
                # separate window and results in no visible plot when the
                # user clicks the Plot button.
                self.plot_win = PlotWindow(None)
            lines: List[Tuple[str, List[float], List[float]]] = []
            for path, sets in lines_by_file.items():
                base = os.path.basename(path)
                for y_name, xs, ys in sets:
                    label = f"{base} {y_name}" if len(lines_by_file) > 1 else y_name
                    lines.append((label, xs, ys))
            title = self.title_edit.text().strip()
            if not title:
                base = (
                    os.path.basename(next(iter(lines_by_file)))
                    if len(lines_by_file) == 1
                    else f"{len(lines_by_file)} files"
                )
                title = f"{' / '.join(selected)} vs {x_name} — {base}"
            self._plot_to_window(self.plot_win, lines, title)
            self.plot_win.show()
            self.last_plot_window = self.plot_win
            if self.save_cb.isChecked():
                self._save_window(self.plot_win)
        else:  # Separate
            for w in self.plot_wins:
                w.close()
            self.plot_wins = []
            for path, sets in lines_by_file.items():
                # As above, create top-level windows so they are independent
                # from the settings dialog and show up correctly.
                win = PlotWindow(None)
                lines: List[Tuple[str, List[float], List[float]]] = []
                for y_name, xs, ys in sets:
                    lines.append((y_name, xs, ys))
                base = os.path.basename(path)
                title = (
                    self.title_edit.text().strip()
                    or f"{' / '.join(selected)} vs {x_name} — {base}"
                )
                self._plot_to_window(win, lines, title)
                win.show()
                self.plot_wins.append(win)
                if self.save_cb.isChecked():
                    self._save_window(win)
            self.last_plot_window = self.plot_wins[0] if self.plot_wins else None

    # ------------------------------------------------------------------
    def clear_plot(self) -> None:
        if self.plot_win is not None:
            self.plot_win.close()
            self.plot_win = None
        for w in self.plot_wins:
            w.close()
        self.plot_wins = []
        self.last_plot_window = None

    # ------------------------------------------------------------------
    def closeEvent(
        self, event: QtGui.QCloseEvent
    ) -> None:  # pragma: no cover - GUI cleanup
        """Ensure all plot windows are closed when the main window closes."""
        self.clear_plot()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    def _save_window(self, win: PlotWindow) -> None:
        out_dir = self.out_dir.text().strip() or os.getcwd()
        os.makedirs(out_dir, exist_ok=True)
        ext = self.format_combo.currentText().lower()
        base = win.ax.get_title() or "plot"
        safe = re.sub(r"[^\w\-\.]+", "_", base)
        path = os.path.join(out_dir, f"{safe}.{ext}")
        win.canvas.figure.savefig(path, dpi=int(self.dpi_spin.value()))

    def save_current(self) -> None:
        if self.last_plot_window is None:
            QtWidgets.QMessageBox.information(self, "No data", "Nothing to save.")
            return
        self._save_window(self.last_plot_window)


def main() -> QtWidgets.QWidget:
    win = PdfPlotterWindow()
    win.show()
    return win


if __name__ == "__main__":  # pragma: no cover - manual execution
    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    _w = main()
    app.exec()
