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


def test_set_tree_item_text_accepts_positional_name() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        item = QtWidgets.QTreeWidgetItem()
        window._set_tree_item_text(item, "Sample", details="Details")
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


def test_select_directories_native_dialog_loop(monkeypatch, tmp_path: Path) -> None:
    dir_one = tmp_path / "folder_one"
    dir_two = tmp_path / "folder_two"
    dir_one.mkdir()
    dir_two.mkdir()

    picks = iter([str(dir_one), str(dir_two)])
    answers = iter(
        [
            QtWidgets.QMessageBox.StandardButton.Yes,
            QtWidgets.QMessageBox.StandardButton.No,
        ]
    )

    def _fake_get_existing_directory(*_args, **_kwargs) -> str:
        return next(picks, "")

    def _fake_question(*_args, **_kwargs) -> QtWidgets.QMessageBox.StandardButton:
        return next(answers, QtWidgets.QMessageBox.StandardButton.No)

    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getExistingDirectory",
        _fake_get_existing_directory,
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        _fake_question,
    )

    selected = PyPlotWorkbench._select_directories(
        None,
        title="Import Data Folder(s)",
        start_dir=tmp_path,
    )

    assert selected == [str(dir_one.resolve()), str(dir_two.resolve())]


def test_select_directories_cancel_returns_empty(monkeypatch, tmp_path: Path) -> None:
    def _fake_get_existing_directory(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getExistingDirectory",
        _fake_get_existing_directory,
    )

    selected = PyPlotWorkbench._select_directories(
        None,
        title="Import Data Folder(s)",
        start_dir=tmp_path,
    )

    assert selected == []


def test_select_directories_accepts_bound_parent_argument(monkeypatch, tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        def _fake_get_existing_directory(*_args, **_kwargs) -> str:
            return ""

        monkeypatch.setattr(
            QtWidgets.QFileDialog,
            "getExistingDirectory",
            _fake_get_existing_directory,
        )

        selected = window._select_directories(
            window,
            title="Import Data Folder(s)",
            start_dir=tmp_path,
        )

        assert selected == []
    finally:
        window.close()
        app.processEvents()


def test_import_data_from_folder_uses_plugin_scoped_start_dir(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    plugin_name = "VSM Hysteresis Loops"
    start_dir = tmp_path / "plugin_start"
    start_dir.mkdir()
    captured: dict[str, Path] = {}
    try:
        window._current_plotter_name = plugin_name  # noqa: SLF001 - test hook
        window._plugin_last_directories[plugin_name] = start_dir  # noqa: SLF001 - test hook

        def _fake_select(
            _parent: QtWidgets.QWidget | None,
            *,
            title: str,
            start_dir: Path | str,
        ) -> list[str]:
            _ = title
            captured["start_dir"] = Path(start_dir)
            return []

        window._select_directories = _fake_select  # type: ignore[assignment]
        window._import_data_from_folder()

        assert captured.get("start_dir") == start_dir
    finally:
        window.close()
        app.processEvents()


def test_compact_path_text_accepts_bound_call_signature(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        path = tmp_path / "one" / "two" / "three"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

        direct = window._compact_path_text(path, max_parts=2)  # noqa: SLF001 - test hook
        bound_like = PyPlotWorkbench._compact_path_text(window, path, max_parts=2)

        assert direct == bound_like
        assert "two/three" in direct
    finally:
        window.close()
        app.processEvents()


def test_import_paths_vsm_hys_file_registers_without_type_error(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        source = tmp_path / "202602250001-Hys-00.VSM-HYS-DATA"
        source.write_text("Header\n1\t2\t3\n", encoding="utf-8")

        window._import_paths([source])  # noqa: SLF001 - exercise import registration path

        assert window._workbooks  # noqa: SLF001 - workbook created from import
        assert window._worksheets  # noqa: SLF001 - worksheet created from import
    finally:
        window._clear_project_dirty()  # noqa: SLF001 - avoid close prompt in headless tests
        window.close()
        app.processEvents()


def test_vsm_hysteresis_folder_import_updates_selected_paths_and_enables_plot(
    tmp_path: Path,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        source_dir = tmp_path / "vsm_hys"
        source_dir.mkdir(parents=True, exist_ok=True)
        source_file = source_dir / "202507101320-Hys-a140-T-30-00.VSM-Hys-Data"
        source_file.write_text(
            "\n".join(
                [
                    "@Section 0",
                    "Column 0: Time since start, Time [s]",
                    "Column 1: Applied Field, Applied Field [Oe]",
                    "Column 2: Signal parallel with sample, Moment [emu]",
                    "@@END Columns",
                    "@@End of Header.",
                    "@@Data",
                    "New Section: Section 0:",
                    "0.0 0.0 0.0",
                    "1.0 5.0 0.2",
                    "2.0 -5.0 -0.2",
                    "@@END Data",
                ]
            ),
            encoding="utf-8",
        )

        window._select_directories = (  # type: ignore[assignment]
            lambda _parent=None, *, title, start_dir: [str(source_dir)]
        )
        window._import_data_from_folder()

        assert len(window._selected_paths()) > 0  # noqa: SLF001 - plugin host state synced
        assert len(window.measurements) > 0  # noqa: SLF001 - VSM measurements loaded
        assert len(window._worksheets) > 0  # noqa: SLF001 - worksheet tree populated
        assert window.plot_button.isEnabled()
        window._update_action_states()  # noqa: SLF001 - shared button gating
        assert window.plot_button.isEnabled()
    finally:
        window._clear_project_dirty()  # noqa: SLF001 - avoid close prompt in headless tests
        window.close()
        app.processEvents()


def test_vsm_hysteresis_folder_menu_action_uses_plugin_import_handler(
    tmp_path: Path,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        source_dir = tmp_path / "vsm_hys_action"
        source_dir.mkdir(parents=True, exist_ok=True)
        source_file = source_dir / "202507101320-Hys-a140-T-30-00.VSM-Hys-Data"
        source_file.write_text(
            "\n".join(
                [
                    "@Section 0",
                    "Column 0: Time since start, Time [s]",
                    "Column 1: Applied Field, Applied Field [Oe]",
                    "Column 2: Signal parallel with sample, Moment [emu]",
                    "@@END Columns",
                    "@@End of Header.",
                    "@@Data",
                    "New Section: Section 0:",
                    "0.0 0.0 0.0",
                    "1.0 5.0 0.2",
                    "2.0 -5.0 -0.2",
                    "@@END Data",
                ]
            ),
            encoding="utf-8",
        )

        window._select_directories = (  # type: ignore[assignment]
            lambda _parent=None, *, title, start_dir: [str(source_dir)]
        )
        assert window._import_folder_action is not None  # noqa: SLF001 - test guard
        window._import_folder_action.trigger()  # noqa: SLF001 - exercise menu action route

        assert len(window.measurements) > 0  # noqa: SLF001 - plugin parser loaded data
        assert len(window._worksheets) > 0  # noqa: SLF001 - worksheet tree populated
        assert window.plot_button.isEnabled()
    finally:
        window._clear_project_dirty()  # noqa: SLF001 - avoid close prompt in headless tests
        window.close()
        app.processEvents()
