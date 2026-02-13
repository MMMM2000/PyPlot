from __future__ import annotations

import re
import re
from pathlib import Path
from typing import Any, Iterable, TYPE_CHECKING

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api
from . import core as temp_sens_core


def _format_units(units: str | None) -> str | None:
    if not units:
        return None
    value = str(units).strip()
    if not value:
        return None
    if value.startswith("[") and value.endswith("]"):
        return value
    return f"[{value}]"


def _variable_units(variable: str) -> str | None:
    label = temp_sens_core.TS_LABELS.get(variable)
    if not label:
        return None
    match = re.search(r"\\((.*?)\\)", label)
    return match.group(1).strip() if match else None


def _units_from_label(label: str | None) -> str | None:
    if not label:
        return None
    match = re.search(r"\(([^)]+)\)\s*$", label.strip())
    if match:
        return match.group(1).strip()
    return None

if TYPE_CHECKING:
    from plotting.pyplot.window import (
        GraphLineState,
        WorksheetColumnMeta,
        WorksheetData,
        WorkbookData,
        TabDescriptor,
    )


MODE_LABELS = {"none": "Raw", "zero_25": "Zero @25°C"}


@register_plugin("Temperature Sensitivity")
class TemperatureSensitivityPlugin(PyPlotPlugin):
    """Embed the temperature sensitivity workflow directly inside PyPlot."""

    requires_imported_data = True
    auto_load_on_import = True
    exposes_load_data = False
    uses_shared_plot_workbooks = False

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._data: pd.DataFrame | None = None
        self._loaded_files: list[str] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._panel_widget: QtWidgets.QWidget | None = None
        self._var_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._baseline_combo: QtWidgets.QComboBox | None = None
        self._include_continuous_checkbox: QtWidgets.QCheckBox | None = None
        self._med_spin: QtWidgets.QSpinBox | None = None
        self._ma_spin: QtWidgets.QSpinBox | None = None
        self._workbook_keys: dict[str, str] = {}
        self._managed_workbooks: set[str] = set()

    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        if self._panel_widget is not None:
            return self._panel_widget

        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        window_module = window_api()
        overview_section, overview_layout = window_module.create_toolbar_section("Overview", parent=container)
        summary = QtWidgets.QLabel(
            "Import temperature sensitivity files and plot to generate graphs and workbooks."
        )
        summary.setWordWrap(True)
        overview_layout.addWidget(summary)
        overview_layout.addStretch(1)
        layout.addWidget(overview_section)
        self._summary_label = summary

        layout.addStretch(1)
        self._panel_widget = container
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        if self._settings_widget is not None:
            return self._settings_widget
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        window_module = window_api()
        var_section, var_layout = window_module.create_toolbar_section("Variables to plot", parent=container)
        for key, label in temp_sens_core.TS_LABELS.items():
            checkbox = QtWidgets.QCheckBox(label, var_section)
            checkbox.setChecked(key in temp_sens_core.PLOT_VARS)
            self._var_checks[key] = checkbox
            var_layout.addWidget(checkbox)
        var_layout.addStretch(1)
        layout.addWidget(var_section)

        def _form_layout(parent: QtWidgets.QWidget) -> QtWidgets.QFormLayout:
            form = QtWidgets.QFormLayout(parent)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(4)
            form.setFieldGrowthPolicy(
                QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            return form

        baseline_section, baseline_layout = window_module.create_toolbar_section(
            "Baseline options",
            parent=container,
            layout_factory=_form_layout,
        )
        baseline_combo = QtWidgets.QComboBox(baseline_section)
        baseline_combo.addItem("Do not shift baseline", "none")
        baseline_combo.addItem("Shift to zero at 25°C", "zero_25")
        baseline_combo.addItem("Plot both baselines", "both")
        baseline_value = (
            temp_sens_core.BASELINE_MODE
            if temp_sens_core.BASELINE_MODE in {"none", "zero_25", "both"}
            else "none"
        )
        baseline_combo.setCurrentIndex({"none": 0, "zero_25": 1, "both": 2}[baseline_value])
        self._baseline_combo = baseline_combo
        baseline_layout.addRow("Baseline mode:", baseline_combo)

        include_box = QtWidgets.QCheckBox("Include continuous sweeps", baseline_section)
        include_box.setChecked(bool(temp_sens_core.INCLUDE_CONTINUOUS))
        self._include_continuous_checkbox = include_box
        baseline_layout.addRow(include_box)
        layout.addWidget(baseline_section)

        smoothing_section, smoothing_layout = window_module.create_toolbar_section(
            "Smoothing",
            parent=container,
            layout_factory=_form_layout,
        )
        med_spin = QtWidgets.QSpinBox(smoothing_section)
        med_spin.setRange(1, 9999)
        med_spin.setValue(int(temp_sens_core.MED_WINDOW))
        self._med_spin = med_spin
        smoothing_layout.addRow("Median window:", med_spin)

        ma_spin = QtWidgets.QSpinBox(smoothing_section)
        ma_spin.setRange(1, 9999)
        ma_spin.setValue(int(temp_sens_core.MA_WINDOW))
        self._ma_spin = ma_spin
        smoothing_layout.addRow("Moving average window:", ma_spin)
        layout.addWidget(smoothing_section)

        layout.addStretch(1)
        self._settings_widget = container
        return container

    def plot_action_label(self) -> str:  # type: ignore[override]
        return "Plot Temperature Sensitivity"

    def _has_loaded_data(self) -> bool:
        if self._data is None:
            return False
        if isinstance(self._data, pd.DataFrame):
            return not self._data.empty
        return True

    def _selected_variables(self) -> list[str]:
        selected = [key for key, cb in self._var_checks.items() if cb.isChecked() and cb.isEnabled()]
        if selected:
            return selected
        fallback = list(getattr(temp_sens_core, "PLOT_VARS", []))
        if not fallback:
            fallback = list(temp_sens_core.TS_LABELS.keys())
        return fallback

    def _gather_config(self, *, apply_to_core: bool = False) -> dict[str, Any]:
        vars_selected = self._selected_variables()
        baseline_value = "none"
        if isinstance(self._baseline_combo, QtWidgets.QComboBox):
            baseline_value = self._baseline_combo.currentData() or "none"
        if baseline_value not in {"none", "zero_25", "both"}:
            baseline_value = "none"
        include_cont = bool(self._include_continuous_checkbox and self._include_continuous_checkbox.isChecked())
        med_window = temp_sens_core.MED_WINDOW
        if isinstance(self._med_spin, QtWidgets.QSpinBox):
            med_window = int(self._med_spin.value())
        ma_window = temp_sens_core.MA_WINDOW
        if isinstance(self._ma_spin, QtWidgets.QSpinBox):
            ma_window = int(self._ma_spin.value())
        if apply_to_core:
            temp_sens_core.PLOT_VARS = list(vars_selected)
            temp_sens_core.BASELINE_MODE = baseline_value
            temp_sens_core.INCLUDE_CONTINUOUS = include_cont
            temp_sens_core.MED_WINDOW = med_window
            temp_sens_core.MA_WINDOW = ma_window
            temp_sens_core.SAVE_PLOTS = False
            temp_sens_core.OUTPUT_DIR = str(temp_sens_core.OUTPUT_DIR)
            temp_sens_core.SHOW_PLOTS = False
            temp_sens_core.BACKEND = "matplotlib"
        return {
            "variables": list(vars_selected),
            "baseline_mode": baseline_value,
            "include_continuous": include_cont,
            "save": False,
            "output_dir": "",
            "med_window": med_window,
            "ma_window": ma_window,
        }

    def _apply_settings_to_core(self) -> dict[str, Any]:
        return self._gather_config(apply_to_core=True)

    def load_data(self) -> None:  # type: ignore[override]
        host = self.host

        paths = host.ensure_data_selection(self, warn_on_missing=True)
        if not paths:
            return
        valid_paths: list[Path] = []
        skipped: list[str] = []
        for path in paths:
            if temp_sens_core.parse_metadata(path.stem) is None:
                skipped.append(path.name)
                continue
            valid_paths.append(path)
        if skipped:
            self._log(
                "Ignoring files that do not follow the temperature sensitivity naming pattern:\n"
                + ", ".join(skipped)
            )
        if not valid_paths:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "None of the selected files match the temperature sensitivity format.",
            )
            return
        if hasattr(host, "_commit_selected_paths"):
            try:
                host._commit_selected_paths(valid_paths)  # type: ignore[attr-defined]
            except Exception:
                pass
        string_paths = [str(path) for path in valid_paths]
        try:
            self._data = temp_sens_core.load_data(string_paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to load data:\n{exc}")
            self._data = None
            return
        self._loaded_files = string_paths
        if valid_paths:
            self.host._plugin_last_directories[self.name] = valid_paths[0].parent
        if self._summary_label is not None:
            self._summary_label.setText(
                "Data loaded. Click Plot Temperature Sensitivity to generate graphs and workbooks."
            )
        self._log(
            "Loaded {count} temperature sensitivity file(s): {names}".format(
                count=len(valid_paths),
                names=", ".join(path.name for path in valid_paths),
            )
        )
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if self._data is None:
            self.load_data()
        if self._data is None:
            return
        window_module = window_api()
        config = self._apply_settings_to_core()
        dataframe = temp_sens_core.maybe_handle_outliers(self._data.copy())
        clear = getattr(self.host, "_clear_tab_list", None)
        if callable(clear):
            clear(self._plot_tabs)
        else:
            for tab in self._plot_tabs:
                index = self.host.tab_widget.indexOf(tab)
                if index >= 0:
                    self.host.tab_widget.removeTab(index)
        self._plot_tabs.clear()
        modes = [config["baseline_mode"]]
        if config["baseline_mode"] == "both":
            modes = ["none", "zero_25"]
        mode_labels = {"none": "Raw", "zero_25": "Zero @25°C"}
        plots_created = 0
        grouped = dataframe.groupby(["composition", "anneal"], dropna=False)
        include_cont = config["include_continuous"]
        active_workbooks: set[str] = set()
        for (_, _), group in grouped:
            for variable in config["variables"]:
                for mode in modes:
                    ctx = temp_sens_core.build_temperature_graph_context(
                        group,
                        variable,
                        baseline_mode=mode,
                        include_cont=include_cont,
                        med_window=config["med_window"],
                        ma_window=config["ma_window"],
                    )
                    if ctx is None:
                        continue
                    try:
                        fig, fname = temp_sens_core.plot_variable(
                            group,
                            variable,
                            config["save"],
                            config["output_dir"],
                            baseline_mode=mode,
                            include_cont=config["include_continuous"],
                            med_window=config["med_window"],
                            ma_window=config["ma_window"],
                        )
                    except Exception as exc:
                        self._log(f"Failed to plot {variable} ({mode}): {exc}", level="error")
                        continue
                    if config["baseline_mode"] == "both":
                        path_obj = Path(fname)
                        saved_name = f"{path_obj.stem}_{mode}{path_obj.suffix}"
                    else:
                        saved_name = fname
                    canvas = FigureCanvas(fig)
                    canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
                    canvas.setMinimumSize(640, 360)
                    canvas.setSizePolicy(
                        QtWidgets.QSizePolicy.Policy.Expanding,
                        QtWidgets.QSizePolicy.Policy.Expanding,
                    )
                    tab = QtWidgets.QWidget()
                    tab_layout = QtWidgets.QVBoxLayout(tab)
                    tab_layout.setContentsMargins(0, 0, 0, 0)
                    tab_layout.addWidget(canvas, 1)
                    tab_layout.setStretch(tab_layout.indexOf(canvas), 1)
                    ax = fig.axes[0] if fig.axes else None
                    title = ax.get_title() if ax else variable
                    x_label = ax.get_xlabel() if ax else "Temperature"
                    y_label = ax.get_ylabel() if ax else variable
                    tab_label = temp_sens_core.TS_LABELS.get(variable, variable)
                    if config["baseline_mode"] == "both":
                        tab_label = f"{tab_label} ({mode_labels.get(mode, mode)})"
                    lines: dict[tuple[str, float | str], GraphLineState] = {}
                    if ax is not None:
                        for index, line in enumerate(ax.get_lines(), start=1):
                            label = line.get_label() or f"Series {index}"
                            state = window_module.GraphLineState(
                                key=(label, float(index)),
                                label=label,
                                line=line,
                                base_x=line.get_xdata(),
                                base_y=line.get_ydata(),
                            )
                            lines[state.key] = state
                    metadata = {
                        "variable": variable,
                        "baseline_mode": mode,
                        "source_files": list(self._loaded_files),
                        "saved_path": saved_name if config["save"] else "",
                    }
                    if not group.empty:
                        row0 = group.iloc[0]
                        metadata.update({
                            "composition": row0.get("composition", ""),
                            "anneal": row0.get("anneal", ""),
                        })
                    descriptor = window_module.TabDescriptor(
                        kind="temperature_sensitivity",
                        title=title,
                        root_label=tab_label,
                        x_label=x_label,
                        y_label=y_label,
                        canvas=canvas,
                        axes=ax,
                        lines=lines,
                        metadata=metadata,
                    )
                    self.host.tab_widget.addTab(tab, tab_label)
                    self.host._register_plot_tab(tab, canvas, ax, descriptor)
                    self._plot_tabs.append(tab)
                    plots_created += 1
                    workbook_key = self._register_workbook_for_context(
                        ctx,
                        variable,
                        mode,
                        include_cont,
                    )
                    if workbook_key:
                        active_workbooks.add(workbook_key)
        if self._plot_tabs:
            first_tab = self._plot_tabs[0]
            index = self.host.tab_widget.indexOf(first_tab)
            if index >= 0:
                self.host.tab_widget.setCurrentIndex(index)
        self._log(f"Generated {plots_created} temperature sensitivity plot(s).")
        finalize = getattr(self, "_finalize_workbooks", None)
        if callable(finalize):
            finalize(active_workbooks)
        self.update_ui()


    def _format_workbook_name(
        self,
        composition: str,
        anneal: str,
        title: str,
        variable: str,
        mode: str,
    ) -> str:
        label = temp_sens_core.TS_LABELS.get(variable, variable)
        base_parts = [composition.strip(), anneal.strip()]
        head = " ".join(part for part in base_parts if part)
        if title and title not in base_parts:
            head = f"{head} {title}".strip()
        mode_label = MODE_LABELS.get(mode, mode)
        name = f"{head} - {label}".strip() if head else label
        if mode_label:
            name = f"{name} ({mode_label})"
        return name.strip()

    def _build_graph_workbook(
        self,
        window_module: Any,
        workbook: "WorkbookData",
        ctx: temp_sens_core.TemperatureGraphContext,
        variable: str,
        mode: str,
        include_cont: bool,
    ) -> list["WorksheetData"]:
        host = self.host
        units = _format_units(_variable_units(variable))
        mode_label = MODE_LABELS.get(mode, mode)
        raw_columns = [
            "sample",
            "sample_label",
            "sample_idx",
            "temp",
            "temp_label",
            "legend_label",
            "X",
            "Y",
            "value",
            "baseline",
            "composition",
            "anneal",
        ]
        raw_frame = ctx.raw_points[[col for col in raw_columns if col in ctx.raw_points.columns]].copy()
        raw_frame.rename(
            columns={
                "sample": "sample_id",
                "sample_label": "sample_label",
                "sample_idx": "sample_index",
                "temp": "temperature_c",
                "temp_label": "temperature_label",
                "legend_label": "series",
                "X": "x_position",
                "Y": "plotted_value",
                "value": "raw_value",
                "baseline": "baseline_value",
            },
            inplace=True,
        )

        mean_columns = [
            "sample",
            "sample_label",
            "sample_idx",
            "temp",
            "temp_label",
            "legend_label",
            "plot_x",
            "plot_y",
            "composition",
            "anneal",
        ]
        mean_frame = ctx.mean_points[[col for col in mean_columns if col in ctx.mean_points.columns]].copy()
        mean_frame.rename(
            columns={
                "sample": "sample_id",
                "sample_label": "sample_label",
                "sample_idx": "sample_index",
                "temp": "temperature_c",
                "temp_label": "temperature_label",
                "legend_label": "series",
                "plot_x": "x_position",
                "plot_y": "plotted_value",
            },
            inplace=True,
        )

        sheets: list["WorksheetData"] = []
        raw_sheet = host._create_worksheet_from_frame(workbook, "Raw points", raw_frame)
        self._apply_column_meta(
            raw_sheet,
            {
                "sample_id": ("Sample", None, "Sample identifier", ""),
                "sample_label": ("Sample label", None, "Formatted label used in plots", ""),
                "sample_index": ("Sample index", None, "Numeric index assigned per sample", ""),
                "temperature_c": ("Temperature", _format_units("°C"), "Discrete measurement temperature", ""),
                "temperature_label": ("Temperature label", None, "Display label for temperature", ""),
                "series": ("Legend entry", None, "Legend label for this series", ""),
                "x_position": ("X position", None, "Jittered position used for plotting", "X"),
                "plotted_value": (f"{ctx.var_label} ({mode_label})", units, "Value sent to the graph", "Y"),
                "raw_value": (f"{ctx.var_label} (raw)", units, "Original measurement value", ""),
                "baseline_value": (f"{ctx.var_label} baseline", units, "Mean at 25°C per sample", ""),
                "composition": ("Composition", None, "Alloy composition", ""),
                "anneal": ("Anneal", None, "Annealing condition", ""),
            },
            window_module,
        )
        sheets.append(raw_sheet)

        mean_sheet = host._create_worksheet_from_frame(workbook, "Means", mean_frame)
        self._apply_column_meta(
            mean_sheet,
            {
                "sample_id": ("Sample", None, "Sample identifier", ""),
                "sample_label": ("Sample label", None, "Formatted label used in plots", ""),
                "sample_index": ("Sample index", None, "Numeric index assigned per sample", ""),
                "temperature_c": ("Temperature", _format_units("°C"), "Discrete measurement temperature", ""),
                "temperature_label": ("Temperature label", None, "Display label for temperature", ""),
                "series": ("Legend entry", None, "Legend label for this series", ""),
                "x_position": ("X position", None, "Position assigned to the mean marker", "X"),
                "plotted_value": (f"{ctx.var_label} mean", units, "Mean value per temperature", "Y"),
                "composition": ("Composition", None, "Alloy composition", ""),
                "anneal": ("Anneal", None, "Annealing condition", ""),
            },
            window_module,
        )
        sheets.append(mean_sheet)

        if include_cont and ctx.cont_series:
            cont_frame = pd.concat(ctx.cont_series, ignore_index=True)
            cont_frame.rename(columns={"X": "x_position", "Y": "plotted_value"}, inplace=True)
            cont_sheet = host._create_worksheet_from_frame(workbook, "Continuous", cont_frame)
            self._apply_column_meta(
                cont_sheet,
                {
                    "sample": ("Sample", None, "Sample identifier", ""),
                    "x_position": ("X position", None, "Mapped temperature axis for continuous data", "X"),
                    "plotted_value": (f"{ctx.var_label} smoothed", units, "Median/MA smoothed continuous series", "Y"),
                },
                window_module,
            )
            sheets.append(cont_sheet)

        annotation_rows: list[dict[str, float | str]] = []
        for sample, label in zip(ctx.samples, ctx.display_samples):
            annotation_rows.append(
                {
                    "type": "sample_label",
                    "sample": sample,
                    "label": label,
                    "x": ctx.sample_label_positions.get(sample, float(ctx.sample_idx.get(sample, 0))),
                    "y": ctx.axis.tick_level,
                }
            )
        for x_pos, y_pos, text in ctx.delta_labels:
            annotation_rows.append({"type": "delta_label", "label": text, "x": x_pos, "y": y_pos})
        if annotation_rows:
            annotations = pd.DataFrame(annotation_rows)
            anno_sheet = host._create_worksheet_from_frame(workbook, "Annotations", annotations)
            self._apply_column_meta(
                anno_sheet,
                {
                    "type": ("Kind", None, "Annotation type (sample label or delta)", ""),
                    "sample": ("Sample", None, "Related sample (labels only)", ""),
                    "label": ("Label", None, "Displayed annotation text", ""),
                    "x": ("X position", None, "Annotation X coordinate", "X"),
                    "y": ("Y position", None, "Annotation Y coordinate", "Y"),
                },
                window_module,
            )
            sheets.append(anno_sheet)

        return sheets

    def _apply_column_meta(
        self,
        worksheet: "WorksheetData" | None,
        meta_map: dict[str, tuple[str, str | None, str, str]],
        window_module: Any,
    ) -> None:
        if worksheet is None:
            return
        for column, spec in meta_map.items():
            meta = worksheet.columns.get(column)
            if not isinstance(meta, window_module.WorksheetColumnMeta):
                continue
            if len(spec) == 2:
                long_name, units_text = spec
                comments = ""
                formula = ""
            else:
                long_name, units_text, comments, formula = spec
            meta.long_name = long_name
            meta.units = units_text
            meta.comments = comments
            meta.formula = formula

    def _register_workbook_for_context(
        self,
        ctx: temp_sens_core.TemperatureGraphContext,
        variable: str,
        mode: str,
        include_cont: bool,
    ) -> str | None:
        host = self.host
        window_module = window_api()
        graph_id = f"{ctx.comp}|{ctx.anneal}|{variable}|{mode}"
        key = self._workbook_keys.get(graph_id)
        if not key:
            safe_id = temp_sens_core._sanitise_stem(ctx.comp, ctx.anneal, variable, mode)  # type: ignore[attr-defined]
            key = f"temperature_sensitivity::{safe_id}"
            self._workbook_keys[graph_id] = key
        workbook = window_module.WorkbookData(
            key=key,
            name=self._format_workbook_name(ctx.comp, ctx.anneal, ctx.title, variable, mode),
            worksheets=[],
            source=None,
            folder=None,
        )
        worksheets = self._build_graph_workbook(
            window_module,
            workbook,
            ctx,
            variable,
            mode,
            include_cont,
        )
        if not worksheets:
            return None
        workbook.worksheets = [worksheet.key for worksheet in worksheets]
        host._register_imported_workbook(workbook, worksheets)
        try:
            ensure_workbooks = getattr(host, "_ensure_workbook_root", None)
            tree_root = ensure_workbooks() if callable(ensure_workbooks) else None
            if tree_root is not None:
                tree_root.setExpanded(True)
            node = host._data_workbook_items.get(workbook.key)
            if node is not None:
                parent = node.parent()
                if parent is not None:
                    parent.setExpanded(True)
                node.setExpanded(True)
        except Exception:
            pass
        return workbook.key

    def _finalize_workbooks(self, active_keys: set[str]) -> None:
        host = self.host
        stale = self._managed_workbooks - active_keys
        if stale:
            self._remove_managed_workbooks(stale)
        self._managed_workbooks = active_keys
        if active_keys or stale:
            host._refresh_imported_data_summary()
            host._sync_selected_paths_with_imports()

    def _remove_managed_workbooks(self, keys: Iterable[str]) -> None:
        host = self.host
        for key in keys:
            workbook = host._workbooks.get(key)
            if workbook is not None:
                for sheet_key in list(workbook.worksheets):
                    host._remove_worksheet(sheet_key)
            host._workbooks.pop(key, None)
            item = host._data_workbook_items.pop(key, None)
            if item is not None:
                parent = item.parent()
                if parent is not None:
                    index = parent.indexOfChild(item)
                    if index >= 0:
                        parent.takeChild(index)
            self._managed_workbooks.discard(key)
        cleanup = getattr(host, "_remove_workbook_root_if_empty", None)
        if callable(cleanup):
            cleanup()

    def open_origin(self) -> None:  # type: ignore[override]
        if not self._loaded_files:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Load temperature sensitivity data before exporting to Origin.",
            )
            return
        try:
            self._apply_settings_to_core()
            temp_sens_core.SHOW_PLOTS = False
            temp_sens_core.main(self._loaded_files, backend="origin")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to export to Origin:\n{exc}")
            self._log(f"Origin export failed: {exc}", level="error")
        else:
            self._log("Sent temperature sensitivity plots to Origin.")

    def update_ui(self) -> None:
        host = self.host
        has_data = self._has_loaded_data()
        ready_to_plot = has_data
        if hasattr(host, "plot_button"):
            host.plot_button.setEnabled(ready_to_plot)
        if self._summary_label is not None:
            if self._plot_tabs:
                self._summary_label.clear()
                self._summary_label.setVisible(False)
            else:
                self._summary_label.setVisible(True)
                if not ready_to_plot:
                    self._summary_label.setText(
                        "Import temperature sensitivity files, then click Plot Temperature Sensitivity."
                    )
                elif not has_data:
                    self._summary_label.setText(
                        "Click Plot Temperature Sensitivity to load data and build graphs/workbooks with the current settings."
                    )
                elif not self._summary_label.text().strip():
                    self._summary_label.setText(
                        "Data loaded. Adjust settings and click Plot Temperature Sensitivity to create graphs and workbooks."
                    )
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(bool(self._plot_tabs))
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(has_data)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(has_data)
        if hasattr(self.host, "popout_button"):
            self.host.popout_button.setEnabled(bool(self._plot_tabs))
        self.host._update_project_actions()
