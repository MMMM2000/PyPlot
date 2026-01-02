from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from PyQt6 import QtWidgets

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api

try:
    from experiments.simple_scripts.dma_isostress import parse_dma_txt
except Exception:  # pragma: no cover - optional dependency
    parse_dma_txt = None  # type: ignore[assignment]


@dataclass
class DmaIsoStressEntry:
    path: Path
    sample: str
    datasets: Dict[int, Tuple[List[float], List[float]]]


@register_plugin("DMA Iso-Stress")
class DmaIsoStressPlugin(PyPlotPlugin):
    """PyPlot integration for DMA iso-stress TXT files."""

    requires_imported_data = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        if parse_dma_txt is None:  # pragma: no cover - defensive import guard
            raise RuntimeError("parse_dma_txt is not available.")
        self._dataset: List[DmaIsoStressEntry] = []
        self._plot_tabs: List[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._markers_checkbox: QtWidgets.QCheckBox | None = None
        self._sort_checkbox: QtWidgets.QCheckBox | None = None
        self._loaded_paths: List[Path] = []

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QtWidgets.QLabel(
            "Import DMA iso-stress TXT files, then plot temperature vs strain for each stress level."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        layout.addStretch(1)
        self._summary_label = summary
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        if self._settings_widget is not None:
            return self._settings_widget

        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        window_module = window_api()
        options_section, options_layout = window_module.create_toolbar_section(
            "Plot options",
            parent=container,
        )
        self._markers_checkbox = QtWidgets.QCheckBox("Show markers", options_section)
        self._markers_checkbox.setChecked(False)
        options_layout.addWidget(self._markers_checkbox)
        self._sort_checkbox = QtWidgets.QCheckBox(
            "Sort stress levels ascending", options_section
        )
        self._sort_checkbox.setChecked(True)
        options_layout.addWidget(self._sort_checkbox)
        options_layout.addStretch(1)
        layout.addWidget(options_section)
        layout.addStretch(1)

        self._settings_widget = container
        return container

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return
        entries: List[DmaIsoStressEntry] = []
        for path in paths:
            try:
                datasets = parse_dma_txt(Path(path))
            except Exception as exc:
                self._log(f"Failed to parse {path.name}: {exc}", level="error")
                continue
            if not datasets:
                self._log(f"No iso-stress data found in {path.name}.", level="error")
                continue
            entries.append(DmaIsoStressEntry(path=Path(path), sample=path.stem, datasets=datasets))

        self._dataset = entries
        self._data = entries  # satisfy host readiness checks
        self._loaded_paths = list(paths)
        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent
        self._log(f"Loaded {len(entries)} DMA iso-stress file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._dataset:
            self.load_data()
        if not self._dataset:
            return

        self._clear_tabs()
        window_module = window_api()
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

        show_markers = bool(
            self._markers_checkbox.isChecked() if self._markers_checkbox is not None else False
        )
        sort_stress = bool(
            self._sort_checkbox.isChecked() if self._sort_checkbox is not None else True
        )

        for entry in self._dataset:
            fig = Figure(figsize=(8.5, 5))
            ax = fig.add_subplot(111)
            stresses = list(entry.datasets.keys())
            if sort_stress:
                stresses.sort()
            for stress in stresses:
                temps, strains = entry.datasets[stress]
                label = f"{stress} MPa"
                marker = "o" if show_markers else None
                ax.plot(temps, strains, linewidth=1.4, marker=marker, label=label)
            ax.set_title(f"{entry.sample} - DMA Iso-Stress")
            ax.set_xlabel("Temperature (°C)")
            ax.set_ylabel("Strain (%)")
            ax.legend(loc="best")
            tab = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            canvas = FigureCanvas(fig)
            canvas.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            layout.addWidget(canvas)
            descriptor = window_module.TabDescriptor(
                kind="dma_iso_stress",
                title=f"{entry.sample} - DMA Iso-Stress",
                root_label=f"{entry.sample} - IsoStress",
                x_label="Temperature (°C)",
                y_label="Strain (%)",
                canvas=canvas,
                axes=ax,
                lines={},
                metadata={"sample": entry.sample, "path": str(entry.path)},
            )
            index = self.host.tab_widget.addTab(tab, descriptor.root_label or "Plot")
            setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
            if callable(setter):
                setter(index)
            self.host._register_plot_tab(tab, canvas, ax, descriptor)
            self._plot_tabs.append(tab)
        self._set_tab_bar_visible(False)

    def update_ui(self) -> None:  # type: ignore[override]
        has_data = bool(self._dataset)
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(has_data or self._host_has_data_selection())
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(False)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(False)
        if hasattr(self.host, "export_origin_button"):
            self.host.export_origin_button.setEnabled(False)
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(False)
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if self._summary_label is not None:
            if not has_data:
                self._summary_label.setText(
                    "Import DMA iso-stress TXT files, then plot temperature vs strain."
                )
            else:
                self._summary_label.clear()
        self.host._update_project_actions()

    def _clear_tabs(self) -> None:
        if not self._plot_tabs:
            return
        host = self.host
        for tab in list(self._plot_tabs):
            remover = getattr(host, "_remove_tab_internal", None)
            if callable(remover):
                try:
                    remover(tab)
                    continue
                except Exception:
                    pass
            index = host.tab_widget.indexOf(tab)
            if index >= 0:
                host.tab_widget.removeTab(index)
        self._plot_tabs.clear()
        host._rebuild_object_manager_for_tab(host.tab_widget.currentWidget())

    def _set_tab_bar_visible(self, visible: bool) -> None:
        bar_getter = getattr(self.host.tab_widget, "tabBar", None)
        if callable(bar_getter):
            try:
                bar = bar_getter()
            except Exception:
                return
            try:
                bar.setVisible(visible)
                bar.setMaximumHeight(0 if not visible else 16777215)
                auto_hide = getattr(self.host.tab_widget, "setTabBarAutoHide", None)
                if callable(auto_hide):
                    try:
                        auto_hide(not visible)
                    except Exception:
                        pass
            except Exception:
                pass
