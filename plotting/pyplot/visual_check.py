from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from PyQt6 import QtCore, QtWidgets

from plotting.plugins.shape_memory_stress_strain.shape_memory_stress_strain_plugin import (
    LAYOUT_DUAL_AXIS,
    LAYOUT_SEPARATE_TABS,
)
from plotting.shared.utils import ensure_app_theme

from .app import PyPlotWorkbench
from .window import WorkbookData, WorksheetData


_SHAPE_MEMORY_PLUGIN_NAME = "Shape Memory Stress/Strain"


@dataclass
class VisualCheckResult:
    output_dir: Path
    matplotlib_images: list[Path] = field(default_factory=list)
    matplotlib_canvas_images: list[Path] = field(default_factory=list)
    subwindow_images: list[Path] = field(default_factory=list)
    origin_images: list[Path] = field(default_factory=list)
    window_image: Path | None = None
    tab_widget_image: Path | None = None
    summary_json: Path | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _safe_stem(value: str, fallback: str = "plot") -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_")
    return token or fallback


def _pump_events(app: QtWidgets.QApplication, iterations: int = 6) -> None:
    for _ in range(max(1, iterations)):
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)


def _default_shape_memory_inputs() -> list[Path]:
    base = Path("sample_data/manual_stress-strain")
    return [
        (base / "Ni50Fe27Ga23 5_4 50mA.txt").resolve(),
        (base / "Ni50Fe27Ga23 5_4 87mA.txt").resolve(),
    ]


def _collect_shape_memory_tabs(window: PyPlotWorkbench) -> list[tuple[QtWidgets.QWidget, Any]]:
    tabs: list[tuple[QtWidgets.QWidget, Any]] = []
    for index in range(window.tab_widget.count()):
        tab = window.tab_widget.widget(index)
        if not isinstance(tab, QtWidgets.QWidget):
            continue
        descriptor = window._tab_descriptors.get(tab)
        if descriptor is None:
            continue
        if window._tab_plugin_name(descriptor) != _SHAPE_MEMORY_PLUGIN_NAME:
            continue
        tabs.append((tab, descriptor))
    return tabs


def _set_shape_memory_layout(window: PyPlotWorkbench, mode: str) -> None:
    plugin = window._current_plugin
    if plugin is None:
        return
    combo = getattr(plugin, "_layout_mode_combo", None)
    if not isinstance(combo, QtWidgets.QComboBox):
        return
    target_token = LAYOUT_DUAL_AXIS if mode == "dual" else LAYOUT_SEPARATE_TABS
    index = combo.findData(target_token)
    if index >= 0:
        combo.setCurrentIndex(index)
    persist = getattr(plugin, "_persist_layout_mode_setting", None)
    if callable(persist):
        persist()


def _capture_matplotlib_outputs(
    window: PyPlotWorkbench,
    app: QtWidgets.QApplication,
    output_dir: Path,
    summary: dict[str, Any],
) -> tuple[list[Path], list[Path], list[Path]]:
    figure_images: list[Path] = []
    canvas_images: list[Path] = []
    subwindow_images: list[Path] = []
    tab_records: list[dict[str, Any]] = []

    tabs = _collect_shape_memory_tabs(window)
    subwindow_for = getattr(window.tab_widget, "_subwindow_for", None)
    for index, (tab, descriptor) in enumerate(tabs, start=1):
        window.tab_widget.setCurrentWidget(tab)
        _pump_events(app, iterations=4)

        title = str(getattr(descriptor, "title", "") or f"tab_{index}")
        stem = _safe_stem(title, fallback=f"tab_{index:02d}")
        canvas = getattr(descriptor, "canvas", None)
        figure = getattr(canvas, "figure", None)

        figure_path = output_dir / f"matplotlib_{index:02d}_{stem}.png"
        if figure is not None:
            try:
                figure.savefig(figure_path, dpi=180)
                figure_images.append(figure_path)
            except Exception:
                pass

        canvas_path = output_dir / f"canvas_{index:02d}_{stem}.png"
        if isinstance(canvas, QtWidgets.QWidget):
            try:
                canvas.grab().save(str(canvas_path))
                canvas_images.append(canvas_path)
            except Exception:
                pass

        subwindow_path = output_dir / f"subwindow_{index:02d}_{stem}.png"
        subwindow_geometry: dict[str, int] | None = None
        if callable(subwindow_for):
            try:
                subwindow = subwindow_for(tab)
            except Exception:
                subwindow = None
            if isinstance(subwindow, QtWidgets.QWidget):
                try:
                    rect = subwindow.geometry()
                    subwindow_geometry = {
                        "x": int(rect.x()),
                        "y": int(rect.y()),
                        "width": int(rect.width()),
                        "height": int(rect.height()),
                    }
                    subwindow.grab().save(str(subwindow_path))
                    subwindow_images.append(subwindow_path)
                except Exception:
                    subwindow_geometry = None

        figure_width = None
        figure_height = None
        axis_positions: list[list[float]] = []
        if figure is not None:
            try:
                width, height = figure.get_size_inches()
                figure_width = float(width)
                figure_height = float(height)
                for axis in list(getattr(figure, "axes", [])):
                    try:
                        bounds = list(axis.get_position().bounds)
                    except Exception:
                        continue
                    axis_positions.append([float(v) for v in bounds])
            except Exception:
                figure_width = None
                figure_height = None

        canvas_size = None
        if isinstance(canvas, QtWidgets.QWidget):
            try:
                size = canvas.size()
                canvas_size = {"width": int(size.width()), "height": int(size.height())}
            except Exception:
                canvas_size = None

        tab_records.append(
            {
                "title": title,
                "figure_png": str(figure_path) if figure_path.exists() else None,
                "canvas_png": str(canvas_path) if canvas_path.exists() else None,
                "subwindow_png": str(subwindow_path) if subwindow_path.exists() else None,
                "figure_size_inches": [figure_width, figure_height],
                "canvas_size_px": canvas_size,
                "subwindow_geometry": subwindow_geometry,
                "axis_positions": axis_positions,
            }
        )

    summary["matplotlib_tabs"] = tab_records
    return figure_images, canvas_images, subwindow_images


def _capture_origin_outputs(
    window: PyPlotWorkbench,
    output_dir: Path,
    plugin_name: str,
    summary: dict[str, Any],
) -> tuple[list[Path], list[str], list[str]]:
    origin_images: list[Path] = []
    origin_errors: list[str] = []
    origin_warnings: list[str] = []

    window._prune_shared_plot_workbooks()
    workbooks = window._shared_plot_workbooks_for_plugin(plugin_name)
    if not workbooks:
        for tab, descriptor in _collect_shape_memory_tabs(window):
            window._register_shared_plot_workbook_for_tab(tab, descriptor)
        window._prune_shared_plot_workbooks()
        workbooks = window._shared_plot_workbooks_for_plugin(plugin_name)

    if not workbooks:
        origin_warnings.append("No shared plot workbooks were available for Origin capture.")
        summary["origin"] = {
            "exported": 0,
            "plotted": 0,
            "errors": [],
            "warnings": list(origin_warnings),
            "images": [],
        }
        return origin_images, origin_errors, origin_warnings

    def _graph_callback(graph: Any, workbook: WorkbookData, worksheet: WorksheetData) -> None:
        stem = _safe_stem(f"{workbook.name}_{worksheet.name}", fallback="origin")
        path = output_dir / f"origin_{len(origin_images) + 1:02d}_{stem}.png"
        save_fig = getattr(graph, "save_fig", None)
        if not callable(save_fig):
            origin_warnings.append(f"Origin graph object has no save_fig for {workbook.name}/{worksheet.name}.")
            return
        save_fig(str(path))
        if path.exists():
            origin_images.append(path)
        else:
            origin_warnings.append(f"Origin save_fig did not produce file: {path}")

    try:
        exported, plotted, errors = window._push_workbooks_to_origin(
            workbooks,
            create_graphs=True,
            graph_callback=_graph_callback,
            keep_origin_open=False,
        )
    except ModuleNotFoundError as exc:
        origin_errors.append(str(exc))
        exported = 0
        plotted = 0
        errors = []
    except Exception as exc:
        origin_errors.append(str(exc))
        exported = 0
        plotted = 0
        errors = []

    if errors:
        origin_errors.extend(str(item) for item in errors)

    summary["origin"] = {
        "exported": int(exported),
        "plotted": int(plotted),
        "errors": list(origin_errors),
        "warnings": list(origin_warnings),
        "images": [str(path) for path in origin_images],
    }
    return origin_images, origin_errors, origin_warnings


def run_shape_memory_visual_check(
    *,
    output_dir: Path,
    input_paths: Iterable[Path] | None = None,
    layout_mode: str = "dual",
    include_origin: bool = True,
    show_window: bool = False,
) -> VisualCheckResult:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = VisualCheckResult(output_dir=output_dir)

    requested_inputs = list(input_paths or _default_shape_memory_inputs())
    if not requested_inputs:
        result.errors.append("No input files were provided for shape-memory visual check.")
        return result

    resolved_inputs: list[Path] = []
    for candidate in requested_inputs:
        path = Path(candidate).resolve()
        if not path.exists():
            result.errors.append(f"Input file not found: {path}")
            continue
        if not path.is_file():
            result.errors.append(f"Input path is not a file: {path}")
            continue
        resolved_inputs.append(path)
    if not resolved_inputs:
        return result

    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(["visual-check"])
        created_app = True
    ensure_app_theme(app)

    summary: dict[str, Any] = {
        "plugin": _SHAPE_MEMORY_PLUGIN_NAME,
        "layout_mode": layout_mode,
        "input_paths": [str(path) for path in resolved_inputs],
        "output_dir": str(output_dir),
    }

    window: PyPlotWorkbench | None = None
    try:
        window = PyPlotWorkbench(initial_plotter=_SHAPE_MEMORY_PLUGIN_NAME)
        window.resize(1800, 1080)
        window.show()
        _pump_events(app)

        if not window._activate_plotter_for_project_load(_SHAPE_MEMORY_PLUGIN_NAME):
            result.errors.append(f"Failed to activate plugin: {_SHAPE_MEMORY_PLUGIN_NAME}")
            return result
        _set_shape_memory_layout(window, layout_mode)
        window._commit_selected_paths(resolved_inputs)
        _pump_events(app)

        plugin = window._current_plugin
        if plugin is None:
            result.errors.append("Shape-memory plugin instance is not available.")
            return result
        plugin.load_data()
        plugin.generate()
        _pump_events(app, iterations=12)

        if show_window:
            _pump_events(app, iterations=6)

        window_path = output_dir / "pyplot_window.png"
        tab_widget_path = output_dir / "pyplot_tab_widget.png"
        try:
            window.grab().save(str(window_path))
            result.window_image = window_path
        except Exception as exc:
            result.warnings.append(f"Failed to capture PyPlot window: {exc}")
        try:
            window.tab_widget.grab().save(str(tab_widget_path))
            result.tab_widget_image = tab_widget_path
        except Exception as exc:
            result.warnings.append(f"Failed to capture tab widget: {exc}")

        (
            result.matplotlib_images,
            result.matplotlib_canvas_images,
            result.subwindow_images,
        ) = _capture_matplotlib_outputs(window, app, output_dir, summary)

        if include_origin:
            origin_images, origin_errors, origin_warnings = _capture_origin_outputs(
                window,
                output_dir,
                _SHAPE_MEMORY_PLUGIN_NAME,
                summary,
            )
            result.origin_images = origin_images
            result.errors.extend(origin_errors)
            result.warnings.extend(origin_warnings)

    finally:
        if window is not None:
            clear_dirty = getattr(window, "_clear_project_dirty", None)
            if callable(clear_dirty):
                try:
                    clear_dirty()
                except Exception:
                    pass
            try:
                setattr(window, "_project_dirty", False)
            except Exception:
                pass
            try:
                window.close()
            except Exception:
                pass
        if created_app:
            app.quit()

    summary.update(
        {
            "window_png": str(result.window_image) if result.window_image is not None else None,
            "tab_widget_png": str(result.tab_widget_image) if result.tab_widget_image is not None else None,
            "matplotlib_images": [str(path) for path in result.matplotlib_images],
            "matplotlib_canvas_images": [str(path) for path in result.matplotlib_canvas_images],
            "subwindow_images": [str(path) for path in result.subwindow_images],
            "origin_images": [str(path) for path in result.origin_images],
            "errors": list(result.errors),
            "warnings": list(result.warnings),
        }
    )
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    result.summary_json = summary_path
    return result


__all__ = ["VisualCheckResult", "run_shape_memory_visual_check"]
