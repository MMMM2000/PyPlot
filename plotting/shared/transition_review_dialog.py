"""Small logger-facing editor for portable transition-review sidecars."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd
import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets

from plotting.plugins.current_annealing import core as annealing_core
from plotting.plugins.mini_dma import core as tma_core
from plotting.shared.transition_review import (
    atomic_write_review,
    is_transition_label,
    load_review,
    sidecar_path_for_measurement,
    utc_now_text,
)
from plotting.shared.transition_review_adapters import (
    current_annealing_review_draft,
    tma_review_draft,
)


LABELS = ("As", "Af", "Ms", "Mf", "As1", "Af1", "Ms1", "Mf1", "As2", "Af2", "Ms2", "Mf2")
_POINT_ORDER = {point: index for index, point in enumerate(("As", "Af", "Ms", "Mf"))}


def _transition_label_parts(label: str) -> tuple[str, int | None] | None:
    match = re.fullmatch(r"(As|Af|Ms|Mf)(\d*)", str(label))
    if match is None:
        return None
    return match.group(1), int(match.group(2)) if match.group(2) else None


def _ordered_transition_labels(labels: Sequence[str]) -> list[str]:
    valid = [str(label) for label in labels if is_transition_label(label)]
    return sorted(
        set(valid),
        key=lambda label: (
            (_transition_label_parts(label) or ("As", None))[1] or 0,
            _POINT_ORDER[(_transition_label_parts(label) or ("As", None))[0]],
        ),
    )

@dataclass(frozen=True)
class ReviewPlot:
    x: pd.Series
    y: pd.Series
    title: str
    y_label: str
    derives_transition_strain: bool = False
    strain_reference: Mapping[str, Any] | None = None
    unit_series: Mapping[str, tuple[pd.Series, pd.Series]] | None = None
    unit_branches: Mapping[str, annealing_core.AnnealingReviewCycle] | None = None
    x_label: str = 'Current'
    x_unit: str = 'mA'
    value_unit: str = 'mA'
    heating_series: tuple[pd.Series, pd.Series] | None = None
    cooling_series: tuple[pd.Series, pd.Series] | None = None


class PortableTransitionReviewDialog(QtWidgets.QDialog):
    """Edit all targets in one portable review record."""

    advanceRequested = QtCore.pyqtSignal()

    def __init__(
        self,
        payload: Mapping[str, Any],
        plots: Mapping[str, ReviewPlot],
        sidecar_path: Path,
        parent: QtWidgets.QWidget | None = None,
        save_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.payload = copy.deepcopy(dict(payload))
        self.plots = dict(plots)
        self.sidecar_path = Path(sidecar_path)
        self._save_callback = save_callback
        self._queue_mode = False
        self._loading = False
        self._target_index = -1
        self.setWindowTitle("Transition review")
        self.resize(1050, 560)
        self.setMinimumSize(820, 500)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        self.heading = QtWidgets.QLabel(
            f"Review after safe run completion \N{MIDDLE DOT} saves {self.sidecar_path.name} only"
        )
        root.addWidget(self.heading)

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        root.addWidget(split, 1)
        left = QtWidgets.QWidget()
        self.target_panel = left
        left.setMinimumWidth(170)
        left.setMaximumWidth(250)
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        self.navigation_label = QtWidgets.QLabel("Samples / cycles")
        left_layout.addWidget(self.navigation_label)
        self.target_list = QtWidgets.QListWidget()
        self._navigation_items: list[tuple[int, int]] = []
        targets = list(self.payload.get("targets", []))
        for target_index, target in enumerate(targets):
            units = self._review_units_for_target(target)
            target_label = self._target_display_label(target)
            for unit_index, (unit_title, _labels) in enumerate(units):
                if len(targets) == 1 and len(units) > 1:
                    label = unit_title
                elif len(units) > 1:
                    label = f"{target_label} · {unit_title}"
                else:
                    label = target_label
                self.target_list.addItem(label)
                self._navigation_items.append((target_index, unit_index))
        self.target_list.currentRowChanged.connect(self._target_changed)
        left_layout.addWidget(self.target_list, 1)
        split.addWidget(left)
        left.setVisible(self.target_list.count() > 1)

        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        plot_panel = QtWidgets.QWidget()
        plot_layout = QtWidgets.QVBoxLayout(plot_panel)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_widget = pg.PlotWidget()
        self.canvas = self.plot_widget
        self.plot_item = self.plot_widget.getPlotItem()
        self.plot_item.showGrid(x=True, y=True, alpha=0.16)
        self.plot_item.setDownsampling(auto=True, mode="peak")
        self.plot_item.setClipToView(True)
        self.legend = self.plot_item.addLegend(offset=(10, 10))
        self.heating_curve_item = self.plot_item.plot(
            [], [], pen=pg.mkPen('#ef4444', width=1.6), name='Heating'
        )
        self.cooling_curve_item = self.plot_item.plot(
            [], [], pen=pg.mkPen('#3b82f6', width=1.6), name='Cooling'
        )
        self.heating_symbol_item = self.plot_item.plot(
            [], [], pen=None, symbol='o', symbolSize=4,
            symbolPen=None, symbolBrush=pg.mkBrush('#ef4444')
        )
        self.cooling_symbol_item = self.plot_item.plot(
            [], [], pen=None, symbol='o', symbolSize=4,
            symbolPen=None, symbolBrush=pg.mkBrush('#3b82f6')
        )
        self._cooling_legend_visible = True
        # Retain the former public-ish attribute for callers and tests that use
        # it, while drawing the two physical sweep directions independently.
        self.curve_item = self.heating_curve_item
        self._auto_marker_items: dict[str, pg.InfiniteLine] = {}
        self._manual_marker_items: dict[str, pg.InfiniteLine] = {}
        self.plot_widget.scene().sigMouseClicked.connect(self._plot_scene_clicked)
        plot_layout.addWidget(self.plot_widget, 1)
        right.addWidget(plot_panel)

        review_panel = QtWidgets.QWidget()
        review_panel.setMinimumWidth(310)
        review_panel.setMaximumWidth(390)
        review_layout = QtWidgets.QVBoxLayout(review_panel)
        review_layout.setContentsMargins(6, 0, 0, 0)
        review_layout.setSpacing(6)

        self.review_unit_row = QtWidgets.QWidget()
        review_unit_layout = QtWidgets.QHBoxLayout(self.review_unit_row)
        review_unit_layout.setContentsMargins(0, 0, 0, 0)
        review_unit_layout.setSpacing(4)
        self.review_unit_label = QtWidgets.QLabel("Cycle")
        self.review_unit_combo = QtWidgets.QComboBox()
        review_unit_layout.addWidget(self.review_unit_label)
        review_unit_layout.addWidget(self.review_unit_combo, 1)
        review_layout.addWidget(self.review_unit_row)
        self.review_unit_row.hide()
        self._review_unit_labels: list[list[str]] = []
        self._active_unit_labels: list[str] = []

        self.values_box = QtWidgets.QGroupBox("Transition choices (mA)")
        self.values_box.setStyleSheet(
            "QPushButton { padding: 4px 5px; } "
            "QPushButton:checked { background: #2563eb; color: white; "
            "border: 1px solid #1d4ed8; border-radius: 3px; }"
        )
        values_layout = QtWidgets.QVBoxLayout(self.values_box)
        values_layout.setContentsMargins(6, 4, 6, 4)
        values_layout.setSpacing(4)
        self.values_table = QtWidgets.QTableWidget(0, 4)
        self.values_table.setHorizontalHeaderLabels(
            ["Point", "Auto", "Manual", "Not observed"]
        )
        self.values_table.verticalHeader().setVisible(False)
        self.values_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.values_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.values_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        table_header = self.values_table.horizontalHeader()
        table_header.setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        for column in range(1, 4):
            table_header.setSectionResizeMode(
                column, QtWidgets.QHeaderView.ResizeMode.Stretch
            )
        self.choice_buttons: dict[str, dict[str, QtWidgets.QPushButton]] = {}
        self.choice_groups: dict[str, QtWidgets.QButtonGroup] = {}
        self._choices: dict[str, str | None] = {}
        self._manual_values: dict[str, float] = {}
        values_layout.addWidget(self.values_table)

        self.manual_editor = QtWidgets.QWidget()
        manual_layout = QtWidgets.QHBoxLayout(self.manual_editor)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.setSpacing(4)
        self.manual_editor_label = QtWidgets.QLabel("Manual value")
        self.manual_value_edit = QtWidgets.QLineEdit()
        self.manual_value_edit.setMaximumWidth(86)
        manual_validator = QtGui.QDoubleValidator(self.manual_value_edit)
        manual_validator.setNotation(QtGui.QDoubleValidator.Notation.StandardNotation)
        self.manual_value_edit.setValidator(manual_validator)
        self.manual_graph_hint = QtWidgets.QLabel("mA \N{MIDDLE DOT} or click graph")
        manual_layout.addWidget(self.manual_editor_label)
        manual_layout.addWidget(self.manual_value_edit)
        manual_layout.addWidget(self.manual_graph_hint, 1)
        values_layout.addWidget(self.manual_editor)
        self.derived_strain_label = QtWidgets.QLabel()
        self.derived_strain_label.setWordWrap(True)
        self.derived_strain_label.setStyleSheet("color: #9ca3af;")
        values_layout.addWidget(self.derived_strain_label)
        self.cooling_branch_row = QtWidgets.QWidget()
        cooling_branch_layout = QtWidgets.QVBoxLayout(self.cooling_branch_row)
        cooling_branch_layout.setContentsMargins(0, 0, 0, 0)
        cooling_branch_layout.setSpacing(1)
        self.cooling_branch_check = QtWidgets.QCheckBox("Cooling branch recorded")
        self.cooling_branch_reason = QtWidgets.QLabel()
        self.cooling_branch_reason.setWordWrap(True)
        self.cooling_branch_reason.setStyleSheet("color: #9ca3af;")
        cooling_branch_layout.addWidget(self.cooling_branch_check)
        cooling_branch_layout.addWidget(self.cooling_branch_reason)
        values_layout.addWidget(self.cooling_branch_row)
        self.cooling_branch_row.hide()
        review_layout.addWidget(self.values_box)

        disposition_row = QtWidgets.QHBoxLayout()
        disposition_row.setContentsMargins(0, 0, 0, 0)
        disposition_row.setSpacing(6)
        self.exclude_check = QtWidgets.QCheckBox("Exclude from Builder analysis")
        self.exclude_check.setToolTip(
            "Keep the reviewed values in the run folder, but do not use this "
            "target in Builder analysis."
        )
        disposition_row.addWidget(self.exclude_check)
        disposition_row.addStretch(1)
        self.archive_button = QtWidgets.QPushButton("Mark for archive")
        self.archive_button.setCheckable(True)
        self.archive_button.setToolTip(
            "Mark the complete measurement run for a later archive operation. "
            "The data are excluded from Builder analysis, but no files are moved now."
        )
        self.archive_button.setStyleSheet(
            "QPushButton:checked { background: #991b1b; color: white; "
            "border: 1px solid #ef4444; border-radius: 3px; }"
        )
        self.archive_button.setChecked(
            self.payload.get("archive_requested") is True
        )
        disposition_row.addWidget(self.archive_button)
        review_layout.addLayout(disposition_row)
        self.decision_summary = QtWidgets.QLabel()
        self.decision_summary.setWordWrap(True)
        review_layout.addWidget(self.decision_summary)
        review_layout.addStretch(1)

        self.exclude_check.toggled.connect(self._target_controls_changed)
        self.archive_button.toggled.connect(self._archive_requested_changed)
        self.review_unit_combo.currentIndexChanged.connect(
            self._review_unit_changed
        )
        self.values_table.itemSelectionChanged.connect(self._selected_row_changed)
        self.manual_value_edit.textChanged.connect(self._manual_text_changed)
        self.manual_value_edit.editingFinished.connect(
            self._manual_value_committed
        )
        self.cooling_branch_check.toggled.connect(
            self._cooling_branch_override_changed
        )
        right.addWidget(review_panel)
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 0)
        right.setSizes([720, 340])
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([190, 910] if self.target_list.count() > 1 else [0, 1100])

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("Save review")
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        if self.target_list.count():
            self.target_list.setCurrentRow(0)
        self._update_archive_controls()

    def _targets(self) -> list[dict[str, Any]]:
        return self.payload.setdefault("targets", [])

    def _target_display_label(self, target: Mapping[str, Any]) -> str:
        display_label = str(target.get('display_label') or '').strip()
        if display_label:
            return display_label
        metadata = target.get('target')
        if isinstance(metadata, Mapping):
            stress = metadata.get("stress_mpa")
            load = metadata.get("load_g")
            if stress is not None:
                label = f"{float(stress):.6g} MPa"
                sweep_index = metadata.get("sweep_index")
                sweep_count = metadata.get("sweep_count")
                if sweep_index is not None and int(sweep_count or 1) > 1:
                    label += (
                        f" \N{MIDDLE DOT} sweep {int(sweep_index)}/"
                        f"{int(sweep_count)}"
                    )
                if load is not None:
                    label += f" \N{MIDDLE DOT} {float(load):.6g} g"
                return label
        if self.payload.get("experiment_family") == "current_annealing":
            return "Current Annealing"
        return str(target.get("target_key") or "Transition target")

    def _labels_for_target(self, target: Mapping[str, Any]) -> list[str]:
        available: set[str] = set()
        for field in ("auto_values", "manual_values", "final_values"):
            values = target.get(field)
            if isinstance(values, Mapping):
                available.update(str(label) for label in values)
        available.update(str(label) for label in target.get("cleared_labels", ()))
        if self.payload.get("experiment_family") == "current_annealing":
            plot = self.plots.get(str(target.get('target_key') or ''))
            if plot is not None and plot.unit_branches:
                available = set()
                overrides = target.get("cooling_branch_overrides")
                overrides = overrides if isinstance(overrides, Mapping) else {}
                for title, branch in plot.unit_branches.items():
                    match = re.fullmatch(r'Cycle\s+(\d+)', str(title))
                    if match is None:
                        continue
                    loop = int(match.group(1))
                    available.update((f"As{loop}", f"Af{loop}"))
                    cooling_recorded = bool(
                        overrides.get(title, branch.cooling_recorded)
                    )
                    if cooling_recorded:
                        available.update((f"Ms{loop}", f"Mf{loop}"))
                return _ordered_transition_labels(available)
            loop_numbers = {
                parts[1]
                for label in available
                if (parts := _transition_label_parts(label)) is not None
                and parts[1] is not None
            }
            plot = self.plots.get(str(target.get('target_key') or ''))
            if plot is not None:
                for title in (plot.unit_series or {}):
                    match = re.fullmatch(r'Cycle\s+(\d+)', str(title))
                    if match is not None:
                        loop_numbers.add(int(match.group(1)))
            if not loop_numbers:
                loop_numbers = {1, 2}
            for loop in loop_numbers:
                available.update(
                    (f"As{loop}", f"Af{loop}", f"Ms{loop}", f"Mf{loop}")
                )
        else:
            available.update(("As", "Af", "Ms", "Mf"))
        return _ordered_transition_labels(available)

    def _review_units_for_target(
        self,
        target: Mapping[str, Any],
    ) -> list[tuple[str, list[str]]]:
        labels = self._labels_for_target(target)
        if self.payload.get("experiment_family") != "current_annealing":
            return [("Transitions", labels)]
        plot = self.plots.get(str(target.get('target_key') or ''))
        if plot is not None and plot.unit_branches:
            return [
                (
                    title,
                    [
                        label
                        for label in labels
                        if label.endswith(str(index))
                    ],
                )
                for index, title in enumerate(plot.unit_branches, start=1)
            ]
        loop_numbers = sorted(
            {
                parts[1]
                for label in labels
                if (parts := _transition_label_parts(label)) is not None
                and parts[1] is not None
            }
        )
        return [
            (
                f"Cycle {loop}",
                [label for label in labels if label.endswith(str(loop))],
            )
            for loop in loop_numbers
        ]

    def _populate_review_units(
        self,
        target: Mapping[str, Any],
        *,
        selected_index: int = 0,
    ) -> None:
        blocker = QtCore.QSignalBlocker(self.review_unit_combo)
        try:
            units = self._review_units_for_target(target)
            self.review_unit_combo.clear()
            self._review_unit_labels = []
            for title, labels in units:
                self.review_unit_combo.addItem(title)
                self._review_unit_labels.append(labels)
            selected_index = min(max(int(selected_index), 0), max(len(units) - 1, 0))
            self.review_unit_combo.setCurrentIndex(selected_index if units else -1)
            self._active_unit_labels = (
                list(self._review_unit_labels[selected_index])
                if self._review_unit_labels
                else []
            )
            self.review_unit_row.hide()
        finally:
            del blocker

    def _update_cooling_branch_control(
        self,
        target: Mapping[str, Any],
    ) -> None:
        plot = self.plots.get(str(target.get("target_key") or ""))
        title = self.review_unit_combo.currentText()
        branch = (plot.unit_branches or {}).get(title) if plot is not None else None
        if branch is None:
            self.cooling_branch_row.hide()
            return
        overrides = target.get("cooling_branch_overrides")
        overrides = overrides if isinstance(overrides, Mapping) else {}
        checked = bool(overrides.get(title, branch.cooling_recorded))
        blocker = QtCore.QSignalBlocker(self.cooling_branch_check)
        try:
            self.cooling_branch_check.setChecked(checked)
        finally:
            del blocker
        if title in overrides:
            prefix = "Manual override. "
        else:
            prefix = ""
        self.cooling_branch_reason.setText(prefix + branch.cooling_reason)
        self.cooling_branch_row.show()

    def _cooling_branch_override_changed(self, checked: bool) -> None:
        if self._loading or self._target_index < 0:
            return
        self._store_target_controls()
        target = self._targets()[self._target_index]
        plot = self.plots.get(str(target.get("target_key") or ""))
        title = self.review_unit_combo.currentText()
        branch = (plot.unit_branches or {}).get(title) if plot is not None else None
        if branch is None:
            return
        overrides = target.setdefault("cooling_branch_overrides", {})
        if checked == branch.cooling_recorded:
            overrides.pop(title, None)
        else:
            overrides[title] = bool(checked)
        if not overrides:
            target.pop("cooling_branch_overrides", None)
        if checked:
            unavailable = target.pop("branch_unavailable_review", None)
            if isinstance(unavailable, Mapping):
                for field in ("manual_values", "final_values"):
                    values = unavailable.get(field)
                    if isinstance(values, Mapping):
                        target.setdefault(field, {}).update(values)
                cleared = unavailable.get("cleared_labels")
                if isinstance(cleared, Sequence) and not isinstance(
                    cleared, (str, bytes)
                ):
                    target["cleared_labels"] = sorted(
                        set(target.get("cleared_labels", ()))
                        | {str(label) for label in cleared}
                    )
        unit_index = self.review_unit_combo.currentIndex()
        units = self._review_units_for_target(target)
        self._review_unit_labels = [labels for _title, labels in units]
        self._active_unit_labels = list(self._review_unit_labels[unit_index])
        self._loading = True
        self._populate_values_table(target)
        self._loading = False
        self._update_cooling_branch_control(target)
        self._selected_row_changed()
        self._update_decision_summary()
        self._draw_target()

    def _review_unit_changed(self, index: int) -> None:
        if self._loading or index < 0 or index >= len(self._review_unit_labels):
            return
        self._store_target_controls()
        self._active_unit_labels = list(self._review_unit_labels[index])
        target = self._targets()[self._target_index]
        self._loading = True
        self._populate_values_table(target)
        self._update_cooling_branch_control(target)
        self._loading = False
        self._selected_row_changed()
        self._update_decision_summary()
        self._draw_target()

    def _initial_choice(
        self,
        target: Mapping[str, Any],
        label: str,
    ) -> str | None:
        status = str(target.get("status") or "unreviewed")
        auto = target.get("auto_values") if isinstance(target.get("auto_values"), Mapping) else {}
        manual = target.get("manual_values") if isinstance(target.get("manual_values"), Mapping) else {}
        final = target.get("final_values") if isinstance(target.get("final_values"), Mapping) else {}
        cleared = set(str(item) for item in target.get("cleared_labels", ()))
        if status == "no_transition" or label in cleared:
            return "not_observed"
        if label in manual:
            return "manual"
        if label in auto and (status == "accepted_auto" or label in final):
            return "auto"
        return None

    def _populate_values_table(self, target: Mapping[str, Any]) -> None:
        blocker = QtCore.QSignalBlocker(self.values_table)
        try:
            labels = (
                list(self._active_unit_labels)
                if self._active_unit_labels
                else self._labels_for_target(target)
            )
            auto = target.get("auto_values") if isinstance(target.get("auto_values"), Mapping) else {}
            manual = target.get("manual_values") if isinstance(target.get("manual_values"), Mapping) else {}
            self.values_table.clearContents()
            self.values_table.setRowCount(len(labels))
            self.choice_buttons = {}
            self.choice_groups = {}
            self._choices = {}
            self._manual_values = {
                label: float(value)
                for label, value in manual.items()
                if label in labels
            }
            for row, label in enumerate(labels):
                point_item = QtWidgets.QTableWidgetItem(label)
                point_item.setFlags(point_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                point_item.setData(QtCore.Qt.ItemDataRole.UserRole, label)
                self.values_table.setItem(row, 0, point_item)

                group = QtWidgets.QButtonGroup(self)
                group.setExclusive(True)
                buttons: dict[str, QtWidgets.QPushButton] = {}
                for column, choice in enumerate(
                    ("auto", "manual", "not_observed"), start=1
                ):
                    button = QtWidgets.QPushButton()
                    button.setCheckable(True)
                    button.setProperty("transitionChoice", choice)
                    button.clicked.connect(
                        lambda _checked=False, selected_label=label, selected_choice=choice: self._choice_clicked(
                            selected_label, selected_choice
                        )
                    )
                    group.addButton(button)
                    self.values_table.setCellWidget(row, column, button)
                    buttons[choice] = button
                auto_value = auto.get(label)
                buttons["auto"].setEnabled(auto_value is not None)
                buttons["auto"].setText(
                    "\N{EM DASH}" if auto_value is None else f"{float(auto_value):.6g}"
                )
                buttons["auto"].setToolTip(
                    "Automatic detector did not return this point."
                    if auto_value is None
                    else f"Use automatic {label} = {float(auto_value):.6g} mA"
                )
                buttons["not_observed"].setText("\N{EM DASH}")
                buttons["not_observed"].setToolTip(
                    f"Mark {label} as reviewed but not observed in this run."
                )
                self.choice_buttons[label] = buttons
                self.choice_groups[label] = group
                self._choices[label] = self._initial_choice(target, label)
                self._update_choice_row(label)
                self.values_table.setRowHeight(row, 29)
            if labels:
                self.values_table.selectRow(0)
            self.values_table.setMaximumHeight(
                min(
                    290,
                    self.values_table.horizontalHeader().height()
                    + 29 * max(len(labels), 1)
                    + 4,
                )
            )
        finally:
            del blocker
        self._selected_row_changed()

    def _selected_label(self) -> str:
        row = self.values_table.currentRow()
        item = self.values_table.item(row, 0) if row >= 0 else None
        return str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "") if item else ""

    def _row_for_label(self, label: str) -> int:
        for row in range(self.values_table.rowCount()):
            item = self.values_table.item(row, 0)
            if item and item.data(QtCore.Qt.ItemDataRole.UserRole) == label:
                return row
        return -1

    def _update_choice_row(self, label: str) -> None:
        buttons = self.choice_buttons.get(label, {})
        choice = self._choices.get(label)
        for name, button in buttons.items():
            button.setChecked(name == choice)
        manual_value = self._manual_values.get(label)
        manual_button = buttons.get("manual")
        if manual_button is not None:
            manual_button.setText(
                "Set…"
                if manual_value is None
                else f"{manual_value:.6g}"
            )
        row = self._row_for_label(label)
        point_item = self.values_table.item(row, 0) if row >= 0 else None
        if point_item is not None:
            point_item.setToolTip(
                "Choose the automatic value, set a manual value, or mark the point not observed."
            )

    def _choice_clicked(self, label: str, choice: str) -> None:
        if self._loading:
            return
        row = self._row_for_label(label)
        if row >= 0:
            self.values_table.selectRow(row)
        self._choices[label] = choice
        self._update_choice_row(label)
        self._selected_row_changed()
        if choice == "manual":
            self.manual_value_edit.setFocus()
            self.manual_value_edit.selectAll()
        self._target_controls_changed()

    def _selected_row_changed(self) -> None:
        label = self._selected_label()
        choice = self._choices.get(label)
        manual_mode = bool(label) and choice == "manual"
        blocker = QtCore.QSignalBlocker(self.manual_value_edit)
        try:
            value = self._manual_values.get(label) if manual_mode else None
            self.manual_value_edit.setText(
                "" if value is None else f"{float(value):.6g}"
            )
        finally:
            del blocker
        self.manual_editor_label.setText(
            f"Manual {label}" if label else "Manual value"
        )
        self.manual_value_edit.setEnabled(manual_mode)
        self.manual_graph_hint.setEnabled(manual_mode)
        self._update_derived_strain_label()

    def _update_derived_strain_label(self) -> None:
        label = self._selected_label()
        if not label or self._target_index < 0:
            self.derived_strain_label.clear()
            self.derived_strain_label.hide()
            return
        target = self._targets()[self._target_index]
        values = target.get("strain_at_transition_pct")
        strain = values.get(label) if isinstance(values, Mapping) else None
        if strain is None:
            self.derived_strain_label.clear()
            self.derived_strain_label.hide()
            return
        reference = target.get("strain_reference")
        l0_mm = reference.get("l0_mm") if isinstance(reference, Mapping) else None
        suffix = f" · target L₀ {float(l0_mm):.6g} mm" if l0_mm is not None else ""
        self.derived_strain_label.setText(
            f"{label} strain: {float(strain):.6g}%{suffix}"
        )
        self.derived_strain_label.show()

    def _manual_text_changed(self, text: str) -> None:
        if self._loading:
            return
        label = self._selected_label()
        if not label or self._choices.get(label) != "manual":
            return
        normalized = text.strip().replace(",", ".")
        try:
            value = float(normalized)
        except ValueError:
            self._manual_values.pop(label, None)
        else:
            if math.isfinite(value):
                self._manual_values[label] = value
            else:
                self._manual_values.pop(label, None)
        self._update_choice_row(label)
        self._store_target_controls()
        self._update_decision_summary()

    def _manual_value_committed(self) -> None:
        if self._loading:
            return
        self._store_target_controls()
        self._draw_target()
        self._update_decision_summary()

    def _archive_requested(self) -> bool:
        return self.payload.get("archive_requested") is True

    def _update_archive_controls(self) -> None:
        requested = self._archive_requested()
        self.archive_button.setText(
            "Marked for archive" if requested else "Mark for archive"
        )
        self.values_box.setEnabled(not requested)
        self.exclude_check.setEnabled(not requested)
        if self._queue_mode:
            if requested:
                self.save_button.setText("Save marked run and next")
            else:
                row = max(self.target_list.currentRow(), 0)
                final_unit = row == self.target_list.count() - 1
                next_kind = (
                    "target"
                    if self.payload.get("experiment_family") == "tma"
                    else "cycle"
                )
                self.save_button.setText(
                    "Save run and next"
                    if final_unit
                    else f"Save and next {next_kind}"
                )

    def _archive_requested_changed(self, requested: bool) -> None:
        if self._loading:
            return
        self._store_target_controls()
        if requested:
            self.payload["archive_requested"] = True
            self.payload.setdefault("archive_requested_utc", utc_now_text())
            for target in self._targets():
                target.setdefault(
                    "status_before_archive",
                    str(target.get("status") or "unreviewed"),
                )
                target["status"] = "excluded"
                target["included"] = False
                target["analysis_included"] = False
        else:
            self.payload.pop("archive_requested", None)
            self.payload.pop("archive_requested_utc", None)
            for target in self._targets():
                restored = str(
                    target.pop("status_before_archive", "unreviewed")
                    or "unreviewed"
                )
                target["status"] = restored
                target["included"] = restored in {
                    "accepted_auto",
                    "manual_adjusted",
                }
                target["analysis_included"] = restored in {
                    "accepted_auto",
                    "manual_adjusted",
                    "no_transition",
                }
            row = self.target_list.currentRow()
            if row >= 0:
                self._loading = True
                self._target_changed(row)
        self._update_archive_controls()
        self._update_decision_summary()

    def _current_target_ready(self) -> bool:
        if self._archive_requested():
            return True
        if not self._choices:
            return False
        for label, choice in self._choices.items():
            if choice is None:
                return False
            if choice == "manual" and label not in self._manual_values:
                return False
        return True

    def _target_ready(self, target: Mapping[str, Any]) -> bool:
        if self._archive_requested():
            return True
        for label in self._labels_for_target(target):
            choice = self._initial_choice(target, label)
            if choice is None:
                return False
            if choice == "manual":
                manual = target.get("manual_values")
                if not isinstance(manual, Mapping) or label not in manual:
                    return False
        return True

    def _all_targets_ready(self) -> bool:
        if self._archive_requested():
            return True
        return bool(self._targets()) and all(
            self._target_ready(target) for target in self._targets()
        )

    def _navigation_row_ready(self, row: int) -> bool:
        if self._archive_requested():
            return True
        if row < 0 or row >= len(self._navigation_items):
            return False
        target_index, unit_index = self._navigation_items[row]
        target = self._targets()[target_index]
        units = self._review_units_for_target(target)
        if unit_index < 0 or unit_index >= len(units):
            return False
        for label in units[unit_index][1]:
            choice = self._initial_choice(target, label)
            if choice is None:
                return False
            if choice == 'manual':
                manual = target.get('manual_values')
                if not isinstance(manual, Mapping) or label not in manual:
                    return False
        return True

    def _update_decision_summary(self) -> None:
        if self._archive_requested():
            self.decision_summary.setText(
                "Marked for archive. Excluded from Builder analysis; no files "
                "will be moved until a later archive operation."
            )
            self.save_button.setEnabled(True)
            return
        choices = list(self._choices.values())
        pending = sum(choice is None for choice in choices)
        missing_manual = sum(
            choice == "manual" and label not in self._manual_values
            for label, choice in self._choices.items()
        )
        if pending:
            text = f"Choose a result for {pending} remaining point(s)."
        elif missing_manual:
            text = f"Enter or pick {missing_manual} remaining manual value(s)."
        else:
            counts = {
                choice: choices.count(choice)
                for choice in ("auto", "manual", "not_observed")
            }
            text = (
                f"Chosen: {counts['auto']} auto, {counts['manual']} manual, "
                f"{counts['not_observed']} not observed."
            )
        if self.exclude_check.isChecked():
            text += " Excluded from Builder analysis."
        elif self._current_target_ready() and not self._all_targets_ready():
            text += " Review the remaining target(s) before saving."
        self.decision_summary.setText(text)
        self.save_button.setEnabled(
            self._current_target_ready()
            if self._queue_mode
            else self._all_targets_ready()
        )

    def _target_controls_changed(self, *_args: object) -> None:
        if self._loading:
            return
        self._store_target_controls()
        self._draw_target()
        self._update_decision_summary()

    def _store_target_controls(self) -> None:
        if (
            self._loading
            or self._archive_requested()
            or not self._targets()
            or self._target_index < 0
            or self._target_index >= len(self._targets())
        ):
            return
        target = self._targets()[self._target_index]
        auto = dict(target.get("auto_values") or {})
        active_labels = set(self._choices)
        applicable_labels = set(self._labels_for_target(target))
        current_annealing = (
            self.payload.get("experiment_family") == "current_annealing"
        )
        prior_manual = dict(target.get("manual_values") or {})
        prior_final = dict(target.get("final_values") or {})
        prior_cleared = set(str(label) for label in target.get("cleared_labels", ()))
        unavailable_labels = {
            label
            for label in set(prior_manual) | set(prior_final) | prior_cleared
            if current_annealing
            and re.fullmatch(r"(?:As|Af|Ms|Mf)\d+", str(label))
            and label not in applicable_labels
        }
        if unavailable_labels:
            target["branch_unavailable_review"] = {
                "manual_values": {
                    label: float(prior_manual[label])
                    for label in unavailable_labels
                    if label in prior_manual
                },
                "final_values": {
                    label: float(prior_final[label])
                    for label in unavailable_labels
                    if label in prior_final
                },
                "cleared_labels": sorted(prior_cleared & unavailable_labels),
            }
        else:
            target.pop("branch_unavailable_review", None)
        manual = {
            str(label): float(value)
            for label, value in prior_manual.items()
            if label not in active_labels
            and (not current_annealing or label in applicable_labels)
        }
        manual.update(
            {
                label: self._manual_values[label]
                for label, choice in self._choices.items()
                if choice == "manual" and label in self._manual_values
            }
        )
        cleared = {
            str(label)
            for label in prior_cleared
            if label not in active_labels
            and (not current_annealing or label in applicable_labels)
        }
        cleared.update(
            label
            for label, choice in self._choices.items()
            if choice == "not_observed"
        )
        final = {
            str(label): float(value)
            for label, value in prior_final.items()
            if label not in active_labels
            and (not current_annealing or label in applicable_labels)
        }
        final.update(
            {
                label: float(auto[label])
                for label, choice in self._choices.items()
                if choice == "auto" and label in auto
            }
        )
        final.update(
            {
                label: self._manual_values[label]
                for label, choice in self._choices.items()
                if choice == "manual" and label in self._manual_values
            }
        )
        target["manual_values"] = manual
        target["final_values"] = final
        target["cleared_labels"] = sorted(cleared)
        all_choices = {
            label: (
                self._choices[label]
                if label in self._choices
                else self._initial_choice(target, label)
            )
            for label in self._labels_for_target(target)
        }
        ready = all(choice is not None for choice in all_choices.values())
        selected = set(all_choices.values())
        if not ready:
            base_status = "unreviewed"
        elif selected == {"auto"}:
            base_status = "accepted_auto"
        elif selected == {"not_observed"}:
            base_status = "no_transition"
            target["final_values"] = {}
        else:
            base_status = "manual_adjusted"
        excluded = self.exclude_check.isChecked() and ready
        status = "excluded" if excluded else base_status
        target["status"] = status
        target["included"] = not excluded and base_status in {
            "accepted_auto",
            "manual_adjusted",
        }
        target["analysis_included"] = not excluded and base_status in {
            "accepted_auto",
            "manual_adjusted",
            "no_transition",
        }
        plot = self.plots.get(str(target.get("target_key") or ""))
        if plot is not None and plot.derives_transition_strain:
            target["strain_at_transition_pct"] = (
                {}
                if base_status == "no_transition"
                else tma_core.interpolate_transition_strain_pct(
                    plot.x,
                    plot.y,
                    target.get("final_values") or {},
                )
            )
            if plot.strain_reference:
                target["strain_reference"] = dict(plot.strain_reference)
        self._update_derived_strain_label()

    def _target_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._navigation_items):
            return
        self._store_target_controls()
        target_index, unit_index = self._navigation_items[row]
        self._target_index = target_index
        target = self._targets()[target_index]
        self._loading = True
        self.exclude_check.setChecked(
            not self._archive_requested()
            and str(target.get("status") or "") == "excluded"
        )
        self._populate_review_units(target, selected_index=unit_index)
        self._populate_values_table(target)
        self._update_cooling_branch_control(target)
        self._loading = False
        self._selected_row_changed()
        self._update_decision_summary()
        self._draw_target()

    def _marker_item(
        self,
        pool: dict[str, pg.InfiniteLine],
        label: str,
        *,
        color: str,
        dashed: bool,
        movable: bool,
    ) -> pg.InfiniteLine:
        marker = pool.get(label)
        if marker is not None:
            marker.setMovable(movable)
            return marker
        style = (
            QtCore.Qt.PenStyle.DashLine
            if dashed
            else QtCore.Qt.PenStyle.SolidLine
        )
        marker = pg.InfiniteLine(
            angle=90,
            movable=movable,
            pen=pg.mkPen(color, width=1.4, style=style),
            hoverPen=pg.mkPen("#f59e0b", width=2.0),
            label=label,
            labelOpts={"position": 0.92, "color": color},
        )
        if movable:
            marker.sigPositionChangeFinished.connect(
                lambda moved, selected_label=label: self._manual_marker_moved(
                    selected_label, float(moved.value())
                )
            )
        self.plot_item.addItem(marker)
        pool[label] = marker
        return marker

    def _hide_markers(self) -> None:
        for marker in (
            *self._auto_marker_items.values(),
            *self._manual_marker_items.values(),
        ):
            marker.hide()

    def _draw_target(self) -> None:
        self._hide_markers()
        target = self._targets()[self._target_index]
        plot = self.plots.get(str(target.get("target_key") or ""))
        if plot is None:
            self.heating_curve_item.setData([], [])
            self.cooling_curve_item.setData([], [])
            self.heating_symbol_item.setData([], [])
            self.cooling_symbol_item.setData([], [])
            self.plot_item.setTitle("Plot unavailable")
            return
        unit_title = self.review_unit_combo.currentText()
        unit_series = (plot.unit_series or {}).get(unit_title)
        plot_x, plot_y = unit_series if unit_series is not None else (plot.x, plot.y)
        self.heating_curve_item.setPen(pg.mkPen('#ef4444', width=1.6))
        self.cooling_curve_item.setPen(pg.mkPen('#3b82f6', width=1.6))

        def set_curve_data(
            item: pg.PlotDataItem,
            symbol_item: pg.PlotDataItem,
            line_x: Any,
            line_y: Any,
            marker_x: Any | None = None,
            marker_y: Any | None = None,
        ) -> None:
            item.setData(line_x, line_y, connect='finite')
            marker_x = line_x if marker_x is None else marker_x
            marker_y = line_y if marker_y is None else marker_y
            count = min(len(marker_x), len(marker_y))
            if not count:
                symbol_item.setData([], [])
                return
            stride = max(1, math.ceil(count / 180))
            symbol_item.setData(marker_x[::stride], marker_y[::stride])

        def set_branch(
            item: pg.PlotDataItem,
            symbol_item: pg.PlotDataItem,
            series_pair: tuple[pd.Series, pd.Series] | None,
        ) -> bool:
            if series_pair is None:
                item.setData([], [])
                symbol_item.setData([], [])
                return False
            branch_x = pd.to_numeric(series_pair[0], errors='coerce')
            branch_y = pd.to_numeric(series_pair[1], errors='coerce')
            valid = branch_x.notna() & branch_y.notna()
            set_curve_data(
                item,
                symbol_item,
                branch_x.where(valid).to_numpy(dtype=float),
                branch_y.where(valid).to_numpy(dtype=float),
                branch_x.loc[valid].to_numpy(dtype=float),
                branch_y.loc[valid].to_numpy(dtype=float),
            )
            return True

        unit_branch = (plot.unit_branches or {}).get(unit_title)
        cooling_enabled = bool(
            self.cooling_branch_check.isChecked()
            if unit_branch is not None
            else True
        )
        heating_series = plot.heating_series
        cooling_series = plot.cooling_series
        if unit_branch is not None:
            heating_frame = unit_branch.heating
            if not cooling_enabled and unit_branch.cooling is not None:
                heating_frame = pd.concat(
                    (unit_branch.heating, unit_branch.cooling),
                    axis=0,
                )
            heating_series = (
                heating_frame["I_mA"],
                heating_frame["R_Ohm"],
            )
            cooling_series = (
                (
                    unit_branch.cooling["I_mA"],
                    unit_branch.cooling["R_Ohm"],
                )
                if cooling_enabled and unit_branch.cooling is not None
                else None
            )
        explicit_branches = (
            set_branch(
                self.heating_curve_item,
                self.heating_symbol_item,
                heating_series,
            ),
            set_branch(
                self.cooling_curve_item,
                self.cooling_symbol_item,
                cooling_series,
            ),
        )
        if not any(explicit_branches):
            x = pd.to_numeric(plot_x, errors="coerce")
            y = pd.to_numeric(plot_y, errors="coerce")
            valid = x.notna() & y.notna()
            x_values = x.loc[valid].to_numpy(dtype=float)
            y_values = y.loc[valid].to_numpy(dtype=float)
            if x_values.size:
                peak_index = int(x_values.argmax())
                heating_end = peak_index + 1
                set_curve_data(
                    self.heating_curve_item,
                    self.heating_symbol_item,
                    x_values[:heating_end], y_values[:heating_end]
                )
                set_curve_data(
                    self.cooling_curve_item,
                    self.cooling_symbol_item,
                    x_values[peak_index:], y_values[peak_index:]
                )
            else:
                self.heating_curve_item.setData([], [])
                self.cooling_curve_item.setData([], [])
                self.heating_symbol_item.setData([], [])
                self.cooling_symbol_item.setData([], [])
        if cooling_series is None and self._cooling_legend_visible:
            self.legend.removeItem(self.cooling_curve_item)
            self._cooling_legend_visible = False
        elif cooling_series is not None and not self._cooling_legend_visible:
            self.legend.addItem(self.cooling_curve_item, "Cooling")
            self._cooling_legend_visible = True
        self.plot_item.setLabel('bottom', plot.x_label, units=plot.x_unit)
        self.plot_item.setLabel("left", plot.y_label)
        self.plot_item.setTitle(plot.title)
        self.values_box.setTitle(f'Transition choices ({plot.value_unit})')
        self.manual_graph_hint.setText(
            f'{plot.value_unit} \N{MIDDLE DOT} or click graph'
        )
        auto = (
            target.get("auto_values")
            if isinstance(target.get("auto_values"), Mapping)
            else {}
        )
        for label, value in auto.items():
            if label not in self._active_unit_labels:
                continue
            if self._choices.get(label) == "not_observed":
                continue
            marker = self._marker_item(
                self._auto_marker_items,
                label,
                color="#9ca3af",
                dashed=True,
                movable=False,
            )
            marker.setPos(float(value))
            marker.show()
        for label, value in self._manual_values.items():
            if self._choices.get(label) != "manual":
                continue
            marker = self._marker_item(
                self._manual_marker_items,
                label,
                color="#dc2626",
                dashed=False,
                movable=True,
            )
            marker.setPos(float(value))
            marker.show()
        self.plot_item.enableAutoRange()

    def _set_manual_plot_value(self, value: float) -> None:
        label = self._selected_label()
        if not label or not math.isfinite(value):
            return
        self._choices[label] = "manual"
        self._manual_values[label] = float(value)
        self._update_choice_row(label)
        self._selected_row_changed()
        self._target_controls_changed()

    def _plot_scene_clicked(self, event: object) -> None:
        button = getattr(event, "button", lambda: None)()
        if button not in {None, QtCore.Qt.MouseButton.LeftButton}:
            return
        scene_position = getattr(event, "scenePos", lambda: None)()
        if scene_position is None:
            return
        view_box = self.plot_item.getViewBox()
        if not self.plot_item.sceneBoundingRect().contains(scene_position):
            return
        value = view_box.mapSceneToView(scene_position).x()
        self._set_manual_plot_value(float(value))

    def _manual_marker_moved(self, label: str, value: float) -> None:
        if label not in self._choices or not math.isfinite(value):
            return
        row = self._row_for_label(label)
        if row >= 0:
            self.values_table.selectRow(row)
        self._choices[label] = "manual"
        self._manual_values[label] = float(value)
        self._update_choice_row(label)
        self._selected_row_changed()
        self._store_target_controls()
        self._update_decision_summary()

    def _plot_clicked(self, event: Any) -> None:
        if event.inaxes is None or event.xdata is None:
            return
        self._set_manual_plot_value(float(event.xdata))

    def _write_review(self) -> None:
        self.payload['review_revision'] = int(
            self.payload.get('review_revision', 0) or 0
        ) + 1
        self.payload['updated_utc'] = utc_now_text()
        if self._save_callback is not None:
            self._save_callback(copy.deepcopy(self.payload))
        else:
            atomic_write_review(self.sidecar_path, self.payload)

    def _save_and_accept(self) -> None:
        self._store_target_controls()
        self._update_decision_summary()
        ready = (
            self._current_target_ready()
            if self._queue_mode
            else self._all_targets_ready()
        )
        if not ready:
            return
        self._write_review()
        if self._queue_mode:
            self.advanceRequested.emit()
            return
        self.accept()



@dataclass(frozen=True)
class ReviewUnitSummary:
    label: str
    state: str
    tooltip: str = ""


_REVIEW_STATE_DISPLAY = {
    "accepted": ("● Accepted", "#22c55e"),
    "manual": ("● Manual", "#22c55e"),
    "no_transition": ("● No transition", "#60a5fa"),
    "excluded": ("● Excluded", "#ef4444"),
    "archive_requested": ("◆ Archive requested", "#ef4444"),
    "partial": ("◐ Partial", "#f59e0b"),
    "unreviewed": ("○ Unreviewed", "#9ca3af"),
    "needs_attention": ("⚠ Needs attention", "#f59e0b"),
    "load_failed": ("⚠ Load failed", "#ef4444"),
}


def _review_unit_state(
    target: Mapping[str, Any], labels: Sequence[str]
) -> tuple[str, str]:
    status = str(target.get("status") or "unreviewed").strip().casefold()
    auto = target.get("auto_values") if isinstance(target.get("auto_values"), Mapping) else {}
    manual = target.get("manual_values") if isinstance(target.get("manual_values"), Mapping) else {}
    final = target.get("final_values") if isinstance(target.get("final_values"), Mapping) else {}
    cleared = {str(label) for label in target.get("cleared_labels", ())}
    choices: list[str | None] = []
    details: list[str] = []
    for label in labels:
        if status == "no_transition" or label in cleared:
            choice = "not_observed"
        elif label in manual:
            choice = "manual"
        elif label in final:
            choice = "manual" if status == "manual_adjusted" else "auto"
        elif label in auto and status == "accepted_auto":
            choice = "auto"
        else:
            choice = None
        choices.append(choice)
        if label in final:
            origin = "manual" if label in manual else "auto"
            details.append(f"{label}={float(final[label]):.6g} ({origin})")
        elif choice == "not_observed":
            details.append(f"{label}=not observed")
    if status in {"needs_attention", "invalid", "error"}:
        state = "needs_attention"
    elif status == "excluded":
        state = "excluded"
    elif not choices or all(choice is None for choice in choices):
        state = "unreviewed"
    elif any(choice is None for choice in choices):
        state = "partial"
    elif all(choice == "not_observed" for choice in choices):
        state = "no_transition"
    elif any(choice in {"manual", "not_observed"} for choice in choices):
        state = "manual"
    else:
        state = "accepted"
    return state, "; ".join(details)


def _review_units_from_payload(
    payload: Mapping[str, Any] | None,
) -> tuple[ReviewUnitSummary, ...]:
    if not isinstance(payload, Mapping):
        return ()
    family = str(payload.get("experiment_family") or "")
    archive_requested = payload.get("archive_requested") is True
    summaries: list[ReviewUnitSummary] = []
    targets = payload.get("targets")
    if not isinstance(targets, Sequence) or isinstance(targets, (str, bytes)):
        return ()
    for target in targets:
        if not isinstance(target, Mapping):
            continue
        labels = {
            str(label)
            for field in ("auto_values", "manual_values", "final_values")
            for label in (
                target.get(field, {}).keys()
                if isinstance(target.get(field), Mapping)
                else ()
            )
        }
        labels.update(str(label) for label in target.get("cleared_labels", ()))
        if family == "current_annealing":
            loops = sorted(
                {
                    parts[1]
                    for label in labels
                    if (parts := _transition_label_parts(label)) is not None
                    and parts[1] is not None
                }
            )
            if not loops:
                loops = [1, 2]
            units = [
                (
                    f"Cycle {loop}",
                    [f"{point}{loop}" for point in ("As", "Af", "Ms", "Mf")],
                )
                for loop in loops
            ]
        else:
            display = str(
                target.get("display_label")
                or target.get("target_key")
                or "Transitions"
            )
            units = [(display, [point for point in ("As", "Af", "Ms", "Mf")])]
        for label, unit_labels in units:
            if archive_requested:
                state = "archive_requested"
                tooltip = "Complete measurement run marked for later archiving."
            else:
                state, tooltip = _review_unit_state(target, unit_labels)
            summaries.append(ReviewUnitSummary(label, state, tooltip))
    return tuple(summaries)


@dataclass(frozen=True)
class ReviewQueueEntry:
    sample_label: str
    run_label: str
    builder: Callable[[QtWidgets.QWidget], PortableTransitionReviewDialog]
    saved: bool = False
    review_units: tuple[ReviewUnitSummary, ...] = ()

    @property
    def label(self) -> str:
        return " · ".join(
            part for part in (self.sample_label, self.run_label) if part
        )


class PortableTransitionReviewQueueDialog(QtWidgets.QDialog):
    """Lazily navigate every sample, run, and cycle in one review popup."""

    def __init__(
        self,
        entries: Sequence[ReviewQueueEntry],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.entries = list(entries)
        self._editors: dict[int, PortableTransitionReviewDialog] = {}
        self._run_items: list[QtWidgets.QTreeWidgetItem] = []
        self._saved_indices: set[int] = set()
        self._current_index: int | None = None
        self.setWindowTitle("Transition review")
        self.resize(1280, 720)
        self.setMinimumSize(900, 560)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        hint = QtWidgets.QLabel(
            "Select any sample, run, and cycle or stress target. "
            "Measurements load only when first opened."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, self)
        root.addWidget(splitter, 1)

        navigation = QtWidgets.QWidget(splitter)
        navigation.setMinimumWidth(250)
        navigation.setMaximumWidth(540)
        navigation_layout = QtWidgets.QVBoxLayout(navigation)
        navigation_layout.setContentsMargins(0, 0, 4, 0)
        navigation_heading = QtWidgets.QHBoxLayout()
        navigation_heading.addWidget(QtWidgets.QLabel("Samples / runs / cycles"))
        navigation_heading.addStretch(1)
        navigation_heading.addWidget(QtWidgets.QLabel("Show:"))
        self.review_filter = QtWidgets.QComboBox(navigation)
        self.review_filter.addItems(
            ["All", "Unreviewed", "Reviewed", "Excluded", "Archive requested"]
        )
        self.review_filter.setCurrentText("Unreviewed")
        navigation_heading.addWidget(self.review_filter)
        navigation_layout.addLayout(navigation_heading)
        self.tree = QtWidgets.QTreeWidget(navigation)
        self.tree.setHeaderLabels(["Sample / run / cycle", "Review"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tree.installEventFilter(self)
        tree_header = self.tree.header()
        tree_header.setStretchLastSection(False)
        tree_header.setMinimumSectionSize(64)
        tree_header.setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        tree_header.setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Interactive
        )
        tree_header.resizeSection(1, 112)
        navigation_layout.addWidget(self.tree, 1)
        splitter.addWidget(navigation)

        self.editor_host = QtWidgets.QWidget(splitter)
        self.editor_layout = QtWidgets.QVBoxLayout(self.editor_host)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.placeholder = QtWidgets.QLabel("Select a sample or run to load its review.")
        self.placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.editor_layout.addWidget(self.placeholder, 1)
        splitter.addWidget(self.editor_host)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([440, 840])

        footer = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel()
        footer.addWidget(self.status_label, 1)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        root.addLayout(footer)

        sample_items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for index, entry in enumerate(self.entries):
            sample_label = entry.sample_label or "Unknown sample"
            sample_item = sample_items.get(sample_label)
            if sample_item is None:
                sample_item = QtWidgets.QTreeWidgetItem([sample_label, ""])
                sample_item.setData(
                    0, QtCore.Qt.ItemDataRole.UserRole, ("sample", -1, -1)
                )
                self.tree.addTopLevelItem(sample_item)
                sample_items[sample_label] = sample_item
            run_item = QtWidgets.QTreeWidgetItem(
                [entry.run_label or entry.label, ""]
            )
            run_item.setData(
                0, QtCore.Qt.ItemDataRole.UserRole, ("run", index, -1)
            )
            sample_item.addChild(run_item)
            sample_item.setExpanded(True)
            self._run_items.append(run_item)
            self._populate_run_units(index, entry.review_units)
        self.tree.currentItemChanged.connect(self._selection_changed)
        self.review_filter.currentTextChanged.connect(self._apply_review_filter)
        self._refresh_review_hierarchy()
        self._apply_review_filter()
        self._update_status()
        if self._run_items:
            QtCore.QTimer.singleShot(
                0, self._select_first_visible_item
            )

    @property
    def completed_count(self) -> int:
        return len(self._saved_indices)

    @staticmethod
    def _is_reviewed_state(state: str) -> bool:
        return state in {
            "accepted",
            "manual",
            "no_transition",
            "excluded",
            "archive_requested",
        }

    def _set_review_state(
        self,
        item: QtWidgets.QTreeWidgetItem,
        state: str,
        tooltip: str = "",
    ) -> None:
        label, color = _REVIEW_STATE_DISPLAY.get(
            state, _REVIEW_STATE_DISPLAY["unreviewed"]
        )
        item.setData(1, QtCore.Qt.ItemDataRole.UserRole, state)
        item.setText(1, label)
        item.setForeground(1, QtGui.QBrush(QtGui.QColor(color)))
        item.setToolTip(1, tooltip)

    def _populate_run_units(
        self,
        run_index: int,
        summaries: Sequence[ReviewUnitSummary],
    ) -> None:
        run_item = self._run_items[run_index]
        blocker = QtCore.QSignalBlocker(self.tree)
        try:
            while run_item.childCount():
                run_item.removeChild(run_item.child(0))
            for navigation_row, summary in enumerate(summaries):
                child = QtWidgets.QTreeWidgetItem([summary.label, ""])
                child.setData(
                    0,
                    QtCore.Qt.ItemDataRole.UserRole,
                    ("unit", run_index, navigation_row),
                )
                self._set_review_state(child, summary.state, summary.tooltip)
                run_item.addChild(child)
            run_item.setExpanded(True)
            if not summaries:
                run_item.setData(
                    1, QtCore.Qt.ItemDataRole.UserRole, "unreviewed"
                )
        finally:
            del blocker

    def _run_unit_states(
        self, run_item: QtWidgets.QTreeWidgetItem
    ) -> list[str]:
        if run_item.childCount():
            return [
                str(
                    run_item.child(row).data(
                        1, QtCore.Qt.ItemDataRole.UserRole
                    )
                    or "unreviewed"
                )
                for row in range(run_item.childCount())
            ]
        return [
            str(
                run_item.data(1, QtCore.Qt.ItemDataRole.UserRole)
                or "unreviewed"
            )
        ]

    def _refresh_review_hierarchy(self) -> None:
        for run_item in self._run_items:
            states = self._run_unit_states(run_item)
            reviewed = sum(self._is_reviewed_state(state) for state in states)
            total = len(states)
            run_item.setText(1, f"{reviewed}/{total} reviewed")
            color = "#22c55e" if reviewed == total else (
                "#f59e0b" if reviewed else "#9ca3af"
            )
            run_item.setForeground(1, QtGui.QBrush(QtGui.QColor(color)))
            run_item.setToolTip(
                1,
                ", ".join(
                    state.replace("_", " ").title()
                    for state in states
                ),
            )
        for sample_row in range(self.tree.topLevelItemCount()):
            sample_item = self.tree.topLevelItem(sample_row)
            states = [
                state
                for run_row in range(sample_item.childCount())
                for state in self._run_unit_states(sample_item.child(run_row))
            ]
            reviewed = sum(self._is_reviewed_state(state) for state in states)
            total = len(states)
            sample_item.setText(1, f"{reviewed}/{total} reviewed")
            color = "#22c55e" if total and reviewed == total else (
                "#f59e0b" if reviewed else "#9ca3af"
            )
            sample_item.setForeground(1, QtGui.QBrush(QtGui.QColor(color)))

    @staticmethod
    def _state_matches_filter(state: str, selected_filter: str) -> bool:
        if selected_filter == "All":
            return True
        if selected_filter == "Excluded":
            return state == "excluded"
        if selected_filter == "Archive requested":
            return state == "archive_requested"
        reviewed = PortableTransitionReviewQueueDialog._is_reviewed_state(state)
        if selected_filter == "Reviewed":
            return reviewed and state != "excluded"
        return not reviewed

    def _apply_review_filter(
        self, _value: str = "", *, select_if_hidden: bool = True
    ) -> None:
        selected_filter = self.review_filter.currentText()
        for sample_row in range(self.tree.topLevelItemCount()):
            sample_item = self.tree.topLevelItem(sample_row)
            sample_visible = False
            for run_row in range(sample_item.childCount()):
                run_item = sample_item.child(run_row)
                run_visible = False
                if run_item.childCount():
                    states = self._run_unit_states(run_item)
                    keep_incomplete_run_together = (
                        selected_filter == "Unreviewed"
                        and any(
                            not self._is_reviewed_state(state)
                            for state in states
                        )
                    )
                    for unit_row in range(run_item.childCount()):
                        unit_item = run_item.child(unit_row)
                        state = str(
                            unit_item.data(
                                1, QtCore.Qt.ItemDataRole.UserRole
                            )
                            or "unreviewed"
                        )
                        visible = keep_incomplete_run_together or (
                            self._state_matches_filter(state, selected_filter)
                        )
                        unit_item.setHidden(not visible)
                        run_visible = run_visible or visible
                else:
                    run_visible = any(
                        self._state_matches_filter(state, selected_filter)
                        for state in self._run_unit_states(run_item)
                    )
                run_item.setHidden(not run_visible)
                sample_visible = sample_visible or run_visible
            sample_item.setHidden(not sample_visible)
        current = self.tree.currentItem()
        if select_if_hidden and (current is None or current.isHidden()):
            self._select_first_visible_item()

    def _select_first_visible_item(self) -> None:
        for sample_row in range(self.tree.topLevelItemCount()):
            sample_item = self.tree.topLevelItem(sample_row)
            if sample_item.isHidden():
                continue
            for run_row in range(sample_item.childCount()):
                run_item = sample_item.child(run_row)
                if run_item.isHidden():
                    continue
                for unit_row in range(run_item.childCount()):
                    unit_item = run_item.child(unit_row)
                    if not unit_item.isHidden():
                        self.tree.setCurrentItem(unit_item)
                        return
                self.tree.setCurrentItem(run_item)
                return

    def _update_status(self, message: str = "") -> None:
        saved_total = sum(
            entry.saved or index in self._saved_indices
            for index, entry in enumerate(self.entries)
        )
        summary = (
            f"{len(self.entries)} run(s) · {saved_total} saved review(s) · "
            f"{self.completed_count} saved in this session"
        )
        self.status_label.setText(f"{message}  {summary}".strip())

    def _review_units_from_editor(
        self, editor: PortableTransitionReviewDialog
    ) -> tuple[ReviewUnitSummary, ...]:
        summaries: list[ReviewUnitSummary] = []
        targets = editor._targets()  # noqa: SLF001
        for navigation_row, (target_index, unit_index) in enumerate(
            editor._navigation_items  # noqa: SLF001
        ):
            target = targets[target_index]
            units = editor._review_units_for_target(target)  # noqa: SLF001
            unit_title, labels = units[unit_index]
            if editor._archive_requested():  # noqa: SLF001
                state = "archive_requested"
                tooltip = "Complete measurement run marked for later archiving."
            else:
                state, tooltip = _review_unit_state(target, labels)
            item = editor.target_list.item(navigation_row)
            label = item.text() if item is not None else unit_title
            summaries.append(ReviewUnitSummary(label, state, tooltip))
        return tuple(summaries)

    def _refresh_run_from_editor(
        self, run_index: int, *, select_if_hidden: bool = False
    ) -> None:
        editor = self._editors.get(run_index)
        if editor is None:
            return
        self._populate_run_units(run_index, self._review_units_from_editor(editor))
        self._refresh_review_hierarchy()
        self._apply_review_filter(select_if_hidden=select_if_hidden)

    def _build_editor(self, run_index: int) -> PortableTransitionReviewDialog | None:
        existing = self._editors.get(run_index)
        if existing is not None:
            return existing
        entry = self.entries[run_index]
        run_item = self._run_items[run_index]
        run_item.setText(1, "Loading...")
        run_item.setForeground(1, QtGui.QBrush(QtGui.QColor("#60a5fa")))
        QtWidgets.QApplication.processEvents(
            QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
        )
        try:
            editor = entry.builder(self.editor_host)
        except Exception as exc:
            run_item.setData(1, QtCore.Qt.ItemDataRole.UserRole, "load_failed")
            self._set_review_state(run_item, "load_failed", str(exc))
            run_item.setToolTip(0, str(exc))
            self._apply_review_filter()
            self._update_status(f"Could not load {entry.label}: {exc}")
            return None
        editor.setWindowFlags(QtCore.Qt.WindowType.Widget)
        editor._queue_mode = True  # noqa: SLF001
        editor._update_archive_controls()  # noqa: SLF001
        editor.target_panel.hide()
        editor.heading.setText(
            f"{entry.label} · saves {editor.sidecar_path.name} beside this measurement"
        )
        editor.save_button.setText('Save and continue')
        for button_box in editor.findChildren(QtWidgets.QDialogButtonBox):
            cancel_button = button_box.button(
                QtWidgets.QDialogButtonBox.StandardButton.Cancel
            )
            if cancel_button is not None:
                cancel_button.hide()
        editor.accepted.connect(
            lambda selected_index=run_index: self._editor_saved(selected_index)
        )
        editor.advanceRequested.connect(
            lambda selected_index=run_index: self._advance_editor(selected_index)
        )
        self.editor_layout.addWidget(editor, 1)
        editor.hide()
        self._editors[run_index] = editor

        self._refresh_run_from_editor(run_index)
        return editor

    def _show_editor(self, run_index: int, navigation_row: int = 0) -> None:
        if self._current_index is not None and self._current_index != run_index:
            current = self._editors.get(self._current_index)
            if current is not None:
                current._store_target_controls()  # noqa: SLF001
                self._refresh_run_from_editor(
                    self._current_index, select_if_hidden=False
                )
                current.hide()
        editor = self._build_editor(run_index)
        if editor is None:
            return
        self.placeholder.hide()
        self._current_index = run_index
        if editor.target_list.count():
            navigation_row = min(
                max(int(navigation_row), 0), editor.target_list.count() - 1
            )
            editor.target_list.setCurrentRow(navigation_row)
            final_unit = navigation_row == editor.target_list.count() - 1
            next_kind = (
                'target'
                if editor.payload.get('experiment_family') == 'tma'
                else 'cycle'
            )
            editor.save_button.setText(
                'Save run and next' if final_unit else f'Save and next {next_kind}'
            )
            editor._update_decision_summary()  # noqa: SLF001
        for other_index, other_editor in self._editors.items():
            if other_index != run_index:
                other_editor.hide()
        editor.show()
        self._update_status(self.entries[run_index].label)

    def eventFilter(
        self, watched: QtCore.QObject, event: QtCore.QEvent
    ) -> bool:
        if (
            watched is self.tree
            and event.type() == QtCore.QEvent.Type.KeyPress
            and isinstance(event, QtGui.QKeyEvent)
        ):
            if event.key() == QtCore.Qt.Key.Key_Up:
                self._move_review_selection(-1)
                event.accept()
                return True
            if event.key() == QtCore.Qt.Key.Key_Down:
                self._move_review_selection(1)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _select_review_unit(self, run_index: int, navigation_row: int) -> bool:
        if not 0 <= run_index < len(self._run_items):
            return False
        editor = self._build_editor(run_index)
        if editor is None:
            return False
        run_item = self._run_items[run_index]
        if not run_item.childCount():
            self._show_editor(run_index, 0)
            return True
        row = min(max(int(navigation_row), 0), run_item.childCount() - 1)
        self.tree.setCurrentItem(run_item.child(row))
        return True

    def _visual_run_indices(self) -> list[int]:
        """Return run indices in their actual displayed sample-group order."""

        visual_indices: list[int] = []
        for sample_row in range(self.tree.topLevelItemCount()):
            sample_item = self.tree.topLevelItem(sample_row)
            for run_row in range(sample_item.childCount()):
                run_item = sample_item.child(run_row)
                ref = run_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if (
                    isinstance(ref, tuple)
                    and len(ref) == 3
                    and ref[0] == "run"
                    and not run_item.isHidden()
                ):
                    visual_indices.append(int(ref[1]))
        return visual_indices

    @staticmethod
    def _visible_unit_rows(
        run_item: QtWidgets.QTreeWidgetItem,
    ) -> list[int]:
        return [
            row
            for row in range(run_item.childCount())
            if not run_item.child(row).isHidden()
        ]

    def _adjacent_visual_run(self, run_index: int, step: int) -> int | None:
        visual_indices = self._visual_run_indices()
        try:
            visual_row = visual_indices.index(run_index)
        except ValueError:
            return None
        adjacent_row = visual_row + (1 if step > 0 else -1)
        if not 0 <= adjacent_row < len(visual_indices):
            return None
        return visual_indices[adjacent_row]

    def _move_review_selection(self, step: int) -> bool:
        current = self.tree.currentItem()
        if current is None:
            return self._select_review_unit(0, 0)
        ref = current.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(ref, tuple) or len(ref) != 3:
            return False
        kind, run_index, navigation_row = ref
        run_index = int(run_index)
        navigation_row = int(navigation_row)
        if kind == "unit":
            run_item = self._run_items[run_index]
            visible_rows = self._visible_unit_rows(run_item)
            if navigation_row in visible_rows:
                visible_index = visible_rows.index(navigation_row) + step
                if 0 <= visible_index < len(visible_rows):
                    return self._select_review_unit(
                        run_index, visible_rows[visible_index]
                    )
            adjacent_run = self._adjacent_visual_run(run_index, step)
            if adjacent_run is None:
                return True
            if step > 0:
                adjacent_rows = self._visible_unit_rows(
                    self._run_items[adjacent_run]
                )
                return self._select_review_unit(
                    adjacent_run, adjacent_rows[0] if adjacent_rows else 0
                )
            editor = self._build_editor(adjacent_run)
            if editor is None:
                return False
            adjacent_rows = self._visible_unit_rows(
                self._run_items[adjacent_run]
            )
            return self._select_review_unit(
                adjacent_run,
                adjacent_rows[-1]
                if adjacent_rows
                else max(editor.target_list.count() - 1, 0),
            )
        if kind == "run":
            target_run = (
                run_index
                if step > 0
                else self._adjacent_visual_run(run_index, step)
            )
            if target_run is None:
                return True
            editor = self._build_editor(target_run)
            if editor is None:
                return False
            row = 0 if step > 0 else max(editor.target_list.count() - 1, 0)
            return self._select_review_unit(target_run, row)
        if kind == "sample" and current.childCount():
            run_item = current.child(0 if step > 0 else current.childCount() - 1)
            run_ref = run_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(run_ref, tuple) and len(run_ref) == 3:
                target_run = int(run_ref[1])
                editor = self._build_editor(target_run)
                if editor is None:
                    return False
                row = 0 if step > 0 else max(editor.target_list.count() - 1, 0)
                return self._select_review_unit(target_run, row)
        return False

    def _selection_changed(
        self,
        current: QtWidgets.QTreeWidgetItem | None,
        _previous: QtWidgets.QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            return
        ref = current.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(ref, tuple) or len(ref) != 3:
            return
        kind, run_index, navigation_row = ref
        run_index = int(run_index)
        if kind in {"sample", "run"}:
            # Changing the current item recursively from this signal can leave
            # Qt displaying a group's highlight while its first cycle is shown.
            # Defer the transfer so the visible selection and editor agree.
            QtCore.QTimer.singleShot(
                0, lambda item=current: self._activate_group_selection(item)
            )
            return
        self._show_editor(run_index, int(navigation_row))

    def _activate_group_selection(
        self, item: QtWidgets.QTreeWidgetItem
    ) -> None:
        if self.tree.currentItem() is not item or item.isHidden():
            return
        ref = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(ref, tuple) or len(ref) != 3:
            return
        kind, run_index, _navigation_row = ref
        if kind == "sample":
            run_item = next(
                (
                    item.child(row)
                    for row in range(item.childCount())
                    if not item.child(row).isHidden()
                ),
                None,
            )
            if run_item is None:
                return
            run_ref = run_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if not isinstance(run_ref, tuple) or len(run_ref) != 3:
                return
            run_index = run_ref[1]
        elif kind != "run":
            return
        run_index = int(run_index)
        run_item = self._run_items[run_index]
        editor = self._build_editor(run_index)
        if editor is None:
            return
        visible_rows = self._visible_unit_rows(run_item)
        self._select_review_unit(
            run_index, visible_rows[0] if visible_rows else 0
        )

    def _advance_editor(self, run_index: int) -> None:
        editor = self._editors.get(run_index)
        if editor is None:
            return
        current_row = max(editor.target_list.currentRow(), 0)
        run_item = self._run_items[run_index]
        self._refresh_run_from_editor(run_index, select_if_hidden=False)
        if editor._archive_requested():  # noqa: SLF001
            self._editor_saved(run_index)
            return
        next_row = current_row + 1
        if next_row < editor.target_list.count():
            self.tree.setCurrentItem(run_item.child(next_row))
            self._update_status(
                f'Saved cycle/target {current_row + 1} of {editor.target_list.count()}.'
            )
            return
        if not editor._all_targets_ready():  # noqa: SLF001
            for row in range(editor.target_list.count()):
                if not editor._navigation_row_ready(row):  # noqa: SLF001
                    self.tree.setCurrentItem(run_item.child(row))
                    self._update_status('Review the remaining cycle/target before completing this run.')
                    return
        self._editor_saved(run_index)

    def _editor_saved(self, run_index: int) -> None:
        self._saved_indices.add(run_index)
        self._refresh_run_from_editor(run_index, select_if_hidden=False)
        self._update_status(f"Saved {self.entries[run_index].label}.")
        self._apply_review_filter()
        # Completing a run can hide it under the default Unreviewed filter.
        # In that case the filter has already selected and displayed the next
        # visible run; do not continue and re-show the editor we just left.
        if self._current_index != run_index:
            return
        next_index = self._adjacent_visual_run(run_index, 1)
        if next_index is not None and not self._run_items[next_index].isHidden():
            self.tree.setCurrentItem(self._run_items[next_index])
            return
        self._select_first_visible_item()
        editor = self._editors.get(run_index)
        if editor is not None:
            editor.show()

    def accept(self) -> None:
        if self._current_index is not None:
            editor = self._editors.get(self._current_index)
            if editor is not None:
                editor._store_target_controls()  # noqa: SLF001
        super().accept()

def _current_annealing_cycle_branches(
    frame: pd.DataFrame,
) -> dict[str, annealing_core.AnnealingReviewCycle]:
    """Return explicitly classified physical branches for each review cycle."""

    return {
        f"Cycle {index}": cycle
        for index, cycle in enumerate(
            annealing_core.split_review_cycles(frame),
            start=1,
        )
    }

def _build_current_annealing_review_dialog(
    parent: QtWidgets.QWidget,
    measurement_path: Path,
    *,
    sample: Mapping[str, Any] | None = None,
    initial_payload: Mapping[str, Any] | None = None,
) -> PortableTransitionReviewDialog:
    path = Path(measurement_path)
    sidecar = sidecar_path_for_measurement(path, family="current_annealing")
    draft = current_annealing_review_draft(path, sample=sample)
    payload = (
        load_review(sidecar)
        if sidecar.exists()
        else copy.deepcopy(dict(initial_payload))
        if isinstance(initial_payload, Mapping)
        else draft
    )
    if payload["measurement_fingerprint"] != draft["measurement_fingerprint"]:
        raise ValueError("Existing transition review belongs to different measurement content.")
    frame = annealing_core.load_file(str(path))
    review_frame = annealing_core.review_measurement_frame(frame)
    branches = _current_annealing_cycle_branches(review_frame)
    if not branches:
        branches = {
            "Cycle 1": annealing_core.AnnealingReviewCycle(
                heating=review_frame,
                cooling=None,
                cooling_recorded=False,
                cooling_reason="No usable measurement samples were recorded.",
            )
        }
    payload.setdefault("analysis", {})["branch_classifier"] = (
        "current_voltage_resistance_v1"
    )
    for target in payload.get("targets", ()):
        if not isinstance(target, dict) or target.get("target_key") != "graph":
            continue
        target["branch_availability"] = {
            title: {
                "heating_recorded": True,
                "cooling_recorded": cycle.cooling_recorded,
                "reason": cycle.cooling_reason,
            }
            for title, cycle in branches.items()
        }
    plot = ReviewPlot(
        review_frame["I_mA"],
        review_frame["R_Ohm"],
        annealing_core.measurement_display_name(path),
        "Resistance (ohm)",
        unit_series={
            title: (
                cycle.heating["I_mA"],
                cycle.heating["R_Ohm"],
            )
            for title, cycle in branches.items()
        },
        unit_branches=branches,
    )
    return PortableTransitionReviewDialog(
        payload, {"graph": plot}, sidecar, parent
    )


def review_current_annealing_file(
    parent: QtWidgets.QWidget,
    measurement_path: Path,
    *,
    sample: Mapping[str, Any] | None = None,
    queue_position: tuple[int, int] | None = None,
) -> bool:
    dialog = _build_current_annealing_review_dialog(
        parent, measurement_path, sample=sample
    )
    _apply_queue_context(dialog, queue_position)
    return dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted

def _build_tma_review_dialog(
    parent: QtWidgets.QWidget,
    run_path: Path,
    *,
    initial_payload: Mapping[str, Any] | None = None,
) -> PortableTransitionReviewDialog:
    run_dir = Path(run_path)
    sidecar = sidecar_path_for_measurement(run_dir, family="tma")
    draft = tma_review_draft(run_dir)
    payload = (
        load_review(sidecar)
        if sidecar.exists()
        else copy.deepcopy(dict(initial_payload))
        if isinstance(initial_payload, Mapping)
        else draft
    )
    if payload["measurement_fingerprint"] != draft["measurement_fingerprint"]:
        raise ValueError("Existing transition review belongs to different TMA run content.")
    run = tma_core.load_run(run_dir)
    plots: dict[str, ReviewPlot] = {}
    groups = tma_core.current_sweep_groups(run.frame)
    sweep_counts: dict[str, int] = {}
    for target, _group in groups:
        stress_key = f"{float(target):.9g}"
        sweep_counts[stress_key] = sweep_counts.get(stress_key, 0) + 1
    sweep_indices: dict[str, int] = {}
    for target, group in groups:
        stress_key = f"{float(target):.9g}"
        sweep_indices[stress_key] = sweep_indices.get(stress_key, 0) + 1
        sweep_index = sweep_indices[stress_key]
        sweep_count = sweep_counts[stress_key]
        key = f"stress_mpa:{stress_key}"
        if sweep_count > 1:
            key += f"|sweep:{sweep_index}"
        title = f"{run.sample_name} \N{MIDDLE DOT} {float(target):.6g} MPa"
        if sweep_count > 1:
            title += f" \N{MIDDLE DOT} sweep {sweep_index}/{sweep_count}"
        l0_mm = tma_core.group_l0_mm(run, group)
        plots[key] = ReviewPlot(
            pd.to_numeric(group["current_mA"], errors="coerce"),
            tma_core.strain_from_trace_minimum_length(run, group),
            title,
            "Strain (%) · per-target L₀",
            derives_transition_strain=True,
            strain_reference={
                "method": (
                    "per_target_minimum_length"
                    if l0_mm is not None
                    else "per_target_minimum_recorded_strain"
                ),
                **({"l0_mm": l0_mm} if l0_mm is not None else {}),
            },
        )
    return PortableTransitionReviewDialog(payload, plots, sidecar, parent)


def review_tma_run(
    parent: QtWidgets.QWidget,
    run_path: Path,
    *,
    queue_position: tuple[int, int] | None = None,
) -> bool:
    dialog = _build_tma_review_dialog(parent, run_path)
    _apply_queue_context(dialog, queue_position)
    return dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted


def _apply_queue_context(
    dialog: PortableTransitionReviewDialog,
    queue_position: tuple[int, int] | None,
) -> None:
    if queue_position is None:
        return
    index, total = queue_position
    dialog.setWindowTitle(f"Transition review - run {index}/{total}")
    dialog.heading.setText(
        f"Run {index}/{total} - "
        f"saves {dialog.sidecar_path.name} in this run folder"
    )
    dialog.save_button.setText("Save && next" if index < total else "Save review")


def _queue_labels(
    path: Path,
    sample: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    sample = sample if isinstance(sample, Mapping) else {}
    explicit_sample = str(sample.get("sample") or "").strip()
    composition = str(sample.get("composition") or "").strip()
    microwire = str(sample.get("microwire") or "").strip()
    sample_label = explicit_sample or " ".join(
        part for part in (composition, microwire) if part
    )
    if not sample_label:
        sample_label = path.parent.name if path.is_file() else path.name
    run_label = path.stem if path.is_file() else path.name
    if run_label == sample_label:
        run_label = "Measurement"
    return sample_label or "Unknown sample", run_label


def _saved_review_units(sidecar: Path) -> tuple[ReviewUnitSummary, ...]:
    if not sidecar.exists():
        return ()
    try:
        return _review_units_from_payload(load_review(sidecar))
    except Exception:
        return (
            ReviewUnitSummary(
                "Saved review", "needs_attention", "Could not read saved review."
            ),
        )


def _review_units_have_completed_review(
    review_units: Sequence[ReviewUnitSummary],
) -> bool:
    return any(
        unit.state
        in {"accepted", "manual", "no_transition", "excluded", "archive_requested"}
        for unit in review_units
    )


def review_current_annealing_files(
    parent: QtWidgets.QWidget,
    measurement_paths: Sequence[Path],
    *,
    sample_for_path: Callable[[Path], Mapping[str, Any] | None] | None = None,
    review_units_for_path: Callable[[Path], Sequence[ReviewUnitSummary]] | None = None,
    review_payload_for_path: Callable[[Path], Mapping[str, Any] | None] | None = None,
) -> int:
    entries: list[ReviewQueueEntry] = []
    for path_value in measurement_paths:
        path = Path(path_value)
        sample = sample_for_path(path) if sample_for_path is not None else None
        sample_label, run_label = _queue_labels(path, sample)
        sidecar = sidecar_path_for_measurement(path, family="current_annealing")
        review_units = _saved_review_units(sidecar)
        if not review_units and review_units_for_path is not None:
            review_units = tuple(review_units_for_path(path))
        entries.append(
            ReviewQueueEntry(
                sample_label=sample_label,
                run_label=run_label,
                saved=sidecar.exists()
                or _review_units_have_completed_review(review_units),
                review_units=review_units,
                builder=lambda owner, selected_path=path, selected_sample=sample: _build_current_annealing_review_dialog(
                    owner,
                    selected_path,
                    sample=selected_sample,
                    initial_payload=(
                        review_payload_for_path(selected_path)
                        if review_payload_for_path is not None
                        else None
                    ),
                ),
            )
        )
    if not entries:
        return 0
    dialog = PortableTransitionReviewQueueDialog(entries, parent)
    dialog.exec()
    return dialog.completed_count


def review_tma_runs(
    parent: QtWidgets.QWidget,
    run_paths: Sequence[Path],
    *,
    sample_for_path: Callable[[Path], Mapping[str, Any] | None] | None = None,
    review_units_for_path: Callable[[Path], Sequence[ReviewUnitSummary]] | None = None,
    review_payload_for_path: Callable[[Path], Mapping[str, Any] | None] | None = None,
) -> int:
    entries: list[ReviewQueueEntry] = []
    for path_value in run_paths:
        path = Path(path_value)
        sample = sample_for_path(path) if sample_for_path is not None else None
        sample_label, run_label = _queue_labels(path, sample)
        sidecar = sidecar_path_for_measurement(path, family="tma")
        review_units = _saved_review_units(sidecar)
        if not review_units and review_units_for_path is not None:
            review_units = tuple(review_units_for_path(path))
        entries.append(
            ReviewQueueEntry(
                sample_label=sample_label,
                run_label=run_label,
                saved=sidecar.exists()
                or _review_units_have_completed_review(review_units),
                review_units=review_units,
                builder=lambda owner, selected_path=path: _build_tma_review_dialog(
                    owner,
                    selected_path,
                    initial_payload=(
                        review_payload_for_path(selected_path)
                        if review_payload_for_path is not None
                        else None
                    ),
                ),
            )
        )
    if not entries:
        return 0
    dialog = PortableTransitionReviewQueueDialog(entries, parent)
    dialog.exec()
    return dialog.completed_count

__all__ = [
    "PortableTransitionReviewDialog",
    "PortableTransitionReviewQueueDialog",
    "ReviewQueueEntry",
    "ReviewPlot",
    "review_current_annealing_file",
    "review_current_annealing_files",
    "review_tma_run",
    "review_tma_runs",
]
