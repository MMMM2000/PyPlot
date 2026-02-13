from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from matplotlib import cm, colors
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
from PyQt6 import QtWidgets

from plotting.plugins._window import window_api
from plotting.plugins.base import PyPlotPlugin, register_plugin

from .core import EntropyResult, VSMIsothermEntry, VSMIsothermProcessor


@register_plugin("VSM Isotherms")
class VSMIsothermsPlugin(PyPlotPlugin):
    """PyPlot plugin for VSM isotherm VIR files."""

    requires_imported_data = True
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._processor = VSMIsothermProcessor()
        self._processor.attach_logger(lambda message: self._log(message))
        self._dataset: list[VSMIsothermEntry] | None = None
        self._loaded_paths: list[Path] = []
        self._panel_widget: QtWidgets.QWidget | None = None
        self._summary_label: QtWidgets.QLabel | None = None
        self._entropy_checkbox: QtWidgets.QCheckBox | None = None
        self._entropy_field_edit: QtWidgets.QLineEdit | None = None
        self._plot_entropy: bool = True
        self._entropy_field_levels: list[float] | None = None
        self._entropy_fields_text: str = ""
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._managed_workbooks: set[str] = set()

    # ------------------------------------------------------------------ lifecycle
    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self._set_tab_bar_visible(False)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)
        self._set_tab_bar_visible(True)

    # ------------------------------------------------------------------ UI
    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        if self._panel_widget is not None:
            return self._panel_widget

        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QtWidgets.QLabel(
            "Load VSM VIR isotherms, then plot separate angle graphs (for example 0° and 90°)."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
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
        entropy_section, entropy_layout = window_module.create_toolbar_section(
            "Derived metrics",
            parent=container,
        )
        entropy_checkbox = QtWidgets.QCheckBox(
            "Plot magnetic entropy estimate (-ΔS_M vs Temperature)",
            entropy_section,
        )
        entropy_checkbox.setChecked(self._plot_entropy)
        entropy_checkbox.toggled.connect(self._on_plot_entropy_toggled)
        entropy_layout.addWidget(entropy_checkbox)

        hint = QtWidgets.QLabel(
            "Entropy is estimated from isothermal M(H) data using a Maxwell-relation finite difference.",
            entropy_section,
        )
        hint.setWordWrap(True)
        entropy_layout.addWidget(hint)

        field_label = QtWidgets.QLabel(
            "Entropy fields ΔH [Oe] (comma-separated, blank = auto):",
            entropy_section,
        )
        entropy_layout.addWidget(field_label)
        field_edit = QtWidgets.QLineEdit(entropy_section)
        field_edit.setPlaceholderText("Example: 2000, 5000, 10000")
        field_edit.setClearButtonEnabled(True)
        field_edit.setText(self._entropy_fields_text)
        field_edit.editingFinished.connect(self._on_entropy_fields_changed)
        entropy_layout.addWidget(field_edit)

        entropy_layout.addStretch(1)
        layout.addWidget(entropy_section)
        layout.addStretch(1)

        self._entropy_checkbox = entropy_checkbox
        self._entropy_field_edit = field_edit
        self._settings_widget = container
        return container

    # ------------------------------------------------------------------ host actions
    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return

        vir_paths = [path for path in paths if path.suffix.lower() == ".vsm-vir-data"]
        if not vir_paths:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "No VSM VIR files were found in the current selection.",
            )
            self._dataset = None
            self._data = None
            self._loaded_paths = []
            self._remove_managed_workbooks(self._managed_workbooks)
            self.update_ui()
            return

        try:
            dataset = self._processor.load(vir_paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"Failed to load VSM isotherm data:\n{exc}",
            )
            self._dataset = None
            self._data = None
            self._remove_managed_workbooks(self._managed_workbooks)
            self.update_ui()
            return

        self._dataset = dataset
        self._data = dataset
        self._loaded_paths = list(vir_paths)
        if vir_paths:
            self.host._plugin_last_directories[self.name] = vir_paths[0].parent
        self._register_workbooks()
        self._log(
            f"Loaded {len(dataset)} VSM isotherm dataset(s) from {len(vir_paths)} VIR file(s)."
        )
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if self._dataset is None:
            self.load_data()
        if self._dataset is None:
            return

        self._clear_tabs()
        groups = self._grouped_entries()
        if not groups:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "No plot-ready angle groups were found in the loaded data.",
            )
            return

        plotted = 0
        for (sample, angle), entries in groups.items():
            if not entries:
                continue
            self._plot_isotherm_group(sample=sample, angle=angle, entries=entries)
            plotted += 1
            if self._plot_entropy:
                entropy = self._processor.compute_entropy(
                    entries,
                    field_levels_oe=self._entropy_field_levels,
                )
                if entropy is None:
                    self._log(
                        f"{sample} @ {self._format_angle(angle)}: "
                        "entropy skipped (need >= 3 distinct temperatures)."
                    )
                else:
                    self._plot_entropy_group(sample=sample, angle=angle, entropy=entropy)
        if plotted == 0:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "No VSM isotherm graphs could be generated from the loaded files.",
            )
        self.update_ui()

    def plot_action_label(self) -> str:
        return "Plot VSM Isotherms"

    # ------------------------------------------------------------------ plotting helpers
    def _plot_isotherm_group(
        self,
        *,
        sample: str,
        angle: float,
        entries: list[VSMIsothermEntry],
    ) -> None:
        fig = Figure(figsize=(8.5, 5))
        ax = fig.add_subplot(111)

        sorted_entries = sorted(entries, key=lambda item: (item.temperature, item.path.name.lower()))
        temperatures = [entry.temperature for entry in sorted_entries]
        cmap = cm.get_cmap("viridis")
        if len(temperatures) > 1 and max(temperatures) > min(temperatures):
            normalizer = colors.Normalize(vmin=min(temperatures), vmax=max(temperatures))
        else:
            normalizer = None

        seen_labels: set[str] = set()
        for index, entry in enumerate(sorted_entries):
            frame = entry.dataframe.sort_values("field")
            fields = frame["field"].to_numpy(dtype=float)
            signal = frame["signal"].to_numpy(dtype=float)
            if fields.size == 0:
                continue

            label = f"{entry.temperature:.1f} °C"
            plot_label = label if label not in seen_labels else "_nolegend_"
            seen_labels.add(label)
            if normalizer is not None:
                color = cmap(normalizer(entry.temperature))
            else:
                color = cmap(index / max(1, len(sorted_entries) - 1))
            ax.plot(fields, signal, linewidth=1.25, color=color, label=plot_label)

        angle_label = self._format_angle(angle)
        ax.set_title(f"{sample} - {angle_label} Isotherms")
        ax.set_xlabel("Applied Field [Oe]")
        ax.set_ylabel("Signal X [emu]")
        if ax.lines:
            ax.legend(loc="best", fontsize=8)
        ax.grid(True, alpha=0.2)

        self._register_plot_tab(
            figure=fig,
            axes=ax,
            kind="vsm_isotherms",
            title=f"{sample} - {angle_label} Isotherms",
            root_label=f"{sample} [{angle_label}]",
            x_label="Applied Field [Oe]",
            y_label="Signal X [emu]",
            metadata={"sample": sample, "angle": angle},
        )

    def _plot_entropy_group(
        self,
        *,
        sample: str,
        angle: float,
        entropy: EntropyResult,
    ) -> None:
        frame = entropy.frame.copy()
        entropy_columns = [column for column in frame.columns if column != "temperature"]
        if not entropy_columns:
            return

        fig = Figure(figsize=(8.5, 5))
        ax = fig.add_subplot(111)
        temperatures = frame["temperature"].to_numpy(dtype=float)
        for column in entropy_columns:
            values = frame[column].to_numpy(dtype=float)
            label = self._entropy_label(column)
            ax.plot(temperatures, values, marker="o", markersize=3.5, linewidth=1.2, label=label)

        angle_label = self._format_angle(angle)
        ax.set_title(f"{sample} - {angle_label} Magnetic Entropy")
        ax.set_xlabel("Temperature [°C]")
        ax.set_ylabel("-ΔS_M [arb. units]")
        if ax.lines:
            ax.legend(loc="best", fontsize=8)
        ax.grid(True, alpha=0.2)

        self._register_plot_tab(
            figure=fig,
            axes=ax,
            kind="vsm_isotherms_entropy",
            title=f"{sample} - {angle_label} Magnetic Entropy",
            root_label=f"{sample} [{angle_label}] entropy",
            x_label="Temperature [°C]",
            y_label="-ΔS_M [arb. units]",
            metadata={
                "sample": sample,
                "angle": angle,
                "max_delta_field_oe": entropy.max_delta_field,
            },
        )

    def _register_plot_tab(
        self,
        *,
        figure: Figure,
        axes: Any,
        kind: str,
        title: str,
        root_label: str,
        x_label: str,
        y_label: str,
        metadata: dict[str, Any],
    ) -> None:
        window_module = window_api()
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        canvas = FigureCanvas(figure)
        canvas.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(canvas)
        descriptor = window_module.TabDescriptor(
            kind=kind,
            title=title,
            root_label=root_label,
            x_label=x_label,
            y_label=y_label,
            canvas=canvas,
            axes=axes,
            lines={},
            metadata=metadata,
        )
        index = self.host.tab_widget.addTab(tab, root_label)
        setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
        if callable(setter):
            setter(index)
        self.host._register_plot_tab(tab, canvas, axes, descriptor)
        self._plot_tabs.append(tab)
        self._set_tab_bar_visible(False)

    def _clear_tabs(self) -> None:
        self.clear_plot_tabs(self._plot_tabs)

    # ------------------------------------------------------------------ workbook helpers
    def _grouped_entries(self) -> dict[tuple[str, float], list[VSMIsothermEntry]]:
        dataset = self._dataset
        if not dataset:
            return {}
        return self._processor.group_by_sample_angle(dataset)

    def _register_workbooks(self) -> None:
        host = self.host
        groups = self._grouped_entries()
        if not groups:
            self._remove_managed_workbooks(self._managed_workbooks)
            return

        window_module = window_api()
        active_keys: set[str] = set()

        for (sample, angle), entries in groups.items():
            if not entries:
                continue
            angle_label = self._format_angle(angle)
            workbook_folder = self._common_parent(entries)
            workbook_source = entries[0].path if len(entries) == 1 else None

            frame, columns, axis_roles = self._build_isotherm_workbook_frame(entries)
            wb_key = self._workbook_key(entries, kind="isotherms")
            workbook = window_module.WorkbookData(
                key=wb_key,
                name=f"{sample} ({angle_label} isotherms)",
                worksheets=[],
                source=workbook_source,
                folder=workbook_folder,
            )
            sheet_key = host._worksheet_key(wb_key, "Isotherms")
            worksheet = window_module.WorksheetData(
                key=sheet_key,
                name="Isotherms",
                dataframe=frame,
                columns=columns,
                source=workbook_source,
                workbook_key=wb_key,
                axis_roles=axis_roles,
            )
            workbook.worksheets = [sheet_key]
            host._register_imported_workbook(workbook, [worksheet])
            active_keys.add(wb_key)

            entropy = self._processor.compute_entropy(
                entries,
                field_levels_oe=self._entropy_field_levels,
            )
            if entropy is None:
                continue
            entropy_key = self._workbook_key(entries, kind="entropy")
            entropy_workbook = window_module.WorkbookData(
                key=entropy_key,
                name=f"{sample} ({angle_label} entropy)",
                worksheets=[],
                source=None,
                folder=workbook_folder,
            )
            entropy_sheet_key = host._worksheet_key(entropy_key, "Entropy")
            entropy_columns = self._build_entropy_columns(entropy.frame)
            entropy_roles = "X" + ("Y" * max(0, len(entropy.frame.columns) - 1))
            entropy_sheet = window_module.WorksheetData(
                key=entropy_sheet_key,
                name="Entropy",
                dataframe=entropy.frame.copy(),
                columns=entropy_columns,
                source=None,
                workbook_key=entropy_key,
                axis_roles=entropy_roles,
            )
            entropy_workbook.worksheets = [entropy_sheet_key]
            host._register_imported_workbook(entropy_workbook, [entropy_sheet])
            active_keys.add(entropy_key)

        stale = self._managed_workbooks - active_keys
        if stale:
            self._remove_managed_workbooks(stale)
        self._managed_workbooks = active_keys
        if active_keys or stale:
            host._refresh_imported_data_summary()
            host._sync_selected_paths_with_imports()
            host._sync_shared_action_states()

    def _remove_managed_workbooks(self, keys: Iterable[str]) -> None:
        host = self.host
        for key in list(keys):
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
        host._sync_shared_action_states()

    def _workbook_key(self, entries: list[VSMIsothermEntry], *, kind: str) -> str:
        resolved_paths: list[str] = []
        for entry in entries:
            try:
                resolved_paths.append(str(entry.path.resolve()))
            except Exception:
                resolved_paths.append(str(entry.path))
        digest = hashlib.sha1("|".join(sorted(resolved_paths)).encode("utf-8")).hexdigest()[:12]
        return f"vsm_isotherms::{kind}::{digest}"

    def _build_isotherm_workbook_frame(
        self,
        entries: list[VSMIsothermEntry],
    ) -> tuple[pd.DataFrame, dict[str, Any], str]:
        columns_data: dict[str, list[float]] = {}
        columns_meta: dict[str, Any] = {}
        rows: list[tuple[str, list[float], str, list[float], float, str]] = []
        max_len = 0
        window_module = window_api()

        for index, entry in enumerate(entries, start=1):
            frame = entry.dataframe.sort_values("field")
            fields = frame["field"].to_numpy(dtype=float).tolist()
            signal = frame["signal"].to_numpy(dtype=float).tolist()
            if not fields:
                continue
            max_len = max(max_len, len(fields), len(signal))
            field_col = f"H{index}"
            signal_col = f"M{index}"
            rows.append(
                (
                    field_col,
                    fields,
                    signal_col,
                    signal,
                    entry.temperature,
                    entry.path.name,
                )
            )

        axis_roles = ""
        for field_col, fields, signal_col, signal, temperature, source_name in rows:
            columns_data[field_col] = fields + [np.nan] * (max_len - len(fields))
            columns_data[signal_col] = signal + [np.nan] * (max_len - len(signal))
            comment = f"{temperature:.1f} °C ({source_name})"
            columns_meta[field_col] = window_module.WorksheetColumnMeta(
                long_name="Applied Field",
                units="Oe",
                comments=comment,
            )
            columns_meta[signal_col] = window_module.WorksheetColumnMeta(
                long_name="Signal X",
                units="emu",
                comments=comment,
            )
            axis_roles += "XY"

        return pd.DataFrame(columns_data), columns_meta, axis_roles

    def _build_entropy_columns(self, frame: pd.DataFrame) -> dict[str, Any]:
        window_module = window_api()
        columns_meta: dict[str, Any] = {}
        for column in frame.columns:
            if column == "temperature":
                columns_meta[column] = window_module.WorksheetColumnMeta(
                    long_name="Temperature",
                    units="°C",
                )
                continue
            columns_meta[column] = window_module.WorksheetColumnMeta(
                long_name="-ΔS_M",
                units="arb. units",
                comments=self._entropy_label(column),
            )
        return columns_meta

    def _common_parent(self, entries: list[VSMIsothermEntry]) -> Path | None:
        parents: set[Path] = set()
        for entry in entries:
            try:
                parents.add(entry.path.resolve().parent)
            except Exception:
                parents.add(entry.path.parent)
        if len(parents) == 1:
            return next(iter(parents))
        return None

    def _set_tab_bar_visible(self, visible: bool) -> None:
        bar_getter = getattr(self.host.tab_widget, "tabBar", None)
        if not callable(bar_getter):
            return
        try:
            bar = bar_getter()
        except Exception:
            return
        try:
            bar.setVisible(visible)
            bar.setMaximumHeight(0 if not visible else 16777215)
            auto_hide = getattr(self.host.tab_widget, "setTabBarAutoHide", None)
            if callable(auto_hide):
                auto_hide(not visible)
        except Exception:
            return

    # ------------------------------------------------------------------ state
    def update_ui(self) -> None:
        has_data = self._dataset is not None
        has_plots = bool(self._plot_tabs)
        self.apply_shared_action_state(
            can_plot=has_data or self._host_has_data_selection(),
            can_save_graph=has_plots,
            can_normalize=False,
            can_export_txt=False,
            can_export_workbooks=bool(self._managed_workbooks),
            update_project_actions=False,
        )
        if self._summary_label is not None:
            if not has_data:
                self._summary_label.setText(
                    "Import VSM VIR isotherm files, then click Plot VSM Isotherms."
                )
            else:
                groups = self._grouped_entries()
                curve_count = sum(len(entries) for entries in groups.values())
                self._summary_label.setText(
                    f"Loaded {len(self._dataset)} VIR files; {curve_count} unique "
                    "temperature curves after duplicate cleanup."
                )
        self.host._update_project_actions()

    # ------------------------------------------------------------------ small helpers
    def _on_plot_entropy_toggled(self, enabled: bool) -> None:
        self._plot_entropy = bool(enabled)

    def _on_entropy_fields_changed(self) -> None:
        edit = self._entropy_field_edit
        if not isinstance(edit, QtWidgets.QLineEdit):
            return
        candidate = edit.text().strip()
        try:
            parsed = self._parse_entropy_field_text(candidate)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(
                self.host,
                self.name,
                f"Invalid entropy field list:\n{exc}",
            )
            edit.setText(self._entropy_fields_text)
            return
        self._entropy_fields_text = candidate
        self._entropy_field_levels = parsed
        if self._dataset is not None:
            self._register_workbooks()
            self.update_ui()

    def _parse_entropy_field_text(self, text: str) -> list[float] | None:
        stripped = text.strip()
        if not stripped:
            return None
        tokens = [token.strip() for token in stripped.replace(";", ",").split(",")]
        values: list[float] = []
        for token in tokens:
            if not token:
                continue
            cleaned = token.lower().replace("oe", "").strip()
            try:
                value = float(cleaned)
            except Exception as exc:
                raise ValueError(f"'{token}' is not a valid number.") from exc
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"'{token}' must be a positive field value in Oe.")
            values.append(value)
        if not values:
            return None
        unique: dict[int, float] = {}
        for value in sorted(values):
            unique[int(round(value))] = float(value)
        return [unique[key] for key in sorted(unique)]

    def _format_angle(self, angle: float) -> str:
        if abs(angle - round(angle)) < 1e-6:
            return f"{int(round(angle))}°"
        return f"{angle:.1f}°"

    def _entropy_label(self, column_name: str) -> str:
        if column_name.startswith("dS_") and column_name.endswith("Oe"):
            value = column_name[3:-2]
            return f"ΔH = {value} Oe"
        return column_name


__all__ = ["VSMIsothermsPlugin"]
