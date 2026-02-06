import os
import sys
from pathlib import Path

import pytest
from PyQt6 import QtWidgets

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
