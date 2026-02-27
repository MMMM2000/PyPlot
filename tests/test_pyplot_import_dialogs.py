from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from PyQt6 import QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import pandas as pd

from plotting.plugins.base import PyPlotPlugin
from plotting.pyplot.app import PyPlotWorkbench
from plotting.pyplot.window import (
    TOOLBAR_SECTION_PROPERTY,
    GraphLineState,
    PyPlotWindow,
    TabDescriptor,
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def _iter_toolbar_sections(widget: QtWidgets.QWidget | None) -> list[str]:
    if widget is None:
        return []
    sections: list[str] = []
    for child in widget.findChildren(QtWidgets.QWidget):
        title = child.property(TOOLBAR_SECTION_PROPERTY)
        if isinstance(title, str) and title.strip():
            sections.append(title.strip())
    return sections


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


def test_vsm_hysteresis_uses_shared_controls_and_origin_routing() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        plugin = window._current_plugin  # noqa: SLF001 - test hook
        assert isinstance(plugin, PyPlotPlugin)
        assert plugin.uses_shared_plot_workbooks is True
        assert type(plugin).open_origin is PyPlotPlugin.open_origin
        sections = _iter_toolbar_sections(plugin.settings_widget())
        assert "Appearance" not in sections
    finally:
        window.close()
        app.processEvents()


def test_vsm_hysteresis_register_plot_tab_enables_shared_open_origin() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        fig = Figure(figsize=(4, 3))
        ax = fig.add_subplot(111)
        line, = ax.plot([0.0, 1.0], [0.0, 1.0], label="0°")
        canvas = FigureCanvas(fig)
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)
        window.tab_widget.addTab(tab, "120 °C")
        window.tab_widget.setCurrentWidget(tab)

        descriptor = TabDescriptor(
            kind="temperature",
            title="120 °C",
            root_label="120 °C",
            x_label="Field [Oe]",
            y_label="Moment [emu]",
            canvas=canvas,
            axes=ax,
            lines={
                ("angle", 0.0): GraphLineState(
                    key=("angle", 0.0),
                    label="0°",
                    line=line,
                    base_x=[0.0, 1.0],
                    base_y=[0.0, 1.0],
                    full_x=[0.0, 1.0],
                    full_y=[0.0, 1.0],
                )
            },
            metadata={"plugin": "VSM Hysteresis Loops"},
        )
        window._register_plot_tab(tab, canvas, ax, descriptor)
        window._sync_shared_action_states()  # noqa: SLF001
        assert window.open_origin_button.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_vsm_hysteresis_register_plot_tab_respects_shared_grid_default() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        window._graph_option_defaults_by_plugin["VSM Hysteresis Loops"] = {  # noqa: SLF001 - test hook
            "show_grid": False
        }

        fig = Figure(figsize=(4, 3))
        ax = fig.add_subplot(111)
        line, = ax.plot([0.0, 1.0], [0.0, 1.0], label="0Â°")
        canvas = FigureCanvas(fig)
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)
        window.tab_widget.addTab(tab, "120 Â°C")
        window.tab_widget.setCurrentWidget(tab)

        descriptor = TabDescriptor(
            kind="temperature",
            title="120 Â°C",
            root_label="120 Â°C",
            x_label="Field [Oe]",
            y_label="Moment [emu]",
            canvas=canvas,
            axes=ax,
            lines={
                ("angle", 0.0): GraphLineState(
                    key=("angle", 0.0),
                    label="0Â°",
                    line=line,
                    base_x=[0.0, 1.0],
                    base_y=[0.0, 1.0],
                    full_x=[0.0, 1.0],
                    full_y=[0.0, 1.0],
                )
            },
            metadata={"plugin": "VSM Hysteresis Loops"},
        )
        window._register_plot_tab(tab, canvas, ax, descriptor)
        app.processEvents()

        grid_lines = list(ax.get_xgridlines()) + list(ax.get_ygridlines())
        assert not any(bool(line.get_visible()) for line in grid_lines)
    finally:
        window.close()
        app.processEvents()


def test_vsm_hysteresis_prefers_plot_field_axis_when_applied_field_is_flat() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        # Ensure plugin methods are bound onto the host.
        assert callable(getattr(window, "_resolve_axis_for_loop_plot", None))
        window.x_axis_combo.clear()
        window.x_axis_combo.addItems(
            [
                "Applied Field [Oe]",
                "Applied Field For Plot [Oe]",
                "Signal X direction [emu]",
            ]
        )

        flat_field = pd.DataFrame(
            {
                "Applied Field [Oe]": [0.0, 0.0, 0.0],
                "Applied Field For Plot [Oe]": [-1000.0, 0.0, 1000.0],
            }
        )
        varied_field = pd.DataFrame(
            {
                "Applied Field [Oe]": [0.0, 0.0, 0.0],
                "Applied Field For Plot [Oe]": [-500.0, 0.0, 500.0],
            }
        )
        measurements = [
            SimpleNamespace(data=flat_field),
            SimpleNamespace(data=varied_field),
        ]

        resolved, changed = window._resolve_axis_for_loop_plot(  # type: ignore[attr-defined]
            "Applied Field [Oe]",
            measurements,
            axis="x",
        )
        assert resolved == "Applied Field For Plot [Oe]"
        assert changed is True
    finally:
        window.close()
        app.processEvents()


def test_vsm_hysteresis_populate_axis_defaults_to_applied_field_for_plot() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        assert callable(getattr(window, "_populate_axis_combos", None))
        window._stored_axes = ("Applied Field [Oe]", "Signal X direction [emu]")  # noqa: SLF001
        window.measurements = [  # type: ignore[attr-defined]
            SimpleNamespace(
                data=pd.DataFrame(
                    {
                        "Applied Field [Oe]": [0.0, 0.0, 0.0],
                        "Applied Field For Plot [Oe]": [-1000.0, 0.0, 1000.0],
                        "Signal X direction [emu]": [-0.1, 0.0, 0.1],
                    }
                )
            )
        ]
        labels = [
            "Applied Field [Oe]",
            "Applied Field For Plot [Oe]",
            "Signal X direction [emu]",
        ]

        window._populate_axis_combos(labels)  # type: ignore[attr-defined]

        assert window.x_axis_combo.currentText() == "Applied Field For Plot [Oe]"
    finally:
        window.close()
        app.processEvents()


def test_vsm_hysteresis_preserves_shared_window_handlers() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        expected = {
            "_open_origin_prompt": PyPlotWorkbench._open_origin_prompt,
            "_populate_graph_settings": PyPlotWorkbench._populate_graph_settings,
            "_ensure_graph_tree_item": PyPlotWindow._ensure_graph_tree_item,
            "_focus_tree_on_tab": PyPlotWindow._focus_tree_on_tab,
            "_handle_current_tab_changed": PyPlotWindow._handle_current_tab_changed,
            "_update_tab_buttons": PyPlotWindow._update_tab_buttons,
            "_rebuild_object_manager_for_tab": PyPlotWindow._rebuild_object_manager_for_tab,
            "_handle_object_item_changed": PyPlotWindow._handle_object_item_changed,
        }
        for name, target in expected.items():
            method = getattr(window, name, None)
            assert callable(method)
            assert getattr(method, "__func__", None) is target
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


def test_vsm_hysteresis_generate_falls_back_from_flat_applied_field_axis(
    tmp_path: Path,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        source = tmp_path / "202507101320-Hys-a140-T-30-00.VSM-Hys-Data"
        source.write_text(
            "\n".join(
                [
                    "@Section 0",
                    "Column 0: Time since start, Time [s]",
                    "Column 1: Applied Field, Applied Field [Oe]",
                    "Column 2: Applied Field For Plot, Applied Field For Plot [Oe]",
                    "Column 3: Signal X direction, Signal X direction [emu]",
                    "@@END Columns",
                    "@@End of Header.",
                    "@@Data",
                    "New Section: Section 0:",
                    "0.0 0.0 -10000.0 -0.20",
                    "1.0 0.0 -5000.0 -0.10",
                    "2.0 0.0 0.0 0.00",
                    "3.0 0.0 5000.0 0.10",
                    "4.0 0.0 10000.0 0.20",
                    "@@END Data",
                ]
            ),
            encoding="utf-8",
        )

        window._commit_selected_paths([source])  # noqa: SLF001 - test hook
        window.path_edit.setText(str(source))
        window._load_measurements(show_warning=False)
        assert window.x_axis_combo.currentText() == "Applied Field For Plot [Oe]"
        assert window.y_axis_combo.currentText() == "Signal X direction [emu]"

        x_index = window.x_axis_combo.findText("Applied Field [Oe]")
        y_index = window.y_axis_combo.findText("Signal X direction [emu]")
        assert x_index >= 0
        assert y_index >= 0
        window.x_axis_combo.setCurrentIndex(x_index)
        window.y_axis_combo.setCurrentIndex(y_index)

        window._generate_plots()
        app.processEvents()

        assert window.x_axis_combo.currentText() == "Applied Field For Plot [Oe]"
        assert window.tab_widget.count() > 0
        assert window.open_origin_button.isEnabled()
    finally:
        window._clear_project_dirty()  # noqa: SLF001 - avoid close prompt in headless tests
        window.close()
        app.processEvents()
