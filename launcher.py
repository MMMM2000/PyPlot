from __future__ import annotations

import argparse
import sys
import os
import time
import logging
import traceback
import json
import csv
import math
import re
import shutil
import base64
import pickle
import secrets
import socket
import socketserver
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from functools import lru_cache
from importlib import import_module
from typing import TYPE_CHECKING, Any, Callable, Dict, Mapping, Tuple, Sequence, cast, Protocol

import pandas as pd
from PyQt6 import QtWidgets, QtGui, QtCore
from PIL import Image

from plotting.shared.experiment_processes import (
    ExperimentProcessSpec,
    launch_experiment_process,
)


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


def _experiment_process_launcher(
    display_name: str,
    module: str,
    resource_tag: str,
) -> LauncherFactory:
    def factory(*_args: Any, **_kwargs: Any) -> QtWidgets.QWidget | None:
        launch_experiment_process(
            ExperimentProcessSpec(
                display_name=display_name,
                module=module,
                resource_tag=resource_tag,
            )
        )
        return None

    return factory


EXPERIMENT_PROCESS_MODULES: dict[str, ExperimentProcessSpec] = {
    "current_annealing": ExperimentProcessSpec(
        display_name="Current Annealing Logger",
        module="data_logging.current_annealing_logger.current_annealing_logger",
        resource_tag="current_annealing",
    ),
    "mini_dma": ExperimentProcessSpec(
        display_name="Mini DMA Logger",
        module="data_logging.mini_dma_logger.mini_dma_logger",
        resource_tag="mini_dma",
    ),
    "ac_susceptibility": ExperimentProcessSpec(
        display_name="AC Susceptibility Logger",
        module="data_logging.ac_susceptibility_logger.ac_susceptibility_logger",
        resource_tag="ac_susceptibility",
    ),
}


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


SESSION_PROTOCOL_VERSION = 1
DEFAULT_SESSION_COMMAND_TIMEOUT_S = 120.0


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


@dataclass(frozen=True)
class _PreparedFileArchive:
    temp_path: Path
    archive_path: Path


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _temporary_sibling_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    os.close(handle)
    return Path(raw_path)


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _write_text_atomic(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    temp_path = _temporary_sibling_path(path)
    try:
        with temp_path.open("w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        _unlink_quietly(temp_path)
        raise


def _copy_file_atomic(source: Path, target: Path) -> None:
    temp_path = _temporary_sibling_path(target)
    try:
        shutil.copyfile(source, temp_path)
        _fsync_file(temp_path)
        os.replace(temp_path, target)
    except Exception:
        _unlink_quietly(temp_path)
        raise


def _next_available_path(path: Path) -> Path:
    target = path
    counter = 1
    while target.exists():
        target = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        counter += 1
    return target


def _prepare_file_archive(source: Path, archive_path: Path) -> _PreparedFileArchive | None:
    if not source.exists():
        return None
    target = _next_available_path(archive_path)
    temp_path = _temporary_sibling_path(target)
    try:
        shutil.copyfile(source, temp_path)
        _fsync_file(temp_path)
    except Exception:
        _unlink_quietly(temp_path)
        raise
    return _PreparedFileArchive(temp_path=temp_path, archive_path=target)


def _prepared_archive_path(prepared: _PreparedFileArchive | None) -> str | None:
    if prepared is None:
        return None
    return _absolute_path(prepared.archive_path)


def _finish_prepared_file_archive(prepared: _PreparedFileArchive | None) -> str | None:
    if prepared is None:
        return None
    os.replace(prepared.temp_path, prepared.archive_path)
    return _absolute_path(prepared.archive_path)


def _discard_prepared_file_archive(prepared: _PreparedFileArchive | None) -> None:
    if prepared is not None:
        _unlink_quietly(prepared.temp_path)


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


def _validate_builder_project_payload(payload: dict[str, Any], *, path: Path) -> None:
    if payload.get("kind") != "MicrowireDataBuilder":
        raise _AutomationRecipeError(
            f"Project '{path}' is not a Microwire Data Builder project."
        )
    if payload.get("version") != 1:
        raise _AutomationRecipeError(
            f"Project '{path}' uses unsupported Microwire Data Builder project version {payload.get('version')!r}."
        )


def _collect_builder_paths(
    paths: Sequence[Path],
    *,
    supported_suffixes: Sequence[str],
    exclude_dir_names: Sequence[str] = (),
) -> list[Path]:
    suffixes = {suffix.lower() for suffix in supported_suffixes}
    excluded_names = {str(name).strip().lower() for name in exclude_dir_names if str(name).strip()}

    def _is_excluded(candidate: Path, root: Path) -> bool:
        if not excluded_names:
            return False
        try:
            relative_parts = candidate.relative_to(root).parts
        except ValueError:
            relative_parts = candidate.parts
        return any(part.lower() in excluded_names for part in relative_parts[:-1])

    collected: list[Path] = []
    for path in paths:
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if not candidate.is_file():
                    continue
                if _is_excluded(candidate, path):
                    continue
                if suffixes and candidate.suffix.lower() not in suffixes:
                    continue
                collected.append(candidate)
        elif path.is_file():
            if suffixes and path.suffix.lower() not in suffixes:
                continue
            collected.append(path)
    return list(dict.fromkeys(collected))


def _record_path_key(record: object) -> str:
    raw_path = getattr(record, "path", "")
    try:
        return str(Path(raw_path).resolve())
    except Exception:
        return str(raw_path)


def _builder_section_specs(builder_ui: Any) -> dict[str, dict[str, Any]]:
    return {
        "microscope": {
            "class": builder_ui.MicroscopeSection,
            "payload": "microscope_index",
            "payload_kind": "mapping",
            "table_builder": lambda records, extra: builder_ui._microscope_index_to_frame(
                records,
                extra.get("overrides", {}) if isinstance(extra, Mapping) else {},
            ),
        },
        "annealing": {
            "class": builder_ui.AnnealingSection,
            "payload": "annealing_records",
            "table_builder": lambda records: builder_ui._annealing_records_to_frame(records, LOGGER),
        },
        "vsm_hysteresis": {
            "class": builder_ui.VsmHysteresisSection,
            "payload": "vsm_hysteresis_records",
            "graph_column": builder_ui.VSM_HYSTERESIS_COLUMN,
        },
        "vsm_temperature_scan": {
            "class": builder_ui.VsmTemperatureScanSection,
            "payload": "vsm_temperature_scan_records",
            "graph_column": builder_ui.VSM_TEMPERATURE_SCAN_COLUMN,
        },
        "dma_iso_stress": {
            "class": builder_ui.DmaIsoStressSection,
            "payload": "dma_iso_stress_records",
            "graph_column": builder_ui.DMA_ISOSTRESS_COLUMN,
        },
        "mini_dma": {
            "class": builder_ui.MiniDmaSection,
            "payload": "mini_dma_records",
            "graph_column": builder_ui.MINI_DMA_COLUMN,
            "table_builder": builder_ui._mini_dma_records_to_frame,
        },
        "shape_memory_stress_strain": {
            "class": builder_ui.ShapeMemoryStressStrainSection,
            "payload": "shape_memory_stress_strain_records",
            "graph_column": builder_ui.SHAPE_MEMORY_STRESS_STRAIN_COLUMN,
        },
        "fmr": {
            "class": builder_ui.FmrSection,
            "payload": "fmr_records",
            "graph_column": builder_ui.FMR_COLUMN,
        },
    }


def _merge_builder_records(existing_records: Sequence[object], new_records: Sequence[object]) -> list[object]:
    merged: dict[str, object] = {}
    fallback_index = 0
    for record in [*existing_records, *new_records]:
        key = _record_path_key(record)
        if not key:
            fallback_index += 1
            key = f"record-{fallback_index}"
        merged[key] = record
    return list(merged.values())


def _run_builder_update_section_command(
    *,
    builder_ui: Any,
    command: dict[str, Any],
    command_index: int,
    section_name: str,
    sections: dict[str, Any],
    base_dir: Path,
) -> dict[str, Any]:
    section_specs = _builder_section_specs(builder_ui)
    spec = section_specs.get(section_name)
    if spec is None:
        supported = ", ".join(sorted(section_specs))
        raise _AutomationRecipeError(
            f"Unsupported builder update_section command {command_index}: section={section_name!r}. "
            f"Supported sections: {supported}."
        )

    section_class = spec["class"]
    payload_name = str(spec["payload"])
    graph_column = spec.get("graph_column")
    table_builder = spec.get("table_builder")
    raw_paths = command.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise _AutomationRecipeError(
            f"{section_name} update requires a non-empty 'paths' array."
        )
    input_paths: list[Path] = []
    for path_index, raw_path in enumerate(raw_paths):
        path = _resolve_recipe_path_value(
            raw_path,
            base_dir=base_dir,
            field_name=f"commands[{command_index}].paths[{path_index}]",
        )
        if path is None or not path.exists():
            raise _AutomationRecipeError(f"Builder input path does not exist: {path}")
        input_paths.append(path)

    supported_suffixes = getattr(section_class, "supported_suffixes", ())
    raw_exclude_dir_names = command.get("exclude_dir_names", [])
    if raw_exclude_dir_names in (None, ""):
        raw_exclude_dir_names = []
    if not isinstance(raw_exclude_dir_names, list):
        raise _AutomationRecipeError(
            f"{section_name} update field 'exclude_dir_names' must be an array when provided."
        )
    candidates = _collect_builder_paths(
        input_paths,
        supported_suffixes=supported_suffixes,
        exclude_dir_names=[str(name) for name in raw_exclude_dir_names],
    )
    section = section_class(LOGGER, lambda *_args: None)
    try:
        section.import_project_payload(sections.get(section_name, {}))
        existing_payload = section.store.load_payload(payload_name)
        existing_records = list(existing_payload) if isinstance(existing_payload, list) else []
        source_strings = [str(path) for path in input_paths]
        section.data.sources = list(dict.fromkeys([*section.data.sources, *source_strings]))
        result = section.process(candidates)

        processed_keys = set()
        for processed_path in result.processed:
            try:
                processed_keys.add(str(Path(processed_path).resolve()))
            except Exception:
                processed_keys.add(str(processed_path))
        skipped_sources: list[str] = []
        for candidate in candidates:
            try:
                candidate_key = str(candidate.resolve())
            except Exception:
                candidate_key = str(candidate)
            if candidate_key not in processed_keys:
                skipped_sources.append(str(candidate))

        payload_kind = spec.get("payload_kind", "sequence")
        if payload_kind == "mapping":
            existing_mapping = dict(existing_payload) if isinstance(existing_payload, Mapping) else {}
            new_payload = result.payloads.get(payload_name, {})
            new_mapping = dict(new_payload) if isinstance(new_payload, Mapping) else {}
            merged_records = {**existing_mapping, **new_mapping}
        else:
            new_payload = result.payloads.get(payload_name, [])
            new_records = list(new_payload) if isinstance(new_payload, list) else []
            merged_records = _merge_builder_records(existing_records, new_records)

        if callable(table_builder):
            try:
                section.data.table = table_builder(merged_records, result.extra)
            except TypeError:
                section.data.table = table_builder(merged_records)
        else:
            section.data.table = builder_ui._graph_records_to_frame(
                merged_records,
                str(graph_column),
                sample_column="_sample",
            )
        section.data.processed = {**section.data.processed, **result.processed}
        if isinstance(result.extra, dict):
            section.data.extra.update(result.extra)
        section.data.extra["payloads"] = {payload_name: payload_name}
        section.store.save_payload(payload_name, merged_records)
        section.store.save(section.data)
        sections[section_name] = section.export_project_payload()
        return {
            "action": "update_section",
            "section": section_name,
            "status": "ok",
            "input_count": len(input_paths),
            "candidate_count": len(candidates),
            "updated_count": len(processed_keys),
            "skipped_count": len(skipped_sources),
            "skipped_sources": skipped_sources,
            "record_count": len(merged_records),
            "row_count": int(len(section.data.table.index)),
            "sources": source_strings,
        }
    finally:
        section.close()


def _builder_section_has_payload(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    for key in ("rows", "payloads", "sources", "imported_rows"):
        value = payload.get(key)
        if isinstance(value, Mapping) and value:
            return True
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and value:
            return True
    return False


def _builder_rebuild_sections_from_command(
    command: Mapping[str, Any],
    *,
    sections: Mapping[str, Any],
    command_index: int,
) -> set[str]:
    raw_sections = command.get("sections")
    if raw_sections in (None, ""):
        return {
            str(key)
            for key, payload in sections.items()
            if key not in {"assemble", "compare"} and _builder_section_has_payload(payload)
        }
    if not isinstance(raw_sections, list):
        raise _AutomationRecipeError(
            f"Builder rebuild_assemble command {command_index} field 'sections' must be an array when provided."
        )
    selected = {str(value).strip() for value in raw_sections if str(value).strip()}
    if not selected:
        raise _AutomationRecipeError(
            f"Builder rebuild_assemble command {command_index} field 'sections' must not be empty."
        )
    return selected


def _decode_builder_section_payload(
    builder_ui: Any,
    sections: Mapping[str, Any],
    section_name: str,
    payload_name: str,
) -> Any:
    section = sections.get(section_name)
    if not isinstance(section, Mapping):
        return None
    payloads = section.get("payloads")
    if not isinstance(payloads, Mapping):
        return None
    encoded = payloads.get(payload_name)
    decoder = getattr(builder_ui, "_decode_project_payload", None)
    if not callable(decoder):
        return None
    return decoder(encoded)


def _builder_section_rows_as_frame(
    sections: Mapping[str, Any],
    section_name: str,
) -> pd.DataFrame:
    section = sections.get(section_name)
    if not isinstance(section, Mapping):
        return pd.DataFrame()
    rows = section.get("rows")
    columns = section.get("columns")
    if not isinstance(rows, list):
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    if isinstance(columns, list):
        ordered = [str(column) for column in columns if str(column) in frame.columns]
        extra = [column for column in frame.columns if column not in ordered]
        frame = frame[ordered + extra]
    return frame


def _transition_rows_to_map(frame: pd.DataFrame) -> dict[str, dict[str, float]]:
    if frame.empty:
        return {}
    result: dict[str, dict[str, float]] = {}
    for row in frame.to_dict(orient="records"):
        key = str(row.get("_group_key") or "").strip()
        if not key:
            composition = str(row.get("Composition") or "").strip()
            microwire = str(row.get("Microwire") or "").strip()
            if composition and microwire:
                key = f"{composition}|{microwire.replace('/', '|')}"
        if not key:
            continue
        entry: dict[str, float] = {}
        for label, column in {
            "As": "As (°C)",
            "Af": "Af (°C)",
            "Ms": "Ms (°C)",
            "Mf": "Mf (°C)",
        }.items():
            value = row.get(column)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric):
                entry[label] = numeric
        if entry:
            result[key] = entry
    return result


def _run_builder_rebuild_assemble_command_lightweight(
    *,
    builder_ui: Any,
    command: dict[str, Any],
    command_index: int,
    sections: dict[str, Any],
    output_project: Path,
) -> dict[str, Any]:
    selected = _builder_rebuild_sections_from_command(
        command,
        sections=sections,
        command_index=command_index,
    )
    if not selected:
        raise _AutomationRecipeError(
            f"Builder rebuild_assemble command {command_index} has no section payloads to assemble."
        )

    def _payload(section_name: str, payload_name: str, fallback: Any) -> Any:
        value = _decode_builder_section_payload(builder_ui, sections, section_name, payload_name)
        return value if value is not None else fallback

    config = builder_ui.BuilderConfig(
        annealing_files=[],
        fabrication_files=[],
        output_dir=output_project.parent,
        microscope_files=[],
        video_files=[],
        strain_files=[],
        make_plots=False,
        export_formats=(),
        plot_backends=(),
        output_name=output_project.stem,
        include_microscope_crops=False,
    )
    transition_points = _transition_rows_to_map(
        _builder_section_rows_as_frame(sections, "transition_temps")
    )
    result = builder_ui.build_database(
        config,
        logger=LOGGER,
        fabrication_index=_payload("fabrication", "fabrication_index", builder_ui.FabricationIndex()),
        measurement_records=(
            _payload("annealing", "annealing_records", [])
            if "annealing" in selected
            else []
        ),
        vsm_hysteresis_records=(
            _payload("vsm_hysteresis", "vsm_hysteresis_records", [])
            if "vsm_hysteresis" in selected
            else []
        ),
        vsm_temperature_scan_records=(
            _payload("vsm_temperature_scan", "vsm_temperature_scan_records", [])
            if "vsm_temperature_scan" in selected
            else []
        ),
        dma_iso_stress_records=(
            _payload("dma_iso_stress", "dma_iso_stress_records", [])
            if "dma_iso_stress" in selected
            else []
        ),
        mini_dma_records=(
            _payload("mini_dma", "mini_dma_records", [])
            if "mini_dma" in selected
            else []
        ),
        shape_memory_stress_strain_records=(
            _payload(
                "shape_memory_stress_strain",
                "shape_memory_stress_strain_records",
                [],
            )
            if "shape_memory_stress_strain" in selected
            else []
        ),
        fmr_records=(
            _payload("fmr", "fmr_records", [])
            if "fmr" in selected
            else []
        ),
        microscope_index=(
            _payload("microscope", "microscope_index", {})
            if "microscope" in selected
            else {}
        ),
        video_index=(
            _payload("videos", "video_index", {})
            if "videos" in selected
            else {}
        ),
        strain_records=(
            _payload("strain", "strain_records", {})
            if "strain" in selected
            else {}
        ),
        transition_temps=transition_points if "transition_temps" in selected else {},
        skip_exports=True,
        include_fabrication_draw_siblings=True,
    )
    dataframe = result.dataframe if hasattr(result, "dataframe") else pd.DataFrame()
    if not isinstance(dataframe, pd.DataFrame):
        dataframe = pd.DataFrame()
    rows = [
        {str(column): builder_ui._json_safe(row.get(column)) for column in dataframe.columns}
        for row in dataframe.to_dict(orient="records")
    ]
    sections["assemble"] = {
        "section": "assemble",
        "title": "Assemble",
        "columns": [str(column) for column in dataframe.columns],
        "rows": rows,
        "index": [builder_ui._json_safe(index) for index in dataframe.index.tolist()],
    }
    return {
        "action": "rebuild_assemble",
        "status": "ok",
        "sections": sorted(selected),
        "row_count": int(len(dataframe.index)),
        "column_count": int(len(dataframe.columns)),
    }


def _timestamp_for_builder_database(recipe: Mapping[str, Any]) -> str:
    raw = recipe.get("timestamp")
    if raw is not None:
        text = str(raw).strip()
        if text:
            return re.sub(r"[^0-9A-Za-z_-]+", "_", text)
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def _builder_database_paths(
    recipe: Mapping[str, Any],
    *,
    base_dir: Path,
) -> dict[str, Path | str] | None:
    raw_database_dir = recipe.get("database_dir")
    if raw_database_dir in (None, ""):
        return None
    database_dir = _resolve_recipe_path_value(
        raw_database_dir,
        base_dir=base_dir,
        field_name="database_dir",
    )
    if database_dir is None:
        raise _AutomationRecipeError("Builder database field 'database_dir' must be a path.")
    database_name = str(recipe.get("database_name") or "microwire_database").strip()
    if not database_name:
        database_name = "microwire_database"
    database_name = re.sub(r"[^0-9A-Za-z_.-]+", "_", database_name)
    timestamp = _timestamp_for_builder_database(recipe)
    archive_dir = database_dir / "archive"
    return {
        "database_dir": database_dir,
        "database_name": database_name,
        "timestamp": timestamp,
        "latest_project": database_dir / f"{database_name}_latest.pydpj",
        "latest_manifest": database_dir / "update_manifest_latest.json",
        "archive_dir": archive_dir,
        "archive_project": archive_dir / f"{database_name}_{timestamp}.pydpj",
        "archive_manifest": archive_dir / f"update_manifest_{timestamp}.json",
    }


def _promote_builder_database_latest(
    *,
    database_paths: Mapping[str, Path | str],
    output_project: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> None:
    database_dir = cast(Path, database_paths["database_dir"])
    latest_project = cast(Path, database_paths["latest_project"])
    latest_manifest = cast(Path, database_paths["latest_manifest"])
    archive_project = cast(Path, database_paths["archive_project"])
    archive_manifest = cast(Path, database_paths["archive_manifest"])
    archive_dir = cast(Path, database_paths["archive_dir"])

    database_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    project_archive = _prepare_file_archive(latest_project, archive_project)
    manifest_archive = _prepare_file_archive(latest_manifest, archive_manifest)
    project_promoted = False
    manifest_promoted = False
    try:
        _copy_file_atomic(output_project, latest_project)
        project_promoted = True
        archived_project = _finish_prepared_file_archive(project_archive)
        manifest["database"] = {
            "database_dir": str(database_dir.resolve()),
            "database_name": str(database_paths["database_name"]),
            "timestamp": str(database_paths["timestamp"]),
            "latest_project": str(latest_project.resolve()),
            "latest_manifest": str(latest_manifest.resolve()),
            "archived_project": archived_project,
            "archived_manifest": _prepared_archive_path(manifest_archive),
        }
        _write_json(manifest_path, manifest)
        _write_json(latest_manifest, manifest)
        manifest_promoted = True
        _finish_prepared_file_archive(manifest_archive)
    except Exception:
        if not project_promoted:
            _discard_prepared_file_archive(project_archive)
        if not manifest_promoted:
            _discard_prepared_file_archive(manifest_archive)
        raise


def _run_builder_automation_recipe(recipe_path: Path) -> int:
    try:
        recipe = _load_json_object(recipe_path, label="Automation recipe")
        base_dir = recipe_path.parent
        if recipe.get("kind") != "builder":
            raise _AutomationRecipeError("Builder automation recipe kind must be 'builder'.")
        if recipe.get("version") != 1:
            raise _AutomationRecipeError(
                f"Unsupported builder automation recipe version {recipe.get('version')!r}. Only version 1 is supported."
            )

        database_paths = _builder_database_paths(recipe, base_dir=base_dir)
        raw_project_value = recipe.get("project")
        if raw_project_value in (None, "") and database_paths is not None:
            latest_project = cast(Path, database_paths["latest_project"])
            raw_project_value = str(latest_project) if latest_project.exists() else None
        project_path = _resolve_recipe_path_value(
            raw_project_value,
            base_dir=base_dir,
            field_name="project",
        )
        if project_path is None or not project_path.exists():
            raise _AutomationRecipeError("Builder automation field 'project' must point to an existing .pydpj file.")

        source_payload = _load_json_object(project_path, label="Microwire Data Builder project")
        _validate_builder_project_payload(source_payload, path=project_path)

        working_copy_dir = _resolve_recipe_path_value(
            recipe.get("working_copy_dir"),
            base_dir=base_dir,
            field_name="working_copy_dir",
        )
        if working_copy_dir is None:
            if database_paths is not None:
                working_copy_dir = cast(Path, database_paths["database_dir"]) / "_working"
            else:
                working_copy_dir = (base_dir / "builder_automation").resolve()
        working_copy_dir.mkdir(parents=True, exist_ok=True)

        output_project = _resolve_recipe_path_value(
            recipe.get("output_project"),
            base_dir=base_dir,
            field_name="output_project",
        )
        if output_project is None:
            if database_paths is not None:
                output_project = (
                    working_copy_dir
                    / f"{database_paths['database_name']}_{database_paths['timestamp']}.pydpj"
                )
            else:
                output_project = working_copy_dir / f"{project_path.stem}.updated{project_path.suffix}"
        output_project = output_project.with_suffix(".pydpj")
        try:
            same_project = project_path.resolve() == output_project.resolve()
        except Exception:
            same_project = str(project_path) == str(output_project)
        if same_project and not bool(recipe.get("overwrite_source", False)):
            raise _AutomationRecipeError(
                "Builder automation refuses to overwrite the source .pydpj without overwrite_source=true."
            )
        output_project.parent.mkdir(parents=True, exist_ok=True)
        if not same_project:
            shutil.copy2(project_path, output_project)

        project_payload = _load_json_object(output_project, label="Microwire Data Builder project copy")
        _validate_builder_project_payload(project_payload, path=output_project)
        sections = project_payload.get("sections")
        if not isinstance(sections, dict):
            sections = {}
            project_payload["sections"] = sections

        commands = recipe.get("commands")
        if not isinstance(commands, list) or not commands:
            raise _AutomationRecipeError("Builder automation field 'commands' must be a non-empty array.")

        manifest_path = _resolve_recipe_path_value(
            recipe.get("manifest_path"),
            base_dir=base_dir,
            field_name="manifest_path",
        )
        if manifest_path is None:
            if database_paths is not None:
                manifest_path = (
                    working_copy_dir
                    / f"update_manifest_{database_paths['timestamp']}.json"
                )
            else:
                manifest_path = output_project.with_suffix(".manifest.json")

        os.environ.setdefault("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", "1")
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication([])

        from microwire_data_builder import storage as builder_storage
        from microwire_data_builder import ui as builder_ui

        original_storage_root = builder_storage._storage_root
        automation_store = working_copy_dir / "_builder_store"
        if automation_store.exists():
            shutil.rmtree(automation_store)
        builder_storage._storage_root = lambda: automation_store  # type: ignore[assignment]
        builder_storage.MiniDatabaseStore._memory_data = {}
        builder_storage.MiniDatabaseStore._memory_payloads = {}
        builder_storage.MiniDatabaseStore._pending_sections = set()
        builder_storage.MiniDatabaseStore._pending_payloads = set()
        builder_storage.MiniDatabaseStore._disk_writes_suspended = 0

        command_results: list[dict[str, Any]] = []
        try:
            for index, command in enumerate(commands):
                if not isinstance(command, dict):
                    raise _AutomationRecipeError(f"Builder command {index} must be an object.")
                action = str(command.get("action") or "").strip()
                section_name = str(command.get("section") or "").strip()
                if action == "rebuild_assemble":
                    command_results.append(
                        _run_builder_rebuild_assemble_command_lightweight(
                            builder_ui=builder_ui,
                            command=command,
                            command_index=index,
                            sections=sections,
                            output_project=output_project,
                        )
                    )
                    continue
                if action != "update_section":
                    raise _AutomationRecipeError(
                        f"Unsupported builder command {index}: action={action!r}, section={section_name!r}."
                    )
                command_results.append(
                    _run_builder_update_section_command(
                        builder_ui=builder_ui,
                        command=command,
                        command_index=index,
                        section_name=section_name,
                        sections=sections,
                        base_dir=base_dir,
                    )
                )
        finally:
            builder_storage._storage_root = original_storage_root  # type: ignore[assignment]
            builder_storage.MiniDatabaseStore._memory_data = {}
            builder_storage.MiniDatabaseStore._memory_payloads = {}
            builder_storage.MiniDatabaseStore._pending_sections = set()
            builder_storage.MiniDatabaseStore._pending_payloads = set()
            builder_storage.MiniDatabaseStore._disk_writes_suspended = 0

        project_payload["saved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        _write_json(output_project, project_payload)
        manifest = {
            "kind": "builder",
            "version": 1,
            "status": "ok",
            "source_project": str(project_path.resolve()),
            "copied_project": str(output_project.resolve()),
            "manifest_path": str(manifest_path.resolve()),
            "commands": command_results,
        }
        if database_paths is not None:
            _promote_builder_database_latest(
                database_paths=database_paths,
                output_project=output_project,
                manifest_path=manifest_path,
                manifest=manifest,
            )
        else:
            _write_json(manifest_path, manifest)
        print(json.dumps(manifest, ensure_ascii=False))
        return 0
    except _AutomationRecipeError as exc:
        print(f"[automation-recipe] {exc}")
        return 2
    except Exception as exc:
        print(f"[automation-recipe] {type(exc).__name__}: {exc}")
        return 1


def _load_automation_recipe_request(recipe_path: Path) -> _PyPlotAutomationRequest:
    recipe = _load_json_object(recipe_path, label="Automation recipe")
    base_dir = recipe_path.parent

    kind = recipe.get("kind")
    if kind != "pyplot":
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
        "--mini-dma-bench-plan",
        default=None,
        help="Run or dry-run an explicitly armed Mini DMA bench automation plan JSON file.",
    )
    parser.add_argument(
        "--experiment-process",
        choices=tuple(EXPERIMENT_PROCESS_MODULES),
        default=None,
        help=argparse.SUPPRESS,
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
        "--pyplot-session-start",
        action="store_true",
        help="Launch a persistent PyPlot automation session and keep it running.",
    )
    parser.add_argument(
        "--pyplot-session-list",
        action="store_true",
        help="List currently registered live PyPlot automation sessions and exit.",
    )
    parser.add_argument(
        "--pyplot-session-send",
        action="store_true",
        help="Send a JSON automation command to a live PyPlot session and exit.",
    )
    parser.add_argument(
        "--pyplot-session-state",
        action="store_true",
        help="Fetch the current state from a live PyPlot session and exit.",
    )
    parser.add_argument(
        "--pyplot-session-close",
        action="store_true",
        help="Request that a live PyPlot session close and exit.",
    )
    parser.add_argument(
        "--pyplot-session-id",
        default=None,
        help="Live PyPlot session id for session-state/send/close commands.",
    )
    parser.add_argument(
        "--pyplot-session-command-json",
        default=None,
        help="Inline JSON command payload for --pyplot-session-send.",
    )
    parser.add_argument(
        "--pyplot-session-command-file",
        default=None,
        help="Path to a JSON command payload for --pyplot-session-send.",
    )
    parser.add_argument(
        "--pyplot-session-info-file",
        default=None,
        help="When starting a live PyPlot session, also write the session metadata JSON to this path.",
    )
    parser.add_argument(
        "--visual-check",
        action="store_true",
        help="Run automated visual verification flow instead of opening the launcher UI.",
    )
    parser.add_argument(
        "--visual-plugin",
        default="manual-stress-strain",
        help="Plugin visual-check target. Currently supported: manual-stress-strain.",
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
        help="Manual stress/strain graph layout for visual-check mode.",
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
    parser.add_argument(
        "--microwire-eda",
        default=None,
        help="Generate a Microwire EDA report from a .pydpj project or assembled spreadsheet and exit.",
    )
    parser.add_argument(
        "--microwire-word-report",
        default=None,
        help=(
            "Generate Word sample reports from a Builder .pydpj, assembled spreadsheet, "
            "or direct R vs T CSV without opening the Builder UI."
        ),
    )
    parser.add_argument(
        "--microwire-word-job",
        default=None,
        help=(
            "Run a Microwire Word export job request JSON and write machine-readable "
            "status/progress artifacts."
        ),
    )
    parser.add_argument(
        "--microwire-word-sample",
        default=None,
        help='Limit the Word export to one sample, e.g. "Ni50Fe27Ga23 12/2".',
    )
    parser.add_argument(
        "--microwire-word-force-project-rebuild",
        action="store_true",
        help="For .pydpj Word exports, rebuild Assemble rows transiently before writing reports.",
    )
    parser.add_argument(
        "--microwire-word-origin",
        dest="microwire_word_origin",
        action="store_true",
        help="Generate available Origin objects for Word reports (default).",
    )
    parser.add_argument(
        "--no-microwire-word-origin",
        dest="microwire_word_origin",
        action="store_false",
        help="Skip Origin object generation for Word report CLI runs.",
    )
    parser.add_argument(
        "--microwire-word-graphs-only",
        action="store_true",
        help="Only write Word reports for microwires with at least one generated Origin graph descriptor.",
    )
    parser.add_argument(
        "--rows",
        choices=(("all", "filtered", "selected")),
        default="all",
        help="Row scope for Microwire EDA CLI runs. Builder-provided filtered/selected scopes are only available when launched from the Builder UI.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory for Microwire EDA and Microwire Word CLI runs.",
    )
    parser.add_argument(
        "--microwire-eda-title",
        default="Microwire EDA Report",
        help="Report title for Microwire EDA output.",
    )
    parser.add_argument(
        "--microwire-eda-working-copy-dir",
        default=None,
        help="Directory for the disposable Microwire EDA project copy used during CLI runs.",
    )
    parser.add_argument(
        "--microwire-eda-copy-project",
        dest="microwire_eda_copy_project",
        action="store_true",
        help="Copy .pydpj inputs to a disposable working path before analysis (default).",
    )
    parser.add_argument(
        "--no-microwire-eda-copy-project",
        dest="microwire_eda_copy_project",
        action="store_false",
        help="Analyze the source .pydpj directly instead of making a disposable copy.",
    )
    parser.add_argument(
        "--microwire-eda-no-legacy-breakage",
        dest="microwire_eda_legacy_breakage",
        action="store_false",
        help="Disable the auxiliary broke/OK legacy analysis section.",
    )
    parser.add_argument(
        "--microwire-eda-no-composition-splits",
        dest="microwire_eda_composition_splits",
        action="store_false",
        help="Disable composition-split summaries in Microwire EDA output.",
    )
    parser.add_argument(
        "--microwire-eda-no-findings",
        dest="microwire_eda_findings",
        action="store_false",
        help="Disable findings JSON/Markdown outputs for Microwire EDA runs.",
    )
    parser.add_argument(
        "--microwire-eda-force-project-rebuild",
        dest="microwire_eda_force_project_rebuild",
        action="store_true",
        help="Rebuild Assemble rows transiently from the Builder project sections even when saved Assemble rows already exist.",
    )
    parser.add_argument(
        "--microwire-eda-aggregation",
        choices=("raw", "per_wire_median", "per_wire_best"),
        default="raw",
        help="How Microwire EDA should treat repeated measurements of the same Composition+Microwire key.",
    )
    parser.set_defaults(
        visual_origin=True,
        microwire_eda_copy_project=True,
        microwire_eda_legacy_breakage=True,
        microwire_eda_composition_splits=True,
        microwire_eda_findings=True,
        microwire_eda_force_project_rebuild=False,
        microwire_word_origin=True,
    )
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


def _is_mini_dma_bench_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "mini_dma_bench_plan", None))


def _is_experiment_process_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "experiment_process", None))


def _is_pyplot_session_requested(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "pyplot_session_start", False)
        or getattr(args, "pyplot_session_list", False)
        or getattr(args, "pyplot_session_send", False)
        or getattr(args, "pyplot_session_state", False)
        or getattr(args, "pyplot_session_close", False)
    )


def _is_microwire_eda_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "microwire_eda", None))


def _is_microwire_word_report_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "microwire_word_report", None))


def _is_microwire_word_job_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "microwire_word_job", None))


def _run_microwire_eda_cli(args: argparse.Namespace) -> int:
    from microwire_eda import MicrowireEdaConfig
    from microwire_eda.core import generate_report

    input_path = Path(str(getattr(args, "microwire_eda", "")).strip()).expanduser()
    output_dir_value = getattr(args, "out", None)
    output_dir = Path(str(output_dir_value)).expanduser() if output_dir_value else None
    working_copy_dir_value = getattr(args, "microwire_eda_working_copy_dir", None)
    working_copy_dir = (
        Path(str(working_copy_dir_value)).expanduser()
        if working_copy_dir_value
        else None
    )
    config = MicrowireEdaConfig(
        input_path=input_path,
        row_scope=str(getattr(args, "rows", "all") or "all"),
        output_dir=output_dir,
        report_title=str(getattr(args, "microwire_eda_title", "Microwire EDA Report")),
        working_copy_dir=working_copy_dir,
        copy_project=bool(getattr(args, "microwire_eda_copy_project", True)),
        force_project_rebuild=bool(getattr(args, "microwire_eda_force_project_rebuild", False)),
        aggregation_mode=str(getattr(args, "microwire_eda_aggregation", "raw") or "raw"),
        include_legacy_breakage_analysis=bool(getattr(args, "microwire_eda_legacy_breakage", True)),
        include_composition_splits=bool(getattr(args, "microwire_eda_composition_splits", True)),
        write_findings=bool(getattr(args, "microwire_eda_findings", True)),
    )
    result = generate_report(config)
    print(f"[microwire-eda] report={result.report_path}")
    print(f"[microwire-eda] workbook={result.workbook_path}")
    print(f"[microwire-eda] dataset={result.csv_path}")
    print(f"[microwire-eda] manifest={result.manifest_path}")
    if result.findings_json_path is not None:
        print(f"[microwire-eda] findings_json={result.findings_json_path}")
    if result.findings_md_path is not None:
        print(f"[microwire-eda] findings_md={result.findings_md_path}")
    if result.copied_project_path is not None:
        print(f"[microwire-eda] copied_project={result.copied_project_path}")
    if getattr(result, "used_project_rebuild", False):
        print("[microwire-eda] rebuilt_assemble=true")
    if result.findings:
        for finding in result.findings[:3]:
            print(f"[microwire-eda] finding={finding.get('headline', 'Finding')}")
    return 0


_RVST_CSV_HEADER = ("iso_time", "t_elapsed_s", "sp_c", "pv_c", "resistance_ohm")
_MINI_DMA_REQUIRED_COLUMNS = {
    "elapsed_s",
    "automation_phase",
    "automation_target_value",
    "plateau_index",
    "strain_pct",
    "resistance_ohm",
}
_MINI_DMA_EXCLUDED_SCAN_DIRS = {
    ".cache",
    ".pytest_cache",
    "__pycache__",
    "_cache",
    "_scratch",
    "archive",
    "automation",
    "automation_history",
    "automated_control_tests",
    "automated",
    "cache",
    "cached",
    "scratch",
    "test",
    "tests",
}


def _parse_microwire_word_sample(sample: object) -> tuple[str | None, str | None]:
    text = str(sample or "").strip()
    if not text:
        return None, None
    match = re.search(
        r"^(?P<composition>\S+)\s+(?P<draw>\d+)\s*[/_\-]\s*(?P<piece>\d+[A-Za-z0-9]*)",
        text,
    )
    if match:
        return (
            match.group("composition"),
            f"{match.group('draw')}/{match.group('piece')}",
        )
    return text, None


def _normalise_microwire_word_part(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).casefold()


def _normalise_microwire_word_key(value: object) -> str:
    text = _normalise_microwire_word_part(value)
    return text.replace("\\", "/").replace("-", "/").replace("_", "/")


def _looks_like_rvst_csv(path: Path) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    try:
        header = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return False
    columns = tuple(column.strip().casefold() for column in header.split(";"))
    return columns[: len(_RVST_CSV_HEADER)] == _RVST_CSV_HEADER


def _looks_like_mini_dma_measurement(path: Path) -> bool:
    if path.name.casefold() != "measurement.csv":
        return False
    try:
        header = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return False
    columns = {column.strip().casefold() for column in header.split(",")}
    current_columns = {"current_measured_ma", "current_set_ma"}
    return _MINI_DMA_REQUIRED_COLUMNS.issubset(columns) and bool(columns.intersection(current_columns))


def _is_active_mini_dma_measurement(path: Path) -> bool:
    if any(part.casefold() in _MINI_DMA_EXCLUDED_SCAN_DIRS for part in path.parts):
        return False
    return path.is_file() and _looks_like_mini_dma_measurement(path)


def _infer_rvst_word_sample(path: Path, sample_override: object) -> tuple[str, str]:
    composition, microwire = _parse_microwire_word_sample(sample_override)
    if composition and microwire:
        return composition, microwire

    tokens = [token for token in re.split(r"[_\s]+", path.stem.strip()) if token]
    if len(tokens) >= 3 and tokens[1].isdigit():
        piece_match = re.match(r"(?P<piece>\d+[A-Za-z0-9]*)", tokens[2])
        if piece_match:
            return tokens[0], f"{tokens[1]}/{piece_match.group('piece')}"
    if composition:
        return composition, microwire or ""
    return path.stem, ""


def _infer_mini_dma_word_sample(path: Path) -> tuple[str, str]:
    container = path.parent if path.name.casefold() == "measurement.csv" else path
    text = container.name.strip()
    tokens = [token for token in re.split(r"[_\s]+", text) if token]
    if len(tokens) >= 3 and tokens[1].isdigit():
        piece_match = re.match(r"(?P<piece>\d+[A-Za-z0-9]*)", tokens[2])
        if piece_match:
            return tokens[0], f"{tokens[1]}/{piece_match.group('piece')}"
    composition, microwire = _parse_microwire_word_sample(text.replace("_", "/"))
    return composition or text, microwire or ""


def _format_numeric_range(values: object) -> str:
    import pandas as pd

    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return ""
    return f"{float(numeric.min()):.6g} to {float(numeric.max()):.6g}"


def _set_origin_plot_color(plot_obj: object, color: str) -> None:
    for attr, value in (
        ("color", color),
        ("symbol_edge_color", color),
        ("symbol_fill_color", color),
        ("line_width", 1.5),
        ("symbol_kind", 0),
    ):
        if not hasattr(plot_obj, attr):
            continue
        try:
            setattr(plot_obj, attr, value)
        except Exception:
            continue


def _export_rvst_origin_artifact(
    rvst_frame: object,
    *,
    title: str,
    source_path: Path,
    output_dir: Path,
):
    import pandas as pd

    from microwire_data_builder.core import export_origin_graph_artifact, _safe_plot_stem
    from plotting.plugins.r_vs_t import core as rvst_core
    from plotting.shared.origin import (
        _ensure_origin_sdk_on_path,
        hide_origin_workbook,
        origin_safe_token,
        set_origin_axis_title,
        set_origin_graph_title,
    )

    segments = rvst_core.split_heating_cooling(pd.DataFrame(rvst_frame))
    if not segments:
        return None

    data: dict[str, pd.Series] = {}
    for segment in segments:
        data[f"{segment.label} temperature"] = pd.Series(segment.x)
        data[f"{segment.label} resistance"] = pd.Series(segment.y)
    workbook_frame = pd.DataFrame(data)
    origin_dir = output_dir / "_origin_objects"
    origin_dir.mkdir(parents=True, exist_ok=True)
    descriptor_stem = f"{_safe_plot_stem(source_path.stem)}_rvst"

    _ensure_origin_sdk_on_path()
    import originpro as origin_any  # type: ignore

    try:
        origin_any.set_show()
    except Exception:
        pass

    try:
        lt_int = getattr(origin_any, "lt_int", None)
        if callable(lt_int):
            lt_int("@V")
    except Exception:
        pass
    book_name = origin_safe_token(f"{source_path.stem}_rvst", fallback="RvsT", max_len=32)
    workbook = origin_any.new_book("w", lname=book_name)
    worksheet = workbook[0]
    try:
        worksheet.name = origin_safe_token("RvsT", fallback="RvsT", max_len=13)
    except Exception:
        pass
    worksheet.from_df(workbook_frame)
    try:
        worksheet.cols_axis("".join("XY" for _segment in segments))
    except Exception:
        pass

    graph = None
    for template in ("line", "ORIGIN"):
        try:
            graph = origin_any.new_graph(template=template)
        except Exception:
            graph = None
        if graph is not None:
            break
    if graph is None:
        graph = origin_any.new_graph()
    layer = graph[0]
    for index, segment in enumerate(segments):
        try:
            plot_obj = layer.add_plot(worksheet, coly=(index * 2) + 1, colx=index * 2, type="y")
        except TypeError:
            plot_obj = layer.add_plot(worksheet, coly=(index * 2) + 1, colx=index * 2)
        try:
            plot_obj.lname = segment.label
        except Exception:
            pass
        palette = rvst_core.HEATING_COLORS if segment.kind == "heating" else rvst_core.COOLING_COLORS
        color = palette[index % len(palette)] if palette else ""
        if color:
            _set_origin_plot_color(plot_obj, color)
    try:
        layer.rescale()
    except Exception:
        pass
    set_origin_axis_title(layer, "x", "Temperature (deg C)")
    set_origin_axis_title(layer, "y", "Resistance (Ohm)")
    set_origin_graph_title(origin_any, graph, layer, title)
    hide_origin_workbook(origin_any, workbook, graph)

    artifact = export_origin_graph_artifact(
        handles={
            "origin": origin_any,
            "graph": graph,
            "workbook": workbook,
            "worksheet": worksheet,
            "legend_label": title,
        },
        descriptor_stem=descriptor_stem,
        origin_dir=origin_dir,
        display_text=f"R vs T Origin graph: {title}",
        log=LOGGER,
    )

    if artifact is None or (artifact.object_path is None and not getattr(artifact, "clipboard_fallback", False)):
        return None
    return artifact


def _load_rvst_word_report_frame(source_path: Path, sample_override: object, output_dir: Path, *, include_origin: bool):
    import pandas as pd

    from microwire_data_builder.core import (
        RVT_FILE_COLUMN,
        RVT_GRAPH_COLUMN,
        RVT_ORIGIN_COLUMN,
        RVT_RESIDUAL_ORIGIN_COLUMN,
        RVT_POINT_COUNT_COLUMN,
        RVT_RESISTANCE_RANGE_COLUMN,
        RVT_TEMPERATURE_RANGE_COLUMN,
        _safe_plot_stem,
    )
    from plotting.plugins.r_vs_t.core import load_file

    frame = load_file(source_path)
    composition, microwire = _infer_rvst_word_sample(source_path, sample_override)
    first_timestamp = ""
    if "iso_time" in frame.columns and not frame.empty:
        try:
            parsed_timestamp = pd.to_datetime(frame["iso_time"].iloc[0], errors="coerce")
        except Exception:
            parsed_timestamp = pd.NaT
        if pd.notna(parsed_timestamp):
            first_timestamp = parsed_timestamp.isoformat(sep=" ", timespec="seconds")
        else:
            first_timestamp = str(frame["iso_time"].iloc[0]).strip()
    title = " ".join(part for part in (composition, microwire) if part).strip() or source_path.stem
    origin_artifacts = {}
    row = {
        "Composition": composition,
        "Microwire": microwire,
        "Production datetime": first_timestamp,
        "Data source": "R vs T CSV",
        RVT_FILE_COLUMN: str(source_path),
        RVT_POINT_COUNT_COLUMN: int(len(frame)),
        RVT_TEMPERATURE_RANGE_COLUMN: _format_numeric_range(frame["pv_c"]),
        RVT_RESISTANCE_RANGE_COLUMN: _format_numeric_range(frame["resistance_ohm"]),
    }
    if include_origin:
        try:
            artifacts = _export_pyplot_origin_artifacts_for_paths(
                paths=[source_path],
                plugin_name="R vs T",
                output_dir=output_dir,
                descriptor_prefix=_safe_plot_stem(f"{source_path.stem}_rvst"),
                display_prefix=f"R vs T Origin graph: {title}",
            )
        except Exception as exc:  # pragma: no cover - depends on local Origin/COM setup
            LOGGER.warning("R vs T Origin object generation skipped for %s: %s", source_path, exc)
            artifacts = []
        if artifacts:
            artifact = artifacts[0]
            row[RVT_GRAPH_COLUMN] = artifact.display_text or artifact.descriptor
            row[RVT_ORIGIN_COLUMN] = artifact.descriptor
            origin_artifacts[artifact.descriptor] = artifact
    return pd.DataFrame([row]), origin_artifacts


def _export_pyplot_origin_artifacts_for_paths(
    *,
    paths: list[Path],
    plugin_name: str,
    output_dir: Path,
    descriptor_prefix: str,
    display_prefix: str,
    plot_mode: str | None = None,
) -> list[object]:
    from microwire_data_builder.core import export_pyplot_origin_artifacts_for_paths

    return list(
        export_pyplot_origin_artifacts_for_paths(
            paths=paths,
            plugin_name=plugin_name,
            origin_dir=output_dir / "_origin_objects",
            descriptor_prefix=descriptor_prefix,
            display_prefix=display_prefix,
            log=LOGGER,
            plot_mode=plot_mode,
        )
    )


def _project_section_rows(section: object) -> list[dict[str, Any]]:
    if not isinstance(section, dict):
        return []
    rows = section.get("rows")
    columns = section.get("columns")
    if not isinstance(rows, list):
        return []
    if rows and isinstance(rows[0], dict):
        return [dict(row) for row in rows if isinstance(row, dict)]
    if isinstance(columns, list):
        column_names = [str(column) for column in columns]
        converted: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, list):
                converted.append(dict(zip(column_names, row)))
        return converted
    return []


def _decode_word_project_payload(payload: object) -> object:
    if not isinstance(payload, Mapping):
        return None
    if payload.get("encoding") != "pickle-base64":
        return None
    value = payload.get("value")
    if not isinstance(value, str):
        return None
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
        return pickle.loads(raw)
    except Exception:
        return None


def _word_project_section_payload(section: object, payload_name: str) -> object:
    if not isinstance(section, Mapping):
        return None
    payloads = section.get("payloads")
    if not isinstance(payloads, Mapping):
        return None
    return _decode_word_project_payload(payloads.get(payload_name))


def _word_project_record_sample(record: object) -> tuple[str, str]:
    key = getattr(record, "key", None)
    if isinstance(key, (list, tuple)) and len(key) >= 3:
        composition = str(key[0] or "").strip()
        draw = str(key[1] or "").strip()
        piece = str(key[2] or "").strip()
        suffix = str(key[3] or "").strip() if len(key) >= 4 and key[3] is not None else ""
        if composition and draw and piece:
            microwire = f"{draw}/{piece}"
            if suffix:
                microwire = f"{microwire} {suffix}"
            return composition, microwire
    sample = getattr(record, "sample", "")
    composition, microwire = _parse_microwire_word_sample(sample)
    return composition or "", microwire or ""


def _word_project_record_path(record: object) -> Path | None:
    for attr in ("path", "source_path"):
        value = getattr(record, attr, None)
        if value:
            return Path(value)
    if isinstance(record, Mapping):
        for key in ("path", "source_path", "Source path", "Path"):
            value = record.get(key)
            if value:
                return Path(str(value))
    return None


def _word_project_shape_memory_payload_sources(
    section: object,
) -> dict[tuple[str, str], list[str]]:
    records = _word_project_section_payload(
        section,
        "shape_memory_stress_strain_records",
    )
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        return {}
    sources_by_key: dict[tuple[str, str], list[str]] = {}
    for record in records:
        composition, microwire = _word_project_record_sample(record)
        path = _word_project_record_path(record)
        if not composition or not microwire or path is None:
            continue
        key = (
            _normalise_microwire_word_part(composition),
            _normalise_microwire_word_key(microwire),
        )
        sources_by_key.setdefault(key, []).append(str(path))
    return {
        key: list(dict.fromkeys(paths))
        for key, paths in sources_by_key.items()
        if paths
    }


def _word_project_row_sample(row: dict[str, Any]) -> tuple[str, str]:
    composition = str(row.get("Composition") or "").strip()
    microwire = str(row.get("Microwire") or "").strip()
    if not microwire:
        draw = row.get("Draw")
        piece = row.get("Piece")
        try:
            draw_text = str(int(float(draw)))
            piece_text = str(int(float(piece)))
        except (TypeError, ValueError):
            draw_text = str(draw or "").strip()
            piece_text = str(piece or "").strip()
        if draw_text and piece_text:
            microwire = f"{draw_text}/{piece_text}"
    return composition, microwire


def _word_project_value_items(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, float) and value != value:
        return []
    if isinstance(value, (list, tuple, set)):
        items: list[object] = []
        for item in value:
            items.extend(_word_project_value_items(item))
        return items
    try:
        if value in ("", [], {}):
            return []
    except Exception:
        pass
    return [value]


def _word_project_merge_value(existing: object, incoming: object) -> object:
    incoming_items = _word_project_value_items(incoming)
    if not incoming_items:
        return existing
    existing_items = _word_project_value_items(existing)
    merged: list[object] = []
    seen: set[str] = set()
    for item in [*existing_items, *incoming_items]:
        try:
            marker = f"{float(item):.12g}"
        except (TypeError, ValueError):
            marker = str(item)
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(item)
    return merged if len(merged) > 1 else merged[0]


def _word_project_public_source_name(source: object) -> str:
    text = str(source or "").strip()
    if not text:
        return ""
    return Path(text).name


def _word_project_add_current_annealing_sources(target: dict[str, Any], sources: object) -> None:
    from microwire_data_builder.core import FIGURE_COLUMNS

    high_sources: list[str] = []
    other_sources: list[str] = []
    for item in _word_project_value_items(sources):
        text = str(item)
        filename = Path(text).name
        if "1000ma" in filename.replace(" ", "").casefold():
            high_sources.append(text)
        else:
            other_sources.append(text)
    if high_sources:
        target["_word_annealing_1000_sources"] = _word_project_merge_value(
            target.get("_word_annealing_1000_sources"),
            high_sources,
        )
        target[FIGURE_COLUMNS[0]] = _word_project_merge_value(
            target.get(FIGURE_COLUMNS[0]),
            [_word_project_public_source_name(source) for source in high_sources],
        )
    if other_sources:
        target["_word_annealing_other_sources"] = _word_project_merge_value(
            target.get("_word_annealing_other_sources"),
            other_sources,
        )
        target[FIGURE_COLUMNS[1]] = _word_project_merge_value(
            target.get(FIGURE_COLUMNS[1]),
            [_word_project_public_source_name(source) for source in other_sources],
        )


_WORD_PROJECT_GRAPH_SOURCE_SPECS: dict[str, tuple[str, str, str, str, str]] = {
    "vsm_temperature_scan": (
        "_word_vsm_temperature_scan_sources",
        "VSM temperature scan graphs",
        "VSM temperature scan graphs (Origin)",
        "VSM Temperature Scan",
        "VSM temperature scan Origin graph",
    ),
    "vsm_hysteresis": (
        "_word_vsm_hysteresis_sources",
        "VSM hysteresis graphs",
        "VSM hysteresis graphs (Origin)",
        "VSM Hysteresis Loops",
        "VSM hysteresis Origin graph",
    ),
    "dma_iso_stress": (
        "_word_dma_iso_stress_sources",
        "DMA iso-stress graphs",
        "DMA iso-stress graphs (Origin)",
        "DMA Iso-Stress",
        "DMA iso-stress Origin graph",
    ),
    "mini_dma": (
        "_word_mini_dma_sources",
        "Mini DMA graphs",
        "Mini DMA graphs (Origin)",
        "Mini DMA",
        "Mini DMA Origin graph",
    ),
    "shape_memory_stress_strain": (
        "_word_shape_memory_stress_strain_sources",
        "Manual stress/strain graphs",
        "Manual stress/strain graphs (Origin)",
        "Manual Stress/Strain",
        "Manual stress/strain Origin graph",
    ),
    "fmr": (
        "_word_fmr_sources",
        "FMR graphs",
        "FMR graphs (Origin)",
        "FMR",
        "FMR Origin graph",
    ),
}


_WORD_REPORT_GRAPH_MANIFEST_SECTIONS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "Current annealing",
        ("_word_annealing_1000_sources", "_word_annealing_other_sources"),
        ("Figure — 1000 mA", "Figure — other annealing", "Figure — 1000 mA (Origin)", "Figure — other annealing (Origin)"),
    ),
    (
        "R vs T",
        ("R vs T files",),
        ("R vs T graphs", "R vs T graphs (Origin)", "R vs T residual graphs (Origin)"),
    ),
    (
        "VSM temperature scan",
        ("_word_vsm_temperature_scan_sources",),
        ("VSM temperature scan graphs", "VSM temperature scan graphs (Origin)"),
    ),
    (
        "VSM hysteresis loops",
        ("_word_vsm_hysteresis_sources",),
        ("VSM hysteresis graphs", "VSM hysteresis graphs (Origin)"),
    ),
    (
        "DMA iso-stress",
        ("_word_dma_iso_stress_sources",),
        ("DMA iso-stress graphs", "DMA iso-stress graphs (Origin)"),
    ),
    (
        "Mini DMA",
        ("_word_mini_dma_sources",),
        ("Mini DMA graphs", "Mini DMA graphs (Origin)"),
    ),
    (
        "Manual stress/strain",
        ("_word_shape_memory_stress_strain_sources",),
        (
            "Manual stress/strain graphs",
            "Manual stress/strain graphs (Origin)",
            "Shape memory stress/strain graphs",
            "Shape memory stress/strain graphs (Origin)",
        ),
    ),
    (
        "FMR",
        ("_word_fmr_sources",),
        ("FMR graphs", "FMR graphs (Origin)"),
    ),
)


def _word_project_add_graph_sources(
    target: dict[str, Any],
    section_name: str,
    sources: object,
) -> None:
    spec = _WORD_PROJECT_GRAPH_SOURCE_SPECS.get(section_name)
    if spec is None:
        return
    source_column, graph_column, _origin_column, _plugin_name, _display_prefix = spec
    source_values = [str(item) for item in _word_project_value_items(sources) if str(item or "").strip()]
    if not source_values:
        return
    target[source_column] = _word_project_merge_value(target.get(source_column), source_values)
    target[graph_column] = _word_project_merge_value(
        target.get(graph_column),
        [_word_project_public_source_name(source) for source in source_values],
    )


def _word_project_shape_memory_current(path: Path) -> float | None:
    match = re.search(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*mA\b", path.stem, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _word_project_current_density(current_mA: float | None, diameter_um: object) -> float | None:
    if current_mA is None:
        return None
    try:
        diameter = float(diameter_um)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(diameter) or diameter <= 0:
        return None
    area_mm2 = math.pi * (diameter / 2000.0) ** 2
    if area_mm2 <= 0:
        return None
    return current_mA / area_mm2


def _word_project_shape_memory_summary(path: Path) -> dict[str, Any]:
    try:
        from plotting.plugins.shape_memory_stress_strain.core import load_manual_stress_strain_file
        import pandas as pd
    except Exception:
        return {}
    try:
        frame = load_manual_stress_strain_file(path)
    except Exception:
        return {}
    if frame.empty:
        return {}

    def _max(column: str) -> float | None:
        if column not in frame.columns:
            return None
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        if values.empty:
            return None
        return float(values.max())

    summary: dict[str, Any] = {}
    is_fracture = "fracture" in path.stem.casefold()
    current = _word_project_shape_memory_current(path)
    if is_fracture:
        summary["Fracture load (g)"] = _max("load_g")
        summary["Fracture strain (%)"] = _max("strain_pct")
        summary["Fracture stress (MPa)"] = _max("stress_mpa")
        if current is not None:
            summary["Fracture stress/strain current (mA)"] = current
    else:
        summary["Load (g)"] = _max("load_g")
        summary["Strain (%)"] = _max("strain_pct")
        summary["Stress (MPa)"] = _max("stress_mpa")
        if current is not None:
            summary["Stress/strain current (mA)"] = current
    return {key: value for key, value in summary.items() if value not in (None, "")}


def _word_project_enrich_shape_memory_row(source_row: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(source_row)
    for source in _word_project_value_items(source_row.get("_sources")):
        path = Path(str(source))
        if not path.exists():
            continue
        for column, value in _word_project_shape_memory_summary(path).items():
            if enriched.get(column) in (None, ""):
                enriched[column] = value
    return enriched


def _load_project_word_report_frame(
    source_path: Path,
    sample: object,
    output_dir: Path,
    *,
    include_origin: bool,
    extra_search_roots: Sequence[Path] | None = None,
):
    import pandas as pd

    from microwire_data_builder.core import (
        FIGURE_COLUMNS,
        DIAMETER_COLUMN,
        DIAMETER_RATIO_COLUMN,
        GLASS_DIAMETER_COLUMN,
        MICROSCOPE_IMAGE_COLUMNS,
        DMA_ISOSTRESS_ORIGIN_COLUMN,
        FMR_ORIGIN_COLUMN,
        MINI_DMA_COLUMN,
        MINI_DMA_ORIGIN_COLUMN,
        RVT_FILE_COLUMN,
        RVT_GRAPH_COLUMN,
        RVT_ORIGIN_COLUMN,
        RVT_RESIDUAL_ORIGIN_COLUMN,
        RVT_POINT_COUNT_COLUMN,
        RVT_RESISTANCE_RANGE_COLUMN,
        RVT_TEMPERATURE_RANGE_COLUMN,
        SHAPE_MEMORY_STRESS_STRAIN_ORIGIN_COLUMN,
        VSM_HYSTERESIS_ORIGIN_COLUMN,
        VSM_TEMPERATURE_SCAN_ORIGIN_COLUMN,
        WORD_MICROWIRE_DATA_COLUMNS,
        _load_annealing,
        _plot_measurement_origin,
        _safe_plot_stem,
    )
    from microwire_data_builder import ui as builder_ui
    from plotting.plugins.r_vs_t.core import load_file

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    sections = payload.get("sections") if isinstance(payload, dict) else {}
    if not isinstance(sections, dict):
        raise ValueError("Project file does not contain Builder sections.")

    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    assemble_rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    microscope_dimension_columns = {DIAMETER_COLUMN, GLASS_DIAMETER_COLUMN, DIAMETER_RATIO_COLUMN}
    rvt_search_roots: list[Path] = [source_path.parent]
    for root in extra_search_roots or ():
        if root not in rvt_search_roots:
            rvt_search_roots.append(root)
    shape_memory_payload_sources = _word_project_shape_memory_payload_sources(
        sections.get("shape_memory_stress_strain")
    )

    def _remember_rvt_search_root(value: object) -> None:
        for item in _word_project_value_items(value):
            text = str(item or "").strip()
            if not text:
                continue
            path = Path(text)
            try:
                if not path.exists():
                    continue
            except OSError:
                continue
            for parent in path.parents:
                try:
                    rvt_root = parent / "RvsT"
                    if rvt_root.exists() and parent not in rvt_search_roots:
                        rvt_search_roots.append(parent)
                except OSError:
                    continue

    for section_name, section in sections.items():
        if section_name in {"compare"}:
            continue
        if section_name == "mini_dma" and isinstance(section, Mapping):
            for item in _word_project_value_items(section.get("sources")):
                text = str(item or "").strip()
                if not text:
                    continue
                path = Path(text)
                try:
                    if not path.exists():
                        continue
                except OSError:
                    continue
                search_root = path.parent if path.name.casefold() in {"mini dma", "mini_dma"} else path
                if search_root not in rvt_search_roots:
                    rvt_search_roots.append(search_root)
        for source_row in _project_section_rows(section):
            if section_name == "shape_memory_stress_strain":
                source_row = _word_project_enrich_shape_memory_row(source_row)
            composition, microwire = _word_project_row_sample(source_row)
            if not composition or not microwire:
                continue
            key = (
                _normalise_microwire_word_part(composition),
                _normalise_microwire_word_key(microwire),
            )
            if section_name == "assemble":
                assemble_rows_by_key[key] = dict(source_row)
            target = rows_by_key.setdefault(
                key,
                {
                    "Composition": composition,
                    "Microwire": microwire,
                },
            )
            for column, value in source_row.items():
                if column in {"_key", "_group_key", "_shape_memory_group_key", "_shape_memory_group_order"}:
                    continue
                if column == "Graph — 1000 mA":
                    column = FIGURE_COLUMNS[0]
                elif column == "Graph — other annealing":
                    column = FIGURE_COLUMNS[1]
                elif column == "_core_image":
                    column = MICROSCOPE_IMAGE_COLUMNS[0]
                elif column == "_glass_image":
                    column = MICROSCOPE_IMAGE_COLUMNS[1]
                elif column == "_sources" and section_name == "annealing":
                    _remember_rvt_search_root(value)
                    _word_project_add_current_annealing_sources(target, value)
                    continue
                elif column == "_sources" and section_name in _WORD_PROJECT_GRAPH_SOURCE_SPECS:
                    _remember_rvt_search_root(value)
                    _word_project_add_graph_sources(target, section_name, value)
                    continue
                elif column.startswith("_"):
                    continue
                if column in microscope_dimension_columns and section_name != "microscope":
                    continue
                if section_name == "assemble":
                    if _word_project_value_items(value) or not _word_project_value_items(
                        target.get(column)
                    ):
                        target[column] = value
                else:
                    target[column] = _word_project_merge_value(target.get(column), value)

    for key, assemble_row in assemble_rows_by_key.items():
        target = rows_by_key.get(key)
        if target is None:
            continue
        for column in WORD_MICROWIRE_DATA_COLUMNS:
            if column in assemble_row:
                target[column] = assemble_row.get(column)

    for key, sources in shape_memory_payload_sources.items():
        target = rows_by_key.get(key)
        if target is None:
            continue
        target["_word_shape_memory_stress_strain_sources"] = _word_project_merge_value(
            target.get("_word_shape_memory_stress_strain_sources"),
            sources,
        )

    frame = pd.DataFrame(list(rows_by_key.values()))
    for index, row in frame.iterrows():
        diameter = row.get(DIAMETER_COLUMN)
        for current_column, density_column in (
            ("Stress/strain current (mA)", "Stress/strain current density (A/mm^2)"),
            (
                "Fracture stress/strain current (mA)",
                "Fracture stress/strain current density (A/mm^2)",
            ),
        ):
            if density_column not in frame.columns:
                frame[density_column] = pd.Series([None] * len(frame), dtype=object)
            else:
                frame[density_column] = frame[density_column].astype(object)
            if _word_project_value_items(row.get(density_column)):
                continue
            densities = []
            for value in _word_project_value_items(row.get(current_column)):
                try:
                    current_value = float(value)
                except (TypeError, ValueError):
                    continue
                density = _word_project_current_density(current_value, diameter)
                if density is not None:
                    densities.append(density)
            densities = [value for value in densities if value is not None]
            if densities:
                frame.at[index, density_column] = densities if len(densities) > 1 else densities[0]
    frame = _filter_microwire_word_report_frame(frame, sample)
    origin_artifacts: dict[str, Any] = {}
    for column in (
        f"{FIGURE_COLUMNS[0]} (Origin)",
        f"{FIGURE_COLUMNS[1]} (Origin)",
        RVT_FILE_COLUMN,
        RVT_GRAPH_COLUMN,
        RVT_ORIGIN_COLUMN,
        RVT_RESIDUAL_ORIGIN_COLUMN,
        RVT_POINT_COUNT_COLUMN,
        RVT_TEMPERATURE_RANGE_COLUMN,
        RVT_RESISTANCE_RANGE_COLUMN,
        VSM_TEMPERATURE_SCAN_ORIGIN_COLUMN,
        VSM_HYSTERESIS_ORIGIN_COLUMN,
        DMA_ISOSTRESS_ORIGIN_COLUMN,
        MINI_DMA_ORIGIN_COLUMN,
        SHAPE_MEMORY_STRESS_STRAIN_ORIGIN_COLUMN,
        FMR_ORIGIN_COLUMN,
    ):
        if column not in frame.columns:
            frame[column] = pd.Series([None] * len(frame), dtype=object)
        else:
            frame[column] = frame[column].astype(object)

    mini_dma_candidates: list[Path] = []
    seen_mini_dma_paths: set[Path] = set()
    for root in rvt_search_roots:
        for mini_root_name in ("mini DMA", "Mini DMA", "mini_dma"):
            mini_root = root / mini_root_name
            if not mini_root.exists():
                continue
            for path in mini_root.rglob("measurement.csv"):
                if any(
                    part.casefold() in _MINI_DMA_EXCLUDED_SCAN_DIRS
                    for part in path.relative_to(mini_root).parts[:-1]
                ):
                    continue
                try:
                    resolved = path.resolve()
                except OSError:
                    resolved = path
                if resolved in seen_mini_dma_paths or not path.is_file():
                    continue
                if not _is_active_mini_dma_measurement(path):
                    continue
                seen_mini_dma_paths.add(resolved)
                mini_dma_candidates.append(path)

    mini_dma_paths: list[Path] = []
    mini_dma_reportability: list[dict[str, Any]] = []
    if mini_dma_candidates:
        mini_dma_paths, mini_dma_reportability = builder_ui._reportable_mini_dma_measurements(
            mini_dma_candidates,
            sources=[str(path) for path in rvt_search_roots],
            excluded_dirs=_MINI_DMA_EXCLUDED_SCAN_DIRS,
        )

    if mini_dma_paths or mini_dma_reportability:
        source_column = "_word_mini_dma_sources"
        if source_column not in frame.columns:
            frame[source_column] = pd.Series([None] * len(frame), dtype=object)
        else:
            frame[source_column] = frame[source_column].astype(object)
        if MINI_DMA_COLUMN not in frame.columns:
            frame[MINI_DMA_COLUMN] = pd.Series([None] * len(frame), dtype=object)
        else:
            frame[MINI_DMA_COLUMN] = frame[MINI_DMA_COLUMN].astype(object)
        for index, row in frame.iterrows():
            composition = row.get("Composition")
            microwire = row.get("Microwire")
            matching_mini_dma = []
            for path in mini_dma_paths:
                inferred_composition, inferred_microwire = _infer_mini_dma_word_sample(path)
                if (
                    _normalise_microwire_word_part(inferred_composition)
                    == _normalise_microwire_word_part(composition)
                    and _normalise_microwire_word_key(inferred_microwire)
                    == _normalise_microwire_word_key(microwire)
                ):
                    matching_mini_dma.append(path.parent)
            if matching_mini_dma:
                values = [str(path) for path in dict.fromkeys(matching_mini_dma)]
                frame.at[index, source_column] = values if len(values) > 1 else values[0]
                graph_values = [path.name for path in dict.fromkeys(matching_mini_dma)]
                frame.at[index, MINI_DMA_COLUMN] = (
                    graph_values if len(graph_values) > 1 else graph_values[0]
                )
                continue
            blocked_mini_dma = []
            for entry in mini_dma_reportability:
                if bool(entry.get("reportable")):
                    continue
                measurement = entry.get("measurement")
                if not isinstance(measurement, str) or not measurement:
                    continue
                path = Path(measurement)
                inferred_composition, inferred_microwire = _infer_mini_dma_word_sample(path)
                if (
                    _normalise_microwire_word_part(inferred_composition)
                    == _normalise_microwire_word_part(composition)
                    and _normalise_microwire_word_key(inferred_microwire)
                    == _normalise_microwire_word_key(microwire)
                ):
                    blocked_mini_dma.append(entry)
            if blocked_mini_dma:
                frame.at[index, source_column] = None
                frame.at[index, MINI_DMA_COLUMN] = None

    if include_origin:
        origin_dir = output_dir / "_origin_objects"
        for index, row in frame.iterrows():
            for source_column, origin_column in (
                ("_word_annealing_1000_sources", f"{FIGURE_COLUMNS[0]} (Origin)"),
                ("_word_annealing_other_sources", f"{FIGURE_COLUMNS[1]} (Origin)"),
            ):
                descriptors: list[str] = []
                for source in _word_project_value_items(row.get(source_column)):
                    path = Path(str(source))
                    if not path.exists():
                        continue
                    try:
                        annealing_frame = _load_annealing(path)
                        artifact = _plot_measurement_origin(
                            annealing_frame,
                            path,
                            origin_dir,
                            LOGGER,
                        )
                    except Exception as exc:  # pragma: no cover - depends on local Origin/COM setup
                        LOGGER.warning("Current annealing Origin object generation skipped for %s: %s", path, exc)
                        artifact = None
                    if artifact is None:
                        continue
                    descriptors.append(artifact.descriptor)
                    origin_artifacts[artifact.descriptor] = artifact
                if descriptors:
                    frame.at[index, origin_column] = descriptors if len(descriptors) > 1 else descriptors[0]

            for section_name, spec in _WORD_PROJECT_GRAPH_SOURCE_SPECS.items():
                source_column, _graph_column, origin_column, plugin_name, display_prefix = spec
                descriptors = []
                source_paths = [
                    Path(str(source))
                    for source in _word_project_value_items(row.get(source_column))
                    if str(source or "").strip()
                ]
                source_paths = [path for path in dict.fromkeys(source_paths) if path.exists()]
                if not source_paths:
                    continue
                sample_title = " ".join(
                    part
                    for part in (
                        str(row.get("Composition") or "").strip(),
                        str(row.get("Microwire") or "").strip(),
                    )
                    if part
                ).strip()
                prefix = _safe_plot_stem(
                    "_".join(
                        part
                        for part in (
                            sample_title or f"sample_{index + 1}",
                            section_name,
                        )
                        if part
                    )
                )
                try:
                    artifacts = _export_pyplot_origin_artifacts_for_paths(
                        paths=source_paths,
                        plugin_name=plugin_name,
                        output_dir=output_dir,
                        descriptor_prefix=prefix,
                        display_prefix=display_prefix,
                    )
                    if section_name == "rvt":
                        artifacts.extend(
                            _export_pyplot_origin_artifacts_for_paths(
                                paths=source_paths,
                                plugin_name=plugin_name,
                                output_dir=output_dir,
                                descriptor_prefix=f"{prefix}_residual",
                                display_prefix=f"{display_prefix} residual",
                                plot_mode="residual",
                            )
                        )
                except Exception as exc:  # pragma: no cover - depends on local Origin/COM setup
                    LOGGER.warning("%s Origin object generation skipped for %s: %s", display_prefix, sample_title, exc)
                    artifacts = []
                for artifact in artifacts:
                    descriptor = getattr(artifact, "descriptor", None)
                    if not descriptor:
                        continue
                    descriptors.append(str(descriptor))
                    origin_artifacts[str(descriptor)] = artifact
                if descriptors:
                    if section_name == "rvt":
                        raw_descriptors = [
                            descriptor
                            for descriptor in descriptors
                            if "residual" not in descriptor.casefold()
                        ]
                        residual_descriptors = [
                            descriptor
                            for descriptor in descriptors
                            if "residual" in descriptor.casefold()
                        ]
                        if raw_descriptors:
                            frame.at[index, origin_column] = raw_descriptors if len(raw_descriptors) > 1 else raw_descriptors[0]
                        if residual_descriptors:
                            frame.at[index, RVT_RESIDUAL_ORIGIN_COLUMN] = residual_descriptors if len(residual_descriptors) > 1 else residual_descriptors[0]
                    else:
                        frame.at[index, origin_column] = descriptors if len(descriptors) > 1 else descriptors[0]

    rvt_paths = []
    seen_rvt_paths: set[Path] = set()
    for root in rvt_search_roots:
        rvt_root = root / "RvsT"
        if not rvt_root.exists():
            continue
        for path in rvt_root.rglob("*.csv"):
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved in seen_rvt_paths or not path.is_file() or not _looks_like_rvst_csv(path):
                continue
            seen_rvt_paths.add(resolved)
            rvt_paths.append(path)

    for index, row in frame.iterrows():
        composition = row.get("Composition")
        microwire = row.get("Microwire")
        matching_rvt = []
        for path in rvt_paths:
            inferred_composition, inferred_microwire = _infer_rvst_word_sample(path, None)
            if (
                _normalise_microwire_word_part(inferred_composition)
                == _normalise_microwire_word_part(composition)
                and _normalise_microwire_word_key(inferred_microwire)
                == _normalise_microwire_word_key(microwire)
            ):
                matching_rvt.append(path)
        if matching_rvt:
            frame.at[index, RVT_FILE_COLUMN] = [str(path) for path in matching_rvt]
            frame.at[index, RVT_GRAPH_COLUMN] = [path.name for path in matching_rvt]

        rvt_origin_descriptors: list[str] = []
        for path in matching_rvt:
            rvt_frame = load_file(path)
            frame.at[index, RVT_POINT_COUNT_COLUMN] = int(len(rvt_frame))
            frame.at[index, RVT_TEMPERATURE_RANGE_COLUMN] = _format_numeric_range(rvt_frame["pv_c"])
            frame.at[index, RVT_RESISTANCE_RANGE_COLUMN] = _format_numeric_range(rvt_frame["resistance_ohm"])
            if include_origin:
                title = " ".join(part for part in (str(composition or ""), str(microwire or "")) if part).strip()
                try:
                    artifacts = _export_pyplot_origin_artifacts_for_paths(
                        paths=[path],
                        plugin_name="R vs T",
                        output_dir=output_dir,
                        descriptor_prefix=_safe_plot_stem(
                            "_".join(
                                part
                                for part in (
                                    title or path.stem,
                                    "r_vs_t",
                                    path.stem,
                                )
                                if part
                            )
                        ),
                        display_prefix=f"R vs T Origin graph: {title or path.stem}",
                    )
                except Exception as exc:  # pragma: no cover - depends on local Origin/COM setup
                    LOGGER.warning("R vs T Origin object generation skipped for %s: %s", path, exc)
                    artifacts = []
                for artifact in artifacts:
                    rvt_origin_descriptors.append(artifact.descriptor)
                    origin_artifacts[artifact.descriptor] = artifact
                    frame.at[index, RVT_GRAPH_COLUMN] = artifact.display_text or artifact.descriptor
        rvt_residual_origin_descriptors: list[str] = []
        if include_origin:
            for path in matching_rvt:
                title = " ".join(part for part in (str(composition or ""), str(microwire or "")) if part).strip()
                try:
                    residual_artifacts = _export_pyplot_origin_artifacts_for_paths(
                        paths=[path],
                        plugin_name="R vs T",
                        output_dir=output_dir,
                        descriptor_prefix=_safe_plot_stem(
                            "_".join(
                                part
                                for part in (
                                    title or path.stem,
                                    "r_vs_t_residual",
                                    path.stem,
                                )
                                if part
                            )
                        ),
                        display_prefix=f"R vs T residual Origin graph: {title or path.stem}",
                        plot_mode="residual",
                    )
                except Exception as exc:  # pragma: no cover - depends on local Origin/COM setup
                    LOGGER.warning("R vs T residual Origin object generation skipped for %s: %s", path, exc)
                    residual_artifacts = []
                for artifact in residual_artifacts:
                    rvt_residual_origin_descriptors.append(artifact.descriptor)
                    origin_artifacts[artifact.descriptor] = artifact
        if rvt_origin_descriptors:
            frame.at[index, RVT_ORIGIN_COLUMN] = (
                rvt_origin_descriptors if len(rvt_origin_descriptors) > 1 else rvt_origin_descriptors[0]
            )
        if rvt_residual_origin_descriptors:
            frame.at[index, RVT_RESIDUAL_ORIGIN_COLUMN] = (
                rvt_residual_origin_descriptors
                if len(rvt_residual_origin_descriptors) > 1
                else rvt_residual_origin_descriptors[0]
            )

    return frame.reset_index(drop=True), origin_artifacts


def _filter_microwire_word_report_frame(frame, sample: object):
    import pandas as pd

    if not sample:
        return frame.reset_index(drop=True)
    composition, microwire = _parse_microwire_word_sample(sample)
    if not composition and not microwire:
        return frame.reset_index(drop=True)
    if "Composition" not in frame.columns:
        raise ValueError("Cannot filter Word reports by sample because the input has no Composition column.")

    mask = frame["Composition"].map(_normalise_microwire_word_part) == _normalise_microwire_word_part(composition)
    if microwire:
        if "Microwire" not in frame.columns:
            raise ValueError("Cannot filter Word reports by sample because the input has no Microwire column.")
        mask = mask & (
            frame["Microwire"].map(_normalise_microwire_word_key)
            == _normalise_microwire_word_key(microwire)
        )
    filtered = frame.loc[mask].copy()
    if filtered.empty:
        raise ValueError(f"No rows matched sample {sample!r}.")
    return filtered.reset_index(drop=True)


def _load_microwire_word_report_frame(source_path: Path, args: argparse.Namespace, output_dir: Path):
    if _looks_like_rvst_csv(source_path):
        return _load_rvst_word_report_frame(
            source_path,
            getattr(args, "microwire_word_sample", None),
            output_dir,
            include_origin=bool(getattr(args, "microwire_word_origin", True)),
        )
    if source_path.suffix.lower() == ".pydpj":
        copy_dir = output_dir / "_project_copy"
        copy_dir.mkdir(parents=True, exist_ok=True)
        copied_source = copy_dir / f"{source_path.stem}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}{source_path.suffix}"
        shutil.copy2(source_path, copied_source)
        setattr(args, "_microwire_word_copied_project", str(copied_source))
        print(f"[microwire-word] copied_project={copied_source}")
        return _load_project_word_report_frame(
            copied_source,
            getattr(args, "microwire_word_sample", None),
            output_dir,
            include_origin=bool(getattr(args, "microwire_word_origin", True)),
            extra_search_roots=[source_path.parent],
        )

    from microwire_eda import MicrowireEdaConfig
    from microwire_eda.core import load_analysis_frame

    config = MicrowireEdaConfig(
        input_path=source_path,
        copy_project=True,
        force_project_rebuild=bool(getattr(args, "microwire_word_force_project_rebuild", False)),
    )
    frame, _kind, _working_path, _copied_project_path, _used_project_rebuild = load_analysis_frame(config)
    return (
        _filter_microwire_word_report_frame(
            frame,
            getattr(args, "microwire_word_sample", None),
        ),
        {},
    )


def _microwire_word_graph_sections_for_row(
    row: Any,
    origin_artifacts: Mapping[str, Any] | None = None,
    *,
    include_all: bool = False,
    ole_embedding_results: Sequence[Any] | Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    from microwire_data_builder.core import word_report_section_manifest_for_row

    row_data = row if hasattr(row, "index") else pd.Series(row)
    sections: dict[str, dict[str, Any]] = {}
    evaluated = {
        str(item.get("title") or ""): item
        for item in word_report_section_manifest_for_row(
            row_data,
            origin_artifacts or {},
            ole_embedding_results=ole_embedding_results,
        )
    }
    for section_name, source_columns, graph_columns in _WORD_REPORT_GRAPH_MANIFEST_SECTIONS:
        source_values: list[str] = []
        for column in source_columns:
            source_values.extend(
                str(item)
                for item in _word_project_value_items(row_data.get(column))
                if str(item or "").strip()
            )
        source_values = list(dict.fromkeys(source_values))
        summary = evaluated.get(section_name, {})
        included = bool(summary.get("included"))
        if included or include_all:
            sections[section_name] = {
                "included": included,
                "status": str(summary.get("status") or ("included" if included else "skipped")),
                "reason": str(summary.get("reason") or ""),
                "sources": source_values,
                "graphs": list(summary.get("origin_descriptors") or []),
                "origin_artifacts_accepted": list(summary.get("origin_artifacts_accepted") or []),
                "origin_artifacts_attempted": list(summary.get("origin_artifacts_attempted") or []),
                "ole_insertions": list(summary.get("ole_insertions") or []),
                "ole_insertions_attempted": list(summary.get("ole_insertions_attempted") or []),
                "ole_insertions_succeeded": list(summary.get("ole_insertions_succeeded") or []),
                "ole_insertions_failed": list(summary.get("ole_insertions_failed") or []),
                "ole_insertions_skipped": list(summary.get("ole_insertions_skipped") or []),
                "ole_insertions_missing_artifact": list(summary.get("ole_insertions_missing_artifact") or []),
                "references": list(summary.get("references") or []),
                "invalid_origin_descriptors": list(summary.get("invalid_origin_descriptors") or []),
                "missing_origin_descriptors": list(summary.get("missing_origin_descriptors") or []),
                "invalid_references": list(summary.get("invalid_references") or []),
            }
    return sections


def _filter_microwire_word_graph_rows(
    frame: Any,
    origin_artifacts: Mapping[str, Any] | None = None,
):
    if frame.empty:
        return frame
    keep_indices = [
        index
        for index, row in frame.iterrows()
        if any(
            section.get("included")
            for section in _microwire_word_graph_sections_for_row(
                row,
                origin_artifacts,
            ).values()
        )
    ]
    return frame.loc[keep_indices].reset_index(drop=True)


def _word_report_output_filenames(frame: Any) -> list[str]:
    from microwire_data_builder.core import _word_report_filename

    used_names: set[str] = set()
    filenames: list[str] = []
    for index, (_, row) in enumerate(frame.reset_index(drop=True).iterrows()):
        filename = _word_report_filename(row, index)
        stem = Path(filename).stem
        suffix = Path(filename).suffix or ".docx"
        candidate = filename
        duplicate_index = 2
        while candidate.lower() in used_names:
            candidate = f"{stem}_{duplicate_index}{suffix}"
            duplicate_index += 1
        used_names.add(candidate.lower())
        filenames.append(candidate)
    return filenames


def _word_manifest_stat_target(path: Path) -> Path:
    if path.is_dir():
        for child_name in ("measurement.csv", "metadata.json"):
            child = path / child_name
            if child.exists():
                return child
    return path


def _word_manifest_source_entry(source: str) -> dict[str, Any]:
    path = Path(source)
    stat_path = _word_manifest_stat_target(path)
    entry: dict[str, Any] = {
        "path": str(path),
        "stat_path": str(stat_path),
        "exists": False,
        "is_dir": False,
        "mtime": None,
        "size": None,
    }
    try:
        entry["exists"] = path.exists()
        entry["is_dir"] = path.is_dir()
        if stat_path.exists():
            stat = stat_path.stat()
            entry["mtime"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
            entry["size"] = int(stat.st_size)
    except OSError:
        pass
    return entry


def _write_microwire_word_manifest(
    frame: Any,
    reports: Sequence[Path],
    output_dir: Path,
    *,
    source_path: Path,
    copied_project: str | None,
    include_origin: bool,
    origin_artifacts: Mapping[str, Any] | None = None,
    ole_embedding_results: Mapping[Path, Sequence[Any]] | None = None,
) -> tuple[Path, Path]:
    exported_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for index, (_, row) in enumerate(frame.reset_index(drop=True).iterrows()):
        report_path = Path(reports[index]) if index < len(reports) else output_dir / _word_report_output_filenames(frame)[index]
        sections = _microwire_word_graph_sections_for_row(
            row,
            origin_artifacts,
            include_all=True,
            ole_embedding_results=(
                ole_embedding_results.get(report_path)
                if ole_embedding_results is not None
                else None
            ),
        )
        included_sections = sorted(
            section_name
            for section_name, section_data in sections.items()
            if section_data.get("included")
        )
        skipped_sections = sorted(
            section_name
            for section_name, section_data in sections.items()
            if not section_data.get("included") and section_data.get("status") == "skipped"
        )
        invalid_sections = sorted(
            section_name
            for section_name, section_data in sections.items()
            if section_data.get("status") == "invalid"
        )
        source_entries = {
            section_name: [
                _word_manifest_source_entry(source)
                for source in section_data.get("sources", [])
            ]
            for section_name, section_data in sections.items()
        }
        rows.append(
            {
                "composition": str(row.get("Composition") or "").strip(),
                "microwire": str(row.get("Microwire") or "").strip(),
                "docx": str(report_path),
                "docx_name": report_path.name,
                "graph_sections": included_sections,
                "included_sections": included_sections,
                "skipped_sections": skipped_sections,
                "invalid_sections": invalid_sections,
                "sections": sections,
                "source_files": source_entries,
            }
        )

    manifest = {
        "format": "microwire-docx-export-manifest-v1",
        "exported_at": exported_at,
        "source_project": str(source_path),
        "copied_project": copied_project,
        "output_dir": str(output_dir),
        "origin_embeddings": bool(include_origin),
        "report_count": len(rows),
        "reports": rows,
    }
    json_path = output_dir / "docx_export_manifest.json"
    csv_path = output_dir / "docx_export_manifest.csv"
    json_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "composition",
                "microwire",
                "docx_name",
                "graph_sections",
                "source_count",
                "sources",
            ],
        )
        writer.writeheader()
        for item in rows:
            sources = []
            for section_data in item["source_files"].values():
                for source_entry in section_data:
                    sources.append(str(source_entry.get("path") or ""))
            writer.writerow(
                {
                    "composition": item["composition"],
                    "microwire": item["microwire"],
                    "docx_name": item["docx_name"],
                    "graph_sections": "; ".join(item["graph_sections"]),
                    "source_count": len(sources),
                    "sources": "; ".join(sources),
                }
            )
    return json_path, csv_path


def _archive_existing_microwire_word_reports(frame: Any, output_dir: Path) -> list[Path]:
    archive_dir = output_dir / "archive"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archived: list[Path] = []
    for filename in _word_report_output_filenames(frame):
        path = output_dir / filename
        if not path.exists():
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / f"{path.stem}_before_batch_{timestamp}{path.suffix}"
        counter = 2
        while target.exists():
            target = archive_dir / f"{path.stem}_before_batch_{timestamp}_{counter}{path.suffix}"
            counter += 1
        shutil.move(str(path), str(target))
        archived.append(target)
    return archived


def _disable_originpro_exit_detach() -> None:
    try:
        import plotting.shared.origin as shared_origin

        setattr(shared_origin, "_ORIGIN_RELEASED", True)
    except Exception:
        pass
    try:
        import atexit
        import originpro.config as origin_config  # type: ignore
    except Exception:
        return
    handler = getattr(origin_config, "_exit_handler", None)
    if callable(handler):
        try:
            atexit.unregister(handler)
        except Exception:
            pass
    obj_count = getattr(origin_config, "_OBJS_COUNT", None)
    if isinstance(obj_count, list) and obj_count:
        try:
            obj_count[0] = max(int(obj_count[0]), 1)
        except Exception:
            obj_count[0] = 1


def _attach_origin_for_word_report() -> None:
    from plotting.shared.origin import _ensure_origin_sdk_on_path

    _ensure_origin_sdk_on_path()
    import originpro as op  # type: ignore

    attach = getattr(op, "attach", None)
    if callable(attach):
        attach()
    try:
        op.set_show()
    except Exception:
        pass


def _run_microwire_word_report_cli(args: argparse.Namespace) -> int:
    from microwire_data_builder.core import export_word_reports

    try:
        source_path = Path(str(getattr(args, "microwire_word_report", "")).strip()).expanduser()
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        output_dir_value = getattr(args, "out", None)
        output_dir = (
            Path(str(output_dir_value)).expanduser()
            if output_dir_value
            else source_path.with_suffix("").parent / f"{source_path.stem}_word_reports"
        )
        if bool(getattr(args, "microwire_word_origin", True)):
            try:
                _attach_origin_for_word_report()
            except Exception as exc:
                LOGGER.warning("Origin object generation skipped for Word report: %s", exc)
                setattr(args, "microwire_word_origin", False)
        frame, origin_artifacts = _load_microwire_word_report_frame(source_path, args, output_dir)
        if bool(getattr(args, "microwire_word_graphs_only", False)):
            before_count = len(frame)
            frame = _filter_microwire_word_graph_rows(frame, origin_artifacts)
            print(f"[microwire-word] graph_rows={len(frame)}")
            print(f"[microwire-word] skipped_graphless_rows={before_count - len(frame)}")
        archived_reports = _archive_existing_microwire_word_reports(frame, output_dir)
        for archived in archived_reports:
            print(f"[microwire-word] archived={archived}")
        ole_embedding_results: dict[Path, list[Any]] = {}
        reports = export_word_reports(
            frame,
            output_dir,
            origin_artifacts=origin_artifacts,
            ole_embedding_results=ole_embedding_results,
            logger=LOGGER,
        )
        manifest_json, manifest_csv = _write_microwire_word_manifest(
            frame,
            reports,
            output_dir,
            source_path=source_path,
            copied_project=getattr(args, "_microwire_word_copied_project", None),
            include_origin=bool(getattr(args, "microwire_word_origin", True)),
            origin_artifacts=origin_artifacts,
            ole_embedding_results=ole_embedding_results,
        )
        print(f"[microwire-word] output_dir={output_dir}")
        print(f"[microwire-word] reports={len(reports)}")
        print(f"[microwire-word] manifest={manifest_json}")
        print(f"[microwire-word] manifest_csv={manifest_csv}")
        for report in reports:
            print(f"[microwire-word] report={report}")
        return 0
    finally:
        _disable_originpro_exit_detach()


def _run_microwire_word_job_cli(args: argparse.Namespace) -> int:
    from microwire_data_builder.jobs import (
        JobRequestError,
        append_progress,
        error_payload,
        load_microwire_word_job_request,
        microwire_word_command,
        write_manifest,
        write_status,
    )

    job_path = Path(str(getattr(args, "microwire_word_job", "")).strip()).expanduser()
    request = None
    try:
        request = load_microwire_word_job_request(job_path)
        command = microwire_word_command(request)
        request.paths.log.parent.mkdir(parents=True, exist_ok=True)
        request.paths.log.write_text(
            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} loaded job {request.job_id}\n",
            encoding="utf-8",
        )
        write_status(request, state="running", step="validate", message="Validating Microwire Word export job.")
        append_progress(
            request,
            event="started",
            step="validate",
            message="Microwire Word export job accepted.",
            fraction=0.0,
        )
        if request.paths.cancel.exists():
            write_status(
                request,
                state="cancelled",
                step="validate",
                message="Job was cancelled before export started.",
                exit_code=130,
            )
            append_progress(
                request,
                event="cancelled",
                step="validate",
                message="Cancel marker existed before export started.",
                fraction=1.0,
            )
            write_manifest(request, state="cancelled", exit_code=130, command=command)
            print(f"[microwire-word-job] status={request.paths.status}")
            print(f"[microwire-word-job] manifest={request.paths.manifest}")
            return 130
        if not request.source.exists():
            raise FileNotFoundError(request.source)
        if request.dry_run:
            append_progress(
                request,
                event="validated",
                step="dry_run",
                message="Dry run validated the request without generating DOCX files.",
                fraction=1.0,
            )
            write_status(
                request,
                state="succeeded",
                step="dry_run",
                message="Dry run complete; no DOCX or Origin objects were generated.",
                exit_code=0,
            )
            write_manifest(request, state="succeeded", exit_code=0, command=command)
            print(f"[microwire-word-job] dry_run=true")
            print(f"[microwire-word-job] status={request.paths.status}")
            print(f"[microwire-word-job] progress={request.paths.progress}")
            print(f"[microwire-word-job] manifest={request.paths.manifest}")
            return 0

        append_progress(
            request,
            event="export_started",
            step="export",
            message="Starting existing Microwire Word export path.",
            fraction=0.1,
        )
        export_args = argparse.Namespace(
            microwire_word_report=str(request.source),
            microwire_word_sample=request.sample,
            microwire_word_force_project_rebuild=request.force_project_rebuild,
            microwire_word_origin=request.include_origin,
            microwire_word_graphs_only=request.graphs_only,
            out=str(request.output_dir) if request.output_dir is not None else None,
        )
        exit_code = _run_microwire_word_report_cli(export_args)
        state = "succeeded" if exit_code == 0 else "failed"
        append_progress(
            request,
            event="export_finished",
            step="export",
            message=f"Microwire Word export finished with exit code {exit_code}.",
            fraction=1.0,
        )
        write_status(
            request,
            state=state,
            step="export",
            message=f"Microwire Word export finished with exit code {exit_code}.",
            exit_code=exit_code,
        )
        write_manifest(request, state=state, exit_code=exit_code, command=command)
        print(f"[microwire-word-job] status={request.paths.status}")
        print(f"[microwire-word-job] progress={request.paths.progress}")
        print(f"[microwire-word-job] manifest={request.paths.manifest}")
        return exit_code
    except (JobRequestError, FileNotFoundError) as exc:
        print(f"[microwire-word-job] {exc}")
        if request is not None:
            payload = error_payload(exc, user_message=str(exc))
            write_status(
                request,
                state="failed",
                step="validate",
                message=str(exc),
                exit_code=2,
                error=payload,
            )
            append_progress(request, event="failed", step="validate", message=str(exc), fraction=1.0)
            write_manifest(request, state="failed", exit_code=2, command=microwire_word_command(request))
        return 2
    except Exception as exc:
        LOGGER.exception("Microwire Word job failed")
        print(f"[microwire-word-job] {type(exc).__name__}: {exc}")
        if request is not None:
            payload = error_payload(exc, user_message="Microwire Word job failed. See status JSON for details.")
            write_status(
                request,
                state="failed",
                step="export",
                message=str(exc),
                exit_code=1,
                error=payload,
            )
            append_progress(request, event="failed", step="export", message=str(exc), fraction=1.0)
            write_manifest(request, state="failed", exit_code=1, command=microwire_word_command(request))
        return 1


def _run_visual_check(args: argparse.Namespace) -> int:
    plugin_token = str(getattr(args, "visual_plugin", "manual-stress-strain")).strip().lower()
    supported_tokens = {
        "manual",
        "manual-stress-strain",
        "manual_stress_strain",
        "manual stress/strain",
        "shape-memory",
        "shape_memory",
        "shape-memory-stress-strain",
        "shape_memory_stress_strain",
        "shape memory stress/strain",
    }
    if plugin_token not in supported_tokens:
        print(
            f"Unsupported --visual-plugin '{plugin_token}'. "
            "Only manual stress/strain visual-check is currently implemented."
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
    state_getter = getattr(window, "automation_get_state", None)
    if callable(state_getter):
        state = state_getter()
        if isinstance(state, dict):
            return state
    return {"plugin": plugin_name}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text_atomic(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _select_pyplot_plugin(window: "PyPlotWorkbench", plugin_name: str) -> None:
    selector = getattr(window, "automation_select_plugin", None)
    if callable(selector):
        selector(plugin_name)
        return
    raise RuntimeError("PyPlot plugin selection automation is unavailable.")


def _session_registry_dir() -> Path:
    return Path(tempfile.gettempdir()) / "pyplot_automation_sessions"


def _session_record_path(session_id: str) -> Path:
    return _session_registry_dir() / f"{session_id}.json"


def _write_session_record(payload: dict[str, Any]) -> Path:
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise RuntimeError("Session record payload is missing session_id.")
    path = _session_record_path(session_id)
    _write_json(path, payload)
    return path


def _remove_session_record(session_id: str) -> None:
    path = _session_record_path(session_id)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass


def _is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _load_session_record(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _list_session_records() -> list[dict[str, Any]]:
    registry = _session_registry_dir()
    if not registry.exists():
        return []
    payloads: list[dict[str, Any]] = []
    for path in sorted(registry.glob("*.json")):
        payload = _load_session_record(path)
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _get_session_record(session_id: str) -> dict[str, Any]:
    token = str(session_id or "").strip()
    if not token:
        raise _AutomationRecipeError("PyPlot session id is required.")
    record_path = _session_record_path(token)
    payload = _load_session_record(record_path)
    if not isinstance(payload, dict):
        deadline = time.time() + 5.0
        while time.time() < deadline:
            time.sleep(0.1)
            payload = _load_session_record(record_path)
            if isinstance(payload, dict):
                break
    if not isinstance(payload, dict):
        raise _AutomationRecipeError(f"PyPlot session '{token}' is not available.")
    return payload


def _coerce_session_timeout(value: object) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        timeout = DEFAULT_SESSION_COMMAND_TIMEOUT_S
    return max(5.0, timeout)


class _PyPlotSessionBridge(QtCore.QObject):
    _command_signal = QtCore.pyqtSignal(object, object)

    def __init__(
        self,
        *,
        window: "PyPlotWorkbench",
        session_id: str,
        token: str,
        port: int,
        record_path: Path,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.session_id = session_id
        self.token = token
        self.port = port
        self.record_path = record_path
        self._command_signal.connect(self._execute_queued_command)

    def session_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "token": self.token,
            "port": self.port,
            "pid": os.getpid(),
            "cwd": str(Path.cwd()),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "plugin": getattr(self.window, "_current_plotter_name", None),
            "protocol_version": SESSION_PROTOCOL_VERSION,
        }

    def dispatch_remote_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("token") != self.token:
            raise RuntimeError("PyPlot session token is invalid.")
        command = payload.get("command")
        if not isinstance(command, dict):
            raise RuntimeError("PyPlot session request must include an object 'command'.")
        return self._dispatch_on_gui_thread(
            command,
            timeout_s=_coerce_session_timeout(payload.get("timeout_s")),
        )

    def _dispatch_on_gui_thread(
        self,
        command: dict[str, Any],
        *,
        timeout_s: float = DEFAULT_SESSION_COMMAND_TIMEOUT_S,
    ) -> dict[str, Any]:
        if QtCore.QThread.currentThread() is self.thread():
            return self.window.automation_execute_command(command)
        holder: dict[str, Any] = {}
        event = threading.Event()
        self._command_signal.emit(command, (holder, event))
        if not event.wait(timeout=timeout_s):
            raise TimeoutError("Timed out waiting for PyPlot session command to finish.")
        error = holder.get("error")
        if isinstance(error, BaseException):
            raise error
        result = holder.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("PyPlot session returned an invalid response payload.")
        return result

    @QtCore.pyqtSlot(object, object)
    def _execute_queued_command(self, command: object, transport: object) -> None:
        holder, event = cast(tuple[dict[str, Any], threading.Event], transport)
        try:
            if not isinstance(command, dict):
                raise RuntimeError("PyPlot session command payload is invalid.")
            holder["result"] = self.window.automation_execute_command(command)
        except Exception as exc:
            holder["error"] = exc
        finally:
            event.set()


class _PyPlotSessionTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int]) -> None:
        self.bridge: _PyPlotSessionBridge | None = None
        super().__init__(server_address, _PyPlotSessionRequestHandler)


class _PyPlotSessionRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(1_000_000)
        if not raw:
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            response: dict[str, Any] = {
                "status": "error",
                "error": f"Invalid PyPlot session JSON payload: {exc}",
            }
        else:
            if not isinstance(payload, dict):
                response = {
                    "status": "error",
                    "error": "PyPlot session request must be a JSON object.",
                }
            else:
                try:
                    bridge = getattr(self.server, "bridge", None)  # type: ignore[attr-defined]
                    if bridge is None:
                        raise RuntimeError("PyPlot session bridge is not ready.")
                    response = bridge.dispatch_remote_command(payload)
                except Exception as exc:
                    response = {
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
        self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))


def _send_pyplot_session_command(
    session_id: str,
    command: dict[str, Any],
    *,
    timeout_s: float = DEFAULT_SESSION_COMMAND_TIMEOUT_S,
) -> dict[str, Any]:
    record = _get_session_record(session_id)
    host = str(record.get("host") or "127.0.0.1")
    port = record.get("port")
    token = record.get("token")
    if not isinstance(port, int) or port <= 0:
        raise _AutomationRecipeError(f"PyPlot session '{session_id}' has an invalid port.")
    if not isinstance(token, str) or not token:
        raise _AutomationRecipeError(f"PyPlot session '{session_id}' is missing its auth token.")
    payload = {
        "token": token,
        "command": command,
        "timeout_s": _coerce_session_timeout(timeout_s),
    }
    try:
        with socket.create_connection((host, port), timeout=max(5.0, float(timeout_s))) as sock:
            sock.settimeout(max(5.0, float(timeout_s)))
            raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            sock.sendall(raw)
            buffer = b""
            while not buffer.endswith(b"\n"):
                chunk = sock.recv(65_536)
                if not chunk:
                    break
                buffer += chunk
    except OSError as exc:
        raise _AutomationRecipeError(
            f"Failed to contact PyPlot session '{session_id}' on {host}:{port}: {exc}"
        ) from exc
    try:
        response = json.loads(buffer.decode("utf-8"))
    except Exception as exc:
        raise _AutomationRecipeError(
            f"PyPlot session '{session_id}' returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(response, dict):
        raise _AutomationRecipeError(f"PyPlot session '{session_id}' returned an invalid response.")
    return response


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
        if not window_pixmap.isNull():
            temp_gui_path: Path | None = None
            temp_overlay_path: Path | None = None
            try:
                fd_gui, temp_gui_name = tempfile.mkstemp(prefix="codex-review-gui-", suffix=".png")
                os.close(fd_gui)
                temp_gui_path = Path(temp_gui_name)
                if window_pixmap.save(str(temp_gui_path)):
                    current_canvas = None
                    try:
                        current_canvas = window._current_canvas()  # type: ignore[attr-defined]
                    except Exception:
                        current_canvas = None
                    current_figure = getattr(current_canvas, "figure", None) if current_canvas is not None else None
                    current_descriptor = None
                    try:
                        current_descriptor = getattr(window, "_tab_descriptors", {}).get(current_widget)
                    except Exception:
                        current_descriptor = None
                    if current_canvas is not None and current_figure is not None:
                        fd_overlay, temp_overlay_name = tempfile.mkstemp(prefix="codex-review-fig-", suffix=".png")
                        os.close(fd_overlay)
                        temp_overlay_path = Path(temp_overlay_name)
                        restore_size: tuple[float, float] | None = None
                        if (
                            current_descriptor is not None
                            and str(getattr(current_descriptor, "kind", "") or "") == "layout_graph"
                        ):
                            metadata = getattr(current_descriptor, "metadata", {}) or {}
                            config = metadata.get("layout_config") if isinstance(metadata, dict) else None
                            if isinstance(config, dict):
                                try:
                                    export_w = float(config.get("figure_width") or 0.0)
                                    export_h = float(config.get("figure_height") or 0.0)
                                except Exception:
                                    export_w = export_h = 0.0
                                if export_w > 0.0 and export_h > 0.0:
                                    try:
                                        size = current_figure.get_size_inches()
                                        restore_size = (float(size[0]), float(size[1]))
                                        current_figure.set_size_inches(export_w, export_h, forward=False)
                                    except Exception:
                                        restore_size = None
                        try:
                            current_figure.savefig(temp_overlay_path, dpi=300)
                        finally:
                            if restore_size is not None:
                                try:
                                    current_figure.set_size_inches(restore_size[0], restore_size[1], forward=False)
                                except Exception:
                                    pass
                        base = Image.open(temp_gui_path).convert("RGBA")
                        overlay = Image.open(temp_overlay_path).convert("RGBA")
                        origin = current_canvas.mapTo(window, QtCore.QPoint(0, 0))
                        canvas_w = max(1, int(current_canvas.width()))
                        canvas_h = max(1, int(current_canvas.height()))
                        scale = min(canvas_w / overlay.width, canvas_h / overlay.height)
                        scale *= 0.92
                        scaled_w = max(1, int(round(overlay.width * scale)))
                        scaled_h = max(1, int(round(overlay.height * scale)))
                        overlay = overlay.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
                        background = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 255))
                        paste_x = max(0, (canvas_w - scaled_w) // 2)
                        paste_y = max(0, (canvas_h - scaled_h) // 2)
                        background.alpha_composite(overlay, (paste_x, paste_y))
                        base.alpha_composite(background, (int(origin.x()), int(origin.y())))
                        base.save(gui_target)
                    else:
                        temp_gui_path.replace(gui_target)
                    review_paths.append(gui_target)
            except Exception:
                try:
                    if window_pixmap.save(str(gui_target)):
                        review_paths.append(gui_target)
                except Exception:
                    pass
            finally:
                for temp_path in (temp_gui_path, temp_overlay_path):
                    if temp_path is not None and temp_path.exists():
                        try:
                            temp_path.unlink()
                        except Exception:
                            pass
        axes = window._current_axes()  # type: ignore[attr-defined]
        if axes is not None and getattr(axes, "figure", None) is not None:
            target = output_dir / "current-figure.png"
            descriptor = None
            try:
                descriptor = getattr(window, "_tab_descriptors", {}).get(current_widget)
            except Exception:
                descriptor = None
            restore_size: tuple[float, float] | None = None
            if descriptor is not None and str(getattr(descriptor, "kind", "") or "") == "layout_graph":
                metadata = getattr(descriptor, "metadata", {}) or {}
                config = metadata.get("layout_config") if isinstance(metadata, dict) else None
                if isinstance(config, dict):
                    try:
                        export_w = float(config.get("figure_width") or 0.0)
                        export_h = float(config.get("figure_height") or 0.0)
                    except Exception:
                        export_w = export_h = 0.0
                    if export_w > 0.0 and export_h > 0.0:
                        try:
                            size = axes.figure.get_size_inches()
                            restore_size = (float(size[0]), float(size[1]))
                            axes.figure.set_size_inches(export_w, export_h, forward=False)
                        except Exception:
                            restore_size = None
            try:
                axes.figure.savefig(target, dpi=180)
            finally:
                if restore_size is not None:
                    try:
                        axes.figure.set_size_inches(restore_size[0], restore_size[1], forward=False)
                    except Exception:
                        pass
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
            loader = getattr(window, "automation_load_project", None)
            if callable(loader):
                loader(request.load_project_path)
            else:
                raise RuntimeError("PyPlot project-load automation is unavailable.")

        if request.plugin_name:
            _select_pyplot_plugin(window, request.plugin_name)
            _pump_qt_events(app, rounds=4)

        if request.import_entries:
            importer = getattr(window, "automation_import_paths", None)
            if callable(importer):
                importer(request.import_entries)
            else:
                raise RuntimeError("PyPlot import automation is unavailable.")
            imported_paths = list(request.import_entries)

        if request.generate:
            generator = getattr(window, "automation_generate", None)
            if callable(generator):
                generator()
            else:
                raise RuntimeError("PyPlot generate automation is unavailable.")

        for graph_payload in request.build_graphs:
            creator = getattr(window, "automation_build_graph", None)
            if callable(creator):
                creator(graph_payload)
            else:
                raise RuntimeError("PyPlot graph builder automation is unavailable.")

        for figure_payload in request.create_figures:
            creator = getattr(window, "automation_create_figure", None)
            if callable(creator):
                creator(figure_payload)
            else:
                raise RuntimeError("PyPlot figure layout automation is unavailable.")

        if request.open_graph_format:
            opener = getattr(window, "automation_open_graph_format", None)
            if callable(opener):
                opener()
            else:
                raise RuntimeError("PyPlot graph-format automation is unavailable.")

        if request.open_origin:
            opener = getattr(window, "automation_open_origin", None)
            if callable(opener):
                opener()
            else:
                raise RuntimeError("PyPlot Origin automation is unavailable.")

        wait_ms = max(0, int(request.wait_ms or 0))
        if wait_ms > 0:
            deadline = time.time() + wait_ms / 1000.0
            while time.time() < deadline:
                _pump_qt_events(app, rounds=1)
                time.sleep(min(0.02, max(0.0, deadline - time.time())))

        if isinstance(request.window_image_path, Path):
            capturer = getattr(window, "automation_capture_window", None)
            if callable(capturer):
                capturer(request.window_image_path)
            else:
                raise RuntimeError("PyPlot window-capture automation is unavailable.")

        if isinstance(request.current_plot_image_path, Path):
            capturer = getattr(window, "automation_capture_current_plot", None)
            if callable(capturer):
                capturer(request.current_plot_image_path)
            else:
                raise RuntimeError("PyPlot plot-capture automation is unavailable.")

        if isinstance(request.plot_images_dir, Path):
            exported_plot_paths = _export_visible_plot_images(window, app, request.plot_images_dir)

        if isinstance(request.export_all_figures_dir, Path) and isinstance(request.export_all_figures_format, str):
            exporter = getattr(window, "automation_export_all_figures", None)
            if not callable(exporter):
                raise RuntimeError("PyPlot batch figure export automation is unavailable.")
            export_result = exporter(
                output_dir=request.export_all_figures_dir,
                fmt=request.export_all_figures_format,
                dpi=request.export_all_figures_dpi,
                transparent=bool(request.export_all_figures_transparent),
            )
            exported_all_figure_paths = [
                Path(item)
                for item in cast(dict[str, Any], export_result).get("paths", [])
                if isinstance(item, str)
            ]

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
            saver = getattr(window, "automation_save_project", None)
            if not callable(saver):
                raise RuntimeError("PyPlot project-save automation is unavailable.")
            save_result = saver(request.save_project_path)
            saved = cast(dict[str, Any], save_result).get("saved_project")
            if isinstance(saved, str) and saved.strip():
                saved_project_path = Path(saved)

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
        recipe = _load_json_object(recipe_path, label="Automation recipe")
        if recipe.get("kind") == "builder":
            return _run_builder_automation_recipe(recipe_path)
        request = _load_automation_recipe_request(recipe_path)
    except _AutomationRecipeError as exc:
        print(f"[automation-recipe] {exc}")
        return 2
    return _run_pyplot_automation_request(request, qt_args)


def _load_session_command_from_args(args: argparse.Namespace) -> dict[str, Any]:
    command_file = getattr(args, "pyplot_session_command_file", None)
    if isinstance(command_file, str) and command_file.strip():
        path = Path(command_file).expanduser()
        payload = _load_json_object(path, label="PyPlot session command")
        return payload
    command_json = getattr(args, "pyplot_session_command_json", None)
    if not isinstance(command_json, str) or not command_json.strip():
        raise _AutomationRecipeError(
            "PyPlot session send requires either --pyplot-session-command-json or --pyplot-session-command-file."
        )
    try:
        payload = json.loads(command_json)
    except json.JSONDecodeError as exc:
        raise _AutomationRecipeError(
            f"PyPlot session command JSON is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise _AutomationRecipeError("PyPlot session command must be a JSON object.")
    return payload


def _run_pyplot_session_client(args: argparse.Namespace) -> int:
    try:
        if getattr(args, "pyplot_session_list", False):
            response = {
                "status": "ok",
                "sessions": _list_session_records(),
            }
        elif getattr(args, "pyplot_session_state", False):
            session_id = str(getattr(args, "pyplot_session_id", None) or "")
            response = _send_pyplot_session_command(session_id, {"action": "state"})
        elif getattr(args, "pyplot_session_close", False):
            session_id = str(getattr(args, "pyplot_session_id", None) or "")
            response = _send_pyplot_session_command(session_id, {"action": "close"})
        elif getattr(args, "pyplot_session_send", False):
            session_id = str(getattr(args, "pyplot_session_id", None) or "")
            command = _load_session_command_from_args(args)
            response = _send_pyplot_session_command(session_id, command)
        else:
            raise _AutomationRecipeError("No PyPlot session action was requested.")
    except _AutomationRecipeError as exc:
        print(f"[pyplot-session] {exc}")
        return 2

    print(json.dumps(response, ensure_ascii=False))
    return 0 if response.get("status") == "ok" else 1


def _run_pyplot_session_start(args: argparse.Namespace, qt_args: list[str]) -> int:
    from plotting.pyplot.app import PyPlotWorkbench

    show_window = bool(getattr(args, "pyplot_show_window", False))
    created_app = False
    app = QtWidgets.QApplication.instance()
    if not isinstance(app, QtWidgets.QApplication):
        if not show_window:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication([sys.argv[0], *qt_args])
        created_app = True
        _schedule_theme_application(app)
    try:
        app.setQuitOnLastWindowClosed(True)
    except Exception:
        pass

    request = _pyplot_request_from_legacy_args(args)
    window = PyPlotWorkbench(initial_plotter=request.plugin_name)
    if show_window:
        window.show()
    _pump_qt_events(app, rounds=4)

    if request.plugin_name:
        _select_pyplot_plugin(window, request.plugin_name)
    if request.import_entries:
        importer = getattr(window, "automation_import_paths", None)
        if callable(importer):
            importer(request.import_entries)
    if request.generate:
        generator = getattr(window, "automation_generate", None)
        if callable(generator):
            generator()
    if request.open_graph_format:
        opener = getattr(window, "automation_open_graph_format", None)
        if callable(opener):
            opener()
    if request.open_origin:
        opener = getattr(window, "automation_open_origin", None)
        if callable(opener):
            opener()

    session_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(24)
    server = _PyPlotSessionTcpServer(("127.0.0.1", 0))
    port = int(server.server_address[1])
    bridge = _PyPlotSessionBridge(
        window=window,
        session_id=session_id,
        token=token,
        port=port,
        record_path=_session_record_path(session_id),
    )
    server.bridge = bridge
    server_thread = threading.Thread(
        target=server.serve_forever,
        name=f"PyPlotSessionServer-{session_id}",
        daemon=True,
    )
    server_thread.start()

    session_payload = bridge.session_payload()
    session_payload["host"] = "127.0.0.1"
    record_path = _write_session_record(session_payload)

    info_path_value = getattr(args, "pyplot_session_info_file", None)
    if isinstance(info_path_value, str) and info_path_value.strip():
        _write_json(Path(info_path_value).expanduser(), session_payload)

    def _cleanup() -> None:
        _remove_session_record(session_id)
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass

    app.aboutToQuit.connect(_cleanup)
    window.destroyed.connect(lambda *_args: _cleanup())

    print(json.dumps(session_payload, ensure_ascii=False))
    try:
        app.exec()
    finally:
        _cleanup()
        if created_app:
            try:
                app.quit()
            except Exception:
                pass
    return 0


def _run_pyplot_automation(args: argparse.Namespace, qt_args: list[str]) -> int:
    if getattr(args, "pyplot_list_plugins", False):
        _pyplot_main, plugin_names = _load_pyplot_metadata()
        for name in plugin_names:
            print(name)
        return 0
    request = _pyplot_request_from_legacy_args(args)
    return _run_pyplot_automation_request(request, qt_args)


def _run_mini_dma_bench_plan(args: argparse.Namespace, qt_args: list[str]) -> int:
    from data_logging.mini_dma_logger.bench_automation import (
        MiniDmaBenchAutomationError,
        run_mini_dma_bench_plan,
    )

    try:
        summary = run_mini_dma_bench_plan(str(getattr(args, "mini_dma_bench_plan")), qt_args=qt_args)
    except MiniDmaBenchAutomationError as exc:
        print(f"[mini-dma-bench] {exc}")
        return 2
    except Exception as exc:
        print(f"[mini-dma-bench] {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _run_experiment_process(args: argparse.Namespace) -> int:
    key = getattr(args, "experiment_process", None)
    spec = EXPERIMENT_PROCESS_MODULES.get(key)
    if spec is None:
        print(f"[experiment-process] Unknown experiment process: {key}")
        return 2
    try:
        module_obj = import_module(spec.module)
        main_func = getattr(module_obj, "main")
        main_func()
    except Exception as exc:
        print(f"[experiment-process] {spec.display_name}: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1
    return 0


LOGGERS: Dict[str, LauncherFactory] = {
    "Serial Data Logger": _lazy("data_logging.data_logger", "main"),
    "Current Annealing Logger": _experiment_process_launcher(
        "Current Annealing Logger",
        "data_logging.current_annealing_logger.current_annealing_logger",
        "current_annealing",
    ),
    "AC Susceptibility Logger": _experiment_process_launcher(
        "AC Susceptibility Logger",
        "data_logging.ac_susceptibility_logger.ac_susceptibility_logger",
        "ac_susceptibility",
    ),
    "Mini DMA Logger": _experiment_process_launcher(
        "Mini DMA Logger",
        "data_logging.mini_dma_logger.mini_dma_logger",
        "mini_dma",
    ),
    "Shared HMP PSU Setup": _lazy(
        "data_logging.shared_power_supply.setup_ui", "main"
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
    "Universal Video Builder": _lazy("microwire_data_builder.universal_video_builder", "main"),
    "Microwire EDA": _lazy("microwire_eda", "main"),
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
    if _is_pyplot_session_requested(args):
        if getattr(args, "pyplot_session_start", False):
            raise SystemExit(_run_pyplot_session_start(args, qt_args))
        raise SystemExit(_run_pyplot_session_client(args))
    if args.visual_check:
        raise SystemExit(_run_visual_check(args))
    if getattr(args, "automation_recipe", None):
        raise SystemExit(_run_automation_recipe(args, qt_args))
    if _is_mini_dma_bench_requested(args):
        raise SystemExit(_run_mini_dma_bench_plan(args, qt_args))
    if _is_experiment_process_requested(args):
        raise SystemExit(_run_experiment_process(args))
    if _is_microwire_word_job_requested(args):
        raise SystemExit(_run_microwire_word_job_cli(args))
    if _is_microwire_word_report_requested(args):
        raise SystemExit(_run_microwire_word_report_cli(args))
    if _is_microwire_eda_requested(args):
        raise SystemExit(_run_microwire_eda_cli(args))
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
