from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api
from . import core as sens_core


def _format_units(units: str | None) -> str | None:
    if not units:
        return None
    value = units.strip()
    if not value:
        return None
    if value.startswith("[") and value.endswith("]"):
        return value
    return f"[{value}]"


def _variable_units(variable: str) -> str | None:
    label = sens_core.LABELS.get(variable)
    if not label:
        return None
    match = re.search(r"\((.*?)\)", label)
    return match.group(1).strip() if match else None


@register_plugin("Stress Sensitivity")
class StressSensitivityPlugin(PyPlotPlugin):
    """Embed the stress sensitivity workflow directly inside PyPlot."""

    requires_imported_data = True
    uses_shared_plot_workbooks = False

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._data: pd.DataFrame | None = None
        self._loaded_files: list[str] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._panel_widget: QtWidgets.QWidget | None = None
        self._var_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._include_dep_checkbox: QtWidgets.QCheckBox | None = None
        self._med_spin: QtWidgets.QSpinBox | None = None
        self._ma_spin: QtWidgets.QSpinBox | None = None
        stored_export = getattr(host, "_plugin_last_export_dirs", {}).get(name)
        self._last_export_dir: Path | None = stored_export if isinstance(stored_export, Path) else None
        self._workbook_keys: Dict[str, str] = {}
        self._managed_workbooks: set[str] = set()
    # Lifecycle -----------------------------------------------------
    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    # UI helpers ----------------------------------------------------
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
            "Select stress sensitivity files, load them, then generate plots."
        )
        summary.setWordWrap(True)
        overview_layout.addWidget(summary)
        overview_layout.addStretch(1)
        layout.addWidget(overview_section)
        layout.addStretch(1)
        self._summary_label = summary
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
        for key, label in sens_core.LABELS.items():
            checkbox = QtWidgets.QCheckBox(label, var_section)
            checkbox.setChecked(key in getattr(sens_core, "PLOT_VARS", []))
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

        dep_section, dep_layout = window_module.create_toolbar_section(
            "Stress dependence overlay",
            parent=container,
            layout_factory=_form_layout,
        )
        include_cb = QtWidgets.QCheckBox("Include processed dependence curve", dep_section)
        include_cb.setChecked(bool(getattr(sens_core, "INCLUDE_DEPENDENCE", False)))
        self._include_dep_checkbox = include_cb
        dep_layout.addRow(include_cb)

        med_spin = QtWidgets.QSpinBox(dep_section)
        med_spin.setRange(1, 9999)
        med_spin.setValue(int(getattr(sens_core, "MED_WINDOW", 5)))
        self._med_spin = med_spin
        dep_layout.addRow("Median window:", med_spin)

        ma_spin = QtWidgets.QSpinBox(dep_section)
        ma_spin.setRange(1, 9999)
        ma_spin.setValue(int(getattr(sens_core, "MA_WINDOW", 20)))
        self._ma_spin = ma_spin
        dep_layout.addRow("Moving average window:", ma_spin)
        layout.addWidget(dep_section)

        layout.addStretch(1)
        self._settings_widget = container
        return container

    # Behaviour -----------------------------------------------------
    def _selected_variables(self) -> list[str]:
        selected = [key for key, cb in self._var_checks.items() if cb.isChecked()]
        if selected:
            return selected
        return list(sens_core.LABELS.keys())

    def _gather_config(self, *, apply_to_core: bool = False) -> dict[str, Any]:
        variables = self._selected_variables()
        include_dep = bool(self._include_dep_checkbox and self._include_dep_checkbox.isChecked())
        med_window = sens_core.MED_WINDOW
        if isinstance(self._med_spin, QtWidgets.QSpinBox):
            med_window = int(self._med_spin.value())
        ma_window = sens_core.MA_WINDOW
        if isinstance(self._ma_spin, QtWidgets.QSpinBox):
            ma_window = int(self._ma_spin.value())
        if apply_to_core:
            sens_core.PLOT_VARS = list(variables)
            sens_core.PLOT_SUM = "sum" in variables
            sens_core.PLOT_DT = "dT" in variables
            sens_core.PLOT_T1 = "T1" in variables
            sens_core.PLOT_T2 = "T2" in variables
            sens_core.INCLUDE_DEPENDENCE = include_dep
            sens_core.MED_WINDOW = med_window
            sens_core.MA_WINDOW = ma_window
            sens_core.BACKEND = "matplotlib"
            sens_core.SHOW_PLOTS = False
            sens_core.SAVE_PLOTS = False
        return {
            "variables": list(variables),
            "save": False,
            "output_dir": "",
            "include_dep": include_dep,
            "med_window": med_window,
            "ma_window": ma_window,
        }

    def _apply_settings_to_core(self) -> dict[str, Any]:
        return self._gather_config(apply_to_core=True)

    def _clear_existing_tabs(self) -> None:
        if not self._plot_tabs:
            return
        clear = getattr(self.host, "_clear_tab_list", None)
        if callable(clear):
            clear(self._plot_tabs)
        else:
            for tab in self._plot_tabs:
                index = self.host.tab_widget.indexOf(tab)
                if index >= 0:
                    self.host.tab_widget.removeTab(index)
        self._plot_tabs.clear()

    def _register_workbooks(self, config: dict[str, Any]) -> None:
        data = self._data
        if data is None:
            return
        host = self.host
        window_module = window_api()
        active_keys: set[str] = set()
        variables = config.get("variables") or list(sens_core.LABELS.keys())
        grouped = data.groupby(["composition", "title", "anneal"], dropna=False)
        for (composition, title, anneal), group in grouped:
            try:
                table, _ = sens_core.build_workbook_table(group)
            except Exception as exc:
                self._log(f"Failed to prepare workbook data: {exc}", level="error")
                continue
            for variable in variables:
                raw_columns = [
                    "composition",
                    "title",
                    "anneal",
                    "sample_end",
                    "sample_label",
                    "filename",
                    "dir",
                    "load",
                    "line",
                    variable,
                    f"{variable}_relative",
                    f"baseline_{variable}",
                    f"delta_{variable}",
                ]
                available = [column for column in raw_columns if column in table.columns]
                if not available:
                    continue
                frame = table[available].copy()
                if frame.empty:
                    continue
                workbook_id = f"{composition}|{title}|{anneal}|{variable}"
                key = self._workbook_keys.get(workbook_id)
                if not key:
                    safe_id = sens_core._sanitise_stem(composition, title, anneal, variable)  # type: ignore[attr-defined]
                    key = f"stress_sensitivity::{safe_id}"
                    self._workbook_keys[workbook_id] = key
                label = sens_core.LABELS.get(variable, variable)
                workbook = window_module.WorkbookData(
                    key=key,
                    name=f"{composition} {anneal} - {label}",
                    worksheets=[],
                    source=None,
                    folder=None,
                )
                worksheet = host._create_worksheet_from_frame(workbook, "Processed data", frame)
                self._apply_column_meta(window_module, worksheet, variable)
                workbook.worksheets = [worksheet.key]
                host._register_imported_workbook(workbook, [worksheet])
                active_keys.add(workbook.key)
        stale = self._managed_workbooks - active_keys
        if stale:
            self._remove_managed_workbooks(stale)
        self._managed_workbooks = active_keys
        if active_keys or stale:
            host._refresh_imported_data_summary()
            host._sync_selected_paths_with_imports()

    def _apply_column_meta(
        self,
        window_module: Any,
        worksheet: "WorksheetData" | None,
        variable: str,
    ) -> None:
        if worksheet is None:
            return
        units = _format_units(_variable_units(variable))
        label = sens_core.LABELS.get(variable, variable)
        meta_map = {
            "composition": ("Composition", None),
            "title": ("Title", None),
            "anneal": ("Anneal", None),
            "sample_end": ("Sample end", None),
            "sample_label": ("Sample label", None),
            "filename": ("Source file", None),
            "dir": ("Direction", None),
            "load": ("Load", "g"),
            "line": ("Line", None),
            variable: (label, units),
            f"{variable}_relative": (f"{label} relative", units),
            f"baseline_{variable}": (f"{label} baseline", units),
            f"delta_{variable}": (f"{label} delta", units),
        }
        for column, (long_name, units_text) in meta_map.items():
            meta = worksheet.columns.get(column)
            if isinstance(meta, window_module.WorksheetColumnMeta):
                meta.long_name = long_name
                meta.units = units_text or ""
                if column.startswith("baseline_"):
                    meta.comments = f"Baseline at {sens_core.BASE_LOAD} g"
                elif column.startswith("delta_"):
                    meta.comments = f"Delta (end at {sens_core.END_LOAD} g minus baseline)"
                elif column.endswith("_relative"):
                    meta.comments = "Value - baseline"
                elif column == variable:
                    meta.comments = label
                else:
                    meta.comments = meta.comments or (long_name or column)
            elif column in worksheet.dataframe.columns:
                worksheet.columns[column] = window_module.WorksheetColumnMeta(
                    long_name=long_name or column,
                    units=units_text or "",
                    comments=long_name or column,
                )

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

    # Host actions --------------------------------------------------
    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return
        string_paths = [str(path) for path in paths]
        try:
            self._data = sens_core.load_data(string_paths)
        except Exception as exc:  # pragma: no cover - GUI path
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"Failed to load stress sensitivity data:\n{exc}",
            )
            self._data = None
            return
        self._loaded_files = string_paths
        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent
        config = self._gather_config()
        self._register_workbooks(config)
        if self._summary_label is not None:
            self._summary_label.setText(
                "Click Plot Stress Sensitivity to review stress sensitivity summaries."
            )
        self._log(f"Loaded {len(paths)} stress sensitivity file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if self._data is None:
            self.load_data()
        if self._data is None:
            return
        window_module = window_api()
        config = self._apply_settings_to_core()
        dataframe = sens_core.maybe_handle_outliers(self._data.copy())
        grouped = list(
            dataframe.groupby(["composition", "title", "anneal"], sort=False)
        )
        if not grouped:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "No valid stress sensitivity groups were found in the selected files.",
            )
            return
        self._clear_existing_tabs()
        total_steps = len(grouped) * len(config["variables"])
        progress_dialog: QtWidgets.QProgressDialog | None = None
        if total_steps > 1:
            progress_dialog = QtWidgets.QProgressDialog(
                "Generating stress sensitivity plots…",
                "Cancel",
                0,
                total_steps,
                self.host,
            )
            progress_dialog.setWindowTitle("Processing")
            progress_dialog.setAutoClose(True)
            progress_dialog.setAutoReset(True)
            progress_dialog.show()

        cancelled = False
        plots_created = 0
        self._plot_tabs.clear()
        min_width = 1280
        min_height = 900

        for (composition, title, anneal), group in grouped:
            for variable in config["variables"]:
                if progress_dialog is not None:
                    QtWidgets.QApplication.processEvents()
                    if progress_dialog.wasCanceled():
                        cancelled = True
                        break
                try:
                    fig, saved_name = sens_core.plot_samples(
                        group.copy(), variable, config["save"], config["output_dir"]
                    )
                except Exception as exc:
                    self._log(
                        f"Failed to plot {variable} for {composition} {anneal}: {exc}",
                        level="error",
                    )
                    continue

                canvas = FigureCanvas(fig)
                canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
                canvas.setMinimumSize(min_width, min_height)
                canvas.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Expanding,
                    QtWidgets.QSizePolicy.Policy.Expanding,
                )

                tab = QtWidgets.QWidget()
                tab.setMinimumSize(min_width, min_height)
                tab_layout = QtWidgets.QVBoxLayout(tab)
                tab_layout.setContentsMargins(0, 0, 0, 0)
                tab_layout.addWidget(canvas)
                tab_layout.setStretch(0, 1)

                ax = fig.axes[0] if fig.axes else None
                title_text = ax.get_title() if ax else variable
                descriptor = window_module.TabDescriptor(
                    kind="stress_sensitivity",
                    title=title_text,
                    root_label=f"{composition} {anneal}",
                    x_label="Sample",
                    y_label=sens_core.LABELS.get(variable, variable),
                    canvas=canvas,
                    axes=ax,
                    lines={},
                    metadata={
                        "composition": composition,
                        "title": title,
                        "anneal": anneal,
                        "variable": variable,
                        "saved_path": saved_name if config["save"] else "",
                        "source_files": list(self._loaded_files),
                    },
                )
                self.host.tab_widget.addTab(tab, sens_core.LABELS.get(variable, variable))
                self.host._register_plot_tab(tab, canvas, ax, descriptor)

                self._plot_tabs.append(tab)
                plots_created += 1
                if progress_dialog is not None:
                    progress_dialog.setValue(progress_dialog.value() + 1)

            if cancelled:
                break

        if progress_dialog is not None:
            progress_dialog.close()

        if not self._plot_tabs:
            QtWidgets.QMessageBox.warning(
                self.host,
                self.name,
                "No plots were generated. Check your settings and input files.",
            )
            return

        first_tab = self._plot_tabs[0]
        index = self.host.tab_widget.indexOf(first_tab)
        if index >= 0:
            self.host.tab_widget.setCurrentIndex(index)
        if self._summary_label is not None:
            self._summary_label.setText(
                f"Generated {plots_created} stress sensitivity plot(s)."
            )
        if cancelled:
            self._log("Plot generation cancelled by user.", level="error")
        else:
            self._log(f"Generated {plots_created} stress sensitivity plot(s).")

        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        self._apply_settings_to_core()

        def _task() -> None:
            sens_core.SHOW_PLOTS = False
            sens_core.main(self._loaded_files, backend="origin")

        self.run_origin_export(
            ready=bool(self._loaded_files),
            missing_message="Load stress sensitivity data before exporting to Origin.",
            task=_task,
            success_log="Sent stress sensitivity plots to Origin.",
            failure_message="Failed to export stress sensitivity plots to Origin",
        )

    def export_txt(self) -> None:  # type: ignore[override]
        if self._data is None:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Load stress sensitivity data before exporting TXT files.",
            )
            return
        self._apply_settings_to_core()
        start_dir = self.host._preferred_export_directory(self.name, self._last_export_dir)
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self.host,
            "Select TXT export folder",
            str(start_dir),
        )
        if not directory:
            return
        target = Path(directory)
        exported = 0
        try:
            dataframe = sens_core.maybe_handle_outliers(self._data.copy())
            for _, grp in dataframe.groupby(
                ["composition", "title", "anneal"], dropna=False
            ):
                sens_core.export_group_to_txt(grp, target)
                exported += 1
        except Exception as exc:  # pragma: no cover - GUI path
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"Failed to export TXT data:\n{exc}",
            )
            self._log(f"TXT export failed: {exc}", level="error")
            return
        if not exported:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "No stress sensitivity groups were available to export.",
            )
            return
        self._last_export_dir = target
        self.host._remember_plugin_export_dir(self.name, target)
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            f"Exported {exported} stress sensitivity table(s) to {target}",
        )
        self._log(f"Exported {exported} stress sensitivity table(s) to {target}.")

    def update_ui(self) -> None:
        has_data = self._data is not None
        has_files = bool(self._loaded_files)
        has_plots = bool(self._plot_tabs)
        ready_to_plot = has_data or self._host_has_data_selection()
        self.apply_shared_action_state(
            can_plot=ready_to_plot,
            can_save_graph=has_plots,
            can_normalize=False,
            can_export_txt=has_data,
            can_open_origin=has_files,
            can_popout=has_plots,
        )
