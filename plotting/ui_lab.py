"""Isolated Qt-native UI laboratory for the PyPlot redesign.

The laboratory deliberately uses synthetic data and does not read or write
projects, application settings, or user data.  It exists to validate layout,
window management, interaction states, and Qt rendering before production UI
changes are attempted.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6 import QtCore, QtGui, QtWidgets


@dataclass(frozen=True)
class PrototypeTool:
    """Small launcher record used only by the UI laboratory."""

    name: str
    category: str
    description: str
    status: str = "Ready"


PROTOTYPE_TOOLS: tuple[PrototypeTool, ...] = (
    PrototypeTool(
        "TMA",
        "Plotting",
        "Thermomechanical analysis logger and target-linked review.",
        "Review",
    ),
    PrototypeTool(
        "Current Annealing",
        "Plotting",
        "Resistance and temperature with heating and cooling branches.",
    ),
    PrototypeTool(
        "VSM Hysteresis Loops",
        "Plotting",
        "Compare magnetic hysteresis loops across measurement runs.",
    ),
    PrototypeTool(
        "VSM Temperature Scan",
        "Plotting",
        "Compare temperature-dependent magnetic measurements.",
    ),
    PrototypeTool(
        "VSM Isotherms",
        "Plotting",
        "Review field-dependent isothermal magnetization.",
    ),
    PrototypeTool(
        "DMA Iso-Stress",
        "Plotting",
        "Plot iso-stress DMA sweeps and transformation response.",
    ),
    PrototypeTool("FMR", "Plotting", "Plot ferromagnetic resonance datasets."),
    PrototypeTool(
        "Hsw Distribution",
        "Plotting",
        "Inspect switching-field distributions.",
    ),
    PrototypeTool(
        "Hsw Load Compare",
        "Plotting",
        "Compare switching fields across mechanical loads.",
    ),
    PrototypeTool(
        "Hysteresis Loops",
        "Plotting",
        "Build and compare general hysteresis loops.",
    ),
    PrototypeTool(
        "Manual Stress/Strain",
        "Plotting",
        "Create stress-strain plots from manually selected data.",
    ),
    PrototypeTool(
        "Maxion Continuous",
        "Plotting",
        "Review continuous Maxion acquisition files.",
    ),
    PrototypeTool("PDF Plotter", "Plotting", "Arrange PDF pages for comparison."),
    PrototypeTool("PyPlot", "Plotting", "Open the full scientific plotting workspace."),
    PrototypeTool("R vs T", "Plotting", "Plot resistance as a function of temperature."),
    PrototypeTool(
        "Strain 3D Plot",
        "Plotting",
        "Explore strain measurements in a three-dimensional view.",
    ),
    PrototypeTool(
        "Stress Dependence",
        "Plotting",
        "Compare material response across applied stress.",
    ),
    PrototypeTool(
        "Stress Sensitivity",
        "Plotting",
        "Analyze stress-dependent sensitivity.",
    ),
    PrototypeTool(
        "Temperature Dependence",
        "Plotting",
        "Compare measurements across temperature.",
    ),
    PrototypeTool(
        "Temperature Sensitivity",
        "Plotting",
        "Analyze temperature-dependent sensitivity.",
    ),
    PrototypeTool(
        "Microwire Data Builder",
        "Builders",
        "Assemble and review a synthetic microwire dataset.",
    ),
    PrototypeTool(
        "Current Annealing Logger",
        "Loggers",
        "Synthetic logger entry shown without hardware access.",
        "Unavailable in prototype",
    ),
    PrototypeTool(
        "Instrument Emulator",
        "Emulators",
        "Open a software-only instrument emulator.",
    ),
    PrototypeTool(
        "TMA UI Design Lab",
        "Experiments",
        "Open the tracked TMA interface experiments.",
    ),
)


@dataclass(frozen=True)
class LauncherVariant:
    key: str
    title: str
    size: QtCore.QSize
    minimum_size: QtCore.QSize
    two_columns: bool = False
    compact_rows: bool = False
    bottom_dock: bool = False


LAUNCHER_VARIANTS: dict[str, LauncherVariant] = {
    "a": LauncherVariant(
        "a",
        "Conservative refinement",
        QtCore.QSize(760, 540),
        QtCore.QSize(640, 440),
    ),
    "b": LauncherVariant(
        "b",
        "Compact two-column browser",
        QtCore.QSize(720, 460),
        QtCore.QSize(640, 400),
        two_columns=True,
        compact_rows=True,
    ),
    "c": LauncherVariant(
        "c",
        "Quiet tabs and action dock",
        QtCore.QSize(700, 500),
        QtCore.QSize(620, 420),
        compact_rows=True,
        bottom_dock=True,
    ),
}


def _standard_icon(
    widget: QtWidgets.QWidget, pixmap: QtWidgets.QStyle.StandardPixmap
) -> QtGui.QIcon:
    return widget.style().standardIcon(pixmap)


def apply_prototype_theme(
    app: QtWidgets.QApplication, *, dark: bool = True
) -> None:
    """Apply a restrained Fusion palette to the standalone prototype."""

    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    if dark:
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#111417"))
        palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#f1f3f5"))
        palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#111417"))
        palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#181c20"))
        palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor("#1a1e22"))
        palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor("#f1f3f5"))
        palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#e5e7eb"))
        palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor("#1a1e22"))
        palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#f1f3f5"))
        palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor("#3a3222"))
        palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor("#f4b63f"))
        palette.setColor(QtGui.QPalette.ColorRole.PlaceholderText, QtGui.QColor("#8f969f"))
    else:
        palette = app.style().standardPalette()
        palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor("#d99822"))
        palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor("#ffffff"))
    app.setPalette(palette)
    app.setStyleSheet(
        """
        QMainWindow, QWidget { font-family: "Segoe UI"; font-size: 9pt; }
        QToolBar { spacing: 4px; padding: 3px; }
        QToolButton { padding: 4px 7px; }
        QPushButton { min-height: 26px; padding: 2px 10px; border-radius: 3px; }
        QLineEdit, QComboBox, QSpinBox { min-height: 26px; padding: 1px 7px; border-radius: 3px; }
        QListWidget, QTreeWidget, QTableWidget { border: 1px solid palette(mid); }
        QDockWidget::title { padding: 5px; }
        QGroupBox { margin-top: 12px; }
        QGroupBox::title { subcontrol-origin: margin; left: 7px; }
        QProgressBar { min-height: 16px; text-align: center; }
        QProgressBar::chunk { background-color: #e8ad43; }
        QLabel[status="ready"] { color: #4fc78a; font-weight: 600; }
        QLabel[status="warning"] { color: #e1b45b; font-weight: 600; }
        """
    )


class _ToolListDelegate(QtWidgets.QStyledItemDelegate):
    """Render compact two-line tool rows while retaining a native item view."""

    def __init__(
        self,
        parent: QtCore.QObject | None = None,
        *,
        compact: bool = False,
        columns: int = 1,
    ) -> None:
        super().__init__(parent)
        self._compact = compact
        self._columns = max(1, columns)

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtCore.QSize:
        width = option.rect.width()
        view = self.parent()
        if self._columns > 1 and isinstance(view, QtWidgets.QAbstractItemView):
            width = max(180, view.viewport().width() // self._columns)
        return QtCore.QSize(width, 26 if self._compact else 48)

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        tool = index.data(QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(tool, PrototypeTool):
            super().paint(painter, option, index)
            return

        item_option = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(item_option, index)
        item_option.text = ""
        widget = option.widget
        style = widget.style() if widget is not None else QtWidgets.QApplication.style()
        selected = bool(
            option.state & QtWidgets.QStyle.StateFlag.State_Selected
        )
        hovered = bool(option.state & QtWidgets.QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.fillRect(
                option.rect,
                QtGui.QColor("#24282d" if selected else "#191d21"),
            )
        if selected:
            painter.fillRect(
                QtCore.QRect(option.rect.left(), option.rect.top(), 3, option.rect.height()),
                QtGui.QColor("#e8ad43"),
            )

        primary = QtGui.QColor("#f1f3f5" if selected else "#d9dde2")
        secondary = QtGui.QColor(primary)
        secondary.setAlpha(155)
        status_color = (
            QtGui.QColor("#e8ad43")
            if selected
            else QtGui.QColor("#4fc78a" if tool.status == "Ready" else "#e1b45b")
        )

        content = option.rect.adjusted(12, 3 if self._compact else 4, -12, -3)
        painter.save()
        name_font = QtGui.QFont(option.font)
        name_font.setWeight(
            QtGui.QFont.Weight.DemiBold if selected else QtGui.QFont.Weight.Normal
        )
        painter.setFont(name_font)
        if self._compact:
            name = painter.fontMetrics().elidedText(
                tool.name,
                QtCore.Qt.TextElideMode.ElideRight,
                content.width(),
            )
            painter.setPen(primary)
            painter.drawText(
                content,
                QtCore.Qt.AlignmentFlag.AlignLeft
                | QtCore.Qt.AlignmentFlag.AlignVCenter,
                name,
            )
            painter.restore()
            return

        status_width = min(170, max(68, content.width() // 4))
        name_rect = QtCore.QRect(content.left(), content.top(), content.width() - status_width - 10, 19)
        status_rect = QtCore.QRect(
            name_rect.right() + 10, content.top(), status_width, 19
        )
        name = painter.fontMetrics().elidedText(
            tool.name, QtCore.Qt.TextElideMode.ElideRight, name_rect.width()
        )
        painter.setPen(primary)
        painter.drawText(
            name_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft
            | QtCore.Qt.AlignmentFlag.AlignVCenter,
            name,
        )
        status = painter.fontMetrics().elidedText(
            tool.status,
            QtCore.Qt.TextElideMode.ElideRight,
            status_rect.width(),
        )
        painter.setPen(status_color)
        painter.drawText(
            status_rect,
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter,
            status,
        )

        description_font = QtGui.QFont(option.font)
        description_font.setPointSizeF(max(8.0, description_font.pointSizeF() - 1.0))
        painter.setFont(description_font)
        description_rect = QtCore.QRect(
            content.left(), content.top() + 19, content.width(), 18
        )
        description = painter.fontMetrics().elidedText(
            tool.description,
            QtCore.Qt.TextElideMode.ElideRight,
            description_rect.width(),
        )
        painter.setPen(secondary)
        painter.drawText(
            description_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft
            | QtCore.Qt.AlignmentFlag.AlignVCenter,
            description,
        )
        painter.restore()


class LauncherPrototype(QtWidgets.QMainWindow):
    """Compact evolution of the existing category-and-list launcher."""

    workspace_opened = QtCore.pyqtSignal(object)

    def __init__(
        self,
        tools: Sequence[PrototypeTool] = PROTOTYPE_TOOLS,
        *,
        variant: str = "c",
    ) -> None:
        super().__init__()
        if variant not in LAUNCHER_VARIANTS:
            raise ValueError(f"Unknown launcher variant: {variant}")
        self.variant = LAUNCHER_VARIANTS[variant]
        self.setObjectName("pyplotUiLabLauncher")
        self.setProperty("launcherVariant", variant)
        self.setWindowTitle(f"PyPlot Launcher - Option {variant.upper()}")
        self.resize(self.variant.size)
        self.setMinimumSize(self.variant.minimum_size)
        self._tools = tuple(tools)
        self._workspaces: list[PlotWorkspacePrototype] = []

        self._build_menu()
        self._build_ui()
        self._apply_launcher_style()
        self._populate_categories()
        self._filter_tools()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        close_action = file_menu.addAction("&Close")
        close_action.setShortcut(QtGui.QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close)
        view_menu = self.menuBar().addMenu("&View")
        focus_search = view_menu.addAction("Focus &Search")
        focus_search.setShortcut(QtGui.QKeySequence("Ctrl+K"))
        focus_search.triggered.connect(lambda: self.search.setFocus())
        self.menuBar().addMenu("&Developer")
        self.menuBar().addMenu("&Help")
        self.menuBar().addMenu("&Sort")

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        central.setObjectName("launcherCentral")
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(12, 10, 12, 0 if self.variant.bottom_dock else 10)
        outer.setSpacing(8)

        self.search = QtWidgets.QLineEdit()
        self.search.setObjectName("launcherSearch")
        self.search.setAccessibleName("Search PyPlot tools")
        self.search.setPlaceholderText("Search tools...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_tools)
        outer.addWidget(self.search)

        self.categories = QtWidgets.QTabBar()
        self.categories.setObjectName("launcherCategories")
        self.categories.setAccessibleName("Tool categories")
        self.categories.setExpanding(False)
        self.categories.setUsesScrollButtons(True)
        self.categories.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.categories.currentChanged.connect(self._filter_tools)
        outer.addWidget(self.categories)

        self.tools = QtWidgets.QListWidget()
        self.tools.setObjectName("launcherTools")
        self.tools.setAccessibleName("Available tools")
        self.tools.setItemDelegate(
            _ToolListDelegate(
                self.tools,
                compact=self.variant.compact_rows,
                columns=2 if self.variant.two_columns else 1,
            )
        )
        self.tools.setUniformItemSizes(True)
        self.tools.setAlternatingRowColors(False)
        self.tools.setMouseTracking(True)
        self.tools.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.tools.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        if self.variant.two_columns:
            self.tools.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
            self.tools.setFlow(QtWidgets.QListView.Flow.TopToBottom)
            self.tools.setWrapping(True)
            self.tools.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
            self.tools.setMovement(QtWidgets.QListView.Movement.Static)
            self.tools.setSpacing(0)
        self.tools.currentRowChanged.connect(self._update_selection)
        self.tools.itemDoubleClicked.connect(lambda _item: self.open_selected())
        outer.addWidget(self.tools, 1)

        footer_widget = QtWidgets.QFrame()
        footer_widget.setObjectName("launcherFooter")
        footer = QtWidgets.QHBoxLayout(footer_widget)
        footer.setContentsMargins(0 if not self.variant.bottom_dock else 12, 8, 0 if not self.variant.bottom_dock else 12, 8)
        footer.setSpacing(10)
        self.selection_summary = QtWidgets.QLabel()
        self.selection_summary.setAccessibleName("Selected tool summary")
        footer.addWidget(self.selection_summary)
        self.selection_description = QtWidgets.QLabel()
        self.selection_description.setObjectName("selectionDescription")
        self.selection_description.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        footer.addWidget(self.selection_description, 1)
        footer.addStretch(1)
        self.tool_count = QtWidgets.QLabel()
        self.tool_count.setObjectName("toolCount")
        footer.addWidget(self.tool_count)
        self.open_button = QtWidgets.QPushButton("Run")
        self.open_button.setObjectName("openWorkspaceButton")
        self.open_button.setAccessibleName("Open selected tool workspace")
        self.open_button.clicked.connect(self.open_selected)
        self.open_button.setMinimumWidth(86)
        footer.addWidget(self.open_button)
        outer.addWidget(footer_widget)

        self.setCentralWidget(central)
        QtWidgets.QWidget.setTabOrder(self.search, self.categories)
        QtWidgets.QWidget.setTabOrder(self.categories, self.tools)
        QtWidgets.QWidget.setTabOrder(self.tools, self.open_button)

    def _apply_launcher_style(self) -> None:
        tab_rules = (
            """
            QTabBar::tab {
                background: #1a1e22;
                color: #b8bec6;
                border: 1px solid #30363d;
                border-bottom: 0;
                padding: 7px 14px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                color: #f1f3f5;
                border-bottom: 2px solid #e8ad43;
            }
            """
            if self.variant.key in {"a", "b"}
            else """
            QTabBar::tab {
                background: transparent;
                color: #aeb4bc;
                border: 0;
                border-bottom: 1px solid #30363d;
                padding: 7px 14px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                color: #f1f3f5;
                border-bottom: 2px solid #e8ad43;
            }
            """
        )
        footer_rules = (
            """
            QFrame#launcherFooter {
                background: #181c20;
                border-top: 1px solid #30363d;
            }
            """
            if self.variant.bottom_dock
            else "QFrame#launcherFooter { border: 0; }"
        )
        self.setStyleSheet(
            f"""
            QMainWindow#pyplotUiLabLauncher, QWidget#launcherCentral {{
                background: #111417;
                color: #e5e7eb;
            }}
            QMenuBar {{
                background: #181c20;
                border-bottom: 1px solid #252a30;
                spacing: 3px;
            }}
            QMenuBar::item {{ padding: 4px 8px; }}
            QLineEdit#launcherSearch {{
                background: #111417;
                border: 1px solid #3a4149;
                color: #e5e7eb;
                padding: 4px 8px;
                selection-background-color: #6b5327;
            }}
            QListWidget#launcherTools {{
                background: #111417;
                border: 1px solid #30363d;
                outline: 0;
            }}
            QLabel#selectionDescription, QLabel#toolCount {{
                color: #979fa9;
            }}
            QPushButton#openWorkspaceButton {{
                background: #e8ad43;
                border: 1px solid #efb84e;
                color: #111417;
                font-weight: 600;
                padding: 4px 16px;
            }}
            QPushButton#openWorkspaceButton:hover {{ background: #f1b94b; }}
            QPushButton#openWorkspaceButton:pressed {{ background: #cf942b; }}
            QPushButton#openWorkspaceButton:disabled {{
                background: #2a2f35;
                border-color: #343a41;
                color: #737b84;
            }}
            {tab_rules}
            {footer_rules}
            """
        )

    def _populate_categories(self) -> None:
        preferred = ["Loggers", "Plotting", "Emulators", "Builders", "Experiments"]
        available = {tool.category for tool in self._tools}
        categories = [category for category in preferred if category in available]
        for category in categories:
            self.categories.addTab(category)
        plotting_index = categories.index("Plotting") if "Plotting" in categories else 0
        self.categories.setCurrentIndex(plotting_index)

    def _filter_tools(self, *_: object) -> None:
        selected_name = self._selected_tool_name()
        category_name = self.categories.tabText(self.categories.currentIndex())
        query = self.search.text().strip().casefold()
        self.tools.clear()
        for tool in self._tools:
            haystack = f"{tool.name} {tool.description} {tool.category}".casefold()
            if tool.category != category_name:
                continue
            if query and query not in haystack:
                continue
            item = QtWidgets.QListWidgetItem(tool.name)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, tool)
            self.tools.addItem(item)
            if tool.name == selected_name:
                self.tools.setCurrentItem(item)
        if self.tools.currentRow() < 0 and self.tools.count():
            self.tools.setCurrentRow(0)
        self._update_selection(self.tools.currentRow())

    def _selected_tool_name(self) -> str | None:
        item = self.tools.currentItem()
        return item.text() if item is not None else None

    def selected_tool(self) -> PrototypeTool | None:
        item = self.tools.currentItem()
        tool = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
        return tool if isinstance(tool, PrototypeTool) else None

    def _update_selection(self, _row: int) -> None:
        tool = self.selected_tool()
        enabled = tool is not None and tool.status != "Unavailable in prototype"
        self.open_button.setEnabled(enabled)
        self.tool_count.setText(f"{self.tools.count()} tools")
        if tool is None:
            self.selection_summary.setText("No matching tools")
            self.selection_description.clear()
            return
        self.selection_summary.setText(tool.name)
        self.selection_description.setText(tool.description)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self.variant.two_columns and hasattr(self, "tools"):
            self._update_two_column_grid()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self.variant.two_columns:
            QtCore.QTimer.singleShot(0, self._update_two_column_grid)

    def _update_two_column_grid(self) -> None:
        viewport_width = max(360, self.tools.viewport().width())
        self.tools.setGridSize(QtCore.QSize(max(180, viewport_width // 2), 26))

    def open_selected(self) -> PlotWorkspacePrototype | None:
        tool = self.selected_tool()
        if tool is None or not self.open_button.isEnabled():
            return None
        workspace = PlotWorkspacePrototype(tool_name=tool.name)
        workspace.destroyed.connect(
            lambda _obj=None, window=workspace: self._forget_workspace(window)
        )
        self._workspaces.append(workspace)
        workspace.show()
        self.workspace_opened.emit(workspace)
        return workspace

    def _forget_workspace(self, workspace: PlotWorkspacePrototype) -> None:
        try:
            self._workspaces.remove(workspace)
        except ValueError:
            pass


class PlotWorkspacePrototype(QtWidgets.QMainWindow):
    """Qt-native plotting workspace with movable half-width graph windows."""

    task_finished = QtCore.pyqtSignal()

    def __init__(self, *, tool_name: str = "Current Annealing") -> None:
        super().__init__()
        self.setObjectName("pyplotUiLabWorkspace")
        self.setWindowTitle(f"PyPlot UI Laboratory - {tool_name}")
        self.resize(1280, 800)
        self.setMinimumSize(900, 620)
        self._tool_name = tool_name
        self._graph_counter = 0
        self._initial_graph_placed = False
        self._task_timer = QtCore.QTimer(self)
        self._task_timer.timeout.connect(self._advance_task)

        self._build_workspace()
        self._build_actions()
        self._build_docks()
        self._build_menu_and_toolbar()
        self._build_task_status()
        self.add_graph()

    def _build_actions(self) -> None:
        self.import_action = QtGui.QAction(
            _standard_icon(self, QtWidgets.QStyle.StandardPixmap.SP_DialogOpenButton),
            "Import",
            self,
        )
        self.import_action.setShortcut(QtGui.QKeySequence.StandardKey.Open)
        self.import_action.setToolTip("Import synthetic files")
        self.import_action.triggered.connect(lambda: self.start_background_task())

        self.new_graph_action = QtGui.QAction(
            _standard_icon(self, QtWidgets.QStyle.StandardPixmap.SP_FileDialogNewFolder),
            "New graph",
            self,
        )
        self.new_graph_action.setShortcut(QtGui.QKeySequence("Ctrl+G"))
        self.new_graph_action.triggered.connect(self.add_graph)

        self.tile_action = QtGui.QAction("Tile", self)
        self.tile_action.triggered.connect(self.mdi.tileSubWindows)
        self.cascade_action = QtGui.QAction("Cascade", self)
        self.cascade_action.triggered.connect(self.mdi.cascadeSubWindows)
        self.fullscreen_action = QtGui.QAction("Fullscreen graph", self)
        self.fullscreen_action.setShortcut(QtGui.QKeySequence("F11"))
        self.fullscreen_action.triggered.connect(self.toggle_active_graph_maximized)
        self.export_action = QtGui.QAction(
            _standard_icon(self, QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton),
            "Export",
            self,
        )
        self.export_action.setShortcut(QtGui.QKeySequence.StandardKey.Save)

    def _build_menu_and_toolbar(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.import_action)
        file_menu.addAction(self.export_action)
        file_menu.addSeparator()
        close_action = file_menu.addAction("Close")
        close_action.setShortcut(QtGui.QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close)
        graph_menu = self.menuBar().addMenu("&Graph")
        graph_menu.addAction(self.new_graph_action)
        graph_menu.addAction(self.fullscreen_action)
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.project_dock.toggleViewAction())
        view_menu.addAction(self.properties_dock.toggleViewAction())
        window_menu = self.menuBar().addMenu("&Window")
        window_menu.addAction(self.tile_action)
        window_menu.addAction(self.cascade_action)

        toolbar = self.addToolBar("Plot commands")
        toolbar.setObjectName("plotCommands")
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addAction(self.import_action)
        toolbar.addSeparator()
        toolbar.addAction(self.new_graph_action)
        toolbar.addAction(self.tile_action)
        toolbar.addAction(self.cascade_action)
        toolbar.addAction(self.fullscreen_action)
        toolbar.addSeparator()
        toolbar.addAction(self.export_action)

    def _build_workspace(self) -> None:
        self.mdi = QtWidgets.QMdiArea(self)
        self.mdi.setObjectName("graphWorkspace")
        self.mdi.setAccessibleName("Movable graph workspace")
        self.mdi.setViewMode(QtWidgets.QMdiArea.ViewMode.SubWindowView)
        self.mdi.setOption(
            QtWidgets.QMdiArea.AreaOption.DontMaximizeSubWindowOnActivation, True
        )
        self.mdi.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.mdi.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.setCentralWidget(self.mdi)

    def _build_docks(self) -> None:
        self.project_dock = QtWidgets.QDockWidget("Project and data", self)
        self.project_dock.setObjectName("prototypeProjectDock")
        self.project_dock.setAllowedAreas(
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            | QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        )
        tree = QtWidgets.QTreeWidget()
        tree.setAccessibleName("Synthetic project data")
        tree.setHeaderLabels(["Synthetic project"])
        annealing = QtWidgets.QTreeWidgetItem(tree, ["Annealing"])
        QtWidgets.QTreeWidgetItem(annealing, ["650 C / 20 min"])
        selected = QtWidgets.QTreeWidgetItem(annealing, ["700 C / 10 min"])
        QtWidgets.QTreeWidgetItem(tree, ["VSM loops"])
        QtWidgets.QTreeWidgetItem(tree, ["TMA"])
        tree.expandAll()
        tree.setCurrentItem(selected)
        self.project_dock.setWidget(tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)

        self.properties_dock = QtWidgets.QDockWidget("Graph properties", self)
        self.properties_dock.setObjectName("prototypePropertiesDock")
        properties = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(properties)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        x_axis = QtWidgets.QComboBox()
        x_axis.addItems(["Current (mA)", "Temperature (C)", "Time (s)"])
        y_axis = QtWidgets.QComboBox()
        y_axis.addItems(["Resistance (ohm)", "Temperature (C)"])
        line_width = QtWidgets.QDoubleSpinBox()
        line_width.setRange(0.5, 8.0)
        line_width.setValue(1.5)
        line_width.setSuffix(" pt")
        legend = QtWidgets.QCheckBox("Show legend")
        legend.setChecked(True)
        form.addRow("X axis", x_axis)
        form.addRow("Y axis", y_axis)
        form.addRow("Line width", line_width)
        form.addRow("", legend)
        apply_button = QtWidgets.QPushButton("Apply to active graph")
        apply_button.setAccessibleName("Apply properties to active graph")
        form.addRow(apply_button)
        self.properties_dock.setWidget(properties)
        self.addDockWidget(
            QtCore.Qt.DockWidgetArea.RightDockWidgetArea, self.properties_dock
        )
        self.resizeDocks(
            [self.project_dock, self.properties_dock],
            [210, 250],
            QtCore.Qt.Orientation.Horizontal,
        )

    def _build_task_status(self) -> None:
        self.task_label = QtWidgets.QLabel("Ready")
        self.task_label.setAccessibleName("Background task status")
        self.task_label.setProperty("status", "ready")
        self.task_progress = QtWidgets.QProgressBar()
        self.task_progress.setAccessibleName("Background task progress")
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(0)
        self.task_progress.setFixedWidth(210)
        self.task_progress.hide()
        self.cancel_task_button = QtWidgets.QToolButton()
        self.cancel_task_button.setText("Cancel")
        self.cancel_task_button.setToolTip("Cancel background task")
        self.cancel_task_button.clicked.connect(self.cancel_background_task)
        self.cancel_task_button.hide()
        self.statusBar().addPermanentWidget(self.task_label)
        self.statusBar().addPermanentWidget(self.task_progress)
        self.statusBar().addPermanentWidget(self.cancel_task_button)

    def add_graph(self) -> QtWidgets.QMdiSubWindow:
        self._graph_counter += 1
        figure = Figure(figsize=(7.2, 5.0), constrained_layout=True)
        canvas = FigureCanvas(figure)
        canvas.setAccessibleName(f"Synthetic annealing graph {self._graph_counter}")
        axes = figure.subplots()
        current = np.linspace(2.0, 82.0, 120)
        base = 205.0 + 2.15 * current
        transition = 58.0 / (1.0 + np.exp(-(current - 55.5) / 0.9))
        ripple = 2.3 * np.sin(current / 4.2 + self._graph_counter * 0.25)
        resistance = base + transition + ripple
        axes.plot(
            current,
            resistance,
            color="#d62728",
            marker="o",
            markersize=3.2,
            linewidth=1.2,
            label="Increasing",
        )
        axes.plot(
            current[-18:],
            resistance[-18:] - 11.0,
            color="#2468d8",
            marker="o",
            markersize=3.2,
            linewidth=1.2,
            label="Decreasing",
        )
        axes.set_title(f"Synthetic current annealing {self._graph_counter}")
        axes.set_xlabel("Current (mA)")
        axes.set_ylabel("Resistance (ohm)")
        axes.grid(True, color="#d9dde0", linewidth=0.6)
        axes.legend(loc="upper left")

        subwindow = self.mdi.addSubWindow(canvas)
        subwindow.setObjectName(f"graphSubWindow{self._graph_counter}")
        subwindow.setWindowTitle(f"Current Annealing {self._graph_counter}")
        subwindow.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        subwindow.show()
        self._place_graph_half_width(subwindow)
        self.mdi.setActiveSubWindow(subwindow)
        return subwindow

    def _place_graph_half_width(self, subwindow: QtWidgets.QMdiSubWindow) -> None:
        viewport = self.mdi.viewport().size()
        width = max(360, int(viewport.width() * 0.52))
        height = max(420, int(viewport.height() * 0.78))
        width = min(width, max(320, viewport.width() - 36))
        height = min(height, max(280, viewport.height() - 36))
        offset = 22 * max(0, len(self.mdi.subWindowList()) - 1)
        max_x = max(8, viewport.width() - width - 8)
        max_y = max(8, viewport.height() - height - 8)
        subwindow.setGeometry(
            min(18 + offset, max_x),
            min(18 + offset, max_y),
            width,
            height,
        )

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._initial_graph_placed:
            return
        self._initial_graph_placed = True
        if self.width() < 1100:
            self.properties_dock.hide()

        def _place_after_layout() -> None:
            active = self.mdi.activeSubWindow()
            if active is not None and not active.isMaximized():
                self._place_graph_half_width(active)

        QtCore.QTimer.singleShot(0, _place_after_layout)

    def toggle_active_graph_maximized(self) -> None:
        active = self.mdi.activeSubWindow()
        if active is None:
            return
        if active.isMaximized():
            active.showNormal()
        else:
            active.showMaximized()

    def start_background_task(self, *, interval_ms: int = 35) -> None:
        self.task_progress.setValue(0)
        self.task_progress.show()
        self.cancel_task_button.show()
        self.task_label.setText("Indexing synthetic files")
        self.task_label.setProperty("status", "warning")
        self._refresh_status_label()
        self._task_timer.start(max(1, interval_ms))

    def _advance_task(self) -> None:
        value = min(100, self.task_progress.value() + 5)
        self.task_progress.setValue(value)
        if value >= 100:
            self._task_timer.stop()
            self.task_label.setText("Ready")
            self.task_label.setProperty("status", "ready")
            self._refresh_status_label()
            self.cancel_task_button.hide()
            self.task_finished.emit()

    def cancel_background_task(self) -> None:
        self._task_timer.stop()
        self.task_progress.hide()
        self.cancel_task_button.hide()
        self.task_label.setText("Task cancelled")
        self.task_label.setProperty("status", "warning")
        self._refresh_status_label()

    def _refresh_status_label(self) -> None:
        self.task_label.style().unpolish(self.task_label)
        self.task_label.style().polish(self.task_label)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self._task_timer.stop()
        for subwindow in self.mdi.subWindowList():
            canvas = subwindow.widget()
            if isinstance(canvas, FigureCanvas):
                # Matplotlib schedules deferred redraws during the final resize.
                # Clear the pending flag before Qt destroys the canvas.
                canvas._draw_pending = False  # noqa: SLF001 - backend lifecycle guard
        super().closeEvent(event)


def render_launcher_variants(
    app: QtWidgets.QApplication,
    output_dir: Path,
) -> list[Path]:
    """Render fixed-size launcher screenshots using only synthetic records."""

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for key, variant in LAUNCHER_VARIANTS.items():
        launcher = LauncherPrototype(variant=key)
        launcher.resize(variant.size)
        launcher.show()
        app.processEvents()
        launcher.tools.setFocus()
        app.processEvents()
        path = output_dir / f"launcher-option-{key}.png"
        if not launcher.grab().save(str(path), "PNG"):
            raise RuntimeError(f"Could not save launcher screenshot: {path}")
        paths.append(path)
        launcher.close()
        launcher.deleteLater()
        app.processEvents()
    return paths


def _parse_main_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the isolated PyPlot UI laboratory")
    parser.add_argument(
        "--launcher-variant",
        choices=tuple(LAUNCHER_VARIANTS),
        default="c",
    )
    parser.add_argument(
        "--render-launchers",
        action="store_true",
        help="Render all launcher variants and exit",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts") / "pyplot-ui-lab" / "pr303-launchers",
    )
    return parser.parse_args(list(argv)[1:])


def main(argv: Sequence[str] | None = None) -> int:
    """Run or render the standalone UI laboratory."""

    args = list(sys.argv if argv is None else argv)
    parsed = _parse_main_args(args)
    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication(args)
    apply_prototype_theme(app)
    if parsed.render_launchers:
        render_launcher_variants(app, parsed.output_dir)
        return 0
    launcher = LauncherPrototype(variant=parsed.launcher_variant)
    launcher.show()
    if owns_app:
        return app.exec()
    return 0


__all__ = [
    "LauncherPrototype",
    "LAUNCHER_VARIANTS",
    "PROTOTYPE_TOOLS",
    "PlotWorkspacePrototype",
    "PrototypeTool",
    "apply_prototype_theme",
    "main",
    "render_launcher_variants",
]


if __name__ == "__main__":
    raise SystemExit(main())
