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
        self.resize(1050, 720)

        root = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel(
            f"Review after safe run completion · saves {self.sidecar_path.name} only"
        )
        heading.setWordWrap(True)
        root.addWidget(heading)

        split = QtWidgets.QSplitter()
        root.addWidget(split, 1)
        left = QtWidgets.QWidget()
        self.target_panel = left
        left_layout = QtWidgets.QVBoxLayout(left)
        self.target_list = QtWidgets.QListWidget()
        for target in self.payload.get("targets", []):
            self.target_list.addItem(self._target_display_label(target))
        self.target_list.currentRowChanged.connect(self._target_changed)
        left_layout.addWidget(self.target_list, 1)
        split.addWidget(left)
        left.setVisible(self.target_list.count() > 1)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        self.figure = Figure(figsize=(7.2, 4.3), constrained_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.mpl_connect("button_press_event", self._plot_clicked)
        right_layout.addWidget(self.canvas, 1)

        decision_box = QtWidgets.QGroupBox("What did this run show?")
        decision_box.setStyleSheet(
            "QPushButton { padding: 7px 14px; } "
            "QPushButton:checked { background: #2563eb; color: white; "
            "border: 1px solid #1d4ed8; border-radius: 3px; }"
        )
        decision_layout = QtWidgets.QHBoxLayout(decision_box)
        self.accept_auto_button = QtWidgets.QPushButton("Accept automatic")
        self.manual_button = QtWidgets.QPushButton("Adjust manually")
        self.no_transition_button = QtWidgets.QPushButton("No transition")
        self.decision_group = QtWidgets.QButtonGroup(self)
        self.decision_group.setExclusive(True)
        for button, status in (
            (self.accept_auto_button, "accepted_auto"),
            (self.manual_button, "manual_adjusted"),
            (self.no_transition_button, "no_transition"),
        ):
            button.setCheckable(True)
            button.setProperty("reviewStatus", status)
            self.decision_group.addButton(button)
            decision_layout.addWidget(button)
        right_layout.addWidget(decision_box)

        self.values_box = QtWidgets.QGroupBox("Transition values")
        values_layout = QtWidgets.QVBoxLayout(self.values_box)
        self.values_table = QtWidgets.QTableWidget(0, 3)
        self.values_table.setHorizontalHeaderLabels(
            ["Point", "Automatic (mA)", "Chosen (mA)"]
        )
        self.values_table.verticalHeader().setVisible(False)
        self.values_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.values_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.values_table.horizontalHeader().setStretchLastSection(True)
        self.value_edits: dict[str, QtWidgets.QLineEdit] = {}
        self._cleared_labels: set[str] = set()
        values_layout.addWidget(self.values_table)

        values_actions = QtWidgets.QHBoxLayout()
        self.graph_hint = QtWidgets.QLabel(
            "Select a row, then click the graph to set that point."
        )
        self.graph_hint.setWordWrap(True)
        self.omit_button = QtWidgets.QPushButton("Omit selected point")
        values_actions.addWidget(self.graph_hint, 1)
        values_actions.addWidget(self.omit_button)
        values_layout.addLayout(values_actions)
        right_layout.addWidget(self.values_box)

        self.exclude_check = QtWidgets.QCheckBox(
            "Exclude this target from Builder analysis"
        )
        self.exclude_check.setToolTip(
            "Keep the reviewed values in the run folder, but do not use this "
            "target in Builder analysis."
        )
        right_layout.addWidget(self.exclude_check)
        self.decision_summary = QtWidgets.QLabel()
        self.decision_summary.setWordWrap(True)
        right_layout.addWidget(self.decision_summary)

        self.decision_group.buttonClicked.connect(self._decision_changed)
        self.exclude_check.toggled.connect(self._update_decision_summary)
        self.values_table.itemSelectionChanged.connect(self._selected_row_changed)
        self.omit_button.clicked.connect(self._toggle_selected_point)
        split.addWidget(right)
        split.setStretchFactor(1, 1)

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

    def _current_decision(self) -> str:
        for button in self.decision_group.buttons():
            if button.isChecked():
                return str(button.property("reviewStatus") or "unreviewed")
        return "unreviewed"

    def _labels_for_target(self, target: Mapping[str, Any]) -> list[str]:
        available: set[str] = set()
        for field in ("auto_values", "manual_values", "final_values"):
            values = target.get(field)
            if isinstance(values, Mapping):
                available.update(str(label) for label in values)
        available.update(str(label) for label in target.get("cleared_labels", ()))
        if not available:
            if self.payload.get("experiment_family") == "current_annealing":
                available.update(("As1", "Af1", "Ms1", "Mf1", "As2", "Af2", "Ms2", "Mf2"))
            else:
                available.update(("As", "Af", "Ms", "Mf"))
        return [label for label in LABELS if label in available]

    def _populate_values_table(self, target: Mapping[str, Any]) -> None:
        blocker = QtCore.QSignalBlocker(self.values_table)
        try:
            labels = self._labels_for_target(target)
            auto = target.get("auto_values") if isinstance(target.get("auto_values"), Mapping) else {}
            manual = target.get("manual_values") if isinstance(target.get("manual_values"), Mapping) else {}
            self.values_table.clearContents()
            self.values_table.setRowCount(len(labels))
            self.value_edits = {}
            for row, label in enumerate(labels):
                point_item = QtWidgets.QTableWidgetItem(label)
                point_item.setFlags(point_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                point_item.setData(QtCore.Qt.ItemDataRole.UserRole, label)
                self.values_table.setItem(row, 0, point_item)

                auto_value = auto.get(label)
                auto_text = "—" if auto_value is None else f"{float(auto_value):.6g}"
                auto_item = QtWidgets.QTableWidgetItem(auto_text)
                auto_item.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
                auto_item.setFlags(auto_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.values_table.setItem(row, 1, auto_item)

                edit = QtWidgets.QLineEdit()
                validator = QtGui.QDoubleValidator(edit)
                validator.setNotation(QtGui.QDoubleValidator.Notation.StandardNotation)
                edit.setValidator(validator)
                if label in manual:
                    edit.setText(f"{float(manual[label]):.6g}")
                edit.textChanged.connect(self._manual_text_changed)
                edit.editingFinished.connect(self._manual_value_edited)
                self.value_edits[label] = edit
                self.values_table.setCellWidget(row, 2, edit)
                self._update_value_row_state(row, label)
            self.values_table.resizeRowsToContents()
            if labels:
                self.values_table.selectRow(0)
            self.values_table.setMaximumHeight(
                min(300, self.values_table.horizontalHeader().height() + 34 * max(len(labels), 1) + 4)
            )
            self.accept_auto_button.setEnabled(bool(auto))
        finally:
            del blocker
        self._selected_row_changed()

    def _selected_label(self) -> str:
        row = self.values_table.currentRow()
        item = self.values_table.item(row, 0) if row >= 0 else None
        return str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "") if item else ""

    def _update_value_row_state(self, row: int, label: str) -> None:
        edit = self.value_edits.get(label)
        if edit is None:
            return
        omitted = label in self._cleared_labels
        manual_mode = self._current_decision() == "manual_adjusted"
        auto_item = self.values_table.item(row, 1)
        auto_text = auto_item.text() if auto_item is not None else "—"
        edit.setEnabled(manual_mode and not omitted)
        edit.setPlaceholderText(
            "Omitted" if omitted else (f"Use automatic ({auto_text})" if auto_text != "—" else "Enter value")
        )
        point_item = self.values_table.item(row, 0)
        if point_item is not None:
            font = point_item.font()
            font.setStrikeOut(omitted)
            point_item.setFont(font)
            text_color = (
                QtGui.QColor("#6b7280")
                if omitted
                else self.values_table.palette().color(QtGui.QPalette.ColorRole.Text)
            )
            point_item.setForeground(QtGui.QBrush(text_color))

    def _refresh_value_edit_state(self) -> None:
        for row in range(self.values_table.rowCount()):
            item = self.values_table.item(row, 0)
            label = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "") if item else ""
            if label:
                self._update_value_row_state(row, label)
        self._selected_row_changed()

    def _decision_changed(self, _button: QtWidgets.QAbstractButton) -> None:
        if self._loading:
            return
        if self._current_decision() == "accepted_auto":
            self._cleared_labels.clear()
            for edit in self.value_edits.values():
                edit.clear()
        self._refresh_value_edit_state()
        self._store_target_controls()
        self._draw_target()
        self._update_decision_summary()

    def _manual_text_changed(self, _text: str) -> None:
        if self._loading or self._current_decision() != "manual_adjusted":
            return
        self.manual_button.setChecked(True)
        self._refresh_value_edit_state()
        self._update_decision_summary()

    def _manual_value_edited(self) -> None:
        if self._loading:
            return
        self.manual_button.setChecked(True)
        self._store_target_controls()
        self._draw_target()
        self._refresh_value_edit_state()
        self._update_decision_summary()

    def _selected_row_changed(self) -> None:
        label = self._selected_label()
        manual_mode = self._current_decision() == "manual_adjusted"
        self.omit_button.setEnabled(bool(label) and manual_mode)
        self.omit_button.setText(
            "Restore selected point" if label in self._cleared_labels else "Omit selected point"
        )

    def _toggle_selected_point(self) -> None:
        label = self._selected_label()
        if not label or self._current_decision() != "manual_adjusted":
            return
        if label in self._cleared_labels:
            self._cleared_labels.remove(label)
        else:
            self._cleared_labels.add(label)
        self._refresh_value_edit_state()
        self._store_target_controls()
        self._draw_target()
        self._update_decision_summary()

    def _manual_values_from_edits(self) -> dict[str, float]:
        manual: dict[str, float] = {}
        for label, edit in self.value_edits.items():
            text = edit.text().strip().replace(",", ".")
            if not text:
                continue
            try:
                value = float(text)
            except ValueError:
                continue
            if math.isfinite(value):
                manual[label] = value
        return manual

    def _has_manual_adjustment(self) -> bool:
        return bool(self._cleared_labels or self._manual_values_from_edits())

    def _update_decision_summary(self) -> None:
        decision = self._current_decision()
        manual_ready = self._has_manual_adjustment()
        if self.exclude_check.isChecked():
            text = (
                "The review will remain saved in this run folder, but this target "
                "will be excluded from Builder analysis."
            )
        elif decision == "accepted_auto":
            text = "Automatic values will be saved as the reviewed result."
        elif decision == "manual_adjusted" and not manual_ready:
            text = "Select a point and enter a value or click the graph before saving."
        elif decision == "manual_adjusted":
            text = (
                "Chosen values override the automatic values. Select a row and click "
                "the graph to adjust it."
            )
        elif decision == "no_transition":
            text = (
                "Reviewed result: no transition observed. This remains a useful "
                "categorical result in Builder analysis."
            )
        else:
            text = "Choose one of the three review decisions above."
        self.decision_summary.setText(text)
        self.save_button.setEnabled(
            decision != "unreviewed"
            and (decision != "manual_adjusted" or manual_ready)
        )

    def _store_target_controls(self) -> None:
        if (
            self._loading
            or not self._targets()
            or self._target_index < 0
            or self._target_index >= len(self._targets())
        ):
            return
        target = self._targets()[self._target_index]
        decision = self._current_decision()
        manual = self._manual_values_from_edits()
        auto = dict(target.get("auto_values") or {})
        cleared = [] if decision == "accepted_auto" else sorted(self._cleared_labels)
        final = {key: float(value) for key, value in auto.items() if key not in cleared}
        final.update({key: value for key, value in manual.items() if key not in cleared})
        if decision == "accepted_auto":
            manual = {}
            final = {key: float(value) for key, value in auto.items()}
        elif decision in {"no_transition", "unreviewed"}:
            final = {}
        status = "excluded" if self.exclude_check.isChecked() else decision
        if decision == "unreviewed" and str(target.get("status") or "") == "needs_attention":
            status = "needs_attention"
        target["status"] = status
        target["included"] = status in {"accepted_auto", "manual_adjusted"}
        target["analysis_included"] = status in {
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
        status = str(target.get("status") or "unreviewed")
        self.exclude_check.setChecked(status == "excluded")
        decision = status
        if status == "excluded":
            decision = "manual_adjusted" if target.get("manual_values") else "accepted_auto"
        for button in self.decision_group.buttons():
            button.setChecked(str(button.property("reviewStatus") or "") == decision)
        self._cleared_labels = set(str(label) for label in target.get("cleared_labels", ()))
        self._populate_values_table(target)
        self._loading = False
        self._refresh_value_edit_state()
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
        manual = target.get("manual_values") if isinstance(target.get("manual_values"), Mapping) else {}
        cleared = set(target.get("cleared_labels") or ())
        for label, value in auto.items():
            if label not in cleared:
                axes.axvline(float(value), color="#9ca3af", linestyle="--", linewidth=0.9)
                axes.text(float(value), 0.98, label, transform=axes.get_xaxis_transform(), va="top")
        for label, value in manual.items():
            if label not in cleared:
                axes.axvline(float(value), color="#dc2626", linewidth=1.5)
                axes.text(float(value), 0.88, label, transform=axes.get_xaxis_transform(), va="top")
        self.canvas.draw_idle()

    def _plot_clicked(self, event: Any) -> None:
        if event.inaxes is None or event.xdata is None:
            return
        label = self._selected_label()
        edit = self.value_edits.get(label)
        if edit is None:
            return
        self.manual_button.setChecked(True)
        self._cleared_labels.discard(label)
        edit.setText(f"{float(event.xdata):.6g}")
        self._refresh_value_edit_state()
        self._store_target_controls()
        self._draw_target()
        self._update_decision_summary()

    def _save_and_accept(self) -> None:
        self._store_target_controls()
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
