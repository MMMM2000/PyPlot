from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins._window import window_api
from plotting.plugins.base import PyPlotPlugin, register_plugin

from . import core as compare_core

if TYPE_CHECKING:
    from plotting.pyplot.window import GraphLineState


@register_plugin("Hsw Load Compare")
class HswLoadComparePlugin(PyPlotPlugin):
    """Shared-native PyPlot integration for HSW load-compare analysis."""

    requires_imported_data = True
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._loaded_files: list[str] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._panel_widget: QtWidgets.QWidget | None = None
        self._summary_label: QtWidgets.QLabel | None = None
        self._tt_cb: QtWidgets.QCheckBox | None = None
        self._hh_cb: QtWidgets.QCheckBox | None = None
        self._raw_cb: QtWidgets.QCheckBox | None = None
        self._hist_cb: QtWidgets.QCheckBox | None = None
        self._ind_cb: QtWidgets.QCheckBox | None = None
        self._comb_cb: QtWidgets.QCheckBox | None = None
        self._share_cb: QtWidgets.QCheckBox | None = None

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
            "Import ascending-load HSW files, then generate raw, histogram, and reduced-field comparison plots."
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
        tt_cb = QtWidgets.QCheckBox("TT", plots_section)
        tt_cb.setChecked(True)
        hh_cb = QtWidgets.QCheckBox("HH", plots_section)
        hh_cb.setChecked(True)
        raw_cb = QtWidgets.QCheckBox("Raw", plots_section)
        hist_cb = QtWidgets.QCheckBox("Histograms", plots_section)
        ind_cb = QtWidgets.QCheckBox("Individual ln(dp/dh)", plots_section)
        comb_cb = QtWidgets.QCheckBox("Combined ln(dp/dh)", plots_section)
        comb_cb.setChecked(True)
        share_cb = QtWidgets.QCheckBox("Share histogram Y scale", plots_section)
        share_cb.setChecked(bool(compare_core.SAME_HIST_Y))
        for widget in (tt_cb, hh_cb, raw_cb, hist_cb, ind_cb, comb_cb, share_cb):
            plots_layout.addWidget(widget)
        plots_layout.addStretch(1)
        self._tt_cb = tt_cb
        self._hh_cb = hh_cb
        self._raw_cb = raw_cb
        self._hist_cb = hist_cb
        self._ind_cb = ind_cb
        self._comb_cb = comb_cb
        self._share_cb = share_cb
        layout.addWidget(plots_section)
        layout.addStretch(1)
        self._settings_widget = container
        return container

    def plot_action_label(self) -> str:  # type: ignore[override]
        return "Plot HSW Load Compare"

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return
        valid: list[Path] = []
        for path in paths:
            metadata = compare_core.parse_metadata(path.stem)
            if metadata is None:
                continue
            valid.append(path)
        if not valid:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "None of the selected files matched the HSW load-compare naming pattern.",
            )
            self._loaded_files = []
            self.update_ui()
            return
        self._loaded_files = [str(path) for path in valid]
        self.host._commit_selected_paths(valid)  # type: ignore[attr-defined]
        self._log(f"Loaded {len(valid)} HSW load-compare file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._loaded_files:
            self.load_data()
        if not self._loaded_files:
            return
        cfg = self._config()
        self.clear_plot_tabs(self._plot_tabs)
        figures = compare_core.build_figures(self._loaded_files, cfg)
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
            x_label = axes.get_xlabel() if axes is not None else ""
            y_label = axes.get_ylabel() if axes is not None else ""
            if not x_label:
                if stem == "log_compare":
                    x_label = r"$\Delta h^{3/2}$"
                elif stem == "hist_compare":
                    x_label = "h = H/Hsw,max"
                elif stem == "raw_compare":
                    x_label = "Index"
            if not y_label:
                if stem == "log_compare":
                    y_label = "ln(dp/dh)"
                elif stem == "hist_compare":
                    y_label = "Counts"
                elif stem == "raw_compare":
                    y_label = "Switching Field"
            descriptor = window_module.TabDescriptor(
                kind="hsw_load_compare",
                title=title,
                root_label=title,
                x_label=x_label,
                y_label=y_label,
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
        self._log(f"Generated {len(self._plot_tabs)} HSW load-compare plot(s).")
        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        super().open_origin()

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
                    "Import ascending-load HSW files, then generate raw, histogram, and reduced-field comparison plots."
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
            "TT": bool(self._tt_cb.isChecked()) if self._tt_cb is not None else True,
            "HH": bool(self._hh_cb.isChecked()) if self._hh_cb is not None else True,
            "raw": bool(self._raw_cb.isChecked()) if self._raw_cb is not None else False,
            "hist": bool(self._hist_cb.isChecked()) if self._hist_cb is not None else False,
            "ind_log": bool(self._ind_cb.isChecked()) if self._ind_cb is not None else False,
            "comb_log": bool(self._comb_cb.isChecked()) if self._comb_cb is not None else True,
            "share_y": bool(self._share_cb.isChecked()) if self._share_cb is not None else True,
            "show": False,
            "save": False,
            "out_dir": str(Path.cwd()),
            "BACKEND": backend,
        }


__all__ = ["HswLoadComparePlugin"]
