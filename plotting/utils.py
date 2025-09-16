from PyQt6 import QtWidgets, QtGui, QtCore
import os
import sys
from pathlib import Path
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.collections import PathCollection
from contextlib import contextmanager
from typing import Callable
import matplotlib.pyplot as plt
import datetime


_SUBSCRIPT_MAP = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


@contextmanager
def origin_session():
    """Return an Origin session that is closed on exit."""

    import originpro as op  # lazy import
    try:
        op.set_show()
    except Exception:
        pass
    try:
        yield op
    finally:
        try:
            op.exit()
        except Exception:
            pass


def release_origin() -> None:
    """Release control of Origin so the application can be closed."""

    try:
        import originpro as op  # type: ignore
    except Exception:
        return
    try:
        op.detach()
    except Exception:
        pass


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

    if scheme is None:
        scheme = app.styleHints().colorScheme()

    if sys.platform.startswith("win"):
        if scheme == QtCore.Qt.ColorScheme.Dark:
            accent = app.style().standardPalette().color(
                QtGui.QPalette.ColorRole.Highlight
            )
            app.setPalette(_dark_palette(accent))
        else:
            app.setPalette(app.style().standardPalette())
    elif sys.platform == "darwin":
        app.setPalette(QtGui.QPalette())
    else:
        if scheme == QtCore.Qt.ColorScheme.Dark:
            accent = app.style().standardPalette().color(
                QtGui.QPalette.ColorRole.Highlight
            )
            app.setPalette(_dark_palette(accent))
        else:
            app.setPalette(app.style().standardPalette())


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

    scheme = app.styleHints().colorScheme()

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
    if hasattr(hints, "colorSchemeChanged"):
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


def _download_dir() -> str:
    return str(Path.home() / "Downloads")


def _sample_dir() -> str:
    sample = Path(__file__).resolve().parents[1] / "sample_data"
    return str(sample) if sample.exists() else _download_dir()


def _settings() -> QtCore.QSettings:
    return QtCore.QSettings("microwire", "plotting")


def get_last_output_dir(default: str | None = None, *, key: str | None = None) -> str:
    s = _settings()
    if key:
        return s.value(f"{key}_last_output_dir", default or _download_dir(), type=str)
    return s.value("last_output_dir", default or _download_dir(), type=str)


def set_last_output_dir(path: str, *, key: str | None = None) -> None:
    s = _settings()
    if key:
        s.setValue(f"{key}_last_output_dir", path)
    else:
        s.setValue("last_output_dir", path)


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
    start_dir = settings.value(last_in_key, _sample_dir(), type=str)
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


def run_with_console(func: Callable[[], None], console: QtWidgets.QPlainTextEdit) -> None:
    old_out, old_err = sys.stdout, sys.stderr
    stream = _ConsoleStream(console)
    sys.stdout = sys.stderr = stream
    try:
        func()
    finally:
        sys.stdout = old_out
        sys.stderr = old_err


def get_readability(key: str) -> bool:
    return _settings().value(f"{key}_readability", False, type=bool)


def set_readability(key: str, value: bool) -> None:
    _settings().setValue(f"{key}_readability", value)


def apply_readability_fonts(title_size: int = 22, base_size: int = 18) -> None:
    plt.rcParams.update({"font.size": base_size, "axes.titlesize": title_size})


def prepare_output_dir(base: str, script: str, create_sub: bool) -> str:
    path = Path(base or _download_dir())
    if create_sub:
        stamp = datetime.date.today().isoformat()
        folder = f"{script} data {stamp}"
        path = path / folder
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


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

    def add_files() -> None:
        new = select_files_or_folder(parent, ext, key=key)
        for f in new:
            if f not in files:
                files.append(f)
        file_list.clear()
        file_list.addItems(files)

    def remove_selected() -> None:
        for item in file_list.selectedItems():
            files.remove(item.text())
            file_list.takeItem(file_list.row(item))

    def open_item(item: QtWidgets.QListWidgetItem) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(item.text()))

    file_list.itemDoubleClicked.connect(open_item)

    # Controls row
    add_btn = QtWidgets.QPushButton("Add Files/Folders")
    add_btn.clicked.connect(add_files)
    remove_btn = QtWidgets.QPushButton("Remove Selected")
    remove_btn.clicked.connect(remove_selected)
    # Optional outlier toggles now live with each plotting dialog
    from . import common as _common  # local import to avoid cycles at module import
    chk_out_btn = QtWidgets.QPushButton("Check Outliers")
    chk_out_btn.setCheckable(True)
    chk_out_btn.setToolTip("Enable outlier detection during plotting")
    chk_out_btn.setChecked(bool(_common.CHECK_OUTLIERS))
    auto_rm_cb = QtWidgets.QCheckBox("Remove automatically")
    auto_rm_cb.setToolTip("Skip confirmation when removing outliers")

    prefs = _settings()
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
    btn_row.addWidget(chk_out_btn)
    btn_row.addWidget(auto_rm_cb)
    btn_row.addStretch()
    layout.addLayout(btn_row)
    layout.addWidget(file_list)

    return files, container


def arrange_side_panel(
    dialog: QtWidgets.QDialog,
    left: QtWidgets.QWidget,
    file_widget: QtWidgets.QWidget,
    console: QtWidgets.QPlainTextEdit,
) -> None:
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


def arrange_top_layout(
    dialog: QtWidgets.QDialog,
    file_widget: QtWidgets.QWidget,
    center: QtWidgets.QWidget,
    console: QtWidgets.QPlainTextEdit,
) -> None:
    """Place settings beside file list and console for a compact dialog."""

    # Reuse the side-panel arrangement to keep the console narrow while giving
    # the scrolling settings area ample width.
    arrange_side_panel(dialog, center, file_widget, console)

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


class ReadabilityControls:
    def __init__(self) -> None:
        self.legend_show: QtWidgets.QCheckBox
        self.legend_size: QtWidgets.QSpinBox
        self.legend_orient: QtWidgets.QComboBox
        self.legend_loc: QtWidgets.QComboBox
        self.legend_symbol: QtWidgets.QCheckBox
        self.legend_symbol_size: QtWidgets.QDoubleSpinBox
        self.tick_show: QtWidgets.QCheckBox
        self.tick_size: QtWidgets.QSpinBox
        self.axis_show: QtWidgets.QCheckBox
        self.axis_size: QtWidgets.QSpinBox
        self.title_show: QtWidgets.QCheckBox
        self.title_size: QtWidgets.QSpinBox


def create_readability_group(key: str, orig_module) -> tuple[ReadabilityControls, QtWidgets.QGroupBox]:
    """Return a fully featured readability group and its controls."""

    s = _settings()
    ctrl = ReadabilityControls()
    grp = QtWidgets.QGroupBox("Readability")
    lay = QtWidgets.QGridLayout(grp)

    setattr(orig_module, "IMPROVE_READABILITY", True)
    if not hasattr(orig_module, "LEGEND_LOCATION"):
        setattr(orig_module, "LEGEND_LOCATION", "inside")

    ctrl.legend_size = QtWidgets.QSpinBox()
    ctrl.legend_size.setRange(6, 72)
    ctrl.legend_size.setValue(int(s.value(f"{key}_legend_size", getattr(orig_module, "LEGEND_SIZE", 18), type=int)))
    ctrl.legend_show = QtWidgets.QCheckBox("Show")
    ctrl.legend_show.setChecked(bool(s.value(f"{key}_show_legend", getattr(orig_module, "SHOW_LEGEND", True), type=bool)))
    ctrl.legend_orient = QtWidgets.QComboBox()
    ctrl.legend_orient.addItems(["Auto", "Vertical", "Horizontal"])
    ctrl.legend_orient.setCurrentText(
        s.value(f"{key}_legend_orient", getattr(orig_module, "LEGEND_ORIENTATION", "auto"), type=str).capitalize()
    )
    ctrl.legend_loc = QtWidgets.QComboBox()
    ctrl.legend_loc.addItem("Inside", "inside")
    ctrl.legend_loc.addItem("Outside (right)", "outside_right")
    stored_loc = str(
        s.value(
            f"{key}_legend_location",
            getattr(orig_module, "LEGEND_LOCATION", "inside"),
            type=str,
        )
    ).strip().lower()
    if stored_loc not in {"inside", "outside_right"}:
        stored_loc = "inside"
    idx = ctrl.legend_loc.findData(stored_loc)
    ctrl.legend_loc.setCurrentIndex(idx if idx >= 0 else 0)
    orig_module.LEGEND_LOCATION = stored_loc
    ctrl.legend_symbol_size = QtWidgets.QDoubleSpinBox()
    ctrl.legend_symbol_size.setRange(1.0, 50.0)
    ctrl.legend_symbol_size.setValue(
        float(s.value(f"{key}_legend_symbol_size", getattr(orig_module, "LEGEND_SYMBOL_SIZE", 10), type=float))
    )
    ctrl.legend_symbol = QtWidgets.QCheckBox("Show symbols")
    ctrl.legend_symbol.setChecked(
        bool(s.value(f"{key}_legend_symbols", getattr(orig_module, "LEGEND_SHOW_SYMBOLS", False), type=bool))
    )

    ctrl.tick_size = QtWidgets.QSpinBox()
    ctrl.tick_size.setRange(6, 72)
    ctrl.tick_size.setValue(int(s.value(f"{key}_tick_size", getattr(orig_module, "TICK_SIZE", 18), type=int)))
    ctrl.tick_show = QtWidgets.QCheckBox("Show")
    ctrl.tick_show.setChecked(bool(s.value(f"{key}_show_ticks", getattr(orig_module, "SHOW_TICK_LABELS", True), type=bool)))

    ctrl.axis_size = QtWidgets.QSpinBox()
    ctrl.axis_size.setRange(6, 72)
    ctrl.axis_size.setValue(int(s.value(f"{key}_axis_size", getattr(orig_module, "AXIS_LABEL_SIZE", 18), type=int)))
    ctrl.axis_show = QtWidgets.QCheckBox("Show")
    ctrl.axis_show.setChecked(bool(s.value(f"{key}_show_axis", getattr(orig_module, "SHOW_AXIS_LABELS", True), type=bool)))

    ctrl.title_size = QtWidgets.QSpinBox()
    ctrl.title_size.setRange(6, 96)
    ctrl.title_size.setValue(int(s.value(f"{key}_title_size", getattr(orig_module, "TITLE_SIZE", 22), type=int)))
    ctrl.title_show = QtWidgets.QCheckBox("Show")
    ctrl.title_show.setChecked(bool(s.value(f"{key}_show_title", getattr(orig_module, "SHOW_TITLE", True), type=bool)))

    lay.addWidget(QtWidgets.QLabel("Legend text size:"), 0, 0)
    lay.addWidget(ctrl.legend_size, 0, 1)
    lay.addWidget(ctrl.legend_show, 0, 2)
    lay.addWidget(QtWidgets.QLabel("Legend orientation:"), 1, 0)
    lay.addWidget(ctrl.legend_orient, 1, 1, 1, 2)
    lay.addWidget(QtWidgets.QLabel("Legend location:"), 2, 0)
    lay.addWidget(ctrl.legend_loc, 2, 1, 1, 2)
    lay.addWidget(QtWidgets.QLabel("Legend symbol size:"), 3, 0)
    lay.addWidget(ctrl.legend_symbol_size, 3, 1)
    lay.addWidget(ctrl.legend_symbol, 3, 2)
    lay.addWidget(QtWidgets.QLabel("Tick label size:"), 4, 0)
    lay.addWidget(ctrl.tick_size, 4, 1)
    lay.addWidget(ctrl.tick_show, 4, 2)
    lay.addWidget(QtWidgets.QLabel("Axis label size:"), 5, 0)
    lay.addWidget(ctrl.axis_size, 5, 1)
    lay.addWidget(ctrl.axis_show, 5, 2)
    lay.addWidget(QtWidgets.QLabel("Title size:"), 6, 0)
    lay.addWidget(ctrl.title_size, 6, 1)
    lay.addWidget(ctrl.title_show, 6, 2)

    def _toggle_legend(checked: bool) -> None:
        ctrl.legend_size.setEnabled(checked)
        ctrl.legend_orient.setEnabled(checked)
        ctrl.legend_loc.setEnabled(checked)
        ctrl.legend_symbol.setEnabled(checked)
        ctrl.legend_symbol_size.setEnabled(checked and ctrl.legend_symbol.isChecked())

    def _toggle_symbol(checked: bool) -> None:
        ctrl.legend_symbol_size.setEnabled(checked and ctrl.legend_show.isChecked())

    ctrl.legend_show.toggled.connect(_toggle_legend)
    ctrl.legend_symbol.toggled.connect(_toggle_symbol)
    ctrl.tick_show.toggled.connect(lambda checked: ctrl.tick_size.setEnabled(checked))
    ctrl.axis_show.toggled.connect(lambda checked: ctrl.axis_size.setEnabled(checked))
    ctrl.title_show.toggled.connect(lambda checked: ctrl.title_size.setEnabled(checked))

    _toggle_legend(ctrl.legend_show.isChecked())
    _toggle_symbol(ctrl.legend_symbol.isChecked())
    ctrl.legend_loc.setEnabled(ctrl.legend_show.isChecked())
    ctrl.tick_size.setEnabled(ctrl.tick_show.isChecked())
    ctrl.axis_size.setEnabled(ctrl.axis_show.isChecked())
    ctrl.title_size.setEnabled(ctrl.title_show.isChecked())

    return ctrl, grp


def sync_readability(key: str, ctrl: ReadabilityControls, orig_module) -> None:
    """Copy readability UI state into ``orig_module`` and persist to settings."""

    orig_module.IMPROVE_READABILITY = True
    orig_module.SHOW_LEGEND = ctrl.legend_show.isChecked()
    orig_module.LEGEND_SIZE = int(ctrl.legend_size.value())
    orig_module.LEGEND_ORIENTATION = ctrl.legend_orient.currentText().lower()
    loc_data = ctrl.legend_loc.currentData()
    orig_module.LEGEND_LOCATION = str(loc_data).lower() if loc_data else "inside"
    orig_module.LEGEND_SHOW_SYMBOLS = ctrl.legend_symbol.isChecked()
    orig_module.LEGEND_SYMBOL_SIZE = float(ctrl.legend_symbol_size.value())
    orig_module.SHOW_TICK_LABELS = ctrl.tick_show.isChecked()
    orig_module.TICK_SIZE = int(ctrl.tick_size.value())
    orig_module.SHOW_AXIS_LABELS = ctrl.axis_show.isChecked()
    orig_module.AXIS_LABEL_SIZE = int(ctrl.axis_size.value())
    orig_module.SHOW_TITLE = ctrl.title_show.isChecked()
    orig_module.TITLE_SIZE = int(ctrl.title_size.value())
    s = _settings()
    s.setValue(f"{key}_show_legend", orig_module.SHOW_LEGEND)
    s.setValue(f"{key}_legend_size", orig_module.LEGEND_SIZE)
    s.setValue(f"{key}_legend_orient", orig_module.LEGEND_ORIENTATION)
    s.setValue(f"{key}_legend_location", orig_module.LEGEND_LOCATION)
    s.setValue(f"{key}_legend_symbols", orig_module.LEGEND_SHOW_SYMBOLS)
    s.setValue(f"{key}_legend_symbol_size", orig_module.LEGEND_SYMBOL_SIZE)
    s.setValue(f"{key}_show_ticks", orig_module.SHOW_TICK_LABELS)
    s.setValue(f"{key}_tick_size", orig_module.TICK_SIZE)
    s.setValue(f"{key}_show_axis", orig_module.SHOW_AXIS_LABELS)
    s.setValue(f"{key}_axis_size", orig_module.AXIS_LABEL_SIZE)
    s.setValue(f"{key}_show_title", orig_module.SHOW_TITLE)
    s.setValue(f"{key}_title_size", orig_module.TITLE_SIZE)


def apply_readability(ax: plt.Axes, cfg: dict) -> None:
    """Apply common readability settings to ``ax`` using values from ``cfg``."""

    apply_readability_fonts(
        cfg.get("TITLE_SIZE", 22), cfg.get("TICK_SIZE", 18)
    )

    if not cfg.get("SHOW_TICK_LABELS", True):
        ax.set_xticklabels([])
        ax.set_yticklabels([])
    else:
        ax.tick_params(labelsize=cfg.get("TICK_SIZE", 18))

    if not cfg.get("SHOW_AXIS_LABELS", True):
        ax.set_xlabel("")
        ax.set_ylabel("")
    else:
        ax.xaxis.label.set_size(cfg.get("AXIS_LABEL_SIZE", 18))
        ax.yaxis.label.set_size(cfg.get("AXIS_LABEL_SIZE", 18))

    if not cfg.get("SHOW_TITLE", True):
        ax.set_title("")
    else:
        ax.title.set_size(cfg.get("TITLE_SIZE", 22))

    legend = ax.get_legend()
    if legend:
        if not cfg.get("SHOW_LEGEND", True):
            legend.set_visible(False)
            return

        handles_existing: list[object] = []
        for attr in ("legendHandles", "legend_handles"):
            found = getattr(legend, attr, None)
            if found:
                handles_existing = list(found)
                break
        labels_existing = [text.get_text() for text in legend.get_texts()]
        location_raw = str(cfg.get("LEGEND_LOCATION", "inside") or "inside").strip().lower()
        legend.remove()

        legend_loc = "best"
        bbox = None
        if location_raw in {"outside_right", "outside", "outside right"}:
            legend_loc = "center left"
            bbox = (1.02, 0.5)
        elif location_raw not in {"inside", "auto", "best", ""}:
            legend_loc = location_raw

        legend_kwargs: dict[str, object] = {"loc": legend_loc}
        if bbox is not None:
            legend_kwargs["bbox_to_anchor"] = bbox
            legend_kwargs["borderaxespad"] = 0.0

        if handles_existing and labels_existing:
            legend = ax.legend(handles=handles_existing, labels=labels_existing, **legend_kwargs)
        else:
            legend = ax.legend(**legend_kwargs)

        legend.set_visible(True)
        size = cfg.get("LEGEND_SIZE", 18)
        for text in legend.get_texts():
            try:
                text.set_fontsize(size)
            except Exception:
                pass

        orient = cfg.get("LEGEND_ORIENTATION", "auto")
        if orient == "horizontal":
            legend.set_ncol(max(1, len(legend.get_texts())))
        elif orient == "vertical":
            legend.set_ncol(1)

        handles: list[object] = []
        for attr in ("legendHandles", "legend_handles"):
            found = getattr(legend, attr, None)
            if found:
                handles = list(found)
                break

        show_symbols = bool(cfg.get("LEGEND_SHOW_SYMBOLS", False))
        marker_size = cfg.get("LEGEND_SYMBOL_SIZE", 10)
        for handle in handles:
            if hasattr(handle, "set_markersize"):
                try:
                    handle.set_markersize(marker_size)
                except Exception:
                    pass
            marker_getter = getattr(handle, "get_marker", None)
            marker_setter = getattr(handle, "set_marker", None)
            linestyle_getter = getattr(handle, "get_linestyle", None)
            has_line = False
            if callable(linestyle_getter):
                try:
                    ls = linestyle_getter()
                except Exception:
                    ls = None
                has_line = ls not in (None, "None", "", " ")
            if isinstance(handle, PathCollection):
                try:
                    if show_symbols:
                        handle.set_sizes([marker_size ** 2])
                        handle.set_alpha(1.0)
                    else:
                        handle.set_sizes([0.1])
                        handle.set_alpha(0.0)
                except Exception:
                    pass
            elif isinstance(handle, Patch):
                try:
                    handle.set_alpha(1.0 if show_symbols else 0.0)
                except Exception:
                    pass
            if callable(marker_setter):
                if not show_symbols:
                    try:
                        marker_setter(None)
                    except Exception:
                        try:
                            marker_setter("")
                        except Exception:
                            pass
                elif not has_line and callable(marker_getter):
                    try:
                        current = marker_getter()
                    except Exception:
                        current = None
                    if current in (None, "", " ", "None"):
                        try:
                            marker_setter("o")
                        except Exception:
                            pass
