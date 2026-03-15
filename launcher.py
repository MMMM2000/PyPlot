from __future__ import annotations

import argparse
import sys
import os
import time
import logging
import traceback
import json
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from functools import lru_cache
from importlib import import_module
from typing import TYPE_CHECKING, Any, Callable, Dict, Tuple, cast, Protocol

from PyQt6 import QtWidgets, QtGui, QtCore


LauncherFactory = Callable[..., QtWidgets.QWidget | None]

if TYPE_CHECKING:
    from plotting.shared import common as _common_module


class _DeveloperOptionsProtocol(Protocol):
    experiments_visibility_changed: QtCore.pyqtBoundSignal

    def show_experiments(self) -> bool:
        ...

    def set_show_experiments(self, enabled: bool) -> None:
        ...


def _lazy(module: str, attr: str = "main") -> LauncherFactory:
    def factory(*args: Any, **kwargs: Any) -> QtWidgets.QWidget | None:
        module_obj = import_module(module)
        target: Any = module_obj
        for segment in attr.split("."):
            target = getattr(target, segment)
        if not callable(target):
            raise TypeError(f"{module}.{attr} is not callable")
        callable_target = cast(LauncherFactory, target)
        return callable_target(*args, **kwargs)

    return factory


LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_install_standard_menu() -> Callable[..., QtWidgets.QMenuBar]:
    from plotting.shared.utils import install_standard_menu

    return install_standard_menu


@lru_cache(maxsize=1)
def _load_developer_options() -> Callable[[], "_DeveloperOptionsProtocol"]:
    module = import_module("plotting.shared.developer")
    return cast(Callable[[], "_DeveloperOptionsProtocol"], getattr(module, "developer_options"))


def _install_launcher_menu(*args: Any, **kwargs: Any) -> QtWidgets.QMenuBar:
    install = _load_install_standard_menu()
    return install(*args, **kwargs)


def _reset_outlier_flags() -> None:
    try:
        common_module = cast(
            "_common_module", import_module("plotting.shared.common")
        )
    except Exception:
        LOGGER.debug("Unable to load plotting.shared.common", exc_info=True)
        return
    common_module.CHECK_OUTLIERS = False
    common_module.AUTO_REMOVE_OUTLIERS = False


def _schedule_theme_application(app: QtWidgets.QApplication) -> None:
    def _apply_theme() -> None:
        try:
            from plotting.shared.theme import ensure_app_theme
        except Exception:
            LOGGER.debug("Unable to import plotting.shared.theme", exc_info=True)
            return
        try:
            ensure_app_theme(app)
        except Exception:
            LOGGER.warning("Failed to apply app theme", exc_info=True)

    QtCore.QTimer.singleShot(0, _apply_theme)


def _crash_log_path() -> Path:
    return Path(__file__).resolve().parent / "logs" / "crash_log.txt"


def _append_crash_log(message: str) -> None:
    try:
        from plotting.shared.logfiles import append_text_with_rotation
    except Exception:
        return
    try:
        append_text_with_rotation(
            _crash_log_path(),
            message,
            max_bytes=1_000_000,
            backup_count=5,
        )
    except Exception:
        pass


def _install_crash_log_hook() -> None:
    previous_hook = sys.excepthook

    def _hook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous_hook(exc_type, exc_value, exc_tb)
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        trace = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _append_crash_log(f"[{timestamp}] Unhandled exception\n{trace}\n")
        previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


@lru_cache(maxsize=1)
def _load_pyplot_metadata() -> Tuple[LauncherFactory, Tuple[str, ...]]:
    from plotting.pyplot.app import main as pyplot_main
    from plotting.plugins import builtin_plugin_registry

    plugin_names = tuple(sorted(builtin_plugin_registry()))
    return cast(LauncherFactory, pyplot_main), plugin_names


def _plotter_registry() -> Dict[str, LauncherFactory]:
    pyplot_main, plugin_names = _load_pyplot_metadata()
    registry: Dict[str, LauncherFactory] = {
        "PyPlot": lambda: pyplot_main(initial_plotter=None)
    }
    for name in plugin_names:
        registry[name] = (
            lambda plotter_name=name: pyplot_main(initial_plotter=plotter_name)
        )
    return registry


@lru_cache(maxsize=1)
def _load_experiments_registry() -> Dict[str, LauncherFactory]:
    try:
        from experiments import EXPERIMENTS as experiments_map
    except Exception as exc:
        LOGGER.warning("Failed to load experiments module", exc_info=exc)
        return {}
    return dict(experiments_map)


def _build_registry() -> dict[str, Dict[str, LauncherFactory]]:
    registry: dict[str, Dict[str, LauncherFactory]] = {
        "loggers": dict(LOGGERS),
        "plotters": _plotter_registry(),
        "emulators": dict(EMULATORS),
    }
    if BUILDERS:
        registry["builders"] = dict(BUILDERS)
    experiments = _load_experiments_registry()
    if experiments:
        registry["experiments"] = experiments
    return registry


def launch_pyplot(initial: str | None = None) -> QtWidgets.QWidget | None:
    """Open the base plotter workbench, optionally selecting a script."""

    pyplot_main, _ = _load_pyplot_metadata()
    return pyplot_main(initial_plotter=initial)


def _create_launcher_icon() -> QtGui.QIcon:
    """Return the shared launcher icon, generating it on first use."""

    cached: QtGui.QIcon | None = getattr(_create_launcher_icon, "_cache", None)
    if isinstance(cached, QtGui.QIcon):
        return cached
    size = 256
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    background = QtGui.QColor("#1f2937")
    painter.setBrush(background)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    rect = pixmap.rect().adjusted(12, 12, -12, -12)
    radius = size * 0.18
    painter.drawRoundedRect(rect, radius, radius)
    painter.setPen(QtGui.QPen(QtGui.QColor("#f9fafb")))
    font = painter.font()
    font.setBold(True)
    font.setPointSize(88)
    painter.setFont(font)
    painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "Py")
    painter.end()
    icon = QtGui.QIcon(pixmap)
    setattr(_create_launcher_icon, "_cache", icon)
    return icon


class _AutomationRecipeError(Exception):
    """Raised when an automation recipe is invalid or unsupported."""


@dataclass
class _PyPlotAutomationRequest:
    plugin_name: str | None = None
    import_entries: list[Path] = field(default_factory=list)
    load_project_path: Path | None = None
    build_graphs: list[dict[str, Any]] = field(default_factory=list)
    create_figures: list[dict[str, Any]] = field(default_factory=list)
    generate: bool = False
    open_graph_format: bool = False
    open_origin: bool = False
    window_image_path: Path | None = None
    current_plot_image_path: Path | None = None
    plot_images_dir: Path | None = None
    export_all_figures_dir: Path | None = None
    export_all_figures_format: str | None = None
    export_all_figures_dpi: float | None = None
    export_all_figures_transparent: bool = False
    review_output_dir: Path | None = None
    review_dark_gui: bool = False
    summary_path: Path | None = None
    save_project_path: Path | None = None
    show_window: bool = False
    wait_ms: int = 0
    manifest_kind: str = "pyplot"
    manifest_version: int = 1


def _absolute_path(path: Path | None) -> str | None:
    if not isinstance(path, Path):
        return None
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def _safe_automation_label(value: str, fallback: str = "plot") -> str:
    token = "".join(
        ch if ch.isalnum() or ch in {" ", "-", "_"} else "_"
        for ch in str(value).strip()
    ).strip(" ._")
    return token or fallback


def _normalise_project_path(path: Path, *, suffix: str = ".pypj") -> Path:
    if path.suffix.lower() == suffix.lower():
        return path
    return path.with_suffix(suffix)


def _validate_pyplot_plugin_name(plugin_name: str | None) -> None:
    if plugin_name is None:
        return
    _pyplot_main, plugin_names = _load_pyplot_metadata()
    if plugin_name not in plugin_names:
        raise _AutomationRecipeError(
            f"Unknown PyPlot plugin '{plugin_name}'. "
            f"Available plugins: {', '.join(plugin_names)}"
        )


def _origin_is_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import_module("originpro")
    except Exception:
        return False
    return True


def _validate_origin_request(enabled: bool) -> None:
    if enabled and not _origin_is_available():
        raise _AutomationRecipeError(
            "Origin automation is unavailable in this environment. "
            "It requires Windows with the 'originpro' dependency installed."
        )


def _resolve_recipe_path_value(
    value: object,
    *,
    base_dir: Path,
    field_name: str,
) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _AutomationRecipeError(
            f"Automation recipe field '{field_name}' must be a non-empty string when provided."
        )
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise _AutomationRecipeError(f"{label} file not found: {path}") from exc
    except Exception as exc:
        raise _AutomationRecipeError(f"Failed to read {label} file {path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _AutomationRecipeError(f"{label} file is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _AutomationRecipeError(f"{label} file must contain a JSON object.")
    return payload


def _validate_pyplot_project_file(path: Path) -> None:
    payload = _load_json_object(path, label="PyPlot project")
    if payload.get("kind") != "pyplot":
        raise _AutomationRecipeError(
            f"Project '{path}' is not a PyPlot project (expected kind 'pyplot')."
        )
    if payload.get("version") != 1:
        raise _AutomationRecipeError(
            f"Project '{path}' uses unsupported PyPlot project version {payload.get('version')!r}."
        )


def _load_automation_recipe_request(recipe_path: Path) -> _PyPlotAutomationRequest:
    recipe = _load_json_object(recipe_path, label="Automation recipe")
    base_dir = recipe_path.parent

    kind = recipe.get("kind")
    if kind != "pyplot":
        if kind == "builder":
            raise _AutomationRecipeError(
                "Automation recipe kind 'builder' is reserved for future work and is not implemented yet."
            )
        raise _AutomationRecipeError(
            f"Unsupported automation recipe kind {kind!r}. Only 'pyplot' is supported in v1."
        )

    version = recipe.get("version")
    if version != 1:
        raise _AutomationRecipeError(
            f"Unsupported automation recipe version {version!r}. Only version 1 is supported."
        )

    plugin_name = recipe.get("plugin")
    if plugin_name is not None and (not isinstance(plugin_name, str) or not plugin_name.strip()):
        raise _AutomationRecipeError("Automation recipe field 'plugin' must be a non-empty string.")
    if isinstance(plugin_name, str):
        plugin_name = plugin_name.strip()
    _validate_pyplot_plugin_name(plugin_name)

    load_project_path = _resolve_recipe_path_value(
        recipe.get("load_project"),
        base_dir=base_dir,
        field_name="load_project",
    )
    if isinstance(load_project_path, Path):
        load_project_path = _normalise_project_path(load_project_path)
        _validate_pyplot_project_file(load_project_path)

    imports_raw = recipe.get("imports", [])
    if imports_raw is None:
        imports_raw = []
    if not isinstance(imports_raw, list):
        raise _AutomationRecipeError("Automation recipe field 'imports' must be an array of paths.")
    import_entries: list[Path] = []
    for index, entry in enumerate(imports_raw):
        resolved = _resolve_recipe_path_value(
            entry,
            base_dir=base_dir,
            field_name=f"imports[{index}]",
        )
        if resolved is None:
            continue
        if not resolved.exists():
            raise _AutomationRecipeError(f"Automation import path does not exist: {resolved}")
        import_entries.append(resolved)

    generate = recipe.get("generate", False)
    if not isinstance(generate, bool):
        raise _AutomationRecipeError("Automation recipe field 'generate' must be true or false.")

    open_origin = recipe.get("open_origin", False)
    if not isinstance(open_origin, bool):
        raise _AutomationRecipeError("Automation recipe field 'open_origin' must be true or false.")
    _validate_origin_request(open_origin)

    wait_ms = recipe.get("wait_ms", 0)
    if not isinstance(wait_ms, int) or wait_ms < 0:
        raise _AutomationRecipeError("Automation recipe field 'wait_ms' must be a non-negative integer.")

    show_window = recipe.get("show_window", False)
    if not isinstance(show_window, bool):
        raise _AutomationRecipeError("Automation recipe field 'show_window' must be true or false.")

    save_project_path = _resolve_recipe_path_value(
        recipe.get("save_project"),
        base_dir=base_dir,
        field_name="save_project",
    )
    if isinstance(save_project_path, Path):
        save_project_path = _normalise_project_path(save_project_path)

    exports = recipe.get("exports", {})
    if exports is None:
        exports = {}
    if not isinstance(exports, dict):
        raise _AutomationRecipeError("Automation recipe field 'exports' must be an object.")

    window_image_path = _resolve_recipe_path_value(
        exports.get("window_image"),
        base_dir=base_dir,
        field_name="exports.window_image",
    )
    current_plot_image_path = _resolve_recipe_path_value(
        exports.get("current_plot_image"),
        base_dir=base_dir,
        field_name="exports.current_plot_image",
    )
    plot_images_dir = _resolve_recipe_path_value(
        exports.get("plot_images_dir"),
        base_dir=base_dir,
        field_name="exports.plot_images_dir",
    )
    export_all_figures = exports.get("all_figures")
    export_all_figures_dir = None
    export_all_figures_format = None
    export_all_figures_dpi = None
    export_all_figures_transparent = False
    if export_all_figures is not None:
        if not isinstance(export_all_figures, dict):
            raise _AutomationRecipeError("Automation recipe field 'exports.all_figures' must be an object.")
        export_all_figures_dir = _resolve_recipe_path_value(
            export_all_figures.get("dir"),
            base_dir=base_dir,
            field_name="exports.all_figures.dir",
        )
        export_all_figures_format = str(export_all_figures.get("format") or "png").strip().lower()
        if export_all_figures_format not in {"png", "pdf", "svg", "tif", "eps"}:
            raise _AutomationRecipeError("Automation recipe exports.all_figures.format must be one of png/pdf/svg/tif/eps.")
        dpi_value = export_all_figures.get("dpi")
        if dpi_value is not None:
            try:
                export_all_figures_dpi = float(dpi_value)
            except Exception as exc:
                raise _AutomationRecipeError("Automation recipe exports.all_figures.dpi must be numeric.") from exc
        export_all_figures_transparent = bool(export_all_figures.get("transparent", False))
    review_output_dir = None
    review_dark_gui = False
    review_capture = exports.get("review_screenshots")
    if review_capture is not None:
        if not isinstance(review_capture, dict):
            raise _AutomationRecipeError("Automation recipe field 'exports.review_screenshots' must be an object.")
        review_output_dir = _resolve_recipe_path_value(
            review_capture.get("dir"),
            base_dir=base_dir,
            field_name="exports.review_screenshots.dir",
        )
        review_dark_gui = bool(review_capture.get("dark_gui", False))
    summary_path = _resolve_recipe_path_value(
        recipe.get("manifest_path"),
        base_dir=base_dir,
        field_name="manifest_path",
    )

    if plugin_name is None and (generate or open_origin):
        raise _AutomationRecipeError(
            "Automation recipe field 'plugin' is required when generate or open_origin are requested."
        )

    build_graphs = recipe.get("build_graphs", [])
    if build_graphs is None:
        build_graphs = []
    if not isinstance(build_graphs, list) or not all(isinstance(entry, dict) for entry in build_graphs):
        raise _AutomationRecipeError("Automation recipe field 'build_graphs' must be an array of objects.")

    create_figures = recipe.get("create_figures", [])
    if create_figures is None:
        create_figures = []
    if not isinstance(create_figures, list) or not all(isinstance(entry, dict) for entry in create_figures):
        raise _AutomationRecipeError("Automation recipe field 'create_figures' must be an array of objects.")

    return _PyPlotAutomationRequest(
        plugin_name=plugin_name,
        import_entries=import_entries,
        load_project_path=load_project_path,
        build_graphs=[dict(entry) for entry in build_graphs],
        create_figures=[dict(entry) for entry in create_figures],
        generate=generate,
        open_origin=open_origin,
        window_image_path=window_image_path,
        current_plot_image_path=current_plot_image_path,
        plot_images_dir=plot_images_dir,
        export_all_figures_dir=export_all_figures_dir,
        export_all_figures_format=export_all_figures_format,
        export_all_figures_dpi=export_all_figures_dpi,
        export_all_figures_transparent=export_all_figures_transparent,
        review_output_dir=review_output_dir,
        review_dark_gui=review_dark_gui,
        summary_path=summary_path,
        save_project_path=save_project_path,
        show_window=show_window,
        wait_ms=wait_ms,
        manifest_kind="pyplot",
        manifest_version=1,
    )


def _parse_launcher_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--automation-recipe",
        default=None,
        help="Run a machine-facing automation recipe JSON file.",
    )
    parser.add_argument(
        "--pyplot-list-plugins",
        action="store_true",
        help="List available PyPlot plugin names and exit.",
    )
    parser.add_argument(
        "--pyplot-plugin",
        default=None,
        help="Open PyPlot directly with the selected plugin active.",
    )
    parser.add_argument(
        "--pyplot-import",
        action="append",
        default=[],
        help="File or folder to import in PyPlot automation mode. Can be provided multiple times.",
    )
    parser.add_argument(
        "--pyplot-plot",
        action="store_true",
        help="Trigger the active plugin plot/generate action in PyPlot automation mode.",
    )
    parser.add_argument(
        "--pyplot-open-graph-format",
        action="store_true",
        help="Open the shared Graph formatting window in PyPlot automation mode.",
    )
    parser.add_argument(
        "--pyplot-open-origin",
        action="store_true",
        help="Trigger the active plugin Origin export action in PyPlot automation mode.",
    )
    parser.add_argument(
        "--pyplot-screenshot",
        default=None,
        help="Save a screenshot of the PyPlot window to this path.",
    )
    parser.add_argument(
        "--pyplot-plot-image",
        default=None,
        help="Save the current active Matplotlib graph image to this path.",
    )
    parser.add_argument(
        "--pyplot-summary-json",
        default=None,
        help="Write a JSON summary of the automation run to this path.",
    )
    parser.add_argument(
        "--pyplot-show-window",
        action="store_true",
        help="Keep the PyPlot window visible during automation.",
    )
    parser.add_argument(
        "--pyplot-wait-ms",
        type=int,
        default=0,
        help="Wait this many milliseconds after actions before capturing artifacts.",
    )
    parser.add_argument(
        "--visual-check",
        action="store_true",
        help="Run automated visual verification flow instead of opening the launcher UI.",
    )
    parser.add_argument(
        "--visual-plugin",
        default="shape-memory",
        help="Plugin visual-check target. Currently supported: shape-memory.",
    )
    parser.add_argument(
        "--visual-input",
        action="append",
        default=[],
        help="Input file path for visual-check mode. Can be provided multiple times.",
    )
    parser.add_argument(
        "--visual-layout",
        choices=("dual", "separate"),
        default="dual",
        help="Shape-memory graph layout for visual-check mode.",
    )
    parser.add_argument(
        "--visual-output-dir",
        default=str(Path("logs") / "visual_checks"),
        help="Directory where visual-check artifacts will be saved.",
    )
    parser.add_argument(
        "--visual-origin",
        dest="visual_origin",
        action="store_true",
        help="Enable Origin graph export capture in visual-check mode (default).",
    )
    parser.add_argument(
        "--no-visual-origin",
        dest="visual_origin",
        action="store_false",
        help="Disable Origin capture during visual-check mode.",
    )
    parser.add_argument(
        "--visual-show-window",
        action="store_true",
        help="Keep UI visible while visual-check runs.",
    )
    parser.set_defaults(visual_origin=True)
    args, qt_args = parser.parse_known_args(argv)
    return args, qt_args


def _is_pyplot_automation_requested(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "pyplot_list_plugins", False)
        or getattr(args, "pyplot_plugin", None)
        or getattr(args, "pyplot_import", None)
        or getattr(args, "pyplot_plot", False)
        or getattr(args, "pyplot_open_graph_format", False)
        or getattr(args, "pyplot_open_origin", False)
        or getattr(args, "pyplot_screenshot", None)
        or getattr(args, "pyplot_plot_image", None)
        or getattr(args, "pyplot_summary_json", None)
    )


def _run_visual_check(args: argparse.Namespace) -> int:
    plugin_token = str(getattr(args, "visual_plugin", "shape-memory")).strip().lower()
    supported_tokens = {
        "shape-memory",
        "shape_memory",
        "shape-memory-stress-strain",
        "shape_memory_stress_strain",
        "shape memory stress/strain",
    }
    if plugin_token not in supported_tokens:
        print(
            f"Unsupported --visual-plugin '{plugin_token}'. "
            "Only shape-memory visual-check is currently implemented."
        )
        return 2

    from plotting.pyplot.visual_check import run_shape_memory_visual_check

    output_dir = Path(str(getattr(args, "visual_output_dir", "logs/visual_checks"))).expanduser()
    raw_inputs = getattr(args, "visual_input", []) or []
    input_paths = [Path(str(entry)).expanduser() for entry in raw_inputs]
    include_origin = bool(getattr(args, "visual_origin", True))
    layout_mode = str(getattr(args, "visual_layout", "dual")).strip().lower()
    show_window = bool(getattr(args, "visual_show_window", False))

    result = run_shape_memory_visual_check(
        output_dir=output_dir,
        input_paths=input_paths or None,
        layout_mode=layout_mode,
        include_origin=include_origin,
        show_window=show_window,
    )
    print(f"[visual-check] output_dir={result.output_dir}")
    if result.summary_json is not None:
        print(f"[visual-check] summary={result.summary_json}")
    if result.window_image is not None:
        print(f"[visual-check] pyplot_window_png={result.window_image}")
    if result.tab_widget_image is not None:
        print(f"[visual-check] pyplot_tab_widget_png={result.tab_widget_image}")
    print(f"[visual-check] matplotlib_images={len(result.matplotlib_images)}")
    print(f"[visual-check] matplotlib_canvas_images={len(result.matplotlib_canvas_images)}")
    print(f"[visual-check] subwindow_images={len(result.subwindow_images)}")
    print(f"[visual-check] origin_images={len(result.origin_images)}")
    for warning in result.warnings:
        print(f"[visual-check][warn] {warning}")
    for error in result.errors:
        print(f"[visual-check][error] {error}")
    return 1 if result.errors else 0


def _pump_qt_events(app: QtWidgets.QApplication, *, rounds: int = 3) -> None:
    for _ in range(max(1, int(rounds))):
        try:
            app.processEvents()
        except Exception:
            break


def _path_payload(paths: list[Path]) -> list[str]:
    payload: list[str] = []
    for path in paths:
        try:
            payload.append(str(path.resolve()))
        except Exception:
            payload.append(str(path))
    return payload


def _pyplot_summary(window: "PyPlotWorkbench", plugin_name: str | None) -> dict[str, Any]:
    current_tab = window.tab_widget.currentWidget()
    axes = None
    try:
        axes = window._current_axes()
    except Exception:
        axes = None
    current_title = ""
    current_x = ""
    current_y = ""
    if axes is not None:
        try:
            current_title = str(axes.get_title() or "")
        except Exception:
            current_title = ""
        try:
            current_x = str(axes.get_xlabel() or "")
        except Exception:
            current_x = ""
        try:
            current_y = str(axes.get_ylabel() or "")
        except Exception:
            current_y = ""
    visible_plot_tabs: list[str] = []
    for index in range(window.tab_widget.count()):
        try:
            visible_plot_tabs.append(window.tab_widget.tabText(index))
        except Exception:
            continue
    return {
        "plugin": plugin_name,
        "selected_paths": _path_payload(window._selected_paths()),  # type: ignore[attr-defined]
        "worksheet_count": len(getattr(window, "_worksheets", {})),
        "workbook_count": len(getattr(window, "_workbooks", {})),
        "tab_count": int(window.tab_widget.count()),
        "tab_labels": visible_plot_tabs,
        "current_tab_label": window.tab_widget.tabText(window.tab_widget.currentIndex())
        if window.tab_widget.currentIndex() >= 0
        else "",
        "current_title": current_title,
        "current_x_label": current_x,
        "current_y_label": current_y,
        "graph_format_visible": bool(
            isinstance(getattr(window, "_graph_format_dialog", None), QtWidgets.QDialog)
            and window._graph_format_dialog.isVisible()  # type: ignore[attr-defined]
        ),
        "current_tab_has_axes": axes is not None,
        "object_tree_top_level_count": int(getattr(window, "object_tree", QtWidgets.QTreeWidget()).topLevelItemCount()),
        "current_widget_has_descriptor": current_tab in getattr(window, "_tab_descriptors", {}),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _select_pyplot_plugin(window: "PyPlotWorkbench", plugin_name: str) -> None:
    current = getattr(window, "_current_plotter_name", None)
    if current == plugin_name and getattr(window, "_current_plugin", None) is not None:
        return
    combo = getattr(window, "_plotter_combo", None)
    if isinstance(combo, QtWidgets.QComboBox):
        index = combo.findData(plugin_name)
        if index < 0:
            raise RuntimeError(f"PyPlot plugin '{plugin_name}' is not available in this session.")
        combo.setCurrentIndex(index)
    if getattr(window, "_current_plotter_name", None) == plugin_name:
        return
    apply_selected = getattr(window, "_apply_selected_plotter", None)
    if callable(apply_selected):
        apply_selected()
    if getattr(window, "_current_plotter_name", None) != plugin_name:
        raise RuntimeError(f"Failed to activate PyPlot plugin '{plugin_name}'.")


def _export_visible_plot_images(
    window: "PyPlotWorkbench",
    app: QtWidgets.QApplication,
    output_dir: Path,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []
    current_tab = window.tab_widget.currentWidget()
    visible_checker = getattr(window, "_is_tab_visible", None)
    export_index = 0
    try:
        for index in range(window.tab_widget.count()):
            tab = window.tab_widget.widget(index)
            if not isinstance(tab, QtWidgets.QWidget):
                continue
            descriptor = getattr(window, "_tab_descriptors", {}).get(tab)
            if descriptor is None:
                continue
            if callable(visible_checker) and not bool(visible_checker(tab)):
                continue
            canvas = getattr(descriptor, "canvas", None)
            figure = getattr(canvas, "figure", None)
            if figure is None:
                continue
            export_index += 1
            label = ""
            try:
                label = window.tab_widget.tabText(index)
            except Exception:
                label = ""
            if not label:
                label = str(getattr(descriptor, "title", "") or f"plot_{export_index}")
            safe_label = _safe_automation_label(label, fallback=f"plot_{export_index:02d}")
            target = output_dir / f"{export_index:02d}-{safe_label}.png"
            window.tab_widget.setCurrentWidget(tab)
            _pump_qt_events(app, rounds=3)
            figure.savefig(target, dpi=160)
            exported.append(target)
    finally:
        if current_tab is not None:
            try:
                window.tab_widget.setCurrentWidget(current_tab)
            except Exception:
                pass
            _pump_qt_events(app, rounds=2)
    return exported


def _capture_review_screenshots(
    window: "PyPlotWorkbench",
    app: QtWidgets.QApplication,
    output_dir: Path,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    review_paths: list[Path] = []
    tab_widget = getattr(window, "tab_widget", None)
    current_index = -1
    current_widget: QtWidgets.QWidget | None = None
    visibility_state: list[tuple[int, bool]] = []
    previous_size = window.size()
    if isinstance(tab_widget, QtWidgets.QWidget):
        try:
            current_index = int(tab_widget.currentIndex())
        except Exception:
            current_index = -1
        try:
            current_widget = tab_widget.currentWidget()
        except Exception:
            current_widget = None
        for index in range(getattr(tab_widget, "count", lambda: 0)()):
            visible = True
            try:
                visible = bool(tab_widget.isTabVisible(index))
            except Exception:
                pass
            visibility_state.append((index, visible))
        if current_widget is not None:
            for index, was_visible in visibility_state:
                widget = None
                try:
                    widget = tab_widget.widget(index)
                except Exception:
                    widget = None
                should_show = bool(was_visible and widget is current_widget)
                try:
                    tab_widget.setTabVisible(index, should_show)
                except Exception:
                    if isinstance(widget, QtWidgets.QWidget):
                        widget.setVisible(should_show)
            if current_index >= 0:
                try:
                    tab_widget.setCurrentIndex(current_index)
                except Exception:
                    pass
    try:
        window.resize(max(previous_size.width(), 1720), max(previous_size.height(), 1080))
        window.show()
        try:
            window.raise_()
            window.activateWindow()
        except Exception:
            pass
        arranger = getattr(tab_widget, "_arrange_subwindows", None)
        if callable(arranger):
            try:
                arranger()
            except Exception:
                pass
        _pump_qt_events(app, rounds=6)
        for sub in list(getattr(tab_widget, "_ordered_visible_subwindows", lambda: [])() if tab_widget is not None else []):
            try:
                canvas = getattr(tab_widget, "_canvas_for_subwindow", lambda _sub: None)(sub)
            except Exception:
                canvas = None
            if canvas is None:
                continue
            try:
                canvas.draw()
            except Exception:
                try:
                    canvas.draw_idle()
                except Exception:
                    pass
            try:
                canvas.repaint()
            except Exception:
                pass
        try:
            window.repaint()
        except Exception:
            pass
        _pump_qt_events(app, rounds=8)
        gui_target = output_dir / "pyplot-gui.png"
        window_pixmap = window.grab()
        if not window_pixmap.isNull() and current_widget is not None:
            subwindow_for = getattr(tab_widget, "_subwindow_for", None)
            if callable(subwindow_for):
                try:
                    subwindow = subwindow_for(current_widget)
                except Exception:
                    subwindow = None
                if isinstance(subwindow, QtWidgets.QWidget):
                    subwindow_pixmap = subwindow.grab()
                    if not subwindow_pixmap.isNull():
                        try:
                            origin = subwindow.mapTo(window, QtCore.QPoint(0, 0))
                            painter = QtGui.QPainter(window_pixmap)
                            painter.drawPixmap(
                                QtCore.QRect(
                                    origin.x(),
                                    origin.y(),
                                    subwindow.width(),
                                    subwindow.height(),
                                ),
                                subwindow_pixmap,
                            )
                            painter.end()
                        except Exception:
                            pass
            current_canvas = None
            try:
                current_canvas = window._current_canvas()  # type: ignore[attr-defined]
            except Exception:
                current_canvas = None
            current_figure = getattr(current_canvas, "figure", None) if current_canvas is not None else None
            if current_canvas is not None and current_figure is not None:
                try:
                    buffer = io.BytesIO()
                    target_dpi = max(72.0, float(getattr(current_figure, "dpi", 72.0) or 72.0))
                    current_figure.savefig(buffer, format="png", dpi=target_dpi)
                    overlay = QtGui.QPixmap()
                    if overlay.loadFromData(buffer.getvalue(), "PNG"):
                        origin = current_canvas.mapTo(window, QtCore.QPoint(0, 0))
                        painter = QtGui.QPainter(window_pixmap)
                        painter.drawPixmap(
                            QtCore.QRect(
                                origin.x(),
                                origin.y(),
                                current_canvas.width(),
                                current_canvas.height(),
                            ),
                            overlay,
                        )
                        painter.end()
                except Exception:
                    pass
        if not window_pixmap.isNull() and window_pixmap.save(str(gui_target)):
            review_paths.append(gui_target)
        axes = window._current_axes()  # type: ignore[attr-defined]
        if axes is not None and getattr(axes, "figure", None) is not None:
            target = output_dir / "current-figure.png"
            axes.figure.savefig(target, dpi=180)
            review_paths.append(target)
    finally:
        if isinstance(tab_widget, QtWidgets.QWidget):
            for index, visible in visibility_state:
                widget = None
                try:
                    widget = tab_widget.widget(index)
                except Exception:
                    widget = None
                try:
                    tab_widget.setTabVisible(index, visible)
                except Exception:
                    if isinstance(widget, QtWidgets.QWidget):
                        widget.setVisible(visible)
            if current_index >= 0:
                try:
                    tab_widget.setCurrentIndex(current_index)
                except Exception:
                    pass
            arranger = getattr(tab_widget, "_arrange_subwindows", None)
            if callable(arranger):
                try:
                    arranger()
                except Exception:
                    pass
        window.resize(previous_size)
        _pump_qt_events(app, rounds=4)
    return review_paths


def _execute_pyplot_automation_request(
    request: _PyPlotAutomationRequest,
    qt_args: list[str],
) -> dict[str, Any]:
    from plotting.pyplot.app import PyPlotWorkbench
    from plotting.shared.toolkit import theme_manager

    created_app = False
    app = QtWidgets.QApplication.instance()
    if not isinstance(app, QtWidgets.QApplication):
        if not request.show_window:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication([sys.argv[0], *qt_args])
        created_app = True
        try:
            app.setQuitOnLastWindowClosed(False)
        except Exception:
            pass
        _schedule_theme_application(app)
    baseline_top_level_widgets = set(app.topLevelWidgets()) if isinstance(app, QtWidgets.QApplication) else set()

    window: PyPlotWorkbench | None = None
    imported_paths: list[Path] = []
    exported_plot_paths: list[Path] = []
    exported_all_figure_paths: list[Path] = []
    review_paths: list[Path] = []
    saved_project_path: Path | None = None
    try:
        window = PyPlotWorkbench(initial_plotter=request.plugin_name)
        if (
            request.show_window
            or request.window_image_path is not None
            or request.review_output_dir is not None
        ):
            window.show()
        _pump_qt_events(app, rounds=4)

        if isinstance(request.load_project_path, Path):
            window._load_project_from_path(request.load_project_path)  # type: ignore[attr-defined]
            _pump_qt_events(app, rounds=8)
            if getattr(window, "_project_path", None) != request.load_project_path:
                raise RuntimeError(f"Failed to load PyPlot project {request.load_project_path}")

        if request.plugin_name:
            _select_pyplot_plugin(window, request.plugin_name)
            _pump_qt_events(app, rounds=4)

        if request.import_entries:
            window._import_paths(request.import_entries)  # type: ignore[attr-defined]
            _pump_qt_events(app, rounds=6)
            imported_paths = list(request.import_entries)

        plugin = getattr(window, "_current_plugin", None)
        if request.generate:
            if plugin is None:
                raise RuntimeError("No active PyPlot plugin selected for generate.")
            plugin.generate()
            _pump_qt_events(app, rounds=8)

        for graph_payload in request.build_graphs:
            creator = getattr(window, "_automation_create_graph", None)
            if not callable(creator):
                raise RuntimeError("PyPlot graph builder automation is unavailable.")
            creator(graph_payload)
            _pump_qt_events(app, rounds=6)

        for figure_payload in request.create_figures:
            creator = getattr(window, "_automation_create_figure", None)
            if not callable(creator):
                raise RuntimeError("PyPlot figure layout automation is unavailable.")
            creator(figure_payload)
            _pump_qt_events(app, rounds=6)

        if request.open_graph_format:
            opener = getattr(window, "_open_graph_format_dialog", None)
            if callable(opener):
                opener()
            _pump_qt_events(app, rounds=4)

        if request.open_origin:
            if plugin is None:
                raise RuntimeError("No active PyPlot plugin selected for Origin export.")
            plugin.open_origin()
            _pump_qt_events(app, rounds=6)

        wait_ms = max(0, int(request.wait_ms or 0))
        if wait_ms > 0:
            deadline = time.time() + wait_ms / 1000.0
            while time.time() < deadline:
                _pump_qt_events(app, rounds=1)
                time.sleep(min(0.02, max(0.0, deadline - time.time())))

        if isinstance(request.window_image_path, Path):
            target = request.window_image_path
            target.parent.mkdir(parents=True, exist_ok=True)
            _pump_qt_events(app, rounds=4)
            if not window.grab().save(str(target)):
                raise RuntimeError(f"Failed to save PyPlot screenshot to {target}")

        if isinstance(request.current_plot_image_path, Path):
            axes = window._current_axes()  # type: ignore[attr-defined]
            if axes is None or getattr(axes, "figure", None) is None:
                raise RuntimeError("No active plot is available for current_plot_image export.")
            target = request.current_plot_image_path
            target.parent.mkdir(parents=True, exist_ok=True)
            axes.figure.savefig(target, dpi=160)

        if isinstance(request.plot_images_dir, Path):
            exported_plot_paths = _export_visible_plot_images(window, app, request.plot_images_dir)

        if isinstance(request.export_all_figures_dir, Path) and isinstance(request.export_all_figures_format, str):
            exporter = getattr(window, "_automation_export_all_figures", None)
            if not callable(exporter):
                raise RuntimeError("PyPlot batch figure export automation is unavailable.")
            exported_all_figure_paths = exporter(
                output_dir=request.export_all_figures_dir,
                fmt=request.export_all_figures_format,
                dpi=request.export_all_figures_dpi,
                transparent=bool(request.export_all_figures_transparent),
            )

        if isinstance(request.review_output_dir, Path):
            theme = theme_manager()
            previous_mode = theme.current_mode()
            if request.review_dark_gui:
                theme.set_mode("dark")
                _pump_qt_events(app, rounds=4)
            try:
                review_paths.extend(
                    _capture_review_screenshots(
                        window,
                        app,
                        request.review_output_dir,
                    )
                )
            finally:
                if request.review_dark_gui:
                    theme.set_mode(previous_mode)
                    _pump_qt_events(app, rounds=4)

        if isinstance(request.save_project_path, Path):
            target = _normalise_project_path(request.save_project_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            window._write_project_file(target)  # type: ignore[attr-defined]
            _pump_qt_events(app, rounds=4)
            if getattr(window, "_project_path", None) != target or not target.exists():
                raise RuntimeError(f"Failed to save PyPlot project to {target}")
            saved_project_path = target

        active_plugin_name = getattr(window, "_current_plotter_name", None) or request.plugin_name
        summary = _pyplot_summary(window, active_plugin_name)
        summary.update(
            {
                "status": "ok",
                "kind": request.manifest_kind,
                "version": request.manifest_version,
                "loaded_project": _absolute_path(request.load_project_path),
                "saved_project": _absolute_path(saved_project_path),
                "imported_paths": _path_payload(imported_paths),
                "plot_image_paths": _path_payload(exported_plot_paths),
                "all_figure_export_paths": _path_payload(exported_all_figure_paths),
                "review_paths": _path_payload(review_paths),
                "window_image": _absolute_path(request.window_image_path),
                "current_plot_image": _absolute_path(request.current_plot_image_path),
                "warnings": [],
                "errors": [],
            }
        )
        return summary
    finally:
        if window is not None:
            try:
                clear_dirty = getattr(window, "_clear_project_dirty", None)
                if callable(clear_dirty):
                    clear_dirty()
            except Exception:
                pass
            try:
                window.close()
            except Exception:
                pass
        if isinstance(app, QtWidgets.QApplication):
            for widget in list(app.topLevelWidgets()):
                if widget in baseline_top_level_widgets:
                    continue
                if not isinstance(widget, QtWidgets.QWidget):
                    continue
                try:
                    widget.close()
                except Exception:
                    pass
            _pump_qt_events(app, rounds=4)
            if created_app:
                try:
                    app.quit()
                except Exception:
                    pass


def _pyplot_request_from_legacy_args(args: argparse.Namespace) -> _PyPlotAutomationRequest:
    request = _PyPlotAutomationRequest(
        plugin_name=getattr(args, "pyplot_plugin", None),
        import_entries=[
            Path(str(entry)).expanduser()
            for entry in (getattr(args, "pyplot_import", []) or [])
        ],
        generate=bool(getattr(args, "pyplot_plot", False)),
        open_graph_format=bool(getattr(args, "pyplot_open_graph_format", False)),
        open_origin=bool(getattr(args, "pyplot_open_origin", False)),
        show_window=bool(getattr(args, "pyplot_show_window", False)),
        wait_ms=max(0, int(getattr(args, "pyplot_wait_ms", 0) or 0)),
    )
    screenshot_path = getattr(args, "pyplot_screenshot", None)
    if isinstance(screenshot_path, str) and screenshot_path.strip():
        request.window_image_path = Path(screenshot_path).expanduser()
    plot_image_path = getattr(args, "pyplot_plot_image", None)
    if isinstance(plot_image_path, str) and plot_image_path.strip():
        request.current_plot_image_path = Path(plot_image_path).expanduser()
    summary_path = getattr(args, "pyplot_summary_json", None)
    if isinstance(summary_path, str) and summary_path.strip():
        request.summary_path = Path(summary_path).expanduser()
    return request


def _run_pyplot_automation_request(
    request: _PyPlotAutomationRequest,
    qt_args: list[str],
) -> int:
    try:
        summary = _execute_pyplot_automation_request(request, qt_args)
    except _AutomationRecipeError as exc:
        print(f"[pyplot-cli] recipe error: {exc}")
        return 2
    except Exception as exc:
        message = f"[pyplot-cli] {type(exc).__name__}: {exc}"
        print(message)
        return 1

    if isinstance(request.summary_path, Path):
        _write_json(request.summary_path, summary)
    else:
        print(json.dumps(summary, ensure_ascii=False))
    return 0


def _run_automation_recipe(args: argparse.Namespace, qt_args: list[str]) -> int:
    recipe_value = getattr(args, "automation_recipe", None)
    if not isinstance(recipe_value, str) or not recipe_value.strip():
        return 2
    try:
        recipe_path = Path(recipe_value).expanduser()
        request = _load_automation_recipe_request(recipe_path)
    except _AutomationRecipeError as exc:
        print(f"[automation-recipe] {exc}")
        return 2
    return _run_pyplot_automation_request(request, qt_args)


def _run_pyplot_automation(args: argparse.Namespace, qt_args: list[str]) -> int:
    if getattr(args, "pyplot_list_plugins", False):
        _pyplot_main, plugin_names = _load_pyplot_metadata()
        for name in plugin_names:
            print(name)
        return 0
    request = _pyplot_request_from_legacy_args(args)
    return _run_pyplot_automation_request(request, qt_args)

LOGGERS: Dict[str, LauncherFactory] = {
    "Serial Data Logger": _lazy("data_logging.data_logger", "main"),
    "Current Annealing Logger": _lazy(
        "data_logging.current_annealing_logger", "main"
    ),
    "Manual Stress/Strain Logger": _lazy(
        "data_logging.manual_stress_strain_logger", "main"
    ),
}

EMULATORS: Dict[str, LauncherFactory] = {
    "Universal Serial Emulator": _lazy(
        "emulators.virtual_serial_emulator_gui", "main"
    ),
}

BUILDERS: Dict[str, LauncherFactory] = {
    "Microwire Data Builder": _lazy("microwire_data_builder", "main"),
}


class MasterLauncher(QtWidgets.QWidget):
    ready = QtCore.pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PyPlot Launcher")
        self.setWindowIcon(_create_launcher_icon())
        self.main_layout = QtWidgets.QVBoxLayout(self)

        # Ensure window bookkeeping exists even if later setup fails so the
        # destroyed callbacks can run safely.
        self._open_windows: list[QtWidgets.QWidget] = []

        self._settings = QtCore.QSettings("MicrowireData", "Launcher")
        dev_opts_factory = _load_developer_options()
        self.dev_opts = dev_opts_factory()
        self._closing = False
        self._registry_loaded = False
        placeholder_plotters: Dict[str, LauncherFactory] = {
            "PyPlot": lambda: launch_pyplot(initial=None)
        }
        self._registry: dict[str, Dict[str, LauncherFactory]] = {
            "loggers": dict(LOGGERS),
            "plotters": placeholder_plotters,
            "emulators": dict(EMULATORS),
        }
        if BUILDERS:
            self._registry["builders"] = dict(BUILDERS)

        try:
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_QuitOnClose, False)
        except Exception:
            pass

        app = QtWidgets.QApplication.instance()
        if isinstance(app, QtWidgets.QApplication):
            try:
                app.setQuitOnLastWindowClosed(False)
            except Exception:
                pass
            try:
                app.lastWindowClosed.connect(self._restore_launcher)
            except Exception:
                pass
            try:
                app.installEventFilter(self)
            except Exception:
                pass

        self.search_bar = QtWidgets.QLineEdit(self)
        self.search_bar.setPlaceholderText("Search tools...")
        try:
            self.search_bar.setClearButtonEnabled(True)
        except Exception:
            pass
        self.tabs = QtWidgets.QTabWidget()
        self.log_tab = QtWidgets.QWidget()
        self.plot_tab = QtWidgets.QWidget()
        self.emu_tab = QtWidgets.QWidget()
        self.builder_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.log_tab, "Loggers")
        self.tabs.addTab(self.plot_tab, "Plotting")
        self.tabs.addTab(self.emu_tab, "Emulators")
        if self._registry.get("builders"):
            self.tabs.addTab(self.builder_tab, "Builders")
        self.exp_tab = QtWidgets.QWidget()
        self._experiments_index: int | None = None

        self.log_list = QtWidgets.QListWidget()
        log_layout = QtWidgets.QVBoxLayout(self.log_tab)
        log_layout.addWidget(self.log_list)

        self.plot_list = QtWidgets.QListWidget()
        plot_layout = QtWidgets.QVBoxLayout(self.plot_tab)
        plot_layout.addWidget(self.plot_list)

        self.emu_list = QtWidgets.QListWidget()
        emu_layout = QtWidgets.QVBoxLayout(self.emu_tab)
        emu_layout.addWidget(self.emu_list)

        self.builder_list = QtWidgets.QListWidget()
        builder_layout = QtWidgets.QVBoxLayout(self.builder_tab)
        builder_layout.addWidget(self.builder_list)

        self.exp_list = QtWidgets.QListWidget()
        exp_layout = QtWidgets.QVBoxLayout(self.exp_tab)
        exp_layout.addWidget(self.exp_list)

        self._update_category_labels()

        self._list_widgets = {
            "loggers": self.log_list,
            "plotters": self.plot_list,
            "emulators": self.emu_list,
        }
        if self._registry.get("builders"):
            self._list_widgets["builders"] = self.builder_list
        if "experiments" in self._registry:
            self._list_widgets["experiments"] = self.exp_list

        self._sort_modes: dict[str, str] = {}
        for category in self._list_widgets:
            stored = self._settings.value(f"sort/{category}", "last_used")
            if not isinstance(stored, str) or stored not in {"last_used", "name_asc", "name_desc"}:
                stored = "last_used"
            self._sort_modes[category] = stored
        self._last_order_counter = self._load_last_order_counter()
        # Keep plotting tools in "last opened" order regardless of prior sort
        # settings so recent workflows stay at the top.
        self._sort_modes["plotters"] = "last_used"
        try:
            self._settings.setValue("sort/plotters", "last_used")
        except Exception:
            pass
        self._sort_groups: dict[str, QtGui.QActionGroup] = {}

        self.main_layout.addWidget(self.search_bar)
        self.main_layout.addWidget(self.tabs)

        self._set_lists_loading()
        QtCore.QTimer.singleShot(0, self._load_registry_async)
        self.dev_opts.experiments_visibility_changed.connect(self._sync_experiments_tab)
        self.search_bar.textChanged.connect(self._apply_search_filter)
        self.tabs.currentChanged.connect(self._handle_tab_changed)

        self.run_button = QtWidgets.QPushButton("Run")
        self.run_button.clicked.connect(self.run_selected)
        self.run_button.setEnabled(False)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.run_button)

        self.main_layout.addLayout(button_row)

        menu_bar = _install_launcher_menu(
            self,
            help_topic="launcher",
            close_window=self._close_launcher,
        )
        sort_menu = menu_bar.addMenu("&Sort")
        if sort_menu is None:
            sort_menu = QtWidgets.QMenu("&Sort", self)
            menu_bar.addMenu(sort_menu)
        self._sort_menu = sort_menu
        self._install_sort_menu(sort_menu)

    def _close_launcher(self) -> None:
        """Close hook that satisfies :func:`install_standard_menu`."""

        # ``QWidget.close`` returns ``bool`` and Pylance/Pyright expect the menu
        # callback to return ``None``.  We call the underlying method but
        # intentionally drop the return value to keep the type contract tidy.
        self.close()

    def _set_lists_loading(self) -> None:
        for list_widget in self._list_widgets.values():
            list_widget.clear()
            list_widget.setEnabled(False)
            placeholder = QtWidgets.QListWidgetItem("Loading...")
            placeholder.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            list_widget.addItem(placeholder)

    def _update_category_labels(self) -> None:
        labels: dict[str, str] = {
            "loggers": "Loggers",
            "plotters": "Plotting",
            "emulators": "Emulators",
        }
        if self._registry.get("builders"):
            labels["builders"] = "Builders"
        experiments = self._registry.get("experiments")
        if experiments:
            labels["experiments"] = "Experiments"
        self._category_labels = labels

    def _load_registry_async(self) -> None:
        try:
            registry = _build_registry()
        except Exception as exc:  # pragma: no cover - unexpected import failure
            LOGGER.exception("Failed to build launcher registry", exc_info=exc)
            QtWidgets.QMessageBox.critical(
                self,
                "Launcher error",
                f"Failed to load tools:\n{exc}",
            )
            registry = None
        else:
            self._registry = registry
            if "experiments" in registry:
                self._list_widgets["experiments"] = self.exp_list
            self._update_category_labels()
            if hasattr(self, "_sort_menu"):
                self._sort_menu.clear()
                self._sort_groups.clear()
                self._install_sort_menu(self._sort_menu)
            for category in registry:
                self._sort_modes.setdefault(category, "last_used")
        finally:
            self._registry_loaded = True
            self.run_button.setEnabled(True)
            self._apply_search_filter(self.search_bar.text())
            self._sync_experiments_tab(self.dev_opts.show_experiments())
            self.ready.emit()

    def _restore_launcher(self) -> None:
        if self._closing:
            return
        if self._registry_loaded:
            self._refresh_all_lists()
        if not self.isVisible():
            self.show()
            try:
                self.raise_()
                self.activateWindow()
            except Exception:
                pass

    def changeEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.ActivationChange:
            if self.isActiveWindow() and self._registry_loaded:
                self._refresh_all_lists()

    def _register_window(self, widget: QtWidgets.QWidget) -> None:
        """Track ``widget`` so closing the launcher can warn appropriately."""

        if widget in self._open_windows:
            return

        self._open_windows.append(widget)

        try:
            widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_QuitOnClose, False)
        except Exception:
            pass

        def _remove(_: object = None, w: QtWidgets.QWidget = widget) -> None:
            windows = getattr(self, "_open_windows", None)
            if windows is None:
                return
            try:
                windows.remove(w)
            except ValueError:
                pass

        widget.destroyed.connect(_remove)

    def _sync_experiments_tab(self, enabled: bool) -> None:
        has_items = self.exp_list.count() > 0
        index = self.tabs.indexOf(self.exp_tab)
        if enabled and has_items:
            if index == -1:
                self._experiments_index = self.tabs.addTab(
                    self.exp_tab, "Experiments"
                )
        else:
            if index != -1:
                self.tabs.removeTab(index)
            self._experiments_index = None

    def _install_sort_menu(self, parent_menu: QtWidgets.QMenu) -> None:
        for category, label in self._category_labels.items():
            if category not in self._list_widgets:
                continue
            if not self._registry.get(category):
                continue
            submenu = parent_menu.addMenu(label)
            if submenu is None:
                submenu = QtWidgets.QMenu(label, self)
                parent_menu.addMenu(submenu)
            if submenu is None:
                continue
            group = QtGui.QActionGroup(self)
            group.setExclusive(True)
            for mode, text in (
                ("last_used", "Last Used (Most Recent)"),
                ("name_asc", "Name (A-Z)"),
                ("name_desc", "Name (Z-A)"),
            ):
                action = submenu.addAction(text)
                if action is None:
                    continue
                action.setCheckable(True)
                action.setData((category, mode))
                if self._sort_modes.get(category, "last_used") == mode:
                    action.setChecked(True)
                group.addAction(action)
            group.triggered.connect(self._handle_sort_trigger)
            self._sort_groups[category] = group

    def _apply_search_filter(self, _: str) -> None:
        self._refresh_all_lists()

    def _refresh_all_lists(self) -> None:
        for category, list_widget in self._list_widgets.items():
            current_item = list_widget.currentItem()
            selected = current_item.text() if current_item is not None else None
            self._refresh_list(category, select_name=selected)

    def _refresh_list(self, category: str, select_name: str | None = None) -> None:
        list_widget = self._list_widgets.get(category)
        if list_widget is None:
            return
        names = self._sorted_names(category)
        search_text = self.search_bar.text().strip().casefold()
        list_widget.blockSignals(True)
        list_widget.clear()
        for name in names:
            if search_text and search_text not in name.casefold():
                continue
            list_widget.addItem(name)
        list_widget.blockSignals(False)
        list_widget.setEnabled(self._registry_loaded)
        if select_name:
            matches = list_widget.findItems(select_name, QtCore.Qt.MatchFlag.MatchExactly)
            if matches:
                list_widget.setCurrentItem(matches[0])
        if list_widget.currentRow() == -1 and list_widget.count():
            list_widget.setCurrentRow(0)

    def _current_list_widget(self) -> QtWidgets.QListWidget | None:
        current = self.tabs.currentWidget()
        if current is self.log_tab:
            return self.log_list
        if current is self.plot_tab:
            return self.plot_list
        if current is self.emu_tab:
            return self.emu_list
        if current is self.builder_tab:
            return self.builder_list
        if current is self.exp_tab:
            return self.exp_list
        return None

    def _ensure_selection(self, list_widget: QtWidgets.QListWidget | None) -> None:
        if list_widget is None:
            return
        if list_widget.count() and list_widget.currentRow() == -1:
            list_widget.setCurrentRow(0)

    def _focus_current_list(self, select_first: bool = False) -> None:
        list_widget = self._current_list_widget()
        if list_widget is None:
            return
        if select_first and list_widget.count() and list_widget.currentRow() == -1:
            list_widget.setCurrentRow(0)
        self._ensure_selection(list_widget)
        try:
            list_widget.setFocus(QtCore.Qt.FocusReason.TabFocusReason)
        except Exception:
            list_widget.setFocus()

    def _handle_tab_changed(self, _: int) -> None:
        list_widget = self._current_list_widget()
        self._ensure_selection(list_widget)
        focus_widget = QtWidgets.QApplication.focusWidget()
        if isinstance(focus_widget, QtWidgets.QTabBar):
            self._focus_current_list()

    def _sorted_names(self, category: str) -> list[str]:
        mapping = self._registry.get(category, {})
        names = list(mapping.keys())
        mode = self._sort_modes.get(category, "last_used")
        if mode == "name_asc":
            names.sort(key=str.casefold)
        elif mode == "name_desc":
            names.sort(key=str.casefold, reverse=True)
        else:
            names.sort(
                key=lambda name: (
                    -self._last_order(category, name),
                    -self._launcher_last_used(category, name),
                    name.casefold(),
                )
            )
        return names

    def _launcher_last_used(self, category: str, name: str) -> float:
        value = self._settings.value(f"launcher_last_used/{category}/{name}")
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _load_last_order_counter(self) -> int:
        raw = self._settings.value("launcher_last_order/seq", 0)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    def _last_order(self, category: str, name: str) -> int:
        value = self._settings.value(f"launcher_last_order/{category}/{name}", 0)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _update_last_used(self, category: str, name: str) -> None:
        now = time.time()
        self._settings.setValue(f"launcher_last_used/{category}/{name}", now)
        # Keep legacy key in sync for backward compatibility with older builds.
        self._settings.setValue(f"last_used/{category}/{name}", now)
        self._last_order_counter = max(0, int(getattr(self, "_last_order_counter", 0))) + 1
        self._settings.setValue("launcher_last_order/seq", self._last_order_counter)
        self._settings.setValue(
            f"launcher_last_order/{category}/{name}",
            self._last_order_counter,
        )

    def _set_sort_mode(self, category: str, mode: str) -> None:
        if category not in self._list_widgets:
            return
        if mode not in {"last_used", "name_asc", "name_desc"}:
            return
        current_item = self._list_widgets[category].currentItem()
        selected = current_item.text() if current_item is not None else None
        self._sort_modes[category] = mode
        self._settings.setValue(f"sort/{category}", mode)
        self._refresh_list(category, select_name=selected)

    def _handle_sort_trigger(self, action: QtGui.QAction) -> None:
        data = action.data()
        if isinstance(data, tuple) and len(data) == 2:
            category, mode = data
            self._set_sort_mode(str(category), str(mode))

    def _advance_tab(self, offset: int) -> bool:
        count = self.tabs.count()
        if count <= 1:
            return False
        current_index = self.tabs.currentIndex()
        if current_index < 0:
            return False
        new_index = (current_index + offset) % count
        if new_index == current_index:
            return False
        self.tabs.setCurrentIndex(new_index)
        self._focus_current_list(select_first=True)
        return True

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        try:
            etype = event.type()
        except RecursionError:
            return False
        if etype == QtCore.QEvent.Type.KeyPress and isinstance(event, QtGui.QKeyEvent):
            key_event = cast(QtGui.QKeyEvent, event)
            focus_widget = QtWidgets.QApplication.focusWidget()
            if focus_widget is not None and not self.isAncestorOf(focus_widget):
                return super().eventFilter(obj, event)
            if not self.isActiveWindow():
                return super().eventFilter(obj, event)
            key = key_event.key()
            if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                list_widget = self._current_list_widget()
                self._ensure_selection(list_widget)
                if list_widget is not None and list_widget.count():
                    if list_widget.currentRow() == -1:
                        list_widget.setCurrentRow(0)
                self.run_selected()
                event.accept()
                return True
            if key in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right):
                if isinstance(focus_widget, QtWidgets.QLineEdit):
                    return super().eventFilter(obj, event)
                direction = -1 if key == QtCore.Qt.Key.Key_Left else 1
                if self._advance_tab(direction):
                    event.accept()
                    return True
            if key in (QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down):
                list_widget = self._current_list_widget()
                if list_widget is None or list_widget.count() == 0:
                    return super().eventFilter(obj, event)
                if isinstance(focus_widget, QtWidgets.QLineEdit):
                    if key == QtCore.Qt.Key.Key_Down:
                        self._focus_current_list(select_first=True)
                        event.accept()
                        return True
                    return super().eventFilter(obj, event)
                if focus_widget is list_widget:
                    return super().eventFilter(obj, event)
                current_row = list_widget.currentRow()
                if current_row == -1:
                    new_row = 0 if key == QtCore.Qt.Key.Key_Down else list_widget.count() - 1
                elif key == QtCore.Qt.Key.Key_Down:
                    new_row = min(current_row + 1, list_widget.count() - 1)
                else:
                    new_row = max(current_row - 1, 0)
                list_widget.setCurrentRow(new_row)
                self._focus_current_list()
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def run_selected(self) -> None:
        if not self._registry_loaded:
            return
        category: str | None = None
        item: QtWidgets.QListWidgetItem | None
        if self.tabs.currentWidget() is self.log_tab:
            category = "loggers"
            item = self.log_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(self, "No selection", "Please select a logger")
                return
        elif self.tabs.currentWidget() is self.plot_tab:
            category = "plotters"
            item = self.plot_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(self, "No selection", "Please select a plotting script")
                return
        elif self.tabs.currentWidget() is self.emu_tab:
            category = "emulators"
            item = self.emu_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(self, "No selection", "Please select an emulator")
                return
        elif self.tabs.currentWidget() is self.builder_tab:
            category = "builders"
            item = self.builder_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(
                    self, "No selection", "Please select a builder tool"
                )
                return
        elif self.tabs.currentWidget() is self.exp_tab:
            category = "experiments"
            item = self.exp_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.information(
                    self, "No selection", "Enable and pick an experiment to launch"
                )
                return
        else:
            return

        assert item is not None
        assert category is not None
        item_text = item.text()
        registry = self._registry.get(category, {})
        func = registry.get(item_text)
        if func is None:
            QtWidgets.QMessageBox.critical(
                self,
                "Missing entry",
                f"No handler registered for {item_text}",
            )
            return
        _reset_outlier_flags()

        app_instance = QtWidgets.QApplication.instance()
        assert isinstance(app_instance, QtWidgets.QApplication)

        existing_windows = set(app_instance.topLevelWidgets())

        result: QtWidgets.QWidget | None = None

        try:
            result = func()
            if isinstance(result, QtWidgets.QWidget):
                self._register_window(result)
        except SystemExit as exc:
            code = exc.code
            if code not in (None, 0):
                QtWidgets.QMessageBox.critical(self, "Error", str(code))
        except Exception as exc:  # pragma: no cover - unexpected errors
            QtWidgets.QMessageBox.critical(
                self, "Error", f"{type(exc).__name__}: {exc}"
            )

        try:
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

        new_windows = [
            w for w in app_instance.topLevelWidgets() if w not in existing_windows
        ]
        if isinstance(result, QtWidgets.QWidget) and result not in new_windows:
            new_windows.append(result)
        for w in new_windows:
            try:
                w.raise_()
                w.activateWindow()
            except RuntimeError:
                pass
            if isinstance(w, QtWidgets.QWidget):
                self._register_window(w)

        for w in app_instance.topLevelWidgets():
            if w is self:
                continue
            if isinstance(w, QtWidgets.QWidget):
                self._register_window(w)

        self._update_last_used(category, item_text)
        self._refresh_list(category, select_name=item_text)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        open_windows = [w for w in list(self._open_windows) if isinstance(w, QtWidgets.QWidget) and w.isVisible()]
        if open_windows:
            reply = QtWidgets.QMessageBox.question(
                self,
                "Close Launcher",
                f"Closing the launcher will also close {len(open_windows)} open window(s). Continue?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                self._closing = False
                event.ignore()
                return
            for w in list(open_windows):
                try:
                    w.close()
                except Exception:
                    pass
        self._closing = True
        event.accept()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
            QtCore.QTimer.singleShot(0, app.quit)


def main(argv: list[str] | None = None) -> None:
    argv_list = list(sys.argv if argv is None else argv)
    args, qt_args = _parse_launcher_args(argv_list[1:])
    if args.visual_check:
        raise SystemExit(_run_visual_check(args))
    if getattr(args, "automation_recipe", None):
        raise SystemExit(_run_automation_recipe(args, qt_args))
    if _is_pyplot_automation_requested(args):
        raise SystemExit(_run_pyplot_automation(args, qt_args))
    _install_crash_log_hook()

    # Ensure a GUI platform plugin is used (not an offscreen one from tests)
    # Some test environments set QT_QPA_PLATFORM=offscreen. If that leaks into
    # an interactive run, Qt's style engine may try to paint using QPainter on
    # an invalid device, producing warnings like "QPainter::begin: Paint device
    # returned engine == 0". Clear it so the default (e.g. 'windows') is used.
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in {"offscreen", "minimal", "headless"}:
        os.environ.pop("QT_QPA_PLATFORM", None)
    # External Qt distributions (e.g., conda/other apps) can inject plugin-path
    # variables that point to incompatible binaries and cause startup errors:
    # "no Qt platform plugin could be initialized".
    for env_key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        if os.environ.get(env_key):
            os.environ.pop(env_key, None)

    app = QtWidgets.QApplication([argv_list[0], *qt_args])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("PyPlot Launcher")
    _schedule_theme_application(app)
    icon = _create_launcher_icon()
    app.setWindowIcon(icon)
    placeholder = QtWidgets.QMainWindow()
    placeholder.setWindowIcon(icon)
    placeholder.setWindowTitle("PyPlot Launcher")
    placeholder.resize(420, 260)
    loading_label = QtWidgets.QLabel("Loading PyPlot Launcher...", placeholder)
    loading_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    loading_label.setStyleSheet("font-size: 16px; font-weight: 600;")
    placeholder.setCentralWidget(loading_label)
    placeholder.show()
    try:
        app.processEvents()
    except Exception:
        pass

    launcher_holder: dict[str, MasterLauncher] = {}

    def _create_launcher() -> None:
        window = MasterLauncher()
        launcher_holder["window"] = window

        def _show_when_ready() -> None:
            window.ready.disconnect(_show_when_ready)
            window.show()
            placeholder.close()

        window.ready.connect(_show_when_ready)

    def _fallback_show() -> None:
        window = launcher_holder.get("window")
        if isinstance(window, MasterLauncher) and not window.isVisible():
            window.show()
            placeholder.close()

    QtCore.QTimer.singleShot(0, _create_launcher)
    QtCore.QTimer.singleShot(5000, _fallback_show)
    app.exec()


if __name__ == "__main__":
    main()
