from PyQt6 import QtWidgets, QtGui, QtCore
import os
import sys
import weakref
import atexit
import sys
from pathlib import Path
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.collections import PathCollection
from matplotlib.axes import Axes
from matplotlib import colors as mcolors
from contextlib import contextmanager
from typing import Any, Callable, Iterator, cast
import matplotlib.pyplot as plt
import datetime

from app_help import show_help


_SUBSCRIPT_MAP = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _apply_standard_icon(
    action: QtGui.QAction | None,
    role: QtWidgets.QStyle.StandardPixmap | None,
    style: QtWidgets.QStyle | None = None,
) -> None:
    """Assign a style-derived icon to an action when available."""

    if action is None or role is None:
        return
    resolved_style: QtWidgets.QStyle | None = style
    if resolved_style is None:
        try:
            parent = action.parent()
        except Exception:
            parent = None
        if isinstance(parent, QtWidgets.QWidget):
            try:
                resolved_style = parent.style()
            except Exception:
                resolved_style = None
    if resolved_style is None:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                resolved_style = app.style()
            except Exception:
                resolved_style = None
    if resolved_style is None:
        return
    try:
        icon = resolved_style.standardIcon(role)
    except Exception:
        icon = QtGui.QIcon()
    if not icon.isNull():
        try:
            action.setIcon(icon)
        except Exception:
            pass


@contextmanager
def origin_session() -> Iterator[Any]:
    """Return an Origin session that stays available for inspection."""

    import originpro as op  # lazy import

    app = None
    try:
        app = op.Application()  # type: ignore[attr-defined]
    except Exception:
        app = None

    try:
        if app is not None:
            try:
                app.Visible = 1  # type: ignore[attr-defined, assignment]
            except Exception:
                pass
        else:
            try:
                op.set_show()
            except Exception:
                pass
        yield cast(Any, op)
    finally:
        # Keep Origin running for the user; release is handled via ``schedule_origin_release``.
        try:
            cast(Any, op).lt_exec("win -a;")
        except Exception:
            pass


_ORIGIN_RELEASED = False
_ORIGIN_RELEASE_REGISTERED = False
_ORIGIN_RELEASE_SLOTS: list[Callable[[], None]] = []


def release_origin() -> None:
    """Release control of Origin so the application can be closed."""

    global _ORIGIN_RELEASED
    if _ORIGIN_RELEASED:
        return

    try:
        import originpro as op  # type: ignore
    except Exception:
        return

    try:
        cast(Any, op).detach()
    except Exception:
        pass

    _ORIGIN_RELEASED = True


def schedule_origin_release() -> None:
    """Ensure Origin detaches once the application shuts down."""

    global _ORIGIN_RELEASE_REGISTERED

    if _ORIGIN_RELEASED or _ORIGIN_RELEASE_REGISTERED:
        return

    app = QtWidgets.QApplication.instance()
    if app is None:
        release_origin()
        return

    def _detach() -> None:
        release_origin()

    try:
        app.aboutToQuit.connect(_detach)  # type: ignore[arg-type]
    except Exception:
        release_origin()
        return

    _ORIGIN_RELEASE_SLOTS.append(_detach)
    _ORIGIN_RELEASE_REGISTERED = True


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


def _available_geometry(widget: QtWidgets.QWidget) -> QtCore.QRect | None:
    """Return the available screen geometry for ``widget``."""

    screen = getattr(widget, "screen", lambda: None)()
    if screen is None:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                screen = app.primaryScreen()
            except Exception:
                screen = None
    if screen is None:
        return None
    try:
        return screen.availableGeometry()
    except Exception:
        return None


def _center_window(widget: QtWidgets.QWidget) -> None:
    """Move ``widget`` to the centre of its current screen."""

    if not isinstance(widget, QtWidgets.QWidget):
        return
    widget.showNormal()
    available = _available_geometry(widget)
    frame = widget.frameGeometry()
    if available is None:
        widget.move(max(0, frame.x()), max(0, frame.y()))
        return

    size = frame.size()
    if size.width() <= 0 or size.height() <= 0:
        hint = widget.sizeHint()
        width = max(widget.minimumWidth(), hint.width(), 320)
        height = max(widget.minimumHeight(), hint.height(), 240)
        size.setWidth(width)
        size.setHeight(height)
        frame.setSize(size)

    frame.moveCenter(available.center())
    widget.move(frame.topLeft())


def _fill_window(widget: QtWidgets.QWidget) -> None:
    """Expand ``widget`` to fill the available screen geometry."""

    if not isinstance(widget, QtWidgets.QWidget):
        return
    widget.showNormal()
    available = _available_geometry(widget)
    if available is None:
        try:
            widget.showMaximized()
        except Exception:
            pass
        return
    widget.setGeometry(available)


def _activate_window(widget: QtWidgets.QWidget) -> None:
    """Make ``widget`` the active, front-most window."""

    if not isinstance(widget, QtWidgets.QWidget):
        return
    try:
        widget.show()
        widget.raise_()
        widget.activateWindow()
    except Exception:
        pass


def _visible_windows(exclude: QtWidgets.QWidget | None = None) -> list[QtWidgets.QWidget]:
    """Return a list of visible top-level windows, ordered by title."""

    app = QtWidgets.QApplication.instance()
    if app is None:
        return []
    windows: list[QtWidgets.QWidget] = []
    for widget in app.topLevelWidgets():
        if not isinstance(widget, QtWidgets.QWidget):
            continue
        if not widget.isWindow():
            continue
        if exclude is not None and widget is exclude:
            continue
        try:
            visible = widget.isVisible()
        except Exception:
            visible = False
        if not visible:
            continue
        windows.append(widget)

    def _sort_key(item: QtWidgets.QWidget) -> tuple[int, str, int]:
        title = item.windowTitle() or item.objectName() or item.__class__.__name__
        try:
            active = QtWidgets.QApplication.activeWindow() is item
        except Exception:
            active = False
        return (0 if active else 1, title.casefold(), id(item))

    windows.sort(key=_sort_key)
    return windows


def _cycle_window(offset: int) -> None:
    """Activate the next or previous window relative to the current one."""

    windows = _visible_windows()
    if not windows:
        return

    try:
        active = QtWidgets.QApplication.activeWindow()
    except Exception:
        active = None

    if active in windows:
        index = windows.index(active)
    else:
        index = 0

    target = windows[(index + offset) % len(windows)]
    _activate_window(target)


def _bring_all_to_front() -> None:
    """Raise every visible window so they appear in front."""

    for widget in _visible_windows():
        _activate_window(widget)


def _show_move_resize_dialog(widget: QtWidgets.QWidget) -> None:
    """Prompt for explicit geometry settings and apply them to ``widget``."""

    if not isinstance(widget, QtWidgets.QWidget):
        return

    dialog = QtWidgets.QDialog(widget)
    dialog.setWindowTitle("Move && Resize")
    dialog.setModal(True)

    layout = QtWidgets.QFormLayout(dialog)
    layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

    geometry = widget.geometry()
    available = _available_geometry(widget)

    min_width = max(200, widget.minimumWidth())
    min_height = max(200, widget.minimumHeight())
    width_hint = widget.sizeHint().width() if widget.sizeHint().width() > 0 else min_width
    height_hint = widget.sizeHint().height() if widget.sizeHint().height() > 0 else min_height

    width_max = max(min_width, available.width() if available is not None else min_width * 4)
    height_max = max(min_height, available.height() if available is not None else min_height * 4)

    width_value = max(min_width, min(width_max, geometry.width() or width_hint))
    height_value = max(min_height, min(height_max, geometry.height() or height_hint))

    x_spin = QtWidgets.QSpinBox(dialog)
    x_spin.setRange(-10000, 10000)
    x_spin.setValue(geometry.x())
    layout.addRow("X position", x_spin)

    y_spin = QtWidgets.QSpinBox(dialog)
    y_spin.setRange(-10000, 10000)
    y_spin.setValue(geometry.y())
    layout.addRow("Y position", y_spin)

    width_spin = QtWidgets.QSpinBox(dialog)
    width_spin.setRange(min_width, width_max)
    width_spin.setValue(width_value)
    layout.addRow("Width", width_spin)

    height_spin = QtWidgets.QSpinBox(dialog)
    height_spin.setRange(min_height, height_max)
    height_spin.setValue(height_value)
    layout.addRow("Height", height_spin)

    button_box = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Ok
        | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
        parent=dialog,
    )
    layout.addRow(button_box)

    button_box.accepted.connect(dialog.accept)
    button_box.rejected.connect(dialog.reject)

    if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
        return

    new_width = width_spin.value()
    new_height = height_spin.value()
    new_x = x_spin.value()
    new_y = y_spin.value()

    if available is not None:
        new_width = min(new_width, available.width())
        new_height = min(new_height, available.height())
        new_x = max(available.left(), min(new_x, available.right() - new_width))
        new_y = max(available.top(), min(new_y, available.bottom() - new_height))

    widget.showNormal()
    widget.setGeometry(QtCore.QRect(new_x, new_y, new_width, new_height))
    _activate_window(widget)


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


def run_with_console(func: Callable[[], object | None], console: QtWidgets.QPlainTextEdit) -> None:
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
    on_change: Callable[[list[str]], None] | None = None,
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

    def _notify_change() -> None:
        if on_change is None:
            return
        try:
            on_change(list(files))
        except Exception as exc:
            print(f"file change callback failed: {exc}")

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
            _notify_change()

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
            _notify_change()

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
            _notify_change()

    def remove_all() -> None:
        if not files:
            return
        files.clear()
        _refresh_items()
        _store_files()
        _notify_change()

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
    _notify_change()

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
    else:
        layout = target.layout()
        if layout is None:
            layout = QtWidgets.QVBoxLayout(target)
            target.setLayout(layout)

        bar = getattr(layout, "menuBar", lambda: None)()
        if bar is None:
            bar = QtWidgets.QMenuBar(target)
            layout.setMenuBar(bar)

    if sys.platform == "darwin":
        try:
            bar.setNativeMenuBar(True)
        except Exception:
            pass

    return bar


class _WindowMenuManager(QtCore.QObject):
    """Populate the shared Window menu with native-feeling actions."""

    def __init__(self, menu: QtWidgets.QMenu, target: QtWidgets.QWidget) -> None:
        super().__init__(menu)
        self._menu = menu
        self._target_ref = weakref.ref(target)
        menu.aboutToShow.connect(self.rebuild)

    def rebuild(self) -> None:
        menu = self._menu
        target = self._target_ref()
        menu.clear()
        if target is None:
            placeholder = menu.addAction("No window")
            if placeholder is not None:
                placeholder.setEnabled(False)
            return

        style = getattr(target, "style", lambda: None)()
        if style is None:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                style = app.style()

        def _set_shortcut(
            action: QtGui.QAction | None,
            shortcut: QtGui.QKeySequence.StandardKey | str,
        ) -> None:
            if action is None:
                return
            try:
                if isinstance(shortcut, QtGui.QKeySequence.StandardKey):
                    action.setShortcut(QtGui.QKeySequence(shortcut))
                else:
                    action.setShortcut(QtGui.QKeySequence(shortcut))
            except Exception:
                pass

        # Minimize ---------------------------------------------------------
        minimize_action = menu.addAction("Minimize")
        _apply_standard_icon(minimize_action, QtWidgets.QStyle.StandardPixmap.SP_TitleBarMinButton, style)
        try:
            minimize_attr = getattr(QtGui.QKeySequence.StandardKey, "Minimize")
        except AttributeError:
            minimize_attr = None
        except Exception:
            minimize_attr = None
        try:
            unknown_key = getattr(QtGui.QKeySequence.StandardKey, "UnknownKey")
        except AttributeError:
            unknown_key = None
        except Exception:
            unknown_key = None
        minimize_shortcut: QtGui.QKeySequence.StandardKey | str
        if minimize_attr is None or minimize_attr == unknown_key:
            minimize_shortcut = "Meta+M" if sys.platform == "darwin" else "Ctrl+M"
        else:
            minimize_shortcut = minimize_attr
        _set_shortcut(minimize_action, minimize_shortcut)
        if minimize_action is not None:
            if hasattr(target, "showMinimized"):
                minimize_action.triggered.connect(target.showMinimized)
            else:
                minimize_action.setEnabled(False)

        # Zoom / Maximize --------------------------------------------------
        zoom_label = "Zoom" if sys.platform == "darwin" else "Maximize"
        zoom_action = menu.addAction(zoom_label)
        _apply_standard_icon(zoom_action, QtWidgets.QStyle.StandardPixmap.SP_TitleBarMaxButton, style)
        if zoom_action is not None:

            def _toggle_zoom() -> None:
                if not hasattr(target, "isMaximized"):
                    zoom_action.setEnabled(False)
                    return
                try:
                    if target.isMaximized():
                        target.showNormal()
                    else:
                        target.showMaximized()
                except Exception:
                    pass

            zoom_action.triggered.connect(_toggle_zoom)

        # Fill -------------------------------------------------------------
        fill_action = menu.addAction("Fill Screen")
        _apply_standard_icon(fill_action, QtWidgets.QStyle.StandardPixmap.SP_DesktopIcon, style)
        if fill_action is not None:

            def _fill_target() -> None:
                _fill_window(target)

            fill_action.triggered.connect(_fill_target)

        # Center -----------------------------------------------------------
        center_action = menu.addAction("Center on Screen")
        _apply_standard_icon(center_action, QtWidgets.QStyle.StandardPixmap.SP_DialogResetButton, style)
        if center_action is not None:

            def _center_target() -> None:
                _center_window(target)

            center_action.triggered.connect(_center_target)

        # Move & Resize ----------------------------------------------------
        move_action = menu.addAction("Move && Resize…")
        _apply_standard_icon(move_action, QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView, style)
        if move_action is not None:

            def _move_resize_target() -> None:
                _show_move_resize_dialog(target)

            move_action.triggered.connect(_move_resize_target)

        # Full screen ------------------------------------------------------
        full_screen_active = False
        if hasattr(target, "isFullScreen"):
            try:
                full_screen_active = target.isFullScreen()
            except Exception:
                full_screen_active = False
        full_screen_text = "Exit Full Screen" if full_screen_active else "Enter Full Screen"
        full_screen_action = menu.addAction(full_screen_text)
        _apply_standard_icon(full_screen_action, QtWidgets.QStyle.StandardPixmap.SP_TitleBarShadeButton, style)
        if full_screen_action is not None:
            _set_shortcut(
                full_screen_action,
                QtGui.QKeySequence.StandardKey.FullScreen,
            )

            def _toggle_full_screen() -> None:
                if not hasattr(target, "isFullScreen"):
                    full_screen_action.setEnabled(False)
                    return
                try:
                    if target.isFullScreen():
                        target.showNormal()
                    else:
                        target.showFullScreen()
                except Exception:
                    pass
                self.rebuild()

            full_screen_action.triggered.connect(_toggle_full_screen)

        # Navigation -------------------------------------------------------
        menu.addSeparator()
        next_action = menu.addAction("Next Window")
        _apply_standard_icon(next_action, QtWidgets.QStyle.StandardPixmap.SP_ArrowForward, style)
        if next_action is not None:
            _set_shortcut(
                next_action,
                QtGui.QKeySequence.StandardKey.NextChild,
            )
            next_action.triggered.connect(lambda: _cycle_window(+1))

        prev_action = menu.addAction("Previous Window")
        _apply_standard_icon(prev_action, QtWidgets.QStyle.StandardPixmap.SP_ArrowBack, style)
        if prev_action is not None:
            _set_shortcut(
                prev_action,
                QtGui.QKeySequence.StandardKey.PreviousChild,
            )
            prev_action.triggered.connect(lambda: _cycle_window(-1))

        bring_action = menu.addAction("Bring All to Front")
        _apply_standard_icon(bring_action, QtWidgets.QStyle.StandardPixmap.SP_BrowserReload, style)
        if bring_action is not None:
            bring_action.triggered.connect(_bring_all_to_front)

        # Window list ------------------------------------------------------
        windows = _visible_windows()
        menu.addSeparator()
        if windows:
            menu.addSection("Windows")
            try:
                active = QtWidgets.QApplication.activeWindow()
            except Exception:
                active = None
            for widget in windows:
                title = widget.windowTitle() or widget.objectName() or widget.__class__.__name__
                entry = menu.addAction(title)
                if entry is None:
                    continue
                icon = getattr(widget, "windowIcon", lambda: QtGui.QIcon())()
                if icon is not None and not icon.isNull():
                    entry.setIcon(icon)
                entry.setCheckable(True)
                entry.setChecked(widget is active)

                def _activate_target(_: bool = False, w: QtWidgets.QWidget = widget) -> None:
                    _activate_window(w)

                entry.triggered.connect(_activate_target)
        else:
            placeholder = menu.addAction("No open windows")
            if placeholder is not None:
                placeholder.setEnabled(False)


def install_standard_menu(
    target: QtWidgets.QWidget,
    *,
    help_topic: str | None = None,
    console: QtWidgets.QWidget | None = None,
    file_widget: QtWidgets.QWidget | None = None,
    splitter: QtWidgets.QSplitter | None = None,
    default_split_sizes: list[int] | tuple[int, int] | None = None,
    open_file: Callable[[], None] | None = None,
    open_folder: Callable[[], None] | None = None,
    close_window: Callable[[], None] | None = None,
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

    def _resolve_handler(
        default: Callable[[], None] | None,
        names: tuple[str, ...],
    ) -> Callable[[], None] | None:
        if callable(default):
            return default
        for name in names:
            candidate = getattr(target, name, None)
            if callable(candidate):
                return candidate
        return None

    file_handler = _resolve_handler(
        open_file,
        (
            "_open_files_from_menu",
            "open_files",
            "open_file",
            "choose_files",
            "choose_file",
            "browse_files",
            "browse_file",
            "select_files",
            "select_file",
        ),
    )
    folder_handler = _resolve_handler(
        open_folder,
        (
            "_open_folder_from_menu",
            "open_folder",
            "open_directory",
            "open_dir",
            "choose_folder",
            "choose_directory",
            "choose_dir",
            "browse_folder",
            "browse_directory",
            "browse_dir",
            "select_folder",
            "select_directory",
        ),
    )

    close_handler: Callable[[], None] | None = close_window if callable(close_window) else None
    if close_handler is None:
        candidate = getattr(target, "close", None)
        close_handler = candidate if callable(candidate) else None

    file_menu = QtWidgets.QMenu("&File", menu_bar)
    file_menu.setObjectName("mw_shared_file")
    first_action = menu_bar.actions()[0] if menu_bar.actions() else None
    if first_action is not None:
        menu_bar.insertMenu(first_action, file_menu)
    else:
        menu_bar.addMenu(file_menu)

    open_file_action = file_menu.addAction("Open &File…")
    if open_file_action is not None:
        try:
            open_file_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Open))
        except Exception:
            pass
        if file_handler is not None:
            open_file_action.triggered.connect(file_handler)
        else:
            open_file_action.setEnabled(False)

    open_folder_action = file_menu.addAction("Open F&older…")
    if open_folder_action is not None:
        shortcut = "Ctrl+Shift+O"
        if sys.platform == "darwin":
            shortcut = "Meta+Shift+O"
        try:
            open_folder_action.setShortcut(QtGui.QKeySequence(shortcut))
        except Exception:
            pass
        if folder_handler is not None:
            open_folder_action.triggered.connect(folder_handler)
        else:
            open_folder_action.setEnabled(False)

    file_menu.addSeparator()

    close_action = file_menu.addAction("&Close Window")
    if close_action is not None:
        try:
            close_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Close))
        except Exception:
            pass
        if close_handler is not None:
            close_action.triggered.connect(close_handler)
        else:
            close_action.setEnabled(False)

    quit_action = file_menu.addAction("&Quit")
    if quit_action is not None:
        try:
            quit_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Quit))
        except Exception:
            pass
        try:
            quit_role = QtGui.QAction.MenuRole.QuitRole  # type: ignore[attr-defined]
        except AttributeError:
            quit_role = None
        if quit_role is not None:
            try:
                quit_action.setMenuRole(quit_role)
            except Exception:
                pass

        def _quit_application() -> None:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.quit()
            else:  # pragma: no cover - fallback for embedded usage
                sys.exit(0)

        quit_action.triggered.connect(_quit_application)

    edit_menu = QtWidgets.QMenu("Edit" if sys.platform == "darwin" else "&Edit", menu_bar)
    edit_menu.setObjectName("mw_shared_edit")
    menu_bar.addMenu(edit_menu)

    def _focused_widget() -> QtWidgets.QWidget | None:
        widget = QtWidgets.QApplication.focusWidget()
        while widget is not None and not widget.isEnabled():
            widget = widget.parentWidget()
        return widget

    edit_actions: list[tuple[QtGui.QAction, tuple[str, ...]]] = []

    def _invoke_focus(methods: tuple[str, ...]) -> None:
        widget = _focused_widget()
        if widget is None:
            return
        for name in methods:
            target_method = getattr(widget, name, None)
            if callable(target_method):
                try:
                    target_method()
                except Exception:
                    pass
                break

    def _add_edit_action(
        label: str,
        shortcut: QtGui.QKeySequence.StandardKey | str | None,
        icon: QtWidgets.QStyle.StandardPixmap | None,
        methods: tuple[str, ...],
    ) -> None:
        action = edit_menu.addAction(label)
        if action is None:
            return
        if icon is not None:
            _apply_standard_icon(action, icon)
        if shortcut is not None:
            try:
                action.setShortcut(QtGui.QKeySequence(shortcut))
            except Exception:
                pass
        action.triggered.connect(lambda checked=False, m=methods: _invoke_focus(m))
        edit_actions.append((action, methods))

    _add_edit_action(
        "Undo",
        QtGui.QKeySequence.StandardKey.Undo,
        QtWidgets.QStyle.StandardPixmap.SP_ArrowBack,
        ("undo",),
    )
    _add_edit_action(
        "Redo",
        QtGui.QKeySequence.StandardKey.Redo,
        QtWidgets.QStyle.StandardPixmap.SP_ArrowForward,
        ("redo",),
    )
    edit_menu.addSeparator()
    _add_edit_action(
        "Cut",
        QtGui.QKeySequence.StandardKey.Cut,
        QtWidgets.QStyle.StandardPixmap.SP_DialogResetButton,
        ("cut",),
    )
    _add_edit_action(
        "Copy",
        QtGui.QKeySequence.StandardKey.Copy,
        QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView,
        ("copy",),
    )
    _add_edit_action(
        "Paste",
        QtGui.QKeySequence.StandardKey.Paste,
        QtWidgets.QStyle.StandardPixmap.SP_DialogOpenButton,
        ("paste",),
    )
    edit_menu.addSeparator()
    _add_edit_action(
        "Select All",
        QtGui.QKeySequence.StandardKey.SelectAll,
        QtWidgets.QStyle.StandardPixmap.SP_DialogYesButton,
        ("selectAll",),
    )

    def _update_edit_actions() -> None:
        widget = _focused_widget()
        for action, methods in edit_actions:
            enabled = False
            current = widget
            while current is not None and not enabled:
                if current.isEnabled():
                    for name in methods:
                        candidate = getattr(current, name, None)
                        if callable(candidate):
                            enabled = True
                            break
                current = current.parentWidget()
            action.setEnabled(enabled)

    edit_menu.aboutToShow.connect(_update_edit_actions)
    _update_edit_actions()

    view_menu = QtWidgets.QMenu("&View", menu_bar)
    menu_bar.addMenu(view_menu)
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

    window_title = "Window" if sys.platform == "darwin" else "&Window"
    window_menu = QtWidgets.QMenu(window_title, menu_bar)
    menu_bar.addMenu(window_menu)
    window_menu.setObjectName("mw_shared_window")
    try:
        window_role = QtGui.QAction.MenuRole.WindowRole  # type: ignore[attr-defined]
    except AttributeError:
        window_role = None
    if window_role is not None:
        try:
            window_menu.menuAction().setMenuRole(window_role)
        except Exception:
            pass
    if not hasattr(window_menu, "_mw_manager"):
        window_menu._mw_manager = _WindowMenuManager(window_menu, target)  # type: ignore[attr-defined]

    developer_menu = developer_options().create_menu(menu_bar)
    developer_menu.setObjectName("mw_shared_developer")
    menu_bar.addMenu(developer_menu)

    help_menu = QtWidgets.QMenu("&Help", menu_bar)
    menu_bar.addMenu(help_menu)
    help_menu.setObjectName("mw_shared_help")
    try:
        help_role = QtGui.QAction.MenuRole.HelpMenuRole  # type: ignore[attr-defined]
    except AttributeError:
        help_role = None
    if help_role is not None:
        try:
            help_menu.menuAction().setMenuRole(help_role)
        except Exception:
            pass
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

    _ensure_help_last(menu_bar)
    if not hasattr(menu_bar, "_mw_help_order_sync"):
        sync = _HelpMenuOrderSync(menu_bar)
        menu_bar.installEventFilter(sync)
        setattr(menu_bar, "_mw_help_order_sync", sync)
    else:
        helper = getattr(menu_bar, "_mw_help_order_sync")
        if isinstance(helper, _HelpMenuOrderSync):
            helper.sync_soon()

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


def _ensure_help_last(menu_bar: QtWidgets.QMenuBar) -> None:
    help_action: QtGui.QAction | None = None
    for action in menu_bar.actions():
        menu = action.menu()
        if menu is not None and menu.objectName() == "mw_shared_help":
            help_action = action
    if help_action is None:
        return
    actions = menu_bar.actions()
    if actions and actions[-1] is help_action:
        return
    menu_bar.removeAction(help_action)
    menu_bar.addAction(help_action)


class _HelpMenuOrderSync(QtCore.QObject):
    """Keep the shared Help menu anchored to the far right."""

    def __init__(self, menu_bar: QtWidgets.QMenuBar) -> None:
        super().__init__(menu_bar)
        self._menu_bar = menu_bar
        self._pending = False

    def eventFilter(
        self, watched: QtCore.QObject | None, event: QtCore.QEvent | None
    ) -> bool:  # noqa: D401
        if watched is self._menu_bar and event is not None:
            if event.type() in {
                QtCore.QEvent.Type.ActionAdded,
                QtCore.QEvent.Type.ActionRemoved,
            }:
                self.sync_soon()
        return super().eventFilter(watched, event)

    def sync_soon(self) -> None:
        if self._pending:
            return
        self._pending = True

        def _sync() -> None:
            self._pending = False
            _ensure_help_last(self._menu_bar)

        QtCore.QTimer.singleShot(0, _sync)


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
        menu = QtWidgets.QMenu("&Develop", parent)

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


class ReadabilityControls:
    def __init__(self) -> None:
        self.legend_show: QtWidgets.QCheckBox
        self.legend_size: QtWidgets.QSpinBox
        self.legend_orient: QtWidgets.QComboBox
        self.legend_loc: QtWidgets.QComboBox
        self.legend_symbol: QtWidgets.QCheckBox
        self.legend_symbol_size: QtWidgets.QDoubleSpinBox
        self.legend_color_match: QtWidgets.QCheckBox
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
    ctrl.legend_color_match = QtWidgets.QCheckBox("Match legend text to curve colors")
    ctrl.legend_color_match.setChecked(
        bool(
            s.value(
                f"{key}_legend_match_colors",
                getattr(orig_module, "LEGEND_MATCH_COLORS", False),
                type=bool,
            )
        )
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
    lay.addWidget(ctrl.legend_color_match, 3, 0, 1, 3)
    lay.addWidget(QtWidgets.QLabel("Legend symbol size:"), 4, 0)
    lay.addWidget(ctrl.legend_symbol_size, 4, 1)
    lay.addWidget(ctrl.legend_symbol, 4, 2)
    lay.addWidget(QtWidgets.QLabel("Tick label size:"), 5, 0)
    lay.addWidget(ctrl.tick_size, 5, 1)
    lay.addWidget(ctrl.tick_show, 5, 2)
    lay.addWidget(QtWidgets.QLabel("Axis label size:"), 6, 0)
    lay.addWidget(ctrl.axis_size, 6, 1)
    lay.addWidget(ctrl.axis_show, 6, 2)
    lay.addWidget(QtWidgets.QLabel("Title size:"), 7, 0)
    lay.addWidget(ctrl.title_size, 7, 1)
    lay.addWidget(ctrl.title_show, 7, 2)

    def _toggle_legend(checked: bool) -> None:
        ctrl.legend_size.setEnabled(checked)
        ctrl.legend_orient.setEnabled(checked)
        ctrl.legend_loc.setEnabled(checked)
        ctrl.legend_symbol.setEnabled(checked)
        ctrl.legend_symbol_size.setEnabled(checked and ctrl.legend_symbol.isChecked())
        ctrl.legend_color_match.setEnabled(checked)

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
    ctrl.legend_color_match.setEnabled(ctrl.legend_show.isChecked())
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
    orig_module.LEGEND_MATCH_COLORS = ctrl.legend_color_match.isChecked()
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
    s.setValue(f"{key}_legend_match_colors", orig_module.LEGEND_MATCH_COLORS)
    s.setValue(f"{key}_show_ticks", orig_module.SHOW_TICK_LABELS)
    s.setValue(f"{key}_tick_size", orig_module.TICK_SIZE)
    s.setValue(f"{key}_show_axis", orig_module.SHOW_AXIS_LABELS)
    s.setValue(f"{key}_axis_size", orig_module.AXIS_LABEL_SIZE)
    s.setValue(f"{key}_show_title", orig_module.SHOW_TITLE)
    s.setValue(f"{key}_title_size", orig_module.TITLE_SIZE)


def apply_readability(ax: Axes, cfg: dict) -> None:
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
        ax.xaxis.label.set_fontsize(cfg.get("AXIS_LABEL_SIZE", 18))
        ax.yaxis.label.set_fontsize(cfg.get("AXIS_LABEL_SIZE", 18))

    if not cfg.get("SHOW_TITLE", True):
        ax.set_title("")
    else:
        ax.title.set_fontsize(cfg.get("TITLE_SIZE", 22))

    legend = ax.get_legend()
    if legend:
        if not cfg.get("SHOW_LEGEND", True):
            legend.set_visible(False)
            return

        handles_existing: list[Any] = []
        for attr in ("legendHandles", "legend_handles"):
            found = getattr(legend, attr, None)
            if found:
                handles_existing = list(found)
                break
        labels_existing = [text.get_text() for text in legend.get_texts()]
        entry_count = max(len(labels_existing), len(handles_existing), 1)
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

        orient = str(cfg.get("LEGEND_ORIENTATION", "auto") or "auto").strip().lower()
        if orient == "horizontal":
            legend_kwargs["ncol"] = entry_count
        elif orient == "vertical":
            legend_kwargs["ncol"] = 1

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

        handles: list[Any] = []
        for attr in ("legendHandles", "legend_handles"):
            found = getattr(legend, attr, None)
            if found:
                handles = list(found)
                break

        show_symbols = bool(cfg.get("LEGEND_SHOW_SYMBOLS", False))
        marker_size = cfg.get("LEGEND_SYMBOL_SIZE", 10)
        match_colors = bool(cfg.get("LEGEND_MATCH_COLORS", False))
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

        if match_colors and handles:
            def _extract_color(handle: Any) -> tuple[float, float, float, float] | None:
                candidates: list[Any] = []
                for attr in ("get_color", "get_facecolor", "get_facecolors", "get_edgecolor"):
                    getter = getattr(handle, attr, None)
                    if not callable(getter):
                        continue
                    try:
                        value = getter()
                    except Exception:
                        continue
                    if value is None:
                        continue
                    candidates.append(value)
                for value in candidates:
                    try:
                        rgba = mcolors.to_rgba_array(value)
                    except Exception:
                        continue
                    if len(rgba):
                        return tuple(rgba[0])
                return None

            for handle, text in zip(handles, legend.get_texts()):
                color = _extract_color(handle)
                if color is not None:
                    try:
                        text.set_color(color)
                    except Exception:
                        pass
