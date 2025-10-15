from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.utils import install_standard_menu


@dataclass
class PlotTabState:
    """Track Matplotlib artefacts for a rendered plot tab."""

    axes: Any
    canvas: FigureCanvas
    lines: Dict[float, Any]


@dataclass
class GraphLineState:
    """Describe a plotted line within the embedded Matplotlib canvas."""

    key: tuple[str, float | str]
    label: str
    line: Any
    base_x: Any
    base_y: Any
    normalized: bool = False


@dataclass
class TabDescriptor:
    """Capture metadata for a tabbed plot and its project-manager bindings."""

    kind: str
    title: str
    root_label: str
    x_label: str
    y_label: str
    canvas: FigureCanvas
    axes: Any
    lines: Dict[tuple[str, float | str], GraphLineState]
    metadata: Dict[str, Any]
    layout_initialized: bool = False
    stored_limits: Dict[str, tuple[float, float]] = field(default_factory=dict)


class GraphSelectionDialog(QtWidgets.QDialog):
    """Offer choices for which plotted data series should be processed."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        *,
        entries: Sequence[tuple[str, str, QtWidgets.QWidget]],
        title: str,
        prompt: str,
        current: QtWidgets.QWidget | None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._entries = list(entries)
        self._current = current if any(tab is current for _, _, tab in entries) else None
        self._selected: List[QtWidgets.QWidget] = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        label = QtWidgets.QLabel(prompt)
        label.setWordWrap(True)
        layout.addWidget(label)

        self.current_radio = QtWidgets.QRadioButton("Current tab only")
        self.all_radio = QtWidgets.QRadioButton("All plotted data")
        self.custom_radio = QtWidgets.QRadioButton("Choose specific items")

        radio_column = QtWidgets.QVBoxLayout()
        radio_column.setSpacing(4)
        radio_column.addWidget(self.current_radio)
        radio_column.addWidget(self.all_radio)
        radio_column.addWidget(self.custom_radio)
        layout.addLayout(radio_column)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        for label_text, detail, tab in self._entries:
            item = QtWidgets.QListWidgetItem(label_text or "Graph")
            if detail and detail != label_text:
                item.setToolTip(detail)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, tab)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, 1)

        self.custom_radio.toggled.connect(self._toggle_custom_list)
        self._toggle_custom_list(False)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        if self._current is not None:
            self.current_radio.setChecked(True)
        else:
            self.current_radio.setEnabled(False)
            self.all_radio.setChecked(True)

        if len(self._entries) <= 1:
            self.custom_radio.setEnabled(False)

    def _toggle_custom_list(self, enabled: bool) -> None:
        self.list_widget.setEnabled(enabled)

    def selected_tabs(self) -> List[QtWidgets.QWidget]:
        return list(self._selected)

    def accept(self) -> None:  # type: ignore[override]
        if self.custom_radio.isChecked():
            chosen: List[QtWidgets.QWidget] = []
            for index in range(self.list_widget.count()):
                item = self.list_widget.item(index)
                if item is None or item.checkState() != QtCore.Qt.CheckState.Checked:
                    continue
                tab = item.data(QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(tab, QtWidgets.QWidget):
                    chosen.append(tab)
            if not chosen:
                QtWidgets.QMessageBox.information(
                    self,
                    "Select Data",
                    "Select at least one plotted item.",
                )
                return
            self._selected = chosen
        elif self.current_radio.isChecked():
            if self._current is None:
                QtWidgets.QMessageBox.information(
                    self,
                    "Select Data",
                    "No plotted tab is currently active.",
                )
                return
            self._selected = [self._current]
        else:
            self._selected = [tab for _, _, tab in self._entries]
            if not self._selected:
                QtWidgets.QMessageBox.information(
                    self,
                    "Select Data",
                    "No plotted items are available.",
                )
                return
        super().accept()


class BasePlotWindow(QtWidgets.QMainWindow, ABC):
    """Shared UI frame used by plotting tools."""

    help_topic: str = "plotter"

    def __init__(self, *, title: str) -> None:
        super().__init__()
        self.setWindowTitle(title)

        # Tab/graph bookkeeping shared by subclasses.
        self._tab_descriptors: Dict[QtWidgets.QWidget, TabDescriptor] = {}
        self._canvas_by_tab: Dict[QtWidgets.QWidget, FigureCanvas] = {}
        self._axes_by_tab: Dict[QtWidgets.QWidget, Any] = {}
        self._plot_tabs: Dict[float, PlotTabState] = {}
        self._object_items: Dict[
            tuple[QtWidgets.QWidget, tuple[str, float | str]],
            QtWidgets.QTreeWidgetItem,
        ] = {}
        self._temperature_tab_widgets: List[QtWidgets.QWidget] = []
        self._metrics_angle_tabs: List[QtWidgets.QWidget] = []
        self._metrics_temperature_tabs: List[QtWidgets.QWidget] = []
        self._overlay_tab_widgets: List[QtWidgets.QWidget] = []
        self._graph_tree_root: QtWidgets.QTreeWidgetItem | None = None
        self._worksheet_tree_root: QtWidgets.QTreeWidgetItem | None = None
        self._graph_tree_items: Dict[QtWidgets.QWidget, QtWidgets.QTreeWidgetItem] = {}
        self._worksheet_tree_items: Dict[Path, QtWidgets.QTreeWidgetItem] = {}
        self._worksheet_tabs_open: Dict[Path, QtWidgets.QWidget] = {}
        self._tab_to_worksheet_path: Dict[QtWidgets.QWidget, Path] = {}
        self._hidden_tabs: set[QtWidgets.QWidget] = set()

        self._log_has_unread_errors = False

        self._build_base_ui()

    # ------------------------------------------------------------------ abstract hooks
    @abstractmethod
    def _handle_manual_path_entry(self) -> None:
        """Validate manually entered sources."""

    @abstractmethod
    def _choose_files(self) -> None:
        """Prompt for one or more files."""

    @abstractmethod
    def _choose_folder(self) -> None:
        """Prompt for a folder."""

    @abstractmethod
    def _generate_plots(self) -> None:
        """Generate plot data from currently loaded sources."""

    @abstractmethod
    def _open_matplotlib_window(self) -> None:
        """Pop out the current tab into a Matplotlib window."""

    @abstractmethod
    def _save_current_graph(self) -> None:
        """Export the current graph to an image file."""

    @abstractmethod
    def _normalize_current_graph(self) -> None:
        """Toggle normalization for the active plot."""

    @abstractmethod
    def _export_txt(self) -> None:
        """Export plotted data to ASCII."""

    @abstractmethod
    def _open_origin_prompt(self) -> None:
        """Trigger Origin output workflow."""

    @abstractmethod
    def _populate_graph_settings(self, layout: QtWidgets.QVBoxLayout) -> None:
        """Allow subclasses to augment the graph-settings panel."""

    # ------------------------------------------------------------------ base UI
    def _build_base_ui(self) -> None:
        central = QtWidgets.QWidget()
        central_layout = QtWidgets.QVBoxLayout(central)
        central_layout.setContentsMargins(12, 12, 12, 12)
        central_layout.setSpacing(10)

        controls = QtWidgets.QWidget()
        controls_layout = QtWidgets.QGridLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setHorizontalSpacing(8)
        controls_layout.setVerticalSpacing(6)

        controls_layout.addWidget(QtWidgets.QLabel("Data sources"), 0, 0)
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("Select files or folders…")
        self.path_edit.editingFinished.connect(self._handle_manual_path_entry)
        controls_layout.addWidget(self.path_edit, 0, 1, 1, 3)

        self.browse_files_button = QtWidgets.QPushButton("Browse files…")
        self.browse_files_button.clicked.connect(self._choose_files)
        controls_layout.addWidget(self.browse_files_button, 0, 4)

        self.browse_folder_button = QtWidgets.QPushButton("Browse folder…")
        self.browse_folder_button.clicked.connect(self._choose_folder)
        controls_layout.addWidget(self.browse_folder_button, 0, 5)

        controls_layout.setColumnStretch(1, 1)
        controls_layout.setColumnStretch(2, 1)
        central_layout.addWidget(controls)

        action_row = QtWidgets.QHBoxLayout()
        self.plot_button = QtWidgets.QPushButton("Generate plots")
        self.plot_button.clicked.connect(self._generate_plots)
        self.plot_button.setEnabled(False)
        action_row.addWidget(self.plot_button)

        self.popout_button = QtWidgets.QPushButton("Open in Matplotlib")
        self.popout_button.clicked.connect(self._open_matplotlib_window)
        self.popout_button.setEnabled(False)
        action_row.addWidget(self.popout_button)

        self.save_graph_button = QtWidgets.QPushButton("Save graph…")
        self.save_graph_button.setEnabled(False)
        self.save_graph_button.clicked.connect(self._save_current_graph)
        action_row.addWidget(self.save_graph_button)

        self.normalize_button = QtWidgets.QPushButton("Normalize Y")
        self.normalize_button.setEnabled(False)
        self.normalize_button.clicked.connect(self._normalize_current_graph)
        action_row.addWidget(self.normalize_button)

        self.export_button = QtWidgets.QPushButton("Export TXT…")
        self.export_button.clicked.connect(self._export_txt)
        self.export_button.setEnabled(False)
        action_row.addWidget(self.export_button)

        self.open_origin_button = QtWidgets.QPushButton("Open in Origin…")
        self.open_origin_button.clicked.connect(self._open_origin_prompt)
        self.open_origin_button.setEnabled(False)
        action_row.addWidget(self.open_origin_button)

        action_row.addStretch(1)
        central_layout.addLayout(action_row)

        self.tab_widget = QtWidgets.QTabWidget()
        self.tab_widget.currentChanged.connect(self._handle_current_tab_changed)
        central_layout.addWidget(self.tab_widget, 1)

        self.setCentralWidget(central)

        self.project_tree = QtWidgets.QTreeWidget()
        self.project_tree.setHeaderLabels(["Project Explorer", "Details"])
        self.project_tree.header().setStretchLastSection(True)
        self.project_tree.itemDoubleClicked.connect(self._handle_project_item_double_click)
        project_dock = self._create_dock_widget("Project Explorer", "projectExplorerDock")
        project_dock.setWidget(self.project_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, project_dock)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        log_dock = self._create_dock_widget("Message Log", "messageLogDock")
        log_dock.setWidget(self.log_view)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, log_dock)
        self.tabifyDockWidget(project_dock, log_dock)
        project_dock.raise_()

        self.object_tree = QtWidgets.QTreeWidget()
        self.object_tree.setHeaderLabels(["Object Manager"])
        self.object_tree.setColumnCount(1)
        self.object_tree.itemChanged.connect(self._handle_object_item_changed)
        object_dock = self._create_dock_widget("Object Manager", "objectManagerDock")
        object_dock.setWidget(self.object_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, object_dock)

        graph_settings_widget = QtWidgets.QWidget()
        graph_layout = QtWidgets.QVBoxLayout(graph_settings_widget)
        graph_layout.setContentsMargins(8, 8, 8, 8)
        graph_layout.setSpacing(12)
        self._populate_graph_settings(graph_layout)
        graph_layout.addStretch(1)

        graph_dock = self._create_dock_widget("Graph Settings", "graphSettingsDock")
        graph_dock.setWidget(graph_settings_widget)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, graph_dock)

        menu_bar = install_standard_menu(
            self,
            help_topic=self.help_topic,
            console=self.log_view,
            open_file=self._open_files_from_menu,
            open_folder=self._open_folder_from_menu,
            close_window=self.close,
        )
        self._extend_menus(menu_bar)
        self._after_base_ui_created(project_dock=project_dock, log_dock=log_dock, graph_dock=graph_dock)

    def _extend_menus(self, menu_bar: QtWidgets.QMenuBar) -> None:
        """Allow subclasses to customise the main menu."""
        _ = menu_bar  # appease linters until subclasses override

    def _after_base_ui_created(
        self,
        *,
        project_dock: QtWidgets.QDockWidget,
        log_dock: QtWidgets.QDockWidget,
        graph_dock: QtWidgets.QDockWidget,
    ) -> None:
        """Hook invoked once base dock widgets have been created."""
        _ = (project_dock, log_dock, graph_dock)

    def _create_dock_widget(self, title: str, object_name: str) -> QtWidgets.QDockWidget:
        dock = QtWidgets.QDockWidget(title, self)
        dock.setObjectName(object_name)
        return dock

    # ------------------------------------------------------------------ menu helpers
    def _open_files_from_menu(self) -> None:
        self._choose_files()

    def _open_folder_from_menu(self) -> None:
        self._choose_folder()

    # ------------------------------------------------------------------ project tree helpers
    def _handle_project_item_double_click(
        self,
        item: QtWidgets.QTreeWidgetItem,
        column: int,
    ) -> None:
        if column != 0:
            return
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not data:
            return
        role = data[0]
        if role == "graph":
            tab = data[1]
            if isinstance(tab, QtWidgets.QWidget):
                self._show_tab(tab)
        elif role == "worksheet":
            path = data[1]
            if isinstance(path, Path):
                self._open_worksheet_tab(path)
        elif role == "worksheet_group":
            item.setExpanded(not item.isExpanded())

    # Placeholder methods that subclasses may override or extend -----------------
    def _handle_object_item_changed(self, *_: Any) -> None:
        """Subclasses should override to toggle line visibility."""

    def _rebuild_object_manager_for_tab(self, *_: Any) -> None:
        """Rebuild the object manager tree for ``tab``."""

    def _open_worksheet_tab(self, path: Path) -> None:
        """Open or focus the worksheet that originated from ``path``."""
        widget = self._worksheet_tabs_open.get(path)
        if widget is not None:
            self._show_tab(widget)

    def _show_tab(self, tab: QtWidgets.QWidget) -> None:
        index = self.tab_widget.indexOf(tab)
        if index < 0:
            return
        self._set_tab_visibility(tab, True)
        self.tab_widget.setCurrentIndex(index)
        self._update_tab_buttons()

    def _is_tab_visible(self, tab: QtWidgets.QWidget) -> bool:
        index = self.tab_widget.indexOf(tab)
        if index < 0:
            return False
        try:
            return self.tab_widget.isTabVisible(index)
        except Exception:
            return tab.isVisible()

    def _set_tab_visibility(self, tab: QtWidgets.QWidget, visible: bool) -> None:
        index = self.tab_widget.indexOf(tab)
        if index < 0:
            return
        try:
            self.tab_widget.setTabVisible(index, visible)
        except Exception:
            tab.setVisible(visible)
        if visible:
            self._hidden_tabs.discard(tab)
        else:
            self._hidden_tabs.add(tab)

    def _update_tab_buttons(self) -> None:
        tab_bar = self.tab_widget.tabBar()
        if tab_bar is None:
            return
        for index in range(self.tab_widget.count()):
            button = tab_bar.tabButton(index, QtWidgets.QTabBar.ButtonPosition.RightSide)
            if button is not None and bool(button.property("mw_tab_controls")):
                tab_bar.setTabButton(index, QtWidgets.QTabBar.ButtonPosition.RightSide, None)

        current_index = self.tab_widget.currentIndex()
        if current_index < 0:
            return
        tab = self.tab_widget.widget(current_index)
        if tab is None:
            return

        container = QtWidgets.QWidget()
        container.setProperty("mw_tab_controls", True)
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        minimize_button = QtWidgets.QToolButton(container)
        minimize_button.setText("-")
        minimize_button.setAutoRaise(True)
        minimize_button.setToolTip("Hide this tab")
        minimize_button.clicked.connect(lambda _, t=tab: self._minimize_tab(t))
        layout.addWidget(minimize_button)

        if tab in self._tab_descriptors:
            close_button = QtWidgets.QToolButton(container)
            close_button.setText("x")
            close_button.setAutoRaise(True)
            close_button.setToolTip("Close this graph tab")
            close_button.clicked.connect(lambda _, t=tab: self._close_tab(t))
            layout.addWidget(close_button)

        tab_bar.setTabButton(
            current_index,
            QtWidgets.QTabBar.ButtonPosition.RightSide,
            container,
        )

    def _minimize_tab(self, tab: QtWidgets.QWidget) -> None:
        index = self.tab_widget.indexOf(tab)
        if index < 0:
            return
        next_index = None
        if self.tab_widget.currentWidget() is tab:
            next_index = self._find_alternate_tab_index(index)
        self._set_tab_visibility(tab, False)
        if next_index is not None:
            self.tab_widget.setCurrentIndex(next_index)
        self._update_tab_buttons()

    def _find_alternate_tab_index(self, current_index: int) -> int | None:
        count = self.tab_widget.count()
        for offset in range(1, count):
            forward = (current_index + offset) % count
            if self._is_tab_visible(self.tab_widget.widget(forward)):
                return forward
        return None

    def _close_tab(self, tab: QtWidgets.QWidget) -> None:
        index = self.tab_widget.indexOf(tab)
        if index >= 0:
            self.tab_widget.removeTab(index)
        self._hidden_tabs.discard(tab)
        self._tab_descriptors.pop(tab, None)
        self._canvas_by_tab.pop(tab, None)
        self._axes_by_tab.pop(tab, None)
        descriptor_item = self._graph_tree_items.pop(tab, None)
        if descriptor_item is not None:
            parent = descriptor_item.parent()
            if parent is not None:
                parent.removeChild(descriptor_item)
            else:
                top_index = self.project_tree.indexOfTopLevelItem(descriptor_item)
                if top_index >= 0:
                    self.project_tree.takeTopLevelItem(top_index)
        path = self._tab_to_worksheet_path.pop(tab, None)
        if path is not None:
            self._worksheet_tabs_open.pop(path, None)
        self._update_tab_buttons()

    # ------------------------------------------------------------------ state helpers
    def _handle_current_tab_changed(self, index: int) -> None:
        tab = self.tab_widget.widget(index) if index >= 0 else None
        self._update_tab_buttons()
        self._focus_tree_on_tab(tab)
        self._rebuild_object_manager_for_tab(tab)

    def _focus_tree_on_tab(self, tab: QtWidgets.QWidget | None) -> None:
        self.project_tree.blockSignals(True)
        self.project_tree.clearSelection()
        target_item: QtWidgets.QTreeWidgetItem | None = None
        if tab is not None:
            if tab in self._tab_descriptors:
                target_item = self._graph_tree_items.get(tab)
            else:
                path = self._tab_to_worksheet_path.get(tab)
                if path is not None:
                    target_item = self._worksheet_tree_items.get(path)
        if target_item is not None:
            self.project_tree.setCurrentItem(target_item)
            self.project_tree.scrollToItem(target_item)
        self.project_tree.blockSignals(False)


__all__ = [
    "BasePlotWindow",
    "GraphLineState",
    "GraphSelectionDialog",
    "PlotTabState",
    "TabDescriptor",
]
