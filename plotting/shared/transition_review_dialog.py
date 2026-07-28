"""Small logger-facing editor for portable transition-review sidecars."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from PyQt6 import QtCore, QtWidgets
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
STATUS_LABELS = {
    "unreviewed": "Unreviewed",
    "accepted_auto": "Accepted automatic",
    "manual_adjusted": "Manual adjusted",
    "no_transition": "No transition",
    "excluded": "Excluded",
    "needs_attention": "Needs attention",
}


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
        left_layout = QtWidgets.QVBoxLayout(left)
        self.target_list = QtWidgets.QListWidget()
        for target in self.payload.get("targets", []):
            self.target_list.addItem(str(target.get("target_key") or "graph"))
        self.target_list.currentRowChanged.connect(self._target_changed)
        left_layout.addWidget(self.target_list, 1)
        split.addWidget(left)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        self.figure = Figure(figsize=(7.2, 4.3), constrained_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.mpl_connect("button_press_event", self._plot_clicked)
        right_layout.addWidget(self.canvas, 1)

        controls = QtWidgets.QGridLayout()
        controls.addWidget(QtWidgets.QLabel("Review state"), 0, 0)
        self.status_combo = QtWidgets.QComboBox()
        for key, label in STATUS_LABELS.items():
            self.status_combo.addItem(label, key)
        controls.addWidget(self.status_combo, 0, 1, 1, 2)
        controls.addWidget(QtWidgets.QLabel("Click assigns"), 0, 3)
        self.pick_label = QtWidgets.QComboBox()
        self.pick_label.addItems(LABELS)
        controls.addWidget(self.pick_label, 0, 4)

        self.value_edits: dict[str, QtWidgets.QLineEdit] = {}
        self.clear_boxes: dict[str, QtWidgets.QCheckBox] = {}
        for offset, label in enumerate(LABELS):
            row = 1 + offset // 4
            column = (offset % 4) * 2
            controls.addWidget(QtWidgets.QLabel(label), row, column)
            edit = QtWidgets.QLineEdit()
            edit.setMaximumWidth(90)
            edit.setPlaceholderText("auto")
            self.value_edits[label] = edit
            controls.addWidget(edit, row, column + 1)
        clear_row = 1 + math.ceil(len(LABELS) / 4)
        for index, label in enumerate(LABELS):
            box = QtWidgets.QCheckBox(f"clear {label}")
            self.clear_boxes[label] = box
            controls.addWidget(box, clear_row + index // 6, index % 6)
        right_layout.addLayout(controls)
        split.addWidget(right)
        split.setStretchFactor(1, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        if self.target_list.count():
            self.target_list.setCurrentRow(0)

    def _targets(self) -> list[dict[str, Any]]:
        return self.payload.setdefault("targets", [])

    def _store_target_controls(self) -> None:
        if (
            self._loading
            or not self._targets()
            or self._target_index < 0
            or self._target_index >= len(self._targets())
        ):
            return
        target = self._targets()[self._target_index]
        status = str(self.status_combo.currentData() or "unreviewed")
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
        cleared = sorted(label for label, box in self.clear_boxes.items() if box.isChecked())
        auto = dict(target.get("auto_values") or {})
        final = {key: float(value) for key, value in auto.items() if key not in cleared}
        final.update({key: value for key, value in manual.items() if key not in cleared})
        if status == "accepted_auto" and manual:
            status = "manual_adjusted"
        if status in {"no_transition", "needs_attention", "unreviewed"}:
            final = {}
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
        status_index = self.status_combo.findData(target.get("status"))
        self.status_combo.setCurrentIndex(max(0, status_index))
        manual = target.get("manual_values") if isinstance(target.get("manual_values"), Mapping) else {}
        auto = target.get("auto_values") if isinstance(target.get("auto_values"), Mapping) else {}
        cleared = set(target.get("cleared_labels") or ())
        for label, edit in self.value_edits.items():
            edit.setText("" if label not in manual else f"{float(manual[label]):.6g}")
            edit.setPlaceholderText(
                "auto" if label not in auto else f"{float(auto[label]):.6g}"
            )
            self.clear_boxes[label].setChecked(label in cleared)
        available = [label for label in LABELS if label in auto or label in manual]
        self.pick_label.clear()
        self.pick_label.addItems(available or list(LABELS[:4]))
        self._loading = False
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
        label = self.pick_label.currentText().strip()
        edit = self.value_edits.get(label)
        if edit is None:
            return
        edit.setText(f"{float(event.xdata):.6g}")
        self.clear_boxes[label].setChecked(False)
        if self.status_combo.currentData() in {"unreviewed", "accepted_auto"}:
            index = self.status_combo.findData("manual_adjusted")
            self.status_combo.setCurrentIndex(index)
        self._store_target_controls()
        self._draw_target()

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
