from PyQt6 import QtWidgets, QtGui, QtCore
import os
import sys
from pathlib import Path
from matplotlib.figure import Figure
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


def get_last_output_dir(default: str | None = None) -> str:
    return _settings().value("last_output_dir", default or _download_dir(), type=str)


def set_last_output_dir(path: str) -> None:
    _settings().setValue("last_output_dir", path)


def select_files_or_folder(parent: QtWidgets.QWidget | None = None, ext: str = ".txt") -> list[str]:
    """Return a list of files with extension ``ext`` chosen by the user.

    Remembers the last input directory and defaults to the repository's
    ``sample_data`` folder or the user's ``Downloads`` directory if it does not
    exist.
    """

    settings = _settings()
    start_dir = settings.value("last_input_dir", _sample_dir(), type=str)
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
            settings.setValue("last_input_dir", os.path.dirname(paths[0]))
    elif clicked == folder_btn:
        directory = QtWidgets.QFileDialog.getExistingDirectory(parent, "Select folder", start_dir)
        if directory:
            settings.setValue("last_input_dir", directory)
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


def create_file_widget(parent: QtWidgets.QWidget, ext: str = ".txt") -> tuple[list[str], QtWidgets.QWidget]:
    """Return a widget managing a list of input files and the backing list."""

    files: list[str] = []
    file_list = QtWidgets.QListWidget()
    file_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)

    def add_files() -> None:
        new = select_files_or_folder(parent, ext)
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

    add_btn = QtWidgets.QPushButton("Add Files/Folders")
    add_btn.clicked.connect(add_files)
    remove_btn = QtWidgets.QPushButton("Remove Selected")
    remove_btn.clicked.connect(remove_selected)

    container = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(container)
    layout.addWidget(file_list, 1)
    btn_layout = QtWidgets.QVBoxLayout()
    btn_layout.addWidget(add_btn)
    btn_layout.addWidget(remove_btn)
    btn_layout.addStretch()
    layout.addLayout(btn_layout)

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
    scroll.setWidget(left)
    scroll.setWidgetResizable(True)
    splitter.addWidget(scroll)

    right = QtWidgets.QWidget()
    side_layout = QtWidgets.QVBoxLayout(right)
    side_layout.addWidget(file_widget)
    side_layout.addWidget(console)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 3)
    splitter.setStretchFactor(1, 2)

    main_layout = QtWidgets.QVBoxLayout(dialog)
    main_layout.addWidget(splitter)
