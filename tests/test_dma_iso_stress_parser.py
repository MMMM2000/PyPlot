import os
import sys
from pathlib import Path

import pytest
from PyQt6 import QtWidgets
from matplotlib import ticker as mticker

from plotting.plugins.dma_iso_stress.parser import parse_dma_txt
from plotting.plugins.dma_iso_stress.dma_iso_stress_plugin import DmaIsoStressEntry
from plotting.pyplot.app import PyPlotWorkbench


def test_parse_dma_txt_reads_sample_iso_stress_data() -> None:
    sample = Path("sample_data/DMA/Ni50Fe27Ga23 11_1 s1.txt")
    parsed = parse_dma_txt(sample)

    assert parsed, "expected at least one IsoStress dataset"
    for stress, (temps, strains) in parsed.items():
        assert isinstance(stress, int)
        assert temps
        assert strains
        assert len(temps) == len(strains)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def _activate_dma_plugin(window: PyPlotWorkbench):
    combo = getattr(window, "_plotter_combo", None)
    assert isinstance(combo, QtWidgets.QComboBox)
    index = combo.findText("DMA Iso-Stress")
    assert index >= 0
    combo.setCurrentIndex(index)
    plugin = window._current_plugin
    assert plugin is not None
    return plugin


def _seed_dma_graphs(plugin) -> list[tuple[QtWidgets.QWidget, object]]:
    plugin._dataset = [  # noqa: SLF001 - test fixture data setup
        DmaIsoStressEntry(
            path=Path("sample_a.txt"),
            sample="SampleA",
            datasets={40: ([0.0, 10.0, 20.0], [1.0, 1.5, 2.0])},
        ),
        DmaIsoStressEntry(
            path=Path("sample_b.txt"),
            sample="SampleB",
            datasets={40: ([0.0, 10.0, 20.0], [2.0, 2.5, 3.0])},
        ),
    ]
    plugin.generate()
    return list(plugin._iter_dma_descriptors())  # noqa: SLF001 - test hook


def test_dma_graph_formatting_can_hide_title_and_axis_labels() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        plugin = _activate_dma_plugin(window)
        descriptors = _seed_dma_graphs(plugin)
        assert descriptors

        assert plugin._show_title_checkbox is not None  # noqa: SLF001
        assert plugin._show_xlabel_checkbox is not None  # noqa: SLF001
        assert plugin._show_ylabel_checkbox is not None  # noqa: SLF001
        plugin._show_title_checkbox.setChecked(False)  # noqa: SLF001
        plugin._show_xlabel_checkbox.setChecked(False)  # noqa: SLF001
        plugin._show_ylabel_checkbox.setChecked(False)  # noqa: SLF001

        plugin._apply_formatting_to_current_plot()  # noqa: SLF001
        tab = window.tab_widget.currentWidget()
        assert tab is not None
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001
        assert descriptor is not None
        axes = descriptor.axes
        assert axes is not None

        assert not bool(axes.title.get_visible())
        assert not bool(axes.xaxis.label.get_visible())
        assert not bool(axes.yaxis.label.get_visible())
    finally:
        window.close()
        app.processEvents()


def test_dma_selected_format_groups_apply_only_requested_changes() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        plugin = _activate_dma_plugin(window)
        descriptors = _seed_dma_graphs(plugin)
        assert len(descriptors) >= 2

        current_tab = window.tab_widget.currentWidget()
        assert current_tab is not None
        target_tab = None
        target_descriptor = None
        for tab, descriptor in descriptors:
            if tab is not current_tab:
                target_tab = tab
                target_descriptor = descriptor
                break
        assert target_tab is not None
        assert target_descriptor is not None

        assert plugin._title_edit is not None  # noqa: SLF001
        assert plugin._show_title_checkbox is not None  # noqa: SLF001
        assert plugin._line_width_spin is not None  # noqa: SLF001
        plugin._title_edit.setText("Styled {sample}")  # noqa: SLF001
        plugin._show_title_checkbox.setChecked(False)  # noqa: SLF001
        plugin._line_width_spin.setValue(3.7)  # noqa: SLF001

        line = target_descriptor.axes.get_lines()[0]
        before_width = float(line.get_linewidth())

        plugin._apply_formatting_to_descriptor(  # noqa: SLF001
            target_tab,
            target_descriptor,
            groups={"title"},
        )

        assert target_descriptor.axes.get_title() == "Styled SampleA"
        assert not bool(target_descriptor.axes.title.get_visible())
        assert float(line.get_linewidth()) == pytest.approx(before_width)
    finally:
        window.close()
        app.processEvents()


def test_dma_can_rewrite_legend_entries_for_current_graph() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        plugin = _activate_dma_plugin(window)
        descriptors = _seed_dma_graphs(plugin)
        assert descriptors

        tab = window.tab_widget.currentWidget()
        assert tab is not None
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001
        assert descriptor is not None
        axes = descriptor.axes
        assert axes is not None

        line = axes.get_lines()[0]
        assert str(line.get_label()) == "40 MPa"

        plugin._set_legend_overrides_for_descriptor(  # noqa: SLF001
            descriptor, {"40 MPa": "Annealed 40 MPa"}
        )
        plugin._apply_formatting_to_descriptor(  # noqa: SLF001
            tab, descriptor, groups={"legend_labels"}
        )

        assert str(line.get_label()) == "Annealed 40 MPa"
        legend = axes.get_legend()
        assert legend is not None
        labels = [text.get_text() for text in legend.get_texts()]
        assert labels == ["Annealed 40 MPa"]
    finally:
        window.close()
        app.processEvents()


def test_dma_selected_copy_can_propagate_legend_labels() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        plugin = _activate_dma_plugin(window)
        descriptors = _seed_dma_graphs(plugin)
        assert len(descriptors) >= 2

        source_tab = window.tab_widget.currentWidget()
        assert source_tab is not None
        source_descriptor = window._tab_descriptors.get(source_tab)  # noqa: SLF001
        assert source_descriptor is not None
        source_axes = source_descriptor.axes
        assert source_axes is not None
        source_line = source_axes.get_lines()[0]
        assert str(source_line.get_label()) == "40 MPa"

        plugin._set_legend_overrides_for_descriptor(  # noqa: SLF001
            source_descriptor, {"40 MPa": "Source Label"}
        )
        source_overrides = plugin._legend_overrides_for_descriptor(source_descriptor)  # noqa: SLF001

        target_tab = None
        target_descriptor = None
        for tab, descriptor in descriptors:
            if tab is source_tab:
                continue
            target_tab = tab
            target_descriptor = descriptor
            break
        assert target_tab is not None
        assert target_descriptor is not None
        target_axes = target_descriptor.axes
        assert target_axes is not None
        target_line = target_axes.get_lines()[0]
        before_width = float(target_line.get_linewidth())

        plugin._apply_formatting_to_descriptor(  # noqa: SLF001
            target_tab,
            target_descriptor,
            groups={"legend_labels"},
            source_legend_overrides=source_overrides,
        )

        assert str(target_line.get_label()) == "Source Label"
        assert float(target_line.get_linewidth()) == pytest.approx(before_width)
        target_legend = target_axes.get_legend()
        assert target_legend is not None
        target_labels = [text.get_text() for text in target_legend.get_texts()]
        assert target_labels == ["Source Label"]
    finally:
        window.close()
        app.processEvents()


def test_dma_tick_controls_apply_increment_and_count_modes() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        plugin = _activate_dma_plugin(window)
        descriptors = _seed_dma_graphs(plugin)
        assert descriptors

        assert plugin._x_tick_mode_combo is not None  # noqa: SLF001
        assert plugin._x_tick_count_spin is not None  # noqa: SLF001
        assert plugin._y_tick_mode_combo is not None  # noqa: SLF001
        assert plugin._y_tick_step_edit is not None  # noqa: SLF001

        plugin._x_tick_mode_combo.setCurrentIndex(plugin._x_tick_mode_combo.findData("count"))  # noqa: SLF001
        plugin._x_tick_count_spin.setValue(4)  # noqa: SLF001
        plugin._y_tick_mode_combo.setCurrentIndex(plugin._y_tick_mode_combo.findData("step"))  # noqa: SLF001
        plugin._y_tick_step_edit.setText("0.5")  # noqa: SLF001
        plugin._apply_formatting_to_current_plot()  # noqa: SLF001

        tab = window.tab_widget.currentWidget()
        assert tab is not None
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001
        assert descriptor is not None
        axes = descriptor.axes
        assert axes is not None

        assert isinstance(axes.xaxis.get_major_locator(), mticker.MaxNLocator)
        assert isinstance(axes.yaxis.get_major_locator(), mticker.MultipleLocator)
    finally:
        window.close()
        app.processEvents()


def test_dma_project_payload_restores_plotted_graph_and_formatting(tmp_path: Path) -> None:
    app = _ensure_app()
    data_path = tmp_path / "dma_restore_sample.txt"
    data_path.write_text(
        "\n".join(
            [
                "IsoStress section",
                "Step time\tTemperature\tStrain\tStress",
                "s\t°C\t%\tMPa",
                "0\t20\t1.0\t40",
                "1\t30\t1.4\t40",
                "",
            ]
        ),
        encoding="utf-8",
    )

    window = PyPlotWorkbench()
    restored_window: PyPlotWorkbench | None = None
    try:
        plugin = _activate_dma_plugin(window)
        window._commit_selected_paths([data_path])  # noqa: SLF001 - test setup
        plugin.load_data()
        plugin.generate()
        assert window.save_graph_button.isEnabled()

        tab = window.tab_widget.currentWidget()
        assert tab is not None
        descriptor = window._tab_descriptors.get(tab)  # noqa: SLF001 - test hook
        assert descriptor is not None

        assert plugin._title_edit is not None  # noqa: SLF001
        assert plugin._x_tick_mode_combo is not None  # noqa: SLF001
        assert plugin._x_tick_count_spin is not None  # noqa: SLF001
        plugin._title_edit.setText("Saved {sample}")  # noqa: SLF001
        plugin._x_tick_mode_combo.setCurrentIndex(plugin._x_tick_mode_combo.findData("count"))  # noqa: SLF001
        plugin._x_tick_count_spin.setValue(4)  # noqa: SLF001
        plugin._set_legend_overrides_for_descriptor(  # noqa: SLF001
            descriptor,
            {"40 MPa": "Saved 40 MPa"},
        )
        plugin._apply_formatting_to_current_plot()  # noqa: SLF001

        payload = window._build_project_payload(base_path=tmp_path)  # noqa: SLF001

        restored_window = PyPlotWorkbench()
        assert restored_window._apply_project_payload(payload, project_dir=tmp_path)  # noqa: SLF001

        restored_plugin = restored_window._current_plugin
        assert restored_plugin is not None
        assert restored_window._current_plotter_name == "DMA Iso-Stress"  # noqa: SLF001
        assert restored_window.save_graph_button.isEnabled()

        dma_tabs = [
            (tab_item, desc)
            for tab_item, desc in restored_window._tab_descriptors.items()  # noqa: SLF001
            if getattr(desc, "kind", "") == "dma_iso_stress"
        ]
        assert dma_tabs, "expected at least one restored DMA graph tab"
        restored_tab, restored_descriptor = dma_tabs[0]
        restored_axes = restored_descriptor.axes
        assert restored_axes is not None

        assert restored_axes.get_title().startswith("Saved ")
        assert isinstance(restored_axes.xaxis.get_major_locator(), mticker.MaxNLocator)
        legend = restored_axes.get_legend()
        assert legend is not None
        labels = [text.get_text() for text in legend.get_texts()]
        assert "Saved 40 MPa" in labels

        restored_index = restored_window.tab_widget.indexOf(restored_tab)
        assert restored_index >= 0
    finally:
        if restored_window is not None:
            restored_window.close()
        window.close()
        app.processEvents()


def test_dma_uses_single_shared_graph_formatting_section() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench()
    try:
        _activate_dma_plugin(window)
        sections = list(getattr(window, "_graph_settings_sections", []))  # noqa: SLF001
        titles = [str(title) for title, _anchor in sections]
        assert any(title == "Plot options" for title in titles)
        assert titles.count("Graph formatting") == 1
    finally:
        window.close()
        app.processEvents()
