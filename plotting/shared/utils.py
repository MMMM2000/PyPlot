"""Compatibility layer exposing shared PyPlot helper functions."""

from __future__ import annotations

from typing import Callable

from PyQt6 import QtWidgets

from .toolkit import (
    arrange_top_layout,
    create_file_widget,
    developer_options,
    format_annealing_title,
    restore_backend_choice,
    store_backend_choice,
    selected_backend,
    restore_png_dpi,
    store_png_dpi,
    restore_combo_choice,
    store_combo_choice,
    run_with_console,
    show_plots,
    save_figure,
    install_standard_menu as _install_standard_menu,
)
from plotting.shared.theme import ensure_app_theme
from plotting.shared.paths import (
    prepare_output_dir,
    get_last_output_dir,
    set_last_output_dir,
    get_last_used_dir,
    set_last_used_dir,
)
from plotting.shared.origin import (
    origin_session,
    release_origin,
    schedule_origin_release,
)
from plotting.shared.readability import (
    create_readability_group,
    sync_readability,
    apply_readability,
    apply_readability_fonts,
    ReadabilityControls,
)


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
    """Install the shared menu bar with optional File menu actions."""

    menu_bar = _install_standard_menu(
        target,
        help_topic=help_topic,
        console=console,
        file_widget=file_widget,
        splitter=splitter,
        default_split_sizes=default_split_sizes,
    )

    file_menu: QtWidgets.QMenu | None = None
    for action in menu_bar.actions():
        menu = action.menu()
        if menu is not None and menu.objectName() == "mw_shared_file":
            file_menu = menu
            break

    if file_menu is None:
        file_menu = QtWidgets.QMenu("&File", menu_bar)
        file_menu.setObjectName("mw_shared_file")
        first_action = menu_bar.actions()[0] if menu_bar.actions() else None
        if first_action is not None:
            menu_bar.insertMenu(first_action, file_menu)
        else:
            menu_bar.addMenu(file_menu)

    def _append_action(name: str, callback: Callable[[], object | None]) -> None:
        action = file_menu.addAction(name)
        if action is None:
            return

        def _runner() -> None:
            try:
                result = callback()
            except Exception:
                return
            if result is None:
                return
            try:
                bool(result)
            except Exception:
                pass

        action.triggered.connect(_runner)

    added = False
    if callable(open_file):
        _append_action("Open &File…", open_file)
        added = True
    if callable(open_folder):
        _append_action("Open &Folder…", open_folder)
        added = True
    if added:
        file_menu.addSeparator()
    if callable(close_window):
        _append_action("&Close", close_window)

    return menu_bar

__all__ = [
    "arrange_top_layout",
    "create_file_widget",
    "developer_options",
    "format_annealing_title",
    "restore_backend_choice",
    "store_backend_choice",
    "selected_backend",
    "restore_png_dpi",
    "store_png_dpi",
    "restore_combo_choice",
    "store_combo_choice",
    "run_with_console",
    "show_plots",
    "install_standard_menu",
    "save_figure",
    "ensure_app_theme",
    "prepare_output_dir",
    "get_last_output_dir",
    "set_last_output_dir",
    "get_last_used_dir",
    "set_last_used_dir",
    "origin_session",
    "release_origin",
    "schedule_origin_release",
    "create_readability_group",
    "sync_readability",
    "apply_readability",
    "apply_readability_fonts",
    "ReadabilityControls",
]
