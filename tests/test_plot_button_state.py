from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib import colors as mcolors
from matplotlib import ticker as mticker
import pandas as pd

from plotting.pyplot.app import PyPlotWorkbench
from plotting.pyplot import window as pyplot_window_module
from plotting.plugins.fmr.fmr_plugin import FmrEntry
from plotting.plugins.vsm_temperature_scan.core import VSMEntry as VSMTempEntry
from plotting.pyplot.window import (
    PRIMARY_DOCK_EXPAND_THRESHOLD,
    PRIMARY_DOCK_EXPANDED_FRACTION,
    PRIMARY_DOCK_EXPANDED_MAX,
    PRIMARY_DOCK_MAX_FRACTION,
    PRIMARY_DOCK_DEFAULT_WIDTH,
    WorkbookData,
    WorksheetColumnMeta,
    WorksheetData,
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def _activate_temperature_plugin(window: PyPlotWorkbench) -> None:
    combo = getattr(window, "_plotter_combo", None)
    assert isinstance(combo, QtWidgets.QComboBox)
    index = combo.findText("Temperature Sensitivity")
    assert index >= 0
    combo.setCurrentIndex(index)


def test_plot_button_requires_data_before_click() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        _activate_temperature_plugin(window)
        plot_action = getattr(window, "plot_button", None)
        assert hasattr(plot_action, "isEnabled")

        # No selections and no loaded data => disabled
        window._selected_path_entries = []
        window._current_plugin._data = None  # type: ignore[attr-defined]
        window._update_action_states()
        assert not plot_action.isEnabled()

        # Imported file paths without loaded data -> still disabled
        window._selected_path_entries = [Path(__file__)]
        window._current_plugin._data = None  # type: ignore[attr-defined]
        window._update_action_states()
        assert not plot_action.isEnabled()

        # Loaded data enables the button
        window._current_plugin._data = object()  # type: ignore[attr-defined]
        window._update_action_states()
        assert plot_action.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_graph_formatting_controls_apply_to_current_axes() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        assert window._axes_by_tab
        axes = next(iter(window._axes_by_tab.values()))
        assert axes is not None
        line = axes.plot([1.0, 2.0, 3.0], [2.0, 4.0, 8.0], marker="o", label="Series")[0]

        controls = window._graph_format_controls
        assert isinstance(controls.get("title_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("x_label_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("y_label_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("x_scale_combo"), QtWidgets.QComboBox)
        assert isinstance(controls.get("y_scale_combo"), QtWidgets.QComboBox)

        controls["title_edit"].setText("Edited title")
        controls["x_label_edit"].setText("Edited X")
        controls["y_label_edit"].setText("Edited Y")
        controls["title_font_spin"].setValue(20)
        controls["label_font_spin"].setValue(14)
        controls["tick_font_spin"].setValue(11)
        controls["tick_length_spin"].setValue(9.0)
        controls["tick_width_spin"].setValue(1.8)
        controls["line_width_spin"].setValue(3.4)
        controls["marker_size_spin"].setValue(7.2)
        controls["x_scale_combo"].setCurrentIndex(controls["x_scale_combo"].findData("log"))
        controls["y_scale_combo"].setCurrentIndex(controls["y_scale_combo"].findData("log"))
        controls["x_min_edit"].setText("1")
        controls["x_max_edit"].setText("10")
        controls["y_min_edit"].setText("1")
        controls["y_max_edit"].setText("20")
        controls["show_grid_cb"].setChecked(True)
        controls["show_legend_cb"].setChecked(True)

        window._apply_graph_format(apply_all=True)

        assert axes.get_title() == "Edited title"
        assert axes.get_xlabel() == "Edited X"
        assert axes.get_ylabel() == "Edited Y"
        assert axes.get_xscale() == "log"
        assert axes.get_yscale() == "log"
        x_min, x_max = axes.get_xlim()
        y_min, y_max = axes.get_ylim()
        assert x_min == pytest.approx(1.0)
        assert x_max == pytest.approx(10.0)
        assert y_min == pytest.approx(1.0)
        assert y_max == pytest.approx(20.0)
        assert line.get_linewidth() == pytest.approx(3.4)
        assert line.get_markersize() == pytest.approx(7.2)
    finally:
        window.close()
        app.processEvents()


def test_export_current_graph_pdf_uses_pdf_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        captured: dict[str, object] = {}

        def _fake_save_graph_for_current_tab(
            *, parent: QtWidgets.QWidget | None = None, preferred_suffix: str | None = None
        ) -> bool:
            captured["parent"] = parent
            captured["suffix"] = preferred_suffix
            return True

        monkeypatch.setattr(window, "_save_graph_for_current_tab", _fake_save_graph_for_current_tab)
        window._export_current_graph_pdf()
        assert captured.get("suffix") == ".pdf"
    finally:
        window.close()
        app.processEvents()


def test_apply_graph_format_rebuilds_legend_with_visible_lines_only() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        assert window._axes_by_tab  # noqa: SLF001 - test hook
        axes = next(iter(window._axes_by_tab.values()))  # noqa: SLF001 - test hook
        assert axes is not None

        axes.plot([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], label="Visible")
        hidden = axes.plot([1.0, 2.0, 3.0], [2.0, 3.0, 4.0], label="Hidden")[0]
        hidden.set_visible(False)
        axes.legend(loc="best")

        controls = window._graph_format_controls
        controls["show_legend_cb"].setChecked(True)
        window._apply_graph_format(apply_all=True)

        legend = axes.get_legend()
        assert legend is not None
        labels = [text.get_text() for text in legend.get_texts()]
        assert labels == ["Visible"]
    finally:
        window.close()
        app.processEvents()


def test_apply_graph_options_uses_single_legend_for_twin_axes() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = window._current_axes()
        assert axes is not None
        twin = axes.twinx()
        axes.plot([1.0, 2.0, 3.0], [1.0, 1.5, 2.0], label="50 Oe")
        twin.plot([1.0, 2.0, 3.0], [4.0, 3.5, 3.0], label="10 kOe")

        window._apply_graph_options_to_axes(axes, plugin_name=None)  # noqa: SLF001

        visible_legends = []
        for axis in axes.figure.axes:
            legend = axis.get_legend()
            if legend is not None and legend.get_visible():
                visible_legends.append(legend)
        assert len(visible_legends) == 1
        labels = [text.get_text() for text in visible_legends[0].get_texts()]
        assert "50 Oe" in labels
        assert "10 kOe" in labels
    finally:
        window.close()
        app.processEvents()


def test_apply_graph_format_updates_legend_orientation() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = window._current_axes()
        assert axes is not None
        axes.plot([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], label="A")
        axes.plot([1.0, 2.0, 3.0], [2.0, 3.0, 4.0], label="B")
        axes.legend(loc="best")

        controls = window._graph_format_controls
        orientation_combo = controls.get("legend_orientation_combo")
        assert isinstance(orientation_combo, QtWidgets.QComboBox)
        controls["show_legend_cb"].setChecked(True)

        orientation_combo.setCurrentIndex(orientation_combo.findData("horizontal"))
        window._apply_graph_format(apply_all=False)
        legend = axes.get_legend()
        assert legend is not None
        assert getattr(legend, "_mw_orientation", None) == "horizontal"
        assert int(getattr(legend, "_ncol", 1)) >= 2

        orientation_combo.setCurrentIndex(orientation_combo.findData("vertical"))
        window._apply_graph_format(apply_all=False)
        legend = axes.get_legend()
        assert legend is not None
        assert getattr(legend, "_mw_orientation", None) == "vertical"
        assert int(getattr(legend, "_ncol", 1)) == 1
    finally:
        window.close()
        app.processEvents()


def test_apply_graph_format_reflects_axis_factor_in_label_and_hides_offset_text() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = window._current_axes()
        assert axes is not None
        axes.plot([-1.0, 0.0, 1.0], [-6.0e-10, 0.0, 6.0e-10], label="Series")
        axes.set_xlabel("Magnetic field [A/m]")
        axes.set_ylabel("Magnetic flux [Wb]")

        controls = window._graph_format_controls
        y_factor_edit = controls.get("y_value_factor_edit")
        reflect_y = controls.get("reflect_y_scale_units_cb")
        assert isinstance(y_factor_edit, QtWidgets.QLineEdit)
        assert isinstance(reflect_y, QtWidgets.QCheckBox)
        window._sync_graph_format_controls_from_current_axes()  # noqa: SLF001 - sync control state
        y_factor_edit.setText("10^10")
        reflect_y.setChecked(True)

        window._apply_graph_format(apply_all=False)

        y_label = axes.get_ylabel()
        assert "Magnetic flux" in y_label
        assert "Wb" in y_label
        assert "\u00d710" in y_label
        assert not bool(axes.yaxis.get_offset_text().get_visible())
    finally:
        window.close()
        app.processEvents()


def test_dark_mode_toggle_restores_light_legend_when_snapshot_missing() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._handle_dark_mode_toggled(True)  # noqa: SLF001 - test hook
        app.processEvents()
        window._create_blank_graph()
        axes = window._current_axes()
        assert axes is not None
        axes.plot([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], label="Series")
        legend = axes.legend(loc="best")
        assert legend is not None

        # Simulate plugin-local dark legend styling without a stored light snapshot.
        legend.get_frame().set_facecolor("#1e1e1e")
        legend.get_frame().set_edgecolor("#f1f3f4")
        state = window._axes_theme_state.setdefault(axes, {})  # noqa: SLF001 - test hook
        state.pop("legend", None)

        window._handle_dark_mode_toggled(False)  # noqa: SLF001 - test hook
        app.processEvents()

        legend = axes.get_legend()
        assert legend is not None
        r, g, b, _ = mcolors.to_rgba(legend.get_frame().get_facecolor())
        luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
        assert luminance > 0.75
    finally:
        window.close()
        app.processEvents()


def test_shared_graph_options_persist_to_pyplot_settings_when_plugin_swaps_settings(
    tmp_path: Path,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        shared_ini = tmp_path / "pyplot-shared.ini"
        plugin_ini = tmp_path / "plugin-local.ini"
        shared_settings = QtCore.QSettings(str(shared_ini), QtCore.QSettings.Format.IniFormat)
        plugin_settings = QtCore.QSettings(str(plugin_ini), QtCore.QSettings.Format.IniFormat)
        window._shared_settings = shared_settings  # noqa: SLF001 - test hook
        window.settings = plugin_settings

        payload = window._clean_graph_option_payload(  # noqa: SLF001 - test hook
            {
                **window.GRAPH_OPTION_DEFAULTS,
                "title_font": 27,
                "legend_columns": 3,
            }
        )
        window._graph_option_defaults_global = payload  # noqa: SLF001 - test hook
        window._graph_option_defaults_by_plugin = {}  # noqa: SLF001 - test hook
        window._save_graph_option_settings()  # noqa: SLF001 - test hook

        shared_settings.sync()
        plugin_settings.sync()
        assert isinstance(shared_settings.value("graph_options_global"), str)
        assert plugin_settings.value("graph_options_global", "") in {"", None}
    finally:
        window.close()
        app.processEvents()


def test_preferred_export_directory_prefers_plugin_specific_history(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        plugin_name = "FMR"
        import_dir = tmp_path / "import"
        export_dir = tmp_path / "export"
        import_dir.mkdir()
        export_dir.mkdir()

        window._plugin_last_directories = {plugin_name: import_dir}  # noqa: SLF001 - test hook
        window._plugin_last_export_dirs = {plugin_name: export_dir}  # noqa: SLF001 - test hook

        preferred = window._preferred_export_directory(plugin_name)  # noqa: SLF001 - test hook
        assert preferred == export_dir

        window._plugin_last_export_dirs = {plugin_name: tmp_path / "missing-export"}  # noqa: SLF001
        preferred = window._preferred_export_directory(plugin_name)  # noqa: SLF001 - test hook
        assert preferred == import_dir
    finally:
        window.close()
        app.processEvents()


def test_remember_plugin_export_dir_persists_mapping(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        settings_path = tmp_path / "pyplot.ini"
        window.settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.Format.IniFormat)
        plugin_name = "VSM Temperature Scan"
        export_dir = tmp_path / "vsm-export"
        export_dir.mkdir()

        window._remember_plugin_export_dir(plugin_name, export_dir)  # noqa: SLF001 - test hook

        mapping = getattr(window, "_plugin_last_export_dirs", {})
        assert isinstance(mapping, dict)
        assert mapping.get(plugin_name) == export_dir

        raw = window.settings.value("plugin_last_export_dirs", "")
        assert isinstance(raw, str) and raw
        payload = json.loads(raw)
        assert payload.get(plugin_name) == str(export_dir)
    finally:
        window.close()
        app.processEvents()


def test_vsm_hysteresis_activation_keeps_shared_pyplot_settings() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        shared_settings = window._shared_qsettings()  # noqa: SLF001 - test hook
        assert isinstance(shared_settings, QtCore.QSettings)
        assert window._activate_plotter_for_project_load("VSM Hysteresis Loops")  # noqa: SLF001
        assert window.settings is shared_settings
        plugin_settings = getattr(window, "_vsm_hysteresis_settings", None)
        assert isinstance(plugin_settings, QtCore.QSettings)
        assert plugin_settings is not shared_settings
    finally:
        window.close()
        app.processEvents()


def test_project_tree_worksheet_item_opens_by_hashable_key() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        worksheet = WorksheetData(
            key="wb::hash::sheet",
            name="Sheet",
            dataframe=pd.DataFrame({"x": [1.0], "y": [2.0]}),
            columns={
                "x": WorksheetColumnMeta(long_name="x"),
                "y": WorksheetColumnMeta(long_name="y"),
            },
            workbook_key="wb::hash",
        )
        workbook = WorkbookData(
            key="wb::hash",
            name="Workbook",
            worksheets=[worksheet.key],
        )
        window._register_imported_workbook(workbook, [worksheet])  # noqa: SLF001
        item = window._worksheet_tree_items.get(worksheet.key)  # noqa: SLF001
        assert item is not None

        window._handle_project_item_double_click(item, 0)  # noqa: SLF001
        widget = window._worksheet_tabs_open.get(worksheet.key)  # noqa: SLF001
        assert isinstance(widget, QtWidgets.QWidget)
        assert window.tab_widget.indexOf(widget) >= 0
    finally:
        window.close()
        app.processEvents()


def test_removing_workbook_does_not_break_graph_activation() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._create_blank_graph()
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        axes = window._current_axes()
        assert axes is not None
        axes.plot([0.0, 1.0], [0.0, 1.0], label="Series")
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001
        assert descriptor is not None
        window._register_shared_plot_workbook_for_tab(tab, descriptor)  # noqa: SLF001
        graph_item = window._graph_tree_items.get(tab)  # noqa: SLF001
        assert graph_item is not None
        workbook_key = window._shared_plot_workbook_by_tab.get(tab)  # noqa: SLF001
        assert workbook_key is not None

        window._remove_imported_workbook(workbook_key)  # noqa: SLF001
        window._handle_project_item_double_click(graph_item, 0)  # noqa: SLF001

        assert window.tab_widget.currentWidget() is tab
        assert window.tab_widget.indexOf(tab) >= 0
    finally:
        window.close()
        app.processEvents()


def test_tight_layout_warning_reports_font_offender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = window._current_axes()
        assert axes is not None
        figure = axes.figure
        axes.set_title("Extremely long title for layout warning checks", fontsize=42)

        def _fake_tight_layout(**_: object) -> None:
            import warnings

            warnings.warn(
                "Tight layout not applied. The left and right margins cannot be made large enough to accommodate all Axes decorations.",
                UserWarning,
                stacklevel=2,
            )

        monkeypatch.setattr(figure, "tight_layout", _fake_tight_layout, raising=False)
        assert window._tight_layout_with_feedback(figure, context="Graph formatting", pad=1.0)  # noqa: SLF001

        text = str(getattr(window, "_last_tight_layout_warning_message", "") or "")  # noqa: SLF001
        assert "Likely too large font: Axes 1 title (42.0 pt" in text
    finally:
        window.close()
        app.processEvents()


def test_tight_layout_warning_apply_to_all_uses_one_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = window._current_axes()
        assert axes is not None
        figure = axes.figure

        calls = {"dialog": 0, "autofit": 0}

        monkeypatch.setattr(window, "_can_show_tight_layout_warning_dialog", lambda: True)

        def _fake_dialog(*, message: str, can_apply_override: bool) -> tuple[str, bool]:
            _ = (message, can_apply_override)
            calls["dialog"] += 1
            return ("auto", True)

        def _fake_auto_fit(_figure: object, _recommendations: dict[str, float]) -> int:
            calls["autofit"] += 1
            return 1

        monkeypatch.setattr(window, "_show_tight_layout_warning_dialog", _fake_dialog)
        monkeypatch.setattr(window, "_apply_tight_layout_auto_fit", _fake_auto_fit)

        window._handle_tight_layout_warning(  # noqa: SLF001 - test hook
            figure,
            [
                "Tight layout not applied. The left and right margins cannot be made large enough to accommodate all Axes decorations. (A)",
            ],
            context="Graph formatting",
        )
        window._handle_tight_layout_warning(  # noqa: SLF001 - test hook
            figure,
            [
                "Tight layout not applied. The left and right margins cannot be made large enough to accommodate all Axes decorations. (B)",
            ],
            context="Graph formatting",
        )
        assert calls["dialog"] == 1
        assert calls["autofit"] == 2
    finally:
        window.close()
        app.processEvents()


def test_tight_layout_warning_uses_saved_plugin_override_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = window._current_axes()
        assert axes is not None
        figure = axes.figure

        calls = {"override": 0, "dialog": 0}
        monkeypatch.setattr(window, "_can_show_tight_layout_warning_dialog", lambda: True)
        monkeypatch.setattr(window, "_plugin_name_for_axes", lambda _axes: "Temperature Sensitivity")
        monkeypatch.setattr(window, "_has_plugin_graph_option_override", lambda _name: True)

        def _fake_override(_axes: object, _recommendations: dict[str, float]) -> bool:
            calls["override"] += 1
            return True

        def _fake_dialog(*, message: str, can_apply_override: bool) -> tuple[str, bool]:
            _ = (message, can_apply_override)
            calls["dialog"] += 1
            return ("keep", False)

        monkeypatch.setattr(window, "_apply_plugin_graph_option_override_for_axes", _fake_override)
        monkeypatch.setattr(window, "_show_tight_layout_warning_dialog", _fake_dialog)

        window._handle_tight_layout_warning(  # noqa: SLF001
            figure,
            [
                "Tight layout not applied. The left and right margins cannot be made large enough to accommodate all Axes decorations.",
            ],
            context="Graph formatting",
        )
        assert calls["override"] == 1
        assert calls["dialog"] == 0
    finally:
        window.close()
        app.processEvents()


def test_subwindow_change_event_on_darwin_updates_fullscreen_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab_proxy = window.tab_widget
        subwindow_for = getattr(tab_proxy, "_subwindow_for", None)
        assert callable(subwindow_for)
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        sub = subwindow_for(tab)
        assert sub is not None

        called: list[bool] = []

        def _record(maximized: bool, *, source: object | None = None) -> None:
            _ = source
            called.append(bool(maximized))

        monkeypatch.setattr(pyplot_window_module.sys, "platform", "darwin", raising=False)
        monkeypatch.setattr(tab_proxy, "_handle_subwindow_state_change", _record)

        sub.showMaximized()
        app.processEvents()
        assert called, "Expected state-change handler to run on macOS change events"
    finally:
        window.close()
        app.processEvents()


def test_switching_tabs_refits_figure_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        first_tab = window.tab_widget.currentWidget()
        assert isinstance(first_tab, QtWidgets.QWidget)
        window._create_blank_graph()
        second_tab = window.tab_widget.currentWidget()
        assert isinstance(second_tab, QtWidgets.QWidget)

        fitted: list[object] = []

        def _record_fit(figure: object) -> None:
            fitted.append(figure)

        monkeypatch.setattr(window, "_fit_figure_to_content", _record_fit)
        window.tab_widget.setCurrentWidget(first_tab)
        app.processEvents()
        window.tab_widget.setCurrentWidget(second_tab)
        app.processEvents()
        assert fitted, "Expected tab switch to trigger figure re-fit"
    finally:
        window.close()
        app.processEvents()


def test_switching_tabs_preserves_maximized_subwindow_mode() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        first_tab = window.tab_widget.currentWidget()
        assert isinstance(first_tab, QtWidgets.QWidget)
        window._create_blank_graph()
        second_tab = window.tab_widget.currentWidget()
        assert isinstance(second_tab, QtWidgets.QWidget)

        subwindow_for = getattr(window.tab_widget, "_subwindow_for", None)
        maximize_single = getattr(window.tab_widget, "_maximize_single", None)
        assert callable(subwindow_for)
        assert callable(maximize_single)
        first_sub = subwindow_for(first_tab)
        second_sub = subwindow_for(second_tab)
        assert first_sub is not None
        assert second_sub is not None

        maximize_single(first_sub)
        app.processEvents()
        assert not first_sub.isHidden()
        assert not second_sub.isHidden()

        window.tab_widget.setCurrentWidget(second_tab)
        app.processEvents()
        assert not second_sub.isHidden()
        assert not first_sub.isHidden()
        assert bool(getattr(window.tab_widget, "_global_maximized", False))
        assert bool(getattr(window.tab_widget, "_fullscreen_lock", False))
    finally:
        window.close()
        app.processEvents()


def test_fullscreen_geometry_fills_viewport_for_wide_aspect_graphs() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(1600, 980)
        window._create_blank_graph()
        app.processEvents()

        tab_proxy = window.tab_widget
        mdi = getattr(tab_proxy, "_mdi", None)
        subwindow_for = getattr(tab_proxy, "_subwindow_for", None)
        maximize_single = getattr(tab_proxy, "_maximize_single", None)
        assert isinstance(mdi, QtWidgets.QMdiArea)
        assert callable(subwindow_for)
        assert callable(maximize_single)

        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        sub = subwindow_for(tab)
        assert sub is not None
        sub.set_aspect_ratio(8.0)
        maximize_single(sub)
        app.processEvents()

        margin = getattr(tab_proxy, "_layout_margin", 6)
        viewport = mdi.viewport().rect().adjusted(margin, margin, -margin, -margin)
        geometry = sub.geometry()
        assert geometry.width() >= viewport.width() - 2
        assert geometry.height() >= viewport.height() - 2
    finally:
        window.close()
        app.processEvents()


def test_non_native_demote_event_clears_fullscreen_lock() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        window._create_blank_graph()
        app.processEvents()

        tab_proxy = window.tab_widget
        subwindow_for = getattr(tab_proxy, "_subwindow_for", None)
        maximize_single = getattr(tab_proxy, "_maximize_single", None)
        state_change = getattr(tab_proxy, "_handle_subwindow_state_change", None)
        assert callable(subwindow_for)
        assert callable(maximize_single)
        assert callable(state_change)

        first_tab = window.tab_widget.widget(0)
        second_tab = window.tab_widget.widget(1)
        assert isinstance(first_tab, QtWidgets.QWidget)
        assert isinstance(second_tab, QtWidgets.QWidget)
        first_sub = subwindow_for(first_tab)
        second_sub = subwindow_for(second_tab)
        assert first_sub is not None
        assert second_sub is not None

        tab_proxy._native_subwindow_maximize = False  # noqa: SLF001 - force mac path
        maximize_single(first_sub)
        app.processEvents()

        setattr(first_sub, "_mw_last_old_maximized", False)
        state_change(False, source=first_sub)
        app.processEvents()

        assert not bool(getattr(tab_proxy, "_fullscreen_lock", False))
        assert not bool(getattr(tab_proxy, "_global_maximized", False))
        visible_count = int(not first_sub.isHidden()) + int(not second_sub.isHidden())
        assert visible_count == 2
    finally:
        window.close()
        app.processEvents()


def test_current_annealing_generate_updates_shared_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        monkeypatch.setattr(window, "_confirm_close_with_unsaved_data", lambda: True)
        combo = getattr(window, "_plotter_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)
        index = combo.findText("Current Annealing")
        assert index >= 0
        combo.setCurrentIndex(index)
        plugin = getattr(window, "_current_plugin", None)
        assert plugin is not None

        import pandas as pd

        plugin._data_by_file = {  # noqa: SLF001 - test hook
            "/tmp/anneal-sample.txt": pd.DataFrame(
                {"I_mA": [0.0, 10.0, 20.0, 10.0], "R_Ohm": [10.0, 12.0, 15.0, 13.0]}
            )
        }

        events: list[tuple[str, object]] = []

        def _begin(*args: object, **kwargs: object) -> None:
            events.append(("begin", (args, kwargs)))

        def _update(*args: object, **kwargs: object) -> None:
            events.append(("update", (args, kwargs)))

        def _end(*args: object, **kwargs: object) -> None:
            events.append(("end", (args, kwargs)))

        def _fail_progress_dialog(*args: object, **kwargs: object) -> None:
            raise AssertionError("Current Annealing must use shared task progress instead of QProgressDialog.")

        monkeypatch.setattr(window, "_begin_task_progress", _begin)
        monkeypatch.setattr(window, "_update_task_progress", _update)
        monkeypatch.setattr(window, "_end_task_progress", _end)
        monkeypatch.setattr(QtWidgets, "QProgressDialog", _fail_progress_dialog)

        plugin.generate()
        kinds = [entry[0] for entry in events]
        assert "begin" in kinds
        assert "update" in kinds
        assert "end" in kinds
    finally:
        window.close()
        app.processEvents()


def test_current_annealing_project_payload_restore_rebuilds_plots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        monkeypatch.setattr(window, "_confirm_close_with_unsaved_data", lambda: True)
        file_one = tmp_path / "anneal_one.txt"
        file_two = tmp_path / "anneal_two.txt"
        sample_text = "\n".join(
            (
                "0.001 0.01 10",
                "0.002 0.02 12",
                "0.003 0.03 14",
                "0.002 0.02 13",
            )
        )
        file_one.write_text(sample_text + "\n", encoding="utf-8")
        file_two.write_text(sample_text + "\n", encoding="utf-8")

        combo = getattr(window, "_plotter_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)
        index = combo.findText("Current Annealing")
        assert index >= 0
        combo.setCurrentIndex(index)
        plugin = getattr(window, "_current_plugin", None)
        assert plugin is not None

        commit_paths = getattr(window, "_commit_selected_paths", None)
        assert callable(commit_paths)
        commit_paths([file_one, file_two])

        plugin.load_data()
        plugin.generate()
        app.processEvents()
        assert len(getattr(plugin, "_plot_tabs", [])) == 2

        payload = window._build_project_payload(base_path=tmp_path)  # noqa: SLF001 - persistence API
        assert isinstance(payload.get("active_plugin_state"), dict)

        window._reset_project_state()  # noqa: SLF001 - persistence API
        app.processEvents()
        assert window._apply_project_payload(payload, project_dir=tmp_path)  # noqa: SLF001
        app.processEvents()

        restored_plugin = getattr(window, "_current_plugin", None)
        assert restored_plugin is not None
        assert len(getattr(restored_plugin, "_plot_tabs", [])) == 2
        plot_action = getattr(window, "plot_button", None)
        assert hasattr(plot_action, "isEnabled")
        assert plot_action.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_project_load_autoloads_current_annealing_without_plugin_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        monkeypatch.setattr(window, "_confirm_close_with_unsaved_data", lambda: True)
        path = tmp_path / "autoload_anneal.txt"
        path.write_text(
            "\n".join(
                (
                    "0.001 0.01 9",
                    "0.002 0.02 10",
                    "0.003 0.03 11",
                    "0.002 0.02 10.5",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        payload = {
            "selected_paths": [path.name],
            "workbooks": [],
            "active_plugin": "Current Annealing",
            "active_plugin_state": None,
        }
        assert window._apply_project_payload(payload, project_dir=tmp_path)  # noqa: SLF001
        app.processEvents()

        plugin = getattr(window, "_current_plugin", None)
        assert plugin is not None
        assert window._plugin_has_loaded_data(plugin)  # noqa: SLF001 - readiness helper
        plot_action = getattr(window, "plot_button", None)
        assert hasattr(plot_action, "isEnabled")
        assert plot_action.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_plugin_project_payload_includes_shared_wrapper_for_custom_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        monkeypatch.setattr(window, "_confirm_close_with_unsaved_data", lambda: True)
        source = tmp_path / "shared_wrapper_source.txt"
        source.write_text("0.001 0.01 9\n", encoding="utf-8")
        window._commit_selected_paths([source])  # noqa: SLF001 - persistence helper

        combo = getattr(window, "_plotter_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)

        custom_index = combo.findText("Current Annealing")
        assert custom_index >= 0
        combo.setCurrentIndex(custom_index)
        custom_plugin = getattr(window, "_current_plugin", None)
        assert custom_plugin is not None
        monkeypatch.setattr(
            custom_plugin,
            "serialize_project_state",
            lambda *, base_path: {"custom_state": "ok"},
        )
        payload_custom = window._build_project_payload(base_path=tmp_path)  # noqa: SLF001
        state_custom = payload_custom.get("active_plugin_state")
        assert isinstance(state_custom, dict)
        assert state_custom.get("custom_state") == "ok"
        shared_custom = state_custom.get(window.PLUGIN_SHARED_STATE_KEY)  # noqa: SLF001
        assert isinstance(shared_custom, dict)
        assert isinstance(shared_custom.get("selected_paths"), list)
        assert shared_custom.get("auto_load_on_import") is True
        assert shared_custom.get("had_plots") is False

        window._create_blank_graph()  # noqa: SLF001 - create one plugin-associated tab
        payload_with_plot = window._build_project_payload(base_path=tmp_path)  # noqa: SLF001
        state_with_plot = payload_with_plot.get("active_plugin_state")
        assert isinstance(state_with_plot, dict)
        shared_with_plot = state_with_plot.get(window.PLUGIN_SHARED_STATE_KEY)  # noqa: SLF001
        assert isinstance(shared_with_plot, dict)
        assert shared_with_plot.get("had_plots") is True

    finally:
        window.close()
        app.processEvents()


def test_plugin_project_payload_includes_shared_wrapper_for_default_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        monkeypatch.setattr(window, "_confirm_close_with_unsaved_data", lambda: True)
        source = tmp_path / "shared_wrapper_default.txt"
        source.write_text("0.001 0.01 9\n", encoding="utf-8")
        window._commit_selected_paths([source])  # noqa: SLF001 - persistence helper

        combo = getattr(window, "_plotter_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)
        default_index = combo.findText("Stress Dependence")
        assert default_index >= 0
        combo.setCurrentIndex(default_index)

        payload_default = window._build_project_payload(base_path=tmp_path)  # noqa: SLF001
        state_default = payload_default.get("active_plugin_state")
        assert isinstance(state_default, dict)
        shared_default = state_default.get(window.PLUGIN_SHARED_STATE_KEY)  # noqa: SLF001
        assert isinstance(shared_default, dict)
        assert isinstance(shared_default.get("selected_paths"), list)
    finally:
        window.close()
        app.processEvents()


def test_project_restore_passes_plugin_specific_state_without_shared_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        monkeypatch.setattr(window, "_confirm_close_with_unsaved_data", lambda: True)
        source = tmp_path / "wrapped_state_source.txt"
        source.write_text("0.001 0.01 9\n0.002 0.02 10\n", encoding="utf-8")

        combo = getattr(window, "_plotter_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)
        index = combo.findText("Current Annealing")
        assert index >= 0
        combo.setCurrentIndex(index)
        plugin = getattr(window, "_current_plugin", None)
        assert plugin is not None

        captured: dict[str, object] = {}

        def _capture_restore(state: dict[str, object], *, project_dir: Path) -> None:
            _ = project_dir
            captured.update(state)

        monkeypatch.setattr(plugin, "restore_project_state", _capture_restore)
        monkeypatch.setattr(plugin, "load_data", lambda: None)

        payload = {
            "selected_paths": [],
            "workbooks": [],
            "active_plugin": "Current Annealing",
            "active_plugin_state": {
                "custom_marker": "value",
                window.PLUGIN_SHARED_STATE_KEY: {  # noqa: SLF001 - reserved wrapper key
                    "selected_paths": [source.name],
                    "auto_load_on_import": True,
                },
            },
        }
        assert window._apply_project_payload(payload, project_dir=tmp_path)  # noqa: SLF001
        app.processEvents()

        assert captured == {"custom_marker": "value"}
        selected = window._selected_paths()  # noqa: SLF001 - persistence helper
        assert selected and selected[0].name == source.name
    finally:
        window.close()
        app.processEvents()


def test_project_restore_regenerates_shared_plots_when_plugin_state_lacks_tabs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        monkeypatch.setattr(window, "_confirm_close_with_unsaved_data", lambda: True)
        source = tmp_path / "fmr_autoload.csv"
        source.write_text("field,x,y\n1,2,3\n", encoding="utf-8")

        combo = getattr(window, "_plotter_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)
        index = combo.findText("FMR")
        assert index >= 0
        combo.setCurrentIndex(index)
        plugin = getattr(window, "_current_plugin", None)
        assert plugin is not None

        calls = {"load": 0, "generate": 0}

        def _fake_load_data() -> None:
            calls["load"] += 1
            setattr(plugin, "_data", object())

        def _fake_generate() -> None:
            calls["generate"] += 1

        monkeypatch.setattr(plugin, "load_data", _fake_load_data)
        monkeypatch.setattr(plugin, "generate", _fake_generate)
        monkeypatch.setattr(plugin, "has_loaded_data", lambda: True, raising=False)
        monkeypatch.setattr(plugin, "restore_project_state", lambda state, *, project_dir: None)

        payload = {
            "selected_paths": [],
            "workbooks": [],
            "active_plugin": "FMR",
            "active_plugin_state": {
                window.PLUGIN_SHARED_STATE_KEY: {  # noqa: SLF001 - reserved wrapper key
                    "selected_paths": [source.name],
                    "auto_load_on_import": False,
                    "had_plots": True,
                },
            },
        }

        assert window._apply_project_payload(payload, project_dir=tmp_path)  # noqa: SLF001
        app.processEvents()
        assert calls["load"] == 1
        assert calls["generate"] == 1
    finally:
        window.close()
        app.processEvents()


def test_shared_rescale_works_for_fmr_graphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="FMR")
    try:
        monkeypatch.setattr(window, "_confirm_close_with_unsaved_data", lambda: True)
        import pandas as pd

        combo = getattr(window, "_plotter_combo", None)
        assert isinstance(combo, QtWidgets.QComboBox)
        index = combo.findText("FMR")
        assert index >= 0
        combo.setCurrentIndex(index)
        plugin = getattr(window, "_current_plugin", None)
        assert plugin is not None

        frame = pd.DataFrame(
            {
                "Magnetic Field [Oe]": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
                "Signal X [V]": [0.0, 2.0, 1.0, 3.0, 2.0, 4.0],
                "Signal Y [V]": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0],
            }
        )
        plugin._dataset = [  # noqa: SLF001 - test fixture injection
            FmrEntry(
                path=Path("/tmp/fmr_rescale.csv"),
                sample="Sample A",
                frame=frame,
                units={
                    "Magnetic Field [Oe]": "Oe",
                    "Signal X [V]": "V",
                    "Signal Y [V]": "V",
                },
            )
        ]
        plugin._data = plugin._dataset  # noqa: SLF001 - loaded-state fixture
        plugin.generate()
        app.processEvents()

        axes = window._current_axes()  # noqa: SLF001 - shared navigation target
        assert axes is not None
        lines = [line for line in axes.get_lines() if bool(line.get_visible())]
        assert lines

        x_values: list[float] = []
        y_values: list[float] = []
        for line in lines:
            x_values.extend([float(value) for value in line.get_xdata()])
            y_values.extend([float(value) for value in line.get_ydata()])
        x_min = min(x_values)
        x_max = max(x_values)
        y_min = min(y_values)
        y_max = max(y_values)

        axes.set_xlim(x_min + 1.0, x_max - 1.0)
        axes.set_ylim(y_min + 0.2, y_max - 0.2)
        app.processEvents()

        window._rescale_current_axes("both")  # noqa: SLF001 - shared feature under test
        app.processEvents()

        xlim = axes.get_xlim()
        ylim = axes.get_ylim()
        assert xlim[0] <= x_min
        assert xlim[1] >= x_max
        assert ylim[0] <= y_min
        assert ylim[1] >= y_max
    finally:
        window.close()
        app.processEvents()


def test_shared_rescale_works_for_vsm_temperature_scan_graphs() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Temperature Scan")
    try:
        window._confirm_close_with_unsaved_data = lambda: True  # noqa: SLF001 - avoid modal close prompt
        plugin = getattr(window, "_current_plugin", None)
        assert plugin is not None
        frame = pd.DataFrame(
            {
                "temperature": [20.0, 30.0, 40.0, 50.0, 60.0],
                "field": [10000.0, 10000.0, 10000.0, 10000.0, 10000.0],
                "signal": [2.2e-4, 2.0e-4, 1.9e-4, 1.85e-4, 1.8e-4],
                "section_index": [0, 0, 0, 0, 0],
            }
        )
        plugin._dataset = [  # noqa: SLF001 - test fixture injection
            VSMTempEntry(path=Path("/tmp/vsm_temp_scan_rescale.txt"), sample="Sample", dataframe=frame)
        ]
        plugin.generate()
        app.processEvents()

        axes = window._current_axes()  # noqa: SLF001 - shared navigation target
        assert axes is not None
        axes.set_xlim(38.0, 42.0)
        axes.set_ylim(1.90e-4, 1.91e-4)

        window._rescale_current_axes("both")  # noqa: SLF001 - shared feature under test
        app.processEvents()

        x_limits = axes.get_xlim()
        y_limits = axes.get_ylim()
        assert x_limits[0] < 25.0 and x_limits[1] > 55.0
        assert y_limits[0] < 1.81e-4 and y_limits[1] > 2.19e-4
    finally:
        window.close()
        app.processEvents()


def test_navigation_mode_follows_active_graph_tab() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        first_tab = window.tab_widget.currentWidget()
        window._create_blank_graph()
        second_tab = window.tab_widget.currentWidget()
        assert isinstance(first_tab, QtWidgets.QWidget)
        assert isinstance(second_tab, QtWidgets.QWidget)
        assert first_tab is not second_tab

        window.tab_widget.setCurrentWidget(first_tab)
        app.processEvents()
        window._handle_zoom_triggered(True)  # noqa: SLF001 - shared toolbar hook
        app.processEvents()
        first_canvas = window._current_canvas()  # noqa: SLF001 - internal graph target
        assert first_canvas is not None
        assert getattr(window, "_nav_mode", None) == "zoom"  # noqa: SLF001

        window.tab_widget.setCurrentWidget(second_tab)
        app.processEvents()
        second_canvas = window._current_canvas()  # noqa: SLF001 - internal graph target
        assert second_canvas is not None
        assert second_canvas is not first_canvas
        assert getattr(window, "_nav_mode", None) == "zoom"  # noqa: SLF001
        assert getattr(window, "_nav_active_canvas", None) is second_canvas  # noqa: SLF001
        zoom_action = getattr(window, "_zoom_action", None)
        assert isinstance(zoom_action, QtGui.QAction)
        assert zoom_action.isChecked()
    finally:
        window.close()
        app.processEvents()


def test_vsm_temp_scan_smoothed_derivative_toggle_does_not_force_raw_derivative() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Temperature Scan")
    try:
        plugin = getattr(window, "_current_plugin", None)
        assert plugin is not None
        _ = plugin.settings_widget()
        derivative_cb = getattr(plugin, "_derivative_cb", None)
        smoothed_cb = getattr(plugin, "_smoothed_derivative_cb", None)
        assert isinstance(derivative_cb, QtWidgets.QCheckBox)
        assert isinstance(smoothed_cb, QtWidgets.QCheckBox)

        derivative_cb.setChecked(False)
        smoothed_cb.setChecked(True)

        assert bool(getattr(plugin._processor, "show_smoothed_derivative", False))  # noqa: SLF001
        assert not bool(getattr(plugin._processor, "show_derivative", False))  # noqa: SLF001
        assert not derivative_cb.isChecked()
    finally:
        window.close()
        app.processEvents()


def test_vsm_temp_scan_registers_smoothed_derivative_workbook_without_raw_derivative() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Temperature Scan")
    try:
        plugin = getattr(window, "_current_plugin", None)
        assert plugin is not None

        frame = pd.DataFrame(
            {
                "temperature": [20.0, 30.0, 40.0],
                "field": [10000.0, 10000.0, 10000.0],
                "signal": [2.0e-4, 1.8e-4, 1.6e-4],
                "section_index": [0, 0, 0],
            }
        )
        plugin._dataset = [  # noqa: SLF001 - test fixture injection
            VSMTempEntry(path=Path("/tmp/tscan_smoothed_only.txt"), sample="Sample", dataframe=frame)
        ]
        plugin._processor.set_show_derivative(False)  # noqa: SLF001
        plugin._processor.set_show_smoothed_derivative(True)  # noqa: SLF001
        plugin._processor.set_smooth_derivative(True)  # noqa: SLF001

        plugin._register_workbooks()  # noqa: SLF001 - exercise registration path

        managed = set(getattr(plugin, "_managed_workbooks", set()))  # noqa: SLF001
        assert any(key.endswith("::derivative_smoothed") for key in managed)
        assert not any(key.endswith("::derivative") for key in managed)
    finally:
        window.close()
        app.processEvents()


def test_vsm_temp_scan_project_state_restores_plot_options(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Temperature Scan")
    try:
        plugin = getattr(window, "_current_plugin", None)
        assert plugin is not None
        _ = plugin.settings_widget()

        split_cb = getattr(plugin, "_split_cb", None)
        combine_cb = getattr(plugin, "_combine_fields_cb", None)
        derivative_cb = getattr(plugin, "_derivative_cb", None)
        smoothed_derivative_cb = getattr(plugin, "_smoothed_derivative_cb", None)
        smoothed_cb = getattr(plugin, "_smooth_cb", None)
        overlay_cb = getattr(plugin, "_overlay_cb", None)
        median_spin = getattr(plugin, "_median_spin", None)
        ma_spin = getattr(plugin, "_ma_spin", None)
        deriv_median_spin = getattr(plugin, "_deriv_median_spin", None)
        deriv_ma_spin = getattr(plugin, "_deriv_ma_spin", None)
        assert isinstance(split_cb, QtWidgets.QCheckBox)
        assert isinstance(combine_cb, QtWidgets.QCheckBox)
        assert isinstance(derivative_cb, QtWidgets.QCheckBox)
        assert isinstance(smoothed_derivative_cb, QtWidgets.QCheckBox)
        assert isinstance(smoothed_cb, QtWidgets.QCheckBox)
        assert isinstance(overlay_cb, QtWidgets.QCheckBox)
        assert isinstance(median_spin, QtWidgets.QSpinBox)
        assert isinstance(ma_spin, QtWidgets.QSpinBox)
        assert isinstance(deriv_median_spin, QtWidgets.QSpinBox)
        assert isinstance(deriv_ma_spin, QtWidgets.QSpinBox)

        split_cb.setChecked(False)
        combine_cb.setChecked(True)
        derivative_cb.setChecked(False)
        smoothed_derivative_cb.setChecked(True)
        smoothed_cb.setChecked(True)
        overlay_cb.setChecked(True)
        median_spin.setValue(9)
        ma_spin.setValue(21)
        deriv_median_spin.setValue(7)
        deriv_ma_spin.setValue(31)
        app.processEvents()

        payload = window._build_project_payload(base_path=tmp_path)  # noqa: SLF001
        state = payload.get("active_plugin_state")
        assert isinstance(state, dict)
        assert state.get("combine_fields") is True
        assert state.get("split_directions") is False
        assert state.get("show_derivative") is False
        assert state.get("show_smoothed_derivative") is True
        assert state.get("show_smoothed_plot") is True
        assert state.get("show_overlay_derivative") is True
        assert state.get("median_window") == 9
        assert state.get("moving_avg_window") == 21
        assert state.get("derivative_median_window") == 7
        assert state.get("derivative_moving_avg_window") == 31
        plugin_state = {
            key: value
            for key, value in state.items()
            if key != window.PLUGIN_SHARED_STATE_KEY  # noqa: SLF001
        }

        split_cb.setChecked(True)
        combine_cb.setChecked(False)
        derivative_cb.setChecked(True)
        smoothed_derivative_cb.setChecked(False)
        smoothed_cb.setChecked(False)
        overlay_cb.setChecked(False)
        median_spin.setValue(3)
        ma_spin.setValue(5)
        deriv_median_spin.setValue(3)
        deriv_ma_spin.setValue(5)
        app.processEvents()

        plugin.restore_project_state(plugin_state, project_dir=tmp_path)
        app.processEvents()

        assert not bool(getattr(plugin._processor, "split_directions", True))  # noqa: SLF001
        assert bool(getattr(plugin._processor, "combine_fields", False))  # noqa: SLF001
        assert not bool(getattr(plugin._processor, "show_derivative", True))  # noqa: SLF001
        assert bool(getattr(plugin._processor, "show_smoothed_derivative", False))  # noqa: SLF001
        assert bool(getattr(plugin._processor, "show_smoothed_plot", False))  # noqa: SLF001
        assert bool(getattr(plugin._processor, "show_overlay_derivative", False))  # noqa: SLF001
        assert int(getattr(plugin._processor, "median_window", 0)) == 9  # noqa: SLF001
        assert int(getattr(plugin._processor, "moving_avg_window", 0)) == 21  # noqa: SLF001
        assert int(getattr(plugin._processor, "derivative_median_window", 0)) == 7  # noqa: SLF001
        assert int(getattr(plugin._processor, "derivative_moving_avg_window", 0)) == 31  # noqa: SLF001

        split_cb = getattr(plugin, "_split_cb", None)
        combine_cb = getattr(plugin, "_combine_fields_cb", None)
        derivative_cb = getattr(plugin, "_derivative_cb", None)
        smoothed_derivative_cb = getattr(plugin, "_smoothed_derivative_cb", None)
        smoothed_cb = getattr(plugin, "_smooth_cb", None)
        overlay_cb = getattr(plugin, "_overlay_cb", None)
        median_spin = getattr(plugin, "_median_spin", None)
        ma_spin = getattr(plugin, "_ma_spin", None)
        deriv_median_spin = getattr(plugin, "_deriv_median_spin", None)
        deriv_ma_spin = getattr(plugin, "_deriv_ma_spin", None)
        assert isinstance(split_cb, QtWidgets.QCheckBox) and not split_cb.isChecked()
        assert isinstance(combine_cb, QtWidgets.QCheckBox) and combine_cb.isChecked()
        assert isinstance(derivative_cb, QtWidgets.QCheckBox) and not derivative_cb.isChecked()
        assert (
            isinstance(smoothed_derivative_cb, QtWidgets.QCheckBox)
            and smoothed_derivative_cb.isChecked()
        )
        assert isinstance(smoothed_cb, QtWidgets.QCheckBox) and smoothed_cb.isChecked()
        assert isinstance(overlay_cb, QtWidgets.QCheckBox) and overlay_cb.isChecked()
        assert isinstance(median_spin, QtWidgets.QSpinBox) and median_spin.value() == 9
        assert isinstance(ma_spin, QtWidgets.QSpinBox) and ma_spin.value() == 21
        assert (
            isinstance(deriv_median_spin, QtWidgets.QSpinBox)
            and deriv_median_spin.value() == 7
        )
        assert isinstance(deriv_ma_spin, QtWidgets.QSpinBox) and deriv_ma_spin.value() == 31
    finally:
        window.close()
        app.processEvents()


def test_vsm_temp_scan_workbooks_support_outlier_detection_workflow() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Temperature Scan")
    try:
        window._confirm_close_with_unsaved_data = lambda: True  # noqa: SLF001 - avoid modal close prompt
        plugin = getattr(window, "_current_plugin", None)
        assert plugin is not None

        temperatures = list(range(30))
        frame = pd.DataFrame(
            {
                "temperature": temperatures,
                "field": [10000.0] * len(temperatures),
                "signal": ([2.0e-4] * 29) + [9.0e-4],
                "section_index": [0] * len(temperatures),
            }
        )
        plugin._dataset = [  # noqa: SLF001 - test fixture injection
            VSMTempEntry(path=Path("/tmp/tscan_outlier.txt"), sample="Outlier", dataframe=frame)
        ]
        plugin._processor.set_show_derivative(False)  # noqa: SLF001
        plugin._processor.set_show_smoothed_derivative(False)  # noqa: SLF001
        plugin._processor.set_show_smoothed(False)  # noqa: SLF001
        plugin._register_workbooks()  # noqa: SLF001 - produce worksheet data

        findings = window._collect_outlier_findings()  # noqa: SLF001 - shared outlier flow
        assert findings
        target = next(
            (
                finding
                for finding in findings
                if str(finding.workbook_name).startswith("Outlier (TScan)")
            ),
            None,
        )
        assert target is not None
        assert target.outlier_count >= 1
        assert 29 in target.row_indices

        removed = window._apply_outlier_findings([target])  # noqa: SLF001 - shared outlier flow
        assert removed >= 1

        workbook_key = next(
            key for key in window._workbooks.keys() if str(key).startswith("vsm_temp_scan::")
        )  # noqa: SLF001
        workbook = window._workbooks[workbook_key]  # noqa: SLF001
        assert workbook.worksheets
        worksheet = window._worksheets[workbook.worksheets[0]]  # noqa: SLF001
        assert len(worksheet.dataframe) == 29
    finally:
        window.close()
        app.processEvents()


def test_vsm_plugin_binds_static_project_helpers() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        fn = getattr(window, "_json_friendly", None)
        assert callable(fn)
        assert fn(float("inf")) is None
    finally:
        window.close()
        app.processEvents()


def test_vsm_plugin_keeps_host_owned_project_methods_on_workbench() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        build_payload = getattr(window, "_build_project_payload", None)
        apply_payload = getattr(window, "_apply_project_payload", None)
        assert callable(build_payload)
        assert callable(apply_payload)
        assert getattr(build_payload, "__func__", None) is PyPlotWorkbench._build_project_payload
        assert getattr(apply_payload, "__func__", None) is PyPlotWorkbench._apply_project_payload

        plot_metrics = getattr(window, "_plot_metrics_vs_angle", None)
        assert callable(plot_metrics)
        module_name = getattr(getattr(plot_metrics, "__func__", None), "__module__", "")
        assert module_name.endswith("plotting.plugins.vsm_hysteresis.vsm_hysteresis_loops")
    finally:
        window.close()
        app.processEvents()


def test_vsm_plugin_uses_shared_project_payload_format(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        payload = window._build_project_payload(base_path=tmp_path)  # noqa: SLF001
        assert payload.get("active_plugin") == "VSM Hysteresis Loops"
        state = payload.get("active_plugin_state")
        assert isinstance(state, dict)
        shared = state.get(window.PLUGIN_SHARED_STATE_KEY)  # noqa: SLF001
        assert isinstance(shared, dict)
        assert shared.get("auto_load_on_import") is True
        assert window.PROJECT_VERSION == PyPlotWorkbench.PROJECT_VERSION
    finally:
        window.close()
        app.processEvents()


def test_shared_project_payload_includes_connected_folders(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Hysteresis Loops")
    try:
        connected = tmp_path / "connected"
        connected.mkdir()
        window._connect_data_folders([connected])  # noqa: SLF001
        payload = window._build_project_payload(base_path=tmp_path)  # noqa: SLF001
        state = payload.get("active_plugin_state")
        assert isinstance(state, dict)
        shared = state.get(window.PLUGIN_SHARED_STATE_KEY)  # noqa: SLF001
        assert isinstance(shared, dict)
        connected_folders = shared.get("connected_folders")
        assert isinstance(connected_folders, list)
        assert connected.name in connected_folders[0]
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_connected_folders_do_not_restore_without_project_path(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="Hysteresis Loops")
    try:
        connected = tmp_path / "connected"
        connected.mkdir()
        window.settings.setValue(window._connected_folder_storage_key, [str(connected)])  # noqa: SLF001
        window._connected_data_folders = []  # noqa: SLF001
        window._restore_connected_folders()  # noqa: SLF001
        assert window._connected_data_folders == []  # noqa: SLF001
    finally:
        window.settings.remove(window._connected_folder_storage_key)  # noqa: SLF001
        window.close()
        app.processEvents()


def test_connected_folders_restore_from_project_local_cache_per_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    monkeypatch.setattr(QtWidgets.QApplication, "platformName", lambda *_args: "windows")
    window = PyPlotWorkbench(initial_plotter="Hysteresis Loops")
    try:
        project_one = tmp_path / "one.pypj"
        project_two = tmp_path / "two.pypj"
        folder_one = tmp_path / "folder_one"
        folder_two = tmp_path / "folder_two"
        folder_one.mkdir()
        folder_two.mkdir()

        window._project_path = project_one  # noqa: SLF001
        window._connected_data_folders = [folder_one]  # noqa: SLF001
        window._persist_connected_folders()  # noqa: SLF001

        window._project_path = project_two  # noqa: SLF001
        window._connected_data_folders = [folder_two]  # noqa: SLF001
        window._persist_connected_folders()  # noqa: SLF001

        window._project_path = project_one  # noqa: SLF001
        window._connected_data_folders = []  # noqa: SLF001
        window._restore_connected_folders(force_local=True)  # noqa: SLF001
        assert window._connected_data_folders == [folder_one]  # noqa: SLF001

        window._project_path = project_two  # noqa: SLF001
        window._connected_data_folders = []  # noqa: SLF001
        window._restore_connected_folders(force_local=True)  # noqa: SLF001
        assert window._connected_data_folders == [folder_two]  # noqa: SLF001
    finally:
        for project in (project_one, project_two):
            key = window._project_local_connected_folder_key(project)  # noqa: SLF001
            window.settings.remove(key)  # noqa: SLF001
        window.close()
        app.processEvents()


def test_register_imported_workbook_recovers_after_tree_clear() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._ensure_data_root()  # noqa: SLF001 - establish root first
        tree = getattr(window, "project_tree", None)
        assert isinstance(tree, QtWidgets.QTreeWidget)
        tree.clear()
        app.processEvents()

        import pandas as pd

        workbook = WorkbookData(key="wb::1", name="Workbook 1")
        worksheet = WorksheetData(
            key="wb::1::sheet",
            name="Sheet",
            dataframe=pd.DataFrame({"X": [1.0, 2.0]}),
            columns={"X": WorksheetColumnMeta(long_name="X")},
            workbook_key="wb::1",
        )
        window._register_imported_workbook(workbook, [worksheet])  # noqa: SLF001
        assert "wb::1" in window._workbooks  # noqa: SLF001
        assert "wb::1::sheet" in window._worksheets  # noqa: SLF001
    finally:
        window.close()
        app.processEvents()


def test_save_graph_works_with_mdi_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        out_path = tmp_path / "saved_graph.png"
        monkeypatch.setattr(
            QtWidgets.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(out_path), "PNG Image (*.png)"),
        )
        assert window._save_graph_for_current_tab()
        assert out_path.exists()
    finally:
        window.close()
        app.processEvents()


def test_current_axes_and_canvas_resolve_with_mdi_proxy() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = window._current_axes()
        canvas = window._current_canvas()
        assert axes is not None
        assert canvas is not None
    finally:
        window.close()
        app.processEvents()


def test_single_mdi_subwindow_fills_viewport_after_arrange() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(1500, 950)
        window._create_blank_graph()
        app.processEvents()
        tab_proxy = window.tab_widget
        mdi = getattr(tab_proxy, "_mdi", None)
        assert isinstance(mdi, QtWidgets.QMdiArea)
        tab_proxy._arrange_subwindows()  # noqa: SLF001 - exercising internal layout logic
        app.processEvents()
        sub = mdi.activeSubWindow()
        assert isinstance(sub, QtWidgets.QMdiSubWindow)
        viewport = mdi.viewport().rect()
        margin = getattr(tab_proxy, "_layout_margin", 6)
        assert sub.geometry().width() >= viewport.width() - margin * 2
        assert sub.geometry().height() >= viewport.height() - margin * 2
    finally:
        window.close()
        app.processEvents()


def test_single_mdi_subwindow_normalizes_after_activation_helper() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(1500, 950)
        window._create_blank_graph()
        app.processEvents()
        tab_proxy = window.tab_widget
        mdi = getattr(tab_proxy, "_mdi", None)
        assert isinstance(mdi, QtWidgets.QMdiArea)
        sub = mdi.activeSubWindow()
        assert isinstance(sub, QtWidgets.QMdiSubWindow)
        sub.setGeometry(40, 40, 320, 700)
        app.processEvents()
        window._normalize_single_visible_graph_subwindow()  # noqa: SLF001 - test hook
        app.processEvents()
        viewport = mdi.viewport().rect()
        margin = getattr(tab_proxy, "_layout_margin", 6)
        assert sub.geometry().width() >= viewport.width() - margin * 2
    finally:
        window.close()
        app.processEvents()


def test_hidden_subwindow_restore_keeps_manual_geometry_offscreen() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(1500, 950)
        window._create_blank_graph()
        window._create_blank_graph()
        app.processEvents()

        tab_proxy = window.tab_widget
        set_mode = getattr(tab_proxy, "set_arrangement_mode", None)
        assert callable(set_mode)
        set_mode("cascade")

        first_tab = tab_proxy.widget(0)
        second_tab = tab_proxy.widget(1)
        assert isinstance(first_tab, QtWidgets.QWidget)
        assert isinstance(second_tab, QtWidgets.QWidget)

        subwindow_for = getattr(tab_proxy, "_subwindow_for", None)
        assert callable(subwindow_for)
        first_sub = subwindow_for(first_tab)
        assert isinstance(first_sub, QtWidgets.QMdiSubWindow)

        expected = QtCore.QRect(120, 140, 640, 420)
        tab_proxy.setCurrentWidget(first_tab)
        app.processEvents()
        first_sub.setGeometry(expected)
        app.processEvents()
        before = first_sub.geometry()
        assert before.x() == expected.x()
        assert before.y() == expected.y()
        assert before.width() == expected.width()
        assert before.height() == expected.height()

        tab_proxy.set_max_visible_windows(1)
        tab_proxy.setCurrentWidget(second_tab)
        app.processEvents()
        tab_proxy.setCurrentWidget(first_tab)
        app.processEvents()

        restored = first_sub.geometry()
        assert restored.x() == expected.x()
        assert restored.y() == expected.y()
        assert restored.width() == expected.width()
        assert restored.height() == expected.height()
    finally:
        window.close()
        app.processEvents()


def test_primary_dock_target_width_scales_on_large_window() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(PRIMARY_DOCK_EXPAND_THRESHOLD + 1200, 950)
        app.processEvents()
        dock = getattr(window, "project_dock", None)
        assert isinstance(dock, QtWidgets.QDockWidget)
        target = window._primary_dock_target_width(  # noqa: SLF001 - internal sizing helper
            dock,
            PRIMARY_DOCK_DEFAULT_WIDTH,
        )
        expected_min = int(window.width() * PRIMARY_DOCK_EXPANDED_FRACTION)
        if PRIMARY_DOCK_EXPANDED_MAX > 0:
            expected_min = min(expected_min, PRIMARY_DOCK_EXPANDED_MAX)
        assert target >= expected_min
    finally:
        window.close()
        app.processEvents()


def test_primary_dock_target_width_caps_large_persisted_values() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(PRIMARY_DOCK_EXPAND_THRESHOLD + 1200, 950)
        app.processEvents()
        dock = getattr(window, "project_dock", None)
        assert isinstance(dock, QtWidgets.QDockWidget)
        window._primary_dock_widths[dock] = 5000  # noqa: SLF001 - internal sizing helper
        target = window._primary_dock_target_width(  # noqa: SLF001 - internal sizing helper
            dock,
            PRIMARY_DOCK_DEFAULT_WIDTH,
        )
        expected_max = int(window.width() * PRIMARY_DOCK_MAX_FRACTION)
        if PRIMARY_DOCK_EXPANDED_MAX > 0:
            expected_max = min(expected_max, PRIMARY_DOCK_EXPANDED_MAX)
        assert target <= expected_max
    finally:
        window.close()
        app.processEvents()


def test_status_bar_cursor_readout_stays_visible_during_progress_on_narrow_window() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(720, 520)
        app.processEvents()
        window._begin_task_progress(  # noqa: SLF001 - internal status-bar helper
            "Loading large dataset...",
            maximum=10,
            value=1,
        )
        app.processEvents()

        status = window.statusBar()
        cursor_label = window._cursor_label  # noqa: SLF001 - internal status-bar helper
        progress_label = window._task_progress_label  # noqa: SLF001 - internal status-bar helper
        progress_bar = window._task_progress_bar  # noqa: SLF001 - internal status-bar helper
        progress_dialog = window._task_progress_dialog  # noqa: SLF001 - internal progress helper
        assert isinstance(status, QtWidgets.QStatusBar)
        assert isinstance(cursor_label, QtWidgets.QLabel)
        assert isinstance(progress_label, QtWidgets.QLabel)
        assert isinstance(progress_bar, QtWidgets.QProgressBar)
        assert isinstance(progress_dialog, QtWidgets.QDialog)
        assert progress_dialog.isVisible()
        assert cursor_label.minimumWidth() >= 120
        assert progress_bar.value() == 1

        window._update_cursor_status(  # noqa: SLF001 - internal status-bar helper
            SimpleNamespace(inaxes=object(), xdata=1234.567, ydata=-9876.543)
        )
        text = cursor_label.text()
        assert text
        assert "x:" in text or "," in text
    finally:
        window._end_task_progress()  # noqa: SLF001 - internal status-bar helper
        window.close()
        app.processEvents()


def test_project_explorer_defaults_to_elided_readable_columns() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        tree = window.project_tree  # noqa: SLF001 - UI fixture
        header = tree.header()
        assert tree.textElideMode() == QtCore.Qt.TextElideMode.ElideMiddle
        assert tree.alternatingRowColors()
        assert header.sectionResizeMode(0) == QtWidgets.QHeaderView.ResizeMode.Interactive
        assert header.sectionResizeMode(1) == QtWidgets.QHeaderView.ResizeMode.Stretch
    finally:
        window.close()
        app.processEvents()


def test_project_explorer_hides_imported_data_root_until_data_is_loaded() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        tree = window.project_tree  # noqa: SLF001 - UI fixture
        names = [tree.topLevelItem(index).text(0) for index in range(tree.topLevelItemCount())]
        assert "Imported Data" not in names
    finally:
        window.close()
        app.processEvents()


def test_project_explorer_search_filters_tree_items() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        tree = window.project_tree  # noqa: SLF001 - UI fixture
        search = window.project_tree_search  # noqa: SLF001 - UI fixture
        assert isinstance(search, QtWidgets.QLineEdit)

        root = QtWidgets.QTreeWidgetItem(["Alpha workbook", ""])
        child = QtWidgets.QTreeWidgetItem(["Beta sheet", "rows x columns"])
        root.addChild(child)
        root.setExpanded(True)
        tree.addTopLevelItem(root)

        other = QtWidgets.QTreeWidgetItem(["Gamma workbook", ""])
        tree.addTopLevelItem(other)
        app.processEvents()

        search.setText("beta")
        app.processEvents()
        assert not root.isHidden()
        assert not child.isHidden()
        assert other.isHidden()

        search.setText("rows x")
        app.processEvents()
        assert not child.isHidden()
        assert not root.isHidden()

        search.setText("")
        app.processEvents()
        assert not root.isHidden()
        assert not child.isHidden()
        assert not other.isHidden()
    finally:
        window.close()
        app.processEvents()


def test_project_explorer_search_applies_to_new_rows_while_active() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        tree = window.project_tree  # noqa: SLF001 - UI fixture
        search = window.project_tree_search  # noqa: SLF001 - UI fixture
        assert isinstance(search, QtWidgets.QLineEdit)

        search.setText("alpha")
        app.processEvents()

        hidden_item = QtWidgets.QTreeWidgetItem(["Gamma workbook", ""])
        visible_item = QtWidgets.QTreeWidgetItem(["Alpha workbook", ""])
        tree.addTopLevelItem(hidden_item)
        tree.addTopLevelItem(visible_item)
        app.processEvents()

        assert hidden_item.isHidden()
        assert not visible_item.isHidden()
    finally:
        window.close()
        app.processEvents()


def test_apply_graph_format_supports_tick_increment_and_count() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = next(iter(window._axes_by_tab.values()))  # noqa: SLF001 - test hook
        assert axes is not None
        axes.plot([0.0, 50.0, 100.0], [0.0, 5.0, 10.0], label="Series")

        controls = window._graph_format_controls
        assert isinstance(controls.get("x_tick_mode_combo"), QtWidgets.QComboBox)
        assert isinstance(controls.get("y_tick_mode_combo"), QtWidgets.QComboBox)
        assert isinstance(controls.get("x_tick_step_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("y_tick_count_spin"), QtWidgets.QSpinBox)

        controls["x_tick_mode_combo"].setCurrentIndex(
            controls["x_tick_mode_combo"].findData("step")
        )
        controls["x_tick_step_edit"].setText("25")
        controls["y_tick_mode_combo"].setCurrentIndex(
            controls["y_tick_mode_combo"].findData("count")
        )
        controls["y_tick_count_spin"].setValue(4)

        window._apply_graph_format(apply_all=False)

        x_ticks = axes.get_xticks()
        assert len(x_ticks) >= 3
        assert x_ticks[1] - x_ticks[0] == pytest.approx(25.0)
        assert axes.yaxis.get_major_locator().__class__.__name__ == "MaxNLocator"
    finally:
        window.close()
        app.processEvents()


def test_graph_format_can_toggle_title_and_axis_label_visibility() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = next(iter(window._axes_by_tab.values()))  # noqa: SLF001 - test hook
        assert axes is not None

        controls = window._graph_format_controls
        assert isinstance(controls.get("title_visible_cb"), QtWidgets.QCheckBox)
        assert isinstance(controls.get("x_label_visible_cb"), QtWidgets.QCheckBox)
        assert isinstance(controls.get("y_label_visible_cb"), QtWidgets.QCheckBox)
        assert isinstance(controls.get("title_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("x_label_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("y_label_edit"), QtWidgets.QLineEdit)

        controls["title_edit"].setText("Hidden Title")
        controls["x_label_edit"].setText("Hidden X")
        controls["y_label_edit"].setText("Hidden Y")
        controls["title_visible_cb"].setChecked(False)
        controls["x_label_visible_cb"].setChecked(False)
        controls["y_label_visible_cb"].setChecked(False)
        window._apply_graph_format(apply_all=False)

        assert axes.get_title() == "Hidden Title"
        assert axes.get_xlabel() == "Hidden X"
        assert axes.get_ylabel() == "Hidden Y"
        assert not axes.title.get_visible()
        assert not axes.xaxis.label.get_visible()
        assert not axes.yaxis.label.get_visible()

        controls["title_visible_cb"].setChecked(True)
        controls["x_label_visible_cb"].setChecked(True)
        controls["y_label_visible_cb"].setChecked(True)
        window._apply_graph_format(apply_all=False)
        assert axes.title.get_visible()
        assert axes.xaxis.label.get_visible()
        assert axes.yaxis.label.get_visible()
    finally:
        window.close()
        app.processEvents()


def test_registered_plot_tab_allows_canvas_to_shrink_without_scrollbars() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        canvas = window._current_canvas()
        assert canvas is not None
        assert tab.minimumWidth() == 0
        assert tab.minimumHeight() == 0
        assert canvas.minimumWidth() == 0
        assert canvas.minimumHeight() == 0
    finally:
        window.close()
        app.processEvents()


def test_apply_graph_format_sets_figure_dimensions_and_axes_aspect() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = next(iter(window._axes_by_tab.values()))  # noqa: SLF001 - test hook
        assert axes is not None
        axes.plot([0.0, 1.0, 2.0], [0.0, 1.0, 4.0], label="Series")

        controls = window._graph_format_controls
        assert isinstance(controls.get("figure_width_spin"), QtWidgets.QDoubleSpinBox)
        assert isinstance(controls.get("figure_height_spin"), QtWidgets.QDoubleSpinBox)
        assert isinstance(controls.get("figure_width_auto_cb"), QtWidgets.QCheckBox)
        assert isinstance(controls.get("figure_height_auto_cb"), QtWidgets.QCheckBox)
        assert isinstance(controls.get("axes_aspect_combo"), QtWidgets.QComboBox)
        assert isinstance(controls.get("axes_aspect_ratio_spin"), QtWidgets.QDoubleSpinBox)

        controls["figure_width_auto_cb"].setChecked(False)
        controls["figure_height_auto_cb"].setChecked(False)
        controls["figure_width_spin"].setValue(203.2)  # 8.0 in
        controls["figure_height_spin"].setValue(127.0)  # 5.0 in
        controls["axes_aspect_combo"].setCurrentIndex(
            controls["axes_aspect_combo"].findData("custom")
        )
        controls["axes_aspect_ratio_spin"].setValue(1.5)
        window._apply_graph_format(apply_all=False)

        figure = axes.figure
        assert figure is not None
        width, height = figure.get_size_inches()
        assert width == pytest.approx(8.0, rel=1e-2)
        assert height == pytest.approx(5.0, rel=1e-2)
        assert float(axes.get_aspect()) == pytest.approx(1.5)
    finally:
        window.close()
        app.processEvents()


def test_canvas_resize_scales_display_dpi_but_preserves_figure_inches() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(1200, 780)
        window.show()
        app.processEvents()
        window._create_blank_graph()
        app.processEvents()

        canvas = window._current_canvas()
        axes = window._current_axes()
        assert canvas is not None
        assert axes is not None
        figure = axes.figure
        assert figure is not None

        base_width, base_height = figure.get_size_inches()
        baseline_dpi = float(getattr(figure, "dpi", 100.0) or 100.0)
        baseline_canvas_width = int(canvas.width())
        baseline_canvas_height = int(canvas.height())

        window.resize(1800, 1180)
        app.processEvents()
        app.processEvents()

        expanded_width, expanded_height = figure.get_size_inches()
        expanded_dpi = float(getattr(figure, "dpi", baseline_dpi) or baseline_dpi)
        expanded_canvas_width = int(canvas.width())
        expanded_canvas_height = int(canvas.height())
        assert expanded_width == pytest.approx(base_width, rel=1e-3)
        assert expanded_height == pytest.approx(base_height, rel=1e-3)
        assert expanded_dpi > baseline_dpi
        assert expanded_canvas_width >= baseline_canvas_width
        assert expanded_canvas_height >= baseline_canvas_height

        window.resize(980, 680)
        app.processEvents()
        app.processEvents()

        shrunk_width, shrunk_height = figure.get_size_inches()
        shrunk_dpi = float(getattr(figure, "dpi", expanded_dpi) or expanded_dpi)
        assert shrunk_width == pytest.approx(base_width, rel=1e-3)
        assert shrunk_height == pytest.approx(base_height, rel=1e-3)
        assert shrunk_dpi < expanded_dpi
    finally:
        window.close()
        app.processEvents()


def test_mdi_subwindow_resize_updates_embedded_canvas_geometry() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(1280, 820)
        window.show()
        app.processEvents()
        window._create_blank_graph()
        app.processEvents()

        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        canvas = window._current_canvas()
        assert canvas is not None
        subwindow_for = getattr(window.tab_widget, "_subwindow_for", None)
        fitter = getattr(window.tab_widget, "_fit_subwindow", None)
        assert callable(subwindow_for)
        assert callable(fitter)
        sub = subwindow_for(tab)
        assert isinstance(sub, QtWidgets.QMdiSubWindow)

        baseline_canvas_width = int(canvas.width())
        baseline_canvas_height = int(canvas.height())
        baseline_sub_width = int(sub.width())
        baseline_sub_height = int(sub.height())
        fitter(sub, use_half_width=False, preferred_width=max(520, int(sub.width() * 1.45)))
        app.processEvents()
        app.processEvents()

        grown_canvas_width = int(canvas.width())
        grown_canvas_height = int(canvas.height())
        grown_sub_width = int(sub.width())
        grown_sub_height = int(sub.height())
        assert grown_canvas_width != baseline_canvas_width or grown_canvas_height != baseline_canvas_height
        assert (grown_canvas_width - baseline_canvas_width) * (grown_sub_width - baseline_sub_width) >= 0
        assert (grown_canvas_height - baseline_canvas_height) * (grown_sub_height - baseline_sub_height) >= 0
    finally:
        window.close()
        app.processEvents()


def test_apply_graph_format_dimensions_remain_fixed_after_window_resize() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.resize(1300, 860)
        window.show()
        app.processEvents()
        window._create_blank_graph()
        app.processEvents()
        axes = next(iter(window._axes_by_tab.values()))  # noqa: SLF001 - test hook
        assert axes is not None

        controls = window._graph_format_controls
        assert isinstance(controls.get("figure_width_spin"), QtWidgets.QDoubleSpinBox)
        assert isinstance(controls.get("figure_height_spin"), QtWidgets.QDoubleSpinBox)
        assert isinstance(controls.get("figure_width_auto_cb"), QtWidgets.QCheckBox)
        assert isinstance(controls.get("figure_height_auto_cb"), QtWidgets.QCheckBox)
        controls["figure_width_auto_cb"].setChecked(False)
        controls["figure_height_auto_cb"].setChecked(False)
        controls["figure_width_spin"].setValue(203.2)  # 8.0 in
        controls["figure_height_spin"].setValue(127.0)  # 5.0 in
        window._apply_graph_format(apply_all=False)
        app.processEvents()

        figure = axes.figure
        target_width, target_height = figure.get_size_inches()
        assert target_width == pytest.approx(8.0, rel=1e-2)
        assert target_height == pytest.approx(5.0, rel=1e-2)

        window.resize(1820, 1200)
        app.processEvents()
        app.processEvents()

        window.resize(960, 660)
        app.processEvents()
        app.processEvents()

        resized_width, resized_height = figure.get_size_inches()
        assert resized_width == pytest.approx(target_width, rel=1e-3)
        assert resized_height == pytest.approx(target_height, rel=1e-3)
        assert resized_width / resized_height == pytest.approx(
            target_width / target_height,
            rel=1e-4,
        )
    finally:
        window.close()
        app.processEvents()


def test_apply_graph_format_supports_axis_value_factor_and_unit_reflection() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = next(iter(window._axes_by_tab.values()))  # noqa: SLF001 - test hook
        assert axes is not None
        axes.plot([0.0, 500.0, 1000.0], [0.0, 2.0, 4.0], label="Series")

        controls = window._graph_format_controls
        assert isinstance(controls.get("x_label_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("y_label_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("x_value_factor_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("y_value_factor_edit"), QtWidgets.QLineEdit)
        assert isinstance(controls.get("reflect_x_scale_units_cb"), QtWidgets.QCheckBox)
        assert isinstance(controls.get("reflect_y_scale_units_cb"), QtWidgets.QCheckBox)

        controls["x_label_edit"].setText("Field [Oe]")
        controls["y_label_edit"].setText("Signal [V]")
        controls["x_value_factor_edit"].setText("10^-3")
        controls["y_value_factor_edit"].setText("2")
        controls["reflect_x_scale_units_cb"].setChecked(True)
        controls["reflect_y_scale_units_cb"].setChecked(True)
        window._apply_graph_format(apply_all=False)

        assert axes.get_xlabel() == "Field [Oe \u00d710\u00b3]"
        assert axes.get_ylabel() == "Signal [V 0.5]"
        x_formatter = axes.xaxis.get_major_formatter()
        y_formatter = axes.yaxis.get_major_formatter()
        assert isinstance(x_formatter, mticker.FuncFormatter)
        assert isinstance(y_formatter, mticker.FuncFormatter)
        assert x_formatter(1000.0, 0) == "1"
        assert y_formatter(2.5, 0) == "5"

        controls["x_value_factor_edit"].setText("1")
        controls["y_value_factor_edit"].setText("1")
        controls["reflect_x_scale_units_cb"].setChecked(False)
        controls["reflect_y_scale_units_cb"].setChecked(False)
        window._apply_graph_format(apply_all=False)
        assert axes.get_xlabel() == "Field [Oe]"
        assert axes.get_ylabel() == "Signal [V]"
    finally:
        window.close()
        app.processEvents()


def test_graph_format_dialog_footer_buttons_stay_outside_scroll_area() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._open_graph_format_dialog()  # noqa: SLF001 - test hook
        dialog = window._graph_format_dialog  # noqa: SLF001 - test hook
        assert isinstance(dialog, QtWidgets.QDialog)
        panel = window._graph_format_controls.get("button_panel")  # noqa: SLF001 - test hook
        assert isinstance(panel, QtWidgets.QWidget)
        assert panel.parentWidget() is dialog
        layout = dialog.layout()
        assert isinstance(layout, QtWidgets.QVBoxLayout)
        assert layout.indexOf(panel) >= 0
    finally:
        window.close()
        app.processEvents()


def test_save_graph_remembers_last_selected_export_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        first_out = tmp_path / "graph_first"
        monkeypatch.setattr(
            QtWidgets.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(first_out), "PDF Document (*.pdf)"),
        )
        assert window._save_graph_for_current_tab()  # noqa: SLF001 - exercised public flow
        assert (tmp_path / "graph_first.pdf").exists()

        captured: dict[str, str] = {}

        def _capture_dialog(*args, **kwargs):
            captured["suggested_path"] = str(args[2]) if len(args) > 2 else ""
            captured["selected_filter"] = str(args[4]) if len(args) > 4 else ""
            return "", ""

        monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", _capture_dialog)
        assert not window._save_graph_for_current_tab()  # noqa: SLF001
        assert captured.get("suggested_path", "").endswith(".pdf")
        assert captured.get("selected_filter") == "PDF Document (*.pdf)"
        assert window._last_graph_format == ".pdf"  # noqa: SLF001 - persisted state
    finally:
        window.close()
        app.processEvents()


def test_double_click_title_routes_to_shared_graph_format_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001 - test hook
        assert descriptor is not None
        axes = descriptor.axes
        canvas = descriptor.canvas
        assert axes is not None
        assert canvas is not None
        axes.set_title("Clickable Title")
        canvas.draw()

        renderer = canvas.get_renderer()
        bbox = axes.title.get_window_extent(renderer=renderer)
        called: dict[str, object] = {}

        def _capture(*, axes, text_field=None, axis=None, legend=False):
            called["axes"] = axes
            called["text_field"] = text_field
            called["axis"] = axis
            called["legend"] = legend
            return True

        monkeypatch.setattr(window, "_open_shared_graph_format_from_double_click", _capture)
        event = SimpleNamespace(
            dblclick=True,
            inaxes=None,
            x=float(bbox.x0 + bbox.width * 0.5),
            y=float(bbox.y0 + bbox.height * 0.5),
            canvas=canvas,
        )
        window._handle_canvas_button_press(event)  # noqa: SLF001 - event hook
        assert called.get("axes") is axes
        assert called.get("text_field") == "title"
        assert called.get("axis") is None
    finally:
        window.close()
        app.processEvents()


def test_double_click_axis_falls_back_to_axis_dialog_when_shared_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001 - test hook
        assert descriptor is not None
        axes = descriptor.axes
        canvas = descriptor.canvas
        assert axes is not None
        assert canvas is not None

        monkeypatch.setattr(
            window,
            "_open_shared_graph_format_from_double_click",
            lambda **_: False,
        )
        called: dict[str, object] = {}

        def _capture(target_axes, axis):
            called["axes"] = target_axes
            called["axis"] = axis

        monkeypatch.setattr(window, "_edit_axis_scale_from_double_click", _capture)
        monkeypatch.setattr(window, "_artist_hit", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(window, "_axis_from_event_hit", lambda *_args, **_kwargs: "x")
        event = SimpleNamespace(
            dblclick=True,
            inaxes=axes,
            x=0.0,
            y=0.0,
            canvas=canvas,
        )
        window._handle_canvas_button_press(event)  # noqa: SLF001 - event hook
        assert called.get("axes") is axes
        assert called.get("axis") == "x"
    finally:
        window.close()
        app.processEvents()


def test_double_click_axis_routes_to_shared_graph_format_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001 - test hook
        assert descriptor is not None
        axes = descriptor.axes
        canvas = descriptor.canvas
        assert axes is not None
        assert canvas is not None

        called: dict[str, object] = {}

        def _capture(*, axes, text_field=None, axis=None, legend=False):
            called["axes"] = axes
            called["text_field"] = text_field
            called["axis"] = axis
            called["legend"] = legend
            return True

        monkeypatch.setattr(window, "_open_shared_graph_format_from_double_click", _capture)
        monkeypatch.setattr(window, "_artist_hit", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(window, "_axis_from_event_hit", lambda *_args, **_kwargs: "y")
        event = SimpleNamespace(
            dblclick=True,
            inaxes=axes,
            x=0.0,
            y=0.0,
            canvas=canvas,
        )
        window._handle_canvas_button_press(event)  # noqa: SLF001 - event hook
        assert called.get("axes") is axes
        assert called.get("text_field") is None
        assert called.get("axis") == "y"
    finally:
        window.close()
        app.processEvents()


def test_graph_formatting_section_opens_movable_dialog() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        anchor = window._graph_format_anchor_section  # noqa: SLF001 - test hook
        assert isinstance(anchor, QtWidgets.QWidget)
        window._set_graph_settings_anchor(anchor)  # noqa: SLF001 - test hook

        dialog = window._graph_format_dialog  # noqa: SLF001 - test hook
        assert isinstance(dialog, QtWidgets.QDialog)
        assert dialog.isVisible()
        assert bool(dialog.windowFlags() & QtCore.Qt.WindowType.Window)
    finally:
        window.close()
        app.processEvents()


def test_graph_formatting_section_button_uses_dialog_not_popup_menu() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        button = next(
            (
                candidate
                for candidate in window._graph_section_buttons  # noqa: SLF001 - test hook
                if candidate.text().strip().lower() == "graph formatting"
            ),
            None,
        )
        assert isinstance(button, QtWidgets.QToolButton)
        assert button.menu() is None
    finally:
        window.close()
        app.processEvents()


def test_unit_labels_normalize_parentheses_to_brackets_on_register() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001 - test hook
        assert descriptor is not None
        axes = descriptor.axes
        assert axes is not None
        axes.set_xlabel("Temperature (°C)")
        axes.set_ylabel("Strain (%)")

        # Re-run the shared normalizer to mirror plugin-registration behavior.
        window._normalize_axes_unit_labels(axes, descriptor=descriptor)  # noqa: SLF001

        assert axes.get_xlabel() == "Temperature [°C]"
        assert axes.get_ylabel() == "Strain [%]"
    finally:
        window.close()
        app.processEvents()


def test_object_manager_lists_line_items_for_each_axes() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        assert window._axes_by_tab  # noqa: SLF001 - test hook
        tab, axes = next(iter(window._axes_by_tab.items()))  # noqa: SLF001 - test hook
        assert axes is not None
        secondary = axes.twinx()
        axes.plot([0.0, 1.0], [1.0, 2.0], label="Left axis")
        secondary.plot([0.0, 1.0], [2.0, 3.0], label="Right axis")

        index = window.tab_widget.indexOf(tab)
        if index >= 0:
            window.tab_widget.setCurrentIndex(index)
        window._rebuild_object_manager_for_tab(tab)

        tree = getattr(window, "object_tree", None)
        assert isinstance(tree, QtWidgets.QTreeWidget)
        root = tree.topLevelItem(0)
        assert root is not None

        labels: list[str] = []

        def _collect(item: QtWidgets.QTreeWidgetItem) -> None:
            for idx in range(item.childCount()):
                child = item.child(idx)
                data = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("kind") == "line":
                    labels.append(child.text(0))
                _collect(child)

        _collect(root)
        assert set(labels) >= {"Left axis", "Right axis"}
    finally:
        window.close()
        app.processEvents()


def test_object_manager_lists_line_items_when_legend_exists() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        assert window._axes_by_tab  # noqa: SLF001 - test hook
        tab, axes = next(iter(window._axes_by_tab.items()))  # noqa: SLF001 - test hook
        assert axes is not None
        axes.plot([0.0, 1.0], [1.0, 2.0], label="Series A")
        axes.plot([0.0, 1.0], [2.0, 3.0], label="Series B")
        axes.legend(loc="best")

        index = window.tab_widget.indexOf(tab)
        if index >= 0:
            window.tab_widget.setCurrentIndex(index)
        window._rebuild_object_manager_for_tab(tab)

        tree = getattr(window, "object_tree", None)
        assert isinstance(tree, QtWidgets.QTreeWidget)
        root = tree.topLevelItem(0)
        assert root is not None

        labels: list[str] = []

        def _collect(item: QtWidgets.QTreeWidgetItem) -> None:
            for idx in range(item.childCount()):
                child = item.child(idx)
                data = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("kind") == "line":
                    labels.append(child.text(0))
                _collect(child)

        _collect(root)
        assert set(labels) >= {"Series A", "Series B"}
    finally:
        window.close()
        app.processEvents()


def test_object_manager_line_hide_updates_line_visibility_and_legend() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        assert window._axes_by_tab  # noqa: SLF001 - test hook
        tab, axes = next(iter(window._axes_by_tab.items()))  # noqa: SLF001 - test hook
        assert axes is not None
        line_a = axes.plot([0.0, 1.0], [1.0, 2.0], label="Visible A")[0]
        _ = axes.plot([0.0, 1.0], [2.0, 3.0], label="Hide B")[0]
        axes.legend(loc="best")

        index = window.tab_widget.indexOf(tab)
        if index >= 0:
            window.tab_widget.setCurrentIndex(index)
        window._rebuild_object_manager_for_tab(tab)

        tree = getattr(window, "object_tree", None)
        assert isinstance(tree, QtWidgets.QTreeWidget)
        root = tree.topLevelItem(0)
        assert root is not None

        target_item: QtWidgets.QTreeWidgetItem | None = None

        def _find_line(item: QtWidgets.QTreeWidgetItem) -> QtWidgets.QTreeWidgetItem | None:
            for idx in range(item.childCount()):
                child = item.child(idx)
                data = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("kind") == "line" and child.text(0) == "Hide B":
                    return child
                found = _find_line(child)
                if found is not None:
                    return found
            return None

        target_item = _find_line(root)
        assert target_item is not None
        target_item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
        app.processEvents()

        lines = list(axes.get_lines())
        hidden_line = next((line for line in lines if line.get_label() == "Hide B"), None)
        assert hidden_line is not None
        assert not bool(hidden_line.get_visible())
        assert bool(line_a.get_visible())

        legend = axes.get_legend()
        assert legend is not None
        labels = [text.get_text() for text in legend.get_texts()]
        assert "Visible A" in labels
        assert "Hide B" not in labels
    finally:
        window.close()
        app.processEvents()


def test_object_manager_legend_hide_updates_visibility() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab, axes = next(iter(window._axes_by_tab.items()))  # noqa: SLF001 - test hook
        assert axes is not None
        axes.plot([0.0, 1.0], [1.0, 2.0], label="Series A")
        legend = axes.legend(loc="best")
        assert legend is not None

        index = window.tab_widget.indexOf(tab)
        if index >= 0:
            window.tab_widget.setCurrentIndex(index)
        window._rebuild_object_manager_for_tab(tab)

        tree = window.object_tree  # noqa: SLF001 - UI fixture
        assert isinstance(tree, QtWidgets.QTreeWidget)
        root = tree.topLevelItem(0)
        assert root is not None

        legend_item: QtWidgets.QTreeWidgetItem | None = None

        def _find_legend(item: QtWidgets.QTreeWidgetItem) -> QtWidgets.QTreeWidgetItem | None:
            for idx in range(item.childCount()):
                child = item.child(idx)
                data = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("kind") == "legend":
                    return child
                found = _find_legend(child)
                if found is not None:
                    return found
            return None

        legend_item = _find_legend(root)
        assert legend_item is not None
        legend_item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
        app.processEvents()

        legend = axes.get_legend()
        assert legend is not None
        assert not bool(legend.get_visible())
    finally:
        window.close()
        app.processEvents()


def test_graph_format_dialog_uses_fixed_tab_bar_with_per_tab_scroll() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        assert window._open_graph_format_dialog()  # noqa: SLF001
        app.processEvents()

        dialog = getattr(window, "_graph_format_dialog", None)
        assert isinstance(dialog, QtWidgets.QDialog)
        tabs = window._control_widget("format_tabs")  # noqa: SLF001
        assert isinstance(tabs, QtWidgets.QTabWidget)
        assert tabs.tabBar().isVisible()
        assert dialog.findChild(QtWidgets.QScrollArea, "mw_graph_format_dialog_scroll") is None
    finally:
        dialog = getattr(window, "_graph_format_dialog", None)
        if isinstance(dialog, QtWidgets.QDialog):
            dialog.close()
        window.close()
        app.processEvents()


def test_double_click_line_opens_shared_graph_format_line_controls() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        axes = window._current_axes()
        assert axes is not None

        called: dict[str, object] = {}

        def _fake_open_graph_format_dialog(
            checked: bool = False,
            *,
            focus_key: str | None = None,
            select_all: bool = False,
        ) -> bool:
            called["checked"] = checked
            called["focus_key"] = focus_key
            called["select_all"] = select_all
            return True

        window._open_graph_format_dialog = _fake_open_graph_format_dialog  # type: ignore[assignment]
        opened = window._open_shared_graph_format_from_double_click(  # noqa: SLF001
            axes=axes,
            line=True,
        )
        assert opened is True
        assert called.get("focus_key") == "line_width_spin"
    finally:
        window.close()
        app.processEvents()


def test_object_manager_legend_context_menu_exposes_reconstruct() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab, axes = next(iter(window._axes_by_tab.items()))  # noqa: SLF001
        assert axes is not None
        axes.plot([0.0, 1.0], [1.0, 2.0], label="Series A")
        legend = axes.legend(loc="best")
        assert legend is not None

        index = window.tab_widget.indexOf(tab)
        if index >= 0:
            window.tab_widget.setCurrentIndex(index)
        window._rebuild_object_manager_for_tab(tab)

        tree = window.object_tree  # noqa: SLF001
        assert isinstance(tree, QtWidgets.QTreeWidget)
        root = tree.topLevelItem(0)
        assert root is not None

        legend_item: QtWidgets.QTreeWidgetItem | None = None

        def _find_legend(item: QtWidgets.QTreeWidgetItem) -> QtWidgets.QTreeWidgetItem | None:
            for idx in range(item.childCount()):
                child = item.child(idx)
                data = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("kind") == "legend":
                    return child
                found = _find_legend(child)
                if found is not None:
                    return found
            return None

        legend_item = _find_legend(root)
        assert legend_item is not None

        called = {"reconstruct": False}

        def _fake_reconstruct(item: QtWidgets.QTreeWidgetItem) -> None:
            called["reconstruct"] = item is legend_item

        window._reconstruct_legend_from_item = _fake_reconstruct  # type: ignore[assignment]
        original_exec = QtWidgets.QMenu.exec

        def _fake_exec(menu: QtWidgets.QMenu, *_args, **_kwargs):
            actions = menu.actions()
            assert [action.text() for action in actions] == [
                "Legend settings...",
                "Reconstruct legend",
            ]
            return actions[1]

        QtWidgets.QMenu.exec = _fake_exec  # type: ignore[assignment]
        try:
            rect = tree.visualItemRect(legend_item)
            window._handle_object_context_menu(rect.center())  # noqa: SLF001
        finally:
            QtWidgets.QMenu.exec = original_exec  # type: ignore[assignment]
        assert called["reconstruct"] is True
    finally:
        window.close()
        app.processEvents()


def test_project_explorer_graph_item_activation_switches_to_tab() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        window._create_blank_graph()
        app.processEvents()

        tree = getattr(window, "project_tree", None)
        assert isinstance(tree, QtWidgets.QTreeWidget)
        assert tree.topLevelItemCount() > 0

        plots_root = tree.topLevelItem(0)
        assert plots_root is not None
        assert plots_root.text(0) == "Plots"
        assert plots_root.childCount() >= 2

        first_plot_item = plots_root.child(0)
        second_plot_item = plots_root.child(1)
        assert first_plot_item is not None
        assert second_plot_item is not None

        first_payload = first_plot_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        second_payload = second_plot_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        assert isinstance(first_payload, tuple) and len(first_payload) >= 2
        assert isinstance(second_payload, tuple) and len(second_payload) >= 2
        assert first_payload[0] == "graph"
        assert second_payload[0] == "graph"

        first_tab = first_payload[1]
        second_tab = second_payload[1]
        assert isinstance(first_tab, QtWidgets.QWidget)
        assert isinstance(second_tab, QtWidgets.QWidget)

        second_index = window.tab_widget.indexOf(second_tab)
        assert second_index >= 0
        window.tab_widget.setCurrentIndex(second_index)
        assert window.tab_widget.currentWidget() is second_tab

        window._dispatch_project_item_activation(first_plot_item, 0)
        assert window.tab_widget.currentWidget() is first_tab
    finally:
        window.close()
        app.processEvents()


def test_project_explorer_graph_selection_switches_to_tab() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.show()
        window.activateWindow()
        app.processEvents()
        window._create_blank_graph()
        window._create_blank_graph()
        app.processEvents()

        tree = getattr(window, "project_tree", None)
        assert isinstance(tree, QtWidgets.QTreeWidget)
        plots_root = tree.topLevelItem(0)
        assert plots_root is not None
        assert plots_root.childCount() >= 2

        first_plot_item = plots_root.child(0)
        second_plot_item = plots_root.child(1)
        assert first_plot_item is not None
        assert second_plot_item is not None

        first_payload = first_plot_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        second_payload = second_plot_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        assert isinstance(first_payload, tuple) and len(first_payload) >= 2
        assert isinstance(second_payload, tuple) and len(second_payload) >= 2
        assert first_payload[0] == "graph"
        assert second_payload[0] == "graph"

        first_tab = first_payload[1]
        second_tab = second_payload[1]
        assert isinstance(first_tab, QtWidgets.QWidget)
        assert isinstance(second_tab, QtWidgets.QWidget)

        tree.setCurrentItem(first_plot_item)
        app.processEvents()
        assert window.tab_widget.currentWidget() is first_tab

        tree.setCurrentItem(second_plot_item)
        app.processEvents()
        assert window.tab_widget.currentWidget() is second_tab

        tree.setFocus()
        app.processEvents()
        tree.setCurrentItem(first_plot_item)
        app.processEvents()
        assert tree.currentItem() is first_plot_item
        assert window.tab_widget.currentWidget() is first_tab
    finally:
        window.close()
        app.processEvents()


def test_project_explorer_arrow_navigation_keeps_tree_focus() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window.show()
        window.activateWindow()
        app.processEvents()
        window._create_blank_graph()
        window._create_blank_graph()
        window._create_blank_graph()
        app.processEvents()

        tree = getattr(window, "project_tree", None)
        assert isinstance(tree, QtWidgets.QTreeWidget)
        plots_root = tree.topLevelItem(0)
        assert plots_root is not None
        assert plots_root.childCount() >= 3

        first_item = plots_root.child(0)
        second_item = plots_root.child(1)
        third_item = plots_root.child(2)
        assert first_item is not None
        assert second_item is not None
        assert third_item is not None

        first_payload = first_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        second_payload = second_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        third_payload = third_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        assert isinstance(first_payload, tuple) and len(first_payload) >= 2
        assert isinstance(second_payload, tuple) and len(second_payload) >= 2
        assert isinstance(third_payload, tuple) and len(third_payload) >= 2
        first_tab = first_payload[1]
        second_tab = second_payload[1]
        third_tab = third_payload[1]
        assert isinstance(first_tab, QtWidgets.QWidget)
        assert isinstance(second_tab, QtWidgets.QWidget)
        assert isinstance(third_tab, QtWidgets.QWidget)

        tree.setCurrentItem(first_item)
        tree.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        app.processEvents()
        assert tree.currentItem() is first_item
        assert window.tab_widget.currentWidget() is first_tab

        down_press = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_Down,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        down_release = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyRelease,
            QtCore.Qt.Key.Key_Down,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )

        QtWidgets.QApplication.sendEvent(tree, down_press)
        QtWidgets.QApplication.sendEvent(tree, down_release)
        app.processEvents()
        assert tree.currentItem() is second_item
        assert window.tab_widget.currentWidget() is second_tab
        focus_widget = QtWidgets.QApplication.focusWidget()
        assert focus_widget is tree or (
            isinstance(focus_widget, QtWidgets.QWidget) and tree.isAncestorOf(focus_widget)
        )

        QtWidgets.QApplication.sendEvent(tree, down_press)
        QtWidgets.QApplication.sendEvent(tree, down_release)
        app.processEvents()
        assert tree.currentItem() is third_item
        assert window.tab_widget.currentWidget() is third_tab
        focus_widget = QtWidgets.QApplication.focusWidget()
        assert focus_widget is tree or (
            isinstance(focus_widget, QtWidgets.QWidget) and tree.isAncestorOf(focus_widget)
        )
    finally:
        window.close()
        app.processEvents()


def test_copy_graph_to_clipboard_produces_png_pixmap() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        app.processEvents()

        assert window._copy_graph_to_clipboard()  # noqa: SLF001 - host helper
        clipboard = QtWidgets.QApplication.clipboard()
        assert clipboard is not None
        pixmap = clipboard.pixmap()
        assert isinstance(pixmap, QtGui.QPixmap)
        assert not pixmap.isNull()
    finally:
        window.close()
        app.processEvents()


def test_canvas_right_click_routes_to_context_menu() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        app.processEvents()
        canvas = window._current_canvas()  # noqa: SLF001 - test helper
        assert canvas is not None

        calls = {"count": 0}

        def _fake_context_menu(event: object) -> None:
            _ = event
            calls["count"] += 1

        window._show_canvas_context_menu = _fake_context_menu  # type: ignore[assignment]
        event = SimpleNamespace(button=3, dblclick=False, canvas=canvas)
        window._handle_canvas_button_press(event)  # noqa: SLF001 - event dispatch helper
        assert calls["count"] == 1
    finally:
        window.close()
        app.processEvents()


def test_apply_axes_text_value_updates_descriptor_and_tree() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001 - test hook
        assert descriptor is not None
        axes = descriptor.axes
        assert axes is not None

        assert window._apply_axes_text_value(axes, field="title", value="Updated Title")
        assert window._apply_axes_text_value(axes, field="x_label", value="Updated X")
        assert window._apply_axes_text_value(axes, field="y_label", value="Updated Y")
        assert axes.get_title() == "Updated Title"
        assert axes.get_xlabel() == "Updated X"
        assert axes.get_ylabel() == "Updated Y"
        assert descriptor.title == "Updated Title"
        assert descriptor.x_label == "Updated X"
        assert descriptor.y_label == "Updated Y"

        tree_item = window._graph_tree_items.get(tab)  # noqa: SLF001 - test hook
        assert isinstance(tree_item, QtWidgets.QTreeWidgetItem)
        assert tree_item.text(1) == "Updated Title"
    finally:
        window.close()
        app.processEvents()


def test_apply_axis_scale_settings_supports_log_and_validation() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        window._create_blank_graph()
        tab = window.tab_widget.currentWidget()
        assert isinstance(tab, QtWidgets.QWidget)
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001 - test hook
        assert descriptor is not None
        axes = descriptor.axes
        assert axes is not None

        ok = window._apply_axis_scale_settings(
            axes,
            "x",
            scale="log",
            auto_limits=False,
            lower=1.0,
            upper=10.0,
            show_dialog_errors=False,
        )
        assert ok
        assert axes.get_xscale() == "log"
        x_min, x_max = axes.get_xlim()
        assert x_min == pytest.approx(1.0)
        assert x_max == pytest.approx(10.0)

        failed = window._apply_axis_scale_settings(
            axes,
            "y",
            scale="log",
            auto_limits=False,
            lower=-1.0,
            upper=10.0,
            show_dialog_errors=False,
        )
        assert not failed
    finally:
        window.close()
        app.processEvents()
