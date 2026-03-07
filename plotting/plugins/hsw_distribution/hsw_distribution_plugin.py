from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins._window import window_api
from plotting.plugins.base import PyPlotPlugin, register_plugin

from . import dialog as hsw_dist_core

if TYPE_CHECKING:
    from plotting.pyplot.window import GraphLineState


@register_plugin("Hsw Distribution")
class HswDistributionPlugin(PyPlotPlugin):
    """Shared-native PyPlot integration for HSW distribution analysis."""

    requires_imported_data = True
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._loaded_files: list[str] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._panel_widget: QtWidgets.QWidget | None = None
        self._raw_cb: QtWidgets.QCheckBox | None = None
        self._trim_cb: QtWidgets.QCheckBox | None = None
        self._hist_cb: QtWidgets.QCheckBox | None = None
        self._ind_cb: QtWidgets.QCheckBox | None = None
        self._comb_cb: QtWidgets.QCheckBox | None = None
        self._auto_bins_rb: QtWidgets.QRadioButton | None = None
        self._manual_bins_rb: QtWidgets.QRadioButton | None = None
        self._bin_width_spin: QtWidgets.QDoubleSpinBox | None = None
        self._share_bins_cb: QtWidgets.QCheckBox | None = None
        self._core_bins_spin: QtWidgets.QSpinBox | None = None
        self._core_min_spin: QtWidgets.QSpinBox | None = None
        self._tthh_rb: QtWidgets.QRadioButton | None = None
        self._t1t2_rb: QtWidgets.QRadioButton | None = None

    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        if self._panel_widget is not None:
            return self._panel_widget
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        window_module = window_api()
        section, section_layout = window_module.create_toolbar_section("Overview", parent=container)
        summary = QtWidgets.QLabel(
            "Import HSW files and generate filtered raw, histogram, and reduced switching-field distribution plots."
        )
        summary.setWordWrap(True)
        section_layout.addWidget(summary)
        section_layout.addStretch(1)
        layout.addWidget(section)
        layout.addStretch(1)
        self._summary_label = summary
        self._panel_widget = container
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        if self._settings_widget is not None:
            return self._settings_widget
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        window_module = window_api()

        plots_section, plots_layout = window_module.create_toolbar_section("Plots", parent=container)
        raw_cb = QtWidgets.QCheckBox("Raw TT/HH vs index", plots_section)
        raw_cb.setChecked(True)
        trim_cb = QtWidgets.QCheckBox("Show trimmed data", plots_section)
        trim_cb.setChecked(True)
        hist_cb = QtWidgets.QCheckBox("Counts histogram", plots_section)
        hist_cb.setChecked(True)
        ind_cb = QtWidgets.QCheckBox("Individual ln(dp/dh)", plots_section)
        comb_cb = QtWidgets.QCheckBox("Combined ln(dp/dh)", plots_section)
        comb_cb.setChecked(True)
        for widget in (raw_cb, trim_cb, hist_cb, ind_cb, comb_cb):
            plots_layout.addWidget(widget)
        plots_layout.addStretch(1)
        self._raw_cb = raw_cb
        self._trim_cb = trim_cb
        self._hist_cb = hist_cb
        self._ind_cb = ind_cb
        self._comb_cb = comb_cb
        layout.addWidget(plots_section)

        bins_section, bins_layout = window_module.create_toolbar_section("Histogram bins", parent=container)
        auto_rb = QtWidgets.QRadioButton("Automatic", bins_section)
        auto_rb.setChecked(True)
        manual_rb = QtWidgets.QRadioButton("Manual Δh", bins_section)
        bin_width_spin = QtWidgets.QDoubleSpinBox(bins_section)
        bin_width_spin.setDecimals(6)
        bin_width_spin.setRange(1e-6, 1.0)
        bin_width_spin.setValue(1e-4)
        share_bins_cb = QtWidgets.QCheckBox("Share TT/HH bins", bins_section)
        share_bins_cb.setChecked(True)
        bins_layout.addWidget(auto_rb)
        bins_layout.addWidget(manual_rb)
        bins_layout.addWidget(bin_width_spin)
        bins_layout.addWidget(share_bins_cb)
        bins_layout.addStretch(1)
        self._auto_bins_rb = auto_rb
        self._manual_bins_rb = manual_rb
        self._bin_width_spin = bin_width_spin
        self._share_bins_cb = share_bins_cb
        layout.addWidget(bins_section)

        core_section, core_layout = window_module.create_toolbar_section("Core filter", parent=container)
        core_bins_spin = QtWidgets.QSpinBox(core_section)
        core_bins_spin.setRange(1, 9999)
        core_bins_spin.setValue(50)
        core_min_spin = QtWidgets.QSpinBox(core_section)
        core_min_spin.setRange(1, 9999)
        core_min_spin.setValue(3)
        core_layout.addWidget(QtWidgets.QLabel("n_bins:", core_section))
        core_layout.addWidget(core_bins_spin)
        core_layout.addWidget(QtWidgets.QLabel("min_count:", core_section))
        core_layout.addWidget(core_min_spin)
        core_layout.addStretch(1)
        self._core_bins_spin = core_bins_spin
        self._core_min_spin = core_min_spin
        layout.addWidget(core_section)

        labels_section, labels_layout = window_module.create_toolbar_section("Column labels", parent=container)
        tthh_rb = QtWidgets.QRadioButton("TT / HH", labels_section)
        tthh_rb.setChecked(True)
        t1t2_rb = QtWidgets.QRadioButton("T1 / T2", labels_section)
        labels_layout.addWidget(tthh_rb)
        labels_layout.addWidget(t1t2_rb)
        labels_layout.addStretch(1)
        self._tthh_rb = tthh_rb
        self._t1t2_rb = t1t2_rb
        layout.addWidget(labels_section)
        layout.addStretch(1)
        self._settings_widget = container
        return container

    def plot_action_label(self) -> str:  # type: ignore[override]
        return "Plot HSW Distribution"

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return
        valid = [path for path in paths if path.suffix.lower() == ".txt"]
        if not valid:
            self._loaded_files = []
            self.update_ui()
            return
        self._loaded_files = [str(path) for path in valid]
        self.host._commit_selected_paths(valid)  # type: ignore[attr-defined]
        self._log(f"Loaded {len(valid)} HSW distribution file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._loaded_files:
            self.load_data()
        if not self._loaded_files:
            return
        cfg = self._config()
        self.clear_plot_tabs(self._plot_tabs)
        figures = hsw_dist_core.build_figures(self._loaded_files, cfg)
        window_module = window_api()
        for fig, stem in figures:
            canvas = FigureCanvas(fig)
            canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.addWidget(canvas)
            axes = fig.axes[0] if fig.axes else None
            lines: dict[tuple[str, float | str], GraphLineState] = {}
            if axes is not None:
                for index, line in enumerate(axes.get_lines(), start=1):
                    label = line.get_label() or f"Series {index}"
                    state = window_module.GraphLineState(
                        key=(label, float(index)),
                        label=label,
                        line=line,
                        base_x=line.get_xdata(),
                        base_y=line.get_ydata(),
                    )
                    lines[state.key] = state
            title = axes.get_title() if axes is not None else stem
            descriptor = window_module.TabDescriptor(
                kind="hsw_distribution",
                title=title,
                root_label=stem,
                x_label=axes.get_xlabel() if axes is not None else "",
                y_label=axes.get_ylabel() if axes is not None else "",
                canvas=canvas,
                axes=axes,
                lines=lines,
                metadata={"source_files": list(self._loaded_files), "stem": stem},
            )
            self.host.tab_widget.addTab(tab, stem)
            self.host._register_plot_tab(tab, canvas, axes, descriptor)
            self._plot_tabs.append(tab)
        if self._plot_tabs:
            self.host.tab_widget.setCurrentWidget(self._plot_tabs[0])
        self._log(f"Generated {len(self._plot_tabs)} HSW distribution plot(s).")
        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        def _task() -> None:
            hsw_dist_core.run_distribution(self._loaded_files, self._config(backend="origin"))

        self.run_origin_export(
            ready=bool(self._loaded_files),
            missing_message="Load HSW distribution data before exporting to Origin.",
            task=_task,
            success_log="Sent HSW distribution plots to Origin.",
        )

    def update_ui(self) -> None:  # type: ignore[override]
        has_data = bool(self._loaded_files)
        has_plots = bool(self._plot_tabs)
        if self._summary_label is not None:
            if has_data:
                self._summary_label.setText(
                    f"Loaded {len(self._loaded_files)} HSW file(s). Plot to generate shared PyPlot graph tabs."
                )
            else:
                self._summary_label.setText(
                    "Import HSW files and generate filtered raw, histogram, and reduced switching-field distribution plots."
                )
        self.apply_shared_action_state(
            can_plot=has_data or bool(self.host._selected_paths()),
            can_export_txt=has_plots,
            can_open_origin=has_plots,
            update_project_actions=False,
        )
        updater = getattr(self.host, "_update_project_actions", None)
        if callable(updater):
            updater()

    def _config(self, *, backend: str = "matplotlib") -> dict[str, object]:
        return {
            "raw": bool(self._raw_cb.isChecked()) if self._raw_cb is not None else True,
            "show_trimmed": bool(self._trim_cb.isChecked()) if self._trim_cb is not None else True,
            "hist": bool(self._hist_cb.isChecked()) if self._hist_cb is not None else True,
            "ind_log": bool(self._ind_cb.isChecked()) if self._ind_cb is not None else False,
            "comb_log": bool(self._comb_cb.isChecked()) if self._comb_cb is not None else True,
            "bin_mode": "manual" if self._manual_bins_rb is not None and self._manual_bins_rb.isChecked() else "auto",
            "bin_width": float(self._bin_width_spin.value()) if self._bin_width_spin is not None else 1e-4,
            "share_bins": bool(self._share_bins_cb.isChecked()) if self._share_bins_cb is not None else True,
            "core_bins": int(self._core_bins_spin.value()) if self._core_bins_spin is not None else 50,
            "core_min": int(self._core_min_spin.value()) if self._core_min_spin is not None else 3,
            "labels": ("TT", "HH") if self._t1t2_rb is None or not self._t1t2_rb.isChecked() else ("T1", "T2"),
            "backend": backend,
        }


__all__ = ["HswDistributionPlugin"]
