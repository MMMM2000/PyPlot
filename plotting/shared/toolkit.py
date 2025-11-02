"""Shared toolkit functions for PyPlot."""

from __future__ import annotations

import os
import sys
import weakref
import atexit
from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from PyQt6 import QtWidgets, QtGui, QtCore

from app_help import show_help
from .readability import (
    ReadabilityControls,
    apply_readability,
    apply_readability_fonts,
    create_readability_group,
    get_readability,
    set_readability,
    sync_readability,
)
from .settings import get_settings
from .paths import (
    prepare_output_dir,
    get_last_output_dir,
    set_last_output_dir,
    download_dir,
    sample_dir,
)
from .origin import (
    origin_session,
    release_origin,
    schedule_origin_release,
)


_SUBSCRIPT_MAP = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


atexit.register(release_origin)


def format_annealing_title(base: str) -> str:
    """Return ``base`` with composition digits subscripted and microwire
    identifiers using a slash instead of an underscore."""

    parts = base.split()
    if parts:
        parts[0] = parts[0].translate(_SUBSCRIPT_MAP)
    if len(parts) > 1:
        parts[1] = parts[1].replace("_", "/")
    return " ".join(parts)


def save_figure(fig: Figure, base_path: str | Path, fmt: str = "png", dpi: int = 1200) -> None:
    """Save ``fig`` to ``base_path`` with format ``fmt``.

    ``base_path`` should omit the file extension. ``dpi`` is only applied when
    saving PNG files to allow high-resolution outputs.
    """

    path = f"{base_path}.{fmt}"
    if fmt.lower() == "png":
        fig.savefig(path, dpi=dpi, format=fmt)
    else:
        fig.savefig(path, format=fmt)


def show_plots() -> None:
    """Display Matplotlib figures without starting a new Qt event loop."""

    if QtWidgets.QApplication.instance() is not None:
        plt.show(block=False)
        QtWidgets.QApplication.processEvents()
    else:
        plt.show()


def _dark_palette(accent: QtGui.QColor) -> QtGui.QPalette:
    """Return a dark palette using ``accent`` for highlighted items."""

    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(32, 32, 32))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(220, 220, 220))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(24, 24, 24))
    palette.setColor(
        QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(32, 32, 32)
    )
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor(240, 240, 240))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor(0, 0, 0))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(220, 220, 220))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(32, 32, 32))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(220, 220, 220))
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor(255, 0, 0))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, accent)
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(0, 0, 0))
    return palette


def _apply_color_scheme(
    app: QtWidgets.QApplication, scheme: QtCore.Qt.ColorScheme | None = None
) -> None:
    """Apply a palette matching ``scheme``.

    When ``scheme`` is ``None`` the current system color scheme is queried via
    :meth:`QGuiApplication.styleHints`.
    """

    hints = app.styleHints()
    if scheme is None:
        scheme = (
            hints.colorScheme()
            if hints is not None and hasattr(hints, "colorScheme")
            else QtCore.Qt.ColorScheme.Light
        )

    style = app.style()
    standard_palette = (
        style.standardPalette() if style is not None else QtGui.QPalette()
    )
    accent = standard_palette.color(QtGui.QPalette.ColorRole.Highlight)

    if sys.platform.startswith("win"):
        if scheme == QtCore.Qt.ColorScheme.Dark:
            app.setPalette(_dark_palette(accent))
        else:
            app.setPalette(standard_palette)
    elif sys.platform == "darwin":
        app.setPalette(QtGui.QPalette())
    else:
        if scheme == QtCore.Qt.ColorScheme.Dark:
            app.setPalette(_dark_palette(accent))
        else:
            app.setPalette(standard_palette)


def apply_system_theme(app: QtWidgets.QApplication) -> None:
    """Apply a palette and style that follow the host operating system.

    Windows uses the native ``windowsvista`` style with colors tuned to match
    Fluent Design, including the current system accent color for highlights.
    macOS applies the ``macos``/``macintosh`` style and relies on the operating
    system to provide an appropriate palette for light or dark mode.  Other
    platforms fall back to the cross‑platform ``Fusion`` style.  The current
    color scheme is inspected to decide whether a dark or light palette should
    be applied and updates automatically when the system appearance changes.
    """

    hints = app.styleHints()
    if hints is not None and hasattr(hints, "colorScheme"):
        scheme = hints.colorScheme()
    else:
        scheme = QtCore.Qt.ColorScheme.Light

    if sys.platform.startswith("win"):
        style = "windowsvista" if scheme == QtCore.Qt.ColorScheme.Light else "Fusion"
        app.setStyle(style)
    elif sys.platform == "darwin":
        # Prefer the modern 'macos' style when available. If not present,
        # avoid forcing the deprecated 'macintosh' style to suppress Qt's
        # deprecation warning and let Qt choose the native default.
        if "macos" in QtWidgets.QStyleFactory.keys():
            app.setStyle("macos")
        else:
            # Leave default style in place (typically macOS native)
            pass
    else:
        app.setStyle("Fusion")

    _apply_color_scheme(app, scheme)

    hints = app.styleHints()
    if hints is not None and hasattr(hints, "colorSchemeChanged"):
        def update_scheme(new_scheme: QtCore.Qt.ColorScheme) -> None:
            if sys.platform.startswith("win"):
                style = "windowsvista" if new_scheme == QtCore.Qt.ColorScheme.Light else "Fusion"
                app.setStyle(style)
            _apply_color_scheme(app, new_scheme)

        hints.colorSchemeChanged.connect(update_scheme)


def apply_theme(app: QtWidgets.QApplication, mode: str = "system") -> None:
    """Apply a specific theme: 'system', 'light', or 'dark'.

    This mirrors apply_system_theme but allows forcing a color scheme.
    """
    m = (mode or "system").lower()
    if m == "system":
        apply_system_theme(app)
        return
    scheme = QtCore.Qt.ColorScheme.Dark if m == "dark" else QtCore.Qt.ColorScheme.Light

    if sys.platform.startswith("win"):
        style = "windowsvista" if scheme == QtCore.Qt.ColorScheme.Light else "Fusion"
        app.setStyle(style)
    elif sys.platform == "darwin":
        if "macos" in QtWidgets.QStyleFactory.keys():
            app.setStyle("macos")
    else:
        app.setStyle("Fusion")
    _apply_color_scheme(app, scheme)


def apply_dark_theme(app: QtWidgets.QApplication) -> None:
    """Backward compatible wrapper around :func:`apply_system_theme`."""
    apply_system_theme(app)


def _settings() -> QtCore.QSettings:
    return get_settings()


_BACKEND_CHOICES: tuple[str, ...] = ("matplotlib", "origin", "both")


def restore_backend_choice(
    key: str, combo: QtWidgets.QComboBox, default: str = "matplotlib"
) -> str:
    """Set ``combo`` to the last backend stored for ``key``.

    Returns the normalised backend string that was applied.
    """

    stored = str(_settings().value(f"{key}_backend", default, type=str) or default).lower()
    if stored not in _BACKEND_CHOICES:
        fallback = str(default or _BACKEND_CHOICES[0]).lower()
        stored = fallback if fallback in _BACKEND_CHOICES else _BACKEND_CHOICES[0]
    combo.setCurrentIndex(_BACKEND_CHOICES.index(stored))
    return stored


def store_backend_choice(key: str, backend: str) -> str:
    """Persist ``backend`` for ``key`` and return the normalised value."""

    normalised = str(backend or "").lower()
    if normalised not in _BACKEND_CHOICES:
        normalised = _BACKEND_CHOICES[0]
    _settings().setValue(f"{key}_backend", normalised)
    return normalised


def selected_backend(combo: QtWidgets.QComboBox) -> str:
    """Return the backend represented by ``combo``'s current index."""

    idx = combo.currentIndex()
    if 0 <= idx < len(_BACKEND_CHOICES):
        return _BACKEND_CHOICES[idx]
    return _BACKEND_CHOICES[0]


def _combo_values(combo: QtWidgets.QComboBox) -> list[str]:
    values: list[str] = []
    for idx in range(combo.count()):
        data = combo.itemData(idx, QtCore.Qt.ItemDataRole.UserRole)
        if data is None:
            data = combo.itemText(idx)
        values.append(str(data))
    return values


def restore_combo_choice(
    key: str,
    name: str,
    combo: QtWidgets.QComboBox,
    default: str,
) -> str:
    """Restore a stored combo-box value identified by ``key``/``name``."""

    stored = str(_settings().value(f"{key}_{name}", default, type=str) or default)
    values = _combo_values(combo)
    if not values:
        return stored

    stored_lower = stored.lower()
    lowered = [value.lower() for value in values]
    if stored_lower in lowered:
        combo.setCurrentIndex(lowered.index(stored_lower))
    elif default.lower() in lowered:
        combo.setCurrentIndex(lowered.index(default.lower()))
        stored = values[combo.currentIndex()]
    else:
        combo.setCurrentIndex(0)
        stored = values[0]
    return values[combo.currentIndex()]


def store_combo_choice(key: str, name: str, combo: QtWidgets.QComboBox) -> str:
    """Persist the combo-box selection identified by ``key``/``name``."""

    idx = combo.currentIndex()
    if idx < 0:
        value = ""
    else:
        data = combo.itemData(idx, QtCore.Qt.ItemDataRole.UserRole)
        value = str(data if data is not None else combo.itemText(idx))
    _settings().setValue(f"{key}_{name}", value.lower())
    return value.lower()


def restore_png_dpi(key: str, spin: QtWidgets.QSpinBox, default: int) -> int:
    """Set ``spin`` to the last stored PNG DPI for ``key`` and return it."""

    try:
        value = int(_settings().value(f"{key}_png_dpi", int(default), type=int))
    except Exception:
        value = int(default)
    spin.setValue(value)
    return value


def store_png_dpi(key: str, dpi: int) -> int:
    """Persist ``dpi`` for ``key`` and return the stored integer value."""

    value = int(dpi)
    _settings().setValue(f"{key}_png_dpi", value)
    return value


def select_files_or_folder(
    parent: QtWidgets.QWidget | None = None,
    ext: str = ".txt",
    *,
    key: str | None = None,
) -> list[str]:
    """Return a list of files with extension ``ext`` chosen by the user.

    Remembers the last input directory and defaults to the repository's
    ``sample_data`` folder or the user's ``Downloads`` directory if it does not
    exist.
    """

    settings = _settings()
    last_in_key = f"{key}_last_input_dir" if key else "last_input_dir"
    start_dir = settings.value(last_in_key, sample_dir(), type=str)
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle("Select Input")
    box.setText("Choose input files or a folder with data")
    files_btn = box.addButton("Files", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    folder_btn = box.addButton("Folder", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
    box.exec()

    clicked = box.clickedButton()
    paths: list[str] = []
    label = ext.lstrip(".").upper()
    if clicked == files_btn:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            parent,
            f"Select {label} files",
            start_dir,
            f"{label} files (*{ext});;All files (*)",
        )
        if paths:
            settings.setValue(last_in_key, os.path.dirname(paths[0]))
    elif clicked == folder_btn:
        directory = QtWidgets.QFileDialog.getExistingDirectory(parent, "Select folder", start_dir)
        if directory:
            settings.setValue(last_in_key, directory)
            for root, _dirs, files in os.walk(directory):
                for name in files:
                    if name.lower().endswith(ext.lower()):
                        paths.append(os.path.join(root, name))
            paths.sort()
    return list(paths)


class _ConsoleStream:
    def __init__(self, widget: QtWidgets.QPlainTextEdit):
        self.widget = widget

    def write(self, msg: str) -> None:
        self.widget.appendPlainText(msg.rstrip())

    def flush(self) -> None:  # pragma: no cover - required by file-like API
        pass


def run_with_console(func: Callable[[], object | None], console: QtWidgets.QPlainTextEdit) -> None:
    old_out, old_err = sys.stdout, sys.stderr
    stream = _ConsoleStream(console)
    sys.stdout = sys.stderr = stream
    try:
        func()
    finally:
        sys.stdout = old_out
        sys.stderr = old_err


def create_file_widget(
    parent: QtWidgets.QWidget,
    ext: str = ".txt",
    *,
    key: str | None = None,
    on_outlier_toggle: Callable[[bool, list[str]], bool | None] | None = None,
) -> tuple[list[str], QtWidgets.QWidget]:
    """Return a widget managing a list of input files and the backing list."""

    files: list[str] = []
    file_list = QtWidgets.QListWidget()
    file_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
    file_list.setHorizontalScrollBarPolicy(
        QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    file_list.setWordWrap(True)
    file_list.setTextElideMode(QtCore.Qt.TextElideMode.ElideNone)
    file_list.setUniformItemSizes(False)
    file_list.setMinimumWidth(400)
    file_list.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Expanding,
        QtWidgets.QSizePolicy.Policy.Expanding,
    )

    prefs = _settings()
    dev_opts = developer_options()
    storage_key = f"{key}_remembered_files" if key else None

    def _refresh_items() -> None:
        file_list.clear()
        for path in files:
            item = QtWidgets.QListWidgetItem(path)
            if path and not Path(path).exists():
                item.setForeground(QtGui.QColor("#c0392b"))
                item.setToolTip("File could not be found on disk")
            file_list.addItem(item)

    def _store_files() -> None:
        if storage_key is None:
            return
        if dev_opts.keep_files():
            prefs.setValue(storage_key, list(files))
        else:
            prefs.remove(storage_key)

    def _load_persisted_files() -> None:
        if storage_key is None or not dev_opts.keep_files():
            return
        raw = prefs.value(storage_key, [])
        if isinstance(raw, str):
            parts = [seg for seg in raw.splitlines() if seg]
            candidates = parts or ([raw] if raw else [])
        elif isinstance(raw, (list, tuple, set)):
            candidates = [str(seg) for seg in raw if seg]
        else:
            candidates = []
        changed = False
        for candidate in candidates:
            if candidate not in files:
                files.append(candidate)
                changed = True
        if changed or files:
            _refresh_items()

    def add_files() -> None:
        new = select_files_or_folder(parent, ext, key=key)
        changed = False
        for f in new:
            if f not in files:
                files.append(f)
                changed = True
        if changed:
            _refresh_items()
            _store_files()

    def remove_selected() -> None:
        removed = False
        for item in file_list.selectedItems():
            try:
                files.remove(item.text())
                removed = True
            except ValueError:
                pass
            file_list.takeItem(file_list.row(item))
        if removed:
            _refresh_items()
            _store_files()

    def remove_all() -> None:
        if not files:
            return
        files.clear()
        _refresh_items()
        _store_files()

    def open_item(item: QtWidgets.QListWidgetItem) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(item.text()))

    file_list.itemDoubleClicked.connect(open_item)

    # Controls row
    add_btn = QtWidgets.QPushButton("Add Files/Folders")
    add_btn.clicked.connect(add_files)
    remove_btn = QtWidgets.QPushButton("Remove Selected")
    remove_btn.clicked.connect(remove_selected)
    remove_all_btn = QtWidgets.QPushButton("Remove All")
    remove_all_btn.clicked.connect(remove_all)
    # Optional outlier toggles now live with each plotting dialog
    from . import common as _common  # local import to avoid cycles at module import
    chk_out_btn = QtWidgets.QPushButton("Check Outliers")
    chk_out_btn.setCheckable(True)
    chk_out_btn.setToolTip("Enable outlier detection during plotting")
    chk_out_btn.setChecked(bool(_common.CHECK_OUTLIERS))
    auto_rm_cb = QtWidgets.QCheckBox("Remove automatically")
    auto_rm_cb.setToolTip("Skip confirmation when removing outliers")

    auto_key = f"{key}_auto_remove_outliers" if key else None
    auto_pref = bool(_common.AUTO_REMOVE_OUTLIERS)
    if auto_key is not None:
        try:
            auto_pref = bool(prefs.value(auto_key, auto_pref, type=bool))
        except Exception:
            auto_pref = bool(auto_pref)
    auto_rm_cb.setChecked(auto_pref)
    _common.AUTO_REMOVE_OUTLIERS = bool(_common.CHECK_OUTLIERS and auto_rm_cb.isChecked())

    def _set_outlier_enabled(enabled: bool) -> None:
        _common.CHECK_OUTLIERS = bool(enabled)
        _common.AUTO_REMOVE_OUTLIERS = bool(enabled) and auto_rm_cb.isChecked()
        proceed = True
        if on_outlier_toggle is not None:
            try:
                proceed = on_outlier_toggle(bool(enabled), list(files))
            except Exception as exc:
                QtWidgets.QMessageBox.critical(
                    parent,
                    "Outlier Check Failed",
                    str(exc),
                )
                proceed = False
        if proceed is False and enabled:
            _common.CHECK_OUTLIERS = False
            _common.AUTO_REMOVE_OUTLIERS = False
            chk_out_btn.blockSignals(True)
            chk_out_btn.setChecked(False)
            chk_out_btn.blockSignals(False)
            return
        if auto_key is not None:
            prefs.setValue(auto_key, auto_rm_cb.isChecked())

    def _set_auto_remove(enabled: bool) -> None:
        if auto_key is not None:
            prefs.setValue(auto_key, bool(enabled))
        if not _common.CHECK_OUTLIERS:
            _common.AUTO_REMOVE_OUTLIERS = False
            return
        _common.AUTO_REMOVE_OUTLIERS = bool(enabled)

    chk_out_btn.toggled.connect(_set_outlier_enabled)
    auto_rm_cb.toggled.connect(_set_auto_remove)

    container = QtWidgets.QWidget()
    container.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Expanding,
        QtWidgets.QSizePolicy.Policy.Expanding,
    )
    layout = QtWidgets.QVBoxLayout(container)
    btn_row = QtWidgets.QHBoxLayout()
    btn_row.addWidget(add_btn)
    btn_row.addWidget(remove_btn)
    btn_row.addWidget(remove_all_btn)
    btn_row.addWidget(chk_out_btn)
    btn_row.addWidget(auto_rm_cb)
    btn_row.addStretch()
    layout.addLayout(btn_row)
    layout.addWidget(file_list)

    _load_persisted_files()

    def _toggle_keep_files(enabled: bool) -> None:
        if storage_key is None:
            return
        if enabled:
            _load_persisted_files()
            _store_files()
        else:
            prefs.remove(storage_key)

    dev_opts.keep_files_changed.connect(_toggle_keep_files)

    def _cleanup(*_: object) -> None:
        try:
            dev_opts.keep_files_changed.disconnect(_toggle_keep_files)
        except Exception:
            pass

    container.destroyed.connect(_cleanup)

    return files, container


def _as_widget(item: QtWidgets.QWidget | QtWidgets.QLayout | None) -> QtWidgets.QWidget | None:
    """Return ``item`` as a widget, wrapping layouts in a temporary widget."""

    if item is None:
        return None
    if isinstance(item, QtWidgets.QWidget):
        return item
    wrapper = QtWidgets.QWidget()
    wrapper.setLayout(item)
    return wrapper


def arrange_side_panel(
    dialog: QtWidgets.QDialog,
    left: QtWidgets.QWidget,
    file_widget: QtWidgets.QWidget,
    console: QtWidgets.QPlainTextEdit,
    *,
    footer: QtWidgets.QWidget | QtWidgets.QLayout | None = None,
) -> QtWidgets.QSplitter:
    """Place settings beside file list and console to keep dialogs compact."""

    splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

    scroll = QtWidgets.QScrollArea()
    left.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Ignored,
        QtWidgets.QSizePolicy.Policy.Preferred,
    )
    scroll.setWidget(left)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(
        QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    scroll.setSizeAdjustPolicy(
        QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents
    )

    footer_widget = _as_widget(footer)
    if footer_widget is not None:
        footer_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        container = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(scroll, 1)
        column.addWidget(footer_widget, 0)
        splitter.addWidget(container)
    else:
        splitter.addWidget(scroll)

    right = QtWidgets.QWidget()
    side_layout = QtWidgets.QVBoxLayout(right)
    file_widget.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Expanding,
        QtWidgets.QSizePolicy.Policy.Expanding,
    )
    console.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Expanding,
        QtWidgets.QSizePolicy.Policy.Expanding,
    )
    side_layout.addWidget(file_widget, 3)
    side_layout.addWidget(console, 1)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 2)
    splitter.setStretchFactor(1, 3)

    main_layout = QtWidgets.QVBoxLayout(dialog)
    main_layout.addWidget(splitter)

    return splitter


def arrange_top_layout(
    dialog: QtWidgets.QDialog,
    file_widget: QtWidgets.QWidget,
    center: QtWidgets.QWidget,
    console: QtWidgets.QPlainTextEdit,
    *,
    footer: QtWidgets.QWidget | QtWidgets.QLayout | None = None,
    help_topic: str | None = None,
) -> None:
    """Place settings beside file list and console for a compact dialog."""

    # Reuse the side-panel arrangement to keep the console narrow while giving
    # the scrolling settings area ample width.
    splitter = arrange_side_panel(dialog, center, file_widget, console, footer=footer)

    # Ensure the dialog fits comfortably on screen and widgets remain visible
    # when resized. This mirrors the sizing logic of the previous top layout but
    # applies it to the side-panel arrangement.
    screen = dialog.screen() or QtGui.QGuiApplication.primaryScreen()
    if screen is not None:
        rect = screen.availableGeometry()
        width = min(1200, rect.width() - 80)
        height = min(900, rect.height() - 80)
    else:
        width, height = 1000, 800
    dialog.resize(width, height)
    dialog.setMinimumSize(min(width, 900), min(height, 600))

    split_sizes = [int(width * 0.44), int(width * 0.56)]
    if splitter is not None and splitter.orientation() == QtCore.Qt.Orientation.Horizontal:
        try:
            splitter.setSizes(split_sizes)
        except Exception:
            pass

    install_standard_menu(
        dialog,
        help_topic=help_topic,
        console=console,
        file_widget=file_widget,
        splitter=splitter,
        default_split_sizes=split_sizes,
    )


def _ensure_menu_bar(target: QtWidgets.QWidget) -> QtWidgets.QMenuBar:
    """Return a menu bar for ``target``, creating one if required."""

    if isinstance(target, QtWidgets.QMainWindow):
        bar = target.menuBar()
        if bar is None:
            bar = QtWidgets.QMenuBar(target)
            target.setMenuBar(bar)
        return bar

    layout = target.layout()
    if layout is None:
        layout = QtWidgets.QVBoxLayout(target)
        target.setLayout(layout)

    bar = getattr(layout, "menuBar", lambda: None)()
    if bar is None:
        bar = QtWidgets.QMenuBar(target)
        layout.setMenuBar(bar)
    return bar


def install_standard_menu(
    target: QtWidgets.QWidget,
    *,
    help_topic: str | None = None,
    console: QtWidgets.QWidget | None = None,
    file_widget: QtWidgets.QWidget | None = None,
    splitter: QtWidgets.QSplitter | None = None,
    default_split_sizes: list[int] | tuple[int, int] | None = None,
) -> QtWidgets.QMenuBar:
    """Attach the shared menu bar with theme, layout, and help entries."""

    menu_bar = _ensure_menu_bar(target)

    # Clear any previous shared menus while keeping custom entries intact.
    # We identify menus we manage by object names to avoid clobbering user
    # customisations.
    to_remove = []
    for action in menu_bar.actions():
        menu = action.menu()
        if menu is not None and menu.objectName().startswith("mw_shared_"):
            to_remove.append(action)
    for action in to_remove:
        menu_bar.removeAction(action)

    view_menu = menu_bar.addMenu("&View")
    if view_menu is None:
        return menu_bar
    view_menu.setObjectName("mw_shared_view")
    theme_submenu = theme_manager().create_theme_menu(view_menu)
    if isinstance(theme_submenu, QtWidgets.QMenu):
        theme_submenu.setObjectName("mw_shared_theme")
        view_menu.addMenu(theme_submenu)

    if file_widget is not None:
        view_menu.addSeparator()
        files_action = view_menu.addAction("Show &File Browser")
        if files_action is not None:
            files_action.setCheckable(True)
            files_action.setChecked(file_widget.isVisible())
            files_action.toggled.connect(file_widget.setVisible)

    if console is not None:
        view_menu.addSeparator()
        console_action = view_menu.addAction("Show &Console")
        if console_action is not None:
            console_action.setCheckable(True)
            console_action.setChecked(console.isVisible())

            def _set_console_visible(checked: bool) -> None:
                console.setVisible(checked)

            console_action.toggled.connect(_set_console_visible)

            def _sync_console() -> None:
                state = console.isVisible()
                if console_action.isChecked() != state:
                    console_action.blockSignals(True)
                    console_action.setChecked(state)
                    console_action.blockSignals(False)

            sync_filter = _VisibilitySync(console_action, _sync_console)
            console.installEventFilter(sync_filter)
            setattr(console, "_mw_visibility_sync", sync_filter)

    if splitter is not None and default_split_sizes:
        view_menu.addSeparator()

        def _reset_layout() -> None:
            sizes = [max(40, int(default_split_sizes[0])), max(40, int(default_split_sizes[1]))]
            try:
                splitter.setSizes(sizes)
            except Exception:
                pass

        reset_action = view_menu.addAction("&Reset Layout")
        if reset_action is not None:
            reset_action.triggered.connect(_reset_layout)

    developer_menu = developer_options().create_menu(menu_bar)
    developer_menu.setObjectName("mw_shared_developer")
    menu_bar.addMenu(developer_menu)

    help_menu = menu_bar.addMenu("&Help")
    if help_menu is None:
        return menu_bar
    help_menu.setObjectName("mw_shared_help")
    if help_topic:
        help_action = help_menu.addAction("View Help")
        if help_action is not None:
            try:
                help_action.setShortcut(
                    QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.HelpContents)
                )
            except Exception:
                pass

            def _show_help() -> None:
                show_help(help_topic, target)

            help_action.triggered.connect(_show_help)
    else:
        help_menu.setEnabled(False)

    return menu_bar


class _VisibilitySync(QtCore.QObject):
    """Synchronise a checkable action with a widget's visibility."""

    def __init__(self, action: QtGui.QAction, sync: Callable[[], None]):
        super().__init__(action.parent())
        self._action = action
        self._sync = sync

    def eventFilter(
        self,
        a0: QtCore.QObject | None,
        a1: QtCore.QEvent | None,
    ) -> bool:  # noqa: D401
        if a1 is not None and a1.type() in {
            QtCore.QEvent.Type.Show,
            QtCore.QEvent.Type.Hide,
            QtCore.QEvent.Type.ShowToParent,
            QtCore.QEvent.Type.HideToParent,
        }:
            self._sync()
        return False


class _ThemeManager(QtCore.QObject):
    """Coordinate theme changes across every window."""

    theme_changed = QtCore.pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        settings = QtCore.QSettings("microwire", "ui")
        stored = str(settings.value("theme_mode", "system") or "system").lower()
        self._settings = settings
        self._mode = stored if stored in {"system", "light", "dark"} else "system"
        self._actions: list[weakref.ReferenceType[QtGui.QAction]] = []
        app = QtWidgets.QApplication.instance()
        if isinstance(app, QtWidgets.QApplication):
            apply_theme(app, self._mode)

    def current_mode(self) -> str:
        return self._mode

    def apply(self, app: QtWidgets.QApplication) -> None:
        apply_theme(app, self._mode)

    def set_mode(self, mode: str) -> None:
        mode = (mode or "system").lower()
        if mode not in {"system", "light", "dark"}:
            mode = "system"
        if mode == self._mode:
            return
        self._mode = mode
        self._settings.setValue("theme_mode", self._mode)
        app = QtWidgets.QApplication.instance()
        if isinstance(app, QtWidgets.QApplication):
            apply_theme(app, self._mode)
        self._sync_actions()
        self.theme_changed.emit(self._mode)

    def create_theme_menu(self, parent: QtWidgets.QWidget) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu("&Theme", parent)
        group = QtGui.QActionGroup(menu)
        group.setExclusive(True)
        for mode, label in (("system", "System"), ("light", "Light"), ("dark", "Dark")):
            action = group.addAction(label)
            if action is None:
                continue
            action.setData(mode)
            action.setCheckable(True)
            action.setChecked(mode == self._mode)

            def _set_theme(checked: bool, value: str = mode) -> None:
                if checked:
                    theme_manager().set_mode(value)

            action.triggered.connect(_set_theme)
            menu.addAction(action)
            self._actions.append(weakref.ref(action))
        return menu

    def _sync_actions(self) -> None:
        alive: list[weakref.ReferenceType[QtGui.QAction]] = []
        for ref in self._actions:
            action = ref()
            if action is None:
                continue
            alive.append(ref)
            target_state = action.data() == self._mode
            if action.isChecked() != target_state:
                action.blockSignals(True)
                action.setChecked(target_state)
                action.blockSignals(False)
        self._actions = alive


_THEME_MANAGER: _ThemeManager | None = None


def theme_manager() -> _ThemeManager:
    """Return the shared :class:`_ThemeManager` singleton."""

    global _THEME_MANAGER
    if _THEME_MANAGER is None:
        _THEME_MANAGER = _ThemeManager()
    return _THEME_MANAGER


class _DeveloperOptions(QtCore.QObject):
    """Store developer conveniences shared across every window."""

    keep_files_changed = QtCore.pyqtSignal(bool)
    experiments_visibility_changed = QtCore.pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._settings = QtCore.QSettings("microwire", "ui")
        self._keep_files = self._read_bool("developer_keep_files", default=False)
        self._show_experiments = self._read_bool(
            "developer_show_experiments", default=False
        )
        self._keep_actions: list[weakref.ReferenceType[QtGui.QAction]] = []
        self._experiment_actions: list[weakref.ReferenceType[QtGui.QAction]] = []

    # ------------------------------------------------------------------ helpers
    def _read_bool(self, key: str, *, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return default

    def _sync(self, actions: list[weakref.ReferenceType[QtGui.QAction]], state: bool) -> None:
        alive: list[weakref.ReferenceType[QtGui.QAction]] = []
        for ref in actions:
            action = ref()
            if action is None:
                continue
            alive.append(ref)
            if action.isChecked() != state:
                action.blockSignals(True)
                action.setChecked(state)
                action.blockSignals(False)
        actions[:] = alive

    # ------------------------------------------------------------------ exposed API
    def keep_files(self) -> bool:
        return self._keep_files

    def show_experiments(self) -> bool:
        return self._show_experiments

    def set_keep_files(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._keep_files:
            return
        self._keep_files = enabled
        self._settings.setValue("developer_keep_files", enabled)
        self._sync(self._keep_actions, enabled)
        self.keep_files_changed.emit(enabled)

    def set_show_experiments(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._show_experiments:
            return
        self._show_experiments = enabled
        self._settings.setValue("developer_show_experiments", enabled)
        self._sync(self._experiment_actions, enabled)
        self.experiments_visibility_changed.emit(enabled)

    def create_menu(self, parent: QtWidgets.QWidget) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu("&Developer", parent)

        keep_action = menu.addAction("Keep &File Selections")
        if keep_action is not None:
            keep_action.setObjectName("mw_keep_files")
            keep_action.setCheckable(True)
            keep_action.setChecked(self._keep_files)
            keep_action.toggled.connect(self.set_keep_files)
            self._keep_actions.append(weakref.ref(keep_action))

        exp_action = menu.addAction("Show &Experiments Tab")
        if exp_action is not None:
            exp_action.setObjectName("mw_show_experiments")
            exp_action.setCheckable(True)
            exp_action.setChecked(self._show_experiments)
            exp_action.toggled.connect(self.set_show_experiments)
            self._experiment_actions.append(weakref.ref(exp_action))

        return menu


_DEVELOPER_OPTIONS: _DeveloperOptions | None = None


def developer_options() -> _DeveloperOptions:
    """Return the shared :class:`_DeveloperOptions` singleton."""

    global _DEVELOPER_OPTIONS
    if _DEVELOPER_OPTIONS is None:
        _DEVELOPER_OPTIONS = _DeveloperOptions()
    return _DEVELOPER_OPTIONS


def ensure_app_theme(app: QtWidgets.QApplication) -> None:
    """Apply the stored theme preference to ``app``."""

    theme_manager().apply(app)
