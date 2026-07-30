"""Small logger-facing editor for portable transition-review sidecars."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

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
        heading = QtWidgets.QLabel(
            f"Review after safe run completion \N{MIDDLE DOT} saves {self.sidecar_path.name} only"
        )
        root.addWidget(heading)

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        root.addWidget(split, 1)
        left = QtWidgets.QWidget()
        self.target_panel = left
        left.setMinimumWidth(170)
        left.setMaximumWidth(250)
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        self.target_list = QtWidgets.QListWidget()
        for target in self.payload.get("targets", []):
            self.target_list.addItem(self._target_display_label(target))
        self.target_list.currentRowChanged.connect(self._target_changed)
        left_layout.addWidget(self.target_list, 1)
        split.addWidget(left)
        left.setVisible(self.target_list.count() > 1)

        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        plot_panel = QtWidgets.QWidget()
        plot_layout = QtWidgets.QVBoxLayout(plot_panel)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.figure = Figure(figsize=(7.2, 4.3), constrained_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.mpl_connect("button_press_event", self._plot_clicked)
        plot_layout.addWidget(self.canvas, 1)
        right.addWidget(plot_panel)

        review_panel = QtWidgets.QWidget()
        review_panel.setMinimumWidth(310)
        review_panel.setMaximumWidth(390)
        review_layout = QtWidgets.QVBoxLayout(review_panel)
        review_layout.setContentsMargins(6, 0, 0, 0)
        review_layout.setSpacing(6)

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
                if load is not None:
                    label += f" · {float(load):.6g} g"
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
            labels = self._labels_for_target(target)
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

    def _all_targets_ready(self) -> bool:
        for index, target in enumerate(self._targets()):
            if index == self._target_index:
                if not self._current_target_ready():
                    return False
                continue
            if str(target.get("status") or "unreviewed") in {
                "unreviewed",
                "needs_attention",
            }:
                return False
        return bool(self._targets())

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
        ready = self._current_target_ready()
        manual = {
            label: self._manual_values[label]
            for label, choice in self._choices.items()
            if choice == "manual" and label in self._manual_values
        }
        cleared = sorted(
            label
            for label, choice in self._choices.items()
            if choice == "not_observed"
        )
        final = {
            label: float(auto[label])
            for label, choice in self._choices.items()
            if choice == "auto" and label in auto
        }
        final.update(manual)
        selected = set(self._choices.values())
        if not ready:
            base_status = "unreviewed"
        elif selected == {"auto"}:
            base_status = "accepted_auto"
        elif selected == {"not_observed"}:
            base_status = "no_transition"
            final = {}
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
        target["manual_values"] = manual
        target["final_values"] = final
        target["cleared_labels"] = cleared

    def _target_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._targets()):
            return
        self._store_target_controls()
        self._target_index = row
        target = self._targets()[row]
        self._loading = True
        self.exclude_check.setChecked(str(target.get("status") or "") == "excluded")
        self._populate_values_table(target)
        self._loading = False
        self._selected_row_changed()
        self._update_decision_summary()
        self._draw_target()

    def _draw_target(self) -> None:
        self.figure.clear()
        axes = self.figure.add_subplot(111)
        target = self._targets()[self._target_index]
        plot = self.plots.get(str(target.get("target_key") or ""))
        if plot is None:
            axes.text(0.5, 0.5, "Plot unavailable", ha="center", va="center")
        else:
            axes.plot(plot.x, plot.y, color="#1f2937", linewidth=1.2)
            axes.set_xlabel("Current (mA)")
            axes.set_ylabel(plot.y_label)
            axes.set_title(plot.title)
        auto = target.get("auto_values") if isinstance(target.get("auto_values"), Mapping) else {}
        for label, value in auto.items():
            if self._choices.get(label) != "not_observed":
                axes.axvline(
                    float(value), color="#9ca3af", linestyle="--", linewidth=0.9
                )
                axes.text(
                    float(value),
                    0.98,
                    label,
                    transform=axes.get_xaxis_transform(),
                    va="top",
                )
        for label, value in self._manual_values.items():
            if self._choices.get(label) == "manual":
                axes.axvline(float(value), color="#dc2626", linewidth=1.5)
                axes.text(
                    float(value),
                    0.88,
                    label,
                    transform=axes.get_xaxis_transform(),
                    va="top",
                )
        self.canvas.draw_idle()

    def _plot_clicked(self, event: Any) -> None:
        if event.inaxes is None or event.xdata is None:
            return
        label = self._selected_label()
        if not label:
            return
        self._choices[label] = "manual"
        self._manual_values[label] = float(event.xdata)
        self._update_choice_row(label)
        self._selected_row_changed()
        self._target_controls_changed()

    def _save_and_accept(self) -> None:
        self._store_target_controls()
        self._update_decision_summary()
        if not self._all_targets_ready():
            return
        self.payload["review_revision"] = int(self.payload.get("review_revision", 0) or 0) + 1
        self.payload["updated_utc"] = utc_now_text()
        atomic_write_review(self.sidecar_path, self.payload)
        self.accept()



def review_current_annealing_file(
    parent: QtWidgets.QWidget,
    measurement_path: Path,
    *,
    sample: Mapping[str, Any] | None = None,
) -> bool:
    path = Path(measurement_path)
    sidecar = sidecar_path_for_measurement(path, family="current_annealing")
    draft = current_annealing_review_draft(path, sample=sample)
    payload = load_review(sidecar) if sidecar.exists() else draft
    if payload["measurement_fingerprint"] != draft["measurement_fingerprint"]:
        raise ValueError("Existing transition review belongs to different measurement content.")
    frame = annealing_core.load_file(str(path))
    plot = ReviewPlot(frame["I_mA"], frame["R_Ohm"], path.parent.name or path.stem, "Resistance (ohm)")
    dialog = PortableTransitionReviewDialog(payload, {"graph": plot}, sidecar, parent)
    return dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted


def review_tma_run(parent: QtWidgets.QWidget, run_path: Path) -> bool:
    run_dir = Path(run_path)
    sidecar = sidecar_path_for_measurement(run_dir, family="tma")
    draft = tma_review_draft(run_dir)
    payload = load_review(sidecar) if sidecar.exists() else draft
    if payload["measurement_fingerprint"] != draft["measurement_fingerprint"]:
        raise ValueError("Existing transition review belongs to different TMA run content.")
    run = tma_core.load_run(run_dir)
    plots: dict[str, ReviewPlot] = {}
    for target, group in tma_core.current_sweep_groups(run.frame):
        key = f"stress_mpa:{float(target):.9g}"
        plots[key] = ReviewPlot(
            pd.to_numeric(group["current_mA"], errors="coerce"),
            pd.to_numeric(group["strain_pct"], errors="coerce"),
            f"{run.sample_name} · {float(target):.6g} MPa",
            "Strain (%)",
        )
    dialog = PortableTransitionReviewDialog(payload, plots, sidecar, parent)
    return dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted


__all__ = [
    "PortableTransitionReviewDialog",
    "ReviewPlot",
    "review_current_annealing_file",
    "review_tma_run",
]
