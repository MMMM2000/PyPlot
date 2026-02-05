from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import pandas as pd
from PyQt6 import QtWidgets

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api
from plotting.shared.origin import origin_session

from .core import FmrParseResult, parse_fmr_csv, select_fmr_axes


@dataclass
class FmrEntry:
    path: Path
    sample: str
    frame: pd.DataFrame
    units: Dict[str, str]


@register_plugin("FMR")
class FmrPlugin(PyPlotPlugin):
    """PyPlot integration for FMR CSV files."""

    requires_imported_data = True
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._dataset: List[FmrEntry] = []
        self._plot_tabs: List[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._markers_checkbox: QtWidgets.QCheckBox | None = None
        self._combine_x_checkbox: QtWidgets.QCheckBox | None = None
        self._loaded_paths: List[Path] = []

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QtWidgets.QLabel(
            "Import FMR CSV files, then plot Field vs X/Y voltage traces."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        layout.addStretch(1)
        self._summary_label = summary
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        if self._settings_widget is not None:
            return self._settings_widget

        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        window_module = window_api()
        options_section, options_layout = window_module.create_toolbar_section(
            "Plot options",
            parent=container,
        )
        self._combine_x_checkbox = QtWidgets.QCheckBox("Plot all samples (X only)", options_section)
        self._combine_x_checkbox.setChecked(False)
        options_layout.addWidget(self._combine_x_checkbox)
        self._markers_checkbox = QtWidgets.QCheckBox("Show markers", options_section)
        self._markers_checkbox.setChecked(False)
        options_layout.addWidget(self._markers_checkbox)
        options_layout.addStretch(1)
        layout.addWidget(options_section)
        layout.addStretch(1)

        self._settings_widget = container
        return container

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return
        entries: List[FmrEntry] = []
        for path in paths:
            try:
                parsed = parse_fmr_csv(Path(path))
            except Exception as exc:
                self._log(f"Failed to parse {Path(path).name}: {exc}", level="error")
                continue
            frame = parsed.frame
            if frame.empty:
                self._log(f"No FMR data found in {Path(path).name}.", level="error")
                continue
            sample = Path(path).stem
            entries.append(FmrEntry(path=Path(path), sample=sample, frame=frame, units=parsed.units))

        self._dataset = entries
        self._data = entries
        self._loaded_paths = list(paths)
        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent
        self._log(f"Loaded {len(entries)} FMR file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._dataset:
            self.load_data()
        if not self._dataset:
            return

        self._clear_tabs()
        window_module = window_api()
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

        show_markers = bool(
            self._markers_checkbox.isChecked() if self._markers_checkbox is not None else False
        )
        combine_x = bool(
            self._combine_x_checkbox.isChecked() if self._combine_x_checkbox is not None else False
        )

        if combine_x:
            self._plot_combined_x(show_markers)
            self._set_tab_bar_visible(False)
            return

        for entry in self._dataset:
            fig = Figure(figsize=(8.5, 5))
            ax = fig.add_subplot(111)
            columns = [str(col) for col in entry.frame.columns]
            field_col, x_col, y_col = select_fmr_axes(columns)
            if not field_col or not x_col or not y_col:
                self._log(
                    f"Missing Field/X/Y columns in {entry.path.name}; available: {columns}",
                    level="error",
                )
                continue
            data = entry.frame[[field_col, x_col, y_col]].apply(pd.to_numeric, errors="coerce")
            data = data.dropna(how="any")
            if data.empty:
                self._log(f"No numeric data in {entry.path.name}.", level="error")
                continue
            marker = "o" if show_markers else None
            ax.plot(
                data[field_col],
                data[x_col],
                color="#111111",
                linewidth=1.2,
                marker=marker,
                label="X",
            )
            ax.plot(
                data[field_col],
                data[y_col],
                color="#dc2626",
                linewidth=1.2,
                marker=marker,
                label="Y",
            )
            x_unit = entry.units.get(field_col, "Oe")
            y_unit = entry.units.get(x_col) or entry.units.get(y_col) or "V"
            ax.set_title(entry.sample)
            axis_field = "Field" if "field" in field_col.lower() else field_col
            ax.set_xlabel(f"{axis_field} [{x_unit}]" if x_unit else axis_field)
            ax.set_ylabel(f"X [{y_unit}]" if y_unit else "X")
            ax.legend(loc="best")
            fig.tight_layout()
            tab = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            canvas = FigureCanvas(fig)
            canvas.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            layout.addWidget(canvas)
            descriptor = window_module.TabDescriptor(
                kind="fmr",
                title=f"{entry.sample} - FMR",
                root_label=entry.sample,
                x_label=ax.get_xlabel(),
                y_label=ax.get_ylabel(),
                canvas=canvas,
                axes=ax,
                lines={},
                metadata={"sample": entry.sample, "path": str(entry.path)},
            )
            index = self.host.tab_widget.addTab(tab, descriptor.root_label or "Plot")
            setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
            if callable(setter):
                setter(index)
            self.host._register_plot_tab(tab, canvas, ax, descriptor)
            self._plot_tabs.append(tab)
        self._set_tab_bar_visible(False)

    def _plot_combined_x(self, show_markers: bool) -> None:
        window_module = window_api()
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

        fig = Figure(figsize=(8.5, 5))
        ax = fig.add_subplot(111)
        marker = "o" if show_markers else None
        base_labels = None
        mismatch_logged = False
        plotted = 0

        for entry in self._dataset:
            columns = [str(col) for col in entry.frame.columns]
            field_col, x_col, _y_col = select_fmr_axes(columns)
            if not field_col or not x_col:
                self._log(
                    f"Missing Field/X columns in {entry.path.name}; available: {columns}",
                    level="error",
                )
                continue
            data = entry.frame[[field_col, x_col]].apply(pd.to_numeric, errors="coerce")
            data = data.dropna(how="any")
            if data.empty:
                self._log(f"No numeric data in {entry.path.name}.", level="error")
                continue
            if base_labels is None:
                x_unit = entry.units.get(field_col, "Oe")
                y_unit = entry.units.get(x_col, "V")
                axis_field = "Field" if "field" in field_col.lower() else field_col
                ax.set_xlabel(f"{axis_field} [{x_unit}]" if x_unit else axis_field)
                ax.set_ylabel(f"X [{y_unit}]" if y_unit else "X")
                base_labels = (field_col, x_col, x_unit, y_unit, entry.sample)
            elif not mismatch_logged:
                base_field, base_x, base_x_unit, base_y_unit, base_sample = base_labels
                if (
                    field_col != base_field
                    or x_col != base_x
                    or entry.units.get(field_col, "Oe") != base_x_unit
                    or entry.units.get(x_col, "V") != base_y_unit
                ):
                    self._log(
                        f"Combined plot axis labels follow {base_sample}; {entry.sample} uses {field_col}/{x_col}.",
                        level="warning",
                    )
                    mismatch_logged = True
            ax.plot(
                data[field_col],
                data[x_col],
                linewidth=1.2,
                marker=marker,
                label=entry.sample,
            )
            plotted += 1

        if plotted == 0:
            self._log("No FMR data available for combined X plot.", level="error")
            return

        ax.set_title("FMR X (All Samples)")
        ax.legend(loc="best")
        fig.tight_layout()

        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        canvas = FigureCanvas(fig)
        canvas.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(canvas)
        descriptor = window_module.TabDescriptor(
            kind="fmr",
            title="FMR X - All Samples",
            root_label="FMR X (All)",
            x_label=ax.get_xlabel(),
            y_label=ax.get_ylabel(),
            canvas=canvas,
            axes=ax,
            lines={},
            metadata={"mode": "combined_x"},
        )
        index = self.host.tab_widget.addTab(tab, descriptor.root_label or "Plot")
        setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
        if callable(setter):
            setter(index)
        self.host._register_plot_tab(tab, canvas, ax, descriptor)
        self._plot_tabs.append(tab)

    def open_origin(self) -> None:  # type: ignore[override]
        if not self._dataset:
            self.load_data()
        if not self._dataset:
            return
        try:
            with origin_session(keep_open=True) as op:
                exported = 0
                show_markers = bool(
                    self._markers_checkbox.isChecked() if self._markers_checkbox is not None else False
                )
                marker_size = 6.0 if show_markers else 0.0
                for entry in self._dataset:
                    columns = [str(col) for col in entry.frame.columns]
                    field_col, x_col, y_col = select_fmr_axes(columns)
                    if not field_col or not x_col or not y_col:
                        continue
                    data = entry.frame[[field_col, x_col, y_col]].apply(
                        pd.to_numeric, errors="coerce"
                    )
                    data = data.dropna(how="any")
                    if data.empty:
                        continue
                    try:
                        book = op.new_book("w")
                    except Exception:
                        continue
                    try:
                        book.lname = entry.sample
                    except Exception:
                        pass
                    sheet = book[0] if len(book) else book.add_sheet()
                    sheet.name = "Data"
                    sheet.from_df(data)
                    try:
                        sheet.header_rows("LUC")
                    except Exception:
                        pass
                    try:
                        sheet.cols_axis("XYY")
                    except Exception:
                        pass
                    labels = {
                        field_col: field_col,
                        x_col: x_col,
                        y_col: y_col,
                    }
                    units = {
                        field_col: entry.units.get(field_col, "Oe"),
                        x_col: entry.units.get(x_col, "V"),
                        y_col: entry.units.get(y_col, "V"),
                    }
                    comments = {
                        x_col: "X",
                        y_col: "Y",
                    }
                    for idx, name in enumerate([field_col, x_col, y_col]):
                        try:
                            sheet.set_label(idx, labels.get(name, name), "L")
                        except Exception:
                            pass
                        unit = units.get(name)
                        if unit:
                            try:
                                sheet.set_label(idx, unit, "U")
                            except Exception:
                                pass
                        comment = comments.get(name)
                        if comment:
                            try:
                                sheet.set_label(idx, comment, "C")
                            except Exception:
                                pass
                    graph = op.new_graph(template="line")
                    layer = graph[0] if graph else None
                    if layer is None:
                        continue
                    try:
                        plot_x = layer.add_plot(sheet, coly=1, colx=0, type="y")
                        plot_y = layer.add_plot(sheet, coly=2, colx=0, type="y")
                    except Exception:
                        continue
                    try:
                        layer.rescale()
                    except Exception:
                        pass
                    graph_title = entry.sample
                    try:
                        graph.set_str("title", graph_title)
                        graph.name = self._origin_graph_name(graph_title)
                        graph.lname = f"{entry.sample} - FMR"
                    except Exception:
                        pass
                    try:
                        graph.activate()
                        safe_title = graph_title.replace('"', "'")
                        op.lt_exec(f'title -s "{safe_title}";')
                    except Exception:
                        pass
                    for plot_obj, color in ((plot_x, "#111111"), (plot_y, "#dc2626")):
                        if plot_obj is None:
                            continue
                        try:
                            plot_obj.color = color
                        except Exception:
                            pass
                        try:
                            plot_obj.line_width = 1.2
                        except Exception:
                            pass
                        if show_markers:
                            try:
                                plot_obj.symbol_shape = 2
                                plot_obj.symbol_size = marker_size
                                plot_obj.symbol_edge_color = color
                                plot_obj.symbol_fill_color = color
                            except Exception:
                                pass
                        else:
                            try:
                                plot_obj.symbol_size = 0
                                plot_obj.symbol_shape = 0
                            except Exception:
                                pass
                    exported += 1
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to send data to Origin:\n{exc}")
            self._log(f"Origin export failed: {exc}", level="error")
            return
        self._log("Sent FMR data to Origin.")

    @staticmethod
    def _origin_graph_name(label: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in label).strip("_")
        return (cleaned or "FMR")[:18]

    def update_ui(self) -> None:  # type: ignore[override]
        has_data = bool(self._dataset)
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(has_data or self._host_has_data_selection())
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(False)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(has_data)
        if hasattr(self.host, "export_origin_button"):
            self.host.export_origin_button.setEnabled(False)
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(False)
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if self._summary_label is not None:
            if not has_data:
                self._summary_label.setText(
                    "Import FMR CSV files, then plot Field vs X/Y voltage traces."
                )
            else:
                self._summary_label.clear()
        self.host._update_project_actions()

    def _clear_tabs(self) -> None:
        if not self._plot_tabs:
            return
        host = self.host
        for tab in list(self._plot_tabs):
            remover = getattr(host, "_remove_tab_internal", None)
            if callable(remover):
                try:
                    remover(tab)
                    continue
                except Exception:
                    pass
            index = host.tab_widget.indexOf(tab)
            if index >= 0:
                host.tab_widget.removeTab(index)
        self._plot_tabs.clear()
        host._rebuild_object_manager_for_tab(host.tab_widget.currentWidget())

    def _set_tab_bar_visible(self, visible: bool) -> None:
        bar_getter = getattr(self.host.tab_widget, "tabBar", None)
        if callable(bar_getter):
            try:
                bar = bar_getter()
            except Exception:
                return
            try:
                bar.setVisible(visible)
                bar.setMaximumHeight(0 if not visible else 16777215)
                auto_hide = getattr(self.host.tab_widget, "setTabBarAutoHide", None)
                if callable(auto_hide):
                    try:
                        auto_hide(not visible)
                    except Exception:
                        pass
            except Exception:
                pass
