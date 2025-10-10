"""Visualise strain worksheet or database metrics across scatter combinations."""

from __future__ import annotations

import itertools
import logging
import re
import sys
from pathlib import Path
from typing import Iterable, List, NamedTuple, Sequence, Tuple

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib import cm
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from plotting.backends import wants_matplotlib, wants_origin
from plotting.utils import (
    ensure_app_theme,
    install_standard_menu,
    origin_session,
    schedule_origin_release,
)

from experiments.microwire_data_builder.core import _parse_numeric, _parse_strain_float


def _clean_header(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _pretty_header(value: object, index: int) -> str:
    cleaned = _clean_header(value)
    if cleaned and not cleaned.lower().startswith("unnamed"):
        return cleaned
    return f"Column {index + 1}"


def _extract_element_counts(composition: str) -> dict[str, float]:
    """Extract Ni/Fe/Ga/Co counts from a composition string."""

    counts = {"Ni": 0.0, "Fe": 0.0, "Ga": 0.0, "Co": 0.0}
    if not composition:
        return counts

    for element, value in re.findall(r"(Ni|Fe|Ga|Co)\s*(\d+(?:\.\d+)?)", composition):
        try:
            counts[element] = float(value)
        except ValueError:
            continue
    return counts


def _build_column_map(columns: Sequence[object]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, header in enumerate(columns):
        name = _clean_header(header)
        lowered = name.lower()
        if not lowered:
            continue
        if "composition" in lowered:
            mapping.setdefault("composition", idx)
        elif "microwire" in lowered or "wire" in lowered:
            mapping.setdefault("microwire", idx)
        elif "strain" in lowered or "%" in lowered:
            mapping.setdefault("strain", idx)
        elif "status" in lowered or "broke" in lowered:
            mapping.setdefault("status", idx)
    return mapping


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not pd.isna(value):
        return str(value).strip()
    if pd.isna(value):
        return ""
    return str(value).strip()


class PlotConfig(NamedTuple):
    labels: Tuple[str, ...]
    dimension: int  # 2 or 3


def _auto_plot_combinations(
    labels: Iterable[str],
    strain_label: str,
    include_2d: bool,
    include_3d: bool,
) -> List[PlotConfig]:
    """Build automatic combinations ensuring strain is always present."""

    label_list = list(dict.fromkeys(labels))  # preserve order, drop duplicates
    combos: List[PlotConfig] = []
    if include_2d:
        for combo in itertools.combinations(label_list, 2):
            if strain_label in combo:
                combos.append(PlotConfig(combo, 2))
    if include_3d:
        for combo in itertools.combinations(label_list, 3):
            if strain_label in combo:
                combos.append(PlotConfig(combo, 3))
    return combos


class Strain3DPlotter(QtWidgets.QWidget):
    """Widget that renders 2D/3D scatter plots for strain metrics."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Strain Plot Explorer")
        self.resize(1480, 980)

        self.logger = logging.getLogger("strain_3d_plotter")
        self.logger.setLevel(logging.INFO)
        self.settings = QtCore.QSettings("MicrowireLab", "Strain3DPlotter")
        self._floating_windows: list[QtWidgets.QMainWindow] = []

        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------ ui helpers
    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)

        self.controls = QtWidgets.QFrame()
        self.controls.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.controls.setMinimumWidth(320)
        controls_layout = QtWidgets.QVBoxLayout(self.controls)
        controls_layout.setContentsMargins(12, 12, 12, 12)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self.input_edit = QtWidgets.QLineEdit()
        self.input_button = QtWidgets.QPushButton("Browse…")
        self.input_button.clicked.connect(self._choose_input_file)
        input_row = QtWidgets.QHBoxLayout()
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(self.input_button)
        form.addRow("Worksheet or database", input_row)

        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        form.addRow("Output backend", self.backend_combo)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Automatic combinations", "Manual axes"])
        self.mode_combo.currentIndexChanged.connect(self._update_mode_state)
        form.addRow("Plot mode", self.mode_combo)

        self.auto_options = QtWidgets.QWidget()
        auto_layout = QtWidgets.QHBoxLayout(self.auto_options)
        auto_layout.setContentsMargins(0, 0, 0, 0)
        self.auto_2d_check = QtWidgets.QCheckBox("Generate 2D plots")
        self.auto_3d_check = QtWidgets.QCheckBox("Generate 3D plots")
        self.auto_3d_check.setChecked(True)
        auto_layout.addWidget(self.auto_2d_check)
        auto_layout.addWidget(self.auto_3d_check)
        auto_layout.addStretch(1)
        form.addRow("Automatic options", self.auto_options)

        self.manual_options = QtWidgets.QWidget()
        manual_form = QtWidgets.QGridLayout(self.manual_options)
        manual_form.setContentsMargins(0, 0, 0, 0)
        self.manual_dimension_combo = QtWidgets.QComboBox()
        self.manual_dimension_combo.addItems(["2D", "3D"])
        self.manual_dimension_combo.currentIndexChanged.connect(
            self._update_manual_dimension_state
        )
        manual_form.addWidget(QtWidgets.QLabel("Plot type"), 0, 0)
        manual_form.addWidget(self.manual_dimension_combo, 0, 1)
        self.axis_x_combo = QtWidgets.QComboBox()
        self.axis_y_combo = QtWidgets.QComboBox()
        self.axis_z_combo = QtWidgets.QComboBox()
        manual_form.addWidget(QtWidgets.QLabel("X axis"), 1, 0)
        manual_form.addWidget(self.axis_x_combo, 1, 1)
        manual_form.addWidget(QtWidgets.QLabel("Y axis"), 2, 0)
        manual_form.addWidget(self.axis_y_combo, 2, 1)
        manual_form.addWidget(QtWidgets.QLabel("Z axis"), 3, 0)
        manual_form.addWidget(self.axis_z_combo, 3, 1)
        form.addRow("Manual options", self.manual_options)

        controls_layout.addLayout(form)

        buttons_row = QtWidgets.QHBoxLayout()
        self.run_button = QtWidgets.QPushButton("Generate plots")
        self.run_button.clicked.connect(self._generate_plots)
        buttons_row.addWidget(self.run_button)

        self.fullscreen_button = QtWidgets.QPushButton("Open selected plot in new window")
        self.fullscreen_button.setEnabled(False)
        self.fullscreen_button.clicked.connect(self._open_fullscreen_plot)
        buttons_row.addWidget(self.fullscreen_button)
        buttons_row.addStretch(1)
        controls_layout.addLayout(buttons_row)
        controls_layout.addStretch(1)

        layout.addWidget(self.controls, 0)

        self.output_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        layout.addWidget(self.output_splitter, 1)
        layout.setStretch(0, 0)
        layout.setStretch(1, 1)

        self.tab_widget = QtWidgets.QTabWidget()
        self.output_splitter.addWidget(self.tab_widget)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Load a worksheet to generate scatter plots…")
        self.output_splitter.addWidget(self.log_view)
        self.output_splitter.setStretchFactor(0, 4)
        self.output_splitter.setStretchFactor(1, 2)
        self.output_splitter.setChildrenCollapsible(False)

        self.tab_widget.currentChanged.connect(self._update_fullscreen_state)

        install_standard_menu(
            self,
            help_topic="strain_3d_plotter",
            console=self.log_view,
            open_file=self._choose_input_file,
        )
        self._update_mode_state()
        self._update_manual_dimension_state()

    def _load_settings(self) -> None:
        value = self.settings.value("input_path", "")
        if isinstance(value, str):
            self.input_edit.setText(value)
        backend = self.settings.value("backend", "Matplotlib")
        if isinstance(backend, str):
            index = self.backend_combo.findText(backend, QtCore.Qt.MatchFlag.MatchFixedString)
            if index >= 0:
                self.backend_combo.setCurrentIndex(index)
        mode = self.settings.value("mode", "Automatic combinations")
        if isinstance(mode, str):
            mode_index = self.mode_combo.findText(mode, QtCore.Qt.MatchFlag.MatchFixedString)
            if mode_index >= 0:
                self.mode_combo.setCurrentIndex(mode_index)
        manual_dim = self.settings.value("manual_dimension", "2D")
        if isinstance(manual_dim, str):
            dim_index = self.manual_dimension_combo.findText(
                manual_dim, QtCore.Qt.MatchFlag.MatchFixedString
            )
            if dim_index >= 0:
                self.manual_dimension_combo.setCurrentIndex(dim_index)
        auto_2d = self.settings.value("auto_2d", False)
        if isinstance(auto_2d, bool):
            self.auto_2d_check.setChecked(auto_2d)
        auto_3d = self.settings.value("auto_3d", True)
        if isinstance(auto_3d, bool):
            self.auto_3d_check.setChecked(auto_3d)

    def _save_settings(self) -> None:
        self.settings.setValue("input_path", self.input_edit.text())
        self.settings.setValue("backend", self.backend_combo.currentText())
        self.settings.setValue("mode", self.mode_combo.currentText())
        self.settings.setValue("manual_dimension", self.manual_dimension_combo.currentText())
        self.settings.setValue("auto_2d", self.auto_2d_check.isChecked())
        self.settings.setValue("auto_3d", self.auto_3d_check.isChecked())
        self.settings.sync()

    def _update_mode_state(self) -> None:
        automatic = self.mode_combo.currentText() == "Automatic combinations"
        self.auto_options.setVisible(automatic)
        self.manual_options.setVisible(not automatic)

    def _update_manual_dimension_state(self) -> None:
        is_3d = self.manual_dimension_combo.currentText() == "3D"
        self.axis_z_combo.setEnabled(is_3d)

    # ------------------------------------------------------------------ file selection
    def _choose_input_file(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select worksheet or database",
            self.input_edit.text() or str(Path.home()),
            "Excel files (*.xlsx *.xlsm *.xls)",
        )
        if filename:
            self.input_edit.setText(filename)
            self._save_settings()

    # ------------------------------------------------------------------ plotting logic
    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.logger.info(message)

    def _update_fullscreen_state(self) -> None:
        tab = self.tab_widget.currentWidget()
        if tab is None:
            self.fullscreen_button.setEnabled(False)
            return
        has_data = (
            tab.property("plot_data") is not None
            and tab.property("plot_combo") is not None
            and tab.property("plot_dimension") is not None
        )
        self.fullscreen_button.setEnabled(has_data)

    def _open_fullscreen_plot(self) -> None:
        tab = self.tab_widget.currentWidget()
        if tab is None:
            return

        subset = tab.property("plot_data")
        combo = tab.property("plot_combo")
        dimension = tab.property("plot_dimension")
        title = tab.property("plot_title") or self.tab_widget.tabText(self.tab_widget.currentIndex())
        if subset is None or combo is None or dimension is None:
            QtWidgets.QMessageBox.information(
                self,
                "Strain Plot Explorer",
                "Select a tab generated from worksheet data before opening a full-screen plot.",
            )
            return

        window = PlotWindow(self, title, subset, combo, dimension)
        window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.showMaximized()
        self._floating_windows.append(window)
        window.destroyed.connect(
            lambda: self._floating_windows.remove(window) if window in self._floating_windows else None
        )

    def _generate_plots(self) -> None:
        self.tab_widget.clear()
        self.log_view.clear()
        self.fullscreen_button.setEnabled(False)

        for window in list(self._floating_windows):
            try:
                window.close()
            except Exception:
                pass
        self._floating_windows.clear()

        path = Path(self.input_edit.text().strip())
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, "Strain Plot Explorer", "Please select an existing file.")
            return

        try:
            df = pd.read_excel(path)
        except Exception as exc:  # pragma: no cover - user feedback
            QtWidgets.QMessageBox.critical(
                self,
                "Strain Plot Explorer",
                f"Failed to read worksheet:\n{exc}",
            )
            return

        df = df.dropna(how="all")
        if df.empty:
            QtWidgets.QMessageBox.information(self, "Strain Plot Explorer", "The worksheet does not contain any data.")
            return

        columns = list(df.columns)
        mapping = _build_column_map(columns)
        composition_idx = mapping.get("composition") if mapping.get("composition") is not None else (0 if columns else None)
        microwire_idx = mapping.get("microwire") if mapping.get("microwire") is not None else (1 if len(columns) > 1 else None)
        strain_idx = mapping.get("strain")
        status_idx = mapping.get("status")

        if strain_idx is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Strain Plot Explorer",
                "Could not locate a strain column. Ensure the header contains 'strain'.",
            )
            return

        strain_label = _pretty_header(columns[strain_idx], strain_idx)

        numeric_columns: List[Tuple[int, str]] = []
        for idx, header in enumerate(columns):
            if idx == strain_idx or idx == composition_idx or idx == microwire_idx or idx == status_idx:
                continue
            label = _pretty_header(header, idx)
            lowered_label = label.lower()
            if "m length" in lowered_label or "a length" in lowered_label:
                continue
            if "file" in lowered_label or "matplotlib" in lowered_label or "figure" in lowered_label:
                continue
            numeric_columns.append((idx, label))

        records = []
        for row_index, row in df.iterrows():
            if composition_idx is not None:
                if _clean_cell(row.iloc[composition_idx]).lower() == "composition":
                    continue
            status_value = _clean_cell(row.iloc[status_idx]) if status_idx is not None else ""
            if status_value.lower().startswith("broke"):
                continue
            strain_value = _parse_strain_float(row.iloc[strain_idx])
            if strain_value is None:
                continue
            microwire_label = _clean_cell(row.iloc[microwire_idx]) if microwire_idx is not None else ""
            composition_label = _clean_cell(row.iloc[composition_idx]) if composition_idx is not None else ""
            record = {
                "Microwire": microwire_label or composition_label or f"Row {row_index}",
                "Composition": composition_label,
                strain_label: strain_value,
            }
            for col_idx, label in numeric_columns:
                parsed = _parse_numeric(row.iloc[col_idx])
                record[label] = parsed
            for element, value in _extract_element_counts(composition_label).items():
                record[f"{element} (%)"] = value
            records.append(record)

        if not records:
            QtWidgets.QMessageBox.information(
                self,
                "Strain Plot Explorer",
                "No rows with strain measurements were found in the worksheet.",
            )
            return

        plot_df = pd.DataFrame(records)
        valid_numeric_labels = [
            label
            for label in plot_df.columns
            if label not in {"Microwire", "Composition"}
            and plot_df[label].notna().sum() >= 2
            and plot_df[label].dropna().nunique() >= 2
        ]
        if strain_label not in valid_numeric_labels:
            valid_numeric_labels.insert(0, strain_label)

        self._refresh_axis_options(valid_numeric_labels, strain_label)

        configs = self._build_plot_configs(valid_numeric_labels, strain_label)
        if not configs:
            return

        backend_choice = self.backend_combo.currentText()
        self._save_settings()

        render_matplotlib = wants_matplotlib(backend_choice)
        export_origin = wants_origin(backend_choice)

        self._append_log(f"Loaded {len(records)} rows with strain measurements.")
        two_d = sum(1 for cfg in configs if cfg.dimension == 2)
        three_d = sum(1 for cfg in configs if cfg.dimension == 3)
        if two_d:
            self._append_log(f"Prepared {two_d} 2D combinations.")
        if three_d:
            self._append_log(f"Prepared {three_d} 3D combinations.")
        if not two_d and not three_d:
            self._append_log("No plot combinations satisfied the requested settings.")

        if render_matplotlib:
            self._render_matplotlib_tabs(plot_df, configs)
        else:
            self.tab_widget.setVisible(False)

        if export_origin:
            self._export_origin(plot_df, configs)

        if not render_matplotlib and not export_origin:
            self._append_log("No backends selected—nothing to generate.")

    def _refresh_axis_options(self, labels: List[str], strain_label: str) -> None:
        def _populate(combo: QtWidgets.QComboBox) -> None:
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(labels)
            if current and combo.findText(current) >= 0:
                combo.setCurrentText(current)
            combo.blockSignals(False)

        for widget in (self.axis_x_combo, self.axis_y_combo, self.axis_z_combo):
            _populate(widget)

        if labels and not self.axis_x_combo.currentText():
            self.axis_x_combo.setCurrentText(strain_label)
        if len(labels) > 1 and not self.axis_y_combo.currentText():
            for label in labels:
                if label != strain_label:
                    self.axis_y_combo.setCurrentText(label)
                    break
        if len(labels) > 2 and not self.axis_z_combo.currentText():
            for label in labels:
                if label not in {self.axis_x_combo.currentText(), self.axis_y_combo.currentText()}:
                    self.axis_z_combo.setCurrentText(label)
                    break

    def _build_plot_configs(self, labels: List[str], strain_label: str) -> List[PlotConfig]:
        if self.mode_combo.currentText() == "Automatic combinations":
            include_2d = self.auto_2d_check.isChecked()
            include_3d = self.auto_3d_check.isChecked()
            if not include_2d and not include_3d:
                QtWidgets.QMessageBox.information(
                    self,
                    "Strain Plot Explorer",
                    "Enable at least one automatic plot type (2D or 3D).",
                )
                return []
            configs = _auto_plot_combinations(labels, strain_label, include_2d, include_3d)
            if not configs:
                QtWidgets.QMessageBox.information(
                    self,
                    "Strain Plot Explorer",
                    "No combinations with strain were available for the selected plot types.",
                )
            return configs

        dimension = 3 if self.manual_dimension_combo.currentText() == "3D" else 2
        x_axis = self.axis_x_combo.currentText()
        y_axis = self.axis_y_combo.currentText()
        z_axis = self.axis_z_combo.currentText()
        if not x_axis or not y_axis:
            QtWidgets.QMessageBox.warning(
                self,
                "Strain Plot Explorer",
                "Select at least X and Y axes for manual plotting.",
            )
            return []
        if dimension == 3 and not z_axis:
            QtWidgets.QMessageBox.warning(
                self,
                "Strain Plot Explorer",
                "Select a Z axis for 3D plotting or switch to 2D mode.",
            )
            return []
        labels_tuple: Tuple[str, ...] = (x_axis, y_axis) if dimension == 2 else (x_axis, y_axis, z_axis)
        return [PlotConfig(labels_tuple, dimension)]

    def _render_matplotlib_tabs(self, plot_df: pd.DataFrame, configs: List[PlotConfig]) -> None:
        self.tab_widget.setVisible(True)
        generated = 0

        for config in configs:
            subset = plot_df[["Microwire", *config.labels]].dropna()
            if subset.empty:
                continue
            title = " vs ".join(config.labels)
            fig = Figure(figsize=(10.5, 7.6))
            if config.dimension == 3:
                ax = fig.add_subplot(111, projection="3d")
            else:
                ax = fig.add_subplot(111)
            self._draw_scatter(ax, subset, config.labels)
            fig.tight_layout()

            canvas = FigureCanvas(fig)
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.addWidget(canvas)
            tab.setProperty("plot_combo", config.labels)
            tab.setProperty("plot_data", subset.copy())
            tab.setProperty("plot_title", title)
            tab.setProperty("plot_dimension", config.dimension)
            self.tab_widget.addTab(tab, title)
            generated += 1

        if generated == 0:
            QtWidgets.QMessageBox.information(
                self,
                "Strain Plot Explorer",
                "No complete data combinations were available for plotting.",
            )
        else:
            self._append_log(
                "Finished generating Matplotlib plots. Use the tabs above to review them."
            )
        self._update_fullscreen_state()

    @staticmethod
    def _draw_scatter(ax, subset: pd.DataFrame, labels: Tuple[str, ...]) -> None:
        xs = subset[labels[0]].to_numpy(dtype=float)
        ys = subset[labels[1]].to_numpy(dtype=float)
        labels_text = subset["Microwire"].tolist()

        if xs.max() != xs.min():
            norm = (xs - xs.min()) / (xs.max() - xs.min())
        else:
            norm = [0.5] * len(xs)
        colors = [tuple(rgba) for rgba in cm.viridis(norm)]

        if len(labels) == 3:
            zs = subset[labels[2]].to_numpy(dtype=float)
            ax.scatter(xs, ys, zs, c=colors, s=60, depthshade=True)
            for x, y, z, label_text in zip(xs, ys, zs, labels_text):
                ax.text(x, y, z, label_text, fontsize=9)
        else:
            ax.scatter(xs, ys, c=colors, s=60)
            for x, y, label_text in zip(xs, ys, labels_text):
                ax.text(x, y, label_text, fontsize=9)

        ax.set_xlabel(labels[0])
        ax.set_ylabel(labels[1])
        if len(labels) == 3:
            ax.set_zlabel(labels[2])
        ax.set_title(" vs ".join(labels))

    def _export_origin(self, plot_df: pd.DataFrame, configs: List[PlotConfig]) -> None:
        try:
            with origin_session() as op:
                schedule_origin_release()
                exported = 0
                for config in configs:
                    subset = plot_df[["Microwire", *config.labels]].dropna()
                    if subset.empty:
                        continue
                    try:
                        self._build_origin_graph(op, subset, config)
                        exported += 1
                    except Exception as exc:  # pragma: no cover - Origin specific
                        self._append_log(f"Origin plot failed for {' vs '.join(config.labels)}: {exc}")
                if exported:
                    self._append_log(
                        f"Sent {exported} scatter combinations to Origin."
                    )
                else:
                    self._append_log(
                        "No Origin plots were generated because every combination was empty."
                    )
        except (ModuleNotFoundError, ImportError):
            self._append_log(
                "OriginPro is not installed. Install the originpro package to enable Origin output."
            )
        except Exception as exc:  # pragma: no cover - Origin specific
            self._append_log(f"Unexpected Origin error: {exc}")

    def _build_origin_graph(
        self,
        origin_any,
        subset: pd.DataFrame,
        config: PlotConfig,
    ) -> None:
        title = " vs ".join(config.labels)
        safe_title = self._escape_origin_text(title)

        book = origin_any.new_book('w', lname=self._origin_book_name(config.labels))
        book.activate()
        sheet = book[0]

        data = subset[list(config.labels)].astype(float).copy()
        if config.dimension == 3:
            data.columns = ["X", "Y", "Z"]
        else:
            data.columns = ["X", "Y"]
        sheet.from_df(data)
        try:
            sheet.cols_axis('XYZ' if config.dimension == 3 else 'XY')
        except Exception:
            pass

        try:
            sheet.add_cols(1)
            sheet.from_list(data.shape[1], subset["Microwire"].tolist())
            sheet.set_label(data.shape[1], "Microwire")
        except Exception:
            pass

        book.activate()
        if config.dimension == 3:
            origin_any.lt_exec('worksheet -s 0 0 -1 2; worksheet -t plot3d scatter;')
            origin_any.lt_exec('page.antialias=1; layer -aa 1;')
        else:
            origin_any.lt_exec('worksheet -s 0 0 -1 1; worksheet -t plot scatter;')
            origin_any.lt_exec('page.antialias=1;')
        origin_any.lt_exec(f'title -s "{safe_title}";')

    def _origin_book_name(self, combo: Tuple[str, ...]) -> str:
        label = "_".join(combo)
        sanitized = "".join(ch if ch.isalnum() else "_" for ch in label)
        return f"Strain3D_{sanitized[:30]}"

    def _escape_origin_text(self, text: str) -> str:
        return text.replace("\"", "''")


class PlotWindow(QtWidgets.QMainWindow):
    """Floating window for reviewing a Matplotlib scatter plot full screen."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        title: str,
        subset: pd.DataFrame,
        combo: Tuple[str, ...],
        dimension: int,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{title} — Strain Plot Explorer")
        self._subset = subset.copy()
        self._combo = combo
        self._dimension = dimension

        canvas = FigureCanvas(Figure(figsize=(13.0, 9.5)))
        self.setCentralWidget(canvas)

        fig = canvas.figure
        fig.clear()
        if self._dimension == 3:
            ax = fig.add_subplot(111, projection="3d")
        else:
            ax = fig.add_subplot(111)
        Strain3DPlotter._draw_scatter(ax, self._subset, self._combo)
        fig.tight_layout()


def main() -> QtWidgets.QWidget | None:  # pragma: no cover - thin launcher wrapper
    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        created_app = True
    ensure_app_theme(app)
    widget = Strain3DPlotter()
    widget.show()
    if created_app:
        app.exec()
        return None
    return widget


if __name__ == "__main__":  # pragma: no cover - manual launch
    main()
