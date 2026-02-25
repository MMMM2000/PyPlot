from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6 import QtWidgets

from plotting.pyplot.app import PyPlotWorkbench


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def test_set_tree_item_text_updates_item() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        item = QtWidgets.QTreeWidgetItem()
        window._set_tree_item_text(item, name="Sample", details="Details")
        assert item.text(0) == "Sample"
        assert item.text(1) == "Details"
    finally:
        window.close()
        app.processEvents()


def test_choose_folder_accepts_multiple_directories(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    dir_one = tmp_path / "folder_one"
    dir_two = tmp_path / "folder_two"
    dir_one.mkdir()
    dir_two.mkdir()
    try:
        window._select_directories = (  # type: ignore[assignment]
            lambda _parent, *, title, start_dir: [str(dir_one), str(dir_two)]
        )
        window._choose_folder()
        assert window._selected_paths() == [dir_one, dir_two]  # noqa: SLF001
    finally:
        window.close()
        app.processEvents()


def test_import_data_from_folder_forwards_multiple_directories(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    dir_one = tmp_path / "folder_one"
    dir_two = tmp_path / "folder_two"
    dir_one.mkdir()
    dir_two.mkdir()
    captured: list[Path] = []
    try:
        window._select_directories = (  # type: ignore[assignment]
            lambda _parent, *, title, start_dir: [str(dir_one), str(dir_two)]
        )
        window._import_paths = lambda paths: captured.extend(list(paths))  # type: ignore[assignment]
        window._import_data_from_folder()
        assert captured == [dir_one, dir_two]
    finally:
        window.close()
        app.processEvents()
