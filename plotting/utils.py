from PyQt6 import QtWidgets, QtGui, QtCore
import os
import sys


def _dark_palette() -> QtGui.QPalette:
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(35, 35, 35))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor(255, 0, 0))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(42, 130, 218))
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
    if scheme == QtCore.Qt.ColorScheme.Dark:
        app.setPalette(_dark_palette())
    else:
        app.setPalette(app.style().standardPalette())


def apply_system_theme(app: QtWidgets.QApplication) -> None:
    """Apply a palette and style that follow the host operating system.

    On Windows the native ``windowsvista`` style is used which blends in well
    with the Fluent Design language.  macOS uses the ``macintosh`` style.  Other
    platforms fall back to the cross‑platform ``Fusion`` style.  The current
    color scheme is then inspected to decide whether a dark or light palette
    should be applied, mimicking the system light/dark appearance.  The palette
    updates automatically when the system color scheme changes.
    """

    if sys.platform.startswith("win"):
        app.setStyle("windowsvista")
    elif sys.platform == "darwin":
        # ``macintosh`` is available on all Qt builds for macOS; ``macos`` was
        # introduced in Qt 6.5.  ``setStyle`` ignores unknown styles so this
        # conditional keeps compatibility with older versions.
        style_name = "macos" if "macos" in QtWidgets.QStyleFactory.keys() else "macintosh"
        app.setStyle(style_name)
    else:
        app.setStyle("Fusion")

    _apply_color_scheme(app)

    hints = app.styleHints()
    if hasattr(hints, "colorSchemeChanged"):
        hints.colorSchemeChanged.connect(lambda scheme: _apply_color_scheme(app, scheme))


def apply_dark_theme(app: QtWidgets.QApplication) -> None:
    """Backward compatible wrapper around :func:`apply_system_theme`."""
    apply_system_theme(app)


def select_files_or_folder(parent: QtWidgets.QWidget | None = None) -> list[str]:
    """Return a list of ``.txt`` files chosen by the user.

    A small dialog lets the user pick between selecting individual files or a
    directory.  When a directory is chosen all ``.txt`` files inside it and any
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
    if clicked == files_btn:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            parent,
            "Select measurement files",
            "",
            "Text files (*.txt);;All files (*)",
        )
    elif clicked == folder_btn:
        directory = QtWidgets.QFileDialog.getExistingDirectory(parent, "Select folder")
        if directory:
            for root, _dirs, files in os.walk(directory):
                for name in files:
                    if name.lower().endswith(".txt"):
                        paths.append(os.path.join(root, name))
            paths.sort()
    return list(paths)
