
from __future__ import annotations

import os
import re
import sys
import warnings
from typing import Any, Dict, Iterable, List, Tuple, cast

import matplotlib.pyplot as plt
import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.figure import Figure

warnings.warn(
    "This module is deprecated and will be removed in a future version. "
    "Use plotting.plugins.pdf_plotter instead.",
    DeprecationWarning,
)

try:  # optional dependency
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore

from plotting.shared.theme import ensure_app_theme
from plotting.shared.paths import (
    prepare_output_dir,
    get_last_output_dir,
    set_last_output_dir,
)
from plotting.shared.readability import apply_readability_fonts, set_readability
from plotting.shared.backends import wants_matplotlib, wants_origin
from plotting.shared.toolkit import (
    save_figure,
    run_with_console,
    arrange_top_layout,
    restore_backend_choice,
    store_backend_choice,
    selected_backend,
    restore_png_dpi,
    store_png_dpi,
    create_file_widget,
    show_plots,
    restore_combo_choice,
    store_combo_choice,
)




NumberRow = Tuple[float, float, float, float]  # T1, T2, Force, Strain

_LINE_SANITIZE_RE = re.compile(r"[^\d;.,\-\+\s]")
_TOKEN_TRANSLATION = str.maketrans({
    chr(0x2212): '-',  # minus sign
    chr(0x2012): '-',
    chr(0x2013): '-',
    chr(0x2014): '-',
    chr(0x2015): '-',
    chr(0xFF0D): '-',
    chr(0xFE63): '-',
    chr(0x00A0): ' ',  # non-breaking space
    chr(0x202F): ' ',  # narrow no-break space
    chr(0x2009): ' ',  # thin space
    chr(0x200A): ' ',
    chr(0x2007): ' ',
    "'": '',
    '`': '',
})


def _normalize_numeric_token(token: str) -> float:
    token = token.strip()
    if not token:
        raise ValueError('empty token')
    if token.count(',') and token.count('.'):
        if token.rfind(',') > token.rfind('.'):
            token = token.replace('.', '')
            token = token.replace(',', '.')
        else:
            token = token.replace(',', '')
    elif token.count(','):
        token = token.replace(',', '.')
    token = token.replace(' ', '')
    return float(token)

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

    Supports legacy ``T1;T2;Force;Strain`` rows and the newer
    ``T1;T2;T2-T1;T1+T2;Force;Strain`` layout. Comma or dot decimal separators
    are accepted and stray characters are stripped before parsing.
    """
    if PdfReader is None:
        raise RuntimeError("pypdf not installed. Install with: pip install pypdf")
    rows: List[NumberRow] = []
    reader = PdfReader(path)
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = raw.translate(_TOKEN_TRANSLATION).strip()
            if not line:
                continue
            sanitized = _LINE_SANITIZE_RE.sub('', line)
            parts = [segment.strip() for segment in sanitized.split(';') if segment.strip()]
            if len(parts) < 4:
                continue
            try:
                numbers = [_normalize_numeric_token(part) for part in parts]
            except ValueError:
                continue
            if len(numbers) >= 6:
                t1, t2 = numbers[0], numbers[1]
                force, strain = numbers[-2], numbers[-1]
            elif len(numbers) == 4:
                t1, t2, force, strain = numbers
            else:
                continue
            rows.append((t1, t2, force, strain))
    return rows

# -----------------------------------------------------------------------------
# Main settings window
# -----------------------------------------------------------------------------
class PdfPlotterWindow(QtWidgets.QDialog):
    """Settings window for plotting data extracted from PDFs."""


    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF T1/T2 Plotter")

        self._initialising = True

        # Loaded data caches
        self.data: List[Tuple[str, List[NumberRow]]] = []
        self._data_cache: Dict[str, List[NumberRow]] = {}
        self._data_mtime: Dict[str, float] = {}

        # Track Matplotlib figures and last plotted data
        self.matplotlib_figures: List[Figure] = []
        self._last_lines: List[Tuple[str, np.ndarray, np.ndarray]] = []
        self._last_title: str = ""
        self._last_x_label: str = ""
        self._last_y_label: str = ""

        self.files, file_widget = create_file_widget(
            self,
            ext=".pdf",
            key="pdf_plotter",
            on_change=self._on_files_changed,
        )
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(120)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QGridLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setHorizontalSpacing(12)
        left_layout.setVerticalSpacing(12)

        # Y variables
        variables_group = QtWidgets.QGroupBox("Y Variables")
        var_layout = QtWidgets.QVBoxLayout(variables_group)
        self.y_checks: List[QtWidgets.QCheckBox] = []
        for name in ["T1+T2", "T1", "T2", "T2-T1"]:
            cb = QtWidgets.QCheckBox(name)
            cb.setChecked(name == "T1+T2")
            cb.stateChanged.connect(self._maybe_auto_plot)
            var_layout.addWidget(cb)
            self.y_checks.append(cb)
        var_layout.addStretch(1)
        left_layout.addWidget(variables_group, 0, 0)

        # Axes and mode
        self.x_combo = QtWidgets.QComboBox()
        self.x_combo.addItems(["Force (N)", "Strain (mm)", "Force & Strain"])
        self.x_combo.currentIndexChanged.connect(self._maybe_auto_plot)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Combined", "Separate"])
        self.mode_combo.currentIndexChanged.connect(self._maybe_auto_plot)

        self.zero_cb = QtWidgets.QCheckBox("First point at zero")
        self.zero_cb.setChecked(True)
        self.zero_cb.stateChanged.connect(self._maybe_auto_plot)

        axes_group = QtWidgets.QGroupBox("Axes & Mode")
        axes_form = QtWidgets.QFormLayout(axes_group)
        axes_form.addRow("X axis", self.x_combo)
        axes_form.addRow("Plot mode", self.mode_combo)
        axes_form.addRow("Zero first point", self.zero_cb)
        left_layout.addWidget(axes_group, 0, 1)

        # Series styling
        self.line_style = QtWidgets.QComboBox()
        self.line_style.addItems(["-", "--", ":", "-.", "None"])
        self.line_style.setCurrentIndex(0)
        self.line_style.currentIndexChanged.connect(self._maybe_auto_plot)

        self.marker_style = QtWidgets.QComboBox()
        self.marker_style.addItems(["o", "s", "d", "^", "v", "x", "+", ".", "None"])
        self.marker_style.setCurrentIndex(0)
        self.marker_style.currentIndexChanged.connect(self._maybe_auto_plot)

        self.line_width = _NoWheelDoubleSpinBox()
        self.line_width.setRange(0.1, 10.0)
        self.line_width.setSingleStep(0.1)
        self.line_width.setValue(1.5)
        self.line_width.valueChanged.connect(self._maybe_auto_plot)

        self.marker_size = _NoWheelDoubleSpinBox()
        self.marker_size.setRange(0.5, 30.0)
        self.marker_size.setSingleStep(0.5)
        self.marker_size.setValue(5.0)
        self.marker_size.valueChanged.connect(self._maybe_auto_plot)

        self.grid_cb = QtWidgets.QCheckBox()
        self.grid_cb.setChecked(True)
        self.grid_cb.stateChanged.connect(self._maybe_auto_plot)

        self.dark_cb = QtWidgets.QCheckBox("Dark background")
        self.dark_cb.setChecked(False)
        self.dark_cb.toggled.connect(self._apply_dark_global)

        style_group = QtWidgets.QGroupBox("Series Style")
        style_form = QtWidgets.QFormLayout(style_group)
        style_form.addRow("Line style", self.line_style)
        style_form.addRow("Marker", self.marker_style)
        style_form.addRow("Line width", self.line_width)
        style_form.addRow("Marker size", self.marker_size)
        style_form.addRow("Grid", self.grid_cb)
        style_form.addRow(self.dark_cb)
        left_layout.addWidget(style_group, 1, 0)

        # Legend options
        self.legend_cb = QtWidgets.QCheckBox("Show legend")
        self.legend_cb.setChecked(True)
        self.legend_cb.stateChanged.connect(self._maybe_auto_plot)

        self.legend_loc = QtWidgets.QComboBox()
        self.legend_loc.addItems([
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
        ])
        self.legend_loc.setCurrentIndex(0)
        self.legend_loc.currentIndexChanged.connect(self._maybe_auto_plot)

        self.legend_fs = _NoWheelSpinBox()
        self.legend_fs.setRange(6, 48)
        self.legend_fs.setValue(10)
        self.legend_fs.valueChanged.connect(self._maybe_auto_plot)

        legend_group = QtWidgets.QGroupBox("Legend")
        legend_form = QtWidgets.QFormLayout(legend_group)
        legend_form.addRow(self.legend_cb)
        legend_form.addRow("Location", self.legend_loc)
        legend_form.addRow("Font size", self.legend_fs)
        left_layout.addWidget(legend_group, 1, 1)

        # Font sizes
        self.title_fs = _NoWheelSpinBox()
        self.title_fs.setRange(6, 72)
        self.title_fs.setValue(12)
        self.title_fs.valueChanged.connect(self._maybe_auto_plot)

        self.label_fs = _NoWheelSpinBox()
        self.label_fs.setRange(6, 72)
        self.label_fs.setValue(11)
        self.label_fs.valueChanged.connect(self._maybe_auto_plot)

        self.tick_fs = _NoWheelSpinBox()
        self.tick_fs.setRange(6, 48)
        self.tick_fs.setValue(10)
        self.tick_fs.valueChanged.connect(self._maybe_auto_plot)

        fonts_group = QtWidgets.QGroupBox("Fonts")
        fonts_form = QtWidgets.QFormLayout(fonts_group)
        fonts_form.addRow("Title size", self.title_fs)
        fonts_form.addRow("Label size", self.label_fs)
        fonts_form.addRow("Tick size", self.tick_fs)
        left_layout.addWidget(fonts_group, 2, 0)

        # Output and saving
        self.save_cb = QtWidgets.QCheckBox("Save on plot")
        self.save_cb.setChecked(False)

        self.out_dir = QtWidgets.QLineEdit(get_last_output_dir(key="pdf_plotter"))
        self.browse_out_btn = QtWidgets.QPushButton("Browse")
        self.browse_out_btn.clicked.connect(self._browse_out)

        self.subdir_cb = QtWidgets.QCheckBox("Create subfolder")
        self.subdir_cb.setChecked(False)

        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(["png", "pdf", "svg"])

        self.dpi_spin = _NoWheelSpinBox()
        self.dpi_spin.setRange(72, 3000)
        restore_png_dpi("pdf_plotter", self.dpi_spin, 1200)

        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        restore_backend_choice("pdf_plotter", self.backend_combo, "matplotlib")
        self.backend_combo.currentIndexChanged.connect(self._maybe_auto_plot)

        output_group = QtWidgets.QGroupBox("Output")
        out_form = QtWidgets.QFormLayout(output_group)
        out_form.addRow("Backend", self.backend_combo)
        out_form.addRow(self.save_cb)
        out_form.addRow("Format", self.format_combo)
        out_form.addRow("PNG dpi", self.dpi_spin)
        out_form.addRow(self.subdir_cb)
        out_form.addRow("Output dir", self._hbox(self.out_dir, self.browse_out_btn))
        left_layout.addWidget(output_group, 3, 0, 1, 2)

        # Figure sizing
        self.fig_w = _NoWheelDoubleSpinBox()
        self.fig_h = _NoWheelDoubleSpinBox()
        self.fig_units = QtWidgets.QComboBox()
        self.fig_units.addItems(["mm", "cm", "in"])
        self.lock_aspect_cb = QtWidgets.QCheckBox("Lock aspect ratio")
        self.lock_aspect_cb.setChecked(True)
        self._fig_limits_mm = {
            "w": (60.0, 1600.0),
            "h": (45.0, 1200.0),
        }
        self._updating_size = False
        self._current_units = "mm"
        self.fig_units.setCurrentText(self._current_units)
        self._current_units = self.fig_units.currentText()
        self._update_fig_ranges(self._current_units)
        self._configure_size_spinboxes(self._current_units)
        self.fig_w.setValue(160.0)
        self.fig_h.setValue(120.0)
        self._aspect_ratio = self.fig_w.value() / max(self.fig_h.value(), 1e-9)

        self.fig_units.currentTextChanged.connect(self._on_units_changed)
        self.lock_aspect_cb.toggled.connect(self._on_lock_toggled)
        self.fig_w.valueChanged.connect(self._on_width_changed)
        self.fig_h.valueChanged.connect(self._on_height_changed)

        figure_group = QtWidgets.QGroupBox("Figure Size")
        fig_form = QtWidgets.QFormLayout(figure_group)
        mult_label = QtWidgets.QLabel("x")
        mult_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        fig_form.addRow("Size", self._hbox(self.fig_w, mult_label, self.fig_h, self.fig_units, self.lock_aspect_cb))
        left_layout.addWidget(figure_group, 2, 1)
        left_layout.setRowStretch(4, 1)
        left_layout.setColumnStretch(0, 1)
        left_layout.setColumnStretch(1, 1)

        # Action buttons
        self.auto_cb = QtWidgets.QCheckBox("Auto update on change")
        self.auto_cb.setChecked(True)
        self.auto_cb.stateChanged.connect(self._maybe_auto_plot)

        self.plot_btn = QtWidgets.QPushButton("Plot")
        self.plot_btn.clicked.connect(lambda: run_with_console(self.plot, self.console))
        self.plot_btn.setDefault(True)

        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear_plot)

        self.save_now_btn = QtWidgets.QPushButton("Save Now")
        self.save_now_btn.clicked.connect(self.save_current)

        footer_row = QtWidgets.QHBoxLayout()
        footer_row.addWidget(self.auto_cb)
        footer_row.addStretch(1)
        footer_row.addWidget(self.save_now_btn)
        footer_row.addWidget(self.clear_btn)
        footer_row.addWidget(self.plot_btn)

        arrange_top_layout(
            self,
            file_widget,
            left,
            self.console,
            footer=footer_row,
            help_topic="plot_pdf",
        )

        self._initialising = False
        self._reload_selected_files(show_feedback=False)
    def _hbox(self, *widgets: QtWidgets.QWidget) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        l = QtWidgets.QHBoxLayout(w)
        for x in widgets:
            l.addWidget(x)
        return w

    def _on_files_changed(self, _: List[str]) -> None:
        if getattr(self, "_initialising", False):
            return
        self._reload_selected_files(show_feedback=True)
        self._maybe_auto_plot()

    def _reload_selected_files(self, *, show_feedback: bool) -> None:
        current = [path for path in self.files if path]
        cached_paths = set(self._data_cache)
        for path in cached_paths - set(current):
            self._data_cache.pop(path, None)
            self._data_mtime.pop(path, None)

        errors: List[str] = []
        new_data: List[Tuple[str, List[NumberRow]]] = []
        total_rows = 0
        updated = False

        for path in current:
            rows = self._data_cache.get(path)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = -1.0
            if rows is None or self._data_mtime.get(path) != mtime:
                try:
                    rows = parse_pdf_to_rows(path)
                except Exception as exc:
                    errors.append(f"{os.path.basename(path)}: {exc}")
                    rows = []
                self._data_cache[path] = rows
                self._data_mtime[path] = mtime
                updated = True
            new_data.append((path, rows or []))
            total_rows += len(rows or [])

        self.data = new_data

        if errors and updated:
            QtWidgets.QMessageBox.critical(
                self,
                "Parse error",
                "\n".join(errors),
            )
        if show_feedback and updated and current and total_rows == 0 and not errors:
            QtWidgets.QMessageBox.information(
                self,
                "No data",
                "No numeric rows found. Check the PDF contents.",
            )

    def _figure_size_inches(self) -> Tuple[float, float]:
        w = float(self.fig_w.value())
        h = float(self.fig_h.value())
        unit = self.fig_units.currentText()
        mm_per_unit = self._unit_to_mm(unit)
        if mm_per_unit <= 0:
            return w, h
        return (w * mm_per_unit) / 25.4, (h * mm_per_unit) / 25.4

    def _convert_units(self, value: float, from_unit: str, to_unit: str) -> float:
        from_mm = self._unit_to_mm(from_unit)
        to_mm = self._unit_to_mm(to_unit)
        if from_mm <= 0 or to_mm <= 0:
            return value
        mm = value * from_mm
        return mm / to_mm

    def _unit_to_mm(self, unit: str) -> float:
        return {"mm": 1.0, "cm": 10.0, "in": 25.4}.get(unit, 25.4)

    def _update_fig_ranges(self, unit: str) -> None:
        factor = self._unit_to_mm(unit)
        if factor <= 0:
            factor = 25.4
        limits = [
            (self.fig_w, self._fig_limits_mm.get("w", (60.0, 1600.0))),
            (self.fig_h, self._fig_limits_mm.get("h", (45.0, 1200.0))),
        ]
        for spin, (min_mm, max_mm) in limits:
            min_val = min_mm / factor
            max_val = max_mm / factor
            block = spin.blockSignals(True)
            try:
                spin.setRange(min_val, max_val)
            finally:
                spin.blockSignals(block)

    def _configure_size_spinboxes(self, unit: str) -> None:
        if unit == "mm":
            step = 5.0
            decimals = 1
        elif unit == "cm":
            step = 0.5
            decimals = 2
        else:
            step = 0.25
            decimals = 2
        for spin in (self.fig_w, self.fig_h):
            block = spin.blockSignals(True)
            try:
                spin.setSingleStep(step)
                spin.setDecimals(decimals)
            finally:
                spin.blockSignals(block)

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
            self._update_fig_ranges(new_unit)
            self._configure_size_spinboxes(new_unit)
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
            set_last_output_dir(d, key="pdf_plotter")

    def _maybe_auto_plot(self) -> None:
        if self.auto_cb.isChecked():
            self.plot()

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
                    elif y_name == "T2-T1":
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
        apply_readability_fonts()
        set_readability("pdf_plotter", True)
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
                title = f"{y_label} vs {x_label} - {base_title}"
            else:
                title = f"{y_label} vs {x_label} - {len(lines_by_file)} files"
            if wants_matplotlib(backend):
                self._plot_with_matplotlib(lines, title, x_label, y_label)
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
                title = f"{y_label} vs {x_label} - {base}"
                if wants_matplotlib(backend):
                    self._plot_with_matplotlib(lines, title, x_label, y_label)
                    if self.save_cb.isChecked():
                        self._save_lines(self._last_lines, self._last_title, x_label, y_label)
                if wants_origin(backend):
                    try:
                        self._plot_to_origin(lines, title, x_label, y_label)
                    except Exception as e:
                        print(f"Origin plot failed: {e}")

    def _plot_with_matplotlib(
        self,
        lines: Iterable[Tuple[str, np.ndarray, np.ndarray]],
        title: str,
        x_label: str,
        y_label: str,
    ) -> Figure:
        fig_w, fig_h = self._figure_size_inches()
        prepared = [
            (lbl, np.asarray(xs, dtype=float), np.asarray(ys, dtype=float))
            for (lbl, xs, ys) in lines
        ]
        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
        self._draw_on_axes(ax, prepared, title, x_label, y_label)
        try:
            manager = fig.canvas.manager
        except Exception:
            manager = None
        if manager is not None:
            try:
                manager.set_window_title(title)
            except Exception:
                pass
        self.matplotlib_figures.append(fig)
        self._last_lines = prepared
        self._last_title = title
        self._last_x_label = x_label
        self._last_y_label = y_label
        show_plots()
        return fig

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
        gp = cast(Any, op.new_graph(template='scatter'))
        gl = gp[0]
        for idx, (lbl, xs, ys) in enumerate(lines):
            w = cast(Any, op.new_sheet('w', lname=f'data_{idx}'))
            w.from_list(0, np.asarray(xs, dtype=float).tolist())
            w.from_list(1, np.asarray(ys, dtype=float).tolist())
            w.cols_axis('XY')
            plot_obj = gl.add_plot(w, coly=1, colx=0, type='y')
            if plot_obj is not None:
                p_any = cast(Any, plot_obj)
                try:
                    p_any.symbol_shape = 2
                except Exception:
                    pass
                try:
                    p_any.lname = lbl
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
        self._reload_selected_files(show_feedback=False)
        if not self.data:
            QtWidgets.QMessageBox.information(self, "No data", "Load PDF files first.")
            return
        self.clear_plot()
        x_choice = self.x_combo.currentText()
        targets = ["Force (N)", "Strain (mm)"] if x_choice == "Force & Strain" else [x_choice]
        for x_name in targets:
            self._plot_single(x_name)

    def clear_plot(self) -> None:
        for fig in self.matplotlib_figures:
            try:
                plt.close(fig)
            except Exception:
                pass
        self.matplotlib_figures.clear()
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
    ensure_app_theme(app)
    _w = main()
    app.exec()
