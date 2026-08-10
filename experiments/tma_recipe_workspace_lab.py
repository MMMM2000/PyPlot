"""Python-native adaptive workspace studies for TMA iso-load and iso-strain."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from experiments.tma_ui_design_lab import (
    C,
    DIRECTIONS,
    PlotPanel,
    StatusDot,
    install_palette,
    label,
    metric,
    render_widget,
    status_row,
)


SCREENSHOTS = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "tma-adaptive-workspace"
    / "recipe-studies"
)


@dataclass(frozen=True)
class RecipeProfile:
    key: str
    name: str
    target_name: str
    target_unit: str
    alternate_unit: str
    active_target: float
    targets: tuple[float, ...]
    outcome_name: str
    outcome_unit: str
    phase: str
    phase_detail: str


PROFILES = {
    "iso-load": RecipeProfile(
        key="iso-load",
        name="Iso-load current sweep",
        target_name="Load",
        target_unit="g",
        alternate_unit="MPa",
        active_target=2.5,
        targets=tuple(index * 0.5 for index in range(1, 11)),
        outcome_name="Strain",
        outcome_unit="%",
        phase="LOAD RECOVERY HOLD",
        phase_detail="Current held at 17.4 mA while the motor restores load",
    ),
    "iso-strain": RecipeProfile(
        key="iso-strain",
        name="Iso-strain current sweep",
        target_name="Strain",
        target_unit="%",
        alternate_unit="mm",
        active_target=2.5,
        targets=tuple(index * 0.5 for index in range(1, 11)),
        outcome_name="Stress",
        outcome_unit="MPa",
        phase="STRAIN RECOVERY HOLD",
        phase_detail="Current held at 17.4 mA while the motor restores strain",
    ),
}


def compact_number(value: float, *, decimals: int = 3) -> str:
    rounded = round(float(value))
    if math.isclose(float(value), float(rounded), abs_tol=1e-9):
        return str(rounded)
    return f"{float(value):.{decimals}f}".rstrip("0").rstrip(".")


def alternate_value(profile: RecipeProfile, target: float) -> float:
    if profile.key == "iso-load":
        return target / 0.00753
    return target * 0.421


def target_text(profile: RecipeProfile, target: float) -> str:
    return f"{compact_number(target)} {profile.target_unit}"


def alternate_text(profile: RecipeProfile, target: float) -> str:
    value = alternate_value(profile, target)
    decimals = 1 if profile.key == "iso-load" else 3
    return f"{compact_number(value, decimals=decimals)} {profile.alternate_unit}"


def _curve(profile: RecipeProfile, target: float) -> tuple[list[float], list[float], list[float]]:
    current = [1.0 + index * 0.5 for index in range(59)]
    normalized = target / max(profile.targets)
    resistance = [
        248.0
        + 7.0 * target
        + 160.0 / (1.0 + math.exp(-(value - 16.0) / 3.0))
        + 2.0 * math.sin(value * 0.7 + target)
        for value in current
    ]
    if profile.key == "iso-load":
        outcome = [
            0.18 * target
            + (2.2 + 5.8 * normalized)
            / (1.0 + math.exp(-(value - (17.5 - target * 0.4)) / 2.3))
            + 0.03 * math.sin(value * 0.8 + target)
            for value in current
        ]
    else:
        outcome = [
            85.0
            + target * 74.0
            - (35.0 + target * 19.0)
            / (1.0 + math.exp(-(value - (16.5 - target * 0.25)) / 2.6))
            + 2.2 * math.sin(value * 0.65 + target)
            for value in current
        ]
    return current, outcome, resistance


def _progress(profile: RecipeProfile) -> dict[str, list[float]]:
    count = 520
    time_s: list[float] = []
    controlled: list[float] = []
    response: list[float] = []
    current: list[float] = []
    for index in range(count):
        x = index / (count - 1)
        target_index = min(len(profile.targets) - 1, int(x * len(profile.targets)))
        target = profile.targets[target_index]
        phase = (x * len(profile.targets)) % 1.0
        triangular = 1.0 - abs(phase * 2.0 - 1.0)
        time_s.append(x * 7200.0)
        controlled.append(target + 0.025 * math.sin(index * 0.35))
        if profile.key == "iso-load":
            response.append(target * 0.22 + triangular * (1.9 + target * 0.95))
        else:
            response.append(88.0 + target * 72.0 - triangular * (28.0 + target * 16.0))
        current.append(1.0 + triangular * 29.0)
    return {
        "time": time_s,
        "controlled": controlled,
        "response": response,
        "current": current,
    }


class RecipeWorkspaceWindow(QtWidgets.QMainWindow):
    def __init__(self, profile: RecipeProfile) -> None:
        super().__init__()
        self.profile = profile
        self.follow_active = True
        self.selected_target: float | None = profile.active_target
        self.target_items: dict[float | None, QtWidgets.QTreeWidgetItem] = {}
        self.setWindowTitle(f"TMA UI Design Lab - {profile.name}")
        self.resize(1440, 900)
        self.setMinimumSize(1100, 720)
        self._apply_style()

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_workspace(), 1)
        root.addWidget(self._build_dock())
        self._set_target(profile.active_target, follow=True)

    def _apply_style(self) -> None:
        accent = DIRECTIONS["adaptive-v4"].accent
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: {C['window']}; }}
            QWidget {{ color: {C['text']}; background: transparent; font-family: 'Segoe UI'; font-size: 13px; }}
            QFrame#header {{ background: {C['surface']}; border-bottom: 1px solid {C['line']}; }}
            QFrame#dock {{ background: {C['surface']}; border-top: 1px solid {C['line']}; }}
            QFrame#inspectorPanel, QFrame#targetPanel {{ background: {C['surface']}; border: 1px solid {C['line_soft']}; border-radius: 4px; }}
            QLabel[role='product'] {{ font-size: 20px; font-weight: 700; }}
            QLabel[role='direction'], QLabel[role='hint'], QLabel[role='metric-name'] {{ color: {C['muted']}; font-size: 11px; }}
            QLabel[role='page-title'] {{ font-size: 17px; font-weight: 650; }}
            QLabel[role='value'] {{ font-weight: 600; }}
            QLabel[role='metric-value'] {{ font-size: 18px; font-weight: 700; }}
            QLabel[role='phase'] {{ background: {accent}; color: #101214; border-radius: 3px; padding: 4px 8px; font-size: 11px; font-weight: 750; }}
            QPushButton {{ background: {C['surface2']}; border: 1px solid {C['line']}; border-radius: 4px; padding: 7px 12px; min-height: 20px; }}
            QPushButton:hover {{ background: {C['surface3']}; }}
            QPushButton[stage='true'] {{ border: 0; background: transparent; color: {C['muted']}; padding: 8px 12px; }}
            QPushButton[stage='true'][active='true'] {{ color: {C['text']}; border-bottom: 2px solid {accent}; font-weight: 650; }}
            QPushButton#primary {{ background: {accent}; color: #101214; border-color: {accent}; font-weight: 700; }}
            QPushButton#danger {{ background: {C['red']}; color: white; border-color: #e34b4b; font-weight: 750; min-width: 132px; }}
            QPushButton#followActive:checked {{ background: {accent}; color: #101214; border-color: {accent}; font-weight: 700; }}
            QTreeWidget#targetNavigator {{ background: transparent; border: 0; outline: 0; }}
            QTreeWidget#targetNavigator::item {{ min-height: 31px; padding: 2px 5px; color: {C['muted']}; }}
            QTreeWidget#targetNavigator::item:selected {{ background: {C['surface2']}; color: {C['text']}; border-left: 3px solid {accent}; }}
            QProgressBar {{ background: {C['base']}; border: 1px solid {C['line']}; border-radius: 3px; text-align: center; min-height: 20px; }}
            QProgressBar::chunk {{ background: {accent}; }}
            QTabWidget::pane {{ border: 0; }}
            QTabBar::tab {{ background: transparent; color: {C['muted']}; padding: 8px 13px; border-bottom: 1px solid {C['line']}; }}
            QTabBar::tab:selected {{ color: {C['text']}; border-bottom: 2px solid {accent}; }}
            """
        )

    def _build_header(self) -> QtWidgets.QWidget:
        header = QtWidgets.QFrame(self)
        header.setObjectName("header")
        header.setFixedHeight(76)
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(18)
        brand = QtWidgets.QWidget(header)
        brand_layout = QtWidgets.QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(0)
        brand_layout.addWidget(label("TMA Logger", "product"))
        brand_layout.addWidget(label(self.profile.name, "direction"))
        brand.setMinimumWidth(255)
        layout.addWidget(brand)
        for text in ("Prepare", "Run", "Review"):
            button = QtWidgets.QPushButton(text, header)
            button.setProperty("stage", True)
            button.setProperty("active", text == "Run")
            layout.addWidget(button)
        layout.addStretch(1)
        if self.profile.key == "iso-load":
            values = (
                ("LOAD", "2.52 g", "335 MPa"),
                ("STRAIN", "6.82 %", "2.871 mm"),
                ("CURRENT", "17.4 mA", "235 A/mm2"),
            )
        else:
            values = (
                ("STRAIN", "2.54 %", "1.069 mm"),
                ("STRESS", "282 MPa", "2.12 g"),
                ("CURRENT", "17.4 mA", "235 A/mm2"),
            )
        for item in values:
            layout.addWidget(metric(item[0], item[1], secondary=item[2]))
        emergency = QtWidgets.QPushButton("EMERGENCY STOP", header)
        emergency.setObjectName("danger")
        layout.addWidget(emergency)
        return header

    def _build_workspace(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(14)
        layout.addWidget(self._build_targets())

        center = QtWidgets.QWidget(page)
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)
        headline = QtWidgets.QHBoxLayout()
        self.headline_target = label("", "page-title")
        headline.addWidget(self.headline_target)
        headline.addWidget(label(self.profile.phase, "phase"))
        headline.addWidget(label(self.profile.phase_detail, "hint"))
        headline.addStretch(1)
        headline.addWidget(status_row("Temperature", "37.8 C"))
        center_layout.addLayout(headline)

        view_bar = QtWidgets.QHBoxLayout()
        self.view_context = label("", "value")
        view_bar.addWidget(self.view_context)
        view_bar.addStretch(1)
        self.return_button = QtWidgets.QPushButton("Following active", center)
        self.return_button.setEnabled(False)
        self.return_button.clicked.connect(
            lambda: self._set_target(self.profile.active_target, follow=True)
        )
        view_bar.addWidget(self.return_button)
        center_layout.addLayout(view_bar)

        self.result_tabs = QtWidgets.QTabWidget(center)
        self.outcome_plot = PlotPanel("", [], "Measured current (mA)", "")
        self.resistance_plot = PlotPanel("", [], "Measured current (mA)", "Resistance (ohm)")
        self.result_tabs.addTab(
            self.outcome_plot,
            f"{self.profile.outcome_name} vs current",
        )
        self.result_tabs.addTab(self.resistance_plot, "Resistance vs current")
        center_layout.addWidget(self.result_tabs, 3)

        support = QtWidgets.QHBoxLayout()
        support.setSpacing(10)
        self.controlled_plot = PlotPanel("", [], "Time (s)", "", compact=True)
        self.response_plot = PlotPanel("", [], "Time (s)", "", compact=True)
        self.current_plot = PlotPanel("Current progress", [], "Time (s)", "Current (mA)", compact=True)
        support.addWidget(self.controlled_plot, 1)
        support.addWidget(self.response_plot, 1)
        support.addWidget(self.current_plot, 1)
        center_layout.addLayout(support, 2)
        layout.addWidget(center, 1)
        layout.addWidget(self._build_inspector())
        return page

    def _build_targets(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame(self)
        frame.setObjectName("targetPanel")
        frame.setFixedWidth(215)
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(9, 10, 9, 8)
        layout.setSpacing(6)
        title = label(f"{self.profile.target_name} targets", "value")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        layout.addWidget(label("5 of 10 active", "hint"))
        self.follow_button = QtWidgets.QPushButton("Follow active target", frame)
        self.follow_button.setObjectName("followActive")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(True)
        self.follow_button.clicked.connect(
            lambda checked: self._set_target(self.profile.active_target, follow=checked)
        )
        layout.addWidget(self.follow_button)
        self.target_tree = QtWidgets.QTreeWidget(frame)
        self.target_tree.setObjectName("targetNavigator")
        self.target_tree.setColumnCount(2)
        self.target_tree.setHeaderHidden(True)
        self.target_tree.setRootIsDecorated(False)
        self.target_tree.setIndentation(0)
        self.target_tree.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.target_tree.setColumnWidth(0, 104)
        all_item = QtWidgets.QTreeWidgetItem(["All targets", ""])
        all_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, None)
        self.target_tree.addTopLevelItem(all_item)
        self.target_items[None] = all_item
        for target in self.profile.targets:
            active = math.isclose(target, self.profile.active_target)
            item = QtWidgets.QTreeWidgetItem(
                [
                    target_text(self.profile, target) + ("  active" if active else ""),
                    alternate_text(self.profile, target),
                ]
            )
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, target)
            color = C["green"] if target < self.profile.active_target else DIRECTIONS["adaptive-v4"].accent if active else C["faint"]
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(color)))
            item.setForeground(1, QtGui.QBrush(QtGui.QColor(C["muted"])))
            self.target_tree.addTopLevelItem(item)
            self.target_items[target] = item
        self.target_tree.itemClicked.connect(self._inspect_item)
        layout.addWidget(self.target_tree, 1)
        return frame

    def _build_inspector(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame(self)
        frame.setObjectName("inspectorPanel")
        frame.setFixedWidth(320)
        root = QtWidgets.QVBoxLayout(frame)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        root.addWidget(label("Active sweep", "page-title"))
        self.inspector_subtitle = label("", "hint")
        root.addWidget(self.inspector_subtitle)
        progress = QtWidgets.QProgressBar(frame)
        progress.setRange(0, 100)
        progress.setValue(48)
        progress.setFormat("48% | ETA 38 min")
        root.addWidget(progress)
        self.target_metric = metric("TARGET", "-", secondary="-")
        self.processed_metric = metric("PROCESSED", "-", secondary="-")
        self.error_metric = metric("ERROR / BAND", "-", secondary="-")
        values = QtWidgets.QGridLayout()
        values.setHorizontalSpacing(14)
        values.setVerticalSpacing(8)
        values.addWidget(self.target_metric, 0, 0)
        values.addWidget(self.processed_metric, 0, 1)
        values.addWidget(self.error_metric, 1, 0)
        values.addWidget(metric("CURRENT HELD", "17.4 mA", secondary="235 A/mm2"), 1, 1)
        root.addLayout(values)
        root.addSpacing(4)
        root.addWidget(status_row("Motor", "correcting | 2.871 mm", DIRECTIONS["adaptive-v4"].accent))
        if self.profile.key == "iso-load":
            root.addWidget(status_row("Raw load", "2.56 g", C["faint"]))
            root.addWidget(status_row("Live stress", "340 MPa", C["faint"]))
        else:
            root.addWidget(status_row("Raw strain", "2.57 %", C["faint"]))
            root.addWidget(status_row("Live displacement", "1.082 mm", C["faint"]))
        root.addSpacing(8)
        root.addWidget(label("Remaining recipe", "value"))
        root.addWidget(status_row("Targets", "3 - 5 " + self.profile.target_unit, C["faint"]))
        root.addWidget(status_row("Current", "1 - 30 mA", C["faint"]))
        root.addWidget(status_row("Tolerance", "automatic", C["green"]))
        root.addStretch(1)
        return frame

    def _build_dock(self) -> QtWidgets.QWidget:
        dock = QtWidgets.QFrame(self)
        dock.setObjectName("dock")
        dock.setFixedHeight(66)
        layout = QtWidgets.QHBoxLayout(dock)
        layout.setContentsMargins(18, 9, 18, 9)
        status = QtWidgets.QWidget(dock)
        status_layout = QtWidgets.QVBoxLayout(status)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(1)
        status_layout.addWidget(label(f"At {target_text(self.profile, self.profile.active_target)} | {self.profile.phase.lower()}", "value"))
        status_layout.addWidget(label("Target 5/10 | Motor correcting | Overall 48% | ETA 38 min", "hint"))
        layout.addWidget(status, 1)
        layout.addWidget(QtWidgets.QPushButton("Run log", dock))
        update = QtWidgets.QPushButton("Update remaining sweeps", dock)
        update.setObjectName("primary")
        layout.addWidget(update)
        layout.addWidget(QtWidgets.QPushButton("Pause", dock))
        layout.addWidget(QtWidgets.QPushButton("Stop", dock))
        return dock

    def _inspect_item(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        self._set_target(None if value is None else float(value), follow=False)

    def _set_target(self, target: float | None, *, follow: bool) -> None:
        self.selected_target = target
        self.follow_active = follow
        blocker = QtCore.QSignalBlocker(self.follow_button)
        self.follow_button.setChecked(follow)
        del blocker
        self.target_tree.setCurrentItem(self.target_items[target])
        self.return_button.setText("Following active" if follow else "Return to active")
        self.return_button.setEnabled(not follow)
        active_text = target_text(self.profile, self.profile.active_target)
        if follow:
            context = f"Following active target | {active_text}"
        elif target is None:
            context = f"Comparing all measured targets | live target {active_text}"
        else:
            context = f"Inspecting {target_text(self.profile, target)} | live target remains {active_text}"
        self.view_context.setText(context)
        self.headline_target.setText(active_text)
        self.inspector_subtitle.setText(f"{active_text} | target 5 of 10")
        self._update_metrics()
        self._update_plots()

    def _update_metrics(self) -> None:
        target = self.profile.active_target
        equivalent = alternate_text(self.profile, target)
        processed = target + (0.02 if self.profile.key == "iso-load" else 0.04)
        error = processed - target
        band = 0.04 if self.profile.key == "iso-load" else 0.08
        self._set_metric(self.target_metric, target_text(self.profile, target), equivalent)
        self._set_metric(
            self.processed_metric,
            target_text(self.profile, processed),
            alternate_text(self.profile, processed),
        )
        self._set_metric(
            self.error_metric,
            f"{compact_number(error)} / +/-{compact_number(band)} {self.profile.target_unit}",
            f"{alternate_text(self.profile, error)} / +/-{alternate_text(self.profile, band)}",
        )

    @staticmethod
    def _set_metric(widget: QtWidgets.QWidget, value: str, secondary: str) -> None:
        labels = widget.findChildren(QtWidgets.QLabel)
        labels[1].setText(value)
        if len(labels) > 2:
            labels[2].setText(secondary)

    def _series(self, kind: str) -> list[tuple[list[float], list[float], str, str]]:
        targets = self.profile.targets if self.selected_target is None else (self.selected_target,)
        result = []
        for target in targets:
            current, outcome, resistance = _curve(self.profile, target)
            active = math.isclose(target, self.profile.active_target)
            color = DIRECTIONS["adaptive-v4"].accent if active else C["teal"]
            if self.selected_target is None and not active:
                color = "#237f78"
            values = outcome if kind == "outcome" else resistance
            result.append((current, values, color, target_text(self.profile, target)))
        return result

    def _update_plots(self) -> None:
        scope = (
            f"all measured {self.profile.target_name.lower()} targets"
            if self.selected_target is None
            else target_text(self.profile, self.selected_target)
        )
        self.outcome_plot.title = f"{self.profile.outcome_name} vs current | {scope}"
        self.outcome_plot.y_label = f"{self.profile.outcome_name} ({self.profile.outcome_unit})"
        self.outcome_plot.series = self._series("outcome")
        self.outcome_plot.update()
        self.resistance_plot.title = f"Resistance vs current | {scope}"
        self.resistance_plot.series = self._series("resistance")
        self.resistance_plot.update()

        progress = _progress(self.profile)
        time_s = progress["time"]
        self.controlled_plot.title = f"{self.profile.target_name} progress"
        self.controlled_plot.y_label = f"{self.profile.target_name} ({self.profile.target_unit})"
        self.controlled_plot.series = [(time_s, progress["controlled"], C["orange"], self.profile.target_name)]
        self.controlled_plot.update()
        response_name = "Strain" if self.profile.key == "iso-load" else "Stress"
        response_unit = "%" if self.profile.key == "iso-load" else "MPa"
        self.response_plot.title = f"{response_name} response progress"
        self.response_plot.y_label = f"{response_name} ({response_unit})"
        self.response_plot.series = [(time_s, progress["response"], C["green"], response_name)]
        self.response_plot.update()
        self.current_plot.series = [(time_s, progress["current"], C["coral"], "Current")]
        self.current_plot.update()


def launch_profile(profile: str) -> RecipeWorkspaceWindow:
    window = RecipeWorkspaceWindow(PROFILES[profile])
    window.show()
    return window


def launch_iso_load(*_args: object, **_kwargs: object) -> RecipeWorkspaceWindow:
    return launch_profile("iso-load")


def launch_iso_strain(*_args: object, **_kwargs: object) -> RecipeWorkspaceWindow:
    return launch_profile("iso-strain")


def render_recipe_studies(app: QtWidgets.QApplication) -> tuple[Path, Path]:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for key in ("iso-load", "iso-strain"):
        window = RecipeWorkspaceWindow(PROFILES[key])
        window.resize(1440, 900)
        window.show()
        app.processEvents()
        path = SCREENSHOTS / f"tma-adaptive-{key}-1440x900.png"
        render_widget(window, path)
        paths.append(path)
        window.close()
        app.processEvents()
    return paths[0], paths[1]


def main() -> int:
    import sys

    app = QtWidgets.QApplication(sys.argv)
    install_palette(app)
    window = launch_iso_load()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
