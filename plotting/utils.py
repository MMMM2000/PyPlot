from PyQt6 import QtWidgets, QtGui, QtCore
import os
import sys
from pathlib import Path
from matplotlib.figure import Figure


_SUBSCRIPT_MAP = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def format_annealing_title(base: str) -> str:
    """Return ``base`` with composition digits subscripted and microwire
    identifiers using a slash instead of an underscore."""

    parts = base.split()
    if parts:
        parts[0] = parts[0].translate(_SUBSCRIPT_MAP)
    if len(parts) > 1:
        parts[1] = parts[1].replace("_", "/")
    return " ".join(parts)


def save_figure(fig: Figure, base_path: str | Path, fmt: str = "png", dpi: int = 1000) -> None:
    """Save ``fig`` to ``base_path`` with format ``fmt``.

    ``base_path`` should omit the file extension. ``dpi`` is only applied when
    saving PNG files to allow high-resolution outputs.
    """

    path = f"{base_path}.{fmt}"
    if fmt.lower() == "png":
        fig.savefig(path, dpi=dpi, format=fmt)
    else:
        fig.savefig(path, format=fmt)


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


def select_files_or_folder(parent: QtWidgets.QWidget | None = None, ext: str = ".txt") -> list[str]:
    """Return a list of files with extension ``ext`` chosen by the user.

    A small dialog lets the user pick between selecting individual files or a
    directory.  When a directory is chosen all matching files inside it and any
    sub-directories are returned sorted alphabetically.
    """

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
            "",
            f"{label} files (*{ext});;All files (*)",
        )
    elif clicked == folder_btn:
        directory = QtWidgets.QFileDialog.getExistingDirectory(parent, "Select folder")
        if directory:
            for root, _dirs, files in os.walk(directory):
                for name in files:
                    if name.lower().endswith(ext.lower()):
                        paths.append(os.path.join(root, name))
            paths.sort()
    return list(paths)
