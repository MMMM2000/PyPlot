"""Target-linked view state and navigation for the TMA Run workspace."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, Sequence

from PyQt6 import QtCore, QtGui, QtWidgets


STRESS_BASIS = "stress_mpa"
TARGET_MATCH_ABS_TOLERANCE_MPA = 1e-6


class TargetedMeasurementPoint(Protocol):
    elapsed_s: float
    automation_phase: str | None
    automation_basis: str | None
    automation_target_value: float | None


@dataclass(frozen=True)
class StressTargetSelection:
    """Requested scope for result and progress plots."""

    mode: str
    target_mpa: float | None = None

    @classmethod
    def follow_active(cls) -> "StressTargetSelection":
        return cls("follow_active")

    @classmethod
    def all_targets(cls) -> "StressTargetSelection":
        return cls("all")

    @classmethod
    def target(cls, target_mpa: float) -> "StressTargetSelection":
        return cls("target", float(target_mpa))


def stress_target_for_point(point: TargetedMeasurementPoint) -> float | None:
    if str(point.automation_basis or "") != STRESS_BASIS:
        return None
    # A target ramp publishes its continuously moving setpoint. Those values are
    # acquisition progress, not independently measured stress plateaus.
    if str(point.automation_phase or "") == "target_ramp":
        return None
    value = point.automation_target_value
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def measured_stress_targets(points: Sequence[TargetedMeasurementPoint]) -> list[float]:
    targets = {
        round(float(target), 9)
        for point in points
        if (target := stress_target_for_point(point)) is not None
    }
    return sorted(targets)


def resolve_selected_target(
    selection: StressTargetSelection,
    *,
    active_target_mpa: float | None,
    measured_targets_mpa: Sequence[float],
) -> float | None:
    if selection.mode == "all":
        return None
    if selection.mode == "target":
        return selection.target_mpa
    if active_target_mpa is not None and math.isfinite(float(active_target_mpa)):
        return float(active_target_mpa)
    if measured_targets_mpa:
        return float(measured_targets_mpa[-1])
    return None


def points_for_target(
    points: Sequence[TargetedMeasurementPoint],
    target_mpa: float | None,
) -> list[TargetedMeasurementPoint]:
    if target_mpa is None:
        return list(points)
    selected: list[TargetedMeasurementPoint] = []
    for point in points:
        point_target = stress_target_for_point(point)
        if point_target is None:
            continue
        if math.isclose(
            float(point_target),
            float(target_mpa),
            rel_tol=0.0,
            abs_tol=TARGET_MATCH_ABS_TOLERANCE_MPA,
        ):
            selected.append(point)
    return selected


def format_target_mpa(value: float) -> str:
    rounded = round(float(value))
    if math.isclose(float(value), float(rounded), abs_tol=1e-9):
        return f"{rounded} MPa"
    return f"{float(value):.3f}".rstrip("0").rstrip(".") + " MPa"


class StressTargetNavigator(QtWidgets.QWidget):
    """Compact navigator shared by all target-scoped Run plots."""

    selection_changed = QtCore.pyqtSignal(object)
    configure_requested = QtCore.pyqtSignal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tmaStressTargetNavigator")
        self._selection = StressTargetSelection.follow_active()
        self._active_target_mpa: float | None = None
        self._targets_mpa: tuple[float, ...] = ()
        self._equivalent_by_target: dict[float, str] = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(9, 10, 9, 8)
        layout.setSpacing(6)

        title = QtWidgets.QLabel("Stress targets", self)
        title.setObjectName("targetNavigatorTitle")
        title_font = title.font()
        title_font.setPointSize(max(10, title_font.pointSize()))
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        self.count_label = QtWidgets.QLabel("0 of 0 active", self)
        self.count_label.setObjectName("targetNavigatorCount")
        layout.addWidget(self.count_label)

        self.follow_button = QtWidgets.QPushButton("Follow active target", self)
        self.follow_button.setObjectName("followActiveTargetButton")
        self.follow_button.setCheckable(True)
        self.follow_button.clicked.connect(self._select_follow_active)
        layout.addWidget(self.follow_button)

        self.target_list = QtWidgets.QTreeWidget(self)
        self.target_list.setObjectName("stressTargetList")
        self.target_list.setColumnCount(2)
        self.target_list.setHeaderHidden(True)
        self.target_list.setRootIsDecorated(False)
        self.target_list.setIndentation(0)
        self.target_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.target_list.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.target_list.itemClicked.connect(self._select_item)
        layout.addWidget(self.target_list, stretch=1)

        self.all_button = QtWidgets.QPushButton("All measured targets", self)
        self.all_button.setObjectName("allStressTargetsButton")
        self.all_button.setCheckable(True)
        self.all_button.clicked.connect(self._select_all)
        self.all_button.setVisible(False)

        self.active_label = QtWidgets.QLabel("Active  -", self)
        self.active_label.setObjectName("activeTargetLabel")
        self.active_label.setWordWrap(True)
        self.active_label.setVisible(False)

        self.inspected_label = QtWidgets.QLabel("Inspecting  -", self)
        self.inspected_label.setObjectName("inspectedTargetLabel")
        self.inspected_label.setWordWrap(True)
        self.inspected_label.setVisible(False)

        self.configure_button = QtWidgets.QPushButton("Recipe, sample and hardware", self)
        self.configure_button.setObjectName("openTmaConfigurationButton")
        self.configure_button.clicked.connect(self.configure_requested)
        self.configure_button.setVisible(False)

        self.setStyleSheet(
            """
            QWidget#tmaStressTargetNavigator {
                background: #191c20;
                border: 1px solid #343a42;
                border-radius: 4px;
            }
            QPushButton#followActiveTargetButton,
            QPushButton#allStressTargetsButton {
                min-height: 28px;
                text-align: left;
                padding: 4px 8px;
                border: 1px solid #343a42;
                border-radius: 3px;
            }
            QPushButton#followActiveTargetButton:checked,
            QPushButton#allStressTargetsButton:checked {
                background: #20242a;
                border-color: #343a42;
                color: #edf0f3;
            }
            QLabel#targetNavigatorCount {
                color: #9ca5af;
                font-size: 9px;
            }
            QTreeWidget#stressTargetList {
                border: 0;
                outline: 0;
                background: transparent;
            }
            QTreeWidget#stressTargetList::item {
                min-height: 28px;
                padding: 1px 5px;
                color: #9ca5af;
            }
            QTreeWidget#stressTargetList::item:selected {
                border-left: 3px solid #e8ad43;
                background: #20242a;
                color: #edf0f3;
            }
            QLabel#activeTargetLabel {
                color: #e8ad43;
                font-weight: 600;
            }
            QLabel#inspectedTargetLabel {
                color: #edf0f3;
            }
            """
        )
        self._sync_controls()

    @property
    def selection(self) -> StressTargetSelection:
        return self._selection

    def set_context(
        self,
        *,
        targets_mpa: Sequence[float],
        active_target_mpa: float | None,
        selection: StressTargetSelection,
        equivalent_by_target: dict[float, str] | None = None,
    ) -> None:
        normalized_targets = tuple(sorted({round(float(value), 9) for value in targets_mpa}))
        normalized_equivalents = {
            round(float(target), 9): str(text)
            for target, text in (equivalent_by_target or {}).items()
        }
        targets_changed = (
            normalized_targets != self._targets_mpa
            or normalized_equivalents != self._equivalent_by_target
        )
        self._targets_mpa = normalized_targets
        self._active_target_mpa = (
            None if active_target_mpa is None else float(active_target_mpa)
        )
        self._selection = selection
        self._equivalent_by_target = normalized_equivalents
        if targets_changed:
            self._rebuild_target_items()
        self._sync_controls()

    def _rebuild_target_items(self) -> None:
        self.target_list.clear()
        all_item = QtWidgets.QTreeWidgetItem(["All targets", ""])
        all_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, "all")
        all_item.setToolTip(0, "Compare all measured stress targets")
        self.target_list.addTopLevelItem(all_item)
        for target_mpa in self._targets_mpa:
            equivalent = self._equivalent_by_target.get(round(target_mpa, 9), "")
            item = QtWidgets.QTreeWidgetItem(
                [
                    format_target_mpa(target_mpa),
                    "" if equivalent == "-" else equivalent,
                ]
            )
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, float(target_mpa))
            item.setToolTip(
                0,
                f"Inspect plots for {format_target_mpa(target_mpa)}"
                + ("" if not equivalent else f" ({equivalent})")
            )
            self.target_list.addTopLevelItem(item)
        self.target_list.resizeColumnToContents(1)

    def _sync_controls(self) -> None:
        resolved = resolve_selected_target(
            self._selection,
            active_target_mpa=self._active_target_mpa,
            measured_targets_mpa=self._targets_mpa,
        )
        self.follow_button.setChecked(self._selection.mode == "follow_active")
        self.all_button.setChecked(self._selection.mode == "all")
        active_count = 0
        if self._active_target_mpa is not None:
            active_count = sum(
                1
                for target in self._targets_mpa
                if target <= self._active_target_mpa + TARGET_MATCH_ABS_TOLERANCE_MPA
            )
        self.count_label.setText(f"{active_count} of {len(self._targets_mpa)} active")
        self.target_list.blockSignals(True)
        try:
            self.target_list.clearSelection()
            if self._selection.mode == "all":
                self.target_list.setCurrentItem(self.target_list.topLevelItem(0))
            elif self._selection.mode in {"target", "follow_active"} and resolved is not None:
                for row in range(self.target_list.topLevelItemCount()):
                    item = self.target_list.topLevelItem(row)
                    value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                    if isinstance(value, (int, float)) and math.isclose(
                        float(value),
                        float(resolved),
                        rel_tol=0.0,
                        abs_tol=TARGET_MATCH_ABS_TOLERANCE_MPA,
                    ):
                        item.setSelected(True)
                        self.target_list.setCurrentItem(item)
                        break
            for row in range(self.target_list.topLevelItemCount()):
                item = self.target_list.topLevelItem(row)
                value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                font = QtGui.QFont(item.font(0))
                is_active = (
                    isinstance(value, (int, float))
                    and self._active_target_mpa is not None
                    and math.isclose(
                        float(value),
                        self._active_target_mpa,
                        rel_tol=0.0,
                        abs_tol=TARGET_MATCH_ABS_TOLERANCE_MPA,
                    )
                )
                font.setBold(is_active)
                item.setFont(0, font)
                if isinstance(value, (int, float)):
                    target_text = format_target_mpa(float(value))
                    item.setText(0, target_text)
                    equivalent = self._equivalent_by_target.get(
                        round(float(value), 9),
                        "",
                    )
                    item.setToolTip(
                        0,
                        ("Active target. " if is_active else "")
                        + f"Inspect plots for {target_text}"
                        + ("" if not equivalent else f" ({equivalent})"),
                    )
                item.setForeground(
                    0,
                    QtGui.QBrush(
                        QtGui.QColor("#e8ad43" if is_active else "#9ca5af")
                    ),
                )
                item.setForeground(1, QtGui.QBrush(QtGui.QColor("#9ca5af")))
        finally:
            self.target_list.blockSignals(False)

        active_text = (
            "-"
            if self._active_target_mpa is None
            else format_target_mpa(self._active_target_mpa)
        )
        self.active_label.setText(f"Active  {active_text}")
        if self._selection.mode == "all":
            inspected_text = "All measured targets"
        elif resolved is None:
            inspected_text = "-"
        else:
            inspected_text = format_target_mpa(resolved)
        self.inspected_label.setText(f"Inspecting  {inspected_text}")

    def _select_follow_active(self) -> None:
        self._selection = StressTargetSelection.follow_active()
        self._sync_controls()
        self.selection_changed.emit(self._selection)

    def _select_all(self) -> None:
        self._selection = StressTargetSelection.all_targets()
        self._sync_controls()
        self.selection_changed.emit(self._selection)

    def _select_item(
        self,
        item: QtWidgets.QTreeWidgetItem,
        _column: int = 0,
    ) -> None:
        target_mpa = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if target_mpa == "all":
            self._select_all()
            return
        if target_mpa is None:
            return
        self._selection = StressTargetSelection.target(float(target_mpa))
        self._sync_controls()
        self.selection_changed.emit(self._selection)
