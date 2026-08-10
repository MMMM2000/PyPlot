"""Small logger-facing editor for portable transition-review sidecars."""

from __future__ import annotations

import copy
import math
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
    load_review,
    sidecar_path_for_measurement,
    utc_now_text,
)
from plotting.shared.transition_review_adapters import (
    current_annealing_review_draft,
    tma_review_draft,
)


LABELS = ("As", "Af", "Ms", "Mf", "As1", "Af1", "Ms1", "Mf1", "As2", "Af2", "Ms2", "Mf2")

@dataclass(frozen=True)
class ReviewPlot:
    x: pd.Series
    y: pd.Series
    title: str
    y_label: str
    derives_transition_strain: bool = False
    strain_reference: Mapping[str, Any] | None = None
    unit_series: Mapping[str, tuple[pd.Series, pd.Series]] | None = None


class PortableTransitionReviewDialog(QtWidgets.QDialog):
    """Edit all targets in one portable review record."""

    def __init__(
        self,
        payload: Mapping[str, Any],
        plots: Mapping[str, ReviewPlot],
        sidecar_path: Path,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.payload = copy.deepcopy(dict(payload))
        self.plots = dict(plots)
        self.sidecar_path = Path(sidecar_path)
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
        self.curve_item = self.plot_item.plot(
            [], [], pen=pg.mkPen("#9ca3af", width=1.4)
        )
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
        review_layout.addWidget(self.values_box)

        self.exclude_check = QtWidgets.QCheckBox("Exclude from Builder analysis")
        self.exclude_check.setToolTip(
            "Keep the reviewed values in the run folder, but do not use this "
            "target in Builder analysis."
        )
        review_layout.addWidget(self.exclude_check)
        self.decision_summary = QtWidgets.QLabel()
        self.decision_summary.setWordWrap(True)
        review_layout.addWidget(self.decision_summary)
        review_layout.addStretch(1)

        self.exclude_check.toggled.connect(self._target_controls_changed)
        self.review_unit_combo.currentIndexChanged.connect(
            self._review_unit_changed
        )
        self.values_table.itemSelectionChanged.connect(self._selected_row_changed)
        self.manual_value_edit.textChanged.connect(self._manual_text_changed)
        self.manual_value_edit.editingFinished.connect(
            self._manual_value_committed
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

    def _targets(self) -> list[dict[str, Any]]:
        return self.payload.setdefault("targets", [])

    def _target_display_label(self, target: Mapping[str, Any]) -> str:
        metadata = target.get("target")
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
            loop_numbers = {
                int(label[-1])
                for label in available
                if label[:-1] in {"As", "Af", "Ms", "Mf"} and label[-1:].isdigit()
            }
            if not loop_numbers:
                loop_numbers = {1, 2}
            for loop in loop_numbers:
                available.update(
                    (f"As{loop}", f"Af{loop}", f"Ms{loop}", f"Mf{loop}")
                )
        else:
            available.update(("As", "Af", "Ms", "Mf"))
        return [label for label in LABELS if label in available]

    def _review_units_for_target(
        self,
        target: Mapping[str, Any],
    ) -> list[tuple[str, list[str]]]:
        labels = self._labels_for_target(target)
        if self.payload.get("experiment_family") != "current_annealing":
            return [("Transitions", labels)]
        loop_numbers = sorted(
            {
                int(label[-1])
                for label in labels
                if label[:-1] in {"As", "Af", "Ms", "Mf"}
                and label[-1:].isdigit()
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

    def _review_unit_changed(self, index: int) -> None:
        if self._loading or index < 0 or index >= len(self._review_unit_labels):
            return
        self._store_target_controls()
        self._active_unit_labels = list(self._review_unit_labels[index])
        target = self._targets()[self._target_index]
        self._loading = True
        self._populate_values_table(target)
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

    def _current_target_ready(self) -> bool:
        if not self._choices:
            return False
        for label, choice in self._choices.items():
            if choice is None:
                return False
            if choice == "manual" and label not in self._manual_values:
                return False
        return True

    def _target_ready(self, target: Mapping[str, Any]) -> bool:
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
        return bool(self._targets()) and all(
            self._target_ready(target) for target in self._targets()
        )

    def _update_decision_summary(self) -> None:
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
        self.save_button.setEnabled(self._all_targets_ready())

    def _target_controls_changed(self, *_args: object) -> None:
        if self._loading:
            return
        self._store_target_controls()
        self._draw_target()
        self._update_decision_summary()

    def _store_target_controls(self) -> None:
        if (
            self._loading
            or not self._targets()
            or self._target_index < 0
            or self._target_index >= len(self._targets())
        ):
            return
        target = self._targets()[self._target_index]
        auto = dict(target.get("auto_values") or {})
        active_labels = set(self._choices)
        manual = {
            str(label): float(value)
            for label, value in dict(target.get("manual_values") or {}).items()
            if label not in active_labels
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
            for label in target.get("cleared_labels", ())
            if label not in active_labels
        }
        cleared.update(
            label
            for label, choice in self._choices.items()
            if choice == "not_observed"
        )
        final = {
            str(label): float(value)
            for label, value in dict(target.get("final_values") or {}).items()
            if label not in active_labels
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
        self.exclude_check.setChecked(str(target.get("status") or "") == "excluded")
        self._populate_review_units(target, selected_index=unit_index)
        self._populate_values_table(target)
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
            self.curve_item.setData([], [])
            self.plot_item.setTitle("Plot unavailable")
            return
        unit_title = self.review_unit_combo.currentText()
        unit_series = (plot.unit_series or {}).get(unit_title)
        plot_x, plot_y = unit_series if unit_series is not None else (plot.x, plot.y)
        x = pd.to_numeric(plot_x, errors="coerce")
        y = pd.to_numeric(plot_y, errors="coerce")
        valid = x.notna() & y.notna()
        self.curve_item.setData(
            x.loc[valid].to_numpy(dtype=float),
            y.loc[valid].to_numpy(dtype=float),
        )
        self.plot_item.setLabel("bottom", "Current", units="mA")
        self.plot_item.setLabel("left", plot.y_label)
        self.plot_item.setTitle(plot.title)
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

    def _save_and_accept(self) -> None:
        self._store_target_controls()
        self._update_decision_summary()
        if not self._all_targets_ready():
            return
        self.payload["review_revision"] = int(self.payload.get("review_revision", 0) or 0) + 1
        self.payload["updated_utc"] = utc_now_text()
        atomic_write_review(self.sidecar_path, self.payload)
        self.accept()



@dataclass(frozen=True)
class ReviewQueueEntry:
    sample_label: str
    run_label: str
    builder: Callable[[QtWidgets.QWidget], PortableTransitionReviewDialog]
    saved: bool = False

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
        navigation.setMaximumWidth(390)
        navigation_layout = QtWidgets.QVBoxLayout(navigation)
        navigation_layout.setContentsMargins(0, 0, 4, 0)
        navigation_layout.addWidget(QtWidgets.QLabel("Samples / runs / cycles"))
        self.tree = QtWidgets.QTreeWidget(navigation)
        self.tree.setHeaderLabels(["Sample / run / cycle", "Review"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tree.header().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.tree.header().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
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
        splitter.setSizes([310, 970])

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
            status = "Saved" if entry.saved else "Not opened"
            run_item = QtWidgets.QTreeWidgetItem(
                [entry.run_label or entry.label, status]
            )
            run_item.setData(
                0, QtCore.Qt.ItemDataRole.UserRole, ("run", index, -1)
            )
            sample_item.addChild(run_item)
            sample_item.setExpanded(True)
            self._run_items.append(run_item)
        self.tree.currentItemChanged.connect(self._selection_changed)
        self._update_status()
        if self._run_items:
            QtCore.QTimer.singleShot(
                0, lambda: self.tree.setCurrentItem(self._run_items[0])
            )

    @property
    def completed_count(self) -> int:
        return len(self._saved_indices)

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

    def _build_editor(self, run_index: int) -> PortableTransitionReviewDialog | None:
        existing = self._editors.get(run_index)
        if existing is not None:
            return existing
        entry = self.entries[run_index]
        try:
            editor = entry.builder(self.editor_host)
        except Exception as exc:
            self._run_items[run_index].setText(1, "Load failed")
            self._run_items[run_index].setToolTip(0, str(exc))
            self._update_status(f"Could not load {entry.label}: {exc}")
            return None
        editor.setWindowFlags(QtCore.Qt.WindowType.Widget)
        editor.target_panel.hide()
        editor.heading.setText(
            f"{entry.label} · saves {editor.sidecar_path.name} beside this measurement"
        )
        editor.save_button.setText("Save current run")
        for button_box in editor.findChildren(QtWidgets.QDialogButtonBox):
            cancel_button = button_box.button(
                QtWidgets.QDialogButtonBox.StandardButton.Cancel
            )
            if cancel_button is not None:
                cancel_button.hide()
        editor.accepted.connect(
            lambda selected_index=run_index: self._editor_saved(selected_index)
        )
        self.editor_layout.addWidget(editor, 1)
        editor.hide()
        self._editors[run_index] = editor

        top_item = self._run_items[run_index]
        blocker = QtCore.QSignalBlocker(self.tree)
        try:
            while top_item.childCount():
                top_item.removeChild(top_item.child(0))
            for navigation_row in range(editor.target_list.count()):
                child = QtWidgets.QTreeWidgetItem(
                    [editor.target_list.item(navigation_row).text(), ""]
                )
                child.setData(
                    0,
                    QtCore.Qt.ItemDataRole.UserRole,
                    ("unit", run_index, navigation_row),
                )
                top_item.addChild(child)
            top_item.setExpanded(True)
            if top_item.text(1) == "Not opened":
                top_item.setText(1, "Open")
        finally:
            del blocker
        return editor

    def _show_editor(self, run_index: int, navigation_row: int = 0) -> None:
        if self._current_index is not None:
            current = self._editors.get(self._current_index)
            if current is not None:
                current._store_target_controls()  # noqa: SLF001
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
        editor.show()
        self._update_status(self.entries[run_index].label)

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
        if kind == "sample":
            if current.childCount():
                self.tree.setCurrentItem(current.child(0))
            return
        if kind == "run":
            editor = self._build_editor(run_index)
            if editor is None:
                return
            top_item = self._run_items[run_index]
            if top_item.childCount():
                self.tree.setCurrentItem(top_item.child(0))
                return
            self._show_editor(run_index, 0)
            return
        self._show_editor(run_index, int(navigation_row))

    def _editor_saved(self, run_index: int) -> None:
        self._saved_indices.add(run_index)
        self._run_items[run_index].setText(1, "Saved")
        self._update_status(f"Saved {self.entries[run_index].label}.")
        next_index = run_index + 1
        if next_index < len(self._run_items):
            self.tree.setCurrentItem(self._run_items[next_index])
            return
        editor = self._editors.get(run_index)
        if editor is not None:
            editor.show()

    def accept(self) -> None:
        if self._current_index is not None:
            editor = self._editors.get(self._current_index)
            if editor is not None:
                editor._store_target_controls()  # noqa: SLF001
        super().accept()

def _current_annealing_cycle_series(
    frame: pd.DataFrame,
) -> dict[str, tuple[pd.Series, pd.Series]]:
    """Return one heating/cooling trace per current cycle for review."""

    resistance_column = "R_Ohm" if "R_Ohm" in frame.columns else "R_ohm"
    if "I_mA" not in frame.columns or resistance_column not in frame.columns:
        return {}
    working = pd.DataFrame(
        {
            "I_mA": pd.to_numeric(frame["I_mA"], errors="coerce"),
            "R_Ohm": pd.to_numeric(frame[resistance_column], errors="coerce"),
        }
    ).dropna()
    if working.empty:
        return {}
    _directions, segments = annealing_core._direction_profile(  # noqa: SLF001
        working["I_mA"].to_numpy(dtype=float)
    )
    result: dict[str, tuple[pd.Series, pd.Series]] = {}
    cycle = 0
    for segment_index, (start, end, direction) in enumerate(segments):
        if direction < 0:
            continue
        cycle += 1
        cycle_end = end
        for _next_start, next_end, next_direction in segments[segment_index + 1 :]:
            if next_direction >= 0:
                break
            cycle_end = next_end
        trace = working.iloc[start:cycle_end]
        result[f"Cycle {cycle}"] = (trace["I_mA"], trace["R_Ohm"])
    return result

def _build_current_annealing_review_dialog(
    parent: QtWidgets.QWidget,
    measurement_path: Path,
    *,
    sample: Mapping[str, Any] | None = None,
) -> PortableTransitionReviewDialog:
    path = Path(measurement_path)
    sidecar = sidecar_path_for_measurement(path, family="current_annealing")
    draft = current_annealing_review_draft(path, sample=sample)
    payload = load_review(sidecar) if sidecar.exists() else draft
    if payload["measurement_fingerprint"] != draft["measurement_fingerprint"]:
        raise ValueError("Existing transition review belongs to different measurement content.")
    frame = annealing_core.load_file(str(path))
    plot = ReviewPlot(
        frame["I_mA"],
        frame["R_Ohm"],
        path.parent.name or path.stem,
        "Resistance (ohm)",
        unit_series=_current_annealing_cycle_series(frame),
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
) -> PortableTransitionReviewDialog:
    run_dir = Path(run_path)
    sidecar = sidecar_path_for_measurement(run_dir, family="tma")
    draft = tma_review_draft(run_dir)
    payload = load_review(sidecar) if sidecar.exists() else draft
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


def review_current_annealing_files(
    parent: QtWidgets.QWidget,
    measurement_paths: Sequence[Path],
    *,
    sample_for_path: Callable[[Path], Mapping[str, Any] | None] | None = None,
) -> int:
    entries: list[ReviewQueueEntry] = []
    for path_value in measurement_paths:
        path = Path(path_value)
        sample = sample_for_path(path) if sample_for_path is not None else None
        sample_label, run_label = _queue_labels(path, sample)
        sidecar = sidecar_path_for_measurement(path, family="current_annealing")
        entries.append(
            ReviewQueueEntry(
                sample_label=sample_label,
                run_label=run_label,
                saved=sidecar.exists(),
                builder=lambda owner, selected_path=path, selected_sample=sample: _build_current_annealing_review_dialog(
                    owner, selected_path, sample=selected_sample
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
) -> int:
    entries: list[ReviewQueueEntry] = []
    for path_value in run_paths:
        path = Path(path_value)
        sample = sample_for_path(path) if sample_for_path is not None else None
        sample_label, run_label = _queue_labels(path, sample)
        sidecar = sidecar_path_for_measurement(path, family="tma")
        entries.append(
            ReviewQueueEntry(
                sample_label=sample_label,
                run_label=run_label,
                saved=sidecar.exists(),
                builder=lambda owner, selected_path=path: _build_tma_review_dialog(
                    owner, selected_path
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
